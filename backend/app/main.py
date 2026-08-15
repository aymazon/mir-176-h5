"""FastAPI application entry point for the mir-176-h5 backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.db.pool import close_pool, init_pool


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle: open the database pool, close it on shutdown."""
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="mir-176-h5", lifespan=lifespan)
app.include_router(auth_router, prefix="/api")


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Liveness probe; deliberately independent of the database."""
    return {"ok": True}
