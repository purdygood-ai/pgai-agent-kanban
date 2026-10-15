"""
test_purge_closed_states_from_install_root.py
=============================================
End-to-end coverage for the old-file purge's closed-state reader.
purge_closed_bugs and purge_closed_priorities (scripts/lib/purge-helpers.sh)
load the shared closed-state list from pgai_agent_kanban.lib.terminal_states
through pp_run_ops. This runs both from an install-shaped kanban root --
copies of scripts/ and pgai_agent_kanban/ side by side, the way install.sh
lays out a live root -- in a subprocess whose environment is an explicit
minimal set (no PYTHONPATH) and whose working directory is outside the root.
In that shape a wrong package root or a missing pp_run_ops source would make
every file read as not closed, which a unit run from team/ cannot catch.

Every fixture lives under pytest's tmp_path; no live kanban install is read
or written.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_STATUSES = ("done", "wont-do", "superseded", "open")
_OLD = time.time() - 100 * 86400


def _install_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    root.mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban",
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for subdir in ("bugs", "priority"):
        item_dir = root / "projects" / "p" / subdir
        item_dir.mkdir(parents=True)
        for status in _STATUSES:
            path = item_dir / f"ITEM-{status}.md"
            path.write_text(f"# ITEM\n\n## Status\n{status}\n", encoding="utf-8")
            os.utime(path, (_OLD, _OLD))
    return root


def test_purge_reads_the_shared_closed_state_list_from_an_install_root(tmp_path: pathlib.Path) -> None:
    root = _install_root(tmp_path)
    proj = root / "projects" / "p"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir()
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
        },
    )

    proc = subprocess.run(
        [
            "bash", "-c",
            f'set -euo pipefail\nsource "{root}/scripts/lib/purge-helpers.sh"\n'
            f'purge_closed_bugs "{proj}" 30 false\npurge_closed_priorities "{proj}" 30 false\n',
        ],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(elsewhere), check=False,
    )

    assert proc.returncode == 0, f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    lines = proc.stdout.splitlines()
    for subdir in ("bugs", "priority"):
        item_dir = proj / subdir
        assert f"[WOULD PURGE] {item_dir / 'ITEM-done.md'} (status=done)" in lines, proc.stdout
        assert f"[WOULD PURGE] {item_dir / 'ITEM-wont-do.md'} (status=wont-do)" in lines, proc.stdout
        assert (
            f"[WOULD PURGE] {item_dir / 'ITEM-superseded.md'} (status=wont-do; recorded as superseded)" in lines
        ), proc.stdout
        assert f"[SKIP] {item_dir / 'ITEM-open.md'} (status=open; not closed)" in lines, proc.stdout
    assert "could not load the closed-state list" not in proc.stderr
