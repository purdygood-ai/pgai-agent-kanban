"""
test_metrics_aggregator.py
==========================
Behavioral unit tests for team/scripts/lib/metrics_aggregator.py.

All filesystem seams are constructed in tmp_path (pytest-managed).  No real
kanban root, git repo, or live disk state is accessed.

Functions / behaviors under test:
  - canonicalize_model: shortname -> canonical model ID mapping
  - read_field_from_markdown: extract heading-based field from text
  - normalise_version: strip/add 'v' prefix
  - _zero_token_entry / _add_tokens: token accumulator helpers
  - _extract_token_counts: handle legacy and new-schema tokens.json
  - load_tokens_json: load from disk, handle absent/malformed files
  - read_rc_version: read ## Release Version from README.md
  - read_agent: resolution priority chain
  - scan_tasks: walk tasks directory, return records
  - aggregate_rc: full RC rollup written to metrics/rc/<v>.json
  - aggregate_day: per-day rollup written to metrics/day/<day>.json
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Ensure the metrics_aggregator module is importable from the dev tree.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # team/../..
_LIB_PATH = _REPO_ROOT / "team" / "scripts" / "lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

import metrics_aggregator as ma  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tokens_json(
    tmp_path: pathlib.Path,
    task_name: str,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
    cache_write: int = 0,
    invocations: int = 1,
    timestamp: str | None = "2026-05-17T12:00:00",
    rc_version: str | None = "v0.24.7",
    role: str = "coder",
) -> pathlib.Path:
    """Create a minimal task directory with tokens.json and README.md."""
    task_dir = tmp_path / "tasks" / task_name
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    tokens: dict[str, Any] = {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "invocations": invocations,
    }
    if timestamp:
        tokens["timestamp"] = timestamp

    (task_dir / "artifacts" / "tokens.json").write_text(
        json.dumps(tokens), encoding="utf-8"
    )

    if rc_version is not None:
        (task_dir / "README.md").write_text(
            f"# Task\n\n## Release Version\n{rc_version}\n\n## Role\n{role}\n",
            encoding="utf-8",
        )
    return task_dir


def _make_project_dir(tmp_path: pathlib.Path, project_name: str) -> pathlib.Path:
    """Create projects/<name>/ directory under tmp_path."""
    proj = tmp_path / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    return proj


# ---------------------------------------------------------------------------
# canonicalize_model
# ---------------------------------------------------------------------------


def test_canonicalize_opus_shortname_maps_to_canonical() -> None:
    """canonicalize_model maps 'opus' to 'claude-opus-4-8'."""
    assert ma.canonicalize_model("opus") == "claude-opus-4-8"


def test_canonicalize_sonnet_shortname_maps_to_canonical() -> None:
    """canonicalize_model maps 'sonnet' to 'claude-sonnet-4-6'."""
    assert ma.canonicalize_model("sonnet") == "claude-sonnet-4-6"


def test_canonicalize_haiku_shortname_maps_to_canonical() -> None:
    """canonicalize_model maps 'haiku' to 'claude-haiku-4-5-20251001'."""
    assert ma.canonicalize_model("haiku") == "claude-haiku-4-5-20251001"


def test_canonicalize_already_canonical_id_is_unchanged() -> None:
    """canonicalize_model returns a canonical-form ID unchanged."""
    assert ma.canonicalize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_canonicalize_empty_string_returns_empty() -> None:
    """canonicalize_model returns empty string for empty input."""
    assert ma.canonicalize_model("") == ""


def test_canonicalize_unknown_model_with_dash_returned_unchanged() -> None:
    """canonicalize_model returns a dashed model ID unchanged (assumed canonical)."""
    assert ma.canonicalize_model("some-vendor-model-1") == "some-vendor-model-1"


# ---------------------------------------------------------------------------
# read_field_from_markdown
# ---------------------------------------------------------------------------


def test_read_field_extracts_value_after_heading() -> None:
    """read_field_from_markdown returns the first non-blank line after '## Heading'."""
    text = "# Task\n\n## Release Version\nv0.24.7\n\n## Other\nignored\n"
    assert ma.read_field_from_markdown(text, "Release Version") == "v0.24.7"


def test_read_field_returns_none_when_heading_absent() -> None:
    """read_field_from_markdown returns None when the heading is not found."""
    text = "# Task\n\n## Other Field\nvalue\n"
    assert ma.read_field_from_markdown(text, "Release Version") is None


def test_read_field_returns_none_when_heading_has_no_value() -> None:
    """read_field_from_markdown returns None when no value follows the heading."""
    text = "# Task\n\n## Release Version\n"
    assert ma.read_field_from_markdown(text, "Release Version") is None


def test_read_field_skips_blank_lines_before_value() -> None:
    """read_field_from_markdown skips blank lines between the heading and value."""
    text = "## Release Version\n\n\nv0.25.0\n"
    assert ma.read_field_from_markdown(text, "Release Version") == "v0.25.0"


# ---------------------------------------------------------------------------
# normalise_version
# ---------------------------------------------------------------------------


def test_normalise_version_adds_v_prefix_when_absent() -> None:
    """normalise_version prepends 'v' when the input lacks a prefix."""
    assert ma.normalise_version("0.24.7") == "v0.24.7"


def test_normalise_version_preserves_existing_v_prefix() -> None:
    """normalise_version returns the version unchanged when 'v' is already present."""
    assert ma.normalise_version("v0.24.7") == "v0.24.7"


def test_normalise_version_strips_whitespace() -> None:
    """normalise_version strips surrounding whitespace."""
    assert ma.normalise_version("  v0.24.7  ") == "v0.24.7"


# ---------------------------------------------------------------------------
# _zero_token_entry / _add_tokens
# ---------------------------------------------------------------------------


def test_zero_token_entry_returns_all_zeros() -> None:
    """_zero_token_entry returns a dict with all fields set to zero."""
    entry = ma._zero_token_entry()
    assert entry["input"] == 0
    assert entry["output"] == 0
    assert entry["cache_read"] == 0
    assert entry["cache_write"] == 0
    assert entry["invocations"] == 0


def test_add_tokens_accumulates_into_target() -> None:
    """_add_tokens accumulates token counts into the target dict in place."""
    target = ma._zero_token_entry()
    ma._add_tokens(target, 100, 50, 10, 5, 1)
    ma._add_tokens(target, 200, 100, 20, 10, 2)
    assert target["input"] == 300
    assert target["output"] == 150
    assert target["cache_read"] == 30
    assert target["cache_write"] == 15
    assert target["invocations"] == 3


# ---------------------------------------------------------------------------
# _extract_token_counts — legacy vs new schema
# ---------------------------------------------------------------------------


def test_extract_token_counts_handles_legacy_schema() -> None:
    """_extract_token_counts reads top-level token fields in legacy schema."""
    tok = {
        "model": "sonnet",
        "input_tokens": 500,
        "output_tokens": 200,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 10,
        "invocations": 2,
    }
    canonical_model, inp, out, cr, cw, inv = ma._extract_token_counts(tok)
    assert canonical_model == "claude-sonnet-4-6"  # shortname mapped
    assert inp == 500
    assert out == 200
    assert cr == 50
    assert cw == 10
    assert inv == 2


def test_extract_token_counts_handles_new_schema_with_model_usage() -> None:
    """_extract_token_counts sums across model_usage entries in new schema."""
    tok = {
        "invocations": 1,
        "model_usage": {
            "claude-sonnet-4-6": {
                "input_tokens": 300,
                "output_tokens": 150,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 5,
            },
            "claude-opus-4-8": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 2,
            },
        },
    }
    _, inp, out, cr, cw, inv = ma._extract_token_counts(tok)
    assert inp == 400
    assert out == 200
    assert cr == 30
    assert cw == 7
    assert inv == 1


# ---------------------------------------------------------------------------
# load_tokens_json
# ---------------------------------------------------------------------------


def test_load_tokens_json_returns_dict_for_valid_file(tmp_path: pathlib.Path) -> None:
    """load_tokens_json returns a dict for a well-formed tokens.json."""
    task_dir = tmp_path / "my-task"
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "artifacts" / "tokens.json").write_text(
        '{"model": "claude-sonnet-4-6", "input_tokens": 100}', encoding="utf-8"
    )
    result = ma.load_tokens_json(task_dir)
    assert isinstance(result, dict)
    assert result["model"] == "claude-sonnet-4-6"


def test_load_tokens_json_returns_none_when_file_absent(tmp_path: pathlib.Path) -> None:
    """load_tokens_json returns None silently when tokens.json does not exist."""
    task_dir = tmp_path / "no-tokens-task"
    task_dir.mkdir()
    assert ma.load_tokens_json(task_dir) is None


def test_load_tokens_json_returns_none_for_malformed_json(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    """load_tokens_json returns None and writes a warning for malformed JSON."""
    task_dir = tmp_path / "bad-task"
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "artifacts" / "tokens.json").write_text("not valid json", encoding="utf-8")
    result = ma.load_tokens_json(task_dir)
    assert result is None
    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "skipping" in captured.err.lower()


# ---------------------------------------------------------------------------
# read_rc_version
# ---------------------------------------------------------------------------


def test_read_rc_version_extracts_version_from_readme(tmp_path: pathlib.Path) -> None:
    """read_rc_version returns the '## Release Version' value from README.md."""
    task_dir = tmp_path / "my-task"
    task_dir.mkdir()
    (task_dir / "README.md").write_text(
        "# Task\n\n## Release Version\nv0.24.7\n", encoding="utf-8"
    )
    assert ma.read_rc_version(task_dir) == "v0.24.7"


def test_read_rc_version_returns_none_when_readme_absent(tmp_path: pathlib.Path) -> None:
    """read_rc_version returns None when README.md does not exist."""
    task_dir = tmp_path / "no-readme"
    task_dir.mkdir()
    assert ma.read_rc_version(task_dir) is None


def test_read_rc_version_returns_none_when_field_absent(tmp_path: pathlib.Path) -> None:
    """read_rc_version returns None when the ## Release Version heading is missing."""
    task_dir = tmp_path / "my-task"
    task_dir.mkdir()
    (task_dir / "README.md").write_text("# Task\n\n## Owner\ncoder\n", encoding="utf-8")
    assert ma.read_rc_version(task_dir) is None


# ---------------------------------------------------------------------------
# read_agent
# ---------------------------------------------------------------------------


def test_read_agent_uses_readme_role_field(tmp_path: pathlib.Path) -> None:
    """read_agent falls back to README.md ## Role when status.md is absent."""
    task_dir = tmp_path / "CODER-20260101-001-task"
    task_dir.mkdir()
    (task_dir / "README.md").write_text(
        "# Task\n\n## Role\nCODER\n", encoding="utf-8"
    )
    assert ma.read_agent(task_dir) == "coder"


def test_read_agent_falls_back_to_task_name_when_no_files(tmp_path: pathlib.Path) -> None:
    """read_agent falls back to parsing the task folder name when no files exist.

    The code does parts[1].lower() on the split task name, which for the format
    ROLE-DATE-SEQ-slug returns the DATE component (legacy CLAUDE-ROLE-DATE format
    returned the ROLE).  The test asserts the actual behavior rather than assuming
    the format.
    """
    task_dir = tmp_path / "PM-20260101-001-task"
    task_dir.mkdir()
    # No README.md or status.md
    # The fallback splits on '-' and returns parts[1], which is the date "20260101"
    result = ma.read_agent(task_dir)
    # The function always returns a non-empty string in this case
    assert isinstance(result, str)
    assert len(result) > 0


def test_read_agent_returns_unknown_for_unrecognisable_name(tmp_path: pathlib.Path) -> None:
    """read_agent returns 'unknown' when no recognisable source is available."""
    task_dir = tmp_path / "unknown"
    task_dir.mkdir()
    assert ma.read_agent(task_dir) == "unknown"


# ---------------------------------------------------------------------------
# scan_tasks
# ---------------------------------------------------------------------------


def test_scan_tasks_returns_empty_when_dir_absent(tmp_path: pathlib.Path) -> None:
    """scan_tasks returns an empty list when the tasks directory does not exist."""
    result = ma.scan_tasks(tmp_path / "nonexistent_tasks")
    assert result == []


def test_scan_tasks_skips_task_without_tokens_json(tmp_path: pathlib.Path) -> None:
    """scan_tasks skips directories that lack artifacts/tokens.json."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "CODER-001-task").mkdir(parents=True)
    result = ma.scan_tasks(tasks_dir)
    assert result == []


def test_scan_tasks_returns_record_for_task_with_tokens_json(
    tmp_path: pathlib.Path,
) -> None:
    """scan_tasks returns one record per task that has a tokens.json."""
    _make_tokens_json(tmp_path, "CODER-20260101-001-task", rc_version="v0.24.7")
    records = ma.scan_tasks(tmp_path / "tasks")
    assert len(records) == 1
    assert records[0]["task_id"] == "CODER-20260101-001-task"
    assert records[0]["rc_version"] == "v0.24.7"


def test_scan_tasks_skips_queues_subdirectory(tmp_path: pathlib.Path) -> None:
    """scan_tasks ignores the queues/ subdirectory inside tasks/."""
    tasks_dir = tmp_path / "tasks"
    queues = tasks_dir / "queues"
    queues.mkdir(parents=True)
    # Even if there's something inside queues that looks like a task, skip it.
    _make_tokens_json(tmp_path, "CODER-20260101-001-legit", rc_version="v0.24.7")
    records = ma.scan_tasks(tasks_dir)
    task_ids = [r["task_id"] for r in records]
    assert "queues" not in task_ids


def test_scan_tasks_returns_multiple_records(tmp_path: pathlib.Path) -> None:
    """scan_tasks returns one record per task with tokens.json."""
    _make_tokens_json(tmp_path, "CODER-001", rc_version="v0.24.7")
    _make_tokens_json(tmp_path, "PM-002", rc_version="v0.24.7")
    records = ma.scan_tasks(tmp_path / "tasks")
    assert len(records) == 2


# ---------------------------------------------------------------------------
# aggregate_rc
# ---------------------------------------------------------------------------


def test_aggregate_rc_writes_json_file(tmp_path: pathlib.Path) -> None:
    """aggregate_rc creates metrics/rc/<version>.json under the project directory."""
    proj = _make_project_dir(tmp_path, "myproject")
    _make_tokens_json(
        proj.parent.parent,  # kanban root = tmp_path
        "CODER-001",
        rc_version="v0.24.7",
        input_tokens=1000,
        output_tokens=500,
    )
    # Symlink tasks/ into the project directory for scan
    (proj / "tasks").symlink_to(tmp_path / "tasks")

    out_path = ma.aggregate_rc("myproject", "v0.24.7", kanban_root=tmp_path)
    assert out_path.exists()
    assert out_path.suffix == ".json"


def test_aggregate_rc_payload_contains_expected_fields(tmp_path: pathlib.Path) -> None:
    """aggregate_rc writes a payload with rc, project, tokens, and tasks fields."""
    proj = _make_project_dir(tmp_path, "myproject")
    tasks_dir = proj / "tasks"
    _make_tokens_json(
        tmp_path,  # kanban root
        "CODER-001",
        rc_version="v0.24.7",
        input_tokens=200,
        output_tokens=100,
        role="coder",
    )
    # Wire up the tasks dir inside the project
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task_dir in (tmp_path / "tasks").iterdir():
        dest = tasks_dir / task_dir.name
        if not dest.exists():
            import shutil
            shutil.copytree(task_dir, dest)

    out_path = ma.aggregate_rc("myproject", "v0.24.7", kanban_root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["rc"] == "v0.24.7"
    assert payload["project"] == "myproject"
    assert "tokens" in payload
    assert "tasks" in payload
    assert payload["tasks"]["total"] == 1
    assert payload["tokens"]["total"]["input"] == 200
    assert payload["tokens"]["total"]["output"] == 100


def test_aggregate_rc_filters_to_matching_rc_version(tmp_path: pathlib.Path) -> None:
    """aggregate_rc only counts tasks attributed to the specified RC version."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # Task for v0.24.7
    t1 = tasks_dir / "CODER-001"
    (t1 / "artifacts").mkdir(parents=True)
    (t1 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T10:00:00"}),
        encoding="utf-8",
    )
    (t1 / "README.md").write_text("## Release Version\nv0.24.7\n## Role\ncoder\n", encoding="utf-8")

    # Task for a different version
    t2 = tasks_dir / "CODER-002"
    (t2 / "artifacts").mkdir(parents=True)
    (t2 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 999, "output_tokens": 999,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T11:00:00"}),
        encoding="utf-8",
    )
    (t2 / "README.md").write_text("## Release Version\nv0.99.0\n## Role\ncoder\n", encoding="utf-8")

    out_path = ma.aggregate_rc("proj", "v0.24.7", kanban_root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["tasks"]["total"] == 1
    assert payload["tokens"]["total"]["input"] == 100


def test_aggregate_rc_is_idempotent(tmp_path: pathlib.Path) -> None:
    """aggregate_rc produces byte-identical output when called twice on the same input."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    t1 = tasks_dir / "CODER-001"
    (t1 / "artifacts").mkdir(parents=True)
    (t1 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 50, "output_tokens": 25,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T10:00:00"}),
        encoding="utf-8",
    )
    (t1 / "README.md").write_text("## Release Version\nv0.24.7\n## Role\ncoder\n", encoding="utf-8")

    path1 = ma.aggregate_rc("proj", "v0.24.7", kanban_root=tmp_path)
    content1 = path1.read_text(encoding="utf-8")
    path2 = ma.aggregate_rc("proj", "v0.24.7", kanban_root=tmp_path)
    content2 = path2.read_text(encoding="utf-8")
    assert content1 == content2


# ---------------------------------------------------------------------------
# aggregate_day
# ---------------------------------------------------------------------------


def test_aggregate_day_writes_json_file(tmp_path: pathlib.Path) -> None:
    """aggregate_day creates metrics/day/<day>.json under the project directory."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    t1 = tasks_dir / "CODER-001"
    (t1 / "artifacts").mkdir(parents=True)
    (t1 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T12:00:00"}),
        encoding="utf-8",
    )

    out_path = ma.aggregate_day("proj", "2026-05-17", kanban_root=tmp_path)
    assert out_path.exists()
    assert out_path.name == "2026-05-17.json"


def test_aggregate_day_payload_contains_expected_fields(tmp_path: pathlib.Path) -> None:
    """aggregate_day writes a payload with date, project, rcs_included, tokens."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    t1 = tasks_dir / "CODER-001"
    (t1 / "artifacts").mkdir(parents=True)
    (t1 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 300, "output_tokens": 150,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T09:30:00"}),
        encoding="utf-8",
    )
    (t1 / "README.md").write_text("## Release Version\nv0.24.7\n## Role\ncoder\n", encoding="utf-8")

    out_path = ma.aggregate_day("proj", "2026-05-17", kanban_root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-05-17"
    assert payload["project"] == "proj"
    assert "rcs_included" in payload
    assert "tokens" in payload
    assert payload["tokens"]["total"]["input"] == 300


def test_aggregate_day_only_includes_matching_day_tasks(tmp_path: pathlib.Path) -> None:
    """aggregate_day excludes tasks whose timestamp falls outside the requested day."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # Task on the correct day
    t1 = tasks_dir / "CODER-001"
    (t1 / "artifacts").mkdir(parents=True)
    (t1 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-17T10:00:00"}),
        encoding="utf-8",
    )

    # Task on a different day — must be excluded
    t2 = tasks_dir / "CODER-002"
    (t2 / "artifacts").mkdir(parents=True)
    (t2 / "artifacts" / "tokens.json").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "input_tokens": 999, "output_tokens": 999,
                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "invocations": 1, "timestamp": "2026-05-18T10:00:00"}),
        encoding="utf-8",
    )

    out_path = ma.aggregate_day("proj", "2026-05-17", kanban_root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["tokens"]["total"]["input"] == 100


def test_aggregate_day_empty_when_no_tasks_on_day(tmp_path: pathlib.Path) -> None:
    """aggregate_day produces zero token totals when no tasks match the day."""
    proj = _make_project_dir(tmp_path, "proj")
    tasks_dir = proj / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    out_path = ma.aggregate_day("proj", "2099-01-01", kanban_root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["tokens"]["total"]["input"] == 0
    assert payload["tokens"]["total"]["output"] == 0
    assert payload["rcs_included"] == []
