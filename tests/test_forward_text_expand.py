"""转发展开改走正文文本占位：占位识别、文本替换、展开后可搜、多转发不串号。

全部走真实入口（insert_messages / get_messages / query_messages），不直接调
私有替换方法。
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("_angel_eye_under_test")
package.__path__ = [str(ROOT)]
package.__package__ = "_angel_eye_under_test"
sys.modules[package.__name__] = package

from _angel_eye_under_test.services import qq_history_service as service_module
from _angel_eye_under_test.services.history_repository import HistoryRepository

GROUP = "123"
BASE = 1700000000
END_TIME = 4102444800


def text(value):
    return {"type": "text", "data": {"text": value}}


def forward(reference):
    return {"type": "forward", "data": {"id": str(reference)}}


def message(number, chain):
    return {
        "message_id": number,
        "message_seq": number,
        "time": BASE + number,
        "sender": {"user_id": "u1", "nickname": "阿花", "role": "member"},
        "message": chain,
    }


class ForwardBot:
    """只回答 get_forward_msg，记录每次读了哪个资源。"""

    def __init__(self, contents=None, failed=()):
        self.api = self
        self.contents = contents or {}
        self.failed = {str(value) for value in failed}
        self.calls = []

    async def call_action(self, action, **params):
        if action != "get_forward_msg":
            raise AssertionError(f"unexpected remote action: {action}")
        reference = params["id"]
        self.calls.append(reference)
        if reference in self.failed:
            raise RuntimeError("synthetic read failure")
        return {
            "messages": [
                {"message": self.contents.get(reference, [text("needle " + reference)])}
            ]
        }


class ForwardTextExpandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = HistoryRepository(Path(self.directory.name) / "synthetic.db")
        self.addCleanup(self.repo.close)
        with patch.object(service_module, "HistoryRepository", return_value=self.repo):
            self.service = service_module.QQChatHistoryService()
        self.service.self_id = "999999"
        # 查询区间已确认覆盖：先查后补两侧都不缺，本组用例只验正文与展开。
        self.repo.upsert_sync_state(GROUP, covered_from=BASE + 1, covered_to=BASE + 200)

    async def query(self, bot, keywords):
        return await self.service.get_messages(
            bot, GROUP, start_time=BASE + 1, end_time=BASE + 200, keywords=keywords
        )

    def bodies(self):
        rows = self.repo.query_messages(GROUP, 0, END_TIME, None, None, 50)
        return [row["body"] for row in rows]

    async def test_unexpanded_forward_keeps_placeholder_in_body(self):
        """占位识别：未展开的转发入库后，正文里留着带资源 ID 的占位。"""
        self.repo.insert_messages(GROUP, [message(1, [text("看这个"), forward("r1")])])

        body = self.bodies()[0]
        self.assertIn("看这个", body)
        self.assertIn("[合并转发：尚未展开，资源ID=r1]", body)

    async def test_expand_replaces_placeholder_with_forward_detail(self):
        """文本替换：展开后占位换成转发明细，不再含「尚未展开」。"""
        self.repo.insert_messages(GROUP, [message(1, [forward("r1")])])
        bot = ForwardBot(contents={"r1": [text("转发里的第一句")]})

        await self.query(bot, keywords=["转发里的第一句"])

        body = self.bodies()[0]
        self.assertIn("[合并转发：以下为转发内容]", body)
        self.assertIn("转发里的第一句", body)
        self.assertNotIn("尚未展开", body)
        self.assertEqual(bot.calls, ["r1"])

    async def test_forward_content_becomes_searchable_after_expand(self):
        """展开后可搜：转发内容里的关键词，展开前搜不到，展开后搜得到。"""
        self.repo.insert_messages(GROUP, [message(1, [forward("r1")])])
        bot = ForwardBot(contents={"r1": [text("秘密词在这里")]})

        self.assertEqual(
            self.repo.query_messages(GROUP, 0, END_TIME, ["秘密词"], None, 10), []
        )

        result = await self.query(bot, keywords=["秘密词"])
        self.assertEqual(result.returned_count, 1)
        self.assertIn("秘密词", result.formatted_messages[0])

        hits = self.repo.query_messages(GROUP, 0, END_TIME, ["秘密词"], None, 10)
        self.assertEqual(len(hits), 1)

    async def test_two_forwards_in_one_message_are_replaced_without_crosstalk(self):
        """多个转发：一条消息里的两个转发各自替换，顺序不串号。"""
        self.repo.insert_messages(GROUP, [message(1, [forward("a"), forward("b")])])
        bot = ForwardBot(
            contents={"a": [text("AAA 内容")], "b": [text("BBB 内容")]}
        )

        await self.query(bot, keywords=["AAA"])

        body = self.bodies()[0]
        self.assertNotIn("尚未展开", body)
        self.assertIn("AAA 内容", body)
        self.assertIn("BBB 内容", body)
        self.assertLess(body.index("AAA 内容"), body.index("BBB 内容"))
        self.assertEqual(bot.calls, ["a", "b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
