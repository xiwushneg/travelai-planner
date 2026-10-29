from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.api.routes as routes_module
import app.auth as auth_module
from app.config import Settings
from app.login_guard import backoff_seconds
from app.main import create_app

PASSWORD = "guard-pass-1"


def make_app(tmp_path, name="guard.db", monkeypatch=None):
    if monkeypatch is not None:
        import app.main as main_module

        settings = Settings(trust_proxy_headers=True)
        monkeypatch.setattr(routes_module, "settings", settings)
        monkeypatch.setattr(main_module, "settings", settings)
    return create_app(str(tmp_path / name))


def login(client, username, password=PASSWORD, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=headers,
    )


def register(client, username, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": PASSWORD},
        headers=headers,
    )


def test_backoff_grows_exponentially_and_caps():
    threshold, base, cap = 5, 30, 1800
    assert backoff_seconds(4, threshold, base, cap) == 0
    assert backoff_seconds(5, threshold, base, cap) == 30
    assert backoff_seconds(6, threshold, base, cap) == 60
    assert backoff_seconds(7, threshold, base, cap) == 120
    assert backoff_seconds(20, threshold, base, cap) == cap


def test_five_failures_block_then_expire(tmp_path):
    app = create_app(str(tmp_path / "expire.db"))
    with TestClient(app) as client:
        assert register(client, "alice").status_code == 201
        for _ in range(5):
            assert login(client, "alice", "wrong-pass-1").status_code == 401
        # 退避期内即使密码正确也返回同样的失败响应
        assert login(client, "alice").status_code == 401

    repository = app.state.planning_service.repository
    assert repository.get_login_failure("ip", "testclient")["fail_count"] == 5
    assert repository.get_login_failure("account", "alice")["fail_count"] == 5

    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    repository.set_login_block("ip", "testclient", past)
    repository.set_login_block("account", "alice", past)
    with TestClient(app) as client:
        assert login(client, "alice").status_code == 200


def test_success_clears_account_counter(tmp_path, monkeypatch):
    app = make_app(tmp_path, "reset.db", monkeypatch=monkeypatch)
    with TestClient(app) as client:
        assert register(client, "bob").status_code == 201
        for index in range(4):
            assert login(client, "bob", "wrong-pass-1", ip=f"10.9.0.{index}").status_code == 401
        assert login(client, "bob", ip="10.9.0.99").status_code == 200

    repository = app.state.planning_service.repository
    assert repository.get_login_failure("account", "bob") is None

    with TestClient(app) as client:
        for index in range(4):
            assert login(client, "bob", "wrong-pass-1", ip=f"10.8.0.{index}").status_code == 401
    state = repository.get_login_failure("account", "bob")
    assert state["fail_count"] == 4  # 重置后重新计数，未触发账号退避


def test_ip_and_account_dimensions_are_independent(tmp_path):
    app = create_app(str(tmp_path / "independent.db"))
    with TestClient(app) as client:
        for index in range(5):
            assert login(client, f"ghost-{index}").status_code == 401

    repository = app.state.planning_service.repository
    guard = app.state.login_guard
    assert repository.get_login_failure("ip", "testclient")["fail_count"] == 5
    assert repository.get_login_failure("account", "ghost-0")["fail_count"] == 1
    assert guard.check("testclient", "brand-new")[0] == "backoff_ip"
    assert guard.check("another-ip", "ghost-0") is None


def test_account_dimension_blocks_across_ips(tmp_path, monkeypatch):
    app = make_app(tmp_path, "account-block.db", monkeypatch=monkeypatch)
    with TestClient(app) as client:
        assert register(client, "carol").status_code == 201
        for index in range(5):
            assert login(client, "carol", "wrong-pass-1", ip=f"10.7.0.{index}").status_code == 401
        assert login(client, "carol", ip="10.7.1.1").status_code == 401

    repository = app.state.planning_service.repository
    assert repository.get_login_failure("account", "carol")["fail_count"] == 5
    assert repository.get_login_failure("ip", "10.7.0.0")["fail_count"] == 1
    assert app.state.login_guard.check("10.7.1.1", "carol")[0] == "backoff_account"


def test_blocked_attempt_still_runs_one_bcrypt(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "blocked-timing.db"))
    with TestClient(app) as client:
        for _ in range(5):
            assert login(client, "ghost").status_code == 401

        calls = []
        real_checkpw = auth_module.bcrypt.checkpw

        def spy(password, password_hash):
            calls.append(password_hash)
            return real_checkpw(password, password_hash)

        monkeypatch.setattr(auth_module.bcrypt, "checkpw", spy)
        assert login(client, "ghost").status_code == 401
    assert len(calls) == 1


def test_admin_reset_clears_account_backoff(tmp_path):
    from app.auth import hash_password

    app = create_app(str(tmp_path / "reset-backoff.db"))
    with TestClient(app) as client:
        assert register(client, "bob").status_code == 201
        for _ in range(5):
            assert login(client, "bob", "wrong-password-1").status_code == 401

        repository = app.state.planning_service.repository
        assert repository.get_login_failure("account", "bob")["fail_count"] == 5

        repository.reset_password_and_revoke_sessions(
            "bob", hash_password("new-password-9")
        )
        assert repository.get_login_failure("account", "bob") is None

        # 换一个出口 IP 模拟异地登录；IP 维度退避按设计保留，不在本用例范围。
        repository.clear_login_failures("ip", "testclient")
        assert login(client, "bob", "new-password-9").status_code == 200
