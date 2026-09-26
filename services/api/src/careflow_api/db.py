"""Async SQLAlchemy engine and sessions (psycopg 3). The app connects as careflow_app."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from careflow_api.config import get_settings, sqlalchemy_url


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(
        sqlalchemy_url(get_settings().database_url), pool_pre_ping=True, pool_size=5
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(get_engine(), expire_on_commit=False)
