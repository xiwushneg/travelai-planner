"""规则意图解析：没有 LLM 时也能识别天气、住宿、机票、门票与行程需求。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

INTENT_NAMES = {
    "plan_trip", "weather_query", "hotel_search", "flight_query",
    "ticket_query", "guide_query", "general_chat",
}

INTENT_LABELS = {
    "plan_trip": "规划行程",
    "weather_query": "天气查询",
    "hotel_search": "住宿建议",
    "flight_query": "机票说明",
    "ticket_query": "门票查询",
    "guide_query": "景点攻略",
    "general_chat": "通用问答",
}

# 用于识别用户提到的城市；能否真正规划由景点库决定。
STATIC_CITIES = [
    "北京", "上海", "广州", "深圳", "成都", "杭州", "重庆", "西安", "南京", "武汉",
    "苏州", "天津", "长沙", "青岛", "厦门", "昆明", "大理", "丽江", "三亚", "桂林",
    "香港", "澳门", "东京", "大阪", "京都", "首尔", "曼谷", "新加坡", "巴黎", "伦敦",
]

CHINESE_NUM = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_PREFERENCE_KEYWORDS = {
    "亲子": "亲子", "孩子": "亲子", "小孩": "亲子", "带娃": "亲子",
    "自然": "自然", "公园": "公园", "户外": "自然",
    "文化": "文化", "历史": "文化", "博物馆": "博物馆",
    "购物": "购物", "美食": "美食", "夜景": "夜景", "摄影": "摄影",
}

_EXPLICIT_DATE = re.compile(
    r"(?:(\d{4})\s*[年\-/.]\s*)?(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?"
)
_RELATIVE_DAYS = {"大后天": 3, "后天": 2, "明天": 1, "明日": 1, "今天": 0, "今日": 0}
_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
_DURATION_RE = re.compile(r"(?<![第月])(\d{1,2}|[一二两三四五六七八九十])\s*[天日](?![气线])")
_PARTY_RE = re.compile(r"(\d{1,2}|[一两二三四五六七八九十])\s*个?\s*人")
_ADULT_RE = re.compile(r"(\d{1,2}|[一两二三四五六七八九十])\s*个?\s*(?:大人|成人)")
_CHILD_RE = re.compile(r"(\d{1,2}|[一两二三四五六七八九十])\s*个?\s*(?:小孩|孩子|儿童)")
_BUDGET_RE = re.compile(r"预算(?:是|为|大概|约|在)?\s*(\d+(?:\.\d+)?)")

_WEATHER_WORDS = ("天气", "气温", "温度", "下雨", "带伞", "穿什么", "冷不冷", "热不热", "下雪")
_HOTEL_WORDS = ("酒店", "住宿", "住哪", "住哪里", "住什么地方", "宾馆", "民宿", "客栈")
_FLIGHT_WORDS = ("机票", "航班", "飞机", "机场")
_TICKET_WORDS = ("门票", "票价", "预约", "免票", "学生票", "开放时间")
_PLAN_WORDS = ("规划", "行程", "安排", "计划", "日程", "排个", "帮我排", "生成", "旅游", "旅行")
_GUIDE_WORDS = ("攻略", "推荐", "好玩", "景点", "介绍", "必去", "打卡", "值得", "美食")


def _to_int(token: str) -> Optional[int]:
    if token.isdigit():
        return int(token)
    return CHINESE_NUM.get(token)


def detect_city(message: str, known_cities: list[str]) -> Optional[str]:
    # 同时出现多个城市名时取最长的；等长时取先出现的，避免「上海南京路」被误判为南京。
    candidates = sorted(
        (name for name in set(known_cities) | set(STATIC_CITIES) if name and name in message),
        key=lambda name: (-len(name), message.find(name)),
    )
    return candidates[0] if candidates else None


def _explicit_dates(message: str, today: date) -> list[date]:
    found: list[date] = []
    for match in _EXPLICIT_DATE.finditer(message):
        year_text, month_text, day_text = match.groups()
        try:
            month, day = int(month_text), int(day_text)
            if year_text:
                year = int(year_text)
            else:
                year = today.year
                # 只写月日时，若已明显过去则理解为下一年。
                if date(year, month, day) < today - timedelta(days=1):
                    year += 1
            value = date(year, month, day)
        except ValueError:
            continue
        if 2000 <= value.year <= 2100 and value not in found:
            found.append(value)
    return found


def _relative_date(message: str, today: date) -> Optional[date]:
    for word, offset in _RELATIVE_DAYS.items():
        if word in message:
            return today + timedelta(days=offset)
    match = re.search(r"下(?:周|星期)([一二三四五六日天])", message)
    if match:
        target = _WEEKDAY_INDEX[match.group(1)]
        delta = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    if "周末" in message:
        delta = (5 - today.weekday()) % 7
        return today + timedelta(days=delta)
    return None


def extract_days(message: str) -> Optional[int]:
    for match in _DURATION_RE.finditer(message):
        value = _to_int(match.group(1))
        if value and 1 <= value <= 14:
            return value
    return None


def extract_party(message: str) -> tuple[int, int]:
    adults, children = 2, 0
    match = _ADULT_RE.search(message) or _PARTY_RE.search(message)
    if match:
        value = _to_int(match.group(1))
        if value:
            adults = min(value, 20)
    if "一家三口" in message:
        adults, children = 2, 1
    elif "一家四口" in message:
        adults, children = 2, 2
    child_match = _CHILD_RE.search(message)
    if child_match:
        value = _to_int(child_match.group(1))
        if value:
            children = min(value, 10)
    elif any(word in message for word in ("亲子", "带娃", "带孩子", "带小孩")):
        children = max(children, 1)
    return adults, children


def extract_budget(message: str) -> Optional[float]:
    match = _BUDGET_RE.search(message)
    if not match:
        return None
    return min(float(match.group(1)), 1_000_000)


def extract_preferences(message: str) -> list[str]:
    values: list[str] = []
    for keyword, category in _PREFERENCE_KEYWORDS.items():
        if keyword in message and category not in values:
            values.append(category)
    return values


def detect_intent(message: str, city: Optional[str], days: Optional[int]) -> tuple[str, float]:
    if any(word in message for word in _WEATHER_WORDS):
        return "weather_query", 0.95
    if any(word in message for word in _HOTEL_WORDS):
        return "hotel_search", 0.9
    if any(word in message for word in _FLIGHT_WORDS):
        return "flight_query", 0.85
    if any(word in message for word in _TICKET_WORDS):
        return "ticket_query", 0.85
    if any(word in message for word in _PLAN_WORDS):
        return "plan_trip", 0.9
    if any(word in message for word in _GUIDE_WORDS):
        return "guide_query", 0.75
    if city and days:
        return "plan_trip", 0.6
    return "general_chat", 0.3


@dataclass
class Intent:
    name: str = "general_chat"
    city: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    days: Optional[int] = None
    adults: int = 2
    children: int = 0
    budget: Optional[float] = None
    preferences: list[str] = field(default_factory=list)
    query: str = ""
    source: str = "rules"
    confidence: float = 0.3


def parse_rule_intent(
    message: str, known_cities: list[str], today: Optional[date] = None,
) -> Intent:
    today = today or date.today()
    text = (message or "").strip()
    city = detect_city(text, known_cities)
    days = extract_days(text)
    dates = _explicit_dates(text, today)
    if not dates:
        relative = _relative_date(text, today)
        if relative:
            dates = [relative]
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    if dates:
        start_date = dates[0]
        if len(dates) > 1 and dates[1] > dates[0]:
            end_date = dates[1]
        elif days:
            end_date = start_date + timedelta(days=days - 1)
    adults, children = extract_party(text)
    name, confidence = detect_intent(text, city, days)
    return Intent(
        name=name, city=city, start_date=start_date, end_date=end_date, days=days,
        adults=adults, children=children, budget=extract_budget(text),
        preferences=extract_preferences(text), query=text, confidence=confidence,
    )
