"""
Angel Eye 插件 - QQ 群聊历史服务
实现本地缓存优先 + 头部/尾部增量同步
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

from ..core.exceptions import ResourceBusyError
from ..core.formatter import format_unified_message
from .history_repository import HistoryRepository, SyncState

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from astrbot.api import Bot


@dataclass
class FetchConfig:
    max_failures: int = 3
    server_call_delay: float = 0.1
    history_exhausted_no_progress_pages: int = 3
    default_hours: int = 24
    default_limit: int = 50
    max_limit: int = 500


@dataclass
class SyncResult:
    direction: str
    pages_fetched: int
    inserted_count: int
    stop_reason: str


@dataclass
class HistoryQueryResult:
    formatted_messages: List[str]
    total_in_range: int
    returned_count: int
    remaining_in_range: int
    query_start: int
    query_end: int
    covered_from: Optional[int]
    covered_to: Optional[int]
    coverage_status: str
    history_exhausted: bool
    stop_reasons: List[str]


class QQChatHistoryService:
    """QQ 群聊历史服务，实现本地缓存与增量同步。"""

    def __init__(self):
        self.self_id: Optional[str] = None
        self.config = FetchConfig()
        self.repo = HistoryRepository()
        self._group_locks: Dict[str, asyncio.Lock] = {}

    async def get_messages(
        self,
        bot: "Bot",
        group_id: str,
        hours: Optional[int] = None,
        count: Optional[int] = None,
        filter_user_ids: Optional[List[int]] = None,
        keywords: Optional[List[str]] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: Optional[int] = None,
        slice_expr: Optional[str] = None,
    ) -> HistoryQueryResult:
        if self.self_id is None:
            self.self_id = await self._initialize_self_id(bot)

        query_start, query_end, query_limit, query_offset, query_from_latest = self._normalize_query_params(
            hours=hours,
            count=count,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            slice_expr=slice_expr,
        )

        group_lock = self._group_locks.get(group_id)
        if group_lock is None:
            group_lock = asyncio.Lock()
            self._group_locks[group_id] = group_lock

        if group_lock.locked():
            logger.warning("AngelEye[busy]: group_id=%s 当前有同步任务进行中，直接返回忙碌。", group_id)
            raise ResourceBusyError("当前群聊历史同步忙碌中，请稍后再试。")
        stop_reasons: List[str] = []

        await group_lock.acquire()
        try:
            initial_state = self.repo.get_sync_state(group_id)
            head_result = await self._head_fill(bot, group_id, initial_state, query_start)
            stop_reasons.append(f"head:{head_result.stop_reason}")

            state_after_head = self.repo.update_coverage_from_messages(group_id)
            needs_tail = state_after_head.covered_from is None or query_start < state_after_head.covered_from

            if needs_tail:
                tail_result = await self._tail_fill(bot, group_id, query_start, state_after_head)
                stop_reasons.append(f"tail:{tail_result.stop_reason}")

            final_state = self.repo.update_coverage_from_messages(group_id)

            local_messages = self.repo.query_messages(
                group_id=group_id,
                start_time=query_start,
                end_time=query_end,
                keywords=keywords,
                user_ids=filter_user_ids,
                limit=query_limit,
                offset=query_offset,
                from_latest=query_from_latest,
            )
            total_in_range = self.repo.count_messages_in_range(
                group_id=group_id,
                start_time=query_start,
                end_time=query_end,
                keywords=keywords,
                user_ids=filter_user_ids,
            )
        finally:
            group_lock.release()

        formatted_messages = self._format_messages(local_messages)
        returned_count = len(formatted_messages)
        remaining_in_range = max(total_in_range - returned_count, 0)
        coverage_status = self._determine_coverage_status(query_start, query_end, final_state)

        logger.info(
            "AngelEye[query]: group_id=%s query_range=(%s,%s) covered_range=(%s,%s) coverage=%s result_count=%s",
            group_id,
            query_start,
            query_end,
            final_state.covered_from,
            final_state.covered_to,
            coverage_status,
            len(formatted_messages),
        )

        return HistoryQueryResult(
            formatted_messages=formatted_messages,
            total_in_range=total_in_range,
            returned_count=returned_count,
            remaining_in_range=remaining_in_range,
            query_start=query_start,
            query_end=query_end,
            covered_from=final_state.covered_from,
            covered_to=final_state.covered_to,
            coverage_status=coverage_status,
            history_exhausted=final_state.history_exhausted,
            stop_reasons=stop_reasons,
        )

    async def _initialize_self_id(self, bot: "Bot") -> str:
        try:
            login_info = await bot.api.call_action("get_login_info")
            return str(login_info.get("user_id"))
        except Exception as exc:
            logger.error("AngelEye[QQChatHistoryService]: 获取机器人ID失败: %s", exc)
            return "-1"

    def _normalize_query_params(
        self,
        hours: Optional[int],
        count: Optional[int],
        start_time: Optional[int],
        end_time: Optional[int],
        limit: Optional[int],
        slice_expr: Optional[str],
    ) -> tuple[int, int, int, int, bool]:
        now_ts = int(time.time())

        if end_time is None:
            end_time = now_ts
        else:
            end_time = min(int(end_time), now_ts)

        if start_time is None:
            if hours is not None:
                start_time = int((datetime.now() - timedelta(hours=float(hours))).timestamp())
            else:
                start_time = int((datetime.now() - timedelta(hours=self.config.default_hours)).timestamp())
        else:
            start_time = int(start_time)

        if start_time > end_time:
            raise ValueError("start_time 不能晚于 end_time")

        requested_limit = limit if limit is not None else count
        if requested_limit is None:
            requested_limit = self.config.default_limit
        query_limit = max(1, min(int(requested_limit), self.config.max_limit))

        slice_limit, slice_offset, from_latest = self._parse_slice_expr(slice_expr, query_limit)
        return start_time, end_time, slice_limit, slice_offset, from_latest

    def _parse_slice_expr(self, slice_expr: Optional[str], default_limit: int) -> tuple[int, int, bool]:
        if slice_expr is None:
            return default_limit, 0, False

        text = slice_expr.strip()
        if not text:
            raise ValueError("slice 不能为空")

        if text.isdigit():
            n = int(text)
            if n <= 0:
                raise ValueError("slice 数字必须 > 0")
            return min(n, self.config.max_limit), 0, True

        if ":" not in text:
            raise ValueError("slice 格式错误，支持 :N / N: / A:B / N")

        left, right = text.split(":", 1)
        left = left.strip()
        right = right.strip()

        if not left and not right:
            raise ValueError("slice 不能是 ':'")

        if not left:
            if not right.isdigit() or int(right) <= 0:
                raise ValueError("slice ':N' 中 N 必须为正整数")
            return min(int(right), self.config.max_limit), 0, True

        if not right:
            if not left.isdigit() or int(left) <= 0:
                raise ValueError("slice 'N:' 中 N 必须为正整数")
            return min(int(left), self.config.max_limit), 0, False

        if not left.isdigit() or not right.isdigit():
            raise ValueError("slice 'A:B' 中 A/B 必须为正整数")

        start_idx = int(left)
        end_idx = int(right)
        if start_idx <= 0 or end_idx <= 0:
            raise ValueError("slice 'A:B' 中 A/B 必须 > 0")
        if start_idx > end_idx:
            raise ValueError("slice 'A:B' 中 A 不能大于 B")

        length = end_idx - start_idx + 1
        return min(length, self.config.max_limit), start_idx - 1, False

    async def _head_fill(
        self,
        bot: "Bot",
        group_id: str,
        state: SyncState,
        target_start: int,
    ) -> SyncResult:
        cursor_id = 0
        pages_fetched = 0
        inserted_count = 0
        consecutive_failures = 0
        stop_reason = "caught_up"

        is_empty_cache = state.covered_to is None or self.repo.count_messages(group_id) == 0

        while True:
            try:
                server_messages = await self._fetch_page(bot, group_id, cursor_id)
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                logger.error("AngelEye[head]: 群 %s 拉取失败: %s", group_id, exc)
                if consecutive_failures >= self.config.max_failures:
                    stop_reason = "failures"
                    break
                await asyncio.sleep(1)
                continue

            pages_fetched += 1
            if not server_messages:
                stop_reason = "empty"
                break

            page_min_time, page_max_time = self._get_page_time_range(server_messages)

            insert_result = self.repo.insert_messages(group_id, server_messages)
            inserted_count += insert_result.inserted_count

            self.repo.upsert_sync_state(
                group_id=group_id,
                covered_from=page_min_time,
                covered_to=page_max_time,
            )

            if is_empty_cache and page_min_time <= target_start:
                stop_reason = "target_reached"
                break

            if insert_result.inserted_count == 0:
                stop_reason = "caught_up"
                break

            next_cursor = self.repo.extract_anchor(server_messages[0])
            if next_cursor is None:
                stop_reason = "cursor_missing"
                break
            if pages_fetched > 1 and next_cursor == cursor_id:
                stop_reason = "cursor_stuck"
                break
            cursor_id = next_cursor
            await asyncio.sleep(self.config.server_call_delay)

        logger.info(
            "AngelEye[sync]: group_id=%s direction=head pages=%s inserted=%s stop=%s",
            group_id,
            pages_fetched,
            inserted_count,
            stop_reason,
        )
        return SyncResult(
            direction="head",
            pages_fetched=pages_fetched,
            inserted_count=inserted_count,
            stop_reason=stop_reason,
        )

    async def _tail_fill(
        self,
        bot: "Bot",
        group_id: str,
        target_start: int,
        state: SyncState,
    ) -> SyncResult:
        if state.history_exhausted:
            return SyncResult(direction="tail", pages_fetched=0, inserted_count=0, stop_reason="history_exhausted")

        cursor_id = state.oldest_seq
        if cursor_id is None:
            cursor_id = self.repo.rebuild_oldest_seq_from_messages(group_id)
            if cursor_id is not None:
                self.repo.upsert_sync_state(group_id=group_id, oldest_seq=cursor_id)

        if cursor_id is None:
            return SyncResult(direction="tail", pages_fetched=0, inserted_count=0, stop_reason="no_cursor")

        pages_fetched = 0
        inserted_count = 0
        consecutive_failures = 0
        stop_reason = "target_reached"

        no_progress_pages = 0
        last_oldest_time = state.covered_from

        while True:
            try:
                server_messages = await self._fetch_page(bot, group_id, cursor_id)
                consecutive_failures = 0
            except Exception as exc:
                consecutive_failures += 1
                logger.error("AngelEye[tail]: 群 %s 拉取失败: %s", group_id, exc)
                if consecutive_failures >= self.config.max_failures:
                    stop_reason = "failures"
                    break
                await asyncio.sleep(1)
                continue

            pages_fetched += 1
            if not server_messages:
                stop_reason = "empty"
                break

            page_min_time, page_max_time = self._get_page_time_range(server_messages)

            insert_result = self.repo.insert_messages(group_id, server_messages)
            inserted_count += insert_result.inserted_count

            self.repo.upsert_sync_state(
                group_id=group_id,
                oldest_seq=insert_result.page_oldest_anchor,
                covered_from=page_min_time,
                covered_to=page_max_time,
            )

            refreshed_state = self.repo.get_sync_state(group_id)
            if refreshed_state.covered_from is not None and refreshed_state.covered_from <= target_start:
                stop_reason = "target_reached"
                break

            if insert_result.inserted_count == 0 and refreshed_state.covered_from == last_oldest_time:
                no_progress_pages += 1
            else:
                no_progress_pages = 0
                last_oldest_time = refreshed_state.covered_from

            if no_progress_pages >= self.config.history_exhausted_no_progress_pages:
                self.repo.upsert_sync_state(group_id=group_id, history_exhausted=True)
                stop_reason = "history_exhausted"
                break

            next_cursor = self.repo.extract_anchor(server_messages[0])
            if next_cursor is None or next_cursor == cursor_id:
                stop_reason = "cursor_stuck"
                break
            cursor_id = next_cursor
            await asyncio.sleep(self.config.server_call_delay)

        logger.info(
            "AngelEye[sync]: group_id=%s direction=tail pages=%s inserted=%s stop=%s target_start=%s",
            group_id,
            pages_fetched,
            inserted_count,
            stop_reason,
            target_start,
        )
        return SyncResult(
            direction="tail",
            pages_fetched=pages_fetched,
            inserted_count=inserted_count,
            stop_reason=stop_reason,
        )

    async def _fetch_page(self, bot: "Bot", group_id: str, cursor_id: int) -> List[Dict]:
        payload = {
            "group_id": int(group_id),
            "message_seq": int(cursor_id),
            "reverseOrder": True,
        }
        result = await bot.api.call_action("get_group_msg_history", **payload)
        if not result or "messages" not in result:
            raise ValueError(f"API返回无效结果: {result}")
        messages = result.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError(f"API返回 messages 非列表: {type(messages)}")
        return messages

    def _determine_coverage_status(self, query_start: int, query_end: int, state: SyncState) -> str:
        if state.covered_from is None or state.covered_to is None:
            return "PARTIAL"
        if query_start >= state.covered_from and query_end <= state.covered_to:
            return "FULL"
        return "PARTIAL"

    def _format_messages(self, messages: List[Dict]) -> List[str]:
        formatted: List[str] = []
        for msg in messages:
            try:
                formatted.append(format_unified_message(msg, self.self_id))
            except Exception as exc:
                logger.warning("AngelEye: 格式化单条消息失败: %s", exc)
        return formatted

    def close(self) -> None:
        self.repo.close()

    @staticmethod
    def _get_page_time_range(messages: List[Dict]) -> tuple[int, int]:
        times = [int(m.get("time", 0) or 0) for m in messages]
        times = [t for t in times if t > 0]
        if not times:
            now_ts = int(time.time())
            return now_ts, now_ts
        return min(times), max(times)
