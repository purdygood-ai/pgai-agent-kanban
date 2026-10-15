"""
test_runner_targeted_files.py
=============================
Behavioural tests for the gated runners' ``--files`` targeted mode
(run-unit-tests.sh and run-integration-tests.sh).

WHAT A TARGETED RUN PROMISES
----------------------------
- pytest collects exactly the named test files;
- it prints the header line ``targeted run — not a certification run``;
- it writes its exit code to ``targeted-<suite>.exit`` under the temp root;
- it never touches the certification files TESTER reads -- the aggregate
  findings file (``lint/findings-<suite>.json`` and its ``.runid`` claim)
  and a ``<suite>.exit`` certification exit file -- whether or not they
  already exist;
- the fatal pre-flight gates (breakage-class, container-engine-free, and
  the unit runner's other fatal gates) still run; of the quality-class
  lints only the two per-file test-authoring lints run (over the named
  files -- see test_runner_targeted_file_lints.py), and no other does;
- a help invocation (``--help`` or ``-h``, alone or beside other flags) is
  not a run at all: it prints usage, exits 0, runs no gate, and leaves the
  findings file, its ``.runid`` claim and the green ledger byte-identical
  (and writes no ``targeted-<suite>.exit`` either);
- a path outside the runner's own test roots, a path that does not exist,
  ``--files`` with no path, and ``--files`` with ``--coverage`` are each
  refused with the dual-channel error/message/remedy triple.

WHY A FIXTURE TREE
------------------
Same reasoning as test_gated_runner_findings_on_failure.py: letting a
runner reach real pytest against the real team/tests/ tree from inside this
pytest process would recursively re-run the suite. Each case builds a
throwaway REPO_ROOT-shaped tree holding the REAL runner and the REAL
scripts/lib/ (copied byte-for-byte, so the shipped mechanism is what runs)
plus stand-ins for every lint/gate script the runner shells out to. Each
stand-in appends its own name to a log file before exiting, which is how
these tests observe which gates ran.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_HEADER = "targeted run — not a certification run"

# Fatal pre-flight gates each runner runs before pytest -- these must still
# run on a targeted run.
_FATAL_GATES = {
    "run-unit-tests.sh": [
        "check_breakage_class.py",
        "lint_dockerfile_static.py",
        "lint_container_engine_free.py",
        "lint_api_parity.py",
    ],
    "run-integration-tests.sh": [
        "check_breakage_class.py",
        "lint_container_engine_free.py",
    ],
}

# Quality-class lint scripts the runners invoke. On a targeted run only the
# two per-file test-authoring lints (_PER_FILE_LINTS) run, after the fatal
# gates; none of the others may. Explicit list (not derived from the runner
# source) so the intent is visible; a lint missing from here is simply not
# asserted on.
_QUALITY_PY_SCRIPTS = [
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
_QUALITY_SH_SCRIPTS = ["lint_skip_bug_gate.sh", "lint_governance_byte_budgets.sh"]
# The per-file test-authoring lints a targeted run applies to its named
# files, in the order the runners invoke them.
_PER_FILE_LINTS = ["lint_test_anti_patterns.py", "lint_skip_bug_gate.sh"]

_RUNNER_CONFIG = {
    "run-unit-tests.sh": {
        "suite": "unit",
        "test_dir": "tests/unit",
        "extra_dirs": ["pgai_agent_kanban/api/tests"],
    },
    "run-integration-tests.sh": {
        "suite": "integration",
        "test_dir": "tests/integration",
        "extra_dirs": [],
    },
}
_RUNNER_NAMES = list(_RUNNER_CONFIG)


# ---------------------------------------------------------------------------
# Fixture tree
# ---------------------------------------------------------------------------


def _write_logging_stub(path: pathlib.Path, name: str, *, exit_code: int = 0) -> None:
    """A gate/lint stand-in that records *name* in $GATE_LOG, then exits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".py":
        body = (
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "with open(os.environ['GATE_LOG'], 'a', encoding='utf-8') as fh:\n"
            f"    fh.write({name!r} + '\\n')\n"
            f"sys.exit({exit_code})\n"
        )
    else:
        body = (
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' {name!r} >> \"$GATE_LOG\"\n"
            f"exit {exit_code}\n"
        )
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _build_fixture(
    tmp_path: pathlib.Path, runner_name: str, *, breakage_fails: bool = False
) -> pathlib.Path:
    """Build the throwaway tree; return its team/ directory.

    Each runner's test root holds test_named.py (passes) and test_other.py
    (fails deliberately), so a run that collected anything beyond the named
    file would go red.
    """
    team = tmp_path / "fixture_repo" / "team"
    scripts = team / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(_SCRIPTS_DIR / runner_name, scripts / runner_name)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")

    for name in _FATAL_GATES["run-unit-tests.sh"] + _QUALITY_PY_SCRIPTS:
        failing = breakage_fails and name == "check_breakage_class.py"
        _write_logging_stub(scripts / name, name, exit_code=1 if failing else 0)
    for name in _QUALITY_SH_SCRIPTS:
        _write_logging_stub(scripts / name, name)

    config = _RUNNER_CONFIG[runner_name]
    for rel in [config["test_dir"], *config["extra_dirs"]]:
        (team / rel).mkdir(parents=True, exist_ok=True)
    test_dir = team / config["test_dir"]
    (test_dir / "test_named.py").write_text(
        "def test_named_passes():\n    assert True\n", encoding="utf-8"
    )
    (test_dir / "test_other.py").write_text(
        "def test_other_must_not_be_collected():\n"
        "    assert False, 'collected a file the targeted run did not name'\n",
        encoding="utf-8",
    )
    return team


class _Sandbox:
    """Throwaway kanban root, temp root and HOME for one runner invocation.

    The kanban root holds only a kanban.cfg stating [testing] workers = 1,
    so every fixture run is one single-process pytest run: the default
    budget would start pytest-xdist workers inside the suite running this
    test. test_runner_parallel_lanes.py covers the parallel lanes.
    """

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.kanban_root = tmp_path / "kanban_root"
        self.temp_dir = tmp_path / "temp_root"
        self.home = tmp_path / "home"
        self.gate_log = tmp_path / "gate.log"
        for d in (self.kanban_root, self.temp_dir, self.home):
            d.mkdir()
        self.gate_log.touch()
        (self.kanban_root / "kanban.cfg").write_text("[testing]\nworkers = 1\n", encoding="utf-8")

    def run(self, team: pathlib.Path, runner_name: str, *args: str) -> subprocess.CompletedProcess:
        env = build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.kanban_root),
                "KANBAN_ROOT": str(self.kanban_root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.temp_dir),
                "GATE_LOG": str(self.gate_log),
            },
        )
        return subprocess.run(
            ["bash", str(team / "scripts" / runner_name), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(team.parent),
        )

    def gates_run(self) -> list[str]:
        return [ln for ln in self.gate_log.read_text(encoding="utf-8").splitlines() if ln]


def _named(runner_name: str) -> str:
    return f"{_RUNNER_CONFIG[runner_name]['test_dir']}/test_named.py"


def _exit_file(sandbox: _Sandbox, runner_name: str) -> pathlib.Path:
    return sandbox.temp_dir / f"targeted-{_RUNNER_CONFIG[runner_name]['suite']}.exit"


def _certification_files(sandbox: _Sandbox, runner_name: str) -> list[pathlib.Path]:
    suite = _RUNNER_CONFIG[runner_name]["suite"]
    findings = sandbox.temp_dir / "lint" / f"findings-{suite}.json"
    return [findings, pathlib.Path(f"{findings}.runid"), sandbox.temp_dir / f"{suite}.exit"]


def _describe(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# ---------------------------------------------------------------------------
# Targeted run: collection, header, exit file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_collects_only_the_named_file(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _named(runner_name))

    assert result.returncode == 0, (
        "a targeted run of one passing file must exit 0; a non-zero exit "
        "here usually means test_other.py (deliberately failing) was "
        "collected too\n" + _describe(result)
    )
    assert "1 passed" in result.stdout, _describe(result)
    assert "test_other" not in result.stdout, (
        "pytest output names test_other.py, which the targeted run did not "
        "name\n" + _describe(result)
    )
    assert result.stdout.splitlines()[0] == _HEADER, (
        f"the first line of a targeted run must be the header {_HEADER!r}\n" + _describe(result)
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_writes_its_exit_code_to_the_targeted_exit_file(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    exit_file = _exit_file(sandbox, runner_name)

    passing = sandbox.run(team, runner_name, "--files", _named(runner_name))
    assert passing.returncode == 0, _describe(passing)
    assert exit_file.read_text(encoding="utf-8") == "0\n"

    failing_rel = f"{_RUNNER_CONFIG[runner_name]['test_dir']}/test_other.py"
    failing = sandbox.run(team, runner_name, "--files", failing_rel)
    assert failing.returncode == 1, _describe(failing)
    assert exit_file.read_text(encoding="utf-8") == "1\n", (
        "the targeted exit file must record the failing run's exit code"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_leaves_existing_certification_files_untouched(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    seeded = {}
    for path in _certification_files(sandbox, runner_name):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"certification content of {path.name}\n", encoding="utf-8")
        os.utime(path, (1_000_000_000, 1_000_000_000))
        seeded[path] = (path.read_bytes(), path.stat().st_mtime_ns)

    result = sandbox.run(team, runner_name, "--files", _named(runner_name))

    assert result.returncode == 0, _describe(result)
    for path, (content, mtime_ns) in seeded.items():
        assert path.read_bytes() == content, f"{path.name} content changed by a targeted run"
        assert path.stat().st_mtime_ns == mtime_ns, f"{path.name} mtime changed by a targeted run"
    assert "findings file written" not in result.stdout + result.stderr, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_creates_no_certification_files_when_absent(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _named(runner_name))

    assert result.returncode == 0, _describe(result)
    for path in _certification_files(sandbox, runner_name):
        assert not path.exists(), f"a targeted run created {path.name}"


# ---------------------------------------------------------------------------
# Help invocation: prints usage, writes nothing
# ---------------------------------------------------------------------------

_HELP_ARGV_CASES = [
    pytest.param(["--help"], id="long"),
    pytest.param(["-h"], id="short"),
    pytest.param(["--verbose", "--help"], id="long-after-flag"),
    pytest.param(["--files", "{named}", "-h"], id="short-after-files"),
]


def _green_ledger_file(sandbox: _Sandbox) -> pathlib.Path:
    """A ledger at the shape green_ledger.sh appends to:
    <temp-root>/projects/<project>/green-ledger/<rc>/ledger.jsonl."""
    return sandbox.temp_dir / "projects" / "pgai-agent-kanban" / "green-ledger" / "v9.9.9" / "ledger.jsonl"


def _snapshot_files(root: pathlib.Path) -> dict[pathlib.Path, tuple[bytes, int]]:
    return {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize("argv", _HELP_ARGV_CASES)
def test_help_leaves_findings_file_claim_and_ledger_byte_identical(
    runner_name: str, argv: list[str], tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    suite = _RUNNER_CONFIG[runner_name]["suite"]
    findings = sandbox.temp_dir / "lint" / f"findings-{suite}.json"
    seeds = {
        findings: '{"seed":1}\n',
        pathlib.Path(f"{findings}.runid"): "seeded-run-token\n",
        _green_ledger_file(sandbox): '{"commit": "seed", "suite": "%s", "exit": 0}\n' % suite,
    }
    for path, content in seeds.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (1_000_000_000, 1_000_000_000))
    before = _snapshot_files(sandbox.temp_dir)
    args = [a.format(named=_named(runner_name)) for a in argv]

    result = sandbox.run(team, runner_name, *args)

    assert result.returncode == 0, _describe(result)
    assert "Usage:" in result.stdout, _describe(result)
    assert "findings file written" not in result.stdout + result.stderr, (
        "a help invocation must not write the findings file\n" + _describe(result)
    )
    assert sandbox.gates_run() == [], "a help invocation must not run any gate or lint"
    after = _snapshot_files(sandbox.temp_dir)
    for path, (content, mtime_ns) in before.items():
        assert path in after, f"{path.name} was removed by {' '.join(args)}"
        assert after[path][0] == content, f"{path.name} content changed by {' '.join(args)}"
        assert after[path][1] == mtime_ns, f"{path.name} mtime changed by {' '.join(args)}"
    created = sorted(str(p.relative_to(sandbox.temp_dir)) for p in after.keys() - before.keys())
    assert created == [], f"{' '.join(args)} created files under the temp root: {created}"


# ---------------------------------------------------------------------------
# Which gates run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_keeps_fatal_gates_and_runs_only_the_per_file_lints(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _named(runner_name))

    assert result.returncode == 0, _describe(result)
    expected = _FATAL_GATES[runner_name] + _PER_FILE_LINTS
    assert sandbox.gates_run() == expected, (
        "a targeted run must run exactly the fatal pre-flight gates "
        f"{_FATAL_GATES[runner_name]} and then the per-file lints "
        f"{_PER_FILE_LINTS}, and no other quality-class lint; ran "
        f"{sandbox.gates_run()}"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_certification_run_still_runs_quality_lints(runner_name: str, tmp_path: pathlib.Path) -> None:
    """Control for the test above: the same logging stubs DO record the
    quality-class lints on a run without --files, so their absence on a
    targeted run is the targeted mode's doing, not a broken stub."""
    team = _build_fixture(tmp_path, runner_name)
    (team / _RUNNER_CONFIG[runner_name]["test_dir"] / "test_other.py").unlink()
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name)

    assert result.returncode == 0, _describe(result)
    ran = sandbox.gates_run()
    assert ran[: len(_FATAL_GATES[runner_name])] == _FATAL_GATES[runner_name], ran
    assert "lint_test_anti_patterns.py" in ran, ran
    assert (sandbox.temp_dir / "lint" / f"findings-{_RUNNER_CONFIG[runner_name]['suite']}.json").is_file()


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_targeted_run_still_stops_on_breakage_class_failure(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name, breakage_fails=True)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--files", _named(runner_name))

    assert result.returncode == 1, _describe(result)
    assert f"{runner_name.removesuffix('.sh')}.breakage-class-gate" in result.stderr, _describe(result)
    assert "passed" not in result.stdout, "pytest must not run after a breakage-class failure"
    assert _exit_file(sandbox, runner_name).read_text(encoding="utf-8") == "1\n"
    for path in _certification_files(sandbox, runner_name):
        assert not path.exists(), f"a refused targeted run created {path.name}"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

_REFUSAL_CASES = [
    pytest.param(["--files", "tests/../../etc/passwd"], "targeted-file-outside-test-root", id="outside-root"),
    pytest.param(["--files", "{test_dir}/test_absent.py"], "targeted-file-not-found", id="nonexistent"),
    pytest.param(["--files", "{test_dir}"], "targeted-file-not-found", id="directory"),
    pytest.param(["--files", "{named}", "--coverage"], "targeted-files-with-coverage", id="coverage-after"),
    pytest.param(["--coverage", "--files", "{named}"], "targeted-files-with-coverage", id="coverage-before"),
    pytest.param(["--files"], "targeted-files-missing", id="no-paths"),
]


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
@pytest.mark.parametrize(("argv", "key_suffix"), _REFUSAL_CASES)
def test_targeted_refusals_speak_the_dual_channel_contract(
    runner_name: str, argv: list[str], key_suffix: str, tmp_path: pathlib.Path
) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)
    args = [
        a.format(test_dir=_RUNNER_CONFIG[runner_name]["test_dir"], named=_named(runner_name))
        for a in argv
    ]

    result = sandbox.run(team, runner_name, *args)

    assert result.returncode != 0, _describe(result)
    expected_key = f"{runner_name.removesuffix('.sh')}.{key_suffix}"
    lines = result.stderr.splitlines()
    assert f"error:   {expected_key}" in lines, _describe(result)
    message = [ln for ln in lines if ln.startswith("message: ")]
    remedy = [ln for ln in lines if ln.startswith("remedy:  ")]
    assert message and message[0].strip() != "message:", _describe(result)
    assert remedy and f"{runner_name}\" --files " in remedy[0], (
        "the remedy must be the corrected runner command\n" + _describe(result)
    )
    assert "<" not in remedy[0], "a remedy must carry no angle-bracket placeholder"
    assert sandbox.gates_run() == [], "a refused targeted run must refuse before any gate runs"
    for path in _certification_files(sandbox, runner_name):
        assert not path.exists(), f"a refused targeted run created {path.name}"


def test_unit_runner_refuses_an_integration_test_path(tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, "run-unit-tests.sh")
    (team / "tests" / "integration").mkdir(parents=True)
    (team / "tests" / "integration" / "test_i.py").write_text("def test_i():\n    pass\n", encoding="utf-8")
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, "run-unit-tests.sh", "--files", "tests/integration/test_i.py")

    assert result.returncode != 0, _describe(result)
    assert "error:   run-unit-tests.targeted-file-outside-test-root" in result.stderr, _describe(result)


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_help_documents_files_flag(runner_name: str, tmp_path: pathlib.Path) -> None:
    team = _build_fixture(tmp_path, runner_name)
    sandbox = _Sandbox(tmp_path)

    result = sandbox.run(team, runner_name, "--help")

    assert result.returncode == 0, _describe(result)
    assert "--files PATH [PATH ...]" in result.stdout, _describe(result)
    assert f"targeted-{_RUNNER_CONFIG[runner_name]['suite']}.exit" in result.stdout, _describe(result)


# ---------------------------------------------------------------------------
# Path classification helper (team/scripts/lib/gated_runner_lint.sh)
# ---------------------------------------------------------------------------


def _resolve(team: pathlib.Path, path: str, *roots: str) -> tuple[int, str]:
    script = (
        'source "$1"; shift; '
        'out="$(gated_runner_targeted_resolve_path "$@")"; rc=$?; '
        'printf "%s %s" "$rc" "$out"'
    )
    result = subprocess.run(
        ["bash", "-c", script, "resolve", str(_SCRIPTS_DIR / "lib" / "gated_runner_lint.sh"), str(team), path, *roots],
        capture_output=True,
        text=True,
        timeout=30,
    )
    rc, _, out = result.stdout.partition(" ")
    return int(rc), out


def test_resolve_path_accepts_team_relative_repo_relative_and_absolute(tmp_path: pathlib.Path) -> None:
    team = tmp_path / "team"
    (team / "tests" / "unit").mkdir(parents=True)
    (team / "tests" / "unit" / "test_a.py").write_text("", encoding="utf-8")

    for form in ["tests/unit/test_a.py", "team/tests/unit/test_a.py", str(team / "tests/unit/test_a.py")]:
        assert _resolve(team, form, "tests/unit") == (0, "tests/unit/test_a.py"), form


def test_resolve_path_judges_a_symlink_by_where_it_points(tmp_path: pathlib.Path) -> None:
    team = tmp_path / "team"
    (team / "tests" / "unit").mkdir(parents=True)
    outside = tmp_path / "outside_test.py"
    outside.write_text("", encoding="utf-8")
    (team / "tests" / "unit" / "test_link.py").symlink_to(outside)

    rc, _ = _resolve(team, "tests/unit/test_link.py", "tests/unit")

    assert rc == 3, "a symlink under the test root that points outside it must classify as outside"
