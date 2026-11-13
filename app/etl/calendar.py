"""Calendar events as model features.

Events live in ``ml_calendar_events`` (Eid, Puja, paydays, store sales). Each
row is either global (``organization_id`` NULL) or tenant-scoped. The model
consumes them as forward-looking flags: unlike traffic, an event date is known
in advance, so it is safe to use in a forecast horizon.

The table is empty until the operator adds events. Everything here degrades to
"no events" cleanly, so the feature matrix and every model keep working before
the first event ever exists.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from psycopg import Connection

CALENDAR_QUERY = """
SELECT event_date, event_type, event_name, impact_factor
FROM ml_calendar_events
WHERE (organization_id = %(org_id)s OR organization_id IS NULL)
  AND event_date >= %(start_date)s
  AND event_date <= %(end_date)s
ORDER BY event_date
"""

CALENDAR_COLUMNS = [
    "is_holiday",
    "is_payday",
    "is_festival_period",
    "is_sale_event",
    "event_impact",
    "days_to_event",
    "days_since_event",
    "holiday_name",
    "festival_name",
]

FESTIVAL_TYPES = {"festival", "eid", "puja", "holiday"}
SALE_TYPES = {"sale", "campaign", "promotion", "discount"}


def load_calendar_events(
    conn: Connection,
    organization_id: int,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Read the events in range; returns an empty frame when none exist."""
    with conn.cursor() as cur:
        cur.execute(
            CALENDAR_QUERY,
            {
                "org_id": organization_id,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        rows = cur.fetchall()

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["event_date"] = pd.to_datetime(frame["event_date"])
    return frame


def calendar_frame(events: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    """Expand events into one row per day with event flags and event distance.

    ``days_to_event`` is the countdown to the next event and ``days_since_event``
    the gap after the last one. Both are bounded at 90 days so a quiet stretch
    does not dominate a tree split.
    """
    base = pd.DataFrame({"ds": pd.date_range(start_date, end_date, freq="D")})
    base["is_holiday"] = False
    base["is_payday"] = False
    base["is_festival_period"] = False
    base["is_sale_event"] = False
    base["event_impact"] = 1.0
    base["days_to_event"] = 90
    base["days_since_event"] = 90
    base["holiday_name"] = None
    base["festival_name"] = None

    if events is None or events.empty:
        return base

    prepared = events.copy()
    if "event_date" not in prepared.columns:
        return base
    prepared["event_date"] = pd.to_datetime(prepared["event_date"], errors="coerce")
    prepared = prepared.dropna(subset=["event_date"])
    if prepared.empty:
        return base

    prepared["event_type"] = prepared["event_type"].astype(str).str.lower().str.strip()
    prepared["event_name"] = prepared["event_name"].astype(str)
    prepared["impact_factor"] = pd.to_numeric(
        prepared.get("impact_factor"), errors="coerce"
    ).fillna(1.0)

    event_days = sorted(set(prepared["event_date"].dt.normalize()))

    def countdown(day: pd.Timestamp) -> int:
        upcoming = [event_day for event_day in event_days if event_day >= day]
        if not upcoming:
            return 90
        return int(min((upcoming[0] - day).days, 90))

    def since(day: pd.Timestamp) -> int:
        past = [event_day for event_day in event_days if event_day <= day]
        if not past:
            return 90
        return int(min((day - past[-1]).days, 90))

    base["days_to_event"] = [countdown(day.normalize()) for day in base["ds"]]
    base["days_since_event"] = [since(day.normalize()) for day in base["ds"]]

    for _, event in prepared.iterrows():
        day = event["event_date"].normalize()
        mask = base["ds"].dt.normalize() == day
        if not mask.any():
            continue

        kind = event["event_type"]
        base.loc[mask, "event_impact"] = float(event["impact_factor"])

        if kind in FESTIVAL_TYPES:
            base.loc[mask, "is_holiday"] = True
            base.loc[mask, "is_festival_period"] = True
            base.loc[mask, "holiday_name"] = event["event_name"]
            base.loc[mask, "festival_name"] = event["event_name"]
        elif kind == "payday":
            base.loc[mask, "is_payday"] = True
        elif kind in SALE_TYPES:
            base.loc[mask, "is_sale_event"] = True
            base.loc[mask, "holiday_name"] = base.loc[mask, "holiday_name"].fillna(
                event["event_name"]
            )

    return base


def future_calendar(events: pd.DataFrame, last_date: date, periods: int) -> pd.DataFrame:
    """Calendar features for the forward horizon, so events inform predictions."""
    last = pd.Timestamp(last_date)
    start = last + pd.Timedelta(days=1)
    end = last + pd.Timedelta(days=periods)
    return calendar_frame(events, start.date(), end.date())


def event_summary(events: pd.DataFrame) -> dict[str, Any]:
    """Counts for run summaries and the freshness report."""
    if events is None or events.empty:
        return {"events": 0, "types": {}, "first": None, "last": None}

    dates = pd.to_datetime(events["event_date"], errors="coerce").dropna()
    types = events["event_type"].astype(str).value_counts().to_dict()

    return {
        "events": int(len(events)),
        "types": {str(key): int(value) for key, value in types.items()},
        "first": dates.min().date().isoformat() if not dates.empty else None,
        "last": dates.max().date().isoformat() if not dates.empty else None,
    }
