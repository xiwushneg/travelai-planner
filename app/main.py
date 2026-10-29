"""FastAPI 应用工厂。核心运行不依赖外部 LLM、Chroma 或天气服务。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import router
from app.config import settings, validate_settings
from app.data.seed import DEMO_GUIDES, DEMO_SPOTS
from app.rag.local import LocalLexicalRetriever
from app.login_guard import LoginGuard
from app.repository import NotFoundError, Repository, VersionConflictError
from app.service import ModificationError, PlanningService


class UTF8JSONResponse(JSONResponse):
    """显式声明 UTF-8，避免 PowerShell 等客户端按 Latin-1 解码中文。"""

    media_type = "application/json; charset=utf-8"


def create_app(db_path: str | None = None, web_dist: str | None = None) -> FastAPI:
    validate_settings(settings)
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="旅行规划 Web 工作台 + 确定性排程 + 可选证据检索 + 不可变版本",
        default_response_class=UTF8JSONResponse,
    )
    repository = Repository(db_path or settings.db_path)
    if settings.seed_demo_data and not repository.list_spots():
        repository.replace_spots(DEMO_SPOTS)
    retriever = LocalLexicalRetriever(DEMO_GUIDES) if settings.evidence_enabled else None
    planning_service = PlanningService(repository, retriever, settings=settings)
    app.state.planning_service = planning_service
    app.state.login_guard = LoginGuard(
        repository,
        threshold=settings.login_failure_threshold,
        base_seconds=settings.login_backoff_base_seconds,
        max_seconds=settings.login_backoff_max_seconds,
    )
    # 注册限流直接使用数据库计数（register_attempts），多实例共享，无需进程内状态。

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError):
        return UTF8JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(VersionConflictError)
    async def version_conflict(_: Request, exc: VersionConflictError):
        return UTF8JSONResponse(status_code=409, content={
            "code": "VERSION_CONFLICT", "detail": str(exc),
            "expected_version": exc.expected, "current_version": exc.current,
        })

    @app.exception_handler(ModificationError)
    async def invalid_modification(_: Request, exc: ModificationError):
        return UTF8JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def utf8_http_exception(request: Request, exc: StarletteHTTPException):
        return UTF8JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    app.include_router(router)

    dist = Path(web_dist) if web_dist else Path(__file__).resolve().parents[1] / "frontend" / "dist"
    index_file = dist / "index.html"
    if index_file.is_file():
        for asset_dir in ("assets", "images"):
            if (dist / asset_dir).is_dir():
                app.mount(f"/{asset_dir}", StaticFiles(directory=dist / asset_dir), name=asset_dir)

    @app.get("/", include_in_schema=False)
    async def web_home():
        if index_file.is_file():
            # index.html 不缓存，保证前端重新构建后用户能拿到新的资源指纹。
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return JSONResponse({
            "name": settings.app_name,
            "message": "前端尚未构建；请运行 cd frontend && npm install && npm run build",
            "api_docs": "/docs",
        }, media_type="application/json; charset=utf-8")

    @app.get("/{path:path}", include_in_schema=False)
    async def web_fallback(path: str):
        if path.split("/", 1)[0] in {"api", "docs", "openapi.json", "redoc"}:
            return UTF8JSONResponse(status_code=404, content={"detail": "Not Found"})
        if index_file.is_file():
            # index.html 不缓存，保证前端重新构建后用户能拿到新的资源指纹。
            return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
        return UTF8JSONResponse(status_code=404, content={"detail": "Not Found"})

    return app


app = create_app()
