"""Phase 7 training pipeline: Supabase features -> models -> prediction tables.

Used by the Modal training container and by the local CLI when DB credentials
are available. The database connection is psycopg 3 with prepared statements
disabled so it is compatible with the Supabase Supavisor pooler.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
from psycopg.rows import dict_row

from app.training import writers
from app.monitoring.health import assert_horizon_is_forward, feature_span
from app.training.risk import build_recommendation, build_risk, stock_for
from app.training.trainer import forecast_rows, train_entity


def connect() -> psycopg.Connection:
    missing = [name for name in ("DB_HOST", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing database settings: {', '.join(missing)}")

    kwargs: dict[str, Any] = {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ["DB_DATABASE"],
        "user": os.environ["DB_USERNAME"],
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 20,
        "prepare_threshold": None,
        "application_name": "demandiq-training",
        "row_factory": dict_row,
    }
    sslmode = os.environ.get("DB_SSLMODE")
    if sslmode:
        kwargs["sslmode"] = sslmode

    return psycopg.connect(**kwargs)


def load_features(conn: psycopg.Connection, organization_id: int, store_id: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM ml_daily_features
            WHERE organization_id = %s AND store_id = %s
            ORDER BY entity_type, entity_id, feature_date
            """,
            (organization_id, store_id),
        )
        rows = cur.fetchall()

    return pd.DataFrame(rows)


def load_stock(conn: psycopg.Connection, organization_id: int) -> dict[tuple[str, int], float]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 'item' AS entity_type, id AS entity_id,
                   (quantity_on_hand - quantity_reserved)::float AS available
            FROM sp_items
            WHERE organization_id = %s AND deleted_at IS NULL
            UNION ALL
            SELECT 'product', id,
                   (quantity_on_hand - quantity_reserved)::float
            FROM sp_products
            WHERE organization_id = %s AND deleted_at IS NULL
            """,
            (organization_id, organization_id),
        )
        rows = cur.fetchall()

    return {(str(row["entity_type"]), int(row["entity_id"])): float(row["available"] or 0.0) for row in rows}


def run_training(
    organization_id: int = 1,
    store_id: int = 1,
    periods: int = 14,
    engine: str = "auto",
    artifact_dir: str | Path | None = None,
    run_id: str | None = None,
    compute_run_id: int | None = None,
    trigger_source: str | None = None,
) -> dict[str, Any]:
    """Train every entity, write forecasts/risks/registry, and return a summary."""
    started = datetime.now(UTC)
    run_id = run_id or f"run-{started.strftime('%Y%m%dT%H%M%SZ')}"
    artifact_root = Path(artifact_dir) if artifact_dir else None

    with connect() as conn:
        features = load_features(conn, organization_id, store_id)
        if features.empty:
            message = "No ml_daily_features rows found for this organization/store."
            if compute_run_id is not None:
                writers.update_compute_run(
                    conn,
                    compute_run_id,
                    status="failed",
                    error=message,
                    run_key=run_id,
                )
                conn.commit()
            raise RuntimeError(message)

        stock = load_stock(conn, organization_id)
        entities = list(features.groupby(["entity_type", "entity_id"], sort=True))
        total = len(entities)

        first_date, last_date = feature_span(features)
        if last_date is None:
            raise RuntimeError("Feature rows carry no usable feature_date values.")

        try:
            assert_horizon_is_forward(last_date)
        except RuntimeError as exc:
            if compute_run_id is not None:
                writers.update_compute_run(
                    conn, compute_run_id, status="failed", error=str(exc), run_key=run_id
                )
                conn.commit()
            raise

        print(
            f"run {run_id}: {len(features)} feature rows · {total} entities · "
            f"org {organization_id} store {store_id} · features {first_date} → {last_date}",
            flush=True,
        )

        if compute_run_id is None and trigger_source:
            compute_run_id = writers.create_compute_run(
                conn,
                organization_id,
                trigger_source,
                run_id,
                total,
            )
            conn.commit()
        elif compute_run_id is not None:
            writers.update_compute_run(
                conn,
                compute_run_id,
                status="running",
                done=0,
                total=total,
                run_key=run_id,
            )
            conn.commit()

        summaries: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        forecast_count = 0
        recommendation_count = 0

        for index, ((entity_type, entity_id), group) in enumerate(entities, start=1):
            entity_type = str(entity_type)
            entity_id = int(entity_id)

            try:
                result = train_entity(
                    group,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    periods=periods,
                    engine=engine,
                    artifact_dir=artifact_root,
                    run_id=run_id,
                    organization_id=organization_id,
                )
            except Exception as exc:
                failures.append(
                    {"entity": f"{entity_type}:{entity_id}", "error": str(exc)}
                )
                print(f"[{index}/{total}] {entity_type}:{entity_id} FAILED: {exc}", flush=True)
                continue

            risk = build_risk(result, organization_id, stock_for(stock, result), periods)
            recommendation = build_recommendation(result, organization_id, risk)

            registry_version = f"{result.version}-{entity_type}-{entity_id}-{run_id}"
            registry_metrics = {
                **result.metrics.as_dict(),
                "selection_model": result.choice.model,
                "fallback": result.warning,
            }
            registry_hyperparameters = {
                **result.model.get_hyperparameters(),
                "algorithm_version": result.version,
                "selection_reason": result.choice.reason,
                "traffic": result.choice.traffic,
                "signal": result.choice.signal,
                "warning": result.warning,
            }

            rows = forecast_rows(result, organization_id)
            predictions = [
                {
                    "organization_id": organization_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "forecast_date": item.forecast_date,
                    "predicted_qty": item.predicted_quantity,
                    "model_version": result.version,
                    "run_id": run_id,
                }
                for item in result.predictions
            ]

            try:
                forecast_count += writers.upsert_forecasts(conn, rows)
                if rows:
                    writers.prune_stale_forecasts(
                        conn,
                        organization_id,
                        entity_type,
                        entity_id,
                        rows[0]["forecast_date"],
                    )
                writers.upsert_risk(conn, risk)
                writers.replace_pending_recommendation(
                    conn,
                    recommendation,
                    organization_id,
                    entity_type,
                    entity_id,
                )
                writers.retire_active_models(
                    conn,
                    organization_id,
                    entity_type,
                    entity_id,
                    registry_version,
                )
                writers.upsert_registry(
                    conn,
                    {
                        "organization_id": organization_id,
                        "model_name": result.model_name,
                        "model_version": registry_version,
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "framework": type(result.model).__name__,
                        "hyperparameters": registry_hyperparameters,
                        "metrics": registry_metrics,
                        "artifact_path": result.artifact_path,
                        "training_rows": len(result.frame),
                        "training_start": pd.Timestamp(result.frame["ds"].min()).date(),
                        "training_end": pd.Timestamp(result.frame["ds"].max()).date(),
                        "status": "active",
                    },
                )
                writers.upsert_prediction_log(conn, predictions)

                if compute_run_id is not None:
                    writers.update_compute_run(
                        conn,
                        compute_run_id,
                        status="running",
                        done=index,
                        total=total,
                        run_key=run_id,
                    )

                conn.commit()
            except Exception:
                conn.rollback()
                failures.append(
                    {"entity": f"{entity_type}:{entity_id}", "error": "database write failed"}
                )
                continue

            if recommendation is not None:
                recommendation_count += 1

            print(
                f"[{index}/{total}] {entity_type}:{entity_id} {result.model_name} "
                f"mae={result.metrics.mae} wape={result.metrics.wape} "
                f"risk={risk['risk_level']}",
                flush=True,
            )

            summaries.append(
                {
                    "entity": f"{entity_type}:{entity_id}",
                    "model": result.model_name,
                    "version": result.version,
                    "mae": result.metrics.mae,
                    "wape": result.metrics.wape,
                    "pinball": result.metrics.pinball_loss,
                    "risk": risk["risk_level"],
                    "warning": result.warning,
                }
            )

        finished = datetime.now(UTC)
        summary = {
            "run_id": run_id,
            "organization_id": organization_id,
            "store_id": store_id,
            "periods": periods,
            "engine": engine,
            "entities": total,
            "trained": len(summaries),
            "failed": failures,
            "models": dict(Counter(item["model"] for item in summaries)),
            "risks": dict(Counter(item["risk"] for item in summaries)),
            "forecast_rows": forecast_count,
            "recommendations": recommendation_count,
            "registry_rows": len(summaries),
            "prediction_log_rows": forecast_count,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "results": summaries,
        }

        if compute_run_id is not None:
            writers.update_compute_run(
                conn,
                compute_run_id,
                status="succeeded" if not failures else "failed",
                done=len(summaries),
                total=total,
                run_key=run_id,
                error=json.dumps(failures) if failures else None,
            )
            conn.commit()

        if artifact_root is not None:
            summary_path = artifact_root / "runs" / f"{run_id}.json"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
            summary["summary_path"] = str(summary_path)

    return summary

