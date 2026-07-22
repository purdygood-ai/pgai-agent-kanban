#!/usr/bin/env python3
"""
metrics_aggregator.py -- Per-RC and per-day metrics rollup aggregator.

Provides aggregate_rc() and aggregate_day() as importable functions and as a
stand-alone CLI.

OUTPUT SCHEMA
=============

Per-RC rollup (projects/<name>/metrics/rc/<v>.json):

  {
    "rc": "v0.24.7",
    "project": "pgai-agent-kanban",
    "workflow_type": "release",
    "opened_at": null,
    "closed_at": null,
    "wall_time_minutes": null,
    "outcome": "UNKNOWN",
    "tasks": {
      "total": <int>,
      "by_agent": { "coder": <int>, ... }
    },
    "tokens": {
      "total": {
        "input": <int>,
        "output": <int>,
        "cache_read": <int>,
        "cache_write": <int>,
        "invocations": <int>
      },
      "by_model": {
        "claude-opus-4-8": {
          "input": <int>, "output": <int>,
          "cache_read": <int>, "cache_write": <int>,
          "invocations": <int>
        }, ...
      },
      "by_agent": {
        "coder": {
          "input": <int>, "output": <int>,
          "cache_read": <int>, "cache_write": <int>,
          "invocations": <int>
        }, ...
      }
    },
    "bugs_filed_during_verification": [],
    "operator_interventions": [],
    "input_files": {}
  }

Per-day rollup (projects/<name>/metrics/day/<YYYY-MM-DD>.json):

  {
    "date": "2026-05-17",
    "project": "pgai-agent-kanban",
    "rcs_included": ["v0.24.7"],
    "tokens": {
      "total": { "input": ..., "output": ..., "cache_read": ..., "cache_write": ..., "invocations": ... },
      "by_model": { ... },
      "by_agent": { ... }
    }
  }

READ-TIME CANONICALIZATION
==========================

Shortname model IDs written by legacy subagent token capture are mapped at
read time to canonical model IDs.  Existing tokens.json files are NOT modified.

  opus   -> claude-opus-4-8
  sonnet -> claude-sonnet-4-6
  haiku  -> claude-haiku-4-5-20251001

Any model string not in the shortname map and not starting with "claude-" or a
known provider prefix is left unchanged (and a single warning is emitted).

RC ATTRIBUTION
==============

Each task is attributed to an RC by reading the "## Release Version" field from
the task's README.md.  This matches the approach used in aggregate_tokens.py
(the cost aggregator) so the two tools are consistent.

Fallback: if README.md is absent or the field is missing, the task is attributed
to the version "unknown" for RC roll-ups and is included in per-day roll-ups
based on its tokens.json timestamp.

IDEMPOTENCY
===========

The rollup is fully recomputed from source tokens.json files on each call.
Writing is done atomically: the payload is serialised with deterministic key
ordering (sort_keys=True) and written to the output path via a temp file and
os.replace(), so concurrent writers can't corrupt the file.  Because the input
is stable (existing tokens.json files are not modified) and the output is
deterministic JSON (sort_keys=True, fixed indent), running aggregate_rc() twice
on the same input produces byte-identical output.

USAGE (CLI)
===========

  python3 metrics_aggregator.py --project pgai-agent-kanban --rc v0.24.7
  python3 metrics_aggregator.py --project pgai-agent-kanban --day 2026-05-17
  python3 metrics_aggregator.py --project pgai-agent-kanban --all

  Optional:
    --kanban-root PATH   Override PGAI_AGENT_KANBAN_ROOT_PATH
                         (default: ~/pgai_agent_kanban)

  Exit codes:
    0 -- success (warnings may be emitted to stderr)
    1 -- usage error or unrecoverable configuration failure
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Canonical model ID map (shortname -> canonical)
# Applied at read time; existing tokens.json files are NOT modified.
# ---------------------------------------------------------------------------
SHORTNAME_TO_CANONICAL: dict[str, str] = {
    "opus":   "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TOKENS_JSON = "artifacts/tokens.json"
README_MD = "README.md"
STATUS_MD = "status.md"

# Track which unrecognised shortnames we've already warned about (once per run).
_SHORTNAME_WARNED: set[str] = set()

# ---------------------------------------------------------------------------
# Helpers: kanban root resolution
# ---------------------------------------------------------------------------


def resolve_kanban_root(override: str | None = None) -> pathlib.Path:
    """Return the kanban root directory as an absolute Path.

    Priority:
      1. override argument (from --kanban-root CLI flag)
      2. PGAI_AGENT_KANBAN_ROOT_PATH environment variable (canonical)
      3. ~/pgai_agent_kanban default (fresh-install path)

    Exits with code 1 if the resolved path is not a directory.
    """
    if override:
        p = pathlib.Path(override).expanduser().resolve()
    else:
        root = (
            os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
            or str(pathlib.Path.home() / "pgai_agent_kanban")
        )
        p = pathlib.Path(root).expanduser().resolve()
    if not p.is_dir():
        print(f"ERROR: kanban root not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p


def resolve_project_dir(kanban_root: pathlib.Path, project: str) -> pathlib.Path:
    """Return the project directory, exiting with code 1 if it does not exist."""
    d = kanban_root / "projects" / project
    if not d.is_dir():
        print(f"ERROR: project directory not found: {d}", file=sys.stderr)
        sys.exit(1)
    return d


# ---------------------------------------------------------------------------
# Read-time canonicalization
# ---------------------------------------------------------------------------


def canonicalize_model(model: str) -> str:
    """Map a model shortname to its canonical ID at read time.

    If `model` is already a canonical ID (starts with a known provider prefix
    or contains a dash), it is returned unchanged.  Only the three known
    shortnames ('opus', 'sonnet', 'haiku') are mapped.

    A one-time warning is emitted for any unrecognised bare shortname (one
    without dashes that isn't in the known map).
    """
    global _SHORTNAME_WARNED

    if not model:
        return model

    canonical = SHORTNAME_TO_CANONICAL.get(model)
    if canonical is not None:
        return canonical

    # If the model ID contains a dash it's almost certainly already canonical.
    if "-" in model:
        return model

    # Unknown bare word (no dash, not in shortname map) — warn once.
    if model not in _SHORTNAME_WARNED:
        print(
            f"WARNING: unrecognised model shortname '{model}'; "
            "using it as-is. Add it to SHORTNAME_TO_CANONICAL if needed.",
            file=sys.stderr,
        )
        _SHORTNAME_WARNED.add(model)
    return model


# ---------------------------------------------------------------------------
# Helpers: markdown field extraction
# ---------------------------------------------------------------------------


def read_field_from_markdown(text: str, heading: str) -> str | None:
    """Return the first non-blank, non-comment line after '## <heading>'.

    Returns None if the heading or a value line is not found.
    """
    lines = text.splitlines()
    target = f"## {heading}"
    for i, line in enumerate(lines):
        if line.strip() == target:
            for follow in lines[i + 1:]:
                v = follow.strip()
                if v and not v.startswith("#"):
                    return v
            return None
    return None


# ---------------------------------------------------------------------------
# Helpers: task metadata reading
# ---------------------------------------------------------------------------


def load_tokens_json(task_dir: pathlib.Path) -> dict[str, Any] | None:
    """Load artifacts/tokens.json for a task directory.

    Returns the parsed dict, or None if the file is absent (silent) or
    malformed (with a stderr warning).
    """
    path = task_dir / TOKENS_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top-level value is not a JSON object")
        return data
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        print(
            f"WARNING: skipping malformed tokens.json for task {task_dir.name}: {exc}",
            file=sys.stderr,
        )
        return None


def read_rc_version(task_dir: pathlib.Path) -> str | None:
    """Read '## Release Version' from the task README.md.

    Returns the raw string value (e.g. 'v0.23.22') or None if not found.
    """
    readme = task_dir / README_MD
    if not readme.is_file():
        return None
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError:
        return None
    return read_field_from_markdown(text, "Release Version")


def read_agent(task_dir: pathlib.Path) -> str:
    """Read the agent type for a task.

    Priority:
      1. status.md '## Agent' field (strips model suffix, e.g. 'coder (claude-sonnet-4-6)' -> 'coder')
      2. README.md '## Role' field (lowercased)
      3. Task ID prefix: CLAUDE-<ROLE>-YYYYMMDD-NNN-slug -> <role> (lowercased)
      4. Fallback: 'unknown'
    """
    # 1. status.md Agent field
    status_path = task_dir / STATUS_MD
    if status_path.is_file():
        try:
            text = status_path.read_text(encoding="utf-8")
            val = read_field_from_markdown(text, "Agent")
            if val:
                return val.split("(")[0].strip().lower()
        except OSError:
            pass

    # 2. README.md Role field
    readme = task_dir / README_MD
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8")
            val = read_field_from_markdown(text, "Role")
            if val:
                return val.lower()
        except OSError:
            pass

    # 3. Derive from task folder name
    parts = task_dir.name.split("-")
    if len(parts) >= 2:
        return parts[1].lower()
    return "unknown"


def read_workflow_type(task_dir: pathlib.Path) -> str:
    """Read '## Workflow Type' from the task README.md.

    Returns the lowercased value or 'release' as default.
    """
    readme = task_dir / README_MD
    if readme.is_file():
        try:
            text = readme.read_text(encoding="utf-8")
            val = read_field_from_markdown(text, "Workflow Type")
            if val:
                return val.lower()
        except OSError:
            pass
    return "release"


# ---------------------------------------------------------------------------
# Version normalisation
# ---------------------------------------------------------------------------


def normalise_version(version: str) -> str:
    """Ensure the version string starts with 'v'."""
    version = version.strip()
    if version and not version.startswith("v"):
        return f"v{version}"
    return version


# ---------------------------------------------------------------------------
# Token accounting helpers
# ---------------------------------------------------------------------------


def _zero_token_entry() -> dict[str, Any]:
    """Return an empty token accumulator dict."""
    return {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "invocations": 0,
    }


def _add_tokens(
    target: dict[str, Any],
    input_tok: int,
    output_tok: int,
    cache_read: int,
    cache_write: int,
    invocations: int,
) -> None:
    """Accumulate token counts into a mutable target dict in place."""
    target["input"] += input_tok
    target["output"] += output_tok
    target["cache_read"] += cache_read
    target["cache_write"] += cache_write
    target["invocations"] += invocations


def _extract_token_counts(tok: dict[str, Any]) -> tuple[str, int, int, int, int, int]:
    """Extract and canonicalize token counts from a tokens.json record.

    Handles both schema variants:

    New schema (has 'model_usage' dict):
      Token counts are summed across model_usage entries.
      Read-time canonicalization is applied to each model key.

    Legacy schema (has top-level model/input_tokens/etc. fields):
      Read-time canonicalization is applied to the 'model' field.

    Returns:
      (canonical_model, input_tok, output_tok, cache_read, cache_write, invocations)

    For new-schema records, canonical_model is the first model key in model_usage
    (or 'unknown').  All individual model data is also available via
    _extract_model_usage_entries() for by_model breakdown.
    """
    invocations = int(tok.get("invocations", 1) or 1)

    model_usage: dict[str, Any] | None = tok.get("model_usage")
    if isinstance(model_usage, dict) and model_usage:
        # New schema: sum token counts across model entries
        total_input = 0
        total_output = 0
        total_cache_read = 0
        total_cache_write = 0
        first_model = "unknown"
        for idx, (raw_model, mu) in enumerate(model_usage.items()):
            if not isinstance(mu, dict):
                continue
            canonical = canonicalize_model(raw_model)
            if idx == 0:
                first_model = canonical
            total_input += int(mu.get("input_tokens", 0) or 0)
            total_output += int(mu.get("output_tokens", 0) or 0)
            total_cache_read += int(mu.get("cache_read_input_tokens", 0) or 0)
            total_cache_write += int(mu.get("cache_creation_input_tokens", 0) or 0)
        return (first_model, total_input, total_output, total_cache_read, total_cache_write, invocations)
    else:
        # Legacy schema
        raw_model = str(tok.get("model", "unknown"))
        canonical = canonicalize_model(raw_model)
        input_tok = int(tok.get("input_tokens", 0) or 0)
        output_tok = int(tok.get("output_tokens", 0) or 0)
        cache_read = int(tok.get("cache_read_input_tokens", 0) or 0)
        cache_write = int(tok.get("cache_creation_input_tokens", 0) or 0)
        return (canonical, input_tok, output_tok, cache_read, cache_write, invocations)


def _extract_model_usage_entries(
    tok: dict[str, Any],
    invocations: int,
) -> list[tuple[str, int, int, int, int, int]]:
    """Return a list of (canonical_model, input, output, cache_read, cache_write, invocations)
    for each model in this record, for populating the by_model breakdown.

    For legacy schema, returns a single entry with the top-level model.
    For new schema, returns one entry per model in model_usage.
    """
    model_usage: dict[str, Any] | None = tok.get("model_usage")
    if isinstance(model_usage, dict) and model_usage:
        entries = []
        for raw_model, mu in model_usage.items():
            if not isinstance(mu, dict):
                continue
            canonical = canonicalize_model(raw_model)
            entries.append((
                canonical,
                int(mu.get("input_tokens", 0) or 0),
                int(mu.get("output_tokens", 0) or 0),
                int(mu.get("cache_read_input_tokens", 0) or 0),
                int(mu.get("cache_creation_input_tokens", 0) or 0),
                invocations,
            ))
        return entries
    else:
        raw_model = str(tok.get("model", "unknown"))
        canonical = canonicalize_model(raw_model)
        return [(
            canonical,
            int(tok.get("input_tokens", 0) or 0),
            int(tok.get("output_tokens", 0) or 0),
            int(tok.get("cache_read_input_tokens", 0) or 0),
            int(tok.get("cache_creation_input_tokens", 0) or 0),
            invocations,
        )]


# ---------------------------------------------------------------------------
# Task scanner
# ---------------------------------------------------------------------------


def scan_tasks(tasks_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Walk tasks_dir and collect metadata for every task that has a tokens.json.

    Returns a list of records:
      {
        "task_id":       str,
        "task_dir":      Path,
        "rc_version":    str | None,   -- from README.md ## Release Version
        "agent":         str,          -- from status.md, README.md, or task ID
        "tokens":        dict,         -- raw tokens.json data
        "timestamp":     str | None,   -- tokens.json 'timestamp' field
      }
    """
    records: list[dict[str, Any]] = []

    if not tasks_dir.is_dir():
        return records

    for entry in sorted(tasks_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip queue subdirectory and similar non-task dirs
        if entry.name in ("queues", "queue"):
            continue

        tokens = load_tokens_json(entry)
        if tokens is None:
            continue  # No tokens.json or malformed (warning already emitted)

        rc_version = read_rc_version(entry)
        agent = read_agent(entry)
        timestamp = tokens.get("timestamp")

        records.append({
            "task_id":    entry.name,
            "task_dir":   entry,
            "rc_version": rc_version,
            "agent":      agent,
            "tokens":     tokens,
            "timestamp":  timestamp,
        })

    return records


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------


def _atomic_write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Write payload as deterministic JSON to path atomically.

    Uses a temp file in the same directory and os.replace() so concurrent
    writers cannot corrupt the output file.  sort_keys=True ensures the
    output is byte-identical for identical inputs (idempotency guarantee).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"

    # Write to a temp file in the same directory so os.replace() is atomic
    # on POSIX (same filesystem, no cross-device move).
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp-metrics-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Outcome detection
# ---------------------------------------------------------------------------


def _detect_outcome(rc_version: str, project_dir: pathlib.Path) -> str:
    """Detect the outcome of an RC by inspecting git tags and release-state JSON.

    Resolution order:
      1. Local git tag matching rc_version exists -> 'shipped'
      2. projects/<name>/release-state/<version>.json exists and has a
         recognised outcome field -> use that value
      3. Fallback -> 'unknown'

    Args:
      rc_version:   The normalised RC version string (e.g. 'v0.29.15').
      project_dir:  Absolute Path to the project directory (used both as the
                    git cwd and as the base for the release-state file lookup).

    Returns:
      One of: 'shipped', 'cancelled', 'failed', 'in_progress', 'unknown'.
    """
    # 1. Check for a local git tag (shipped RCs always have a tag).
    try:
        result = subprocess.run(
            ["git", "tag", "-l", rc_version],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip() == rc_version:
            return "shipped"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass

    # 2. Check the release-state JSON for a recorded outcome.
    state_path = project_dir / "release-state" / f"{rc_version}.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            recorded = state.get("outcome")
            if recorded in ("shipped", "cancelled", "failed", "in_progress"):
                return recorded
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Fallback.
    return "unknown"


# ---------------------------------------------------------------------------
# Core: aggregate_rc
# ---------------------------------------------------------------------------


def aggregate_rc(
    project: str,
    version: str,
    kanban_root: pathlib.Path | None = None,
    workflow_type: str = "release",
) -> pathlib.Path:
    """Produce a per-RC JSON rollup at projects/<project>/metrics/rc/<version>.json.

    Reads all tasks under projects/<project>/tasks/ that have a tokens.json
    file and whose README.md ## Release Version matches `version`.

    Applies read-time canonicalization to model shortnames so legacy
    tokens.json files (e.g., model='opus') contribute to the same by_model
    bucket as canonical-ID files (e.g., model='claude-opus-4-7').

    Idempotent: running twice on the same input produces byte-identical output.

    Args:
      project:       Project name under projects/ in the kanban root.
      version:       RC or document version string (e.g. 'v0.24.7' or '0.24.7').
      kanban_root:   Override the kanban root path (default: auto-resolve).
      workflow_type: Workflow type to record in the rollup JSON (default: 'release').
                     Pass 'document' for document-workflow versions so that
                     history.csv rows for document projects carry the correct
                     workflow_type rather than the hardcoded 'release' default.

    Returns:
      The absolute Path of the written JSON file.
    """
    root = kanban_root or resolve_kanban_root()
    project_dir = resolve_project_dir(root, project)
    tasks_dir = project_dir / "tasks"
    out_dir = project_dir / "metrics" / "rc"

    rc_version = normalise_version(version)

    records = scan_tasks(tasks_dir)

    # Filter to tasks attributed to this RC
    matching = [
        r for r in records
        if normalise_version(r.get("rc_version") or "") == rc_version
    ]

    # Counters
    tasks_total = len(matching)
    tasks_by_agent: dict[str, int] = {}
    total_tok = _zero_token_entry()
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}

    for r in matching:
        tok = r["tokens"]
        agent = r["agent"]
        invocations = int(tok.get("invocations", 1) or 1)

        # Accumulate task count by agent
        tasks_by_agent[agent] = tasks_by_agent.get(agent, 0) + 1

        # Extract token counts (with canonicalization)
        _, inp, out, cr, cw, inv = _extract_token_counts(tok)
        _add_tokens(total_tok, inp, out, cr, cw, inv)

        # by_agent
        if agent not in by_agent:
            by_agent[agent] = _zero_token_entry()
        _add_tokens(by_agent[agent], inp, out, cr, cw, inv)

        # by_model (one entry per model, for new schema potentially multiple)
        model_entries = _extract_model_usage_entries(tok, invocations)
        for canonical_model, m_inp, m_out, m_cr, m_cw, m_inv in model_entries:
            if canonical_model not in by_model:
                by_model[canonical_model] = _zero_token_entry()
            _add_tokens(by_model[canonical_model], m_inp, m_out, m_cr, m_cw, m_inv)

    # --- Outcome and timestamps from release-state JSON ---
    # Read projects/<name>/release-state/<version>.json when present.
    # For historical RCs that predate this fix the file may not exist; in
    # that case all three fields remain None and outcome falls back to 'unknown'.
    opened_at: str | None = None
    closed_at: str | None = None
    wall_time_minutes: float | None = None

    state_path = project_dir / "release-state" / f"{rc_version}.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            opened_at = state.get("opened_at") or None
            closed_at = state.get("closed_at") or None
            if opened_at and closed_at:
                try:
                    opened_dt = datetime.fromisoformat(
                        opened_at.replace("Z", "+00:00")
                    )
                    closed_dt = datetime.fromisoformat(
                        closed_at.replace("Z", "+00:00")
                    )
                    wall_time_minutes = round(
                        (closed_dt - opened_dt).total_seconds() / 60.0, 2
                    )
                except (ValueError, OverflowError) as exc:
                    print(
                        f"WARNING: could not compute wall_time_minutes for {rc_version}: {exc}",
                        file=sys.stderr,
                    )
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"WARNING: could not read release-state for {rc_version}: {exc}",
                file=sys.stderr,
            )

    # Detect outcome: git tag takes priority, then state JSON, then 'unknown'.
    outcome = _detect_outcome(rc_version, project_dir)

    payload: dict[str, Any] = {
        "rc":                rc_version,
        "project":           project,
        "workflow_type":     workflow_type,
        "opened_at":         opened_at,
        "closed_at":         closed_at,
        "wall_time_minutes": wall_time_minutes,
        "outcome":           outcome,
        "tasks": {
            "total":    tasks_total,
            "by_agent": tasks_by_agent,
        },
        "tokens": {
            "total":    total_tok,
            "by_model": by_model,
            "by_agent": by_agent,
        },
        "bugs_filed_during_verification": [],
        "operator_interventions":         [],
        "input_files":                    {},
    }

    out_path = out_dir / f"{rc_version}.json"
    _atomic_write_json(out_path, payload)
    print(f"[metrics_aggregator] RC rollup written: {out_path}", file=sys.stderr)
    return out_path


# ---------------------------------------------------------------------------
# Core: aggregate_day
# ---------------------------------------------------------------------------


def aggregate_day(
    project: str,
    day: str,
    kanban_root: pathlib.Path | None = None,
) -> pathlib.Path:
    """Produce a per-day JSON rollup at projects/<project>/metrics/day/<day>.json.

    Includes all tasks whose tokens.json 'timestamp' falls within the named
    UTC day (YYYY-MM-DD format).  Also includes tasks from per-RC rollups
    whose RC closed_at falls on this day (if such rollup files exist and
    have closed_at populated).

    Currently reads directly from tokens.json files (same as aggregate_rc),
    using the timestamp field for day attribution.  Per-RC rollup files are
    referenced in rcs_included for informational completeness.

    Args:
      project:     Project name under projects/ in the kanban root.
      day:         UTC day string in YYYY-MM-DD format.
      kanban_root: Override the kanban root path (default: auto-resolve).

    Returns:
      The absolute Path of the written JSON file.
    """
    root = kanban_root or resolve_kanban_root()
    project_dir = resolve_project_dir(root, project)
    tasks_dir = project_dir / "tasks"
    out_dir = project_dir / "metrics" / "day"

    try:
        day_obj = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        print(f"ERROR: invalid day format '{day}' (expected YYYY-MM-DD)", file=sys.stderr)
        sys.exit(1)

    def ts_in_day(ts_str: str | None) -> bool:
        """Return True when ts_str is an ISO-8601 timestamp on day_obj (UTC)."""
        if not ts_str:
            return False
        try:
            # Handle trailing Z and +HH:MM offsets
            clean = ts_str.strip()
            if clean.endswith("Z"):
                clean = clean[:-1]
            if "+" in clean:
                clean = clean.split("+")[0]
            dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
            return dt.date() == day_obj
        except ValueError:
            return False

    records = scan_tasks(tasks_dir)
    matching = [r for r in records if ts_in_day(r.get("timestamp"))]

    total_tok = _zero_token_entry()
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    rcs_seen: set[str] = set()

    for r in matching:
        tok = r["tokens"]
        agent = r["agent"]
        invocations = int(tok.get("invocations", 1) or 1)

        rc_ver = r.get("rc_version")
        if rc_ver:
            rcs_seen.add(normalise_version(rc_ver))

        # Extract token counts (with canonicalization)
        _, inp, out, cr, cw, inv = _extract_token_counts(tok)
        _add_tokens(total_tok, inp, out, cr, cw, inv)

        # by_agent
        if agent not in by_agent:
            by_agent[agent] = _zero_token_entry()
        _add_tokens(by_agent[agent], inp, out, cr, cw, inv)

        # by_model
        model_entries = _extract_model_usage_entries(tok, invocations)
        for canonical_model, m_inp, m_out, m_cr, m_cw, m_inv in model_entries:
            if canonical_model not in by_model:
                by_model[canonical_model] = _zero_token_entry()
            _add_tokens(by_model[canonical_model], m_inp, m_out, m_cr, m_cw, m_inv)

    payload: dict[str, Any] = {
        "date":          day,
        "project":       project,
        "rcs_included":  sorted(rcs_seen),
        "tokens": {
            "total":    total_tok,
            "by_model": by_model,
            "by_agent": by_agent,
        },
    }

    out_path = out_dir / f"{day}.json"
    _atomic_write_json(out_path, payload)
    print(f"[metrics_aggregator] Day rollup written: {out_path}", file=sys.stderr)
    return out_path


# ---------------------------------------------------------------------------
# --all mode helpers
# ---------------------------------------------------------------------------


def all_rc_versions(records: list[dict[str, Any]]) -> list[str]:
    """Return a sorted list of distinct RC versions seen in the records."""
    versions: set[str] = set()
    for r in records:
        rv = r.get("rc_version")
        if rv:
            versions.add(normalise_version(rv))
    return sorted(versions)


def all_days(records: list[dict[str, Any]]) -> list[str]:
    """Return a sorted list of distinct UTC days seen in the records."""
    days: set[str] = set()
    for r in records:
        ts_str = r.get("timestamp")
        if ts_str:
            try:
                clean = ts_str.strip().rstrip("Z")
                if "+" in clean:
                    clean = clean.split("+")[0]
                dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
                days.add(dt.strftime("%Y-%m-%d"))
            except ValueError:
                pass
    return sorted(days)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate per-task token usage into per-RC and per-day metrics rollups.\n"
            # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
            "Implements the schema defined in PRIORITY-0037."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        required=True,
        metavar="NAME",
        help="Project name (subdirectory of projects/ in the kanban root).",
    )
    parser.add_argument(
        "--rc",
        metavar="VERSION",
        help="Produce a per-RC rollup for the named version (e.g. v0.24.7).",
    )
    parser.add_argument(
        "--day",
        metavar="YYYY-MM-DD",
        help="Produce a per-day rollup for the named UTC day.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_rollups",
        help="Rebuild ALL per-RC and per-day rollups for the project.",
    )
    parser.add_argument(
        "--kanban-root",
        metavar="PATH",
        help="Override PGAI_AGENT_KANBAN_ROOT_PATH.",
    )
    parser.add_argument(
        "--workflow-type",
        metavar="TYPE",
        default="release",
        help=(
            "Workflow type to record in the rollup JSON (default: 'release'). "
            "Pass 'document' when aggregating a document-workflow version so that "
            "history.csv rows carry the correct workflow_type. "
            "Has no effect when --day or --all is used."
        ),
    )

    args = parser.parse_args()

    if not args.rc and not args.day and not args.all_rollups:
        parser.error("at least one of --rc, --day, or --all is required")

    kanban_root = resolve_kanban_root(args.kanban_root)
    project = args.project

    if args.all_rollups:
        project_dir = resolve_project_dir(kanban_root, project)
        tasks_dir = project_dir / "tasks"
        records = scan_tasks(tasks_dir)
        print(
            f"[metrics_aggregator] --all: found {len(records)} task(s) with tokens.json",
            file=sys.stderr,
        )
        versions = all_rc_versions(records)
        days = all_days(records)
        print(
            f"[metrics_aggregator] --all: building {len(versions)} RC rollup(s)"
            f" and {len(days)} day rollup(s).",
            file=sys.stderr,
        )
        for ver in versions:
            aggregate_rc(project, ver, kanban_root=kanban_root)
        for day in days:
            aggregate_day(project, day, kanban_root=kanban_root)
    else:
        if args.rc:
            aggregate_rc(
                project,
                args.rc,
                kanban_root=kanban_root,
                workflow_type=args.workflow_type,
            )
        if args.day:
            aggregate_day(project, args.day, kanban_root=kanban_root)

    sys.exit(0)


if __name__ == "__main__":
    main()
