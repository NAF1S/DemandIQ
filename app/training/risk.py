"""Stockout-risk and restock calculations (pure, database-free)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.training.trainer import EntityTraining


def build_risk(
    result: EntityTraining,
    organization_id: int,
    current_stock: float,
    periods: int,
) -> dict[str, Any]:
    daily_burn = round(
        sum(item.predicted_quantity for item in result.predictions) / max(periods, 1),
        4,
    )
    current_stock = round(float(current_stock), 4)
    days_until = round(current_stock / daily_burn, 2) if daily_burn > 0 else None

    if current_stock <= 0 or (days_until is not None and days_until < 3):
        risk_level = "critical"
    elif days_until is not None and days_until <= 7:
        risk_level = "warning"
    else:
        risk_level = "healthy"

    estimated = None
    if days_until is not None:
        estimated = date.today() + timedelta(days=max(0, int(math.floor(days_until))))

    return {
        "organization_id": organization_id,
        "entity_type": result.entity_type,
        "entity_id": result.entity_id,
        "current_stock": current_stock,
        "daily_burn_rate": daily_burn,
        "days_until_stockout": days_until,
        "risk_level": risk_level,
        "estimated_stockout_date": estimated,
        "model_version": result.version,
    }


def build_recommendation(
    result: EntityTraining,
    organization_id: int,
    risk: dict[str, Any],
) -> dict[str, Any] | None:
    if risk["risk_level"] not in ("critical", "warning"):
        return None

    daily_burn = float(risk["daily_burn_rate"])
    current_stock = float(risk["current_stock"])
    reorder_qty = round(max(0.0, daily_burn * 30.0 - current_stock), 4)
    urgency = "high" if risk["risk_level"] == "critical" else "medium"
    days = risk["days_until_stockout"]

    if days is None:
        reasoning = (
            f"{risk['risk_level'].title()} stockout risk at {current_stock:g} units available; "
            f"reorder {reorder_qty:g} units for 30 days of cover."
        )
    else:
        reasoning = (
            f"{risk['risk_level'].title()} stockout risk: {days:g} days of cover at "
            f"{daily_burn:g} units/day; reorder {reorder_qty:g} units for 30 days."
        )

    return {
        "organization_id": organization_id,
        "entity_type": result.entity_type,
        "entity_id": result.entity_id,
        "recommended_reorder_qty": reorder_qty,
        "urgency": urgency,
        "reasoning": reasoning,
        "model_version": result.version,
    }


def stock_for(
    stock: dict[tuple[str, int], float],
    result: EntityTraining,
) -> float:
    key = (result.entity_type, result.entity_id)
    if key in stock:
        return stock[key]

    last = result.frame.iloc[-1]
    value = last.get("stock_available")
    if value is None or pd.isna(value):
        return 0.0
    return float(value)
