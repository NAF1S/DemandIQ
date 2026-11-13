"""Import and inspect calendar events.

Events are the forward-looking signal the models can use: a festival, a payday
or a flash sale is known before it happens, unlike traffic. Bring them in from
a CSV with ``python -m cli import-events --file events.csv``; the file needs
``event_date,event_type,event_name`` and may carry ``impact_factor``.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from psycopg import Connection

VALID_TYPES = {
    "festival",
    "holiday",
    "sale",
    "campaign",
    "promotion",
    "discount",
    "payday",
    "eid",
    "puja",
}

INSERT_EVENT = """
INSERT INTO ml_calendar_events (
    organization_id, event_date, event_type, event_name, country_code, impact_factor, created_at
) VALUES (
    %(organization_id)s, %(event_date)s, %(event_type)s, %(event_name)s,
    %(country_code)s, %(impact_factor)s, NOW()
)
ON CONFLICT DO NOTHING
"""

UPSERT_EVENT = """
INSERT INTO ml_calendar_events (
    organization_id, event_date, event_type, event_name, country_code, impact_factor, created_at
) VALUES (
    %(organization_id)s, %(event_date)s, %(event_type)s, %(event_name)s,
    %(country_code)s, %(impact_factor)s, NOW()
)
"""


def write_events_csv(path: Path, events: list[dict[str, Any]]) -> int:
    """Export events in the same shape ``read_events_csv`` consumes."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["event_date", "event_type", "event_name", "impact_factor"]
        )
        writer.writeheader()
        for event in events:
            writer.writerow(
                {
                    "event_date": event["event_date"].isoformat()
                    if isinstance(event["event_date"], date)
                    else event["event_date"],
                    "event_type": event["event_type"],
                    "event_name": event["event_name"],
                    "impact_factor": event.get("impact_factor", 1.0),
                }
            )

    return len(events)


def sync_events(
    conn: Connection,
    organization_id: int | None,
    years: list[int],
    country: str = "BD",
    include_sales: bool = True,
    replace: bool = False,
) -> dict[str, Any]:
    """Pull public holidays and monthly sales for the years and store them."""
    from app.etl.holiday_sync import build_calendar

    events = build_calendar(years, country=country, include_sales=include_sales)
    written = import_events(
        conn,
        organization_id=organization_id,
        rows=events,
        replace=replace,
        country_code=country,
    )

    from collections import Counter

    return {
        "written": written,
        "years": years,
        "types": dict(Counter(str(event["event_type"]) for event in events)),
        "estimated": sum(1 for event in events if event.get("estimated")),
        "first": events[0]["event_date"].isoformat() if events else None,
        "last": events[-1]["event_date"].isoformat() if events else None,
    }


def read_events_csv(path: Path) -> list[dict[str, Any]]:
    """Parse and validate the event CSV; raises on the first bad row."""
    if not path.is_file():
        raise FileNotFoundError(f"Event file not found: {path}")

    required = {"event_date", "event_type", "event_name"}
    rows: list[dict[str, Any]] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Event file is missing columns: {', '.join(sorted(missing))}")

        for line_number, raw in enumerate(reader, start=2):
            raw_date = (raw.get("event_date") or "").strip()
            kind = (raw.get("event_type") or "").strip().lower()
            name = (raw.get("event_name") or "").strip()

            if not raw_date or not kind or not name:
                raise ValueError(f"Line {line_number}: event_date, event_type and event_name are required.")

            try:
                event_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: event_date must be YYYY-MM-DD, got '{raw_date}'.") from exc

            if kind not in VALID_TYPES:
                raise ValueError(
                    f"Line {line_number}: unknown event_type '{kind}'. "
                    f"Use one of: {', '.join(sorted(VALID_TYPES))}."
                )

            raw_impact = (raw.get("impact_factor") or "").strip()
            try:
                impact = float(raw_impact) if raw_impact else 1.0
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: impact_factor must be a number.") from exc

            rows.append(
                {
                    "event_date": event_date,
                    "event_type": kind,
                    "event_name": name,
                    "impact_factor": impact,
                }
            )

    return rows


def import_events(
    conn: Connection,
    organization_id: int | None,
    rows: list[dict[str, Any]],
    replace: bool = False,
    country_code: str = "BD",
) -> int:
    """Insert events, optionally replacing the existing set for that scope.

    Without ``replace`` this is additive and de-duplicates on
    (scope, date, type, name) so re-running a sync for an overlapping year is
    safe.
    """
    if not rows:
        return 0

    payload = [
        {
            "organization_id": organization_id,
            "event_date": row["event_date"],
            "event_type": row["event_type"],
            "event_name": row["event_name"],
            "country_code": country_code,
            "impact_factor": row["impact_factor"],
        }
        for row in rows
    ]

    with conn.cursor() as cur:
        if replace:
            if organization_id is None:
                cur.execute("DELETE FROM ml_calendar_events WHERE organization_id IS NULL")
            else:
                cur.execute(
                    "DELETE FROM ml_calendar_events WHERE organization_id = %s",
                    (organization_id,),
                )

        cur.executemany(INSERT_EVENT, payload)
        written = cur.rowcount if cur.rowcount > 0 else len(payload)
    conn.commit()

    return int(written)


def event_inventory(conn: Connection, organization_id: int) -> dict[str, Any]:
    """Counts by type and the covered date range, for the CLI and health check."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS events,
                   min(event_date) AS first_event,
                   max(event_date) AS last_event
            FROM ml_calendar_events
            WHERE organization_id = %s OR organization_id IS NULL
            """,
            (organization_id,),
        )
        totals = cur.fetchone()

        cur.execute(
            """
            SELECT event_type, count(*) AS n
            FROM ml_calendar_events
            WHERE organization_id = %s OR organization_id IS NULL
            GROUP BY event_type
            ORDER BY event_type
            """,
            (organization_id,),
        )
        types = {str(row["event_type"]): int(row["n"]) for row in cur.fetchall()}

    return {
        "events": int(totals["events"] or 0),
        "types": types,
        "first": totals["first_event"].isoformat() if totals["first_event"] else None,
        "last": totals["last_event"].isoformat() if totals["last_event"] else None,
    }
