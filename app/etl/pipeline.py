"""Phase 5 ETL: raw Supabase tables -> zero-filled daily features.

Reads and writes go through psql COPY because the Supabase Supavisor pooler is
unreliable with asyncpg prepared statements in this environment; the feature
math stays in pandas.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.etl.features import build_daily_features
from app.etl.psql import extract_all, write_features
from app.etl.quality import validate_features


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int:
    number = _number(value)
    return int(number) if number is not None else 0


def _feature_rows(
    features: pd.DataFrame,
    organization_id: int,
    store_id: int,
    entity_type: str,
    entity_id: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for _, row in features.iterrows():
        page_views = _integer(row.get("page_views"))
        source = row.get("traffic_source")
        traffic_sources: dict[str, int] = {}

        if page_views > 0 and source is not None and not pd.isna(source):
            traffic_sources[str(source)] = page_views

        rows.append(
            {
                "organization_id": organization_id,
                "store_id": store_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "feature_date": pd.Timestamp(row["ds"]).date(),
                "units_consumed": _number(row.get("net_consumed")) or 0,
                "units_returned": _number(row.get("returned")) or 0,
                "units_reserved": _number(row.get("units_reserved")) or 0,
                "order_count": _integer(row.get("order_count")),
                "revenue": _number(row.get("revenue")) or 0,
                "avg_order_value": _number(row.get("avg_order_value")) or 0,
                "stock_on_hand": _number(row.get("stock_on_hand")),
                "stock_reserved": _number(row.get("stock_reserved")),
                "stock_available": _number(row.get("stock_available")),
                "page_views": page_views,
                "add_to_cart": _integer(row.get("add_to_cart")),
                "cart_removals": _integer(row.get("cart_removals")),
                "sessions": _integer(row.get("sessions")),
                "unique_visitors": _integer(row.get("unique_visitors")),
                "traffic_sources": traffic_sources,
                "ad_spend": None,
                "ad_impressions": None,
                "ad_clicks": None,
                "is_weekend": bool(row.get("is_weekend", False)),
                "is_holiday": False,
                "holiday_name": None,
                "days_to_next_holiday": None,
                "is_payday": False,
                "is_festival_period": False,
                "festival_name": None,
            }
        )

    return rows


def run_feature_pipeline(
    organization_id: int = 1,
    store_id: int = 1,
    lookback_days: int = 90,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build zero-filled daily features for every active item/product."""
    lookback_days = max(1, int(lookback_days))
    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days - 1)

    data = extract_all(organization_id, store_id, start_date)

    entities = data["entities"]
    consumption_all = data["consumption"]
    traffic_all = data["traffic"]
    site_df = data["site"]
    referrer_df = data["referrers"]
    product_orders_all = data["product_orders"]
    item_orders_all = data["item_orders"]
    product_returns_all = data["product_returns"]
    stock_all = data["stock"]
    reserved_all = data["reserved"]

    quality_reports: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    traffic_entities = 0

    for _, entity in entities.iterrows():
        entity_type = str(entity["entity_type"])
        entity_id = int(entity["id"])

        if entity_type == "product":
            product_orders = (
                product_orders_all[product_orders_all["entity_id"] == entity_id]
                if not product_orders_all.empty
                else pd.DataFrame(columns=["ds", "order_count", "revenue", "units"])
            )
            orders_df = product_orders[["ds", "order_count", "revenue"]].copy()

            returns_df = (
                product_returns_all[product_returns_all["entity_id"] == entity_id][["ds", "returned"]]
                if not product_returns_all.empty
                else pd.DataFrame(columns=["ds", "returned"])
            )

            if product_orders.empty:
                consumption_df = pd.DataFrame(columns=["ds", "consumed", "returned"])
            else:
                consumption_df = product_orders[["ds", "units"]].rename(columns={"units": "consumed"})
                consumption_df = consumption_df.merge(returns_df, on="ds", how="outer")

            entity_traffic = (
                traffic_all[traffic_all["product_id"] == entity_id]
                if not traffic_all.empty
                else pd.DataFrame()
            )
        else:
            item_orders = (
                item_orders_all[item_orders_all["entity_id"] == entity_id]
                if not item_orders_all.empty
                else pd.DataFrame(columns=["ds", "order_count", "revenue"])
            )
            orders_df = item_orders[["ds", "order_count", "revenue"]].copy()

            consumption_df = (
                consumption_all[consumption_all["item_id"] == entity_id][["ds", "consumed", "returned"]]
                if not consumption_all.empty
                else pd.DataFrame(columns=["ds", "consumed", "returned"])
            )
            entity_traffic = pd.DataFrame()

        entity_stock = (
            stock_all[stock_all["item_id"] == entity_id]
            if entity_type == "item" and not stock_all.empty
            else (
                stock_all[stock_all["product_id"] == entity_id]
                if entity_type == "product" and not stock_all.empty
                else pd.DataFrame(columns=["ds", "on_hand", "reserved"])
            )
        )
        entity_reserved = (
            reserved_all[reserved_all["item_id"] == entity_id]
            if entity_type == "item" and not reserved_all.empty
            else (
                reserved_all[reserved_all["product_id"] == entity_id]
                if entity_type == "product" and not reserved_all.empty
                else pd.DataFrame(columns=["ds", "reserved"])
            )
        )

        features = build_daily_features(
            consumption_df=consumption_df,
            orders_df=orders_df,
            stock_df=entity_stock[["ds", "on_hand", "reserved"]].copy(),
            traffic_df=entity_traffic,
            site_df=site_df,
            referrer_df=referrer_df,
            start_date=start_date,
            end_date=end_date,
            reserved_df=entity_reserved[["ds", "reserved"]].copy(),
        )

        quality = validate_features(features, entity_id, entity_type)
        quality_reports.append(quality)

        if not entity_traffic.empty and int(entity_traffic["page_views"].sum()) > 0:
            traffic_entities += 1

        rows.extend(_feature_rows(features, organization_id, store_id, entity_type, entity_id))

    rows_written = 0 if dry_run else write_features(rows)
    recommendations = Counter(report["recommended_model"] for report in quality_reports)

    return {
        "organization_id": organization_id,
        "store_id": store_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "dry_run": dry_run,
        "entities": len(entities),
        "items": sum(1 for value in entities["entity_type"] if value == "item"),
        "products": sum(1 for value in entities["entity_type"] if value == "product"),
        "traffic_entities": traffic_entities,
        "feature_rows": len(rows),
        "rows_written": rows_written,
        "entities_with_enough_data": sum(1 for report in quality_reports if report["has_enough_data"]),
        "model_recommendations": dict(recommendations),
        "quality": sorted(
            quality_reports,
            key=lambda report: (report["entity_type"], report["entity_id"]),
        ),
    }
