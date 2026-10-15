"""
test_verify_blocked_task_guard.py
=================================
Keeps scripts/verify-blocked-task-guard.sh honest in both directions.

The verifier runs the blocked-task guard's hold decision
(pgai_agent_kanban.overwatch.block_storm) against synthetic queues, checks
the guard's wiring where it lives -- inside run_project_chain in
scripts/lib/wake_driver.sh -- and checks that both provider wake scripts
(scripts/wake/claude.sh, scripts/wake/codex.sh) source that shared driver.
A verifier that always fails cannot tell a broken guard from a working one,
and neither can one that always passes, so this module asserts both:

  1. On the shipped tree the verifier exits 0 and reports 0 failed.
  2. Pointed at a copy of the driver with the guard block removed, the
     verifier exits 1 and names the missing guard and decision call.
  3. Pointed at a copy of the decision module that no longer checks
     Needs Human (so it would count every BLOCKED task), the verifier exits
     1 and names its scanner check.
  4. Pointed at a copy of the decision module that no longer checks the
     State (so it would count any [B]-marked Needs Human: yes task, whatever
     its State), the verifier exits 1 and names its scanner check.
  5. Pointed at a copy of the driver whose overwatch exemption is removed,
     the verifier exits 1 and names its overwatch check.
  6. Pointed at a copy of the driver whose WAITING/BLOCKED re-checks are
     back inside the held block, the verifier exits 1 and names its
     re-check check.
  7. The verifier script parses (bash -n).

Every run uses a sandbox root under pytest's tmp_path with all five
live-install names set or cleared together (docs/test-authoring-guidelines.md
"Live-install safety"): KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH and
PGAI_AGENT_KANBAN_TEMP_DIR point into the sandbox, PGAI_TASKS_DIR and
PGAI_PROJECT_ROOT are unset. PYTHONPATH is unset and the verifier runs
from tmp_path, so its module calls resolve the package beside the scripts it
runs from. No live task folder is read or written.
"""

from __future__ import annotations

import pathlib
import re
import shutil

from tests.unit.shell_harness import run_bash

_TEAM_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERIFIER_REL = "scripts/verify-blocked-task-guard.sh"

# Files the verifier reads, relative to scripts/. Copying exactly these into
# a scratch scripts/ tree (with the package beside it) lets a test mutate the
# driver or the decision module without touching the dev tree; the verifier
# self-locates from its own path, and pp_run_ops resolves the package from
# the tree it sits in.
_VERIFIER_INPUTS = (
    "verify-blocked-task-guard.sh",
    "lib/temp.sh",
    "lib/ini_parser.sh",
    "lib/task_ids.sh",
    "lib/pp_run_ops.sh",
    "lib/wake_driver.sh",
    "wake/claude.sh",
    "wake/codex.sh",
)

_GUARD_HEADER = "# --- Blocked-task project guard ---"
_STORM_MODULE_REL = pathlib.Path("pgai_agent_kanban") / "overwatch" / "block_storm.py"
# The Needs Human half of the decision's human-gated predicate, and an
# always-true body that disables it.
_NEEDS_HUMAN_PREDICATE = (
    'return task_status.section_value(status_text, "Needs Human").strip().lower() == "yes"'
)
_NEEDS_HUMAN_DISABLED = "return True"
# The State half: the queue scan's rule, and a rule that accepts any state.
_STATE_PREDICATE = 'parse_scan_rule("B=BLOCKED")'
_STATE_DISABLED = 'parse_scan_rule("B=*")'
# The driver's overwatch exemption, and the always-false test that removes it.
_OVERWATCH_BRANCH = 'if [[ "$_gate_verdict" == "systemic" && "$AGENT" == "overwatch" ]]; then'
_OVERWATCH_REMOVED = 'if false; then'
# The driver's re-checks after the held block's closing fi.
_GATE_CLOSE = '  fi  # end: if [[ "$_dispatch_gated" == "false" ]]\n'
_RECHECKS = "  recheck_waiting_tasks\n  recheck_blocked_tasks\n"
_RESULTS_RE = re.compile(r"RESULTS:\s+(\d+)\s+passed,\s+(\d+)\s+failed")


def _copy_verifier_inputs(tmp_path: pathlib.Path, *, with_package: bool = True) -> pathlib.Path:
    """Copy the verifier, the files it reads and the package into tmp_path."""
    scripts = tmp_path / "scripts"
    for rel in _VERIFIER_INPUTS:
        dest = scripts / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_TEAM_ROOT / "scripts" / rel, dest)
    if with_package:
        shutil.copytree(
            _TEAM_ROOT / "pgai_agent_kanban",
            tmp_path / "pgai_agent_kanban",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    return scripts


def _run_verifier_in_sandbox(tmp_path: pathlib.Path, verifier: pathlib.Path):
    """Run *verifier* with every live-install name pointed into a sandbox."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "tmp").mkdir(parents=True)
    script = (
        "env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT -u PYTHONPATH "
        f'KANBAN_ROOT="{sandbox}" '
        f'PGAI_AGENT_KANBAN_ROOT_PATH="{sandbox}" '
        f'PGAI_AGENT_KANBAN_TEMP_DIR="{sandbox}/tmp" '
        f'bash "{verifier}"'
    )
    # cwd is tmp_path, never team/: `python3 -m` puts its cwd first on
    # sys.path, so running from team/ would import the dev tree's package
    # ahead of a mutated copy beside the copied scripts.
    return run_bash(tmp_path, script, timeout=180, cwd=tmp_path), sandbox


def _results(stdout: str) -> tuple[int, int]:
    """Return (passed, failed) from the verifier's RESULTS line."""
    match = _RESULTS_RE.search(stdout)
    assert match, f"verifier printed no RESULTS line:\n{stdout}"
    return int(match.group(1)), int(match.group(2))


def _replace_once(path: pathlib.Path, old: str, new: str) -> None:
    """Replace the one occurrence of *old* in *path* with *new*."""
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (
        f"expected exactly one {old!r} in {path.name}; update this test's constant "
        "if the code it mutates was reworded"
    )
    path.write_text(text.replace(old, new), encoding="utf-8")


def _strip_guard_block(driver_text: str) -> str:
    """Remove the guard, from its header through the if/elif chain that sets
    _dispatch_gated=true, keeping its two local declarations so the rest of
    the driver still parses and reads _dispatch_gated as false."""
    lines = driver_text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.strip() == _GUARD_HEADER.strip())
    set_gated = next(
        i for i in range(start, len(lines)) if lines[i].strip() == "_dispatch_gated=true"
    )
    end = next(i for i in range(set_gated, len(lines)) if lines[i].strip() == "fi")
    kept = [line for line in lines[start : end + 1] if line.lstrip().startswith("local ")]
    return "".join(lines[:start] + kept + lines[end + 1 :])


def _assert_verifier_fails(tmp_path: pathlib.Path, scripts: pathlib.Path, *expected: str) -> str:
    """Run the copied verifier; assert it exits 1 naming every *expected* FAIL."""
    result, _ = _run_verifier_in_sandbox(tmp_path, scripts / "verify-blocked-task-guard.sh")
    assert result.returncode == 1, (
        f"verifier exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    _, failed = _results(result.stdout)
    assert failed > 0
    for check in expected:
        assert f"FAIL: {check}" in result.stdout, f"no 'FAIL: {check}' line:\n{result.stdout}"
    return result.stdout


def test_verifier_passes_on_shipped_tree(tmp_path):
    result, sandbox = _run_verifier_in_sandbox(tmp_path, _TEAM_ROOT / _VERIFIER_REL)

    assert result.returncode == 0, (
        f"verifier exited {result.returncode} on the shipped tree\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    passed, failed = _results(result.stdout)
    assert failed == 0
    assert passed > 0
    # Its fixture is removed on exit; nothing is left in the sandbox temp dir.
    assert list((sandbox / "tmp").iterdir()) == []


def test_verifier_fails_when_guard_is_removed_from_driver(tmp_path):
    scripts = _copy_verifier_inputs(tmp_path, with_package=True)
    driver = scripts / "lib" / "wake_driver.sh"
    stripped = _strip_guard_block(driver.read_text(encoding="utf-8"))
    assert _GUARD_HEADER not in stripped
    driver.write_text(stripped, encoding="utf-8")

    _assert_verifier_fails(tmp_path, scripts, "AC-3 (header)", "AC-3 (decision)")


def test_verifier_fails_when_decision_ignores_needs_human(tmp_path):
    scripts = _copy_verifier_inputs(tmp_path)
    _replace_once(tmp_path / _STORM_MODULE_REL, _NEEDS_HUMAN_PREDICATE, _NEEDS_HUMAN_DISABLED)

    stdout = _assert_verifier_fails(tmp_path, scripts, "AC-2 (scanner)")
    # Only the decision's scanner changed; the driver's structure still holds.
    assert "PASS: AC-3 (header)" in stdout


def test_verifier_fails_when_decision_ignores_state(tmp_path):
    scripts = _copy_verifier_inputs(tmp_path)
    _replace_once(tmp_path / _STORM_MODULE_REL, _STATE_PREDICATE, _STATE_DISABLED)

    stdout = _assert_verifier_fails(tmp_path, scripts, "AC-2 (scanner)")
    assert "PASS: AC-3 (header)" in stdout


def test_verifier_fails_when_overwatch_exemption_is_removed(tmp_path):
    scripts = _copy_verifier_inputs(tmp_path)
    _replace_once(scripts / "lib" / "wake_driver.sh", _OVERWATCH_BRANCH, _OVERWATCH_REMOVED)

    stdout = _assert_verifier_fails(tmp_path, scripts, "AC-3 (overwatch)")
    assert "PASS: AC-2 (storm)" in stdout


def test_verifier_fails_when_rechecks_move_inside_the_held_block(tmp_path):
    scripts = _copy_verifier_inputs(tmp_path)
    driver = scripts / "lib" / "wake_driver.sh"
    text = driver.read_text(encoding="utf-8")
    assert text.count(_GATE_CLOSE) == 1
    assert text.count(_GATE_CLOSE + "\n") == 1, "expected a blank line after the held block's closing fi"
    # Drop the unconditional re-checks after the closing fi and put them back
    # just before it, where a held wake never reaches them.
    head, tail = text.split(_GATE_CLOSE, 1)
    assert _RECHECKS in tail
    tail = tail.replace(_RECHECKS, "", 1)
    driver.write_text(head + _RECHECKS + _GATE_CLOSE + tail, encoding="utf-8")

    _assert_verifier_fails(tmp_path, scripts, "AC-3 (re-checks)")


def test_verifier_script_parses(tmp_path):
    result = run_bash(tmp_path, f'bash -n "{_TEAM_ROOT / _VERIFIER_REL}"')
    assert result.returncode == 0, result.stderr
