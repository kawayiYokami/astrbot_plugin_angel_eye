"""Real history flows with synthetic OneBot pages and temporary SQLite."""

import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('_angel_eye_cursor_under_test')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from _angel_eye_cursor_under_test.services import history_repository, qq_history_service


def message(number, timestamp):
    return {
        'message_id': -number, 'message_seq': number, 'time': timestamp,
        'sender': {'user_id': 123456, 'nickname': 'Synthetic'},
        'message': [{'type': 'text', 'data': {'text': f'page {number}'}}],
    }


def anchor(app, number):
    return -number if app == 'SnowLuma' else number


def history_params(app, group, cursors):
    if app == 'SnowLuma':
        return [dict(group_id=int(group), message_id=cursor, reverse_order=True)
                for cursor in cursors]
    return [dict(group_id=int(group), message_seq=cursor, reverseOrder=True)
            for cursor in cursors]


class HistoryBot:
    """Route by the cursor actually sent; never manufacture a next page."""

    def __init__(self, app, pages, responses=None):
        self.api = self
        self.app = app
        self.history_calls = []
        self.set_pages(pages, responses)

    def set_pages(self, pages, responses=None):
        self.responses = {cursor: [page] for cursor, page in pages.items()}
        self.responses.update(responses or {})
        self.responses = deepcopy(self.responses)

    async def call_action(self, action, **params):
        if action == 'get_version_info':
            return {'app_name': self.app}
        if action == 'get_login_info':
            return {'user_id': 999999}
        if action != 'get_group_msg_history':
            raise AssertionError(f'Unexpected remote action: {action}')
        self.history_calls.append(params)
        field = 'message_id' if self.app == 'SnowLuma' else 'message_seq'
        cursor = params[field]
        outcomes = self.responses[cursor]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return {'messages': deepcopy(outcome)}


class TailCursorRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # Failure retries stay offline and do not add seconds to the suite.
        self.sleep = self.enterContext(patch.object(
            qq_history_service.asyncio, 'sleep', new=AsyncMock()))

    def new_service(self, name):
        with patch.object(history_repository.StarTools, 'get_data_dir',
                          return_value=Path(self.directory.name)):
            repo = history_repository.HistoryRepository(
                Path(self.directory.name) / f'{name}.db')
        with patch.object(qq_history_service, 'HistoryRepository', return_value=repo):
            service = qq_history_service.QQChatHistoryService()
        service.config.server_call_delay = 0
        self.addCleanup(service.close)
        return service

    @staticmethod
    def seed(service, group, app, messages):
        cached = [dict(item, _history_backend='snowluma') if app == 'SnowLuma'
                  else dict(item) for item in messages]
        service.repo.insert_messages(group, cached)
        service.repo.upsert_sync_state(
            group, oldest_seq=anchor(app, -messages[-1]['message_id']),
            covered_from=min(item['time'] for item in cached),
            covered_to=max(item['time'] for item in cached))

    async def query(self, service, bot, group='123', start=50):
        return await asyncio.wait_for(service.get_messages(
            bot, group, start_time=start, end_time=1000), timeout=2)

    async def tail(self, service, bot, group='123', start=1):
        return await asyncio.wait_for(service._tail_fill(
            bot, group, start, service.repo.get_sync_state(group)), timeout=2)

    async def test_restart_rebuilds_across_persisted_boundary_without_new_messages(self):
        for app in ['SnowLuma', 'NapCat']:
            with self.subTest(app=app):
                service = self.new_service(app)
                cached = [message(number, 1000 - number * 100)
                          for number in range(1, 5)]
                self.seed(service, '123', app, cached)
                service.close()
                service = self.new_service(app)
                self.assertEqual(service._snowluma_history_cursors, {})
                self.assertEqual(service._napcat_history_cursors, {})
                pages = {0: [cached[0]], **{
                    anchor(app, number): [cached[number]]
                    for number in range(1, 4)}, anchor(app, 4): [message(5, 40)]}
                bot = HistoryBot(app, pages)

                result = await self.query(service, bot)

                self.assertEqual(result.stop_reasons, ['head:caught_up', 'tail:target_reached'])
                self.assertEqual(result.covered_from, 40)
                self.assertEqual(result.returned_count, 4)
                self.assertEqual(service.repo.count_messages('123'), 5)
                self.assertFalse(result.history_exhausted)
                self.assertEqual(bot.history_calls, history_params(
                    app, '123', [0, 0, *[anchor(app, n) for n in range(1, 5)]]))

    async def test_first_bootstrap_anchor_may_equal_unconfirmed_persisted_cursor(self):
        for app in ['SnowLuma', 'NapCat']:
            with self.subTest(app=app):
                service = self.new_service(f'first-{app}')
                self.seed(service, '123', app, [message(1, 100)])
                # An empty head response leaves the persisted boundary unconfirmed.
                bot = HistoryBot(app, {anchor(app, 1): [message(2, 40)]},
                                 responses={0: [[], [message(1, 100)]]})

                result = await self.query(service, bot)

                self.assertEqual(result.stop_reasons, ['head:empty', 'tail:target_reached'])
                self.assertEqual(result.covered_from, 40)
                self.assertEqual(bot.history_calls, history_params(
                    app, '123', [0, 0, anchor(app, 1)]))

    async def test_backend_switches_rebuild_both_directions_in_full_queries(self):
        for first_app, second_app in [('SnowLuma', 'NapCat'), ('NapCat', 'SnowLuma')]:
            with self.subTest(first=first_app, second=second_app):
                service = self.new_service(f'switch-{first_app}')
                cached = [message(number, 1000 - number * 100)
                          for number in range(1, 5)]
                self.seed(service, '123', first_app, cached)
                bot = HistoryBot(first_app, {
                    0: [cached[0]], **{anchor(first_app, n): [cached[n]]
                                      for n in range(1, 4)},
                    anchor(first_app, 4): [message(5, 40)]})
                await self.query(service, bot)
                self.assertEqual(bot.history_calls, history_params(
                    first_app, '123', [0, 0, *[anchor(first_app, n) for n in range(1, 5)]]))
                bot.app = second_app
                bot.history_calls.clear()
                bot.set_pages({0: [cached[0]], **{
                    anchor(second_app, n): [cached[n]] for n in range(1, 4)},
                    anchor(second_app, 4): [message(5, 40)],
                    anchor(second_app, 5): [message(6, 5)]})

                result = await self.query(service, bot, start=10)

                self.assertEqual(result.stop_reasons, ['head:caught_up', 'tail:target_reached'])
                self.assertEqual(result.covered_from, 5)
                self.assertEqual(bot.history_calls, history_params(
                    second_app, '123', [0, 0, *[anchor(second_app, n) for n in range(1, 6)]]))
                active = (service._snowluma_history_cursors if second_app == 'SnowLuma'
                          else service._napcat_history_cursors)
                inactive = (service._napcat_history_cursors if second_app == 'SnowLuma'
                            else service._snowluma_history_cursors)
                self.assertEqual(active[(id(bot), '123')], anchor(second_app, 6))
                self.assertNotIn((id(bot), '123'), inactive)

    async def test_returned_cursor_cycles_and_repeats_are_bounded(self):
        for numbers in [[1, 2, 1], [1, 1]]:
            with self.subTest(numbers=numbers):
                service = self.new_service(f'cycle-{len(numbers)}')
                self.seed(service, '123', 'SnowLuma', [message(9, 100)])
                pages = {0: [message(1, 90)]}
                for current, following in zip(numbers, numbers[1:]):
                    pages[-current] = [message(following, 90 - following)]
                bot = HistoryBot('SnowLuma', pages)

                result = await self.tail(service, bot)

                self.assertEqual(result.stop_reason, 'cursor_cycle')
                self.assertEqual(result.pages_fetched, len(numbers))
                self.assertEqual(bot.history_calls, history_params(
                    'SnowLuma', '123', [0, *[-number for number in numbers[:-1]]]))

    async def test_empty_and_missing_anchor_responses_are_bounded(self):
        missing = {'time': 90, 'message': []}
        for app, page, reason, attempts, pages_fetched in [
            ('SnowLuma', [], 'empty', 1, 1), ('NapCat', [], 'empty', 1, 1),
            ('SnowLuma', [missing], 'failures', 3, 0),
            ('NapCat', [missing], 'cursor_stuck', 1, 1),
        ]:
            with self.subTest(app=app, page=page):
                service = self.new_service(f'missing-{app}-{attempts}-{pages_fetched}')
                self.seed(service, '123', app, [message(9, 100)])
                bot = HistoryBot(app, {0: page})

                result = await self.tail(service, bot)

                self.assertEqual(result.stop_reason, reason)
                self.assertEqual(result.pages_fetched, pages_fetched)
                self.assertEqual(bot.history_calls, history_params(app, '123', [0] * attempts))
                self.assertFalse(service.repo.get_sync_state('123').history_exhausted)

    async def test_transient_failures_do_not_poison_returned_anchors(self):
        for app in ['SnowLuma', 'NapCat']:
            with self.subTest(app=app):
                service = self.new_service(f'retry-{app}')
                self.seed(service, '123', app, [message(9, 100)])
                bot = HistoryBot(app, {}, responses={
                    0: [RuntimeError('offline failure'), RuntimeError('offline failure'),
                        [message(1, 90)]],
                    anchor(app, 1): [RuntimeError('offline failure'), RuntimeError('offline failure'),
                                     [message(2, 1)]],
                })

                result = await self.tail(service, bot)

                self.assertEqual(result.stop_reason, 'target_reached')
                self.assertEqual(result.pages_fetched, 2)
                self.assertEqual(bot.history_calls, history_params(
                    app, '123', [0, 0, 0, *[anchor(app, 1)] * 3]))

    async def test_permanent_failure_stops_after_configured_attempts(self):
        for app in ['SnowLuma', 'NapCat']:
            with self.subTest(app=app):
                service = self.new_service(f'failed-{app}')
                self.seed(service, '123', app, [message(9, 100)])
                bot = HistoryBot(app, {}, responses={0: [RuntimeError('offline failure')]})

                result = await self.tail(service, bot)

                self.assertEqual(result.stop_reason, 'failures')
                self.assertEqual(result.pages_fetched, 0)
                self.assertEqual(bot.history_calls, history_params(app, '123', [0] * 3))
                self.assertEqual(service.repo.count_messages('123'), 1)
                self.assertFalse(service.repo.get_sync_state('123').history_exhausted)
