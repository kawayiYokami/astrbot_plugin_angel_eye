"""Synthetic rich-message regressions; no sends, downloads or model calls.

These tests require AstrBot's Python dependencies and API modules, but never
construct a running bot. An isolated package alias loads this checkout even
when its directory has a different name from the installed plugin.
"""

import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("_angel_eye_under_test")
package.__path__ = [str(ROOT)]
package.__package__ = "_angel_eye_under_test"
sys.modules[package.__name__] = package

import asyncio
import copy
import json
import unittest
from types import SimpleNamespace

from astrbot.api.message_components import File, Json, Plain, Reply
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.platform_message_history_mgr import PlatformMessageHistoryManager
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import ProviderRequest

from _angel_eye_under_test.core.event_enrichment import component_segment, enrich_event, enrich_request
from _angel_eye_under_test.core.formatter import format_unified_message
from _angel_eye_under_test.core.rich_message import extract_json_card, extract_rich_segments, extract_xml_card, format_rich_segment, public_url
from _angel_eye_under_test.services.history_repository import HistoryRepository
from _angel_eye_under_test.services.rich_content_service import RichContentResolver


def card():
    return {"app": "com.tencent.miniapp.lua", "prompt": "[分享]空间说说", "meta": {"miniapp": {
        "title": "测试卡片标题", "source": "空间", "preview": "https://cdn.example.org/p.png?signature=keep&n=1",
        "legacyUrl": "https://example.org/story?id=7", "pcJumpUrl": "https://example.org/story?id=7",
        "jumpUrl": "mqqapi://private-launch", "sourcelogo": "https://example.org/logo.png"}},
        "config": {"token": "PRIVATE_CONFIG_SECRET"}, "extra": {"password": "PRIVATE_EXTRA_SECRET"}}


def segment(kind, **data):
    return {"type": kind, "data": data}


class FakeBot:
    def __init__(self, fail=False):
        self.api = self
        self.calls = []
        self.fail = fail

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.fail:
            raise RuntimeError("synthetic unavailable")
        return {"messages": [{"message": [segment("text", text="转发里的真实文字"), segment("json", data=json.dumps(card()))]}]}


class FakeEvent:
    def __init__(self, chain, raw=None, bot=None):
        self.message_obj = AstrBotMessage()
        self.message_obj.message = chain
        self.message_obj.message_str = "".join(c.text for c in chain if isinstance(c, Plain))
        self.message_obj.raw_message = raw or {}
        self.message_str = self.message_obj.message_str
        self.bot = bot
        self.is_at_or_wake_command = False
        self.is_wake = False
        self.unified_msg_origin = "test:GroupMessage:123"

    def get_messages(self):
        return self.message_obj.message

    def get_platform_name(self):
        return "aiocqhttp"

    def get_group_id(self):
        return "123"


class RenderingTests(unittest.TestCase):
    def test_miniapp_preserves_shared_content_and_queries(self):
        text = extract_json_card(card())
        for expected in ("测试卡片标题", "https://example.org/story?id=7", "https://cdn.example.org/p.png?signature=keep&n=1"):
            self.assertIn(expected, text)
        self.assertEqual(text.count("https://example.org/story?id=7"), 1)
        for secret in ("PRIVATE_CONFIG_SECRET", "PRIVATE_EXTRA_SECRET", "mqqapi://", "logo.png"):
            self.assertNotIn(secret, text)

    def test_json_string_and_wrapper(self):
        self.assertIn("测试卡片标题", extract_json_card({"data": json.dumps(card())}))

    def test_malformed_and_oversized_cards_are_explicit(self):
        self.assertIn("格式无效", extract_json_card("not JSON"))
        self.assertIn("过长", extract_json_card("x" * 140000))
        self.assertLess(len(extract_json_card({"text": "x" * 100000})), 13000)

    def test_news_and_detail_cards(self):
        for meta in ({"news": [{"title": "新闻标题", "desc": "新闻内容", "url": "https://example.org/news"}]}, {"detail_1": {"title": "新闻标题", "qqdocurl": "https://example.org/news"}}):
            self.assertIn("新闻标题", extract_json_card({"meta": meta}))

    def test_nonpublic_urls_and_paths_are_not_exposed(self):
        for value in ("file:///etc/passwd", "http://127.0.0.1/x", "http://[::1]/x", "http://10.0.0.1/x", "http://localhost/x", "http://service.internal/x", "https://user:pass@example.org/x", "http://2130706433/x", "javascript:alert(1)", "https://example.org/\nattack"):
            self.assertEqual(public_url(value), "", value)
        self.assertEqual(public_url("https://example.org/i?a=1&amp;b=2"), "https://example.org/i?a=1&b=2")

    def test_xml_title_links_and_no_entities(self):
        text = extract_xml_card({'data': '<msg url="https://example.org/page"><item><title>XML标题</title><summary>摘要</summary><picture cover="https://example.org/p.png"/></item></msg>'})
        self.assertIn("XML标题", text)
        self.assertIn("https://example.org/p.png", text)
        text = extract_xml_card('<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><msg>&x;</msg>')
        self.assertIn("未解析", text)
        self.assertNotIn("file:///", text)

    def test_markdown_content_shapes(self):
        for data in ({"content": "**标题** 正文"}, {"markdown": "**标题** 正文"}, {"markdown": {"content": "**标题** 正文"}}):
            self.assertIn("正文", format_rich_segment({"type": "markdown", "data": data}))

    def test_file_name_size_and_reference(self):
        text = format_rich_segment(segment("file", name="notes.pdf", size="1234", url="https://example.org/notes.pdf"))
        for expected in ("notes.pdf", "1234", "https://example.org/notes.pdf"):
            self.assertIn(expected, text)
        self.assertNotIn("/etc/", format_rich_segment(segment("file", file="/etc/passwd", url="file:///etc/passwd")))

    def test_media_resources_do_not_fabricate_transcripts(self):
        for kind in ("image", "record", "video"):
            text = format_rich_segment(segment(kind, url="https://example.org/media", duration=3, summary="附带摘要"))
            self.assertIn("https://example.org/media", text)
            self.assertNotIn("识别结果", text)
        self.assertIn("未提供", format_rich_segment(segment("record", file="/private/local.amr")))

    def test_inline_forward_nodes_and_depth_limit(self):
        node = segment("node", content=[segment("text", text="转发正文")])
        text = format_rich_segment(segment("forward", content=[node]))
        self.assertIn("转发正文", text)
        deep = segment("forward", content=[node])
        for _ in range(7):
            deep = segment("forward", content=[{"message": [deep]}])
        self.assertIn("深度上限", format_rich_segment(deep))

    def test_unknown_type_preserves_explicit_text_only(self):
        text = format_rich_segment(segment("custom", text="可读文字", token="PRIVATE_CONFIG_SECRET"))
        self.assertIn("可读文字", text)
        self.assertNotIn("PRIVATE_CONFIG_SECRET", text)

    def test_formatter_shows_card_and_original_signed_message_id(self):
        msg = {"sender": {"user_id": 42, "nickname": "测试"}, "message_id": -77, "message": [segment("json", data=json.dumps(card()))]}
        text = format_unified_message(msg)
        self.assertIn("测试卡片标题", text)
        self.assertIn("消息ID:-77", text)
        self.assertNotIn("[json]", text)

    def test_search_index_includes_card_and_complete_plain_text(self):
        text = "正文" * 8000
        msg = {"message": [segment("text", text=text), segment("json", data=card())]}
        search = HistoryRepository._build_search_text(msg)
        self.assertIn(text, search)
        self.assertIn("测试卡片标题", search)
        self.assertNotIn("PRIVATE_CONFIG_SECRET", search)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_forward_resolves_and_caches_read_only_action(self):
        resolver, bot = RichContentResolver(), FakeBot()
        chain = [segment("forward", id="sample-resource")]
        result = await resolver.enrich_chain(bot, chain, scope="A")
        self.assertIn("转发里的真实文字", extract_rich_segments(result))
        self.assertNotIn("content", chain[0]["data"])
        await resolver.enrich_chain(bot, chain, scope="A")
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0][0], "get_forward_msg")

    async def test_forward_cache_does_not_cross_scopes_or_clients(self):
        resolver, bot = RichContentResolver(), FakeBot()
        chain = [segment("forward", id="sample-resource")]
        await resolver.enrich_chain(bot, chain, scope="A")
        await resolver.enrich_chain(bot, chain, scope="B")
        other = FakeBot()
        await resolver.enrich_chain(other, chain, scope="A")
        self.assertEqual((len(bot.calls), len(other.calls)), (2, 1))

    async def test_forward_call_budget_and_explicit_failures(self):
        bot, resolver = FakeBot(fail=True), RichContentResolver()
        result = await resolver.enrich_chain(bot, [segment("forward", id=str(i)) for i in range(7)], scope="A")
        self.assertEqual(len(bot.calls), 3)
        text = extract_rich_segments(result)
        self.assertIn("读取失败", text)
        self.assertIn("读取上限", text)

    async def test_resolver_retains_ordinary_segments_after_budget_window(self):
        chain = [segment("text", text=str(i)) for i in range(100)]
        result = await RichContentResolver().enrich_chain(FakeBot(), chain, scope="A")
        self.assertEqual(result, chain)

    async def test_malformed_forward_payload_does_not_crash(self):
        result = await RichContentResolver().enrich_chain(FakeBot(), [{"type": "forward", "data": "bad"}], scope="A")
        self.assertEqual(len(result), 1)

    async def test_direct_json_reaches_text_without_changing_wake(self):
        event = FakeEvent([Json(data=card())])
        await enrich_event(event, RichContentResolver())
        self.assertIn("测试卡片标题", event.message_str)
        self.assertEqual(event.message_obj.message_str, event.message_str)
        self.assertFalse(event.is_at_or_wake_command)
        self.assertFalse(event.is_wake)
        self.assertIsInstance(event.get_messages()[0], Json)

    async def test_reply_json_has_both_chain_and_text(self):
        event = FakeEvent([Plain(text="请看引用"), Reply(id=-9, chain=[Json(data=card())])])
        await enrich_event(event, RichContentResolver())
        reply = next(c for c in event.get_messages() if isinstance(c, Reply))
        self.assertIn("测试卡片标题", reply.message_str)
        self.assertIn("测试卡片标题", reply.text)
        self.assertTrue(any(isinstance(c, Plain) and "测试卡片标题" in c.text for c in reply.chain))

    async def test_event_enrichment_is_idempotent(self):
        event = FakeEvent([Json(data=card())])
        resolver = RichContentResolver()
        await enrich_event(event, resolver)
        before = event.message_str
        size = len(event.get_messages())
        await enrich_event(event, resolver)
        self.assertEqual(event.message_str, before)
        self.assertEqual(len(event.get_messages()), size)

    async def test_shallow_event_copy_does_not_mutate_parent(self):
        parent = FakeEvent([Json(data=card()), Reply(id=-9, chain=[Json(data=card())])])
        child = copy.copy(parent)
        await enrich_event(child, RichContentResolver())
        self.assertEqual(parent.message_str, "")
        self.assertEqual(len(parent.get_messages()), 2)
        self.assertEqual(parent.get_messages()[1].message_str, "")
        self.assertIn("测试卡片标题", child.message_str)
        await enrich_event(parent, RichContentResolver())
        self.assertIn("测试卡片标题", parent.message_str)

    async def test_plain_messages_are_unchanged(self):
        event = FakeEvent([Plain(text="普通文字 原样保留")])
        original = event.message_obj
        self.assertEqual(await enrich_event(event, RichContentResolver()), [])
        self.assertIs(event.message_obj, original)
        self.assertEqual(event.message_str, "普通文字 原样保留")

    async def test_foreign_raw_group_is_not_used(self):
        event = FakeEvent([Plain(text="本群文字")], raw={"group_id": 999, "message": [segment("json", data=card())]})
        self.assertEqual(await enrich_event(event, RichContentResolver()), [])
        self.assertNotIn("测试卡片标题", event.message_str)

    async def test_native_history_retains_new_plain_content(self):
        saved = []
        class DB:
            async def insert_platform_message_history(self, **kwargs):
                saved.append(kwargs)
        event = FakeEvent([Json(data=card())])
        await enrich_event(event, RichContentResolver())
        await PlatformMessageHistoryManager(DB()).insert_message_chain("test", "test-scope", MessageChain(chain=event.get_messages()), "user")
        self.assertIn("测试卡片标题", json.dumps(saved[0]["content"], ensure_ascii=False))
        self.assertNotIn("PRIVATE_CONFIG_SECRET", json.dumps(saved[0]["content"], ensure_ascii=False))

    async def test_custom_request_gets_content_once(self):
        event, request = FakeEvent([Json(data=card())]), ProviderRequest(prompt="自定义请求")
        resolver = RichContentResolver()
        await enrich_request(event, request, resolver)
        first = request.prompt
        await enrich_request(event, request, resolver)
        self.assertEqual(request.prompt, first)
        self.assertEqual(request.prompt.count("测试卡片标题"), 1)

    async def test_file_metadata_never_uses_synchronous_file_getter(self):
        class TrapFile(File):
            @property
            def file(self):
                raise AssertionError("sync getter must not run")
        file = TrapFile(name="test.pdf", url="https://example.org/test.pdf")
        self.assertEqual(component_segment(file)["data"]["name"], "test.pdf")
        event = FakeEvent([file])
        await enrich_event(event, RichContentResolver())
        self.assertIn("test.pdf", event.message_str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
