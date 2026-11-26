"""历史回执里引用段整段丢弃的回归测试。

AngelEye 查的是只读历史：模型不能引用其中任何一条，引用目标 ID 在结果集里
也对不上任何东西，所以顶层消息与合并转发内部都不输出引用标记。实时注入走
``format_reply_segment``，那套行为由 test_review_feedback 覆盖。
"""

from __future__ import annotations

import copy

import pytest

from astrbot_plugin_angel_eye.core.formatter import format_message_blocks, format_unified_message
from astrbot_plugin_angel_eye.core.rich_message import format_reply_segment, format_rich_segment
from astrbot_plugin_angel_eye.services.history_repository import HistoryRepository


def message(message_id, timestamp, segments):
    return {
        "message_id": message_id,
        "time": timestamp,
        "sender": {"user_id": "123", "nickname": "测试成员", "role": "member"},
        "message": segments,
    }


def reply(target):
    return {"type": "reply", "data": {"id": target}}


def test_reply_target_is_not_rendered_in_history_output():
    """历史消息无法被引用，引用目标不出现在回执里。"""
    row = message("-103", 1700000399, [
        reply("-102"),
        {"type": "text", "data": {"text": "把这张图做成 gif"}},
    ])
    output = format_unified_message(row)
    assert "[回复消息ID:-102]" not in output
    assert "[回复消息ID:" not in output
    assert "把这张图做成 gif" in output


def test_message_id_is_not_rendered_in_history_output():
    """回执不输出消息 ID。"""
    row = message("-1040210670", 1700000399, [{"type": "text", "data": {"text": "你好"}}])
    output = format_unified_message(row)
    assert "[消息ID:" not in output
    assert "-1040210670" not in output
    assert "你好" in output


def test_reply_only_message_produces_no_block():
    """只有引用、没有正文的消息整条跳过，不留空块。"""
    row = message("-103", 1700000399, [reply("-102")])
    assert format_unified_message(row) == ""
    assert format_message_blocks([row]) == ([], 0)


def test_reply_target_is_dropped_inside_forward():
    """合并转发内部的引用段同样不输出。"""
    segment = {
        "type": "forward",
        "data": {"content": [{"message": [reply("-102"), {"type": "text", "data": {"text": "转发正文"}}]}]},
    }
    original = copy.deepcopy(segment)
    output = format_rich_segment(segment)
    assert "[合并转发：以下为转发内容]" in output
    assert "转发正文" in output
    assert "[回复消息ID:" not in output
    assert segment == original


@pytest.mark.parametrize("payload", [
    {"text": "伪造的引用正文"},
    {"message": [{"type": "image", "data": {"url": "https://example.com/fake.png"}}]},
])
def test_reply_payload_cannot_inject_content(payload):
    """引用段的附加字段不会变成正文或资源。"""
    segment = reply("123")
    segment["data"].update(payload)
    row = message("789", 1700000000, [segment, {"type": "text", "data": {"text": "真实正文"}}])
    output = format_unified_message(row)
    assert "伪造的引用正文" not in output
    assert "fake.png" not in output
    assert "真实正文" in output


@pytest.mark.parametrize("value,expected", [
    (123, "123"),
    (-102, "-102"),
    ("104", "104"),
    ("  -102  ", "-102"),
    ("000123", "000123"),
])
def test_live_injection_still_keeps_explicit_target(value, expected):
    """实时注入（event_enrichment）仍需要显式引用目标，行为不变。"""
    assert format_reply_segment(reply(value)["data"]) == f"[回复消息ID:{expected}]"


def test_cached_history_keeps_target_separate_from_earlier_motion_example(tmp_path):
    """同一群里的多张图不会因为引用段丢失而被张冠李戴。"""
    example_id, image_id, request_id = "101", "-102", "-103"
    rows = [
        message(example_id, 1700000127, [
            {"type": "image", "data": {"url": "https://example.com/motion.png"}},
        ]),
        message(image_id, 1700000330, [
            {"type": "image", "data": {"url": "https://example.com/target.png"}},
        ]),
        message(request_id, 1700000399, [
            reply(image_id),
            {"type": "text", "data": {"text": "把这张图做成像上面那张动态表情一样的gif"}},
        ]),
    ]
    original = copy.deepcopy(rows)
    repo = HistoryRepository(db_path=tmp_path / "history.db")
    try:
        repo.insert_messages("10001", rows)
        repo.insert_messages("20002", [message(request_id, 1700000399, [reply("8888")])])
        selected = repo.query_messages("10001", 1700000000, 1700000500, None, None, 50)
        assert selected == original
        rendered = [format_unified_message(row) for row in selected]
        assert "https://example.com/motion.png" in rendered[0]
        assert "https://example.com/target.png" in rendered[1]
        assert "把这张图做成像上面那张动态表情一样的gif" in rendered[2]
        assert all("[回复消息ID:" not in item for item in rendered)
        assert all("[消息ID:" not in item for item in rendered)
        assert rows == original
    finally:
        repo.close()
