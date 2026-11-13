"""Authenticated REST boundary. A single process owns the embedded local worker."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from proofbeak import __version__
from proofbeak.service import ServiceError, TrialService


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    test: dict
    candidates: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(min_length=1, max_length=4)


class LookupRequest(RunRequest):
    fresh: bool = False


class BodyLimit:
    def __init__(self, app, limit=65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.limit:
                return await JSONResponse({"detail": "request_too_large"}, status_code=413)(scope, receive, send)
            messages.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        async def buffered():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, buffered, send)


def create_app(service: TrialService, background=True):
    @asynccontextmanager
    async def lifespan(app):
        service.start(background=background)
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Proofbeak", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(BodyLimit)
    bearer = HTTPBearer(auto_error=False)

    def tenant(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise ServiceError("unauthorized", 401)
        return service.authenticate(credentials.credentials)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse({"detail": exc.code}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "invalid_request"}, status_code=422)

    @app.get("/health")
    def health():
        ready = service.available
        return JSONResponse({"status": "ready" if ready else "unavailable",
                             "maintenance": "deferred" if service.maintenance_deferred else "ok",
                             "reason_code": service.coordinator_failure},
                            status_code=200 if ready else 503)

    @app.get("/packs")
    def packs(owner: str = Depends(tenant)):
        return service.packs(owner)

    @app.post("/runs", status_code=202)
    def submit(body: RunRequest, owner: str = Depends(tenant),
               key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
        result = service.submit(owner, body.test, body.candidates, key)
        return JSONResponse(result, status_code=202, headers={"Location": f'/runs/{result["run_id"]}'})

    @app.get("/runs")
    def history(owner: str = Depends(tenant), limit: int = Query(default=25, ge=1, le=100), before: str | None = None):
        return service.history(owner, limit, before)

    @app.get("/audit")
    def audit(owner: str = Depends(tenant), limit: int = Query(default=50, ge=1, le=100),
              before: int | None = Query(default=None, ge=1, le=2**63 - 1)):
        return service.audit_events(owner, limit, before)

    @app.post("/evidence/lookup")
    def lookup(body: LookupRequest, owner: str = Depends(tenant)):
        return service.lookup(owner, body.test, body.candidates, body.fresh)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, owner: str = Depends(tenant)):
        return service.get_run(owner, run_id)

    @app.get("/runs/{run_id}/comparison")
    def comparison(run_id: str, owner: str = Depends(tenant)):
        return service.comparison(owner, run_id)

    @app.get("/evidence/{evidence_id}")
    def evidence(evidence_id: str, owner: str = Depends(tenant)):
        return service.evidence(owner, evidence_id)

    @app.post("/runs/{run_id}/cleanup")
    def cleanup(run_id: str, owner: str = Depends(tenant)):
        return service.cleanup(owner, run_id)

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, owner: str = Depends(tenant)):
        return service.cancel(owner, run_id)

    @app.post("/runs/{run_id}/revoke")
    def revoke(run_id: str, owner: str = Depends(tenant)):
        return service.revoke(owner, run_id)

    @app.delete("/runs/{run_id}")
    def delete(run_id: str, owner: str = Depends(tenant)):
        return service.delete_run(owner, run_id)

    return app
