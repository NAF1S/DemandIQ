"""Async Postgres engine/session wiring for the Supabase database.

Supabase hands out plain ``postgresql://`` DSNs. asyncpg, on the other hand, rejects
``sslmode`` and ``pgbouncer`` query parameters and needs TLS configured through
``connect_args``. :func:`normalize_database_url` bridges the two so the value from the
Supabase dashboard can be pasted into ``.env`` unchanged.
"""

from __future__ import annotations

import ssl as ssl_module
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import NotConfiguredError

_ASYNC_DRIVER = "postgresql+asyncpg"


def _ssl_context(sslmode: str) -> ssl_module.SSLContext:
    """Translate a libpq ``sslmode`` into the asyncpg equivalent.

    asyncpg's ``ssl=True`` means "encrypt *and* verify", but libpq's ``require`` only
    means "encrypt". Supabase's pooler presents a chain that is not in the default trust
    store, so mapping ``require`` to ``True`` would reject a perfectly valid server.
    """
    context = ssl_module.create_default_context()
    if sslmode == "verify-full":
        return context
    if sslmode == "verify-ca":
        context.check_hostname = False
        return context
    context.check_hostname = False
    context.verify_mode = ssl_module.CERT_NONE
    return context


def normalize_database_url(raw_url: str) -> tuple[str, dict[str, Any]]:
    """Return an asyncpg-compatible URL plus the ``connect_args`` it implies."""
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = f"{_ASYNC_DRIVER}://{url[len('postgres://'):]}"
    elif url.startswith("postgresql://"):
        url = f"{_ASYNC_DRIVER}://{url[len('postgresql://'):]}"

    if not url.startswith(f"{_ASYNC_DRIVER}://"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string.")

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    connect_args: dict[str, Any] = {}

    # asyncpg rejects `sslmode` and `pgbouncer` as connect() keywords, so pop them here.
    sslmode = query.pop("sslmode", None)
    if sslmode:
        connect_args["ssl"] = _ssl_context(sslmode)

    pooled = query.pop("pgbouncer", None)
    if parts.port == 6543 or pooled in {"true", "1"}:
        # Supabase's transaction pooler does not support prepared statements.
        connect_args["statement_cache_size"] = 0
        # Supavisor always terminates TLS, so encrypt even without an explicit sslmode.
        connect_args.setdefault("ssl", _ssl_context("require"))

    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return cleaned, connect_args


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.database_configured:
            raise NotConfiguredError(
                "DATABASE_URL is not set. Copy .env.example to .env and add your "
                "Supabase Postgres connection string."
            )
        url, connect_args = normalize_database_url(settings.database_url)
        _engine = create_async_engine(
            url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session; rolls back if the block raises."""
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for routes that require the database."""
    if not get_settings().database_configured:
        raise NotConfiguredError(
            "DATABASE_URL is not set. Copy .env.example to .env and add your "
            "Supabase Postgres connection string."
        )
    async with session_scope() as session:
        yield session


async def get_optional_db() -> AsyncIterator[AsyncSession | None]:
    """Dependency for routes that keep working when Postgres is unavailable."""
    if not get_settings().database_configured:
        yield None
        return
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
