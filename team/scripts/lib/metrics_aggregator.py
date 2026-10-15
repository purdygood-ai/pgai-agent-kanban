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
    "blocked_minutes": null,
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
    "wall": {
      "by_agent": {
        "coder": {
          "seconds": <number>,   -- sum of elapsed_seconds across this
                                  -- agent's tasks in this RC
          "task_count": <int>    -- count of that agent's tasks that had a
                                  -- usable elapsed_seconds (the averaging
                                  -- denominator; not the same as
                                  -- tasks.by_agent, which counts every
                                  -- task regardless of timing data)
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

ARCHIVED TASKS
==============

Every scan reads the project's live task folders and its archived ones
(archive/<project>/tasks/<ID>/, written by the archive run after a release
ships), merged in task-ID order. A task folder keeps its README.md,
status.md and artifacts/tokens.json when it is archived, so a rollup
rebuilt after an archive run -- --all, a missing --rc/--day file built on
demand, or the late-landing refresh -- is byte-identical to one built
before. A task that is live is read from its live folder only.

RC ATTRIBUTION
==============

Each task is attributed to an RC by reading the "## Release Version" field from
the task's README.md.  This matches the approach used in aggregate_tokens.py
(the cost aggregator) so the two tools are consistent.

Fallback: if README.md is absent or the field is missing, the task is attributed
to the version "unknown" for RC roll-ups and is included in per-day roll-ups
based on its tokens.json timestamp.

PER-AGENT WALL TIME
====================

The RC-level "wall_time_minutes" field is the elapsed time of the whole RC --
every agent that touched the RC shares that one number, so it cannot answer
"how long does coder's own work typically take." The "wall"."by_agent" block
answers that: it sums each task's own tokens.json "elapsed_seconds" per
agent, alongside a "task_count" of how many of that agent's tasks actually
carried a usable value. A task whose tokens.json has no "elapsed_seconds", or
a non-numeric one, is left out of both the sum and the count -- it is never
folded in as a zero, which would silently drag the agent's average down.
Downstream averaging (e.g. the per-agent metrics window) divides
"seconds" by "task_count" itself; this rollup only carries the raw sums.

BLOCKED MINUTES
===============

"wall_time_minutes" is honest open-to-ship elapsed time and is never
touched by this field. "blocked_minutes" is the second number: how many
of those minutes the RC spent unable to proceed at all, read exclusively
through team/pgai_agent_kanban/metrics/blocked_ledger.py's blocked_minutes()
-- the single reader for that ledger, never re-derived here from HALT
files, logs, or task-folder scans. It is computed only when the RC has
both opened_at and closed_at (the same window wall_time_minutes uses);
without a window there is nothing to clamp against and the field is left
None, exactly like wall_time_minutes itself. Given a window: an RC with
no ledger, or no boundaries recorded for its own version, reads back as
the real number 0 (never an empty value) -- absence of blocking is a
measured zero, not missing data. A ledger that fails to parse degrades
the field to None with a stderr warning instead of failing this
aggregator or any of its callers -- CENSUS CLASS CONVERT
(docs/blocking-site-census.md): a malformed ledger must never gate a
release rollup. Whole-minute totals are recorded as plain integers
(e.g. 50, not 50.0); a boundary landing off the minute keeps two decimal
places, matching wall_time_minutes' own rounding.

ONE-RELEASE LAG BETWEEN A FIELD MERGING HERE AND IT APPEARING IN ROLLUPS
==========================================================================

This aggregator is invoked at release-finalize time from the LIVE INSTALL
copy of this file (the finalize steps in
team/scripts/lib/cm/release_finalize.sh and team/scripts/cm/finalize.sh both
shell out to "$KANBAN_ROOT/scripts/lib/metrics_aggregator.py", never to the
dev-tree copy), so a field added or changed here does not take effect the
moment the change merges to an RC branch. It takes effect once the LIVE
INSTALL is refreshed to a version carrying the change -- which normally
happens as part of shipping that very release, and the refresh happens
AFTER that release's own RC rollup has already been produced by the OLD
(pre-change) aggregator. Concretely: the release whose merge adds a new
output field (as "wall" was added) ships an RC rollup written by the
pre-change aggregator and therefore WITHOUT that field; only RCs finalized
in a later release, once the live install has been refreshed past the
merging one, carry it. The lag is invisible from the dev tree -- the new
code reads as present and correct there the whole time -- and is purely a
live-install-refresh timing effect, not a code defect; do not mistake a
field's absence from the RC that introduced it for a bug in the write path.

A TOKENS.JSON THAT LANDS AFTER ITS RC'S ROLLUP
===============================================

aggregate_rc() counts only the tasks that have a tokens.json when it runs,
and it runs from release finalize while the CM release task driving that
finalize is still in its agent session. The wake driver writes a task's
tokens.json only after the session ends, so the release task's own usage
lands minutes after the rollup it caused to be written -- left alone, every
shipped RC's rollup omits the task that shipped it, along with that task's
tokens and elapsed_seconds. The wake driver closes the gap at its finalize
stage: once a task's tokens.json is final, metrics_rollup_refresh.sh's
metrics_rollup_refresh_for_task reads the task's "## Release Version" and,
when a rollup for that version already exists, re-runs this CLI for that
one version (passing the rollup's own workflow_type back through
--workflow-type) so the rollup is recomputed from every tokens.json now on
disk. The refresh is the same deterministic recompute as the original
write, so the result is exactly what finalize would have produced had the
task's tokens.json existed then. It runs from the live-install copy of the
wake driver, so it carries the same one-release lag described above: the
RC whose release first ships the refresh is finalized by the pre-refresh
driver and its own rollup is not refreshed; the next RC's is.

The history.csv row appended at finalize is not revisited by the refresh --
that file is append-only and its writer skips an RC that already has a
row, so the row keeps the finalize-time figures.

BACKFILL DECISION FOR A NEWLY ADDED FIELD
===========================================

Historical per-RC rollups are NOT backfilled when a new field is added to
this schema (this was the explicit decision for "wall"."by_agent" and is
the standing policy for any future field), and they are likewise NOT
backfilled by the late-landing tokens.json refresh described in the
section above: that refresh fires only for a tokens.json written while the
refresh is live, and never scans for rollups older than their tasks'
tokens.json files. Every rollup written before the refresh existed keeps
the task count and token totals it was written with. An operator who wants
one specific historical rollup corrected re-runs this CLI for that version
by hand (--rc), which is the supported regeneration path. Two reasons
converge on not backfilling automatically:

  1. aggregate_rc() always recomputes a rollup from scratch from its source
     tokens.json files (see IDEMPOTENCY below) and never reads or merges
     into a prior rollup -- there is no code path that could patch a field
     into an existing file even if backfilling were wanted.
  2. projects/<name>/metrics/rc/*.json is the only per-RC, per-agent
     source that exists. The other persisted metrics record,
     metrics/history.csv, carries only an RC-level figure with no
     per-agent breakdown. For a rollup written before a field existed,
     nothing else on disk carries the missing data to backfill FROM.

The consequence a caller must expect: a reader averaging over a window that
spans both pre-field and post-field rollups (e.g. compute_agent_averages()
in team/pgai_agent_kanban/lib/agent_metrics.py) simply excludes the
field-less RCs from that one average rather than erroring or treating the
absence as zero -- see that module's docstring for the full reader-side
contract. The window self-heals with no further action, one RC at a time,
as wall-bearing rollups enter the window and pre-field ones age out the
tail. No manual backfill step exists or is planned.

IDEMPOTENCY
===========

The rollup is fully recomputed from source tokens.json files on each call.
Writing is done atomically: the payload is serialised with deterministic key
ordering (sort_keys=True) and written through the shared atomic writer
(pgai_agent_kanban.lib.atomic_write: a same-directory temp file, fsync, then
os.replace()), so concurrent writers can't corrupt the file.  Because the input
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
from datetime import datetime, timezone
from typing import Any

# Import the blocked-interval ledger reader from the pgai_agent_kanban
# package -- the ONE place blocked_minutes() is defined
# (team/pgai_agent_kanban/metrics/blocked_ledger.py). This module runs from
# within the full kanban tree (dev tree or live install), where
# team/pgai_agent_kanban/ is always a sibling of team/scripts/, so reaching
# INTO the package from here is the same safe self-location direction
# team/scripts/lib/agent_metrics.py already uses for its own package import.
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # lib/ -> scripts/ -> team/
_team_dir_str = str(_TEAM_DIR)
if _team_dir_str not in sys.path:
    sys.path.insert(0, _team_dir_str)
from pgai_agent_kanban.metrics.blocked_ledger import (  # noqa: E402
    LedgerError,
    blocked_minutes as read_blocked_minutes,
    ledger_path_for_project,
)
from pgai_agent_kanban.task_status import section_value  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.archive.lookup import task_dirs_with_archived  # noqa: E402

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
    """Return the value of the '## <heading>' section, or None.

    The value is read by task_status.section_value -- the first non-blank
    line of the section, HTML comments dropped and a bold wrapper removed.
    An empty section reads as None rather than as the next section's text.

    Returns None if the heading or a value line is not found.
    """
    return section_value(text, heading) or None


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


def _extract_elapsed_seconds(tok: dict[str, Any]) -> float | None:
    """Return a task's tokens.json 'elapsed_seconds' as a number, or None.

    None is returned -- with no exception raised -- when the field is
    absent, is a bool (which json.loads would otherwise happily hand back
    as an int subclass), or is any other non-numeric value (string, list,
    dict, null). Callers treat None as "this task contributes nothing to
    per-agent wall aggregation," never as zero.
    """
    val = tok.get("elapsed_seconds")
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return val


# ---------------------------------------------------------------------------
# Task scanner
# ---------------------------------------------------------------------------


def scan_tasks(tasks_dir: pathlib.Path) -> list[dict[str, Any]]:
    """Walk tasks_dir and collect metadata for every task that has a tokens.json.

    The walk covers the project's archived task folders too
    (archive/<project>/tasks/<ID>/, for IDs with no live folder), merged
    with the live ones in task-ID order -- see
    pgai_agent_kanban.archive.lookup.task_dirs_with_archived -- so a rollup
    rebuilt after an archive run counts exactly the tasks, tokens and wall
    time it counted before the run.

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

    for entry in task_dirs_with_archived(tasks_dir):
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


def _write_rollup_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Write payload as deterministic JSON to path through the shared writer.

    The parent directory is created first. ``atomic_write`` writes a scratch
    file in the same directory, fsyncs it, and renames it over *path*, so a
    reader sees the previous rollup or the new one, never a partial file,
    and a failed write leaves the previous rollup in place with no scratch
    file behind. sort_keys=True, indent=2 and the trailing newline make the
    output byte-identical for identical inputs (idempotency guarantee).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write(path, content, prefix=".tmp-metrics-")


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

    Reads all tasks under projects/<project>/tasks/ and
    archive/<project>/tasks/ (see scan_tasks) that have a tokens.json
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
    wall_by_agent: dict[str, dict[str, Any]] = {}

    for r in matching:
        tok = r["tokens"]
        agent = r["agent"]
        invocations = int(tok.get("invocations", 1) or 1)

        # Accumulate task count by agent
        tasks_by_agent[agent] = tasks_by_agent.get(agent, 0) + 1

        # Accumulate per-agent wall-clock seconds from this task's own
        # elapsed_seconds. A task with no usable value (absent or
        # non-numeric) contributes nothing -- it is never counted as a
        # zero-second task, since that would silently pull an agent's
        # average down.
        elapsed = _extract_elapsed_seconds(tok)
        if elapsed is not None:
            if agent not in wall_by_agent:
                wall_by_agent[agent] = {"seconds": 0, "task_count": 0}
            wall_by_agent[agent]["seconds"] += elapsed
            wall_by_agent[agent]["task_count"] += 1

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

    # --- Blocked minutes from the per-project blocked-interval ledger ---
    # See the module docstring's "BLOCKED MINUTES" section for the full
    # contract. Read exclusively through blocked_ledger.blocked_minutes()
    # -- never re-derived here -- and only when a window (opened_at AND
    # closed_at) exists to clamp against; without one the field stays
    # None, exactly like wall_time_minutes does in the same situation.
    blocked_minutes_value: float | int | None = None
    if opened_at and closed_at:
        ledger_path = ledger_path_for_project(str(root), project)
        try:
            raw_blocked = read_blocked_minutes(ledger_path, rc_version, opened_at, closed_at)
            blocked_minutes_value = (
                int(raw_blocked) if raw_blocked == int(raw_blocked) else round(raw_blocked, 2)
            )
        except LedgerError as exc:
            # CENSUS CLASS: CONVERT (docs/blocking-site-census.md) -- a
            # malformed ledger degrades this one field to an absent value;
            # it never fails the aggregator or any of its callers.
            print(
                f"WARNING: could not read blocked-interval ledger for {rc_version}: {exc}",
                file=sys.stderr,
            )
            blocked_minutes_value = None

    payload: dict[str, Any] = {
        "rc":                rc_version,
        "project":           project,
        "workflow_type":     workflow_type,
        "opened_at":         opened_at,
        "closed_at":         closed_at,
        "wall_time_minutes": wall_time_minutes,
        "blocked_minutes":   blocked_minutes_value,
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
        "wall": {
            "by_agent": wall_by_agent,
        },
        "bugs_filed_during_verification": [],
        "operator_interventions":         [],
        "input_files":                    {},
    }

    out_path = out_dir / f"{rc_version}.json"
    _write_rollup_json(out_path, payload)
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
    _write_rollup_json(out_path, payload)
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
    """Run the metrics aggregator CLI: build per-RC and per-day metrics roll-ups.

    ``--project NAME`` with at least one of ``--rc VERSION``, ``--day DATE`` or
    ``--all`` (``--kanban-root`` and ``--workflow-type`` optional). Exit codes:
    0 success, warnings possibly on stderr; 1 the kanban root or project
    directory is missing, or ``--day`` is not YYYY-MM-DD; 2 usage error
    (argparse, including none of --rc/--day/--all).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate per-task token usage into per-RC and per-day metrics rollups.\n"
            "Rollups are fixed-schema, append-only summaries keyed by RC and by day."
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
