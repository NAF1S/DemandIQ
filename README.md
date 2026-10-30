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

### Connecting on a network without IPv6

`db.<project-ref>.supabase.co` resolves to an **IPv6-only** address. If your machine has no
IPv6 route, the direct connection will fail with a DNS/connect error even though the
credentials are correct. Use the IPv4-compatible Supavisor pooler instead:

```
postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

- `<region>` is the project's region (`ap-southeast-1`, `us-east-1`, …). Check it in
  Project Settings → General.
- Use port **5432** for a long-running server. Port **6543** is transaction mode, which
  does not support prepared statements — the app detects it and disables asyncpg's
  statement cache automatically.
- `sslmode=require` encrypts but does not validate the certificate, which is what libpq
  does and what Supavisor needs. Raise it to `verify-full` to enforce validation.

Create the schema (or run the equivalent SQL in the Supabase SQL editor):

```powershell
python -m app.db.init_db
```

## Phase 1 — data audit

The read-only audit validates the live StockPilot Postgres database and the three
Jetpack traffic exports in `stock_pilot/web-traffic-data/`. It never writes to the
database.

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... python -m app.etl.audit --org 1 \
  --traffic-dir ../web-traffic-data
```

It writes `reports/phase1-audit.json` and `reports/phase1-audit.md`, and prints a
summary including the usable order/traffic overlap window.

The canonical extractors live in `app/etl/extractors.py`; the parameterised SQL is
in `app/etl/queries.py`. Consumption is based on `movement_type='confirm'`,
`qty_delta < 0`, buckets by **event time** (`COALESCE(occurred_at, created_at)`),
nets same-day returns, and skips `sp_movement_exclusions`.

Tests (no database required):

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Phase 3 — historical traffic backfill

Apply the DemandIQ-owned progress/miss tables once:

```bash
psql "$DATABASE_URL" -f migrations/002_ml_backfill_jobs.sql
```

Validate first, then import the three Jetpack exports:

```bash
python -m cli backfill-traffic --dir ../web-traffic-data --org 1 --store 1 --dry-run
python -m cli backfill-traffic --dir ../web-traffic-data --org 1 --store 1
```

The import is idempotent (Postgres `ON CONFLICT`), records per-file progress in
`ml_backfill_jobs`, logs unmatched product rows in `ml_traffic_mapping_misses`, and
never inserts an unmapped product into `sp_traffic_daily`. Mapping is exact
`Post_ID → woo_product_id`, with `sp_products.slug` (when that column exists) and
normalised-title fallbacks. `jetpack` is authoritative through 2026-10-26;
`plugin_realtime` covers later dates and is a cross-check.

## Phase 4 — ML schema

Apply the DemandIQ-owned ML tables once:

```bash
psql "$DATABASE_URL" -f migrations/001_create_ml_tables.sql
```

- `ml_daily_features` — the core store-scoped training table
  (`organization_id, store_id, entity_type, entity_id, feature_date`).
- `ml_model_registry`, `ml_prediction_log`, `ml_calendar_events`.
- The portal migrations add `sp_products.slug`/`permalink`, the ML unique indexes,
  and the multi-store order key `(organization_id, store_id, woocommerce_id)`.

The ORM models live in `app/db/models.py`; they describe the live schema for the
feature builder and training pipeline.

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
