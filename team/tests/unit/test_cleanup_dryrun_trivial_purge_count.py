"""
test_cleanup_dryrun_trivial_purge_count.py
============================================
Behavioral unit tests for team/scripts/cleanup/cleanup.sh's dry-run trivial
log purge summary.

purge_trivial_logs() narrates each dry-run candidate through log(), which
writes to stdout. When the function's result reached its caller through
command substitution, that narration shared the same channel as the
function's own count, so the summary's trivial_logs_deleted field printed
whatever narration text log() had just written instead of an integer. This
lock-in test seeds a sandboxed root with a known number of matching trivial
logs, runs --dry-run, and asserts:

  - the summary's trivial_logs_deleted field parses as a plain integer
    equal to the seeded count (the failure signature of the contamination
    bug is a non-integer value there instead)
  - the per-file "[DRY-RUN] ... would delete: <path>" lines still appear,
    once each, in the Step 0 section of the run log (the fix separates the
    channels; it does not silence the narration)
  - no seeded file is deleted (dry-run never mutates)

Every invocation sandboxes both KANBAN_ROOT and PGAI_AGENT_KANBAN_ROOT_PATH
to a throwaway tmp_path root, per the Live-Install Safety convention in
roles/CODER.md.
"""

from __future__ import annotations

import os
import pathlib
import re
import time

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"
_PROJECT_NAME = "demo"


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
        # Cleared (not just left to the ambient default) so cleanup.sh
        # derives both from KANBAN_ROOT above rather than refusing on a
        # stale export inherited from the calling shell or another
        # fixture's default -- this test is exercising the trivial-log
        # summary, not export reconciliation.
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
    }


def _seed_trivial_batch_logs(root: pathlib.Path, count: int) -> list[pathlib.Path]:
    """Create *count* batch log files small and old enough to match the
    default trivial-log purge thresholds (<8192 bytes, >6 hours old)."""
    agents_dir = root / "logs" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    old_time = time.time() - (7 * 3600)  # 7h old: past the 6h default threshold
    paths = []
    for i in range(count):
        path = agents_dir / f"coder-batch-2026091{i}-000000.log"
        path.write_text("tiny\n", encoding="utf-8")
        os.utime(path, (old_time, old_time))
        paths.append(path)
    return paths


def _seed_release_state(root: pathlib.Path) -> None:
    """Populate the preflight release-state.md the non-trivial-only,
    non-temp-only path requires before Step 0 runs."""
    project_dir = root / "projects" / _PROJECT_NAME
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\nv1.0.0\n",
        encoding="utf-8",
    )


def test_dry_run_reports_seeded_trivial_log_count_as_a_plain_integer(
    tmp_path: pathlib.Path,
) -> None:
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()
    _seed_release_state(sandbox)
    seeded = _seed_trivial_batch_logs(sandbox, 3)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project {_PROJECT_NAME} --dry-run",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    match = re.search(r"trivial_logs_deleted:\s*(\S+)", result.stdout)
    assert match is not None, (
        f"summary is missing a trivial_logs_deleted field:\n{result.stdout}"
    )
    value = match.group(1)
    assert value.isdigit(), (
        f"trivial_logs_deleted must be a plain integer, got {value!r} -- a "
        "non-integer value means the dry-run narration leaked into the "
        f"count channel again:\n{result.stdout}"
    )
    assert value == "3", (
        f"expected the seeded count 3, got {value!r}:\n{result.stdout}"
    )

    # Per-file narration still appears, once each, in the Step 0 section --
    # the fix separates the channels, it does not silence the narration.
    for path in seeded:
        narration_line = f"[DRY-RUN] trivial-purge would delete: {path}"
        assert result.stdout.count(narration_line) == 1, (
            f"expected exactly one narration line for {path}:\n{result.stdout}"
        )

    # Dry-run never deletes.
    for path in seeded:
        assert path.exists(), f"--dry-run must not delete {path}"


def test_dry_run_with_no_matching_trivial_logs_reports_zero(
    tmp_path: pathlib.Path,
) -> None:
    """No seeded logs -- the count reads 0, not an empty or corrupted value."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()
    _seed_release_state(sandbox)

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --project {_PROJECT_NAME} --dry-run",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 0
    match = re.search(r"trivial_logs_deleted:\s*(\S+)", result.stdout)
    assert match is not None
    assert match.group(1) == "0", f"expected 0 with nothing seeded:\n{result.stdout}"
