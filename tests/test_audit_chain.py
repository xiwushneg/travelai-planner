"""审计哈希链、篡改检测与留存清理。"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.audit import record_audit, verify_audit_chain
from app.repository import Repository


def _insert_legacy(repo: Repository, event_type: str, created_at: str) -> None:
    with repo.connect() as conn:
        conn.execute(
            "INSERT INTO audit_events(id,created_at,actor_key,event_type,ip,result,detail) "
            "VALUES(?,?,?,?,?,?,?)",
            (f"legacy-{event_type}-{created_at}", created_at, None, event_type, None, "legacy", None),
        )


def test_audit_chain_is_valid(tmp_path):
    repo = Repository(tmp_path / "chain.db")
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")
    record_audit(repo, "login_failed", username="bob", ip="10.0.0.2", result="failure")
    record_audit(repo, "logout_all", username="alice", ip="10.0.0.1", result="success")

    report = verify_audit_chain(repo.audit_chain_rows())
    assert report["ok"] is True
    assert report["hashed"] == 3
    assert report["legacy"] == 0


def test_audit_chain_detects_tamper(tmp_path):
    repo = Repository(tmp_path / "tamper.db")
    first = record_audit(repo, "login_failed", username="alice", ip="10.0.0.1", result="failure")
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")

    with repo.connect() as conn:
        conn.execute(
            "UPDATE audit_events SET result='success' WHERE id=?", (first,)
        )

    report = verify_audit_chain(repo.audit_chain_rows())
    assert report["ok"] is False
    assert report["first_broken"] == first


def test_legacy_rows_are_reported_but_tolerated(tmp_path):
    repo = Repository(tmp_path / "legacy.db")
    _insert_legacy(repo, "login_succeeded", "2025-01-01T00:00:00+00:00")
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")

    report = verify_audit_chain(repo.audit_chain_rows())
    assert report["ok"] is True
    assert report["legacy"] == 1
    assert report["hashed"] == 1


def test_retention_deletes_oldest_prefix_and_keeps_chain_valid(tmp_path):
    repo = Repository(tmp_path / "retention.db")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    _insert_legacy(repo, "login_failed", old)
    _insert_legacy(repo, "trip_access_denied", old)
    record_audit(repo, "password_reset", username="alice", ip="10.0.0.1", result="success")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    assert repo.count_audit_events_before(cutoff) == 2
    assert repo.delete_audit_events_before(cutoff) == 2
    assert repo.count_audit_events() == 1

    report = verify_audit_chain(repo.audit_chain_rows())
    assert report["ok"] is True
    assert report["hashed"] == 1
    assert report["legacy"] == 0
    # 幂等：再次删除返回 0
    assert repo.delete_audit_events_before(cutoff) == 0
    sqlite3.connect(tmp_path / "retention.db").close()
