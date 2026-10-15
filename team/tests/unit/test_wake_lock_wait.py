"""
test_wake_lock_wait.py
======================
The wake chain's repo-lock wait and the lock-starvation guard
(team/scripts/lib/wake_lock_wait.sh), driven through the real bash
functions with a controlled clock.

Time. lock_provenance.sh reads time through ``pgai_lock_wait_now`` and
pauses through ``pgai_lock_wait_pause``; every script here redefines both
after sourcing, so a fake clock advances by exactly the pause asked for and
a two-minute wait runs in well under a second of real time. A lock holder
is a real separate process holding a real flock; the fake pause releases it
once the fake clock reaches the holder's release time, and waits for the
release to land before returning -- so "the holder lets go at T+80" is a
fact of the fake timeline, not a race.

Covered:
  1. A wake-chain holder that lets go 20 s (fake) into a 120 s wait: the
     waiter takes the lock, with the chain's own provenance recorded.
  2. A wake-chain holder that keeps the lock past lock_wait_seconds: the
     waiter gives up at the budget, never later, and says so.
  3. A holder that is not a wake chain is not waited on: no pause at all.
  4. A refused acquisition (blank holder) returns 2 at once, no pause.
  5. The wait never exceeds the remaining batch runtime budget.
  6. The operator stop file ends the wait before the first pause.
  7. One lock_wait_seconds budget per project per firing: a second attempt
     in the same process waits only for what is left, here nothing.
  8. Starvation guard: three deferrals with a BACKLOG task log the warning
     on the third and flag the role for PM; taking the lock, or deferring
     with no BACKLOG task, resets the count; a threshold of 0 is off.

Every path lives under pytest's tmp_path; every subprocess environment is
built from an explicit set (build_scrubbed_env), never the operator's.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap
import time

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "lockwait"
_T0 = 1_800_000_000  # the fake clock's epoch when the holder took the lock
_CM_TASK = "CM-20260101-001-open-rc-0-1-1"


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict:
    return build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        },
    )


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal kanban root with one project and empty role queues."""
    root = tmp_path / "root"
    queues = root / "projects" / _PROJECT / "tasks" / "queues"
    queues.mkdir(parents=True)
    (root / "locks").mkdir()
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    for agent in ("pm", "cm", "coder", "writer", "tester"):
        (queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n\n", encoding="utf-8")
    return root


def _queue_task(root: pathlib.Path, role: str, task_id: str, state: str) -> None:
    tasks = root / "projects" / _PROJECT / "tasks"
    folder = tasks / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    (tasks / "queues" / f"{role}_backlog.md").write_text(
        f"# {role} backlog\n\n- [ ] {task_id} — queued\n", encoding="utf-8"
    )


def _lock_file(root: pathlib.Path) -> pathlib.Path:
    return root / "locks" / f"repo-wake-{_PROJECT}.lock"


class _Holder:
    """A separate process holding the repo lock with *op* until released."""

    def __init__(self, tmp_path: pathlib.Path, env: dict, lock_file: pathlib.Path, op: str) -> None:
        self.ready = tmp_path / "holder-ready"
        self.done = tmp_path / "holder-done"
        script = textwrap.dedent(f"""\
            source scripts/lib/lock_provenance.sh
            fd=""
            pgai_lock_acquire "{lock_file}" pm "$$" "{op}" none fd || exit 9
            touch "{self.ready}"
            for _ in $(seq 1 1200); do [[ -f "{self.done}" ]] && break; sleep 0.05; done
            pgai_lock_release "{lock_file}" "$fd"
        """)
        self.proc = subprocess.Popen(["bash", "-c", script], cwd=str(_TEAM_DIR), env=env)
        deadline = time.time() + 10
        while not self.ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert self.ready.exists(), "the lock holder did not start"

    def stop(self) -> None:
        self.done.touch()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _preamble(root: pathlib.Path, tmp_path: pathlib.Path, *, now: int, release_at: int | None,
              wake_start: int, max_runtime: int = 14400, agent: str = "cm") -> str:
    """Source the stage and its real dependencies, then install the fake
    clock, a log() that prints, and the stop-file check the wake defines."""
    release = "" if release_at is None else str(release_at)
    return textwrap.dedent(f"""\
        set -uo pipefail
        source scripts/lib/ini_parser.sh
        source scripts/lib/temp.sh
        source scripts/lib/project_paths.sh
        source scripts/lib/discovery.sh
        source scripts/lib/lock_provenance.sh
        source scripts/lib/wake_lock_wait.sh
        AGENT="{agent}"
        LOG_FILE=/dev/null
        STOP_FILE="{tmp_path / 'stop'}"
        log() {{ echo "LOG: $*"; }}
        wake_stop_file_present() {{ [[ -n "${{STOP_FILE:-}}" && -f "$STOP_FILE" ]]; }}
        FAKE_NOW={now}
        RELEASE_AT="{release}"
        PAUSES=0
        pgai_lock_wait_now() {{ echo "$FAKE_NOW"; }}
        pgai_lock_wait_pause() {{
            PAUSES=$(( PAUSES + 1 ))
            FAKE_NOW=$(( FAKE_NOW + $1 ))
            if [[ -n "$RELEASE_AT" ]] && (( FAKE_NOW >= RELEASE_AT )) && [[ ! -f "{tmp_path / 'holder-done'}" ]]; then
                touch "{tmp_path / 'holder-done'}"
                for _ in $(seq 1 200); do [[ -e "{_lock_file(root)}" ]] || break; sleep 0.05; done
            fi
        }}
        WAKE_START_EPOCH={wake_start}
        MAX_RUNTIME_SECONDS={max_runtime}
    """)


_ACQUIRE = textwrap.dedent(f"""\
    lock_fd=""
    start=$FAKE_NOW
    rc=0
    wlw_acquire_chain_lock {_PROJECT} "$(pp_repo_lock_path {_PROJECT})" lock_fd || rc=$?
    echo "RC=$rc PAUSES=$PAUSES WAITED=$(( FAKE_NOW - start )) FD=$lock_fd"
    if [[ $rc -eq 0 ]]; then
        cat "$(pp_repo_lock_path {_PROJECT})"
        pgai_lock_release "$(pp_repo_lock_path {_PROJECT})" "$lock_fd"
    fi
""")


def _field(stdout: str, key: str) -> str:
    for token in stdout.split():
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1]
    return ""


def _run_against_holder(tmp_path, *, op: str, release_at: int | None, now: int,
                        wake_start: int | None = None, max_runtime: int = 14400,
                        extra: str = "", body: str = _ACQUIRE, agent: str = "cm"):
    root = _root(tmp_path)
    env = _env(tmp_path, root)
    holder = _Holder(tmp_path, env, _lock_file(root), op)
    script = _preamble(
        root, tmp_path, now=now, release_at=release_at,
        wake_start=now if wake_start is None else wake_start,
        max_runtime=max_runtime, agent=agent,
    ) + extra + body
    started = time.monotonic()
    try:
        result = run_bash(tmp_path, script, base_env=env, timeout=120)
    finally:
        holder.stop()
    return result, time.monotonic() - started


def test_wake_chain_holder_released_during_the_wait_is_acquired(tmp_path):
    # Holder took the lock at T0 and lets go at T0+80; the waiter starts at T0+60.
    result, real_seconds = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=_T0 + 80, now=_T0 + 60,
    )
    out = result.stdout
    assert _field(out, "RC") == "0", out + result.stderr
    assert 20 <= int(_field(out, "WAITED")) < 120, out
    assert _field(out, "FD"), "the caller's fd variable was not set"
    assert "holder=cm" in out and f"op=wake-chain:{_PROJECT}" in out and "task=none" in out, out
    assert f"LOG: project {_PROJECT}: waiting up to 120s for the repo lock held by wake chain holder=pm" in out, out
    assert f"LOG: project {_PROJECT}: repo lock acquired after waiting" in out, out
    assert real_seconds < 60, f"a stubbed 20 s wait took {real_seconds:.1f}s of real time"


def test_wake_chain_holder_past_the_budget_is_given_up_at_the_budget(tmp_path):
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=None, now=_T0 + 60,
    )
    out = result.stdout
    assert _field(out, "RC") == "1", out + result.stderr
    assert int(_field(out, "WAITED")) == 120, out
    assert f"LOG: project {_PROJECT}: repo lock still held after waiting 120s (lock_wait_seconds=120)" in out, out
    assert result.stderr.count("error:   lock.provenance.contended") == 1, result.stderr


def test_configured_lock_wait_seconds_sets_the_budget(tmp_path):
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=None, now=_T0,
        extra="LOCK_WAIT_SECONDS=30\n",
    )
    assert _field(result.stdout, "RC") == "1", result.stdout + result.stderr
    assert int(_field(result.stdout, "WAITED")) == 30, result.stdout


def test_holder_that_is_not_a_wake_chain_is_not_waited_on(tmp_path):
    result, _ = _run_against_holder(
        tmp_path, op="overwatch-halt-first-fix:check", release_at=_T0 + 80, now=_T0 + 60,
    )
    out = result.stdout
    assert _field(out, "RC") == "1", out + result.stderr
    assert _field(out, "PAUSES") == "0" and _field(out, "WAITED") == "0", out
    assert f"LOG: project {_PROJECT}: not waiting for the repo lock: its holder is not a wake chain" in out, out
    assert "waiting up to" not in out, out


def test_refused_acquisition_is_not_waited_on(tmp_path):
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=_T0 + 80, now=_T0 + 60, agent="",
    )
    out = result.stdout
    assert _field(out, "RC") == "2", out + result.stderr
    assert _field(out, "PAUSES") == "0", out
    assert "lock.provenance.incomplete" in result.stderr, result.stderr


def test_wait_never_exceeds_the_remaining_batch_budget(tmp_path):
    # Batch of 100 s started 70 s ago: 30 s left, well under lock_wait_seconds.
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=None, now=_T0 + 60,
        wake_start=_T0 - 10, max_runtime=100,
    )
    out = result.stdout
    assert _field(out, "RC") == "1", out + result.stderr
    assert int(_field(out, "WAITED")) == 30, out
    assert "waiting up to 30s" in out and "batch budget left=30s" in out, out


def test_exhausted_batch_budget_means_a_single_attempt(tmp_path):
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=None, now=_T0 + 60,
        wake_start=_T0 - 100, max_runtime=100,
    )
    out = result.stdout
    assert _field(out, "RC") == "1", out + result.stderr
    assert _field(out, "PAUSES") == "0", out
    assert "no repo-lock wait left this firing" in out, out


def test_stop_file_ends_the_wait(tmp_path):
    (tmp_path / "stop").write_text("", encoding="utf-8")
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=_T0 + 80, now=_T0 + 60,
    )
    out = result.stdout
    assert _field(out, "RC") == "1", out + result.stderr
    assert _field(out, "PAUSES") == "0", out
    assert f"LOG: project {_PROJECT}: not waiting for the repo lock: stop file present at {tmp_path / 'stop'}" in out, out


def test_one_wait_budget_per_project_per_firing(tmp_path):
    twice = _ACQUIRE + 'echo "---"\n' + _ACQUIRE
    result, _ = _run_against_holder(
        tmp_path, op=f"wake-chain:{_PROJECT}", release_at=None, now=_T0, body=twice,
    )
    first, second = result.stdout.split("---\n")
    assert _field(first, "RC") == "1" and int(_field(first, "WAITED")) == 120, first
    assert _field(second, "RC") == "1", second
    assert _field(second, "PAUSES") == _field(first, "PAUSES"), "the second attempt must not pause"
    assert int(_field(second, "WAITED")) == 0, second
    assert "no repo-lock wait left this firing (waited 120s of lock_wait_seconds=120" in second, second


# ---------------------------------------------------------------------------
# Starvation guard
# ---------------------------------------------------------------------------


def _starvation(tmp_path, body: str, *, task_state: str | None = "BACKLOG", extra: str = ""):
    root = _root(tmp_path)
    if task_state is not None:
        _queue_task(root, "cm", _CM_TASK, task_state)
    script = _preamble(root, tmp_path, now=_T0, release_at=None, wake_start=_T0) + extra + body
    result = run_bash(tmp_path, script, base_env=_env(tmp_path, root), timeout=60)
    return result, root


_PM_CHECK = textwrap.dedent(f"""\
    if wlw_starved_role_ready {_PROJECT}; then
        echo "STARVED role=$WLW_STARVED_ROLE task=$WLW_STARVED_TASK deferrals=$WLW_STARVED_DEFERRALS"
    else
        echo "NOT-STARVED"
    fi
""")

_WARNING = (
    f"LOG: project {_PROJECT}: WARNING: lock starvation: cm has deferred on repo-lock contention "
    f"for 3 consecutive firing(s) while its task {_CM_TASK} is BACKLOG -- "
    "PM wakes skip discovery while that task is ready"
)


def test_third_consecutive_deferral_warns_and_flags_the_role_for_pm(tmp_path):
    body = (
        f"wlw_starvation_note_deferral {_PROJECT}\n{_PM_CHECK}"
        f"wlw_starvation_note_deferral {_PROJECT}\n{_PM_CHECK}"
        f"wlw_starvation_note_deferral {_PROJECT}\n{_PM_CHECK}"
    )
    result, root = _starvation(tmp_path, body)
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    lines = out.splitlines()
    assert f"LOG: project {_PROJECT}: lock starvation count for cm: 1 of 3 consecutive contention deferral(s) with task {_CM_TASK} BACKLOG" in lines
    assert lines.count("NOT-STARVED") == 2, out
    assert out.count("WARNING: lock starvation") == 1 and _WARNING in lines, out
    assert lines[-1] == f"STARVED role=cm task={_CM_TASK} deferrals=3", out
    state = (root / "locks" / "lock-starvation" / _PROJECT / "cm.state").read_text(encoding="utf-8")
    assert "deferrals=3\n" in state and f"task={_CM_TASK}\n" in state, state


def test_taking_the_lock_resets_the_count(tmp_path):
    body = (
        f"for _ in 1 2 3; do wlw_starvation_note_deferral {_PROJECT}; done\n"
        f"wlw_starvation_note_acquired {_PROJECT}\n{_PM_CHECK}"
        f"wlw_starvation_note_deferral {_PROJECT}\n"
    )
    result, root = _starvation(tmp_path, body)
    out = result.stdout
    assert f"LOG: project {_PROJECT}: lock starvation count for cm reset: repo lock acquired" in out, out
    assert "NOT-STARVED" in out, out
    assert out.rstrip().endswith("1 of 3 consecutive contention deferral(s) with task " + _CM_TASK + " BACKLOG"), out


def test_deferral_without_a_backlog_task_resets_and_is_not_counted(tmp_path):
    root = _root(tmp_path)
    _queue_task(root, "cm", _CM_TASK, "BACKLOG")
    flip = (
        f"printf '# Status\\n\\n## State\\nWAITING\\n' > "
        f"\"{root / 'projects' / _PROJECT / 'tasks' / _CM_TASK / 'status.md'}\"\n"
    )
    body = (
        f"wlw_starvation_note_deferral {_PROJECT}\n" + flip
        + f"wlw_starvation_note_deferral {_PROJECT}\n{_PM_CHECK}"
    )
    script = _preamble(root, tmp_path, now=_T0, release_at=None, wake_start=_T0) + body
    result = run_bash(tmp_path, script, base_env=_env(tmp_path, root), timeout=60)
    out = result.stdout
    assert f"LOG: project {_PROJECT}: lock starvation count for cm reset: no BACKLOG task" in out, out
    assert "NOT-STARVED" in out, out
    assert not (root / "locks" / "lock-starvation" / _PROJECT / "cm.state").exists()


def test_pm_is_not_held_once_the_starved_task_is_no_longer_ready(tmp_path):
    root = _root(tmp_path)
    _queue_task(root, "cm", _CM_TASK, "BACKLOG")
    flip = (
        f"printf '# Status\\n\\n## State\\nWORKING\\n' > "
        f"\"{root / 'projects' / _PROJECT / 'tasks' / _CM_TASK / 'status.md'}\"\n"
    )
    body = f"for _ in 1 2 3; do wlw_starvation_note_deferral {_PROJECT}; done\n" + flip + _PM_CHECK
    script = _preamble(root, tmp_path, now=_T0, release_at=None, wake_start=_T0) + body
    result = run_bash(tmp_path, script, base_env=_env(tmp_path, root), timeout=60)
    assert result.stdout.splitlines()[-1] == "NOT-STARVED", result.stdout


def test_threshold_zero_turns_the_guard_off(tmp_path):
    body = f"for _ in 1 2 3 4; do wlw_starvation_note_deferral {_PROJECT}; done\n{_PM_CHECK}"
    result, root = _starvation(tmp_path, body, extra="LOCK_STARVATION_FIRINGS=0\n")
    out = result.stdout
    assert "WARNING" not in out and out.splitlines()[-1] == "NOT-STARVED", out
    assert not (root / "locks" / "lock-starvation" / _PROJECT / "cm.state").exists()
