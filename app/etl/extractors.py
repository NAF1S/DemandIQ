"""Async extractors for the four canonical DemandIQ datasets.

Every function accepts an open :class:`AsyncSession` and an organization id, so
the caller controls the transaction and can reuse the same session for several
extracts.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.queries import (
    BOM_QUERY,
    CONSUMPTION_QUERY,
    ORDER_HISTORY_QUERY,
    STOCK_LEVELS_QUERY,
)


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings().all()]


async def extract_consumption(
    session: AsyncSession,
    organization_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily consumption by item/product, net of same-day returns."""
    return await _rows(
        session,
        CONSUMPTION_QUERY,
        {"org_id": organization_id, "start_date": start_date},
    )


async def extract_order_history(
    session: AsyncSession,
    organization_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Order-level history with line and unit aggregates."""
    return await _rows(
        session,
        ORDER_HISTORY_QUERY,
        {"org_id": organization_id, "start_date": start_date},
    )


async def extract_bom(
    session: AsyncSession,
    organization_id: int,
) -> list[dict[str, Any]]:
    """BOM lines, including sub-product components."""
    return await _rows(session, BOM_QUERY, {"org_id": organization_id})


async def extract_stock_levels(
    session: AsyncSession,
    organization_id: int,
) -> list[dict[str, Any]]:
    """Current item and product stock, with available = on-hand - reserved."""
    return await _rows(session, STOCK_LEVELS_QUERY, {"org_id": organization_id})


EXTRACTORS = {
    "consumption": extract_consumption,
    "order_history": extract_order_history,
    "bom": extract_bom,
    "stock_levels": extract_stock_levels,
}
