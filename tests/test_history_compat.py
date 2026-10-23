"""Offline regressions for SnowLuma history cursors using synthetic pages.

Execute the actual service/repository methods without importing an AstrBot
runtime or opening a live history database. All storage is in-memory SQLite.
"""

import ast
import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List, Optional
import unittest

ROOT = Path(__file__).resolve().parents[1]
EYE = ROOT / 'services/qq_history_service.py'
REPO = ROOT / 'services/history_repository.py'


def methods(path, names):
    tree = ast.parse(path.read_text())
    selected = []
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            for node in cls.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
                    node.decorator_list = []
                    selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    env = dict(Any=Any, Dict=Dict, List=List, Optional=Optional, SyncState=lambda **kwargs: SimpleNamespace(**kwargs),
               SyncResult=lambda **kwargs: SimpleNamespace(**kwargs), asyncio=asyncio,
               time=time, logger=logging.getLogger('compat-test'))
    exec(compile(module, str(path), 'exec'), env)
    return {name: env[name] for name in names}


EM = methods(EYE, ['_fetch_page', '_tail_fill'])
ANCHOR = methods(REPO, ['extract_anchor'])['extract_anchor']
RM = methods(REPO, ['_init_schema', 'upsert_sync_state', 'get_sync_state',
                    'update_coverage_from_messages', 'rebuild_oldest_seq_from_messages'])


class FakeBot:
    def __init__(self, app='SnowLuma', pages=None, wrapped=False, failed_probe=False):
        self.app = app
        self.pages = list(pages or [[]])
        self.wrapped = wrapped
        self.failed_probe = failed_probe
        self.calls = []
        self.api = self

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if action == 'get_version_info':
            if self.failed_probe:
                raise RuntimeError('synthetic unavailable')
            info = {'app_name': self.app}
            return {'data': info} if self.wrapped else info
        return {'messages': self.pages.pop(0)}

    def history_params(self):
        return [params for action, params in self.calls if action == 'get_group_msg_history']






def eye_instance():
    obj = SimpleNamespace(_snowluma_history_cursors={}, repo=SimpleNamespace(extract_anchor=ANCHOR))
    for name, func in EM.items():
        setattr(obj, name, MethodType(func, obj))
    return obj


class HistoryCompatibilityTests(unittest.IsolatedAsyncioTestCase):





    async def test_eye_does_not_reuse_old_napcat_cursor(self):
        obj = eye_instance()
        bot = FakeBot(pages=[
            [{'message_id': -12, 'message_seq': 800}],
            [{'message_id': -24, 'message_seq': 750}],
        ])
        page = await obj._fetch_page(bot, '123', 999)
        self.assertEqual(bot.history_params()[0]['message_id'], 0)
        self.assertEqual(ANCHOR(page[0]), -12)
        self.assertEqual(page[0]['message_seq'], 800)
        await obj._fetch_page(bot, '123', ANCHOR(page[0]))
        self.assertEqual(bot.history_params()[1]['message_id'], -12)

    async def test_eye_cursor_scope_does_not_cross_groups_or_bot_connections(self):
        obj = eye_instance()
        bot = FakeBot(pages=[[{'message_id': -12}], [{'message_id': -15}]])
        await obj._fetch_page(bot, '123', 0)
        await obj._fetch_page(bot, '456', -12)
        self.assertEqual(bot.history_params()[1]['message_id'], 0)
        other = FakeBot(pages=[[{'message_id': -22}]])
        await obj._fetch_page(other, '123', -12)
        self.assertEqual(other.history_params()[0]['message_id'], 0)

    async def test_eye_napcat_unchanged_and_plain_anchor_keeps_seq(self):
        obj = eye_instance()
        bot = FakeBot('NapCat', pages=[[{'message_id': -12, 'message_seq': 800}]])
        page = await obj._fetch_page(bot, '123', 999)
        self.assertEqual(bot.history_params(), [dict(group_id=123, message_seq=999, reverseOrder=True)])
        self.assertEqual(ANCHOR(page[0]), 800)

    async def test_eye_tail_rebootstrap_can_cross_more_than_three_cached_pages(self):
        obj = eye_instance()
        bot = FakeBot(pages=[
            [{'message_id': -1, 'time': 900}], [{'message_id': -2, 'time': 800}],
            [{'message_id': -3, 'time': 700}], [{'message_id': -4, 'time': 600}],
            [{'message_id': -5, 'time': 50}],
        ])
        state = SimpleNamespace(history_exhausted=False, oldest_seq=999, covered_from=100)
        obj.config = SimpleNamespace(max_failures=3, server_call_delay=0,
                                     history_exhausted_no_progress_pages=3)
        obj._get_page_time_range = lambda page: (page[0]['time'], page[-1]['time'])
        def update(**params):
            state.covered_from = min(state.covered_from, params.get('covered_from', state.covered_from))
        obj.repo = SimpleNamespace(extract_anchor=ANCHOR, upsert_sync_state=update,
                                  get_sync_state=lambda group: state,
                                  insert_messages=lambda group, page: SimpleNamespace(
                                      inserted_count=0, page_oldest_anchor=ANCHOR(page[0])))
        result = await obj._tail_fill(bot, '123', 60, state)
        self.assertEqual(result.stop_reason, 'target_reached')
        self.assertEqual(result.pages_fetched, 5)
        self.assertEqual([p['message_id'] for p in bot.history_params()], [0, -1, -2, -3, -4])

    async def test_repository_selects_oldest_cursor_by_time_not_opaque_id(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        repo = SimpleNamespace(_conn=conn)
        for name, func in RM.items():
            setattr(repo, name, MethodType(func, repo))
        repo._init_schema()
        repo.upsert_sync_state('123', oldest_seq=-900, covered_from=900, covered_to=900)
        repo.upsert_sync_state('123', oldest_seq=500, covered_from=800, covered_to=850)
        self.assertEqual(repo.get_sync_state('123').oldest_seq, 500)
        repo.upsert_sync_state('123', oldest_seq=-1000, covered_from=850, covered_to=850)
        self.assertEqual(repo.get_sync_state('123').oldest_seq, 500)
        repo.upsert_sync_state('123', oldest_seq=600, covered_from=800, covered_to=850)
        self.assertEqual(repo.get_sync_state('123').oldest_seq, 600)

    async def test_eye_tail_rebootstrap_cursor_cycle_is_bounded(self):
        obj = eye_instance()
        bot = FakeBot(pages=[
            [{'message_id': -1, 'time': 900}], [{'message_id': -2, 'time': 800}],
            [{'message_id': -1, 'time': 900}],
        ])
        state = SimpleNamespace(history_exhausted=False, oldest_seq=999, covered_from=100)
        obj.config = SimpleNamespace(max_failures=3, server_call_delay=0,
                                     history_exhausted_no_progress_pages=3)
        obj._get_page_time_range = lambda page: (page[0]['time'], page[-1]['time'])
        obj.repo = SimpleNamespace(extract_anchor=ANCHOR, upsert_sync_state=lambda **params: None,
                                  get_sync_state=lambda group: state,
                                  insert_messages=lambda group, page: SimpleNamespace(
                                      inserted_count=0, page_oldest_anchor=ANCHOR(page[0])))
        result = await asyncio.wait_for(obj._tail_fill(bot, '123', 60, state), 1)
        self.assertEqual(result.stop_reason, 'cursor_cycle')
        self.assertEqual(result.pages_fetched, 3)

    async def test_repository_rebuilds_persistent_cursor_from_earliest_message_row(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        repo = SimpleNamespace(_conn=conn)
        for name, func in RM.items():
            setattr(repo, name, MethodType(func, repo))
        repo._init_schema()
        repo.upsert_sync_state('123', oldest_seq=-999, covered_from=800, covered_to=900)
        conn.executemany(
            'INSERT INTO messages (group_id,message_id,message_seq,time,raw_json,created_at) VALUES (?,?,?,?,?,?)',
            [('123', '-999', -999, 900, '{}', 0), ('123', '12345', 12345, 800, '{}', 0)],
        )
        state = repo.update_coverage_from_messages('123')
        self.assertEqual(state.oldest_seq, 12345)
        self.assertEqual(state.covered_from, 800)


if __name__ == '__main__':
    unittest.main(verbosity=2)
