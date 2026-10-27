"""Phase 1 — read-only data collection, extraction and coverage audit.

Runs against the live StockPilot Postgres database (``DATABASE_URL``) and the
three Jetpack traffic exports. Writes a machine-readable JSON report and a
human-readable Markdown report. It never writes to the database.

Usage::

    DATABASE_URL=postgresql://... python -m app.etl.audit --org 1 \
        --traffic-dir ../web-traffic-data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import session_scope
from app.etl.extractors import (
    extract_bom,
    extract_consumption,
    extract_order_history,
    extract_stock_levels,
)
from app.etl.traffic import overlap_window, validate_traffic_dir

TABLE_NAMES = (
    "sp_organizations",
    "sp_items",
    "sp_products",
    "sp_bom_lines",
    "sp_orders",
    "sp_order_items",
    "sp_order_item_components",
    "sp_item_movements",
    "sp_webhook_events",
    "sp_store_connections",
    "sp_movement_exclusions",
    "sp_demand_forecasts",
    "sp_stockout_risks",
    "sp_restock_recommendations",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, Decimal)):
        rounded = round(float(value), 4)
        return f"{rounded:g}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(cell) for cell in row) + " |")
    return "\n".join(lines)


async def _one(session: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    result = await session.execute(text(sql), params or {})
    row = result.mappings().first()
    return dict(row) if row else {}


async def _all(session: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params or {})
    return [dict(row) for row in result.mappings().all()]


async def _scalar(session: AsyncSession, sql: str, params: dict[str, Any] | None = None) -> Any:
    result = await session.execute(text(sql), params or {})
    return result.scalar()


async def collect(org_id: int, start_date: date, traffic_dir: Path) -> dict[str, Any]:
    snapshot = datetime.now(timezone.utc)
    params = {"org_id": org_id}

    async with session_scope() as session:
        db = await _one(
            session,
            "SELECT version() AS version, current_setting('TimeZone') AS timezone",
        )
        app_timezone = "UTC"

        table_counts = {}
        for table in TABLE_NAMES:
            try:
                table_counts[table] = int(await _scalar(session, f"SELECT count(*) FROM {table}"))
            except Exception as exc:  # table may not exist on an older schema
                table_counts[table] = f"error: {exc}"

        orders_summary = await _one(
            session,
            """
            SELECT min(order_created_at) AS first_order_at,
                   max(order_created_at) AS last_order_at,
                   count(*) AS orders,
                   count(*) FILTER (WHERE order_created_at IS NULL) AS undated
            FROM sp_orders
            WHERE organization_id = :org_id
            """,
            params,
        )
        first_order = await _one(
            session,
            """
            SELECT id, woocommerce_id, woocommerce_number, status, order_created_at
            FROM sp_orders
            WHERE organization_id = :org_id AND order_created_at IS NOT NULL
            ORDER BY order_created_at ASC LIMIT 1
            """,
            params,
        )
        last_order = await _one(
            session,
            """
            SELECT id, woocommerce_id, woocommerce_number, status, order_created_at
            FROM sp_orders
            WHERE organization_id = :org_id AND order_created_at IS NOT NULL
            ORDER BY order_created_at DESC LIMIT 1
            """,
            params,
        )
        numbers = await _one(
            session,
            """
            SELECT min(nullif(regexp_replace(woocommerce_number, '\\D', '', 'g'), '')::bigint) AS min_number,
                   max(nullif(regexp_replace(woocommerce_number, '\\D', '', 'g'), '')::bigint) AS max_number
            FROM sp_orders
            WHERE organization_id = :org_id
            """,
            params,
        )
        orders_by_month = await _all(
            session,
            """
            SELECT to_char(date_trunc('month', order_created_at), 'YYYY-MM') AS month,
                   count(*) AS orders,
                   coalesce(sum(total), 0) AS revenue
            FROM sp_orders
            WHERE organization_id = :org_id AND order_created_at IS NOT NULL
            GROUP BY 1 ORDER BY 1
            """,
            params,
        )
        orders_by_status = await _all(
            session,
            "SELECT status, count(*) AS orders FROM sp_orders WHERE organization_id = :org_id GROUP BY 1 ORDER BY 2 DESC",
            params,
        )
        before_cutover = int(
            await _scalar(
                session,
                "SELECT count(*) FROM sp_orders WHERE organization_id = :org_id AND order_created_at < :cutover",
                {**params, "cutover": date(2026, 8, 2)},
            )
        )
        after_cutover = int(
            await _scalar(
                session,
                "SELECT count(*) FROM sp_orders WHERE organization_id = :org_id AND order_created_at >= :cutover",
                {**params, "cutover": date(2026, 8, 2)},
            )
        )

        movements_summary = await _one(
            session,
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE occurred_at IS NULL) AS missing_occurred_at,
                   min(created_at) AS first_created_at,
                   max(created_at) AS last_created_at,
                   min(occurred_at) AS first_occurred_at,
                   max(occurred_at) AS last_occurred_at
            FROM sp_item_movements
            WHERE organization_id = :org_id
            """,
            params,
        )
        movements_by_type = await _all(
            session,
            """
            SELECT movement_type,
                   count(*) AS rows,
                   coalesce(sum(qty_delta), 0) AS qty_delta,
                   coalesce(sum(reserved_delta), 0) AS reserved_delta
            FROM sp_item_movements
            WHERE organization_id = :org_id
            GROUP BY 1 ORDER BY 1
            """,
            params,
        )
        consumption_summary = await _one(
            session,
            """
            SELECT count(*) AS rows,
                   coalesce(sum(abs(qty_delta)), 0) AS units,
                   min(coalesce(occurred_at, created_at))::date AS first_day,
                   max(coalesce(occurred_at, created_at))::date AS last_day
            FROM sp_item_movements
            WHERE organization_id = :org_id
              AND movement_type = 'confirm'
              AND qty_delta < 0
            """,
            params,
        )
        consumption_filtered = await _one(
            session,
            """
            SELECT count(*) AS rows,
                   coalesce(sum(abs(m.qty_delta)), 0) AS units
            FROM sp_item_movements m
            WHERE m.organization_id = :org_id
              AND m.movement_type = 'confirm'
              AND m.qty_delta < 0
              AND NOT EXISTS (
                  SELECT 1 FROM sp_movement_exclusions e WHERE e.movement_id = m.id
              )
            """,
            params,
        )
        consumption_by_month = await _all(
            session,
            """
            SELECT to_char(date_trunc('month', coalesce(occurred_at, created_at)), 'YYYY-MM') AS month,
                   count(*) AS rows,
                   coalesce(sum(abs(qty_delta)), 0) AS units
            FROM sp_item_movements
            WHERE organization_id = :org_id
              AND movement_type = 'confirm'
              AND qty_delta < 0
            GROUP BY 1 ORDER BY 1
            """,
            params,
        )

        webhook_status = await _all(
            session,
            "SELECT status, count(*) AS events FROM sp_webhook_events WHERE organization_id = :org_id GROUP BY 1 ORDER BY 2 DESC",
            params,
        )
        latest_plugin_event = await _one(
            session,
            """
            SELECT id, event_id, event_type, wc_status, status, occurred_at, created_at, processed_at
            FROM sp_webhook_events
            WHERE organization_id = :org_id
              AND event_id NOT LIKE 'reprocess-%'
              AND event_id NOT LIKE 'ghost-release-%'
            ORDER BY id DESC LIMIT 1
            """,
            params,
        )
        received_summary = await _one(
            session,
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE event_id LIKE 'reprocess-%') AS reprocess,
                   count(*) FILTER (WHERE event_id NOT LIKE 'reprocess-%') AS other
            FROM sp_webhook_events
            WHERE organization_id = :org_id AND status = 'received'
            """,
            params,
        )
        received_by_status = await _all(
            session,
            """
            SELECT wc_status, count(*) AS events
            FROM sp_webhook_events
            WHERE organization_id = :org_id AND status = 'received'
            GROUP BY 1 ORDER BY 2 DESC
            """,
            params,
        )
        organic_24h = int(
            await _scalar(
                session,
                """
                SELECT count(*) FROM sp_webhook_events
                WHERE organization_id = :org_id
                  AND created_at >= :since
                  AND event_id NOT LIKE 'reprocess-%'
                  AND event_id NOT LIKE 'ghost-release-%'
                """,
                {**params, "since": snapshot - timedelta(days=1)},
            )
        )
        organic_7d = int(
            await _scalar(
                session,
                """
                SELECT count(*) FROM sp_webhook_events
                WHERE organization_id = :org_id
                  AND created_at >= :since
                  AND event_id NOT LIKE 'reprocess-%'
                  AND event_id NOT LIKE 'ghost-release-%'
                """,
                {**params, "since": snapshot - timedelta(days=7)},
            )
        )
        jobs = {}
        for table in ("jobs", "failed_jobs"):
            try:
                jobs[table] = int(await _scalar(session, f"SELECT count(*) FROM {table}"))
            except Exception:
                jobs[table] = None

        stores = await _all(
            session,
            """
            SELECT id, name, last_seen_at, last_sync_at, plugin_version, sync_count
            FROM sp_store_connections
            WHERE organization_id = :org_id
            ORDER BY id
            """,
            params,
        )

        forecast_counts = {
            table: int(await _scalar(session, f"SELECT count(*) FROM {table} WHERE organization_id = :org_id", params))
            for table in (
                "sp_demand_forecasts",
                "sp_stockout_risks",
                "sp_restock_recommendations",
            )
        }
        forecast_columns = await _all(
            session,
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('sp_demand_forecasts', 'sp_stockout_risks', 'sp_restock_recommendations')
            ORDER BY table_name, ordinal_position
            """,
        )
        forecast_indexes = await _all(
            session,
            """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN ('sp_demand_forecasts', 'sp_stockout_risks', 'sp_restock_recommendations')
            ORDER BY 1, 2
            """,
        )
        forecast_unique_key = any(
            "UNIQUE" in str(index.get("indexdef", ""))
            and all(
                column in str(index.get("indexdef", ""))
                for column in ("organization_id", "entity_type", "entity_id", "forecast_date")
            )
            for index in forecast_indexes
            if index.get("tablename") == "sp_demand_forecasts"
        )

        bom_by_type = await _all(
            session,
            "SELECT component_type, count(*) AS rows FROM sp_bom_lines WHERE organization_id = :org_id GROUP BY 1 ORDER BY 1",
            params,
        )
        stock_rows = await extract_stock_levels(session, org_id)
        stock_summary = {
            "rows": len(stock_rows),
            "items": sum(1 for row in stock_rows if row["entity_type"] == "item"),
            "products": sum(1 for row in stock_rows if row["entity_type"] == "product"),
            "on_hand": sum(float(row["quantity_on_hand"] or 0) for row in stock_rows),
            "reserved": sum(float(row["quantity_reserved"] or 0) for row in stock_rows),
            "available": sum(float(row["quantity_available"] or 0) for row in stock_rows),
            "negative_available": sum(1 for row in stock_rows if float(row["quantity_available"] or 0) < 0),
        }

        products = await _all(
            session,
            """
            SELECT id, name, woo_product_id, deleted_at
            FROM sp_products
            WHERE organization_id = :org_id
            ORDER BY id
            """,
            params,
        )

    # Extractors (one round-trip each; the audit is not on a hot path).
    async with session_scope() as session:
        consumption_rows = await extract_consumption(session, org_id, start_date)
        order_rows = await extract_order_history(session, org_id, start_date)
        bom_rows = await extract_bom(session, org_id)
        stock_extract_rows = await extract_stock_levels(session, org_id)

    consumption_dates = [row["ds"] for row in consumption_rows]
    order_dates = [row["order_created_at"] for row in order_rows if row.get("order_created_at")]
    consumption_units = sum(float(row["net_consumed"] or 0) for row in consumption_rows)

    extractors = {
        "consumption": {
            "rows": len(consumption_rows),
            "net_units": consumption_units,
            "date_min": min(consumption_dates).isoformat() if consumption_dates else None,
            "date_max": max(consumption_dates).isoformat() if consumption_dates else None,
            "item_rows": sum(1 for row in consumption_rows if row.get("item_id") is not None),
            "product_rows": sum(1 for row in consumption_rows if row.get("product_id") is not None),
        },
        "order_history": {
            "rows": len(order_rows),
            "total_units": sum(float(row["total_units"] or 0) for row in order_rows),
            "line_revenue": sum(float(row["line_revenue"] or 0) for row in order_rows),
            "date_min": min(order_dates).isoformat() if order_dates else None,
            "date_max": max(order_dates).isoformat() if order_dates else None,
        },
        "bom": {"rows": len(bom_rows)},
        "stock_levels": {"rows": len(stock_extract_rows)},
    }

    traffic = validate_traffic_dir(traffic_dir, products)

    first_order_at = orders_summary.get("first_order_at")
    last_order_at = orders_summary.get("last_order_at")
    product_first = traffic.get("product_traffic", {}).get("date_min")
    product_last = traffic.get("product_traffic", {}).get("date_max")
    site_first = traffic.get("site_traffic", {}).get("first_nonzero_date")
    site_last = traffic.get("site_traffic", {}).get("date_max")

    def _date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and value:
            return date.fromisoformat(value[:10])
        return None

    product_overlap = overlap_window(_date(first_order_at), _date(last_order_at), _date(product_first), _date(product_last))
    site_overlap = overlap_window(_date(first_order_at), _date(last_order_at), _date(site_first), _date(site_last))

    min_number = numbers.get("min_number")
    max_number = numbers.get("max_number")
    number_span = int(max_number) - int(min_number) + 1 if min_number is not None and max_number is not None else None
    orders_count = int(orders_summary.get("orders") or 0)

    warnings: list[str] = []
    if movements_summary.get("missing_occurred_at"):
        warnings.append(
            f"{int(movements_summary['missing_occurred_at'])} movements have no occurred_at (pre-Phase 0B history); "
            "extractors fall back to created_at for those rows."
        )
    if received_summary.get("other"):
        warnings.append(
            f"{int(received_summary['other'])} old plugin events remain status=received (pre-queue-fix backlog); "
            "do not replay them blindly."
        )
    if received_summary.get("reprocess"):
        warnings.append(
            f"{int(received_summary['reprocess'])} manual reprocess events remain status=received; the engine ran, "
            "but runReprocess does not update the event status."
        )
    if not forecast_unique_key:
        warnings.append(
            "sp_demand_forecasts has no unique key on (organization_id, entity_type, entity_id, forecast_date); "
            "Phase 4 must add it before upserts."
        )
    if first_order_at and product_overlap.get("start") and product_overlap["start"] > str(first_order_at)[:10]:
        warnings.append("The usable product-traffic window starts after the first order.")
    if traffic.get("product_traffic", {}).get("mapping", {}).get("unmatched"):
        warnings.append(
            f"{len(traffic['product_traffic']['mapping']['unmatched'])} traffic product groups could not be mapped to a product."
        )
    if traffic.get("product_traffic", {}).get("mapping", {}).get("products_without_traffic"):
        warnings.append(
            f"{len(traffic['product_traffic']['mapping']['products_without_traffic'])} products have no product-page traffic rows."
        )
    if stock_summary.get("negative_available"):
        warnings.append(f"{stock_summary['negative_available']} entities have negative available stock.")

    report = {
        "phase": 1,
        "generated_at": snapshot.isoformat(),
        "organization_id": org_id,
        "start_date": start_date.isoformat(),
        "database": db,
        "app_timezone": app_timezone,
        "table_counts": table_counts,
        "orders": {
            "summary": orders_summary,
            "first": first_order,
            "last": last_order,
            "numbers": {"min": min_number, "max": max_number, "span": number_span, "missing_estimate": (number_span - orders_count) if number_span else None},
            "before_2026_08_02": before_cutover,
            "from_2026_08_02": after_cutover,
            "by_month": orders_by_month,
            "by_status": orders_by_status,
        },
        "movements": {
            "summary": movements_summary,
            "by_type": movements_by_type,
            "consumption": consumption_summary,
            "consumption_filtered": consumption_filtered,
            "consumption_by_month": consumption_by_month,
        },
        "webhooks": {
            "status": webhook_status,
            "latest_plugin_event": latest_plugin_event,
            "received": received_summary,
            "received_by_status": received_by_status,
            "organic_last_24h": organic_24h,
            "organic_last_7d": organic_7d,
            "jobs": jobs,
        },
        "stores": stores,
        "forecast": {
            "counts": forecast_counts,
            "columns": forecast_columns,
            "indexes": forecast_indexes,
            "has_unique_forecast_key": forecast_unique_key,
        },
        "bom": {"by_type": bom_by_type, "total": sum(int(row["rows"]) for row in bom_by_type)},
        "stock": stock_summary,
        "extractors": extractors,
        "traffic": traffic,
        "coverage": {
            "product_traffic_overlap": product_overlap,
            "site_traffic_overlap": site_overlap,
        },
        "warnings": warnings,
    }

    return report


def _markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# DemandIQ — Phase 1 Data Audit")
    lines.append("")
    lines.append(f"**Snapshot:** {report['generated_at']}  ")
    lines.append(f"**Organization:** {report['organization_id']}  ")
    lines.append(f"**Extraction start:** {report['start_date']}")
    lines.append("")

    db = report["database"]
    lines.append("## Database")
    lines.append("")
    lines.append(f"- **Version:** {db.get('version', '—')}")
    lines.append(f"- **DB timezone:** {db.get('timezone', '—')}")
    lines.append(f"- **App timezone:** {report['app_timezone']}")
    lines.append("")
    lines.append(_table(["table", "rows"], [[name, count] for name, count in report["table_counts"].items()]))
    lines.append("")

    orders = report["orders"]
    lines.append("## Orders")
    lines.append("")
    lines.append(f"- **Window:** {orders['summary'].get('first_order_at')} → {orders['summary'].get('last_order_at')}")
    lines.append(f"- **Orders:** {orders['summary'].get('orders')} (undated: {orders['summary'].get('undated')})")
    numbers = orders["numbers"]
    if numbers.get("min") is not None:
        lines.append(
            f"- **Woo number range:** {numbers['min']} → {numbers['max']} "
            f"({numbers['span']} numbers; {orders['summary'].get('orders')} rows; "
            f"~{numbers.get('missing_estimate')} missing)"
        )
    lines.append(f"- **Before 2026-08-02:** {orders['before_2026_08_02']} · **from 2026-08-02:** {orders['from_2026_08_02']}")
    lines.append("")
    lines.append(_table(["month", "orders", "revenue"], [[r["month"], r["orders"], r["revenue"]] for r in orders["by_month"]]))
    lines.append("")

    movements = report["movements"]
    lines.append("## Movements")
    lines.append("")
    lines.append(f"- **Total:** {movements['summary'].get('total')} · **missing occurred_at:** {movements['summary'].get('missing_occurred_at')}")
    lines.append(f"- **Created:** {movements['summary'].get('first_created_at')} → {movements['summary'].get('last_created_at')}")
    lines.append(f"- **Occurred:** {movements['summary'].get('first_occurred_at')} → {movements['summary'].get('last_occurred_at')}")
    lines.append("")
    lines.append(_table(["movement type", "rows", "qty delta", "reserved delta"], [[r["movement_type"], r["rows"], r["qty_delta"], r["reserved_delta"]] for r in movements["by_type"]]))
    lines.append("")
    cons = movements["consumption"]
    filtered = movements["consumption_filtered"]
    lines.append(f"- **Consumption (confirm, qty<0):** {cons.get('rows')} rows · {cons.get('units')} units · {cons.get('first_day')} → {cons.get('last_day')}")
    lines.append(f"- **After exclusions:** {filtered.get('rows')} rows · {filtered.get('units')} units")
    lines.append("")
    lines.append(_table(["month", "rows", "units"], [[r["month"], r["rows"], r["units"]] for r in movements["consumption_by_month"]]))
    lines.append("")

    webhooks = report["webhooks"]
    lines.append("## Data flow health")
    lines.append("")
    lines.append(_table(["webhook status", "events"], [[r["status"], r["events"]] for r in webhooks["status"]]))
    lines.append("")
    latest = webhooks["latest_plugin_event"]
    if latest:
        lines.append(
            f"- **Latest plugin event:** #{latest.get('id')} `{latest.get('event_id')}` "
            f"{latest.get('wc_status')} · {latest.get('status')} · occurred {latest.get('occurred_at')}"
        )
    lines.append(f"- **Organic events (24h / 7d):** {webhooks['organic_last_24h']} / {webhooks['organic_last_7d']}")
    received = webhooks["received"]
    lines.append(f"- **Received backlog:** {received.get('total')} ({received.get('reprocess')} reprocess artifacts, {received.get('other')} old plugin events)")
    lines.append(f"- **Queue jobs / failed jobs:** {webhooks['jobs'].get('jobs')} / {webhooks['jobs'].get('failed_jobs')}")
    for store in report["stores"]:
        lines.append(
            f"- **Store `{store.get('name')}`:** last seen {store.get('last_seen_at')} · last sync {store.get('last_sync_at')} "
            f"· plugin {store.get('plugin_version') or '—'} · syncs {store.get('sync_count')}"
        )
    lines.append("")

    forecast = report["forecast"]
    lines.append("## Forecast schema")
    lines.append("")
    lines.append(_table(["table", "rows"], [[name, count] for name, count in forecast["counts"].items()]))
    lines.append("")
    lines.append(f"- **Unique forecast key present:** {forecast['has_unique_forecast_key']}")
    lines.append(f"- **Forecast columns:** {len(forecast['columns'])} · **indexes:** {len(forecast['indexes'])}")
    lines.append("")

    bom = report["bom"]
    stock = report["stock"]
    lines.append("## BOM & stock")
    lines.append("")
    lines.append(_table(["component type", "lines"], [[r["component_type"], r["rows"]] for r in bom["by_type"]]))
    lines.append("")
    lines.append(
        f"- **Stock rows:** {stock['rows']} ({stock['items']} items, {stock['products']} products) · "
        f"on hand {stock['on_hand']} · reserved {stock['reserved']} · available {stock['available']} · "
        f"negative available {stock['negative_available']}"
    )
    lines.append("")

    extractors = report["extractors"]
    lines.append("## Extractors")
    lines.append("")
    lines.append(_table(
        ["extractor", "rows", "date range", "notes"],
        [
            ["consumption", extractors["consumption"]["rows"], f"{extractors['consumption']['date_min']} → {extractors['consumption']['date_max']}", f"net units {extractors['consumption']['net_units']}"],
            ["order_history", extractors["order_history"]["rows"], f"{extractors['order_history']['date_min']} → {extractors['order_history']['date_max']}", f"units {extractors['order_history']['total_units']}"],
            ["bom", extractors["bom"]["rows"], "—", "item + product lines"],
            ["stock_levels", extractors["stock_levels"]["rows"], "current", "items + products"],
        ],
    ))
    lines.append("")

    traffic = report["traffic"]
    lines.append("## Traffic exports")
    lines.append("")
    export_rows = []
    for name, export in traffic.get("exports", {}).items():
        if "error" in export:
            export_rows.append([name, "—", "—", "—", "—", export["error"]])
        else:
            export_rows.append([
                name,
                export.get("rows"),
                f"{export.get('date_min')} → {export.get('date_max')}",
                export.get("bom"),
                export.get("invalid_date_count"),
                f"duplicate keys: {export.get('duplicate_keys')}",
            ])
    lines.append(_table(["file", "rows", "date range", "BOM", "invalid dates", "notes"], export_rows))
    lines.append("")

    product_traffic = traffic.get("product_traffic", {})
    if product_traffic:
        mapping = product_traffic.get("mapping", {})
        counts = mapping.get("match_counts", {})
        lines.append(f"- **Product rows:** {product_traffic.get('rows')} · **product groups:** {product_traffic.get('groups')}")
        lines.append(
            f"- **Mapped:** {counts.get('woo_id', 0)} by woo id, {counts.get('title', 0)} by title, "
            f"{counts.get('slug', 0)} by slug · **unmatched:** {len(mapping.get('unmatched', []))}"
        )
        lines.append(f"- **Products without product traffic:** {len(mapping.get('products_without_traffic', []))}")
        for item in mapping.get("unmatched", []):
            lines.append(f"  - unmatched: {item['post_id']} — {item['title']} ({item['views']} views)")
        for item in mapping.get("products_without_traffic", []):
            lines.append(f"  - no traffic: {item['product_id']} ({item['woo_product_id']}) — {item['name']}")
        lines.append("")

    site = traffic.get("site_traffic", {})
    if site:
        lines.append(
            f"- **Site traffic:** {site.get('rows')} days · {site.get('date_min')} → {site.get('date_max')} · "
            f"{site.get('total_views')} views / {site.get('total_visitors')} visitors · "
            f"{site.get('zero_days')} zero days · first non-zero {site.get('first_nonzero_date')}"
        )
    referrers = traffic.get("referrers", {})
    if referrers:
        lines.append(f"- **Referrers:** {referrers.get('rows')} rows · {referrers.get('groups')} groups")
        for item in referrers.get("top", [])[:5]:
            lines.append(f"  - {item['referrer']}: {item['views']} views")
    lines.append("")

    coverage = report["coverage"]
    lines.append("## Usable overlap")
    lines.append("")
    lines.append(_table(
        ["scope", "start", "end", "days", "valid"],
        [
            ["product traffic", coverage["product_traffic_overlap"]["start"], coverage["product_traffic_overlap"]["end"], coverage["product_traffic_overlap"]["days"], coverage["product_traffic_overlap"]["valid"]],
            ["site traffic", coverage["site_traffic_overlap"]["start"], coverage["site_traffic_overlap"]["end"], coverage["site_traffic_overlap"]["days"], coverage["site_traffic_overlap"]["valid"]],
        ],
    ))
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    if report["warnings"]:
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    else:
        lines.append("- No blocking findings.")
    lines.append("")

    return "\n".join(lines)


def write_reports(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, default=_json_default) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DemandIQ Phase 1 read-only data audit")
    parser.add_argument("--org", type=int, default=1, help="organization id (default: 1)")
    parser.add_argument("--start", type=date.fromisoformat, default=date.today() - timedelta(days=180), help="extraction start date (YYYY-MM-DD)")
    parser.add_argument("--traffic-dir", type=Path, default=Path(__file__).resolve().parents[3] / "web-traffic-data", help="directory containing the three Jetpack CSVs")
    parser.add_argument("--out", type=Path, default=Path("reports/phase1-audit.json"), help="JSON report path")
    parser.add_argument("--markdown", type=Path, default=Path("reports/phase1-audit.md"), help="Markdown report path")
    parser.add_argument("--dsn", default=None, help="override DATABASE_URL for this run")
    args = parser.parse_args(argv)

    if args.dsn:
        os.environ["DATABASE_URL"] = args.dsn
        get_settings.cache_clear()

    try:
        report = asyncio.run(collect(args.org, args.start, args.traffic_dir))
    except Exception as exc:  # surface the real connection/query error
        print(f"Phase 1 audit failed: {exc}", flush=True)
        return 1

    write_reports(report, args.out, args.markdown)

    orders = report["orders"]["summary"]
    mapping = report["traffic"].get("product_traffic", {}).get("mapping", {})
    counts = mapping.get("match_counts", {})
    coverage = report["coverage"]["product_traffic_overlap"]
    print("Phase 1 audit — read only")
    print(f"Snapshot: {report['generated_at']}")
    print(f"DB: {report['database'].get('version', '—')} · timezone {report['database'].get('timezone', '—')}")
    print(f"Orders: {orders.get('orders')} · {orders.get('first_order_at')} → {orders.get('last_order_at')}")
    print(
        f"Consumption extractor: {report['extractors']['consumption']['rows']} rows · "
        f"{report['extractors']['consumption']['net_units']} net units"
    )
    print(
        f"Traffic mapping: {counts.get('woo_id', 0)}/{mapping.get('traffic_product_ids', 0)} by woo id · "
        f"unmatched {len(mapping.get('unmatched', []))} · "
        f"products without traffic {len(mapping.get('products_without_traffic', []))}"
    )
    print(f"Product-traffic overlap: {coverage['start']} → {coverage['end']} ({coverage['days']} days)")
    print(f"JSON: {args.out}")
    print(f"Markdown: {args.markdown}")
    if report["warnings"]:
        print("Findings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
