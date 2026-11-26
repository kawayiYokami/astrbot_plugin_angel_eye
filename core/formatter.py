"""
Angel Eye 插件 - 消息格式化工具
提供将原始QQ消息对象格式化为可读文本的功能

历史记录是只读回执：模型不能引用其中任何一条，所以这里不输出消息 ID，也不
输出引用目标标记——那些 ID 在结果集里对不上任何东西，只会让模型照着找。
相邻同一发送者的发言并成一块，抬头只出一次。
"""
from __future__ import annotations
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from .rich_message import format_rich_segment

# 定义北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))

# 群成员角色标签，与上游 OneBot 的 sender.role 取值对应。
SENDER_ROLE_LABELS = {
    "owner": "群主",
    "admin": "管理",
    "member": "群友",
}

# 连续同一发送者的发言在 300 秒内并成一块。两个条件都要：中间插进别人、
# 或者隔得超过这个窗口，都断开重出抬头——不能为了合并把顺序拉平。
AGGREGATE_WINDOW_SECONDS = 300

# 这些组件交给富消息渲染器统一处理。
_RICH_TYPES = {
    "json", "xml", "markdown", "image", "file", "record", "video",
    "forward", "node", "share", "contact", "location", "music",
}


def format_angelheart_message(message_dict: Dict[str, Any]) -> str:
    """
    将天使之心提供的消息字典格式化为可读文本
    保留完整的ID、昵称和时间信息
    
    Args:
        message_dict (Dict): 天使之心提供的消息字典
        
    Returns:
        str: 格式化后的字符串，例如：
             "[群友: 小明 (ID: 123456)] (刚刚)\n你好，今天天气怎么样？"
    """
    try:
        role = message_dict.get("role", "unknown")
        content = message_dict.get("content", "")
        sender_id = message_dict.get("sender_id", "Unknown")
        sender_name = message_dict.get("sender_name", "成员")
        timestamp = message_dict.get("timestamp", 0)
        
        # 转换内容为字符串
        if isinstance(content, list):
            # 处理多模态内容
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_content = item.get("text", "")
                    if text_content:
                        text_parts.append(text_content)
            content = "".join(text_parts).strip()
        elif not isinstance(content, str):
            content = str(content)
        
        # 格式化相对时间
        relative_time_str = ""
        if timestamp:
            current_time = time.time()
            delta = current_time - timestamp
            
            if delta < 0:
                relative_time_str = ""
            elif delta < 60:
                relative_time_str = " (刚刚)"
            elif delta < 3600:
                minutes = int(delta / 60)
                relative_time_str = f" ({minutes}分钟前)"
            elif delta < 86400:  # 24小时
                hours = int(delta / 3600)
                relative_time_str = f" ({hours}小时前)"
            else:
                days = int(delta / 86400)
                relative_time_str = f" ({days}天前)"
        
        # 根据角色确定显示格式
        if role == "assistant":
            # 助理消息格式
            return f"[助理: {sender_name}]{relative_time_str}\n{content}"
        elif role == "user":
            # 用户消息格式
            return f"[群友: {sender_name} (ID: {sender_id})]{relative_time_str}\n{content}"
        else:
            # 其他角色
            return f"[{role}: {sender_name}]{relative_time_str}\n{content}"
            
    except Exception as e:
        # 发生错误时返回错误标识
        return f"[格式化错误] 无法处理天使之心消息: {str(e)}"


def _sender_role_label(sender: Any) -> str:
    """从上游 sender 提取群成员角色标签，取值未知时返回空串，不做猜测。"""
    if not isinstance(sender, dict):
        return ""
    raw_role = sender.get("role")
    if not isinstance(raw_role, str):
        return ""
    return SENDER_ROLE_LABELS.get(raw_role.strip().lower(), "")


def _time_tag(timestamp: Any) -> str:
    """把 Unix 时间戳格式化为北京时间，精确到分钟。"""
    if not timestamp:
        return ""
    try:
        moment = datetime.fromtimestamp(float(timestamp), tz=BEIJING_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return moment.strftime("%Y-%m-%d %H:%M")


def _sender_header(role_label: str, nickname: str, sender_id: str, time_tag: str) -> str:
    """拼发言人抬头。群聊与助理共用一套写法，只换角色标签。"""
    header = f"[{role_label}: {nickname} (ID: {sender_id})]"
    return f"{header} ({time_tag})" if time_tag else header


def _content_to_text(message_dict: Dict[str, Any]) -> str:
    """把消息链渲染成正文。引用段整段丢弃，富消息交给富消息渲染器。"""
    message_chain = message_dict.get("message", [])
    if not isinstance(message_chain, list):
        return "[消息格式错误]"

    content_parts = []
    for component in message_chain:
        if not isinstance(component, dict):
            continue
        comp_type = component.get("type")
        data = component.get("data", {})
        if comp_type == "text":
            text_content = re.sub(r"\s+", " ", data.get("text", "")).strip()
            if text_content:
                content_parts.append(text_content)
        elif comp_type == "reply":
            continue
        elif comp_type in _RICH_TYPES:
            content_parts.append("\n" + format_rich_segment(component) + "\n")
        elif comp_type == "face":
            face_id = data.get("id", "?")
            content_parts.append(f"[表情:{face_id}]")
        elif comp_type == "at":
            target_qq = data.get("qq", "?")
            if target_qq == "all":
                content_parts.append("[@全体成员]")
            else:
                content_parts.append(f"[@{target_qq}]")
        else:
            content_parts.append("\n" + format_rich_segment(component) + "\n")
    return "".join(content_parts).strip()


def _render_message(
    message_dict: Dict[str, Any], self_id: str = None
) -> Optional[Tuple[Optional[str], Optional[float], str, str]]:
    """渲染单条消息，返回 (发送者标识, 时间戳, 抬头, 正文)。

    正文为空（例如只有引用段）时返回 None，由调用方跳过。
    """
    sender = message_dict.get("sender")
    if not isinstance(sender, dict):
        # astrbot 上下文：拿不到 QQ 身份，只按 role 给出占位抬头。
        role = message_dict.get("role", "unknown")
        role_label = {"user": "用户", "assistant": "助理"}.get(role, "未知")
        sender_id = {"user": "current_user", "assistant": "assistant"}.get(role, "unknown")
        nickname = {"user": "User", "assistant": "Assistant"}.get(role, "Unknown")
        content = message_dict.get("content", "")
        body = content if isinstance(content, str) else str(content)
        body = body.strip()
        if not body:
            return None
        return None, None, _sender_header(role_label, nickname, sender_id, ""), body

    sender_id = sender.get("user_id", "未知ID")
    nickname = sender.get("card") or sender.get("nickname") or "未知用户"
    is_self = str(sender_id) == str(self_id) if self_id else False
    role_label = "助理" if is_self else (_sender_role_label(sender) or "群友")

    body = _content_to_text(message_dict)
    if not body:
        return None

    raw_time = message_dict.get("time", 0)
    time_value = None
    if raw_time:
        try:
            time_value = float(raw_time)
        except (TypeError, ValueError):
            time_value = None

    header = _sender_header(role_label, nickname, str(sender_id), _time_tag(raw_time))
    return str(sender_id), time_value, header, body


def format_unified_message(message_dict: Dict[str, Any], self_id: str = None) -> str:
    """
    将单条消息字典（来自QQ API或astrbot上下文）格式化为统一的可读纯文本字符串。

    Args:
        message_dict (Dict): 消息字典。可以是来自QQ API的原始消息，也可以是astrbot的上下文。
        self_id (str, optional): 机器人自身的QQ号，用于区分发言角色。

    Returns:
        str: 格式化后的字符串，例如
             "[群友: 红豆泥 (ID: 289104862)] (2025-09-16 15:12): 你好"；
             正文为空时返回空串。
    """
    try:
        rendered = _render_message(message_dict, self_id)
    except Exception as exc:
        return f"[格式化错误] 无法处理此消息: {str(exc)}"
    if rendered is None:
        return ""
    _, _, header, body = rendered
    return f"{header}: {body}"


def format_message_blocks(
    messages: List[Dict[str, Any]], self_id: str = None
) -> Tuple[List[str], int]:
    """把消息列表渲染成块，连续同一发送者的发言并成一块。

    只认连续：同一发送者相邻即合，中间插进别人就断开。合并还要求间隔不超过
    AGGREGATE_WINDOW_SECONDS，隔得太久的发言各自成块。输入顺序原样保留，
    块内按原顺序排列，块与块之间也保持原顺序。抬头用块首那条的时间。

    返回 (块文本列表, 实际渲染出的消息条数)。条数按消息算而不是按块算，
    否则聚合会让「已返回」少报。
    """
    blocks: List[Dict[str, Any]] = []
    for message_dict in messages or []:
        try:
            rendered = _render_message(message_dict, self_id)
        except Exception:
            continue
        if rendered is None:
            continue
        sender_key, time_value, header, body = rendered

        previous = blocks[-1] if blocks else None
        if (
            previous is not None
            and sender_key is not None
            and previous["sender_key"] == sender_key
            and time_value is not None
            and previous["last_time"] is not None
            and 0 <= time_value - previous["last_time"] <= AGGREGATE_WINDOW_SECONDS
        ):
            previous["lines"].append(body)
            previous["last_time"] = time_value
            continue

        blocks.append({
            "sender_key": sender_key,
            "last_time": time_value,
            "header": header,
            "lines": [body],
        })

    texts = [f"{block['header']}: " + "\n".join(block["lines"]) for block in blocks]
    return texts, sum(len(block["lines"]) for block in blocks)
