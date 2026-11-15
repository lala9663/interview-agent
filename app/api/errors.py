"""오류 응답을 API 명세 형식 `{code, message, details}`로 맞춘다 (API 명세 1장)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.graphs.structured import StructuredOutputError
from app.providers.base import ProviderError

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def _body(code: str, message: str, details: dict | None = None) -> dict:
    return {"code": code, "message": message, "details": details or {}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg", "")}
                  for e in exc.errors()]
        return JSONResponse(_body("VALIDATION_FAILED", "입력 형식이 올바르지 않습니다.", {"errors": errors}),
                            status_code=400)

    @app.exception_handler(ProviderError)
    async def _provider(_: Request, exc: ProviderError) -> JSONResponse:
        log.error("모델 호출 실패: %s", exc)
        return JSONResponse(_body("MODEL_FAILED", "모델 호출에 실패했습니다.", {"reason": str(exc)[:300]}),
                            status_code=502)

    @app.exception_handler(StructuredOutputError)
    async def _structured(_: Request, exc: StructuredOutputError) -> JSONResponse:
        log.error("모델 출력 형식 오류: %s", exc)
        return JSONResponse(_body("MODEL_FAILED", "모델 출력이 형식에 맞지 않습니다.", {"reason": str(exc)[:300]}),
                            status_code=502)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            exc.status_code, "ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)
