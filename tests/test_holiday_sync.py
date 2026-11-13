from __future__ import annotations

from datetime import date
from pathlib import Path

from app.etl.events import read_events_csv, write_events_csv
from app.etl.holiday_sync import (
    build_calendar,
    from_holidays_package,
    merge_sources,
    sale_periods,
)


def test_holidays_package_returns_bangladesh_events():
    events = from_holidays_package([2027])

    assert events, "expected the package to know Bangladesh holidays"
    assert all(event["event_date"].year == 2027 for event in events)
    assert any("eid" in event["event_name"].lower() for event in events)


def test_islamic_dates_are_marked_estimated():
    events = from_holidays_package([2027])
    eid = [event for event in events if "eid" in event["event_name"].lower()]

    assert eid
    assert all("estimated" in event["event_name"].lower() for event in eid)
    assert all(event["estimated"] for event in eid)


def test_fixed_holidays_are_not_marked_estimated():
    events = from_holidays_package([2027])
    victory = [event for event in events if "victory" in event["event_name"].lower()]

    assert victory
    assert victory[0]["estimated"] is False


def test_eid_is_classified_as_eid_with_high_impact():
    events = from_holidays_package([2027])
    eid = [event for event in events if event["event_type"] == "eid"]

    assert eid
    assert all(event["impact_factor"] == 2.0 for event in eid)


def test_sale_periods_cover_recurring_retail_dates():
    events = sale_periods([2027])
    names = {event["event_name"] for event in events}

    assert {"Black Friday", "12.12 Sale", "Year-End Sale", "11.11 Sale"} <= names
    assert all(event["event_date"].year == 2027 for event in events)


def test_merge_prefers_non_estimated_entry_for_the_same_day():
    estimated = [
        {
            "event_date": date(2027, 3, 26),
            "event_type": "holiday",
            "event_name": "Independence Day (estimated)",
            "impact_factor": 1.0,
            "estimated": True,
        }
    ]
    official = [
        {
            "event_date": date(2027, 3, 26),
            "event_type": "holiday",
            "event_name": "Independence Day",
            "impact_factor": 1.0,
            "estimated": False,
        }
    ]

    merged = merge_sources(estimated, official)

    assert len(merged) == 1
    assert merged[0]["estimated"] is False


def test_merge_collapses_differently_named_holidays_on_the_same_day():
    package = [
        {
            "event_date": date(2027, 2, 21),
            "event_type": "holiday",
            "event_name": "Martyrs' Day and International Mother Language Day",
            "impact_factor": 1.0,
            "estimated": False,
        }
    ]
    official = [
        {
            "event_date": date(2027, 2, 21),
            "event_type": "holiday",
            "event_name": "International Mother Language Day",
            "impact_factor": 1.0,
            "estimated": False,
        }
    ]

    merged = merge_sources(package, official)

    assert len(merged) == 1
    assert merged[0]["event_name"] == "International Mother Language Day"


def test_merge_keeps_a_sale_and_a_holiday_on_the_same_day():
    holidays = [
        {
            "event_date": date(2027, 3, 10),
            "event_type": "eid",
            "event_name": "Eid al-Fitr (estimated)",
            "impact_factor": 2.0,
            "estimated": True,
        }
    ]
    sales = [
        {
            "event_date": date(2027, 3, 10),
            "event_type": "sale",
            "event_name": "Eid Sale",
            "impact_factor": 1.6,
            "estimated": False,
        }
    ]

    merged = merge_sources(holidays, sales)

    assert len(merged) == 2
    assert {event["event_type"] for event in merged} == {"eid", "sale"}


def test_build_calendar_combines_holidays_and_sales_without_network():
    events = build_calendar([2027], include_nager=False)

    types = {event["event_type"] for event in events}
    assert "eid" in types
    assert "sale" in types
    assert all(event["event_date"].year == 2027 for event in events)


def test_build_calendar_is_sorted_by_date():
    events = build_calendar([2027], include_nager=False)
    dates = [event["event_date"] for event in events]

    assert dates == sorted(dates)


def test_export_round_trips_through_the_csv_importer(tmp_path: Path):
    events = build_calendar([2027], include_nager=False)
    path = tmp_path / "calendar_2027.csv"

    written = write_events_csv(path, events)
    reread = read_events_csv(path)

    assert written == len(events)
    assert len(reread) == len(events)
    assert reread[0]["event_date"] == events[0]["event_date"]
