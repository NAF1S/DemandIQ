"""DemandIQ CLI.

Usage::

    python -m cli backfill-traffic --dir ../web-traffic-data --org 1 --store 1
    python -m cli backfill-traffic --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from app.core.config import get_settings
from app.etl.traffic_backfill import backfill, summary_to_json


def _print_summary(summary: dict) -> None:
    mode = "DRY RUN" if summary["dry_run"] else "APPLIED"
    print(f"Phase 3 traffic backfill — {mode}")
    print(f"Directory: {summary['directory']} · org {summary['organization_id']} · store {summary['store_id']}")

    for name, info in summary["files"].items():
        print(f"  {name}: {info['rows']} rows · {info['first']} → {info['last']}")

    validation = summary["validation"]
    counts = validation["row_counts"]
    mapping = validation["mapping"]
    print(
        "Product mapping: "
        f"{counts['product_rows']} product rows → {counts['product_days']} product-day groups · "
        f"woo_id={mapping.get('woo_id', 0)} slug={mapping.get('slug', 0)} "
        f"title={mapping.get('title', 0)} misses={mapping.get('miss', 0)}"
    )

    post_check = validation["post_vs_overview"]
    print(
        "Post rows vs overview: "
        f"{post_check['compared_dates']} dates compared · "
        f"{post_check['mismatch_dates']} > {post_check['mismatch_threshold_pct']}% · "
        f"max {post_check['max_pct']}%"
    )

    for row in post_check["mismatches"][:10]:
        print(
            f"  mismatch {row['date']}: post={row['post_views']} overview={row['overview_views']} "
            f"({row['pct']}%)"
        )

    coverage = validation["referrer_coverage"]
    print(
        "Referrer coverage: "
        f"{coverage['coverage_dates']} dates · "
        f"min={coverage['min_pct']}% max={coverage['max_pct']}% avg={coverage['avg_pct']}%"
    )
    for row in coverage["overview_exceeded"][:10]:
        print(f"  referrers exceed overview on {row['date']} ({row['pct']}%)")

    if summary["jobs"]:
        print("Jobs:")
        for job in summary["jobs"]:
            print(f"  {job['file']}: {job['status']} · source rows {job['rows']} · written {job['written']}")

    if summary.get("table_counts"):
        print("Table counts:")
        for table, count in summary["table_counts"].items():
            print(f"  {table}: {count}")

    if summary["dry_run"]:
        print("No rows written. Re-run without --dry-run to apply.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cli", description="DemandIQ data pipeline CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    backfill_parser = sub.add_parser("backfill-traffic", help="Import the three Jetpack traffic exports")
    backfill_parser.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "web-traffic-data",
        help="directory containing the three Jetpack CSVs",
    )
    backfill_parser.add_argument("--org", type=int, default=1, help="organization id (default: 1)")
    backfill_parser.add_argument("--store", type=int, default=1, help="store connection id (default: 1)")
    backfill_parser.add_argument("--dry-run", action="store_true", help="validate and report without writing")
    backfill_parser.add_argument("--json", type=Path, default=None, help="write the full summary as JSON")
    backfill_parser.add_argument("--dsn", default=None, help="override DATABASE_URL for this run")

    args = parser.parse_args(argv)

    if args.dsn:
        os.environ["DATABASE_URL"] = args.dsn
        get_settings.cache_clear()

    if args.command == "backfill-traffic":
        summary = asyncio.run(
            backfill(
                directory=args.dir,
                organization_id=args.org,
                store_id=args.store,
                dry_run=args.dry_run,
            )
        )

        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(summary_to_json(summary), encoding="utf-8")
            print(f"JSON summary: {args.json}")

        _print_summary(summary)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
