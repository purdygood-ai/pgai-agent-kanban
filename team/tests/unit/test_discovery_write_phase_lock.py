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
    ahead of the deferred one.

Every write is observed through a recording wrapper around the three
discovery writers: each call records whether this shell's pid is the
holder named in the repo lock's provenance record at that moment.
"""

from __future__ import annotations

import pathlib

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_PROJECT = "project_a"
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


def _run(root: pathlib.Path, tmp_path: pathlib.Path, body: str):
    """Source the discovery libs against *root*, install the recording
    wrappers, then run *body*. The record file is returned alongside."""
    record = tmp_path / "writes.rec"
    script = (
        "set -euo pipefail\n"
        "source scripts/lib/ini_parser.sh\n"
        "source scripts/lib/temp.sh\n"
        "source scripts/lib/semver.sh\n"
        "source scripts/lib/project_paths.sh\n"
        "source scripts/lib/discovery.sh\n"
        f'LOCK="$(pp_repo_lock_path {_PROJECT})"\n'
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
            "PGAI_PROJECT_NAME": _PROJECT,
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
