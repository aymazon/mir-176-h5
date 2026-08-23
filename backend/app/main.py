"""FastAPI application entrypoint.

REST API (todo 3): auth / character / health. SSE stream lands in todo 5.
"""

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.character import router as character_router
from app.api.health import router as health_router

app = FastAPI(title="mir-176-h5", version="0.1.0")

app.include_router(auth_router)
app.include_router(character_router)
app.include_router(health_router)
