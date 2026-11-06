"""
test_pm_discovery_keeps_chain_lock.py
=====================================
wd_run_pm_discovery (team/scripts/lib/wake_driver_discovery.sh), the PM
wake's discovery branch, driven inside a stand-in for run_project_chain's
frame: each simulated firing takes the chain's repo lock exactly as the
chain does (pgai_lock_acquire, op wake-chain:<project>, task none), queues
four other wake chains on that same lock, runs wd_run_pm_discovery, then
performs the end-of-chain release.

  - On an idle chain with an eligible high bug, discovery writes the bundle
    in the firing that finds it, and the next firing queues the PM task for
    that bundle -- every write and the post-discovery dispatch under the
    chain's own hold, with no second pgai_lock_acquire, while the queued
    chains wait. They take the lock only after the end-of-chain release.
  - On a busy chain (an unfinished queued task, or an Active RC) discovery
    exits before any scan, writes nothing, and the chain still holds the
    lock until the end-of-chain release drops it.
  - When the chain's lock record no longer names this process, the write
    hook refuses: nothing is written and the chain marks the lock lost.

How it is observed. The "other chains" are flock(1) processes blocked on
the lock file (each started with the chain's fd closed, so none inherits
the hold); /proc/locks confirms they are queued before discovery starts.
After the chain has taken its lock, pgai_lock_acquire is replaced by a
recorder that refuses, so any second acquisition on the chain path is both
recorded and fatal to the write. Recording wrappers around the three
discovery writers, process_one_task and a fake pm-agent.sh each note
whether the chain's pid is the holder named in the lock's provenance at
that moment. Each firing runs in its own subshell, so the replaced
pgai_lock_acquire never outlives it.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_PROJECT = "project-a"
_CHAIN_BUSY_EXIT = f"discovery_run_pipeline [{_PROJECT}]: chain busy, idle exit before any scan"
_KEPT_LINE = f"project {_PROJECT}: repo lock kept through discovery; the discovery write uses the chain's hold"
_WAITERS = 4
_FAKE_PM_TASK = "PM-20260101-900-decompose-fixture"

# A stand-in pm-agent.sh for discovery Step 3: queues one PM task the way
# the real script does (task folder, pm_backlog line, "Folder :" output)
# and records whether the chain still holds the repo lock while it runs.
_FAKE_PM_AGENT = f"""#!/usr/bin/env bash
set -euo pipefail
if grep -qx "pid=${{T_CHAIN_PID}}" "$T_LOCK" 2>/dev/null; then
    echo "pm-agent HELD" >> "$T_REC"
else
    echo "pm-agent FREE" >> "$T_REC"
fi
tasks="${{KANBAN_ROOT}}/projects/${{PGAI_PROJECT_NAME}}/tasks"
mkdir -p "$tasks/{_FAKE_PM_TASK}"
printf '# Status\\n\\n## State\\nBACKLOG\\n' > "$tasks/{_FAKE_PM_TASK}/status.md"
echo "- [ ] {_FAKE_PM_TASK} — decompose" >> "$tasks/queues/pm_backlog.md"
echo "  Folder  : $tasks/{_FAKE_PM_TASK}"
"""

_PRELUDE = r"""
set -euo pipefail
log() { echo "[log] $*" >&2; }
source scripts/lib/ini_parser.sh
source scripts/lib/temp.sh
source scripts/lib/semver.sh
source scripts/lib/project_paths.sh
source scripts/lib/discovery.sh
source scripts/lib/lock_provenance.sh
source scripts/lib/wake_driver_discovery.sh
AGENT=pm
P="$PGAI_PROJECT_NAME"
T_LOCK="$(pp_repo_lock_path "$P")"
T_CHAIN_PID=$$
export T_LOCK T_REC T_CHAIN_PID
_t_holds() {
    if [[ -f "$T_LOCK" ]] && grep -qx "pid=${T_CHAIN_PID}" "$T_LOCK"; then echo HELD; else echo FREE; fi
}
for _t_fn in _disc_write_bundle _disc_set_status_field _disc_mark_cache_bundled; do
    eval "$(declare -f "$_t_fn" | sed "1s/^${_t_fn}/_t_real${_t_fn}/")"
    eval "${_t_fn}() { echo \"${_t_fn} \$(_t_holds)\" >> \"\$T_REC\"; _t_real${_t_fn} \"\$@\"; }"
done
# wake_driver.sh helpers the post-discovery dispatch calls.
for _t_fn in reconcile_stale_active reconcile_terminal_worktrees \
             reconcile_terminal_liveness_records recheck_waiting_tasks \
             recheck_blocked_tasks sweep_running_intake_census wd_chain_task_timeout_budget; do
    eval "${_t_fn}() { :; }"
done
process_one_task() { echo "process_one_task $(_t_holds)" >> "$T_REC"; return 1; }
TASKS_COMPLETED=0

# Blocked flock waiters on the lock file's current inode, from /proc/locks.
_t_queued_waiters() {
    local _ino
    _ino="$(stat -c %i "$T_LOCK" 2>/dev/null)" || { echo 0; return 0; }
    grep -E -- "->.*:${_ino} " /proc/locks 2>/dev/null | wc -l
}

# One PM wake firing on this project: take the chain lock, queue the other
# chains behind it, run discovery, release at the end of the chain.
# T_TAMPER=1 rewrites the lock record's pid before discovery runs.
_t_firing() {
    local _project_lock_file="$T_LOCK" _project_lock_fd="" _project_lock_lost=false
    local _dispatch_gated=false STOP_REASON="no more BACKLOG tasks in pm queue"
    local _i _waiter_pids=()
    pgai_lock_acquire "$_project_lock_file" pm "$$" "wake-chain:$P" none _project_lock_fd
    for (( _i = 1; _i <= T_WAITERS; _i++ )); do
        eval "flock -x \"\$_project_lock_file\" -c 'echo other-chain-$_i >> \"\$T_REC\"' ${_project_lock_fd}>&- &"
        _waiter_pids+=("$!")
    done
    for _i in $(seq 1 200); do
        (( $(_t_queued_waiters) >= T_WAITERS )) && break
        sleep 0.05
    done
    echo "queued_waiters=$(_t_queued_waiters)"
    pgai_lock_acquire() { echo "second pgai_lock_acquire $*" >> "$T_REC"; return 1; }
    if [[ "${T_TAMPER:-0}" == "1" ]]; then
        sed -i "s/^pid=.*/pid=1/" "$_project_lock_file"
    fi
    wd_run_pm_discovery "$P"
    echo "discovery_status=${DISCOVERY_LAST_STATUS}"
    echo "lock_lost=${_project_lock_lost}"
    echo "after_discovery $(_t_holds)" >> "$T_REC"
    echo "end_of_chain_release" >> "$T_REC"
    if [[ "$_project_lock_lost" == "true" ]]; then
        pgai_lock_abandon "$_project_lock_file" "$_project_lock_fd"
    else
        pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
    fi
    wait "${_waiter_pids[@]}"
}
"""


def _project(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT


def _add_bug(root: pathlib.Path) -> pathlib.Path:
    bugs = _project(root) / "bugs"
    bugs.mkdir(parents=True, exist_ok=True)
    path = bugs / "BUG-0001-sample-defect.md"
    path.write_text(
        "# BUG-0001: sample defect\n\n**Severity:** high\n\n## Status\nopen\n\n## Summary\nA bug.\n",
        encoding="utf-8",
    )
    return path


def _queue_task(root: pathlib.Path, agent: str, task_id: str, state: str) -> None:
    tasks = _project(root) / "tasks"
    (tasks / task_id).mkdir(parents=True, exist_ok=True)
    (tasks / task_id / "status.md").write_text(f"# Status\n\n## State\n{state}\n", encoding="utf-8")
    with (tasks / "queues" / f"{agent}_backlog.md").open("a", encoding="utf-8") as handle:
        handle.write(f"- [ ] {task_id} — queued by the fixture\n")


def _set_active_rc(root: pathlib.Path, version: str) -> None:
    (_project(root) / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{version}\n\n"
        "## RC Opened At\n2026-01-01T00:00:00Z\n\n## RC Opened By Task\nCM-1-open-rc\n",
        encoding="utf-8",
    )


def _install_fake_pm_agent(root: pathlib.Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    fake = scripts / "pm-agent.sh"
    fake.write_text(_FAKE_PM_AGENT, encoding="utf-8")
    fake.chmod(0o755)


def _bundles(root: pathlib.Path) -> list[pathlib.Path]:
    req = _project(root) / "requirements"
    return sorted(req.glob("*-bundle-*.md")) if req.is_dir() else []


def _lock_file(root: pathlib.Path) -> pathlib.Path:
    return root / "locks" / f"repo-wake-{_PROJECT}.lock"


def _run(root: pathlib.Path, tmp_path: pathlib.Path, body: str, *, tamper: bool = False):
    """Run *body* after the prelude against *root*; return the result and
    the record file's lines."""
    require_binary("flock", label="pm-discovery-keeps-chain-lock")
    if not pathlib.Path("/proc/locks").exists():
        pytest.skip("/proc/locks is not available; queued waiters cannot be confirmed")
    record = tmp_path / "chain.rec"
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "PGAI_PROJECT_NAME": _PROJECT,
            "T_REC": str(record),
            "T_WAITERS": str(_WAITERS),
            "T_TAMPER": "1" if tamper else "0",
        },
    )
    result = run_bash(tmp_path, _PRELUDE + body, base_env=env, timeout=120)
    recorded = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return result, recorded


def _firings(recorded: list[str]) -> list[list[str]]:
    """Split the record into one list per firing: everything up to its
    end_of_chain_release line, plus the queued chains' lines after it."""
    firings: list[list[str]] = []
    current: list[str] = []
    index = 0
    while index < len(recorded):
        current.append(recorded[index])
        if recorded[index] == "end_of_chain_release":
            current.extend(recorded[index + 1 : index + 1 + _WAITERS])
            firings.append(current)
            current = []
            index += 1 + _WAITERS
            continue
        index += 1
    if current:
        firings.append(current)
    return firings


def _assert_other_chains_waited(firing: list[str]) -> None:
    release = firing.index("end_of_chain_release")
    after = firing[release + 1 :]
    assert sorted(after) == sorted(
        f"other-chain-{i}" for i in range(1, _WAITERS + 1)
    ), f"every queued chain must take the lock once, after the end-of-chain release; firing: {firing}"
    assert not any(
        line.startswith("other-chain-") for line in firing[:release]
    ), f"no queued chain may take the lock while discovery runs; firing: {firing}"


# ---------------------------------------------------------------------------
# Idle chain: writes in the firing that finds the work, under the chain lock
# ---------------------------------------------------------------------------


def test_idle_chain_bundles_then_queues_pm_under_the_kept_chain_lock(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)
    _install_fake_pm_agent(root)

    result, recorded = _run(root, tmp_path, "( _t_firing )\n( _t_firing )\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.count(f"queued_waiters={_WAITERS}") == 2, result.stdout
    assert result.stdout.count("discovery_status=produced_work") == 2, result.stdout + result.stderr
    assert not [
        line for line in recorded if line.startswith("second pgai_lock_acquire")
    ], f"the chain path must not acquire the repo lock a second time; record: {recorded}"
    assert "repo lock not available for the discovery write" not in result.stderr
    assert "repo lock released" not in result.stderr, "the PM wake must not let go of its chain lock for discovery"
    assert result.stderr.count(_KEPT_LINE) == 2, result.stderr

    first, second = _firings(recorded)

    # Firing 1: the bug is bundled, every write under the chain's hold.
    writes = [line for line in first if line.startswith("_disc_")]
    assert {line.split()[0] for line in writes} == {
        "_disc_write_bundle",
        "_disc_set_status_field",
        "_disc_mark_cache_bundled",
    }, first
    assert all(line.endswith(" HELD") for line in writes), first
    assert "process_one_task HELD" in first, first
    assert "after_discovery HELD" in first, first
    _assert_other_chains_waited(first)

    # Firing 2: the PM task for that bundle is queued, under the chain's hold.
    assert "pm-agent HELD" in second, second
    assert "process_one_task HELD" in second, second
    _assert_other_chains_waited(second)

    assert len(_bundles(root)) == 1
    assert "## Status\nrunning" in bug.read_text(encoding="utf-8")
    pm_queue = (_project(root) / "tasks" / "queues" / "pm_backlog.md").read_text(encoding="utf-8")
    assert _FAKE_PM_TASK in pm_queue
    assert f"bundle marked running (PM task: {_FAKE_PM_TASK})" in result.stderr
    assert not _lock_file(root).exists(), "the end-of-chain release must drop the chain lock"


# ---------------------------------------------------------------------------
# Busy chain: exit before any scan; the lock is released at the end as today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("busy_by", ["queued_task", "active_rc"])
def test_busy_chain_exits_before_any_scan_and_releases_at_end_of_chain(two_project_root, tmp_path, busy_by):
    root = two_project_root
    bug = _add_bug(root)
    if busy_by == "queued_task":
        _queue_task(root, "cm", "CM-20260101-001-open-rc", "BACKLOG")
        cause = "cm_backlog task CM-20260101-001-open-rc is BACKLOG"
    else:
        _set_active_rc(root, "v0.1.0")
        cause = "chain busy — Active RC=v0.1.0"

    result, recorded = _run(root, tmp_path, "( _t_firing )\n")

    assert result.returncode == 0, result.stderr
    assert f"queued_waiters={_WAITERS}" in result.stdout, result.stdout
    assert "discovery_status=idle" in result.stdout
    assert _CHAIN_BUSY_EXIT in result.stderr, result.stderr
    assert cause in result.stderr[: result.stderr.index(_CHAIN_BUSY_EXIT)]
    assert "active ceilings" not in result.stderr
    assert "discovery_step_" not in result.stderr, "no step may scan on a busy chain"
    assert "post-discovery iteration" not in result.stderr
    assert not [
        line for line in recorded if line.startswith(("_disc_", "second pgai_lock_acquire", "process_one_task"))
    ], recorded
    assert "after_discovery HELD" in recorded, "the chain keeps its lock until the end-of-chain release"
    _assert_other_chains_waited(recorded)
    assert _bundles(root) == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")
    assert not _lock_file(root).exists(), "the end-of-chain release must drop the chain lock"


# ---------------------------------------------------------------------------
# Lost ownership: the write hook refuses instead of acquiring again
# ---------------------------------------------------------------------------


def test_write_hook_refuses_when_the_chain_no_longer_owns_the_lock(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)

    result, recorded = _run(root, tmp_path, "( _t_firing )\n", tamper=True)

    assert result.returncode == 0, result.stderr
    assert "discovery_status=idle" in result.stdout
    assert "lock_lost=true" in result.stdout
    assert "lock.provenance.reverify_lost" in result.stderr
    assert f"project {_PROJECT}: discovery write: repo lock ownership lost; not writing" in result.stderr
    assert (
        "discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake"
        in result.stderr
    )
    assert not [
        line for line in recorded if line.startswith(("_disc_", "second pgai_lock_acquire", "process_one_task"))
    ], recorded
    assert _bundles(root) == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")
