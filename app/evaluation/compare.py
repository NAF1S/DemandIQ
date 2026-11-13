"""Per-entity model comparison and traffic ablation for Phase 8."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.etl.quality import validate_features
from app.evaluation.backtest import walk_forward_summary
from app.models.features import prepare_features

TRAFFIC_COLUMNS = ("page_views", "add_to_cart", "cart_removals", "sessions", "unique_visitors")


def candidate_models(frame: pd.DataFrame) -> list[str]:
    prepared = prepare_features(frame)
    quality = validate_features(prepared, 0)
    traffic = float(pd.to_numeric(prepared.get("page_views"), errors="coerce").fillna(0).sum())
    sales_days = int(quality["non_zero_days"])
    total_days = int(quality["total_days"])

    models = ["velocity"]
    if total_days >= 14 and sales_days >= 5:
        models.append("prophet")
    if sales_days >= 20:
        models.append("lightgbm")
    if total_days >= 14 and traffic > 0:
        models.append("funnel")

    return list(dict.fromkeys(models))


def strip_traffic(frame: pd.DataFrame) -> pd.DataFrame:
    """Zero the raw traffic inputs; the feature builder rebuilds lagged/rolling zeros."""
    out = frame.copy()
    for column in TRAFFIC_COLUMNS:
        if column in out.columns:
            out[column] = 0
    return out


def compare_entity(
    features: pd.DataFrame,
    entity_type: str,
    entity_id: int,
    test_periods: int = 14,
    n_splits: int = 3,
) -> dict[str, Any]:
    prepared = prepare_features(features)
    traffic_total = float(
        pd.to_numeric(prepared.get("page_views"), errors="coerce").fillna(0).sum()
    )
    results: dict[str, Any] = {}

    for name in candidate_models(features):
        results[name] = walk_forward_summary(
            features, name, test_periods=test_periods, n_splits=n_splits
        )
        if name in ("lightgbm", "prophet") and traffic_total > 0:
            results[f"{name}_no_traffic"] = walk_forward_summary(
                strip_traffic(features),
                name,
                test_periods=test_periods,
                n_splits=n_splits,
            )

    scored = [
        (name, values["metrics"]["wape"], values["metrics"]["mae"])
        for name, values in results.items()
        if "metrics" in values and not name.endswith("_no_traffic")
    ]
    best = min(scored, key=lambda item: (item[1], item[2])) if scored else None

    ablation: dict[str, Any] = {}
    for name in ("lightgbm", "prophet"):
        with_traffic = results.get(name, {}).get("metrics")
        without_traffic = results.get(f"{name}_no_traffic", {}).get("metrics")
        if with_traffic and without_traffic:
            ablation[name] = {
                "with_wape": with_traffic["wape"],
                "without_wape": without_traffic["wape"],
                "traffic_gain_wape": round(without_traffic["wape"] - with_traffic["wape"], 4),
                "with_mae": with_traffic["mae"],
                "without_mae": without_traffic["mae"],
            }

    return {
        "entity_type": entity_type,
        "entity_id": int(entity_id),
        "best_model": best[0] if best else None,
        "best_wape": best[1] if best else None,
        "models": results,
        "traffic_ablation": ablation,
    }


def compare_all(
    features: pd.DataFrame,
    organization_id: int = 1,
    store_id: int = 1,
    test_periods: int = 14,
    n_splits: int = 3,
    limit: int | None = None,
) -> dict[str, Any]:
    units = (
        features.assign(
            _units=pd.to_numeric(features.get("units_consumed"), errors="coerce").fillna(0)
        )
        .groupby(["entity_type", "entity_id"])["_units"]
        .sum()
        .sort_values(ascending=False)
    )

    if limit is not None:
        units = units.head(int(limit))

    entities = []
    for (entity_type, entity_id), _ in units.items():
        group = features[
            (features["entity_type"] == entity_type) & (features["entity_id"] == entity_id)
        ]
        entities.append(
            compare_entity(
                group,
                str(entity_type),
                int(entity_id),
                test_periods=test_periods,
                n_splits=n_splits,
            )
        )

    best_models = Counter(item["best_model"] for item in entities if item["best_model"])
    errors = [
        {
            "entity": f"{item['entity_type']}:{item['entity_id']}",
            "model": name,
            "error": values.get("error"),
        }
        for item in entities
        for name, values in item["models"].items()
        if values.get("error")
    ]

    traffic_gains = [
        values["traffic_gain_wape"]
        for item in entities
        for values in item["traffic_ablation"].values()
    ]

    return {
        "organization_id": organization_id,
        "store_id": store_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "test_periods": test_periods,
        "n_splits": n_splits,
        "entity_count": len(entities),
        "best_models": dict(best_models),
        "average_traffic_gain_wape": round(sum(traffic_gains) / len(traffic_gains), 4)
        if traffic_gains
        else None,
        "errors": errors,
        "entities": entities,
    }
