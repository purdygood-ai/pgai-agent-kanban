"""
metrics_data.py — Shared per-RC row assembler for the /metrics and /costs JSON format.

Provides two public functions that read the same underlying data files used by the
text renderers (show-metrics.sh and cost-report.sh), assembling typed row dicts
for JSON serialisation.

Both /metrics?format=json and /costs?format=json call these functions.  The text
format continues to use the existing shell-out path unchanged — one source, two
formats (sibling rule).

Row field reference (shared shape for both endpoints):
    version         str   — RC version string, e.g. "v1.5.0"
    tasks           int   — tasks_total from history.csv (omitted when absent or zero)
    wall_seconds    float — wall_time_minutes * 60 (omitted when field is empty in CSV)
    tokens_in       int   — input_tokens (omitted when absent)
    tokens_out      int   — output_tokens (omitted when absent)
    cache_read_pct  float — cache_read_tokens / (input_tokens + cache_read_tokens) * 100,
                            rounded to 1 decimal place (omitted when denominator is zero)
    est_cost        float — total cost in USD from the RC usage file (omitted when the
                            usage/rc/<version>-tokens.json file is absent or has no cost)

Absent or empty source fields produce omitted keys in the row dict, never null or zero fill.
"""

from __future__ import annotations

import csv
import json
import pathlib
from typing import Any

__all__ = ["metrics_rows_for_project", "costs_rows_for_scope"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_int(raw: str | None) -> int | None:
    """Parse a raw string from a CSV cell to int, returning None on failure."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _parse_float(raw: str | None) -> float | None:
    """Parse a raw string to float, returning None on failure."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _cache_read_pct(input_tok: int | None, cache_read: int | None) -> float | None:
    """Compute cache read percentage: cache_read / (input + cache_read) * 100.

    Returns None when either argument is None or when the denominator is zero.
    Rounds to one decimal place.
    """
    if input_tok is None or cache_read is None:
        return None
    denom = input_tok + cache_read
    if denom == 0:
        return None
    return round(cache_read / denom * 100, 1)


def _load_rc_cost(usage_rc_dir: pathlib.Path, version: str) -> float | None:
    """Return the total cost_usd from the RC usage file, or None when absent.

    Reads usage/rc/<version>-tokens.json and returns totals.cost_usd.

    Args:
        usage_rc_dir: Path to the project's usage/rc/ directory.
        version:      RC version string, e.g. "v1.5.0".

    Returns:
        Total cost in USD as a float, or None when the file is absent,
        malformed, or has no cost field.
    """
    rc_file = usage_rc_dir / f"{version}-tokens.json"
    if not rc_file.is_file():
        return None
    try:
        data = json.loads(rc_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    totals = data.get("totals", {})
    if not isinstance(totals, dict):
        return None
    raw = totals.get("cost_usd")
    if raw is None:
        return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def metrics_rows_for_project(
    project_root: pathlib.Path,
    last_n: int | None = None,
) -> list[dict[str, Any]]:
    """Return per-RC metric rows read from history.csv.

    Each row is a dict containing a subset of:
        version, tasks, wall_seconds, tokens_in, tokens_out,
        cache_read_pct, est_cost

    Fields absent or empty in the source file are omitted from the row dict.
    The ``est_cost`` field is populated from ``usage/rc/<version>-tokens.json``
    when that file exists for the version.

    Args:
        project_root: The project's kanban root directory
                      (e.g. ``<kanban_root>/projects/<name>``).
        last_n:       When provided, return only the last N rows (same as the
                      ``--last`` flag of show-metrics.sh).  None returns all rows.

    Returns:
        List of row dicts, one per RC row from history.csv, in CSV order
        (oldest to newest).  Empty list when history.csv is absent.
    """
    history_csv = project_root / "metrics" / "history.csv"
    if not history_csv.is_file():
        return []

    usage_rc_dir = project_root / "usage" / "rc"

    try:
        with history_csv.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            raw_rows = list(reader)
    except OSError:
        return []

    if not raw_rows:
        return []

    if last_n is not None and last_n > 0:
        raw_rows = raw_rows[-last_n:]

    result: list[dict[str, Any]] = []
    for raw in raw_rows:
        row: dict[str, Any] = {}

        version = str(raw.get("rc", "")).strip()
        if not version:
            continue
        row["version"] = version

        tasks = _parse_int(raw.get("tasks_total"))
        if tasks is not None:
            row["tasks"] = tasks

        wall_min = _parse_float(raw.get("wall_time_minutes"))
        if wall_min is not None:
            row["wall_seconds"] = round(wall_min * 60, 1)

        tokens_in = _parse_int(raw.get("input_tokens"))
        if tokens_in is not None:
            row["tokens_in"] = tokens_in

        tokens_out = _parse_int(raw.get("output_tokens"))
        if tokens_out is not None:
            row["tokens_out"] = tokens_out

        cache_read = _parse_int(raw.get("cache_read_tokens"))
        pct = _cache_read_pct(tokens_in, cache_read)
        if pct is not None:
            row["cache_read_pct"] = pct

        est_cost = _load_rc_cost(usage_rc_dir, version)
        if est_cost is not None:
            row["est_cost"] = round(est_cost, 6)

        result.append(row)

    return result


def costs_rows_for_scope(
    project_root: pathlib.Path,
    scope_type: str,
    scope_value: str,
) -> list[dict[str, Any]]:
    """Return per-RC cost rows for the requested scope.

    For ``scope_type="rc"`` the result is a single-element array carrying that
    RC's fields.  For ``scope_type="month"`` or ``"day"``, includes all RC files
    whose ``shipped_at`` starts with ``scope_value``.  An empty ``scope_value``
    with ``scope_type="month"`` returns all available RC files (the "no filter" case).

    The returned row shape mirrors the ``metrics_rows_for_project`` shape so that
    both endpoints produce identically structured JSON objects:
        version, tasks, tokens_in, tokens_out, cache_read_pct, est_cost

    ``wall_seconds`` is always omitted for cost rows — that field comes from
    history.csv which the costs endpoint does not read.

    Args:
        project_root: The project's kanban root directory.
        scope_type:   One of "rc", "month", "day" — mirrors cost-report.sh scopes.
                      For "month" and "day", includes all RC files whose
                      shipped_at matches the scope_value prefix.
                      Unsupported scope types return an empty list.
        scope_value:  Scope argument, e.g. "v1.5.0", "2026-05", "2026-05-16".
                      For "month" scope, an empty string returns all available RC rows.

    Returns:
        List of row dicts sorted by shipped_at ascending.
        Empty list when no matching data is found.
    """
    usage_rc_dir = project_root / "usage" / "rc"
    if not usage_rc_dir.is_dir():
        return []

    if scope_type == "rc":
        return _costs_rc_scope(usage_rc_dir, scope_value)
    elif scope_type in ("month", "day"):
        return _costs_date_scope(usage_rc_dir, scope_value)
    else:
        # range and other scope types: not supported for per-RC JSON rows
        return []


def _read_rc_tokens_file(rc_file: pathlib.Path) -> dict[str, Any] | None:
    """Read and parse a single RC tokens file.

    Returns the parsed dict or None on any error.
    """
    try:
        data = json.loads(rc_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _row_from_rc_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """Build a cost row dict from a parsed RC tokens file.

    Returns None when the file lacks a version field.
    """
    version = str(data.get("version") or data.get("rc") or "").strip()
    if not version:
        return None

    row: dict[str, Any] = {"version": version}

    totals = data.get("totals") or {}

    tasks_raw = totals.get("tasks") or data.get("tasks_count")
    tasks = _parse_int(tasks_raw)
    if tasks is not None and tasks > 0:
        row["tasks"] = tasks

    tokens_in = _parse_int(totals.get("input_tokens"))
    if tokens_in is not None:
        row["tokens_in"] = tokens_in

    tokens_out = _parse_int(totals.get("output_tokens"))
    if tokens_out is not None:
        row["tokens_out"] = tokens_out

    cache_read = _parse_int(totals.get("cache_read_tokens"))
    pct = _cache_read_pct(tokens_in, cache_read)
    if pct is not None:
        row["cache_read_pct"] = pct

    cost_raw = totals.get("cost_usd")
    try:
        cost = float(cost_raw) if cost_raw is not None else 0.0
    except (ValueError, TypeError):
        cost = 0.0
    if cost > 0:
        row["est_cost"] = round(cost, 6)

    return row


def _costs_rc_scope(
    usage_rc_dir: pathlib.Path,
    version: str,
) -> list[dict[str, Any]]:
    """Return a single-element list for a specific RC version."""
    # Normalise: ensure version starts with 'v'
    if not version.startswith("v"):
        version = f"v{version}"
    rc_file = usage_rc_dir / f"{version}-tokens.json"
    data = _read_rc_tokens_file(rc_file) if rc_file.is_file() else None
    if data is None:
        return []
    row = _row_from_rc_data(data)
    return [row] if row is not None else []


def _costs_date_scope(
    usage_rc_dir: pathlib.Path,
    date_prefix: str,
) -> list[dict[str, Any]]:
    """Return rows for all RC files whose shipped_at starts with date_prefix.

    Sorted by shipped_at ascending (oldest RC first).
    """
    candidates: list[tuple[str, dict[str, Any]]] = []

    for rc_file in usage_rc_dir.iterdir():
        if not rc_file.name.endswith("-tokens.json"):
            continue
        data = _read_rc_tokens_file(rc_file)
        if data is None:
            continue
        shipped_at = str(data.get("shipped_at") or "")
        if shipped_at.startswith(date_prefix):
            candidates.append((shipped_at, data))

    # Sort by shipped_at ascending
    candidates.sort(key=lambda c: c[0])

    rows: list[dict[str, Any]] = []
    for _, data in candidates:
        row = _row_from_rc_data(data)
        if row is not None:
            rows.append(row)
    return rows
