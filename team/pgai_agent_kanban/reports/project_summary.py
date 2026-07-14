#!/usr/bin/env python3
"""
project_summary.py — Comprehensive historical audit report for a named project.

Reads bugs, priorities, requirements, release-notes, and task status files
for a project registered under projects/<name>/ and produces a structured
report covering:

  - Project overview (version, release history)
  - Requirements (shipped / in-flight / queued + last-5 one-liners)
  - Bugs (counts by status/severity, oldest open, most recent, per-item one-liners)
  - Priorities (counts + open items one-liner per line)
  - Bundles (requirements acting as release bundles)
  - Recent activity (last 10 chronological events)

Two summary modes:
  offline (default)  — one-liners extracted from file metadata; fast, no LLM cost.
  llm (--llm flag)   — sends each item to the Anthropic API for polished prose.

Usage:
    python3 team/pgai_agent_kanban/reports/project_summary.py [options]

Options:
    --project <name>      Project name (required when multiple projects exist;
                          auto-detected when only one project is registered).
    --project all         Render one section per project plus aggregate totals.
    --days N              Limit "recent" sections to last N days (default: no limit).
    --brief               Counts only — no per-item summaries.
    --all                 Show all items (override default truncation).
    --llm                 Use LLM mode for summaries (requires ANTHROPIC_API_KEY).
    --format text|md|json Output format (default: text).
    --output FILE         Write output to FILE instead of stdout.
    -h, --help            Show this help and exit.

Environment:
    PGAI_AGENT_KANBAN_ROOT_PATH         Kanban root (canonical var, default: ~/pgai_agent_kanban).
    ANTHROPIC_API_KEY                   Required when --llm is used.

Exit codes:
    0  Success.
    1  Usage error or unrecoverable configuration failure.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BRIEF_ITEMS = 5  # per section, when --all is not set
_MAX_ONELINER_LEN = 80

_STATUS_OPEN = "open"
_STATUS_DONE = "done"
_STATUS_WONT_FIX = "wont-fix"
_STATUS_RUNNING = "running"
_STATUS_BLOCKED = "blocked"
_STATUS_WORKING = "working"
_STATUS_BACKLOG = "backlog"
_STATUS_WAITING = "waiting"
_STATUS_WONT_DO = "wont-do"

_OPEN_BUG_STATUSES = {_STATUS_OPEN, _STATUS_BLOCKED}
_CLOSED_BUG_STATUSES = {_STATUS_DONE}
_WONT_FIX_STATUSES = {_STATUS_WONT_FIX}
_WONT_DO_STATUSES = {_STATUS_WONT_DO}

_OPEN_PRIORITY_STATUSES = {_STATUS_OPEN, _STATUS_RUNNING, _STATUS_BLOCKED}
_DONE_PRIORITY_STATUSES = {_STATUS_DONE}

_DONE_TASK_STATUSES = {_STATUS_DONE}
_INFLIGHT_TASK_STATUSES = {_STATUS_WORKING, _STATUS_BACKLOG, _STATUS_WAITING, _STATUS_BLOCKED}

_SEVERITY_ORDER = ["critical", "high", "medium", "low", "unknown"]
_KNOWN_SEVERITIES = {"critical", "high", "medium", "low"}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _kanban_root() -> pathlib.Path:
    """Return the kanban root path via the canonical resolver.

    Delegates to pgai_agent_kanban.env.resolve_kanban_root() which reads
    PGAI_AGENT_KANBAN_ROOT_PATH and fails loud if the variable is unset or empty.
    """
    from pgai_agent_kanban.env import resolve_kanban_root
    return resolve_kanban_root()


def _projects_dir(root: pathlib.Path) -> pathlib.Path:
    """Return the projects/ directory under the kanban root."""
    return root / "projects"


def _list_projects(root: pathlib.Path) -> list[str]:
    """Return a sorted list of project names registered under projects/."""
    pdir = _projects_dir(root)
    if not pdir.is_dir():
        return []
    names = [
        d.name
        for d in sorted(pdir.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    ]
    return names


def _project_root(kanban_root: pathlib.Path, name: str) -> pathlib.Path:
    """Return the root directory for a named project."""
    return _projects_dir(kanban_root) / name


def _read_ini_value(cfg_path: pathlib.Path, section: str, key: str) -> str:
    """Read a single key from a simple INI-format config file.

    Does not require configparser — uses a plain regex scan so the function
    works without any third-party dependencies.

    Returns the value stripped of leading/trailing whitespace, or '' when the
    key is not found.
    """
    text = _safe_read(cfg_path)
    if not text:
        return ""
    in_section = False
    section_re = re.compile(r"^\[" + re.escape(section) + r"\]", re.IGNORECASE)
    any_section_re = re.compile(r"^\[")
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.+)", re.IGNORECASE)
    for line in text.splitlines():
        if section_re.match(line):
            in_section = True
            continue
        if in_section:
            if any_section_re.match(line):
                break  # entered next section
            m = key_re.match(line)
            if m:
                return m.group(1).strip()
    return ""


def _dev_tree_path(project_root: pathlib.Path) -> pathlib.Path | None:
    """Return the dev_tree_path from project.cfg when available.

    Checks for both project.cfg (INI) and PROJECT.cfg (legacy bash key=value).
    Returns None when neither file declares a dev_tree_path.
    """
    for cfg_name in ("project.cfg", "PROJECT.cfg"):
        cfg = project_root / cfg_name
        if cfg.is_file():
            val = _read_ini_value(cfg, "project", "dev_tree_path")
            if val:
                return pathlib.Path(val)
    return None


# ---------------------------------------------------------------------------
# Markdown field parsers
# ---------------------------------------------------------------------------


def _extract_field(text: str, field: str) -> str:
    """Extract the value of a markdown header field.

    Looks for a pattern like:
        ## FieldName
        <value on next non-blank line(s)>

    Returns the stripped first non-blank line after the header, or ''.
    """
    pattern = re.compile(
        r"^#{1,3}\s+" + re.escape(field) + r"\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    m = pattern.search(text)
    if m is None:
        return ""
    rest = text[m.end():]
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _normalize_severity(raw: str) -> str:
    """Normalise a raw severity string to one of the known severity keywords.

    Handles multi-word values like "medium (data completeness; ...)" or
    "**high** — rationale text" by extracting only the leading keyword.
    Falls back to 'unknown' when no known keyword is found.
    """
    if not raw:
        return "unknown"
    # Strip markdown bold markers
    cleaned = re.sub(r"\*+", "", raw).strip().lower()
    # Take first word-like token
    first_token = re.split(r"[\s\(\)\-—,;:]+", cleaned)[0]
    if first_token in _KNOWN_SEVERITIES:
        return first_token
    # Sometimes the first line is a rationale header rather than the value;
    # search for any known keyword in the first 80 characters.
    search_area = cleaned[:80]
    for sev in _KNOWN_SEVERITIES:
        if re.search(r"\b" + sev + r"\b", search_area):
            return sev
    return "unknown"


def _extract_bold_field(text: str, field: str) -> str:
    """Extract value from a bold key-value line.

    Handles two common markdown formatting variants:
      **Field:** value      (field name and colon inside bold markers)
      **Field**: value      (bold field name, colon outside)
      **Field** = value     (field name inside bold, equals separator)
    """
    # Variant 1: **Field:** value  (colon inside the ** markers)
    pattern1 = re.compile(
        r"\*\*" + re.escape(field) + r":\*\*\s*(.+)",
        re.IGNORECASE,
    )
    m = pattern1.search(text)
    if m:
        return m.group(1).strip()
    # Variant 2: **Field**: value  or  **Field** = value  (colon/equals outside)
    pattern2 = re.compile(
        r"\*\*" + re.escape(field) + r"\*\*\s*[:=]\s*(.+)",
        re.IGNORECASE,
    )
    m = pattern2.search(text)
    if m:
        return m.group(1).strip()
    return ""


def _slug_from_filename(path: pathlib.Path) -> str:
    """Return a human-readable slug from a filename (without extension)."""
    stem = path.stem
    # Replace hyphens and underscores with spaces; trim leading IDs
    stem = re.sub(r"^(BUG|PRIORITY|REQUIREMENTS?|REQ)-\d+[-_]?", "", stem, flags=re.IGNORECASE)
    slug = stem.replace("-", " ").replace("_", " ")
    return slug.strip()[:_MAX_ONELINER_LEN] or path.stem[:_MAX_ONELINER_LEN]


def _truncate(text: str, length: int = _MAX_ONELINER_LEN) -> str:
    """Truncate text to at most `length` characters, appending '...' when cut."""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[: length - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class BugItem:
    """Parsed representation of a bug file."""

    __slots__ = (
        "path",
        "bug_id",
        "date",
        "severity",
        "status",
        "symptom",
        "title",
        "oneliner",
    )

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.bug_id = path.stem
        text = _safe_read(path)

        self.status = _extract_field(text, "Status").lower() or "unknown"
        raw_sev = _extract_bold_field(text, "Severity") or _extract_field(text, "Severity")
        self.severity = _normalize_severity(raw_sev)

        date_str = _extract_bold_field(text, "Date")
        if not date_str:
            date_str = _extract_field(text, "Date")
        self.date = _parse_date_loose(date_str)

        self.symptom = _extract_field(text, "Symptom") or ""
        # Title from first H1 line
        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        self.title = h1.group(1).strip() if h1 else self.bug_id

        self.oneliner = ""  # filled by summarizer

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN_BUG_STATUSES

    @property
    def is_done(self) -> bool:
        return self.status in _CLOSED_BUG_STATUSES

    @property
    def is_wont_fix(self) -> bool:
        return self.status in _WONT_FIX_STATUSES

    @property
    def is_wont_do(self) -> bool:
        return self.status in _WONT_DO_STATUSES


class PriorityItem:
    """Parsed representation of a priority file."""

    __slots__ = ("path", "priority_id", "date", "severity", "status", "title", "oneliner")

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.priority_id = path.stem
        text = _safe_read(path)

        self.status = _extract_field(text, "Status").lower() or "unknown"
        self.severity = _normalize_severity(_extract_field(text, "Severity"))

        date_str = _extract_field(text, "Discovered") or _extract_bold_field(text, "Date")
        self.date = _parse_date_loose(date_str)

        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        self.title = h1.group(1).strip() if h1 else self.priority_id

        self.oneliner = ""

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN_PRIORITY_STATUSES

    @property
    def is_done(self) -> bool:
        return self.status in _DONE_PRIORITY_STATUSES


class RequirementItem:
    """Parsed representation of a requirements/bundle file."""

    __slots__ = ("path", "req_id", "status", "target_version", "workflow_type", "title", "oneliner")

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.req_id = path.stem
        text = _safe_read(path)

        self.status = _extract_field(text, "Status").lower() or "unknown"
        self.target_version = _extract_field(text, "Target Version") or ""
        self.workflow_type = _extract_field(text, "Workflow Type").lower() or ""

        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        self.title = h1.group(1).strip() if h1 else self.req_id

        self.oneliner = ""

    @property
    def is_shipped(self) -> bool:
        return self.status == _STATUS_DONE

    @property
    def is_inflight(self) -> bool:
        return self.status in {_STATUS_RUNNING, _STATUS_WORKING, _STATUS_BLOCKED}

    @property
    def is_queued(self) -> bool:
        return self.status in {_STATUS_OPEN, _STATUS_BACKLOG, _STATUS_WAITING}


class ReleaseNote:
    """Parsed representation of a release notes file."""

    __slots__ = ("path", "version", "release_date", "summary", "oneliner")

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        text = _safe_read(path)

        # Extract version from filename first, then from title
        stem = path.stem  # e.g. "v0.23.31"
        if re.match(r"^v\d+\.\d+", stem):
            self.version = stem
        else:
            h1 = re.search(r"^#\s+Release Notes.*?(v[\d.]+)", text, re.IGNORECASE | re.MULTILINE)
            self.version = h1.group(1) if h1 else stem

        date_str = _extract_bold_field(text, "Release Date")
        self.release_date = _parse_date_loose(date_str)

        self.summary = _extract_field(text, "Summary") or ""
        self.oneliner = ""

    def version_tuple(self) -> tuple[int, ...]:
        """Return version as a numeric tuple for sorting (e.g. (0, 23, 31))."""
        nums = re.findall(r"\d+", self.version)
        return tuple(int(n) for n in nums)


class TaskEvent:
    """A single recent activity event derived from a task status file."""

    __slots__ = ("path", "task_id", "state", "role", "date", "summary")

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        text = _safe_read(path)

        task_id = _extract_field(text, "Task") or path.parent.name
        self.task_id = task_id
        self.state = _extract_field(text, "State").upper() or "UNKNOWN"
        self.role = _extract_field(text, "Role").upper() or ""
        self.summary = _extract_field(text, "Summary") or ""

        # Derive a date from the task ID (format: ROLE-YYYYMMDD-NNN-...)
        self.date = _date_from_task_id(task_id)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _safe_read(path: pathlib.Path) -> str:
    """Read a file's text content, returning '' on any IO error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_date_loose(date_str: str) -> datetime | None:
    """Attempt to parse a date string in several common formats.

    Returns a timezone-aware datetime (UTC) or None on failure.
    """
    if not date_str:
        return None
    # Normalise 'Z' suffix
    s = date_str.strip().rstrip(".")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Try ISO 8601 first
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Try YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _date_from_task_id(task_id: str) -> datetime | None:
    """Extract a date from a task ID like ROLE-20260528-NNN-slug."""
    m = re.search(r"(\d{4})(\d{2})(\d{2})", task_id)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _sort_key_date(dt: datetime | None) -> datetime:
    """Return dt for sorting; use epoch for None so None sorts earliest."""
    return dt if dt is not None else datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_bugs(project_root: pathlib.Path) -> list[BugItem]:
    bugs_dir = project_root / "bugs"
    if not bugs_dir.is_dir():
        return []
    items = []
    for f in sorted(bugs_dir.glob("*.md")):
        try:
            items.append(BugItem(f))
        except Exception:  # noqa: BLE001
            pass
    return items


def _load_priorities(project_root: pathlib.Path) -> list[PriorityItem]:
    priority_dir = project_root / "priority"
    if not priority_dir.is_dir():
        return []
    items = []
    for f in sorted(priority_dir.glob("*.md")):
        # Skip template files
        if "template" in f.name.lower():
            continue
        try:
            items.append(PriorityItem(f))
        except Exception:  # noqa: BLE001
            pass
    return items


def _load_requirements(project_root: pathlib.Path) -> list[RequirementItem]:
    req_dir = project_root / "requirements"
    if not req_dir.is_dir():
        return []
    items = []
    for f in sorted(req_dir.glob("*.md")):
        if "template" in f.name.lower() or "readme" in f.name.lower():
            continue
        try:
            items.append(RequirementItem(f))
        except Exception:  # noqa: BLE001
            pass
    return items


def _load_release_notes(
    kanban_root: pathlib.Path, project_root: pathlib.Path
) -> list[ReleaseNote]:
    """Load release notes from project-scoped and kanban-level locations.

    Search order (highest specificity first):
    1. project_root/release-notes/
    2. dev_tree_path/release-notes/  (read from project.cfg dev_tree_path)
    3. kanban_root/release-notes/    (legacy location)
    """
    candidates: list[pathlib.Path] = []
    # Project-local release-notes/
    local = project_root / "release-notes"
    if local.is_dir():
        candidates.extend(sorted(local.glob("*.md")))
    # Dev tree release-notes/ — used by the kanban-self project
    dev_tree = _dev_tree_path(project_root)
    if dev_tree is not None:
        dev_rn = dev_tree / "release-notes"
        if dev_rn.is_dir():
            candidates.extend(sorted(dev_rn.glob("*.md")))
    # Kanban-level release-notes/ (many existing projects put them here)
    kanban_rn = kanban_root / "release-notes"
    if kanban_rn.is_dir():
        candidates.extend(sorted(kanban_rn.glob("*.md")))

    seen: set[str] = set()
    items: list[ReleaseNote] = []
    for f in candidates:
        if f.name in seen:
            continue
        seen.add(f.name)
        if "template" in f.name.lower():
            continue
        try:
            items.append(ReleaseNote(f))
        except Exception:  # noqa: BLE001
            pass

    # Sort by version tuple ascending
    items.sort(key=lambda r: r.version_tuple())
    return items


def _load_task_events(
    project_root: pathlib.Path, cutoff: datetime | None
) -> list[TaskEvent]:
    tasks_dir = project_root / "tasks"
    if not tasks_dir.is_dir():
        return []
    events: list[TaskEvent] = []
    for status_file in sorted(tasks_dir.glob("*/status.md")):
        try:
            ev = TaskEvent(status_file)
        except Exception:  # noqa: BLE001
            continue
        if cutoff is not None and ev.date is not None and ev.date < cutoff:
            continue
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Offline summarizer
# ---------------------------------------------------------------------------


def _offline_oneliner_bug(bug: BugItem) -> str:
    """Produce an 80-char offline one-liner for a bug."""
    # Prefer symptom field, then title, then filename slug
    for candidate in (bug.symptom, bug.title, _slug_from_filename(bug.path)):
        if candidate:
            return _truncate(candidate)
    return _truncate(bug.bug_id)


def _offline_oneliner_priority(p: PriorityItem) -> str:
    for candidate in (p.title, _slug_from_filename(p.path)):
        if candidate:
            return _truncate(candidate)
    return _truncate(p.priority_id)


def _offline_oneliner_requirement(r: RequirementItem) -> str:
    for candidate in (r.title, _slug_from_filename(r.path)):
        if candidate:
            return _truncate(candidate)
    return _truncate(r.req_id)


def _offline_oneliner_release(rn: ReleaseNote) -> str:
    for candidate in (rn.summary, rn.version):
        if candidate:
            return _truncate(candidate)
    return _truncate(rn.version)


def apply_offline_summaries(
    bugs: list[BugItem],
    priorities: list[PriorityItem],
    requirements: list[RequirementItem],
    releases: list[ReleaseNote],
) -> None:
    """Fill .oneliner on every item using offline extraction."""
    for b in bugs:
        b.oneliner = _offline_oneliner_bug(b)
    for p in priorities:
        p.oneliner = _offline_oneliner_priority(p)
    for r in requirements:
        r.oneliner = _offline_oneliner_requirement(r)
    for rn in releases:
        rn.oneliner = _offline_oneliner_release(rn)


# ---------------------------------------------------------------------------
# LLM summarizer
# ---------------------------------------------------------------------------


def _call_anthropic_api(content: str, api_key: str) -> str:
    """Send content to the Anthropic Messages API and return a one-line summary.

    Uses the standard messages endpoint with a small max_tokens budget.
    Raises RuntimeError on any failure.
    """
    import json as _json
    import urllib.error
    import urllib.request

    prompt = (
        "Summarize the following kanban artifact in exactly one sentence (max 80 characters). "
        "Output only the sentence — no preamble, no trailing punctuation beyond a period.\n\n"
        + content[:4000]
    )
    payload = {
        "model": "claude-haiku-4-5",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API HTTP {e.code}: {e.read().decode()[:200]}") from e
    except Exception as e:
        raise RuntimeError(f"Anthropic API error: {e}") from e

    try:
        text = body["content"][0]["text"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected API response shape: {body!r}") from e

    return _truncate(text)


def _call_claude_cli(content: str) -> str:
    """Use the local `claude` CLI to produce a one-line summary.

    Invoked when ANTHROPIC_API_KEY is absent but the Claude CLI is available
    (e.g. the installation uses OAuth credentials written by `claude login`).
    Raises RuntimeError on any failure.
    """
    import subprocess

    prompt = (
        "Summarize the following kanban artifact in exactly one sentence (max 80 characters). "
        "Output only the sentence — no preamble, no trailing punctuation beyond a period.\n\n"
        + content[:4000]
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", "claude-haiku-4-5", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as e:
        raise RuntimeError("claude CLI not found in PATH") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("claude CLI timed out") from e
    except Exception as e:
        raise RuntimeError(f"claude CLI error: {e}") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr[:200]}"
        )
    return _truncate(result.stdout.strip().splitlines()[0] if result.stdout.strip() else "")


def _llm_summarize(content: str, api_key: str) -> str:
    """Produce a one-line summary via LLM, using API key or CLI as available.

    Priority order:
    1. Anthropic Messages API (when api_key is non-empty).
    2. Local `claude` CLI (when api_key is empty but CLI is available).

    Raises RuntimeError when neither path succeeds.
    """
    if api_key:
        return _call_anthropic_api(content, api_key)
    return _call_claude_cli(content)


def apply_llm_summaries(
    bugs: list[BugItem],
    priorities: list[PriorityItem],
    requirements: list[RequirementItem],
    releases: list[ReleaseNote],
    api_key: str,
) -> None:
    """Fill .oneliner on every item using LLM API calls.

    Uses the Anthropic Messages API when api_key is set, falling back to the
    local `claude` CLI (OAuth flow) when only that is available.

    Falls back to offline summary on per-item errors so one bad call does not
    abort the entire report.
    """
    # Apply offline first as fallback base
    apply_offline_summaries(bugs, priorities, requirements, releases)

    for b in bugs:
        text = _safe_read(b.path)
        if text:
            try:
                b.oneliner = _llm_summarize(text, api_key)
            except RuntimeError as e:
                b.oneliner = _truncate(f"[LLM error] {b.oneliner}")
                sys.stderr.write(f"WARNING: LLM summary for {b.bug_id}: {e}\n")

    for p in priorities:
        text = _safe_read(p.path)
        if text:
            try:
                p.oneliner = _llm_summarize(text, api_key)
            except RuntimeError as e:
                p.oneliner = _truncate(f"[LLM error] {p.oneliner}")
                sys.stderr.write(f"WARNING: LLM summary for {p.priority_id}: {e}\n")

    for r in requirements:
        text = _safe_read(r.path)
        if text:
            try:
                r.oneliner = _llm_summarize(text, api_key)
            except RuntimeError as e:
                r.oneliner = _truncate(f"[LLM error] {r.oneliner}")
                sys.stderr.write(f"WARNING: LLM summary for {r.req_id}: {e}\n")

    for rn in releases:
        text = _safe_read(rn.path)
        if text:
            try:
                rn.oneliner = _llm_summarize(text, api_key)
            except RuntimeError as e:
                rn.oneliner = _truncate(f"[LLM error] {rn.oneliner}")
                sys.stderr.write(f"WARNING: LLM summary for {rn.version}: {e}\n")


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


class ProjectReport:
    """Aggregated data for one project's report."""

    def __init__(
        self,
        name: str,
        bugs: list[BugItem],
        priorities: list[PriorityItem],
        requirements: list[RequirementItem],
        releases: list[ReleaseNote],
        task_events: list[TaskEvent],
    ) -> None:
        self.name = name
        self.bugs = bugs
        self.priorities = priorities
        self.requirements = requirements
        self.releases = releases
        self.task_events = task_events

    # Derived views

    def open_bugs(self) -> list[BugItem]:
        return [b for b in self.bugs if b.is_open]

    def done_bugs(self) -> list[BugItem]:
        return [b for b in self.bugs if b.is_done]

    def wont_fix_bugs(self) -> list[BugItem]:
        return [b for b in self.bugs if b.is_wont_fix or b.is_wont_do]

    def open_priorities(self) -> list[PriorityItem]:
        return [p for p in self.priorities if p.is_open]

    def done_priorities(self) -> list[PriorityItem]:
        return [p for p in self.priorities if p.is_done]

    def shipped_requirements(self) -> list[RequirementItem]:
        return [r for r in self.requirements if r.is_shipped]

    def inflight_requirements(self) -> list[RequirementItem]:
        return [r for r in self.requirements if r.is_inflight]

    def queued_requirements(self) -> list[RequirementItem]:
        return [r for r in self.requirements if r.is_queued]

    def current_version(self) -> str:
        if not self.releases:
            return "none"
        return self.releases[-1].version

    def last_release_date(self) -> datetime | None:
        if not self.releases:
            return None
        return self.releases[-1].release_date

    def first_release_date(self) -> datetime | None:
        if not self.releases:
            return None
        return self.releases[0].release_date

    def time_since_last_release(self) -> str:
        dt = self.last_release_date()
        if dt is None:
            return "unknown"
        now = datetime.now(tz=timezone.utc)
        delta = now - dt
        if delta.days == 0:
            hours = delta.seconds // 3600
            return f"{hours}h ago"
        return f"{delta.days}d ago"

    def bugs_by_severity(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for b in self.bugs:
            s = b.severity if b.severity else "unknown"
            counts[s] = counts.get(s, 0) + 1
        return counts

    def oldest_open_bug(self) -> BugItem | None:
        open_bugs = self.open_bugs()
        if not open_bugs:
            return None
        dated = [b for b in open_bugs if b.date is not None]
        undated = [b for b in open_bugs if b.date is None]
        if dated:
            dated.sort(key=lambda b: _sort_key_date(b.date))
            return dated[0]
        return undated[0] if undated else None

    def most_recent_bug(self) -> BugItem | None:
        if not self.bugs:
            return None
        dated = [b for b in self.bugs if b.date is not None]
        if dated:
            dated.sort(key=lambda b: _sort_key_date(b.date), reverse=True)
            return dated[0]
        return self.bugs[-1]

    def recent_events(self, n: int = 10) -> list[TaskEvent]:
        events = sorted(
            self.task_events,
            key=lambda e: _sort_key_date(e.date),
            reverse=True,
        )
        return events[:n]


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------


def _fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d")


def _hr(char: str = "-", width: int = 70) -> str:
    return char * width


def _section_header(title: str, fmt: str) -> str:
    if fmt == "md":
        return f"\n## {title}\n"
    if fmt == "json":
        return ""  # JSON sections are built differently
    return f"\n{_hr()}\n{title}\n{_hr()}\n"


def _bullet(text: str, fmt: str, indent: int = 0) -> str:
    prefix = "  " * indent
    if fmt == "md":
        return f"{prefix}- {text}"
    return f"{prefix}  * {text}"


def render_project_report(
    report: ProjectReport,
    *,
    brief: bool = False,
    show_all: bool = False,
    fmt: str = "text",
    max_items: int = _DEFAULT_BRIEF_ITEMS,
) -> str:
    """Render a ProjectReport to a string in the requested format.

    Args:
        report:    The ProjectReport to render.
        brief:     If True, show counts only (no per-item summaries).
        show_all:  If True, override max_items truncation.
        fmt:       Output format: 'text', 'md', or 'json'.
        max_items: Maximum per-item lines per section (overridden by show_all).
    """
    if fmt == "json":
        return _render_json(report, brief=brief, show_all=show_all)

    limit = None if show_all else max_items
    lines: list[str] = []

    # ------------------------------------------------------------------
    # Project overview
    # ------------------------------------------------------------------
    lines.append(_section_header(f"Project: {report.name}", fmt))

    overview = [
        ("Current version", report.current_version()),
        ("Total releases", str(len(report.releases))),
        ("First release", _fmt_date(report.first_release_date())),
        ("Last shipped", _fmt_date(report.last_release_date())),
        ("Time since last release", report.time_since_last_release()),
    ]
    for label, value in overview:
        if fmt == "md":
            lines.append(f"**{label}:** {value}")
        else:
            lines.append(f"  {label:<28} {value}")

    # ------------------------------------------------------------------
    # Requirements
    # ------------------------------------------------------------------
    lines.append(_section_header("Requirements", fmt))
    shipped = report.shipped_requirements()
    inflight = report.inflight_requirements()
    queued = report.queued_requirements()

    counts_line = (
        f"shipped={len(shipped)}  in-flight={len(inflight)}  queued={len(queued)}"
    )
    lines.append(f"  {counts_line}" if fmt == "text" else counts_line)

    if not brief and shipped:
        recent_shipped = shipped[-5:]  # last 5
        header = "Last 5 shipped:" if len(shipped) > 5 else "Shipped:"
        lines.append(f"\n  {header}" if fmt == "text" else f"\n**{header}**")
        for r in recent_shipped:
            lines.append(_bullet(f"{r.req_id}: {r.oneliner}", fmt, indent=1))

    if not brief and inflight:
        lines.append(f"\n  In-flight:" if fmt == "text" else "\n**In-flight:**")
        items_to_show = inflight if limit is None else inflight[:limit]
        for r in items_to_show:
            lines.append(_bullet(f"{r.req_id}: {r.oneliner}", fmt, indent=1))

    # ------------------------------------------------------------------
    # Bugs
    # ------------------------------------------------------------------
    lines.append(_section_header("Bugs", fmt))
    open_bugs = report.open_bugs()
    done_bugs = report.done_bugs()
    wont_fix = report.wont_fix_bugs()
    sev_counts = report.bugs_by_severity()

    total_bugs = len(report.bugs)
    bug_counts_line = (
        f"total={total_bugs}  open={len(open_bugs)}  "
        f"resolved={len(done_bugs)}  wont-fix={len(wont_fix)}"
    )
    lines.append(f"  {bug_counts_line}" if fmt == "text" else bug_counts_line)

    # Severity breakdown
    sev_parts = []
    for sev in _SEVERITY_ORDER:
        if sev in sev_counts:
            sev_parts.append(f"{sev}={sev_counts[sev]}")
    if sev_parts:
        sev_line = "by severity: " + "  ".join(sev_parts)
        lines.append(f"  {sev_line}" if fmt == "text" else sev_line)

    oldest = report.oldest_open_bug()
    if oldest:
        oldest_line = f"oldest open: {oldest.bug_id} ({_fmt_date(oldest.date)})"
        lines.append(f"  {oldest_line}" if fmt == "text" else oldest_line)

    most_recent = report.most_recent_bug()
    if most_recent:
        recent_line = f"most recent: {most_recent.bug_id} ({_fmt_date(most_recent.date)})"
        lines.append(f"  {recent_line}" if fmt == "text" else recent_line)

    if not brief and open_bugs:
        lines.append(f"\n  Open items:" if fmt == "text" else "\n**Open items:**")
        items_to_show = open_bugs if limit is None else open_bugs[:limit]
        for b in items_to_show:
            sev_tag = f"[{b.severity}] " if b.severity != "unknown" else ""
            lines.append(_bullet(f"{b.bug_id}: {sev_tag}{b.oneliner}", fmt, indent=1))
        if limit is not None and len(open_bugs) > limit:
            lines.append(
                _bullet(f"... and {len(open_bugs) - limit} more", fmt, indent=1)
            )

    # ------------------------------------------------------------------
    # Priorities
    # ------------------------------------------------------------------
    lines.append(_section_header("Priorities", fmt))
    open_prios = report.open_priorities()
    done_prios = report.done_priorities()

    prio_counts_line = (
        f"total={len(report.priorities)}  open={len(open_prios)}  resolved={len(done_prios)}"
    )
    lines.append(f"  {prio_counts_line}" if fmt == "text" else prio_counts_line)

    if not brief and open_prios:
        lines.append(f"\n  Open items:" if fmt == "text" else "\n**Open items:**")
        items_to_show = open_prios if limit is None else open_prios[:limit]
        for p in items_to_show:
            lines.append(_bullet(f"{p.priority_id}: {p.oneliner}", fmt, indent=1))
        if limit is not None and len(open_prios) > limit:
            lines.append(
                _bullet(f"... and {len(open_prios) - limit} more", fmt, indent=1)
            )

    # ------------------------------------------------------------------
    # Bundles (requirements acting as release bundles)
    # ------------------------------------------------------------------
    lines.append(_section_header("Bundles", fmt))
    bundles = report.requirements  # requirements == bundles in this system
    shipped_b = report.shipped_requirements()
    inflight_b = report.inflight_requirements()

    bundle_counts_line = (
        f"total={len(bundles)}  shipped={len(shipped_b)}  in-flight={len(inflight_b)}"
    )
    lines.append(f"  {bundle_counts_line}" if fmt == "text" else bundle_counts_line)

    if not brief and inflight_b:
        lines.append(f"\n  In-flight:" if fmt == "text" else "\n**In-flight:**")
        for b in inflight_b:
            lines.append(_bullet(f"{b.req_id}: {b.oneliner}", fmt, indent=1))

    if not brief and shipped_b:
        recent_bundles = shipped_b[-5:]
        header = "Last 5 shipped:" if len(shipped_b) > 5 else "Shipped:"
        lines.append(f"\n  {header}" if fmt == "text" else f"\n**{header}**")
        for b in recent_bundles:
            lines.append(_bullet(f"{b.req_id}: {b.oneliner}", fmt, indent=1))

    # ------------------------------------------------------------------
    # Recent activity
    # ------------------------------------------------------------------
    lines.append(_section_header("Recent Activity", fmt))
    events = report.recent_events(10)
    if events:
        for ev in events:
            date_s = _fmt_date(ev.date)
            summary_snippet = _truncate(ev.summary, 60) if ev.summary else "(no summary)"
            line_text = f"{date_s}  [{ev.state}]  {ev.task_id}: {summary_snippet}"
            lines.append(_bullet(line_text, fmt))
    else:
        lines.append("  (no recent task activity)" if fmt == "text" else "(no recent task activity)")

    return "\n".join(lines) + "\n"


def _render_json(
    report: ProjectReport,
    *,
    brief: bool = False,
    show_all: bool = False,
) -> str:
    """Render a ProjectReport as a JSON string."""
    open_bugs = report.open_bugs()
    done_bugs = report.done_bugs()
    wont_fix = report.wont_fix_bugs()

    def _bug_obj(b: BugItem) -> dict:
        return {
            "id": b.bug_id,
            "status": b.status,
            "severity": b.severity,
            "date": _fmt_date(b.date),
            "oneliner": b.oneliner,
        }

    def _prio_obj(p: PriorityItem) -> dict:
        return {
            "id": p.priority_id,
            "status": p.status,
            "severity": p.severity,
            "date": _fmt_date(p.date),
            "oneliner": p.oneliner,
        }

    def _req_obj(r: RequirementItem) -> dict:
        return {
            "id": r.req_id,
            "status": r.status,
            "target_version": r.target_version,
            "oneliner": r.oneliner,
        }

    def _release_obj(rn: ReleaseNote) -> dict:
        return {
            "version": rn.version,
            "release_date": _fmt_date(rn.release_date),
            "oneliner": rn.oneliner,
        }

    def _event_obj(ev: TaskEvent) -> dict:
        return {
            "task_id": ev.task_id,
            "state": ev.state,
            "role": ev.role,
            "date": _fmt_date(ev.date),
            "summary": ev.summary,
        }

    data: dict[str, Any] = {
        "project": report.name,
        "overview": {
            "current_version": report.current_version(),
            "total_releases": len(report.releases),
            "first_release": _fmt_date(report.first_release_date()),
            "last_shipped": _fmt_date(report.last_release_date()),
            "time_since_last_release": report.time_since_last_release(),
        },
        "bugs": {
            "total": len(report.bugs),
            "open": len(open_bugs),
            "resolved": len(done_bugs),
            "wont_fix": len(wont_fix),
            "by_severity": report.bugs_by_severity(),
        },
        "priorities": {
            "total": len(report.priorities),
            "open": len(report.open_priorities()),
            "resolved": len(report.done_priorities()),
        },
        "requirements": {
            "shipped": len(report.shipped_requirements()),
            "inflight": len(report.inflight_requirements()),
            "queued": len(report.queued_requirements()),
        },
    }

    if not brief:
        data["bugs"]["open_items"] = [_bug_obj(b) for b in open_bugs]
        data["bugs"]["oldest_open"] = (
            _bug_obj(report.oldest_open_bug()) if report.oldest_open_bug() else None
        )
        data["bugs"]["most_recent"] = (
            _bug_obj(report.most_recent_bug()) if report.most_recent_bug() else None
        )
        data["priorities"]["open_items"] = [_prio_obj(p) for p in report.open_priorities()]
        data["requirements"]["shipped_items"] = [
            _req_obj(r) for r in report.shipped_requirements()[-5:]
        ]
        data["requirements"]["inflight_items"] = [
            _req_obj(r) for r in report.inflight_requirements()
        ]
        data["releases"] = [_release_obj(rn) for rn in report.releases[-5:]]
        data["recent_activity"] = [_event_obj(ev) for ev in report.recent_events(10)]

    return json.dumps(data, indent=2, default=str) + "\n"


def render_aggregate(
    reports: list[ProjectReport],
    *,
    fmt: str = "text",
) -> str:
    """Render a short aggregate summary across all projects."""
    total_bugs = sum(len(r.bugs) for r in reports)
    total_open_bugs = sum(len(r.open_bugs()) for r in reports)
    total_priorities = sum(len(r.priorities) for r in reports)
    total_open_prios = sum(len(r.open_priorities()) for r in reports)
    total_releases = sum(len(r.releases) for r in reports)
    total_requirements = sum(len(r.requirements) for r in reports)

    if fmt == "json":
        data = {
            "aggregate": {
                "projects": len(reports),
                "total_bugs": total_bugs,
                "open_bugs": total_open_bugs,
                "total_priorities": total_priorities,
                "open_priorities": total_open_prios,
                "total_releases": total_releases,
                "total_requirements": total_requirements,
            }
        }
        return json.dumps(data, indent=2) + "\n"

    lines: list[str] = []
    lines.append(_section_header("Aggregate (all projects)", fmt))
    agg = [
        ("Projects", str(len(reports))),
        ("Total bugs", f"{total_bugs}  (open: {total_open_bugs})"),
        ("Total priorities", f"{total_priorities}  (open: {total_open_prios})"),
        ("Total releases", str(total_releases)),
        ("Total requirements", str(total_requirements)),
    ]
    for label, value in agg:
        if fmt == "md":
            lines.append(f"**{label}:** {value}")
        else:
            lines.append(f"  {label:<28} {value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _usage(err: str | None = None) -> None:
    if err:
        sys.stderr.write(f"ERROR: {err}\n\n")
    doc = __doc__ or ""
    sys.stdout.write(doc.lstrip("\n"))


def main() -> None:  # noqa: C901 — linear CLI parsing
    """CLI entry point."""
    kanban_root = _kanban_root()

    # Defaults
    project_arg: str | None = None
    days: int | None = None
    brief = False
    show_all = False
    use_llm = False
    fmt = "text"
    output_file: str | None = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-h", "--help"):
            _usage()
            sys.exit(0)
        elif arg == "--project":
            if i + 1 >= len(args):
                _usage("--project requires a value")
                sys.exit(1)
            project_arg = args[i + 1]
            i += 2
        elif arg == "--days":
            if i + 1 >= len(args):
                _usage("--days requires an integer argument")
                sys.exit(1)
            try:
                days = int(args[i + 1])
                if days < 1:
                    raise ValueError("days must be >= 1")
            except ValueError as exc:
                _usage(f"--days: {exc}")
                sys.exit(1)
            i += 2
        elif arg == "--brief":
            brief = True
            i += 1
        elif arg == "--all":
            show_all = True
            i += 1
        elif arg == "--llm":
            use_llm = True
            i += 1
        elif arg == "--format":
            if i + 1 >= len(args):
                _usage("--format requires text|md|json")
                sys.exit(1)
            fmt = args[i + 1].lower()
            if fmt not in ("text", "md", "json"):
                _usage(f"--format must be text, md, or json (got: {fmt!r})")
                sys.exit(1)
            i += 2
        elif arg == "--output":
            if i + 1 >= len(args):
                _usage("--output requires a file path")
                sys.exit(1)
            output_file = args[i + 1]
            i += 2
        elif arg.startswith("--"):
            _usage(f"unknown argument: {arg}")
            sys.exit(1)
        else:
            _usage(f"unexpected positional argument: {arg}")
            sys.exit(1)

    # Validate LLM mode credentials
    api_key = ""
    if use_llm:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            # Fall back to the local `claude` CLI (OAuth flow used by wake scripts)
            import shutil
            if shutil.which("claude") is None:
                sys.stderr.write(
                    "ERROR: --llm requires either ANTHROPIC_API_KEY or the `claude` CLI.\n"
                    "  Set ANTHROPIC_API_KEY, or install the Claude CLI with `claude login`.\n"
                )
                sys.exit(1)
            # api_key stays '' — _llm_summarize will use the CLI path

    # Project resolution
    all_projects = _list_projects(kanban_root)
    if not all_projects:
        sys.stderr.write(
            f"ERROR: no projects found under {kanban_root / 'projects'}.\n"
            "  Ensure PGAI_AGENT_KANBAN_ROOT_PATH is set correctly.\n"
        )
        sys.exit(1)

    run_all = project_arg == "all"
    if project_arg is None:
        if len(all_projects) > 1:
            sys.stderr.write(
                "ERROR: multiple projects are registered; --project is required.\n"
                f"  Available: {', '.join(all_projects)}\n"
            )
            sys.exit(1)
        # Single-project auto-detect
        project_arg = all_projects[0]

    if not run_all:
        if project_arg not in all_projects:
            sys.stderr.write(
                f"ERROR: project {project_arg!r} not found.\n"
                f"  Available: {', '.join(all_projects)}\n"
            )
            sys.exit(1)
        target_projects = [project_arg]
    else:
        target_projects = all_projects

    # Time cutoff
    cutoff: datetime | None = None
    if days is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)

    # Build reports
    reports: list[ProjectReport] = []
    for pname in target_projects:
        proot = _project_root(kanban_root, pname)
        bugs = _load_bugs(proot)
        priorities = _load_priorities(proot)
        requirements = _load_requirements(proot)
        releases = _load_release_notes(kanban_root, proot)
        events = _load_task_events(proot, cutoff)

        if use_llm:
            apply_llm_summaries(bugs, priorities, requirements, releases, api_key)
        else:
            apply_offline_summaries(bugs, priorities, requirements, releases)

        reports.append(
            ProjectReport(
                name=pname,
                bugs=bugs,
                priorities=priorities,
                requirements=requirements,
                releases=releases,
                task_events=events,
            )
        )

    # Render
    output_parts: list[str] = []
    for rep in reports:
        output_parts.append(
            render_project_report(
                rep,
                brief=brief,
                show_all=show_all,
                fmt=fmt,
            )
        )
    if run_all and len(reports) > 1:
        output_parts.append(render_aggregate(reports, fmt=fmt))

    full_output = "\n".join(output_parts)

    if output_file:
        try:
            pathlib.Path(output_file).write_text(full_output, encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"ERROR: cannot write to {output_file}: {e}\n")
            sys.exit(1)
    else:
        sys.stdout.write(full_output)


if __name__ == "__main__":
    main()
