"""SQLite 持久化：目录导入、行程不可变版本、乐观锁、运行记录与账号归属。"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from uuid import uuid4

from app.auth import DISABLED_PASSWORD_HASH
from app.core.models import AgentRun, Guide, ItineraryPlan, Spot

# 历史匿名数据统一归属到该保留账户：无法登录，仅用于保留证据与事后改判。
LEGACY_USER_ID = "user-legacy"
LEGACY_USERNAME = "legacy"


class NotFoundError(LookupError):
    pass


class UsernameTakenError(ValueError):
    pass


class VersionConflictError(RuntimeError):
    def __init__(self, expected: int, current: int):
        self.expected = expected
        self.current = current
        super().__init__(f"版本冲突：请求基于 v{expected}，当前已是 v{current}")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_ci
                ON users(lower(username));
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS login_failures (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                fail_count INTEGER NOT NULL DEFAULT 0,
                blocked_until TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(scope, key)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS register_attempts (
                key TEXT PRIMARY KEY,
                window_start TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                actor_key TEXT,
                event_type TEXT NOT NULL,
                ip TEXT,
                result TEXT NOT NULL,
                detail TEXT,
                prev_hash TEXT,
                entry_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_key, created_at);
            CREATE TABLE IF NOT EXISTS spots (
                id TEXT PRIMARY KEY,
                city TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_spots_city ON spots(city);
            CREATE TABLE IF NOT EXISTS guides (
                id TEXT PRIMARY KEY,
                city TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_guides_city ON guides(city);
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES users(id),
                current_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trip_versions (
                trip_id TEXT NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL REFERENCES users(id),
                version INTEGER NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(trip_id, version)
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY,
                owner_id TEXT REFERENCES users(id),
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                trip_id TEXT,
                version INTEGER,
                error TEXT,
                request_json TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
            conn.execute(
                "INSERT OR IGNORE INTO users(id,username,password_hash,is_active,created_at) "
                "VALUES(?,?,?,0,?)",
                (LEGACY_USER_ID, LEGACY_USERNAME, DISABLED_PASSWORD_HASH, utcnow()),
            )
            self._ensure_owner_columns(conn)
            self._ensure_audit_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trips_owner ON trips(owner_id, updated_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_owner ON agent_runs(owner_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_versions_owner ON trip_versions(owner_id)"
            )

    @staticmethod
    def _ensure_audit_columns(conn: sqlite3.Connection):
        """旧库增量补齐审计哈希列；新库建表时已包含。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_events)")}
        for column in ("prev_hash", "entry_hash"):
            if column not in columns:
                conn.execute(f"ALTER TABLE audit_events ADD COLUMN {column} TEXT")

    @staticmethod
    def _ensure_owner_columns(conn: sqlite3.Connection):
        """旧库在线补列：ADD COLUMN 不能带 NOT NULL，因此先加可空列再回填。"""
        for table in ("trips", "trip_versions", "agent_runs"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "owner_id" in columns:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN owner_id TEXT")
            conn.execute(
                f"UPDATE {table} SET owner_id=? WHERE owner_id IS NULL",
                (LEGACY_USER_ID,),
            )

    def create_user(self, username: str, password_hash: str) -> dict:
        user = {
            "id": f"user-{uuid4().hex}",
            "username": username,
            "is_active": 1,
            "created_at": utcnow(),
        }
        with self.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users(id,username,password_hash,is_active,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (user["id"], username, password_hash, 1, user["created_at"]),
                )
            except sqlite3.IntegrityError as exc:
                raise UsernameTakenError(f"用户名已被占用：{username}") from exc
        return user

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id,username,password_hash,is_active FROM users "
                "WHERE lower(username)=lower(?)",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def create_session(self, user_id: str, token_hash: str, expires_at: str) -> str:
        session_id = f"session-{uuid4().hex}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions(id,user_id,token_hash,expires_at,created_at) "
                "VALUES(?,?,?,?,?)",
                (session_id, user_id, token_hash, expires_at, utcnow()),
            )
        return session_id

    def get_user_by_session(self, token_hash: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT u.id,u.username FROM sessions s JOIN users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1",
                (token_hash, utcnow()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

    def delete_sessions_for_user(self, user_id: str) -> int:
        """按用户批量失效：sessions 表本身以 user_id 归属，可直接整批删除。"""
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return cursor.rowcount

    def get_user_exact(self, username: str) -> Optional[dict]:
        """精确匹配用户名（区分大小写、不支持通配），供管理员脚本防误操作。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id,username,is_active FROM users WHERE username=?", (username,)
            ).fetchone()
        return dict(row) if row else None

    def reset_password_and_revoke_sessions(
        self, username: str, password_hash: str
    ) -> tuple[str, int]:
        """重置密码并吊销该用户全部会话，同一事务内完成；用户名必须精确匹配。"""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT id FROM users WHERE username=?", (username,)
                ).fetchone()
                if not row:
                    conn.rollback()
                    raise NotFoundError(f"用户不存在（需精确匹配）：{username}")
                user_id = row["id"]
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id)
                )
                cursor = conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                revoked = cursor.rowcount
                # 重置后立即解除账号维度的登录退避，否则用户拿到新密码仍会被锁。
                conn.execute(
                    "DELETE FROM login_failures WHERE scope='account' AND key=?",
                    (username.strip().lower(),),
                )
                conn.commit()
            except NotFoundError:
                raise
            except Exception:
                conn.rollback()
                raise
        return user_id, revoked

    def register_attempt(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """注册配额计数落库，多实例共享；返回 (是否允许, 建议等待秒数)。

        固定窗口计数：调用方按用途分成“重名失败”和“成功建号”两个 key，
        避免共享出口 IP 下的正常注册把失败配额占满。
        """
        now = datetime.now(timezone.utc)
        window = max(1, int(window_seconds))
        limit = max(1, int(limit))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT window_start,count FROM register_attempts WHERE key=?", (key,)
                ).fetchone()
                elapsed = None
                if row is not None:
                    started = datetime.fromisoformat(row["window_start"])
                    elapsed = (now - started).total_seconds()
                if row is None or elapsed is None or elapsed >= window:
                    conn.execute(
                        "INSERT INTO register_attempts(key,window_start,count) VALUES(?,?,1) "
                        "ON CONFLICT(key) DO UPDATE SET window_start=excluded.window_start,count=1",
                        (key, now.isoformat()),
                    )
                    conn.commit()
                    return True, 0
                count = row["count"] + 1
                conn.execute(
                    "UPDATE register_attempts SET count=? WHERE key=?", (count, key)
                )
                conn.commit()
                if count > limit:
                    return False, max(1, math.ceil(window - elapsed))
                return True, 0
            except Exception:
                conn.rollback()
                raise

    def register_quota_exceeded(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """只读检查注册配额是否用尽；返回 (是否超限, 建议等待秒数)。"""
        now = datetime.now(timezone.utc)
        window = max(1, int(window_seconds))
        limit = max(1, int(limit))
        with self.connect() as conn:
            row = conn.execute(
                "SELECT window_start,count FROM register_attempts WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return False, 0
        elapsed = (now - datetime.fromisoformat(row["window_start"])).total_seconds()
        if elapsed >= window:
            return False, 0
        if row["count"] >= limit:
            return True, max(1, math.ceil(window - elapsed))
        return False, 0

    def delete_expired_sessions(self) -> int:
        with self.connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at<=?", (utcnow(),))
        return cursor.rowcount

    def delete_stale_register_attempts(self, older_than_seconds: int = 86400) -> int:
        """删除已过窗口的注册配额行：窗口一旦过期就等价于不存在，可安全清理。"""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM register_attempts WHERE window_start<?", (cutoff,)
            )
        return cursor.rowcount

    def count_stale_register_attempts(self, older_than_seconds: int = 86400) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT count(*) FROM register_attempts WHERE window_start<?", (cutoff,)
                ).fetchone()[0]
            )

    def delete_stale_login_failures(self, older_than_seconds: int = 86400) -> int:
        """删除长期无活动的失败计数，避免随机用户名探测把表撑大。"""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM login_failures WHERE updated_at<?", (cutoff,)
            )
        return cursor.rowcount

    def count_stale_login_failures(self, older_than_seconds: int = 86400) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT count(*) FROM login_failures WHERE updated_at<?", (cutoff,)
            ).fetchone()
        return int(row[0])

    def get_login_failure(self, scope: str, key: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT fail_count,blocked_until,updated_at FROM login_failures "
                "WHERE scope=? AND key=?",
                (scope, key),
            ).fetchone()
        return dict(row) if row else None

    def increment_login_failure(self, scope: str, key: str) -> int:
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT fail_count FROM login_failures WHERE scope=? AND key=?",
                    (scope, key),
                ).fetchone()
                count = (row["fail_count"] if row else 0) + 1
                conn.execute(
                    "INSERT INTO login_failures(scope,key,fail_count,blocked_until,updated_at) "
                    "VALUES(?,?,?,NULL,?) ON CONFLICT(scope,key) DO UPDATE SET "
                    "fail_count=excluded.fail_count,blocked_until=NULL,updated_at=excluded.updated_at",
                    (scope, key, count, now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return count

    def set_login_block(self, scope: str, key: str, blocked_until: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE login_failures SET blocked_until=?,updated_at=? WHERE scope=? AND key=?",
                (blocked_until, utcnow(), scope, key),
            )

    def clear_login_failures(self, scope: str, key: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM login_failures WHERE scope=? AND key=?", (scope, key)
            )

    def replace_spots(self, spots: list[Spot]):
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for spot in spots:
                    conn.execute(
                        "INSERT INTO spots(id,city,payload,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET city=excluded.city,payload=excluded.payload,updated_at=excluded.updated_at",
                        (spot.id, spot.city, spot.model_dump_json(), now),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def upsert_guides(self, guides: list[Guide]):
        self.import_catalog("guides", guides)

    def list_spots(self, city: Optional[str] = None) -> list[Spot]:
        sql = "SELECT payload FROM spots"
        params: tuple = ()
        if city:
            sql += " WHERE city=?"
            params = (city,)
        sql += " ORDER BY id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Spot.model_validate_json(row["payload"]) for row in rows]

    def list_guides(self, city: Optional[str] = None) -> list[Guide]:
        sql = "SELECT payload FROM guides"
        params: tuple = ()
        if city:
            sql += " WHERE city=?"
            params = (city,)
        sql += " ORDER BY id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Guide.model_validate_json(row["payload"]) for row in rows]

    def import_catalog(
        self, kind: Literal["spots", "guides"], records: list[Spot] | list[Guide]
    ) -> dict[str, int]:
        """在单个事务中幂等 upsert；完全相同记录跳过，不改写行程快照。"""
        if kind not in {"spots", "guides"}:
            raise ValueError("未知目录类型")
        model = Spot if kind == "spots" else Guide
        now = utcnow()
        counts = {"inserted": 0, "updated": 0, "unchanged": 0}
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for record in records:
                    row = conn.execute(
                        f"SELECT payload FROM {kind} WHERE id=?", (record.id,)
                    ).fetchone()
                    payload = record.model_dump(mode="json")
                    if row is None:
                        counts["inserted"] += 1
                    elif model.model_validate_json(row["payload"]).model_dump(mode="json") == payload:
                        counts["unchanged"] += 1
                        continue
                    else:
                        counts["updated"] += 1
                    conn.execute(
                        f"INSERT INTO {kind}(id,city,payload,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET city=excluded.city,payload=excluded.payload,updated_at=excluded.updated_at",
                        (
                            record.id,
                            record.city,
                            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                            now,
                        ),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return counts

    def create_run(
        self, kind: str, request_payload: dict, trip_id: Optional[str] = None,
        *, owner_id: str,
    ) -> AgentRun:
        now = datetime.now(timezone.utc)
        run = AgentRun(
            id=f"run-{uuid4().hex}", kind=kind, status="queued",
            trip_id=trip_id, created_at=now, updated_at=now,
        )
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO agent_runs(id,owner_id,kind,status,trip_id,request_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (run.id, owner_id, kind, run.status, trip_id,
                 json.dumps(request_payload, ensure_ascii=False, default=str),
                 run.created_at.isoformat(), run.updated_at.isoformat()),
            )
        return run

    def update_run(
        self, run_id: str, status: str, *, trip_id: Optional[str] = None,
        version: Optional[int] = None, error: Optional[str] = None,
        result: Optional[dict] = None,
    ):
        now = utcnow()
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE agent_runs SET status=?,trip_id=COALESCE(?,trip_id),version=?,error=?,"
                "result_json=?,updated_at=? WHERE id=?",
                (status, trip_id, version, error,
                 json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                 now, run_id),
            )
            if cursor.rowcount != 1:
                raise NotFoundError(f"运行记录不存在：{run_id}")

    def list_trips(self, owner_id: str, limit: int = 20) -> list[dict]:
        """当前用户最近更新的行程摘要，供 Web 工作台恢复与继续规划。"""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT t.id AS trip_id, t.current_version, v.created_at, v.payload
                   FROM trips t JOIN trip_versions v
                     ON v.trip_id=t.id AND v.version=t.current_version
                   WHERE t.owner_id=?
                   ORDER BY v.created_at DESC LIMIT ?""",
                (owner_id, limit),
            ).fetchall()
        summaries = []
        for row in rows:
            plan = json.loads(row["payload"])
            spec = plan["spec"]
            summaries.append({
                "trip_id": row["trip_id"], "version": row["current_version"],
                "destination": spec["destination"], "start_date": spec["start_date"],
                "end_date": spec["end_date"], "updated_at": row["created_at"],
                "day_count": len(plan["days"]),
            })
        return summaries

    def get_run(self, run_id: str, *, owner_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE id=? AND owner_id=?", (run_id, owner_id)
            ).fetchone()
        if not row:
            raise NotFoundError(f"运行记录不存在：{run_id}")
        result = dict(row)
        for key in ("request_json", "result_json"):
            if result.get(key):
                result[key] = json.loads(result[key])
        return result

    def save_new_plan(
        self, plan: ItineraryPlan, *, owner_id: str
    ) -> ItineraryPlan:
        plan.trip_id = plan.trip_id or f"trip-{uuid4().hex}"
        plan.version = 1
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO trips(id,owner_id,current_version,created_at,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (plan.trip_id, owner_id, 1, now, now),
                )
                conn.execute(
                    "INSERT INTO trip_versions(trip_id,owner_id,version,payload,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (plan.trip_id, owner_id, 1, plan.model_dump_json(), now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return plan

    def save_next_plan(
        self, trip_id: str, expected_version: int, plan: ItineraryPlan,
        *, owner_id: str,
    ) -> ItineraryPlan:
        now = utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT current_version FROM trips WHERE id=? AND owner_id=?",
                    (trip_id, owner_id),
                ).fetchone()
                if not row:
                    raise NotFoundError(f"行程不存在：{trip_id}")
                current = int(row["current_version"])
                if current != expected_version:
                    raise VersionConflictError(expected_version, current)
                next_version = current + 1
                plan.trip_id = trip_id
                plan.version = next_version
                conn.execute(
                    "INSERT INTO trip_versions(trip_id,owner_id,version,payload,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (trip_id, owner_id, next_version, plan.model_dump_json(), now),
                )
                cursor = conn.execute(
                    "UPDATE trips SET current_version=?,updated_at=? "
                    "WHERE id=? AND owner_id=? AND current_version=?",
                    (next_version, now, trip_id, owner_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise VersionConflictError(expected_version, current)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return plan

    def get_plan(
        self, trip_id: str, version: Optional[int] = None, *, owner_id: str,
    ) -> ItineraryPlan:
        with self.connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT v.payload FROM trip_versions v JOIN trips t ON t.id=v.trip_id "
                    "AND t.current_version=v.version WHERE v.trip_id=? AND v.owner_id=?",
                    (trip_id, owner_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT payload FROM trip_versions WHERE trip_id=? AND version=? "
                    "AND owner_id=?",
                    (trip_id, version, owner_id),
                ).fetchone()
        if not row:
            raise NotFoundError(f"行程或版本不存在：{trip_id} / {version or 'current'}")
        return ItineraryPlan.model_validate_json(row["payload"])

    def list_versions(self, trip_id: str, *, owner_id: str) -> list[dict]:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM trips WHERE id=? AND owner_id=?", (trip_id, owner_id)
            ).fetchone()
            if not exists:
                raise NotFoundError(f"行程不存在：{trip_id}")
            rows = conn.execute(
                "SELECT version,created_at FROM trip_versions WHERE trip_id=? AND owner_id=? "
                "ORDER BY version",
                (trip_id, owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_trip_owner(self, trip_id: str) -> Optional[str]:
        """仅供越权审计：判断行程是否存在但归属他人。"""
        with self.connect() as conn:
            row = conn.execute("SELECT owner_id FROM trips WHERE id=?", (trip_id,)).fetchone()
        return row["owner_id"] if row else None

    def get_run_owner(self, run_id: str) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT owner_id FROM agent_runs WHERE id=?", (run_id,)
            ).fetchone()
        return row["owner_id"] if row else None

    def add_audit_event(
        self, event_type: str, actor_key: Optional[str], ip: Optional[str],
        result: str, detail: Optional[dict] = None,
    ) -> str:
        """写入审计并串接哈希链；与读取上一条哈希在同一事务内完成，避免分叉。"""
        from app.audit import GENESIS, chain_hash, digest_fields

        event_id = f"audit-{uuid4().hex}"
        created_at = utcnow()
        detail_json = json.dumps(detail, ensure_ascii=False, default=str) if detail else None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT entry_hash FROM audit_events WHERE entry_hash IS NOT NULL "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1"
                ).fetchone()
                prev_hash = row["entry_hash"] if row else GENESIS
                entry_hash = chain_hash(
                    prev_hash,
                    digest_fields(event_id, created_at, actor_key, event_type, ip, result, detail_json),
                )
                conn.execute(
                    "INSERT INTO audit_events(id,created_at,actor_key,event_type,ip,result,detail,"
                    "prev_hash,entry_hash) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        event_id, created_at, actor_key, event_type, ip, result,
                        detail_json, prev_hash, entry_hash,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return event_id

    def audit_chain_rows(self) -> list[dict]:
        """按写入顺序返回审计全量，供哈希链校验与留存清理使用。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id,created_at,actor_key,event_type,ip,result,detail,prev_hash,entry_hash "
                "FROM audit_events ORDER BY created_at ASC, rowid ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def audit_watermark(self) -> dict:
        """审计水位：最大 rowid、总行数与该行链头哈希，供外部锚点检测整库回滚。"""
        with self.connect() as conn:
            head = conn.execute(
                "SELECT rowid AS rid, entry_hash FROM audit_events ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            total = conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]
        return {
            "max_rowid": head["rid"] if head else 0,
            "event_count": total,
            "head_hash": head["entry_hash"] if head else None,
        }

    def audit_hash_at(self, rowid: int) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT entry_hash FROM audit_events WHERE rowid=?", (rowid,)
            ).fetchone()
        return row["entry_hash"] if row else None

    def delete_audit_events_before(self, cutoff: str) -> int:
        """删除过期事件，但始终保留最大 rowid 一行，避免外部锚点的水位行被清空。"""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM audit_events WHERE created_at<? "
                "AND rowid<(SELECT max(rowid) FROM audit_events)",
                (cutoff,),
            )
        return cursor.rowcount

    def count_audit_events_before(self, cutoff: str) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT count(*) FROM audit_events WHERE created_at<? "
                "AND rowid<(SELECT max(rowid) FROM audit_events)",
                (cutoff,),
            ).fetchone()[0]

    def count_audit_events(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]

    def list_audit_events(
        self, limit: int = 50, event_type: Optional[str] = None,
        actor_key: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT id,created_at,actor_key,event_type,ip,result,detail FROM audit_events"
        clauses, params = [], []
        if event_type:
            clauses.append("event_type=?")
            params.append(event_type)
        if actor_key:
            clauses.append("actor_key=?")
            params.append(actor_key)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item["detail"]) if item["detail"] else None
            events.append(item)
        return events
