import json
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
RESET_SCRIPT = ROOT / "scripts" / "admin_reset_password.py"
AUDIT_SCRIPT = ROOT / "scripts" / "audit_query.py"

PASSWORD = "old-password-1"
FAIL_PASSWORD = "wrong-password-9"


def register(client, username, password=PASSWORD):
    return client.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )


def login(client, username, password=PASSWORD):
    return client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )


def run_script(script, *args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        timeout=120, env=env,
    )


def reset_args(db_path, username, confirm="reset-password"):
    return ["--db", str(db_path), "--username", username, "--confirm", confirm]


def audit_rows(db_path):
    return Repository(db_path).list_audit_events(limit=500)


def test_reset_requires_confirm_and_exact_username(tmp_path):
    db_path = tmp_path / "reset-guard.db"
    app = create_app(str(db_path))
    client = TestClient(app)
    assert register(client, "alice").status_code == 201
    assert login(client, "alice").status_code == 200

    missing_confirm = run_script(RESET_SCRIPT, "--db", str(db_path), "--username", "alice")
    assert missing_confirm.returncode != 0

    wrong_confirm = run_script(RESET_SCRIPT, *reset_args(db_path, "alice", confirm="yes"))
    assert wrong_confirm.returncode != 0

    case_mismatch = run_script(RESET_SCRIPT, *reset_args(db_path, "ALICE"))
    assert case_mismatch.returncode == 3
    assert "未找到" in case_mismatch.stderr

    assert client.get("/api/v1/auth/me").status_code == 200
    assert login(client, "alice").status_code == 200

    dry_run = run_script(RESET_SCRIPT, *reset_args(db_path, "alice"), "--dry-run")
    assert dry_run.returncode == 0
    assert "dry-run" in dry_run.stdout
    assert login(client, "alice").status_code == 200


def test_reset_revokes_all_sessions_and_prints_password_once(tmp_path):
    db_path = tmp_path / "reset.db"
    app = create_app(str(db_path))
    first = TestClient(app)
    second = TestClient(app)
    assert register(first, "alice").status_code == 201
    assert login(first, "alice").status_code == 200
    assert login(second, "alice").status_code == 200

    result = run_script(RESET_SCRIPT, *reset_args(db_path, "alice"))
    assert result.returncode == 0, result.stderr
    match = re.search(r"新密码（仅显示一次）：(\S+)", result.stdout)
    assert match
    new_password = match.group(1)
    assert result.stdout.count(new_password) == 1

    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 401
    assert login(first, "alice", PASSWORD).status_code == 401
    assert login(first, "alice", new_password).status_code == 200

    events = audit_rows(db_path)
    resets = [event for event in events if event["event_type"] == "password_reset"]
    assert len(resets) == 1
    assert resets[0]["detail"]["revoked_sessions"] == 3  # 注册时 1 个 + 两次登录各 1 个
    assert new_password not in json.dumps(events, ensure_ascii=False)


def test_logout_all_revokes_every_device(tmp_path):
    db_path = tmp_path / "logout-all.db"
    app = create_app(str(db_path))
    first = TestClient(app)
    second = TestClient(app)
    assert register(first, "bob").status_code == 201
    assert login(first, "bob").status_code == 200
    assert login(second, "bob").status_code == 200

    response = first.post("/api/v1/auth/logout-all")
    assert response.status_code == 200
    assert response.json()["revoked"] >= 2
    assert first.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 401

    events = audit_rows(db_path)
    assert any(event["event_type"] == "logout_all" for event in events)


def test_audit_covers_required_events_without_sensitive_fields(tmp_path):
    db_path = tmp_path / "audit.db"
    app = create_app(str(db_path))
    alice = TestClient(app)
    bob = TestClient(app)
    repository = app.state.planning_service.repository

    assert register(alice, "alice").status_code == 201
    assert register(bob, "bob").status_code == 201
    assert login(alice, "alice").status_code == 200
    alice_id = repository.get_user_by_username("alice")["id"]
    run_id = repository.create_run("create_trip", {"probe": True}, owner_id=alice_id).id

    assert login(bob, "bob").status_code == 200
    assert bob.get(f"/api/v1/runs/{run_id}").status_code == 404
    assert bob.post("/api/v1/auth/logout-all").status_code == 200

    for _ in range(5):
        assert login(alice, "alice", FAIL_PASSWORD).status_code == 401
    assert login(alice, "alice", FAIL_PASSWORD).status_code == 401

    result = run_script(RESET_SCRIPT, *reset_args(db_path, "alice"))
    assert result.returncode == 0, result.stderr

    events = audit_rows(db_path)
    required = {
        "login_succeeded", "login_failed", "login_blocked",
        "trip_access_denied", "password_reset", "logout_all",
    }
    assert required <= {event["event_type"] for event in events}
    for event in events:
        assert event["created_at"] and event["result"] and event["actor_key"]

    dump = json.dumps(events, ensure_ascii=False)
    alice_token = alice.cookies.get("travelai_session") or ""
    bob_token = bob.cookies.get("travelai_session") or ""
    for secret in (PASSWORD, FAIL_PASSWORD, alice_token, bob_token, "travelai_session"):
        if secret:
            assert secret not in dump
    assert "alice" not in dump and "bob" not in dump


def test_audit_query_cli_is_read_only(tmp_path):
    db_path = tmp_path / "audit-read.db"
    app = create_app(str(db_path))
    client = TestClient(app)
    assert register(client, "carol").status_code == 201
    assert login(client, "carol", FAIL_PASSWORD).status_code == 401

    result = run_script(AUDIT_SCRIPT, "--db", str(db_path), "--event", "login_failed", "--json")
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "login_failed"
    assert "detail" in rows[0]
