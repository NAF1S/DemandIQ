"""Backfill realized demand into the prediction audit log."""

from __future__ import annotations

from typing import Any

from app.training.pipeline import connect


def backfill_prediction_actuals(organization_id: int | None = None) -> int:
    """Fill ``ml_prediction_log`` actuals from the cleaned feature matrix."""
    with connect() as conn:
        with conn.cursor() as cur:
            params: list[Any] = []
            org_filter = ""
            if organization_id is not None:
                org_filter = " AND pl.organization_id = %s"
                params.append(organization_id)

            cur.execute(
                f"""
                UPDATE ml_prediction_log pl
                SET actual_qty = COALESCE((
                        SELECT SUM(f.units_consumed)
                        FROM ml_daily_features f
                        WHERE f.organization_id = pl.organization_id
                          AND f.entity_type = pl.entity_type
                          AND f.entity_id = pl.entity_id
                          AND f.feature_date = pl.forecast_date
                    ), 0),
                    residual = COALESCE((
                        SELECT SUM(f.units_consumed)
                        FROM ml_daily_features f
                        WHERE f.organization_id = pl.organization_id
                          AND f.entity_type = pl.entity_type
                          AND f.entity_id = pl.entity_id
                          AND f.feature_date = pl.forecast_date
                    ), 0) - pl.predicted_qty
                WHERE pl.forecast_date < CURRENT_DATE
                  AND pl.actual_qty IS NULL
                  {org_filter}
                """,
                params,
            )
            updated = cur.rowcount
        conn.commit()

    return int(updated or 0)
