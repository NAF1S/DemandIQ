from datetime import date

import pandas as pd

from app.etl.features import build_daily_features


def build_fixture() -> pd.DataFrame:
    return build_daily_features(
        consumption_df=pd.DataFrame([{"ds": date(2026, 1, 2), "consumed": 5, "returned": 1}]),
        orders_df=pd.DataFrame([{"ds": date(2026, 1, 2), "order_count": 2, "revenue": 100}]),
        stock_df=pd.DataFrame([{"ds": date(2026, 1, 1), "on_hand": 10, "reserved": 2}]),
        reserved_df=pd.DataFrame([{"ds": date(2026, 1, 2), "reserved": 3}]),
        traffic_df=pd.DataFrame([{
            "ds": date(2026, 1, 2),
            "page_views": 50,
            "add_to_cart": 5,
            "cart_removals": 1,
            "sessions": 20,
            "unique_visitors": 15,
            "source": "jetpack",
        }]),
        site_df=pd.DataFrame([{
            "ds": date(2026, 1, 2),
            "site_views": 500,
            "site_visitors": 200,
            "cart_page_views": 50,
            "checkout_page_views": 20,
        }]),
        referrer_df=pd.DataFrame([{
            "ds": date(2026, 1, 2),
            "facebook_views": 100,
            "instagram_views": 50,
            "search_views": 25,
        }]),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10),
    )


def test_daily_matrix_is_zero_filled_and_feature_rich():
    df = build_fixture()

    assert len(df) == 10
    assert list(df["ds"].dt.date) == [date(2026, 1, day) for day in range(1, 11)]

    day = df[df["ds"] == pd.Timestamp("2026-01-02")].iloc[0]

    assert day["net_consumed"] == 4
    assert day["units_reserved"] == 3
    assert day["order_count"] == 2
    assert day["revenue"] == 100
    assert day["avg_order_value"] == 50

    assert day["stock_on_hand"] == 10
    assert day["stock_reserved"] == 2
    assert day["stock_available"] == 8

    assert day["page_views"] == 50
    assert day["view_to_cart"] == 0.1
    assert day["view_to_order"] == 0.04
    assert day["units_per_view"] == 0.08
    assert day["site_cart_rate"] == 0.1
    assert day["site_order_rate"] == 0.004
    assert day["view_share_of_site"] == 0.1
    assert day["facebook_share"] == 0.2
    assert day["instagram_share"] == 0.1
    assert day["search_share"] == 0.05
    assert day["traffic_source"] == "jetpack"

    # Sturdy stock is forward-filled; demand is zero-filled on quiet days.
    quiet = df[df["ds"] == pd.Timestamp("2026-01-05")].iloc[0]
    assert quiet["net_consumed"] == 0
    assert quiet["page_views"] == 0
    assert quiet["stock_on_hand"] == 10

    # Friday/Saturday weekend for the BD market, not Saturday/Sunday.
    assert bool(day["is_weekend"]) is True  # 2026-01-02 is Friday
    assert bool(df[df["ds"] == pd.Timestamp("2026-01-03")].iloc[0]["is_weekend"]) is True
    assert bool(df[df["ds"] == pd.Timestamp("2026-01-04")].iloc[0]["is_weekend"]) is False

    # Lags use the zero-filled net consumption series.
    assert df[df["ds"] == pd.Timestamp("2026-01-03")].iloc[0]["lag_1"] == 4
    assert df[df["ds"] == pd.Timestamp("2026-01-09")].iloc[0]["lag_7"] == 4
    assert df[df["ds"] == pd.Timestamp("2026-01-03")].iloc[0]["views_lag_1"] == 50


def test_missing_traffic_and_stock_do_not_break_the_builder():
    df = build_daily_features(
        consumption_df=pd.DataFrame(),
        orders_df=pd.DataFrame(),
        stock_df=pd.DataFrame(),
        reserved_df=None,
        traffic_df=pd.DataFrame(),
        site_df=pd.DataFrame(),
        referrer_df=pd.DataFrame(),
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 7),
    )

    assert len(df) == 7
    assert df["net_consumed"].sum() == 0
    assert df["page_views"].sum() == 0
    assert df["stock_on_hand"].isna().all()
    assert (df["view_to_cart"] == 0).all()
    assert (df["site_cart_rate"] == 0).all()
