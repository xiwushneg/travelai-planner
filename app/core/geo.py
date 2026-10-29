"""确定性地理与交通估算。结果为估算，不冒充实时地图。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.models import Spot, TransportLeg, TransportSettings


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def leg_between(from_id: str, from_lat: float, from_lng: float, to: Spot,
                settings: TransportSettings) -> TransportLeg:
    distance = haversine_km(from_lat, from_lng, to.latitude, to.longitude)
    minutes = math.ceil(distance / settings.average_speed_kmh * 60) + settings.buffer_minutes
    return TransportLeg(
        from_id=from_id,
        to_id=to.id,
        distance_km=round(distance, 3),
        travel_minutes=minutes,
        mode=settings.mode,
    )


@dataclass(frozen=True)
class Coordinate:
    latitude: float
    longitude: float


def distance_to_group(spot: Spot, group: list[Spot]) -> float:
    if not group:
        return 0.0
    return min(
        haversine_km(spot.latitude, spot.longitude, other.latitude, other.longitude)
        for other in group
    )
