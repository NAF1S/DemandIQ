from sqlalchemy import UniqueConstraint

from app.db.models import (
    Base,
    MlBackfillJob,
    MlCalendarEvent,
    MlDailyFeature,
    MlModelRegistry,
    MlPredictionLog,
    MlTrafficMappingMiss,
)


def test_all_ml_tables_are_registered():
    assert set(Base.metadata.tables) >= {
        "ml_daily_features",
        "ml_model_registry",
        "ml_prediction_log",
        "ml_calendar_events",
        "ml_backfill_jobs",
        "ml_traffic_mapping_misses",
    }


def test_daily_features_has_store_scoped_unique_key():
    table = MlDailyFeature.__table__

    assert {"organization_id", "store_id", "entity_type", "entity_id", "feature_date"} <= set(table.c.keys())
    assert "traffic_sources" in table.c
    assert "page_views" in table.c

    names = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("entity_id", "entity_type", "feature_date", "organization_id", "store_id") in names


def test_prediction_log_version_is_not_nullable():
    assert MlPredictionLog.__table__.c.model_version.nullable is False
    assert MlPredictionLog.__tablename__ == "ml_prediction_log"


def test_registry_and_calendar_tables_exist():
    assert MlModelRegistry.__tablename__ == "ml_model_registry"
    assert MlCalendarEvent.__tablename__ == "ml_calendar_events"
    assert MlCalendarEvent.__table__.c.organization_id.nullable is True


def test_backfill_and_miss_tables_exist():
    assert MlBackfillJob.__tablename__ == "ml_backfill_jobs"
    assert MlTrafficMappingMiss.__tablename__ == "ml_traffic_mapping_misses"
