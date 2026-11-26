"""Supplement deduplication checks actual text parts without changing whitespace."""
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('_angel_eye_under_test')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

from astrbot.api.message_components import Plain
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.provider.entities import ProviderRequest
from _angel_eye_under_test.core.event_enrichment import enrich_request
from _angel_eye_under_test.services.rich_content_service import RichContentResolver


BLOCK = '[消息自带内容补充]\n卡片标题\n第一行正文\n第二行正文'


class Event:
    def __init__(self):
        self.message_obj = AstrBotMessage()
        self.message_obj.message = [Plain(text=BLOCK)]
        self.message_obj.message_str = BLOCK
        self.message_obj.raw_message = {}
        self.message_str = BLOCK
        self.unified_msg_origin = 'synthetic:GroupMessage:123'

    def get_messages(self):
        return self.message_obj.message

    def get_platform_name(self):
        return 'aiocqhttp'

    def get_group_id(self):
        return '123'


class EnrichmentPartsTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_multiline_block_in_each_text_form_is_not_appended(self):
        cases = [
            ('prompt', 'before\n' + BLOCK + '\nafter', []),
            ('string', 'question', ['before\n' + BLOCK + '\nafter']),
            ('dictionary', 'question', [{'type': 'text', 'text': BLOCK}]),
            ('text_object', 'question', [Plain(text=BLOCK)]),
            ('object_text_attribute', 'question', [SimpleNamespace(text=BLOCK)]),
        ]
        for name, prompt, parts in cases:
            with self.subTest(name=name):
                event = Event()
                request = ProviderRequest(prompt=prompt, extra_user_content_parts=parts)
                resolver = RichContentResolver()
                await enrich_request(event, request, resolver)
                await enrich_request(event, request, resolver)
                self.assertEqual(request.prompt, prompt)
                self.assertEqual(request.extra_user_content_parts, parts)

    async def test_nontext_and_incomplete_parts_append_complete_block_once(self):
        head, tail = BLOCK.split('\n', 1)
        cases = [
            ('nontext_dictionary', 'question', [{'type': 'image', 'text': BLOCK}]),
            ('missing_dictionary_type', 'question', [{'text': BLOCK}]),
            ('nested_nontext', 'question', [{'type': 'image_url', 'image_url': {'url': BLOCK}}]),
            ('nonstring_text', 'question', [{'type': 'text', 'text': 123}, SimpleNamespace(text=123)]),
            ('nontext_object', 'question', [SimpleNamespace(url=BLOCK), None]),
            ('partial', 'question', [tail]),
            ('split_parts', 'question', [head + '\n', tail]),
            ('split_prompt_and_part', head + '\n', [tail]),
            ('literal_escaped_newlines', 'question', [BLOCK.replace('\n', '\\n')]),
            ('changed_whitespace', 'question', [BLOCK.replace('\n', ' ')]),
            ('no_parts', 'question', None),
        ]
        for name, prompt, parts in cases:
            with self.subTest(name=name):
                event = Event()
                request = ProviderRequest(prompt=prompt, extra_user_content_parts=parts)
                resolver = RichContentResolver()
                await enrich_request(event, request, resolver)
                first = request.prompt
                self.assertEqual(first, (prompt + '\n\n' + BLOCK).strip())
                await enrich_request(event, request, resolver)
                self.assertEqual(request.prompt, first)
                self.assertEqual(request.prompt.count(BLOCK), 1)
                self.assertEqual(request.extra_user_content_parts, parts)


if __name__ == '__main__':
    unittest.main(verbosity=2)
