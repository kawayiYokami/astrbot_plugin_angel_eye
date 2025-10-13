"""
Wikitext 清理器模块。
提供一个独立的函数，用于清理萌娘百科的 Wikitext，移除视觉噪音并标准化格式。
"""

import re


def clean(wikitext: str) -> str:
    """
    清理 Wikitext，移除纯视觉噪音，但保留核心数据结构。

    Args:
        wikitext: 需要清理的原始 Wikitext 字符串。

    Returns:
        清理和标准化后的字符串。
    """
    if wikitext is None:
        return ""

    cleaned_text = wikitext

    # --- 1. 移除纯视觉 HTML 标签 (保留内容) ---
    # Tags that only affect appearance
    cleaned_text = re.sub(r'</?(poem|del|big|small|u)[^>]*>', '', cleaned_text, flags=re.IGNORECASE)
    # Handle <br/> tags: replace with a newline for better readability
    cleaned_text = re.sub(r'<br\s*/?>', '\n', cleaned_text, flags=re.IGNORECASE)
    # Handle <div> tags: often used for styling, remove the tags themselves
    cleaned_text = re.sub(r'</?div[^>]*>', '', cleaned_text, flags=re.IGNORECASE)

    # --- 2. 移除页面级功能性模板 ---
    # Templates that are not part of the main content
    # 匹配 {{TemplateName}} 或 {{TemplateName|...}} 格式的模板
    page_level_templates = [
        r'\{\{(原神TOP|背景图片|注释|references/|玩梗适度)(\|[^}]*)?\}\}',
    ]
    for pattern in page_level_templates:
        cleaned_text = re.sub(pattern, '', cleaned_text, flags=re.IGNORECASE)

    # --- 3. 移除脚注 ---
    cleaned_text = re.sub(r'<ref[^>]*>.*?</ref>', '', cleaned_text, flags=re.DOTALL)

    # --- 4. 移除/展开内联样式模板 (保留内容) ---
    # {{color|...|text}} -> text
    cleaned_text = re.sub(r'\{\{(?:color|genshincolor)\|[^|}]+?\|([^}]+?)\}\}', r'\1', cleaned_text)
    # {{ruby|text|pronunciation}} -> text(pronunciation)
    cleaned_text = re.sub(r'\{\{ruby\|([^|]+)\|([^}]+)\}\}', r'\1(\2)', cleaned_text)

    # --- 5. 标准化数据分隔符 ---
    # Replace custom separators like {{!!}} with standard ones
    cleaned_text = re.sub(r'\{\{!!\}\}', ', ', cleaned_text)

    # --- 6. 简化文件和外部链接 ---
    # [[File:...]] -> [Image: filename]
    cleaned_text = re.sub(r'\[\[File:([^|\]]+).*?\]\]', r'[图片: \1]', cleaned_text)
    # {{BilibiliVideo|id=...}} -> [Bilibili Video: URL]
    cleaned_text = re.sub(r'\{\{BilibiliVideo\|id=(.*?)\}\}', r'[Bilibili视频: https://www.bilibili.com/video/\1]', cleaned_text)

    # --- 7. 基础语法转换 ---
    # 处理 '''粗体'''
    cleaned_text = re.sub(r"'''(.*?)'''", r"**\1**", cleaned_text)

    # 处理 ''斜体''
    cleaned_text = re.sub(r"''(.*?)''", r"*\1*", cleaned_text)

    # 处理 [[内部链接|链接文本]]
    cleaned_text = re.sub(r"\[\[([^|\]]+?)\|([^\]]+?)\]\]", r"[\2](\1)", cleaned_text)
    # 处理 [[内部链接]]
    cleaned_text = re.sub(r"\[\[([^\]]+?)\]\]", r"[\1](\1)", cleaned_text)

    # 处理 == 标题 ==
    cleaned_text = re.sub(r"^======\s*(.*?)\s*======\s*$", r"###### \1", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"^=====\s*(.*?)\s*=====\s*$", r"##### \1", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"^====\s*(.*?)\s*====\s*$", r"#### \1", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"^===\s*(.*?)\s*===\s*$", r"### \1", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"^==\s*(.*?)\s*==\s*$", r"## \1", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"^=\s*(.*?)\s*=\s*$", r"# \1", cleaned_text, flags=re.MULTILINE)

    # 处理 ;小节标题 (简化处理为加粗)
    cleaned_text = re.sub(r"^;\s*(.*?)\s*$", r"**\1**", cleaned_text, flags=re.MULTILINE)

    # 处理 [http://... 外部链接 文本]
    cleaned_text = re.sub(r"\[(https?://[^\s\]]+)\s+([^\]]+?)]", "[\\2](\\1)", cleaned_text)
    # 处理 [http://... 外部链接]
    cleaned_text = re.sub(r"\[(https?://[^\s\]]+)]", "\\1", cleaned_text)

    # 处理 <del>删除线</del>
    cleaned_text = re.sub(r"<del>(.*?)</del>", r"~~\1~~", cleaned_text)

    # --- Final Cleanup ---
    # Consolidate multiple newlines
    cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
    # Strip leading/trailing whitespace
    cleaned_text = cleaned_text.strip()

    return cleaned_text