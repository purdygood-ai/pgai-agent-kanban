"""
test_projects_cfg_enabled.py
============================
projects.cfg ``enabled`` has one meaning everywhere: only ``false`` (any
case, surrounding whitespace ignored) disables a project; an absent key,
an empty value or ``true`` leaves it enabled; any other value leaves it
enabled and draws one warning.

Covered:
  1. The rule itself, in Python (``pgai_agent_kanban.lib.ini``:
     ``parse_project_enabled`` / ``project_enabled``) and bash
     (``projects_cfg_enabled`` in team/scripts/lib/projects.sh), on the same
     projects.cfg -- the two answer alike, and each warns once per process.
  2. ``why`` (ops/verdict.py) reports IDLE "disabled" exactly when the loops
     skip, and not for an unrecognised value.
  3. Read-only listings -- ``projects_cfg_list`` (the dashboards), the
     registry ``GET /projects`` reads -- still list a disabled project.
  4. The PM discovery loop (``pm-agent.sh --auto``) and
     ``overwatch-sweep.sh`` process the enabled project only, each with one
     skip line for the disabled one and no lock or state for it.

The wake loop itself (every agent's wake and the daily OVERWATCH wake) is
driven end to end in tests/integration/test_wake_skips_disabled_project.py.

Every fixture is a throwaway kanban root under pytest's tmp_path; discovery
is stubbed and no provider is reached. Nothing reads or writes the live
install.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from pgai_agent_kanban.api.routers.projects import _read_projects_registry
from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.ops.verdict import VERDICT_IDLE, resolve_project_verdict
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECTS_LIB = _TEAM_DIR / "scripts" / "lib" / "projects.sh"
_SKIP_TEXT = "project alpha: disabled in projects.cfg (enabled=false) — skipped"


def _root(tmp_path: pathlib.Path, alpha_enabled_line: str = "enabled=false") -> pathlib.Path:
    """A kanban root registering alpha (*alpha_enabled_line*) and beta (no enabled key)."""
    root = tmp_path / "kanban"
    for sub in ("tmp", "home", "logs", "locks"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text("[wake]\n", encoding="utf-8")
    (root / "projects.cfg").write_text(
        f"[project:alpha]\npriority=1\n{alpha_enabled_line}\n\n[project:beta]\npriority=2\n",
        encoding="utf-8",
    )
    for project in ("alpha", "beta"):
        proj = root / "projects" / project
        (proj / "tasks" / "queues").mkdir(parents=True)
        (proj / "project.cfg").write_text(f"[project]\nproject_name = {project}\n", encoding="utf-8")
    return root


def _env(root: pathlib.Path, **extra: str) -> dict:
    return build_scrubbed_env(
        home_dir=root / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            **extra,
        },
    )


def _bash(root: pathlib.Path, body: str, *, cwd: pathlib.Path = _TEAM_DIR, timeout: int = 120):
    return subprocess.run(
        ["bash", "-c", body], cwd=str(cwd), env=_env(root), capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(autouse=True)
def _fresh_python_warnings(monkeypatch):
    """Each test starts with no Python 'already warned' entries."""
    monkeypatch.setattr(ini, "_ENABLED_WARNED", set())


# ---------------------------------------------------------------------------
# 1. The rule, Python and bash alike
# ---------------------------------------------------------------------------

# (enabled line for alpha, enabled?, warns?)
_CASES = [
    ("enabled=false", False, False),
    ("enabled=False", False, False),
    ("enabled= false ", False, False),
    ("enabled=FALSE", False, False),
    ("enabled=true", True, False),
    ("enabled=True", True, False),
    ("enabled=", True, False),
    ("", True, False),
    ("enabled=no", True, True),
    ("enabled=0", True, True),
    ("enabled=yes", True, True),
]


@pytest.mark.parametrize(("line", "enabled", "warns"), _CASES, ids=[c[0] or "absent" for c in _CASES])
def test_python_and_bash_agree_on_the_enabled_rule(tmp_path, capsys, line, enabled, warns):
    root = _root(tmp_path, line)

    assert ini.project_enabled(root, "alpha") is enabled
    assert ini.project_enabled(root, "beta") is True
    py_warnings = capsys.readouterr().err.count("WARNING: enabled for 'alpha'")

    proc = _bash(root, f"source '{_PROJECTS_LIB}'\n"
                       "projects_cfg_enabled alpha && echo ALPHA=enabled || echo ALPHA=disabled\n"
                       "projects_cfg_enabled beta && echo BETA=enabled || echo BETA=disabled\n")
    assert proc.returncode == 0, proc.stderr
    assert f"ALPHA={'enabled' if enabled else 'disabled'}" in proc.stdout, proc.stdout
    assert "BETA=enabled" in proc.stdout, proc.stdout
    assert py_warnings == int(warns)
    assert proc.stderr.count("WARNING: enabled for 'alpha'") == int(warns), proc.stderr


def test_unregistered_project_and_missing_registry_read_as_enabled(tmp_path):
    root = _root(tmp_path)
    assert ini.project_enabled(root, "gamma") is True
    (root / "projects.cfg").unlink()
    assert ini.project_enabled(root, "alpha") is True
    proc = _bash(root, f"source '{_PROJECTS_LIB}'; projects_cfg_enabled alpha && echo ENABLED")
    assert "ENABLED" in proc.stdout, proc.stderr


def test_unrecognised_value_warns_once_per_process(tmp_path, capsys):
    root = _root(tmp_path, "enabled=no")

    for _ in range(3):
        assert ini.project_enabled(root, "alpha") is True
    proc = _bash(root, f"source '{_PROJECTS_LIB}'\n"
                       "for i in 1 2 3; do projects_cfg_enabled alpha; done; echo DONE\n")

    assert capsys.readouterr().err.count("WARNING: enabled for 'alpha' is 'no'") == 1
    assert proc.stderr.count("WARNING: enabled for 'alpha' is 'no'") == 1, proc.stderr
    assert "enabled=false to pause it" in proc.stderr, "the warning names the fix"


def test_skip_line_names_the_project_and_the_setting(tmp_path):
    proc = _bash(_root(tmp_path), f"source '{_PROJECTS_LIB}'; projects_cfg_disabled_skip_line alpha")
    assert proc.stdout.strip() == _SKIP_TEXT


# ---------------------------------------------------------------------------
# 2. why agrees with the loops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("line", "idle_disabled"), [
    ("enabled=false", True),
    ("enabled= False ", True),
    ("enabled=no", False),
    ("", False),
])
def test_why_reports_disabled_exactly_when_the_loops_skip(tmp_path, line, idle_disabled):
    root = _root(tmp_path, line)

    verdict = resolve_project_verdict(root, "alpha")

    disabled = verdict.verdict == VERDICT_IDLE and "disabled in projects.cfg" in verdict.cause
    assert disabled is idle_disabled, verdict
    assert disabled is (not ini.project_enabled(root, "alpha"))
    assert "disabled" not in resolve_project_verdict(root, "beta").cause


# ---------------------------------------------------------------------------
# 3. Read-only listings still show the disabled project
# ---------------------------------------------------------------------------


def test_listings_still_include_the_disabled_project(tmp_path):
    root = _root(tmp_path)

    assert [e.name for e in ini.projects_registry(root)] == ["alpha", "beta"]
    assert set(_read_projects_registry(root)) == {"alpha", "beta"}, "GET /projects reads this registry"
    proc = _bash(root, f"source '{_PROJECTS_LIB}'; projects_cfg_list")
    assert proc.stdout.split() == ["alpha", "beta"], "the dashboards list projects_cfg_list"


# ---------------------------------------------------------------------------
# 4. PM discovery loop and OVERWATCH sweep
# ---------------------------------------------------------------------------

_DISCOVERY_STUB = """\
#!/usr/bin/env bash
# Test stand-in for lib/discovery.sh: records which project the --auto loop
# ran discovery for and reports it idle. Never scans or locks anything.
discovery_run_pipeline() {
  echo "$1" >> "${KANBAN_ROOT}/discovered.log"
  DISCOVERY_LAST_STATUS=idle
}
"""


def test_pm_discovery_loop_runs_for_the_enabled_project_only(tmp_path):
    root = _root(tmp_path)
    for dirname in ("scripts", "pgai_agent_kanban", "pm-agent", "workflows", "config"):
        shutil.copytree(_TEAM_DIR / dirname, root / dirname)
    (root / "scripts" / "lib" / "discovery.sh").write_text(_DISCOVERY_STUB, encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(root / "scripts" / "pm-agent.sh"), "--auto"],
        cwd=str(root), env=_env(root), capture_output=True, text=True, timeout=180,
    )

    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert (root / "discovered.log").read_text(encoding="utf-8").split() == ["beta"], out
    assert out.count(_SKIP_TEXT) == 1, out


def test_overwatch_sweep_sweeps_the_enabled_project_only(tmp_path):
    root = _root(tmp_path)

    proc = _bash(root, "bash scripts/overwatch-sweep.sh --only block-triage", timeout=300)

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count(f"overwatch-sweep: {_SKIP_TEXT}") == 1, proc.stderr
    assert (root / "projects" / "beta" / "logs" / "overwatch" / "sweep.log").is_file(), proc.stderr
    assert not (root / "projects" / "alpha" / "overwatch").exists(), "no OVERWATCH state for a disabled project"
    assert not (root / "projects" / "alpha" / "logs").exists(), proc.stderr
    assert not list((root / "locks").glob("*alpha*")), "no firing lock for a disabled project"
    assert "complete — 1 project(s) swept" in proc.stderr, proc.stderr
