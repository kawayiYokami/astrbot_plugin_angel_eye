"""Expose existing rich message content before native history and model setup."""
from __future__ import annotations

import copy

from astrbot.api.message_components import Plain, Reply

from .rich_message import extract_rich_segments


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
    scope = event.unified_msg_origin
    budget = [3]
    bot = getattr(event, "bot", None)
    if bot is not None:
        raw_chain = await resolver.enrich_chain(bot, raw_chain, scope=scope, budget=budget)
    summary = rich_summary(raw_chain, _plain_text(chain))
    supplements = []
    changed = False
    if summary:
        block = "[消息自带内容补充]\n" + summary
        chain.append(Plain(text=block))
        supplements.append(block)
        changed = True
    # A shallow event copy can share its chain while holding an older string.
    # Restore the string from an actual previously added Plain block if needed.
    direct_blocks = [c.text for c in chain if isinstance(c, Plain) and c.text.startswith("[消息自带内容补充]\n")]
    current = event.message_str or ""
    for block in direct_blocks:
        if block not in current:
            current = (current + "\n" + block).strip()
        if block not in supplements:
            supplements.append(block)
    for i, component in enumerate(chain):
        if not isinstance(component, Reply) or not component.chain:
            continue
        reply_chain = list(component.chain)
        segments = [component_segment(c) for c in reply_chain]
        if bot is not None:
            segments = await resolver.enrich_chain(bot, segments, scope=scope, budget=budget)
        reply_summary = rich_summary(segments, _plain_text(reply_chain))
        if reply_summary:
            block = "[引用消息自带内容补充]\n" + reply_summary
            reply_chain.append(Plain(text=block))
        else:
            blocks = [c.text for c in reply_chain if isinstance(c, Plain) and c.text.startswith("[引用消息自带内容补充]\n")]
            block = blocks[-1] if blocks else ""
        if block:
            # AstrBot components store fields in a shared backing dictionary;
            # copy.copy() alone would mutate the original event's Reply.
            replacement = copy.deepcopy(component)
            replacement.chain = reply_chain
            reply_text = component.message_str or ""
            if block not in reply_text:
                reply_text = (reply_text + "\n" + block).strip()
            replacement.message_str = reply_text
            replacement.text = reply_text
            chain[i] = replacement
            changed = True
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
    existing = (request.prompt or "") + str(request.extra_user_content_parts or [])
    missing = [block for block in blocks if block not in existing]
    if missing:
        request.prompt = ((request.prompt or "") + "\n\n" + "\n\n".join(missing)).strip()
