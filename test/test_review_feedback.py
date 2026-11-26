"""Synthetic PR feedback regressions; no providers, QQ or remote resources."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from astrbot.api.message_components import Json, Plain, Reply
from astrbot.core.provider.entities import ProviderRequest

from astrbot_plugin_angel_eye.core.event_enrichment import enrich_event, enrich_request
from astrbot_plugin_angel_eye.core.rich_message import (
    extract_json_card,
    extract_xml_card,
    format_reply_segment,
)
from astrbot_plugin_angel_eye.services.history_repository import HistoryRepository
from astrbot_plugin_angel_eye.services.rich_content_service import RichContentResolver


class Event:
    def __init__(self, *chain):
        self.message_str = "request"
        self.message_obj = SimpleNamespace(
            message=list(chain), message_str=self.message_str, raw_message={},
        )
        self.unified_msg_origin = "test:GroupMessage:100"
        self.bot = None

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "100"

    def get_messages(self):
        return self.message_obj.message


@pytest.mark.parametrize("value", ["000123", "-000123"])
def test_reply_target_keeps_validated_original_digits(value):
    assert format_reply_segment({"id": value}) == f"[回复消息ID:{value}]"


@pytest.mark.parametrize("query", [
    "id=7&token=SYNTHETIC_SECRET&n=2",
    "id=7&%74oken=SYNTHETIC_SECRET&n=2",
    "id=7&X-Amz-Signature=SYNTHETIC_SECRET&n=2",
    "id=7&api_key=SYNTHETIC_SECRET&n=2",
    "id=7&jwt=SYNTHETIC_SECRET&n=2",
    "id=7&access_key=SYNTHETIC_SECRET&n=2",
    "id=7&%6aWt=SYNTHETIC_SECRET&n=2",
    "id=7&Access-Key=SYNTHETIC_SECRET&n=2",
    "id=7;rkey=SYNTHETIC_SECRET;n=2",
])
def test_json_and_xml_card_links_drop_credential_fields_only(query):
    url = "https://example.org/story?" + query
    card = {"url": url}
    original = copy.deepcopy(card)
    json_text = extract_json_card(card)
    xml_text = extract_xml_card('<msg url="' + url.replace("&", "&amp;") + '"/>')
    for text in (json_text, xml_text):
        assert "SYNTHETIC_SECRET" not in text
        assert "https://example.org/story?" + ("id=7;n=2" if ";" in query else "id=7&n=2") in text
    assert card == original


@pytest.mark.parametrize("fragment,expected", [
    ("token=SYNTHETIC_SECRET&tab=2", "tab=2"),
    ("jwt=SYNTHETIC_SECRET&tab=2", "tab=2"),
    ("/view?access_key=SYNTHETIC_SECRET&tab=2", "/view?tab=2"),
    ("/view?signature=SYNTHETIC_SECRET&tab=2", "/view?tab=2"),
    ("/view?tab=2", "/view?tab=2"),
])
def test_card_fragment_credentials_are_not_sent_as_metadata(fragment, expected):
    text = extract_json_card({"url": "https://example.org/story#" + fragment})
    assert "SYNTHETIC_SECRET" not in text
    assert "https://example.org/story#" + expected in text


@pytest.mark.parametrize("credential_key", ["token", "jwt", "access_key"])
def test_credential_values_are_absent_from_new_keyword_index(tmp_path, credential_key):
    card = {"title": "synthetic card", "url": f"https://example.org/story?id=7&{credential_key}=SYNTHETIC_SECRET"}
    message = {
        "message_id": "101", "time": 100,
        "sender": {"user_id": "200", "nickname": "member"},
        "message": [{"type": "json", "data": {"data": json.dumps(card)}}],
    }
    original = copy.deepcopy(message)
    repo = HistoryRepository(db_path=tmp_path / "synthetic.db")
    try:
        repo.insert_messages("100", [message])
        assert repo.query_messages("100", 90, 110, ["SYNTHETIC_SECRET"], None, 10) == []
        assert len(repo.query_messages("100", 90, 110, ["id=7"], None, 10)) == 1
        assert repo.query_messages("100", 90, 110, None, None, 10) == [original]
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_key", ["token", "jwt", "access_key"])
async def test_card_credentials_never_enter_custom_request_and_input_is_retained(credential_key):
    card = {"title": "synthetic card", "url": f"https://example.org/story?id=7&{credential_key}=SYNTHETIC_SECRET"}
    component = Json(data=json.dumps(card))
    event = Event(component)
    request = ProviderRequest(prompt="rewritten request")
    await enrich_request(event, request, RichContentResolver())
    assert "id=7" in request.prompt and "SYNTHETIC_SECRET" not in request.prompt
    assert "SYNTHETIC_SECRET" not in event.message_str
    assert component.data == card


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["000123", "-456"])
async def test_native_quote_target_survives_custom_request_without_target_body(target):
    quote = Reply(id=target, chain=[], message_str="", text="")
    event = Event(Plain(text="request"), quote)
    resolver = RichContentResolver()
    request = ProviderRequest(prompt="rewritten request")
    expected = f"[回复消息ID:{target}]"
    await enrich_request(event, request, resolver)
    assert expected in event.message_str
    assert request.prompt.count(expected) == 1
    assert event.get_messages()[1] is quote
    original = list(event.get_messages())
    await enrich_request(event, request, resolver)
    assert event.get_messages() == original
    assert request.prompt.count(expected) == 1


@pytest.mark.asyncio
async def test_invalid_native_quote_target_adds_no_synthetic_relationship():
    event = Event(Plain(text="request"), Reply(id="not-a-message-id", chain=[]))
    assert await enrich_event(event, RichContentResolver()) == []
    assert event.message_str == "request"


@pytest.mark.parametrize("url", [
    "https://example.org/story?x=a;b&n=2",
    "https://example.org/story?x=a;b=2",
    "https://example.org/story#/view?x=a;b",
    "https://example.org/story?x=a%3Bb&n=2",
])
def test_card_links_preserve_noncredential_separators_and_encoding(url):
    assert url in extract_json_card({"url": url})
