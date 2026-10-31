"""Model registry: artifact storage, run tracking and best-model selection."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime
from typing import Any

import joblib
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BaseForecaster, ModelMetrics
from app.services.supabase_client import download_artifact, upload_artifact


class ModelRegistry:
    """Track training runs in ``ml_model_registry`` and artifacts in Supabase Storage."""

    @staticmethod
    def select_best(metrics_by_model: dict[str, dict[str, Any]]) -> str | None:
        """Pick the model with the lowest MAE, ignoring entries without metrics."""
        scored = [
            (name, float(values["mae"]))
            for name, values in metrics_by_model.items()
            if values.get("mae") is not None
        ]
        return min(scored, key=lambda item: item[1])[0] if scored else None

    @staticmethod
    def run_version(
        model: BaseForecaster,
        entity_type: str,
        entity_id: int,
        at: datetime | None = None,
    ) -> str:
        stamp = (at or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        return f"{model.version}-{entity_type}-{entity_id}-{stamp}"

    @staticmethod
    def artifact_path(
        organization_id: int,
        model_name: str,
        version: str,
        entity_type: str,
        entity_id: int,
    ) -> str:
        return f"{organization_id}/{model_name}/{version}/{entity_type}-{entity_id}.joblib"

    async def save_artifact(
        self,
        model: BaseForecaster,
        organization_id: int,
        version: str,
        entity_type: str,
        entity_id: int,
    ) -> str:
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        path = self.artifact_path(organization_id, model.name, version, entity_type, entity_id)
        await upload_artifact(path, buffer.getvalue())
        return path

    async def load_artifact(self, path: str) -> BaseForecaster:
        data = await download_artifact(path)
        return joblib.load(io.BytesIO(data))

    async def register_run(
        self,
        session: AsyncSession,
        organization_id: int,
        model: BaseForecaster,
        metrics: ModelMetrics | dict[str, Any],
        entity_type: str,
        entity_id: int,
        training_rows: int,
        training_start: date,
        training_end: date,
        artifact_path: str | None = None,
        version: str | None = None,
    ) -> str:
        version = version or self.run_version(model, entity_type, entity_id)
        metric_dict = metrics.as_dict() if isinstance(metrics, ModelMetrics) else dict(metrics)
        hyperparameters = {**model.get_hyperparameters(), "algorithm_version": model.version}

        await session.execute(
            text(
                """
                INSERT INTO ml_model_registry (
                    organization_id, model_name, model_version, entity_type, entity_id,
                    framework, hyperparameters, metrics, artifact_path,
                    training_rows, training_start, training_end, status, trained_at, created_at
                ) VALUES (
                    :organization_id, :model_name, :model_version, :entity_type, :entity_id,
                    :framework, CAST(:hyperparameters AS jsonb), CAST(:metrics AS jsonb),
                    :artifact_path, :training_rows, :training_start, :training_end,
                    'active', NOW(), NOW()
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
            ),
            {
                "organization_id": organization_id,
                "model_name": model.name,
                "model_version": version,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "framework": type(model).__name__,
                "hyperparameters": json.dumps(hyperparameters),
                "metrics": json.dumps(metric_dict),
                "artifact_path": artifact_path,
                "training_rows": training_rows,
                "training_start": training_start,
                "training_end": training_end,
            },
        )
        await session.commit()
        return version

    async def best_for_entity(
        self,
        session: AsyncSession,
        organization_id: int,
        entity_type: str,
        entity_id: int,
    ) -> dict[str, Any] | None:
        result = await session.execute(
            text(
                """
                SELECT model_name, model_version, metrics, artifact_path
                FROM ml_model_registry
                WHERE organization_id = :organization_id
                  AND entity_type = :entity_type
                  AND entity_id = :entity_id
                  AND status = 'active'
                ORDER BY (metrics ->> 'mae')::numeric ASC NULLS LAST
                LIMIT 1
                """
            ),
            {
                "organization_id": organization_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
            },
        )
        row = result.mappings().first()
        return dict(row) if row else None
