from datetime import date

from app.core.models import Anchor, TripSpec
from app.core.scheduler import minutes, plan_trip
from app.data.seed import DEMO_SPOTS


def validate_plan(result):
    assert result.status == "success", result.model_dump(mode="json")
    plan = result.plan
    by_id = {spot.id: spot for spot in DEMO_SPOTS}
    for day in plan.days:
        ordered = sorted(day.items, key=lambda item: (minutes(item.start_time), item.id))
        assert [(i.start_time, i.end_time) for i in ordered] == [
            (i.start_time, i.end_time) for i in day.items
        ]
        previous_end = minutes(plan.spec.daily_start_time)
        last_located = None
        for item in day.items:
            start, end = minutes(item.start_time), minutes(item.end_time)
            assert start >= previous_end, f"时间重叠：{day.date} {item.title}"
            assert end <= minutes(plan.spec.daily_end_time)
            if item.transport_from_previous:
                # 通勤从最近一个有坐标的项目算起，允许中间穿插无坐标的午餐。
                assert last_located is not None
                leg = item.transport_from_previous
                assert minutes(last_located.end_time) + leg.travel_minutes <= start
            if item.spot_id:
                spot = by_id[item.spot_id]
                assert day.date.weekday() not in spot.opening.closed_weekdays
                assert day.date not in spot.opening.closed_dates
                assert start >= minutes(spot.opening.open_time)
                assert end <= minutes(spot.opening.close_time)
            previous_end = end
            if item.latitude is not None and item.longitude is not None:
                last_located = item
    return plan


def test_three_day_plan_is_feasible_and_deterministic():
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 3),
        selected_spot_ids=[spot.id for spot in DEMO_SPOTS],
        preferred_categories=["文化", "亲子"],
    )
    first = validate_plan(plan_trip(spec, DEMO_SPOTS))
    second = validate_plan(plan_trip(spec, DEMO_SPOTS))
    assert first.days == second.days
    assert first.cost == second.cost
    assert len({item.spot_id for day in first.days for item in day.items if item.spot_id}) == 8
    assert first.cost.total_low_cny <= first.cost.total_high_cny


def test_closed_day_must_spot_returns_infeasible_not_fake_plan():
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 5),  # Monday
        end_date=date(2027, 4, 5),
        selected_spot_ids=["tokyo-national-museum"],
        must_include_spot_ids=["tokyo-national-museum"],
        include_lunch=False,
    )
    result = plan_trip(spec, DEMO_SPOTS)
    assert result.status == "infeasible"
    assert result.plan is None
    assert result.conflicts[0].code == "no_feasible_schedule_found"


def test_overlapping_anchors_are_rejected():
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 1),
        selected_spot_ids=["tokyo-sensoji"],
        include_lunch=False,
        anchors=[
            Anchor(title="预约 A", date=date(2027, 4, 1), start_time="10:00", end_time="12:00"),
            Anchor(title="预约 B", date=date(2027, 4, 1), start_time="11:00", end_time="13:00"),
        ],
    )
    result = plan_trip(spec, DEMO_SPOTS)
    assert result.status == "infeasible"


def test_budget_violation_is_explicit():
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 1),
        selected_spot_ids=["tokyo-shibuya-sky"],
        must_include_spot_ids=["tokyo-shibuya-sky"],
        budget_cny=100,
        include_lunch=False,
    )
    result = plan_trip(spec, DEMO_SPOTS)
    assert result.status == "infeasible"
    assert result.conflicts[0].code == "budget_exceeded"


def test_unknown_and_wrong_city_spots_do_not_silently_disappear():
    unknown = TripSpec(
        destination="东京", start_date=date(2027, 4, 1), end_date=date(2027, 4, 1),
        selected_spot_ids=["not-found"],
    )
    result = plan_trip(unknown, DEMO_SPOTS)
    assert result.status == "infeasible"
    assert result.conflicts[0].code == "unknown_spot"

    wrong_city_spots = [spot.model_copy(deep=True) for spot in DEMO_SPOTS]
    wrong_city_spots[0].city = "大阪"
    wrong_city = TripSpec(
        destination="东京", start_date=date(2027, 4, 1), end_date=date(2027, 4, 1),
        selected_spot_ids=[wrong_city_spots[0].id],
    )
    result = plan_trip(wrong_city, wrong_city_spots)
    assert result.status == "infeasible"
    assert result.conflicts[0].code == "required_spot_unavailable"


def test_capacity_omissions_are_reported_and_do_not_inflate_budget():
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 1),
        selected_spot_ids=["tokyo-sensoji", "tokyo-shibuya-sky"],
        must_include_spot_ids=["tokyo-sensoji"],
        max_spots_per_day=1,
        budget_cny=0,
        include_lunch=False,
    )
    result = plan_trip(spec, DEMO_SPOTS)
    assert result.status == "success", result.model_dump(mode="json")
    assert result.plan.unscheduled_spot_ids == ["tokyo-shibuya-sky"]
    assert result.plan.cost.total_low_cny == 0
    assert result.plan.cost.total_high_cny == 0


def test_lunch_cross_day_transport_is_complete():
    """含午餐的跨日行程：午餐继承邻近坐标，通勤链路完整且总计可核对。"""
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 2),
        selected_spot_ids=[spot.id for spot in DEMO_SPOTS[:4]],
        include_lunch=True,
    )
    plan = validate_plan(plan_trip(spec, DEMO_SPOTS))
    days_with_spots = [day for day in plan.days if any(item.spot_id for item in day.items)]
    assert len(days_with_spots) == 2
    for day in days_with_spots:
        assert day.transport_complete is True, day.model_dump(mode="json")
        assert day.uncomputed_legs == []
        assert day.items[0].transport_from_previous is None
        for item in day.items:
            assert item.duration_minutes == minutes(item.end_time) - minutes(item.start_time)
        meals = [item for item in day.items if item.kind == "meal"]
        assert meals
        for meal in meals:
            # 午餐没有自身坐标，但应继承邻近景点坐标，得到 0 km 的显式通勤段而不是 None。
            assert meal.transport_from_previous is not None
            assert meal.transport_from_previous.distance_km == 0
        legs = [item.transport_from_previous for item in day.items if item.transport_from_previous]
        assert round(sum(leg.distance_km for leg in legs), 3) == day.total_travel_km
        assert sum(leg.travel_minutes for leg in legs) == day.total_travel_minutes
        if len([item for item in day.items if item.spot_id]) >= 2:
            assert day.total_travel_km > 0
            assert day.total_travel_minutes > 0


def test_missing_spot_coordinates_reported_as_gap_not_silent_zero():
    """含午餐的跨日行程中，景点缺坐标必须显式记录未计算路段，不能用 0.0 冒充已计算。"""
    broken = DEMO_SPOTS[0].model_copy(update={"latitude": None, "longitude": None})
    spots = [broken, DEMO_SPOTS[1], DEMO_SPOTS[2]]
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 7),
        end_date=date(2027, 4, 8),
        selected_spot_ids=[spot.id for spot in spots],
        include_lunch=True,
    )
    plan = validate_plan(plan_trip(spec, spots))
    incomplete = [day for day in plan.days if day.transport_complete is False]
    assert incomplete
    for day in incomplete:
        assert day.uncomputed_legs
        assert any("未计入" in warning for warning in day.warnings)
        for gap in day.uncomputed_legs:
            assert "未计算" in gap.reason
            assert gap.from_item_id
            gap_item = next(item for item in day.items if item.id == gap.to_item_id)
            assert gap_item.transport_from_previous is None
        legs = [item.transport_from_previous for item in day.items if item.transport_from_previous]
        assert round(sum(leg.distance_km for leg in legs), 3) == day.total_travel_km
        assert sum(leg.travel_minutes for leg in legs) == day.total_travel_minutes
    assert any(item.kind == "meal" for day in plan.days for item in day.items)
