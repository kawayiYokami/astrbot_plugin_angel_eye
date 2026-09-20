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

from core.formatter import format_unified_message
from services.history_repository import HistoryRepository

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
        assert "<群名片>" in out
        assert "QQ昵称" not in out

    def test_fallback_to_nickname_when_card_empty(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": ""})
        out = format_unified_message(msg, self_id="bot")
        assert "<QQ昵称>" in out

    def test_fallback_to_nickname_when_card_missing(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "QQ昵称"})
        out = format_unified_message(msg, self_id="bot")
        assert "<QQ昵称>" in out

    def test_unknown_when_both_blank(self):
        msg = _make_message("m1", {"user_id": "u1", "nickname": "", "card": ""})
        out = format_unified_message(msg, self_id="bot")
        assert "<未知用户>" in out


class TestInsertStoresCard:
    """insert_messages 落库的 nickname 列。"""

    def test_nickname_column_takes_card(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "群名片"})],
            )
            row = repo._conn.execute(
                "SELECT nickname FROM messages WHERE message_id = 'm1'"
            ).fetchone()
            assert row["nickname"] == "群名片"
        finally:
            repo.close()

    def test_nickname_column_falls_back_to_nickname(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": ""})],
            )
            row = repo._conn.execute(
                "SELECT nickname FROM messages WHERE message_id = 'm1'"
            ).fetchone()
            assert row["nickname"] == "QQ昵称"
        finally:
            repo.close()


class TestSearchableByCard:
    """search_text 与关键词检索必须能命中群名片。"""

    def test_build_search_text_uses_card(self):
        text = HistoryRepository._build_search_text(
            _make_message("m1", {"user_id": "u9", "nickname": "QQ昵称", "card": "群名片"})
        )
        assert "群名片" in text
        assert "QQ昵称" not in text
        assert "u9" in text

    def test_keyword_search_hits_card(self, tmp_path):
        repo = HistoryRepository(db_path=tmp_path / "qq_history_cache.db")
        try:
            repo.insert_messages(
                GROUP_ID,
                [_make_message("m1", {"user_id": "u1", "nickname": "QQ昵称", "card": "草神"})],
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
        finally:
            repo.close()
