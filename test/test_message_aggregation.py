"""连续同一发送者发言聚合的回归测试。"""

from __future__ import annotations

import pytest

from astrbot_plugin_angel_eye.core.formatter import (
    AGGREGATE_WINDOW_SECONDS,
    format_message_blocks,
    format_unified_message,
)

BASE_TIME = 1700000000


def message(user_id, text, offset=0, role="member", nickname=None):
    return {
        "message_id": f"m-{user_id}-{offset}",
        "time": BASE_TIME + offset,
        "sender": {"user_id": user_id, "nickname": nickname or f"成员{user_id}", "role": role},
        "message": [{"type": "text", "data": {"text": text}}],
    }


def test_consecutive_messages_from_same_sender_share_one_header():
    blocks, _ = format_message_blocks([
        message("u1", "第一句"),
        message("u1", "第二句", offset=10),
        message("u1", "第三句", offset=20),
    ])
    assert len(blocks) == 1
    assert blocks[0].count("[群友:") == 1
    assert "第一句" in blocks[0]
    assert "第二句" in blocks[0]
    assert "第三句" in blocks[0]


def test_header_uses_first_message_time():
    blocks, _ = format_message_blocks([
        message("u1", "先说的", offset=0),
        message("u1", "后说的", offset=120),
    ])
    assert len(blocks) == 1
    assert "(2023-11-15 06:13)" in blocks[0]


def test_another_sender_breaks_the_block():
    blocks, _ = format_message_blocks([
        message("u1", "甲的第一句"),
        message("u2", "乙插话"),
        message("u1", "甲的第二句", offset=20),
    ])
    assert len(blocks) == 3
    assert "甲的第一句" in blocks[0]
    assert "乙插话" in blocks[1]
    assert "甲的第二句" in blocks[2]


def test_gap_beyond_window_breaks_the_block():
    """连续但隔得太久，各自成块，抬头各带各的时间。"""
    blocks, _ = format_message_blocks([
        message("u1", "早上说的"),
        message("u1", "晚上接着说", offset=AGGREGATE_WINDOW_SECONDS + 1),
    ])
    assert len(blocks) == 2
    assert "早上说的" in blocks[0]
    assert "晚上接着说" in blocks[1]


def test_gap_at_window_boundary_still_aggregates():
    blocks, _ = format_message_blocks([
        message("u1", "早先说的"),
        message("u1", "刚好到边界", offset=AGGREGATE_WINDOW_SECONDS),
    ])
    assert len(blocks) == 1


def test_order_is_preserved_inside_the_block():
    blocks, count = format_message_blocks([
        message("u1", "一", offset=0),
        message("u1", "二", offset=100),
        message("u1", "三", offset=200),
    ])
    assert len(blocks) == 1
    assert count == 3
    assert blocks[0].index("一") < blocks[0].index("二") < blocks[0].index("三")


def test_out_of_order_timestamps_do_not_get_merged():
    """时间倒退的消息不并入前一块，避免把顺序拉平。"""
    blocks, count = format_message_blocks([
        message("u1", "先到的", offset=30),
        message("u1", "时间倒退的", offset=10),
    ])
    assert count == 2
    assert len(blocks) == 2


def test_order_is_preserved_across_blocks():
    blocks, count = format_message_blocks([
        message("u1", "甲一", offset=0),
        message("u2", "乙一", offset=10),
        message("u1", "甲二", offset=20),
    ])
    assert count == 3
    assert len(blocks) == 3
    assert "甲一" in blocks[0]
    assert "乙一" in blocks[1]
    assert "甲二" in blocks[2]


def test_role_label_comes_from_sender_role():
    blocks, _ = format_message_blocks([
        message("u1", "我是群主", role="owner"),
        message("u2", "我是管理", offset=10, role="admin"),
        message("u3", "我是群友", offset=20, role="member"),
    ])
    assert blocks[0].startswith("[群主: ")
    assert blocks[1].startswith("[管理: ")
    assert blocks[2].startswith("[群友: ")


def test_unknown_role_falls_back_to_member():
    blocks, _ = format_message_blocks([message("u1", "身份未知", role="guest")])
    assert blocks[0].startswith("[群友: ")


def test_bot_own_message_uses_assistant_label():
    blocks, _ = format_message_blocks([message("bot", "我自己说的")], self_id="bot")
    assert blocks[0].startswith("[助理: ")


def test_empty_body_message_is_skipped():
    blank = {
        "message_id": "m-blank",
        "time": BASE_TIME,
        "sender": {"user_id": "u1", "nickname": "成员u1", "role": "member"},
        "message": [],
    }
    blocks, _ = format_message_blocks([blank, message("u1", "有正文", offset=10)])
    assert len(blocks) == 1
    assert "有正文" in blocks[0]


def test_single_message_matches_unified_renderer():
    row = message("u1", "单条")
    blocks, count = format_message_blocks([row])
    assert blocks == [format_unified_message(row)]
    assert count == 1
