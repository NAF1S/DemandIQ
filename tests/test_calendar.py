from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.etl.calendar import calendar_frame, event_summary, future_calendar
from app.etl.features import build_daily_features

START = date(2026, 8, 1)
END = date(2026, 8, 10)


def events_frame(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "event_date": pd.Timestamp(day),
                "event_type": kind,
                "event_name": name,
                "impact_factor": impact,
            }
            for day, kind, name, impact in rows
        ]
    )


def test_no_events_yields_neutral_calendar():
    frame = calendar_frame(pd.DataFrame(), START, END)

    assert len(frame) == 10
    assert not frame["is_holiday"].any()
    assert not frame["is_sale_event"].any()
    assert (frame["event_impact"] == 1.0).all()
    assert (frame["days_to_event"] == 90).all()


def test_festival_sets_holiday_flags_and_name():
    events = events_frame([("2026-08-05", "festival", "Eid", 1.8)])

    frame = calendar_frame(events, START, END)
    day = frame[frame["ds"] == pd.Timestamp("2026-08-05")].iloc[0]

    assert bool(day["is_holiday"]) is True
    assert bool(day["is_festival_period"]) is True
    assert day["event_impact"] == 1.8
    assert day["holiday_name"] == "Eid"


def test_sale_event_is_separate_from_holiday():
    events = events_frame([("2026-08-06", "sale", "Flash sale", 1.5)])

    frame = calendar_frame(events, START, END)
    day = frame[frame["ds"] == pd.Timestamp("2026-08-06")].iloc[0]

    assert bool(day["is_sale_event"]) is True
    assert bool(day["is_holiday"]) is False


def test_payday_flag_is_recorded():
    events = events_frame([("2026-08-07", "payday", "Salary day", 1.2)])

    frame = calendar_frame(events, START, END)
    day = frame[frame["ds"] == pd.Timestamp("2026-08-07")].iloc[0]

    assert bool(day["is_payday"]) is True


def test_event_distance_counts_down_and_back():
    events = events_frame([("2026-08-05", "festival", "Eid", 1.8)])

    frame = calendar_frame(events, START, END)
    before = frame[frame["ds"] == pd.Timestamp("2026-08-03")].iloc[0]
    on_the_day = frame[frame["ds"] == pd.Timestamp("2026-08-05")].iloc[0]
    after = frame[frame["ds"] == pd.Timestamp("2026-08-08")].iloc[0]

    assert before["days_to_event"] == 2
    assert on_the_day["days_to_event"] == 0
    assert after["days_since_event"] == 3


def test_future_calendar_covers_the_forward_horizon():
    events = events_frame([("2026-11-20", "sale", "Black Friday", 2.0)])
    horizon = future_calendar(events, date(2026, 11, 13), 14)

    assert len(horizon) == 14
    assert horizon["ds"].min().date() == date(2026, 11, 14)
    assert horizon["ds"].max().date() == date(2026, 11, 27)
    sale_day = horizon[horizon["ds"] == pd.Timestamp("2026-11-20")].iloc[0]
    assert bool(sale_day["is_sale_event"]) is True
    assert sale_day["event_impact"] == 2.0


def test_event_summary_counts_types():
    events = events_frame(
        [
            ("2026-08-05", "festival", "Eid", 1.8),
            ("2026-08-06", "sale", "Flash sale", 1.5),
            ("2026-08-07", "sale", "Weekend sale", 1.3),
        ]
    )

    summary = event_summary(events)

    assert summary["events"] == 3
    assert summary["types"]["sale"] == 2
    assert summary["first"] == "2026-08-05"
    assert summary["last"] == "2026-08-07"


def test_daily_features_carry_quarter_and_event_columns():
    events = events_frame([("2026-08-05", "festival", "Eid", 1.8)])
    calendar = calendar_frame(events, START, END)

    frame = build_daily_features(
        consumption_df=pd.DataFrame(columns=["ds", "consumed", "returned"]),
        orders_df=pd.DataFrame(columns=["ds", "order_count", "revenue"]),
        stock_df=pd.DataFrame(columns=["ds", "on_hand", "reserved"]),
        traffic_df=pd.DataFrame(),
        site_df=pd.DataFrame(),
        referrer_df=pd.DataFrame(),
        start_date=START,
        end_date=END,
        calendar_df=calendar,
    )

    assert {"quarter", "day_of_quarter", "days_to_quarter_end", "quarter_progress"} <= set(frame.columns)
    assert frame["quarter"].iloc[0] == 3
    assert bool(frame[frame["ds"] == pd.Timestamp("2026-08-05")]["is_holiday"].iloc[0]) is True


def test_daily_features_work_without_a_calendar():
    frame = build_daily_features(
        consumption_df=pd.DataFrame(columns=["ds", "consumed", "returned"]),
        orders_df=pd.DataFrame(columns=["ds", "order_count", "revenue"]),
        stock_df=pd.DataFrame(columns=["ds", "on_hand", "reserved"]),
        traffic_df=pd.DataFrame(),
        site_df=pd.DataFrame(),
        referrer_df=pd.DataFrame(),
        start_date=START,
        end_date=END,
    )

    assert len(frame) == 10
    assert not frame["is_holiday"].any()
    assert (frame["event_impact"] == 1.0).all()
