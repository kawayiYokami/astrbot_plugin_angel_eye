"""angel_eye 历史库迁移。

本项目唯一允许出现旧格式字段名的地方。三条约束来自项目迁移规范：

1. 版本推进：一个版本一步，走完一步立刻落盘版本号，跑完不回头。
2. 迁移不允许失败：旧数据读不出来就跳过它，照样推进版本。
3. 不调业务层：裁剪规则在这里复制一份。业务以后怎么改都不影响历史迁移，
   迁移是历史快照，必须冻结。

除本模块外，任何代码都不该出现 raw_message / font / real_id / anonymous
这些旧字段名。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from astrbot.api import logger

#: 当前结构版本。每次改动 raw_json 结构或名字表就 +1，并在下面追加一档。
CURRENT_VERSION = 1

BATCH_SIZE = 2000

#: 只有这些段带 url，且那个 url 是小时级失效的临时下载凭证。
_MEDIA_TYPES = {"image", "video", "record", "file"}

#: 媒体段里唯一值得留的凭证，凭它以后能从协议端重新取图。
_FILEID_KEY = "fileid"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS group_names (
        group_id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_names (
        group_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        nickname TEXT NOT NULL DEFAULT '',
        card TEXT NOT NULL DEFAULT '',
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (group_id, user_id)
    )""",
)


# --------------------------------------------------------------------------
# 版本推进
# --------------------------------------------------------------------------


def _read_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def _write_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()


def run_migrations(conn: sqlite3.Connection, db_path: Optional[Path] = None) -> int:
    """把库推进到 CURRENT_VERSION，返回最终版本号。

    精确匹配起点版本，不用范围判断：停在 0 的库只走 v0→v1，
    不会因为将来加了 v2 就被跳级。不在表里的版本视为数据异常，直接停手。
    """
    _warn_existing_backups(db_path)

    while True:
        version = _read_version(conn)
        if version >= CURRENT_VERSION:
            return version

        if version == 0:
            _v0_to_v1(conn)
            version = 1
        else:
            logger.warning(
                "angel_eye 迁移：遇到未知的结构版本 %s，停在本版本，不做猜测。",
                version,
            )
            return version

        _write_version(conn, version)


# --------------------------------------------------------------------------
# 备份：只提示，从不替用户创建、也从不替用户删除
# --------------------------------------------------------------------------


def _warn_existing_backups(db_path: Optional[Path]) -> None:
    """启动时点名提醒：这里还躺着备份，删不删由用户自己定。"""
    if db_path is None:
        return
    files = [
        path
        for path in sorted(db_path.parent.glob(db_path.name + ".bak*"))
        if path.is_file() and not path.name.endswith(("-wal", "-shm"))
    ]
    if not files:
        return

    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    logger.warning(
        "angel_eye：发现 %d 个历史库备份，共 %.0f MB。插件不会替你删，"
        "确认数据无误后请自行处理：",
        len(files),
        total / 1048576,
    )
    for path in files:
        try:
            size = path.stat().st_size / 1048576
        except OSError:
            size = 0
        logger.warning("  %s（%.0f MB）", path.name, size)


# --------------------------------------------------------------------------
# v0 → v1：裁掉逐条重复的字段，名字抽到独立表
# --------------------------------------------------------------------------


def _v0_to_v1(conn: sqlite3.Connection) -> None:
    """把 messages.raw_json 换成瘦身格式，群名与人名抽到两张表。

    裁剪项：raw_message、font、real_id、anonymous、sender.sex、sender.age、
    group_name、sender.nickname、sender.card，以及媒体段里失效的 url。
    媒体 url 里的 fileid 保留下来，写成段内的 fileid 字段。
    """
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.commit()

    now = int(time.time())
    ids: List[int] = [
        row[0]
        for row in conn.execute("SELECT id FROM messages ORDER BY time ASC, id ASC")
    ]
    total = len(ids)
    logger.info("angel_eye 迁移 v0→v1：待处理 %s 条", total)

    updated = 0
    skipped = 0
    group_names: Dict[str, str] = {}
    user_names: Dict[Tuple[str, str], Tuple[str, str]] = {}

    for start in range(0, total, BATCH_SIZE):
        chunk = ids[start : start + BATCH_SIZE]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, group_id, raw_json FROM messages WHERE id IN ({marks})"
            " ORDER BY time ASC, id ASC",
            chunk,
        ).fetchall()

        payloads: List[Tuple[str, int]] = []
        for row_id, group_id, raw_json in rows:
            try:
                data = json.loads(raw_json)
            except (TypeError, ValueError):
                # 读不出来就跳过这一条，不重试也不回滚，版本照样推进。
                skipped += 1
                continue
            if not isinstance(data, dict):
                skipped += 1
                continue

            gid = str(group_id)
            data, group_name, nickname, card = _slim_message(data)
            if group_name:
                group_names[gid] = group_name

            user_id = ""
            sender = data.get("sender")
            if isinstance(sender, dict):
                user_id = str(sender.get("user_id") or "")
            if user_id and (nickname or card):
                # 按 time 升序遍历，后写的覆盖先写的＝保留最后一次见到的名字。
                user_names[(gid, user_id)] = (nickname, card)

            payloads.append((json.dumps(data, ensure_ascii=False), row_id))

        if payloads:
            conn.executemany("UPDATE messages SET raw_json = ? WHERE id = ?", payloads)
        if group_names:
            conn.executemany(
                "INSERT INTO group_names (group_id, name, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(group_id) DO UPDATE SET "
                "name = excluded.name, updated_at = excluded.updated_at",
                [(gid, name, now) for gid, name in group_names.items()],
            )
        if user_names:
            conn.executemany(
                "INSERT INTO user_names "
                "(group_id, user_id, nickname, card, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(group_id, user_id) DO UPDATE SET "
                "nickname = excluded.nickname, card = excluded.card, "
                "updated_at = excluded.updated_at",
                [
                    (gid, uid, nickname, card, now)
                    for (gid, uid), (nickname, card) in user_names.items()
                ],
            )
        conn.commit()

        updated += len(payloads)
        group_names.clear()
        user_names.clear()

        done = min(start + BATCH_SIZE, total)
        if done % 80000 < BATCH_SIZE:
            logger.info("angel_eye 迁移 v0→v1：进度 %s/%s", done, total)

    logger.info(
        "angel_eye 迁移 v0→v1 完成：处理 %s 条，跳过 %s 条",
        updated,
        skipped,
    )
    _compact(conn)


def _compact(conn: sqlite3.Connection) -> None:
    """挤掉页内空隙。

    UPDATE 把每条 raw_json 改小之后，省下的空间留在原页的空隙里，
    文件一个字节都不会小（实测 freelist 只有 3 MiB，而页内空隙占了 200 MiB）。
    必须重建库才能拿到真实体积。VACUUM 失败只记日志，不阻断迁移。
    """
    try:
        conn.commit()
        # WAL 模式下 VACUUM 会把重建的整份库写进 WAL，主文件一个字节不动，
        # checkpoint(TRUNCATE) 也合不回去（实测主库仍是原大小、WAL 涨到等身）。
        # 切到 DELETE 模式，VACUUM 才直接重写主文件。
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("VACUUM")
    except sqlite3.Error as exc:
        logger.warning("angel_eye 迁移：整理存储失败（%s），数据已迁移完成。", exc)
    finally:
        try:
            # 业务层建连接时本来就会设回 WAL，这里只是不让库停在别的模式上。
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass


def _slim_message(msg: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str, str]:
    """就地裁剪一条消息，返回 (消息, 群名, 昵称, 群名片)。"""
    group_name = str(msg.pop("group_name", "") or "")
    for key in ("raw_message", "font", "real_id", "anonymous"):
        msg.pop(key, None)

    nickname = card = ""
    sender = msg.get("sender")
    if isinstance(sender, dict):
        nickname = str(sender.pop("nickname", "") or "")
        card = str(sender.pop("card", "") or "")
        sender.pop("sex", None)
        sender.pop("age", None)

    chain = msg.get("message")
    if isinstance(chain, list):
        msg["message"] = [_slim_segment(segment) for segment in chain]
    return msg, group_name, nickname, card


def _slim_segment(segment: Any) -> Any:
    """媒体段的 url 换成 fileid，其他段原样返回。"""
    if not isinstance(segment, dict):
        return segment
    info = segment.get("data")
    if not isinstance(info, dict) or "url" not in info:
        return segment
    if segment.get("type") not in _MEDIA_TYPES:
        return segment

    info = dict(info)
    url = info.pop("url", None)
    fileid = _fileid_from_url(url)
    if fileid:
        info.setdefault(_FILEID_KEY, fileid)
    slim = dict(segment)
    slim["data"] = info
    return slim


def _fileid_from_url(url: Any) -> str:
    """url 是带签名参数的临时链接，只取其中的 fileid。"""
    if not isinstance(url, str) or not url:
        return ""
    try:
        values = parse_qs(urlsplit(url).query).get(_FILEID_KEY) or []
    except ValueError:
        return ""
    return str(values[0]) if values else ""
