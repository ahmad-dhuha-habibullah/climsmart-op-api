"""Standard v2 error envelope: {"error": {"code", "message", "details"}}."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} '{ident}' not found")


def conflict(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)


def invalid(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(422, code, message, details)


def envelope(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
               409: "conflict", 413: "payload_too_large", 422: "validation_error", 429: "rate_limited",
               500: "internal_error", 502: "upstream_error", 503: "unavailable"}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(envelope(e.code, e.message, e.details), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        details = [{"loc": list(x.get("loc", [])), "msg": x.get("msg"), "type": x.get("type")} for x in e.errors()]
        return JSONResponse(envelope("validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = _HTTP_CODES.get(e.status_code, "error")
        return JSONResponse(envelope(code, str(e.detail)), status_code=e.status_code, headers=e.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):  # never leak internals
        import logging
        logging.getLogger("climsmart.v2").exception("unhandled error")
        return JSONResponse(envelope("internal_error", "Internal server error"), status_code=500)
