"""
Angel Eye 插件 - QQ 群聊历史仓储层
负责 SQLite 持久化、去重写入、同步状态维护与本地查询
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from astrbot.core.star.star_tools import StarTools


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
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                message_seq INTEGER,
                time INTEGER NOT NULL,
                user_id TEXT,
                nickname TEXT,
                search_text TEXT,
                raw_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(group_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS sync_state (
                group_id TEXT PRIMARY KEY,
                oldest_seq INTEGER,
                covered_from INTEGER,
                covered_to INTEGER,
                history_exhausted INTEGER DEFAULT 0,
                last_sync_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_messages_group_time
            ON messages(group_id, time);

            CREATE INDEX IF NOT EXISTS idx_messages_group_user_time
            ON messages(group_id, user_id, time);
            """
        )
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
                    WHEN excluded.oldest_seq < sync_state.oldest_seq THEN excluded.oldest_seq
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
            self.upsert_sync_state(
                group_id=group_id,
                covered_from=min_time,
                covered_to=max_time,
            )

        return self.get_sync_state(group_id)

    def insert_messages(self, group_id: str, messages: Iterable[Dict[str, Any]]) -> InsertResult:
        payloads: List[tuple] = []
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

            sender = msg.get("sender", {}) or {}
            user_id = str(sender.get("user_id", "")) or None
            nickname = str(sender.get("nickname", "")) or None
            message_seq = self._extract_anchor(msg)
            search_text = self._build_search_text(msg)

            payloads.append(
                (
                    group_id,
                    message_id,
                    message_seq,
                    msg_time,
                    user_id,
                    nickname,
                    search_text,
                    json.dumps(msg, ensure_ascii=False),
                    int(time.time()),
                )
            )

        if not payloads:
            return InsertResult(inserted_count=0, page_oldest_anchor=page_oldest_anchor)

        before_changes = self._conn.total_changes
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO messages (
                group_id,
                message_id,
                message_seq,
                time,
                user_id,
                nickname,
                search_text,
                raw_json,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
        self._conn.commit()
        inserted_count = self._conn.total_changes - before_changes
        return InsertResult(inserted_count=inserted_count, page_oldest_anchor=page_oldest_anchor)

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
        if from_latest:
            rows = self._conn.execute(
                f"""
                SELECT raw_json
                FROM (
                    SELECT raw_json, time
                    FROM messages
                    WHERE {where_sql}
                    ORDER BY time DESC
                    LIMIT ?
                ) t
                ORDER BY time ASC
                """,
                [*params, limit],
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""
                SELECT raw_json
                FROM messages
                WHERE {where_sql}
                ORDER BY time ASC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            try:
                result.append(json.loads(row["raw_json"]))
            except json.JSONDecodeError:
                continue
        return result

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
            f"SELECT COUNT(1) AS c FROM messages WHERE {where_sql}",
            params,
        ).fetchone()
        return int(row["c"] if row else 0)

    def rebuild_oldest_seq_from_messages(self, group_id: str) -> Optional[int]:
        row = self._conn.execute(
            """
            SELECT message_seq, message_id
            FROM messages
            WHERE group_id = ?
            ORDER BY time ASC, id ASC
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
        seq = msg.get("message_seq")
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

    @staticmethod
    def _build_search_text(msg: Dict[str, Any]) -> str:
        text_parts: List[str] = []

        message_chain = msg.get("message", [])
        if isinstance(message_chain, list):
            for component in message_chain:
                if not isinstance(component, dict):
                    continue
                if component.get("type") == "text":
                    text_value = component.get("data", {}).get("text", "")
                    if text_value:
                        text_parts.append(str(text_value))

        sender = msg.get("sender", {}) or {}
        nickname = sender.get("nickname")
        user_id = sender.get("user_id")
        if nickname:
            text_parts.append(str(nickname))
        if user_id:
            text_parts.append(str(user_id))

        return " ".join(text_parts)

    def _build_filter_where_clause(
        self,
        group_id: str,
        start_time: int,
        end_time: int,
        keywords: Optional[List[str]],
        user_ids: Optional[List[int]],
    ) -> tuple[str, List[Any]]:
        clauses = ["group_id = ?", "time >= ?", "time <= ?"]
        params: List[Any] = [group_id, start_time, end_time]

        if user_ids:
            normalized_user_ids = [str(uid) for uid in user_ids]
            placeholders = ",".join("?" for _ in normalized_user_ids)
            clauses.append(f"user_id IN ({placeholders})")
            params.extend(normalized_user_ids)

        if keywords:
            keyword_clauses = []
            for keyword in keywords:
                keyword_clauses.append("search_text LIKE ? ESCAPE '\\'")
                escaped_keyword = self._escape_like_pattern(keyword)
                params.append(f"%{escaped_keyword}%")
            clauses.append(f"({' OR '.join(keyword_clauses)})")

        return " AND ".join(clauses), params
