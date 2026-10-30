"""Idempotent writers for the ML feature tables."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

UPSERT_DAILY_FEATURES = """
INSERT INTO ml_daily_features (
    organization_id, store_id, entity_type, entity_id, feature_date,
    units_consumed, units_returned, units_reserved, order_count, revenue, avg_order_value,
    stock_on_hand, stock_reserved, stock_available,
    page_views, add_to_cart, cart_removals, sessions, unique_visitors, traffic_sources,
    ad_spend, ad_impressions, ad_clicks,
    is_weekend, is_holiday, holiday_name, days_to_next_holiday,
    is_payday, is_festival_period, festival_name,
    created_at, updated_at
) VALUES (
    :organization_id, :store_id, :entity_type, :entity_id, :feature_date,
    :units_consumed, :units_returned, :units_reserved, :order_count, :revenue, :avg_order_value,
    :stock_on_hand, :stock_reserved, :stock_available,
    :page_views, :add_to_cart, :cart_removals, :sessions, :unique_visitors,
    CAST(:traffic_sources AS jsonb),
    :ad_spend, :ad_impressions, :ad_clicks,
    :is_weekend, :is_holiday, :holiday_name, :days_to_next_holiday,
    :is_payday, :is_festival_period, :festival_name,
    NOW(), NOW()
)
ON CONFLICT (organization_id, store_id, entity_type, entity_id, feature_date)
DO UPDATE SET
    units_consumed = EXCLUDED.units_consumed,
    units_returned = EXCLUDED.units_returned,
    units_reserved = EXCLUDED.units_reserved,
    order_count = EXCLUDED.order_count,
    revenue = EXCLUDED.revenue,
    avg_order_value = EXCLUDED.avg_order_value,
    stock_on_hand = EXCLUDED.stock_on_hand,
    stock_reserved = EXCLUDED.stock_reserved,
    stock_available = EXCLUDED.stock_available,
    page_views = EXCLUDED.page_views,
    add_to_cart = EXCLUDED.add_to_cart,
    cart_removals = EXCLUDED.cart_removals,
    sessions = EXCLUDED.sessions,
    unique_visitors = EXCLUDED.unique_visitors,
    traffic_sources = EXCLUDED.traffic_sources,
    ad_spend = EXCLUDED.ad_spend,
    ad_impressions = EXCLUDED.ad_impressions,
    ad_clicks = EXCLUDED.ad_clicks,
    is_weekend = EXCLUDED.is_weekend,
    is_holiday = EXCLUDED.is_holiday,
    holiday_name = EXCLUDED.holiday_name,
    days_to_next_holiday = EXCLUDED.days_to_next_holiday,
    is_payday = EXCLUDED.is_payday,
    is_festival_period = EXCLUDED.is_festival_period,
    festival_name = EXCLUDED.festival_name,
    updated_at = NOW()
"""


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _traffic_sources(row: pd.Series) -> str:
    page_views = _integer(row.get("page_views"))
    source = row.get("traffic_source")

    if page_views <= 0 or source is None or pd.isna(source):
        return "{}"

    return json.dumps({str(source): page_views})


async def upsert_daily_features(
    session: AsyncSession,
    organization_id: int,
    store_id: int,
    entity_type: str,
    entity_id: int,
    features: pd.DataFrame,
) -> int:
    """UPSERT one entity's daily feature matrix; returns the row count."""
    if features.empty:
        return 0

    rows: list[dict[str, Any]] = []

    for _, row in features.iterrows():
        rows.append(
            {
                "organization_id": organization_id,
                "store_id": store_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "feature_date": _date(row["ds"]),
                "units_consumed": _number(row.get("net_consumed")) or 0,
                "units_returned": _number(row.get("returned")) or 0,
                "units_reserved": _number(row.get("units_reserved")) or 0,
                "order_count": _integer(row.get("order_count")),
                "revenue": _number(row.get("revenue")) or 0,
                "avg_order_value": _number(row.get("avg_order_value")) or 0,
                "stock_on_hand": _number(row.get("stock_on_hand")),
                "stock_reserved": _number(row.get("stock_reserved")),
                "stock_available": _number(row.get("stock_available")),
                "page_views": _integer(row.get("page_views")),
                "add_to_cart": _integer(row.get("add_to_cart")),
                "cart_removals": _integer(row.get("cart_removals")),
                "sessions": _integer(row.get("sessions")),
                "unique_visitors": _integer(row.get("unique_visitors")),
                "traffic_sources": _traffic_sources(row),
                "ad_spend": None,
                "ad_impressions": None,
                "ad_clicks": None,
                "is_weekend": bool(row.get("is_weekend", False)),
                "is_holiday": bool(row.get("is_holiday", False)),
                "holiday_name": row.get("holiday_name"),
                "days_to_next_holiday": row.get("days_to_next_holiday"),
                "is_payday": bool(row.get("is_payday", False)),
                "is_festival_period": bool(row.get("is_festival_period", False)),
                "festival_name": row.get("festival_name"),
            }
        )

    await session.execute(text(UPSERT_DAILY_FEATURES), rows)

    return len(rows)
