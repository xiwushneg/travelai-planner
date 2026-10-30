"""本地规则住宿建议：只提供档位与区域参考，不编造具体酒店、实时房价或库存。"""
from __future__ import annotations

import math
from typing import Optional

_TIER_FACTORS = {
    "北京": (1.45, "一线城市"), "上海": (1.45, "一线城市"),
    "广州": (1.25, "一线城市"), "深圳": (1.35, "一线城市"),
    "杭州": (1.2, "新一线城市"), "成都": (1.05, "新一线城市"),
    "重庆": (1.0, "新一线城市"), "西安": (0.95, "新一线城市"),
    "南京": (1.1, "新一线城市"), "武汉": (0.95, "新一线城市"),
    "苏州": (1.1, "新一线城市"), "天津": (0.95, "新一线城市"),
    "长沙": (0.9, "新一线城市"), "青岛": (1.0, "新一线城市"),
    "厦门": (1.1, "热门旅游城市"), "三亚": (1.35, "热门旅游城市"),
    "昆明": (0.9, "旅游城市"), "大理": (0.85, "旅游城市"), "丽江": (0.85, "旅游城市"),
    "桂林": (0.8, "旅游城市"), "东京": (1.6, "境外热门城市"),
    "大阪": (1.35, "境外热门城市"), "京都": (1.45, "境外热门城市"),
}

_TIERS = [
    ("经济型", 200, "连锁快捷或青旅，适合控制预算、白天在外游玩的行程"),
    ("舒适型", 420, "品质连锁或精品民宿，适合家庭与中长途旅行"),
    ("品质型", 780, "高端酒店或景观房，适合重视住宿体验的行程"),
]


def recommend_hotels(
    city: str, spots: list, *, adults: int = 2, children: int = 0,
    nights: Optional[int] = None, budget: Optional[float] = None,
) -> dict:
    factor, tier_label = _TIER_FACTORS.get(city, (0.85, "其他城市"))
    party = max(1, adults + children)
    rooms = max(1, math.ceil(party / 2))
    nights = max(1, nights or 2)
    nearby = sorted(spots, key=lambda spot: spot.rating, reverse=True)[:3]
    areas = [f"{spot.name}一带" for spot in nearby]
    options = []
    for name, base, suitable in _TIERS:
        price = base * factor * rooms
        low = int(round(price * 0.85 / 10) * 10)
        high = int(round(price * 1.15 / 10) * 10)
        options.append({
            "tier": name,
            "price_low_cny": low,
            "price_high_cny": high,
            "rooms": rooms,
            "area": areas[0] if areas else f"{city}市中心或地铁沿线",
            "suitable": suitable,
        })
    return {
        "type": "hotels", "status": "demo", "city": city,
        "tier_label": tier_label, "nights": nights, "party": party, "rooms": rooms,
        "areas": areas,
        "options": options,
        "budget_cny": budget,
        "notice": (
            "以上为按城市档次与房型生成的演示参考，不是实时房价或库存，也不代表任何具体酒店。"
            "请在预订平台或酒店官方渠道核实价格、位置与退改政策。"
        ),
    }
