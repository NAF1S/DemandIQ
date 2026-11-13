"""Direct psycopg extraction for the Phase 5 feature pipeline.

The local/EC2 path shells out to the portal's PHP bridge because Supavisor
drops psql sessions from those hosts. Modal reaches the pooler directly, so it
uses this module instead. Both return the same frame names and columns.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from psycopg import Connection
from psycopg.rows import dict_row

ENTITIES_QUERY = """
SELECT 'item' AS entity_type, id, name, sku, NULL::bigint AS store_id
FROM sp_items
WHERE organization_id = %(org_id)s AND deleted_at IS NULL
UNION ALL
SELECT 'product' AS entity_type, id, name, sku, store_id
FROM sp_products
WHERE organization_id = %(org_id)s AND store_id = %(store_id)s AND deleted_at IS NULL
ORDER BY entity_type, id
"""

TRAFFIC_QUERY = """
SELECT product_id, traffic_date AS ds, page_views, add_to_cart, cart_removals,
       sessions, unique_visitors, source
FROM sp_traffic_daily
WHERE organization_id = %(org_id)s
  AND store_id = %(store_id)s
  AND traffic_date >= %(start_date)s
ORDER BY product_id, traffic_date, (source = 'jetpack') DESC, updated_at DESC
"""

SITE_QUERY = """
SELECT traffic_date AS ds, page_views AS site_views, unique_visitors AS site_visitors,
       cart_page_views, checkout_page_views
FROM sp_traffic_daily_org
WHERE organization_id = %(org_id)s
  AND store_id = %(store_id)s
  AND source = 'jetpack'
  AND traffic_date >= %(start_date)s
ORDER BY traffic_date
"""

REFERRERS_QUERY = """
SELECT traffic_date AS ds,
       COALESCE(SUM(page_views) FILTER (WHERE referrer_group = 'facebook.com'), 0) AS facebook_views,
       COALESCE(SUM(page_views) FILTER (WHERE referrer_group = 'instagram.com'), 0) AS instagram_views,
       COALESCE(SUM(page_views) FILTER (WHERE referrer_group ILIKE '%%search%%'), 0) AS search_views
FROM sp_traffic_referrers_daily
WHERE organization_id = %(org_id)s
  AND store_id = %(store_id)s
  AND source = 'jetpack'
  AND traffic_date >= %(start_date)s
GROUP BY traffic_date
ORDER BY traffic_date
"""

PRODUCT_ORDERS_QUERY = """
SELECT oi.product_id AS entity_id,
       o.order_created_at::date AS ds,
       COUNT(DISTINCT o.id) AS order_count,
       COALESCE(SUM(oi.line_total), 0) AS revenue,
       COALESCE(SUM(oi.quantity), 0) AS units
FROM sp_orders o
JOIN sp_order_items oi ON oi.order_id = o.id
WHERE o.organization_id = %(org_id)s
  AND o.store_id = %(store_id)s
  AND oi.product_id IS NOT NULL
  AND o.order_created_at >= %(start_date)s
GROUP BY oi.product_id, ds
ORDER BY entity_id, ds
"""

ITEM_ORDERS_QUERY = """
SELECT c.item_id AS entity_id,
       o.order_created_at::date AS ds,
       COUNT(DISTINCT o.id) AS order_count,
       COALESCE(SUM(o.total), 0) AS revenue
FROM sp_orders o
JOIN sp_order_item_components c ON c.order_id = o.id
WHERE o.organization_id = %(org_id)s
  AND o.store_id = %(store_id)s
  AND c.item_id IS NOT NULL
  AND o.order_created_at >= %(start_date)s
GROUP BY c.item_id, ds
ORDER BY entity_id, ds
"""

PRODUCT_RETURNS_QUERY = """
SELECT oi.product_id AS entity_id,
       o.returned_at::date AS ds,
       COALESCE(SUM(oi.quantity), 0) AS returned
FROM sp_orders o
JOIN sp_order_items oi ON oi.order_id = o.id
WHERE o.organization_id = %(org_id)s
  AND o.store_id = %(store_id)s
  AND oi.product_id IS NOT NULL
  AND o.returned_at IS NOT NULL
  AND o.returned_at >= %(start_date)s
  AND o.status IN ('returned', 'returned-charge')
GROUP BY oi.product_id, ds
ORDER BY entity_id, ds
"""

STOCK_QUERY = """
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
    WHERE organization_id = %(org_id)s
      AND COALESCE(occurred_at, created_at) >= %(start_date)s
      AND (item_id IS NOT NULL OR product_id IS NOT NULL)
) latest
WHERE rn = 1
ORDER BY ds
"""

RESERVED_QUERY = """
SELECT date_trunc('day', COALESCE(occurred_at, created_at))::date AS ds,
       item_id,
       product_id,
       COALESCE(SUM(reserved_delta), 0) AS reserved
FROM sp_item_movements
WHERE organization_id = %(org_id)s
  AND movement_type = 'reserve'
  AND COALESCE(occurred_at, created_at) >= %(start_date)s
GROUP BY ds, item_id, product_id
ORDER BY ds
"""


def _frame(conn: Connection, query: str, params: dict[str, Any]) -> pd.DataFrame:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return pd.DataFrame(rows)


def extract_all(
    conn: Connection,
    organization_id: int,
    store_id: int,
    start_date: date,
) -> dict[str, pd.DataFrame]:
    """Extract every Phase 5 dataset through a direct Postgres connection."""
    params = {
        "org_id": organization_id,
        "store_id": store_id,
        "start_date": start_date,
    }

    from app.etl.queries import CONSUMPTION_QUERY

    frames = {
        "entities": _frame(conn, ENTITIES_QUERY, params),
        "consumption": _frame(
            conn,
            CONSUMPTION_QUERY.replace(":org_id", "%(org_id)s").replace(
                ":start_date", "%(start_date)s"
            ),
            params,
        ),
        "traffic": _frame(conn, TRAFFIC_QUERY, params),
        "site": _frame(conn, SITE_QUERY, params),
        "referrers": _frame(conn, REFERRERS_QUERY, params),
        "product_orders": _frame(conn, PRODUCT_ORDERS_QUERY, params),
        "item_orders": _frame(conn, ITEM_ORDERS_QUERY, params),
        "product_returns": _frame(conn, PRODUCT_RETURNS_QUERY, params),
        "stock": _frame(conn, STOCK_QUERY, params),
        "reserved": _frame(conn, RESERVED_QUERY, params),
    }

    for name, frame in frames.items():
        if "ds" in frame.columns and not frame.empty:
            frames[name]["ds"] = pd.to_datetime(frame["ds"])

    traffic = frames["traffic"]
    if not traffic.empty:
        frames["traffic"] = traffic.sort_values(["product_id", "ds", "source"]).drop_duplicates(
            ["product_id", "ds"], keep="first"
        )

    return frames
