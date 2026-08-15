"""FastAPI application entry point for the mir-176-h5 backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.characters import router as characters_router
from app.config import Settings
from app.db.persist_worker import PersistWorker
from app.db.pool import close_pool, get_pool, init_pool
from app.net.gateway import gateway_housekeeping, router as ws_router
from app.net.session import GatewayState, SessionRegistry
from app.world.loop import GameLoop
from app.world.world import GameWorld


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle: DB pool, shared world + game loop, gateway, persistence."""
    settings = Settings()
    _ = await init_pool()
    try:
        world = GameWorld()
        game_loop = GameLoop(world)
        _app.state.world = world
        _app.state.game_loop = game_loop
        gateway = GatewayState(
            world=world,
            sessions=SessionRegistry(),
            pool=get_pool(),
            settings=settings,
        )
        _app.state.gateway = gateway
        persist_worker = PersistWorker(gateway)
        _app.state.persist_worker = persist_worker
        async with anyio.create_task_group() as tasks:
            _ = tasks.start_soon(game_loop.run)
            _ = tasks.start_soon(gateway_housekeeping, gateway)
            _ = tasks.start_soon(persist_worker.run)
            try:
                yield
            finally:
                await persist_worker.stop()
                game_loop.stop()
                tasks.cancel_scope.cancel()
    finally:
        await close_pool()


app = FastAPI(title="mir-176-h5", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")
app.include_router(characters_router, prefix="/api")
app.include_router(ws_router)


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Liveness probe; deliberately independent of the database."""
    return {"ok": True}
