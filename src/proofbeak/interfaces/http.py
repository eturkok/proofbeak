"""Authenticated REST boundary. A single process owns the embedded local worker."""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
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


class SuiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suite: dict


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    baseline: str
    current: str
    kind: str = "trial"
    allow_provider_version_change: bool = False


class BundleVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bundle: dict
    public_key: dict


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=32, max_length=256)


class ProviderProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)
    settings: dict


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    candidates: list[str] = Field(min_length=1, max_length=4)
    name: str = Field(min_length=1, max_length=120)
    observer: str
    arguments: dict
    path: list
    expected: object
    predicate: str = "eq"
    actions: list[dict] = Field(default_factory=list)


class OpenAPIImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specification: dict
    operations: list[str] = Field(min_length=1, max_length=64)


class BodyLimit:
    def __init__(self, app, limit=65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = {"/bundles/verify": 16 * 1024 * 1024, "/authoring/import-openapi": 2 * 1024 * 1024}.get(scope.get("path"), self.limit)
        messages, size = [], 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if size > limit:
                return await JSONResponse({"detail": "request_too_large"}, status_code=413)(scope, receive, send)
            messages.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body", False):
                break
        async def buffered():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, buffered, send)


def create_app(service: TrialService, background=True, studio_providers=None):
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
    from proofbeak.web_sessions import BrowserSessions, COOKIE, LIFETIME, same_origin
    sessions = BrowserSessions(service)

    def tenant(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            return sessions.authenticate(request)
        if credentials.scheme.lower() != "bearer":
            raise ServiceError("unauthorized", 401)
        return service.authenticate(credentials.credentials)

    @app.middleware("http")
    async def secure_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.post("/ui/login")
    def login(body: LoginRequest, request: Request):
        cookie, result = sessions.create(body.token, request)
        response = JSONResponse(result)
        response.set_cookie(COOKIE, cookie, max_age=LIFETIME, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return response

    @app.get("/ui/session")
    def session(request: Request):
        same_origin(request)
        value = sessions.get(request)
        return {"authenticated": bool(value), **({"tenant": value["owner"], "csrf": value["csrf"]} if value else {})}

    @app.post("/ui/logout")
    def logout(request: Request, owner: str = Depends(tenant)):
        sessions.discard(request)
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/authoring/catalog")
    def authoring_catalog(owner: str = Depends(tenant)):
        from proofbeak.authoring import operation_catalog
        from proofbeak.exports import signing_status
        return {**operation_catalog(service.packs(owner)), "features": {
            "provider_management": studio_providers is not None and len(service.tenants) == 1,
            "signing": signing_status(), "version": __version__}}

    @app.post("/authoring/draft")
    def draft(body: DraftRequest, owner: str = Depends(tenant)):
        from proofbeak.authoring import draft_scenario
        service._adapters(owner, body.candidates)
        try:
            spec = draft_scenario(service.packs(owner), **body.model_dump())
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ServiceError("invalid_authoring_draft", 422) from None
        return {"test": spec, "validation": service.validate_trial(owner, spec, body.candidates), "execution_performed": False}

    @app.post("/authoring/preview")
    def preview(body: SuiteRequest, owner: str = Depends(tenant)):
        from proofbeak.authoring import preview_suite
        return preview_suite(service, owner, body.suite)

    @app.post("/authoring/import-openapi")
    def import_openapi(body: OpenAPIImportRequest, owner: str = Depends(tenant)):
        from proofbeak.connectors.rest import openapi_operations
        from proofbeak.models import canonical
        if len(canonical(body.specification).encode()) > 1048576:
            raise ServiceError("openapi_too_large", 413)
        try:
            operations = openapi_operations(body.specification, body.operations)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ServiceError("invalid_openapi_import", 422) from None
        return {"operations": operations, "requires_operator_review": True, "provider_work_performed": False}

    @app.post("/studio/providers", status_code=201)
    def configure_provider(body: ProviderProfileRequest, owner: str = Depends(tenant)):
        if studio_providers is None:
            raise ServiceError("operator_configuration_required", 403)
        return studio_providers.add(owner, body.name, body.settings)

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

    @app.post("/suites/validate")
    def validate_suite(body: SuiteRequest, owner: str = Depends(tenant)):
        return service.validate_suite(owner, body.suite)

    @app.post("/suites", status_code=202)
    def submit_suite(body: SuiteRequest, owner: str = Depends(tenant),
                     key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
        result = service.submit_suite(owner, body.suite, key)
        return JSONResponse(result, status_code=202, headers={"Location": f'/suites/{result["suite_id"]}'})

    @app.get("/suites")
    def suites(owner: str = Depends(tenant), limit: int = Query(default=25, ge=1, le=100), before: str | None = None):
        return service.list_suites(owner, limit, before)

    @app.get("/suites/{suite_id}")
    def get_suite(suite_id: str, owner: str = Depends(tenant)):
        return service.get_suite(owner, suite_id)

    @app.post("/suites/{suite_id}/cancel")
    def cancel_suite(suite_id: str, owner: str = Depends(tenant)):
        return service.cancel_suite(owner, suite_id)

    @app.get("/suites/{suite_id}/export")
    def export_suite(suite_id: str, format: str = "json", owner: str = Depends(tenant)):
        from proofbeak import ci
        from proofbeak.models import canonical
        report = service.get_suite(owner, suite_id)
        formats = {"json": (canonical, "application/json", "json"),
                   "html": (ci.render_suite_html, "text/html", "html"),
                   "junit": (ci.junit, "application/xml", "xml"), "summary": (ci.markdown, "text/markdown", "md")}
        if format not in formats:
            raise ServiceError("invalid_export_format", 422)
        render, media_type, suffix = formats[format]
        return Response(render(report), media_type=media_type, headers={
            "Content-Disposition": f'attachment; filename="proofbeak-{suite_id}.{suffix}"',
            "Cache-Control": "no-store"})

    @app.post("/comparisons")
    def compare(body: ComparisonRequest, owner: str = Depends(tenant)):
        from proofbeak.regression import compare_reports
        if body.kind not in {"trial", "suite"}:
            raise ServiceError("invalid_comparison_kind", 422)
        read = service.get_suite if body.kind == "suite" else service.comparison
        return compare_reports(read(owner, body.baseline), read(owner, body.current), body.allow_provider_version_change)

    @app.get("/signing")
    def signing(owner: str = Depends(tenant)):
        from proofbeak.exports import signing_status
        return signing_status()

    @app.post("/bundles/verify")
    def verify_export(body: BundleVerificationRequest, owner: str = Depends(tenant)):
        from proofbeak.bundles import verify_bundle
        try:
            return verify_bundle(body.bundle, body.public_key)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ServiceError("bundle_verification_failed", 422) from None

    @app.post("/suites/{suite_id}/bundle")
    def export_signed_suite(suite_id: str, owner: str = Depends(tenant)):
        from proofbeak.exports import signed_export
        return signed_export(service, owner, suite_id, "suite")

    @app.post("/runs", status_code=202)
    def submit(body: RunRequest, owner: str = Depends(tenant),
               key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128)):
        result = service.submit(owner, body.test, body.candidates, key)
        return JSONResponse(result, status_code=202, headers={"Location": f'/runs/{result["run_id"]}'})

    @app.post("/trials/validate")
    def validate_trial(body: RunRequest, owner: str = Depends(tenant)):
        return service.validate_trial(owner, body.test, body.candidates)

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

    @app.get("/runs/{run_id}/export")
    def export_trial(run_id: str, format: str = "json", owner: str = Depends(tenant)):
        from proofbeak.exports import portable_report
        from proofbeak.models import canonical
        from proofbeak.report import render_html
        if format not in {"json", "html"}:
            raise ServiceError("invalid_export_format", 422)
        report = portable_report(service, owner, run_id)
        return Response(canonical(report) if format == "json" else render_html(report),
            media_type="application/json" if format == "json" else "text/html",
            headers={"Content-Disposition": f'attachment; filename="proofbeak-{run_id}.{format}"', "Cache-Control": "no-store"})

    @app.post("/runs/{run_id}/bundle")
    def export_signed_trial(run_id: str, owner: str = Depends(tenant)):
        from proofbeak.exports import signed_export
        return signed_export(service, owner, run_id)

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

    from importlib.resources import files
    assets = files("proofbeak").joinpath("web")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    def console():
        return FileResponse(str(assets.joinpath("index.html")), media_type="text/html")

    app.mount("/ui/assets", StaticFiles(directory=str(assets), check_dir=False), name="workspace-assets")
    return app
