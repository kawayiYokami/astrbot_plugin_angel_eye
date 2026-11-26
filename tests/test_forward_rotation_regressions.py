"""Forward-index scheduling with temporary SQLite and synthetic read responses."""
from pathlib import Path
import sys
from types import ModuleType
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("_angel_eye_under_test")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from _angel_eye_under_test.services.history_repository import HistoryRepository
from _angel_eye_under_test.services import qq_history_service as service_module


BASE = 1700000000


def forward(reference):
    return {"type": "forward", "data": {"id": str(reference)}}


def text(value):
    return {"type": "text", "data": {"text": value}}


def message(number, chain=None):
    return {
        "message_id": number, "message_seq": number, "time": BASE + number,
        "sender": {"user_id": 123456, "nickname": "Synthetic"},
        "message": chain if chain is not None else [forward(number)],
    }


class ForwardBot:
    def __init__(self, failed=(), contents=None):
        self.api = self
        self.failed = {str(value) for value in failed}
        self.contents = contents or {}
        self.calls = []

    async def call_action(self, action, **params):
        if action != "get_forward_msg":
            raise AssertionError(f"unexpected remote action: {action}")
        reference = params["id"]
        self.calls.append(reference)
        if reference in self.failed:
            raise RuntimeError("synthetic recoverable read failure")
        return {"messages": [{"message": self.contents.get(reference, [text("needle " + reference)])}]}


class ForwardRotationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = HistoryRepository(Path(self.directory.name) / "synthetic.db")
        self.addCleanup(self.repo.close)
        with patch.object(service_module, "HistoryRepository", return_value=self.repo):
            self.service = service_module.QQChatHistoryService()
        self.service.self_id = "999999"
        self.service._head_fill = AsyncMock(return_value=service_module.SyncResult("head", 0, 0, "caught_up"))

    async def query(self, bot, group="123", keywords=None):
        before = len(bot.calls)
        result = await self.service.get_messages(
            bot, group, start_time=BASE + 1, end_time=BASE + 200,
            keywords=keywords if keywords is not None else ["needle"],
        )
        calls = bot.calls[before:]
        self.assertLessEqual(len(calls), 3)
        return result, calls

    async def test_three_latest_failures_allow_older_keyword_hit_on_next_query(self):
        self.repo.insert_messages("123", [message(number) for number in range(1, 5)])
        bot = ForwardBot(failed=[4, 3, 2])
        first, calls = await self.query(bot)
        self.assertEqual(calls, ["4", "3", "2"])
        self.assertEqual(first.returned_count, 0)

        second, calls = await self.query(bot)
        self.assertEqual(calls, ["1", "4", "3"])
        self.assertEqual(second.returned_count, 1)
        self.assertIn("needle 1", second.formatted_messages[0])
        self.assertIn("forward:index_bounded", second.stop_reasons)

    async def test_failed_candidates_are_retried_after_the_resource_recovers(self):
        self.repo.insert_messages("123", [message(number) for number in range(1, 5)])
        bot = ForwardBot(failed=[1, 2, 3, 4])
        await self.query(bot)
        bot.failed.clear()

        recovered, calls = await self.query(bot)
        self.assertEqual(calls, ["1", "4", "3"])
        self.assertEqual(recovered.returned_count, 3)
        complete, calls = await self.query(bot)
        self.assertEqual(calls, ["2"])
        self.assertEqual(complete.returned_count, 4)

    async def test_candidate_removal_and_arrival_keep_waiting_ids_in_order(self):
        self.repo.insert_messages("123", [message(number) for number in range(1, 7)])
        bot = ForwardBot(failed=range(1, 8))
        _, calls = await self.query(bot)
        self.assertEqual(calls, ["6", "5", "4"])

        # The next candidate disappears while a newer message arrives.
        self.repo._conn.execute("DELETE FROM messages WHERE group_id = ? AND message_id = ?", ("123", "3"))
        self.repo._conn.commit()
        self.repo.insert_messages("123", [message(7)])
        _, calls = await self.query(bot)
        self.assertEqual(calls, ["2", "1", "6"])
        _, calls = await self.query(bot)
        self.assertEqual(calls, ["5", "4", "7"])

    async def test_rotation_is_isolated_by_connection_and_group(self):
        for group in ("123", "456"):
            self.repo.insert_messages(group, [message(number) for number in range(1, 5)])
        bot = ForwardBot(failed=range(1, 5))
        other = ForwardBot(failed=range(1, 5))

        self.assertEqual((await self.query(bot, "123"))[1], ["4", "3", "2"])
        self.assertEqual((await self.query(bot, "456"))[1], ["4", "3", "2"])
        self.assertEqual((await self.query(other, "123"))[1], ["4", "3", "2"])
        self.assertEqual((await self.query(bot, "123"))[1], ["1", "4", "3"])

    async def test_latest_64_forward_records_remain_the_candidate_boundary(self):
        self.repo.insert_messages("123", [message(number) for number in range(1, 66)])
        bot = ForwardBot(failed=range(2, 66))
        for _ in range(22):
            result, _ = await self.query(bot)
            self.assertEqual(result.returned_count, 0)
        self.assertEqual(set(bot.calls), {str(number) for number in range(2, 66)})
        self.assertNotIn("1", bot.calls)
        self.assertEqual(len(self.service._forward_index_rotation[(id(bot), "123")]), 64)

    async def test_nested_reads_and_result_display_share_budget_without_skipping_waiter(self):
        self.repo.insert_messages("123", [
            message(1, [text("needle in visible message"), forward("waiting")]),
            message(2, [forward("outer")]),
        ])
        bot = ForwardBot(contents={
            "outer": [forward("nested-a"), forward("nested-b")],
            "nested-a": [text("unrelated a")],
            "nested-b": [text("unrelated b")],
            "waiting": [text("needle now expanded")],
        })
        first, calls = await self.query(bot)
        self.assertEqual(calls, ["outer", "nested-a", "nested-b"])
        self.assertEqual(first.returned_count, 1)
        self.assertNotIn("needle now expanded", first.formatted_messages[0])
        self.assertIn("读取上限", first.formatted_messages[0])

        second, calls = await self.query(bot)
        self.assertEqual(calls, ["waiting"])
        self.assertIn("needle now expanded", second.formatted_messages[0])

    async def test_rotation_retains_a_bounded_number_of_connection_groups(self):
        bot = ForwardBot(failed=[1])
        for number in range(129):
            group = str(number)
            self.repo.insert_messages(group, [message(1)])
            await self.query(bot, group)
        self.assertEqual(len(self.service._forward_index_rotation), 128)
        self.assertNotIn((id(bot), "0"), self.service._forward_index_rotation)
        self.assertIn((id(bot), "128"), self.service._forward_index_rotation)


if __name__ == "__main__":
    unittest.main(verbosity=2)
