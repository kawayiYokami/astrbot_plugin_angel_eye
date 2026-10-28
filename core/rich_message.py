"""Bounded, offline rendering of the content actually present in QQ segments."""
from __future__ import annotations

import html
import ipaddress
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

MAX_INPUT = 131072
MAX_OUTPUT = 12000
MAX_SEGMENTS = 80
MAX_DEPTH = 3
TEXT_KEYS = ("title", "desc", "summary", "content", "text", "source", "tag")
LINK_KEYS = ("url", "jumpUrl", "qqdocurl", "newsUrl", "legacyUrl", "pcJumpUrl", "sourceUrl")
IMAGE_KEYS = ("preview", "image", "imageUrl", "cover", "coverUrl", "picUrl")


def _text(value, limit=2000):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value)).strip()
    return value[:limit] + ("…[文本已截断]" if len(value) > limit else "")


def public_url(value):
    """Preserve usable web resource queries; never expose local paths as URLs.

    This function performs no requests or DNS lookup. Consumers must enforce
    their own network policy when retrieving a resource.
    """
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    value = html.unescape(value.strip())
    if any(c.isspace() or ord(c) < 32 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() not in ("https", "http") or not host:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        if parsed.port is not None and not 0 < parsed.port < 65536:
            return ""
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"[0-9.]+", host) or host.startswith("0x") or "." not in host:
                return ""
        else:
            if not address.is_global:
                return ""
    except (ValueError, UnicodeError):
        return ""
    return value


def _join(parts, limit=MAX_OUTPUT):
    result = []
    length = 0
    for part in parts:
        if not part:
            continue
        if length + len(part) + 1 > limit:
            result.append("[内容较长，余下部分未展开]")
            break
        result.append(part)
        length += len(part) + 1
    return "\n".join(result)


def _payload(value):
    if isinstance(value, dict) and set(value) == {"data"}:
        value = value["data"]
    if isinstance(value, str):
        if len(value) > MAX_INPUT:
            return None
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return None
    return value if isinstance(value, dict) else None


def extract_json_card(data):
    card = _payload(data)
    if card is None:
        return "[JSON卡片：原文格式无效或过长]"
    lines = ["[JSON卡片：以下为消息自带内容]"]
    seen_text, seen_urls = set(), set()

    def emit_text(label, value):
        value = _text(value)
        if value and value not in seen_text:
            seen_text.add(value)
            lines.append(f"{label}：{value}")

    def emit_url(label, value):
        value = public_url(value)
        if value and value not in seen_urls:
            seen_urls.add(value)
            lines.append(f"{label}：{value}")

    # Deliberately ignore config, tokens, authorization, arbitrary extra trees,
    # app-launch schemes and source-logo images.
    emit_text("应用", card.get("app"))
    emit_text("提示", card.get("prompt"))
    fields = [card]
    meta = card.get("meta")
    if isinstance(meta, dict):
        for value in list(meta.values())[:12]:
            if isinstance(value, dict):
                fields.append(value)
            elif isinstance(value, list):
                fields.extend(x for x in value[:6] if isinstance(x, dict))
    for item in fields[:20]:
        for key in TEXT_KEYS:
            emit_text({"title": "标题", "source": "来源", "tag": "类别"}.get(key, "文字"), item.get(key))
        for key in LINK_KEYS:
            emit_url("链接", item.get(key))
        for key in IMAGE_KEYS:
            emit_url("预览图", item.get(key))
    if len(lines) == 1:
        lines.append("[卡片未提供可读文字或网页资源]")
    return _join(lines)


def extract_xml_card(data):
    value = data.get("data", data.get("xml", "")) if isinstance(data, dict) else data
    if not isinstance(value, str) or len(value) > MAX_INPUT:
        return "[XML卡片：原文格式无效或过长]"
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", value, re.I):
        return "[XML卡片：未解析含实体声明的内容]"
    try:
        root = ET.fromstring(value)
    except (ET.ParseError, ValueError, RecursionError):
        return "[XML卡片：原文解析失败]"
    lines, seen = ["[XML卡片：以下为消息自带内容]"], set()
    for node in list(root.iter())[:60]:
        tag = str(node.tag).split("}")[-1].lower()
        candidates = []
        if tag in ("title", "summary", "desc", "text", "content"):
            candidates.append(_text(node.text))
        for key in ("url", "jumpurl", "href"):
            url = public_url(node.attrib.get(key))
            if url:
                candidates.append("链接：" + url)
        for key in ("cover", "preview", "src"):
            url = public_url(node.attrib.get(key))
            if url:
                candidates.append("预览图：" + url)
        if tag == "source":
            candidates.append(_text(node.attrib.get("name")))
        for item in candidates:
            if item and item not in seen:
                seen.add(item)
                lines.append(item)
    if len(lines) == 1:
        lines.append("[卡片未提供可读文字或网页资源]")
    return _join(lines)


def _filename(value):
    if not isinstance(value, str) or value.startswith(("http://", "https://", "base64://", "data:")):
        return ""
    return _text(value.replace("\\", "/").rsplit("/", 1)[-1], 180)


def format_rich_segment(segment, depth=0):
    if not isinstance(segment, dict):
        return ""
    kind = str(segment.get("type", "")).lower()
    data = segment.get("data")
    data = data if isinstance(data, dict) else {}
    if kind in ("text", "plain"):
        return _text(data.get("text"), MAX_OUTPUT)
    if kind == "json":
        return extract_json_card(data.get("data", data))
    if kind == "xml":
        return extract_xml_card(data)
    if kind == "markdown":
        value = data.get("content", data.get("markdown", data.get("text", "")))
        if isinstance(value, dict):
            value = value.get("content", "")
        return _text(value, MAX_OUTPUT) or "[Markdown消息：未提供正文]"
    if kind in ("forward", "node"):
        if depth >= MAX_DEPTH:
            return "[合并转发：达到展开深度上限]"
        nodes = data.get("content", data.get("messages", data.get("nodes")))
        if kind == "node":
            nodes = [data]
        if isinstance(nodes, list) and nodes:
            lines = ["[合并转发：以下为转发内容]"]
            for i, node in enumerate(nodes[:20], 1):
                if not isinstance(node, dict):
                    continue
                node = node.get("data", {}) if node.get("type") == "node" else node
                if not isinstance(node, dict):
                    continue
                chain = node.get("message", node.get("content", []))
                if isinstance(chain, str):
                    text = _text(chain, 4000)
                elif isinstance(chain, list):
                    text = extract_rich_segments(chain, depth + 1)
                else:
                    text = ""
                if text:
                    lines.append(f"[转发条目{i}]\n{text}")
            if len(nodes) > 20 or data.get("_content_truncated"):
                lines.append("[仅展开前20条转发内容]")
            if len(lines) == 1:
                lines.append("[转发条目未提供可读内容]")
            return _join(lines)
        reference = _text(data.get("id", data.get("message_id", "")), 256)
        status = {"timeout": "读取超时", "failed": "读取失败", "budget": "本次读取上限"}.get(data.get("_content_status"), "尚未展开")
        return f"[合并转发：{status}" + (f"，资源ID={reference}" if reference else "") + "]"
    if kind in ("image", "record", "video", "file"):
        label = {"image": "图片", "record": "语音", "video": "视频", "file": "文件"}[kind]
        lines = [f"[{label}]"]
        if kind == "image":
            summary = _text(data.get("summary"), 500)
            if summary:
                lines.append("摘要：" + summary)
        text = _text(data.get("text"), 2000)
        if text:
            lines.append("附带文本：" + text)
        name = _filename(data.get("name", data.get("file_name", data.get("file", ""))))
        if name and kind != "image":
            lines.append("名称：" + name)
        size = data.get("size", data.get("file_size"))
        if isinstance(size, str) and size.isdigit() and len(size) < 20:
            size = int(size)
        if isinstance(size, (int, float)) and not isinstance(size, bool) and size >= 0:
            lines.append(f"大小：{size} 字节")
        duration = data.get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
            lines.append(f"时长：{duration} 秒")
        url = public_url(data.get("url")) or public_url(data.get("file"))
        if url:
            lines.append("资源链接：" + url)
        else:
            reference = _text(data.get("file_id", data.get("id", "")), 256)
            if reference:
                lines.append("资源ID：" + reference)
            lines.append("[消息未提供可直接读取的网页资源地址]")
        return _join(lines)
    if kind == "face":
        return "[表情:" + _text(data.get("id", "?"), 32) + "]"
    if kind == "reply":
        return "[回复]"
    if kind == "at":
        target = _text(data.get("qq", "?"), 64)
        return "[@全体成员]" if target == "all" else f"[@{target}]"
    lines = []
    for key in ("title", "text", "content", "desc", "summary", "name"):
        value = _text(data.get(key))
        if value and value not in lines:
            lines.append(value)
    for key in ("url", "image", "audio", "video"):
        value = public_url(data.get(key))
        if value:
            lines.append("资源链接：" + value)
    if lines:
        return _join([f"[{_text(kind, 40) or '其他消息'}]", *lines])
    return f"[{_text(kind, 40) or '未知消息类型'}：未提供可读内容]"


def extract_rich_segments(chain, depth=0):
    if not isinstance(chain, list):
        return ""
    return _join(format_rich_segment(segment, depth) for segment in chain[:MAX_SEGMENTS])
