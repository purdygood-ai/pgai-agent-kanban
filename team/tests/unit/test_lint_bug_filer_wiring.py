"""
test_lint_bug_filer_wiring.py
================================
End-to-end regression guard for the hygiene lint's aggregate bug filer
being WIRED IN to a gated runner (run-integration-tests.sh's own
findings-file write hook -- see team/scripts/lib/lint_bug_filer.sh's call
site there and in run-unit-tests.sh's matching hook).

THE TWO ACCEPTANCE-CRITERIA CASES THIS GUARDS
------------------------------------------------
1. A runner seeded with a real hygiene-lint finding still exits 0, and the
   finding is turned into exactly one filed bug in the resolved project's
   bugs/ directory.
2. A filer made to fail deliberately still leaves the runner's exit code
   unchanged -- the wiring must never turn a filer-internal failure into a
   test-suite failure.

WHY A FIXTURE TREE, AND WHY A FULLER ONE THAN
test_gated_runner_findings_on_failure.py's
-----------------------------------------------
That sibling suite stubs every lint script (including lint_hygiene.py)
with a trivial always-exit-0 stand-in, because it is testing the findings-
file WRITE mechanism, not any one lint's own behavior. This suite needs
the opposite for lint_hygiene.py specifically -- its REAL implementation,
scanning a REAL seeded violation -- so the findings file this suite's
fixture produces carries a genuine "class"-bearing hygiene finding for the
aggregate filer to act on. It also needs pgai_agent_kanban/ and pm-agent/
present in the fixture tree (not stubbed away) because
team/scripts/lib/lint_bug_filer.sh invokes the real
pgai_agent_kanban.lint.bug_filer module via team/scripts/lib/pp_run_ops.sh,
which resolves its PYTHONPATH from the own-tree root derived from its own
on-disk location -- inside the fixture tree, that means the package must
actually be there for the import to succeed, exactly as it must be in a
real checkout.

Only run-integration-tests.sh is exercised here (not run-unit-tests.sh):
its own pytest surface is a single directory (tests/integration/) with no
dependency on the real pgai_agent_kanban/api/tests/ content the unit
runner's PYTEST_ARGS also names, keeping this fixture smaller without
weakening the wiring assertion -- the write hook that calls
lint_bug_filer_file is structurally identical in both runners (see each
one's own comment at the call site).

NO RECURSION HAZARD: the fixture's own tests/integration/ directory holds
exactly one synthetic passing test, never this test file itself, so
running the fixture's pytest never re-collects the suite this file lives
in (same reasoning as test_gated_runner_findings_on_failure.py's own
module docstring).

Test names describe behaviour under test, never bug/task IDs (team/roles/
TESTER.md, Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_RUNNER_NAME = "run-integration-tests.sh"

# Every script run-integration-tests.sh shells out to, other than the
# runner itself, scripts/lib/*.sh (copied real), and lint_hygiene.py
# (copied real -- see module docstring for why).
_STUB_PY_SCRIPTS = [
    "check_breakage_class.py",
    "lint_container_engine_free.py",
    "lint_test_anti_patterns.py",
    "lint_icd_freshness.py",
    "lint_icd_compat.py",
    "generate_operator_verb_docs.py",
    "lint_changelog_freshness.py",
    "lint_engine_size_dupe.py",
    "lint_env_bootstrap.py",
    "lint_help_presence.py",
    "lint_dashboard_network_git_gate.py",
    "lint_rc_empty_commits.py",
    "lint_dual_channel_pair.py",
    "lint_inline_python.py",
    "lint_docker_helper_path.py",
    "lint_python_duplicates.py",
    "lint_api_route_impl.py",
]
_STUB_SH_SCRIPTS = ["lint_governance_byte_budgets.sh"]

_SEEDED_VIOLATION_FILE = "_wiring_test_seed.py"
_SEEDED_VIOLATION_CONTENT = "# See BUG-0001 for background on this fixture.\n"


def _write_stub_py(path: pathlib.Path) -> None:
    path.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    path.chmod(0o755)


def _write_stub_sh(path: pathlib.Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _build_fixture_repo(tmp_path: pathlib.Path, *, seed_hygiene_violation: bool) -> pathlib.Path:
    """Build a throwaway REPO_ROOT-shaped tree under *tmp_path* and return
    it. Layout:
        <fixture_root>/team/scripts/run-integration-tests.sh  (real, copied)
        <fixture_root>/team/scripts/lib/                       (real, copied)
        <fixture_root>/team/scripts/lint_hygiene.py             (real, copied)
        <fixture_root>/team/scripts/<stub-lint>.py/.sh          (synthetic, exit 0)
        <fixture_root>/team/pgai_agent_kanban/                  (real, copied)
        <fixture_root>/team/pm-agent/                           (real, copied)
        <fixture_root>/team/tests/integration/                  (one synthetic passing test)

    The tree is committed as a throwaway git repository: the runner
    self-locates its dev tree to this fixture root, and the bug filer files
    nothing for a tree it cannot name a commit for.
    """
    fixture_root = tmp_path / "fixture_repo"
    scripts_dir = fixture_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)

    shutil.copy2(_SCRIPTS_DIR / _RUNNER_NAME, scripts_dir / _RUNNER_NAME)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts_dir / "lib")
    shutil.copy2(_SCRIPTS_DIR / "lint_hygiene.py", scripts_dir / "lint_hygiene.py")

    for name in _STUB_PY_SCRIPTS:
        _write_stub_py(scripts_dir / name)
    for name in _STUB_SH_SCRIPTS:
        _write_stub_sh(scripts_dir / name)

    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", fixture_root / "team" / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "pm-agent", fixture_root / "team" / "pm-agent")

    if seed_hygiene_violation:
        (fixture_root / "team" / "pgai_agent_kanban" / _SEEDED_VIOLATION_FILE).write_text(
            _SEEDED_VIOLATION_CONTENT, encoding="utf-8"
        )

    integration_dir = fixture_root / "team" / "tests" / "integration"
    integration_dir.mkdir(parents=True)
    (integration_dir / "test_fixture_marker.py").write_text(
        "def test_fixture_marker():\n    assert True\n", encoding="utf-8"
    )

    git = ["git", "-C", str(fixture_root), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture tree"]):
        subprocess.run([*git, *args], check=True, capture_output=True)

    return fixture_root


def _run_runner_script(
    fixture_root: pathlib.Path, kanban_root: pathlib.Path, temp_dir: pathlib.Path
) -> subprocess.CompletedProcess:
    """Invoke the fixture copy of run-integration-tests.sh as a real bash
    subprocess. PGAI_AGENT_KANBAN_ROOT_PATH points at a fresh directory
    whose only file is a kanban.cfg stating [testing] workers = 1 (no
    [paths] dev_tree_path) -- the runner's self-locate fallback then
    resolves the dev tree to the fixture repo via its own on-disk location,
    never the live install or this real checkout, and the fixture run stays
    single-process (matches test_gated_runner_findings_on_failure.py's own
    _run_runner_script)."""
    cfg = kanban_root / "kanban.cfg"
    if not cfg.exists():
        cfg.write_text("[testing]\nworkers = 1\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    script = fixture_root / "team" / "scripts" / _RUNNER_NAME
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


# ---------------------------------------------------------------------------
# Case 1: a runner seeded with a real hygiene finding still exits 0, and the
# finding is turned into exactly one filed bug.
# ---------------------------------------------------------------------------


def test_runner_exits_zero_and_files_one_bug_when_seeded_with_a_hygiene_finding(
    tmp_path: pathlib.Path,
) -> None:
    fixture_root = _build_fixture_repo(tmp_path, seed_hygiene_violation=True)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    result = _run_runner_script(fixture_root, kanban_root, temp_dir)

    assert result.returncode == 0, (
        f"a runner seeded with a hygiene finding must still exit 0 -- the "
        f"hygiene lint is quality-class, never blocking\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    findings_path = temp_dir / "lint" / "findings-integration.json"
    assert findings_path.is_file(), "the aggregate findings file must exist after the run"
    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    hygiene_findings = [f for f in doc["findings"] if f.get("lint") == "hygiene"]
    assert hygiene_findings, "the seeded violation must produce at least one hygiene finding"
    assert any(f.get("class") == "provenance" for f in hygiene_findings)

    bugs_dir = kanban_root / "projects" / "pgai-agent-kanban" / "bugs"
    filed_bugs = sorted(bugs_dir.glob("BUG-*.md")) if bugs_dir.is_dir() else []
    assert len(filed_bugs) == 1, (
        f"exactly one aggregated bug should be filed for the single seeded "
        f"class; found {[p.name for p in filed_bugs]}"
    )
    content = filed_bugs[0].read_text(encoding="utf-8")
    assert "## Hygiene Lint Class\nprovenance" in content
    assert _SEEDED_VIOLATION_FILE in content


# ---------------------------------------------------------------------------
# Case 2: a filer made to fail deliberately still leaves the runner's exit
# code unchanged.
# ---------------------------------------------------------------------------


def test_a_deliberately_broken_filer_does_not_change_the_runners_exit_code(
    tmp_path: pathlib.Path,
) -> None:
    fixture_root = _build_fixture_repo(tmp_path, seed_hygiene_violation=False)

    # Break the filer module itself: every invocation of
    # "python3 -m pgai_agent_kanban.lint.bug_filer ..." (what
    # lint_bug_filer_file's pp_run_ops call runs) now exits non-zero
    # unconditionally, simulating an internal filer failure.
    broken_filer = fixture_root / "team" / "pgai_agent_kanban" / "lint" / "bug_filer.py"
    broken_filer.write_text(
        "import sys\n"
        "sys.stderr.write('deliberately broken filer for wiring test\\n')\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    result = _run_runner_script(fixture_root, kanban_root, temp_dir)

    assert result.returncode == 0, (
        f"a deliberately failing filer must never change the runner's own "
        f"exit code\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )

    # The runner's own quality-class findings-file contract is unaffected:
    # the file still exists and still describes this run.
    findings_path = temp_dir / "lint" / "findings-integration.json"
    assert findings_path.is_file()

    # No bug was filed, since the filer never ran to completion -- and
    # critically, that absence did not surface as a test failure.
    bugs_dir = kanban_root / "projects" / "pgai-agent-kanban" / "bugs"
    assert not bugs_dir.is_dir() or not list(bugs_dir.glob("BUG-*.md"))
