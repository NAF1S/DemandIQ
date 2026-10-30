"""SQLAlchemy ORM models for DemandIQ-owned ML tables.

These tables are created by the SQL migrations in ``migrations/`` (not by
Laravel); the models describe the existing schema for typed reads/writes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MlDailyFeature(Base):
    __tablename__ = "ml_daily_features"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sp_organizations.id", ondelete="CASCADE"), nullable=False
    )
    store_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sp_store_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    feature_date: Mapped[date] = mapped_column(Date, nullable=False)

    units_consumed: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=0)
    units_returned: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=0)
    units_reserved: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=0)
    order_count: Mapped[int | None] = mapped_column(Integer, default=0)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=0)
    avg_order_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4), default=0)

    stock_on_hand: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    stock_reserved: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    stock_available: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    page_views: Mapped[int | None] = mapped_column(Integer, default=0)
    add_to_cart: Mapped[int | None] = mapped_column(Integer, default=0)
    cart_removals: Mapped[int | None] = mapped_column(Integer, default=0)
    sessions: Mapped[int | None] = mapped_column(Integer, default=0)
    unique_visitors: Mapped[int | None] = mapped_column(Integer, default=0)
    traffic_sources: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    ad_spend: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ad_impressions: Mapped[int | None] = mapped_column(Integer)
    ad_clicks: Mapped[int | None] = mapped_column(Integer)

    is_weekend: Mapped[bool | None] = mapped_column(Boolean, default=False)
    is_holiday: Mapped[bool | None] = mapped_column(Boolean, default=False)
    holiday_name: Mapped[str | None] = mapped_column(String(128))
    days_to_next_holiday: Mapped[int | None] = mapped_column(Integer)
    is_payday: Mapped[bool | None] = mapped_column(Boolean, default=False)
    is_festival_period: Mapped[bool | None] = mapped_column(Boolean, default=False)
    festival_name: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "store_id",
            "entity_type",
            "entity_id",
            "feature_date",
            name="ml_daily_features_unique",
        ),
        Index(
            "idx_ml_daily_features_lookup",
            "organization_id",
            "store_id",
            "entity_type",
            "entity_id",
            "feature_date",
        ),
    )


class MlModelRegistry(Base):
    __tablename__ = "ml_model_registry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sp_organizations.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    framework: Mapped[str] = mapped_column(String(64), nullable=False)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    artifact_path: Mapped[str | None] = mapped_column(String(512))
    training_rows: Mapped[int | None] = mapped_column(Integer)
    training_start: Mapped[date | None] = mapped_column(Date)
    training_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(32), default="active")
    trained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "model_name",
            "model_version",
            name="ml_model_registry_unique",
        ),
    )


class MlPredictionLog(Base):
    __tablename__ = "ml_prediction_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sp_organizations.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    predicted_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    actual_qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    model_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unknown", server_default=text("'unknown'")
    )
    residual: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    run_id: Mapped[str | None] = mapped_column(String(64))
    predicted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "entity_id",
            "forecast_date",
            "model_version",
            name="ml_prediction_log_unique",
        ),
    )


class MlCalendarEvent(Base):
    __tablename__ = "ml_calendar_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sp_organizations.id", ondelete="CASCADE")
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_name: Mapped[str] = mapped_column(String(256), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(8), default="BD")
    impact_factor: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), default=1.0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MlBackfillJob(Base):
    __tablename__ = "ml_backfill_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file: Mapped[str] = mapped_column(Text, nullable=False)
    date_from: Mapped[date | None] = mapped_column(Date)
    date_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ml_backfill_jobs_file_idx", "file", "started_at"),
    )


class MlTrafficMappingMiss(Base):
    __tablename__ = "ml_traffic_mapping_misses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    store_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    traffic_date: Mapped[date] = mapped_column(Date, nullable=False)
    post_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "store_id",
            "traffic_date",
            "post_id",
            "url",
            name="ml_traffic_mapping_misses_unique",
        ),
        Index("ml_traffic_mapping_misses_date_idx", "traffic_date"),
    )


__all__ = [
    "Base",
    "MlCalendarEvent",
    "MlDailyFeature",
    "MlBackfillJob",
    "MlModelRegistry",
    "MlPredictionLog",
    "MlTrafficMappingMiss",
]
