"""
test_gated_runner_stated_test_order.py
========================================
Both certifying suites -- run-unit-tests.sh and run-integration-tests.sh --
must launch pytest with `-p no:randomly` so their execution order is a
stated, reproducible property of the tree rather than a fresh shuffle on
every invocation, and must print that policy (plus the exact replay
command) so a failing run can be reproduced from the runner's own output
with no seed to hunt for in captured logs.

Without the flag, pytest-randomly reorders the collected item list on
every invocation with a fresh random seed recorded only in the captured
`Using --randomly-seed=<N>` line, which is how an order-coupled fixture
can pass in one run and fail in the next with no change to the tree, and
how a suite-load-sensitive check becomes expensive to reproduce.

This is the same neutralisation `verify-test-order-independence.sh`
already applies to both of its own passes (see
test_order_independence_plugin_neutralization.py), extended to the two
runners that actually certify a release -- the constraint in this task's
README requires the identical policy in both, not a split one.

Coverage:
  - Static: each runner's PYTEST_ARGS construction carries `-p no:randomly`
    -- catches a future edit that drops the flag from either script (or
    only one of the two, which the README calls out as its own defect).
  - Static: each runner prints a "Test order:" line naming the policy
    before invoking pytest, and a "Replay this exact run:" line carrying
    the runner's own pytest invocation -- the acceptance criterion that a
    failing run replays from what the runner prints, not from log
    archaeology.
  - Static: each runner's "Replay this exact run:" line is built through
    gated_runner_replay_command (team/scripts/lib/gated_runner_lint.sh)
    rather than printing the bare pytest argv -- catches a future edit
    that reverts either runner to an argv-only replay line (a replay
    that would inherit the operator's own ambient
    KANBAN_ROOT/PYTHONHASHSEED instead of the runner's).
  - Behavioral: gated_runner_replay_command's env-parity guarantee -- a
    family variable (KANBAN_ROOT) the runner deliberately leaves
    unexported/unset before invoking pytest is still forced absent when
    the printed replay command is pasted into a SEPARATE shell that
    ambiently exports a different value for that same name (an operator
    shell that sourced shell-env holds KANBAN_ROOT; the runner's own
    invocation never did).
  - Behavioral (plugin-present case, skipped when pytest-randomly is not
    installed): the literal `-p no:randomly` flag both runners now pass,
    run twice against a tiny fixture suite with pytest-randomly active,
    produces the identical, file-declaration-order execution both times --
    proving the flag is what makes the order stated, not incidental.

Test names describe behaviour under test (team/roles/TESTER.md, Test
Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import ast
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RUN_UNIT_SH = _TEAM_DIR / "scripts" / "run-unit-tests.sh"
_RUN_INTEGRATION_SH = _TEAM_DIR / "scripts" / "run-integration-tests.sh"
_GATED_RUNNER_LINT_SH = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"

_RANDOMLY_INSTALLED = importlib.util.find_spec("pytest_randomly") is not None

# One (script, PYTEST_ARGS pattern, echo-banner pattern) tuple per gated
# runner -- both checked with the same assertions so a policy applied to
# only one script fails loudly rather than passing by omission.
_RUNNERS = [
    (
        _RUN_UNIT_SH,
        re.compile(r'PYTEST_ARGS=\(\s*\n\s*"-p"\s+"no:randomly"'),
    ),
    (
        _RUN_INTEGRATION_SH,
        re.compile(r'PYTEST_ARGS=\("-p"\s+"no:randomly"'),
    ),
]


@pytest.mark.parametrize("script,pattern", _RUNNERS, ids=[r[0].name for r in _RUNNERS])
def test_pytest_args_carries_no_randomly_flag(script: pathlib.Path, pattern: re.Pattern) -> None:
    """Each gated runner's PYTEST_ARGS must carry `-p no:randomly`.

    Static guard: catches a future edit that drops the flag from either
    runner, or applies it to only one -- the task README names a split
    policy between the two certifying suites as the defect in another
    shape.
    """
    text = script.read_text(encoding="utf-8")
    assert pattern.search(text) is not None, (
        f"{script.name} must build PYTEST_ARGS with a literal `-p no:randomly` "
        "so pytest-randomly is neutralized -- the certifying suites must run "
        "in a stated order, not a fresh shuffle on every invocation."
    )


@pytest.mark.parametrize("script", [_RUN_UNIT_SH, _RUN_INTEGRATION_SH], ids=lambda p: p.name)
def test_runner_prints_stated_order_policy_and_replay_command(script: pathlib.Path) -> None:
    """Each gated runner must announce the order policy and print the exact
    command to replay the run, both before invoking pytest.

    This is the acceptance criterion that a failing run replays from what
    the runner itself prints -- not from a seed buried in captured output.
    """
    text = script.read_text(encoding="utf-8")
    assert "Test order: stated" in text, (
        f"{script.name} must echo a 'Test order: stated ...' line so the "
        "policy is verifiable from the runner's own output without reading "
        "the script."
    )
    assert "Replay this exact run:" in text, (
        f"{script.name} must echo a 'Replay this exact run: ...' line "
        "carrying its own pytest invocation, so a failing run is "
        "reproducible from printed output alone."
    )


@pytest.mark.parametrize("script", [_RUN_UNIT_SH, _RUN_INTEGRATION_SH], ids=lambda p: p.name)
def test_runner_builds_replay_line_through_shared_env_parity_helper(script: pathlib.Path) -> None:
    """Each gated runner's "Replay this exact run:" line must be built
    through gated_runner_replay_command, not print the bare pytest argv.

    A bare `python3 -m pytest ${PYTEST_ARGS[*]}` line replays under the
    pasting shell's OWN ambient environment (its own exported
    KANBAN_ROOT from having sourced shell-env, in particular) rather
    than the one this runner actually applied, so a real failure can
    "reproduce" as a pass. This is a static guard: it fails against a
    runner whose replay line prints the bare argv, because that runner
    carries no `gated_runner_replay_command` call at all.
    """
    text = script.read_text(encoding="utf-8")
    assert re.search(
        r'echo\s+"Replay this exact run:.*\$\(gated_runner_replay_command\s',
        text,
    ), (
        f"{script.name} must build its 'Replay this exact run:' line by "
        "calling gated_runner_replay_command (team/scripts/lib/"
        "gated_runner_lint.sh) around the pytest invocation, not print "
        "the bare pytest argv."
    )


def test_gated_runner_replay_command_forces_family_var_absence_across_shells(
    tmp_path: pathlib.Path,
) -> None:
    """gated_runner_replay_command's printed line must reproduce a runner's
    environment even when pasted into a DIFFERENT shell that ambiently
    exports a different value for the same family variable.

    This reproduces the exact operator-visible failure mode, staged as
    two separate subprocesses so the second one's ambient
    KANBAN_ROOT/PYTHONHASHSEED
    genuinely come from ITS OWN environment (simulating an operator shell
    that sourced shell-env) rather than merely being unset earlier in the
    same shell that built the command.

    Stage 1 mimics a gated runner's own invocation: a clean environment
    (no ambient KANBAN_ROOT/PYTHONHASHSEED at all -- the runner's own
    process never exports either at pytest-launch time), sourcing the
    lib, exporting the family variables the runner keeps exported
    (PYTEST_ADDOPTS here stands in for the whole exported subset), and
    printing ONLY the command gated_runner_replay_command builds -- this
    is what the runner's own "Replay this exact run:" banner contains.

    Stage 2 mimics an operator's shell: KANBAN_ROOT and PYTHONHASHSEED are
    exported ambiently to sabotage values BEFORE evaluating the pasted
    command from stage 1. The probe substituted for the pytest invocation
    reports what a real pytest child would see for those names.
    """
    stage1_script = f'''
set -euo pipefail
unset KANBAN_ROOT PYTHONHASHSEED PGAI_LINT_CHANGELOG_MODE 2>/dev/null || true
source "{_GATED_RUNNER_LINT_SH}"
export PYTEST_ADDOPTS="--basetemp={tmp_path / "basetemp"}"
gated_runner_replay_command "python3 -c \\"import os,sys; sys.stdout.write(repr((os.environ.get('KANBAN_ROOT'), os.environ.get('PYTHONHASHSEED'), os.environ.get('PYTEST_ADDOPTS'))))\\""
'''
    stage1 = subprocess.run(
        ["bash", "-c", stage1_script],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert stage1.returncode == 0, (
        f"stage 1 (building the runner's own replay line) failed:\n"
        f"stdout:\n{stage1.stdout}\nstderr:\n{stage1.stderr}"
    )
    replay_command = stage1.stdout.strip()
    assert replay_command, "gated_runner_replay_command printed an empty command"
    assert "KANBAN_ROOT" in replay_command and "-u KANBAN_ROOT" in replay_command, (
        "the runner never exports KANBAN_ROOT before invoking pytest, so the "
        f"replay command must force it absent via `-u KANBAN_ROOT`; got: {replay_command!r}"
    )

    operator_env = dict(os.environ)
    operator_env["PATH"] = os.environ.get("PATH", "")
    operator_env["KANBAN_ROOT"] = "operator-shell-sabotage-value"
    operator_env["PYTHONHASHSEED"] = "13"
    stage2 = subprocess.run(
        ["bash", "-c", replay_command],
        capture_output=True,
        text=True,
        timeout=30,
        env=operator_env,
    )
    assert stage2.returncode == 0, (
        f"stage 2 (pasting the replay command into an operator shell) failed:\n"
        f"command: {replay_command}\nstdout:\n{stage2.stdout}\nstderr:\n{stage2.stderr}"
    )
    observed = ast.literal_eval(stage2.stdout.strip())
    kanban_root_seen, pythonhashseed_seen, pytest_addopts_seen = observed
    assert kanban_root_seen is None, (
        "the pasted replay command must not let the operator shell's own "
        f"ambient KANBAN_ROOT reach the pytest invocation; observed {kanban_root_seen!r} "
        f"(command: {replay_command})"
    )
    assert pythonhashseed_seen is None, (
        "the pasted replay command must not let the operator shell's own "
        f"ambient PYTHONHASHSEED reach the pytest invocation; observed {pythonhashseed_seen!r} "
        f"(command: {replay_command})"
    )
    assert pytest_addopts_seen == f"--basetemp={tmp_path / 'basetemp'}", (
        "the pasted replay command must carry the exact PYTEST_ADDOPTS value "
        f"the runner exported; observed {pytest_addopts_seen!r} (command: {replay_command})"
    )


def test_gated_runner_replay_command_carries_export_with_second_attribute() -> None:
    """gated_runner_replay_command must classify a family variable by its
    export attribute alone, independent of any other attribute (readonly,
    integer, array, lowercase, ...) the same variable also carries.

    A variable declared `declare -rx` (exported AND readonly) is still
    exported -- pytest inherits it exactly like a plain `export` -- so the
    printed replay line must carry it as a literal NAME=value assignment,
    never as `env -u NAME`. A check anchored on the literal prefix
    `declare -x ` misses this because bash's own `declare -p` output for
    this variable is `declare -rx NAME=...`, not `declare -x NAME=...`.
    """
    script = f'''
set -euo pipefail
source "{_GATED_RUNNER_LINT_SH}"
declare -rx PYTEST_ADDOPTS="--basetemp=/replay-example"
gated_runner_replay_command PYTEST_HERE
'''
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, (
        f"building the replay line for a readonly-and-exported variable "
        f"failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    replay_command = result.stdout.strip()
    assert "PYTEST_ADDOPTS=--basetemp=/replay-example" in replay_command, (
        "a family variable that is exported AND carries another attribute "
        "(readonly here) must still print as a literal NAME=value "
        f"assignment; got: {replay_command!r}"
    )
    assert "-u PYTEST_ADDOPTS" not in replay_command, (
        "a family variable that IS exported must never be forced absent "
        "via `-u`, regardless of any other attribute it also carries; got: "
        f"{replay_command!r}"
    )


def _run_fixture_pass(fixture_dir: pathlib.Path, workdir: pathlib.Path, label: str) -> list[str]:
    """Run pytest with the exact `-p no:randomly` flag both gated runners
    now pass against a tiny fixture suite, and return the per-test
    execution order from the resulting junit-xml report.

    PYTEST_ADDOPTS and PYTEST_DEBUG_TEMPROOT are stripped from the child's
    environment for the same reason
    test_order_independence_plugin_neutralization.py strips them: this
    test runs inside the suite's own pytest session, which inherits
    run-unit-tests.sh's PYTEST_ADDOPTS (a --basetemp pinned under the
    OUTER suite's temp root), and pytest refuses a --basetemp that is the
    current working directory or a parent of it.
    """
    junit_path = workdir / f"{label}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:randomly",
        str(fixture_dir),
        "-q",
        f"--basetemp={workdir / f'basetemp_{label}'}",
        f"--junitxml={junit_path}",
    ]
    child_env = dict(os.environ)
    child_env.pop("PYTEST_ADDOPTS", None)
    child_env.pop("PYTEST_DEBUG_TEMPROOT", None)
    result = subprocess.run(
        cmd,
        cwd=fixture_dir,
        capture_output=True,
        text=True,
        timeout=180,
        env=child_env,
    )
    if not junit_path.is_file():
        raise AssertionError(
            f"Nested pytest invocation produced no junit-xml report at {junit_path}.\n"
            f"command: {' '.join(cmd)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    tree = ET.parse(junit_path)
    return [
        f'{tc.get("classname")}::{tc.get("name")}' for tc in tree.getroot().iter("testcase")
    ]


_FIXTURE_TEST_MODULE = '''
def test_a():
    assert True


def test_b():
    assert True


def test_c():
    assert True


def test_d():
    assert True


def test_e():
    assert True
'''


@pytest.mark.skipif(
    not _RANDOMLY_INSTALLED,
    reason=(
        "pytest-randomly is not installed in this environment -- this test "
        "covers only the plugin-present case, where the flag actually has "
        "something to neutralize."
    ),
)
def test_gated_runner_flag_produces_stated_order_with_randomly_active(
    tmp_path: pathlib.Path,
) -> None:
    """With pytest-randomly active, two independent pytest sessions invoked
    with the exact `-p no:randomly` flag both gated runners now pass must
    produce the identical, file-declaration-order execution -- proving the
    runners' new flag actually states the order rather than merely
    reducing how often it changes.
    """
    fixture_dir = tmp_path / "fixture_suite"
    fixture_dir.mkdir()
    (fixture_dir / "test_fixture_order.py").write_text(_FIXTURE_TEST_MODULE, encoding="utf-8")

    workdir = tmp_path / "gated_runner_order_work"
    workdir.mkdir()

    order_1 = _run_fixture_pass(fixture_dir, workdir, "run1")
    order_2 = _run_fixture_pass(fixture_dir, workdir, "run2")

    assert order_1 == order_2, (
        "Two pytest sessions invoked with the gated runners' -p no:randomly "
        "flag must produce the same execution order with pytest-randomly "
        "installed -- a mismatch means the flag is no longer neutralising "
        "the plugin, and the runners' order is a fresh shuffle again."
    )

    declared_order = ["test_a", "test_b", "test_c", "test_d", "test_e"]
    actual_short_names = [node_id.rsplit("::", 1)[-1] for node_id in order_1]
    assert actual_short_names == declared_order, (
        "With pytest-randomly neutralised, execution order must match the "
        "fixture module's file-declaration order -- pytest's own default "
        "collection order with no reordering plugin active."
    )
