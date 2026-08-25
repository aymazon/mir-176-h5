"""FastAPI application entrypoint.

REST API (todo 3): auth / character / health；SSE 流 (todo 5)：/api/stream。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.character import router as character_router
from app.api.consumable import router as consumable_router
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.shop import router as shop_router
from app.api.stream import router as stream_router
from app.api.stream import shutdown_mock_sources
from app.sim.tick_loop import shutdown_all_sessions
from app.sim.zone import shutdown_all


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """关停时取消 dev 模拟源 + 全部 tick task + 地图实例（启动均为懒创建）。"""
    yield
    await shutdown_mock_sources()
    await shutdown_all_sessions()
    await shutdown_all()


app = FastAPI(title="mir-176-h5", version="0.1.0", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(character_router)
app.include_router(consumable_router)
app.include_router(health_router)
app.include_router(inventory_router)
app.include_router(shop_router)
app.include_router(stream_router)
