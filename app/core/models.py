"""TravelAI 领域模型。

原则：结构化行程是主数据；所有事实、估算和未知状态显式区分。
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class Pace(str, Enum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    THIRD_PARTY = "third_party"
    ESTIMATED = "estimated"
    UNVERIFIED = "unverified"


class SourceInfo(BaseModel):
    name: str
    url: Optional[str] = None
    status: VerificationStatus = VerificationStatus.UNVERIFIED
    last_verified_at: Optional[date] = None
    notes: Optional[str] = None


class OpeningHours(BaseModel):
    open_time: time = time(9, 0)
    close_time: time = time(17, 0)
    closed_weekdays: list[int] = Field(default_factory=list)  # Monday=0
    closed_dates: list[date] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self):
        if self.close_time <= self.open_time:
            raise ValueError("MVP 仅支持同日开放的营业窗口，close_time 必须晚于 open_time")
        if any(day < 0 or day > 6 for day in self.closed_weekdays):
            raise ValueError("closed_weekdays 必须为 0..6")
        return self


class Guide(BaseModel):
    """本地或已授权攻略；没有可靠来源时必须标为未核验。"""
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    content: str = Field(min_length=1, max_length=10000)
    source: SourceInfo


class Spot(BaseModel):
    id: str
    name: str
    city: str
    category: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: Optional[str] = None
    rating: float = Field(default=0, ge=0, le=5)
    visit_duration_minutes: int = Field(default=120, gt=0, le=720)
    adult_ticket_cny: float = Field(default=0, ge=0)
    child_ticket_cny: Optional[float] = Field(default=None, ge=0)
    senior_ticket_cny: Optional[float] = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)
    opening: OpeningHours = Field(default_factory=OpeningHours)
    source: SourceInfo


class TravelParty(BaseModel):
    adults: int = Field(default=2, ge=0)
    children: int = Field(default=0, ge=0)
    seniors: int = Field(default=0, ge=0)
    mobility_notes: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.adults + self.children + self.seniors

    @model_validator(mode="after")
    def validate_party(self):
        if self.total < 1:
            raise ValueError("同行人总数至少为 1")
        return self


class TransportSettings(BaseModel):
    mode: Literal["walk", "drive", "transit"] = "drive"
    average_speed_kmh: float = Field(default=24, gt=0, le=130)
    buffer_minutes: int = Field(default=15, ge=0, le=120)
    cost_per_km_cny: float = Field(default=0, ge=0)


class Anchor(BaseModel):
    """不可移动的预约或交通锚点。"""

    id: str = Field(default_factory=lambda: f"anchor-{uuid4().hex[:10]}")
    title: str
    date: date
    start_time: time
    end_time: time
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_time <= self.start_time:
            raise ValueError("锚点 end_time 必须晚于 start_time；跨日锚点请拆分")
        return self


class TripSpec(BaseModel):
    destination: str
    start_date: date
    end_date: date
    party: TravelParty = Field(default_factory=TravelParty)
    pace: Pace = Pace.BALANCED
    max_spots_per_day: Optional[int] = Field(default=None, ge=1, le=6)
    daily_start_time: time = time(9, 0)
    daily_end_time: time = time(20, 0)
    budget_cny: Optional[float] = Field(default=None, ge=0)
    budget_scope: str = "景点门票与市内交通估算，不含机酒餐饮"
    preferred_categories: list[str] = Field(default_factory=list)
    avoided_categories: list[str] = Field(default_factory=list)
    selected_spot_ids: list[str] = Field(default_factory=list)
    must_include_spot_ids: list[str] = Field(default_factory=list)
    locked_days: list[int] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    include_lunch: bool = True
    lunch_start: time = time(12, 0)
    lunch_end: time = time(13, 0)
    transport: TransportSettings = Field(default_factory=TransportSettings)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_spec(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        if self.duration_days > 14:
            raise ValueError("MVP 单次最多规划 14 天")
        if self.daily_end_time <= self.daily_start_time:
            raise ValueError("daily_end_time 必须晚于 daily_start_time")
        if self.include_lunch and self.lunch_end <= self.lunch_start:
            raise ValueError("午餐结束时间必须晚于开始时间")
        if len(set(self.must_include_spot_ids)) != len(self.must_include_spot_ids):
            raise ValueError("must_include_spot_ids 不能重复")
        if any(day < 1 or day > self.duration_days for day in self.locked_days):
            raise ValueError("locked_days 超出行程日期范围")
        for anchor in self.anchors:
            if not self.start_date <= anchor.date <= self.end_date:
                raise ValueError(f"锚点 {anchor.title} 的日期不在行程范围内")
        # 必去景点与排除类别的冲突需要景点事实，在规划器中检查。
        return self

    @property
    def duration_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def spots_per_day(self) -> int:
        if self.max_spots_per_day is not None:
            return self.max_spots_per_day
        return {Pace.RELAXED: 2, Pace.BALANCED: 3, Pace.PACKED: 4}[self.pace]


class TransportLeg(BaseModel):
    from_id: str
    to_id: str
    distance_km: float
    travel_minutes: int
    mode: str
    basis: str = "haversine_estimate"
    provider: str = "local_estimate"
    is_online: bool = False


class TransportGap(BaseModel):
    """无法计算的路段，显式记录而不是静默丢弃。"""
    from_item_id: Optional[str] = None
    to_item_id: str
    reason: str


class ScheduleItem(BaseModel):
    id: str
    kind: Literal["spot", "anchor", "meal"]
    title: str
    date: date
    start_time: time
    end_time: time
    spot_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    ticket_cost_cny: float = 0
    duration_minutes: int = 0
    transport_from_previous: Optional[TransportLeg] = None
    source: Optional[SourceInfo] = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_duration(self):
        """接口层唯一时长字段，由起止时间派生，历史版本读取时也会补齐。"""
        expected = (
            self.end_time.hour * 60 + self.end_time.minute
            - (self.start_time.hour * 60 + self.start_time.minute)
        )
        if self.duration_minutes <= 0 and expected > 0:
            self.duration_minutes = expected
        return self


class DayPlan(BaseModel):
    day_number: int
    date: date
    items: list[ScheduleItem] = Field(default_factory=list)
    total_travel_km: float = 0
    total_travel_minutes: int = 0
    visit_minutes: int = 0
    ticket_cost_cny: float = 0
    transport_complete: bool = True
    uncomputed_legs: list[TransportGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _infer_transport_completeness(self):
        """历史版本未记录通勤标记时，按缺失路段反推，避免用 0.0 冒充已计算。"""
        if self.uncomputed_legs:
            self.transport_complete = False
            return self
        missing = [
            (self.items[index - 1].id, item)
            for index, item in enumerate(self.items)
            if index > 0 and item.transport_from_previous is None
        ]
        if missing:
            self.transport_complete = False
            self.uncomputed_legs = [
                TransportGap(
                    from_item_id=from_id,
                    to_item_id=to_item.id,
                    reason="该段通勤未记录，未计入总计",
                )
                for from_id, to_item in missing
            ]
        return self


class CostBreakdown(BaseModel):
    tickets_cny: float = 0
    transport_estimate_cny: float = 0
    total_low_cny: float = 0
    total_high_cny: float = 0
    excluded: list[str] = Field(default_factory=lambda: ["往返大交通", "住宿", "餐饮", "购物"])
    notes: list[str] = Field(default_factory=list)


class GuideEvidence(BaseModel):
    id: str
    title: str
    city: str
    snippet: str
    source: SourceInfo
    retrieval_method: str = "local_lexical"
    score: float = 0


class ItineraryPlan(BaseModel):
    trip_id: Optional[str] = None
    version: int = 1
    spec: TripSpec
    days: list[DayPlan]
    cost: CostBreakdown
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    unscheduled_spot_ids: list[str] = Field(default_factory=list)
    evidence: list[GuideEvidence] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PlanningConflict(BaseModel):
    code: str
    message: str
    date: Optional[date] = None
    spot_id: Optional[str] = None
    suggestions: list[str] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class PlanningResult(BaseModel):
    status: Literal["success", "infeasible"]
    plan: Optional[ItineraryPlan] = None
    conflicts: list[PlanningConflict] = Field(default_factory=list)


class ItemChange(BaseModel):
    day_number: int
    spot_id: str
    title: str
    before: Optional[str] = None
    after: Optional[str] = None


class PlanDiff(BaseModel):
    from_version: int
    to_version: int
    added: list[ItemChange] = Field(default_factory=list)
    removed: list[ItemChange] = Field(default_factory=list)
    changed: list[ItemChange] = Field(default_factory=list)
    unchanged_days: list[int] = Field(default_factory=list)


class AgentRun(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "running", "succeeded", "failed"]
    trip_id: Optional[str] = None
    version: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
