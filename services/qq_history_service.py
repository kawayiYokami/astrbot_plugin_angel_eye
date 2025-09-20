"""
Angel Eye 插件 - QQ 群聊历史服务
负责获取、格式化和缓存 QQ 群聊历史记录
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional, TYPE_CHECKING

import logging
logger = logging.getLogger(__name__)
from ..core.cache_manager import get, set as set_cache
from ..core.formatter import format_unified_message # 导入新的格式化工具

if TYPE_CHECKING:
    from astrbot.api import Bot

class QQChatHistoryService:
    """QQ 群聊历史服务，实现增量更新和缓存管理"""

    def __init__(self):
        """初始化服务，self_id 将在首次调用 get_messages 时获取"""
        self.self_id: Optional[str] = None

    def _build_cache_key(self, group_id: str) -> str:
        """为聊天记录构建唯一的缓存键"""
        return f"history:{group_id}"

    async def get_messages(
        self,
        bot: 'Bot',
        group_id: str,
        hours: Optional[int] = None,
        count: Optional[int] = None
    ) -> List[str]:
        """
        核心逻辑：使用三阶段取货模型获取并格式化 QQ 群聊历史记录 (KISS V10)
        阶段一: 从服务器取第一批数据 (冷启动)。
        阶段二: 循环消耗本地缓存。
        阶段三: 循环消耗服务器。
        """
        # 1. 获取机器人自身的ID
        if self.self_id is None:
            try:
                login_info = await bot.api.call_action("get_login_info")
                self.self_id = str(login_info.get("user_id"))
            except Exception as e:
                logger.error(f"AngelEye[QQChatHistoryService]: 获取机器人ID失败: {e}")
                self.self_id = "-1"

        # --- 准备阶段 ---
        cache_key = self._build_cache_key(group_id)
        start_timestamp = int((datetime.now() - timedelta(hours=hours)).timestamp()) if hours is not None else None
        
        # 用于O(1)去重的ID集合和最终存储消息的列表
        processed_ids: set[str] = set()
        messages: List[Dict] = []

        # 从缓存加载，并填充 processed_ids 和 messages
        cached_raw_messages: List[Dict] = await get(cache_key) or []
        if cached_raw_messages:
            messages.extend(cached_raw_messages)
            processed_ids.update(msg.get("message_id") for msg in cached_raw_messages)
            logger.debug(f"AngelEye: 群 {group_id}: 缓存命中 {len(messages)} 条消息。")
        
        # 用于在本地缓存中快速定位的ID->索引映射
        id_to_index_map = {msg.get("message_id"): i for i, msg in enumerate(cached_raw_messages)}

        # --- 阶段一：从服务器取第一批数据 (冷启动) ---
        cursor_id = 0
        logger.debug(f"AngelEye: 群 {group_id}: 阶段一 - 从服务器取第一批数据 (cursor_id={cursor_id})...")
        try:
            payloads = {"group_id": int(group_id), "message_seq": cursor_id, "reverseOrder": True}
            result = await bot.api.call_action("get_group_msg_history", **payloads)
            if not result or "messages" not in result:
                raise ValueError(f"API返回无效结果: {result}")
            
            round_1_messages = result.get("messages", [])
            if round_1_messages:
                new_messages = [msg for msg in round_1_messages if msg.get("message_id") not in processed_ids]
                if new_messages:
                    messages.extend(new_messages)
                    processed_ids.update(msg.get("message_id") for msg in new_messages)
                    cursor_id = round_1_messages[0]["message_id"]
                    logger.debug(f"AngelEye: 阶段一完成，获取 {len(new_messages)} 条新消息，新游标: {cursor_id}")
                else:
                    logger.debug(f"AngelEye: 阶段一未获取到新消息。")
            else:
                logger.debug(f"AngelEye: 阶段一服务器返回空列表。")
        except Exception as e:
            logger.error(f"AngelEye: 阶段一取货失败: {e}")
            # 即使失败，我们也继续执行后续阶段

        # 检查阶段一后是否已满足条件
        if count is not None and len(messages) >= count:
            logger.info(f"AngelEye: 阶段一后消息数量 ({len(messages)}) 已满足要求 ({count})，跳过后续阶段。")
            goto_finalization = True
        elif hours is not None and messages:
            temp_sorted = sorted(messages, key=lambda m: m.get('time', 0))
            if temp_sorted[0].get("time", 0) < start_timestamp:
                logger.info(f"AngelEye: 阶段一后最旧消息已早于时间限制，跳过后续阶段。")
                goto_finalization = True
            else:
                goto_finalization = False
        else:
            goto_finalization = False

        # --- 阶段二：循环消耗本地缓存 ---
        if not goto_finalization:
            logger.debug(f"AngelEye: 群 {group_id}: 阶段二 - 循环消耗本地缓存...")
            while True:
                A = len(processed_ids)

                found_index = id_to_index_map.get(cursor_id, -1)
                if found_index != -1:
                    start_slice = max(0, found_index - 20)
                    message_slice = cached_raw_messages[start_slice:found_index]
                    local_round_messages = message_slice[::-1] # 颠倒以模拟服务器行为
                else:
                    logger.debug(f"AngelEye: 本地缓存中未找到游标 {cursor_id}，结束本地阶段。")
                    break

                if not local_round_messages:
                    logger.debug(f"AngelEye: 本地货架返回空列表，结束本地阶段。")
                    break

                new_messages = [msg for msg in local_round_messages if msg.get("message_id") not in processed_ids]
                if new_messages:
                    messages.extend(new_messages)
                    processed_ids.update(msg.get("message_id") for msg in new_messages)

                B = len(processed_ids)

                # 检查退出条件
                if count is not None and len(messages) >= count:
                    logger.info(f"AngelEye: 消息数量 ({len(messages)}) 已满足要求 ({count})，结束本地阶段。")
                    break
                if hours is not None and messages:
                    temp_sorted = sorted(messages, key=lambda m: m.get('time', 0))
                    if temp_sorted[0].get("time", 0) < start_timestamp:
                        logger.info(f"AngelEye: 最旧消息已早于时间限制，结束本地阶段。")
                        break
                if B - A == 0:
                    logger.debug(f"AngelEye: 本地缓存相对于当前篮子已无新货 (B-A={B-A})，结束本地阶段。")
                    break

                cursor_id = local_round_messages[0]['message_id']
                logger.debug(f"AngelEye: 本地阶段更新游标: {cursor_id}, 新增: {B-A}")

        # 检查阶段二后是否已满足条件
        if count is not None and len(messages) >= count:
            logger.info(f"AngelEye: 阶段二后消息数量 ({len(messages)}) 已满足要求 ({count})，跳过服务器阶段。")
            goto_finalization = True
        elif hours is not None and messages:
            temp_sorted = sorted(messages, key=lambda m: m.get('time', 0))
            if temp_sorted[0].get("time", 0) < start_timestamp:
                logger.info(f"AngelEye: 阶段二后最旧消息已早于时间限制，跳过服务器阶段。")
                goto_finalization = True
            # else: goto_finalization 保持 False

        # --- 阶段三：循环消耗服务器 ---
        if not goto_finalization:
            logger.debug(f"AngelEye: 群 {group_id}: 阶段三 - 循环消耗服务器...")
            consecutive_failures = 0
            max_failures = 3
            sync_page_count = 0
            max_sync_pages = 20

            while True:
                A = len(processed_ids)

                try:
                    logger.debug(f"AngelEye: 群 {group_id}: 从服务器取货 (cursor_id={cursor_id})...")
                    payloads = {"group_id": int(group_id), "message_seq": cursor_id, "reverseOrder": True}
                    result = await bot.api.call_action("get_group_msg_history", **payloads)
                    
                    if not result or "messages" not in result:
                        raise ValueError(f"API返回无效结果: {result}")
                    
                    server_round_messages = result.get("messages", [])
                    consecutive_failures = 0
                    sync_page_count += 1

                    if not server_round_messages:
                        logger.info(f"AngelEye: 服务器返回空列表，历史记录同步完成。")
                        break

                    new_messages = [msg for msg in server_round_messages if msg.get("message_id") not in processed_ids]
                    if new_messages:
                        messages.extend(new_messages)
                        processed_ids.update(msg.get("message_id") for msg in new_messages)

                except Exception as e:
                    logger.error(f"AngelEye: 从服务器取货失败: {e}")
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        logger.error(f"AngelEye: 连续失败 {max_failures} 次，停止服务器阶段。")
                        break
                    await asyncio.sleep(1)
                    continue

                B = len(processed_ids)

                # 检查退出条件
                if count is not None and len(messages) >= count:
                    logger.info(f"AngelEye: 消息数量 ({len(messages)}) 已满足要求 ({count})，结束服务器阶段。")
                    break
                if hours is not None and messages:
                    temp_sorted = sorted(messages, key=lambda m: m.get('time', 0))
                    if temp_sorted[0].get("time", 0) < start_timestamp:
                        logger.info(f"AngelEye: 最旧消息已早于时间限制，结束服务器阶段。")
                        break
                if sync_page_count >= max_sync_pages:
                    logger.warning(f"AngelEye: 达到最大同步页数 ({max_sync_pages})，结束服务器阶段。")
                    break
                if B - A == 0:
                    logger.info(f"AngelEye: 服务器已无新货 (B-A={B-A})，历史记录同步完成。")
                    break

                cursor_id = server_round_messages[0]['message_id']
                logger.debug(f"AngelEye: 服务器阶段更新游标: {cursor_id}, 新增: {B-A}")

                await asyncio.sleep(0.1)

        # --- 收尾与缓存 ---
        logger.info(f"AngelEye: 同步完成，共获取 {len(messages)} 条消息，准备排序和缓存。")
        # 按时间升序排序（从旧到新）
        sorted_messages = sorted(messages, key=lambda m: m.get('time', 0))
        
        # 将这个全新的、最完整的列表，完整地写回本地缓存
        await set_cache(cache_key, sorted_messages)

        # --- 格式化并返回 ---
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