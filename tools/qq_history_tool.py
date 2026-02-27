"""
天使之眼插件 - QQ 聊天记录查询工具
将 QQChatHistoryService 封装为 AstrBot 的 FunctionTool。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

from ..core.exceptions import ResourceBusyError
from ..services.qq_history_service import HistoryQueryResult, QQChatHistoryService

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass
class QQHistorySearchTool(FunctionTool):
    """
    根据关键词、时间等条件搜索当前群聊的聊天记录。
    """

    name: str = "search_qq_chat_history"
    description: str = (
        "搜索当前群聊聊天记录。时间参数 start/end 至少精确到天（YYYY-MM-DD），"
        "可选精确到时分秒（YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss）。"
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "关键词查询文本。为空表示不过滤关键词。",
                },
                "start": {
                    "type": "string",
                    "description": "起始时间。",
                },
                "end": {
                    "type": "string",
                    "description": "结束时间（不传默认当前时间）。",
                },
                "user_id": {
                    "type": "integer",
                    "description": "按单个发送者 QQ 号过滤。",
                },
                "slice": {
                    "type": "string",
                    "description": "结果切片语法：':50'(最新50条)、'50:'(最早到第50条)、'25:50'(第25到50条)、'50'(等价':50')。",
                },
            },
            "required": [],
        }
    )

    def __post_init__(self):
        self.history_service = QQChatHistoryService()

    async def run(
        self,
        event: AstrMessageEvent,
        query: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        user_id: Optional[int] = None,
        slice: Optional[str] = None,
        limit: Optional[int] = None,
        # 兼容旧参数（不在 schema 暴露）
        keywords: Optional[List[str]] = None,
        hours: Optional[int] = None,
        count: Optional[int] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        user_ids: Optional[List[int]] = None,
    ) -> str:
        try:
            if event.get_platform_name() == "aiocqhttp":
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent

                assert isinstance(event, AiocqhttpMessageEvent)
                bot = event.bot
                group_id = event.get_group_id()
            else:
                logger.error("AngelEye[QQHistorySearchTool]: 不支持的平台: %s", event.get_platform_name())
                return "当前平台不支持此工具。"

            if not group_id:
                logger.info("AngelEye[QQHistorySearchTool]: 在私聊或无群聊上下文中调用工具，无法查询。")
                return "此工具只能在群聊中使用，无法查询私聊记录。"

        except Exception as exc:
            logger.error("AngelEye[QQHistorySearchTool]: 无法从事件中获取 bot 或 group_id: %s", exc)
            return "无法获取当前群聊信息，请检查插件配置。"

        try:
            parsed_start = self._parse_time_input(start) if start is not None else start_time
            parsed_end = self._parse_time_input(end) if end is not None else end_time
            merged_keywords = keywords
            if query is not None:
                merged_keywords = [query] if query.strip() else None
            merged_user_ids = user_ids
            if user_id is not None:
                merged_user_ids = [user_id]

            result = await self.history_service.get_messages(
                bot=bot,
                group_id=group_id,
                hours=hours,
                count=count,
                filter_user_ids=merged_user_ids,
                keywords=merged_keywords,
                start_time=parsed_start,
                end_time=parsed_end,
                limit=limit,
                slice_expr=slice,
            )
        except ResourceBusyError as exc:
            return str(exc)
        except ValueError as exc:
            return f"参数错误: {exc}"
        except Exception as exc:
            logger.error("AngelEye[QQHistorySearchTool]: 查询聊天记录时发生错误: %s", exc, exc_info=True)
            return f"查询聊天记录时发生错误: {exc}"

        return self._render_result(result)

    def _render_result(self, result: HistoryQueryResult) -> str:
        coverage_line = (
            f"覆盖状态: {result.coverage_status} | 历史到底: {str(result.history_exhausted).lower()} | "
            f"同步停止: {', '.join(result.stop_reasons)}"
        )
        count_line = (
            f"区间命中总数: {result.total_in_range} | 已返回: {result.returned_count} | "
            f"区间内未返回: {result.remaining_in_range}"
        )
        range_line = (
            f"查询区间: {self._fmt_ts(result.query_start)} ~ {self._fmt_ts(result.query_end)}\n"
            f"本地覆盖: {self._fmt_ts(result.covered_from)} ~ {self._fmt_ts(result.covered_to)}"
        )

        if not result.formatted_messages:
            return f"在指定条件下没有匹配消息。\n{coverage_line}\n{count_line}\n{range_line}"

        messages_body = "\n\n".join(result.formatted_messages)
        return f"{coverage_line}\n{count_line}\n{range_line}\n\n{messages_body}"

    @staticmethod
    def _fmt_ts(timestamp: Optional[int]) -> str:
        if timestamp is None:
            return "未知"
        return datetime.fromtimestamp(timestamp, tz=BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_time_input(value: str) -> int:
        text = value.strip()
        if not text:
            raise ValueError("时间参数不能为空字符串")

        formats = ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")
        for fmt in formats:
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=BEIJING_TZ)
                return int(dt.timestamp())
            except ValueError:
                continue
        raise ValueError("时间格式错误，支持 YYYY-MM-DD / YYYY-MM-DD HH:mm / YYYY-MM-DD HH:mm:ss")
