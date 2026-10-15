"""
test_wake_lock_wait_starvation.py
=================================
The repo-lock wait and the lock-starvation guard, driven through the real
production entry point (`bash scripts/wake/claude.sh --agent=...` against a
full, self-contained copy of this tree, with a PATH-shadowing `claude` stub
so no real CLI is reached):

  1. A PM wake chain holds the repo lock for 80 s. A CM wake that starts
     60 s into that hold waits for it, takes the lock once it is released,
     and dispatches its BACKLOG task in the same firing.
  2. The holder keeps the lock past lock_wait_seconds for three
     consecutive CM firings while a CM task is BACKLOG: the third firing
     logs the starvation warning, and the next PM wake logs that it skips
     discovery because the CM task is ready.

The holder. A separate process takes the project's real repo lock through
pgai_lock_acquire with exactly the provenance a PM wake chain records
(holder=pm, op=wake-chain:<project>, task=none) and holds it until this
test, or the fake clock below, releases it. It stands in for a PM wake
whose discovery runs long.

Time. The fixture appends a block to its copy of
scripts/lib/wake_driver.sh redefining lock_provenance.sh's
pgai_lock_wait_now and pgai_lock_wait_pause -- the clock and the pause the
lock wait reads -- so a minutes-long wait runs on a fake clock in seconds
of real time. The fake clock starts at the wake's real start, which the
scenario reads as 60 s into the holder's hold; when FX_RELEASE_AFTER is
set, the first pause that carries the fake clock that many seconds forward
(20 s: the hold's 80 s mark) releases the holder and waits for the lock
file to go before returning. Production files are unchanged; only the
fixture copy carries the block.

Every subprocess environment is built from os.environ minus every name a
live wake context may export, so nothing here reads or writes the live
install. All paths are under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import textwrap
import time

from tests.fixtures.claude_provider_stub import install_claude_provider_stub

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_PROJECT = "lockwait"
_CM_TASK = "CM-20260101-001-publish-draft"
_WAKE_TIMEOUT_SECONDS = 180

_LEAK_PREFIXES = (
    "PGAI_", "PP_", "KANBAN_ROOT", "TEAM_ROOT", "CLAUDE_", "AI_AGENT", "STOP_FILE",
    "TMPDIR", "MAX_TASK_SECONDS", "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE",
    "MAX_PROJECT_SECONDS", "KILL_GRACE_SECONDS", "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED", "AI_AUTH_MODE", "BASH_MAX_TIMEOUT_MS", "DASHBOARD_",
    "PM_MAX_TASKS", "LOCK_WAIT_SECONDS", "LOCK_STARVATION_FIRINGS", "FX_",
)

# Appended to the fixture copy of scripts/lib/wake_driver.sh.
_FAKE_CLOCK_BLOCK = r"""

# --- test fixture: fake clock for the repo-lock wait ---
_FX_NOW="$(date +%s)"
_FX_RELEASE_AT=""
[[ -n "${FX_RELEASE_AFTER:-}" ]] && _FX_RELEASE_AT=$(( _FX_NOW + FX_RELEASE_AFTER ))
pgai_lock_wait_now() { echo "$_FX_NOW"; }
pgai_lock_wait_pause() {
    _FX_NOW=$(( _FX_NOW + $1 ))
    if [[ -n "$_FX_RELEASE_AT" ]] && (( _FX_NOW >= _FX_RELEASE_AT )) && [[ ! -f "$FX_RELEASE_FILE" ]]; then
        touch "$FX_RELEASE_FILE"
        local _fx_lock; _fx_lock="$(pp_repo_lock_path "$FX_PROJECT")"
        for _ in $(seq 1 200); do [[ -e "$_fx_lock" ]] || break; sleep 0.05; done
    fi
}
"""


def _clean_base_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k == p or k.startswith(p) for p in _LEAK_PREFIXES)
    }


def _build_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A full kanban root: one document-workflow project (no dev tree), an
    empty PM queue and one CM task in BACKLOG. lock_wait_seconds and
    lock_starvation_firings are left out of kanban.cfg, so their defaults
    (120 and 3) are what the wakes run with."""
    root = tmp_path / "kanban_root"
    proj = root / "projects" / _PROJECT
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
        f"[project]\nproject_name = {_PROJECT}\nworkflow_type = document\n", encoding="utf-8"
    )
    queues = proj / "tasks" / "queues"
    queues.mkdir(parents=True)
    for agent in ("pm", "writer", "tester"):
        (queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n\n", encoding="utf-8")
    task = proj / "tasks" / _CM_TASK
    task.mkdir(parents=True)
    (task / "README.md").write_text(
        f"# Task: {_CM_TASK}\n\n## Task ID\n{_CM_TASK}\n\n## Role\nCM\n\n"
        "## Workflow Type\ndocument\n\n## Working Directory\nnone\n\n## Source Branch\nnone\n",
        encoding="utf-8",
    )
    (task / "status.md").write_text(
        f"# Status\n\n## Task\n{_CM_TASK}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )
    (queues / "cm_backlog.md").write_text(
        f"# cm backlog\n\n- [ ] {_CM_TASK} — publish the draft\n", encoding="utf-8"
    )
    (root / "logs").mkdir()
    (root / "locks").mkdir()
    for tree in ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config"):
        shutil.copytree(_TEAM_DIR / tree, root / tree, ignore=shutil.ignore_patterns("__pycache__"))
    with (root / "scripts" / "lib" / "wake_driver.sh").open("a", encoding="utf-8") as handle:
        handle.write(_FAKE_CLOCK_BLOCK)
    return root


def _lock_path(root: pathlib.Path) -> pathlib.Path:
    return root / "locks" / f"repo-wake-{_PROJECT}.lock"


class _PmChainHolder:
    """A separate process holding the repo lock as a PM wake chain would."""

    def __init__(self, tmp_path: pathlib.Path, root: pathlib.Path) -> None:
        self.ready = tmp_path / "holder-ready"
        self.release_file = tmp_path / "holder-release"
        script = textwrap.dedent(f"""\
            source "{root / 'scripts' / 'lib' / 'lock_provenance.sh'}"
            fd=""
            pgai_lock_acquire "{_lock_path(root)}" pm "$$" "wake-chain:{_PROJECT}" none fd || exit 9
            touch "{self.ready}"
            for _ in $(seq 1 6000); do [[ -f "{self.release_file}" ]] && break; sleep 0.05; done
            pgai_lock_release "{_lock_path(root)}" "$fd"
        """)
        self.proc = subprocess.Popen(["bash", "-c", script], cwd=str(root), env=_clean_base_env())
        deadline = time.time() + 15
        while not self.ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert self.ready.exists(), "the PM-chain lock holder did not start"

    def stop(self) -> None:
        self.release_file.touch()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _run_wake(tmp_path: pathlib.Path, root: pathlib.Path, agent: str, holder: _PmChainHolder | None,
              *, release_after: int | None = None) -> str:
    install_claude_provider_stub(tmp_path / "stub-bin", tmp_path / "stub-control")
    env = _clean_base_env()
    env["PATH"] = f"{tmp_path / 'stub-bin'}{os.pathsep}{env.get('PATH', '')}"
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["HOME"] = str(tmp_path)
    env["FX_PROJECT"] = _PROJECT
    env["FX_RELEASE_FILE"] = str(holder.release_file if holder else tmp_path / "no-holder")
    if release_after is not None:
        env["FX_RELEASE_AFTER"] = str(release_after)
    result = subprocess.run(
        ["bash", str(root / _WAKE_CLAUDE_REL), f"--agent={agent}"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=_WAKE_TIMEOUT_SECONDS,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{agent} wake exited {result.returncode}.\n{output}"
    return output


def _task_state(root: pathlib.Path) -> str:
    text = (root / "projects" / _PROJECT / "tasks" / _CM_TASK / "status.md").read_text(encoding="utf-8")
    return text.split("## State\n", 1)[1].split("\n", 1)[0].strip()


def test_cm_wake_waits_out_a_pm_chain_and_dispatches_in_the_same_firing(tmp_path):
    root = _build_root(tmp_path)
    holder = _PmChainHolder(tmp_path, root)
    started = time.monotonic()
    try:
        output = _run_wake(tmp_path, root, "cm", holder, release_after=20)
    finally:
        holder.stop()
    real_seconds = time.monotonic() - started

    assert (
        f"project {_PROJECT}: waiting up to 120s for the repo lock held by wake chain "
        f"holder=pm pid={holder.proc.pid} op=wake-chain:{_PROJECT} task=none"
    ) in output, output
    waited = re.search(rf"project {_PROJECT}: repo lock acquired after waiting (\d+)s", output)
    assert waited and 20 <= int(waited.group(1)) < 120, output
    assert "deferring for an end-of-iteration retry" not in output, output
    assert "retry pass:" not in output, output
    assert f"finished task {_CM_TASK} with state DONE" in output, output
    assert _task_state(root) == "DONE"
    assert not _lock_path(root).exists(), "the CM chain must release the lock it took"
    assert real_seconds < 120, f"a stubbed 20 s wait took {real_seconds:.0f}s of real time"


def test_three_starved_cm_firings_warn_and_the_next_pm_wake_skips_discovery(tmp_path):
    root = _build_root(tmp_path)
    holder = _PmChainHolder(tmp_path, root)
    outputs = []
    try:
        for _ in range(3):
            outputs.append(_run_wake(tmp_path, root, "cm", holder))
    finally:
        holder.stop()

    warning = (
        f"project {_PROJECT}: WARNING: lock starvation: cm has deferred on repo-lock contention "
        f"for 3 consecutive firing(s) while its task {_CM_TASK} is BACKLOG -- "
        "PM wakes skip discovery while that task is ready"
    )
    for n, output in enumerate(outputs, start=1):
        assert f"project {_PROJECT}: repo lock still held after waiting 120s (lock_wait_seconds=120)" in output, output
        assert f"project {_PROJECT}: retry pass: still contended after retry" in output, output
        if n < 3:
            assert f"lock starvation count for cm: {n} of 3 consecutive contention deferral(s)" in output, output
            assert "WARNING: lock starvation" not in output, output
    assert warning in outputs[2], outputs[2]
    assert _task_state(root) == "BACKLOG", "the guard must not change any task's state"
    assert not (root / "HALT").exists() and not (root / "projects" / _PROJECT / "HALT").exists()

    pm_output = _run_wake(tmp_path, root, "pm", None)
    assert (
        f"project {_PROJECT}: autonomous scan: skipping discovery -- cm task {_CM_TASK} is ready "
        "and cm has deferred on repo-lock contention for 3 consecutive firing(s)"
    ) in pm_output, pm_output
    assert "running discovery pipeline" not in pm_output, pm_output
    assert "discovery_run_pipeline" not in pm_output, pm_output
