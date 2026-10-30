"""对话编排：意图路由到确定性工具；LLM 只补充意图与通用问答，事实由工具产出。"""
from __future__ import annotations

import copy
import logging
from datetime import date, timedelta
from typing import Optional

from app.agent.hotels import recommend_hotels
from app.agent.llm import DeepSeekClient, LLMError
from app.agent.nlu import INTENT_LABELS, INTENT_NAMES, Intent, parse_rule_intent
from app.agent.weather import OpenMeteoWeather
from app.core.models import Pace, TravelParty, TripSpec

logger = logging.getLogger("travelai.agent")

QUICK_REPLIES = [
    "帮我规划东京3天行程",
    "北京明天天气怎么样",
    "上海住哪里方便",
    "西安景点门票多少钱",
]

_INTENT_PROMPT = (
    "你是旅行助手的意图解析器。只输出一个 JSON 对象，不要输出 markdown 或解释。\n"
    "intent 取值：plan_trip（生成行程）、weather_query（天气）、hotel_search（住宿）、"
    "flight_query（机票/航班）、ticket_query（门票）、guide_query（景点攻略）、general_chat（通用问答）。\n"
    "字段：intent、city（中文城市名或 null）、start_date（YYYY-MM-DD 或 null）、"
    "end_date（YYYY-MM-DD 或 null）、days（1-14 或 null）、adults、children、"
    "budget（人民币数字或 null）、query（检索关键词）。\n"
    "今天日期：{today}\n可选城市：{cities}"
)

_GENERAL_PROMPT = (
    "你是 TravelAI 旅行助手，用简洁中文回答。你可以讨论行程规划、天气、住宿、门票与航班常识，"
    "但不得编造实时天气、航班号、酒店库存或价格；涉及实时数据时明确说明未接入，并建议用户查询官方渠道。"
    "回答控制在 180 字以内，可用短列表。"
)


class AgentChatService:
    def __init__(
        self, repository, planning, settings, *,
        llm: Optional[DeepSeekClient] = None, weather=None,
    ):
        self.repository = repository
        self.planning = planning
        self.settings = settings
        self.llm = llm if llm is not None else DeepSeekClient(
            getattr(settings, "deepseek_api_key", None),
            getattr(settings, "deepseek_base_url", "https://api.deepseek.com"),
            getattr(settings, "deepseek_model", "deepseek-chat"),
            getattr(settings, "llm_timeout_seconds", 25.0),
        )
        self.weather = weather if weather is not None else OpenMeteoWeather(
            repository,
            timeout_seconds=getattr(settings, "weather_timeout_seconds", 6.0),
            enabled=getattr(settings, "weather_enabled", True),
        )

    @property
    def llm_mode(self) -> str:
        return self.llm.mode

    @property
    def weather_mode(self) -> str:
        return "open_meteo" if getattr(self.weather, "enabled", True) else "disabled"

    def handle(self, user: dict, message: str) -> dict:
        message = (message or "").strip()
        history = self.repository.list_chat_messages(user["id"], limit=8)
        intent = self.understand(message, history)
        self.repository.add_chat_message(
            user["id"], "user", message, intent=intent.name,
            payload={"city": intent.city} if intent.city else {},
        )
        try:
            content, cards, extras = self.route(intent, user, history, message)
        except Exception:
            logger.exception("chat route failed intent=%s", intent.name)
            content = "处理这条消息时出现内部错误，请稍后重试，或直接使用左侧工作台生成行程。"
            cards, extras = [], {}
        payload = {
            "cards": cards, "quick_replies": QUICK_REPLIES,
            "city": intent.city, **extras,
        }
        assistant = self.repository.add_chat_message(
            user["id"], "assistant", content, intent=intent.name, payload=payload,
        )
        return {
            "message": assistant,
            "intent": intent.name,
            "intent_label": INTENT_LABELS.get(intent.name, intent.name),
            "trip_id": extras.get("trip_id"),
            "version": extras.get("version"),
            "llm_mode": self.llm_mode,
        }

    def understand(self, message: str, history: list[dict]) -> Intent:
        known = self.repository.list_cities()
        rule = parse_rule_intent(message, known)
        if rule.city is None:
            rule.city = self._last_city(history)
        data: dict = {}
        # 规则命中明确关键词时不再调用 LLM，避免每条消息都增加外部延迟。
        if self.llm.available and rule.confidence < 0.8:
            try:
                data = self.llm.chat_json([
                    {"role": "system", "content": _INTENT_PROMPT.format(
                        today=date.today().isoformat(),
                        cities="、".join(known[:40]) or "（暂无导入城市）",
                    )},
                    {"role": "user", "content": message},
                ])
            except LLMError as exc:
                logger.info("intent llm fallback reason=%s", exc)
        return self._merge_intent(rule, data, known)

    @staticmethod
    def _last_city(history: list[dict]) -> Optional[str]:
        for item in reversed(history):
            city = item.get("city")
            if city:
                return city
        return None

    @staticmethod
    def _merge_intent(rule: Intent, data: dict, known: list[str]) -> Intent:
        intent = copy.deepcopy(rule)
        if not data:
            return intent
        name = data.get("intent")
        if name in INTENT_NAMES and rule.name == "general_chat":
            intent.name = name
            intent.confidence = 0.8
            intent.source = "llm+rules"
        city = data.get("city")
        if not intent.city and isinstance(city, str) and 0 < len(city.strip()) <= 20:
            intent.city = city.strip()
        for field in ("start_date", "end_date"):
            if getattr(intent, field) is None and data.get(field):
                try:
                    setattr(intent, field, date.fromisoformat(str(data[field])))
                except ValueError:
                    continue
        if intent.days is None and isinstance(data.get("days"), int) and 1 <= data["days"] <= 14:
            intent.days = data["days"]
        if intent.budget is None and data.get("budget") not in (None, ""):
            try:
                budget = float(data["budget"])
                if 0 <= budget <= 1_000_000:
                    intent.budget = budget
            except (TypeError, ValueError):
                pass
        if isinstance(data.get("query"), str) and data["query"].strip():
            intent.query = data["query"].strip()
        return intent

    def route(
        self, intent: Intent, user: dict, history: list[dict], message: str,
    ) -> tuple[str, list[dict], dict]:
        handlers = {
            "plan_trip": lambda: self._handle_plan(intent, user),
            "weather_query": lambda: self._handle_weather(intent),
            "hotel_search": lambda: self._handle_hotel(intent),
            "flight_query": lambda: self._handle_flight(intent),
            "ticket_query": lambda: self._handle_ticket(intent),
            "guide_query": lambda: self._handle_guide(intent),
            "general_chat": lambda: self._handle_general(message, history),
        }
        return handlers.get(intent.name, handlers["general_chat"])()

    def _cities_hint(self) -> str:
        cities = self.repository.list_cities()
        return "、".join(cities[:15]) + ("等" if len(cities) > 15 else "")

    def _handle_plan(self, intent: Intent, user: dict) -> tuple[str, list[dict], dict]:
        known = self.repository.list_cities()
        if not intent.city:
            return (
                "告诉我目的地和大概天数就能生成行程，例如「帮我规划东京3天行程」。\n"
                f"当前可规划城市：{self._cities_hint()}。",
                [{"type": "clarify", "title": "缺少目的地", "cities": known[:20], "quick_replies": QUICK_REPLIES}],
                {},
            )
        if intent.city not in known:
            return (
                f"本地景点库暂未收录「{intent.city}」，无法生成可执行的逐日行程。\n"
                f"当前可规划城市：{self._cities_hint()}。",
                [{"type": "notice", "status": "unsupported_city",
                  "title": f"暂不支持 {intent.city}", "cities": known}],
                {},
            )
        today = date.today()
        start = intent.start_date or (today + timedelta(days=1))
        days = intent.days
        if days is None and intent.end_date and intent.end_date >= start:
            days = (intent.end_date - start).days + 1
        days = days or 3
        if days > 14:
            return (
                "单次最多支持 14 天行程，请缩短后重试。",
                [{"type": "notice", "status": "too_long", "title": "行程天数超出上限"}],
                {},
            )
        end = start + timedelta(days=days - 1)
        spec = TripSpec(
            destination=intent.city, start_date=start, end_date=end,
            party=TravelParty(adults=intent.adults, children=intent.children),
            pace=Pace.BALANCED, budget_cny=intent.budget,
            preferred_categories=intent.preferences,
        )
        result, run_id = self.planning.create_trip(spec, True, owner_id=user["id"])
        if result.status == "infeasible":
            conflicts = [
                {"code": item.code, "message": item.message, "suggestions": item.suggestions}
                for item in result.conflicts
            ]
            reason = conflicts[0]["message"] if conflicts else "约束冲突"
            suggestions = "；".join(conflicts[0]["suggestions"][:3]) if conflicts else ""
            content = (
                f"这次没能生成可行行程：{reason}。"
                + (f"\n可以尝试：{suggestions}。" if suggestions else "")
            )
            return (
                content,
                [{"type": "plan_conflict", "title": "行程不可行", "conflicts": conflicts}],
                {"run_id": run_id},
            )
        plan = result.plan
        card = self._plan_card(plan)
        highlights = "、".join(
            f"第{day['day_number']}天 " + "、".join(day["items"][:3])
            for day in card["days"][:3] if day["items"]
        )
        content = (
            f"已在{plan.spec.destination}生成 {len(plan.days)} 天行程（v{plan.version}），并保存到「最近行程」。\n"
            f"{plan.spec.start_date} 至 {plan.spec.end_date}，已排景点 "
            f"{sum(1 for d in plan.days for i in d.items if i.kind == 'spot')} 个，"
            f"门票与市内交通估算 ¥{plan.cost.total_low_cny:.0f}–¥{plan.cost.total_high_cny:.0f}。"
            + (f"\n{highlights}" if highlights else "")
            + "\n可点下方按钮打开行程；需要调整某一天时，在工作台里使用「修改单日」会保留版本记录。"
        )
        return (
            content,
            [card],
            {"trip_id": plan.trip_id, "version": plan.version},
        )

    @staticmethod
    def _plan_card(plan) -> dict:
        return {
            "type": "plan",
            "trip_id": plan.trip_id,
            "version": plan.version,
            "destination": plan.spec.destination,
            "start_date": plan.spec.start_date.isoformat(),
            "end_date": plan.spec.end_date.isoformat(),
            "cost_low_cny": plan.cost.total_low_cny,
            "cost_high_cny": plan.cost.total_high_cny,
            "days": [
                {
                    "day_number": day.day_number,
                    "date": day.date.isoformat(),
                    "items": [item.title for item in day.items if item.kind in {"spot", "anchor"}][:6],
                    "warnings": day.warnings[:2],
                }
                for day in plan.days
            ],
            "warnings": plan.warnings[:4],
        }

    def _handle_weather(self, intent: Intent) -> tuple[str, list[dict], dict]:
        city = intent.city
        if not city:
            return (
                "请告诉我想查哪座城市的天气，例如「上海明天天气怎么样」。",
                [{"type": "clarify", "title": "缺少城市", "quick_replies": QUICK_REPLIES[:3]}],
                {},
            )
        today = date.today()
        start = intent.start_date or today
        days = intent.days or (3 if not intent.end_date else None)
        if intent.end_date:
            end = intent.end_date
        else:
            end = start + timedelta(days=(days or 3) - 1)
        card = self.weather.fetch(city, start, end)
        if card["status"] == "ok":
            lines = [f"{city}天气预报："]
            for item in card["days"][:6]:
                temps = self._temp_text(item["temp_min"], item["temp_max"])
                precip = "" if item["precip_prob"] is None else f"，降水概率 {item['precip_prob']}%"
                advice = f"，{item['advice']}" if item["advice"] else ""
                lines.append(
                    f"- {item['date']} {item['weekday']}：{item['weather']}{temps}{precip}{advice}"
                )
            lines.append(card["notice"])
            content = "\n".join(lines)
        else:
            content = f"{city}天气暂无法查询：{card['notice']}"
        return content, [card], {}

    @staticmethod
    def _temp_text(low, high) -> str:
        try:
            return f"，{float(low):.0f}–{float(high):.0f}°C"
        except (TypeError, ValueError):
            return ""

    def _handle_hotel(self, intent: Intent) -> tuple[str, list[dict], dict]:
        city = intent.city
        if not city:
            return (
                "请告诉我想住哪座城市，例如「杭州住哪里方便」。",
                [{"type": "clarify", "title": "缺少城市", "quick_replies": QUICK_REPLIES[2:]}],
                {},
            )
        spots = self.repository.list_spots(city)
        nights = None
        if intent.days and intent.days > 1:
            nights = intent.days - 1
        elif intent.start_date and intent.end_date and intent.end_date > intent.start_date:
            nights = (intent.end_date - intent.start_date).days
        card = recommend_hotels(
            city, spots, adults=intent.adults, children=intent.children,
            nights=nights, budget=intent.budget,
        )
        lines = [f"{city}住宿参考（{card['tier_label']}，{card['rooms']} 间房，{card['nights']} 晚）："]
        for option in card["options"]:
            lines.append(
                f"- {option['tier']}：约 ¥{option['price_low_cny']}–¥{option['price_high_cny']}/晚，"
                f"建议区域 {option['area']}"
            )
        lines.append(card["notice"])
        return "\n".join(lines), [card], {}

    def _handle_flight(self, intent: Intent) -> tuple[str, list[dict], dict]:
        city = intent.city
        title = f"去{city}的航班" if city else "航班与机票"
        card = {
            "type": "flight", "status": "unavailable", "title": title,
            "message": "本系统未接入实时航班、票价与库存接口，因此不提供航班号、起降时间或价格。",
            "tips": [
                "在航空公司官网或主流票务平台查询实时航班与价格",
                "廉价票改签限制较多，下单前确认退改政策",
                "把到达时间告诉我，我可以在行程里为到达日留出交通与休息时间",
            ],
        }
        content = (
            "我没有实时航班和票价数据，无法给出具体航班号或价格，也不会编造。\n"
            "建议在航空公司官网或票务平台查询，并确认退改签规则。\n"
            "如果你告诉我出发城市、到达时间和预算，我可以帮你把行程的到达日安排得更合理。"
        )
        return content, [card], {}

    def _handle_ticket(self, intent: Intent) -> tuple[str, list[dict], dict]:
        city = intent.city
        if not city:
            return (
                "请告诉我想查哪座城市的景点门票，例如「西安景点门票多少钱」。",
                [{"type": "clarify", "title": "缺少城市", "quick_replies": QUICK_REPLIES[3:]}],
                {},
            )
        spots = self.repository.list_spots(city)
        if not spots:
            return (
                f"本地景点库暂未收录「{city}」的门票数据。当前可查询城市：{self._cities_hint()}。",
                [{"type": "notice", "status": "unsupported_city", "title": f"暂不支持 {city}"}],
                {},
            )
        priced = sorted(
            (spot for spot in spots if spot.adult_ticket_cny > 0),
            key=lambda spot: spot.rating, reverse=True,
        )[:8]
        free_count = sum(1 for spot in spots if spot.adult_ticket_cny <= 0)
        items = [
            {
                "name": spot.name, "price_cny": spot.adult_ticket_cny,
                "child_cny": spot.child_ticket_cny, "senior_cny": spot.senior_ticket_cny,
                "opening": f"{spot.opening.open_time.strftime('%H:%M')}–"
                           f"{spot.opening.close_time.strftime('%H:%M')}",
            }
            for spot in priced
        ]
        card = {
            "type": "tickets", "status": "demo", "city": city, "items": items,
            "free_count": free_count,
            "notice": "票价为本地演示数据，非实时价格；预约规则、免票与闭馆安排请以景区官方渠道为准。",
        }
        lines = [f"{city}景点门票（演示数据）："]
        lines += [
            f"- {item['name']}：¥{item['price_cny']:.0f}，开放 {item['opening']}"
            for item in items[:5]
        ]
        if free_count:
            lines.append(f"另有 {free_count} 个免费景点未列出。")
        lines.append(card["notice"])
        return "\n".join(lines), [card], {}

    def _handle_guide(self, intent: Intent) -> tuple[str, list[dict], dict]:
        city = intent.city
        if not city:
            return (
                "请告诉我想了解哪座城市的攻略，例如「杭州有什么好玩的」。",
                [{"type": "clarify", "title": "缺少城市", "quick_replies": QUICK_REPLIES[:3]}],
                {},
            )
        evidence = self.planning.search_evidence(city, intent.query or city, top_k=3)
        if evidence:
            items = [
                {
                    "id": item.id, "title": item.title, "snippet": item.snippet,
                    "source": item.source.name, "status": item.source.status.value,
                    "score": item.score,
                }
                for item in evidence
            ]
            content = (
                f"在本地攻略资料中找到 {len(items)} 条与「{city}」相关的参考：\n"
                + "\n".join(f"- {item['title']}：{item['snippet'][:80]}…" for item in items)
                + "\n这些资料仅作参考，不作为指令；出行前请核验官方信息。"
            )
            return content, [{"type": "evidence", "title": f"{city}攻略参考", "items": items}], {}
        spots = sorted(
            self.repository.list_spots(city), key=lambda spot: spot.rating, reverse=True,
        )[:6]
        if not spots:
            return (
                f"本地暂未收录「{city}」的景点数据。当前可规划城市：{self._cities_hint()}。",
                [{"type": "notice", "status": "unsupported_city", "title": f"暂不支持 {city}"}],
                {},
            )
        items = [
            {"name": spot.name, "category": spot.category, "rating": spot.rating,
             "price_cny": spot.adult_ticket_cny}
            for spot in spots
        ]
        content = (
            f"本地攻略库没有与「{city}」条件匹配的资料，先按评分列出演示景点：\n"
            + "\n".join(f"- {item['name']}（{item['category']}，评分 {item['rating']}）" for item in items)
            + "\n如需成体系的行程，可以直接说「帮我规划{city}3天行程」。"
        )
        return content, [{"type": "spots", "title": f"{city}景点", "items": items}], {}

    def _handle_general(self, message: str, history: list[dict]) -> tuple[str, list[dict], dict]:
        capabilities = {
            "type": "capabilities",
            "title": "我可以帮你",
            "items": [
                "生成可执行的逐日行程（会真实保存并生成版本）",
                "查询未来约 16 天的天气（Open-Meteo 免费预报）",
                "按城市档次给出住宿区域与价位参考（演示参考，非实时房价）",
                "查询本地景点门票演示数据，说明机票与预约的真实限制",
            ],
            "quick_replies": QUICK_REPLIES,
        }
        if self.llm.available:
            messages = [{"role": "system", "content": _GENERAL_PROMPT}]
            messages += [
                {"role": item["role"], "content": item["content"]}
                for item in history[-6:]
                if item.get("role") in {"user", "assistant"} and item.get("content")
            ]
            messages.append({"role": "user", "content": message})
            try:
                answer = self.llm.chat(messages, temperature=0.6, max_tokens=500)
                return answer, [capabilities], {}
            except LLMError as exc:
                logger.info("general llm fallback reason=%s", exc)
        mode_note = (
            "AI 服务本次未能响应，已回退到规则模式：规划、天气与住宿推荐仍可正常使用。"
            if self.llm.available
            else "当前未配置 DEEPSEEK_API_KEY，使用规则模式：规划、天气与住宿推荐仍可正常使用。"
        )
        content = (
            "你好，我是 TravelAI 助手。我可以生成行程、查询天气预报、给出住宿区域参考、"
            "查询演示门票，并诚实说明机票等实时数据的限制。\n"
            f"{mode_note}\n"
            "试试：请帮我规划东京3天行程 / 北京明天天气怎么样。"
        )
        return content, [capabilities], {}
