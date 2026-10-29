"""软件测试用演示数据，不是实时旅行指南。

所有票价、营业窗口均为演示约束数据，未声称在任一真实日期有效。
"""
from app.core.models import OpeningHours, SourceInfo, Spot, VerificationStatus

DEMO_SOURCE = SourceInfo(
    name="TravelAI 演示种子数据（非实时旅行信息）",
    status=VerificationStatus.UNVERIFIED,
    notes="仅用于软件功能测试；实际出行前必须通过官方渠道重新核验",
)


def spot(id, name, category, lat, lng, duration, price, tags, opening=None, rating=4.5):
    return Spot(
        id=id,
        name=name,
        city="东京",
        category=category,
        latitude=lat,
        longitude=lng,
        address=f"东京演示地址：{name}",
        rating=rating,
        visit_duration_minutes=duration,
        adult_ticket_cny=price,
        child_ticket_cny=price,
        senior_ticket_cny=price,
        tags=tags,
        opening=opening or OpeningHours(),
        source=DEMO_SOURCE,
    )


DEMO_SPOTS = [
    spot("tokyo-sensoji", "浅草寺", "文化", 35.7148, 139.7967, 90, 0,
         ["文化", "历史", "亲子"], rating=4.6),
    spot("tokyo-national-museum", "东京国立博物馆", "博物馆", 35.7188, 139.7765, 180, 50,
         ["文化", "亲子", "雨天备选"], OpeningHours(closed_weekdays=[0]), 4.7),
    spot("tokyo-ueno-park", "上野公园", "公园", 35.7156, 139.7745, 90, 0,
         ["亲子", "自然", "轻松"], OpeningHours(open_time="05:00", close_time="23:00"), 4.4),
    spot("tokyo-meiji-jingu", "明治神宫", "文化", 35.6764, 139.6993, 90, 0,
         ["文化", "自然", "轻松"], OpeningHours(open_time="09:00", close_time="16:00"), 4.6),
    spot("tokyo-shibuya-sky", "涩谷天空观景台", "观景", 35.6584, 139.7022, 90, 150,
         ["摄影", "夜景"], OpeningHours(open_time="10:00", close_time="22:00"), 4.5),
    spot("tokyo-shinjuku-gyoen", "新宿御苑", "公园", 35.6852, 139.7100, 120, 25,
         ["自然", "亲子", "轻松"], OpeningHours(closed_weekdays=[0]), 4.6),
    spot("tokyo-hamarikyu", "滨离宫恩赐庭园", "公园", 35.6598, 139.7637, 90, 15,
         ["自然", "摄影", "轻松"], rating=4.4),
    spot("tokyo-odaiba", "台场海滨公园", "公园", 35.6275, 139.7717, 120, 0,
         ["亲子", "摄影", "轻松"], OpeningHours(open_time="06:00", close_time="22:00"), 4.3),
]

DEMO_GUIDES = [
    {
        "id": "guide-tokyo-family",
        "title": "东京亲子行程的演示建议",
        "city": "东京",
        "tags": ["亲子", "轻松", "雨天备选"],
        "content": "演示资料：亲子行程可为每天保留自由活动与午休时间，并准备室内备选。本文仅用于检索功能测试，不构成实时旅行建议。",
        "source": DEMO_SOURCE.model_dump(),
    },
    {
        "id": "guide-tokyo-culture",
        "title": "东京文化片区的演示游览思路",
        "city": "东京",
        "tags": ["文化", "历史", "博物馆"],
        "content": "演示资料：文化与博物馆候选可按地理区域组织，闭馆日与预约要求应在出行前通过官方渠道核验。",
        "source": DEMO_SOURCE.model_dump(),
    },
    {
        "id": "guide-tokyo-photo",
        "title": "东京摄影与夜景的演示提示",
        "city": "东京",
        "tags": ["摄影", "夜景", "天气"],
        "content": "演示资料：观景台安排应结合天气、预约时段和返程交通；本演示系统没有接入实时天气或实时预约。",
        "source": DEMO_SOURCE.model_dump(),
    },
    {
        "id": "guide-osaka-other",
        "title": "大阪资料（用于验证城市硬过滤）",
        "city": "大阪",
        "tags": ["亲子", "文化"],
        "content": "这条大阪资料不应出现在东京检索结果中。",
        "source": DEMO_SOURCE.model_dump(),
    },
]
