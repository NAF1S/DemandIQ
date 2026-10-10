"""Create the tables declared in :mod:`app.db.models`.

Supabase projects usually manage schema through the dashboard or the Supabase CLI. This
script is a convenience for local development and CI:

    python -m app.db.init_db
"""

from __future__ import annotations

import asyncio
import logging

from app.core.logging import configure_logging
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import dispose_engine, get_engine

# Importing the models module registers every table on Base.metadata.
from app.db import models  # noqa: F401

logger = logging.getLogger(__name__)


async def create_all() -> None:
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def main() -> None:
    configure_logging(get_settings())

    async def _run() -> None:
        try:
            await create_all()
            logger.info("Schema is up to date.")
        finally:
            await dispose_engine()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
