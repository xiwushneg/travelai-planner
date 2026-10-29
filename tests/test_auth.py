from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app

PASSWORD = "correct-horse-9"


def make_app(tmp_path, name="auth.db"):
    return create_app(str(tmp_path / name))


def register(api: TestClient, username: str = "alice", password: str = PASSWORD):
    response = api.post(
        "/api/v1/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


def trip_payload():
    return {
        "spec": {
            "destination": "东京",
            "start_date": "2027-04-01",
            "end_date": "2027-04-02",
            "selected_spot_ids": ["tokyo-sensoji", "tokyo-ueno-park"],
            "must_include_spot_ids": ["tokyo-sensoji", "tokyo-ueno-park"],
            "max_spots_per_day": 1,
            "include_lunch": False,
        },
        "include_evidence": False,
    }


def test_register_login_me_logout(tmp_path):
    with TestClient(make_app(tmp_path)) as api:
        body = register(api)
        assert body["user"]["username"] == "alice"

        me = api.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["username"] == "alice"

        assert api.post("/api/v1/auth/logout").status_code == 200
        assert api.get("/api/v1/auth/me").status_code == 401

        logged_in = api.post(
            "/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        assert logged_in.status_code == 200
        assert api.get("/api/v1/auth/me").json()["user"]["username"] == "alice"


def test_password_is_stored_as_bcrypt_hash(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as api:
        register(api)
    repository = app.state.planning_service.repository
    user = repository.get_user_by_username("alice")
    assert user["password_hash"] != PASSWORD
    assert user["password_hash"].startswith("$2")
    assert PASSWORD not in user["password_hash"]


def test_duplicate_username_and_wrong_password(tmp_path):
    with TestClient(make_app(tmp_path)) as api:
        register(api, "alice")
        duplicate = api.post(
            "/api/v1/auth/register", json={"username": "Alice", "password": PASSWORD}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "REGISTRATION_UNAVAILABLE"
        assert "Alice" not in duplicate.json()["detail"]["message"]
        assert "已占用" not in duplicate.json()["detail"]["message"]

        wrong = api.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "wrong-pass-1"}
        )
        assert wrong.status_code == 401
        assert wrong.json()["detail"]["code"] == "INVALID_CREDENTIALS"

        unknown = api.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": PASSWORD}
        )
        assert unknown.status_code == 401


def test_unauthenticated_trip_endpoints_return_401(tmp_path):
    with TestClient(make_app(tmp_path)) as api:
        assert api.get("/api/v1/trips").status_code == 401
        created = api.post("/api/v1/trips", json=trip_payload())
        assert created.status_code == 401
        assert created.json()["detail"]["code"] == "UNAUTHENTICATED"
        assert api.get("/api/v1/trips/trip-unknown").status_code == 401
        assert api.get("/api/v1/trips/trip-unknown/versions").status_code == 401
        assert api.get(
            "/api/v1/trips/trip-unknown/diff",
            params={"from_version": 1, "to_version": 2},
        ).status_code == 401
        assert api.post("/api/v1/trips/trip-unknown/modify-day", json={
            "expected_version": 1,
            "day_number": 1,
            "desired_spot_ids": ["tokyo-sensoji"],
        }).status_code == 401
        assert api.get("/api/v1/runs/run-unknown").status_code == 401
        assert api.get("/api/v1/auth/me").status_code == 401


def test_other_users_trip_is_hidden_as_404(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "alice")
        register(bob, "bob")

        created = alice.post("/api/v1/trips", json=trip_payload())
        assert created.status_code == 201, created.text
        body = created.json()
        trip_id = body["trip_id"]
        run_id = body["run_id"]

        assert alice.get(f"/api/v1/trips/{trip_id}").status_code == 200

        hidden = bob.get(f"/api/v1/trips/{trip_id}")
        assert hidden.status_code == 404
        # 404 必须与“真的不存在”无法区分，避免泄露行程存在性。
        missing = bob.get("/api/v1/trips/trip-does-not-exist")
        assert missing.status_code == 404
        # 两种 404 的响应结构完全一致，只有回显的 trip_id 不同。
        assert missing.json().keys() == hidden.json().keys() == {"detail"}

        assert bob.get(f"/api/v1/trips/{trip_id}/versions").status_code == 404
        assert bob.get(
            f"/api/v1/trips/{trip_id}/diff",
            params={"from_version": 1, "to_version": 1},
        ).status_code == 404
        assert bob.post(f"/api/v1/trips/{trip_id}/modify-day", json={
            "expected_version": 1,
            "day_number": 2,
            "desired_spot_ids": ["tokyo-shibuya-sky"],
        }).status_code == 404
        assert bob.get(f"/api/v1/runs/{run_id}").status_code == 404
        assert bob.get("/api/v1/trips").json()["trips"] == []
        assert alice.get("/api/v1/trips").json()["trips"][0]["trip_id"] == trip_id


def test_denied_modify_day_leaves_no_orphan_run(tmp_path):
    """越权改某天时先判归属，不应在攻击者名下留下空 run。"""
    app = make_app(tmp_path, "orphan-run.db")
    with TestClient(app) as alice, TestClient(app) as bob:
        register(alice, "alice")
        register(bob, "bob")
        created = alice.post("/api/v1/trips", json=trip_payload())
        assert created.status_code == 201, created.text
        trip_id = created.json()["trip_id"]

        denied = bob.post(f"/api/v1/trips/{trip_id}/modify-day", json={
            "expected_version": 1,
            "day_number": 1,
            "desired_spot_ids": ["tokyo-sensoji"],
        })
        assert denied.status_code == 404

    repository = app.state.planning_service.repository
    bob_id = repository.get_user_by_username("bob")["id"]
    with repository.connect() as conn:
        orphan = conn.execute(
            "SELECT count(*) FROM agent_runs WHERE owner_id=?", (bob_id,)
        ).fetchone()[0]
    assert orphan == 0


def test_expired_session_returns_401(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as api:
        register(api)
        assert api.get("/api/v1/auth/me").status_code == 200

        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with app.state.planning_service.repository.connect() as conn:
            conn.execute("UPDATE sessions SET expires_at=?", (past,))

        expired = api.get("/api/v1/auth/me")
        assert expired.status_code == 401
        assert expired.json()["detail"]["code"] == "UNAUTHENTICATED"
        assert api.get("/api/v1/trips").status_code == 401


def test_username_is_stripped_before_length_check(tmp_path):
    with TestClient(make_app(tmp_path, "whitespace.db")) as api:
        blank = api.post(
            "/api/v1/auth/register", json={"username": "   ", "password": PASSWORD}
        )
        assert blank.status_code == 422

        short = api.post(
            "/api/v1/auth/register", json={"username": " a ", "password": PASSWORD}
        )
        assert short.status_code == 422

        padded = api.post(
            "/api/v1/auth/register", json={"username": "  bob  ", "password": PASSWORD}
        )
        assert padded.status_code == 201
        assert padded.json()["user"]["username"] == "bob"
