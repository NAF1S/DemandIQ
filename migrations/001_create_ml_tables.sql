-- DemandIQ-owned ML feature / registry / audit tables.
-- Additive only: the three existing sp_* prediction tables are untouched here.
--
-- Apply with:
--   psql "$DATABASE_URL" -f migrations/001_create_ml_tables.sql

CREATE TABLE IF NOT EXISTS ml_daily_features (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES sp_organizations(id) ON DELETE CASCADE,
    store_id        BIGINT NOT NULL REFERENCES sp_store_connections(id) ON DELETE CASCADE,
    entity_type     VARCHAR(32) NOT NULL,
    entity_id       BIGINT NOT NULL,
    feature_date    DATE NOT NULL,

    units_consumed      NUMERIC(14,4) DEFAULT 0,
    units_returned      NUMERIC(14,4) DEFAULT 0,
    units_reserved      NUMERIC(14,4) DEFAULT 0,
    order_count         INT DEFAULT 0,
    revenue             NUMERIC(14,4) DEFAULT 0,
    avg_order_value     NUMERIC(14,4) DEFAULT 0,

    stock_on_hand       NUMERIC(14,4),
    stock_reserved      NUMERIC(14,4),
    stock_available     NUMERIC(14,4),

    page_views          INT DEFAULT 0,
    add_to_cart         INT DEFAULT 0,
    cart_removals       INT DEFAULT 0,
    sessions            INT DEFAULT 0,
    unique_visitors     INT DEFAULT 0,
    traffic_sources     JSONB DEFAULT '{}'::jsonb,

    ad_spend            NUMERIC(14,4),
    ad_impressions      INT,
    ad_clicks           INT,

    is_weekend          BOOLEAN DEFAULT FALSE,
    is_holiday          BOOLEAN DEFAULT FALSE,
    holiday_name        VARCHAR(128),
    days_to_next_holiday INT,
    is_payday           BOOLEAN DEFAULT FALSE,
    is_festival_period  BOOLEAN DEFAULT FALSE,
    festival_name       VARCHAR(128),

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT ml_daily_features_unique
        UNIQUE (organization_id, store_id, entity_type, entity_id, feature_date)
);

CREATE INDEX IF NOT EXISTS idx_ml_daily_features_lookup
    ON ml_daily_features (organization_id, store_id, entity_type, entity_id, feature_date);

CREATE TABLE IF NOT EXISTS ml_model_registry (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES sp_organizations(id) ON DELETE CASCADE,
    model_name      VARCHAR(128) NOT NULL,
    model_version   VARCHAR(64) NOT NULL,
    entity_type     VARCHAR(32),
    entity_id       BIGINT,
    framework       VARCHAR(64) NOT NULL,
    hyperparameters JSONB DEFAULT '{}'::jsonb,
    metrics         JSONB DEFAULT '{}'::jsonb,
    artifact_path   VARCHAR(512),
    training_rows   INT,
    training_start  DATE,
    training_end    DATE,
    status          VARCHAR(32) DEFAULT 'active',
    trained_at      TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT ml_model_registry_unique
        UNIQUE (organization_id, model_name, model_version)
);

CREATE TABLE IF NOT EXISTS ml_prediction_log (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES sp_organizations(id) ON DELETE CASCADE,
    entity_type     VARCHAR(32) NOT NULL,
    entity_id       BIGINT NOT NULL,
    forecast_date   DATE NOT NULL,
    predicted_qty   NUMERIC(14,4),
    actual_qty      NUMERIC(14,4),
    model_version   VARCHAR(64) NOT NULL DEFAULT 'unknown',
    residual        NUMERIC(14,4),
    run_id          VARCHAR(64),
    predicted_at    TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT ml_prediction_log_unique
        UNIQUE (organization_id, entity_type, entity_id, forecast_date, model_version)
);

CREATE TABLE IF NOT EXISTS ml_calendar_events (
    id              BIGSERIAL PRIMARY KEY,
    organization_id BIGINT REFERENCES sp_organizations(id) ON DELETE CASCADE,
    event_date      DATE NOT NULL,
    event_type      VARCHAR(64) NOT NULL,
    event_name      VARCHAR(256) NOT NULL,
    country_code    VARCHAR(8) DEFAULT 'BD',
    impact_factor   NUMERIC(4,2) DEFAULT 1.0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
