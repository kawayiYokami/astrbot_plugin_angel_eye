"""
Angel Eye 插件 - QQ 群聊历史服务
负责获取、格式化和缓存 QQ 群聊历史记录
"""
import asyncio
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Optional, TYPE_CHECKING
from dataclasses import dataclass

from ..core.formatter import format_unified_message # 导入新的格式化工具

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from astrbot.api import Bot

@dataclass
class FetchConfig:
    """获取配置类，替代硬编码的Magic Numbers"""
    max_failures: int = 3
    max_sync_pages: int = 20
    server_call_delay: float = 0.1
    local_batch_size: int = 20

@dataclass
class FetchState:
    """获取状态管理类"""
    processed_ids: set[str]
    messages: List[Dict]
    cursor_id: int
    consecutive_failures: int
    sync_page_count: int
    start_timestamp: Optional[int]

class QQChatHistoryService:
    """QQ 群聊历史服务，实现增量更新和缓存管理"""

    def __init__(self):
        """初始化服务，self_id 将在首次调用 get_messages 时获取"""
        self.self_id: Optional[str] = None
        self.config = FetchConfig()


    async def get_messages(
        self,
        bot: 'Bot',
        group_id: str,
        hours: Optional[int] = None,
        count: Optional[int] = None,
        filter_user_ids: Optional[List[int]] = None,
        keywords: Optional[List[str]] = None
    ) -> List[str]:
        """
        核心逻辑：直接从服务器获取并格式化 QQ 群聊历史记录。
        """
        # 1. 获取机器人自身的ID
        if self.self_id is None:
            self.self_id = await self._initialize_self_id(bot)

        # 2. 初始化获取状态和配置
        state = self._prepare_fetch_state(group_id, hours)

        # 3. 直接从服务器获取数据
        await self._fetch_from_server(bot, group_id, state, count)

        # 4. 最终处理和格式化
        return await self._finalize_and_format_messages(
            group_id, state, filter_user_ids, keywords
        )

    async def _initialize_self_id(self, bot: 'Bot') -> str:
        """初始化机器人自身ID"""
        try:
            login_info = await bot.api.call_action("get_login_info")
            return str(login_info.get("user_id"))
        except Exception as e:
            logger.error(f"AngelEye[QQChatHistoryService]: 获取机器人ID失败: {e}")
            return "-1"

    def _prepare_fetch_state(self, group_id: str, hours: Optional[int]) -> FetchState:
        """准备获取状态"""
        start_timestamp = int((datetime.now() - timedelta(hours=hours)).timestamp()) if hours is not None else None
        return FetchState(
            processed_ids=set(),
            messages=[],
            cursor_id=0,
            consecutive_failures=0,
            sync_page_count=0,
            start_timestamp=start_timestamp
        )

    async def _fetch_from_server(
        self, bot: 'Bot', group_id: str, state: FetchState, count: Optional[int]
    ) -> None:
        """直接从服务器循环获取数据，直到满足条件"""
        logger.info(f"AngelEye: 群 {group_id}: 开始从服务器获取聊天记录...")

        while True:
            previous_count = len(state.processed_ids)

            try:
                logger.debug(f"AngelEye: 群 {group_id}: 从服务器取货 (cursor_id={state.cursor_id})...")
                payloads = {"group_id": int(group_id), "message_seq": state.cursor_id, "reverseOrder": True}
                result = await bot.api.call_action("get_group_msg_history", **payloads)

                if not result or "messages" not in result:
                    raise ValueError(f"API返回无效结果: {result}")

                server_messages = result.get("messages", [])
                state.consecutive_failures = 0
                state.sync_page_count += 1

                if not server_messages:
                    logger.info("AngelEye: 服务器返回空列表，历史记录获取完成。")
                    break

                new_messages = [msg for msg in server_messages if msg.get("message_id") not in state.processed_ids]
                if new_messages:
                    state.messages.extend(new_messages)
                    state.processed_ids.update(msg.get("message_id") for msg in new_messages)

            except Exception as e:
                logger.error(f"AngelEye: 从服务器取货失败: {e}")
                state.consecutive_failures += 1
                if state.consecutive_failures >= self.config.max_failures:
                    logger.error(f"AngelEye: 连续失败 {self.config.max_failures} 次，停止获取。")
                    break
                await asyncio.sleep(1)
                continue

            if self._should_exit_stage(state, count, previous_count):
                break

            state.cursor_id = server_messages[0]['message_id']
            logger.debug(f"AngelEye: 服务器阶段更新游标: {state.cursor_id}, 新增: {len(state.processed_ids) - previous_count}")

            await asyncio.sleep(self.config.server_call_delay)

    def _should_finalize(self, state: FetchState, count: Optional[int]) -> bool:
        """检查是否应该结束整个获取流程"""
        if count is not None and len(state.messages) >= count:
            logger.info(f"AngelEye: 消息数量 ({len(state.messages)}) 已满足要求 ({count})，跳过后续阶段。")
            return True

        if state.start_timestamp and state.messages:
            earliest_time = min(msg.get('time', 0) for msg in state.messages)
            if earliest_time < state.start_timestamp:
                logger.info("AngelEye: 最旧消息已早于时间限制，跳过后续阶段。")
                return True

        return False

    def _should_exit_stage(self, state: FetchState, count: Optional[int], previous_count: int) -> bool:
        """检查是否应该退出当前阶段"""
        current_count = len(state.processed_ids)

        if count is not None and len(state.messages) >= count:
            logger.info(f"AngelEye: 消息数量 ({len(state.messages)}) 已满足要求 ({count})，结束当前阶段。")
            return True

        # 移除基于时间的退出条件，改为获取所有消息后统一过滤

        if state.sync_page_count >= self.config.max_sync_pages:
            logger.warning(f"AngelEye: 达到最大同步页数 ({self.config.max_sync_pages})，结束当前阶段。")
            return True

        if current_count - previous_count == 0:
            logger.info("AngelEye: 已无新消息，结束当前阶段。")
            return True

        return False

    async def _finalize_and_format_messages(
        self,
        group_id: str,
        state: FetchState,
        filter_user_ids: Optional[List[int]],
        keywords: Optional[List[str]]
    ) -> List[str]:
        """最终处理、过滤和格式化消息"""
        logger.info(f"AngelEye: 获取完成，共获取 {len(state.messages)} 条消息，准备排序和过滤。")

        # 按时间升序排序
        sorted_messages = sorted(state.messages, key=lambda m: m.get('time', 0))

        # --- 应用过滤条件 ---
        sorted_messages = self._apply_all_filters(sorted_messages, state.start_timestamp, filter_user_ids, keywords)

        # 格式化并返回
        formatted_messages = []
        for msg in sorted_messages:
            try:
                formatted_msg = format_unified_message(msg, self.self_id)
                formatted_messages.append(formatted_msg)
            except Exception as msg_error:
                logger.warning(f"AngelEye: 格式化单条消息失败: {msg_error}")
                continue

        logger.info(f"AngelEye: 最终返回 {len(formatted_messages)} 条已格式化的消息。")
        return formatted_messages

    def _apply_filters(
        self,
        messages: List[Dict],
        start_timestamp: Optional[int],
        filter_user_ids: Optional[List[int]],
        keywords: Optional[List[str]]
    ) -> List[Dict]:
        """应用过滤条件（旧版本，兼容性保留）"""
        return self._apply_all_filters(messages, start_timestamp, filter_user_ids, keywords)

    def _apply_all_filters(
        self,
        messages: List[Dict],
        start_timestamp: Optional[int],
        filter_user_ids: Optional[List[int]],
        keywords: Optional[List[str]]
    ) -> List[Dict]:
        """应用所有过滤条件：时间、用户ID、关键词"""
        logger.info(f"AngelEye: 应用过滤条件 - 开始时间: {start_timestamp}, 用户IDs: {filter_user_ids}, 关键词: {keywords}")

        filtered_messages = []
        for msg in messages:
            # 时间过滤
            if start_timestamp is not None:
                msg_time = msg.get('time', 0)
                if msg_time < start_timestamp:
                    continue  # 消息太旧，跳过

            # 用户ID过滤
            if filter_user_ids:
                user_id = msg.get('user_id')
                if user_id not in filter_user_ids:
                    continue

            # 关键词过滤
            if keywords:
                content = msg.get('content', '')
                if not isinstance(content, str):
                    logger.debug(f"AngelEye: 消息内容类型为 {type(content).__name__}，转换为字符串。message_id: {msg.get('message_id')}")
                    content = str(content)
                if not any(keyword in content for keyword in keywords):
                    continue

            filtered_messages.append(msg)

        logger.info(f"AngelEye: 过滤后剩余 {len(filtered_messages)} 条消息")
        return filtered_messages