"""
test_pm_wake_discovery_lock_scope.py
====================================
The PM wake's discovery branch, driven through the real production entry
point (`bash scripts/wake/claude.sh --agent=pm` against a full,
self-contained copy of this tree, with a PATH-shadowing `claude` stub so no
real CLI is reached):

  1. Busy chain (PM queue empty, a CM task in BACKLOG, Active RC none):
     the wake exits fast -- proved by what ran, never by elapsed time.
     Between discovery's entry and its return exactly one thing happens,
     the chain-busy check: no scan step is called, no lock is acquired,
     nothing is written or dispatched. The log carries the chain-busy idle
     exit before any scan line -- no "active ceilings" line and no Step 1/2
     narration -- and nothing is bundled, though an open bug is waiting.
  2. In that same fixture, the repo lock is not held by the PM wake while
     discovery runs: a separate process's non-blocking acquire succeeds
     from inside discovery's chain-busy check.
  3. Idle chain with an open bug: discovery bundles it, every discovery
     write happens while the wake holds the repo lock again, the scan
     before it ran without the lock, and the post-discovery dispatch runs
     under the lock.

How the lock is observed. The fixture appends a probe block to the copied
scripts/lib/wake_driver.sh (by its end every function the probe wraps is
defined), wrapping -- in the fixture copy only -- discovery_run_pipeline
itself, discovery's chain-busy check, its three scan steps, its
write-phase gate and the two functions that take the repo lock for it,
its three writers, and process_one_task. Each wrapper appends one line to a
record file, in call order: the function's name (prefixed "lock_path" for
the gate and the two lock takers) and whether the wake's own
pid is the holder named in the repo lock's provenance at that moment, and,
inside the chain-busy check, whether a separate process can take the lock
with a non-blocking pgai_lock_acquire right then. discovery_run_pipeline's
wrapper writes an entry line and a return line carrying
DISCOVERY_LAST_STATUS, so the record shows exactly what discovery ran. A
function the probe cannot find is recorded as "probe_missing <name>" and
fails the tests, so a renamed function cannot silently drop out of the
proof. The production files are unchanged; the wrappers only record and
delegate.

Every subprocess environment is built from os.environ minus every name a
live wake context may export (the operator's PGAI_PROJECT_ROOT,
PGAI_TASKS_DIR, PGAI_DEV_TREE_PATH, kanban root and the rest), so nothing
here reads or writes the live install. All paths are under pytest's
tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.claude_provider_stub import install_claude_provider_stub

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_PROJECT = "lockscope"
_CM_TASK = "CM-20260101-001-open-rc-0-1-1"
_BUG_NAME = "BUG-0001-sample-defect.md"
_CHAIN_BUSY_EXIT = f"discovery_run_pipeline [{_PROJECT}]: chain busy, idle exit before any scan"
_WAKE_TIMEOUT_SECONDS = 120

_LEAK_PREFIXES = (
    "PGAI_", "PP_", "KANBAN_ROOT", "TEAM_ROOT", "CLAUDE_", "AI_AGENT", "STOP_FILE",
    "TMPDIR", "MAX_TASK_SECONDS", "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE",
    "MAX_PROJECT_SECONDS", "KILL_GRACE_SECONDS", "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED", "AI_AUTH_MODE", "BASH_MAX_TIMEOUT_MS", "DASHBOARD_",
    "PM_MAX_TASKS", "FX_",
)

# Appended to the fixture copy of scripts/lib/wake_driver.sh.
# FX_RECORD names the record file; FX_PROJECT the project.
_PROBE_BLOCK = r"""

# --- test fixture probe: record repo-lock state around discovery ---
_fx_lock_file() { pp_repo_lock_path "$FX_PROJECT"; }
_fx_wake_holds() {
    local lf; lf="$(_fx_lock_file)"
    if [[ -f "$lf" ]] && grep -qx "pid=$$" "$lf"; then echo HELD; else echo FREE; fi
}
_fx_probe_acquire() {
    bash -c 'source "$1"; fx_fd=""
             if pgai_lock_acquire "$2" fixture-probe "$$" probe none fx_fd 2>/dev/null; then
                 echo ACQUIRED; pgai_lock_release "$2" "$fx_fd"
             else
                 echo CONTENDED
             fi' _ "${SCRIPT_DIR}/../lib/lock_provenance.sh" "$(_fx_lock_file)"
}
for _fx_fn in discovery_run_pipeline _disc_chain_idle; do
    declare -F "$_fx_fn" >/dev/null || echo "probe_missing ${_fx_fn}" >> "$FX_RECORD"
done
eval "$(declare -f discovery_run_pipeline | sed '1s/^discovery_run_pipeline/_fx_real_discovery_run_pipeline/')"
discovery_run_pipeline() {
    local _fx_rc=0
    echo "discovery_enter" >> "$FX_RECORD"
    _fx_real_discovery_run_pipeline "$@" || _fx_rc=$?
    echo "discovery_return status=${DISCOVERY_LAST_STATUS:-unset}" >> "$FX_RECORD"
    return "$_fx_rc"
}
eval "$(declare -f _disc_chain_idle | sed '1s/^_disc_chain_idle/_fx_real_disc_chain_idle/')"
_disc_chain_idle() {
    echo "chain_check wake=$(_fx_wake_holds) probe=$(_fx_probe_acquire)" >> "$FX_RECORD"
    _fx_real_disc_chain_idle "$@"
}
# _fx_wrap <function> <record label>: rename <function> to
# _fx_real<function> and put a recorder in its place that writes
# "<label> wake=HELD|FREE" before delegating. The recorder declares no
# locals, so a callee that writes a variable named by its caller still
# reaches the caller's own local through bash's dynamic scoping.
_fx_wrap() {
    local fn="$1" label="$2"
    if ! declare -F "$fn" >/dev/null; then
        echo "probe_missing ${fn}" >> "$FX_RECORD"
        return 0
    fi
    eval "$(declare -f "$fn" | sed "1s/^${fn}/_fx_real${fn}/")"
    eval "${fn}() { echo \"${label} wake=\$(_fx_wake_holds)\" >> \"\$FX_RECORD\"; _fx_real${fn} \"\$@\"; }"
}
for _fx_fn in discovery_step_bugs discovery_step_priority discovery_step_requirements \
              _disc_write_bundle _disc_set_status_field _disc_mark_cache_bundled \
              process_one_task; do
    _fx_wrap "$_fx_fn" "$_fx_fn"
done
# Discovery reaches the repo lock only through its write-phase gate, which
# takes it with the PM wake's hook or with discovery's own taker; those
# are recorded as "lock_path <function>". pgai_lock_acquire itself is not
# wrapped: its declare -f copy is not faithful (bash re-emits its
# `1>&"$_fd"` provenance write as `&> "$_fd"`, a file named by the fd
# number), so the copy would take the lock without recording provenance.
for _fx_fn in _disc_enter_write_phase wd_discovery_take_repo_lock _disc_take_own_write_lock; do
    _fx_wrap "$_fx_fn" "lock_path ${_fx_fn}"
done
unset _fx_fn
"""


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k == p or k.startswith(p) for p in _LEAK_PREFIXES)
    }


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _init_dev_tree(repo: pathlib.Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch=ai_main", str(repo)], capture_output=True, check=True
    )
    _git(repo, "config", "--local", "user.email", "test@pgai.local")
    _git(repo, "config", "--local", "user.name", "PGAI Test")
    (repo / "README.md").write_text("# fixture dev tree\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")


def _write_task(tasks_dir: pathlib.Path, task_id: str, role: str, state: str) -> None:
    folder = tasks_dir / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "README.md").write_text(
        f"# Task: {task_id}\n\n## Task ID\n{task_id}\n\n## Role\n{role}\n",
        encoding="utf-8",
    )
    (folder / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _build_root(tmp_path: pathlib.Path, *, busy: bool) -> pathlib.Path:
    """A full kanban root: one release-workflow project with an empty PM
    queue, an open bug, ceilings configured (so discovery's ceilings line
    appears whenever it gets that far), and -- when *busy* -- one CM task
    in BACKLOG."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
    repo = tmp_path / "dev_tree"
    _init_dev_tree(repo)

    proj.mkdir(parents=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n"
        f"tmp_root = {tmp_path / 'scratch'}\n"
        "tmp_subdir = pgai_kanban_tmp\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 1\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
    )
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {_PROJECT}\n"
        f"dev_tree_path = {repo}\n"
        "workflow_type = release\n"
        "branch_prefix = ai_\n"
        "push_to_remote = false\n\n"
        "[versioning]\nmax_patch = 99\nmax_minor = 9\nmax_major = 0\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv0.1.0\n",
        encoding="utf-8",
    )
    (proj / "requirements").mkdir()
    (proj / "bugs").mkdir()
    (proj / "bugs" / _BUG_NAME).write_text(
        "# BUG-0001: sample defect\n\n**Severity:** high\n\n## Status\nopen\n\n## Summary\nA bug.\n",
        encoding="utf-8",
    )
    queues = proj / "tasks" / "queues"
    queues.mkdir(parents=True)
    for agent in ("pm", "coder", "writer", "tester", "cm"):
        (queues / f"{agent}_backlog.md").write_text(
            f"# {agent.upper()} Backlog\n\n", encoding="utf-8"
        )
    for cache in ("bug", "priority"):
        (queues / f"{cache}_backlog.md").write_text(f"# {cache} backlog\n\n", encoding="utf-8")
    if busy:
        _write_task(proj / "tasks", _CM_TASK, "CM", "BACKLOG")
        (queues / "cm_backlog.md").write_text(
            f"# CM Backlog\n\n- [ ] {_CM_TASK} — open the RC\n", encoding="utf-8"
        )

    (root / "logs").mkdir()
    (root / "locks").mkdir()
    for tree in ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config"):
        shutil.copytree(
            _TEAM_DIR / tree, root / tree, ignore=shutil.ignore_patterns("__pycache__")
        )
    probe_target = root / "scripts" / "lib" / "wake_driver.sh"
    with probe_target.open("a", encoding="utf-8") as handle:
        handle.write(_PROBE_BLOCK)
    return root


def _run_pm_wake(tmp_path: pathlib.Path, root: pathlib.Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    record = tmp_path / "lock-record.txt"
    install_claude_provider_stub(tmp_path / "stub-bin", tmp_path / "stub-control")
    env = _clean_base_env()
    env["PATH"] = f"{tmp_path / 'stub-bin'}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["HOME"] = str(tmp_path)
    env["FX_RECORD"] = str(record)
    env["FX_PROJECT"] = _PROJECT

    result = subprocess.run(
        ["bash", str(root / _WAKE_CLAUDE_REL), "--agent=pm"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=_WAKE_TIMEOUT_SECONDS,
    )
    lines = record.read_text(encoding="utf-8").splitlines() if record.exists() else []
    return result, lines


def _bundles(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted((root / "projects" / _PROJECT / "requirements").glob("*-bundle-*.md"))


def test_busy_chain_pm_wake_exits_fast_logs_busy_exit_first_and_never_holds_the_lock(tmp_path):
    """"Fast" is proved by the work done, not by a stopwatch: once discovery
    is entered, the chain-busy check is the only thing that runs before it
    returns idle -- no scan step, no lock acquisition, no write, no
    dispatch. A wall-clock budget would track host load instead."""
    require_binary("git", label="pm-wake-discovery-lock-scope")
    root = _build_root(tmp_path, busy=True)

    result, record = _run_pm_wake(tmp_path, root)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert [line for line in record if line.startswith("probe_missing")] == [], (
        f"the fixture probe could not wrap a function it relies on; record: {record}"
    )
    assert _CHAIN_BUSY_EXIT in output, output
    ahead = output[: output.index(_CHAIN_BUSY_EXIT)]
    assert f"cm_backlog task {_CM_TASK} is BACKLOG" in ahead, output
    assert "active ceilings" not in output, "no scan line may come before (or after) a busy exit"
    assert "discovery_step_bugs" not in output and "discovery_step_priority" not in output, output
    assert _bundles(root) == [], "a busy chain must not be bundled into"

    checks = [line for line in record if line.startswith("chain_check")]
    assert checks == ["chain_check wake=FREE probe=ACQUIRED"], (
        "discovery must run without the PM wake holding the repo lock, so a "
        f"concurrent non-blocking acquire succeeds; record: {record}"
    )
    assert not any(line.startswith("_disc_") for line in record), record

    assert "discovery_enter" in record, f"the PM wake never entered discovery; record: {record}"
    from_discovery = record[record.index("discovery_enter"):]
    assert from_discovery == [
        "discovery_enter",
        "chain_check wake=FREE probe=ACQUIRED",
        "discovery_return status=idle",
    ], (
        "a busy chain must leave discovery straight after the chain-busy check "
        "and do nothing after it: no scan step, no write-phase entry, no "
        f"repo-lock take, no write, no dispatch; record: {record}"
    )


def test_idle_chain_pm_wake_bundles_under_the_lock_and_dispatches_under_it(tmp_path):
    require_binary("git", label="pm-wake-discovery-lock-scope")
    root = _build_root(tmp_path, busy=False)

    result, record = _run_pm_wake(tmp_path, root)
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert [line for line in record if line.startswith("probe_missing")] == [], (
        f"the fixture probe could not wrap a function it relies on; record: {record}"
    )
    assert len(_bundles(root)) == 1, output
    bug_text = (root / "projects" / _PROJECT / "bugs" / _BUG_NAME).read_text(encoding="utf-8")
    assert "## Status\nrunning" in bug_text

    checks = [line for line in record if line.startswith("chain_check")]
    assert checks[0] == "chain_check wake=FREE probe=ACQUIRED", (
        f"the unlocked scan must start without the lock; record: {record}"
    )
    assert checks[1:] == ["chain_check wake=HELD probe=CONTENDED"], (
        f"the write phase must re-check the chain under the lock; record: {record}"
    )

    writes = [line for line in record if line.startswith("_disc_")]
    assert {line.split()[0] for line in writes} == {
        "_disc_write_bundle", "_disc_set_status_field", "_disc_mark_cache_bundled",
    }, record
    assert all(line.endswith("wake=HELD") for line in writes), record

    dispatches = [line for line in record if line.startswith("process_one_task")]
    assert len(dispatches) == 2, f"main-loop and post-discovery dispatch expected; record: {record}"
    assert dispatches[-1] == "process_one_task wake=HELD", record
    assert "post-discovery iteration: chain idle and discovery produced work, processing" in output
    lock_file = root / "locks" / f"repo-wake-{_PROJECT}.lock"
    assert not lock_file.exists(), "the end-of-chain release must drop the re-taken lock"
