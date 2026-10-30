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


async def extract_entities(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
) -> list[dict[str, Any]]:
    """Active items (shared) and products for one store."""
    return await _rows(
        session,
        """
        SELECT 'item' AS entity_type, id, name, sku, NULL::bigint AS store_id
        FROM sp_items
        WHERE organization_id = :org_id
          AND deleted_at IS NULL
        UNION ALL
        SELECT 'product' AS entity_type, id, name, sku, store_id
        FROM sp_products
        WHERE organization_id = :org_id
          AND store_id = :store_id
          AND deleted_at IS NULL
        ORDER BY entity_type, id
        """,
        {"org_id": organization_id, "store_id": store_id},
    )


async def extract_entity_orders(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    entity_type: str,
    entity_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily order count/revenue for one entity.

    Products use their order lines; items use the orders whose BOM snapshot
    contains that item. Product rows also return sold units.
    """
    if entity_type == "product":
        sql = """
            SELECT o.order_created_at::date AS ds,
                   COUNT(DISTINCT o.id) AS order_count,
                   COALESCE(SUM(oi.line_total), 0) AS revenue,
                   COALESCE(SUM(oi.quantity), 0) AS units
            FROM sp_orders o
            JOIN sp_order_items oi ON oi.order_id = o.id
            WHERE o.organization_id = :org_id
              AND o.store_id = :store_id
              AND oi.product_id = :entity_id
              AND o.order_created_at >= :start_date
            GROUP BY 1
            ORDER BY 1
        """
    else:
        sql = """
            SELECT o.order_created_at::date AS ds,
                   COUNT(DISTINCT o.id) AS order_count,
                   COALESCE(SUM(o.total), 0) AS revenue,
                   0::numeric AS units
            FROM sp_orders o
            JOIN sp_order_item_components c ON c.order_id = o.id
            WHERE o.organization_id = :org_id
              AND o.store_id = :store_id
              AND c.item_id = :entity_id
              AND o.order_created_at >= :start_date
            GROUP BY 1
            ORDER BY 1
        """

    return await _rows(
        session,
        sql,
        {
            "org_id": organization_id,
            "store_id": store_id,
            "entity_id": entity_id,
            "start_date": start_date,
        },
    )


async def extract_entity_returns(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    product_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily returned product units, bucketed by the order's returned_at."""
    return await _rows(
        session,
        """
        SELECT o.returned_at::date AS ds,
               COALESCE(SUM(oi.quantity), 0) AS returned
        FROM sp_orders o
        JOIN sp_order_items oi ON oi.order_id = o.id
        WHERE o.organization_id = :org_id
          AND o.store_id = :store_id
          AND oi.product_id = :product_id
          AND o.returned_at IS NOT NULL
          AND o.returned_at >= :start_date
          AND o.status IN ('returned', 'returned-charge')
        GROUP BY 1
        ORDER BY 1
        """,
        {
            "org_id": organization_id,
            "store_id": store_id,
            "product_id": product_id,
            "start_date": start_date,
        },
    )


async def extract_entity_reserved(
    session: AsyncSession,
    organization_id: int,
    entity_type: str,
    entity_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily reserved volume from the append-only reserve movements."""
    column = "item_id" if entity_type == "item" else "product_id"

    return await _rows(
        session,
        f"""
        SELECT date_trunc('day', COALESCE(occurred_at, created_at))::date AS ds,
               COALESCE(SUM(reserved_delta), 0) AS reserved
        FROM sp_item_movements
        WHERE organization_id = :org_id
          AND movement_type = 'reserve'
          AND {column} = :entity_id
          AND COALESCE(occurred_at, created_at) >= :start_date
        GROUP BY 1
        ORDER BY 1
        """,
        {
            "org_id": organization_id,
            "entity_id": entity_id,
            "start_date": start_date,
        },
    )


async def extract_entity_stock(
    session: AsyncSession,
    organization_id: int,
    entity_type: str,
    entity_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """End-of-day stock snapshots from the last ledger row of each day."""
    column = "item_id" if entity_type == "item" else "product_id"

    return await _rows(
        session,
        f"""
        SELECT ds, on_hand, reserved
        FROM (
            SELECT date_trunc('day', COALESCE(occurred_at, created_at))::date AS ds,
                   on_hand_after AS on_hand,
                   reserved_after AS reserved,
                   row_number() OVER (
                       PARTITION BY date_trunc('day', COALESCE(occurred_at, created_at))::date
                       ORDER BY COALESCE(occurred_at, created_at) DESC, id DESC
                   ) AS rn
            FROM sp_item_movements
            WHERE organization_id = :org_id
              AND {column} = :entity_id
              AND COALESCE(occurred_at, created_at) >= :start_date
        ) latest
        WHERE rn = 1
        ORDER BY ds
        """,
        {
            "org_id": organization_id,
            "entity_id": entity_id,
            "start_date": start_date,
        },
    )


async def extract_traffic(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Product daily traffic rows; the pipeline keeps jetpack first per day."""
    return await _rows(
        session,
        """
        SELECT product_id,
               traffic_date AS ds,
               page_views,
               add_to_cart,
               cart_removals,
               sessions,
               unique_visitors,
               source
        FROM sp_traffic_daily
        WHERE organization_id = :org_id
          AND store_id = :store_id
          AND traffic_date >= :start_date
        ORDER BY product_id, traffic_date, (source = 'jetpack') DESC, updated_at DESC
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_site_traffic(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Site-wide daily views/visitors and cart/checkout page views."""
    return await _rows(
        session,
        """
        SELECT traffic_date AS ds,
               page_views AS site_views,
               unique_visitors AS site_visitors,
               cart_page_views,
               checkout_page_views
        FROM sp_traffic_daily_org
        WHERE organization_id = :org_id
          AND store_id = :store_id
          AND source = 'jetpack'
          AND traffic_date >= :start_date
        ORDER BY traffic_date
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_referrers(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily Facebook/Instagram/Search views for the channel-mix features."""
    return await _rows(
        session,
        """
        SELECT traffic_date AS ds,
               COALESCE(SUM(page_views) FILTER (WHERE referrer_group = 'facebook.com'), 0) AS facebook_views,
               COALESCE(SUM(page_views) FILTER (WHERE referrer_group = 'instagram.com'), 0) AS instagram_views,
               COALESCE(SUM(page_views) FILTER (WHERE referrer_group ILIKE '%search%'), 0) AS search_views
        FROM sp_traffic_referrers_daily
        WHERE organization_id = :org_id
          AND store_id = :store_id
          AND source = 'jetpack'
          AND traffic_date >= :start_date
        GROUP BY traffic_date
        ORDER BY traffic_date
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_product_orders(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily product order count/revenue/units for all products in one query."""
    return await _rows(
        session,
        """
        SELECT oi.product_id AS entity_id,
               o.order_created_at::date AS ds,
               COUNT(DISTINCT o.id) AS order_count,
               COALESCE(SUM(oi.line_total), 0) AS revenue,
               COALESCE(SUM(oi.quantity), 0) AS units
        FROM sp_orders o
        JOIN sp_order_items oi ON oi.order_id = o.id
        WHERE o.organization_id = :org_id
          AND o.store_id = :store_id
          AND oi.product_id IS NOT NULL
          AND o.order_created_at >= :start_date
        GROUP BY oi.product_id, ds
        ORDER BY entity_id, ds
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_item_orders(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily item order count/revenue for every item in one query."""
    return await _rows(
        session,
        """
        SELECT c.item_id AS entity_id,
               o.order_created_at::date AS ds,
               COUNT(DISTINCT o.id) AS order_count,
               COALESCE(SUM(o.total), 0) AS revenue
        FROM sp_orders o
        JOIN sp_order_item_components c ON c.order_id = o.id
        WHERE o.organization_id = :org_id
          AND o.store_id = :store_id
          AND c.item_id IS NOT NULL
          AND o.order_created_at >= :start_date
        GROUP BY c.item_id, ds
        ORDER BY entity_id, ds
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_product_returns(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily returned units for every product, bucketed by returned_at."""
    return await _rows(
        session,
        """
        SELECT oi.product_id AS entity_id,
               o.returned_at::date AS ds,
               COALESCE(SUM(oi.quantity), 0) AS returned
        FROM sp_orders o
        JOIN sp_order_items oi ON oi.order_id = o.id
        WHERE o.organization_id = :org_id
          AND o.store_id = :store_id
          AND oi.product_id IS NOT NULL
          AND o.returned_at IS NOT NULL
          AND o.returned_at >= :start_date
          AND o.status IN ('returned', 'returned-charge')
        GROUP BY oi.product_id, ds
        ORDER BY entity_id, ds
        """,
        {"org_id": organization_id, "store_id": store_id, "start_date": start_date},
    )


async def extract_stock_snapshots(
    session: AsyncSession,
    organization_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """End-of-day stock snapshots for every item/product in one query."""
    return await _rows(
        session,
        """
        SELECT ds, item_id, product_id, on_hand, reserved
        FROM (
            SELECT date_trunc('day', COALESCE(occurred_at, created_at))::date AS ds,
                   item_id,
                   product_id,
                   on_hand_after AS on_hand,
                   reserved_after AS reserved,
                   row_number() OVER (
                       PARTITION BY item_id, product_id,
                                    date_trunc('day', COALESCE(occurred_at, created_at))::date
                       ORDER BY COALESCE(occurred_at, created_at) DESC, id DESC
                   ) AS rn
            FROM sp_item_movements
            WHERE organization_id = :org_id
              AND COALESCE(occurred_at, created_at) >= :start_date
              AND (item_id IS NOT NULL OR product_id IS NOT NULL)
        ) latest
        WHERE rn = 1
        ORDER BY ds
        """,
        {"org_id": organization_id, "start_date": start_date},
    )


async def extract_reserved_volumes(
    session: AsyncSession,
    organization_id: int,
    start_date: date,
) -> list[dict[str, Any]]:
    """Daily reserved volume for every item/product in one query."""
    return await _rows(
        session,
        """
        SELECT date_trunc('day', COALESCE(occurred_at, created_at))::date AS ds,
               item_id,
               product_id,
               COALESCE(SUM(reserved_delta), 0) AS reserved
        FROM sp_item_movements
        WHERE organization_id = :org_id
          AND movement_type = 'reserve'
          AND COALESCE(occurred_at, created_at) >= :start_date
        GROUP BY ds, item_id, product_id
        ORDER BY ds
        """,
        {"org_id": organization_id, "start_date": start_date},
    )


EXTRACTORS = {
    "consumption": extract_consumption,
    "order_history": extract_order_history,
    "bom": extract_bom,
    "stock_levels": extract_stock_levels,
}
