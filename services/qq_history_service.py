"""
Angel Eye 插件 - QQ 群聊历史服务
实现本地缓存优先 + 头部/尾部增量同步
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TYPE_CHECKING

from ..core.exceptions import ResourceBusyError
from ..core.formatter import format_message_blocks
from .history_repository import HistoryRepository, SyncState
from .rich_content_service import RichContentResolver

# 正文里未展开转发的资源 ID：`[合并转发：尚未展开，资源ID=xxx]`
_FORWARD_REF_PATTERN = re.compile(r"资源ID=([^，\]\s]+)")

# 导入 logger
try:
    from astrbot.api import logger
except ImportError:
    from astrbot.api import logger

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
        self.rich_content_resolver = RichContentResolver()
        self._group_locks: Dict[str, asyncio.Lock] = {}
        # 仅接受当前协议端回包确认过的游标，不复用 NapCat 持久化序号。
        self._snowluma_history_cursors: Dict[tuple[int, str], int] = {}
        self._napcat_history_cursors: Dict[tuple[int, str], int] = {}
        # 协议端类型每个 bot 生命周期内固定，识别一次即可，不必每页重探。
        self._backend_is_snowluma: Dict[int, bool] = {}
        # Stable message IDs keep failed resources from monopolizing each query.
        # Retain at most 64 IDs per scope and 128 recently used connection/groups.
        self._forward_index_rotation: Dict[tuple[int, str], List[str]] = {}

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
        rich_budget = [3]

        await group_lock.acquire()
        try:
            state = self.repo.get_sync_state(group_id)
            # 先查后补：请求区间已被覆盖的一侧不发任何协议端请求。
            needs_head = state.covered_to is None or query_end > state.covered_to

            if needs_head:
                head_result = await self._head_fill(bot, group_id, state, query_start, query_end)
                stop_reasons.append(f"head:{head_result.stop_reason}")
                # head 可能把最老那头也一并确认了（history_end），重新取状态再判 tail。
                state = self.repo.get_sync_state(group_id)

            needs_tail = state.covered_from is None or query_start < state.covered_from

            if needs_tail:
                tail_result = await self._tail_fill(bot, group_id, query_start, state)
                stop_reasons.append(f"tail:{tail_result.stop_reason}")

            final_state = self.repo.get_sync_state(group_id)

            if keywords:
                pending = self.repo.pending_forward_rows(
                    group_id, query_start, query_end, filter_user_ids, limit=64,
                )
                if pending:
                    await self._index_pending_forwards(
                        bot, group_id, pending, rich_budget,
                    )
                    stop_reasons.append("forward:index_bounded")

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

        local_messages = await self.rich_content_resolver.enrich_messages(
            bot, local_messages, scope=group_id, budget=rich_budget,
        )
        formatted_messages, returned_count = self._format_messages(local_messages)
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
            returned_count,
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

    async def _index_pending_forwards(self, bot, group_id, pending, budget):
        """把范围内未展开的转发换成正文文本，写回库里。

        pending 是 [(message_id, body)]。轮转与读取预算的语义保持原样：
        每轮最多碰 3 条候选，预算耗尽就停，未碰到的排到队尾等下次。
        """
        key = (id(bot), str(group_id))
        candidates = {str(message_id): body for message_id, body in pending}
        previous = self._forward_index_rotation.pop(key, [])
        queue = [message_id for message_id in previous if message_id in candidates]
        queue.extend(message_id for message_id in candidates if message_id not in queue)

        attempted = 0
        for message_id in queue[:3]:
            if budget[0] <= 0:
                break
            body = candidates[message_id]
            expanded = await self._expand_forwards_in_body(bot, group_id, body, budget)
            if expanded != body:
                self.repo.update_body(group_id, message_id, expanded)
            attempted += 1

        self._forward_index_rotation[key] = queue[attempted:] + queue[:attempted]
        while len(self._forward_index_rotation) > 128:
            self._forward_index_rotation.pop(next(iter(self._forward_index_rotation)))

    async def _expand_forwards_in_body(self, bot, group_id, body, budget):
        """把正文里的未展开转发占位逐个换成转发明细文本。"""
        for reference in _FORWARD_REF_PATTERN.findall(body or ""):
            if budget[0] <= 0:
                break
            block = await self.rich_content_resolver.expand_forward(
                bot, reference, scope=group_id, budget=budget,
            )
            if not block:
                continue
            body = body.replace(
                f"[合并转发：尚未展开，资源ID={reference}]", block,
            )
        return body

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

    def _settle_coverage(
        self,
        group_id: str,
        direction: str,
        stop_reason: str,
        request_start: int | None,
        request_end: int | None,
    ) -> None:
        """把一次同步的确认结果并进覆盖区间。

        只认「确定到底」的停止原因：
        - empty：协议端真的返回了空数组 → 请求边界算进覆盖；tail 侧还意味着历史见底
        - target_reached：已经翻到请求起点 → 请求边界算进覆盖
        - caught_up：翻回已存数据，更外侧确实没有新消息 → 请求边界算进覆盖
        - history_end：协议端答「没有了」——head 侧是空库翻光，tail 侧是游标回绕，
          两个方向同名不同义：head 两头都算进覆盖，tail 只算最老那头

        failures / cursor_stuck / cursor_missing 一律不扩张：那两个是游标异常，
        不是尽头证据，只认循环里已经写进去的实际页边界。
        """
        if direction == "tail" and stop_reason in {"empty", "history_end"}:
            self.repo.expand_coverage(group_id, from_ts=request_start)
            self.repo.upsert_sync_state(group_id=group_id, history_exhausted=True)
        elif direction == "tail" and stop_reason == "target_reached":
            self.repo.expand_coverage(group_id, from_ts=request_start)
        elif direction == "head" and stop_reason == "history_end":
            # 空库首次把历史翻光：最老那头也到底了，请求起点算进覆盖。
            self.repo.expand_coverage(group_id, from_ts=request_start, to_ts=request_end)
            self.repo.upsert_sync_state(group_id=group_id, history_exhausted=True)
        elif direction == "head" and stop_reason in {"caught_up", "empty", "target_reached"}:
            self.repo.expand_coverage(group_id, to_ts=request_end)

    async def _head_fill(
        self,
        bot: "Bot",
        group_id: str,
        state: SyncState,
        target_start: int,
        request_end: int,
    ) -> SyncResult:
        cursor_id = 0
        pages_fetched = 0
        inserted_count = 0
        consecutive_failures = 0
        stop_reason = "caught_up"

        is_empty_cache = state.covered_to is None or self.repo.count_messages(group_id) == 0
        seen_cursors: set[int] = set()

        while True:
            if cursor_id in seen_cursors:
                stop_reason = "cursor_cycle"
                break
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

            seen_cursors.add(cursor_id)
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
                # inserted==0 的含义取决于库里有没有旧数据：
                # 空库没有「之前存过的数据」，这一页重复只可能来自协议端回绕，
                # 那是确定的到底证据；非空库则可能只是撞上自己存过的，不能当证据用，
                # 最老那头的到底证据只认 tail 的 empty。
                if is_empty_cache and pages_fetched > 1:
                    stop_reason = "history_end"
                else:
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
        self._settle_coverage(group_id, "head", stop_reason, target_start, request_end)
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

        seen_cursors: set[int] = set()

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

            # The requested cursor may have been replaced with 0 while the
            # connection reestablishes its anchor. Track only returned anchors.
            next_cursor = self.repo.extract_anchor(server_messages[0])
            if next_cursor is None:
                stop_reason = "cursor_stuck"
                break
            if next_cursor in seen_cursors:
                # 协议端把已经拉过的一页又原样给一遍 = 没有更老的数据了。
                # tail 从已知最老边界往左翻，起点就在未知区域，循环不可能来自
                # 撞上自己存的数据（那是 head 的处境），所以这就是尽头证据。
                stop_reason = "history_end"
                break
            seen_cursors.add(next_cursor)
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
        self._settle_coverage(group_id, "tail", stop_reason, target_start, None)
        return SyncResult(
            direction="tail",
            pages_fetched=pages_fetched,
            inserted_count=inserted_count,
            stop_reason=stop_reason,
        )

    async def _fetch_page(self, bot: "Bot", group_id: str, cursor_id: int) -> List[Dict]:
        cursor_key = (id(bot), str(group_id))
        is_snowluma = self._backend_is_snowluma.get(id(bot))
        if is_snowluma is None:
            is_snowluma = cursor_key in self._snowluma_history_cursors
            try:
                version = await bot.api.call_action("get_version_info")
                if isinstance(version, dict):
                    version = version.get("data", version)
                    if isinstance(version, dict) and version.get("app_name"):
                        is_snowluma = "snowluma" in str(version["app_name"]).lower()
                        self._backend_is_snowluma[id(bot)] = is_snowluma
            except Exception:
                # 探测暂时失败时保留已识别的协议；首次失败仍沿用原 NapCat 行为。
                pass

        payload = {
            "group_id": int(group_id),
        }
        if is_snowluma:
            # 存量 oldest_seq 可能属于 NapCat，必须从最新页重新取得 SnowLuma 锚点。
            known_cursor = self._snowluma_history_cursors.get(cursor_key)
            payload.update(
                message_id=int(cursor_id) if cursor_id == known_cursor else 0,
                reverse_order=True,
            )
        else:
            self._snowluma_history_cursors.pop(cursor_key, None)
            known_cursor = self._napcat_history_cursors.get(cursor_key)
            payload.update(message_seq=int(cursor_id) if cursor_id == known_cursor else 0,
                           reverseOrder=True)
        result = await bot.api.call_action("get_group_msg_history", **payload)
        if not result or "messages" not in result:
            raise ValueError(f"API返回无效结果: {result}")
        messages = result.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError(f"API返回 messages 非列表: {type(messages)}")
        if not is_snowluma and messages:
            next_cursor = self.repo.extract_anchor(messages[0])
            if next_cursor is not None:
                self._napcat_history_cursors[cursor_key] = next_cursor
        if is_snowluma:
            self._napcat_history_cursors.pop(cursor_key, None)
        if is_snowluma and messages:
            messages = [dict(message, _history_backend="snowluma") for message in messages]
            next_cursor = self.repo.extract_anchor(messages[0])
            if next_cursor is None:
                raise ValueError("SnowLuma 历史回包缺少有效 message_id 游标")
            self._snowluma_history_cursors[cursor_key] = next_cursor
        return messages

    def _determine_coverage_status(self, query_start: int, query_end: int, state: SyncState) -> str:
        if state.covered_from is None or state.covered_to is None:
            return "PARTIAL"
        if query_start >= state.covered_from and query_end <= state.covered_to:
            return "FULL"
        return "PARTIAL"

    def _format_messages(self, messages: List[Dict]) -> tuple[List[str], int]:
        try:
            return format_message_blocks(messages, self.self_id)
        except Exception as exc:
            logger.warning("AngelEye: 格式化消息失败: %s", exc)
            return [], 0

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
