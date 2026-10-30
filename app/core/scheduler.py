"""确定性行程排程器。

MVP 使用有限回溯 + 日内排列搜索，不宣称数学全局最优。
所有被放弃的候选与搜索限制都会显式返回。
"""
from __future__ import annotations

import itertools
import math
from datetime import date, time, timedelta
from typing import Optional

from app.core.geo import haversine_km
from app.maps.routing import RouteEstimator
from app.core.models import (
    CostBreakdown,
    DayPlan,
    ItineraryPlan,
    PlanningConflict,
    PlanningResult,
    ScheduleItem,
    SourceInfo,
    Spot,
    TransportGap,
    TransportLeg,
    TransportSettings,
    TripSpec,
    VerificationStatus,
)

MAX_SEARCH_NODES = 5000
MAX_GROUPS_PER_DAY = 240


def minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def as_time(value: int) -> time:
    return time(value // 60, value % 60)


def _clock(value: int) -> str:
    return as_time(value).strftime("%H:%M")


def ticket_cost(spot: Spot, spec: TripSpec) -> float:
    party = spec.party
    child = spot.child_ticket_cny if spot.child_ticket_cny is not None else spot.adult_ticket_cny
    senior = spot.senior_ticket_cny if spot.senior_ticket_cny is not None else spot.adult_ticket_cny
    return round(
        party.adults * spot.adult_ticket_cny
        + party.children * child
        + party.seniors * senior,
        2,
    )


def _travel(
    from_id: str,
    to_id: str,
    origin: Optional[tuple[float, float]],
    target: Optional[tuple[float, float]],
    settings: TransportSettings,
    route_estimator: Optional[RouteEstimator] = None,
) -> Optional[TransportLeg]:
    """按坐标计算一段通勤；坐标缺失或估算失败返回 None，由调用方记录 gap。"""
    if origin is None or target is None:
        return None
    if origin == target:
        # 同址（如午餐继承景点坐标）：不发生位移，不能叠加缓冲时间。
        return TransportLeg(
            from_id=from_id,
            to_id=to_id,
            distance_km=0,
            travel_minutes=0,
            mode=settings.mode,
            basis="same_location",
        )
    estimator = route_estimator or RouteEstimator()
    route = estimator.estimate(origin, target, settings)
    return TransportLeg(
        from_id=from_id,
        to_id=to_id,
        distance_km=route.distance_km,
        travel_minutes=route.travel_minutes,
        mode=settings.mode,
        basis=route.basis,
        provider=route.provider,
        is_online=route.is_online,
    )


def travel_between(
    a: ScheduleItem,
    b: ScheduleItem,
    settings: TransportSettings,
    route_estimator: Optional[RouteEstimator] = None,
) -> Optional[TransportLeg]:
    origin = (a.latitude, a.longitude) if None not in (a.latitude, a.longitude) else None
    target = (b.latitude, b.longitude) if None not in (b.latitude, b.longitude) else None
    return _travel(a.id, b.id, origin, target, settings, route_estimator)


def _effective_coordinates(items: list[ScheduleItem]) -> list[Optional[tuple[float, float]]]:
    """无坐标项（午餐/锚点）继承最近的上一个已定位项，否则继承下一个已定位项。

    仅用于路线估算，不把坐标写回这些项目本身。
    """
    result: list[Optional[tuple[float, float]]] = []
    previous: Optional[tuple[float, float]] = None
    for item in items:
        has_coords = item.latitude is not None and item.longitude is not None
        if has_coords:
            previous = (item.latitude, item.longitude)
        # 景点必须自带坐标；午餐/锚点等才允许继承邻近景点坐标。
        if item.spot_id:
            result.append((item.latitude, item.longitude) if has_coords else None)
        else:
            result.append(previous)
    following: Optional[tuple[float, float]] = None
    for index in range(len(items) - 1, -1, -1):
        item = items[index]
        if item.latitude is not None and item.longitude is not None:
            following = (item.latitude, item.longitude)
        if result[index] is None and not item.spot_id and following is not None:
            result[index] = following
    return result


def _anchor_item(anchor, source: Optional[SourceInfo] = None) -> ScheduleItem:
    return ScheduleItem(
        id=anchor.id,
        kind="anchor",
        title=anchor.title,
        date=anchor.date,
        start_time=anchor.start_time,
        end_time=anchor.end_time,
        latitude=anchor.latitude,
        longitude=anchor.longitude,
        source=source,
        notes=[anchor.notes] if anchor.notes else [],
    )


def _lunch_item(day: date, start: int, duration: int) -> ScheduleItem:
    return ScheduleItem(
        id=f"lunch-{day.isoformat()}",
        kind="meal",
        title="午餐与休息",
        date=day,
        start_time=as_time(start),
        end_time=as_time(start + duration),
        notes=["位置未建模，不计算午餐地点通勤"],
    )


def _fixed_items(
    spec: TripSpec, day: date, include_lunch: bool = True
) -> Optional[list[ScheduleItem]]:
    """不可移动项：锚点；include_lunch 为真时把午餐按原时段一并固定。"""
    items = [_anchor_item(a) for a in spec.anchors if a.date == day]
    if include_lunch:
        lunch_start, lunch_end = minutes(spec.lunch_start), minutes(spec.lunch_end)
        if lunch_start >= minutes(spec.daily_start_time) and lunch_end <= minutes(spec.daily_end_time):
            items.append(_lunch_item(day, lunch_start, lunch_end - lunch_start))
    items.sort(key=lambda item: (minutes(item.start_time), minutes(item.end_time), item.id))
    previous_end = minutes(spec.daily_start_time)
    day_end = minutes(spec.daily_end_time)
    for item in items:
        start, end = minutes(item.start_time), minutes(item.end_time)
        if start < previous_end or end > day_end:
            return None  # 锚点重叠或超出一日边界
        previous_end = end
    return items


def _gaps(spec: TripSpec, fixed: list[ScheduleItem]) -> list[tuple[int, int]]:
    result = []
    cursor = minutes(spec.daily_start_time)
    for item in fixed:
        start = minutes(item.start_time)
        if start > cursor:
            result.append((cursor, start))
        cursor = minutes(item.end_time)
    if cursor < minutes(spec.daily_end_time):
        result.append((cursor, minutes(spec.daily_end_time)))
    return result


def _spot_item(spot: Spot, spec: TripSpec, day: date, start: int) -> ScheduleItem:
    notes = []
    if spot.source.status != VerificationStatus.VERIFIED:
        notes.append("营业时间与票价未经本系统实时核验")
    return ScheduleItem(
        id=spot.id,
        kind="spot",
        title=spot.name,
        date=day,
        start_time=as_time(start),
        end_time=as_time(start + spot.visit_duration_minutes),
        spot_id=spot.id,
        latitude=spot.latitude,
        longitude=spot.longitude,
        ticket_cost_cny=ticket_cost(spot, spec),
        source=spot.source,
        notes=notes,
    )


def _insert_lunch(
    spec: TripSpec,
    day: date,
    items: list[ScheduleItem],
) -> tuple[list[ScheduleItem], list[str]]:
    """午餐软约束：景点排定后择空档插入，优先保留原时段，其次顺延，无空档则取消。

    顺延上限为午餐时长本身（默认 12:00–13:00 最晚 14:00 开始），
    避免把「午餐」推到傍晚这类名不副实的安排；取消或顺延都写入当日 warnings。
    """
    if not spec.include_lunch:
        return items, []
    lunch_start, lunch_end = minutes(spec.lunch_start), minutes(spec.lunch_end)
    if not (
        minutes(spec.daily_start_time) <= lunch_start
        and lunch_end <= minutes(spec.daily_end_time)
    ):
        return items, []  # 午餐时段不在当日开放窗口内，忽略
    duration = lunch_end - lunch_start
    latest_start = lunch_end + duration

    occupied = sorted(items, key=lambda item: (minutes(item.start_time), minutes(item.end_time), item.id))
    free: list[tuple[int, int]] = []
    cursor = minutes(spec.daily_start_time)
    for item in occupied:
        start, end = minutes(item.start_time), minutes(item.end_time)
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < minutes(spec.daily_end_time):
        free.append((cursor, minutes(spec.daily_end_time)))

    start_found: Optional[int] = None
    for gap_start, gap_end in free:
        candidate = max(gap_start, lunch_start)
        if candidate > latest_start or candidate + duration > gap_end:
            continue
        start_found = candidate
        break
    if start_found is None:
        return items, [
            f"当日行程紧凑，{_clock(lunch_start)}–{_clock(latest_start)} 内没有 "
            f"{duration} 分钟午餐空档，已取消午餐"
        ]
    lunch = _lunch_item(day, start_found, duration)
    result = sorted(
        items + [lunch],
        key=lambda item: (minutes(item.start_time), minutes(item.end_time), item.id),
    )
    if start_found == lunch_start:
        return result, []
    return result, [
        f"午餐已顺延至 {_clock(start_found)}–{_clock(start_found + duration)}"
        f"（原 {_clock(lunch_start)}–{_clock(lunch_end)} 与其他行程冲突）"
    ]


def schedule_day(
    spec: TripSpec,
    day_number: int,
    spots: tuple[Spot, ...],
    route_estimator: Optional[RouteEstimator] = None,
) -> Optional[DayPlan]:
    """为某一天寻找可行的最低通勤估算方案；找不到则返回 None。

    先尝试把午餐固定在原时段（保住 12:00–13:00 的常规安排），
    排不下再退化为「午餐顺延到空档，实在没有则取消」。
    """
    day = spec.start_date + timedelta(days=day_number - 1)
    for lunch_fixed in ((True, False) if spec.include_lunch else (False,)):
        fixed = _fixed_items(spec, day, include_lunch=lunch_fixed)
        if fixed is None:
            continue
        best = _schedule_with_fixed(
            spec, day, day_number, spots, fixed, route_estimator, lunch_fixed
        )
        if best is not None:
            return best
    return None


def _schedule_with_fixed(
    spec: TripSpec,
    day: date,
    day_number: int,
    spots: tuple[Spot, ...],
    fixed: list[ScheduleItem],
    route_estimator: Optional[RouteEstimator],
    lunch_fixed: bool,
) -> Optional[DayPlan]:
    gaps = _gaps(spec, fixed)
    best: Optional[DayPlan] = None
    best_score = None

    for order in itertools.permutations(spots):
        placed: list[ScheduleItem] = []
        feasible = True
        for spot in order:
            if day.weekday() in spot.opening.closed_weekdays or day in spot.opening.closed_dates:
                feasible = False
                break
            spot_placed = False
            for gap_start, gap_end in gaps:
                # 排列即时间顺序；游标必须随已排景点前进，否则会在同一 gap 内重叠。
                lower_bound = minutes(placed[-1].end_time) if placed else minutes(spec.daily_start_time)
                if gap_end <= lower_bound:
                    continue
                earliest = max(gap_start, lower_bound)
                previous = [item for item in fixed + placed if minutes(item.end_time) <= earliest]
                previous = max(previous, key=lambda item: minutes(item.end_time)) if previous else None
                # 无坐标的中间项（如午餐）不能作为路线起点，改从最近一个有坐标的项目估算。
                located_previous = [
                    item for item in fixed + placed
                    if item.latitude is not None and item.longitude is not None
                    and minutes(item.end_time) <= earliest
                ]
                located_previous = max(located_previous, key=lambda item: minutes(item.end_time)) if located_previous else None
                next_fixed = [item for item in fixed if minutes(item.start_time) >= gap_end]
                next_fixed = min(next_fixed, key=lambda item: minutes(item.start_time)) if next_fixed else None

                if previous:
                    earliest = max(earliest, minutes(previous.end_time))
                if earliest + spot.visit_duration_minutes >= 24 * 60:
                    # 起点已接近午夜，当天不可能完成游览；提前跳过，避免构造跨日时间。
                    continue
                if located_previous:
                    probe = _spot_item(spot, spec, day, earliest)
                    leg = travel_between(located_previous, probe, spec.transport, route_estimator)
                    if leg:
                        earliest = max(earliest, minutes(located_previous.end_time) + leg.travel_minutes)
                start = max(earliest, minutes(spot.opening.open_time))
                end = start + spot.visit_duration_minutes
                if end > gap_end or end > minutes(spot.opening.close_time):
                    continue
                candidate = _spot_item(spot, spec, day, start)
                if next_fixed:
                    leg = travel_between(candidate, next_fixed, spec.transport, route_estimator)
                    if leg and end + leg.travel_minutes > minutes(next_fixed.start_time):
                        continue
                placed.append(candidate)
                spot_placed = True
                break
            if not spot_placed:
                feasible = False
                break
        if not feasible:
            continue

        items = sorted(fixed + placed, key=lambda item: (minutes(item.start_time), item.id))
        if lunch_fixed:
            lunch_warnings: list[str] = []
        else:
            items, lunch_warnings = _insert_lunch(spec, day, items)
        total_km = 0.0
        total_travel = 0
        uncomputed: list[TransportGap] = []
        valid = True
        coordinates = _effective_coordinates(items)
        last_located: Optional[ScheduleItem] = None
        for index, item in enumerate(items):
            located_now = item.latitude is not None and item.longitude is not None
            if index > 0:
                previous_item = items[index - 1]
                origin = coordinates[index - 1]
                target = coordinates[index]
                leg = (
                    _travel(previous_item.id, item.id, origin, target, spec.transport, route_estimator)
                    if origin is not None and target is not None
                    else None
                )
                if leg is None:
                    uncomputed.append(TransportGap(
                        from_item_id=previous_item.id,
                        to_item_id=item.id,
                        reason="相邻项目缺少坐标，未计算该段通勤",
                    ))
                else:
                    item.transport_from_previous = leg
                    # 到达时间不早于紧邻项的结束时间，也不早于"上一有坐标项结束 + 通勤"。
                    arrival_from_previous = minutes(previous_item.end_time)
                    arrival_from_located = (
                        minutes(last_located.end_time) + leg.travel_minutes
                        if last_located is not None else arrival_from_previous
                    )
                    if max(arrival_from_previous, arrival_from_located) > minutes(item.start_time):
                        valid = False
                        break
                    total_km += leg.distance_km
                    total_travel += leg.travel_minutes
            if located_now:
                last_located = item
        if not valid:
            continue
        last_end = max((minutes(item.end_time) for item in items), default=minutes(spec.daily_start_time))
        score = (len(uncomputed), total_travel, round(total_km, 3), last_end, tuple(s.id for s in order))
        if best_score is None or score < best_score:
            best_score = score
            best = DayPlan(
                day_number=day_number,
                date=day,
                items=items,
                total_travel_km=round(total_km, 3),
                total_travel_minutes=total_travel,
                visit_minutes=sum(s.visit_duration_minutes for s in spots),
                ticket_cost_cny=round(sum(ticket_cost(s, spec) for s in spots), 2),
                transport_complete=not uncomputed,
                uncomputed_legs=uncomputed,
                warnings=(
                    lunch_warnings
                    + (
                        [f"{len(uncomputed)} 段通勤缺少坐标未计入总计，已单独标记"]
                        if uncomputed else []
                    )
                ),
            )
    return best


def empty_day(
    spec: TripSpec,
    day_number: int,
    route_estimator: Optional[RouteEstimator] = None,
) -> Optional[DayPlan]:
    return schedule_day(spec, day_number, tuple(), route_estimator=route_estimator)


def _rank(spot: Spot, spec: TripSpec) -> float:
    score = spot.rating
    if spot.id in spec.must_include_spot_ids:
        score += 1000
    if spot.category in spec.preferred_categories:
        score += 8
    score += len(set(spot.tags) & set(spec.preferred_categories)) * 2
    return score


def _cost(spec: TripSpec, days: list[DayPlan], used_spots: dict[str, Spot]) -> CostBreakdown:
    scheduled_ids = {
        item.spot_id for day in days for item in day.items if item.spot_id
    }
    used_spots = {
        spot_id: spot for spot_id, spot in used_spots.items()
        if spot_id in scheduled_ids
    }
    tickets = round(sum(day.ticket_cost_cny for day in days), 2)
    transport = round(sum(day.total_travel_km for day in days) * spec.transport.cost_per_km_cny, 2)
    uncertain_tickets = sum(
        ticket_cost(spot, spec)
        for spot in used_spots.values()
        if spot.source.status != VerificationStatus.VERIFIED
    )
    high = round(tickets + uncertain_tickets * 0.2 + transport * 1.5, 2)
    notes = ["交通按球面距离、固定速度和缓冲时间估算，不是实时路况"]
    if uncertain_tickets:
        notes.append("含未实时核验票价，预算上限增加 20% 不确定性缓冲")
    if spec.transport.cost_per_km_cny == 0:
        notes.append("当前交通单价为 0，未计入交通费用")
    return CostBreakdown(
        tickets_cny=tickets,
        transport_estimate_cny=transport,
        total_low_cny=round(tickets + transport, 2),
        total_high_cny=high,
        notes=notes,
    )


def plan_trip(
    spec: TripSpec,
    spots: list[Spot],
    locked_days: Optional[dict[int, DayPlan]] = None,
    fixed_day_spot_ids: Optional[dict[int, list[str]]] = None,
    route_estimator: Optional[RouteEstimator] = None,
) -> PlanningResult:
    """排程入口。

    locked_days 保留完整旧日计划；fixed_day_spot_ids 只固定某天的景点完整集合，
    具体时间和顺序仍由排程器求解。两类日期不能重叠。
    """
    locked_days = locked_days or {}
    fixed_day_spot_ids = fixed_day_spot_ids or {}
    by_id = {spot.id: spot for spot in spots}
    conflicts: list[PlanningConflict] = []
    fixed_required_ids = {
        spot_id for spot_ids in fixed_day_spot_ids.values() for spot_id in spot_ids
    }
    effective_must_ids = set(spec.must_include_spot_ids) | fixed_required_ids

    if set(locked_days) & set(fixed_day_spot_ids):
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="contradictory_constraints",
            message="同一天不能同时作为旧计划锁定日和目标景点固定日",
        )])
    if any(
        day < 1 or day > spec.duration_days or len(spot_ids) > spec.spots_per_day
        for day, spot_ids in fixed_day_spot_ids.items()
    ):
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="capacity_exceeded",
            message="目标日不存在或指定景点数量超过每日容量",
            suggestions=["检查目标日期", "减少目标日景点", "提高每日景点上限"],
        )])

    required_ids = set(spec.selected_spot_ids) | effective_must_ids
    missing = sorted(required_ids - set(by_id))
    if missing:
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="unknown_spot",
            message=f"以下景点不存在：{', '.join(missing)}",
            details={"spot_ids": missing},
        )])

    city_spot_ids = {s.id for s in spots if s.city == spec.destination}
    unavailable = sorted(required_ids - city_spot_ids)
    if unavailable:
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="required_spot_unavailable",
            message=f"以下指定景点不在目的地或不可用：{', '.join(unavailable)}",
            suggestions=["检查目的地", "移除不可用的指定景点"],
            details={"spot_ids": unavailable},
        )])

    candidates = [s for s in spots if s.city == spec.destination]
    if spec.selected_spot_ids:
        allowed = set(spec.selected_spot_ids) | fixed_required_ids
        candidates = [s for s in candidates if s.id in allowed]
    must = [s for s in candidates if s.id in effective_must_ids]
    wrong_city_must = sorted(effective_must_ids - {s.id for s in must})
    if wrong_city_must:
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="must_spot_unavailable",
            message=f"必去景点不在目的地或不可用：{', '.join(wrong_city_must)}",
            suggestions=["调整目的地", "移除对应必去约束"],
        )])
    avoided_must = [s.id for s in must if s.category in spec.avoided_categories]
    if avoided_must:
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="contradictory_constraints",
            message=f"必去景点同时属于排除类别：{', '.join(avoided_must)}",
            suggestions=["移除排除类别", "取消对应必去约束"],
        )])

    candidates = [s for s in candidates if s.category not in spec.avoided_categories or s.id in effective_must_ids]
    locked_spot_ids = {
        item.spot_id
        for day in locked_days.values()
        for item in day.items
        if item.spot_id
    }
    candidates = [s for s in candidates if s.id not in locked_spot_ids]
    candidates.sort(key=lambda s: (-_rank(s, spec), s.id))

    unlocked_count = spec.duration_days - len(locked_days)
    capacity = max(unlocked_count, 0) * spec.spots_per_day
    if len(candidates) > capacity:
        retained = candidates[:capacity]
        omitted = candidates[capacity:]
    else:
        retained, omitted = candidates, []
    if any(s.id in effective_must_ids for s in omitted):
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="capacity_exceeded",
            message="必去景点数量超过当前每日容量",
            suggestions=["增加天数", "提高每日景点上限", "减少必去景点"],
        )])

    warnings = []
    if omitted:
        warnings.append(f"受每日容量限制，{len(omitted)} 个普通候选未进入本次排程")
    used_spots = {s.id: s for s in candidates}
    schedule_cache: dict[tuple[int, tuple[str, ...]], Optional[DayPlan]] = {}

    def cached_day(day_number: int, group: tuple[Spot, ...]) -> Optional[DayPlan]:
        key = (day_number, tuple(sorted(s.id for s in group)))
        if key not in schedule_cache:
            schedule_cache[key] = schedule_day(
                spec,
                day_number,
                tuple(sorted(group, key=lambda s: s.id)),
                route_estimator=route_estimator,
            )
        return schedule_cache[key]

    def attempt(working: list[Spot]) -> Optional[list[DayPlan]]:
        nodes = 0

        def search(day_number: int, remaining: tuple[Spot, ...]) -> Optional[list[DayPlan]]:
            nonlocal nodes
            nodes += 1
            if nodes > MAX_SEARCH_NODES:
                return None
            if day_number > spec.duration_days:
                return [] if not remaining else None
            if day_number in locked_days:
                tail = search(day_number + 1, remaining)
                return [locked_days[day_number]] + tail if tail is not None else None
            if day_number in fixed_day_spot_ids:
                wanted = set(fixed_day_spot_ids[day_number])
                group = tuple(s for s in remaining if s.id in wanted)
                if len(group) != len(wanted):
                    return None
                current = cached_day(day_number, group)
                if current is None:
                    return None
                tail = search(
                    day_number + 1,
                    tuple(s for s in remaining if s.id not in wanted),
                )
                return [current] + tail if tail is not None else None
            if not remaining:
                current = empty_day(spec, day_number, route_estimator=route_estimator)
                if current is None:
                    return None
                tail = search(day_number + 1, remaining)
                return [current] + tail if tail is not None else None

            max_size = min(spec.spots_per_day, len(remaining))
            for size in range(max_size, 0, -1):
                groups = list(itertools.combinations(remaining, size))
                groups.sort(key=lambda group: (
                    -sum(_rank(s, spec) for s in group),
                    sum(1 for s in group if s.id in spec.must_include_spot_ids) * -1,
                    tuple(s.id for s in group),
                ))
                for group in groups[:MAX_GROUPS_PER_DAY]:
                    current = cached_day(day_number, group)
                    if current is None:
                        continue
                    remaining_ids = {s.id for s in group}
                    tail = search(day_number + 1, tuple(s for s in remaining if s.id not in remaining_ids))
                    if tail is not None:
                        return [current] + tail
                    if nodes > MAX_SEARCH_NODES:
                        return None
            # 空日也参与搜索：某些景点可能只在后续日期开放。
            current = empty_day(spec, day_number, route_estimator=route_estimator)
            if current is not None:
                tail = search(day_number + 1, remaining)
                if tail is not None:
                    return [current] + tail
            return None

        return search(1, tuple(working))

    working = list(retained)
    unscheduled: list[str] = [s.id for s in omitted]
    days: Optional[list[DayPlan]] = None
    while True:
        days = attempt(working)
        if days is not None:
            break
        removable = [s for s in working if s.id not in effective_must_ids]
        if not removable:
            return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
                code="no_feasible_schedule_found",
                message="在当前时间窗、预约锚点与搜索限额内，未找到可执行的必去景点方案",
                suggestions=["减少必去景点", "延长每日开放时间", "增加天数", "检查锚点是否过密"],
                details={"search_node_limit": MAX_SEARCH_NODES},
            )])
        # 逐个尝试移除：优先选择移除后立刻可行的候选，避免连排得下的景点一起被丢弃。
        ordered = sorted(removable, key=lambda s: (_rank(s, spec), s.id))
        chosen = None
        for candidate in ordered:
            trial = [s for s in working if s.id != candidate.id]
            trial_days = attempt(trial)
            if trial_days is not None:
                chosen, working, days = candidate, trial, trial_days
                break
        if chosen is None:
            chosen = ordered[0]
            working = [s for s in working if s.id != chosen.id]
        # 每次只移除一个普通候选，并明确记录，不静默丢弃。
        unscheduled.append(chosen.id)

    cost = _cost(spec, days, used_spots)
    if spec.budget_cny is not None and cost.total_low_cny > spec.budget_cny:
        return PlanningResult(status="infeasible", conflicts=[PlanningConflict(
            code="budget_exceeded",
            message=f"当前方案最低估算 {cost.total_low_cny:.2f} 元，超过预算 {spec.budget_cny:.2f} 元",
            suggestions=["减少收费景点", "提高预算", "调整同行人或交通方式"],
            details={"budget_scope": spec.budget_scope},
        )])
    if spec.budget_cny is not None and cost.total_high_cny > spec.budget_cny:
        warnings.append("最低估算未超预算，但考虑不确定性后的预算上限可能超支")
    if unscheduled:
        warnings.append(f"为得到可执行方案，以下普通候选未排入：{', '.join(unscheduled)}")
    warnings.append("开放时间与票价按当前本地目录数据处理；真实出行前应重新核验")
    if route_estimator is not None:
        warnings.extend(
            warning for warning in route_estimator.warnings if warning not in warnings
        )
    return PlanningResult(status="success", plan=ItineraryPlan(
        spec=spec,
        days=days,
        cost=cost,
        assumptions=list(spec.assumptions),
        warnings=warnings,
        unscheduled_spot_ids=unscheduled,
    ))
