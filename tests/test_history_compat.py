"""Offline regressions for SnowLuma history cursors using synthetic pages.

Execute the actual service/repository methods without importing an AstrBot
runtime or opening a live history database. All storage is in-memory SQLite.
"""

import ast
import asyncio
import logging
import sqlite3
import tempfile
import time
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Dict, List, Optional
import unittest

ROOT = Path(__file__).resolve().parents[1]
EYE = ROOT / 'services/qq_history_service.py'
REPO = ROOT / 'services/history_repository.py'


def module_constants(path, names):
    """按字面量读模块级常量，测试里不复述它们的值。"""
    tree = ast.parse(path.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    values[target.id] = ast.literal_eval(node.value)
    return values


def methods(path, names, constants=(), extra=None):
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
               time=time, logger=logging.getLogger('compat-test'), sqlite3=sqlite3,
               **module_constants(path, constants))
    env.update(extra or {})
    exec(compile(module, str(path), 'exec'), env)
    return {name: env[name] for name in names}


EM = methods(EYE, ['_fetch_page', '_tail_fill', '_settle_coverage'])
ANCHOR = methods(REPO, ['extract_anchor'])['extract_anchor']
RM = methods(REPO, ['_init_schema', 'upsert_sync_state', 'get_sync_state',
                    'update_coverage_from_messages', 'rebuild_oldest_seq_from_messages'],
             constants=['SCHEMA_VERSION'])
SCHEMA_VERSION = module_constants(REPO, ['SCHEMA_VERSION'])['SCHEMA_VERSION']
# 走真实入口的仓储构造：__init__ 里含「版本不符就关连接、删文件、重开」，
# 只抽 _init_schema 会整段跳过。
GATE = methods(REPO, ['__init__', '_open', '_rebuild_if_outdated', '_init_schema'],
               constants=['SCHEMA_VERSION'],
               extra={'Path': Path,
                      'StarTools': SimpleNamespace(get_data_dir=lambda _name: Path('.'))})


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
    obj = SimpleNamespace(_snowluma_history_cursors={}, _napcat_history_cursors={},
                          _backend_is_snowluma={},
                          repo=SimpleNamespace(extract_anchor=ANCHOR))
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

    async def test_eye_napcat_reconfirms_unknown_cursor_and_plain_anchor_keeps_seq(self):
        obj = eye_instance()
        bot = FakeBot('NapCat', pages=[[{'message_id': -12, 'message_seq': 800}]])
        page = await obj._fetch_page(bot, '123', 999)
        self.assertEqual(bot.history_params(), [dict(group_id=123, message_seq=0, reverseOrder=True)])
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
        def expand(group, from_ts=None, to_ts=None):
            update(**{name: value for name, value in
                      (('covered_from', from_ts), ('covered_to', to_ts)) if value is not None})
        obj.repo = SimpleNamespace(extract_anchor=ANCHOR, upsert_sync_state=update,
                                  expand_coverage=expand,
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
                                  expand_coverage=lambda group, from_ts=None, to_ts=None: None,
                                  get_sync_state=lambda group: state,
                                  insert_messages=lambda group, page: SimpleNamespace(
                                      inserted_count=0, page_oldest_anchor=ANCHOR(page[0])))
        result = await asyncio.wait_for(obj._tail_fill(bot, '123', 60, state), 1)
        # tail 侧游标回绕现在读作「协议端没有更老的数据了」，不再是异常标签。
        self.assertEqual(result.stop_reason, 'history_end')
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
            'INSERT INTO messages (group_id,message_id,message_seq,time,sender_id,body) VALUES (?,?,?,?,?,?)',
            [('123', '-999', -999, 900, 'u1', 'newer'), ('123', '12345', 12345, 800, 'u1', 'older')],
        )
        state = repo.update_coverage_from_messages('123')
        self.assertEqual(state.oldest_seq, 12345)
        self.assertEqual(state.covered_from, 800)

    async def test_version_guard_drops_old_schema_and_clears_coverage(self):
        """旧库（user_version=0，只有 raw_json）打开后整库清空重建。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.db'
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    message_seq INTEGER,
                    time INTEGER NOT NULL,
                    user_id TEXT,
                    nickname TEXT,
                    search_text TEXT,
                    raw_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(group_id, message_id)
                );
                CREATE TABLE sync_state (
                    group_id TEXT PRIMARY KEY,
                    oldest_seq INTEGER,
                    covered_from INTEGER,
                    covered_to INTEGER,
                    history_exhausted INTEGER DEFAULT 0,
                    last_sync_at INTEGER
                );
                """
            )
            conn.execute(
                "INSERT INTO messages (group_id,message_id,time,raw_json,created_at) "
                "VALUES ('123','-1',100,'{}',0)"
            )
            conn.execute(
                "INSERT INTO sync_state (group_id,covered_from,covered_to,history_exhausted) "
                "VALUES ('123',100,200,0)"
            )
            conn.execute('PRAGMA user_version = 0')
            conn.commit()
            conn.close()
            self.assertTrue(path.exists())

            # 走真实入口：重建发生在 __init__ 里，不在这里手动调 _init_schema，
            # 否则测不到「关闭连接 + 删文件 + 重开」这一段。
            repo = SimpleNamespace(db_path=path)
            for name, func in GATE.items():
                setattr(repo, name, MethodType(func, repo))
            repo.__init__(path)
            self.addCleanup(repo._conn.close)

            columns = {row['name'] for row in repo._conn.execute('PRAGMA table_info(messages)')}
            self.assertEqual(columns, {'group_id', 'message_id', 'message_seq', 'time', 'sender_id', 'body'})
            self.assertEqual(repo._conn.execute('SELECT COUNT(1) FROM messages').fetchone()[0], 0)
            # 覆盖区间必须一起清：留着它等于凭空声称"这段查过"
            self.assertEqual(repo._conn.execute('SELECT COUNT(1) FROM sync_state').fetchone()[0], 0)
            self.assertEqual(repo._conn.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)


if __name__ == '__main__':
    unittest.main(verbosity=2)
