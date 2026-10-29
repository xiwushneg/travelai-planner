from fastapi.testclient import TestClient

from app.core.models import Guide, SourceInfo
from app.main import create_app


def client(tmp_path):
    api = TestClient(create_app(str(tmp_path / "api.db")))
    registered = api.post(
        "/api/v1/auth/register",
        json={"username": "api-tester", "password": "api-tester-pass"},
    )
    assert registered.status_code == 201, registered.text
    return api


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
        "include_evidence": True,
    }


def test_api_create_read_modify_and_run(tmp_path):
    with client(tmp_path) as api:
        health = api.get("/health")
        assert health.status_code == 200
        assert health.json()["planner"] == "deterministic"

        created = api.post("/api/v1/trips", json=trip_payload())
        assert created.status_code == 201, created.text
        body = created.json()
        trip_id = body["trip_id"]
        assert body["version"] == 1
        assert body["result"]["plan"]["evidence"]

        run = api.get(f"/api/v1/runs/{body['run_id']}")
        assert run.status_code == 200
        assert run.json()["status"] == "succeeded"

        modified = api.post(f"/api/v1/trips/{trip_id}/modify-day", json={
            "expected_version": 1,
            "day_number": 2,
            "desired_spot_ids": ["tokyo-shibuya-sky"],
            "lock_other_days": True,
        })
        assert modified.status_code == 200, modified.text
        assert modified.json()["version"] == 2
        assert modified.json()["diff"]["unchanged_days"] == [1]

        stale = api.post(f"/api/v1/trips/{trip_id}/modify-day", json={
            "expected_version": 1,
            "day_number": 2,
            "desired_spot_ids": ["tokyo-hamarikyu"],
        })
        assert stale.status_code == 409

        versions = api.get(f"/api/v1/trips/{trip_id}/versions")
        assert [item["version"] for item in versions.json()["versions"]] == [1, 2]
        comparison = api.get(f"/api/v1/trips/{trip_id}/diff", params={
            "from_version": 1, "to_version": 2,
        })
        assert comparison.status_code == 200


def test_evidence_search_reads_guides_imported_after_startup(tmp_path):
    with client(tmp_path) as api:
        api.app.state.planning_service.repository.upsert_guides([
            Guide(
                id="beijing-guide-test",
                title="北京博物馆预约提示",
                city="北京",
                tags=["预约"],
                content="国家博物馆需提前实名预约，周一闭馆，建议错峰入馆。",
                source=SourceInfo(
                    status="unverified", name="测试来源", url=None,
                    last_verified_at=None, notes="测试数据",
                ),
            )
        ])
        response = api.post("/api/v1/evidence/search",
                            json={"city": "北京", "query": "预约", "top_k": 3})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "beijing-guide-test"


def test_api_declares_utf8_charset(tmp_path):
    with client(tmp_path) as api:
        response = api.get("/api/v1/spots", params={"city": "东京"})
        assert response.status_code == 200
        assert "charset=utf-8" in response.headers["content-type"].lower()
        assert response.json()["items"][0]["city"] == "东京"

        missing = api.get("/api/v1/trips/not-exist")
        assert missing.status_code == 404
        assert "charset=utf-8" in missing.headers["content-type"].lower()


def test_api_returns_structured_infeasible_response(tmp_path):
    with client(tmp_path) as api:
        payload = trip_payload()
        payload["spec"]["start_date"] = "2027-04-05"
        payload["spec"]["end_date"] = "2027-04-05"
        payload["spec"]["selected_spot_ids"] = ["tokyo-national-museum"]
        payload["spec"]["must_include_spot_ids"] = ["tokyo-national-museum"]
        response = api.post("/api/v1/trips", json=payload)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "PLANNING_INFEASIBLE"
        assert detail["run_id"]
        assert detail["conflicts"]
