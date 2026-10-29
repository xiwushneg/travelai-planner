import json
import math
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.geo import haversine_km
from app.core.models import TransportSettings, TravelParty, TripSpec
from app.data.seed import DEMO_SPOTS
from app.maps.base import (
    BaiduRouteProvider,
    LocalRouteProvider,
    MapProviderError,
    MockRouteProvider,
)
from app.maps.factory import build_map_provider
from app.maps.routing import RouteEstimator
from app.rag.base import NullRetriever
from app.repository import Repository
from app.service import PlanningService


ORIGIN = (35.0, 139.0)
DESTINATION = (35.01, 139.02)


def test_local_provider_matches_legacy_haversine_estimate():
    transport = TransportSettings()
    route = LocalRouteProvider().estimate(ORIGIN, DESTINATION, transport)
    distance = haversine_km(*ORIGIN, *DESTINATION)

    assert route.distance_km == round(distance, 3)
    assert route.travel_minutes == math.ceil(distance / transport.average_speed_kmh * 60) + transport.buffer_minutes
    assert route.provider == "local_estimate"
    assert route.basis == "haversine_estimate"
    assert route.is_online is False


def test_baidu_directionlite_parses_route_without_returning_ak(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "status": 0,
                "result": {"routes": [{"distance": 1250, "duration": 601}]},
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.maps.base.urlopen", fake_urlopen)
    provider = BaiduRouteProvider("secret-ak", timeout_seconds=2.5, mode="walking")
    route = provider.estimate(ORIGIN, DESTINATION, TransportSettings())
    query = parse_qs(urlparse(captured["url"]).query)

    assert urlparse(captured["url"]).path == "/directionlite/v1/walking"
    assert query["origin"] == ["35.000000,139.000000"]
    assert query["destination"] == ["35.010000,139.020000"]
    assert query["coord_type"] == ["wgs84"]
    assert captured["timeout"] == 2.5
    assert route.distance_km == 1.25
    assert route.travel_minutes == math.ceil(601 / 60) + 15
    assert route.provider == "baidu_directionlite"
    assert route.is_online is True
    assert "secret-ak" not in route.model_dump_json()


def test_baidu_error_does_not_leak_ak_or_request_url(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"status": 1, "message": "请求参数错误"}).encode("utf-8")

    monkeypatch.setattr("app.maps.base.urlopen", lambda request, timeout: FakeResponse())
    provider = BaiduRouteProvider("secret-ak")

    with pytest.raises(MapProviderError) as caught:
        provider.estimate(ORIGIN, DESTINATION, TransportSettings())

    message = str(caught.value)
    assert "请求参数错误" in message
    assert "secret-ak" not in message
    assert "api.map.baidu.com" not in message


def test_missing_baidu_ak_falls_back_to_offline_provider():
    provider, mode = build_map_provider(SimpleNamespace(
        map_provider="baidu",
        baidu_map_ak=None,
        baidu_route_mode="walking",
        map_timeout_seconds=4,
    ))
    assert isinstance(provider, LocalRouteProvider)
    assert provider.online is False
    assert mode == "local_fallback_baidu_ak_missing"


def test_route_estimator_caches_and_enforces_online_budget():
    provider = MockRouteProvider(distance_km=2, duration_seconds=300)
    provider.name = "online-test"
    provider.online = True
    estimator = RouteEstimator(provider, max_online_calls=1)
    transport = TransportSettings()

    first = estimator.estimate(ORIGIN, DESTINATION, transport)
    cached = estimator.estimate(ORIGIN, DESTINATION, transport)
    over_budget = estimator.estimate(ORIGIN, (35.02, 139.03), transport)

    assert first is cached
    assert len(provider.calls) == 1
    assert estimator.online_calls == 1
    assert over_budget.provider == "local_fallback"
    assert over_budget.basis == "haversine_estimate_after_online_budget"
    assert over_budget.is_online is False
    assert any("1 次上限" in warning for warning in estimator.warnings)


def test_route_estimator_failure_falls_back_with_explicit_warning():
    provider = MockRouteProvider(error="测试中的计划内网络失败")
    provider.online = True
    estimator = RouteEstimator(provider, max_online_calls=2)

    route = estimator.estimate(ORIGIN, DESTINATION, TransportSettings())

    assert route.provider == "local_fallback"
    assert route.basis == "haversine_estimate_after_provider_failure"
    assert route.is_online is False
    assert estimator.online_calls == 1
    assert estimator.warnings == ["测试中的计划内网络失败；受影响路段已使用本地估算，不能视为实时路线。"]


def test_service_propagates_mock_route_metadata_to_plan(tmp_path):
    repository = Repository(tmp_path / "map-plan.db")
    repository.replace_spots(DEMO_SPOTS)
    provider = MockRouteProvider(distance_km=1.5, duration_seconds=300)
    service = PlanningService(repository, NullRetriever(), map_provider=provider)
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 1),
        party=TravelParty(adults=2, children=1),
        pace="relaxed",
        budget_cny=8000,
        selected_spot_ids=["tokyo-ueno-park", "tokyo-sensoji"],
        must_include_spot_ids=["tokyo-ueno-park", "tokyo-sensoji"],
        max_spots_per_day=2,
        include_lunch=False,
    )

    result, _ = service.create_trip(spec, include_evidence=False)

    assert result.status == "success", result.model_dump(mode="json")
    legs = [
        item.transport_from_previous
        for day in result.plan.days
        for item in day.items
        if item.transport_from_previous is not None
    ]
    assert legs
    assert all(leg.provider == "mock" for leg in legs)
    assert all(leg.basis == "mock_route" for leg in legs)
    assert all(leg.is_online is False for leg in legs)
    assert provider.calls
