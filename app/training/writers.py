"""Idempotent Postgres writers for the forecasting pipeline."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from psycopg import Connection

UPSERT_FORECAST = """
INSERT INTO sp_demand_forecasts (
    organization_id, entity_type, entity_id, forecast_date,
    predicted_quantity, lower_bound, upper_bound, model_version,
    created_at, updated_at
) VALUES (
    %(organization_id)s, %(entity_type)s, %(entity_id)s, %(forecast_date)s,
    %(predicted_quantity)s, %(lower_bound)s, %(upper_bound)s, %(model_version)s,
    NOW(), NOW()
)
ON CONFLICT (organization_id, entity_type, entity_id, forecast_date, model_version)
DO UPDATE SET
    predicted_quantity = EXCLUDED.predicted_quantity,
    lower_bound = EXCLUDED.lower_bound,
    upper_bound = EXCLUDED.upper_bound,
    updated_at = NOW()
"""

UPSERT_RISK = """
INSERT INTO sp_stockout_risks (
    organization_id, entity_type, entity_id,
    current_stock, daily_burn_rate, days_until_stockout,
    risk_level, estimated_stockout_date, model_version,
    calculated_at, created_at, updated_at
) VALUES (
    %(organization_id)s, %(entity_type)s, %(entity_id)s,
    %(current_stock)s, %(daily_burn_rate)s, %(days_until_stockout)s,
    %(risk_level)s, %(estimated_stockout_date)s, %(model_version)s,
    NOW(), NOW(), NOW()
)
ON CONFLICT (organization_id, entity_type, entity_id)
DO UPDATE SET
    current_stock = EXCLUDED.current_stock,
    daily_burn_rate = EXCLUDED.daily_burn_rate,
    days_until_stockout = EXCLUDED.days_until_stockout,
    risk_level = EXCLUDED.risk_level,
    estimated_stockout_date = EXCLUDED.estimated_stockout_date,
    model_version = EXCLUDED.model_version,
    calculated_at = NOW(),
    updated_at = NOW()
"""

INSERT_RECOMMENDATION = """
INSERT INTO sp_restock_recommendations (
    organization_id, entity_type, entity_id,
    recommended_reorder_qty, urgency, reasoning, status, model_version,
    created_at, updated_at
) VALUES (
    %(organization_id)s, %(entity_type)s, %(entity_id)s,
    %(recommended_reorder_qty)s, %(urgency)s, %(reasoning)s, 'pending', %(model_version)s,
    NOW(), NOW()
)
"""

UPSERT_REGISTRY = """
INSERT INTO ml_model_registry (
    organization_id, model_name, model_version, entity_type, entity_id,
    framework, hyperparameters, metrics, artifact_path,
    training_rows, training_start, training_end, status, trained_at, created_at
) VALUES (
    %(organization_id)s, %(model_name)s, %(model_version)s, %(entity_type)s, %(entity_id)s,
    %(framework)s, CAST(%(hyperparameters)s AS jsonb), CAST(%(metrics)s AS jsonb),
    %(artifact_path)s, %(training_rows)s, %(training_start)s, %(training_end)s,
    %(status)s, NOW(), NOW()
)
ON CONFLICT (organization_id, model_name, model_version)
DO UPDATE SET
    entity_type = EXCLUDED.entity_type,
    entity_id = EXCLUDED.entity_id,
    framework = EXCLUDED.framework,
    hyperparameters = EXCLUDED.hyperparameters,
    metrics = EXCLUDED.metrics,
    artifact_path = EXCLUDED.artifact_path,
    training_rows = EXCLUDED.training_rows,
    training_start = EXCLUDED.training_start,
    training_end = EXCLUDED.training_end,
    status = EXCLUDED.status,
    trained_at = NOW()
"""

UPSERT_PREDICTION_LOG = """
INSERT INTO ml_prediction_log (
    organization_id, entity_type, entity_id, forecast_date,
    predicted_qty, model_version, run_id, predicted_at
) VALUES (
    %(organization_id)s, %(entity_type)s, %(entity_id)s, %(forecast_date)s,
    %(predicted_qty)s, %(model_version)s, %(run_id)s, NOW()
)
ON CONFLICT (organization_id, entity_type, entity_id, forecast_date, model_version)
DO UPDATE SET
    predicted_qty = EXCLUDED.predicted_qty,
    run_id = EXCLUDED.run_id,
    predicted_at = NOW()
"""


def upsert_forecasts(conn: Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_FORECAST, rows)
    return len(rows)


def upsert_risk(conn: Connection, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(UPSERT_RISK, row)


def replace_pending_recommendation(conn: Connection, row: dict[str, Any] | None, organization_id: int, entity_type: str, entity_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM sp_restock_recommendations
            WHERE organization_id = %s AND entity_type = %s AND entity_id = %s
              AND status = 'pending'
            """,
            (organization_id, entity_type, entity_id),
        )
        if row is not None:
            cur.execute(INSERT_RECOMMENDATION, row)


def retire_active_models(
    conn: Connection,
    organization_id: int,
    entity_type: str,
    entity_id: int,
    keep_version: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ml_model_registry
            SET status = 'retired'
            WHERE organization_id = %s
              AND entity_type = %s
              AND entity_id = %s
              AND status = 'active'
              AND model_version <> %s
            """,
            (organization_id, entity_type, entity_id, keep_version),
        )


def upsert_registry(conn: Connection, row: dict[str, Any]) -> None:
    payload = dict(row)
    payload["hyperparameters"] = json.dumps(payload["hyperparameters"], default=str)
    payload["metrics"] = json.dumps(payload["metrics"], default=str)
    with conn.cursor() as cur:
        cur.execute(UPSERT_REGISTRY, payload)


def upsert_prediction_log(conn: Connection, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_PREDICTION_LOG, rows)
    return len(rows)


def create_compute_run(
    conn: Connection,
    organization_id: int,
    trigger_source: str,
    run_key: str,
    total: int = 0,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sp_compute_runs (
                organization_id, status, trigger_source, total_entities,
                done_entities, progress_pct, run_id, started_at, created_at, updated_at
            ) VALUES (%s, 'running', %s, %s, 0, 0, %s, NOW(), NOW(), NOW())
            RETURNING id
            """,
            (organization_id, trigger_source, total, run_key),
        )
        row = cur.fetchone()

    return int(row["id"])


def update_compute_run(
    conn: Connection,
    run_id: int,
    *,
    status: str,
    done: int | None = None,
    total: int | None = None,
    run_key: str | None = None,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sp_compute_runs
            SET status = %s,
                done_entities = COALESCE(%s, done_entities),
                total_entities = COALESCE(%s, total_entities),
                progress_pct = CASE
                    WHEN COALESCE(%s, total_entities) > 0
                    THEN LEAST(100, ROUND(100.0 * COALESCE(%s, done_entities) / COALESCE(%s, total_entities)))::smallint
                    ELSE progress_pct
                END,
                run_id = COALESCE(%s, run_id),
                error = %s,
                started_at = COALESCE(started_at, NOW()),
                finished_at = CASE WHEN %s IN ('succeeded', 'failed') THEN NOW() ELSE finished_at END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (status, done, total, total, done, total, run_key, error, status, run_id),
        )
