"""Automatic calendar-event sync from public holiday sources.

Two sources are combined, because neither is complete on its own:

* the ``holidays`` package ships a Bangladesh calendar that includes the
  Islamic festivals (Eid, Ashura, Shab-e-Qadr) as *estimated* dates, since they
  follow a lunar calendar; and
* Nager.Date (``date.nager.at``) publishes the government's fixed public
  holidays for Bangladesh.

Neither source knows about commercial sale periods, so those stay manual and
are merged in from the existing table or a CSV.

Islamic dates are noticed as estimates. The importer stores them with an
"(estimated)" suffix in the event name so it is obvious in the feature matrix
and the UI that a moon-sighting correction may move them by a day or two.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"

# Islamic observances from the Python calendar are labelled "estimated".
ESTIMATED_MARKERS = ("estimated", "(estimated)")

# Holiday names that represent the big commercial demand peaks in Bangladesh.
FESTIVAL_KEYWORDS = {
    "eid al-fitr": ("eid", 2.0),
    "eid al-adha": ("eid", 2.0),
    "durga puja": ("puja", 1.7),
    "ashura": ("festival", 1.3),
    "shab-e-barat": ("festival", 1.2),
    "mid-sha'ban": ("festival", 1.2),
    "shab-e-qadr": ("festival", 1.3),
    "laylat al-qadr": ("festival", 1.3),
    "jumu'atul-wida": ("festival", 1.2),
    "prophet's birthday": ("festival", 1.3),
    "eid-e-miladunnabi": ("festival", 1.3),
}


def _classify(name: str) -> tuple[str, float]:
    """Map a holiday name to an event type and impact multiplier."""
    lowered = name.lower()

    for keyword, (kind, impact) in FESTIVAL_KEYWORDS.items():
        if keyword in lowered:
            return kind, impact

    return "holiday", 1.0


def _is_estimated(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in ESTIMATED_MARKERS)


def from_holidays_package(years: list[int], country: str = "BD") -> list[dict[str, Any]]:
    """Read the Bangladesh calendar from the ``holidays`` package."""
    import holidays as holidays_lib

    national = holidays_lib.country_holidays(country, years=years, language="en_US")
    events: list[dict[str, Any]] = []

    for day, name in sorted(national.items()):
        event_type, impact = _classify(str(name))
        clean_name = str(name).replace(" (estimated)", "").replace("(estimated)", "").strip()
        estimated = _is_estimated(str(name))

        events.append(
            {
                "event_date": day,
                "event_type": event_type,
                "event_name": f"{clean_name} (estimated)" if estimated else clean_name,
                "impact_factor": impact,
                "source": "holidays-package",
                "estimated": estimated,
            }
        )

    return events


def from_nager_api(years: list[int], country: str = "BD", timeout: float = 15.0) -> list[dict[str, Any]]:
    """Read official public holidays from Nager.Date; returns [] when offline."""
    events: list[dict[str, Any]] = []

    for year in years:
        url = NAGER_URL.format(year=year, country=country)
        try:
            with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed host
                payload = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, ValueError, OSError):
            # The sync must keep working from a cached package when the network
            # is unavailable rather than losing the whole import.
            continue

        for row in payload or []:
            name = str(row.get("name") or "").strip()
            raw_date = row.get("date")
            if not name or not raw_date:
                continue

            event_type, impact = _classify(name)
            events.append(
                {
                    "event_date": date.fromisoformat(raw_date),
                    "event_type": event_type,
                    "event_name": name,
                    "impact_factor": impact,
                    "source": "nager",
                    "estimated": False,
                }
            )

    return events


def merge_sources(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge holiday sources so each (date, type) appears once.

    The two sources name the same day differently ("Independence Day" versus
    "Independence and National Day"), so keying on the name would double every
    fixed holiday. Keying on date and type keeps one row per meaningful signal
    while still allowing a genuine coincidence — a sale running on Eid — to
    appear as two rows.

    On a tie the non-estimated source wins, because a government-published date
    beats a moon-sighting estimate; otherwise the shorter name wins, which is
    usually the cleaner one.
    """
    merged: dict[tuple[date, str], dict[str, Any]] = {}

    for source in sources:
        for event in source:
            key = (event["event_date"], str(event["event_type"]).lower())
            existing = merged.get(key)

            if existing is None:
                merged[key] = event
                continue

            if existing.get("estimated") and not event.get("estimated"):
                merged[key] = event
            elif existing.get("estimated") == event.get("estimated"):
                if len(event["event_name"]) < len(existing["event_name"]):
                    merged[key] = event

    return sorted(merged.values(), key=lambda item: (item["event_date"], item["event_type"], item["event_name"]))


def sale_periods(years: list[int], country: str = "BD") -> list[dict[str, Any]]:
    """Commercial sale periods, which no public holiday source publishes.

    These are the recurring retail dates Bangladesh stores actually promote:
    Eid-adjacent sales, Black Friday, 12.12 and the year-end run. They are
    deliberately conservative on impact so a manual override is easy to spot.
    """
    events: list[dict[str, Any]] = []

    for year in years:
        events.extend(
            [
                {
                    "event_date": date(year, 1, 1),
                    "event_type": "sale",
                    "event_name": "New Year Sale",
                    "impact_factor": 1.3,
                    "source": "builtin-sales",
                    "estimated": False,
                },
                {
                    "event_date": date(year, 2, 14),
                    "event_type": "sale",
                    "event_name": "Valentine's Day Sale",
                    "impact_factor": 1.3,
                    "source": "builtin-sales",
                    "estimated": False,
                },
                {
                    "event_date": date(year, 11, 11),
                    "event_type": "sale",
                    "event_name": "11.11 Sale",
                    "impact_factor": 1.7,
                    "source": "builtin-sales",
                    "estimated": False,
                },
                {
                    "event_date": date(year, 11, 27),
                    "event_type": "sale",
                    "event_name": "Black Friday",
                    "impact_factor": 2.0,
                    "source": "builtin-sales",
                    "estimated": False,
                },
                {
                    "event_date": date(year, 12, 12),
                    "event_type": "sale",
                    "event_name": "12.12 Sale",
                    "impact_factor": 1.8,
                    "source": "builtin-sales",
                    "estimated": False,
                },
                {
                    "event_date": date(year, 12, 31),
                    "event_type": "sale",
                    "event_name": "Year-End Sale",
                    "impact_factor": 1.6,
                    "source": "builtin-sales",
                    "estimated": False,
                },
            ]
        )

    return events


def build_calendar(
    years: list[int],
    country: str = "BD",
    include_sales: bool = True,
    include_nager: bool = True,
) -> list[dict[str, Any]]:
    """Assemble the full event set for the requested years."""
    sources = [from_holidays_package(years, country)]

    if include_nager:
        sources.append(from_nager_api(years, country))

    if include_sales:
        sources.append(sale_periods(years, country))

    return merge_sources(*sources)
