"""
test_metrics_rollup_refresh.py
==============================
Behavioral unit tests for team/scripts/lib/metrics_rollup_refresh.sh -- the
wake-finalize step that recomputes a per-RC metrics rollup when a task's
tokens.json lands after that rollup was already written.

The scenario under test is the release/finalize ordering: an RC has an
open-rc task and a release task; the rollup is written (the way release
finalize writes it, through the aggregator CLI) while only the open-rc task
has a tokens.json; the release task's tokens.json lands afterwards; the
refresh runs; the rollup now counts both tasks with the release task's
tokens and elapsed seconds included.

Every fixture is built under tmp_path (a synthetic kanban root with one
project). No live install, git repository, or ambient environment value is
read: the kanban root, project name, and task directory are passed to the
shell function explicitly, and the aggregator is the dev-tree copy the lib
locates beside itself. The shell function is exercised through bash via the
shared harness (docs/test-authoring-guidelines.md, "Prove an operator-facing
behaviour through the production entry point").

Coverage:
  - The ordering above: cm == 1 after the first aggregation, cm == 2 with
    the release task's tokens and wall seconds after the refresh.
  - Byte-identical output when the refresh runs twice (idempotency).
  - No rollup yet for the task's RC: nothing is written, nothing narrated.
  - A README without a Release Version: an unrelated rollup is untouched.
  - A version written without its "v" prefix, under a heading carrying
    trailing whitespace, still resolves to the rollup file.
  - A document-workflow rollup keeps workflow_type "document" through the
    refresh rather than being flipped to the aggregator's default.
  - A refresh whose aggregator run fails returns 0 under strict mode, says
    so with a WARNING that names the rollup, and leaves the rollup's bytes
    exactly as finalize wrote them.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from tests.unit.shell_harness import run_bash

# Paths relative to team/, the harness's own working directory.
_LIB = "scripts/lib/metrics_rollup_refresh.sh"
_AGGREGATOR = "scripts/lib/metrics_aggregator.py"

_PROJECT = "fixture-project"
_VERSION = "v1.4.2"


def _team_dir() -> pathlib.Path:
    """The team/ directory, self-located from this file (never inherited)."""
    return pathlib.Path(__file__).resolve().parents[2]


def _make_task(
    root: pathlib.Path,
    task_id: str,
    *,
    role: str = "cm",
    release_version: str | None = _VERSION,
    readme_text: str | None = None,
) -> pathlib.Path:
    """Create projects/<project>/tasks/<task_id>/ with a README naming the RC.

    readme_text, when given, replaces the generated README verbatim so a
    test can shape the heading and value exactly.
    """
    task_dir = root / "projects" / _PROJECT / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    if readme_text is None:
        readme_text = f"# Task\n\n## Role\n{role}\n"
        if release_version is not None:
            readme_text += f"\n## Release Version\n{release_version}\n"
    (task_dir / "README.md").write_text(readme_text, encoding="utf-8")
    return task_dir


def _write_tokens(
    task_dir: pathlib.Path,
    *,
    input_tokens: int,
    output_tokens: int,
    elapsed_seconds: int,
    model: str = "claude-sonnet-4-6",
) -> None:
    """Write a new-schema tokens.json (model_usage form) for one task."""
    payload: dict[str, Any] = {
        "provider": "claude",
        "agent": "cm",
        "rc_version": _VERSION,
        "invocations": 1,
        "elapsed_seconds": elapsed_seconds,
        "timestamp": "2026-05-17T12:00:00Z",
        "total_cost_usd": 0.01,
        "model_usage": {
            model: {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.01,
            }
        },
    }
    (task_dir / "artifacts" / "tokens.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _aggregate_as_finalize_does(
    root: pathlib.Path, version: str = _VERSION, workflow_type: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the aggregator CLI with the argument shape release finalize uses."""
    argv = [
        sys.executable,
        _AGGREGATOR,
        "--project", _PROJECT,
        "--rc", version,
        "--kanban-root", str(root),
    ]
    if workflow_type is not None:
        argv += ["--workflow-type", workflow_type]
    return subprocess.run(
        argv, cwd=str(_team_dir()), capture_output=True, text=True, timeout=60
    )


def _rollup_path(root: pathlib.Path, version: str = _VERSION) -> pathlib.Path:
    return root / "projects" / _PROJECT / "metrics" / "rc" / f"{version}.json"


def _read_rollup(root: pathlib.Path, version: str = _VERSION) -> dict[str, Any]:
    return json.loads(_rollup_path(root, version).read_text(encoding="utf-8"))


def _refresh(
    tmp_path: pathlib.Path,
    task_dir: pathlib.Path,
    root: pathlib.Path,
    *,
    extra_env: dict[str, str] | None = None,
):
    """Invoke metrics_rollup_refresh_for_task through bash under strict mode."""
    script = (
        "set -euo pipefail\n"
        f"source {_LIB}\n"
        f"metrics_rollup_refresh_for_task '{task_dir}' '{root}' '{_PROJECT}'\n"
    )
    return run_bash(tmp_path, script, extra_env=extra_env, timeout=60)


@pytest.fixture
def rc_with_release_task_pending(tmp_path: pathlib.Path):
    """An RC whose rollup was written before its release task's tokens.json.

    Yields (root, release_task_dir). The open-rc task has a tokens.json; the
    release task has only its README; the rollup already exists and counts
    exactly one CM task -- the state release finalize leaves behind.
    """
    root = tmp_path / "kanban"
    open_rc = _make_task(root, "CM-20260517-001-open-rc-1-4-2")
    _write_tokens(open_rc, input_tokens=1000, output_tokens=200, elapsed_seconds=90)
    release = _make_task(root, "CM-20260517-004-release-1-4-2")

    first = _aggregate_as_finalize_does(root)
    assert first.returncode == 0, first.stderr
    before = _read_rollup(root)
    assert before["tasks"]["by_agent"] == {"cm": 1}, (
        "precondition: the finalize-time rollup counts only the open-rc task"
    )
    return root, release


# ---------------------------------------------------------------------------
# The release/finalize ordering
# ---------------------------------------------------------------------------


def test_release_task_tokens_landing_after_rollup_reach_that_rollup(
    tmp_path: pathlib.Path, rc_with_release_task_pending
) -> None:
    """A release task's tokens.json written after the rollup is counted by it,
    tokens and wall seconds included."""
    root, release = rc_with_release_task_pending
    _write_tokens(release, input_tokens=5000, output_tokens=800, elapsed_seconds=300)

    result = _refresh(tmp_path, release, root)

    assert result.returncode == 0, result.stderr
    assert "rollup refresh" in result.stderr, (
        "the refresh should narrate that it recomputed the rollup; stderr was:\n"
        + result.stderr
    )
    after = _read_rollup(root)
    assert after["tasks"]["total"] == 2
    assert after["tasks"]["by_agent"] == {"cm": 2}, (
        "the rollup must count both the open-rc task and the release task"
    )
    assert after["tokens"]["by_agent"]["cm"]["input"] == 1000 + 5000
    assert after["tokens"]["by_agent"]["cm"]["output"] == 200 + 800
    assert after["tokens"]["by_agent"]["cm"]["invocations"] == 2
    assert after["wall"]["by_agent"]["cm"] == {"seconds": 90 + 300, "task_count": 2}


def test_refresh_is_byte_identical_when_run_twice(
    tmp_path: pathlib.Path, rc_with_release_task_pending
) -> None:
    """Two refreshes over the same inputs produce the same bytes."""
    root, release = rc_with_release_task_pending
    _write_tokens(release, input_tokens=5000, output_tokens=800, elapsed_seconds=300)

    first = _refresh(tmp_path, release, root)
    assert first.returncode == 0, first.stderr
    bytes_after_first = _rollup_path(root).read_bytes()

    second = _refresh(tmp_path, release, root)
    assert second.returncode == 0, second.stderr
    assert _rollup_path(root).read_bytes() == bytes_after_first


# ---------------------------------------------------------------------------
# When there is nothing to refresh
# ---------------------------------------------------------------------------


def test_task_whose_rc_has_no_rollup_yet_writes_nothing(tmp_path: pathlib.Path) -> None:
    """Before finalize has written the rollup, the refresh is a silent no-op:
    finalize's own aggregation will already include this task."""
    root = tmp_path / "kanban"
    task = _make_task(root, "CODER-20260517-002-some-work", role="coder")
    _write_tokens(task, input_tokens=10, output_tokens=5, elapsed_seconds=1)

    result = _refresh(tmp_path, task, root)

    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "", (
        "no rollup exists, so nothing should be narrated; stderr was:\n" + result.stderr
    )
    assert not _rollup_path(root).exists()
    assert not (root / "projects" / _PROJECT / "metrics").exists()


def test_task_without_release_version_leaves_other_rollups_untouched(
    tmp_path: pathlib.Path, rc_with_release_task_pending
) -> None:
    """A task that names no Release Version cannot be attributed to any RC,
    so an existing rollup is left byte-for-byte as it was."""
    root, _release = rc_with_release_task_pending
    unattributed = _make_task(
        root, "WRITER-20260517-003-unversioned", role="writer", release_version=None
    )
    _write_tokens(unattributed, input_tokens=10, output_tokens=5, elapsed_seconds=1)
    bytes_before = _rollup_path(root).read_bytes()

    result = _refresh(tmp_path, unattributed, root)

    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
    assert _rollup_path(root).read_bytes() == bytes_before


# ---------------------------------------------------------------------------
# Liberal reading of the README field
# ---------------------------------------------------------------------------


def test_release_version_without_v_prefix_under_padded_heading_resolves(
    tmp_path: pathlib.Path, rc_with_release_task_pending
) -> None:
    """'1.4.2' under '## Release Version   ' (trailing spaces, blank line, padded
    value) still resolves to the v1.4.2 rollup."""
    root, release = rc_with_release_task_pending
    bare_version = _VERSION[1:]
    (release / "README.md").write_text(
        f"# Task\n\n## Role\ncm\n\n## Release Version   \n\n   {bare_version}  \n\n## Notes\nnone\n",
        encoding="utf-8",
    )
    _write_tokens(release, input_tokens=5000, output_tokens=800, elapsed_seconds=300)

    result = _refresh(tmp_path, release, root)

    assert result.returncode == 0, result.stderr
    assert _read_rollup(root)["tasks"]["by_agent"] == {"cm": 2}


# ---------------------------------------------------------------------------
# workflow_type survives the refresh
# ---------------------------------------------------------------------------


def test_document_workflow_rollup_keeps_its_workflow_type(tmp_path: pathlib.Path) -> None:
    """A rollup written with --workflow-type document is refreshed as document,
    not flipped to the aggregator's 'release' default."""
    root = tmp_path / "kanban"
    open_rc = _make_task(root, "CM-20260517-001-open-rc-1-4-2")
    _write_tokens(open_rc, input_tokens=1000, output_tokens=200, elapsed_seconds=90)
    release = _make_task(root, "CM-20260517-004-release-1-4-2")
    first = _aggregate_as_finalize_does(root, workflow_type="document")
    assert first.returncode == 0, first.stderr
    assert _read_rollup(root)["workflow_type"] == "document"
    _write_tokens(release, input_tokens=5000, output_tokens=800, elapsed_seconds=300)

    result = _refresh(tmp_path, release, root)

    assert result.returncode == 0, result.stderr
    after = _read_rollup(root)
    assert after["workflow_type"] == "document"
    assert after["tasks"]["by_agent"] == {"cm": 2}


# ---------------------------------------------------------------------------
# A failed refresh never fails the caller and never damages the rollup
# ---------------------------------------------------------------------------


def test_failed_aggregator_run_is_a_warning_and_rollup_is_unchanged(
    tmp_path: pathlib.Path, rc_with_release_task_pending
) -> None:
    """With python3 failing on PATH, the refresh still returns 0 under strict
    mode, narrates a WARNING naming the rollup, and leaves the rollup's bytes
    exactly as finalize wrote them."""
    root, release = rc_with_release_task_pending
    _write_tokens(release, input_tokens=5000, output_tokens=800, elapsed_seconds=300)
    bytes_before = _rollup_path(root).read_bytes()

    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text("#!/usr/bin/env bash\necho 'aggregator unavailable' >&2\nexit 3\n")
    shim.chmod(0o755)
    shimmed_path = f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    result = _refresh(tmp_path, release, root, extra_env={"PATH": shimmed_path})

    assert result.returncode == 0, (
        "a failed refresh must not fail the caller; stderr was:\n" + result.stderr
    )
    assert "WARNING" in result.stderr
    assert "exited with code 3" in result.stderr
    assert str(_rollup_path(root)) in result.stderr, (
        "the warning should name the rollup that kept its finalize-time contents"
    )
    assert _rollup_path(root).read_bytes() == bytes_before
