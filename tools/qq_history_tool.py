"""
天使之眼插件 - QQ 聊天记录查询工具
将 QQChatHistoryService 封装为 AstrBot 的 FunctionTool。
"""

import logging
from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from astrbot.api import FunctionTool
from dataclasses import dataclass, field
from typing import List, Optional

from ..services.qq_history_service import QQChatHistoryService

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

@dataclass
class QQHistorySearchTool(FunctionTool):
    """
    根据关键词、时间等条件搜索当前群聊的聊天记录。
    """
    name: str = "search_qq_chat_history"
    description: str = "根据关键词、时间范围等条件搜索当前群聊的聊天记录。"
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "用于筛选消息的关键词列表。例如：['天气', 'AstrBot']",
                },
                "hours": {
                    "type": "number",
                    "description": "查询过去多少小时内的聊天记录。例如：24 表示查询过去24小时。",
                },
                "count": {
                    "type": "integer",
                    "description": "最多返回的消息数量。",
                },
            },
            "required": [], # 所有参数都是可选的
        }
    )

    def __post_init__(self):
        """初始化 QQChatHistoryService 实例"""
        self.history_service = QQChatHistoryService()

    async def run(
        self,
        event: AstrMessageEvent,
        keywords: Optional[List[str]] = None,
        hours: Optional[int] = None,
        count: Optional[int] = None,
    ) -> str:
        """
        执行聊天记录搜索。

        Args:
            event: AstrBot 消息事件对象，用于获取上下文。
            keywords: 用于筛选消息的关键词列表。
            hours: 查询过去多少小时内的聊天记录。
            count: 最多返回的消息数量。

        Returns:
            格式化后的聊天记录字符串，或错误/无结果提示。
        """
        # 1. 从 event 中获取必要的上下文信息
        #    - bot 实例
        #    - group_id
        try:
            if event.get_platform_name() == "aiocqhttp":
                # QQ 平台
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                assert isinstance(event, AiocqhttpMessageEvent)
                bot = event.bot  # 得到 client
                group_id = event.get_group_id() # 使用内置方法获取群号
            else:
                logger.error(f"AngelEye[QQHistorySearchTool]: 不支持的平台: {event.get_platform_name()}")
                return "当前平台不支持此工具。"

            # 校验 group_id 是否有效
            if not group_id:
                logger.info("AngelEye[QQHistorySearchTool]: 在私聊或无群聊上下文中调用工具，无法查询。")
                return "此工具只能在群聊中使用，无法查询私聊记录。"

        except Exception as e:
            logger.error(f"AngelEye[QQHistorySearchTool]: 无法从事件中获取 bot 或 group_id: {e}")
            return "无法获取当前群聊信息，请检查插件配置。"
        # 2. 调用现有的 history_service 获取消息
        try:
            formatted_messages = await self.history_service.get_messages(
                bot=bot,
                group_id=group_id,
                hours=hours,
                count=count,
                keywords=keywords,
            )
        except Exception as e:
            logger.error(f"AngelEye[QQHistorySearchTool]: 查询聊天记录时发生错误: {e}", exc_info=True)
            return f"查询聊天记录时发生错误: {e}"

        # 3. 格式化返回结果
        if not formatted_messages:
            return "在指定的条件下没有找到相关的聊天记录。"
        else:
            # 将消息列表合并成一个长字符串，便于 LLM 理解
            return "\n".join(formatted_messages)