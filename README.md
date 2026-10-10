# DemandIQ API

FastAPI service that connects to **Supabase** (Postgres + Storage + Auth) and serves **ML predictions**.

- **Postgres (direct, async):** all relational reads/writes via SQLAlchemy 2.0 + asyncpg.
- **Supabase REST:** Storage for model artifacts, Auth for access-token verification.
- **Predictions:** a model registry decouples the HTTP layer from whichever ML framework you use.

## Layout

```
app/
  main.py                  FastAPI app factory, lifespan, middleware, routers
  core/
    config.py              pydantic-settings configuration (.env)
    logging.py             logging setup
    exceptions.py          AppError hierarchy + JSON handlers
  db/
    base.py                Declarative base
    session.py             async engine/session, DSN normalisation
    models.py              ORM models (PredictionLog)
    repositories.py        query helpers
    init_db.py             create_all helper for local dev
  schemas/                 request/response models
  services/
    supabase_client.py     Storage + Auth integration
    model_service.py       model registry + Predictor protocol
  api/
    deps.py                shared dependencies (db session, user, registry)
    v1/
      health.py            /health, /health/ready
      predictions.py       /predictions/*, /predictions/logs
      storage.py           /storage/*
      router.py            v1 router aggregation
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `.env` from your Supabase dashboard:

| Variable | Where to find it |
| --- | --- |
| `SUPABASE_URL` | Project Settings → API → Project URL |
| `SUPABASE_ANON_KEY` | Project Settings → API → anon/public key |
| `SUPABASE_SERVICE_ROLE_KEY` | Project Settings → API → service_role key (**server-side only**) |
| `DATABASE_URL` | Connect → Transaction pooler (port `6543`) or Direct connection |

`DATABASE_URL` can be pasted verbatim: the app rewrites `postgres://` to `postgresql+asyncpg://`
and converts `sslmode`/`pgbouncer` parameters into the `connect_args` asyncpg expects.

Create the schema (or run the equivalent SQL in the Supabase SQL editor):

```powershell
python -m app.db.init_db
```

## Run

```powershell
uvicorn app.main:app --reload
```

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/ready` | Checks Postgres + Supabase reachability (503 when degraded) |
| `GET` | `/api/v1/predictions/models` | List registered models |
| `POST` | `/api/v1/predictions/{model_name}` | Score a feature payload and log it |
| `GET` | `/api/v1/predictions/logs` | Recent rows from `prediction_logs` |
| `GET` | `/api/v1/storage/objects` | List model artifacts |
| `POST` | `/api/v1/storage/objects/{path}` | Upload an artifact (requires Supabase token) |
| `GET` | `/api/v1/storage/objects/{path}` | Download an artifact (requires Supabase token) |
| `GET` | `/api/v1/storage/objects/{path}/signed-url` | Temporary download URL |
| `DELETE` | `/api/v1/storage/objects?paths=a&paths=b` | Delete artifacts |

Smoke test with the placeholder predictor (enabled while `ENABLE_DEMO_PREDICTOR=true`):

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/predictions/demo `
  -H "Content-Type: application/json" `
  -d '{\"features\": {\"units_last_7d\": 120, \"promo\": 18}, \"entity_id\": \"SKU-1001\"}'
```

## Wiring in your own model

Implement the `Predictor` protocol and register it during startup in
[app/main.py](app/main.py) ([`lifespan`](app/main.py:32)). Nothing else changes.

```python
# app/services/demand_predictor.py
import joblib
from app.services.model_service import PredictionResult, registry


class DemandPredictor:
    name = "demand-forecast"
    version = "1.2.0"
    description = "LightGBM SKU-level demand forecast"

    def __init__(self) -> None:
        self._pipeline = None

    def load(self) -> None:
        # Local file, or pull the artifact from Supabase Storage:
        #   from app.services.supabase_client import download_artifact
        #   path = Path("models/demand.joblib")
        #   path.write_bytes(await download_artifact("demand-forecast/demand.joblib"))
        self._pipeline = joblib.load("models/demand.joblib")

    def is_loaded(self) -> bool:
        return self._pipeline is not None

    def predict(self, features, options=None):
        import pandas as pd

        frame = pd.DataFrame([features])
        value = float(self._pipeline.predict(frame)[0])
        return PredictionResult(prediction={"units": value}, model_version=self.version)


registry.register(DemandPredictor())


```

Then `POST /api/v1/predictions/demand-forecast` serves it. Key contract details:

- `predict` receives the raw `features` dict and must return a `PredictionResult`.
- Anything you return inside `prediction` must be JSON-serialisable — convert NumPy scalars
  (`.item()`) before returning.
- Set `ENABLE_DEMO_PREDICTOR=false` once a real model is registered.
- A predictor is loaded lazily on first request; raise `NotImplementedError` or
  `FileNotFoundError` from `load()` and the API answers `503 model_not_ready`.

## Notes

- Routes that use `OptionalDbSession` (predictions) keep working when `DATABASE_URL` is
  unset; only log persistence is skipped. Routes using `DbSession` return
  `503 not_configured`.
- Writes to Supabase Storage and admin Auth calls need `SUPABASE_SERVICE_ROLE_KEY`. Never
  ship that key to a browser or mobile client.
- Predicted values are logged to `prediction_logs` as JSONB together with the exact inputs,
  so predictions can later be joined against actuals for drift and accuracy monitoring.
