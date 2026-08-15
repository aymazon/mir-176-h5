"""FastAPI application entry point for the mir-176-h5 backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifecycle hook; resource setup arrives in later milestones."""
    yield


app = FastAPI(title="mir-176-h5", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Liveness probe; deliberately independent of the database."""
    return {"ok": True}
