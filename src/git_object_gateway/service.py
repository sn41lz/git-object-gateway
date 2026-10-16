"""A single-repository, read-only API. Authorization precedes Git reads and cache hits."""

import hmac
import threading
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .protocol import GatewayError, canonical
from .repository import Repository
from .telemetry import Telemetry


def validate_token(token: str) -> str:
    if not token.isascii() or not 32 <= len(token) <= 256 or any(c.isspace() for c in token):
        raise GatewayError("token_must_be_32_to_256_ascii_characters")
    return token


def create_app(
    repository: Repository,
    token: str,
    telemetry: Telemetry | None = None,
    *,
    max_requests: int = 16,
) -> FastAPI:
    validate_token(token)
    if not 1 <= max_requests <= 128:
        raise GatewayError("invalid_request_limit")
    metrics = telemetry or Telemetry()
    gate = threading.BoundedSemaphore(max_requests)
    bearer = HTTPBearer(auto_error=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        metrics.close()

    app = FastAPI(
        title="Git Object Gateway",
        version="0.1.0",
        lifespan=lifespan,
        description="Read-only, commit-pinned Git queries. One token grants read access to the history of the configured refs.",
        docs_url="/docs",
        redoc_url=None,
    )

    async def authenticated(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        supplied = (
            credentials.credentials
            if credentials and credentials.scheme.lower() == "bearer"
            else ""
        )
        if not supplied.isascii() or not hmac.compare_digest(supplied, token):
            raise GatewayError("unauthorized", 401)

    auth = [Depends(authenticated)]

    @app.exception_handler(GatewayError)
    async def domain_error(request: Request, exc: GatewayError):
        return JSONResponse(
            {"error": exc.code},
            status_code=exc.status,
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else {},
        )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        if len(request.scope.get("query_string", b"")) > 8192 or len(request.url.path) > 2048:
            return JSONResponse({"error": "request_too_large"}, status_code=414)
        if not gate.acquire(blocking=False):
            return JSONResponse(
                {"error": "server_busy"}, status_code=503, headers={"Retry-After": "1"}
            )
        started = time.perf_counter()
        status, size = 500, 0
        try:
            with metrics.tracer.start_as_current_span(
                "gateway.request", record_exception=False, set_status_on_exception=False
            ) as span:
                try:
                    response = await call_next(request)
                except Exception:
                    response = JSONResponse({"error": "internal_error"}, status_code=500)
                status = response.status_code
                size = int(response.headers.get("content-length", "0"))
                span.set_attribute("http.response.status_code", status)
                span.set_attribute("http.response.body.size", size)
                route = request.scope.get("route")
                span.set_attribute("http.route", getattr(route, "path", "unmatched"))
                response.headers.update(
                    {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
                )
                return response
        finally:
            metrics.observe((time.perf_counter() - started) * 1000, status, size)
            gate.release()

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/v1/resolve", dependencies=auth)
    def resolve(ref: str = Query("main", min_length=1, max_length=256)):
        return repository.resolve(ref)

    @app.get("/v1/snapshots/{commit}/manifest", dependencies=auth)
    def manifest(
        commit: str,
        path: Annotated[list[str], Query(min_length=1, max_length=32)],
        request: Request,
    ):
        result = repository.manifest(commit, path)
        etag = '"' + result.digest + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            canonical(result.payload()), media_type="application/json", headers={"ETag": etag}
        )

    @app.get("/v1/snapshots/{commit}/file", dependencies=auth)
    def file(commit: str, path: str, oid: str, request: Request):
        entry, data, hit = repository.file(commit, path, oid)
        headers = {
            "ETag": '"' + entry.oid + '"',
            "X-Git-Object-Format": repository.algorithm,
            "X-Object-Cache": "hit" if hit else "miss",
        }
        if request.headers.get("if-none-match") == headers["ETag"]:
            return Response(status_code=304, headers=headers)
        return Response(data, media_type="application/octet-stream", headers=headers)

    @app.get("/v1/diff", dependencies=auth)
    def diff(base: str, head: str):
        return repository.diff(base, head)

    @app.get("/v1/stats", dependencies=auth)
    def stats():
        return {"cache": repository.cache_stats(), "http": metrics.stats()}

    return app
