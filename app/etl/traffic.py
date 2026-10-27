"""Validation and product mapping for the historical traffic exports.

The three Jetpack CSVs live outside this repo (``stock_pilot/web-traffic-data``).
This module is intentionally dependency-free (stdlib ``csv`` only) so it can be
used by the Phase 1 audit and reused by the Phase 3 backfill importer.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

POST_VIEWS_FILE = "daily_post_product_views.csv"
OVERVIEW_FILE = "daily_traffic_overview.csv"
REFERRERS_FILE = "daily_referrers.csv"


def parse_traffic_date(value: str | date | datetime) -> date:
    """Parse Jetpack's ``M/D/YYYY`` dates explicitly (never let pandas guess)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported traffic date: {value!r}")


def read_rows(path: str | Path) -> tuple[list[str], list[list[str]]]:
    """Read a CSV, tolerating a UTF-8 BOM and quoted commas."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = [cell.strip() for cell in next(reader)]
        except StopIteration:
            return [], []
        rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    return headers, rows


def _column(headers: list[str], name: str) -> int:
    try:
        return headers.index(name)
    except ValueError as exc:
        raise ValueError(f"Missing column {name!r} in header {headers!r}") from exc


def _int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def validate_export(
    path: str | Path,
    *,
    date_column: str = "Date",
    key_columns: tuple[str, ...] = ("Date",),
) -> dict[str, Any]:
    """Validate one export: header, row count, dates, duplicates, empty columns."""
    path = Path(path)
    raw = path.read_bytes()
    headers, rows = read_rows(path)

    date_idx = _column(headers, date_column) if rows else 0
    parsed: list[date] = []
    invalid: list[str] = []

    for row in rows:
        if date_idx >= len(row):
            invalid.append("<missing>")
            continue
        try:
            parsed.append(parse_traffic_date(row[date_idx]))
        except ValueError:
            invalid.append(row[date_idx])

    duplicate_keys = 0
    if key_columns and rows:
        indexes: list[int] = []
        for name in key_columns:
            indexes.append(_column(headers, name))
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            key = tuple(row[i] if i < len(row) else "" for i in indexes)
            if key in seen:
                duplicate_keys += 1
            seen.add(key)

    return {
        "file": path.name,
        "bytes": len(raw),
        "bom": raw.startswith(b"\xef\xbb\xbf"),
        "headers": headers,
        "rows": len(rows),
        "date_min": min(parsed).isoformat() if parsed else None,
        "date_max": max(parsed).isoformat() if parsed else None,
        "invalid_dates": invalid[:5],
        "invalid_date_count": len(invalid),
        "duplicate_keys": duplicate_keys,
        "empty_columns": [i for i, name in enumerate(headers) if name == ""],
    }


def load_post_views(path: str | Path) -> list[dict[str, Any]]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    post_i = _column(headers, "Post_ID")
    title_i = _column(headers, "Title")
    views_i = _column(headers, "Views")
    url_i = _column(headers, "URL")

    output: list[dict[str, Any]] = []
    for row in rows:
        try:
            day = parse_traffic_date(row[date_i])
        except (ValueError, IndexError):
            continue
        output.append(
            {
                "date": day,
                "post_id": _int(row[post_i]),
                "title": row[title_i] if title_i < len(row) else "",
                "views": _int(row[views_i]),
                "url": row[url_i] if url_i < len(row) else "",
            }
        )
    return output


def group_post_views(
    rows: Iterable[dict[str, Any]],
    *,
    product_only: bool = True,
) -> dict[int, dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        url = row.get("url") or ""
        if product_only and "/product/" not in url:
            continue
        post_id = int(row["post_id"])
        group = groups.setdefault(
            post_id,
            {
                "post_id": post_id,
                "title": row.get("title", ""),
                "url": url,
                "views": 0,
                "days": 0,
                "first": row["date"],
                "last": row["date"],
            },
        )
        group["views"] += int(row.get("views") or 0)
        group["days"] += 1
        if row["date"] < group["first"]:
            group["first"] = row["date"]
        if row["date"] > group["last"]:
            group["last"] = row["date"]
    return groups


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    normalized = normalized.lower().replace("&", " ").replace("+", " ")
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


def url_slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def map_products(
    groups: dict[int, dict[str, Any]],
    products: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Map traffic ``Post_ID`` groups to portal products.

    Primary key: ``Post_ID == sp_products.woo_product_id``. Fallbacks: normalised
    product title, then URL slug vs a slugified product name.
    """
    products = list(products)
    by_woo: dict[int, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    by_slug: dict[str, dict[str, Any]] = {}
    for product in products:
        woo_id = product.get("woo_product_id")
        if woo_id is not None:
            by_woo[int(woo_id)] = product
        if product.get("name"):
            by_title.setdefault(normalize_title(str(product["name"])), product)
            by_slug.setdefault(slugify(str(product["name"])), product)

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    counts = {"woo_id": 0, "title": 0, "slug": 0}

    for post_id, group in sorted(groups.items(), key=lambda item: -item[1]["views"]):
        product = by_woo.get(post_id)
        method = "woo_id" if product else None
        if product is None:
            product = by_title.get(normalize_title(group["title"]))
            method = "title" if product else None
        if product is None:
            product = by_slug.get(url_slug(group["url"]))
            method = "slug" if product else None

        record = {
            "post_id": post_id,
            "title": group["title"],
            "url": group["url"],
            "views": group["views"],
            "days": group["days"],
            "first": group["first"].isoformat(),
            "last": group["last"].isoformat(),
        }

        if product is None:
            unmatched.append(record)
        else:
            counts[method] += 1
            matched.append({**record, "product_id": product["id"], "product_name": product["name"], "method": method})

    matched_product_ids = {item["product_id"] for item in matched}
    products_without_traffic = [
        {
            "product_id": product["id"],
            "woo_product_id": product.get("woo_product_id"),
            "name": product.get("name"),
        }
        for product in products
        if product["id"] not in matched_product_ids
    ]

    return {
        "traffic_product_ids": len(groups),
        "matched": matched,
        "unmatched": unmatched,
        "match_counts": counts,
        "products_without_traffic": products_without_traffic,
    }


def load_overview(path: str | Path) -> list[dict[str, Any]]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    views_i = _column(headers, "Views")
    visitors_i = _column(headers, "Visitors")
    output = []
    for row in rows:
        try:
            day = parse_traffic_date(row[date_i])
        except (ValueError, IndexError):
            continue
        output.append(
            {
                "date": day,
                "views": _int(row[views_i]) if views_i < len(row) else 0,
                "visitors": _int(row[visitors_i]) if visitors_i < len(row) else 0,
            }
        )
    return output


def load_referrers(path: str | Path) -> list[dict[str, Any]]:
    headers, rows = read_rows(path)
    date_i = _column(headers, "Date")
    group_i = _column(headers, "Referrer_Group")
    views_i = _column(headers, "Views")
    output = []
    for row in rows:
        try:
            day = parse_traffic_date(row[date_i])
        except (ValueError, IndexError):
            continue
        output.append(
            {
                "date": day,
                "referrer": row[group_i] if group_i < len(row) else "",
                "views": _int(row[views_i]) if views_i < len(row) else 0,
            }
        )
    return output


def validate_traffic_dir(directory: str | Path, products: Iterable[dict[str, Any]]) -> dict[str, Any]:
    directory = Path(directory)
    if not directory.is_dir():
        return {"directory": str(directory), "error": "traffic directory not found"}

    report: dict[str, Any] = {"directory": str(directory), "exports": {}}
    specs = {
        POST_VIEWS_FILE: ("Date", ("Date", "Post_ID", "URL")),
        OVERVIEW_FILE: ("Date", ("Date",)),
        REFERRERS_FILE: ("Date", ("Date", "Referrer_Group")),
    }

    for filename, (date_column, key_columns) in specs.items():
        path = directory / filename
        if not path.is_file():
            report["exports"][filename] = {"file": filename, "error": "missing"}
            continue
        report["exports"][filename] = validate_export(
            path,
            date_column=date_column,
            key_columns=key_columns,
        )

    post_path = directory / POST_VIEWS_FILE
    if post_path.is_file():
        rows = load_post_views(post_path)
        groups = group_post_views(rows, product_only=True)
        report["product_traffic"] = {
            "rows": sum(1 for row in rows if "/product/" in (row.get("url") or "")),
            "non_product_rows": sum(1 for row in rows if "/product/" not in (row.get("url") or "")),
            "groups": len(groups),
            "date_min": min((group["first"] for group in groups.values()), default=None),
            "date_max": max((group["last"] for group in groups.values()), default=None),
            "mapping": map_products(groups, products),
        }
        if report["product_traffic"]["date_min"] is not None:
            report["product_traffic"]["date_min"] = report["product_traffic"]["date_min"].isoformat()
            report["product_traffic"]["date_max"] = report["product_traffic"]["date_max"].isoformat()

    overview_path = directory / OVERVIEW_FILE
    if overview_path.is_file():
        overview = load_overview(overview_path)
        nonzero = [row for row in overview if row["views"] > 0]
        report["site_traffic"] = {
            "rows": len(overview),
            "date_min": min((row["date"] for row in overview), default=None),
            "date_max": max((row["date"] for row in overview), default=None),
            "total_views": sum(row["views"] for row in overview),
            "total_visitors": sum(row["visitors"] for row in overview),
            "zero_days": sum(1 for row in overview if row["views"] == 0),
            "first_nonzero_date": min((row["date"] for row in nonzero), default=None),
        }
        for key in ("date_min", "date_max", "first_nonzero_date"):
            if report["site_traffic"][key] is not None:
                report["site_traffic"][key] = report["site_traffic"][key].isoformat()

    referrers_path = directory / REFERRERS_FILE
    if referrers_path.is_file():
        referrers = load_referrers(referrers_path)
        totals: dict[str, int] = defaultdict(int)
        for row in referrers:
            totals[row["referrer"]] += row["views"]
        report["referrers"] = {
            "rows": len(referrers),
            "groups": len(totals),
            "date_min": min((row["date"] for row in referrers), default=None),
            "date_max": max((row["date"] for row in referrers), default=None),
            "top": [
                {"referrer": name, "views": views}
                for name, views in sorted(totals.items(), key=lambda item: -item[1])[:10]
            ],
        }
        for key in ("date_min", "date_max"):
            if report["referrers"][key] is not None:
                report["referrers"][key] = report["referrers"][key].isoformat()

    return report


def overlap_window(
    first_order: date | None,
    last_order: date | None,
    traffic_start: date | None,
    traffic_end: date | None,
) -> dict[str, Any]:
    start = max(filter(None, [first_order, traffic_start]), default=None)
    end = min(filter(None, [last_order, traffic_end]), default=None)
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "days": (end - start).days + 1 if start and end else 0,
        "valid": bool(start and end and start <= end),
    }
