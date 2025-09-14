"""
wikitext_cleaner 模块的单元测试。
该文件确保清理逻辑的健壮性和符合预期的行为。
"""

import unittest
from core.wikitext_cleaner import clean


class TestWikitextCleaner(unittest.TestCase):
    """wikitext 清理函数的测试用例。"""

    def test_clean_removes_div_tags(self):
        """测试清理函数是否移除 <div> 标签。"""
        wikitext = "<div style='color:red;'>Text inside div</div> More text."
        expected = "Text inside div More text."
        self.assertEqual(clean(wikitext), expected)

    def test_clean_removes_br_tags(self):
        """测试清理函数是否将 <br/> 标签替换为换行符。"""
        wikitext = "Line 1<br/>Line 2<br />Line 3"
        expected = "Line 1\nLine 2\nLine 3"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_removes_page_level_templates(self):
        """测试清理函数是否移除页面级模板。"""
        wikitext = "Some text {{原神TOP}} and {{背景图片|file.jpg}} more text."
        expected = "Some text  and  more text."
        self.assertEqual(clean(wikitext), expected)

    def test_clean_unwraps_color_templates(self):
        """测试清理函数是否展开颜色模板。"""
        wikitext = "Text with {{color|red|colored text}} and {{genshincolor|冰|冰元素文本}}."
        expected = "Text with colored text and 冰元素文本."
        self.assertEqual(clean(wikitext), expected)

    def test_clean_standardizes_separators(self):
        """测试清理函数是否标准化数据分隔符。"""
        wikitext = "Value1{{!!}}Value2{{!!}}Value3"
        expected = "Value1, Value2, Value3"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_simplifies_links(self):
        """测试清理函数是否简化文件和视频链接。"""
        wikitext = "See [[File:image.png|thumb|desc]] and {{BilibiliVideo|id=12345}}."
        expected = "See [图片: image.png] and [Bilibili视频: https://www.bilibili.com/video/12345]."
        self.assertEqual(clean(wikitext), expected)

    def test_clean_preserves_structural_templates(self):
        """测试清理函数是否保留结构化模板（它们应在模板解析阶段被处理）。"""
        wikitext = "Text before {{GenshinChara|本名=甘雨}} text after."
        # 清理函数不应修改未被模板解析器处理的模板
        # 它们应该保持原样，等待模板解析器处理
        self.assertEqual(clean(wikitext), wikitext)

    def test_clean_preserves_tables(self):
        """测试清理函数是否保留 Wikitext 表格（它们是结构化数据）。"""
        wikitext = "{| class=\"wikitable\"\n|-\n! Header 1 !! Header 2\n|-\n| Cell 1 || Cell 2\n|}"
        # 清理函数不应移除表格
        self.assertEqual(clean(wikitext), wikitext)

    def test_clean_converts_simple_heading(self):
        """测试简单标题的转换。"""
        wikitext = "==Simple Heading=="
        expected = "## Simple Heading"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_converts_multiple_headings(self):
        """测试多个不同级别标题的转换。"""
        wikitext = "= Heading 1 =\n== Heading 2 ==\n=== Heading 3 ==="
        expected = "# Heading 1\n## Heading 2\n### Heading 3"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_converts_wikilink_with_text(self):
        """测试带有自定义文本的 wikilink 的转换。"""
        wikitext = "[[LinkTarget|Link Text]]"
        expected = "[Link Text](LinkTarget)"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_converts_wikilink_without_text(self):
        """测试不带自定义文本的 wikilink 的转换。"""
        wikitext = "[[LinkTarget]]"
        expected = "[LinkTarget](LinkTarget)"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_converts_external_link_with_text(self):
        """测试带有文本的外部链接的转换。"""
        wikitext = "[https://example.com Example Text]"
        expected = "[Example Text](https://example.com)"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_converts_external_link_without_text(self):
        """测试不带文本的外部链接的转换。"""
        wikitext = "[https://example.com]"
        expected = "https://example.com"
        self.assertEqual(clean(wikitext), expected)

    def test_clean_handles_plain_text(self):
        """测试纯文本是否按原样传递。"""
        wikitext = "This is just some plain text."
        expected = "This is just some plain text."
        self.assertEqual(clean(wikitext), expected)

    def test_clean_handles_empty_string(self):
        """测试空字符串的转换。"""
        self.assertEqual(clean(""), "")

    def test_clean_handles_none_input(self):
        """测试 None 输入的处理。"""
        self.assertEqual(clean(None), "")


if __name__ == '__main__':
    unittest.main()