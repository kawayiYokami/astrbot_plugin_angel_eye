"""
Angel Eye 插件 - 消息格式化工具
提供将原始QQ消息对象格式化为可读文本的功能
"""
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

# 定义北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))

def format_unified_message(message_dict: Dict[str, Any], self_id: str = None) -> str:
    """
    将单条消息字典（来自QQ API或astrbot上下文）格式化为统一的可读纯文本字符串。

    Args:
        message_dict (Dict): 消息字典。可以是来自QQ API的原始消息，也可以是astrbot的上下文。
        self_id (str, optional): 机器人自身的QQ号，用于区分发言角色。当处理astrbot上下文时可不传。

    Returns:
        str: 格式化后的字符串，例如 "[群友]红豆泥(289104862) [2025-09-16 15:12]: 你好" 或 "[用户]Current User(current_user): 你好"
    """
    try:
        # 1. 解析发送者和角色
        # 如果是来自QQ API的消息
        if "sender" in message_dict:
            sender = message_dict.get("sender", {})
            sender_id = sender.get("user_id", "未知ID")
            nickname = sender.get("nickname", "未知用户")
            # 判断是否为机器人自己发送的消息
            is_self = str(sender_id) == str(self_id) if self_id else False
            role_display = "[助理]" if is_self else "[群友]"
        # 如果是来自astrbot的上下文
        else:
            role = message_dict.get("role", "unknown")
            content_str = message_dict.get("content", "")

            # 根据 role 决定角色、昵称和ID，生成统一格式
            if role == "user":
                role_display = "[用户]"
                nickname = "User"
                sender_id = "current_user"
            elif role == "assistant":
                role_display = "[助理]"
                nickname = "Assistant"
                sender_id = "assistant"
            else:
                role_display = "[未知]"
                nickname = "Unknown"
                sender_id = "unknown"

            # 对于 astrbot 上下文，时间戳通常不存在
            time_str = ""
            
            # 直接拼接并返回，跳过后续通用逻辑
            formatted_text = f"{role_display}{nickname}({sender_id}): {content_str}"
            return formatted_text

        # 2. 解析时间戳并格式化为北京时间 (可选)
        timestamp = message_dict.get("time", 0)
        time_str = ""
        if timestamp:
            dt_object = datetime.fromtimestamp(timestamp, tz=BEIJING_TZ)
            time_str = f" [{dt_object.strftime('%Y-%m-%d %H:%M')}]"

        # 3. 解析消息内容
        content_str = ""
        # 如果是来自QQ API的消息
        if "message" in message_dict:
            message_chain = message_dict.get("message", [])
            content_parts = []
            if not isinstance(message_chain, list):
                content_parts.append("[消息格式错误]")
            else:
                for component in message_chain:
                    if not isinstance(component, dict):
                        continue
                    comp_type = component.get("type")
                    data = component.get("data", {})
                    if comp_type == "text":
                        text_content = data.get("text", "")
                        text_content = re.sub(r'\s+', ' ', text_content).strip()
                        if text_content:
                            content_parts.append(text_content)
                    elif comp_type == "image":
                        content_parts.append("[图片]")
                    elif comp_type == "face":
                        face_id = data.get("id", "?")
                        content_parts.append(f"[表情:{face_id}]")
                    elif comp_type == "at":
                        target_qq = data.get("qq", "?")
                        if target_qq == "all":
                            content_parts.append("[@全体成员]")
                        else:
                            content_parts.append(f"[@{target_qq}]")
                    elif comp_type == "record":
                        content_parts.append("[语音]")
                    elif comp_type == "video":
                        content_parts.append("[视频]")
                    elif comp_type == "reply":
                        content_parts.append("[回复]")
                    elif comp_type == "forward":
                        content_parts.append("[转发消息]")
                    else:
                        content_parts.append(f"[{comp_type or '未知类型'}]")
            content_str = "".join(content_parts)
        
        # 4. 拼接最终字符串 (仅适用于QQ API消息)
        # 格式: [角色]昵称(ID) [可选的时间]: 内容
        formatted_text = f"{role_display}{nickname}({sender_id}){time_str}: {content_str}"
        return formatted_text
    except Exception as e:
        # 发生任何错误时，返回一个错误标识，避免整个流程中断
        message_id = message_dict.get('message_id') or message_dict.get('id', 'N/A')
        return f"[格式化错误] 无法处理此消息 (ID: {message_id}, Error: {str(e)})"

