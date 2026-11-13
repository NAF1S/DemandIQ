"""Modal deployment for DemandIQ training and prediction backfill.

Run one training pass:
    modal run modal_app.py::train --organization-id 1 --store-id 1 --periods 14

Deploy the daily schedule:
    modal deploy modal_app.py
"""

from __future__ import annotations

from typing import Any

import modal

APP_NAME = "demandiq-forecasting"

app = modal.App(APP_NAME)

models_volume = modal.Volume.from_name("demandiq-models", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgomp1")
    .pip_install(
        "pandas>=2.0",
        "numpy>=1.26",
        "prophet>=1.1.5",
        "scikit-learn>=1.5",
        "lightgbm>=4.5",
        "joblib>=1.4",
        "psycopg[binary]>=3.2",
    )
    .add_local_python_source("app")
)

secrets = [modal.Secret.from_name("demandiq-supabase")]

trigger_image = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi")


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    timeout=1800,
    memory=4096,
)
def train(
    organization_id: int = 1,
    store_id: int = 1,
    periods: int = 14,
    engine: str = "auto",
    run_id: str | None = None,
    compute_run_id: int | None = None,
    trigger_source: str = "manual",
) -> dict[str, Any]:
    """Train all entities and write forecasts, risks, registry and logs."""
    from app.training.pipeline import run_training

    summary = run_training(
        organization_id=organization_id,
        store_id=store_id,
        periods=periods,
        engine=engine,
        artifact_dir="/models",
        run_id=run_id,
        compute_run_id=compute_run_id,
        trigger_source=None if compute_run_id is not None else trigger_source,
    )
    models_volume.commit()
    return summary


@app.function(
    image=trigger_image,
    secrets=[modal.Secret.from_name("demandiq-train-token")],
    timeout=60,
)
@modal.fastapi_endpoint(method="POST")
def trigger(payload: dict[str, Any]) -> dict[str, Any]:
    """Authenticated webhook used by the portal's Compute now button."""
    import os

    from fastapi import HTTPException

    if payload.get("token") != os.environ.get("TRAIN_TOKEN"):
        raise HTTPException(status_code=401, detail="unauthorized")

    train.spawn(
        organization_id=int(payload.get("organization_id", 1)),
        store_id=int(payload.get("store_id", 1)),
        periods=int(payload.get("periods", 14)),
        engine=str(payload.get("engine", "auto")),
        compute_run_id=(
            int(payload["compute_run_id"])
            if payload.get("compute_run_id") is not None
            else None
        ),
        trigger_source="manual",
    )

    return {"status": "queued"}


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    timeout=1800,
    memory=4096,
)
def build_features(
    organization_id: int = 1,
    store_id: int = 1,
    lookback_days: int = 365,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Refresh the daily feature matrix through a direct Postgres connection."""
    from app.etl.pipeline import run_direct_feature_pipeline
    from app.training.pipeline import connect

    with connect() as conn:
        summary = run_direct_feature_pipeline(
            conn,
            organization_id=organization_id,
            store_id=store_id,
            lookback_days=lookback_days,
            dry_run=dry_run,
        )

    print(
        f"features {summary['start_date']} → {summary['end_date']}: "
        f"{summary['feature_rows']} rows for {summary['entities']} entities, "
        f"{summary['rows_written']} written",
        flush=True,
    )

    summary.pop("quality", None)
    return summary


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    timeout=900,
    memory=1024,
)
def features_and_training(
    organization_id: int = 1,
    store_id: int = 1,
    periods: int = 14,
    engine: str = "auto",
    lookback_days: int = 365,
) -> dict[str, Any]:
    """Build features, then train, so the forecast horizon always starts today."""
    features = build_features.remote(
        organization_id=organization_id,
        store_id=store_id,
        lookback_days=lookback_days,
    )

    summary = train.remote(
        organization_id=organization_id,
        store_id=store_id,
        periods=periods,
        engine=engine,
        trigger_source="manual",
    )

    return {
        "features": {
            "rows_written": features["rows_written"],
            "end_date": features["end_date"],
            "entities": features["entities"],
        },
        "run": {
            "run_id": summary["run_id"],
            "trained": summary["trained"],
            "models": summary["models"],
        },
    }


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    timeout=300,
    memory=1024,
)
def health(organization_id: int = 1, store_id: int = 1, max_stale_hours: int = 36) -> dict[str, Any]:
    """Report pipeline freshness; raises when the pipeline is stale."""
    from app.monitoring.health import pipeline_health
    from app.training.pipeline import connect

    with connect() as conn:
        report = pipeline_health(
            conn,
            organization_id=organization_id,
            store_id=store_id,
            max_stale_hours=max_stale_hours,
        )

    print(
        f"pipeline {report['status']}: features {report['last_feature_date']} "
        f"({report['feature_lag_days']}d), forecasts {report['forecast_age_hours']}h old, "
        f"risks {report['risk_age_hours']}h old; problems={report['problems']}",
        flush=True,
    )

    if report["status"] != "ok":
        raise RuntimeError(f"DemandIQ pipeline is stale: {', '.join(report['problems'])}")

    return report


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    schedule=modal.Cron("0 1 * * *"),
    timeout=1800,
    memory=4096,
)
def daily_features() -> dict[str, Any]:
    """Daily 01:00 UTC feature build, one hour before training."""
    from app.etl.pipeline import run_direct_feature_pipeline
    from app.training.pipeline import connect

    with connect() as conn:
        summary = run_direct_feature_pipeline(conn, organization_id=1, store_id=1, lookback_days=365)

    print(
        f"daily features {summary['start_date']} → {summary['end_date']}: "
        f"{summary['rows_written']} rows written",
        flush=True,
    )

    return {
        "start_date": summary["start_date"],
        "end_date": summary["end_date"],
        "entities": summary["entities"],
        "feature_rows": summary["feature_rows"],
        "rows_written": summary["rows_written"],
    }


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    schedule=modal.Cron("0 2 * * *"),
    timeout=1800,
    memory=4096,
)
def daily_training() -> dict[str, Any]:
    """Daily 02:00 UTC training run."""
    from app.training.pipeline import run_training

    summary = run_training(
        organization_id=1,
        store_id=1,
        periods=14,
        engine="auto",
        artifact_dir="/models",
        trigger_source="cron",
    )
    models_volume.commit()
    return {
        "run_id": summary["run_id"],
        "entities": summary["entities"],
        "trained": summary["trained"],
        "models": summary["models"],
        "forecast_rows": summary["forecast_rows"],
    }


@app.function(
    image=image,
    secrets=secrets,
    volumes={"/models": models_volume},
    timeout=1800,
    memory=4096,
)
def evaluate(
    organization_id: int = 1,
    store_id: int = 1,
    periods: int = 14,
    n_splits: int = 3,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run walk-forward comparison and traffic ablation; persist the report."""
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from app.evaluation.compare import compare_all
    from app.training import writers
    from app.training.pipeline import connect, load_features

    started = datetime.now(UTC)
    evaluation_id = f"eval-{started.strftime('%Y%m%dT%H%M%SZ')}"

    with connect() as conn:
        features = load_features(conn, organization_id, store_id)
        report = compare_all(
            features,
            organization_id=organization_id,
            store_id=store_id,
            test_periods=periods,
            n_splits=n_splits,
            limit=limit,
        )

        for entity in report["entities"]:
            for name, values in entity["models"].items():
                if "metrics" not in values:
                    continue
                base_name = name.replace("_no_traffic", "")
                suffix = "-no-traffic" if name.endswith("_no_traffic") else ""
                writers.upsert_registry(
                    conn,
                    {
                        "organization_id": organization_id,
                        "model_name": base_name,
                        "model_version": (
                            f"{values['version']}-{entity['entity_type']}-"
                            f"{entity['entity_id']}-{evaluation_id}{suffix}"
                        ),
                        "entity_type": entity["entity_type"],
                        "entity_id": entity["entity_id"],
                        "framework": name,
                        "hyperparameters": {
                            "evaluation": "walk_forward",
                            "splits": values["splits"],
                            "traffic_used": not name.endswith("_no_traffic"),
                        },
                        "metrics": {
                            **values["metrics"],
                            "folds": values.get("folds", []),
                        },
                        "artifact_path": None,
                        "training_rows": None,
                        "training_start": None,
                        "training_end": None,
                        "status": "evaluated",
                    },
                )
        conn.commit()

    report["evaluation_id"] = evaluation_id
    report_path = Path("/models/reports") / f"{evaluation_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    models_volume.commit()

    return {
        "evaluation_id": evaluation_id,
        "entity_count": report["entity_count"],
        "best_models": report["best_models"],
        "average_traffic_gain_wape": report["average_traffic_gain_wape"],
        "errors": len(report["errors"]),
        "report_path": str(report_path),
    }


@app.function(
    image=image,
    secrets=secrets,
    schedule=modal.Cron("0 6 * * *"),
    timeout=300,
    memory=1024,
)
def backfill_actuals() -> int:
    """Daily 06:00 UTC actual-vs-prediction backfill."""
    from app.evaluation.backfill import backfill_prediction_actuals

    return backfill_prediction_actuals()
