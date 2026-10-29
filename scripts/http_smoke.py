#!/usr/bin/env python3
"""Start a real Uvicorn process and verify the packaged Web/API workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]

# 会话通过 httpOnly Cookie 传递，冒烟脚本需要保留 Cookie。
_OPENER = build_opener(HTTPCookieProcessor(CookieJar()))


def call(base: str, path: str, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data else {}
    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=15) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def json_body(raw: bytes):
    return json.loads(raw.decode("utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON evidence output path")
    args = parser.parse_args()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="travelai-http-smoke-", ignore_cleanup_errors=True) as temporary:
        temp = Path(temporary)
        log_path = temp / "uvicorn.log"
        env = os.environ.copy()
        env.update({
            "TRAVELAI_DB_PATH": str(temp / "smoke.db"),
            "TRAVELAI_SEED_DEMO": "true",
            "TRAVELAI_EVIDENCE": "true",
            "TRAVELAI_MAP_PROVIDER": "local",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(80):
                    if process.poll() is not None:
                        raise RuntimeError(f"Uvicorn exited early:\n{log_path.read_text(encoding='utf-8')}")
                    try:
                        status, _, raw = call(base, "/health")
                        if status == 200:
                            health = json_body(raw)
                            break
                    except (URLError, ConnectionError, TimeoutError):
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"Uvicorn did not become ready:\n{log_path.read_text(encoding='utf-8')}")

                require(health["planner"] == "deterministic", "Unexpected planner")
                require(health["map_provider"] == "local" and health["map_mode"] == "local_estimate", "Default map mode must be offline local")
                require(not any("ak" in key.lower() for key in health), "Health response must not expose AK fields")
                require(health["realtime_weather"] is False and health["realtime_traffic"] is False, "Realtime capability must not be fabricated")

                home_status, _, home_raw = call(base, "/")
                home = home_raw.decode("utf-8")
                require(home_status == 200 and "TravelAI" in home, "Packaged home page unavailable")
                asset_paths = sorted(set(re.findall(r"(?:src|href)=\"(/assets/[^\"]+)\"", home)))
                require(any(path.endswith(".js") for path in asset_paths), "No built JS asset")
                require(any(path.endswith(".css") for path in asset_paths), "No built CSS asset")
                asset_status = {path: call(base, path)[0] for path in asset_paths}
                require(all(status == 200 for status in asset_status.values()), "Built asset unavailable")

                unauthenticated_status, _, unauthenticated_raw = call(base, "/api/v1/trips")
                require(
                    unauthenticated_status == 401
                    and json_body(unauthenticated_raw)["detail"]["code"] == "UNAUTHENTICATED",
                    "Anonymous trip access must be rejected with 401",
                )

                register_status, _, register_raw = call(base, "/api/v1/auth/register", "POST", {
                    "username": "smoke-user", "password": "smoke-user-pass",
                })
                registered = json_body(register_raw)
                require(register_status == 201, f"Register failed: {register_raw.decode('utf-8')}")
                require(registered["user"]["username"] == "smoke-user", "Registered user mismatch")

                spots_status, _, spots_raw = call(base, "/api/v1/spots?city=%E4%B8%9C%E4%BA%AC")
                spots = json_body(spots_raw)
                require(spots_status == 200 and len(spots["items"]) == 8, "Demo spot catalog mismatch")
                initial_recent_status, _, initial_recent_raw = call(base, "/api/v1/trips")
                require(initial_recent_status == 200 and json_body(initial_recent_raw)["trips"] == [], "Temporary DB should start empty")

                create_payload = json.loads((ROOT / "examples/create_trip.json").read_text(encoding="utf-8"))
                create_status, _, create_raw = call(base, "/api/v1/trips", "POST", create_payload)
                created = json_body(create_raw)
                require(create_status == 201, f"Create failed: {create_raw.decode('utf-8')}")
                trip_id = created["trip_id"]
                plan = created["result"]["plan"]
                legs = [
                    item["transport_from_previous"]
                    for day in plan["days"]
                    for item in day["items"]
                    if item.get("transport_from_previous") is not None
                ]
                require(bool(legs), "Expected at least one transport leg")
                require(
                    all(
                        leg["provider"] == "local_estimate"
                        and leg["basis"] in {"haversine_estimate", "same_location"}
                        and leg["is_online"] is False
                        for leg in legs
                    ),
                    "Default routes must be explicit local offline estimates",
                )
                require(created["version"] == 1 and plan["trip_id"] == trip_id, "Initial version mismatch")

                recent_status, _, recent_raw = call(base, "/api/v1/trips")
                recent = json_body(recent_raw)["trips"]
                require(recent_status == 200 and recent[0]["trip_id"] == trip_id and recent[0]["version"] == 1, "Recent trip not persisted")

                modify_payload = json.loads((ROOT / "examples/modify_day.json").read_text(encoding="utf-8"))
                modify_status, _, modify_raw = call(base, f"/api/v1/trips/{trip_id}/modify-day", "POST", modify_payload)
                modified = json_body(modify_raw)
                require(modify_status == 200 and modified["version"] == 2, f"Modify failed: {modify_raw.decode('utf-8')}")
                require(modified["diff"]["unchanged_days"] == [1], "Locked day changed")

                diff_status, _, diff_raw = call(base, f"/api/v1/trips/{trip_id}/diff?from_version=1&to_version=2")
                require(diff_status == 200, "Version comparison failed")
                historical_status, _, historical_raw = call(base, f"/api/v1/trips/{trip_id}?version=1")
                historical = json_body(historical_raw)
                historical_plan = historical.get("result", {}).get("plan", historical)
                require(historical_status == 200 and historical_plan["version"] == 1, "Historical version unavailable")

                versions_status, _, versions_raw = call(base, f"/api/v1/trips/{trip_id}/versions")
                version_entries = json_body(versions_raw)["versions"]
                versions = [entry["version"] if isinstance(entry, dict) else entry for entry in version_entries]
                require(versions_status == 200 and versions == [1, 2], "Version list mismatch")

                stale_status, _, stale_raw = call(base, f"/api/v1/trips/{trip_id}/modify-day", "POST", {
                    "expected_version": 1,
                    "day_number": 2,
                    "desired_spot_ids": ["tokyo-hamarikyu"],
                    "lock_other_days": True,
                })
                stale = json_body(stale_raw)
                require(stale_status == 409 and stale["current_version"] == 2, "Optimistic lock not enforced")

                infeasible_payload = json.loads(json.dumps(create_payload))
                infeasible_payload["spec"].update({
                    "start_date": "2027-04-05",
                    "end_date": "2027-04-05",
                    "selected_spot_ids": ["tokyo-national-museum"],
                    "must_include_spot_ids": ["tokyo-national-museum"],
                })
                infeasible_status, _, infeasible_raw = call(base, "/api/v1/trips", "POST", infeasible_payload)
                detail = json_body(infeasible_raw)["detail"]
                require(infeasible_status == 422 and detail["code"] == "PLANNING_INFEASIBLE", "Infeasible request not rejected")
                require(detail.get("run_id") and detail.get("conflicts"), "Infeasible response lacks diagnostics")

                spa_status, _, spa_raw = call(base, "/workspace/example")
                unknown_status, unknown_type, _ = call(base, "/api/v1/not-found")
                require(spa_status == 200 and b"TravelAI" in spa_raw, "SPA fallback failed")
                require(unknown_status == 404 and "application/json" in unknown_type, "Unknown API must return JSON 404")

                evidence = {
                    "health": health,
                    "home_status": home_status,
                    "asset_status": asset_status,
                    "spots_count": len(spots["items"]),
                    "unauthenticated_status": unauthenticated_status,
                    "register_status": register_status,
                    "initial_recent_status": initial_recent_status,
                    "create_status": create_status,
                    "trip_id": trip_id,
                    "initial_evidence_count": len(plan.get("evidence", [])),
                    "recent_status": recent_status,
                    "recent_trips_version": recent[0]["version"],
                    "modify_status": modify_status,
                    "modify_version": modified["version"],
                    "unchanged_days": modified["diff"]["unchanged_days"],
                    "diff_status": diff_status,
                    "versions": versions,
                    "historical_status": historical_status,
                    "historical_version": historical_plan["version"],
                    "stale_modification_status": stale_status,
                    "stale_current_version": stale["current_version"],
                    "infeasible_status": infeasible_status,
                    "infeasible_code": detail["code"],
                    "infeasible_conflicts": [item["code"] for item in detail["conflicts"]],
                    "spa_fallback_status": spa_status,
                    "unknown_api_status": unknown_status,
                }
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    output = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    print("HTTP smoke: PASS", file=sys.stderr)


if __name__ == "__main__":
    main()
