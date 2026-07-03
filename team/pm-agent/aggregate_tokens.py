#!/usr/bin/env python3
"""
aggregate_tokens.py -- Token usage roll-up aggregator for pgai-agent-kanban.

Produces per-RC and per-day token usage roll-up files from per-task tokens.json
artifacts written by the wake script (via team/scripts/lib/token_capture.sh).

RC ATTRIBUTION SIGNAL:
  A task is attributed to a release candidate (RC) by reading the
  "## Release Version" field from the task's README.md file.  This field
  is populated by the PM agent when it materialises task folders, and
  matches the RC branch name (e.g., "v0.23.22").

  Why this signal:
    - README.md is written by PM at task creation time, before any code lands.
      It is therefore stable: it cannot be contaminated by later status changes
      or branch operations.
    - It is project-scope independent: works for any project under
      projects/<name>/tasks/
    - It is human-readable and auditable without running git commands.
    - The alternative (inspecting git branch names or merge commits) would
      require the aggregator to have access to the git repo and would couple
      it to the CM workflow.  The README.md field is simpler and more robust
      for an aggregation use case.

  Fallback: if README.md is absent or the field is missing, the task is
  attributed to the version "unknown" for RC roll-ups and is included for
  per-day roll-ups (its timestamp falls wherever the tokens.json says it does).

TOKEN SCHEMA (two schemas are supported):

  NEW SCHEMA (written by token_capture.sh when the claude CLI provides
  total_cost_usd and modelUsage):
    {
      "provider":       "claude",
      "agent":          "coder",
      "rc_version":     "v0.24.3",
      "invocations":    2,
      "elapsed_seconds": 90,
      "timestamp":      "2026-05-17T03:24:46Z",
      "total_cost_usd": 0.027,
      "model_usage": {
        "claude-opus-4-7": {
          "input_tokens": 12,
          "output_tokens": 14,
          "cache_creation_input_tokens": 0,
          "cache_read_input_tokens": 52366,
          "cost_usd": 0.026593
        },
        ...
      }
    }
    The aggregator uses total_cost_usd as the authoritative per-file cost and
    reads per-model cost from model_usage[<model>].cost_usd.

  LEGACY SCHEMA (written by token_capture.sh when the old CLI or fallback path
  is used; also present in all historical tokens.json files):
    {
      "model":                       "claude-sonnet-4-6",
      "provider":                    "claude",
      "agent":                       "coder",
      "input_tokens":                N,
      "output_tokens":               N,
      "cache_creation_input_tokens": N,
      "cache_read_input_tokens":     N,
      "invocations":                 N,
      "elapsed_seconds":             N,
      "timestamp":                   "..."
    }
    The aggregator computes cost from token_pricing.json using the four token
    categories.  A single aggregated fallback warning is emitted once per run
    noting the count of files that used the legacy path.

TOKEN CATEGORY ACCOUNTING (legacy schema only):
  Anthropic bills input tokens in three distinct categories, each with its own
  per-1M rate:

    input_tokens                  -- regular (non-cached) new input tokens
    cache_creation_input_tokens   -- tokens written to the prompt cache this turn
    cache_read_input_tokens       -- tokens read from the prompt cache this turn

  Output tokens are a fourth category with their own rate.

  The aggregator sums all four categories separately and computes per-category
  cost from the matching rate field in token_pricing.json:

    input_per_1m              -- rate for input_tokens
    cache_creation_per_1m     -- rate for cache_creation_input_tokens (1.25x input)
    cache_read_per_1m         -- rate for cache_read_input_tokens (0.10x input)
    output_per_1m             -- rate for output_tokens

  Older tokens.json files written before cache-field capture was added will
  simply have 0 for the cache fields, which is correct (they incurred no cache
  charges because they were not captured).

OUTPUT SCHEMA (stable -- downstream consumers depend on this):
  RC roll-up (usage/rc/<version>-tokens.json):
    {
      "version": "v0.23.22",
      "shipped_at": "2026-05-16T12:00:00Z",
      "tasks": [
        {
          "task_id": "ROLE-YYYYMMDD-NNN-slug",
          "agent": "coder",
          "model": "claude-sonnet-4-6",
          "input": <int>,
          "output": <int>,
          "cache_creation_tokens": <int>,
          "cache_read_tokens": <int>,
          "cost_usd": <float>,
          "input_cost_usd": <float>,
          "cache_creation_cost_usd": <float>,
          "cache_read_cost_usd": <float>,
          "output_cost_usd": <float>,
          "invocations": <int>
        }, ...
      ],
      "totals": {
        "input_tokens": <int>,
        "output_tokens": <int>,
        "cache_creation_tokens": <int>,
        "cache_read_tokens": <int>,
        "cost_usd": <float>,
        "input_cost_usd": <float>,
        "cache_creation_cost_usd": <float>,
        "cache_read_cost_usd": <float>,
        "output_cost_usd": <float>,
        "invocations": <int>
      }
    }

  Daily roll-up (usage/daily/YYYY-MM-DD.json):
    {
      "date": "2026-05-16",
      "project": "pgai-agent-kanban",
      "rcs_shipped": ["v0.23.22"],
      "totals": {
        "input_tokens": <int>,
        "output_tokens": <int>,
        "cache_creation_tokens": <int>,
        "cache_read_tokens": <int>,
        "cost_usd": <float>,
        "input_cost_usd": <float>,
        "cache_creation_cost_usd": <float>,
        "cache_read_cost_usd": <float>,
        "output_cost_usd": <float>,
        "invocations": <int>
      },
      "by_agent": {
        "<agent>": {
          "input": <int>, "output": <int>,
          "cache_creation_tokens": <int>, "cache_read_tokens": <int>,
          "cost_usd": <float>,
          "input_cost_usd": <float>, "cache_creation_cost_usd": <float>,
          "cache_read_cost_usd": <float>, "output_cost_usd": <float>,
          "invocations": <int>
        }, ...
      },
      "by_model": {
        "<model>": {
          "input": <int>, "output": <int>,
          "cache_creation_tokens": <int>, "cache_read_tokens": <int>,
          "cost_usd": <float>,
          "input_cost_usd": <float>, "cache_creation_cost_usd": <float>,
          "cache_read_cost_usd": <float>, "output_cost_usd": <float>,
          "invocations": <int>
        }, ...
      }
    }

USAGE:
  python3 aggregate_tokens.py --project <name> [--rc <version>] [--day YYYY-MM-DD] [--all]

  Flags (at least one roll-up target is required):
    --project <name>    Project name under $KANBAN_ROOT/projects/<name>/
    --rc <version>      Produce a per-RC roll-up for the named version.
                        Version may include or omit the leading "v"
                        (e.g., "v0.23.22" and "0.23.22" are both accepted).
    --day YYYY-MM-DD    Produce a per-day roll-up for the named UTC day.
    --all               Rebuild ALL per-RC and per-day roll-ups for the project
                        (scans every task's tokens.json).
    --kanban-root <p>   Override PGAI_AGENT_KANBAN_ROOT_PATH
                        (default: ~/pgai_agent_kanban).

  Output paths (relative to kanban root):
    projects/<name>/usage/rc/<version>-tokens.json
    projects/<name>/usage/daily/YYYY-MM-DD.json

  Exit codes:
    0 -- success (warnings may have been emitted to stderr)
    1 -- usage error or unrecoverable configuration failure

  Graceful degradation:
    - Missing or malformed tokens.json: skip with a single stderr warning, continue.
    - Missing token_pricing.json or unknown model: set cost_usd to 0 for that task,
      emit ONE stderr warning per missing-pricing-file event (not per task), continue.
    - Unknown model: emit ONE stderr warning per unknown model ID, continue with cost=0.
    - Legacy-schema files: emit ONE aggregated warning per run noting how many files
      used the fallback pricing-table path.
    - Output directories are created automatically (mkdir -p semantics).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TOKENS_JSON = "artifacts/tokens.json"
README_MD = "README.md"
STATUS_MD = "status.md"

# Path to pricing file, relative to the dev tree root or kanban root
PRICING_REL_PATH = "team/scripts/lib/token_pricing.json"

# ---------------------------------------------------------------------------
# Helpers: kanban root resolution
# ---------------------------------------------------------------------------


def resolve_kanban_root(override: str | None) -> pathlib.Path:
    """Return the kanban root directory as an absolute Path.

    Precedence: explicit override > PGAI_AGENT_KANBAN_ROOT_PATH (canonical) >
    ~/pgai_agent_kanban.
    """
    if override:
        p = pathlib.Path(override).expanduser().resolve()
    else:
        env = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "")
        if env:
            p = pathlib.Path(env).expanduser().resolve()
        else:
            p = pathlib.Path.home() / "pgai_agent_kanban"
    if not p.is_dir():
        print(f"ERROR: kanban root not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p


def dev_tree_path_for_project(kanban_root: pathlib.Path, project: str) -> pathlib.Path | None:
    """
    Try to read dev_tree_path from project.cfg (preferred) or PROJECT.cfg (fallback)
    for the given project.
    Returns None if not found (pricing lookup falls back to kanban_root).
    """
    proj_dir = kanban_root / "projects" / project
    new_cfg = proj_dir / "project.cfg"
    old_cfg = proj_dir / "PROJECT.cfg"
    cfg = new_cfg if new_cfg.is_file() else (old_cfg if old_cfg.is_file() else None)
    if cfg is None:
        return None
    for line in cfg.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() != "dev_tree_path":
            continue
        val = val.strip().strip('"').strip("'")
        if val:
            return pathlib.Path(val).expanduser().resolve()
    return None


# ---------------------------------------------------------------------------
# Helpers: pricing
# ---------------------------------------------------------------------------

# Emit one warning per run (not per task) when pricing data is absent/unknown.
_PRICING_WARNED = False

# Track which model IDs have already triggered a "not found" warning (one per run).
_MODEL_WARNED: set[str] = set()

def _emit_legacy_fallback_warning(count: int) -> None:
    """
    Emit the aggregated legacy-fallback warning once per run.
    count -- number of legacy-schema token files encountered during this run.
    Emits only when count > 0.
    """
    if count > 0:
        print(
            f"WARNING: {count} token file(s) used the legacy schema "
            "(no total_cost_usd / model_usage fields); cost computed from token_pricing.json "
            "pricing table. Historical files predate the new schema — this is expected.",
            file=sys.stderr,
        )


def load_pricing(kanban_root: pathlib.Path, project: str) -> dict[str, Any]:
    """
    Load token_pricing.json.  Checks the project dev_tree_path first,
    then falls back to the kanban root PRICING_REL_PATH.

    Returns an empty dict if the file is absent (caller handles gracefully).
    """
    global _PRICING_WARNED

    dev_tree = dev_tree_path_for_project(kanban_root, project)
    candidates: list[pathlib.Path] = []
    if dev_tree:
        candidates.append(dev_tree / PRICING_REL_PATH)
    candidates.append(kanban_root / PRICING_REL_PATH)

    for path in candidates:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                if not _PRICING_WARNED:
                    print(
                        f"WARNING: token_pricing.json is malformed ({path}): {exc}; "
                        "cost_usd will be 0 for all tasks.",
                        file=sys.stderr,
                    )
                    _PRICING_WARNED = True
                return {}

    # File not found in any candidate location
    if not _PRICING_WARNED:
        first = candidates[0] if candidates else pathlib.Path("<unresolvable>")
        print(
            f"WARNING: token_pricing.json not found (looked in {len(candidates)} location(s)); "
            "cost_usd will be 0 for all tasks. "
            f"Expected at: {first}",
            file=sys.stderr,
        )
        _PRICING_WARNED = True
    return {}


class CategoryCosts(NamedTuple):
    """Per-category cost breakdown for a single token record."""
    input_cost_usd: float
    cache_creation_cost_usd: float
    cache_read_cost_usd: float
    output_cost_usd: float

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.cache_creation_cost_usd + self.cache_read_cost_usd + self.output_cost_usd


_ZERO_COSTS = CategoryCosts(0.0, 0.0, 0.0, 0.0)


def is_new_schema(tok: dict[str, Any]) -> bool:
    """
    Return True when tok has the new-schema fields: total_cost_usd (float or int)
    and model_usage (non-empty dict).

    New schema has total_cost_usd and model_usage (non-empty dict).
    Legacy schema has top-level model/input_tokens/output_tokens/cache_* fields
    and no total_cost_usd or model_usage.
    """
    total_cost = tok.get("total_cost_usd")
    model_usage = tok.get("model_usage")
    return (
        isinstance(total_cost, (int, float))
        and isinstance(model_usage, dict)
        and len(model_usage) > 0
    )


def is_captured(tok: dict[str, Any]) -> bool:
    """
    Return True when the tokens.json record represents a successful capture.

    Returns False only when tok has an explicit ``captured: false`` field,
    which is written by token_capture.sh when the agent exited without emitting
    usage JSON (e.g. was killed mid-run).  Records that pre-date this field
    (no ``captured`` key) are assumed to be successful captures.
    """
    val = tok.get("captured", True)
    # Explicit False (not falsy-zero, not absent) means capture failed.
    return val is not False


def compute_cost(pricing: dict[str, Any], tok: dict[str, Any]) -> CategoryCosts:
    """
    Compute per-category cost_usd for a legacy-schema tokens.json record given
    the pricing table.

    Returns CategoryCosts(0, 0, 0, 0) if pricing is absent.
    Returns CategoryCosts(0, 0, 0, 0) and emits a ONE-TIME stderr warning if
    the model is not found in the pricing table.

    Each category is computed using its own per-1M rate:
      input_tokens              * input_per_1m
      cache_creation_input_tokens * cache_creation_per_1m
      cache_read_input_tokens   * cache_read_per_1m
      output_tokens             * output_per_1m

    This function is only called for legacy-schema records; new-schema records
    use the authoritative total_cost_usd field and model_usage.cost_usd instead.
    """
    if not pricing:
        return _ZERO_COSTS

    provider = tok.get("provider", "claude")
    model = tok.get("model", "")

    providers_block = pricing.get("providers", {})
    model_rates: dict[str, Any] | None = None

    # Try exact provider match first
    provider_block = providers_block.get(provider, {})
    model_rates = provider_block.get("models", {}).get(model)

    # Try all providers if exact match failed
    if model_rates is None and model:
        for _pb in providers_block.values():
            candidate = _pb.get("models", {}).get(model)
            if candidate is not None:
                model_rates = candidate
                break

    if model_rates is None:
        # Emit a one-time warning per unknown model ID so the operator knows
        # the pricing table needs updating — silently returning 0 hides the gap.
        model_key = f"{provider}/{model}" if model else f"{provider}/<empty>"
        if model_key not in _MODEL_WARNED:
            print(
                f"WARNING: model '{model}' (provider='{provider}') not found in "
                "token_pricing.json; cost_usd=0 for this task. "
                "Update token_pricing.json to add this model's rates.",
                file=sys.stderr,
            )
            _MODEL_WARNED.add(model_key)
        return _ZERO_COSTS

    input_tok     = int(tok.get("input_tokens", 0) or 0)
    output_tok    = int(tok.get("output_tokens", 0) or 0)
    cache_create  = int(tok.get("cache_creation_input_tokens", 0) or 0)
    cache_read    = int(tok.get("cache_read_input_tokens", 0) or 0)

    input_cost         = input_tok    * float(model_rates.get("input_per_1m",            0)) / 1_000_000
    cache_create_cost  = cache_create * float(model_rates.get("cache_creation_per_1m",   0)) / 1_000_000
    cache_read_cost    = cache_read   * float(model_rates.get("cache_read_per_1m",        0)) / 1_000_000
    output_cost        = output_tok   * float(model_rates.get("output_per_1m",            0)) / 1_000_000

    return CategoryCosts(
        input_cost_usd=round(input_cost, 6),
        cache_creation_cost_usd=round(cache_create_cost, 6),
        cache_read_cost_usd=round(cache_read_cost, 6),
        output_cost_usd=round(output_cost, 6),
    )


# ---------------------------------------------------------------------------
# Helpers: task discovery and reading
# ---------------------------------------------------------------------------


def read_field_from_markdown(text: str, heading: str) -> str | None:
    """
    Read the first non-blank, non-comment line after "## <heading>" in a
    markdown file.  Returns None if the heading or a value is not found.
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


def load_tokens_json(task_dir: pathlib.Path) -> dict[str, Any] | None:
    """
    Load artifacts/tokens.json for a task directory.

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
    """
    Read '## Release Version' from the task README.md.
    Returns the raw string value (e.g. "v0.23.22") or None.
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
    """
    Read the agent type for a task.

    Priority:
      1. status.md '## Agent' field (e.g. "coder (claude-sonnet-4-6)" -> "coder")
      2. README.md '## Role' field (lowercased)
      3. Task ID prefix: ROLE-YYYYMMDD-NNN-slug -> <role>
    """
    # 1. status.md Agent field
    status = task_dir / STATUS_MD
    if status.is_file():
        try:
            text = status.read_text(encoding="utf-8")
            val = read_field_from_markdown(text, "Agent")
            if val:
                # Strip model suffix: "coder (claude-sonnet-4-6)" -> "coder"
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

    # 3. Derive from task folder name: ROLE-YYYYMMDD-NNN-slug
    parts = task_dir.name.split("-")
    if len(parts) >= 1:
        return parts[0].lower()
    return "unknown"


# ---------------------------------------------------------------------------
# Task scan
# ---------------------------------------------------------------------------


def scan_tasks(tasks_dir: pathlib.Path) -> list[dict[str, Any]]:
    """
    Walk the tasks/ directory and collect metadata for every task that has
    a tokens.json file.

    Returns a list of records:
      {
        "task_id":    str,
        "task_dir":   Path,
        "rc_version": str | None,
        "tokens":     dict,
        "agent":      str,
        "new_schema": bool,   -- True when total_cost_usd + model_usage present
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

        records.append({
            "task_id":    entry.name,
            "task_dir":   entry,
            "rc_version": rc_version,
            "tokens":     tokens,
            "agent":      agent,
            "new_schema": is_new_schema(tokens),
            "captured":   is_captured(tokens),
        })

    return records


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
# Per-RC roll-up
# ---------------------------------------------------------------------------


def build_rc_rollup(
    project: str,
    rc_version: str,
    records: list[dict[str, Any]],
    pricing: dict[str, Any],
    usage_rc_dir: pathlib.Path,
) -> pathlib.Path:
    """
    Build and write the per-RC roll-up JSON file.

    Writes to: usage_rc_dir/<rc_version>-tokens.json
    Returns the path written.

    Output schema:
      See module docstring "OUTPUT SCHEMA" section for the full field list.
      Per-category cost fields (input_cost_usd, cache_creation_cost_usd,
      cache_read_cost_usd, output_cost_usd) are present in both task entries
      and the totals block.

    Schema handling:
      New-schema files: cost_usd comes from total_cost_usd (authoritative).
        Per-category costs are not available from the new schema; they are
        reported as 0.0 individually while cost_usd reflects the true total.
      Legacy-schema files: cost_usd computed from pricing table using per-
        category token counts; per-category costs are populated normally.
    """
    rc_version = normalise_version(rc_version)

    matching = [
        r for r in records
        if normalise_version(r.get("rc_version") or "") == rc_version
    ]

    tasks_array: list[dict[str, Any]] = []
    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0
    total_input_cost = 0.0
    total_cache_create_cost = 0.0
    total_cache_read_cost = 0.0
    total_output_cost = 0.0
    total_invocations = 0
    total_cost_accumulator = 0.0
    total_uncaptured = 0

    for r in matching:
        tok = r["tokens"]
        invocations = int(tok.get("invocations", 1) or 1)
        new_schema = r["new_schema"]

        # Uncaptured records (captured:false) are counted but excluded from token/
        # cost totals — their zeroed counts would silently under-report the gap.
        if not r.get("captured", True):
            total_uncaptured += 1
            tasks_array.append({
                "task_id":                  r["task_id"],
                "agent":                    r["agent"],
                "model":                    "unknown",
                "input":                    0,
                "output":                   0,
                "cache_creation_tokens":    0,
                "cache_read_tokens":        0,
                "cost_usd":                 0.0,
                "input_cost_usd":           0.0,
                "cache_creation_cost_usd":  0.0,
                "cache_read_cost_usd":      0.0,
                "output_cost_usd":          0.0,
                "invocations":              invocations,
                "captured":                 False,
                "reason":                   tok.get("reason", "no usage emitted"),
            })
            continue

        if new_schema:
            # New schema: authoritative cost from total_cost_usd.
            # Token counts are not stored at the top level — report 0 for
            # individual categories (sum across model_usage if needed in future).
            file_cost = float(tok.get("total_cost_usd", 0.0))
            input_tok = 0
            output_tok = 0
            cache_create = 0
            cache_read = 0
            input_cost_usd = 0.0
            cache_creation_cost_usd = 0.0
            cache_read_cost_usd = 0.0
            output_cost_usd = 0.0
            # Derive a representative model name from model_usage keys
            model_usage_block: dict[str, Any] = tok.get("model_usage", {})
            model = next(iter(model_usage_block), "unknown") if model_usage_block else "unknown"
        else:
            # Legacy schema: compute cost from pricing table.
            input_tok = int(tok.get("input_tokens", 0) or 0)
            output_tok = int(tok.get("output_tokens", 0) or 0)
            cache_create = int(tok.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(tok.get("cache_read_input_tokens", 0) or 0)
            costs = compute_cost(pricing, tok)
            file_cost = costs.total_cost_usd
            input_cost_usd = costs.input_cost_usd
            cache_creation_cost_usd = costs.cache_creation_cost_usd
            cache_read_cost_usd = costs.cache_read_cost_usd
            output_cost_usd = costs.output_cost_usd
            model = str(tok.get("model", "unknown"))

            total_input += input_tok
            total_output += output_tok
            total_cache_create += cache_create
            total_cache_read += cache_read
            total_input_cost += input_cost_usd
            total_cache_create_cost += cache_creation_cost_usd
            total_cache_read_cost += cache_read_cost_usd
            total_output_cost += output_cost_usd

        total_cost_accumulator += file_cost
        total_invocations += invocations

        tasks_array.append({
            "task_id":                  r["task_id"],
            "agent":                    r["agent"],
            "model":                    model,
            "input":                    input_tok,
            "output":                   output_tok,
            "cache_creation_tokens":    cache_create,
            "cache_read_tokens":        cache_read,
            "cost_usd":                 round(file_cost, 6),
            "input_cost_usd":           0.0 if new_schema else round(input_cost_usd, 6),
            "cache_creation_cost_usd":  0.0 if new_schema else round(cache_creation_cost_usd, 6),
            "cache_read_cost_usd":      0.0 if new_schema else round(cache_read_cost_usd, 6),
            "output_cost_usd":          0.0 if new_schema else round(output_cost_usd, 6),
            "invocations":              invocations,
        })

    shipped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict[str, Any] = {
        "version":    rc_version,
        "shipped_at": shipped_at,
        "tasks":      tasks_array,
        "totals": {
            "input_tokens":              total_input,
            "output_tokens":             total_output,
            "cache_creation_tokens":     total_cache_create,
            "cache_read_tokens":         total_cache_read,
            "cost_usd":                  round(total_cost_accumulator, 6),
            "input_cost_usd":            round(total_input_cost, 6),
            "cache_creation_cost_usd":   round(total_cache_create_cost, 6),
            "cache_read_cost_usd":       round(total_cache_read_cost, 6),
            "output_cost_usd":           round(total_output_cost, 6),
            "invocations":               total_invocations,
            "uncaptured_tasks":          total_uncaptured,
        },
    }

    usage_rc_dir.mkdir(parents=True, exist_ok=True)
    out_path = usage_rc_dir / f"{rc_version}-tokens.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[aggregate_tokens] RC roll-up written: {out_path}", file=sys.stderr)
    return out_path


# ---------------------------------------------------------------------------
# Per-day roll-up
# ---------------------------------------------------------------------------


def _zero_agent_entry() -> dict[str, Any]:
    """Return an empty per-agent/per-model accumulator dict."""
    return {
        "input":                    0,
        "output":                   0,
        "cache_creation_tokens":    0,
        "cache_read_tokens":        0,
        "cost_usd":                 0.0,
        "input_cost_usd":           0.0,
        "cache_creation_cost_usd":  0.0,
        "cache_read_cost_usd":      0.0,
        "output_cost_usd":          0.0,
        "invocations":              0,
    }


def build_day_rollup(
    project: str,
    day: str,
    records: list[dict[str, Any]],
    pricing: dict[str, Any],
    usage_daily_dir: pathlib.Path,
) -> pathlib.Path:
    """
    Build and write the per-day roll-up JSON file.

    A task is included if its tokens.json 'timestamp' field (UTC ISO-8601)
    falls within the named UTC day.

    Writes to: usage_daily_dir/<day>.json
    Returns the path written.

    Output schema:
      See module docstring "OUTPUT SCHEMA" section for the full field list.
      Per-category cost fields are present in totals, by_agent entries, and
      by_model entries.  Cache token counts are tracked in by_agent and by_model.

    Schema handling:
      New-schema files: total cost comes from total_cost_usd (authoritative).
        Per-model cost comes from model_usage[<model>].cost_usd.
        Per-agent cost = sum of total_cost_usd for that agent.
      Legacy-schema files: cost computed from pricing table; a single
        aggregated fallback warning is emitted once per run by main().
    """
    try:
        day_obj = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        print(f"ERROR: invalid day format '{day}' (expected YYYY-MM-DD)", file=sys.stderr)
        sys.exit(1)

    def ts_in_day(ts_str: str | None) -> bool:
        if not ts_str:
            return False
        try:
            # Accept trailing Z or +HH:MM offset
            clean = ts_str.rstrip("Z")
            if "+" in clean:
                clean = clean.split("+")[0]
            dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
            return dt.date() == day_obj
        except ValueError:
            return False

    matching = [r for r in records if ts_in_day(r["tokens"].get("timestamp"))]

    total_input          = 0
    total_output         = 0
    total_cache_create   = 0
    total_cache_read     = 0
    total_input_cost     = 0.0
    total_cache_create_cost = 0.0
    total_cache_read_cost   = 0.0
    total_output_cost    = 0.0
    total_invocations    = 0
    total_cost_accumulator = 0.0
    total_uncaptured     = 0

    # by_agent: { agent_name: accumulator_dict }
    by_agent: dict[str, dict[str, Any]] = {}

    # by_model: { model_name: accumulator_dict }
    by_model: dict[str, dict[str, Any]] = {}

    # Collect RC versions seen on this day
    rcs_seen: set[str] = set()

    for r in matching:
        tok         = r["tokens"]
        invocations = int(tok.get("invocations", 1) or 1)
        agent       = r["agent"]
        new_schema  = r["new_schema"]

        rc_ver = r.get("rc_version")
        if rc_ver:
            rcs_seen.add(normalise_version(rc_ver))

        # Uncaptured records (captured:false) are counted but excluded from token/
        # cost totals — their zeroed counts would silently under-report the gap.
        if not r.get("captured", True):
            total_uncaptured += 1
            continue

        if new_schema:
            # ---- NEW SCHEMA ----
            # Authoritative cost from total_cost_usd; per-model from model_usage.
            file_cost = float(tok.get("total_cost_usd", 0.0))
            mu_block: dict[str, Any] = tok.get("model_usage", {})

            # Token counts not available at top level; contribute 0 to totals.
            total_cost_accumulator += file_cost
            total_invocations += invocations

            # -- Accumulate by_agent --
            if agent not in by_agent:
                by_agent[agent] = _zero_agent_entry()
            by_agent[agent]["cost_usd"] = round(
                by_agent[agent]["cost_usd"] + file_cost, 6
            )
            by_agent[agent]["invocations"] += invocations

            # -- Accumulate by_model (one entry per model in model_usage) --
            for model_name, mu in mu_block.items():
                if not isinstance(mu, dict):
                    continue
                model_cost = float(mu.get("cost_usd", 0.0))
                mu_input = int(mu.get("input_tokens", 0) or 0)
                mu_output = int(mu.get("output_tokens", 0) or 0)
                mu_cache_create = int(mu.get("cache_creation_input_tokens", 0) or 0)
                mu_cache_read = int(mu.get("cache_read_input_tokens", 0) or 0)

                if model_name not in by_model:
                    by_model[model_name] = _zero_agent_entry()
                by_model[model_name]["input"]                 += mu_input
                by_model[model_name]["output"]                += mu_output
                by_model[model_name]["cache_creation_tokens"] += mu_cache_create
                by_model[model_name]["cache_read_tokens"]     += mu_cache_read
                by_model[model_name]["cost_usd"] = round(
                    by_model[model_name]["cost_usd"] + model_cost, 6
                )
                by_model[model_name]["invocations"] += invocations

        else:
            # ---- LEGACY SCHEMA ----
            input_tok    = int(tok.get("input_tokens", 0) or 0)
            output_tok   = int(tok.get("output_tokens", 0) or 0)
            cache_create = int(tok.get("cache_creation_input_tokens", 0) or 0)
            cache_read   = int(tok.get("cache_read_input_tokens", 0) or 0)
            costs        = compute_cost(pricing, tok)
            model        = str(tok.get("model", "unknown"))

            total_input          += input_tok
            total_output         += output_tok
            total_cache_create   += cache_create
            total_cache_read     += cache_read
            total_input_cost     += costs.input_cost_usd
            total_cache_create_cost += costs.cache_creation_cost_usd
            total_cache_read_cost   += costs.cache_read_cost_usd
            total_output_cost    += costs.output_cost_usd
            total_cost_accumulator += costs.total_cost_usd
            total_invocations    += invocations

            # -- Accumulate by_agent --
            if agent not in by_agent:
                by_agent[agent] = _zero_agent_entry()
            by_agent[agent]["input"]                   += input_tok
            by_agent[agent]["output"]                  += output_tok
            by_agent[agent]["cache_creation_tokens"]   += cache_create
            by_agent[agent]["cache_read_tokens"]       += cache_read
            by_agent[agent]["cost_usd"]                 = round(
                by_agent[agent]["cost_usd"] + costs.total_cost_usd, 6
            )
            by_agent[agent]["input_cost_usd"]           = round(
                by_agent[agent]["input_cost_usd"] + costs.input_cost_usd, 6
            )
            by_agent[agent]["cache_creation_cost_usd"]  = round(
                by_agent[agent]["cache_creation_cost_usd"] + costs.cache_creation_cost_usd, 6
            )
            by_agent[agent]["cache_read_cost_usd"]      = round(
                by_agent[agent]["cache_read_cost_usd"] + costs.cache_read_cost_usd, 6
            )
            by_agent[agent]["output_cost_usd"]          = round(
                by_agent[agent]["output_cost_usd"] + costs.output_cost_usd, 6
            )
            by_agent[agent]["invocations"]             += invocations

            # -- Accumulate by_model --
            if model not in by_model:
                by_model[model] = _zero_agent_entry()
            by_model[model]["input"]                   += input_tok
            by_model[model]["output"]                  += output_tok
            by_model[model]["cache_creation_tokens"]   += cache_create
            by_model[model]["cache_read_tokens"]       += cache_read
            by_model[model]["cost_usd"]                 = round(
                by_model[model]["cost_usd"] + costs.total_cost_usd, 6
            )
            by_model[model]["input_cost_usd"]           = round(
                by_model[model]["input_cost_usd"] + costs.input_cost_usd, 6
            )
            by_model[model]["cache_creation_cost_usd"]  = round(
                by_model[model]["cache_creation_cost_usd"] + costs.cache_creation_cost_usd, 6
            )
            by_model[model]["cache_read_cost_usd"]      = round(
                by_model[model]["cache_read_cost_usd"] + costs.cache_read_cost_usd, 6
            )
            by_model[model]["output_cost_usd"]          = round(
                by_model[model]["output_cost_usd"] + costs.output_cost_usd, 6
            )
            by_model[model]["invocations"]             += invocations

    payload: dict[str, Any] = {
        "date":         day,
        "project":      project,
        "rcs_shipped":  sorted(rcs_seen),
        "totals": {
            "input_tokens":              total_input,
            "output_tokens":             total_output,
            "cache_creation_tokens":     total_cache_create,
            "cache_read_tokens":         total_cache_read,
            "cost_usd":                  round(total_cost_accumulator, 6),
            "input_cost_usd":            round(total_input_cost, 6),
            "cache_creation_cost_usd":   round(total_cache_create_cost, 6),
            "cache_read_cost_usd":       round(total_cache_read_cost, 6),
            "output_cost_usd":           round(total_output_cost, 6),
            "invocations":               total_invocations,
            "uncaptured_tasks":          total_uncaptured,
        },
        "by_agent": by_agent,
        "by_model": by_model,
    }

    usage_daily_dir.mkdir(parents=True, exist_ok=True)
    out_path = usage_daily_dir / f"{day}.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[aggregate_tokens] Daily roll-up written: {out_path}", file=sys.stderr)
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
        ts_str = r["tokens"].get("timestamp")
        if ts_str:
            try:
                clean = ts_str.rstrip("Z")
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
            "Aggregate per-task token usage into per-RC and per-day roll-ups."
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
        help="Produce a per-RC roll-up for the named version (e.g. v0.23.22).",
    )
    parser.add_argument(
        "--day",
        metavar="YYYY-MM-DD",
        help="Produce a per-day roll-up for the named UTC day.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_rollups",
        help="Rebuild ALL per-RC and per-day roll-ups for the project.",
    )
    parser.add_argument(
        "--kanban-root",
        metavar="PATH",
        help="Override PGAI_AGENT_KANBAN_ROOT_PATH.",
    )

    args = parser.parse_args()

    if not args.rc and not args.day and not args.all_rollups:
        parser.error("at least one of --rc, --day, or --all is required")

    kanban_root = resolve_kanban_root(args.kanban_root)
    project = args.project

    project_dir = kanban_root / "projects" / project
    if not project_dir.is_dir():
        print(f"ERROR: project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    tasks_dir = project_dir / "tasks"
    usage_rc_dir = project_dir / "usage" / "rc"
    usage_daily_dir = project_dir / "usage" / "daily"

    # Load pricing (graceful: returns {} if absent or malformed)
    pricing = load_pricing(kanban_root, project)

    # Scan all tasks for token data
    records = scan_tasks(tasks_dir)
    print(
        f"[aggregate_tokens] Found {len(records)} task(s) with tokens.json under {tasks_dir}",
        file=sys.stderr,
    )

    # Count legacy-schema files once (schema tag set by scan_tasks); emit one
    # aggregated fallback warning per run rather than per file or per rollup.
    legacy_count = sum(1 for r in records if not r["new_schema"])

    if args.all_rollups:
        versions = all_rc_versions(records)
        days = all_days(records)
        print(
            f"[aggregate_tokens] --all: building {len(versions)} RC roll-up(s)"
            f" and {len(days)} daily roll-up(s).",
            file=sys.stderr,
        )
        for ver in versions:
            build_rc_rollup(project, ver, records, pricing, usage_rc_dir)
        for day in days:
            build_day_rollup(project, day, records, pricing, usage_daily_dir)
    else:
        if args.rc:
            build_rc_rollup(project, args.rc, records, pricing, usage_rc_dir)
        if args.day:
            build_day_rollup(project, args.day, records, pricing, usage_daily_dir)

    # Emit the aggregated legacy-fallback warning once, after all rollups complete.
    _emit_legacy_fallback_warning(legacy_count)

    sys.exit(0)


if __name__ == "__main__":
    main()
