"""
Angel Eye 插件 - 消息格式化工具
提供将原始QQ消息对象格式化为可读文本的功能
"""
from __future__ import annotations
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

# 定义北京时间时区
BEIJING_TZ = timezone(timedelta(hours=8))

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

