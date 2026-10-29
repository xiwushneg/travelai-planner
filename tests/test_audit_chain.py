"""审计哈希链、篡改检测、留存清理与外部锚点回滚检测。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.audit import record_audit, verify_audit_chain
from app.audit_anchor import anchor_path, compare_watermark, read_anchor
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
    # 保留水位行后，锚点仍与实际库一致
    anchor = read_anchor(repo.path)
    verdict = compare_watermark(
        anchor, repo.audit_watermark(), repo.audit_hash_at(anchor["max_rowid"])
    )
    assert verdict["status"] == "ok"
    # 幂等：再次删除返回 0
    assert repo.delete_audit_events_before(cutoff) == 0
    sqlite3.connect(tmp_path / "retention.db").close()


def test_anchor_advances_with_audit_events(tmp_path):
    repo = Repository(tmp_path / "anchor.db")
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")
    record_audit(repo, "login_failed", username="bob", ip="10.0.0.2", result="failure")

    anchor = read_anchor(repo.path)
    watermark = repo.audit_watermark()
    assert anchor["present"] is True and anchor["valid"] is True
    assert anchor["max_rowid"] == watermark["max_rowid"] == 2
    verdict = compare_watermark(
        anchor, watermark, repo.audit_hash_at(anchor["max_rowid"])
    )
    assert verdict == {"status": "ok", "rollback_suspected": False}


def test_anchor_detects_database_rollback(tmp_path):
    db = tmp_path / "rollback.db"
    repo = Repository(db)
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")
    record_audit(repo, "login_failed", username="bob", ip="10.0.0.2", result="failure")
    # 备份当前库，再写入第三条把锚点推进到 3
    snapshot = tmp_path / "snapshot.db"
    shutil.copyfile(db, snapshot)
    third = record_audit(repo, "logout_all", username="alice", ip="10.0.0.1", result="success")
    assert third is not None
    anchor = read_anchor(db)
    assert anchor["max_rowid"] == 3

    # 模拟攻击者用旧备份覆盖整库
    for suffix in ("-wal", "-shm"):
        stale = Path(f"{db}{suffix}")
        if stale.exists():
            stale.unlink()
    shutil.copyfile(snapshot, db)

    watermark = repo.audit_watermark()
    assert watermark["max_rowid"] == 2
    verdict = compare_watermark(anchor, watermark, repo.audit_hash_at(anchor["max_rowid"]))
    assert verdict["status"] == "rollback"
    assert verdict["rollback_suspected"] is True


def test_anchor_mac_mismatch_blocks_overwrite(tmp_path):
    repo = Repository(tmp_path / "anchor-tamper.db")
    record_audit(repo, "login_succeeded", username="alice", ip="10.0.0.1", result="success")

    path = anchor_path(repo.path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["head_hash"] = "edited"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert read_anchor(repo.path)["valid"] is False
    assert read_anchor(repo.path)["error"] == "mac_mismatch"

    # 新事件不应把被改写的锚点覆盖掉，否则篡改痕迹会被洗白
    record_audit(repo, "login_failed", username="bob", ip="10.0.0.2", result="failure")
    assert read_anchor(repo.path)["valid"] is False
