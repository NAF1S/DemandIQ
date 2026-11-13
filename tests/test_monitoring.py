from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from app.etl.pipeline import build_feature_rows
from app.monitoring.health import assert_horizon_is_forward, feature_span

TODAY = date(2026, 11, 13)


def _empty_frames() -> dict[str, pd.DataFrame]:
    return {
        "entities": pd.DataFrame(columns=["entity_type", "id", "name", "sku", "store_id"]),
        "consumption": pd.DataFrame(columns=["ds", "item_id", "product_id", "consumed", "returned"]),
        "traffic": pd.DataFrame(columns=["product_id", "ds", "page_views", "source"]),
        "site": pd.DataFrame(columns=["ds", "site_views", "site_visitors"]),
        "referrers": pd.DataFrame(columns=["ds", "facebook_views", "instagram_views", "search_views"]),
        "product_orders": pd.DataFrame(columns=["entity_id", "ds", "order_count", "revenue", "units"]),
        "item_orders": pd.DataFrame(columns=["entity_id", "ds", "order_count", "revenue"]),
        "product_returns": pd.DataFrame(columns=["entity_id", "ds", "returned"]),
        "stock": pd.DataFrame(columns=["ds", "item_id", "product_id", "on_hand", "reserved"]),
        "reserved": pd.DataFrame(columns=["ds", "item_id", "product_id", "reserved"]),
    }


def test_feature_span_reads_first_and_last_date():
    frame = pd.DataFrame(
        {"feature_date": [date(2026, 8, 2), date(2026, 11, 13)], "units_consumed": [1, 2]}
    )

    first, last = feature_span(frame)

    assert first == date(2026, 8, 2)
    assert last == date(2026, 11, 13)


def test_feature_span_is_empty_safe():
    assert feature_span(pd.DataFrame()) == (None, None)


def test_horizon_guard_allows_a_two_day_lag():
    assert_horizon_is_forward(TODAY - timedelta(days=2), today=TODAY)


def test_horizon_guard_rejects_a_stale_feature_matrix():
    with pytest.raises(RuntimeError, match="Run the feature pipeline before training"):
        assert_horizon_is_forward(TODAY - timedelta(days=14), today=TODAY)


def test_horizon_guard_allows_a_future_feature_date():
    assert_horizon_is_forward(TODAY + timedelta(days=1), today=TODAY)


def test_build_feature_rows_offsets_each_entity_to_the_requested_window():
    frames = _empty_frames()
    frames["entities"] = pd.DataFrame(
        [{"entity_type": "item", "id": 1, "name": "Sticker", "sku": "S1", "store_id": None}]
    )

    summary = build_feature_rows(
        frames,
        organization_id=1,
        store_id=1,
        start_date=TODAY - timedelta(days=3),
        end_date=TODAY,
    )

    rows = summary["rows"]
    assert len(rows) == 4
    assert {row["feature_date"] for row in rows} == {
        TODAY - timedelta(days=3),
        TODAY - timedelta(days=2),
        TODAY - timedelta(days=1),
        TODAY,
    }
    assert summary["entities"] == 1
    assert summary["feature_rows"] == 4
    assert summary["traffic_entities"] == 0
