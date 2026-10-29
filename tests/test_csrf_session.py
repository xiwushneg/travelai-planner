import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import create_app
from app.repository import Repository

ROOT = Path(__file__).resolve().parents[1]
CLEANUP_SCRIPT = ROOT / "scripts" / "cleanup_sessions.py"
PASSWORD = "csrf-session-1"


def run_script(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(CLEANUP_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        timeout=120, env=env,
    )


def test_write_endpoints_reject_non_json(tmp_path):
    app = create_app(str(tmp_path / "csrf.db"))
    client = TestClient(app)

    for path in ("/api/v1/auth/register", "/api/v1/auth/login"):
        assert client.post(path, data={"username": "x", "password": PASSWORD}).status_code == 422
        assert client.post(
            path,
            content=b'{"username":"x","password":"y"}',
            headers={"Content-Type": "text/plain"},
        ).status_code == 422

    assert client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": PASSWORD},
    ).status_code == 201
    assert client.post("/api/v1/trips", data={"spec": "x"}).status_code == 422
    assert client.post(
        "/api/v1/trips/fake/modify-day", data={"expected_version": "1"}
    ).status_code == 422
    assert client.post(
        "/api/v1/evidence/search", data={"city": "杭州", "query": "西湖"}
    ).status_code == 422


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FORBIDDEN_SOURCE_PATTERNS = {
    "Form 参数": re.compile(r"\bForm\s*\("),
    "File/UploadFile": re.compile(r"\b(File|UploadFile)\s*\(|\bUploadFile\b"),
    "手动解析 body": re.compile(r"request\.body\s*\(|\brequest\.form\s*\("),
    "multipart/form-data": re.compile(r"multipart/form-data"),
    "CORS 中间件": re.compile(r"CORSMiddleware"),
}


def test_write_routes_do_not_declare_form_or_file_params(tmp_path):
    app = create_app(str(tmp_path / "guard.db"))
    for route in app.routes:
        if not isinstance(route, APIRoute) or not (route.methods & WRITE_METHODS):
            continue
        for param in route.dependant.body_params:
            info_name = type(getattr(param, "field_info", None)).__name__
            annotation_name = type(getattr(param, "type_", None)).__name__
            assert info_name not in {"Form", "File"}, f"{route.path} 使用了 {info_name} 参数"
            assert annotation_name != "UploadFile", f"{route.path} 使用了 UploadFile"


def test_app_source_has_no_non_json_write_constructs(tmp_path):
    app_dir = ROOT / "app"
    violations = []
    for source in app_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_SOURCE_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{source.relative_to(ROOT)}: {label}")
    assert not violations, (
        "检测到会推翻 CSRF 结论的写接口构造，必须升级 CSRF 方案后再合并："
        + "; ".join(violations)
    )


def test_session_cookie_attributes_and_no_cors(tmp_path):
    app = create_app(str(tmp_path / "cookie.db"))
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 201
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie

    middleware_names = {item.cls.__name__ for item in app.user_middleware}
    assert "CORSMiddleware" not in middleware_names


def test_cleanup_script_deletes_expired_and_is_idempotent(tmp_path):
    db_path = tmp_path / "cleanup.db"
    app = create_app(str(db_path))
    client = TestClient(app)
    assert client.post(
        "/api/v1/auth/register", json={"username": "alice", "password": PASSWORD}
    ).status_code == 201
    assert client.get("/api/v1/auth/me").status_code == 200

    repository = Repository(db_path)
    alice_id = repository.get_user_by_username("alice")["id"]
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    repository.create_session(alice_id, "expired-hash-1", past)
    repository.create_session(alice_id, "expired-hash-2", past)

    first = run_script("--db", str(db_path), "--json")
    assert first.returncode == 0, first.stderr
    first_result = json.loads(first.stdout)
    assert first_result == {
        "deleted": 2, "deleted_login_failures": 0, "remaining": 1
    }

    second = run_script("--db", str(db_path), "--json")
    assert json.loads(second.stdout) == {
        "deleted": 0, "deleted_login_failures": 0, "remaining": 1
    }

    assert client.get("/api/v1/auth/me").status_code == 200


def test_cleanup_script_removes_stale_login_failures(tmp_path):
    db_path = tmp_path / "cleanup-failures.db"
    repository = Repository(db_path)
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with repository.connect() as conn:
        conn.execute(
            "INSERT INTO login_failures(scope,key,fail_count,blocked_until,updated_at) "
            "VALUES('account','ghost',3,NULL,?)",
            (stale,),
        )

    dry = json.loads(run_script("--db", str(db_path), "--dry-run", "--json").stdout)
    assert dry["stale_login_failures"] == 1

    applied = json.loads(run_script("--db", str(db_path), "--json").stdout)
    assert applied["deleted_login_failures"] == 1

    again = json.loads(run_script("--db", str(db_path), "--json").stdout)
    assert again["deleted_login_failures"] == 0


def test_cleanup_script_dry_run_does_not_delete(tmp_path):
    db_path = tmp_path / "cleanup-dry.db"
    app = create_app(str(db_path))
    client = TestClient(app)
    assert client.post(
        "/api/v1/auth/register", json={"username": "bob", "password": PASSWORD}
    ).status_code == 201

    repository = Repository(db_path)
    bob_id = repository.get_user_by_username("bob")["id"]
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    repository.create_session(bob_id, "expired-dry-hash", past)

    dry = json.loads(run_script("--db", str(db_path), "--dry-run", "--json").stdout)
    assert dry["dry_run"] is True
    assert dry["would_delete"] == 1

    again = json.loads(run_script("--db", str(db_path), "--dry-run", "--json").stdout)
    assert again["would_delete"] == 1

    applied = json.loads(run_script("--db", str(db_path), "--json").stdout)
    assert applied["deleted"] == 1
