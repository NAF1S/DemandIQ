-- DemandIQ-owned tables (not Laravel migrations).
--
-- ml_backfill_jobs: resumable progress for each imported file.
-- ml_traffic_mapping_misses: product-page rows that could not be resolved to
-- sp_products (never inserted into sp_traffic_daily because product_id is NOT NULL).
--
-- Apply with:
--   psql "$DATABASE_URL" -f migrations/002_ml_backfill_jobs.sql

CREATE TABLE IF NOT EXISTS ml_backfill_jobs (
    id BIGSERIAL PRIMARY KEY,
    file TEXT NOT NULL,
    date_from DATE,
    date_to DATE,
    status TEXT NOT NULL DEFAULT 'running',
    rows INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ml_backfill_jobs_file_idx
    ON ml_backfill_jobs (file, started_at DESC);

CREATE TABLE IF NOT EXISTS ml_traffic_mapping_misses (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    store_id BIGINT NOT NULL,
    traffic_date DATE NOT NULL,
    post_id BIGINT NOT NULL,
    title TEXT,
    url TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ml_traffic_mapping_misses_unique
        UNIQUE (organization_id, store_id, traffic_date, post_id, url)
);

CREATE INDEX IF NOT EXISTS ml_traffic_mapping_misses_date_idx
    ON ml_traffic_mapping_misses (traffic_date);
