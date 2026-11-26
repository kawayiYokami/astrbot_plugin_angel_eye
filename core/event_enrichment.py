"""Expose existing rich message content before native history and model setup."""
from __future__ import annotations

import copy
from collections import Counter

from astrbot.api.message_components import Plain, Reply

from .rich_message import extract_rich_segments, format_reply_segment


VOICE_TRANSCRIPT_PREFIX = "[语音转写（已成功识别）]\n"
QUOTE_BODY_PREFIX = "[引用消息正文]\n"
REPLY_TARGET_PREFIX = "[引用目标]\n"
DIRECT_BLOCK_PREFIXES = (
    "[消息自带内容补充]\n", VOICE_TRANSCRIPT_PREFIX, QUOTE_BODY_PREFIX, REPLY_TARGET_PREFIX,
)
REPLY_RICH_PREFIX = "[引用消息自带内容补充]\n"


def component_segment(component, depth=0):
    """Read stored fields only; in particular, never access File.file."""
    if isinstance(component, dict):
        return component
    values = vars(component)
    kind = values.get("type", getattr(component, "type", ""))
    kind = str(getattr(kind, "value", kind)).lower()
    if kind == "plain":
        kind = "text"
    data = {k: v for k, v in values.items() if k not in ("type", "path", "file_")}
    if kind == "node" and depth < 3:
        data["content"] = [component_segment(item, depth + 1) for item in data.get("content", [])[:20]]
    return {"type": kind, "data": data}


def rich_summary(chain, existing_text=""):
    rich = []
    for segment in chain[:80] if isinstance(chain, list) else []:
        if not isinstance(segment, dict):
            continue
        kind = str(segment.get("type", "")).lower()
        if kind in ("plain", "text", "at", "reply", "face"):
            continue
        text = extract_rich_segments([segment])
        if text and text not in existing_text:
            rich.append(segment)
    return extract_rich_segments(rich)


def _plain_text(chain):
    return "\n".join(c.text for c in chain if isinstance(c, Plain))


def _is_supplement(component):
    return isinstance(component, Plain) and component.text.startswith(
        DIRECT_BLOCK_PREFIXES + (REPLY_RICH_PREFIX,)
    )


def _adapter_segments(raw_chain):
    """Mirror only deterministic adapter transforms before comparing positions.

    OneBot's adapter joins adjacent text segments, strips the joined text and
    skips empty text and mface. Other transforms may depend on API results, so
    mismatches must reject positional inference rather than shift an audio slot.
    """
    result = []
    for index, segment in enumerate(raw_chain):
        if not isinstance(segment, dict):
            return None
        kind = str(segment.get("type", "")).lower()
        data = segment.get("data")
        data = data if isinstance(data, dict) else {}
        if kind in ("text", "plain"):
            value = data.get("text", "")
            if not isinstance(value, str):
                return None
            if result and result[-1]["kind"] == "text" and result[-1]["last"] == index - 1:
                result[-1]["text"] += value
                result[-1]["last"] = index
            else:
                result.append({"kind": "text", "text": value, "indices": [index], "last": index})
        elif kind != "mface":
            result.append({"kind": kind, "text": "", "indices": [index], "last": index})
    for item in result:
        if item["kind"] == "text":
            item["text"] = item["text"].strip()
    return [item for item in result if item["kind"] != "text" or item["text"]]


def _resolved_audio(raw_chain, chain):
    """Identify actual Record -> Plain replacements without raw-index guessing.

    Partial success needs a complete guarded alignment. If another adapter
    transform prevents alignment, only infer transcripts when *all* Records are
    gone and the original plain text is still represented verbatim.
    """
    raw_records = {
        index for index, segment in enumerate(raw_chain)
        if isinstance(segment, dict) and str(segment.get("type", "")).lower() == "record"
    }
    if not raw_records:
        return raw_chain, []
    normalized = [component for component in chain if not _is_supplement(component)]
    adapted = _adapter_segments(raw_chain)
    resolved = set()
    transcripts = []
    aligned = adapted is not None and len(adapted) == len(normalized)
    if aligned:
        for raw_part, component in zip(adapted, normalized):
            kind = component_segment(component)["type"]
            if raw_part["kind"] == "text":
                if not isinstance(component, Plain) or component.text != raw_part["text"]:
                    aligned = False
                    break
            elif raw_part["kind"] == "record" and isinstance(component, Plain) and component.text.strip():
                resolved.update(raw_part["indices"])
                transcripts.append(component.text)
            elif kind != raw_part["kind"]:
                aligned = False
                break
    if not aligned:
        resolved, transcripts = set(), []
        if adapted is not None and not any(component_segment(c)["type"] == "record" for c in normalized):
            expected = Counter(item["text"] for item in adapted if item["kind"] == "text")
            actual = Counter(c.text for c in normalized if isinstance(c, Plain) and c.text.strip())
            # No original text may have disappeared or been rewritten. Remaining
            # Plain components must cover every removed audio slot; otherwise
            # an unconverted/dropped attachment cannot be distinguished safely.
            extra_count = sum((actual - expected).values())
            if not (expected - actual) and extra_count == len(raw_records):
                remaining = actual - expected
                for component in normalized:
                    if isinstance(component, Plain) and remaining[component.text]:
                        transcripts.append(component.text)
                        remaining[component.text] -= 1
                resolved = raw_records
    return [segment for index, segment in enumerate(raw_chain) if index not in resolved], transcripts


async def enrich_event(event, resolver):
    if event.get_platform_name() != "aiocqhttp":
        return []
    original_chain = list(event.get_messages())
    chain = list(original_chain)
    raw = getattr(event.message_obj, "raw_message", None)
    raw_chain = raw.get("message") if hasattr(raw, "get") else None
    group_id = event.get_group_id()
    if group_id and hasattr(raw, "get") and raw.get("group_id") is not None:
        if str(raw.get("group_id")) != str(group_id):
            raw_chain = None
    if not isinstance(raw_chain, list):
        raw_chain = [component_segment(c) for c in original_chain if not isinstance(c, Reply)]
    raw_chain, transcripts = _resolved_audio(raw_chain, chain)
    scope = event.unified_msg_origin
    budget = [3]
    bot = getattr(event, "bot", None)
    if bot is not None:
        raw_chain = await resolver.enrich_chain(bot, raw_chain, scope=scope, budget=budget)
    summary = rich_summary(raw_chain, _plain_text(chain))
    supplements = []
    changed = False
    targets = list(dict.fromkeys(
        format_reply_segment(vars(component))
        for component in original_chain if isinstance(component, Reply)
    ))
    targets = [target for target in targets if target != "[回复]"]
    if targets:
        block = REPLY_TARGET_PREFIX + "\n".join(targets)
        if not any(isinstance(c, Plain) and c.text == block for c in chain):
            chain.append(Plain(text=block))
            changed = True
    if transcripts:
        block = VOICE_TRANSCRIPT_PREFIX + "\n".join(transcripts)
        if not any(isinstance(c, Plain) and c.text == block for c in chain):
            chain.append(Plain(text=block))
            changed = True
    if summary:
        block = "[消息自带内容补充]\n" + summary
        chain.append(Plain(text=block))
        supplements.append(block)
        changed = True
    # A shallow event copy can share its chain while holding an older string.
    # Restore the string from an actual previously added Plain block if needed.
    for i, component in enumerate(list(chain)):
        if not isinstance(component, Reply) or not component.chain:
            continue
        reply_chain = list(component.chain)
        segments = [component_segment(c) for c in reply_chain]
        if bot is not None:
            segments = await resolver.enrich_chain(bot, segments, scope=scope, budget=budget)
        reply_summary = rich_summary(segments, _plain_text(reply_chain))
        if reply_summary:
            block = REPLY_RICH_PREFIX + reply_summary
            reply_chain.append(Plain(text=block))
        else:
            blocks = [c.text for c in reply_chain if isinstance(c, Plain) and c.text.startswith(REPLY_RICH_PREFIX)]
            block = blocks[-1] if blocks else ""
        reply_text = component.message_str or ""
        compatibility_text = getattr(component, "text", "") or ""
        body_parts = [c.text for c in reply_chain if isinstance(c, Plain) and not _is_supplement(c) and c.text.strip()]
        missing_body = [text for text in body_parts if text not in reply_text or text not in compatibility_text]
        if missing_body:
            # A native quoted STT replaces Reply.chain without updating its text
            # fields. The generic label also fits other newly exposed plaintext;
            # the chain alone cannot prove that it originated from speech.
            quote_block = QUOTE_BODY_PREFIX + "\n".join(body_parts)
            if not any(isinstance(c, Plain) and c.text == quote_block for c in chain):
                chain.append(Plain(text=quote_block))
                changed = True
            for text in body_parts:
                if text not in reply_text:
                    reply_text = (reply_text + "\n" + text).strip()
        if block or missing_body:
            # AstrBot components store fields in a shared backing dictionary;
            # copy.copy() alone would mutate the original event's Reply.
            replacement = copy.deepcopy(component)
            replacement.chain = reply_chain
            if block and block not in reply_text:
                reply_text = (reply_text + "\n" + block).strip()
            replacement.message_str = reply_text
            replacement.text = reply_text
            chain[i] = replacement
            changed = True
            if block and block not in supplements:
                supplements.append(block)
    direct_blocks = [c.text for c in chain if isinstance(c, Plain) and c.text.startswith(DIRECT_BLOCK_PREFIXES)]
    current = event.message_str or ""
    for block in direct_blocks:
        if block not in current:
            current = (current + "\n" + block).strip()
        if block not in supplements:
            supplements.append(block)
    if changed or current != event.message_str:
        message_obj = copy.copy(event.message_obj)
        message_obj.message = chain
        message_obj.message_str = current
        event.message_obj = message_obj
        event.message_str = current
    return supplements


async def enrich_request(event, request, resolver):
    blocks = await enrich_event(event, resolver)
    existing = [request.prompt or ""]
    for part in request.extra_user_content_parts or []:
        if isinstance(part, str):
            text = part
        elif isinstance(part, dict):
            text = part.get("text") if part.get("type") == "text" else None
        else:
            text = getattr(part, "text", None)
        if isinstance(text, str):
            existing.append(text)
    missing = [block for block in blocks if not any(block in text for text in existing)]
    if missing:
        request.prompt = ((request.prompt or "") + "\n\n" + "\n\n".join(missing)).strip()
