"""正文入库（body-only 存储）回归测试。

存储只留入库时渲染好的正文，抬头不入库、按 sender_id 现拼；关键词检索只走
正文，不再命中发送者昵称。全部断言都走真实入口（insert_messages /
query_messages / format_unified_message），不直接调私有渲染方法。
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("_angel_eye_under_test")
package.__path__ = [str(ROOT)]
package.__package__ = "_angel_eye_under_test"
sys.modules[package.__name__] = package

from _angel_eye_under_test.core.formatter import (
    _content_to_text,
    format_unified_message,
)
from _angel_eye_under_test.services.history_repository import HistoryRepository

GROUP_ID = "10001"
END_TIME = 4102444800


def message(message_id, chain, sender=None, ts=1700000000):
    return {
        "message_id": message_id,
        "message_seq": message_id,
        "time": ts,
        "sender": sender if sender is not None else {"user_id": "u1", "nickname": "阿花", "role": "member"},
        "message": chain,
    }


def text(value):
    return {"type": "text", "data": {"text": value}}


class BodyStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = HistoryRepository(Path(self.directory.name) / "synthetic.db")
        self.addCleanup(self.repo.close)

    def query(self, keywords=None, user_ids=None):
        return self.repo.query_messages(GROUP_ID, 0, END_TIME, keywords, user_ids, 50)

    def test_stored_body_equals_rendered_content_without_header(self):
        """入库渲染：body 与 _content_to_text 逐字相等，且不含抬头。"""
        chain = [
            text("看这张图"),
            {"type": "image", "data": {"url": "https://example.com/a.png"}},
            {"type": "at", "data": {"qq": "123456"}},
        ]
        msg = message("m1", chain, sender={"user_id": "u1", "nickname": "阿花", "role": "member"})
        self.repo.insert_messages(GROUP_ID, [msg])

        rows = self.query()
        self.assertEqual(len(rows), 1)
        body = rows[0]["body"]
        self.assertEqual(body, _content_to_text(msg))
        self.assertIn("看这张图", body)
        self.assertIn("[@123456]", body)
        # 抬头不入库：昵称、角色标签、时间戳都不该出现在正文里
        self.assertNotIn("阿花", body)
        self.assertNotIn("群友", body)
        self.assertNotIn("2023-11-", body)

    def test_keyword_search_reads_body_only_not_nickname(self):
        """搜索只走 body：昵称里有关键词不算命中，正文里有才算。"""
        self.repo.insert_messages(
            GROUP_ID,
            [message("m1", [text("今天天气不错")],
                     sender={"user_id": "u1", "nickname": "花火花火花", "role": "member"})],
        )
        self.repo.insert_messages(
            GROUP_ID,
            [message("m2", [text("花火确实好看")],
                     sender={"user_id": "u2", "nickname": "路人", "role": "member"})],
        )

        hits = self.query(keywords=["花火"])
        self.assertEqual([row["message_id"] for row in hits], ["m2"])
        # 昵称完全不在检索范围：连发消息的人自己都搜不出来
        self.assertEqual(self.query(keywords=["花火花火花"]), [])

    def test_empty_body_is_skipped_and_counted(self):
        """空正文跳过：只含 reply 段的消息不入库，计入 skipped。"""
        result = self.repo.insert_messages(
            GROUP_ID, [message("m1", [{"type": "reply", "data": {"id": "8888"}}])]
        )
        self.assertEqual(result.inserted_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(self.repo.count_messages(GROUP_ID), 0)

        # 同一批里混一条有正文的：只跳空的那条
        mixed = self.repo.insert_messages(
            GROUP_ID,
            [
                message("m2", [{"type": "reply", "data": {"id": "8888"}}]),
                message("m3", [text("有正文")]),
            ],
        )
        self.assertEqual(mixed.inserted_count, 1)
        self.assertEqual(mixed.skipped_count, 1)

    def test_senders_keeps_one_row_with_latest_nickname_and_role(self):
        """senders 更新：同一人只留一行，昵称与 role 取最新。"""
        self.repo.insert_messages(
            GROUP_ID,
            [message("m1", [text("第一句")],
                     sender={"user_id": "u1", "nickname": "旧昵称", "role": "member"})],
        )
        self.repo.insert_messages(
            GROUP_ID,
            [message("m2", [text("第二句")],
                     sender={"user_id": "u1", "nickname": "新昵称", "role": "admin"})],
        )

        rows = self.query()
        self.assertEqual(len(rows), 2)
        # 两条都读到同一行 senders，所以都是最新昵称
        self.assertEqual({row["sender"]["nickname"] for row in rows}, {"新昵称"})
        self.assertEqual({row["sender"]["role"] for row in rows}, {"admin"})
        self.assertEqual(
            self.repo._conn.execute("SELECT COUNT(1) FROM senders").fetchone()[0], 1
        )

    def test_multiple_keywords_match_different_messages_with_or(self):
        """关键词多词：两个词分别命中不同消息，OR 语义返回两条。"""
        self.repo.insert_messages(GROUP_ID, [message("m1", [text("苹果很好吃")])])
        self.repo.insert_messages(GROUP_ID, [message("m2", [text("香蕉也不错")])])

        hits = self.query(keywords=["苹果", "香蕉"])
        self.assertEqual([row["message_id"] for row in hits], ["m1", "m2"])
        self.assertEqual(len(self.query(keywords=["苹果"])), 1)

    def test_header_is_rebuilt_from_senders_for_old_messages(self):
        """抬头现拼：昵称改了之后，旧消息的抬头跟着变。"""
        self.repo.insert_messages(
            GROUP_ID,
            [message("m1", [text("早上好")],
                     sender={"user_id": "u1", "nickname": "旧昵称", "role": "member"})],
        )
        self.repo.insert_messages(
            GROUP_ID,
            [message("m2", [text("晚上好")],
                     sender={"user_id": "u1", "nickname": "旧昵称", "role": "member"})],
        )
        before = [format_unified_message(row, self_id="999999") for row in self.query()]
        self.assertTrue(all("旧昵称" in item for item in before))
        self.assertTrue(all("[群友: 旧昵称 (ID: u1)]" in item for item in before))

        # 同一个人换了昵称与角色后再入库一条
        self.repo.insert_messages(
            GROUP_ID,
            [message("m3", [text("第二天")],
                     sender={"user_id": "u1", "nickname": "新昵称", "role": "owner"})],
        )
        after = [format_unified_message(row, self_id="999999") for row in self.query()]
        self.assertTrue(all("新昵称" in item for item in after))
        self.assertTrue(all("旧昵称" not in item for item in after))
        # role 也跟着现拼：owner -> 群主
        self.assertTrue(all("[群主: 新昵称 (ID: u1)]" in item for item in after))


if __name__ == "__main__":
    unittest.main(verbosity=2)
