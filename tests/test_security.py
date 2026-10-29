import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes_module
import app.auth as auth_module
import app.main as main_module
from app.config import Settings, validate_settings
from app.main import create_app

PASSWORD = "security-pass-1"


def test_unknown_user_still_runs_one_bcrypt(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "timing.db"))
    checked_hashes = []
    real_checkpw = auth_module.bcrypt.checkpw

    def spy(password, password_hash):
        checked_hashes.append(password_hash)
        return real_checkpw(password, password_hash)

    monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": PASSWORD}
        )
    assert response.status_code == 401
    assert len(checked_hashes) == 1
    assert checked_hashes[0] == auth_module.DUMMY_PASSWORD_HASH.encode("ascii")


def test_unknown_user_and_wrong_password_share_status(tmp_path):
    app = create_app(str(tmp_path / "same-response.db"))
    with TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/register", json={"username": "alice", "password": PASSWORD}
        ).status_code == 201
        # 注册接口的默认限流允许 3 次/10 分钟，这里只做登录对比：
        wrong = client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong-pass-1"})
        missing = client.post("/api/v1/auth/login", json={"username": "ghost", "password": PASSWORD})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"]["code"] == missing.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    assert wrong.json()["detail"]["message"] == missing.json()["detail"]["message"]


def test_login_backoff_is_indistinguishable_from_bad_password(tmp_path):
    app = create_app(str(tmp_path / "login-limit.db"))
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/login", json={"username": "ghost-0", "password": PASSWORD}
        )
        assert first.status_code == 401
        for index in range(1, 5):
            attempt = client.post(
                "/api/v1/auth/login",
                json={"username": f"ghost-{index}", "password": PASSWORD},
            )
            assert attempt.status_code == 401
        blocked = client.post(
            "/api/v1/auth/login", json={"username": "ghost-final", "password": PASSWORD}
        )
    assert blocked.status_code == 401
    assert blocked.json() == first.json()
    assert "retry-after" not in blocked.headers

    repository = app.state.planning_service.repository
    state = repository.get_login_failure("ip", "testclient")
    assert state["fail_count"] == 5
    assert state["blocked_until"]


def test_register_rate_limit(tmp_path):
    app = create_app(str(tmp_path / "register-limit.db"))
    with TestClient(app) as client:
        for index in range(3):
            allowed = client.post(
                "/api/v1/auth/register",
                json={"username": f"user-{index}", "password": PASSWORD},
            )
            assert allowed.status_code == 201
        blocked = client.post(
            "/api/v1/auth/register", json={"username": "user-final", "password": PASSWORD}
        )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "RATE_LIMITED"


def test_production_requires_secure_cookie(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError):
        validate_settings(Settings(environment="production", cookie_secure=False))
    validate_settings(
        Settings(environment="production", cookie_secure=True, audit_key="x" * 32)
    )

    monkeypatch.setattr(
        main_module, "settings", Settings(environment="production", cookie_secure=False)
    )
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path / "rejected.db"))


def test_production_requires_custom_audit_key():
    with pytest.raises(RuntimeError):
        validate_settings(
            Settings(environment="production", cookie_secure=True)
        )
    with pytest.raises(RuntimeError):
        validate_settings(
            Settings(environment="production", cookie_secure=True, audit_key="short")
        )
    validate_settings(
        Settings(environment="production", cookie_secure=True, audit_key="k" * 32)
    )


def test_unknown_environment_is_rejected():
    with pytest.raises(RuntimeError):
        validate_settings(Settings(environment="staging"))


def test_secure_cookie_is_set_when_enabled(tmp_path, monkeypatch):
    secure_settings = Settings(cookie_secure=True)
    monkeypatch.setattr(main_module, "settings", secure_settings)
    monkeypatch.setattr(routes_module, "settings", secure_settings)
    app = create_app(str(tmp_path / "secure.db"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/register", json={"username": "secure-user", "password": PASSWORD}
        )
    assert response.status_code == 201
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_audit_identifier_is_keyed_hmac():
    import hashlib
    import hmac

    from app.audit import hash_identifier
    from app.config import settings

    expected = hmac.new(
        settings.audit_key.encode("utf-8"), b"alice", hashlib.sha256
    ).hexdigest()[:32]
    assert hash_identifier("alice") == expected
    # 与固定盐 SHA-256 方案不再等价，避免低熵用户名被字典反查。
    naive = hashlib.sha256(("travelai-audit:alice").encode("utf-8")).hexdigest()[:32]
    assert hash_identifier("alice") != naive


def test_trust_proxy_header_is_opt_in(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "proxy-off.db"))
    with TestClient(app) as client:
        for index in range(5):
            response = client.post(
                "/api/v1/auth/login",
                json={"username": f"ghost-{index}", "password": PASSWORD},
                headers={"X-Forwarded-For": f"10.0.0.{index}"},
            )
            assert response.status_code == 401
    repository = app.state.planning_service.repository
    assert repository.get_login_failure("ip", "testclient")["fail_count"] == 5
    assert repository.get_login_failure("ip", "10.0.0.0") is None

    monkeypatch.setattr(routes_module, "settings", Settings(trust_proxy_headers=True))
    trusted_app = create_app(str(tmp_path / "proxy-on.db"))
    with TestClient(trusted_app) as client:
        client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": PASSWORD},
            headers={"X-Forwarded-For": "10.1.0.1, 10.1.0.2"},
        )
    trusted_repository = trusted_app.state.planning_service.repository
    assert trusted_repository.get_login_failure("ip", "10.1.0.1")["fail_count"] == 1


def test_register_limit_shared_across_instances(tmp_path):
    """计数落在 SQLite，第二个应用实例同样受限，证明跨进程共享。"""
    db_path = str(tmp_path / "shared-register.db")
    first_app = create_app(db_path)
    with TestClient(first_app) as client:
        for index in range(3):
            assert client.post(
                "/api/v1/auth/register",
                json={"username": f"shared-{index}", "password": PASSWORD},
            ).status_code == 201

    second_app = create_app(db_path)
    with TestClient(second_app) as client:
        blocked = client.post(
            "/api/v1/auth/register",
            json={"username": "shared-final", "password": PASSWORD},
        )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "RATE_LIMITED"
    assert int(blocked.headers["retry-after"]) >= 1
    second_repository = second_app.state.planning_service.repository
    assert second_repository.register_attempt("testclient", 3, 600)[1] >= 1


def test_register_window_resets_after_expiry(tmp_path):
    app = create_app(str(tmp_path / "window.db"))
    repository = app.state.planning_service.repository
    with TestClient(app) as client:
        for index in range(3):
            assert client.post(
                "/api/v1/auth/register",
                json={"username": f"window-{index}", "password": PASSWORD},
            ).status_code == 201
        assert client.post(
            "/api/v1/auth/register",
            json={"username": "window-4", "password": PASSWORD},
        ).status_code == 429

    with repository.connect() as conn:
        conn.execute("UPDATE register_attempts SET window_start=?", ("2020-01-01T00:00:00+00:00",))

    with TestClient(app) as client:
        allowed = client.post(
            "/api/v1/auth/register",
            json={"username": "window-5", "password": PASSWORD},
        )
    assert allowed.status_code == 201

