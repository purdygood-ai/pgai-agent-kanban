"""
test_cache_marker_reset.py
==========================
The reset OVERWATCH's cache-marker drift check makes when a bug or priority
file reads ``## Status: open`` but its backlog-cache line still reads
``[x]``: ``pgai_agent_kanban.overwatch.cache_marker_reset``.

Covered behaviour:

  * item present: every ``- [x] <id>`` line becomes ``- [ ] <id>``, the
    ``x`` the only byte that changes; a longer id sharing the prefix and a
    line for the same id with another marker are untouched; the command
    line exits 0 and names the count on stderr;
  * item absent: nothing is written, the error names the item and file,
    and the command line exits 1 with that one stderr line;
  * unreadable cache file (missing, or a directory): the error starts
    ``Cannot read cache file:`` and the command line exits 1;
  * through check-cache-marker-drift.sh's _ccmd_do_reset_cache_entry, the
    cache file is reset even when the caller defined its own pp_run_ops,
    and an absent item makes the step fail with its narration line.

The check's collaborators overwatch_backup_file / overwatch_log_action are
stubbed, as the other OVERWATCH check tests do. Every fixture lives under
tmp_path; subprocesses run with an environment built from an explicit set.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.overwatch import cache_marker_reset
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CHECK = _TEAM_DIR / "scripts" / "lib" / "overwatch-checks" / "check-cache-marker-drift.sh"
_MODULE = "pgai_agent_kanban.overwatch.cache_marker_reset"

_CACHE = (
    "# Bug Backlog\n"
    "\n"
    "- [x] BUG-0001 — first\n"
    "- [x] BUG-00010 — shares the prefix\n"
    "  -   [x]\tBUG-0001\n"
    "- [B] BUG-0001 another marker\n"
    "- [x] BUG-0002"
)
_RESET = (
    "# Bug Backlog\n"
    "\n"
    "- [ ] BUG-0001 — first\n"
    "- [x] BUG-00010 — shares the prefix\n"
    "  -   [ ]\tBUG-0001\n"
    "- [B] BUG-0001 another marker\n"
    "- [x] BUG-0002"
)


def _run_module(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={"PYTHONPATH": str(_TEAM_DIR)})
    return subprocess.run(
        [sys.executable, "-m", _MODULE, "--", *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60, check=False,
    )


def test_present_item_has_every_processed_line_reset(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(_CACHE, encoding="utf-8")
    assert cache_marker_reset.reset_processed_marker(cache, "BUG-0001") == 2
    assert cache.read_text(encoding="utf-8") == _RESET


def test_command_line_resets_the_item_and_names_the_count(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(_CACHE, encoding="utf-8")
    result = _run_module(tmp_path, str(cache), "BUG-0001")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == f"Reset 2 entry(ies) for BUG-0001 from [x] to [ ] in {cache}\n"
    assert cache.read_text(encoding="utf-8") == _RESET


def test_absent_item_writes_nothing_and_fails(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(_CACHE, encoding="utf-8")
    with pytest.raises(cache_marker_reset.CacheMarkerResetError) as raised:
        cache_marker_reset.reset_processed_marker(cache, "BUG-0003")
    assert str(raised.value) == f"Could not find [x] entry for BUG-0003 in {cache}; no change made."
    assert cache.read_text(encoding="utf-8") == _CACHE

    result = _run_module(tmp_path, str(cache), "BUG-0003")
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == f"Could not find [x] entry for BUG-0003 in {cache}; no change made.\n"
    assert cache.read_text(encoding="utf-8") == _CACHE


@pytest.mark.parametrize("shape", ["missing", "directory"])
def test_unreadable_cache_file_fails_with_one_line(tmp_path: pathlib.Path, shape: str) -> None:
    cache = tmp_path / "bug_backlog.md"
    if shape == "directory":
        cache.mkdir()
    with pytest.raises(cache_marker_reset.CacheMarkerResetError) as raised:
        cache_marker_reset.reset_processed_marker(cache, "BUG-0001")
    assert str(raised.value).startswith("Cannot read cache file: ")

    result = _run_module(tmp_path, str(cache), "BUG-0001")
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.startswith("Cannot read cache file: ") and result.stderr.count("\n") == 1, result.stderr


def _reset_step(tmp_path: pathlib.Path, cache: pathlib.Path, item_id: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        set -euo pipefail
        pp_run_ops() {{ echo "caller-runner $*"; return 0; }}
        overwatch_backup_file() {{ echo none; }}
        overwatch_log_action() {{ :; }}
        source '{_CHECK}'
        rc=0
        _CCMD_CACHE_FILE='{cache}' _CCMD_ITEM_ID='{item_id}' _CCMD_SRC_FILE='bugs/{item_id}.md' \\
            _ccmd_do_reset_cache_entry || rc=$?
        echo "rc=$rc"
    """)
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60, check=False,
    )


def test_drift_check_reset_step_resets_the_cache_through_its_own_runner(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(_CACHE, encoding="utf-8")
    result = _reset_step(tmp_path, cache, "BUG-0001")
    assert result.stdout == "rc=0\n", f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "caller-runner" not in result.stderr, result.stderr
    assert cache.read_text(encoding="utf-8") == _RESET
    assert f"Reset 2 entry(ies) for BUG-0001 from [x] to [ ] in {cache}" in result.stderr
    assert "check-cache-marker-drift: reset cache entry [x] -> [ ] for BUG-0001 (backup: none)" in result.stderr


def test_drift_check_reset_step_fails_for_an_absent_item(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(_CACHE, encoding="utf-8")
    result = _reset_step(tmp_path, cache, "BUG-0003")
    assert result.stdout == "rc=1\n", f"stdout={result.stdout}\nstderr={result.stderr}"
    assert f"check-cache-marker-drift: reset failed for BUG-0003 in {cache}" in result.stderr
    assert cache.read_text(encoding="utf-8") == _CACHE
