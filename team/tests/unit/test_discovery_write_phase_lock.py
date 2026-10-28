"""
test_discovery_write_phase_lock.py
==================================
discovery_run_pipeline (team/scripts/lib/discovery.sh) driven directly, the
way pm-agent.sh --auto calls it, on a two-project fixture root:

  - a busy chain (a non-terminal task in any role queue, or an Active RC)
    idle-exits before any scan: the chain-busy exit line is logged, no
    ceilings line or Step 1/2 narration precedes it, nothing is bundled,
    and the repo lock is never taken;
  - on an idle chain every discovery write (the bundle, the intake item's
    ## Status, its cache marker) happens while the repo lock is held, and a
    lock discovery took itself is released before the call returns;
  - a caller-supplied DISCOVERY_WRITE_LOCK_HOOK is called once, at the
    first write, and its lock is left to the caller;
  - a write phase that cannot take the lock, or finds the chain busy under
    it, writes nothing and stops the iteration -- no later category writes
    ahead of the deferred one;
  - a carried bug (``running``, listed only by a closed bundle) is parked on
    an operator question only inside the write phase: a refused lock leaves
    the bug file and the operator inbox as they were, and a granted one
    converts it while the repo lock is held;
  - with no hook, discovery's own write lock waits up to lock_wait_seconds
    for another holder: a holder that lets go within the budget is waited
    out and the write lands; one that keeps the lock past it defers the
    write with the deferral line. The wait runs on a controlled clock
    (pgai_lock_wait_now / pgai_lock_wait_pause redefined), so no real
    seconds pass.

Every write is observed through a recording wrapper around the three
discovery writers and the carried-item conversion: each call records
whether this shell's pid is the holder named in the repo lock's provenance
record at that moment.
"""

from __future__ import annotations

import pathlib

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.two_project import add_registered_project
from tests.unit.shell_harness import run_bash

_PROJECT = "project-a"
_CHAIN_BUSY_EXIT = f"discovery_run_pipeline [{_PROJECT}]: chain busy, idle exit before any scan"

# Recording wrappers: rename each writer and record the repo lock's holder
# state on every call. Defined after discovery.sh is sourced.
_RECORDING_WRAPPERS = r"""
_t_record() {
    if [[ -f "$LOCK" ]] && grep -qx "pid=$$" "$LOCK"; then
        echo "$1 HELD" >> "$REC"
    else
        echo "$1 FREE" >> "$REC"
    fi
}
for _t_fn in _disc_write_bundle _disc_set_status_field _disc_mark_cache_bundled; do
    eval "$(declare -f "$_t_fn" | sed "1s/^${_t_fn}/_t_real${_t_fn}/")"
    eval "${_t_fn}() { _t_record ${_t_fn}; _t_real${_t_fn} \"\$@\"; }"
done
eval "$(declare -f pp_run_ops | sed "1s/^pp_run_ops/_t_real_pp_run_ops/")"
pp_run_ops() {
    if [[ "${1:-}" == "pgai_agent_kanban.discovery.carried" && "${2:-}" == "convert" ]]; then
        _t_record carried_convert
    fi
    _t_real_pp_run_ops "$@"
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


def _add_priority(root: pathlib.Path) -> pathlib.Path:
    prio = _project(root) / "priority"
    prio.mkdir(parents=True, exist_ok=True)
    path = prio / "PRIORITY-0001-sample-item.md"
    path.write_text("# PRIORITY-0001\n\n## Status\nopen\n\n## Summary\nAn item.\n", encoding="utf-8")
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


def _bundles(root: pathlib.Path) -> list[pathlib.Path]:
    req = _project(root) / "requirements"
    return sorted(req.glob("*-bundle-*.md")) if req.is_dir() else []


def _lock_file(root: pathlib.Path) -> pathlib.Path:
    return root / "locks" / f"repo-wake-{_PROJECT}.lock"


def _run(
    root: pathlib.Path,
    tmp_path: pathlib.Path,
    body: str,
    extra_env: dict[str, str] | None = None,
    project: str = _PROJECT,
):
    """Source the discovery libs against *root*, install the recording
    wrappers, then run *body*. LOCK names *project*'s repo lock. The record
    file is returned alongside."""
    record = tmp_path / "writes.rec"
    script = (
        "set -euo pipefail\n"
        "source scripts/lib/ini_parser.sh\n"
        "source scripts/lib/temp.sh\n"
        "source scripts/lib/semver.sh\n"
        "source scripts/lib/project_paths.sh\n"
        "source scripts/lib/discovery.sh\n"
        f'LOCK="$(pp_repo_lock_path {project})"\n'
        f'REC="{record}"\n'
        + _RECORDING_WRAPPERS
        + body
        + '\necho "DISCOVERY_STATUS=${DISCOVERY_LAST_STATUS}"\n'
    )
    env = build_scrubbed_env(
        home_dir=tmp_path,
        extra_env={
            "KANBAN_ROOT": str(root),
            "TEAM_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "PGAI_PROJECT_NAME": project,
            **(extra_env or {}),
        },
    )
    result = run_bash(tmp_path, script, base_env=env, timeout=120)
    recorded = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return result, recorded


def _before(text: str, needle: str) -> str:
    assert needle in text, f"expected {needle!r} in output:\n{text}"
    return text[: text.index(needle)]


# ---------------------------------------------------------------------------
# Busy chain: idle exit before any scan, no lock
# ---------------------------------------------------------------------------


def test_backlog_task_in_another_role_idle_exits_before_any_scan(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)
    _queue_task(root, "cm", "CM-20260101-001-open-rc", "BACKLOG")

    result, recorded = _run(root, tmp_path, f'discovery_run_pipeline "{_PROJECT}"')

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    ahead = _before(result.stderr, _CHAIN_BUSY_EXIT)
    assert "cm_backlog task CM-20260101-001-open-rc is BACKLOG" in ahead
    assert "active ceilings" not in result.stderr, "the ceilings line belongs after the busy exit"
    assert "discovery_step_" not in result.stderr, "no step may scan on a busy chain"
    assert _bundles(root) == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")
    assert recorded == []
    assert not _lock_file(root).exists(), "a busy-chain exit must not take the repo lock"


def test_active_rc_idle_exits_before_any_scan(two_project_root, tmp_path):
    root = two_project_root
    _add_bug(root)
    _set_active_rc(root, "v0.1.0")

    result, _ = _run(root, tmp_path, f'discovery_run_pipeline "{_PROJECT}"')

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    ahead = _before(result.stderr, _CHAIN_BUSY_EXIT)
    assert "chain busy — Active RC=v0.1.0" in ahead
    assert "discovery_step_" not in result.stderr
    assert _bundles(root) == []


# ---------------------------------------------------------------------------
# Idle chain: writes under the lock
# ---------------------------------------------------------------------------


def test_idle_chain_writes_only_under_the_repo_lock_and_releases_it(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)
    _queue_task(root, "coder", "CODER-20260101-001-finished", "DONE")

    result, recorded = _run(root, tmp_path, f'discovery_run_pipeline "{_PROJECT}"')

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=produced_work" in result.stdout
    assert len(_bundles(root)) == 1
    assert "## Status\nrunning" in bug.read_text(encoding="utf-8")
    writers = {line.split()[0] for line in recorded}
    assert writers == {"_disc_write_bundle", "_disc_set_status_field", "_disc_mark_cache_bundled"}
    assert all(line.endswith(" HELD") for line in recorded), recorded
    assert "discovery_step_bugs: repo lock held for the discovery write" in result.stderr
    assert not _lock_file(root).exists(), "discovery must release the lock it took itself"


def test_hook_is_called_once_and_its_lock_is_left_to_the_caller(two_project_root, tmp_path):
    root = two_project_root
    _add_bug(root)
    hook = (
        "source scripts/lib/lock_provenance.sh\n"
        "HOOK_CALLS=0\n"
        "HOOK_FD=''\n"
        "t_hook() {\n"
        "    HOOK_CALLS=$((HOOK_CALLS + 1))\n"
        '    pgai_lock_acquire "$LOCK" pm "$$" "wake-chain:$1" none HOOK_FD\n'
        "}\n"
        "DISCOVERY_WRITE_LOCK_HOOK=t_hook\n"
        f'discovery_run_pipeline "{_PROJECT}"\n'
        'echo "HOOK_CALLS=${HOOK_CALLS}"\n'
        'grep -qx "pid=$$" "$LOCK" && echo "CALLER_STILL_HOLDS=yes"\n'
        'pgai_lock_release "$LOCK" "$HOOK_FD"\n'
    )

    result, recorded = _run(root, tmp_path, hook)

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=produced_work" in result.stdout
    assert "HOOK_CALLS=1" in result.stdout
    assert "CALLER_STILL_HOLDS=yes" in result.stdout
    assert recorded and all(line.endswith(" HELD") for line in recorded), recorded


# ---------------------------------------------------------------------------
# Deferred write phase: nothing written, iteration stops
# ---------------------------------------------------------------------------


def test_unavailable_lock_defers_the_write_and_no_later_category_runs(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)
    priority = _add_priority(root)
    body = (
        "t_hook_refuses() { return 1; }\n"
        "DISCOVERY_WRITE_LOCK_HOOK=t_hook_refuses\n"
        f'discovery_run_pipeline "{_PROJECT}"\n'
    )

    result, recorded = _run(root, tmp_path, body)

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake" in result.stderr
    assert "discovery_step_priority" not in result.stderr, "a deferred bug write must not let priorities bundle first"
    assert _bundles(root) == []
    assert recorded == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")
    assert "## Status\nopen" in priority.read_text(encoding="utf-8")


# The operator inbox refuses project-a's underscore, so the carried-bug
# cases run in a project whose name it accepts.
_CARRIED_PROJECT = "carried-lock"


def _add_carried_bug(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """``(bug, project_root)``: one ``running`` bug that only a ``done``
    bundle lists -- carried -- in a fresh registered project."""
    project_root = add_registered_project(root, _CARRIED_PROJECT)
    for sub in ("bugs", "requirements"):
        (project_root / sub).mkdir(exist_ok=True)
    bug = project_root / "bugs" / "BUG-9801-carried-lock-probe.md"
    bug.write_text(
        "# BUG-9801-carried-lock-probe\n\n**Severity:** medium\n\n## Status\nrunning\n\n## Symptom\nsynthetic\n",
        encoding="utf-8",
    )
    (project_root / "requirements" / "v0.0.1-bugfix-bundle-20990101.md").write_text(
        "# v0.0.1-bugfix-bundle-20990101\n\n## Status\ndone\n\n## Target Version\nv0.0.1\n\n"
        "## Workflow Type\nrelease\n\n## PM Task\nnone\n\n"
        f"## Bundled Items\n\n- {bug.name} (`{bug}`)\n",
        encoding="utf-8",
    )
    return bug, project_root


def test_unavailable_lock_leaves_a_carried_bug_and_the_operator_inbox_untouched(two_project_root, tmp_path):
    root = two_project_root
    bug, project_root = _add_carried_bug(root)
    inbox = project_root / "operator-inbox.md"
    bug_before = bug.read_bytes()
    inbox_before = inbox.read_bytes() if inbox.exists() else None
    body = (
        "t_hook_refuses() { return 1; }\n"
        "DISCOVERY_WRITE_LOCK_HOOK=t_hook_refuses\n"
        f'discovery_run_pipeline "{_CARRIED_PROJECT}"\n'
    )

    result, recorded = _run(root, tmp_path, body, project=_CARRIED_PROJECT)

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake" in result.stderr
    assert "converted " not in result.stderr, result.stderr
    assert recorded == []
    assert bug.read_bytes() == bug_before
    assert (inbox.read_bytes() if inbox.exists() else None) == inbox_before


def test_carried_bug_is_converted_only_while_the_repo_lock_is_held(two_project_root, tmp_path):
    root = two_project_root
    bug, project_root = _add_carried_bug(root)

    result, recorded = _run(root, tmp_path, f'discovery_run_pipeline "{_CARRIED_PROJECT}"', project=_CARRIED_PROJECT)

    assert result.returncode == 0, result.stderr
    assert recorded == ["carried_convert HELD"], recorded
    assert f"converted {bug.name} to needs-operator" in result.stderr, result.stderr
    assert "## Status\nneeds-operator" in bug.read_text(encoding="utf-8")
    assert (project_root / "operator-inbox.md").is_file()


def test_chain_turning_busy_during_the_scan_is_not_written_into(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)
    tasks = _project(root) / "tasks"
    # The hook stands in for another role queuing work while discovery was
    # scanning unlocked: it queues a BACKLOG CM task, then grants the lock.
    body = (
        "source scripts/lib/lock_provenance.sh\n"
        "HOOK_FD=''\n"
        "t_hook_after_queue() {\n"
        f'    mkdir -p "{tasks}/CM-20260101-002-late"\n'
        f'    printf "# Status\\n\\n## State\\nBACKLOG\\n" > "{tasks}/CM-20260101-002-late/status.md"\n'
        f'    echo "- [ ] CM-20260101-002-late — late" >> "{tasks}/queues/cm_backlog.md"\n'
        '    pgai_lock_acquire "$LOCK" pm "$$" "wake-chain:$1" none HOOK_FD\n'
        "}\n"
        "DISCOVERY_WRITE_LOCK_HOOK=t_hook_after_queue\n"
        f'discovery_run_pipeline "{_PROJECT}"\n'
        'pgai_lock_release "$LOCK" "$HOOK_FD"\n'
    )

    result, recorded = _run(root, tmp_path, body)

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "discovery_step_bugs: chain became busy during the unlocked scan; not writing" in result.stderr
    assert _bundles(root) == []
    assert recorded == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# No hook (pm-agent.sh --auto): the write lock waits up to lock_wait_seconds
# ---------------------------------------------------------------------------

# Another process holds the repo lock until the test tells it to let go.
# The wait runs on a controlled clock: pgai_lock_wait_now reads T_NOW, and
# pgai_lock_wait_pause advances it by the requested seconds and records
# each pause. T_RELEASE_ON_PAUSE=1 makes the first pause tell the holder
# to release and wait for it to exit, so the next attempt finds the lock
# free; T_RELEASE_ON_PAUSE=0 keeps the holder in place until the end.
_HOLDER_AND_CLOCK = r"""
source scripts/lib/lock_provenance.sh
T_READY="$(dirname "$REC")/holder.ready"
T_GO="$(dirname "$REC")/holder.go"
mkfifo "$T_READY" "$T_GO"
bash -c 'source scripts/lib/lock_provenance.sh
         fd=""
         pgai_lock_acquire "$1" coder "$$" "wake-chain:$4" none fd
         echo ready > "$2"
         read -r _ < "$3"
         pgai_lock_release "$1" "$fd"' _ "$LOCK" "$T_READY" "$T_GO" "$PGAI_PROJECT_NAME" &
T_HOLDER_PID=$!
read -r _ < "$T_READY"
T_NOW=1000
T_PAUSES=()
T_RELEASED=0
pgai_lock_wait_now() { echo "$T_NOW"; }
pgai_lock_wait_pause() {
    T_PAUSES+=("$1")
    T_NOW=$(( T_NOW + $1 ))
    if [[ "$T_RELEASE_ON_PAUSE" == "1" && "$T_RELEASED" == "0" ]]; then
        T_RELEASED=1
        echo go > "$T_GO"
        wait "$T_HOLDER_PID"
    fi
}
discovery_run_pipeline "$PGAI_PROJECT_NAME"
_t_total=0
for _t_p in "${T_PAUSES[@]}"; do _t_total=$(( _t_total + _t_p )); done
echo "PAUSES=${#T_PAUSES[@]} PAUSED_SECONDS=${_t_total}"
if [[ "$T_RELEASED" == "0" ]]; then
    echo go > "$T_GO"
    wait "$T_HOLDER_PID"
fi
"""


def _paused_seconds(stdout: str) -> int:
    marker = "PAUSED_SECONDS="
    assert marker in stdout, stdout
    return int(stdout.split(marker, 1)[1].split()[0])


def test_auto_write_lock_waits_for_a_holder_that_lets_go_within_the_budget(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)

    result, recorded = _run(
        root, tmp_path, _HOLDER_AND_CLOCK,
        extra_env={"LOCK_WAIT_SECONDS": "30", "T_RELEASE_ON_PAUSE": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=produced_work" in result.stdout, result.stderr
    assert "discovery write: waiting up to 30s (lock_wait_seconds) for the repo lock held by holder=coder" in result.stderr
    assert "discovery write: repo lock acquired after waiting" in result.stderr
    assert "repo lock not available for the discovery write" not in result.stderr
    assert 0 < _paused_seconds(result.stdout) <= 30
    assert len(_bundles(root)) == 1
    assert "## Status\nrunning" in bug.read_text(encoding="utf-8")
    assert recorded and all(line.endswith(" HELD") for line in recorded), recorded
    assert not _lock_file(root).exists(), "discovery must release the lock it took itself"


def test_auto_write_lock_defers_when_the_holder_keeps_it_past_the_budget(two_project_root, tmp_path):
    root = two_project_root
    bug = _add_bug(root)

    result, recorded = _run(
        root, tmp_path, _HOLDER_AND_CLOCK,
        extra_env={"LOCK_WAIT_SECONDS": "5", "T_RELEASE_ON_PAUSE": "0"},
    )

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "discovery write: waiting up to 5s (lock_wait_seconds)" in result.stderr
    assert "discovery write: repo lock still held after waiting up to 5s (lock_wait_seconds)" in result.stderr
    assert "discovery_step_bugs: repo lock not available for the discovery write; deferring to the next wake" in result.stderr
    assert _paused_seconds(result.stdout) == 5, "the wait must use the whole budget and no more"
    assert _bundles(root) == []
    assert recorded == []
    assert "## Status\nopen" in bug.read_text(encoding="utf-8")


def test_auto_write_lock_budget_comes_from_kanban_cfg_when_not_exported(two_project_root, tmp_path):
    root = two_project_root
    _add_bug(root)
    cfg = root / "kanban.cfg"
    text = cfg.read_text(encoding="utf-8")
    assert "[wake]\n" in text, "the fixture kanban.cfg is expected to carry a [wake] section"
    cfg.write_text(text.replace("[wake]\n", "[wake]\nlock_wait_seconds = 7\n", 1), encoding="utf-8")

    result, _ = _run(root, tmp_path, _HOLDER_AND_CLOCK, extra_env={"T_RELEASE_ON_PAUSE": "0"})

    assert result.returncode == 0, result.stderr
    assert "DISCOVERY_STATUS=idle" in result.stdout
    assert "discovery write: waiting up to 7s (lock_wait_seconds)" in result.stderr
    assert _paused_seconds(result.stdout) == 7
