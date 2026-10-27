"""Async SQLAlchemy engine + session factory."""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
# expire_on_commit=False lets us read attributes after commit without a reload.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
