"""
test_overwatch_sweep_only_flag.py
=================================
``scripts/overwatch-sweep.sh`` options: ``--only <check-slug>`` and
``--lock-wait <seconds>``.

Covered:
  1. ``--only block-triage`` runs check-block-triage alone: the sweep log
     names no other check, and the blocked task is triaged.
  2. Accepted slug spellings (``check-`` prefix, ``.sh`` suffix, underscores,
     ``--only=``) resolve to the same module.
  3. ``--only nosuchcheck`` exits 2 with the dual-channel error / message /
     remedy, names the available check slugs, and sweeps nothing.
  4. Malformed command lines (``--only`` with no value, a non-numeric
     ``--lock-wait``, an unknown option) exit 2 dual-channel and sweep
     nothing.
  5. ``--help`` exits 0 and documents ``--only`` and ``--lock-wait``.
  6. ``--lock-wait``: a repo lock released a few seconds into the run is
     taken (the task is triaged, no refusal is logged); a lock held for the
     whole wait is given up on with one refusal row naming the holder, and
     the task is left unchanged.

Every fixture builds a sandbox kanban root under pytest's tmp_path; nothing
reads or writes the live install or a real provider.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap
import time

import pytest

from tests.unit.shell_harness import run_bash
from tests.unit.test_overwatch_block_triage import Sandbox, _PROJECT, _record, _section, _status

_SWEEP = "scripts/overwatch-sweep.sh"
_LOCK_PROVENANCE = "scripts/lib/lock_provenance.sh"
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def sandbox(tmp_path: pathlib.Path) -> Sandbox:
    sb = Sandbox(tmp_path / "kanban")
    (sb.root / "tmp").mkdir()
    return sb


def _sweep(sb: Sandbox, tmp_path: pathlib.Path, args: str, timeout: int = 300):
    return run_bash(tmp_path, f"bash {_SWEEP} {args}", extra_env=sb.env(), timeout=timeout)


def _checks_run(sb: Sandbox) -> list:
    path = sb.project_root / "logs" / "overwatch" / "sweep.log"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return re.findall(r"\tcheck-(?:ok|error|source-error|fn-missing)\tnone\t(\S+)", text)


def _assert_refused(result, error_key: str) -> None:
    assert result.returncode == 2, f"rc={result.returncode}\n{result.stderr}"
    assert f"error:   {error_key}" in result.stderr, result.stderr
    assert re.search(r"^message: \S", result.stderr, flags=re.M), result.stderr
    remedy = re.search(r"^remedy:  (.+)$", result.stderr, flags=re.M)
    assert remedy and "overwatch-sweep.sh" in remedy.group(1), result.stderr
    assert "<" not in remedy.group(1), "a remedy must be runnable as printed"


# ---------------------------------------------------------------------------
# 1-2. --only runs one check
# ---------------------------------------------------------------------------


def test_only_block_triage_runs_that_check_alone(sandbox, tmp_path):
    sandbox.seed(_status(_record("plugin_load_failed")))
    result = _sweep(sandbox, tmp_path, "--only block-triage")
    assert result.returncode == 0, result.stderr
    assert _checks_run(sandbox) == ["block-triage"], result.stderr
    assert "block-triage:plugin_load_failed:escalate" in sandbox.actions
    assert _section(sandbox.status.read_text(encoding="utf-8"), "Needs Human") == "yes"
    assert "(only block-triage)" in sandbox.actions


@pytest.mark.parametrize("spelling", ["--only check-block-triage.sh", "--only=block_triage", "--only ' block-triage '"])
def test_only_accepts_the_module_name_spellings(sandbox, tmp_path, spelling):
    sandbox.seed(_status(_record("plugin_load_failed")))
    result = _sweep(sandbox, tmp_path, spelling)
    assert result.returncode == 0, result.stderr
    assert _checks_run(sandbox) == ["block-triage"], result.stderr


# ---------------------------------------------------------------------------
# 3-4. Refusals
# ---------------------------------------------------------------------------


def test_unknown_only_slug_is_refused_and_names_the_available_slugs(sandbox, tmp_path):
    sandbox.seed(_status(_record("plugin_load_failed")))
    before = sandbox.snapshot()
    result = _sweep(sandbox, tmp_path, "--only nosuchcheck")
    _assert_refused(result, "overwatch_sweep.unknown_check")
    message = re.search(r"^message: (.+)$", result.stderr, flags=re.M).group(1)
    assert "nosuchcheck" in message
    available = sorted(
        p.name[len("check-"):-len(".sh")]
        for p in (_TEAM_DIR / "scripts" / "lib" / "overwatch-checks").glob("check-*.sh")
    )
    for slug in available:
        assert slug in message, f"{slug} missing from: {message}"
    assert "--only block-triage" in result.stderr
    assert _checks_run(sandbox) == []
    assert sandbox.snapshot() == before
    assert "sweep-start" not in sandbox.actions


@pytest.mark.parametrize(
    "args, error_key",
    [
        ("--only", "overwatch_sweep.option_value_required"),
        ("--lock-wait", "overwatch_sweep.option_value_required"),
        ("--lock-wait soon", "overwatch_sweep.invalid_lock_wait"),
        ("--lock-wait=-5", "overwatch_sweep.invalid_lock_wait"),
        ("--everything", "overwatch_sweep.unknown_argument"),
    ],
)
def test_malformed_command_lines_are_refused(sandbox, tmp_path, args, error_key):
    sandbox.seed(_status(_record("plugin_load_failed")))
    result = _sweep(sandbox, tmp_path, args)
    _assert_refused(result, error_key)
    assert _checks_run(sandbox) == []


def test_help_documents_only_and_lock_wait(tmp_path):
    result = run_bash(tmp_path, f"bash {_SWEEP} --help", timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--only <check-slug>" in result.stdout
    assert "--lock-wait <seconds>" in result.stdout
    assert "Available check slugs:" in result.stdout and "block-triage" in result.stdout


# ---------------------------------------------------------------------------
# 6. --lock-wait
# ---------------------------------------------------------------------------


def _base_env() -> dict:
    """PATH and HOME for a Popen-launched sweep (run_bash supplies its own)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/")}


class _LockHolder:
    """A separate process holding the project's repo lock as a wake chain
    would, until release() is called."""

    def __init__(self, sb: Sandbox) -> None:
        lock_file = sb.root / "locks" / f"repo-wake-{_PROJECT}.lock"
        self.ready = sb.root / "locks" / "holder-ready"
        self.done = sb.root / "locks" / "holder-done"
        script = textwrap.dedent(f"""\
            source {_LOCK_PROVENANCE}
            fd=""
            pgai_lock_acquire "{lock_file}" coder "$$" "wake-chain:{_PROJECT}" none fd
            touch "{self.ready}"
            while [[ ! -f "{self.done}" ]]; do sleep 0.05; done
            pgai_lock_release "{lock_file}" "$fd"
        """)
        self.proc = subprocess.Popen(["bash", "-c", script], cwd=str(_TEAM_DIR))
        deadline = time.time() + 10
        while not self.ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert self.ready.exists(), "the lock holder did not start"

    def release(self) -> None:
        self.done.touch()
        self.proc.wait(timeout=10)


def test_lock_released_during_the_wait_is_taken(sandbox, tmp_path):
    sandbox.seed(_status(_record("plugin_load_failed")))
    holder = _LockHolder(sandbox)
    try:
        sweep = subprocess.Popen(
            ["bash", _SWEEP, "--only", "block-triage", "--lock-wait", "30"],
            cwd=str(_TEAM_DIR), env={**_base_env(), **sandbox.env()},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        time.sleep(3)
    finally:
        holder.release()
    out, _ = sweep.communicate(timeout=300)
    assert sweep.returncode == 0, out
    assert "block-triage:plugin_load_failed:escalate" in sandbox.actions, out
    assert "block-triage-refused" not in sandbox.actions, out
    assert "still contended" not in out, out


def test_lock_held_for_the_whole_wait_is_given_up_naming_the_holder(sandbox, tmp_path):
    sandbox.seed(_status(_record("plugin_load_failed")))
    before = sandbox.snapshot()
    holder = _LockHolder(sandbox)
    try:
        started = time.time()
        result = _sweep(sandbox, tmp_path, "--only block-triage --lock-wait 3")
        elapsed = time.time() - started
    finally:
        holder.release()
    assert result.returncode == 0, result.stderr
    assert elapsed >= 3, f"the sweep gave up after {elapsed:.1f}s, before its 3s wait"
    assert "still contended after retrying for up to 3s" in result.stderr, result.stderr
    assert sandbox.snapshot() == before
    refusals = [line for line in sandbox.actions.splitlines() if "block-triage-refused" in line]
    assert len(refusals) == 1, sandbox.actions
    assert "repo lock held by holder=coder" in refusals[0] and f"wake-chain:{_PROJECT}" in refusals[0]
    assert "block-triage:" not in sandbox.actions
