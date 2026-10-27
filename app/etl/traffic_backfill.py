"""Phase 3 — import the three Jetpack daily traffic exports.

The importer is idempotent (Postgres ON CONFLICT), resumable (progress rows in
``ml_backfill_jobs``), and never inserts an unmapped product row: those go to
``ml_traffic_mapping_misses`` for review.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.etl.traffic import parse_traffic_date, read_rows, url_slug
from app.etl.traffic_mapping import ProductIndex, load_products

POST_VIEWS_FILE = "daily_post_product_views.csv"
OVERVIEW_FILE = "daily_traffic_overview.csv"
REFERRERS_FILE = "daily_referrers.csv"

CART_POST_ID = 8
CHECKOUT_POST_ID = 9
SOURCE = "jetpack"
MISMATCH_PCT = 2.0


@dataclass(frozen=True)
class PostView:
    traffic_date: date
    post_id: int
    title: str
    views: int
    url: str
    slug: str | None


@dataclass(frozen=True)
class OverviewRow:
    traffic_date: date
    page_views: int
    unique_visitors: int


@dataclass(frozen=True)
class ReferrerRow:
    traffic_date: date
    referrer_group: str
    page_views: int


# --------------------------------------------------------------------------- CSV parsing


def _column(headers: list[str], name: str) -> int:
    try:
        return headers.index(name)
    except ValueError as exc:
        raise ValueError(f"Missing column {name!r} in {headers!r}") from exc


def _int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_post_views(path: str | Path) -> list[PostView]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    post_i = _column(headers, "Post_ID")
    title_i = _column(headers, "Title")
    views_i = _column(headers, "Views")
    url_i = _column(headers, "URL")

    output: list[PostView] = []

    for row in rows:
        url = row[url_i] if url_i < len(row) else ""
        slug = url_slug(url) if "/product/" in url else None

        output.append(
            PostView(
                traffic_date=parse_traffic_date(row[date_i]),
                post_id=_int(row[post_i] if post_i < len(row) else "0"),
                title=(row[title_i] if title_i < len(row) else "").strip(),
                views=_int(row[views_i] if views_i < len(row) else "0"),
                url=url.strip(),
                slug=slug,
            )
        )

    return output


def load_overview(path: str | Path) -> list[OverviewRow]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    views_i = _column(headers, "Views")
    visitors_i = _column(headers, "Visitors")

    return [
        OverviewRow(
            traffic_date=parse_traffic_date(row[date_i]),
            page_views=_int(row[views_i] if views_i < len(row) else "0"),
            unique_visitors=_int(row[visitors_i] if visitors_i < len(row) else "0"),
        )
        for row in rows
    ]


def load_referrers(path: str | Path) -> list[ReferrerRow]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    group_i = _column(headers, "Referrer_Group")
    views_i = _column(headers, "Views")

    return [
        ReferrerRow(
            traffic_date=parse_traffic_date(row[date_i]),
            referrer_group=(row[group_i] if group_i < len(row) else "").strip(),
            page_views=_int(row[views_i] if views_i < len(row) else "0"),
        )
        for row in rows
        if (row[group_i] if group_i < len(row) else "").strip()
    ]


def date_range(rows: list[Any]) -> dict[str, str | None]:
    dates = [row.traffic_date for row in rows]
    return {
        "first": min(dates).isoformat() if dates else None,
        "last": max(dates).isoformat() if dates else None,
    }


# --------------------------------------------------------------------------- mapping / validation


def map_product_views(
    post_views: list[PostView],
    index: ProductIndex,
) -> tuple[list[dict[str, Any]], list[PostView], dict[str, int]]:
    groups: dict[tuple[int, date], dict[str, Any]] = {}
    misses: list[PostView] = []
    counts = {"woo_id": 0, "slug": 0, "title": 0, "miss": 0}

    for row in post_views:
        if row.slug is None:
            continue

        resolution = index.resolve(row.post_id, row.title, row.url)

        if resolution.product is None:
            misses.append(row)
            counts["miss"] += 1
            continue

        counts[resolution.method or "miss"] += 1

        product = resolution.product
        key = (product.id, row.traffic_date)
        group = groups.setdefault(
            key,
            {
                "product_id": product.id,
                "woo_product_id": product.woo_product_id or row.post_id,
                "traffic_date": row.traffic_date,
                "page_views": 0,
            },
        )
        group["page_views"] += row.views

    return list(groups.values()), misses, counts


def cart_checkout_views(post_views: list[PostView]) -> tuple[dict[date, int], dict[date, int]]:
    cart: dict[date, int] = defaultdict(int)
    checkout: dict[date, int] = defaultdict(int)

    for row in post_views:
        title = row.title.strip().lower()

        if row.post_id == CART_POST_ID or title == "cart":
            cart[row.traffic_date] += row.views
        elif row.post_id == CHECKOUT_POST_ID or title == "checkout":
            checkout[row.traffic_date] += row.views

    return cart, checkout


def build_validation(
    post_views: list[PostView],
    overview: list[OverviewRow],
    referrers: list[ReferrerRow],
    product_days: list[dict[str, Any]],
    misses: list[PostView],
    mapping_counts: dict[str, int],
) -> dict[str, Any]:
    post_by_date: dict[date, int] = defaultdict(int)
    for row in post_views:
        post_by_date[row.traffic_date] += row.views

    overview_by_date = {row.traffic_date: row for row in overview}

    compared_dates = 0
    mismatches: list[dict[str, Any]] = []
    max_pct = 0.0

    for day, overview_row in sorted(overview_by_date.items()):
        if day not in post_by_date:
            continue

        compared_dates += 1
        post_views_for_day = post_by_date[day]
        overview_views = overview_row.page_views
        diff = abs(post_views_for_day - overview_views)
        pct = (diff / overview_views * 100.0) if overview_views else (100.0 if post_views_for_day else 0.0)
        max_pct = max(max_pct, pct)

        if pct > MISMATCH_PCT:
            mismatches.append(
                {
                    "date": day.isoformat(),
                    "post_views": post_views_for_day,
                    "overview_views": overview_views,
                    "diff": diff,
                    "pct": round(pct, 2),
                }
            )

    referrer_by_date: dict[date, int] = defaultdict(int)
    for row in referrers:
        referrer_by_date[row.traffic_date] += row.page_views

    coverages: list[float] = []
    referrer_over: list[dict[str, Any]] = []

    for day, referrer_views in sorted(referrer_by_date.items()):
        overview_row = overview_by_date.get(day)

        if not overview_row or overview_row.page_views <= 0:
            continue

        coverage = referrer_views / overview_row.page_views
        coverages.append(coverage)

        if referrer_views > overview_row.page_views:
            referrer_over.append(
                {
                    "date": day.isoformat(),
                    "referrer_views": referrer_views,
                    "overview_views": overview_row.page_views,
                    "pct": round(coverage * 100, 2),
                }
            )

    product_rows = sum(mapping_counts.get(key, 0) for key in ("woo_id", "slug", "title", "miss"))

    return {
        "row_counts": {
            "post_views": len(post_views),
            "overview": len(overview),
            "referrers": len(referrers),
            "product_rows": product_rows,
            "product_days": len(product_days),
            "mapping_misses": len(misses),
        },
        "mapping": mapping_counts,
        "post_vs_overview": {
            "compared_dates": compared_dates,
            "mismatch_dates": len(mismatches),
            "mismatch_threshold_pct": MISMATCH_PCT,
            "max_pct": round(max_pct, 2),
            "mismatches": mismatches,
        },
        "referrer_coverage": {
            "coverage_dates": len(coverages),
            "min_pct": round(min(coverages) * 100, 2) if coverages else None,
            "max_pct": round(max(coverages) * 100, 2) if coverages else None,
            "avg_pct": round(sum(coverages) / len(coverages) * 100, 2) if coverages else None,
            "overview_exceeded": referrer_over,
        },
    }


# --------------------------------------------------------------------------- SQL


PRODUCT_DAILY_UPSERT = """
INSERT INTO sp_traffic_daily
    (organization_id, store_id, product_id, woo_product_id, traffic_date,
     page_views, source, created_at, updated_at)
VALUES
    (:organization_id, :store_id, :product_id, :woo_product_id, :traffic_date,
     :page_views, 'jetpack', NOW(), NOW())
ON CONFLICT (organization_id, store_id, product_id, traffic_date, source)
DO UPDATE SET
    page_views = EXCLUDED.page_views,
    woo_product_id = EXCLUDED.woo_product_id,
    updated_at = NOW()
"""

ORG_DAILY_UPSERT = """
INSERT INTO sp_traffic_daily_org
    (organization_id, store_id, traffic_date, page_views, unique_visitors,
     cart_page_views, checkout_page_views, source, created_at, updated_at)
VALUES
    (:organization_id, :store_id, :traffic_date, :page_views, :unique_visitors,
     :cart_page_views, :checkout_page_views, 'jetpack', NOW(), NOW())
ON CONFLICT (organization_id, store_id, traffic_date, source)
DO UPDATE SET
    page_views = EXCLUDED.page_views,
    unique_visitors = EXCLUDED.unique_visitors,
    cart_page_views = EXCLUDED.cart_page_views,
    checkout_page_views = EXCLUDED.checkout_page_views,
    updated_at = NOW()
"""

REFERRER_UPSERT = """
INSERT INTO sp_traffic_referrers_daily
    (organization_id, store_id, traffic_date, referrer_group, page_views,
     source, created_at, updated_at)
VALUES
    (:organization_id, :store_id, :traffic_date, :referrer_group, :page_views,
     'jetpack', NOW(), NOW())
ON CONFLICT (organization_id, store_id, traffic_date, referrer_group, source)
DO UPDATE SET
    page_views = EXCLUDED.page_views,
    updated_at = NOW()
"""

MISS_UPSERT = """
INSERT INTO ml_traffic_mapping_misses
    (organization_id, store_id, traffic_date, post_id, title, url, views,
     created_at, updated_at)
VALUES
    (:organization_id, :store_id, :traffic_date, :post_id, :title, :url, :views,
     NOW(), NOW())
ON CONFLICT (organization_id, store_id, traffic_date, post_id, url)
DO UPDATE SET
    title = EXCLUDED.title,
    views = EXCLUDED.views,
    updated_at = NOW()
"""


# --------------------------------------------------------------------------- backfill


async def _table_counts(session: AsyncSession) -> dict[str, int]:
    tables = (
        "sp_traffic_daily",
        "sp_traffic_daily_org",
        "sp_traffic_referrers_daily",
        "ml_traffic_mapping_misses",
        "ml_backfill_jobs",
    )

    counts: dict[str, int] = {}

    for table in tables:
        try:
            counts[table] = int(await session.scalar(text(f"SELECT count(*) FROM {table}")) or 0)
        except Exception:
            counts[table] = -1

    return counts


async def _start_job(
    session: AsyncSession,
    file_name: str,
    rows: int,
    date_from: str | None,
    date_to: str | None,
) -> int:
    job_id = await session.scalar(
        text(
            """
            INSERT INTO ml_backfill_jobs
                (file, date_from, date_to, status, rows, started_at, created_at, updated_at)
            VALUES
                (:file, :date_from, :date_to, 'running', :rows, NOW(), NOW(), NOW())
            RETURNING id
            """
        ),
        {
            "file": file_name,
            "date_from": date.fromisoformat(date_from) if date_from else None,
            "date_to": date.fromisoformat(date_to) if date_to else None,
            "rows": rows,
        },
    )

    await session.commit()

    return int(job_id)


async def _finish_job(
    session: AsyncSession,
    job_id: int,
    status: str,
    rows: int,
    error: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            UPDATE ml_backfill_jobs
            SET status = :status, rows = :rows, error = :error,
                finished_at = NOW(), updated_at = NOW()
            WHERE id = :id
            """
        ),
        {"id": job_id, "status": status, "rows": rows, "error": error},
    )


async def _run_job(
    session: AsyncSession,
    file_name: str,
    source_rows: int,
    date_from: str | None,
    date_to: str | None,
    writer: Callable[[], Awaitable[int]],
) -> dict[str, Any]:
    job_id = await _start_job(session, file_name, source_rows, date_from, date_to)

    try:
        written = await writer()
        await session.commit()
        await _finish_job(session, job_id, "done", source_rows)
        await session.commit()
        return {"file": file_name, "status": "done", "rows": source_rows, "written": written}
    except Exception as exc:
        await session.rollback()
        await _finish_job(session, job_id, "error", source_rows, str(exc))
        await session.commit()
        raise


async def backfill(
    directory: str | Path,
    organization_id: int = 1,
    store_id: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    directory = Path(directory)
    post_path = directory / POST_VIEWS_FILE
    overview_path = directory / OVERVIEW_FILE
    referrers_path = directory / REFERRERS_FILE

    for path in (post_path, overview_path, referrers_path):
        if not path.is_file():
            raise FileNotFoundError(f"Traffic export not found: {path}")

    post_views = load_post_views(post_path)
    overview = load_overview(overview_path)
    referrers = load_referrers(referrers_path)

    post_range = date_range(post_views)
    overview_range = date_range(overview)
    referrer_range = date_range(referrers)

    async with session_scope() as session:
        products = await load_products(session, organization_id, store_id)
        index = ProductIndex(products)

        product_days, misses, mapping_counts = map_product_views(post_views, index)
        cart_by_date, checkout_by_date = cart_checkout_views(post_views)

        validation = build_validation(
            post_views,
            overview,
            referrers,
            product_days,
            misses,
            mapping_counts,
        )

        summary: dict[str, Any] = {
            "phase": 3,
            "dry_run": dry_run,
            "directory": str(directory),
            "organization_id": organization_id,
            "store_id": store_id,
            "products_loaded": len(products),
            "files": {
                POST_VIEWS_FILE: {"rows": len(post_views), **post_range},
                OVERVIEW_FILE: {"rows": len(overview), **overview_range},
                REFERRERS_FILE: {"rows": len(referrers), **referrer_range},
            },
            "validation": validation,
            "jobs": [],
        }

        if dry_run:
            summary["table_counts"] = await _table_counts(session)
            return summary

        async def write_product_days() -> int:
            if product_days:
                await session.execute(text(PRODUCT_DAILY_UPSERT), [
                    {"organization_id": organization_id, "store_id": store_id, **row}
                    for row in product_days
                ])

            if misses:
                await session.execute(text(MISS_UPSERT), [
                    {
                        "organization_id": organization_id,
                        "store_id": store_id,
                        "traffic_date": row.traffic_date,
                        "post_id": row.post_id,
                        "title": row.title,
                        "url": row.url,
                        "views": row.views,
                    }
                    for row in misses
                ])

            return len(product_days)

        async def write_org_days() -> int:
            rows = [
                {
                    "organization_id": organization_id,
                    "store_id": store_id,
                    "traffic_date": row.traffic_date,
                    "page_views": row.page_views,
                    "unique_visitors": row.unique_visitors,
                    "cart_page_views": cart_by_date.get(row.traffic_date, 0),
                    "checkout_page_views": checkout_by_date.get(row.traffic_date, 0),
                }
                for row in overview
            ]

            if rows:
                await session.execute(text(ORG_DAILY_UPSERT), rows)

            return len(rows)

        async def write_referrers() -> int:
            rows = [
                {
                    "organization_id": organization_id,
                    "store_id": store_id,
                    "traffic_date": row.traffic_date,
                    "referrer_group": row.referrer_group,
                    "page_views": row.page_views,
                }
                for row in referrers
            ]

            if rows:
                await session.execute(text(REFERRER_UPSERT), rows)

            return len(rows)

        summary["jobs"] = [
            await _run_job(
                session,
                POST_VIEWS_FILE,
                len(post_views),
                post_range["first"],
                post_range["last"],
                write_product_days,
            ),
            await _run_job(
                session,
                OVERVIEW_FILE,
                len(overview),
                overview_range["first"],
                overview_range["last"],
                write_org_days,
            ),
            await _run_job(
                session,
                REFERRERS_FILE,
                len(referrers),
                referrer_range["first"],
                referrer_range["last"],
                write_referrers,
            ),
        ]

        summary["table_counts"] = await _table_counts(session)

        return summary


def summary_to_json(summary: dict[str, Any]) -> str:
    return json.dumps(summary, indent=2, default=str) + "\n"
