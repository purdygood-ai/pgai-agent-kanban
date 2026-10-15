"""
test_gated_runner_per_run_basetemp.py
======================================
Every runner that hands pytest a --basetemp -- the two certifying suites
(run-unit-tests.sh, run-integration-tests.sh) and their two siblings
(run-gated-tests.sh, run-cold-build-tests.sh) -- must hand it one that
belongs to that one invocation alone.
pytest removes and recreates whatever --basetemp it is given at session
start, so two invocations of the same runner sharing one fixed path on one
host delete each other's live fixture directories, and whichever run is
still in flight fails on a different tmp_path-backed test each time.

The mechanism under test is the pair of helpers in
team/scripts/lib/gated_runner_lint.sh that all four runners share:
gated_runner_pytest_basetemp claims a fresh directory under
${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/, and
gated_runner_pytest_basetemp_cleanup releases it from the runner's exit
hook so the temp root never accumulates one directory per historical run.

Coverage:
  - Behavioral: two invocations of the claim helper, in the same process
    and in two separate processes, resolve two different directories, both
    of which exist and both of which sit under <temp root>/pytest/.
  - Behavioral: the on-disk label carries PGAI_TASK_ID when it is set and
    the caller's pid otherwise, so a directory seen on disk names what made
    it.
  - Behavioral: releasing one invocation's basetemp leaves a sibling
    invocation's basetemp untouched, is a no-op for an empty or absent
    path, and never returns non-zero -- the runner's exit code must not
    depend on scratch removal.
  - Behavioral: a runner-shaped shell that claims a basetemp and registers
    the release on its EXIT trap leaves nothing behind after exiting, with
    its own exit code preserved -- the prevention, exercised once.
  - Behavioral: the claim helper refuses, naming temp.sh, when the temp
    resolver is not sourced -- it never falls back to a guessed root.
  - Behavioral: each sibling runner (run-gated-tests.sh,
    run-cold-build-tests.sh), run end to end, hands pytest a basetemp that
    exists under <temp root>/pytest/ while the session is live and is gone
    once the runner exits -- on a zero and on a non-zero pytest exit -- and
    two overlapping invocations of the same sibling resolve distinct
    directories with neither removing the other's. These run the real
    runner scripts against a throwaway kanban root, with a python3 stand-in
    on PATH that plays the pytest session: it records the --basetemp it was
    handed and exits with the code the test chose. No container engine is
    needed (the cold-build lane's engine gating sits inside the pytest
    session the stand-in replaces), and no nested pytest ever starts, so
    the suite cannot recurse into itself.
  - Static: each of the four runners claims its basetemp through the shared
    helper with its own suite name, releases it from its exit hook, and no
    longer derives a fixed per-suite path -- catches a future edit that
    reverts any one of them to a shared basetemp.

Test names describe behaviour under test (team/roles/TESTER.md, Test
Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import sys

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RUN_UNIT_SH = _TEAM_DIR / "scripts" / "run-unit-tests.sh"
_RUN_INTEGRATION_SH = _TEAM_DIR / "scripts" / "run-integration-tests.sh"
_RUN_GATED_SH = _TEAM_DIR / "scripts" / "run-gated-tests.sh"
_RUN_COLD_BUILD_SH = _TEAM_DIR / "scripts" / "run-cold-build-tests.sh"

_TEMP_LIB = "scripts/lib/temp.sh"
_LIB = "scripts/lib/gated_runner_lint.sh"


def _source_both(snippet: str) -> str:
    """Bash snippet that sources temp.sh then gated_runner_lint.sh, in the
    order both real runners do, then runs *snippet* under the runners' own
    strict mode."""
    return f"set -euo pipefail\nsource {_TEMP_LIB}\nsource {_LIB}\n{snippet}"


def _temp_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """A private framework temp root for one test, so nothing here touches
    the live host-wide root and PGAI_TASK_ID inherited from a wake does not
    leak into the label assertions."""
    root = tmp_path / "framework_tmp"
    root.mkdir()
    return {"PGAI_AGENT_KANBAN_TEMP_DIR": str(root), "PGAI_TASK_ID": ""}


def _claim_lines(result) -> list[str]:
    assert result.returncode == 0, (
        f"helper invocation failed (rc={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Distinct directories per invocation
# ---------------------------------------------------------------------------


def test_two_claims_in_one_process_resolve_distinct_dirs_under_pytest_subtree(
    tmp_path: pathlib.Path,
) -> None:
    env = _temp_env(tmp_path)
    result = run_bash(
        tmp_path,
        _source_both(
            "gated_runner_pytest_basetemp unit\n"
            "gated_runner_pytest_basetemp unit\n"
        ),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    first, second = _claim_lines(result)
    expected_parent = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest"

    assert first != second, (
        "two invocations must never share a basetemp -- pytest removes and "
        f"recreates it at session start; both resolved to {first}"
    )
    for path in (first, second):
        assert pathlib.Path(path).is_dir(), f"claimed basetemp does not exist: {path}"
        assert pathlib.Path(path).parent == expected_parent, (
            f"basetemp {path} must sit directly under the framework temp "
            f"root's pytest/ subtree ({expected_parent}); the Temporary File "
            "Convention makes that root the parent of every basetemp"
        )
        assert pathlib.Path(path).name.startswith("unit-"), (
            f"basetemp {path} must be labelled with the suite that claimed it"
        )


def test_two_separate_runner_processes_resolve_distinct_dirs(tmp_path: pathlib.Path) -> None:
    """The concurrency shape the runners actually hit: two independent
    shells, same temp root, same suite, same (empty) task id."""
    env = _temp_env(tmp_path)
    snippet = _source_both("gated_runner_pytest_basetemp integration\n")
    first = _claim_lines(run_bash(tmp_path, snippet, extra_env=env, cwd=_TEAM_DIR))[0]
    second = _claim_lines(run_bash(tmp_path, snippet, extra_env=env, cwd=_TEAM_DIR))[0]

    assert first != second, (
        f"two runner processes resolved the same basetemp {first}; the second "
        "run's pytest startup would delete the first run's live fixtures"
    )
    assert pathlib.Path(first).is_dir() and pathlib.Path(second).is_dir()


def test_two_claims_under_the_same_task_id_still_resolve_distinct_dirs(
    tmp_path: pathlib.Path,
) -> None:
    """A nested runner an integration test spawns inherits the outer run's
    PGAI_TASK_ID; scoping by task id alone would recreate the shared-path
    hazard between the outer and inner sessions."""
    env = _temp_env(tmp_path)
    env["PGAI_TASK_ID"] = "CODER-20260101-001-example"
    snippet = _source_both("gated_runner_pytest_basetemp unit\n")
    first = _claim_lines(run_bash(tmp_path, snippet, extra_env=env, cwd=_TEAM_DIR))[0]
    second = _claim_lines(run_bash(tmp_path, snippet, extra_env=env, cwd=_TEAM_DIR))[0]

    assert first != second, (
        f"two claims under one PGAI_TASK_ID resolved the same basetemp {first}"
    )


# ---------------------------------------------------------------------------
# The on-disk label names what made the directory
# ---------------------------------------------------------------------------


def test_label_carries_task_id_when_set(tmp_path: pathlib.Path) -> None:
    env = _temp_env(tmp_path)
    env["PGAI_TASK_ID"] = "CODER-20260101-001-example"
    path = _claim_lines(
        run_bash(tmp_path, _source_both("gated_runner_pytest_basetemp unit\n"), extra_env=env, cwd=_TEAM_DIR)
    )[0]
    assert re.fullmatch(r"unit-CODER-20260101-001-example\.[A-Za-z0-9]{6}", pathlib.Path(path).name), (
        f"basetemp label must name the task that claimed it; got {pathlib.Path(path).name}"
    )


def test_label_carries_caller_pid_when_no_task_id_is_set(tmp_path: pathlib.Path) -> None:
    env = _temp_env(tmp_path)
    lines = _claim_lines(
        run_bash(
            tmp_path,
            _source_both('echo "$$"\ngated_runner_pytest_basetemp unit\n'),
            extra_env=env,
            cwd=_TEAM_DIR,
        )
    )
    shell_pid, path = lines
    assert re.fullmatch(rf"unit-pid{shell_pid}\.[A-Za-z0-9]{{6}}", pathlib.Path(path).name), (
        f"with no PGAI_TASK_ID the label must carry the calling shell's pid "
        f"({shell_pid}); got {pathlib.Path(path).name}"
    )


# ---------------------------------------------------------------------------
# Release: only this invocation's directory, never the caller's exit code
# ---------------------------------------------------------------------------


def test_cleanup_removes_only_the_named_basetemp_and_leaves_a_sibling(tmp_path: pathlib.Path) -> None:
    env = _temp_env(tmp_path)
    result = run_bash(
        tmp_path,
        _source_both(
            'a="$(gated_runner_pytest_basetemp unit)"\n'
            'b="$(gated_runner_pytest_basetemp unit)"\n'
            'touch "$a/live-fixture" "$b/live-fixture"\n'
            'gated_runner_pytest_basetemp_cleanup "$a"\n'
            'echo "rc=$?"\n'
            'echo "$a"\n'
            'echo "$b"\n'
        ),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    rc_line, a, b = _claim_lines(result)
    assert rc_line == "rc=0"
    assert not pathlib.Path(a).exists(), f"released basetemp still present: {a}"
    assert (pathlib.Path(b) / "live-fixture").exists(), (
        f"releasing {a} must not touch the sibling invocation's basetemp {b}"
    )


def test_cleanup_is_a_no_op_for_empty_and_absent_paths_and_never_fails(tmp_path: pathlib.Path) -> None:
    env = _temp_env(tmp_path)
    result = run_bash(
        tmp_path,
        _source_both(
            'gated_runner_pytest_basetemp_cleanup ""; echo "empty=$?"\n'
            'gated_runner_pytest_basetemp_cleanup "$PGAI_AGENT_KANBAN_TEMP_DIR/pytest/never-claimed"; echo "absent=$?"\n'
        ),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    assert _claim_lines(result) == ["empty=0", "absent=0"], result.stdout


def test_cleanup_refuses_a_path_outside_the_framework_temp_root_and_still_returns_zero(
    tmp_path: pathlib.Path,
) -> None:
    env = _temp_env(tmp_path)
    outside = tmp_path / "not_under_temp_root"
    outside.mkdir()
    (outside / "keep-me").write_text("x", encoding="utf-8")
    result = run_bash(
        tmp_path,
        _source_both(f'gated_runner_pytest_basetemp_cleanup "{outside}"; echo "rc=$?"\n'),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    assert _claim_lines(result) == ["rc=0"], result.stdout
    assert (outside / "keep-me").exists(), (
        "cleanup must go through the root-guarded remover and leave anything "
        f"outside the framework temp root in place; {outside} was removed"
    )
    assert "refusing to remove" in result.stderr, result.stderr


def test_cleanup_removes_a_read_only_leftover(tmp_path: pathlib.Path) -> None:
    """A failing test can leave a mode-0555 directory inside the basetemp;
    the release must still leave nothing behind."""
    env = _temp_env(tmp_path)
    result = run_bash(
        tmp_path,
        _source_both(
            'a="$(gated_runner_pytest_basetemp unit)"\n'
            'mkdir -p "$a/ro"; touch "$a/ro/f"; chmod 0555 "$a/ro"\n'
            'gated_runner_pytest_basetemp_cleanup "$a"; echo "rc=$?"\n'
            'echo "$a"\n'
        ),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    rc_line, a = _claim_lines(result)
    assert rc_line == "rc=0"
    assert not pathlib.Path(a).exists(), f"read-only leftover survived release: {a}"


# ---------------------------------------------------------------------------
# The runner-shaped exit path: claim, register release on EXIT, exit
# ---------------------------------------------------------------------------


def test_runner_shaped_exit_releases_basetemp_and_preserves_exit_code(tmp_path: pathlib.Path) -> None:
    """The prevention exercised end to end in a shell shaped like the
    runners: the release is registered on the EXIT trap before the claim,
    the shell exits non-zero as a failing pytest run would, and afterwards
    the pytest/ subtree holds nothing from that invocation."""
    env = _temp_env(tmp_path)
    claimed = tmp_path / "claimed.txt"
    result = run_bash(
        tmp_path,
        _source_both(
            "_BASETEMP=''\n"
            "_release_on_exit() { gated_runner_pytest_basetemp_cleanup \"${_BASETEMP:-}\"; }\n"
            "trap _release_on_exit EXIT\n"
            '_BASETEMP="$(gated_runner_pytest_basetemp unit)"\n'
            f'printf "%s" "$_BASETEMP" > "{claimed}"\n'
            'touch "$_BASETEMP/live-fixture"\n'
            "exit 3\n"
        ),
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    assert result.returncode == 3, (
        f"releasing the basetemp must not alter the runner's own exit code; got "
        f"{result.returncode}\nstderr:\n{result.stderr}"
    )
    basetemp = pathlib.Path(claimed.read_text(encoding="utf-8"))
    assert not basetemp.exists(), f"basetemp survived the exit path: {basetemp}"
    pytest_subtree = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest"
    assert list(pytest_subtree.iterdir()) == [], (
        f"pytest/ subtree must hold nothing after the runner exits; found "
        f"{[p.name for p in pytest_subtree.iterdir()]}"
    )


# ---------------------------------------------------------------------------
# Fail loud, never guess, when the temp resolver is missing
# ---------------------------------------------------------------------------


def test_claim_refuses_naming_temp_lib_when_resolver_is_not_sourced(tmp_path: pathlib.Path) -> None:
    env = _temp_env(tmp_path)
    result = run_bash(
        tmp_path,
        f"set -uo pipefail\nsource {_LIB}\ngated_runner_pytest_basetemp unit; echo \"rc=$?\"\n",
        extra_env=env,
        cwd=_TEAM_DIR,
    )
    assert _claim_lines(result) == ["rc=1"], (
        f"the claim must refuse rather than guess a temp root; stdout:\n{result.stdout}"
    )
    assert "temp.sh" in result.stderr, (
        f"the refusal must name the lib the caller forgot to source; stderr:\n{result.stderr}"
    )
    assert not (pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest").exists()


# ---------------------------------------------------------------------------
# The sibling runners, end to end: claim while live, release on exit
# ---------------------------------------------------------------------------

_SIBLING_RUNNERS = [
    (_RUN_GATED_SH, "gated"),
    (_RUN_COLD_BUILD_SH, "cold"),
]
_SIBLING_IDS = [r[0].name for r in _SIBLING_RUNNERS]

# The python3 stand-in the runner finds first on PATH. It intercepts only the
# runner's own `python3 -m pytest <args>` launch (the `--version` probe in
# the pytest-present pre-flight is delegated, like every other call, to the
# real interpreter), records the --basetemp the runner handed it and whether
# that directory exists at that moment, optionally runs a second invocation
# of the same runner while this "session" is still live, then exits with the
# code the test chose -- so a runner-level exit path is exercised with no
# engine, no real pytest, and no recursion into this suite.
_PYTEST_STAND_IN = """#!/usr/bin/env bash
if [[ "${1:-}" == "-m" && "${2:-}" == "pytest" && "${3:-}" != "--version" ]]; then
  basetemp="${PYTEST_ADDOPTS##*--basetemp=}"
  basetemp="${basetemp%% *}"
  printf 'basetemp=%s\n' "$basetemp" >> "$RUNNER_STAND_IN_RECORD"
  if [[ -d "$basetemp" ]]; then
    printf 'at-launch=present\n' >> "$RUNNER_STAND_IN_RECORD"
  else
    printf 'at-launch=absent\n' >> "$RUNNER_STAND_IN_RECORD"
  fi
  if [[ -n "${RUNNER_STAND_IN_OVERLAP_SCRIPT:-}" ]]; then
    RUNNER_STAND_IN_OVERLAP_SCRIPT="" bash "$RUNNER_STAND_IN_OVERLAP_SCRIPT" >/dev/null 2>&1
    printf 'overlap-rc=%s\n' "$?" >> "$RUNNER_STAND_IN_RECORD"
    if [[ -d "$basetemp" ]]; then
      printf 'after-overlap=present\n' >> "$RUNNER_STAND_IN_RECORD"
    else
      printf 'after-overlap=absent\n' >> "$RUNNER_STAND_IN_RECORD"
    fi
  fi
  exit "${RUNNER_STAND_IN_PYTEST_EXIT:-0}"
fi
exec "$RUNNER_STAND_IN_REAL_PYTHON3" "$@"
"""


def _sibling_env(tmp_path: pathlib.Path) -> dict[str, str]:
    """Environment for one end-to-end sibling-runner invocation.

    Sandboxed per docs/test-authoring-guidelines.md "Live-install safety":
    both root names point at a throwaway kanban root (no shell-env; its
    only kanban.cfg line selects the container images; env_bootstrap.sh
    honours the explicit root),
    the two pre-resolved live-install names are cleared, and the framework
    temp root is private to this test. The dev tree is this checkout, found
    from this file; PATH leads with the python3 stand-in. The container-tier
    authorization is set so the cold runner passes its operator-only gate."""
    root = tmp_path / "kanban_root"
    root.mkdir()
    # The cold runner builds only the images the operator selects; this
    # kanban.cfg default selects them all, so the run reaches its pytest
    # launch rather than listing the images and stopping.
    (root / "kanban.cfg").write_text("[release]\ncontainer_images = all\n", encoding="utf-8")
    temp = tmp_path / "framework_tmp"
    temp.mkdir()
    stand_in_bin = tmp_path / "stand_in_bin"
    stand_in_bin.mkdir()
    stand_in = stand_in_bin / "python3"
    stand_in.write_text(_PYTEST_STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp),
        "PGAI_DEV_TREE_PATH": str(_TEAM_DIR.parent),
        "PGAI_TASK_ID": "",
        "PYTEST_ADDOPTS": "",
        "PATH": f"{stand_in_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "RUNNER_STAND_IN_RECORD": str(tmp_path / "stand_in_record.txt"),
        "RUNNER_STAND_IN_REAL_PYTHON3": shutil.which("python3") or sys.executable,
        # run-cold-build-tests.sh is the operator-only container tier; the
        # stand-in replaces its pytest launch, so authorize it as an operator
        # shell would (and outside any agent wake) to reach that launch.
        "PGAI_CONTAINER_TIER": "1",
        "PGAI_AGENT_WAKE_ACTIVE": "",
    }


def _run_sibling(script: pathlib.Path, tmp_path: pathlib.Path, env: dict[str, str]):
    return run_bash(tmp_path, f'bash "{script}"\n', extra_env=env, cwd=_TEAM_DIR, timeout=120)


def _stand_in_record(env: dict[str, str]) -> list[tuple[str, str]]:
    """The stand-in's record as ordered (key, value) pairs; empty when the
    runner never reached its pytest launch."""
    record = pathlib.Path(env["RUNNER_STAND_IN_RECORD"])
    if not record.exists():
        return []
    pairs = []
    for line in record.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            pairs.append((key, value))
    return pairs


def _assert_claimed_under_pytest_subtree(basetemp: str, suite: str, env: dict[str, str]) -> None:
    path = pathlib.Path(basetemp)
    expected_parent = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest"
    assert path.parent == expected_parent, (
        f"basetemp {basetemp} must sit directly under the framework temp root's "
        f"pytest/ subtree ({expected_parent})"
    )
    assert path.name.startswith(f"{suite}-"), (
        f"basetemp {basetemp} must be labelled with the suite that claimed it ({suite})"
    )
    assert re.fullmatch(rf"{suite}-pid\d+\.[A-Za-z0-9]{{6}}", path.name), (
        f"with no PGAI_TASK_ID the label must be {suite}-pid<pid>.XXXXXX; got {path.name}"
    )


@pytest.mark.parametrize("script,suite", _SIBLING_RUNNERS, ids=_SIBLING_IDS)
@pytest.mark.parametrize(
    "pytest_exit,runner_exit",
    [(0, 0), (1, 1)],
    ids=["pytest-passes", "pytest-fails"],
)
def test_sibling_runner_hands_pytest_a_live_per_invocation_basetemp_and_leaves_none_behind(
    script: pathlib.Path,
    suite: str,
    pytest_exit: int,
    runner_exit: int,
    tmp_path: pathlib.Path,
) -> None:
    """The basetemp the runner hands pytest exists while the session is
    live and is gone once the runner has exited -- on a clean exit and on
    the failing exit a non-zero pytest run produces -- with the runner's
    own verdict preserved."""
    env = _sibling_env(tmp_path)
    env["RUNNER_STAND_IN_PYTEST_EXIT"] = str(pytest_exit)

    result = _run_sibling(script, tmp_path, env)

    assert result.returncode == runner_exit, (
        f"{script.name} must exit {runner_exit} when pytest exits {pytest_exit}; got "
        f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    record = _stand_in_record(env)
    assert record[:2] and record[0][0] == "basetemp", (
        f"{script.name} never reached its pytest launch\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    basetemp = record[0][1]
    _assert_claimed_under_pytest_subtree(basetemp, suite, env)
    assert record[1] == ("at-launch", "present"), (
        f"{script.name} handed pytest a basetemp that did not exist at launch: {basetemp}"
    )
    assert not pathlib.Path(basetemp).exists(), (
        f"{script.name} left its basetemp behind after exiting {result.returncode}: {basetemp}"
    )
    pytest_subtree = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest"
    assert list(pytest_subtree.iterdir()) == [], (
        f"pytest/ subtree must hold nothing after {script.name} exits; found "
        f"{[p.name for p in pytest_subtree.iterdir()]}"
    )


@pytest.mark.parametrize("script,suite", _SIBLING_RUNNERS, ids=_SIBLING_IDS)
def test_overlapping_sibling_runner_invocations_resolve_distinct_basetemps_and_neither_removes_the_others(
    script: pathlib.Path, suite: str, tmp_path: pathlib.Path
) -> None:
    """The collision the per-invocation claim prevents, exercised once: a
    second invocation of the same runner starts and runs to completion
    while the first invocation's pytest session is still live, under one
    PGAI_AGENT_KANBAN_TEMP_DIR. The two resolve different directories, the
    first's basetemp is still present after the second has claimed and
    released its own, and nothing remains once both have exited."""
    env = _sibling_env(tmp_path)
    env["RUNNER_STAND_IN_OVERLAP_SCRIPT"] = str(script)

    result = _run_sibling(script, tmp_path, env)

    assert result.returncode == 0, (
        f"{script.name} exited {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    record = _stand_in_record(env)
    keys = [k for k, _ in record]
    assert keys == [
        "basetemp", "at-launch",          # the outer invocation's session starts
        "basetemp", "at-launch",          # the inner invocation's session, while the outer is live
        "overlap-rc", "after-overlap",    # the inner has exited; the outer is still live
    ], f"unexpected stand-in record shape: {record}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    outer, inner = record[0][1], record[2][1]
    _assert_claimed_under_pytest_subtree(outer, suite, env)
    _assert_claimed_under_pytest_subtree(inner, suite, env)
    assert outer != inner, (
        f"two overlapping invocations of {script.name} resolved the same basetemp "
        f"{outer}; the second run's pytest startup would delete the first run's live fixtures"
    )
    assert record[3] == ("at-launch", "present"), f"inner basetemp missing at launch: {inner}"
    assert record[4] == ("overlap-rc", "0"), (
        f"the inner invocation of {script.name} did not exit 0: {record[4]}"
    )
    assert record[5] == ("after-overlap", "present"), (
        f"the inner invocation of {script.name} removed the outer invocation's live "
        f"basetemp {outer} -- the two shared a path"
    )
    pytest_subtree = pathlib.Path(env["PGAI_AGENT_KANBAN_TEMP_DIR"]) / "pytest"
    assert list(pytest_subtree.iterdir()) == [], (
        f"pytest/ subtree must hold nothing after both invocations exit; found "
        f"{[p.name for p in pytest_subtree.iterdir()]}"
    )


# ---------------------------------------------------------------------------
# Static: every runner goes through the shared helpers, with its own suite
# ---------------------------------------------------------------------------

_RUNNERS = [
    (_RUN_UNIT_SH, "unit", "_RUT_PYTEST_BASETEMP"),
    (_RUN_INTEGRATION_SH, "integration", "_RIT_PYTEST_BASETEMP"),
    (_RUN_GATED_SH, "gated", "_RGT_PYTEST_BASETEMP"),
    (_RUN_COLD_BUILD_SH, "cold", "_RCB_PYTEST_BASETEMP"),
]


@pytest.mark.parametrize("script,suite,var", _RUNNERS, ids=[r[0].name for r in _RUNNERS])
def test_runner_claims_and_releases_its_own_basetemp_through_the_shared_helpers(
    script: pathlib.Path, suite: str, var: str
) -> None:
    text = script.read_text(encoding="utf-8")

    claim = re.compile(rf'{var}="\$\(\s*gated_runner_pytest_basetemp\s+{suite}\s*\)"')
    assert claim.search(text) is not None, (
        f"{script.name} must claim its basetemp via gated_runner_pytest_basetemp "
        f"{suite} into {var} -- the shared helper is the one implementation of "
        "per-invocation isolation for every runner"
    )

    handoff = re.compile(rf'--basetemp=\$\{{{var}\}}')
    assert handoff.search(text) is not None, (
        f"{script.name} must hand the claimed {var} to pytest via PYTEST_ADDOPTS "
        "so the replay line names the exact basetemp the run used"
    )

    release = re.compile(rf'gated_runner_pytest_basetemp_cleanup\s+"\$\{{{var}:-\}}"')
    assert release.search(text) is not None, (
        f"{script.name} must release {var} through gated_runner_pytest_basetemp_cleanup "
        "from its exit hook, so the pytest/ subtree never keeps one directory per run"
    )

    fixed_path = re.compile(rf'_PYTEST_BASETEMP="\$\{{PGAI_AGENT_KANBAN_TEMP_DIR\}}/pytest/{suite}"')
    assert fixed_path.search(text) is None, (
        f"{script.name} still derives a fixed per-suite basetemp; two concurrent "
        "runs of this suite would share it"
    )


@pytest.mark.parametrize("script,suite,var", _RUNNERS, ids=[r[0].name for r in _RUNNERS])
def test_runner_registers_the_release_inside_its_exit_hook_function(
    script: pathlib.Path, suite: str, var: str
) -> None:
    """The release must sit inside the function CLEANUP_ON_EXIT_PRE_HOOK
    names, not in straight-line code a failing pytest `exit` would skip."""
    text = script.read_text(encoding="utf-8")
    hook_name = re.search(r"^CLEANUP_ON_EXIT_PRE_HOOK=(\S+)\s*$", text, re.MULTILINE)
    assert hook_name is not None, f"{script.name} must register a CLEANUP_ON_EXIT_PRE_HOOK"
    body = re.search(
        rf"^{re.escape(hook_name.group(1))}\(\)\s*\{{(.*?)^\}}", text, re.MULTILINE | re.DOTALL
    )
    assert body is not None, f"{script.name}: hook function {hook_name.group(1)} not found"
    assert f"gated_runner_pytest_basetemp_cleanup \"${{{var}:-}}\"" in body.group(1), (
        f"{script.name}: the basetemp release must run from {hook_name.group(1)}, "
        "which fires on every exit path"
    )
