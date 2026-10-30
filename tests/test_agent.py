from datetime import date

from fastapi.testclient import TestClient

from app.agent.llm import DeepSeekClient, LLMError
from app.agent.nlu import parse_rule_intent
from app.main import create_app


class FakeWeather:
    """离线天气适配器：测试不依赖外网。"""

    provider = "fake_weather"

    def fetch(self, city, start_date, end_date):
        return {
            "type": "weather", "status": "ok", "city": city,
            "provider": self.provider, "is_realtime": True,
            "requested": {
                "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
            },
            "days": [{
                "date": start_date.isoformat(), "weekday": "周一", "weather": "晴",
                "temp_max": 24, "temp_min": 15, "precip_prob": 10, "advice": "",
            }],
            "notice": "测试天气数据，不访问网络。",
        }


def client(tmp_path, username="agent-tester"):
    api = TestClient(create_app(str(tmp_path / "agent.db")))
    registered = api.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "agent-tester-pass"},
    )
    assert registered.status_code == 201, registered.text
    # 测试固定走规则模式，避免外部 DEEPSEEK_API_KEY 影响结果。
    api.app.state.agent_chat.llm = DeepSeekClient(None)
    return api


def chat(api, message):
    response = api.post("/api/v1/agent/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()


def test_agent_endpoints_require_login(tmp_path):
    with TestClient(create_app(str(tmp_path / "anon.db"))) as api:
        assert api.post("/api/v1/agent/chat", json={"message": "你好"}).status_code == 401
        assert api.get("/api/v1/agent/history").status_code == 401
        assert api.delete("/api/v1/agent/history").status_code == 401


def test_chat_weather_card_and_history(tmp_path):
    with client(tmp_path) as api:
        api.app.state.agent_chat.weather = FakeWeather()
        body = chat(api, "东京明天天气怎么样")
        assert body["intent"] == "weather_query"
        assert body["message"]["cards"][0]["type"] == "weather"
        assert body["message"]["cards"][0]["status"] == "ok"
        assert "晴" in body["message"]["content"]

        history = api.get("/api/v1/agent/history").json()
        assert history["total"] == 2
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"]
        assert history["messages"][1]["cards"][0]["type"] == "weather"
        assert history["messages"][1]["intent"] == "weather_query"


def test_chat_hotel_flight_and_ticket_are_honest(tmp_path):
    with client(tmp_path) as api:
        hotel = chat(api, "东京住哪里方便")
        assert hotel["intent"] == "hotel_search"
        card = hotel["message"]["cards"][0]
        assert card["type"] == "hotels"
        assert len(card["options"]) == 3
        assert "不是实时房价" in card["notice"]

        # 给出日期区间但未说天数时，住宿晚数按区间计算。
        stay = chat(api, "上海4月1日到4月4日住哪里方便")
        assert stay["message"]["cards"][0]["nights"] == 3

        flight = chat(api, "去东京机票怎么订")
        assert flight["intent"] == "flight_query"
        flight_card = flight["message"]["cards"][0]
        assert flight_card["type"] == "flight"
        assert flight_card["status"] == "unavailable"
        assert "未接入" in flight_card["message"]

        ticket = chat(api, "东京景点门票多少钱")
        assert ticket["intent"] == "ticket_query"
        ticket_card = ticket["message"]["cards"][0]
        assert ticket_card["type"] == "tickets"
        assert ticket_card["items"]
        assert "非实时价格" in ticket_card["notice"]


def test_chat_plan_creates_openable_trip(tmp_path):
    with client(tmp_path, "planner-user") as api:
        body = chat(api, "帮我规划东京3天行程，2027-04-01出发")
        assert body["intent"] == "plan_trip"
        assert body["trip_id"]
        assert body["version"] == 1
        card = body["message"]["cards"][0]
        assert card["type"] == "plan"
        assert card["destination"] == "东京"
        assert len(card["days"]) == 3

        trip = api.get(f"/api/v1/trips/{body['trip_id']}")
        assert trip.status_code == 200
        assert len(trip.json()["days"]) == 3


def test_chat_plan_rejects_unavailable_city(tmp_path):
    with client(tmp_path) as api:
        body = chat(api, "帮我规划巴黎3天行程")
        assert body["intent"] == "plan_trip"
        card = body["message"]["cards"][0]
        assert card["type"] == "notice"
        assert card["status"] == "unsupported_city"
        assert "东京" in body["message"]["content"]


def test_chat_general_falls_back_to_rules(tmp_path):
    with client(tmp_path) as api:
        body = chat(api, "你好，你能做什么")
        assert body["intent"] == "general_chat"
        assert "规则模式" in body["message"]["content"]
        assert body["llm_mode"] == "rules"


def test_chat_history_isolation_and_clear(tmp_path):
    with client(tmp_path, "alice") as alice:
        bob = TestClient(alice.app)
        registered = bob.post(
            "/api/v1/auth/register",
            json={"username": "bob", "password": "bob-agent-pass"},
        )
        assert registered.status_code == 201, registered.text

        chat(alice, "东京明天天气怎么样")
        assert alice.get("/api/v1/agent/history").json()["total"] == 2
        assert bob.get("/api/v1/agent/history").json()["total"] == 0

        chat(bob, "你好")
        cleared = alice.delete("/api/v1/agent/history").json()
        assert cleared["deleted"] == 2
        assert alice.get("/api/v1/agent/history").json()["total"] == 0
        assert bob.get("/api/v1/agent/history").json()["total"] == 2


def test_chat_rejects_blank_message(tmp_path):
    with client(tmp_path) as api:
        response = api.post("/api/v1/agent/chat", json={"message": "   "})
        assert response.status_code == 422


def test_chat_general_reports_llm_outage(tmp_path):
    class BrokenLLM:
        """模拟已配置 Key 但外部服务不可用。"""

        available = True
        mode = "deepseek"

        def chat(self, *args, **kwargs):
            raise LLMError("DeepSeek 服务暂时不可用")

        def chat_json(self, *args, **kwargs):
            raise LLMError("DeepSeek 服务暂时不可用")

    with client(tmp_path) as api:
        api.app.state.agent_chat.llm = BrokenLLM()
        body = chat(api, "你好，你能做什么")
        assert "AI 服务本次未能响应" in body["message"]["content"]
        assert body["llm_mode"] == "deepseek"


def test_rule_intent_parses_dates_party_and_budget():
    intent = parse_rule_intent(
        "帮我规划北京3天行程，2027年4月1日出发，两个人，预算3000元",
        ["北京", "东京"],
        today=date(2026, 10, 30),
    )
    assert intent.name == "plan_trip"
    assert intent.city == "北京"
    assert intent.days == 3
    assert intent.start_date == date(2027, 4, 1)
    assert intent.end_date == date(2027, 4, 3)
    assert intent.adults == 2
    assert intent.budget == 3000
    assert intent.confidence >= 0.8

    weather = parse_rule_intent("上海明天天气", ["上海"], today=date(2026, 10, 30))
    assert weather.name == "weather_query"
    assert weather.city == "上海"
    assert weather.start_date == date(2026, 10, 31)

    # 「N个大人」与「一家三口」不冲突，人数解析要准确。
    party = parse_rule_intent("三个大人带一个孩子去上海玩4天", ["上海"], today=date(2026, 10, 30))
    assert party.adults == 3
    assert party.children == 1
    assert party.days == 4

    # 等长城市名同时出现时按先出现的判断，「上海南京路」不应识别成南京。
    road = parse_rule_intent("上海南京路有什么好玩的", ["上海", "南京"], today=date(2026, 10, 30))
    assert road.city == "上海"
