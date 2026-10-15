"""
test_runner_green_ledger_reuse.py
=================================
Behavioural tests for the per-RC green ledger and ``--reuse-if-green`` on
both gated runners (run-unit-tests.sh and run-integration-tests.sh), whose
shared logic lives in team/scripts/lib/green_ledger.sh.

WHAT THE LEDGER PROMISES
------------------------
- every certification run (no ``--files``) appends one JSON record carrying
  at least ``commit``, ``suite``, ``exit`` and ``findings_sha`` to
  ``<temp-root>/projects/<project>/green-ledger/<active-rc>/ledger.jsonl``;
- ``--reuse-if-green`` at the same commit, with the same lint findings hash,
  after a green run, prints a line containing ``reused``, exits 0 and never
  starts pytest;
- a different HEAD, a different findings hash, a recorded non-zero exit, a
  different marker selection or a dirty tree each run pytest normally;
- a ``--files`` run leaves the ledger byte-identical;
- a missing or corrupt ledger falls back to a normal run after exactly one
  ``[green-ledger]`` narration line.

HOW PYTEST IS OBSERVED
----------------------
Each case builds a throwaway git repository holding the REAL runner and the
REAL scripts/lib/ (copied byte-for-byte), logging stand-ins for every lint
and gate script the runner shells out to, and a stub ``pytest`` package put
first on PYTHONPATH. ``python3 -m pytest`` therefore runs the stub, which
appends its argv to a call log and exits with ``PYTEST_STUB_EXIT`` (default
0). An empty call log after a run is the proof that pytest was not started.
Letting the runner reach real pytest from inside this pytest process would
recursively re-run the suite.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_PROJECT = "pgai-agent-kanban"

# Fatal pre-flight gates and quality-class lints the runners shell out to.
# Explicit list so the fixture states what it stands in for; the unit
# runner's set is a superset of the integration runner's.
_GATE_PY_SCRIPTS = [
    "check_breakage_class.py",
    "lint_dockerfile_static.py",
    "lint_container_engine_free.py",
    "lint_api_parity.py",
    "lint_test_anti_patterns.py",
    "lint_orphan_tests.py",
    "lint_operator_verb_manifest.py",
    "lint_upgrade_no_temp_copy_guard.py",
    "lint_icd_freshness.py",
    "lint_icd_compat.py",
    "lint_changelog_freshness.py",
    "lint_doc_command_truth.py",
    "lint_engine_size_dupe.py",
    "lint_env_bootstrap.py",
    "lint_help_presence.py",
    "lint_hygiene.py",
    "lint_python_import_completeness.py",
    "lint_dashboard_network_git_gate.py",
    "lint_dashboard_hardcode_colors.py",
    "lint_halt_scope_writers.py",
    "lint_raw_marker_read.py",
    "lint_status_writer_bypass.py",
    "lint_tester_report_census.py",
    "lint_docstring_coverage.py",
    "lint_dual_channel_pair.py",
    "lint_runnable_remedy.py",
    "lint_gate_census_compliance.py",
    "lint_block_record_sites.py",
    "lint_inline_python.py",
    "lint_docker_helper_path.py",
    "lint_python_duplicates.py",
    "lint_api_route_impl.py",
    "lint_api_route_docs.py",
    "lint_rc_empty_commits.py",
    "dashboard/generate_window_docs.py",
    "dashboard/lint_tmux_format_jobs.py",
    "generate_operator_verb_docs.py",
    "check_docs_citations.py",
]
_GATE_SH_SCRIPTS = ["lint_skip_bug_gate.sh", "lint_governance_byte_budgets.sh"]

# A quality-class lint both runners run; failing it changes the findings
# hash without touching anything fatal.
_TOGGLED_LINT = "lint_test_anti_patterns.py"

_RUNNER_CONFIG = {
    "run-unit-tests.sh": {
        "suite": "unit",
        "test_dirs": ["tests/unit", "pgai_agent_kanban/api/tests"],
    },
    "run-integration-tests.sh": {
        "suite": "integration",
        "test_dirs": ["tests/integration"],
    },
}
_RUNNER_NAMES = list(_RUNNER_CONFIG)

_STUB_PYTEST_MAIN = """\
import json
import os
import sys

# `--version` is the runner's availability probe, not a test run.
if sys.argv[1:] != ["--version"]:
    with open(os.environ["PYTEST_STUB_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("PYTEST_STUB_EXIT", "0")))
"""


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _write_gate_stub(path: pathlib.Path, name: str) -> None:
    """A gate/lint stand-in that exits 1 only when $FAIL_LINT names it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".py":
        body = (
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"sys.exit(1 if os.environ.get('FAIL_LINT') == {name!r} else 0)\n"
        )
    else:
        body = (
            "#!/usr/bin/env bash\n"
            f"[[ \"${{FAIL_LINT:-}}\" == {name!r} ]] && exit 1\n"
            "exit 0\n"
        )
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _git(repo: pathlib.Path, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(repo.parent / "git_home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
    }
    result = subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=True,
    )
    return result.stdout.strip()


class _Sandbox:
    """A committed fixture repo plus a throwaway kanban root, temp root and HOME."""

    def __init__(self, tmp_path: pathlib.Path, runner_name: str) -> None:
        self.runner_name = runner_name
        self.suite = _RUNNER_CONFIG[runner_name]["suite"]
        self.repo = tmp_path / "fixture_repo"
        self.team = self.repo / "team"
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.stub_dir = tmp_path / "stub_pythonpath"
        self.pytest_log = tmp_path / "pytest_calls.log"
        for d in (self.kanban_root, self.temp_dir, self.home, tmp_path / "git_home"):
            d.mkdir()

        scripts = self.team / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(_SCRIPTS_DIR / runner_name, scripts / runner_name)
        shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")
        # The ledger reads Active RC through the one release-state reader,
        # which lives in the package beside scripts/.
        package = _SCRIPTS_DIR.parent / "pgai_agent_kanban"
        for rel in ("__init__.py", "cm/__init__.py", "cm/read_state_field.py"):
            (self.team / "pgai_agent_kanban" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(package / rel, self.team / "pgai_agent_kanban" / rel)
        for name in _GATE_PY_SCRIPTS + _GATE_SH_SCRIPTS:
            _write_gate_stub(scripts / name, name)
        for rel in _RUNNER_CONFIG[runner_name]["test_dirs"]:
            (self.team / rel).mkdir(parents=True, exist_ok=True)
        first_dir = self.team / _RUNNER_CONFIG[runner_name]["test_dirs"][0]
        (first_dir / "test_sample.py").write_text("def test_sample():\n    pass\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        _git(self.repo, "init", "-q")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "fixture")

        (self.stub_dir / "pytest").mkdir(parents=True)
        (self.stub_dir / "pytest" / "__init__.py").write_text("", encoding="utf-8")
        (self.stub_dir / "pytest" / "__main__.py").write_text(_STUB_PYTEST_MAIN, encoding="utf-8")
        self.pytest_log.touch()

    # -- invocation -------------------------------------------------------

    def run(self, *args: str, pytest_exit: int = 0, fail_lint: str = "") -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "PGAI_PROJECT_NAME": _PROJECT,
                "PYTHONPATH": str(self.stub_dir),
                "PYTEST_STUB_LOG": str(self.pytest_log),
                "PYTEST_STUB_EXIT": str(pytest_exit),
                "FAIL_LINT": fail_lint,
            },
        )
        return subprocess.run(
            ["bash", str(self.team / "scripts" / self.runner_name), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(self.repo),
        )

    # -- observation ------------------------------------------------------

    def pytest_calls(self) -> list[list[str]]:
        return [json.loads(ln) for ln in self.pytest_log.read_text(encoding="utf-8").splitlines() if ln]

    def clear_pytest_calls(self) -> None:
        self.pytest_log.write_text("", encoding="utf-8")

    def ledger(self, rc_scope: str = "no-active-rc") -> pathlib.Path:
        return self.temp_dir / "projects" / _PROJECT / "green-ledger" / rc_scope / "ledger.jsonl"

    def records(self, rc_scope: str = "no-active-rc") -> list[dict]:
        return [json.loads(ln) for ln in self.ledger(rc_scope).read_text(encoding="utf-8").splitlines() if ln]

    def head(self) -> str:
        return _git(self.repo, "rev-parse", "HEAD")

    def test_file(self, name: str) -> str:
        return f"{_RUNNER_CONFIG[self.runner_name]['test_dirs'][0]}/{name}"


def _describe(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def _fallback_lines(result: subprocess.CompletedProcess) -> list[str]:
    return [
        ln for ln in result.stdout.splitlines()
        if ln.startswith("[green-ledger]") and "normally" in ln
    ]


@pytest.fixture(params=_RUNNER_NAMES)
def sandbox(request: pytest.FixtureRequest, tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path, request.param)


def _certify_green(sandbox: _Sandbox) -> None:
    """Run one green certification run and reset the pytest call log."""
    first = sandbox.run()
    assert first.returncode == 0, _describe(first)
    assert len(sandbox.pytest_calls()) == 1, "the certification run must start pytest once"
    sandbox.clear_pytest_calls()


# ---------------------------------------------------------------------------
# Record and reuse
# ---------------------------------------------------------------------------


def test_certification_run_appends_a_ledger_record(sandbox: _Sandbox) -> None:
    result = sandbox.run()

    assert result.returncode == 0, _describe(result)
    records = sandbox.records()
    assert len(records) == 1, records
    record = records[0]
    assert record["commit"] == sandbox.head()
    assert record["suite"] == sandbox.suite
    assert record["exit"] == 0
    assert len(record["findings_sha"]) == 64
    assert record["selection"] == "not quarantine"
    assert record["tree_clean"] is True


def test_reuse_if_green_returns_recorded_result_without_starting_pytest(sandbox: _Sandbox) -> None:
    _certify_green(sandbox)
    before = sandbox.ledger().read_bytes()

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert any("reused" in ln for ln in result.stdout.splitlines()), _describe(result)
    assert sandbox.pytest_calls() == [], "a reused run must not start pytest\n" + _describe(result)
    assert sandbox.ledger().read_bytes() == before, "a reused run appends no record"
    findings = sandbox.temp_dir / "lint" / f"findings-{sandbox.suite}.json"
    assert findings.is_file(), "a reused run still writes the findings file TESTER reads"


def test_different_head_commit_runs_pytest(sandbox: _Sandbox) -> None:
    _certify_green(sandbox)
    (sandbox.team / sandbox.test_file("test_added.py")).write_text("def test_added():\n    pass\n", encoding="utf-8")
    _git(sandbox.repo, "add", "-A")
    _git(sandbox.repo, "commit", "-q", "-m", "move HEAD")

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "a new HEAD must run pytest\n" + _describe(result)
    assert "reused" not in result.stdout, _describe(result)
    assert len(_fallback_lines(result)) == 1, _describe(result)


def test_different_findings_hash_runs_pytest(sandbox: _Sandbox) -> None:
    _certify_green(sandbox)

    result = sandbox.run("--reuse-if-green", fail_lint=_TOGGLED_LINT)

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "a changed findings hash must run pytest\n" + _describe(result)
    assert "findings hash differs" in result.stdout, _describe(result)


def test_recorded_nonzero_exit_runs_pytest(sandbox: _Sandbox) -> None:
    red = sandbox.run(pytest_exit=1)
    assert red.returncode == 1, _describe(red)
    assert sandbox.records()[-1]["exit"] == 1
    sandbox.clear_pytest_calls()

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "a recorded red run must not be reused\n" + _describe(result)
    assert "exited 1" in result.stdout, _describe(result)


def test_different_marker_selection_runs_pytest(sandbox: _Sandbox) -> None:
    _certify_green(sandbox)

    result = sandbox.run("--reuse-if-green", "--quarantined")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "a --quarantined run is not the recorded run\n" + _describe(result)


def test_dirty_tree_runs_pytest(sandbox: _Sandbox) -> None:
    _certify_green(sandbox)
    (sandbox.team / sandbox.test_file("test_sample.py")).write_text(
        "def test_sample():\n    assert 1\n", encoding="utf-8"
    )

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "uncommitted changes must run pytest\n" + _describe(result)


# ---------------------------------------------------------------------------
# --files never touches the ledger
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [[], ["--reuse-if-green"]], ids=["plain", "with-reuse-flag"])
def test_files_run_leaves_ledger_byte_identical(sandbox: _Sandbox, extra: list[str]) -> None:
    _certify_green(sandbox)
    ledger = sandbox.ledger()
    before = (ledger.read_bytes(), ledger.stat().st_mtime_ns)

    result = sandbox.run("--files", sandbox.test_file("test_sample.py"), *extra)

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, "a --files run always runs pytest\n" + _describe(result)
    assert (ledger.read_bytes(), ledger.stat().st_mtime_ns) == before, "a --files run changed the ledger"
    assert "reused" not in result.stdout, _describe(result)


def test_files_run_creates_no_ledger(sandbox: _Sandbox) -> None:
    result = sandbox.run("--files", sandbox.test_file("test_sample.py"))

    assert result.returncode == 0, _describe(result)
    assert not (sandbox.temp_dir / "projects").exists(), "a --files run created ledger state"


# ---------------------------------------------------------------------------
# Ledger problems never fail a run
# ---------------------------------------------------------------------------


def test_missing_ledger_falls_back_with_one_line(sandbox: _Sandbox) -> None:
    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, _describe(result)
    lines = _fallback_lines(result)
    assert len(lines) == 1 and "no ledger" in lines[0], _describe(result)
    assert len(sandbox.records()) == 1, "the fallback run is itself recorded"


def test_corrupt_ledger_falls_back_and_is_moved_aside(sandbox: _Sandbox) -> None:
    ledger = sandbox.ledger()
    ledger.parent.mkdir(parents=True)
    ledger.write_text("this is not json\n", encoding="utf-8")

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, _describe(result)
    lines = _fallback_lines(result)
    assert len(lines) == 1 and "not a ledger record" in lines[0], _describe(result)
    assert pathlib.Path(f"{ledger}.malformed").read_text(encoding="utf-8") == "this is not json\n"
    assert [r["exit"] for r in sandbox.records()] == [0], "a fresh ledger holds this run's record"


def test_unwritable_ledger_directory_does_not_fail_the_run(sandbox: _Sandbox) -> None:
    blocker = sandbox.temp_dir / "projects"
    blocker.write_text("a file where the ledger tree should be\n", encoding="utf-8")

    result = sandbox.run("--reuse-if-green")

    assert result.returncode == 0, _describe(result)
    assert len(sandbox.pytest_calls()) == 1, _describe(result)
    assert "could not append" in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# Location and --help
# ---------------------------------------------------------------------------


def test_ledger_is_scoped_to_the_active_rc(sandbox: _Sandbox) -> None:
    state = sandbox.kanban_root / "projects" / _PROJECT / "release-state.md"
    state.parent.mkdir(parents=True)
    state.write_text("# Release State\n\n## Active RC  \n\n v9.8.7\n\n## RC Opened At\nnone\n", encoding="utf-8")

    result = sandbox.run()

    assert result.returncode == 0, _describe(result)
    assert sandbox.records("v9.8.7")[0]["commit"] == sandbox.head()
    assert not sandbox.ledger().exists()


def test_help_documents_reuse_if_green(sandbox: _Sandbox) -> None:
    result = sandbox.run("--help")

    assert result.returncode == 0, _describe(result)
    assert "--reuse-if-green" in result.stdout, _describe(result)
    assert "green-ledger" in result.stdout, _describe(result)
    assert sandbox.pytest_calls() == []
