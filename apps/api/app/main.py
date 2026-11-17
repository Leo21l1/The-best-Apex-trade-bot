from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import rate_limit_key
from app.api.routes import (
    account,
    auth,
    broker,
    copilot,
    health,
    integrations,
    journal,
    lab,
    market,
    news,
    scanner,
    session,
    smc,
    terminal,
    ws,
)
from app.core.config import Environment, get_settings
from app.core.db import dispose_engine
from app.core.execution_guard import POLICY
from app.core.logging import configure_logging, log_event, request_id_var
from app.core.ratelimit import rate_limiter
from app.core.redis import close_redis

log = logging.getLogger("apex.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    log_event(
        log,
        "startup",
        environment=s.environment.value,
        execution_mode=s.execution_mode.value,
        policy=POLICY,
    )
    yield
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    docs = s.environment != Environment.PRODUCTION
    app = FastAPI(
        title=s.app_name,
        version=s.version,
        lifespan=lifespan,
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", "x-requested-with", "authorization"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        rid = rid[:64]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/health"):
                key, authed = rate_limit_key(request)
                limit = s.user_rate_limit_per_minute if authed else s.rate_limit_per_minute
                if not await rate_limiter.hit("api", key, limit):
                    return JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=429,
                        headers={"x-request-id": rid, **SECURITY_HEADERS},
                    )
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not request.url.path.startswith("/api/docs"):
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if s.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        log_event(
            log,
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
            request_id=rid,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo submitted values (could contain passwords).
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(auth.users_router, prefix="/api")
    app.include_router(market.router, prefix="/api")
    app.include_router(scanner.router, prefix="/api")
    app.include_router(account.router, prefix="/api")
    app.include_router(copilot.router, prefix="/api")
    app.include_router(terminal.router, prefix="/api")  # before lab: /journal/trades/{id} detail routes
    app.include_router(journal.router, prefix="/api")
    app.include_router(lab.router, prefix="/api")
    app.include_router(smc.router, prefix="/api")
    app.include_router(session.router, prefix="/api")
    app.include_router(news.router, prefix="/api")
    app.include_router(integrations.router, prefix="/api")
    app.include_router(broker.router, prefix="/api")
    app.include_router(ws.router, prefix="/api")
    return app


app = create_app()
