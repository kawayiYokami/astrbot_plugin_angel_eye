"""Native AstrBot components, temporary SQLite and synthetic OneBot responses."""
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('_angel_eye_under_test')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from astrbot.api.message_components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage, Group, MessageMember, MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from _angel_eye_under_test.core.rich_message import format_rich_segment
from _angel_eye_under_test.services.history_repository import HistoryRepository
from _angel_eye_under_test.services.qq_history_service import QQChatHistoryService, HistoryQueryResult, FetchConfig, SyncResult
from _angel_eye_under_test.services.rich_content_service import RichContentResolver
from _angel_eye_under_test.tools import qq_history_tool as tool_module


def message(number, timestamp=1700000000, chain=None):
    return {'message_id': -number, 'message_seq': number, 'time': timestamp,
            'sender': {'user_id': 123456, 'nickname': 'Synthetic'},
            'message': chain if chain is not None else [{'type': 'text', 'data': {'text': 'sample'}}]}


class Bot:
    def __init__(self, pages=None, app='SnowLuma'):
        self.api = self
        self.pages = list(pages or [])
        self.app = app
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == 'get_version_info':
            return {'app_name': self.app}
        if action == 'get_forward_msg':
            return {'messages': [{'message': [{'type': 'text', 'data': {'text': 'needle ' + kwargs['id']}}]}]}
        return {'messages': self.pages.pop(0)}


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = HistoryRepository(Path(self.directory.name) / 'synthetic.db')
        self.addCleanup(self.repo.close)
        self.service = object.__new__(QQChatHistoryService)
        self.service.repo = self.repo
        self.service.config = FetchConfig(server_call_delay=0)
        self.service.self_id = '999999'
        self.service._group_locks = {}
        self.service._snowluma_history_cursors = {}
        self.service._napcat_history_cursors = {}
        self.service._forward_index_rotation = {}
        self.service.rich_content_resolver = RichContentResolver()

    def test_non_ascii_size_digits_never_break_rendering_or_indexing(self):
        for value in ['²', '①', '１２', '١٢', '9' * 20]:
            for kind in ['file', 'record', 'video']:
                with self.subTest(value=value, kind=kind):
                    part = {'type': kind, 'data': {'size': value}}
                    self.assertNotIn('大小：', format_rich_segment(part))
                    self.repo._build_search_text(message(1, chain=[part]))
        self.assertIn('123 字节', format_rich_segment({'type': 'file', 'data': {'size': '123'}}))

    def test_coverage_keeps_confirmed_cursor_when_older_page_shares_second(self):
        self.repo.insert_messages('123', [message(20)])
        self.repo.upsert_sync_state('123', oldest_seq=20, covered_from=1700000000)
        self.repo.insert_messages('123', [message(10)])
        self.repo.upsert_sync_state('123', oldest_seq=10, covered_from=1700000000)
        self.assertEqual(self.repo.update_coverage_from_messages('123').oldest_seq, 10)

    async def test_same_second_cached_pages_reach_older_history(self):
        pages = [[message(i, 100)] for i in range(1, 6)] + [[message(6, 40)]]
        self.repo.insert_messages('123', [page[0] for page in pages[:-1]])
        self.repo.upsert_sync_state('123', oldest_seq=999, covered_from=100, covered_to=100)
        result = await self.service._tail_fill(Bot(pages), '123', 50, self.repo.get_sync_state('123'))
        self.assertEqual(result.stop_reason, 'target_reached')
        self.assertEqual(result.pages_fetched, 6)
        self.assertFalse(self.repo.get_sync_state('123').history_exhausted)

    async def test_backend_change_and_new_connection_reconfirm_cursor(self):
        bot = Bot([[message(11)], [message(12)], [message(13)]], app='NapCat')
        self.service._snowluma_history_cursors[(id(bot), '123')] = -999
        await self.service._fetch_page(bot, '123', -999)
        await self.service._fetch_page(bot, '123', 11)
        bot.app = 'SnowLuma'
        await self.service._fetch_page(bot, '123', 12)
        calls = [params for action, params in bot.calls if action == 'get_group_msg_history']
        self.assertEqual([calls[0]['message_seq'], calls[1]['message_seq'], calls[2]['message_id']], [0, 11, 0])
        fresh = Bot([[message(14)]], app='NapCat')
        await self.service._fetch_page(fresh, '123', -999)
        self.assertEqual(fresh.calls[-1][1]['message_seq'], 0)

    async def test_remote_forward_body_is_indexed_before_current_keyword_filter(self):
        original = message(1, chain=[{'type': 'forward', 'data': {'id': 'synthetic'}}])
        self.repo.insert_messages('123', [original])
        self.service._head_fill = AsyncMock(return_value=SyncResult('head', 0, 0, 'caught_up'))
        bot = Bot()
        result = await self.service.get_messages(bot, '123', start_time=1700000000,
                                                 end_time=1700000000, keywords=['needle'])
        self.assertEqual(result.returned_count, 1)
        self.assertIn('needle synthetic', result.formatted_messages[0])
        self.assertIn('forward:index_bounded', result.stop_reasons)
        self.assertNotIn('content', original['message'][0]['data'])
        self.assertEqual(len([1 for action, _ in bot.calls if action == 'get_forward_msg']), 1)

    async def test_forward_index_and_display_share_three_read_budget(self):
        self.repo.insert_messages('123', [message(i, chain=[{'type': 'forward', 'data': {'id': str(i)}}])
                                           for i in range(1, 5)])
        self.service._head_fill = AsyncMock(return_value=SyncResult('head', 0, 0, 'caught_up'))
        bot = Bot()
        first = await self.service.get_messages(bot, '123', start_time=1700000000,
                                                end_time=1700000000, keywords=['needle'])
        self.assertEqual(first.returned_count, 3)
        self.assertEqual(len([1 for action, _ in bot.calls if action == 'get_forward_msg']), 3)
        second = await self.service.get_messages(bot, '123', start_time=1700000000,
                                                 end_time=1700000000, keywords=['needle'])
        self.assertEqual(second.returned_count, 4)
        self.assertEqual(len([1 for action, _ in bot.calls if action == 'get_forward_msg']), 4)


class HistoryParameterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        result = HistoryQueryResult(['sample'], 1, 1, 0, 1700000000, 1700003600,
                                    1700000000, 1700003600, 'FULL', False, ['head:caught_up'])
        self.service = SimpleNamespace(get_messages=AsyncMock(return_value=result))
        with patch.object(tool_module, 'QQChatHistoryService', return_value=self.service):
            self.tool = tool_module.QQHistorySearchTool()
        message_obj = AstrBotMessage()
        message_obj.type = MessageType.GROUP_MESSAGE
        message_obj.self_id = '999999'
        message_obj.sender = MessageMember(user_id='123456', nickname='Synthetic')
        message_obj.group = Group(group_id='10001', group_name='Synthetic')
        message_obj.message_id = '1'
        message_obj.session_id = '10001'
        message_obj.message = [Plain('sample')]
        message_obj.message_str = 'sample'
        self.event = AiocqhttpMessageEvent('sample', message_obj,
            PlatformMetadata(name='aiocqhttp', description='offline', id='synthetic'), '10001', object())

    def test_optional_schema_accepts_null(self):
        for definition in self.tool.parameters['properties'].values():
            self.assertIn('null', definition['type'])

    async def test_zero_none_omitted_and_legacy_sender_filters(self):
        for value in [0, None]:
            await self.tool.run(self.event, user_id=value)
            self.assertIsNone(self.service.get_messages.call_args.kwargs['filter_user_ids'])
            legacy = [123456]
            await self.tool.run(self.event, user_id=value, user_ids=legacy)
            self.assertEqual(self.service.get_messages.call_args.kwargs['filter_user_ids'], legacy)
            self.assertEqual(legacy, [123456])
        await self.tool.run(self.event, user_id=234567, user_ids=[123456])
        self.assertEqual(self.service.get_messages.call_args.kwargs['filter_user_ids'], [234567])

    async def test_invalid_sender_rejected_before_query(self):
        for value in [True, False, -1, 1.0, '123456', [], {}]:
            self.service.get_messages.reset_mock()
            result = await self.tool.run(self.event, user_id=value)
            self.assertTrue(result.startswith('参数错误:'), result)
            self.service.get_messages.assert_not_awaited()

    async def test_blank_time_and_slice_preserve_legacy_values(self):
        for value in ['', ' \t\n', None]:
            await self.tool.run(self.event, start=value, end=value, slice=value,
                                start_time=1700000000, end_time=1700003600)
            args = self.service.get_messages.call_args.kwargs
            self.assertEqual(args['start_time'], 1700000000)
            self.assertEqual(args['end_time'], 1700003600)
            self.assertIsNone(args['slice_expr'])

    async def test_invalid_optional_text_rejected_before_query(self):
        for name in ['start', 'end', 'slice']:
            for value in [0, False, [], {}]:
                self.service.get_messages.reset_mock()
                result = await self.tool.run(self.event, **{name: value})
                self.assertTrue(result.startswith('参数错误:'), result)
                self.service.get_messages.assert_not_awaited()
