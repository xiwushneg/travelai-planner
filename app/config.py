from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class Settings:
    app_name: str = "TravelAI 规划工作台"
    version: str = "1.1.0"
    debug: bool = _bool("TRAVELAI_DEBUG", False)
    environment: str = os.getenv("TRAVELAI_ENV", "development").strip().lower()
    database_url: Path = Path(os.getenv("TRAVELAI_DB_PATH", "./data/travelai.db"))
    access_token_expire_minutes: int = 60 * 24
    cookie_secure: bool = _bool("TRAVELAI_COOKIE_SECURE", False)
    trust_proxy_headers: bool = _bool("TRAVELAI_TRUST_PROXY", False)
    login_failure_threshold: int = _env_int("TRAVELAI_LOGIN_FAILURE_THRESHOLD", 5, 1, 100)
    login_account_lock_threshold: int = _env_int(
        "TRAVELAI_LOGIN_ACCOUNT_LOCK_THRESHOLD", 25, 5, 1000
    )
    login_backoff_base_seconds: int = _env_int("TRAVELAI_LOGIN_BACKOFF_BASE_SECONDS", 30, 1, 3600)
    login_backoff_max_seconds: int = _env_int("TRAVELAI_LOGIN_BACKOFF_MAX_SECONDS", 1800, 1, 86400)
    register_max_attempts: int = _env_int("TRAVELAI_REGISTER_MAX_ATTEMPTS", 3, 1, 1000)
    register_window_seconds: int = _env_int("TRAVELAI_REGISTER_WINDOW_SECONDS", 600, 10, 86400)
    register_max_accounts: int = _env_int("TRAVELAI_REGISTER_MAX_ACCOUNTS", 10, 1, 1000)
    register_account_window_seconds: int = _env_int(
        "TRAVELAI_REGISTER_ACCOUNT_WINDOW_SECONDS", 3600, 60, 86400
    )
    audit_key: str = os.getenv("TRAVELAI_AUDIT_KEY", "travelai-audit")
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://localhost:8000"])
    seed_demo_data: bool = _bool("TRAVELAI_SEED_DEMO", True)
    evidence_enabled: bool = _bool("TRAVELAI_EVIDENCE", True)
    map_provider: str = os.getenv("TRAVELAI_MAP_PROVIDER", "local").strip().lower()
    baidu_map_ak: str | None = os.getenv("BAIDU_MAP_AK") or None
    baidu_route_mode: str = os.getenv("BAIDU_ROUTE_MODE", "walking").strip().lower()
    map_timeout_seconds: float = _env_float("TRAVELAI_MAP_TIMEOUT_SECONDS", 4.0, 0.5, 20.0)
    map_max_calls_per_plan: int = _env_int("TRAVELAI_MAP_MAX_CALLS_PER_PLAN", 40, 0, 200)
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY") or None
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    llm_timeout_seconds: float = _env_float("TRAVELAI_LLM_TIMEOUT_SECONDS", 25.0, 2.0, 120.0)
    weather_enabled: bool = _bool("TRAVELAI_WEATHER", True)
    weather_timeout_seconds: float = _env_float("TRAVELAI_WEATHER_TIMEOUT_SECONDS", 6.0, 1.0, 30.0)
    chat_history_limit: int = _env_int("TRAVELAI_CHAT_HISTORY_LIMIT", 100, 10, 500)

    @property
    def db_path(self) -> Path:
        """兼容原有启动代码的数据库路径名称。"""
        return self.database_url


settings = Settings()


def validate_settings(current: Settings) -> None:
    """启动自检：生产环境配置不安全时直接拒绝启动。"""
    if current.environment not in {"development", "production"}:
        raise RuntimeError(
            f"未知的 TRAVELAI_ENV：{current.environment}（可选 development / production）"
        )
    if current.environment == "production" and not current.cookie_secure:
        raise RuntimeError(
            "production 环境必须设置 TRAVELAI_COOKIE_SECURE=true，并通过 HTTPS 提供服务。"
        )
    if current.environment == "production":
        key = current.audit_key.strip()
        if key in {"", "travelai-audit"} or len(key) < 16:
            raise RuntimeError(
                "production 环境必须设置 TRAVELAI_AUDIT_KEY 为至少 16 位的随机密钥，"
                "不能使用默认值或空值。"
            )
