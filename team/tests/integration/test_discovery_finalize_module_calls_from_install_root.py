"""
test_discovery_finalize_module_calls_from_install_root.py
=========================================================
End-to-end coverage for the discovery and finalize call sites that reach
the shared pgai_agent_kanban modules through pp_run_ops, run from an
install-shaped kanban root rather than from this dev tree:

  team/scripts/lib/discovery_state.sh
      _disc_set_status_field, _disc_read_bundle_field,
      _disc_update_bundle_pm_queued, _disc_mark_cache_bundled
  team/scripts/lib/wake_driver_finalize.sh
      narrate_model_mismatch (## Model read), _wd_append_summary_finding
      (the pollution sweep's ## Summary append)

Each lib is sourced on its own, from a copy of scripts/ and
pgai_agent_kanban/ laid out the way install.sh lays out a live root, in a
subprocess whose environment is built from an explicit minimal set (no
PYTHONPATH) and whose working directory is outside that root. That is the
shape in which a wrong root, a missing self-source of pp_run_ops.sh or
atomic_write.sh, or a dependency on the caller's cwd would break a
migrated site while the unit tests (run from team/) still pass.

The assertions are the byte results the discovery pipeline and the wake
finalize stage depend on: the bundle written by _disc_write_bundle's layout
moves to running with its PM Task id, an intake file's ## Status flips, the
backlog cache gains or flips its entry (including a cache created from
nothing), the finding lands in ## Summary, and a stamped/observed model
mismatch is narrated. No scratch file is left beside any target.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_ITEM = "BUG-0001-install-root-item"
_PM_ID = "PM-20260101-001-decompose-install-root"


@pytest.fixture()
def install_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root holding copies of scripts/ and pgai_agent_kanban/."""
    root = tmp_path / "kanban-root"
    root.mkdir()
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban",
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    return root


def _run(tmp_path: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    """Run *body* in bash from a directory outside *root*, scrubbed env."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir), "KANBAN_ROOT": str(root)},
    )
    script = "set -euo pipefail\nlog() { printf 'LOG: %s\\n' \"$*\"; }\n" + textwrap.dedent(body)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(elsewhere),
    )


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"


def _no_scratch(directory: pathlib.Path) -> None:
    leftovers = sorted(p.name for p in directory.iterdir() if ".tmp." in p.name)
    assert leftovers == [], f"scratch files left behind in {directory}: {leftovers}"


def test_discovery_writers_and_reader_run_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    work = tmp_path / "project"
    (work / "bugs").mkdir(parents=True)
    bundle = work / "requirements" / "v9.9.9-bug-bundle.md"
    bundle.parent.mkdir()
    bundle.write_text(
        "# Requirements: v9.9.9 — BUG bundle\n\n## Status\n\nopen\n\n## PM Task\n\nnone\n\n"
        "## Target Version\n\nv9.9.9\n",
        encoding="utf-8",
    )
    item = work / "bugs" / f"{_ITEM}.md"
    item.write_text(f"# {_ITEM}\n\n## Status\nopen\n\n## Severity\nlow\n", encoding="utf-8")
    cache = work / "tasks" / "queues" / "bug_backlog.md"

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/discovery_state.sh"
        _disc_mark_cache_bundled "{cache}" "{_ITEM}"
        _disc_set_status_field "{item}" running
        _disc_update_bundle_pm_queued "{bundle}" "{_PM_ID}"
        echo "STATUS=[$(_disc_read_bundle_field "{bundle}" Status)]"
        echo "PM=[$(_disc_read_bundle_field "{bundle}" "PM Task")]"
        _disc_read_bundle_field "{bundle}" "No Such Field" || echo "ABSENT_RC=$?"
        """,
    )
    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout == f"STATUS=[running]\nPM=[{_PM_ID}]\nABSENT_RC=1\n", _explain(proc)
    assert bundle.read_text(encoding="utf-8") == (
        f"# Requirements: v9.9.9 — BUG bundle\n\n## Status\nrunning\n\n## PM Task\n\n{_PM_ID}\n\n"
        "## Target Version\n\nv9.9.9\n"
    )
    assert item.read_text(encoding="utf-8") == f"# {_ITEM}\n\n## Status\nrunning\n\n## Severity\nlow\n"
    assert cache.read_text(encoding="utf-8") == f"# Backlog Cache\n\n## Queue\n\n- [x] {_ITEM}\n"
    for directory in (bundle.parent, item.parent, cache.parent):
        _no_scratch(directory)


def test_discovery_cache_flip_rewrites_an_existing_line_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    cache = tmp_path / "bug_backlog.md"
    cache.write_text(f"# Bug Backlog\n\n## Queue\n\n- [ ] {_ITEM} — the bug\n- [ ] BUG-0002-next\n", encoding="utf-8")
    cache.chmod(0o640)
    proc = _run(
        tmp_path,
        install_root,
        f'source "{install_root}/scripts/lib/discovery_state.sh"\n_disc_mark_cache_bundled "{cache}" "{_ITEM}"\n',
    )
    assert proc.returncode == 0, _explain(proc)
    assert cache.read_text(encoding="utf-8") == (
        f"# Bug Backlog\n\n## Queue\n\n- [x] {_ITEM} — the bug\n- [ ] BUG-0002-next\n"
    )
    assert cache.stat().st_mode & 0o777 == 0o640, "the atomic rewrite must keep the cache file's mode"


def test_finalize_model_read_and_summary_append_run_from_an_install_root(
    tmp_path: pathlib.Path, install_root: pathlib.Path
) -> None:
    status = tmp_path / "status.md"
    status.write_text(
        "## Task\nTESTER-20260101-001-demo\n\n## Model\nmodel-a\n\n## State\nDONE\n\n"
        "## Summary\nVerified the RC.\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"model_usage": {"model-b": {"output_tokens": 900}}}), encoding="utf-8")
    finding = "POLLUTION SWEEP WARNING: agent tester modified tracked file(s) during this task: a.sh"

    proc = _run(
        tmp_path,
        install_root,
        f"""\
        source "{install_root}/scripts/lib/wake_driver_finalize.sh"
        _wd_append_summary_finding "{status}" "{finding}"
        narrate_model_mismatch "{status}" "{tokens}" TESTER-20260101-001-demo
        """,
    )
    assert proc.returncode == 0, _explain(proc)
    assert status.read_text(encoding="utf-8") == (
        "## Task\nTESTER-20260101-001-demo\n\n## Model\nmodel-a\n\n## State\nDONE\n\n"
        f"## Summary\nVerified the RC.\n\n{finding}\n\n## Needs Human\nno\n"
    )
    assert "stamped 'model-a' but tokens.json shows 'model-b'" in proc.stdout, _explain(proc)
    _no_scratch(tmp_path)
