"""Zero-filled daily feature engineering for DemandIQ.

The builder is pure: callers pass entity-specific DataFrames plus the shared
site/referrer series, and receive one continuous daily matrix covering the
requested window.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd


def _normalize(df: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["ds", *columns])

    out = df.copy()
    out["ds"] = pd.to_datetime(out["ds"])

    for column in columns:
        if column not in out.columns:
            out[column] = 0

    return out


def build_daily_features(
    consumption_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    stock_df: pd.DataFrame,
    traffic_df: pd.DataFrame,
    site_df: pd.DataFrame,
    referrer_df: pd.DataFrame,
    start_date: date,
    end_date: date,
    reserved_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a complete, zero-filled daily feature matrix."""
    base = pd.DataFrame({"ds": pd.date_range(start_date, end_date, freq="D")})

    consumption = _normalize(consumption_df, ["consumed", "returned"])
    orders = _normalize(orders_df, ["order_count", "revenue"])
    stock = _normalize(stock_df, ["on_hand", "reserved"]).rename(
        columns={"on_hand": "stock_on_hand", "reserved": "stock_reserved"}
    )
    reserved = _normalize(reserved_df, ["reserved"]).rename(
        columns={"reserved": "units_reserved"}
    )
    traffic = _normalize(
        traffic_df,
        ["page_views", "add_to_cart", "cart_removals", "sessions", "unique_visitors", "source"],
    )
    site = _normalize(
        site_df,
        ["site_views", "site_visitors", "cart_page_views", "checkout_page_views"],
    )
    referrers = _normalize(
        referrer_df,
        ["facebook_views", "instagram_views", "search_views"],
    )

    df = base.merge(consumption, on="ds", how="left")
    df = df.merge(orders, on="ds", how="left")
    df = df.merge(stock, on="ds", how="left")
    df = df.merge(reserved, on="ds", how="left")
    df = df.merge(traffic, on="ds", how="left")
    df = df.merge(site, on="ds", how="left")
    df = df.merge(referrers, on="ds", how="left")

    demand_columns = [
        "consumed", "returned", "order_count", "revenue",
        "page_views", "add_to_cart", "cart_removals", "sessions", "unique_visitors",
        "site_views", "site_visitors", "cart_page_views", "checkout_page_views",
        "facebook_views", "instagram_views", "search_views", "units_reserved",
    ]

    for column in demand_columns:
        if column not in df.columns:
            df[column] = 0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0)

    for column in ("stock_on_hand", "stock_reserved"):
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce").ffill()

    df["stock_available"] = df["stock_on_hand"] - df["stock_reserved"]

    df["net_consumed"] = (df["consumed"] - df["returned"]).clip(lower=0)

    df["day_of_week"] = df["ds"].dt.day_of_week
    df["is_weekend"] = df["day_of_week"].isin([4, 5])  # Friday + Saturday in Bangladesh
    df["month"] = df["ds"].dt.month
    df["day_of_month"] = df["ds"].dt.day
    df["is_holiday"] = False
    df["holiday_name"] = None
    df["days_to_next_holiday"] = None
    df["is_payday"] = False
    df["is_festival_period"] = False
    df["festival_name"] = None

    for lag in (1, 7, 14, 28):
        df[f"lag_{lag}"] = df["net_consumed"].shift(lag)

    for window in (7, 14, 30):
        df[f"rolling_mean_{window}"] = df["net_consumed"].rolling(window).mean()
        df[f"rolling_std_{window}"] = df["net_consumed"].rolling(window).std()
        df[f"rolling_max_{window}"] = df["net_consumed"].rolling(window).max()

    df["trend_7_14"] = df["rolling_mean_7"] - df["rolling_mean_14"]
    df["trend_7_30"] = df["rolling_mean_7"] - df["rolling_mean_30"]

    df["avg_order_value"] = np.where(
        df["order_count"] > 0,
        df["revenue"] / df["order_count"],
        0,
    )

    df["view_to_cart"] = np.where(df["page_views"] > 0, df["add_to_cart"] / df["page_views"], 0)
    df["cart_to_order"] = np.where(df["add_to_cart"] > 0, df["order_count"] / df["add_to_cart"], 0)
    df["view_to_order"] = np.where(df["page_views"] > 0, df["order_count"] / df["page_views"], 0)
    df["units_per_view"] = np.where(df["page_views"] > 0, df["net_consumed"] / df["page_views"], 0)

    df["site_cart_rate"] = np.where(df["site_views"] > 0, df["cart_page_views"] / df["site_views"], 0)
    df["site_checkout_rate"] = np.where(df["site_views"] > 0, df["checkout_page_views"] / df["site_views"], 0)
    df["site_order_rate"] = np.where(df["site_views"] > 0, df["order_count"] / df["site_views"], 0)
    df["est_product_cart"] = df["page_views"] * df["site_cart_rate"]
    df["est_product_checkout"] = df["page_views"] * df["site_checkout_rate"]
    df["view_share_of_site"] = np.where(df["site_views"] > 0, df["page_views"] / df["site_views"], 0)

    df["facebook_share"] = np.where(df["site_views"] > 0, df["facebook_views"] / df["site_views"], 0)
    df["instagram_share"] = np.where(df["site_views"] > 0, df["instagram_views"] / df["site_views"], 0)
    df["search_share"] = np.where(df["site_views"] > 0, df["search_views"] / df["site_views"], 0)

    for window in (7, 14, 30):
        df[f"views_roll_{window}"] = df["page_views"].rolling(window).mean()

    df["views_momentum"] = df["views_roll_7"] - df["views_roll_30"]
    df["views_wow_change"] = (
        df["page_views"]
        .pct_change(periods=7)
        .replace([np.inf, -np.inf], 0)
        .fillna(0)
    )
    df["site_views_roll_7"] = df["site_views"].rolling(7).mean()

    for lag in (1, 7, 14):
        df[f"views_lag_{lag}"] = df["page_views"].shift(lag)

    df["traffic_source"] = df.get("source")

    return df


def feature_columns() -> list[str]:
    """Columns written to ml_daily_features by the upsert writer."""
    return [
        "feature_date",
        "units_consumed",
        "units_returned",
        "units_reserved",
        "order_count",
        "revenue",
        "avg_order_value",
        "stock_on_hand",
        "stock_reserved",
        "stock_available",
        "page_views",
        "add_to_cart",
        "cart_removals",
        "sessions",
        "unique_visitors",
        "traffic_sources",
        "is_weekend",
        "is_holiday",
        "holiday_name",
        "days_to_next_holiday",
        "is_payday",
        "is_festival_period",
        "festival_name",
    ]
