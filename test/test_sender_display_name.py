"""发送者显示名（群名片优先）回归测试。

AngelHeart 侧消息记录的显示名规则是「群名片 card 优先，其次 QQ 昵称 nickname」。
AngelEye 作为同源下游历史查询，必须对齐该规则，否则同一群同一人在两边显示名不一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from astrbot_plugin_angel_eye.core.formatter import format_unified_message
from astrbot_plugin_angel_eye.services.history_repository import HistoryRepository

GROUP_ID = "10000"
END_TIME = 4102444800  # 2100-01-01


def _make_message(message_id: str, sender: dict, text: str = "你好", ts: int = 1700000000) -> dict:
    return {
        "message_id": message_id,
        "time": ts,
        "sender": sender,
        "message": [{"type": "text", "data": {"text": text}}],
    }


class TestFormatterSenderName:
    """format_unified_message 的发送者显示名规则。"""

    def test_card_preferred_over_nickname(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "群名片"})
        out = format_unified_message(msg, self_id="bot")
        assert "群名片" in out
        assert "QQ昵称" not in out

    def test_fallback_to_nickname_when_card_empty(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": ""})
        out = format_unified_message(msg, self_id="bot")
        assert "QQ昵称" in out

    def test_fallback_to_nickname_when_card_missing(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "QQ昵称"})
        out = format_unified_message(msg, self_id="bot")
        assert "QQ昵称" in out

    def test_unknown_when_both_blank(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "", "card": ""})
        out = format_unified_message(msg, self_id="bot")
        assert "未知用户" in out


class TestSendersTableStoresCard:
    """insert_messages 把显示名写进 senders 表（不再是 messages.nickname 列）。"""

    def test_senders_takes_card(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "群名片"})],
            )
            row = repo._conn.execute(
                "SELECT nickname FROM senders WHERE user_id = 'u1'"
            ).fetchone()
            assert row["nickname"] == "群名片"
        finally:
            repo.close()

    def test_senders_falls_back_to_nickname(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": ""})],
            )
            row = repo._conn.execute(
                "SELECT nickname FROM senders WHERE user_id = 'u1'"
            ).fetchone()
            assert row["nickname"] == "QQ昵称"
        finally:
            repo.close()


class TestSearchIgnoresSenderName:
    """检索只走正文：发送者昵称不再进关键词索引。"""

    def test_nickname_is_not_searchable(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "草神"},
                               text="今天吃什么")],
            )
            rows = repo.query_messages(
                group_id=GROUP_ID,
                start_time=0,
                end_time=END_TIME,
                keywords=["草神"],
                user_ids=None,
                limit=10,
            )
            assert rows == []
        finally:
            repo.close()

    def test_body_text_is_searchable(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "无关名片"},
                               text="草神复刻了")],
            )
            rows = repo.query_messages(
                group_id=GROUP_ID,
                start_time=0,
                end_time=END_TIME,
                keywords=["草神"],
                user_ids=None,
                limit=10,
            )
            assert len(rows) == 1
            assert rows[0]["message_id"] == "m1"
            assert "草神" in rows[0]["body"]
        finally:
            repo.close()
