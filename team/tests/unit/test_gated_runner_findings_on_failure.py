"""
test_gated_runner_findings_on_failure.py
==========================================
Regression guard: run-unit-tests.sh and run-integration-tests.sh must write
their aggregate quality-class findings file
(${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-{unit,integration}.json) on
EVERY invocation that reached the quality-class lints, including one whose
pytest phase fails.

THE FAILURE MODE THIS GUARDS AGAINST
-------------------------------------
A runner that writes the findings file only near the bottom of the script,
after the pytest-failure `exit 1` (unit) / `exit "$PYTEST_EXIT"` (integration)
early-exit, leaves whatever a PRIOR invocation had written in place on a red
suite -- and since ${PGAI_AGENT_KANBAN_TEMP_DIR} is shared across every
worktree and project on the host, a downstream consumer (TESTER's lint
findings review) could attribute a different branch's lint findings to the
run under verification.

THE MECHANISM
-------------
Both runners register the write as a CLEANUP_ON_EXIT_PRE_HOOK on their own
EXIT trap (see team/scripts/lib/script_common.sh's cleanup_on_exit for the
unit runner; run-integration-tests.sh's own pytest_cleanup_on_exit for the
integration runner), set immediately after the last quality-class lint
runs -- so the hook fires on every exit path from that point forward.

WHY A FIXTURE TREE INSTEAD OF THE REAL REPO
--------------------------------------------
Actually letting a runner reach its own real `python3 -m pytest ...` line
against the real, large team/tests/ tree from inside THIS pytest process
would recursively re-collect and re-run the whole suite (including this
file) -- test_runner_preflight_remedies.py's module docstring documents the
same hazard and deliberately never lets a runner reach real pytest for that
reason. This suite avoids the hazard differently: it builds a small,
throwaway REPO_ROOT-shaped tree (a "fixture repo") with its own tiny
team/tests/unit (or team/tests/integration) directory holding exactly one
synthetic test, and invokes the runner as a real bash subprocess against
THAT tree. No recursion is possible because the fixture tree never contains
this test file.

The runner script itself and the whole scripts/lib/ directory are copied
byte-for-byte from the real checkout, so this suite always exercises the
actual shipped mechanism -- never a reimplementation that could drift from
it. Every OTHER script the runner shells out to (the breakage-class gate,
the Dockerfile static lint, and the ~17 quality-class lints) is replaced
with a trivial always-exit-0 stand-in: this suite is not about any of those
lints' own correctness (each already has its own test elsewhere); it is
about whether the findings file the runner writes describes the run that
just happened.

THE POST-SUITE BRACKET ON A RED RUN
-----------------------------------
The same fixture tree also proves the runners' post-suite live-root
sentinel check (team/scripts/lib/sentinel_leak_check.sh) runs on a
FAILING pytest phase, not only a passing one. A runner that acts on the
pytest verdict before the post-suite checks skips them exactly when a
stray fixture is most likely -- so a HALT the suite removed from (or
left on) the live root goes unreported. PGAI_AGENT_KANBAN_ROOT_PATH is
pointed at a throwaway directory for every case here, so the "live root"
the fixture copy of the runner guards is never the operator's install.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Every script either gated runner shells out to, other than the runner
# itself and scripts/lib/*.sh (which are copied real, not stubbed).
_STUB_PY_SCRIPTS = [
    "check_breakage_class.py",
    "lint_dockerfile_static.py",
    "lint_container_engine_free.py",
    "lint_test_anti_patterns.py",
    "lint_orphan_tests.py",
    "lint_api_parity.py",
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
    "lint_echo_pipe_grep_q.py",
    "lint_config_key_coverage.py",
    "lint_rc_empty_commits.py",
    "dashboard/generate_window_docs.py",
    "dashboard/lint_tmux_format_jobs.py",
    "generate_operator_verb_docs.py",
    "check_docs_citations.py",
]
_STUB_SH_SCRIPTS = ["lint_skip_bug_gate.sh", "lint_governance_byte_budgets.sh"]

_RUNNER_CONFIG = {
    "run-unit-tests.sh": {
        "findings_name": "findings-unit.json",
        "pytest_dirs": ["tests/unit", "pgai_agent_kanban/api/tests"],
        "failing_dir": "tests/unit",
    },
    "run-integration-tests.sh": {
        "findings_name": "findings-integration.json",
        "pytest_dirs": ["tests/integration"],
        "failing_dir": "tests/integration",
    },
}
_RUNNER_NAMES = list(_RUNNER_CONFIG)


# ---------------------------------------------------------------------------
# Fixture-tree construction
# ---------------------------------------------------------------------------


def _write_stub_py(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    path.chmod(0o755)


def _write_stub_sh(path: pathlib.Path) -> None:
    path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _write_breakage_gate_stub(path: pathlib.Path, *, fails: bool) -> None:
    """check_breakage_class.py stand-in: exit 0 normally, or exit 1 with a
    diagnostic line when *fails* -- used to force the breakage-class abort
    path without needing a real syntax error anywhere in the fixture tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fails:
        body = (
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('synthetic breakage-class failure', file=sys.stderr)\n"
            "sys.exit(1)\n"
        )
    else:
        body = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _build_fixture_repo(
    tmp_path: pathlib.Path,
    runner_name: str,
    *,
    deliberately_fail: bool,
    breakage_class_fails: bool = False,
    removes_root_sentinel: bool = False,
) -> pathlib.Path:
    """Build a throwaway REPO_ROOT-shaped tree under *tmp_path* and return it.

    Layout mirrors the real dev tree closely enough for the runner's own
    self-locate fallback (env_bootstrap.sh / dev_tree.sh) to resolve cleanly:
        <fixture_root>/team/scripts/<runner_name>   (real, copied verbatim)
        <fixture_root>/team/scripts/lib/            (real, copied verbatim)
        <fixture_root>/team/scripts/<stub-lint>.py  (synthetic, exit 0)
        <fixture_root>/team/<pytest_dir>/           (one synthetic test each)

    *breakage_class_fails* controls check_breakage_class.py specifically
    (see _write_breakage_gate_stub) -- forcing the fatal breakage-class
    pre-flight gate to abort the runner before pytest ever runs.

    *removes_root_sentinel* adds a second synthetic test that deletes the
    fleet HALT under $PGAI_AGENT_KANBAN_ROOT_PATH -- the throwaway root
    _run_runner_script points the runner at -- from inside the pytest
    session, simulating a fixture that resolved the live root instead of
    its own synthetic board. Arm the HALT with _arm_fleet_halt first.
    """
    fixture_root = tmp_path / "fixture_repo"
    scripts_dir = fixture_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)

    shutil.copy2(_SCRIPTS_DIR / runner_name, scripts_dir / runner_name)
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts_dir / "lib")

    for name in _STUB_PY_SCRIPTS:
        if name == "check_breakage_class.py":
            _write_breakage_gate_stub(scripts_dir / name, fails=breakage_class_fails)
        else:
            _write_stub_py(scripts_dir / name)
    for name in _STUB_SH_SCRIPTS:
        _write_stub_sh(scripts_dir / name)

    config = _RUNNER_CONFIG[runner_name]
    for rel_dir in config["pytest_dirs"]:
        (fixture_root / "team" / rel_dir).mkdir(parents=True, exist_ok=True)

    failing_dir = fixture_root / "team" / config["failing_dir"]
    test_body = (
        "def test_fixture_marker():\n    assert False, 'deliberate fixture failure'\n"
        if deliberately_fail
        else "def test_fixture_marker():\n    assert True\n"
    )
    (failing_dir / "test_fixture_marker.py").write_text(test_body, encoding="utf-8")

    if removes_root_sentinel:
        (failing_dir / "test_fixture_sentinel_mutation.py").write_text(
            "import os\n"
            "import pathlib\n"
            "\n"
            "\n"
            "def test_fixture_sentinel_mutation():\n"
            "    halt = pathlib.Path(os.environ['PGAI_AGENT_KANBAN_ROOT_PATH']) / 'HALT'\n"
            "    assert halt.is_file(), f'fixture precondition: {halt} must be armed'\n"
            "    halt.unlink()\n",
            encoding="utf-8",
        )

    return fixture_root


def _run_runner_script(
    fixture_root: pathlib.Path,
    runner_name: str,
    kanban_root: pathlib.Path,
    temp_dir: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Invoke the fixture copy of *runner_name* as a real bash subprocess.

    PGAI_AGENT_KANBAN_ROOT_PATH points at a fresh directory whose only
    file is a kanban.cfg stating [testing] workers = 1 (no [paths]
    dev_tree_path), matching test_runner_preflight_remedies.py's _run_runner
    helper -- the runner's self-locate fallback then resolves the dev tree
    to the fixture repo via its own on-disk location, never the live
    install or this real checkout. The stated budget keeps the fixture run
    single-process: the default would start pytest-xdist workers inside the
    suite running this test.
    """
    cfg = kanban_root / "kanban.cfg"
    if not cfg.exists():
        cfg.write_text("[testing]\nworkers = 1\n", encoding="utf-8")
    env = dict(os.environ)
    # PYTEST_ADDOPTS must NOT leak in from an enclosing pytest run (this test
    # itself may be running under run-unit-tests.sh, which exports its own
    # --basetemp=... into PYTEST_ADDOPTS). The fixture script appends its own
    # --basetemp rather than overwriting, so an inherited value here would
    # leave two --basetemp flags in effect; the first (the enclosing run's,
    # an ancestor of this fixture's own cwd) wins and pytest refuses to use a
    # basetemp that is an ancestor of the current working directory. The
    # fixture script builds a complete, self-contained pytest invocation of
    # its own, so no PYTEST_ADDOPTS should be inherited at all.
    env.pop("PYTEST_ADDOPTS", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_TEMP_DIR"] = str(temp_dir)
    script = fixture_root / "team" / "scripts" / runner_name
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _run_runner_script_without_pytest(
    fixture_root: pathlib.Path,
    runner_name: str,
    kanban_root: pathlib.Path,
    temp_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> subprocess.CompletedProcess:
    """Invoke the fixture copy of *runner_name* in an environment where
    pytest is unavailable -- forcing the "pytest is not installed" fatal
    pre-flight gate rather than a real assertion failure.

    Builds a tiny PATH-prepended shim directory holding a `python3` wrapper
    that delegates to the real interpreter for every invocation EXCEPT
    `-m pytest ...` (which it fails, simulating pytest being unimportable),
    and omits a `pytest` executable entirely so `command -v pytest` also
    fails -- both halves of the runner's own pytest-availability check
    (see run-unit-tests.sh / run-integration-tests.sh "Check pytest is
    available"). PATH itself is narrowed to this shim directory plus
    /usr/bin and /bin (git, coreutils, bash) so the real interpreter's own
    bin directory -- where a real `pytest` console script would also
    live -- is never reachable.
    """
    real_python3 = sys.executable
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    wrapper = fakebin / "python3"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "-m" && "$2" == "pytest" ]]; then\n'
        '  echo "fake python3: pytest not importable in this simulated environment" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec "{real_python3}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    env = {
        "PATH": f"{fakebin}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", ""),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    script = fixture_root / "team" / "scripts" / runner_name
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _arm_fleet_halt(kanban_root: pathlib.Path) -> pathlib.Path:
    """Arm a fleet-scoped HALT on the throwaway *kanban_root* and return its
    path -- the sentinel the pre-suite snapshot will record and the
    synthetic mutation test (see _build_fixture_repo) will remove."""
    halt = kanban_root / "HALT"
    halt.write_text("armed by the sentinel-bracket fixture\n", encoding="utf-8")
    return halt


def _seed_stale_findings_file(temp_dir: pathlib.Path, findings_name: str) -> pathlib.Path:
    """Pre-seed a findings file that looks like it was left behind by a
    different worktree's invocation on a different branch -- the exact
    shape a consumer could find on disk during verification if the write
    were skipped on a failing run."""
    findings_path = temp_dir / "lint" / findings_name
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    stale_doc = {
        "schema_version": 1,
        "generated_at": "2020-01-01T00:00:00+00:00",
        "runner": findings_name,
        "findings": [
            {
                "lint": "stale_finding_from_another_worktree",
                "severity": "quality",
                "summary": "left behind by a prior invocation on a different branch",
            }
        ],
    }
    findings_path.write_text(json.dumps(stale_doc), encoding="utf-8")
    return findings_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_findings_file_replaced_on_pytest_failure(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Regression guard for the acceptance-criteria case.

    Seeds a stale findings file, forces this run's own pytest phase to
    fail, and asserts the file was replaced by THIS run rather than left
    untouched. Against a runner whose pytest-failure exit skips the write,
    this test fails: the seeded stale content survives byte-for-byte.
    Against a runner that always writes the findings file before exiting,
    it passes.
    """
    fixture_root = _build_fixture_repo(tmp_path, runner_name, deliberately_fail=True)
    kanban_root = tmp_path / "safe_kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    config = _RUNNER_CONFIG[runner_name]
    findings_path = _seed_stale_findings_file(temp_dir, config["findings_name"])

    result = _run_runner_script(fixture_root, runner_name, kanban_root, temp_dir)

    assert result.returncode == 1, (
        f"a failing pytest phase must still exit 1 (the findings-file claim "
        f"leaves the exit status unchanged); got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert findings_path.is_file(), "the findings file must still exist after a failing run"

    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    assert doc["generated_at"] != "2020-01-01T00:00:00+00:00", (
        "generated_at must reflect THIS invocation, not the pre-seeded stale "
        "one -- an unchanged timestamp means the write never happened"
    )
    finding_lints = [f.get("lint") for f in doc["findings"]]
    assert "stale_finding_from_another_worktree" not in finding_lints, (
        "the stale finding from the pre-seeded file must be gone -- the file "
        "must describe THIS run's findings (none, since every lint in this "
        "fixture is a no-op stub), not a prior invocation's"
    )
    assert doc["findings"] == [], (
        "every quality-class lint in this fixture is a no-op stub, so a "
        "correctly-generated findings array is empty"
    )
    assert doc["runner"] == runner_name

    assert "findings file written:" in result.stdout, (
        "the '[gated-runner] findings file written: ...' narration line must "
        "print on the failure path exactly as it does on the success path"
    )
    assert str(findings_path) in result.stdout, (
        "the narration line must name the file that was actually written"
    )
    assert "set unchanged by the suite" in result.stdout, (
        "the post-suite sentinel check must still run and print its "
        "'[pgai-sentinel] ... set unchanged by the suite' verdict on a "
        "failing pytest phase -- a runner that exits on the pytest failure "
        "before the check skips it exactly when a stray fixture is most likely"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_findings_file_still_written_on_pytest_success(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Regression guard: moving the write from an unconditional bottom-of-
    script call to a CLEANUP_ON_EXIT_PRE_HOOK must not break the pre-existing
    success-path write."""
    fixture_root = _build_fixture_repo(tmp_path, runner_name, deliberately_fail=False)
    kanban_root = tmp_path / "safe_kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    config = _RUNNER_CONFIG[runner_name]
    findings_path = _seed_stale_findings_file(temp_dir, config["findings_name"])

    result = _run_runner_script(fixture_root, runner_name, kanban_root, temp_dir)

    assert result.returncode == 0, (
        f"a passing pytest phase must still exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    assert doc["generated_at"] != "2020-01-01T00:00:00+00:00"
    assert doc["findings"] == []
    assert "findings file written:" in result.stdout
    assert "set unchanged by the suite" in result.stdout, (
        "a green run with an untouched sentinel set still prints the "
        "'[pgai-sentinel] ... set unchanged by the suite' verdict line"
    )


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_sentinel_removal_reported_on_pytest_failure(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """A failing suite that also removed the live root's fleet HALT must
    still print the '[pgai-sentinel] REMOVED: <path>' narration, record a
    sentinel_leak finding in the findings file, and exit with pytest's own
    failure code.

    Against a runner that exits on the pytest failure before its post-suite
    checks, this test fails: no sentinel line prints and the findings array
    is empty even though the HALT is gone. The check is report-only, so the
    removed HALT is asserted to stay removed -- the runner narrates the
    mutation and never repairs it.
    """
    fixture_root = _build_fixture_repo(
        tmp_path, runner_name, deliberately_fail=True, removes_root_sentinel=True
    )
    kanban_root = tmp_path / "safe_kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    halt = _arm_fleet_halt(kanban_root)

    config = _RUNNER_CONFIG[runner_name]
    findings_path = _seed_stale_findings_file(temp_dir, config["findings_name"])

    result = _run_runner_script(fixture_root, runner_name, kanban_root, temp_dir)

    assert result.returncode == 1, (
        f"a failing pytest phase must still exit 1 -- the sentinel check is "
        f"report-only and never changes the runner's exit code; got "
        f"{result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert not halt.exists(), (
        "the synthetic mutation test must have removed the HALT, and the "
        "runner must not have restored it -- the check reports, never repairs"
    )
    assert f"[pgai-sentinel] REMOVED: {halt}" in result.stdout, (
        "the post-suite sentinel check must run on a failing suite and name "
        f"the removed path\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "sentinel_leak: quality-class finding(s)" in result.stderr, (
        "the gated-runner wrapper must narrate the sentinel finding as "
        "non-blocking rather than aborting on it"
    )

    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    assert doc["generated_at"] != "2020-01-01T00:00:00+00:00"
    sentinel_findings = [f for f in doc["findings"] if f.get("lint") == "sentinel_leak"]
    assert len(sentinel_findings) == 1, (
        "exactly one sentinel_leak finding must land in the findings file on "
        f"a failing suite that mutated the sentinel set; got {doc['findings']!r}"
    )
    assert sentinel_findings[0]["severity"] == "quality"
    assert str(halt) in sentinel_findings[0].get("detail", ""), (
        "the finding's detail must carry the removed sentinel's path"
    )


# ---------------------------------------------------------------------------
# The two fatal pre-flight gates that sit BEFORE the quality-class lint
# block (breakage-class, pytest-missing) -- the exit paths the findings-
# file write previously never reached at all, because the exit-time write
# hook used to be registered only after every quality-class lint had
# already run.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_findings_file_replaced_on_forced_breakage_class_abort(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Guards against a breakage-class abort leaving a stale findings file.

    Seeds a stale findings file, then forces the breakage-class pre-flight
    gate itself to fail (rather than a pytest assertion) -- a fatal exit
    that a runner can reach before the findings-file write is ever
    registered. Against a runner that registers the findings-file write
    after the breakage gate, this test fails: the seeded stale content
    survives byte-for-byte, because the script exits before
    CLEANUP_ON_EXIT_PRE_HOOK is even assigned. Against a runner that
    installs the claim before either fatal gate it passes: the claim means
    the file is cleared for this run before the abort can happen, and the
    exit-time hook then records a "runner_incomplete" finding describing
    why this run has no quality-class findings -- distinguishing an
    honestly-aborted run from a clean pass that also produced zero
    findings.
    """
    fixture_root = _build_fixture_repo(
        tmp_path, runner_name, deliberately_fail=False, breakage_class_fails=True
    )
    kanban_root = tmp_path / "safe_kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    config = _RUNNER_CONFIG[runner_name]
    findings_path = _seed_stale_findings_file(temp_dir, config["findings_name"])

    result = _run_runner_script(fixture_root, runner_name, kanban_root, temp_dir)

    assert result.returncode == 1, (
        f"a forced breakage-class abort must still exit 1 (the findings-file "
        f"claim leaves the exit status unchanged); got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "synthetic breakage-class failure" in result.stderr, (
        "the breakage-class gate's own diagnostic must still print exactly, "
        "unaltered by the findings-file claim"
    )
    assert "error:   run-" in result.stderr and "breakage-class-gate" in result.stderr, (
        "the dual-channel error contract's machine key must still print "
        "unchanged"
    )

    assert findings_path.is_file(), "the findings file must exist after a forced abort"
    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    assert doc["generated_at"] != "2020-01-01T00:00:00+00:00", (
        "generated_at must reflect THIS invocation -- an unchanged timestamp "
        "means the findings-file write was skipped on this abort path"
    )
    finding_lints = [f.get("lint") for f in doc["findings"]]
    assert "stale_finding_from_another_worktree" not in finding_lints, (
        "the stale finding from the pre-seeded file must be gone"
    )
    assert finding_lints == ["runner_incomplete"], (
        "a run aborted at the breakage-class gate must carry exactly one "
        "runner_incomplete finding recording why -- this is what makes the "
        "file distinguishable from a clean pass with zero findings, which "
        "would otherwise be byte-identical in the findings array"
    )
    assert "breakage-class pre-flight gate" in doc["findings"][0]["summary"]


@pytest.mark.parametrize("runner_name", _RUNNER_NAMES)
def test_findings_file_replaced_on_forced_pytest_missing_abort(
    runner_name: str, tmp_path: pathlib.Path
) -> None:
    """Same regression guard as the breakage-class case above, for the
    OTHER fatal pre-flight gate that sits before the quality-class lint
    block: pytest itself being unavailable."""
    fixture_root = _build_fixture_repo(tmp_path, runner_name, deliberately_fail=False)
    kanban_root = tmp_path / "safe_kanban_root"
    kanban_root.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    config = _RUNNER_CONFIG[runner_name]
    findings_path = _seed_stale_findings_file(temp_dir, config["findings_name"])

    result = _run_runner_script_without_pytest(
        fixture_root, runner_name, kanban_root, temp_dir, tmp_path
    )

    assert result.returncode == 2, (
        f"a forced pytest-missing abort must still exit 2 (the findings-file "
        f"claim leaves the exit status unchanged); got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "pytest is not installed" in result.stderr, (
        "the pytest-missing gate's own diagnostic must still print exactly, "
        "unaltered by the findings-file claim"
    )

    assert findings_path.is_file(), "the findings file must exist after a forced abort"
    doc = json.loads(findings_path.read_text(encoding="utf-8"))
    assert doc["generated_at"] != "2020-01-01T00:00:00+00:00", (
        "generated_at must reflect THIS invocation -- an unchanged timestamp "
        "means the findings-file write was skipped on this abort path"
    )
    finding_lints = [f.get("lint") for f in doc["findings"]]
    assert "stale_finding_from_another_worktree" not in finding_lints
    assert finding_lints == ["runner_incomplete"], (
        "a run aborted because pytest is missing must carry exactly one "
        "runner_incomplete finding recording why"
    )
    assert "pytest is not installed" in doc["findings"][0]["summary"]
