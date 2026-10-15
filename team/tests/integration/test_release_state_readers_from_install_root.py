"""
test_release_state_readers_from_install_root.py
===============================================
End-to-end coverage for the bash release-state.md readers and writers that
reach pgai_agent_kanban.cm.read_state_field / cm.write_rc_state through the
wrappers in scripts/lib/cm/common.sh. Every one runs from an install-shaped
kanban root, not from this dev tree:

  scripts/lib/purge-helpers.sh            _purge_read_active_rc, get_active_rc
  scripts/lib/overwatch-checks/*.sh       _ccd_read_active_rc, _corb_read_active_rc,
                                          _csarc_read_active_rc, _csarc_do_reset
  scripts/lib/green_ledger.sh             _green_ledger_rc_scope
  scripts/lib/discovery_guards.sh         _disc_get_release_field
  scripts/lib/wake_autonomous_scan.sh     get_release_state_field
  scripts/lib/project_paths.sh            pp_last_released_version's
                                          release-state.md fallback
  scripts/dashboard/data.sh               ACTIVE_RC, RC_OPENED_AT
  scripts/dashboard/show-status-window.sh the RC age, from data.sh
  scripts/dashboard/show-multi.sh         each project's RC

The kanban root holds copies of scripts/ and pgai_agent_kanban/ side by
side, the way install.sh lays out a live root. Each lib is sourced on its
own from that copy, in a subprocess whose environment is built from an
explicit minimal set (no PYTHONPATH) and whose working directory is outside
the root. In that shape a wrong root, a missing source of cm/common.sh or
pp_run_ops.sh, or a dependency on the caller's cwd breaks a migrated site
even when the unit tests (run from team/) still pass.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.cm.read_state_field import read_state_section
from tests.fixtures.dashboard_render_fixture import (
    build_dashboard_project_root,
    clean_subprocess_env,
    run_dashboard_script,
)
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]

_RC_OPEN = (
    "# Release State\n\n## Active RC\nv2.30.0\n\n## RC Opened At\n2026-01-05T03:00:00Z\n\n"
    "## RC Opened By Task\nCM-20260105-034-open-rc\n\n## Last Released\nv2.29.2\n\n"
    "## Last Released At\n2026-01-01T00:00:00Z\n\n## Last Released By Task\nCM-old\n"
)
_IDLE = (
    "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
    "## RC Opened By Task\nnone\n\n## Last Released\nv1.4.0\n"
)
_OTHER = "other-project"


@pytest.fixture()
def kanban_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """An install-shaped kanban root: scripts/ and pgai_agent_kanban/ side by
    side, two registered projects (one with an RC open and retired Last
    Released headings, one idle), and a third project directory with no
    project.cfg whose last release comes from release-state.md alone."""
    fixture = build_dashboard_project_root(tmp_path, dev_tree_mode="unconfigured")
    root = fixture.kanban_root
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    (root / "projects" / fixture.project_name / "release-state.md").write_text(_RC_OPEN, encoding="utf-8")

    other = root / "projects" / _OTHER
    shutil.copytree(root / "projects" / fixture.project_name, other)
    (other / "project.cfg").write_text(
        (other / "project.cfg").read_text(encoding="utf-8").replace(fixture.project_name, _OTHER),
        encoding="utf-8",
    )
    (other / "release-state.md").write_text(_IDLE, encoding="utf-8")
    with (root / "projects.cfg").open("a", encoding="utf-8") as fh:
        fh.write(f"\n[project:{_OTHER}]\npriority=2\ndescription=second fixture project\nenabled=true\n")

    doc = root / "projects" / "doc-project"
    doc.mkdir()
    (doc / "release-state.md").write_text(_RC_OPEN, encoding="utf-8")
    return root


def _project(root: pathlib.Path) -> str:
    return next(p.name for p in (root / "projects").iterdir() if p.name not in (_OTHER, "doc-project"))


def _run(tmp_path: pathlib.Path, root: pathlib.Path, body: str) -> subprocess.CompletedProcess:
    """Run *body* in bash from a directory outside *root*, scrubbed env."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    temp_dir = tmp_path / "kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
        },
    )
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(elsewhere),
    )


def _explain(proc: subprocess.CompletedProcess) -> str:
    return f"rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"


def test_lib_readers_read_release_state_from_an_install_root(
    tmp_path: pathlib.Path, kanban_root: pathlib.Path
) -> None:
    lib = kanban_root / "scripts" / "lib"
    proj = kanban_root / "projects" / _project(kanban_root)
    other = kanban_root / "projects" / _OTHER
    rs = proj / "release-state.md"

    proc = _run(
        tmp_path,
        kanban_root,
        f"""\
        ( source "{lib}/purge-helpers.sh"
          echo "purge=$(_purge_read_active_rc "{proj}") get=$(get_active_rc "{proj}") idle=[$(get_active_rc "{other}")]" )
        ( source "{lib}/overwatch-checks/check-changelog-drift.sh"; echo "ccd=$(_ccd_read_active_rc "{rs}")" )
        ( source "{lib}/overwatch-checks/check-orphan-rc-branches.sh"; echo "corb=$(_corb_read_active_rc "{rs}")" )
        ( source "{lib}/overwatch-checks/check-stale-active-rc.sh"; echo "csarc=$(_csarc_read_active_rc "{rs}")" )
        ( source "{lib}/green_ledger.sh"
          echo "ledger=$(PGAI_PROJECT_ROOT="{proj}" _green_ledger_rc_scope demo) idle=$(PGAI_PROJECT_ROOT="{other}" _green_ledger_rc_scope demo)" )
        ( source "{lib}/project_paths.sh"; source "{lib}/discovery_guards.sh"
          echo "disc=$(_disc_get_release_field "{proj.name}" "Active RC") last=$(_disc_get_release_field "{proj.name}" "Last Released")" )
        ( log() {{ :; }}; source "{lib}/project_paths.sh"; source "{lib}/wake_autonomous_scan.sh"
          _CURRENT_PROJECT="{_OTHER}"
          echo "scan=$(get_release_state_field "Active RC") last=$(get_release_state_field "Last Released")" )
        ( source "{lib}/project_paths.sh"; echo "doc-last=$(pp_last_released_version doc-project)" )
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    assert proc.stdout.splitlines() == [
        "purge=v2.30.0 get=v2.30.0 idle=[]",
        "ccd=v2.30.0",
        "corb=v2.30.0",
        "csarc=v2.30.0",
        "ledger=v2.30.0 idle=no-active-rc",
        "disc=v2.30.0 last=v2.29.2",
        "scan=none last=v1.4.0",
        "doc-last=v2.29.2",
    ], _explain(proc)


def test_stale_active_rc_reset_writes_through_the_release_state_writer_from_an_install_root(
    tmp_path: pathlib.Path, kanban_root: pathlib.Path
) -> None:
    rs = kanban_root / "projects" / _project(kanban_root) / "release-state.md"
    rs.chmod(0o640)
    proc = _run(
        tmp_path,
        kanban_root,
        f"""\
        source "{kanban_root}/scripts/lib/overwatch-checks/check-stale-active-rc.sh"
        overwatch_backup_file() {{ echo "{tmp_path}/backup"; }}
        overwatch_log_action() {{ :; }}
        _CSARC_RS_FILE="{rs}"
        _CSARC_ACTIVE_RC=v2.30.0
        _csarc_do_reset
        """,
    )

    assert proc.returncode == 0, _explain(proc)
    after = rs.read_text(encoding="utf-8")
    assert after == (
        _RC_OPEN.replace("## Active RC\nv2.30.0\n", "## Active RC\nnone\n")
        .replace("## RC Opened At\n2026-01-05T03:00:00Z\n", "## RC Opened At\nnone\n")
        .replace("## RC Opened By Task\nCM-20260105-034-open-rc\n", "## RC Opened By Task\nnone\n")
    )
    assert read_state_section(str(rs), "Last Released") == "v2.29.2"
    assert rs.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in rs.parent.iterdir() if p.name.startswith(".release-state.")) == []


def test_dashboard_panes_read_release_state_from_an_install_root(kanban_root: pathlib.Path) -> None:
    dashboard = kanban_root / "scripts" / "dashboard"
    project = _project(kanban_root)
    env = clean_subprocess_env(kanban_root=kanban_root)
    args = ["--project", project, "--kanban-root", str(kanban_root)]

    data = run_dashboard_script(["bash", str(dashboard / "data.sh"), *args], env=env, timeout=120)
    assert data.returncode == 0, data.stderr
    pairs = dict(line.split("=", 1) for line in data.stdout.splitlines() if "=" in line)
    assert (pairs["ACTIVE_RC"], pairs["RC_OPENED_AT"]) == ("v2.30.0", "2026-01-05T03:00:00Z")

    window = run_dashboard_script(["bash", str(dashboard / "show-status-window.sh"), *args], env=env, timeout=120)
    assert window.returncode == 0, window.stderr
    assert "RC: v2.30.0 (" in window.stdout and " active)" in window.stdout, window.stdout

    multi = run_dashboard_script(
        ["bash", str(dashboard / "show-multi.sh"), "--kanban-root", str(kanban_root), "--mode", "header"],
        env=env,
        timeout=120,
    )
    assert multi.returncode == 0, multi.stderr
    rc_lines = [line.split("Last Released")[0].strip() for line in multi.stdout.splitlines() if "RC: " in line]
    assert rc_lines == ["RC: v2.30.0", "RC: none"], multi.stdout
