"""FastAPI application entrypoint.

REST API (todo 3): auth / character / health；SSE 流 (todo 5)：/api/stream。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.character import router as character_router
from app.api.health import router as health_router
from app.api.stream import router as stream_router
from app.api.stream import shutdown_mock_sources


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """关停时取消 dev 模拟日志源（启动为懒创建：首个 SSE 连接触发）。"""
    yield
    await shutdown_mock_sources()


app = FastAPI(title="mir-176-h5", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(character_router)
app.include_router(health_router)
app.include_router(stream_router)
