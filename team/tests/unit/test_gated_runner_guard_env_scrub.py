"""
test_gated_runner_guard_env_scrub.py
======================================
Pins the invariant that both gated runners -- run-unit-tests.sh and
run-integration-tests.sh -- hand pytest an environment in which neither
GIT_GUARD_REAL_GIT nor PGAI_GIT_GUARD_DEPTH is set, whatever the caller's
own ambient environment held.

WHY THIS MATTERS
-----------------
The wake layer (team/scripts/lib/wake_guard_wiring.sh) pins
GIT_GUARD_REAL_GIT into every non-CM agent's environment before dispatch,
and the git-boundary-guard shim (team/scripts/wake/git-guard/git) sets
PGAI_GIT_GUARD_DEPTH as a recursion sentinel. Both stay exported for the
rest of that agent's session -- including a gated runner invoked from
inside it. test_git_boundary_guard.py's own fixtures build a subprocess
environment from `dict(os.environ)` and place a stub "git" ahead of
everything on PATH, expecting THAT stub to be what the shim resolves; a
leaked GIT_GUARD_REAL_GIT from the calling session outranks the stub (the
shim reads GIT_GUARD_REAL_GIT before ever consulting PATH), and a leaked
PGAI_GIT_GUARD_DEPTH short-circuits the one test that seeds a re-entrant
invocation on purpose. Without a scrub, the guard's own test module is red
in exactly the environment every agent wake session actually runs it in,
which makes the gated unit runner's exit code -- the acceptance criterion
every other CODER/WRITER ticket carries -- unobtainable from that session.

WHAT THIS FILE PROVES, AND HOW
--------------------------------
Running either full gated runner as a fixture inside this suite would be
both recursive (pytest launching a script that launches pytest) and slow
(each runner's own quality-lint fleet runs first) -- disproportionate to
proving two `unset` names. Instead, mirroring
test_gated_runner_stated_test_order.py's own env-parity test (which proves
gated_runner_replay_command's behavior by extracting and running the real
mechanism rather than the whole runner), this file:

  1. Extracts each runner's actual scrub statement via regex -- so a future
     edit that drops one variable name, or removes the statement entirely,
     changes what is extracted and fails the assertions below, rather than
     silently passing against a hand-duplicated copy of the intended text.
  2. Executes that exact extracted statement in an isolated bash
     subprocess, seeded with both variables exported beforehand (the wake
     layer's own effect on a dispatched agent's shell), and confirms
     neither variable is visible to a probe run immediately afterward --
     proving the statement actually clears the variables it names, not
     merely that its text mentions them.
  3. Confirms the statement runs before either runner's own pytest
     invocation line (textual order in the script), and before the
     "Replay this exact run:" banner is built, so a pasted replay command
     forces both names absent too (`-u GIT_GUARD_REAL_GIT` /
     `-u PGAI_GIT_GUARD_DEPTH`) rather than carrying a stale ambient value.
  4. Confirms both runners scrub the identical two-name set -- the
     task README's "keep the two gated runners' environment-scrub sets
     identical" constraint, so a guard test placed in either suite behaves
     the same.

Test names describe behaviour under test (team/roles/TESTER.md, Test
Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_RUN_UNIT_SH = _TEAM_DIR / "scripts" / "run-unit-tests.sh"
_RUN_INTEGRATION_SH = _TEAM_DIR / "scripts" / "run-integration-tests.sh"
_GATED_RUNNER_LINT_SH = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"

_GUARD_VAR_NAMES = frozenset({"GIT_GUARD_REAL_GIT", "PGAI_GIT_GUARD_DEPTH"})

# Matches a bash `unset` statement naming both guard variables, in either
# order, on one line -- liberal about whatever else the line's own unset
# might also clear, strict about requiring both names to be present.
_SCRUB_LINE_RE = re.compile(
    r"^[ \t]*unset\b[^\n]*\b(?:GIT_GUARD_REAL_GIT[^\n]*\bPGAI_GIT_GUARD_DEPTH"
    r"|PGAI_GIT_GUARD_DEPTH[^\n]*\bGIT_GUARD_REAL_GIT)\b[^\n]*$",
    re.MULTILINE,
)

# The runner's real, unmodified pytest invocation line (not the
# `--version` probe in the pytest-installed pre-flight check, and not the
# "Replay this exact run:" echo, which merely prints the argv as text).
# Each runner launches pytest once per planned lane, from inside its lane
# loop, with that lane's argv (GATED_RUNNER_LANE_ARGS).
_PYTEST_INVOCATION_RE = re.compile(
    r'^[ \t]*(?:\S+\s+)?python3 -m pytest "\$\{(?:PYTEST_ARGS|GATED_RUNNER_LANE_ARGS)\[@\]\}"', re.MULTILINE
)

_RUNNERS = [_RUN_UNIT_SH, _RUN_INTEGRATION_SH]


def _extract_scrub_line(script: pathlib.Path) -> str:
    text = script.read_text(encoding="utf-8")
    match = _SCRUB_LINE_RE.search(text)
    assert match is not None, (
        f"{script.name} must carry a single `unset` statement naming both "
        "GIT_GUARD_REAL_GIT and PGAI_GIT_GUARD_DEPTH ahead of its pytest "
        "invocation -- team/scripts/lib/wake_guard_wiring.sh pins the "
        "former and the shim (team/scripts/wake/git-guard/git) sets the "
        "latter into every non-CM agent's session, and "
        "test_git_boundary_guard.py's stub-git fixtures need both absent "
        "to be reached at all."
    )
    return match.group(0)


@pytest.mark.parametrize("script", _RUNNERS, ids=lambda p: p.name)
def test_runner_scrub_statement_precedes_pytest_invocation(script: pathlib.Path) -> None:
    """Each runner's guard-var scrub line must appear before its pytest
    launch line, textually -- a scrub added after the launch never runs
    in time to affect the environment pytest actually inherits."""
    text = script.read_text(encoding="utf-8")
    scrub_match = _SCRUB_LINE_RE.search(text)
    pytest_match = _PYTEST_INVOCATION_RE.search(text)
    assert scrub_match is not None, f"{script.name}: no guard-var scrub statement found"
    assert pytest_match is not None, (
        f"{script.name}: no bare `python3 -m pytest \"${{GATED_RUNNER_LANE_ARGS[@]}}\"` "
        "invocation found -- pattern may need updating if the launch line "
        "changed shape"
    )
    assert scrub_match.start() < pytest_match.start(), (
        f"{script.name}: guard-var scrub statement at offset {scrub_match.start()} "
        f"must precede the pytest invocation at offset {pytest_match.start()}"
    )


@pytest.mark.parametrize("script", _RUNNERS, ids=lambda p: p.name)
def test_runner_scrub_statement_actually_clears_guard_vars(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Executing the runner's own extracted scrub statement, seeded with
    both guard variables exported beforehand (the wake layer's effect on
    a dispatched agent's session), must leave neither variable visible to
    a subsequent probe -- proving the statement clears what it names
    rather than merely mentioning the names in a comment or a dead branch.
    """
    scrub_line = _extract_scrub_line(script)
    fake_real_git = tmp_path / "fake-real-git"
    probe_script = f'''
set -euo pipefail
export GIT_GUARD_REAL_GIT="{fake_real_git}"
export PGAI_GIT_GUARD_DEPTH=1
{scrub_line}
python3 -c "import os; print(repr((os.environ.get('GIT_GUARD_REAL_GIT'), os.environ.get('PGAI_GIT_GUARD_DEPTH'))))"
'''
    result = subprocess.run(
        ["bash", "-c", probe_script],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, (
        f"{script.name}'s extracted scrub statement failed to run:\n"
        f"statement: {scrub_line!r}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    real_git_seen, depth_seen = ast.literal_eval(result.stdout.strip())
    assert real_git_seen is None, (
        f"{script.name}'s scrub statement ({scrub_line!r}) left "
        f"GIT_GUARD_REAL_GIT={real_git_seen!r} visible to the pytest child it launches"
    )
    assert depth_seen is None, (
        f"{script.name}'s scrub statement ({scrub_line!r}) left "
        f"PGAI_GIT_GUARD_DEPTH={depth_seen!r} visible to the pytest child it launches"
    )


def test_both_runners_scrub_the_identical_guard_variable_set() -> None:
    """The two runners' scrub statements must name exactly the same set of
    guard variables -- team/SOP-core.md's constraint that a guard test
    placed in either suite behaves the same, applied to this specific
    pair. Fails if a future edit adds or drops a name from only one
    runner's statement.
    """
    names_by_script = {}
    for script in _RUNNERS:
        scrub_line = _extract_scrub_line(script)
        tokens = scrub_line.split()
        assert tokens[0] == "unset", f"{script.name}: expected scrub line to start with 'unset', got {scrub_line!r}"
        names_by_script[script.name] = frozenset(tokens[1:])

    unit_names = names_by_script[_RUN_UNIT_SH.name]
    integration_names = names_by_script[_RUN_INTEGRATION_SH.name]
    assert unit_names == integration_names, (
        "run-unit-tests.sh and run-integration-tests.sh must scrub the "
        f"identical guard-variable set; got {sorted(unit_names)} vs "
        f"{sorted(integration_names)}"
    )
    assert _GUARD_VAR_NAMES <= unit_names, (
        f"expected both {sorted(_GUARD_VAR_NAMES)} in the scrub set; "
        f"got {sorted(unit_names)}"
    )


def test_gated_runner_replay_command_forces_guard_vars_absent(tmp_path: pathlib.Path) -> None:
    """gated_runner_replay_command's printed line must force both guard
    variables absent (`-u GIT_GUARD_REAL_GIT -u PGAI_GIT_GUARD_DEPTH`) when
    the building process itself has already scrubbed them -- exactly the
    state either runner is in by the time it builds its own "Replay this
    exact run:" banner. Mirrors
    test_gated_runner_replay_command_forces_family_var_absence_across_shells
    (test_gated_runner_stated_test_order.py) for this specific pair of
    names: a pasted replay command must not let an operator's own shell,
    which may ambiently export either name from having sourced a guarded
    wake session's environment, leak into the replayed pytest invocation.
    """
    stage1_script = f'''
set -euo pipefail
unset GIT_GUARD_REAL_GIT PGAI_GIT_GUARD_DEPTH 2>/dev/null || true
source "{_GATED_RUNNER_LINT_SH}"
gated_runner_replay_command "python3 -c \\"import os,sys; sys.stdout.write(repr((os.environ.get('GIT_GUARD_REAL_GIT'), os.environ.get('PGAI_GIT_GUARD_DEPTH'))))\\""
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
    assert "-u GIT_GUARD_REAL_GIT" in replay_command, (
        f"replay command must force GIT_GUARD_REAL_GIT absent; got: {replay_command!r}"
    )
    assert "-u PGAI_GIT_GUARD_DEPTH" in replay_command, (
        f"replay command must force PGAI_GIT_GUARD_DEPTH absent; got: {replay_command!r}"
    )

    operator_env = dict(os.environ)
    operator_env["PATH"] = os.environ.get("PATH", "")
    operator_env["GIT_GUARD_REAL_GIT"] = str(tmp_path / "operator-shell-sabotage-git")
    operator_env["PGAI_GIT_GUARD_DEPTH"] = "1"
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
    real_git_seen, depth_seen = ast.literal_eval(stage2.stdout.strip())
    assert real_git_seen is None, (
        "the pasted replay command must not let the operator shell's own "
        f"ambient GIT_GUARD_REAL_GIT reach the pytest invocation; observed "
        f"{real_git_seen!r} (command: {replay_command})"
    )
    assert depth_seen is None, (
        "the pasted replay command must not let the operator shell's own "
        f"ambient PGAI_GIT_GUARD_DEPTH reach the pytest invocation; observed "
        f"{depth_seen!r} (command: {replay_command})"
    )
