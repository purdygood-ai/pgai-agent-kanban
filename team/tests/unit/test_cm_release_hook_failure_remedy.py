"""
test_cm_release_hook_failure_remedy.py
=======================================
Retrofit: a blocking release-lifecycle hook that exits
non-zero (the `_run_release_hook` failure path in
team/scripts/lib/cm/release_hook_runner.sh) is
classified non-deterministic (exempt) — the hook is project-supplied, so its failure cause is not knowable from the
caller.  Per docs/coding-standards.md "The non-deterministic exemption", the
failure is exempt from a numbered fix-command block but must still name the
next diagnostic step and the rule the gate enforces.

With ``--inbox`` (the pre-tag phase) the same blocking failure also writes
one operator inbox entry carrying the error key, message and remedy it
printed; a failed inbox write is narrated and the exit stays 1. Without the
flag (the pre-squash phase, whose trigger 4 HALT writes the entry) the inbox
is left alone.

A runner that cannot start the hook (its PYTHONPATH cannot be composed, or
the bounded runner cannot be imported) is reported as the runner's failure:
the hook does not run, and the stop carries its own error key and names the
runner rather than the hook path.

The harness sources the real release_hook_runner.sh, a single-concern stage
with no source-time dependencies, so every case exercises the production
function bodies.
"""

from __future__ import annotations

import pathlib
import stat

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_LIB_HOOK_RUNNER = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_hook_runner.sh"
_LIB_ERROR_CONTRACT = _TEAM_DIR / "scripts" / "lib" / "error_contract.sh"
_LIB_PP_RUN_OPS = _TEAM_DIR / "scripts" / "lib" / "pp_run_ops.sh"
_LIB_PREFLIGHT = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_preflight.sh"


def _write_hook(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_hook_call(
    tmp_path: pathlib.Path,
    hook_path: pathlib.Path,
    *,
    no_block: bool = False,
    inbox: bool = False,
    setup: str = "",
) -> object:
    """Source the real _run_release_hook() and invoke it against hook_path.

    The harness sources pp_run_ops.sh, which runs the hook. With *inbox* the
    call passes --inbox and the harness also sources the real inbox helper
    (release_preflight.sh), against the sandbox kanban root ``tmp_path/kanban``.
    *setup* is bash run just before the call.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    no_block_arg = " '--no-block'" if no_block else ""
    if inbox:
        no_block_arg = " '--inbox'"
    inbox_sources = f"source {str(_LIB_PREFLIGHT)!r}\n" if inbox else ""
    script = f"""\
set -uo pipefail
REPO_ROOT={str(repo_root)!r}
ACTIVE_RC='v9.9.9'
PROJECT_NAME='remedy-test-proj'
_CM_PROJECT_ROOT={str(tmp_path / "kanban_project")!r}
RC_BRANCH='rc/v9.9.9'
KANBAN_ROOT={str(tmp_path / "kanban")!r}
source {str(_LIB_ERROR_CONTRACT)!r}
source {str(_LIB_PP_RUN_OPS)!r}
{inbox_sources}
source {str(_LIB_HOOK_RUNNER)!r}
{setup}
_run_release_hook 'cm-release-pre-tag' {str(hook_path)!r}{no_block_arg}
"""
    kanban = tmp_path / "kanban"
    return run_bash(
        tmp_path,
        script,
        extra_env={
            "KANBAN_ROOT": str(kanban),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
            "PGAI_PROJECT_ROOT": "",
            "PGAI_TASKS_DIR": "",
        },
        timeout=60,
    )


def test_failing_hook_names_cause_with_path_and_rc(tmp_path: pathlib.Path) -> None:
    """Failure output names the hook path and its exit code as the Cause."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\necho 'hook stdout line' \nexit 7\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 1
    assert "Cause(s):" in result.stderr
    assert str(hook) in result.stderr
    assert "rc=7" in result.stderr


def test_failing_hook_names_next_diagnostic_step_not_a_command_block(
    tmp_path: pathlib.Path,
) -> None:
    """Non-deterministic exemption: no numbered 'To fix:' command block, but a
    'Next diagnostic step' pointer naming the printed [hook ...] lines."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 3\n")

    result = _run_hook_call(tmp_path, hook)
    assert "Next diagnostic step" in result.stderr
    assert "[hook cm-release-pre-tag]" in result.stderr
    # Exempt from the numbered fix-command block (this is NOT a full-remedy gate).
    assert "To fix:" not in result.stderr


def test_failing_hook_prints_rule_line(tmp_path: pathlib.Path) -> None:
    """Failure output states the one-line invariant the gate enforces."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 1\n")

    result = _run_hook_call(tmp_path, hook)
    assert "Rule:" in result.stderr
    assert "must exit 0" in result.stderr


def test_failing_hook_emits_dual_channel_error_contract(tmp_path: pathlib.Path) -> None:
    """The blocking failure path also emits the house-shape error/message/remedy
    triple via team/scripts/lib/error_contract.sh,
    in addition to the pre-existing Cause(s)/Next-diagnostic-step/Rule block —
    a forced hook failure must speak both channels."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 9\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 1
    assert "error:   cm-release.hook-failure.cm-release-pre-tag" in result.stderr
    assert "message: " in result.stderr
    assert "remedy:  " in result.stderr
    # Message names the exit code and phase in a stranger's words, not a bare id.
    assert "rc=9" in result.stderr


def test_no_block_failure_does_not_print_remedy_and_continues(
    tmp_path: pathlib.Path,
) -> None:
    """--no-block failures (post-tag) are a warning only; the new remedy
    block is specific to the blocking path and must not fire here."""
    hook = tmp_path / "hooks" / "cm-release-post-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 1\n")

    result = _run_hook_call(tmp_path, hook, no_block=True)
    assert result.returncode == 0
    assert "Cause(s):" not in result.stderr
    assert "WARNING: hook" in result.stderr


def test_passing_hook_reports_success(tmp_path: pathlib.Path) -> None:
    """A hook that exits 0 reports success and prints no remedy block."""
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 0\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 0
    assert "completed successfully" in result.stdout
    assert "Cause(s):" not in result.stdout + result.stderr


def _inbox_file(tmp_path: pathlib.Path) -> pathlib.Path:
    project = tmp_path / "kanban" / "projects" / "remedy-test-proj"
    project.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return project / "operator-inbox.md"


def test_inbox_failure_writes_one_entry_with_the_emitted_triple(tmp_path: pathlib.Path) -> None:
    """--inbox (the pre-tag phase): a blocking failure still exits 1 with its
    Cause block and error contract, and one operator inbox entry carries the
    same error key, message and remedy the contract printed."""
    inbox = _inbox_file(tmp_path)
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 5\n")

    result = _run_hook_call(tmp_path, hook, inbox=True)
    assert result.returncode == 1, result.stderr
    assert "Cause(s):" in result.stderr
    key = "cm-release.hook-failure.cm-release-pre-tag"
    lines = result.stderr.splitlines()
    at = lines.index(f"error:   {key}")
    message = lines[at + 1].removeprefix("message: ")
    remedy = lines[at + 2].removeprefix("remedy:  ")

    text = inbox.read_text(encoding="utf-8")
    assert text.count("### INBOX-") == 1, text
    entry = text.replace("\n  ", "\n")
    assert "- **Source:** rc:v9.9.9" in entry, entry
    assert key in entry and message in entry and f"Remedy: {remedy}" in entry, entry
    assert "No HALT file was written" in entry, entry
    assert "Operator inbox: INBOX-0001 records this stop" in result.stderr


def test_blocking_failure_without_inbox_writes_no_entry(tmp_path: pathlib.Path) -> None:
    """Without --inbox (the pre-squash phase, whose trigger 4 HALT writes the
    entry instead) a blocking failure leaves the inbox alone."""
    inbox = _inbox_file(tmp_path)
    hook = tmp_path / "hooks" / "cm-release-pre-squash.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 5\n")

    result = _run_hook_call(tmp_path, hook)
    assert result.returncode == 1
    assert not inbox.exists()
    assert "Operator inbox" not in result.stderr


def test_inbox_write_failure_keeps_the_exit_and_is_narrated(tmp_path: pathlib.Path) -> None:
    """An inbox that cannot be written changes nothing about the stop: the
    same exit 1 and Cause block, plus one WARNING naming the failed write."""
    _inbox_file(tmp_path).mkdir()
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, "#!/usr/bin/env bash\nexit 5\n")

    result = _run_hook_call(tmp_path, hook, inbox=True)
    assert result.returncode == 1
    assert "Cause(s):" in result.stderr
    assert "error:   cm-release.hook-failure.cm-release-pre-tag" in result.stderr
    assert "WARNING: the operator inbox entry for this stop" in result.stderr
    assert "could not be written" in result.stderr


# The two ways the runner can fail before the hook starts, each with the
# diagnostic its stop must carry: pp_pythonpath cannot compose a PYTHONPATH
# (no own-tree root), or the bounded runner is not importable from the one
# it composes (an own-tree root and a sandbox KANBAN_ROOT holding no package,
# and the hook's cwd, the sandbox repo, holding none either).
_RUNNER_FAULTS = [
    pytest.param("_PP_RUN_OPS_OWN_ROOT=''", "could not derive own-tree root", id="no-pythonpath"),
    pytest.param(
        "unset PYTHONPATH; _PP_RUN_OPS_OWN_ROOT=/nonexistent-own-root",
        "No module named",
        id="runner-not-importable",
    ),
]

_RUNNER_KEY = "cm-release.hook-runner-failure.cm-release-pre-tag"


def _marker_hook(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A hook that leaves a marker file when it runs, then fails."""
    marker = tmp_path / "hook-ran.txt"
    hook = tmp_path / "hooks" / "cm-release-pre-tag.sh"
    _write_hook(hook, f"#!/usr/bin/env bash\necho ran > {str(marker)!r}\nexit 7\n")
    return hook, marker


@pytest.mark.parametrize(("setup", "reason"), _RUNNER_FAULTS)
def test_runner_fault_names_the_runner_and_never_runs_the_hook(tmp_path: pathlib.Path, setup: str, reason: str) -> None:
    """A runner that cannot start the hook stops the phase under its own error
    key, names the runner and its diagnostic, and never runs or blames the hook."""
    hook, marker = _marker_hook(tmp_path)

    result = _run_hook_call(tmp_path, hook, setup=setup)

    assert result.returncode == 1, result.stderr
    assert not marker.exists(), "the hook ran although its runner could not start it"
    assert f"error:   {_RUNNER_KEY}" in result.stderr, result.stderr
    assert "cm-release.hook-failure." not in result.stderr, result.stderr
    assert str(hook) not in result.stdout + result.stderr, result.stderr
    assert "hook runner" in result.stderr and reason in result.stderr, result.stderr
    assert "Cause(s):" not in result.stderr and "running hook" not in result.stdout
    lines = result.stderr.splitlines()
    remedy = lines[lines.index(f"error:   {_RUNNER_KEY}") + 2].removeprefix("remedy:  ")
    assert str(_LIB_PP_RUN_OPS) in remedy and "<" not in remedy, remedy


# Only the bounded runner fails to start; the inbox writer, which shares
# pp_run_ops' PYTHONPATH, still works.
_BOUNDED_RUN_START_FAULT = (
    'python3() { if [[ "${2:-}" == pgai_agent_kanban.lib.bounded_run ]]; then '
    "echo 'bounded_run: simulated start failure' >&2; return 1; fi; command python3 \"$@\"; }"
)


def test_runner_fault_with_inbox_writes_the_runner_triple(tmp_path: pathlib.Path) -> None:
    """--inbox (the pre-tag phase): the runner's stop writes one inbox entry
    carrying the runner's error key, not the hook-failure key."""
    inbox = _inbox_file(tmp_path)
    hook, marker = _marker_hook(tmp_path)

    result = _run_hook_call(tmp_path, hook, inbox=True, setup=_BOUNDED_RUN_START_FAULT)

    assert result.returncode == 1, result.stderr
    assert not marker.exists()
    text = inbox.read_text(encoding="utf-8")
    assert text.count("### INBOX-") == 1, text
    assert _RUNNER_KEY in text and "cm-release.hook-failure." not in text, text
    assert "simulated start failure" in text, text


def test_runner_fault_under_no_block_is_a_warning(tmp_path: pathlib.Path) -> None:
    """--no-block (post-tag): a runner that cannot start the hook warns, names
    the runner, and the release continues without running the hook."""
    hook, marker = _marker_hook(tmp_path)

    result = _run_hook_call(tmp_path, hook, no_block=True, setup="_PP_RUN_OPS_OWN_ROOT=''")

    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert "WARNING: The release hook runner could not start" in result.stderr, result.stderr
    assert "error:   " not in result.stderr
