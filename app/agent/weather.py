"""Open-Meteo 实时天气预报（免 Key）；失败或超出预报窗口时显式标注，不编造数据。"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
    56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "雷暴伴冰雹",
}

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

FORECAST_WINDOW_DAYS = 16


def describe_weather(code: Optional[int]) -> str:
    try:
        return WEATHER_CODES.get(int(code), "未知天气")
    except (TypeError, ValueError):
        return "未知天气"


def _advice(temp_max: Optional[float], temp_min: Optional[float], precip: Optional[float]) -> str:
    tips = []
    if precip is not None and precip >= 60:
        tips.append("降水概率较高，建议带伞")
    if temp_max is not None and temp_max >= 30:
        tips.append("气温偏高，注意防晒补水")
    if temp_min is not None and temp_min <= 8:
        tips.append("早晚偏凉，注意保暖")
    return "；".join(tips)


class OpenMeteoWeather:
    """天气工具：优先使用景点库坐标，避免额外地理编码；失败一律标注不可用。"""

    provider = "open_meteo"

    def __init__(
        self, repository=None, *, timeout_seconds: float = 6.0,
        enabled: bool = True, cache_ttl_seconds: int = 600,
        today_provider: Callable[[], date] = date.today,
    ):
        self.repository = repository
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled
        self.cache_ttl_seconds = cache_ttl_seconds
        self.today_provider = today_provider
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def fetch(self, city: str, start_date: Optional[date], end_date: Optional[date]) -> dict:
        today = self.today_provider()
        start = start_date or today
        end = end_date or min(start + timedelta(days=2), today + timedelta(days=FORECAST_WINDOW_DAYS - 1))
        if end < start:
            end = start
        if not self.enabled:
            return self._unavailable(city, start, end, "天气查询已关闭")
        if end < today:
            return self._unavailable(city, start, end, "历史天气不在本系统查询范围内")
        if start > today + timedelta(days=FORECAST_WINDOW_DAYS - 1):
            return self._unavailable(
                city, start, end,
                f"免费预报仅覆盖未来约 {FORECAST_WINDOW_DAYS} 天，该日期暂不可查询",
            )
        start = max(start, today)
        end = min(end, today + timedelta(days=FORECAST_WINDOW_DAYS - 1))
        key = (city, start.isoformat(), end.isoformat())
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < self.cache_ttl_seconds:
            return cached[1]
        coords = self._coords(city)
        if not coords:
            return self._unavailable(city, start, end, f"无法定位城市「{city}」")
        card = self._forecast(city, coords, start, end)
        if card["status"] == "ok":
            self._cache[key] = (time.time(), card)
            if len(self._cache) > 64:
                self._cache.pop(next(iter(self._cache)))
        return card

    def _unavailable(self, city: str, start: date, end: date, reason: str) -> dict:
        return {
            "type": "weather", "status": "unavailable", "city": city,
            "provider": self.provider, "is_realtime": False,
            "requested": {"start_date": start.isoformat(), "end_date": end.isoformat()},
            "days": [],
            "notice": f"{reason}。未获取到实时天气，不提供推测数据；出行前请以官方天气预报为准。",
        }

    def _coords(self, city: str) -> Optional[tuple[float, float]]:
        if self.repository is not None:
            spots = self.repository.list_spots(city)
            if spots:
                return (
                    sum(spot.latitude for spot in spots) / len(spots),
                    sum(spot.longitude for spot in spots) / len(spots),
                )
        return self._geocode(city)

    def _geocode(self, city: str) -> Optional[tuple[float, float]]:
        query = urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
        request = Request(
            "https://geocoding-api.open-meteo.com/v1/search?" + query,
            headers={"Accept": "application/json", "User-Agent": "TravelAI/1.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            result = data["results"][0]
            return float(result["latitude"]), float(result["longitude"])
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError,
                json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
            return None

    def _forecast(
        self, city: str, coords: tuple[float, float], start: date, end: date
    ) -> dict:
        query = urlencode({
            "latitude": f"{coords[0]:.5f}",
            "longitude": f"{coords[1]:.5f}",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "Asia/Shanghai",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })
        request = Request(
            "https://api.open-meteo.com/v1/forecast?" + query,
            headers={"Accept": "application/json", "User-Agent": "TravelAI/1.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
            daily = data["daily"]
            dates = daily["time"]
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError,
                json.JSONDecodeError, KeyError, TypeError):
            return self._unavailable(city, start, end, "天气服务暂时不可用")

        def _at(values, index):
            try:
                return values[index]
            except (IndexError, TypeError):
                return None

        days = []
        for index, day_text in enumerate(dates):
            try:
                day = date.fromisoformat(day_text)
            except (TypeError, ValueError):
                continue
            temp_max = _at(daily.get("temperature_2m_max"), index)
            temp_min = _at(daily.get("temperature_2m_min"), index)
            precip = _at(daily.get("precipitation_probability_max"), index)
            days.append({
                "date": day.isoformat(),
                "weekday": _WEEKDAYS[day.weekday()],
                "weather": describe_weather(_at(daily.get("weather_code"), index)),
                "temp_max": temp_max,
                "temp_min": temp_min,
                "precip_prob": precip,
                "advice": _advice(temp_max, temp_min, precip),
            })
        return {
            "type": "weather", "status": "ok", "city": city,
            "provider": self.provider, "is_realtime": True,
            "requested": {"start_date": start.isoformat(), "end_date": end.isoformat()},
            "days": days,
            "notice": "数据来自 Open-Meteo 免费预报，仅供参考；出行前请以官方天气预报为准。",
        }
