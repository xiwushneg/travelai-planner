from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html lang='zh-CN'><head><title>TravelAI</title></head>"
        "<body><div id='app'>旅行规划工作台</div>"
        "<script type='module' src='/assets/app.js'></script></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("export const app = 'travelai';", encoding="utf-8")
    return dist


def test_web_home_assets_and_spa_fallback(tmp_path):
    dist = make_dist(tmp_path)
    with TestClient(create_app(str(tmp_path / "web.db"), web_dist=str(dist))) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert home.headers["content-type"].startswith("text/html")
        assert "旅行规划工作台" in home.text

        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "travelai" in asset.text

        fallback = client.get("/trips/example")
        assert fallback.status_code == 200
        assert "旅行规划工作台" in fallback.text

        missing_api = client.get("/api/v1/not-a-route")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")

        bare_api = client.get("/api")
        assert bare_api.status_code == 404
        assert bare_api.headers["content-type"].startswith("application/json")

        docs_like = client.get("/docsomething")
        assert docs_like.status_code == 200
        assert "旅行规划工作台" in docs_like.text


def test_recent_trips_returns_saved_current_version(tmp_path):
    dist = make_dist(tmp_path)
    with TestClient(create_app(str(tmp_path / "recent.db"), web_dist=str(dist))) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "web-tester", "password": "web-tester-pass"},
        )
        assert registered.status_code == 201, registered.text
        assert client.get("/api/v1/trips").json() == {"trips": []}
        created = client.post("/api/v1/trips", json={
            "spec": {
                "destination": "东京",
                "start_date": "2027-04-01",
                "end_date": "2027-04-01",
                "selected_spot_ids": ["tokyo-sensoji"],
                "must_include_spot_ids": ["tokyo-sensoji"],
                "include_lunch": False,
            },
            "include_evidence": False,
        })
        assert created.status_code == 201, created.text
        trip_id = created.json()["trip_id"]
        listing = client.get("/api/v1/trips")
        assert listing.status_code == 200
        assert listing.json()["trips"] == [{
            "trip_id": trip_id,
            "version": 1,
            "destination": "东京",
            "start_date": "2027-04-01",
            "end_date": "2027-04-01",
            "updated_at": listing.json()["trips"][0]["updated_at"],
            "day_count": 1,
        }]
        assert listing.json()["trips"][0]["updated_at"]
