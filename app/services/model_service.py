"""Model registry: the seam between the HTTP layer and your ML code.

Routes never import scikit-learn, PyTorch or any other framework directly. They ask
:data:`registry` for a predictor by name, which keeps the API stable while the modelling
side changes. To serve your own model, implement :class:`Predictor` and register it --
see the README ("Wiring in your own model").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from app.core.exceptions import ModelNotFoundError, ModelNotReadyError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PredictionResult:
    """Framework-agnostic prediction. ``prediction`` may hold any JSON-serialisable value."""

    prediction: Any
    probabilities: dict[str, float] | None = None
    model_version: str | None = None


@dataclass(slots=True, frozen=True)
class ModelDescriptor:
    name: str
    version: str | None = None
    description: str | None = None
    loaded: bool = False


@runtime_checkable
class Predictor(Protocol):
    """What a predictor must provide to be served by the API."""

    name: str
    version: str | None

    def load(self) -> None:
        """Load artifacts (weights, pipeline, encoders). Called once, lazily."""

    def is_loaded(self) -> bool:
        """Whether :meth:`load` has already succeeded."""

    def predict(self, features: dict[str, Any], options: dict[str, Any] | None = None) -> PredictionResult:
        """Score a single feature payload."""


class ModelRegistry:
    """Thread-safe mapping of model name to predictor."""

    def __init__(self) -> None:
        self._predictors: dict[str, Predictor] = {}
        self._lock = RLock()

    def register(self, predictor: Predictor, *, replace: bool = False) -> None:
        with self._lock:
            if predictor.name in self._predictors and not replace:
                raise ValueError(f"A predictor named '{predictor.name}' is already registered.")
            self._predictors[predictor.name] = predictor
        logger.info("Registered predictor '%s' (version=%s)", predictor.name, predictor.version)

    def unregister(self, name: str) -> None:
        with self._lock:
            self._predictors.pop(name, None)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._predictors)

    def describe(self) -> list[ModelDescriptor]:
        with self._lock:
            predictors = list(self._predictors.values())
        return [
            ModelDescriptor(
                name=predictor.name,
                version=getattr(predictor, "version", None),
                description=getattr(predictor, "description", None) or None,
                loaded=predictor.is_loaded(),
            )
            for predictor in predictors
        ]

    def get(self, name: str) -> Predictor:
        """Return a loaded predictor, loading it on first use."""
        with self._lock:
            predictor = self._predictors.get(name)
        if predictor is None:
            raise ModelNotFoundError(f"No model named '{name}' is registered.")
        if not predictor.is_loaded():
            try:
                predictor.load()
            except NotImplementedError as exc:
                raise ModelNotReadyError(
                    f"Model '{name}' has no artifacts loaded yet. Implement its loader to enable predictions."
                ) from exc
            except FileNotFoundError as exc:
                raise ModelNotReadyError(f"Model '{name}' artifacts are missing: {exc}") from exc
        return predictor

    def predict(
        self, name: str, features: dict[str, Any], options: dict[str, Any] | None = None
    ) -> PredictionResult:
        """Convenience wrapper around :meth:`get` used by the routes."""
        return self.get(name).predict(features, options)

    def clear(self) -> None:
        with self._lock:
            self._predictors.clear()


registry = ModelRegistry()


@dataclass(slots=True)
class DemoPredictor:
    """Deterministic placeholder so the endpoints are testable before a real model exists.

    It is deliberately trivial: it sums the numeric features and buckets the result. It is
    not a model and must be disabled in production (``ENABLE_DEMO_PREDICTOR=false``).
    """

    name: str = "demo"
    version: str = "0.0.0"
    description: str = "Placeholder scorer - replace with your own predictor."
    thresholds: tuple[float, float] = (1.0, 3.0)
    _loaded: bool = field(default=False, repr=False)

    def load(self) -> None:
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def predict(self, features: dict[str, Any], options: dict[str, Any] | None = None) -> PredictionResult:
        numeric = [float(value) for value in features.values() if isinstance(value, (int, float)) and not isinstance(value, bool)]
        score = sum(numeric)
        low, high = self.thresholds

        if score < low:
            label = "low"
        elif score < high:
            label = "medium"
        else:
            label = "high"

        probabilities = {"low": 0.0, "medium": 0.0, "high": 0.0}
        probabilities[label] = 1.0
        return PredictionResult(
            prediction={"label": label, "score": score, "feature_count": len(features)},
            probabilities=probabilities,
            model_version=self.version,
        )


def register_demo_predictor() -> None:
    registry.register(DemoPredictor(), replace=True)
