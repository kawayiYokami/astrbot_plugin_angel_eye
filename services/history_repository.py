"""
Angel Eye 插件 - QQ 群聊历史仓储层
负责 SQLite 持久化、去重写入、同步状态维护与本地查询

存储只保留入库时渲染好的正文（body）。抬头不入库：展示时按 sender_id 去
senders 表现拼，所以同一个人改了昵称，旧消息的抬头跟着变。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from astrbot.core.star.star_tools import StarTools

from ..core.formatter import _content_to_text

try:
    from astrbot.api import logger
except ImportError:
    from astrbot.api import logger

# 表结构版本。与库里的 user_version 不一致时整库清空重建：旧表只有
# raw_json、没有 body，留着也渲染不出正文，历史由协议端重拉。
SCHEMA_VERSION = 2

# 未展开转发在正文里的占位文案前缀，展开时按它定位要替换的片段。
FORWARD_PLACEHOLDER = "合并转发：尚未展开"


@dataclass
class SyncState:
    group_id: str
    oldest_seq: Optional[int]
    covered_from: Optional[int]
    covered_to: Optional[int]
    history_exhausted: bool
    last_sync_at: Optional[int]


@dataclass
class InsertResult:
    inserted_count: int
    page_oldest_anchor: Optional[int]
    skipped_count: int = 0


class HistoryRepository:
    """QQ 历史消息 SQLite 仓储。"""

    def __init__(self, db_path: Optional[Path] = None):
        default_path = StarTools.get_data_dir("astrbot_plugin_angel_eye") / "qq_history_cache.db"
        self.db_path = db_path or default_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # 保持 SQLite 默认线程约束：连接仅可在创建它的线程中使用。
        # 本插件按单进程 + 单事件循环线程模型运行，不支持多线程共享连接。
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            # sync_state 必须一起清。只清消息却留着覆盖区间，库会凭空声称
            # "这段我查过"，查询零网络返回空结果——比没数据更糟。
            self._conn.executescript(
                "DROP TABLE IF EXISTS messages;"
                "DROP TABLE IF EXISTS senders;"
                "DROP TABLE IF EXISTS sync_state;"
            )
            # DROP 只把页还给 freelist，文件本身不缩小：不 VACUUM 的话旧库
            # 那几百 MB 会一直占着，体积目标永远达不到。这是一次性代价。
            try:
                self._conn.execute("VACUUM")
            except sqlite3.Error:
                logger.warning("AngelEye: 清理旧库后 VACUUM 失败，文件体积暂不回收")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                group_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                message_seq INTEGER,
                time INTEGER NOT NULL,
                sender_id TEXT NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (group_id, message_id)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS senders (
                group_id TEXT,
                user_id TEXT,
                nickname TEXT,
                role TEXT,
                PRIMARY KEY (group_id, user_id)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS sync_state (
                group_id TEXT PRIMARY KEY,
                oldest_seq INTEGER,
                covered_from INTEGER,
                covered_to INTEGER,
                history_exhausted INTEGER DEFAULT 0,
                last_sync_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_time
            ON messages(group_id, time);

            CREATE INDEX IF NOT EXISTS idx_user_time
            ON messages(group_id, sender_id, time);
            """
        )
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def get_sync_state(self, group_id: str) -> SyncState:
        row = self._conn.execute(
            """
            SELECT group_id, oldest_seq, covered_from, covered_to, history_exhausted, last_sync_at
            FROM sync_state
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()
        if row is None:
            return SyncState(
                group_id=group_id,
                oldest_seq=None,
                covered_from=None,
                covered_to=None,
                history_exhausted=False,
                last_sync_at=None,
            )

        return SyncState(
            group_id=row["group_id"],
            oldest_seq=row["oldest_seq"],
            covered_from=row["covered_from"],
            covered_to=row["covered_to"],
            history_exhausted=bool(row["history_exhausted"]),
            last_sync_at=row["last_sync_at"],
        )

    def upsert_sync_state(
        self,
        group_id: str,
        oldest_seq: Optional[int] = None,
        covered_from: Optional[int] = None,
        covered_to: Optional[int] = None,
        history_exhausted: Optional[bool] = None,
    ) -> None:
        now_ts = int(time.time())
        self._conn.execute(
            """
            INSERT INTO sync_state (
                group_id, oldest_seq, covered_from, covered_to, history_exhausted, last_sync_at
            ) VALUES (?, ?, ?, ?, COALESCE(?, 0), ?)
            ON CONFLICT(group_id) DO UPDATE SET
                oldest_seq = CASE
                    WHEN excluded.oldest_seq IS NULL THEN sync_state.oldest_seq
                    WHEN sync_state.oldest_seq IS NULL THEN excluded.oldest_seq
                    WHEN excluded.covered_from IS NOT NULL
                         AND (sync_state.covered_from IS NULL
                              OR excluded.covered_from <= sync_state.covered_from)
                        THEN excluded.oldest_seq
                    ELSE sync_state.oldest_seq
                END,
                covered_from = CASE
                    WHEN excluded.covered_from IS NULL THEN sync_state.covered_from
                    WHEN sync_state.covered_from IS NULL THEN excluded.covered_from
                    WHEN excluded.covered_from < sync_state.covered_from THEN excluded.covered_from
                    ELSE sync_state.covered_from
                END,
                covered_to = CASE
                    WHEN excluded.covered_to IS NULL THEN sync_state.covered_to
                    WHEN sync_state.covered_to IS NULL THEN excluded.covered_to
                    WHEN excluded.covered_to > sync_state.covered_to THEN excluded.covered_to
                    ELSE sync_state.covered_to
                END,
                history_exhausted = CASE
                    WHEN excluded.history_exhausted IS NULL THEN sync_state.history_exhausted
                    ELSE excluded.history_exhausted
                END,
                last_sync_at = excluded.last_sync_at
            """,
            (
                group_id,
                oldest_seq,
                covered_from,
                covered_to,
                int(history_exhausted) if history_exhausted is not None else None,
                now_ts,
            ),
        )
        self._conn.commit()

    def expand_coverage(
        self,
        group_id: str,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> None:
        """把一段已确认过的区间并进覆盖范围，只扩不缩。

        走 upsert_sync_state 已有的 MIN/MAX 语义：covered_from 只会变小，
        covered_to 只会变大，传进来的时间落在既有区间内时什么都不改。
        """
        if from_ts is None and to_ts is None:
            return
        self.upsert_sync_state(group_id=group_id, covered_from=from_ts, covered_to=to_ts)

    def update_coverage_from_messages(self, group_id: str) -> SyncState:
        row = self._conn.execute(
            """
            SELECT MIN(time) AS min_time, MAX(time) AS max_time
            FROM messages
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchone()

        min_time = row["min_time"]
        max_time = row["max_time"]

        if min_time is not None or max_time is not None:
            state = self.get_sync_state(group_id)
            cursor_time = self._conn.execute(
                "SELECT MIN(time) AS time FROM messages "
                "WHERE group_id = ? AND message_seq = ?",
                (group_id, state.oldest_seq),
            ).fetchone()["time"]
            self.upsert_sync_state(
                group_id=group_id,
                # Keep a confirmed page boundary when older pages share a second.
                oldest_seq=(self.rebuild_oldest_seq_from_messages(group_id)
                            if state.oldest_seq is None or cursor_time != min_time
                            else None),
                covered_from=min_time,
                covered_to=max_time,
            )

        return self.get_sync_state(group_id)

    def insert_messages(self, group_id: str, messages: Iterable[Dict[str, Any]]) -> InsertResult:
        payloads: List[tuple] = []
        senders: Dict[str, tuple] = {}
        skipped_count = 0
        page_oldest_anchor: Optional[int] = None

        message_list = list(messages)
        if message_list:
            page_oldest_anchor = self._extract_anchor(message_list[0])

        for msg in message_list:
            message_id = str(msg.get("message_id", ""))
            if not message_id:
                continue

            msg_time = int(msg.get("time", 0) or 0)
            if msg_time <= 0:
                continue

            # 入库时渲染一次，查询直接返回；空正文（例如只有引用段）不留行。
            body = _content_to_text(msg)
            if not body:
                skipped_count += 1
                continue

            sender = msg.get("sender", {}) or {}
            sender_id = str(sender.get("user_id") or "")
            nickname = str(sender.get("card") or sender.get("nickname") or "")
            role = str(sender.get("role") or "")

            payloads.append(
                (
                    group_id,
                    message_id,
                    self._extract_anchor(msg),
                    msg_time,
                    sender_id,
                    body,
                )
            )
            if sender_id:
                # 同一页里同一人出现多次时，最后一条的昵称覆盖前面的。
                senders[sender_id] = (group_id, sender_id, nickname, role)

        if not payloads:
            return InsertResult(
                inserted_count=0,
                page_oldest_anchor=page_oldest_anchor,
                skipped_count=skipped_count,
            )

        cursor = self._conn.executemany(
            """
            INSERT OR IGNORE INTO messages (
                group_id,
                message_id,
                message_seq,
                time,
                sender_id,
                body
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
        # 只统计真正插进 messages 的行：senders 的写入不算 inserted。
        inserted_count = max(cursor.rowcount, 0)
        if senders:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO senders (group_id, user_id, nickname, role)
                VALUES (?, ?, ?, ?)
                """,
                list(senders.values()),
            )
        self._conn.commit()
        return InsertResult(
            inserted_count=inserted_count,
            page_oldest_anchor=page_oldest_anchor,
            skipped_count=skipped_count,
        )

    def pending_forward_rows(self, group_id, start_time, end_time, user_ids, limit=64):
        """查询范围内尚未展开的转发：返回 [(message_id, body)]。"""
        where_sql, params = self._build_filter_where_clause(
            group_id, start_time, end_time, None, user_ids,
        )
        rows = self._conn.execute(
            f"SELECT m.message_id, m.body FROM messages m WHERE {where_sql} AND m.body LIKE ? "
            "ORDER BY m.time DESC, m.message_seq DESC LIMIT ?",
            [*params, f"%{FORWARD_PLACEHOLDER}%", int(limit)],
        ).fetchall()
        return [(str(row["message_id"]), str(row["body"])) for row in rows]

    def update_body(self, group_id: str, message_id: str, body: str) -> None:
        """改写一条消息的正文（转发展开后回写）。"""
        self._conn.execute(
            "UPDATE messages SET body = ? WHERE group_id = ? AND message_id = ?",
            (body, group_id, str(message_id)),
        )
        self._conn.commit()

    def query_messages(
        self,
        group_id: str,
        start_time: int,
        end_time: int,
        keywords: Optional[List[str]],
        user_ids: Optional[List[int]],
        limit: int,
        offset: int = 0,
        from_latest: bool = False,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._build_filter_where_clause(
            group_id=group_id,
            start_time=start_time,
            end_time=end_time,
            keywords=keywords,
            user_ids=user_ids,
        )
        select_sql = (
            "SELECT m.message_id, m.message_seq, m.time, m.sender_id, m.body, "
            "s.nickname AS nickname, s.role AS role "
            "FROM messages m "
            "LEFT JOIN senders s ON s.group_id = m.group_id AND s.user_id = m.sender_id "
        )
        if from_latest:
            rows = self._conn.execute(
                f"""
                SELECT * FROM (
                    {select_sql}
                    WHERE {where_sql}
                    ORDER BY m.time DESC, m.message_seq DESC
                    LIMIT ?
                ) t
                ORDER BY t.time ASC, t.message_seq ASC
                """,
                [*params, limit],
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""
                {select_sql}
                WHERE {where_sql}
                ORDER BY m.time ASC, m.message_seq ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        # 返回的是「正文 + 发送者」，不是原始消息链：抬头由 formatter 按
        # sender 现拼，正文直接取 body，不再解析 JSON 重渲染。
        return [
            {
                "group_id": group_id,
                "message_id": str(row["message_id"]),
                "message_seq": row["message_seq"],
                "time": row["time"],
                "sender_id": str(row["sender_id"]),
                "body": str(row["body"]),
                "sender": {
                    "user_id": str(row["sender_id"]),
                    "nickname": str(row["nickname"] or ""),
                    "role": str(row["role"] or ""),
                },
            }
            for row in rows
        ]

    def count_messages_in_range(
        self,
        group_id: str,
        start_time: int,
        end_time: int,
        keywords: Optional[List[str]],
        user_ids: Optional[List[int]],
    ) -> int:
        where_sql, params = self._build_filter_where_clause(
            group_id=group_id,
            start_time=start_time,
            end_time=end_time,
            keywords=keywords,
            user_ids=user_ids,
        )
        row = self._conn.execute(
            f"SELECT COUNT(1) AS c FROM messages m WHERE {where_sql}",
            params,
        ).fetchone()
        return int(row["c"] if row else 0)

    def rebuild_oldest_seq_from_messages(self, group_id: str) -> Optional[int]:
        row = self._conn.execute(
            """
            SELECT message_seq, message_id
            FROM messages
            WHERE group_id = ?
            ORDER BY time ASC, message_seq ASC
            LIMIT 1
            """,
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        if row["message_seq"] is not None:
            return int(row["message_seq"])

        fallback_id = row["message_id"]
        try:
            return int(fallback_id)
        except (TypeError, ValueError):
            return None

    def count_messages(self, group_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(1) AS c FROM messages WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        return int(row["c"] if row else 0)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    @staticmethod
    def extract_anchor(msg: Dict[str, Any]) -> Optional[int]:
        seq = msg.get("message_id") if msg.get("_history_backend") == "snowluma" else msg.get("message_seq")
        if seq is None:
            seq = msg.get("message_id")
        try:
            return int(seq)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_anchor(msg: Dict[str, Any]) -> Optional[int]:
        return HistoryRepository.extract_anchor(msg)

    @staticmethod
    def _escape_like_pattern(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _build_filter_where_clause(
        self,
        group_id: str,
        start_time: int,
        end_time: int,
        keywords: Optional[List[str]],
        user_ids: Optional[List[int]],
    ) -> tuple[str, List[Any]]:
        # 列名统一带 m. 前缀：查询要 LEFT JOIN senders，而 senders 也有
        # group_id 列，不加前缀会变成 ambiguous column。
        clauses = ["m.group_id = ?", "m.time >= ?", "m.time <= ?"]
        params: List[Any] = [group_id, start_time, end_time]

        if user_ids:
            normalized_user_ids = [str(uid) for uid in user_ids]
            placeholders = ",".join("?" for _ in normalized_user_ids)
            clauses.append(f"m.sender_id IN ({placeholders})")
            params.extend(normalized_user_ids)

        if keywords:
            keyword_clauses = []
            for keyword in keywords:
                keyword_clauses.append("m.body LIKE ? ESCAPE '\\'")
                escaped_keyword = self._escape_like_pattern(keyword)
                params.append(f"%{escaped_keyword}%")
            clauses.append(f"({' OR '.join(keyword_clauses)})")

        return " AND ".join(clauses), params
