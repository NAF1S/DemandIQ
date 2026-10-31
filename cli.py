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

from collections import Counter

import pandas as pd

from app.core.config import get_settings
from app.etl.pipeline import run_feature_pipeline
from app.etl.psql import load_features
from app.etl.traffic_backfill import backfill, summary_to_json
from app.models.selection import create_model, select_model


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


def _print_feature_summary(summary: dict) -> None:
    mode = "DRY RUN" if summary["dry_run"] else "WRITTEN"
    print(f"Phase 5 daily features — {mode}")
    print(
        f"Org {summary['organization_id']} · store {summary['store_id']} · "
        f"{summary['start_date']} → {summary['end_date']}"
    )
    print(
        f"Entities: {summary['entities']} ({summary['items']} items, {summary['products']} products) · "
        f"with product traffic: {summary['traffic_entities']}"
    )
    print(
        f"Feature rows: {summary['feature_rows']} · written: {summary['rows_written']} · "
        f"enough data: {summary['entities_with_enough_data']}/{summary['entities']}"
    )
    print(f"Model recommendations: {summary['model_recommendations']}")

    sparse = sorted(
        summary["quality"],
        key=lambda report: (report["non_zero_days"], report["entity_type"], report["entity_id"]),
    )[:10]

    if sparse:
        print("Sparsest entities:")
        for report in sparse:
            print(
                f"  {report['entity_type']} #{report['entity_id']}: "
                f"{report['non_zero_days']}/{report['total_days']} non-zero · "
                f"gap {report['max_gap_days']}d · {report['recommended_model']}"
            )

    if summary["dry_run"]:
        print("No rows written. Re-run without --dry-run to apply.")


def _print_selection_summary(summary: dict) -> None:
    print("Phase 6 model selection")
    print(
        f"Org {summary['organization_id']} · store {summary['store_id']} · "
        f"entities {summary['entities']} · admitted traffic {summary['admitted_traffic']}"
    )
    print(f"Models: {summary['models']}")

    if summary["choices"]:
        print("Recommendations:")
        for choice in summary["choices"][:15]:
            print(
                f"  {choice['entity_type']} #{choice['entity_id']}: {choice['model']} "
                f"({choice['reason']})"
            )

    if summary["backtest"]:
        print("Backtest sample:")
        for row in summary["backtest"]:
            if "error" in row:
                print(f"  {row['entity_type']} #{row['entity_id']} {row['model']}: {row['error']}")
            else:
                metrics = row["metrics"]
                print(
                    f"  {row['entity_type']} #{row['entity_id']} {row['model']}: "
                    f"MAE {metrics['mae']} · MAPE {metrics['mape']}% · "
                    f"coverage {metrics['coverage_95']}"
                )


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

    features_parser = sub.add_parser("build-features", help="Build zero-filled daily ML features")
    features_parser.add_argument("--org", type=int, default=1, help="organization id (default: 1)")
    features_parser.add_argument("--store", type=int, default=1, help="store connection id (default: 1)")
    features_parser.add_argument("--lookback-days", type=int, default=90, help="number of days to build (default: 90)")
    features_parser.add_argument("--dry-run", action="store_true", help="validate and report without writing")
    features_parser.add_argument("--json", type=Path, default=None, help="write the full summary as JSON")
    features_parser.add_argument("--dsn", default=None, help="override DATABASE_URL for this run")

    select_parser = sub.add_parser("select-models", help="Select/backtest models on daily features")
    select_parser.add_argument("--org", type=int, default=1, help="organization id (default: 1)")
    select_parser.add_argument("--store", type=int, default=1, help="store connection id (default: 1)")
    select_parser.add_argument("--limit", type=int, default=5, help="entities in the backtest sample")
    select_parser.add_argument("--horizon", type=int, default=14, help="holdout horizon (default: 14)")
    select_parser.add_argument("--backtest", action="store_true", help="backtest the top entities")
    select_parser.add_argument("--json", type=Path, default=None, help="write the full summary as JSON")
    select_parser.add_argument("--dsn", default=None, help="override DATABASE_URL for this run")

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

    if args.command == "build-features":
        summary = run_feature_pipeline(
            organization_id=args.org,
            store_id=args.store,
            lookback_days=args.lookback_days,
            dry_run=args.dry_run,
        )

        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(summary_to_json(summary), encoding="utf-8")
            print(f"JSON summary: {args.json}")

        _print_feature_summary(summary)
        return 0

    if args.command == "select-models":
        frame = load_features(args.org, args.store)
        if frame.empty:
            print("No daily features found. Run build-features first.")
            return 1

        choices = []
        for (entity_type, entity_id), group in frame.groupby(["entity_type", "entity_id"], sort=True):
            choice = select_model(group)
            choices.append(
                {
                    "entity_type": entity_type,
                    "entity_id": int(entity_id),
                    **choice.as_dict(),
                }
            )

        backtest = []
        if args.backtest:
            units = frame.assign(
                _units=pd.to_numeric(frame["units_consumed"], errors="coerce").fillna(0)
            )
            sample = units.groupby(["entity_type", "entity_id"])["_units"].sum().nlargest(args.limit)
            for entity_type, entity_id in sample.index:
                group = frame[
                    (frame["entity_type"] == entity_type) & (frame["entity_id"] == entity_id)
                ]
                for name in ("velocity", "prophet", "lightgbm"):
                    try:
                        model = create_model(name)
                        metrics = model.backtest(group, horizon=args.horizon)
                        backtest.append(
                            {
                                "entity_type": entity_type,
                                "entity_id": int(entity_id),
                                "model": model.version,
                                "metrics": metrics.as_dict(),
                            }
                        )
                    except Exception as exc:  # optional dependency or sparse entity
                        backtest.append(
                            {
                                "entity_type": entity_type,
                                "entity_id": int(entity_id),
                                "model": name,
                                "error": str(exc),
                            }
                        )

        summary = {
            "organization_id": args.org,
            "store_id": args.store,
            "entities": len(choices),
            "models": dict(Counter(choice["model"] for choice in choices)),
            "admitted_traffic": sum(1 for choice in choices if choice["signal"]["admitted"]),
            "choices": choices,
            "backtest": backtest,
        }

        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(summary_to_json(summary), encoding="utf-8")
            print(f"JSON summary: {args.json}")

        _print_selection_summary(summary)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
