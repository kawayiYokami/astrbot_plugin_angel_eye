"""迁移模块的单测。

只测当下这一档：v0→v1 在旧格式数据上做对没有。
不测「几年前的数据今天还能不能迁」，那种测试价值为零。
"""

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_angel_eye.migrations import (
    CURRENT_VERSION,
    run_migrations,
)

MEDIA_URL = (
    "https://multimedia.nt.qq.com.cn/download?appid=1407"
    "&fileid=EhS3btvULMo7nKXzJqf9JWTEY1xN9hjtzmgg"
    "&spec=0&rkey=CAISMMe3ttx8DghlKQ4VLPYgsCH560EK"
)


def make_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE messages (
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
        )
        """
    )
    return conn


def insert(conn, group_id, message_id, time, payload):
    conn.execute(
        "INSERT INTO messages (group_id, message_id, message_seq, time, user_id,"
        " nickname, search_text, raw_json, created_at)"
        " VALUES (?, ?, 0, ?, '', '', '', ?, 0)",
        (group_id, message_id, time, json.dumps(payload, ensure_ascii=False)),
    )


def legacy_message(**overrides):
    msg = {
        "time": 1795739707,
        "message_type": "group",
        "sub_type": "normal",
        "message_id": "7769094512870314544",
        "message_seq": 437944,
        "group_id": 830624502,
        "group_name": "绝区零&一条龙开发社群",
        "user_id": 603626679,
        "message": [{"type": "text", "data": {"text": "会逃逸，很烦"}}],
        "raw_message": "会逃逸，很烦",
        "font": 0,
        "sender": {
            "user_id": 603626679,
            "nickname": "洛伊",
            "card": "圣檬",
            "role": "member",
            "sex": "unknown",
            "age": 0,
        },
        "anonymous": None,
        "real_id": "7769094512870314544",
    }
    msg.update(overrides)
    return msg


class MigrationTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "history.db"
        self.conn = make_db(self.path)
        self.addCleanup(self.conn.close)

    def raw(self, message_id):
        row = self.conn.execute(
            "SELECT raw_json FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return json.loads(row[0])

    # --- 版本推进 -----------------------------------------------------

    def test_version_advances_from_zero(self):
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        self.assertEqual(run_migrations(self.conn), CURRENT_VERSION)
        self.assertEqual(
            self.conn.execute("PRAGMA user_version").fetchone()[0], CURRENT_VERSION
        )

    def test_empty_db_advances_without_error(self):
        self.assertEqual(run_migrations(self.conn), CURRENT_VERSION)

    def test_second_run_is_noop(self):
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        run_migrations(self.conn)
        first = self.raw("m1")
        run_migrations(self.conn)
        self.assertEqual(self.raw("m1"), first)

    # --- 裁剪字段 -----------------------------------------------------

    def test_legacy_fields_removed(self):
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        run_migrations(self.conn)

        msg = self.raw("m1")
        for key in ("raw_message", "font", "real_id", "anonymous", "group_name"):
            self.assertNotIn(key, msg)
        sender = msg["sender"]
        for key in ("sex", "age", "nickname", "card"):
            self.assertNotIn(key, sender)

    def test_content_preserved(self):
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        run_migrations(self.conn)

        msg = self.raw("m1")
        self.assertEqual(
            msg["message"], [{"type": "text", "data": {"text": "会逃逸，很烦"}}]
        )
        self.assertEqual(msg["sender"]["user_id"], 603626679)
        self.assertEqual(msg["sender"]["role"], "member")
        self.assertEqual(msg["message_seq"], 437944)
        self.assertEqual(msg["group_id"], 830624502)

    # --- 媒体段 -------------------------------------------------------

    def test_media_url_becomes_fileid(self):
        msg = legacy_message(
            message=[
                {
                    "type": "image",
                    "data": {
                        "url": MEDIA_URL,
                        "file": "9CA6.jpg",
                        "summary": "[动画表情]",
                    },
                }
            ]
        )
        insert(self.conn, "1", "m1", 100, msg)
        self.conn.commit()
        run_migrations(self.conn)

        data = self.raw("m1")["message"][0]["data"]
        self.assertNotIn("url", data)
        self.assertEqual(
            data["fileid"],
            "EhS3btvULMo7nKXzJqf9JWTEY1xN9hjtzmgg",
        )
        self.assertEqual(data["file"], "9CA6.jpg")
        self.assertEqual(data["summary"], "[动画表情]")

    def test_non_media_url_untouched(self):
        msg = legacy_message(
            message=[
                {
                    "type": "json",
                    "data": {"url": "https://example.com/card", "data": "{}"},
                }
            ]
        )
        insert(self.conn, "1", "m1", 100, msg)
        self.conn.commit()
        run_migrations(self.conn)

        data = self.raw("m1")["message"][0]["data"]
        self.assertEqual(data["url"], "https://example.com/card")

    def test_media_without_fileid_drops_url(self):
        msg = legacy_message(
            message=[{"type": "image", "data": {"url": "https://x/y.jpg", "file": "y"}}]
        )
        insert(self.conn, "1", "m1", 100, msg)
        self.conn.commit()
        run_migrations(self.conn)

        data = self.raw("m1")["message"][0]["data"]
        self.assertNotIn("url", data)
        self.assertNotIn("fileid", data)

    # --- 名字表 -------------------------------------------------------

    def test_names_extracted(self):
        insert(self.conn, "830624502", "m1", 100, legacy_message())
        self.conn.commit()
        run_migrations(self.conn)

        group = self.conn.execute(
            "SELECT name FROM group_names WHERE group_id = '830624502'"
        ).fetchone()
        self.assertEqual(group[0], "绝区零&一条龙开发社群")

        user = self.conn.execute(
            "SELECT nickname, card FROM user_names"
            " WHERE group_id = '830624502' AND user_id = '603626679'"
        ).fetchone()
        self.assertEqual(tuple(user), ("洛伊", "圣檬"))

    def test_last_name_wins_in_time_order(self):
        first = legacy_message()
        first["sender"] = dict(first["sender"], nickname="旧名", card="旧卡")
        second = legacy_message()
        second["sender"] = dict(second["sender"], nickname="新名", card="新卡")
        # 故意乱序插入，迁移必须按时���升序覆盖
        insert(self.conn, "1", "m2", 200, second)
        insert(self.conn, "1", "m1", 100, first)
        self.conn.commit()
        run_migrations(self.conn)

        row = self.conn.execute("SELECT nickname, card FROM user_names").fetchone()
        self.assertEqual(tuple(row), ("新名", "新卡"))

    def test_names_not_blanked_by_later_message(self):
        """同一人后一条消息没有名字时，不能用空值覆盖已抽到的名字。"""
        named = legacy_message()
        unnamed = legacy_message()
        unnamed["sender"] = {"user_id": 603626679, "role": "member"}
        insert(self.conn, "1", "m1", 100, named)
        insert(self.conn, "1", "m2", 200, unnamed)
        self.conn.commit()
        run_migrations(self.conn)

        row = self.conn.execute("SELECT nickname, card FROM user_names").fetchone()
        self.assertEqual(tuple(row), ("洛伊", "圣檬"))

    # --- 读失败 -------------------------------------------------------

    def test_bad_json_skipped_but_version_advances(self):
        self.conn.execute(
            "INSERT INTO messages (group_id, message_id, message_seq, time, user_id,"
            " nickname, search_text, raw_json, created_at)"
            " VALUES ('1', 'bad', 1, 50, '', '', '', '{not json', 0)"
        )
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()

        self.assertEqual(run_migrations(self.conn), CURRENT_VERSION)
        # 坏条原样留着，好条照常裁剪
        broken = self.conn.execute(
            "SELECT raw_json FROM messages WHERE message_id = 'bad'"
        ).fetchone()
        self.assertEqual(broken[0], "{not json")
        self.assertNotIn("raw_message", self.raw("m1"))

    def test_batch_boundary(self):
        """跨批处理仍然完整，不因分批丢条或漏名字。"""
        from astrbot_plugin_angel_eye import migrations

        original = migrations.BATCH_SIZE
        migrations.BATCH_SIZE = 3
        self.addCleanup(setattr, migrations, "BATCH_SIZE", original)

        for i in range(10):
            insert(self.conn, "1", f"m{i}", 100 + i, legacy_message())
        self.conn.commit()
        run_migrations(self.conn)

        remaining = self.conn.execute(
            "SELECT count(*) FROM messages WHERE raw_json LIKE '%raw_message%'"
        ).fetchone()[0]
        self.assertEqual(remaining, 0)

    # --- 备份提示 -----------------------------------------------------

    def test_no_path_no_crash(self):
        """没传库路径时不提示，也不报错。"""
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        self.assertEqual(run_migrations(self.conn), CURRENT_VERSION)

    def test_startup_warning_never_deletes_backups(self):
        """提示函数只读文件，一个都不许删。"""
        from astrbot_plugin_angel_eye import migrations

        fake = self.path.with_name(self.path.name + ".bak-v0-20260101-000000")
        fake.write_bytes(b"x")
        self.addCleanup(lambda: fake.exists() and fake.unlink())

        migrations._warn_existing_backups(self.path)
        self.assertTrue(fake.exists())

    def test_migration_never_creates_backup(self):
        """迁移不替用户造备份文件。"""
        insert(self.conn, "1", "m1", 100, legacy_message())
        self.conn.commit()
        run_migrations(self.conn, self.path)
        self.assertEqual(list(self.path.parent.glob(self.path.name + ".bak*")), [])

    def test_rerun_after_partial_migration(self):
        """中途崩过：一半已裁剪、一半还是旧格式，版本仍为 0。

        重跑必须把剩下那半补齐，而且不能把上一次已抽走的名字冲成空。
        """
        from astrbot_plugin_angel_eye import migrations

        original = migrations.BATCH_SIZE
        migrations.BATCH_SIZE = 3
        self.addCleanup(setattr, migrations, "BATCH_SIZE", original)

        # 先人为制造「前 4 条已裁剪 + 名字已入表」的半成品状态
        for i in range(4):
            insert(self.conn, "830624502", f"m{i}", 100 + i, legacy_message())
        self.conn.commit()
        migrations.run_migrations(self.conn)
        self.conn.execute("PRAGMA user_version = 0")
        self.conn.commit()

        # 再补 4 条旧格式，模拟崩溃后又有数据进来
        for i in range(4, 8):
            insert(self.conn, "830624502", f"m{i}", 100 + i, legacy_message())
        self.conn.commit()

        self.assertEqual(run_migrations(self.conn), CURRENT_VERSION)

        leftover = self.conn.execute(
            "SELECT count(*) FROM messages WHERE raw_json LIKE '%raw_message%'"
        ).fetchone()[0]
        self.assertEqual(leftover, 0)

        row = self.conn.execute(
            "SELECT nickname, card FROM user_names"
            " WHERE group_id = '830624502' AND user_id = '603626679'"
        ).fetchone()
        self.assertEqual(tuple(row), ("洛伊", "圣檬"))

        group = self.conn.execute(
            "SELECT name FROM group_names WHERE group_id = '830624502'"
        ).fetchone()
        self.assertEqual(group[0], "绝区零&一条龙开发社群")


if __name__ == "__main__":
    unittest.main()
