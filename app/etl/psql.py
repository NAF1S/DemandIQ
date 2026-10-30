"""Portal PHP bridge for Phase 5 extraction and loading.

The Supabase Supavisor pooler accepts the portal's PHP PDO connection but drops
psql/asyncpg SSL sessions from this host. The portal therefore performs the SQL
boundary; pandas performs the feature math in DemandIQ.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PORTAL_ROOT = Path(
    os.environ.get(
        "PORTAL_ROOT",
        Path(__file__).resolve().parents[3] / "portal",
    )
)
EXTRACT_SCRIPT = PORTAL_ROOT / "scripts" / "phase5_extract.php"
WRITE_SCRIPT = PORTAL_ROOT / "scripts" / "phase5_write.php"


def _run_php(script: Path, arguments: list[Any]) -> str:
    result = subprocess.run(
        ["php", str(script), *[str(value) for value in arguments]],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "portal PHP script failed")
    return result.stdout


def _json_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    if isinstance(value, (date, pd.Timestamp)):
        return pd.Timestamp(value).date().isoformat()

    if isinstance(value, bool):
        return value

    if isinstance(value, (dict, list)):
        return value

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    return value


def extract_all(organization_id: int, store_id: int, start_date: date) -> dict[str, pd.DataFrame]:
    payload = json.loads(
        _run_php(EXTRACT_SCRIPT, [organization_id, store_id, start_date.isoformat()])
    )

    frames: dict[str, pd.DataFrame] = {}

    for name, rows in payload.items():
        frame = pd.DataFrame(rows)
        if "ds" in frame.columns and not frame.empty:
            frame["ds"] = pd.to_datetime(frame["ds"])
        frames[name] = frame

    traffic = frames.get("traffic", pd.DataFrame())
    if not traffic.empty:
        frames["traffic"] = traffic.sort_values(
            ["product_id", "ds", "source"]
        ).drop_duplicates(["product_id", "ds"], keep="first")

    return frames


def write_features(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    payload = [{key: _json_value(value) for key, value in row.items()} for row in rows]
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(payload, handle)
    handle.close()

    try:
        _run_php(WRITE_SCRIPT, [handle.name])
    finally:
        Path(handle.name).unlink(missing_ok=True)

    return len(rows)
