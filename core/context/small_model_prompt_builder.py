"""
小模型提示词构建器

负责格式化 AngelHeart 和 Astar 上下文数据。
"""

import json
from typing import List, Dict, Tuple
from datetime import datetime
from ..formatter import format_unified_message


class SmallModelPromptBuilder:
    """小模型提示词构建器"""

    @staticmethod
    def parse_angelheart_context(angelheart_context: str) -> Tuple[List[Dict], Dict, bool]:
        """
        解析 AngelHeart 上下文数据

        Args:
            angelheart_context: JSON 字符串格式的上下文

        Returns:
            一个元组，包含：
            - chat_records: 对话记录列表
            - secretary_decision: 秘书决策字典
            - needs_search: 是否需要搜索
        """
        try:
            if not angelheart_context:
                return [], {}, False

            context = json.loads(angelheart_context)
            chat_records = context.get('chat_records', [])
            secretary_decision = context.get('secretary_decision', {})
            needs_search = context.get('needs_search', False)

            return chat_records, secretary_decision, needs_search
        except (json.JSONDecodeError, TypeError) as e:
            print(f"解析 AngelHeart 上下文失败: {e}")
            return [], {}, False

    @staticmethod
    def format_conversation_summary(angelheart_context: str) -> str:
        """
        格式化 AngelHeart 上下文为对话记录+ 对话目标+当前主题的格式

        Args:
            angelheart_context: JSON 字符串格式的上下文

        Returns:
            格式化后的字符串，包含对话记录、对话目标、当前主题
        """
        chat_records, secretary_decision, needs_search = SmallModelPromptBuilder.parse_angelheart_context(angelheart_context)

        # 1. 构建对话记录部分（最多保留最近7条）
        conversation_lines = []
        if chat_records:
            # 只保留最后7条记录
            recent_records = chat_records[-7:]
            for msg in recent_records:
                role = msg.get("role")
                role_name = "用户" if role == "user" else "助理"

                sender_info = ""
                if role == "user":
                    sender_name = msg.get('sender_name', '用户')
                    sender_id = msg.get('sender_id', 'unknown')
                    sender_info = f" ({sender_name}/{sender_id})"

                timestamp = msg.get('timestamp', 0)
                time_str = SmallModelPromptBuilder.format_relative_time(timestamp)
                content = SmallModelPromptBuilder.extract_text_from_content(msg.get("content", ""))

                conversation_lines.append(f"[{role_name}{sender_info}] ({time_str}): {content}")

        conversation_section = "# 对话记录\n" + "\n".join(conversation_lines) if conversation_lines else "# 对话记录\n暂无对话记录"

        # 2. 构建对话目标部分
        decision_section = ""
        if secretary_decision:
            topic = secretary_decision.get('topic', '未识别到明确主题')
            reply_strategy = secretary_decision.get('reply_strategy', '未指定')
            reply_target = secretary_decision.get('reply_target', '未指定')

            decision_lines = [
                f"- 当前主题: {topic}",
                f"- 回复策略: {reply_strategy}",
                f"- 回复目标: {reply_target}"
            ]
            decision_section = f"\n# 关于当前对话的理解\n" + "\n".join(decision_lines)

        # 组合所有部分
        return f"{conversation_section}{decision_section}"

    @staticmethod
    def format_relative_time(timestamp: float) -> str:
        """
        格式化相对时间

        Args:
            timestamp: Unix时间戳

        Returns:
            相对时间字符串，如"刚刚"、"2分钟前"等
        """
        if not timestamp:
            return ""

        now = datetime.now().timestamp()
        diff = now - timestamp

        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return f"{int(diff / 60)}分钟前"
        if diff < 86400:
            return f"{int(diff / 3600)}小时前"
        return f"{int(diff / 86400)}天前"

    @staticmethod
    def extract_text_from_content(content) -> str:
        """
        从content中提取文本

        Args:
            content: 消息内容，可能是字符串、列表或字典

        Returns:
            提取的文本内容
        """
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return item.get("text", "")
        return ""

    @staticmethod
    def format_astar_conversation(contexts: List[Dict], current_prompt: str = None) -> str:
        """
        格式化 Astar 原生上下文为对话记录格式

        Args:
            contexts: Astar 上下文列表（req.contexts）
            current_prompt: 当前用户输入（可选）

        Returns:
            格式化后的对话记录字符串
        """
        conversation_lines = ["# 对话记录"]

        # 处理历史上下文（最多保留最近6条，加上当前消息共7条）
        recent_contexts = contexts[-6:] if len(contexts) > 6 else contexts
        for context in recent_contexts:
            formatted = format_unified_message(context)
            conversation_lines.append(formatted)

        # 处理当前消息
        if current_prompt:
            current_message = {
                "role": "user",
                "content": current_prompt
            }
            formatted = format_unified_message(current_message)
            conversation_lines.append(formatted)

        return "\n".join(conversation_lines)

    @staticmethod
    def inject_dialogue_into_template(template: str, dialogue: str) -> str:
        """
        将对话内容注入到提示词模板中

        Args:
            template: 提示词模板，包含 {dialogue} 占位符
            dialogue: 要注入的对话内容

        Returns:
            注入后的完整提示词

        Example:
            >>> template = "分析对话：{dialogue}\\n输出格式: {json}"
            >>> dialogue = "用户: 你好"
            >>> result = inject_dialogue_into_template(template, dialogue)
            >>> # result: "分析对话：用户: 你好\\n输出格式: {json}"
        """
        # 直接替换 {dialogue} 占位符
        return template.replace('{dialogue}', dialogue)
