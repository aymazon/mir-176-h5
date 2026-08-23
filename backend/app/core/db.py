"""Async SQLAlchemy engine / session factory for the FastAPI request path.

Single source of DB connectivity (todo 2 built the schema via alembic; runtime
access goes through here). ``pool_pre_ping`` lets the health endpoint surface
dead connections as errors instead of hanging.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(str(settings.DATABASE_URL), pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
