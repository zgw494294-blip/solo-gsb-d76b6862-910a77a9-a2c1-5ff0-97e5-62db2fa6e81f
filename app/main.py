"""FastAPI 入口：求解接口 + 静态页面托管。"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import NetworkError
from .solver import validate_and_solve

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(title="闭环供水管网平衡台", version="1.0.0")


@app.exception_handler(NetworkError)
async def network_error_handler(_request: Request, exc: NetworkError) -> JSONResponse:
    status = 422 if exc.code == "not_converged" else 400
    return JSONResponse(status_code=status, content={"ok": False, "error": exc.message, "code": exc.code})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "error": "请求格式不正确", "code": "bad_request"})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc.detail), "code": "http_error"})


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/solve")
async def solve(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "请求体必须是合法的 JSON", "code": "bad_request"},
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "请求体必须是 JSON 对象", "code": "bad_request"},
        )
    return {"ok": True, "result": validate_and_solve(payload)}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
