"""
test_purge_closed_states.py
===========================
The old-file purge decides which bug and priority files are closed from the
shared closed-state list (``lib.terminal_states``: CLOSED_STATES plus
LEGACY_STATE_ALIASES), never from a list of its own:

  - purge_closed_bugs / purge_closed_priorities (team/scripts/lib/
    purge-helpers.sh) list a done, a wont-do and a legacy superseded file
    under [WOULD PURGE] on a dry run, the superseded one as wont-do, and
    skip an open one;
  - with apply=true the superseded file is deleted like any closed one;
  - when the shared list cannot be loaded, nothing reads as closed and
    nothing is purged;
  - ``python3 -m pgai_agent_kanban.lib.terminal_states closed-map`` -- the
    command the bash readers load the list through -- prints exactly the
    shared list.

Every fixture lives under pytest's tmp_path; no live kanban root is read or
written.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

from pgai_agent_kanban.lib import terminal_states
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PURGE = _TEAM_DIR / "scripts" / "lib" / "purge-helpers.sh"

_STATUSES = ("done", "wont-do", "superseded", "open")
_OLD = time.time() - 100 * 86400

# (function, directory under the project root)
_CATEGORIES = [
    ("purge_closed_bugs", "bugs"),
    ("purge_closed_priorities", "priority"),
]
_CATEGORY_IDS = ["bugs", "priorities"]


def _project(tmp_path: pathlib.Path, subdir: str) -> pathlib.Path:
    """Build projects/p/<subdir>/ with one 100-day-old file per status."""
    proj = tmp_path / "projects" / "p"
    item_dir = proj / subdir
    item_dir.mkdir(parents=True)
    for status in _STATUSES:
        path = item_dir / f"ITEM-{status}.md"
        path.write_text(f"# ITEM {status}\n\n## Status\n{status}\n\n## Symptom\nx\n", encoding="utf-8")
        os.utime(path, (_OLD, _OLD))
    return proj


def _purge(tmp_path: pathlib.Path, proj: pathlib.Path, fn: str, apply: str, prelude: str = "") -> str:
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_PURGE}'\n{prelude}{fn} '{proj}' 30 {apply}\n",
        timeout=60,
    )
    assert result.returncode == 0, f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    return result.stdout


@pytest.mark.parametrize(("fn", "subdir"), _CATEGORIES, ids=_CATEGORY_IDS)
def test_dry_run_lists_every_closed_state_and_reads_superseded_as_wont_do(
    tmp_path: pathlib.Path, fn: str, subdir: str,
) -> None:
    proj = _project(tmp_path, subdir)
    item_dir = proj / subdir

    out = _purge(tmp_path, proj, fn, "false")

    lines = out.splitlines()
    assert f"[WOULD PURGE] {item_dir / 'ITEM-done.md'} (status=done)" in lines
    assert f"[WOULD PURGE] {item_dir / 'ITEM-wont-do.md'} (status=wont-do)" in lines
    assert f"[WOULD PURGE] {item_dir / 'ITEM-superseded.md'} (status=wont-do; recorded as superseded)" in lines
    assert f"[SKIP] {item_dir / 'ITEM-open.md'} (status=open; not closed)" in lines
    assert lines[-1].split()[0] == "3"
    # A dry run deletes nothing.
    assert sorted(p.name for p in item_dir.iterdir()) == sorted(f"ITEM-{s}.md" for s in _STATUSES)


@pytest.mark.parametrize(("fn", "subdir"), _CATEGORIES, ids=_CATEGORY_IDS)
def test_apply_deletes_a_superseded_file_like_any_closed_one(
    tmp_path: pathlib.Path, fn: str, subdir: str,
) -> None:
    proj = _project(tmp_path, subdir)
    item_dir = proj / subdir

    out = _purge(tmp_path, proj, fn, "true")

    assert f"[PURGED] {item_dir / 'ITEM-superseded.md'} (status=wont-do; recorded as superseded)" in out.splitlines()
    assert [p.name for p in item_dir.iterdir()] == ["ITEM-open.md"]


@pytest.mark.parametrize(("fn", "subdir"), _CATEGORIES, ids=_CATEGORY_IDS)
def test_nothing_is_purged_when_the_closed_state_list_cannot_be_loaded(
    tmp_path: pathlib.Path, fn: str, subdir: str,
) -> None:
    proj = _project(tmp_path, subdir)
    item_dir = proj / subdir
    # Shadow the python launcher so the shared list cannot be read.
    unavailable = "pp_run_ops() { return 1; }\n"

    out = _purge(tmp_path, proj, fn, "true", prelude=unavailable)

    assert "[PURGED]" not in out
    assert out.splitlines()[-1] == "0 0"
    assert len(list(item_dir.iterdir())) == len(_STATUSES)


def test_closed_map_command_prints_the_shared_closed_state_list() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.lib.terminal_states", "closed-map"],
        cwd=_TEAM_DIR, capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 0, result.stderr
    pairs = [tuple(line.split()) for line in result.stdout.splitlines()]
    expected = [(s, s) for s in terminal_states.CLOSED_STATES]
    expected += sorted(terminal_states.LEGACY_STATE_ALIASES.items())
    assert pairs == expected


def test_closed_map_command_refuses_any_other_argument() -> None:
    assert terminal_states.main(["closed"]) == 2
    assert terminal_states.main([]) == 2
