"""TravelAI HTTP API。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from app.auth import (
    DUMMY_PASSWORD_HASH,
    PasswordPolicyError,
    hash_password,
    hash_session_token,
    new_session_token,
    normalize_username,
    verify_password,
)
from app.config import settings
from app.audit import record_audit
from app.core.models import PlanDiff, PlanningResult, TripSpec
from app.rag.base import NullRetriever
from app.repository import UsernameTakenError

router = APIRouter()
logger = logging.getLogger("travelai.auth")

SESSION_COOKIE = "travelai_session"


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("username", mode="before")
    @classmethod
    def strip_username(cls, value):
        """先去除首尾空白再校验长度，避免 "   " 通过 min_length 后落成空用户名。"""
        return value.strip() if isinstance(value, str) else value


class TripCreateRequest(BaseModel):
    spec: TripSpec
    include_evidence: bool = True


class ModifyDayRequest(BaseModel):
    expected_version: int = Field(ge=1)
    day_number: int = Field(ge=1)
    desired_spot_ids: list[str]
    lock_other_days: bool = True
    include_evidence: bool = True


class MutationResponse(BaseModel):
    run_id: str
    result: PlanningResult
    trip_id: Optional[str] = None
    version: Optional[int] = None
    diff: Optional[PlanDiff] = None


class EvidenceSearchRequest(BaseModel):
    city: str
    query: str
    top_k: int = Field(default=3, ge=1, le=10)


def service(request: Request):
    return request.app.state.planning_service


def unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHENTICATED", "message": "请先登录后再访问。"},
        headers={"WWW-Authenticate": "Cookie"},
    )


def client_ip(request: Request) -> str:
    """仅当显式信任反向代理时才采信 X-Forwarded-For，避免伪造来源绕过限流。"""
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def invalid_credentials() -> HTTPException:
    """登录失败统一出口：账号不存在、密码错误、退避中都返回同一响应。"""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码不正确。"},
    )


def audit_trip_denial(
    request: Request, user: dict, *, trip_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """行程/运行存在但归属他人时记录越权尝试；不存在则不记录。"""
    repository = service(request).repository
    owner = repository.get_run_owner(run_id) if run_id else repository.get_trip_owner(trip_id)
    if owner and owner != user["id"]:
        record_audit(
            repository, "trip_access_denied",
            username=user["username"], ip=client_ip(request), result="denied",
            detail={
                "target": run_id or trip_id,
                "path": request.url.path,
                "method": request.method,
            },
        )


def rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "RATE_LIMITED", "message": f"尝试过于频繁，请在 {retry_after} 秒后重试。"},
        headers={"Retry-After": str(retry_after)},
    )


def current_user(
    request: Request, travelai_session: Optional[str] = Cookie(default=None)
) -> dict:
    """解析 httpOnly Cookie 中的会话 token；无有效会话一律 401。"""
    if not travelai_session:
        raise unauthenticated()
    user = service(request).repository.get_user_by_session(
        hash_session_token(travelai_session)
    )
    if not user:
        raise unauthenticated()
    return user


def start_session(repository, user_id: str, response: Response) -> dict:
    raw_token, token_hash = new_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    repository.delete_expired_sessions()
    repository.create_session(user_id, token_hash, expires_at.isoformat())
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return {"expires_at": expires_at.isoformat()}


def infeasible(run_id: str, result: PlanningResult):
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "PLANNING_INFEASIBLE",
            "run_id": run_id,
            "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
        },
    )


@router.get("/health")
def health(request: Request):
    planning = service(request)
    retriever = planning.retriever
    return {
        "status": "ok",
        "planner": "deterministic",
        "evidence_mode": "disabled" if isinstance(retriever, NullRetriever) else "local_lexical",
        "map_provider": planning.settings.map_provider,
        "map_mode": planning.map_mode,
        "realtime_weather": False,
        "realtime_traffic": False,
    }


@router.post("/api/v1/auth/register", status_code=201)
def register(payload: AuthRequest, request: Request, response: Response):
    repository = service(request).repository
    ip = client_ip(request)
    allowed, retry_after = repository.register_attempt(
        ip, settings.register_max_attempts, settings.register_window_seconds
    )
    if not allowed:
        raise rate_limited(retry_after)
    try:
        password_hash = hash_password(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "WEAK_PASSWORD", "message": str(exc)},
        )
    try:
        user = repository.create_user(normalize_username(payload.username), password_hash)
    except UsernameTakenError:
        # 中性提示：不回显用户名、不声明“已占用”；真实原因只进日志。
        logger.info("register rejected reason=username_taken ip=%s", ip)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REGISTRATION_UNAVAILABLE",
                "message": "暂时无法完成注册，请更换用户名或直接登录。",
            },
        )
    return {
        "user": {"id": user["id"], "username": user["username"]},
        **start_session(repository, user["id"], response),
    }


@router.post("/api/v1/auth/login")
def login(payload: AuthRequest, request: Request, response: Response):
    repository = service(request).repository
    guard = request.app.state.login_guard
    ip = client_ip(request)
    account = normalize_username(payload.username).lower()

    # IP 与账号两个维度独立计数；退避期间仍然执行一次 bcrypt，保持耗时一致。
    blocked = guard.check(ip, account)
    user = repository.get_user_by_username(account)
    usable = bool(user) and bool(user["is_active"])
    password_hash = user["password_hash"] if usable else DUMMY_PASSWORD_HASH
    matched = verify_password(payload.password, password_hash)

    if blocked:
        reason, remaining = blocked
        logger.warning(
            "login blocked reason=%s remaining=%ss ip=%s account=%s",
            reason, remaining, ip, account,
        )
        record_audit(
            repository, "login_blocked", username=account, ip=ip, result="blocked",
            detail={"reason": reason, "retry_after_seconds": remaining},
        )
        raise invalid_credentials()
    if not usable or not matched:
        reason = "unknown_user" if not user else ("inactive_user" if not usable else "bad_password")
        guard.register_failure(ip, account)
        logger.warning("login failed reason=%s ip=%s account=%s", reason, ip, account)
        record_audit(
            repository, "login_failed", username=account, ip=ip, result="failure",
            detail={"reason": reason},
        )
        raise invalid_credentials()
    guard.clear_account(account)
    record_audit(
        repository, "login_succeeded", username=account, ip=ip, result="success"
    )
    return {
        "user": {"id": user["id"], "username": user["username"]},
        **start_session(repository, user["id"], response),
    }


@router.post("/api/v1/auth/logout")
def logout(
    request: Request, response: Response,
    travelai_session: Optional[str] = Cookie(default=None),
):
    if travelai_session:
        service(request).repository.delete_session(hash_session_token(travelai_session))
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.post("/api/v1/auth/logout-all")
def logout_all(
    request: Request, response: Response,
    user: dict = Depends(current_user),
):
    """撤销该用户全部会话：sessions 按 user_id 归属，可整批删除。"""
    repository = service(request).repository
    revoked = repository.delete_sessions_for_user(user["id"])
    record_audit(
        repository, "logout_all", username=user["username"],
        ip=client_ip(request), result="success",
        detail={"revoked_sessions": revoked},
    )
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"status": "ok", "revoked": revoked}


@router.get("/api/v1/auth/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


@router.get("/api/v1/spots")
def list_spots(request: Request, city: Optional[str] = None):
    spots = service(request).repository.list_spots(city)
    return {"items": spots, "total": len(spots)}


@router.get("/api/v1/trips")
def list_trips(
    request: Request,
    user: dict = Depends(current_user),
    limit: int = Query(default=20, ge=1, le=50),
):
    return {"trips": service(request).repository.list_trips(user["id"], limit)}


@router.post("/api/v1/trips", response_model=MutationResponse, status_code=201)
def create_trip(
    payload: TripCreateRequest, request: Request, user: dict = Depends(current_user)
):
    result, run_id = service(request).create_trip(
        payload.spec, payload.include_evidence, owner_id=user["id"],
    )
    if result.status == "infeasible":
        infeasible(run_id, result)
    return MutationResponse(
        run_id=run_id,
        result=result,
        trip_id=result.plan.trip_id,
        version=result.plan.version,
    )


@router.get("/api/v1/trips/{trip_id}")
def get_trip(
    trip_id: str, request: Request, user: dict = Depends(current_user),
    version: Optional[int] = None,
):
    audit_trip_denial(request, user, trip_id=trip_id)
    return service(request).repository.get_plan(trip_id, version, owner_id=user["id"])


@router.get("/api/v1/trips/{trip_id}/versions")
def list_versions(trip_id: str, request: Request, user: dict = Depends(current_user)):
    audit_trip_denial(request, user, trip_id=trip_id)
    return {
        "trip_id": trip_id,
        "versions": service(request).repository.list_versions(trip_id, owner_id=user["id"]),
    }


@router.get("/api/v1/trips/{trip_id}/diff", response_model=PlanDiff)
def compare_trip(
    trip_id: str, request: Request, from_version: int, to_version: int,
    user: dict = Depends(current_user),
):
    audit_trip_denial(request, user, trip_id=trip_id)
    return service(request).compare(
        trip_id, from_version, to_version, owner_id=user["id"],
    )


@router.post("/api/v1/trips/{trip_id}/modify-day", response_model=MutationResponse)
def modify_day(
    trip_id: str, payload: ModifyDayRequest, request: Request,
    user: dict = Depends(current_user),
):
    audit_trip_denial(request, user, trip_id=trip_id)
    result, run_id, difference = service(request).modify_day(
        trip_id=trip_id,
        expected_version=payload.expected_version,
        day_number=payload.day_number,
        desired_spot_ids=payload.desired_spot_ids,
        lock_other_days=payload.lock_other_days,
        include_evidence=payload.include_evidence,
        owner_id=user["id"],
    )
    if result.status == "infeasible":
        infeasible(run_id, result)
    return MutationResponse(
        run_id=run_id,
        result=result,
        trip_id=trip_id,
        version=result.plan.version,
        diff=difference,
    )


@router.get("/api/v1/runs/{run_id}")
def get_run(run_id: str, request: Request, user: dict = Depends(current_user)):
    audit_trip_denial(request, user, run_id=run_id)
    return service(request).repository.get_run(run_id, owner_id=user["id"])


@router.post("/api/v1/evidence/search")
def search_evidence(payload: EvidenceSearchRequest, request: Request):
    items = service(request).search_evidence(payload.city, payload.query, payload.top_k)
    return {"items": items, "total": len(items), "notice": "检索内容仅作证据，不作为系统指令"}
