"""覆盖区间取并集 + 先查后补的回归测试。

全部走 get_messages 全流程，不直接调 _settle_coverage / _head_fill / _tail_fill：
直接调私有方法证明不了规则在链路里生效。协议端用合成页面回放，零网络。
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("_angel_eye_union_under_test")
package.__path__ = [str(ROOT)]
package.__package__ = "_angel_eye_union_under_test"
sys.modules[package.__name__] = package

from _angel_eye_union_under_test.services import history_repository, qq_history_service


def message(number, timestamp):
    return {
        "message_id": -number,
        "message_seq": number,
        "time": timestamp,
        "sender": {"user_id": 123456, "nickname": "Synthetic"},
        "message": [{"type": "text", "data": {"text": f"page {number}"}}],
    }


class HistoryBot:
    """按实际发出的游标回放合成页面，记录每一次历史请求。"""

    def __init__(self, pages=None, app="SnowLuma"):
        self.api = self
        self.app = app
        self.pages = dict(pages or {})
        self.history_calls = []

    async def call_action(self, action, **params):
        if action == "get_version_info":
            return {"app_name": self.app}
        if action == "get_login_info":
            return {"user_id": 999999}
        if action != "get_group_msg_history":
            raise AssertionError(f"Unexpected remote action: {action}")
        self.history_calls.append(params)
        field = "message_id" if self.app == "SnowLuma" else "message_seq"
        outcome = self.pages.get(params[field], [])
        if isinstance(outcome, Exception):
            raise outcome
        return {"messages": [dict(item) for item in outcome]}


class CoverageUnionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # 失败重试保持离线，不给套件加秒数。
        self.enterContext(patch.object(
            qq_history_service.asyncio, "sleep", new=AsyncMock()))

    def new_service(self, name):
        with patch.object(history_repository.StarTools, "get_data_dir",
                          return_value=Path(self.directory.name)):
            repo = history_repository.HistoryRepository(
                Path(self.directory.name) / f"{name}.db")
        with patch.object(qq_history_service, "HistoryRepository", return_value=repo):
            service = qq_history_service.QQChatHistoryService()
        service.config.server_call_delay = 0
        service.self_id = "999999"
        self.addCleanup(service.close)
        return service

    async def query(self, service, bot, start, end, group="123"):
        return await asyncio.wait_for(
            service.get_messages(bot, group, start_time=start, end_time=end),
            timeout=2,
        )

    async def test_returned_page_wider_than_request_keeps_real_boundary(self):
        """返回宽于请求：覆盖取实际返回的 2013，不按请求的 2014 截断。"""
        service = self.new_service("wider-return")
        bot = HistoryBot({0: [message(number, 2012 + number) for number in range(1, 7)]})

        result = await self.query(service, bot, 2014, 2018)

        state = service.repo.get_sync_state("123")
        self.assertEqual(state.covered_from, 2013)
        self.assertEqual(state.covered_to, 2018)
        self.assertEqual(result.covered_from, 2013)
        self.assertEqual(result.covered_to, 2018)
        # 这一页已经把请求的 2014 也覆盖了，tail 一侧不必再发请求。
        self.assertEqual(result.stop_reasons, ["head:target_reached"])

    async def test_request_wider_than_return_stops_coverage_at_request_start(self):
        """请求宽于返回：tail 拉到 2005 后 empty，覆盖停在请求起点 2000。"""
        service = self.new_service("wider-request")
        service.repo.insert_messages("123", [message(100, 2009), message(101, 2010)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-100, covered_from=2009, covered_to=2010)
        bot = HistoryBot({
            0: [message(number, 2004 + number) for number in range(1, 5)],
            -1: [],
        })

        result = await self.query(service, bot, 2000, 2010)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["tail:empty"])
        self.assertEqual(state.covered_from, 2000)
        self.assertEqual(state.covered_to, 2010)
        self.assertTrue(state.history_exhausted)

    async def test_failure_keeps_actual_page_boundary_instead_of_request_start(self):
        """失败不扩张：failures 停下时覆盖只认实际返回页的最小时间。"""
        service = self.new_service("failure")
        service.repo.insert_messages("123", [message(100, 2009), message(101, 2010)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-100, covered_from=2009, covered_to=2010)
        bot = HistoryBot({
            0: [message(number, 2004 + number) for number in range(1, 5)],
            -1: RuntimeError("offline failure"),
        })

        result = await self.query(service, bot, 2000, 2010)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["tail:failures"])
        self.assertEqual(state.covered_from, 2005)
        self.assertNotEqual(state.covered_from, 2000)
        self.assertFalse(state.history_exhausted)

    async def test_confirming_a_sub_range_never_shrinks_coverage(self):
        """只增不减：先按 2000~2010 扩张，再查 2005~2008 不得缩回消息自身范围。"""
        service = self.new_service("monotone")
        service.repo.insert_messages(
            "123", [message(number, 2004 + number) for number in range(1, 5)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-1, covered_from=2005, covered_to=2008)
        bot = HistoryBot({0: [message(number, 2004 + number) for number in range(1, 5)],
                          -1: []})

        await self.query(service, bot, 2000, 2010)
        wide = service.repo.get_sync_state("123")
        self.assertEqual(wide.covered_from, 2000)
        self.assertEqual(wide.covered_to, 2010)

        bot.history_calls.clear()
        await self.query(service, bot, 2005, 2008)
        narrow = service.repo.get_sync_state("123")
        self.assertEqual(bot.history_calls, [])
        self.assertEqual(narrow.covered_from, 2000)
        self.assertEqual(narrow.covered_to, 2010)

    async def test_covered_range_sends_no_request(self):
        """区间已包含：覆盖 [100,1000]，查 [200,800] 一个协议端请求都不发。"""
        service = self.new_service("covered")
        service.repo.insert_messages("123", [message(1, 100), message(2, 1000)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-1, covered_from=100, covered_to=1000)
        bot = HistoryBot()

        result = await self.query(service, bot, 200, 800)

        self.assertEqual(bot.history_calls, [])
        self.assertEqual(result.stop_reasons, [])
        self.assertEqual(result.coverage_status, "FULL")

    async def test_only_missing_head_triggers_head_fill(self):
        """只缺 head：覆盖 [100,1000]，查 [200,2000] 只发 head 请求。"""
        service = self.new_service("only-head")
        service.repo.insert_messages("123", [message(1, 100), message(2, 1000)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-1, covered_from=100, covered_to=1000)
        bot = HistoryBot({0: [message(3, 1600), message(4, 1500)], -3: []})

        result = await self.query(service, bot, 200, 2000)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["head:empty"])
        self.assertEqual(state.covered_from, 100)
        self.assertEqual(state.covered_to, 2000)

    async def test_only_missing_tail_triggers_tail_fill(self):
        """只缺 tail：覆盖 [100,1000]，查 [50,800] 只发 tail 请求。"""
        service = self.new_service("only-tail")
        service.repo.insert_messages("123", [message(1, 100), message(2, 1000)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-1, covered_from=100, covered_to=1000)
        bot = HistoryBot({0: [message(5, 60)], -5: []})

        result = await self.query(service, bot, 50, 800)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["tail:empty"])
        self.assertEqual(state.covered_from, 50)
        self.assertEqual(state.covered_to, 1000)
        self.assertTrue(state.history_exhausted)

    async def test_non_empty_cache_never_claims_the_far_end_from_head(self):
        """非空库翻到已存数据就停：head 只能算新端，最老那头交给 tail 的循环。"""
        service = self.new_service("gap")
        service.repo.insert_messages("123", [message(10, 2026), message(11, 2027)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-10, covered_from=2026, covered_to=2027)
        bot = HistoryBot({
            0: [message(20, 2029), message(21, 2028)],
            -20: [message(10, 2026)],
            -10: [message(10, 2026)],
        })

        result = await self.query(service, bot, 2020, 2030)

        state = service.repo.get_sync_state("123")
        # head 撞上那页最老一条就是已知最老边界，也只算新端到底；
        # tail 从 2026 往左翻 2 页后游标回绕，由它认最老那头。
        self.assertEqual(result.stop_reasons, ["head:caught_up", "tail:history_end"])
        self.assertEqual(state.covered_to, 2030)
        self.assertEqual(state.covered_from, 2020)
        self.assertTrue(state.history_exhausted)

    async def test_non_empty_cache_settles_far_end_through_tail_empty(self):
        """非空库查极早区间：最老那头只由 tail 的 empty 确认，head 不越权。"""
        service = self.new_service("drained")
        service.repo.insert_messages("123", [message(10, 2026)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-10, covered_from=2026, covered_to=2030)
        bot = HistoryBot({
            0: [message(30, 2031)],
            -30: [message(10, 2026)],
            -10: [message(9, 2024)],
            -9: [],
        })

        result = await self.query(service, bot, 2020, 2031)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["head:caught_up", "tail:empty"])
        self.assertNotIn("head:history_end", result.stop_reasons)
        self.assertEqual(state.covered_from, 2020)
        self.assertEqual(state.covered_to, 2031)
        self.assertTrue(state.history_exhausted)

    async def test_tail_cursor_wraparound_settles_at_request_start(self):
        """tail 以游标回绕收尾：覆盖推到请求起点，不是实际拉到的最老一条。"""
        service = self.new_service("wrap")
        service.repo.insert_messages("123", [message(10, 2026)])
        service.repo.upsert_sync_state(
            "123", oldest_seq=-10, covered_from=2026, covered_to=2030)
        bot = HistoryBot({
            0: [message(30, 2031)],
            -30: [message(10, 2026)],
            -10: [message(9, 2024)],
            -9: [message(8, 2022)],
            -8: [message(9, 2024)],
        })

        result = await self.query(service, bot, 2020, 2031)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["head:caught_up", "tail:history_end"])
        # 实际拉到的最老一条是 2022，但覆盖停在请求起点 2020。
        self.assertEqual(state.covered_from, 2020)
        self.assertEqual(state.covered_to, 2031)
        self.assertTrue(state.history_exhausted)

    async def test_first_head_pass_that_drains_history_settles_the_far_end(self):
        """空库首次翻光历史：head 翻到第 2 页没有新消息，两头一起算进覆盖。"""
        service = self.new_service("history-end")
        page = [message(1, 2028), message(2, 2027)]
        bot = HistoryBot({0: page, -1: page})

        result = await self.query(service, bot, 2020, 2030)

        state = service.repo.get_sync_state("123")
        self.assertEqual(result.stop_reasons, ["head:history_end"])
        self.assertEqual(state.covered_from, 2020)
        self.assertEqual(state.covered_to, 2030)
        self.assertTrue(state.history_exhausted)
        # 最老那头已由 head 确认到底，tail 不该再跑一轮（真机上那轮只会翻到尽头）。
        self.assertEqual(len(bot.history_calls), 2)
