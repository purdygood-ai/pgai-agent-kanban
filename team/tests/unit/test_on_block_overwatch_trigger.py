"""
test_on_block_overwatch_trigger.py
===================================
The on-BLOCK nudge: a wake chain that blocks a task starts OVERWATCH's block
triage (``overwatch-sweep.sh --only block-triage``) only after the chain has
released the project's repo lock, so the woken triage can take that lock.

Covered:
  1. Sandbox wake (the real ``scripts/wake/claude.sh`` entry point): a chain
     whose dispatched task blocks fires the nudge after its "done" line and
     its lock release; the woken sweep runs only block-triage, takes the
     repo lock, and escalates the blocked task. Neither the trigger log nor
     the action log carries a lock-contention refusal from the woken run.
  2. Sandbox wake: a chain whose task completes (blocks nothing) fires no
     nudge -- no nudge line, no trigger log.
  2b. Sandbox wake: a chain that blocks a task BEFORE dispatching it (its
     worktree cannot be created: the project's dev tree is not a git
     repository) calls fire_block_triage_nudge -- stubbed to record its
     calls -- exactly once, once the repo lock is no longer held, and never
     starts the provider.
  3. ``fire_block_triage_nudge`` itself: runs the sweep with
     ``--only block-triage --lock-wait <BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS>``;
     the sweep outlives a caller that exits through the wake's
     job-reaping EXIT trap (cleanup_on_exit); a no-op for AGENT=overwatch; a
     missing or failing sweep leaves the caller's exit status untouched.
  4. Structure: wd_finalize_task's BLOCKED arm only records the block; the
     nudge call in run_project_chain sits after the end-of-chain lock
     release and behind the "this chain blocked a task" flag.

Every fixture builds a throwaway kanban root under pytest's tmp_path (a
document-workflow project, so no dev tree or real repository is involved)
and shadows the provider CLI with a stub on PATH; no live install, network
or real provider is reached. Subprocess environments are built from an
explicit set (tests/fixtures/scrubbed_subprocess_env.py).
"""

from __future__ import annotations

import pathlib
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MIRRORED_DIRS = ("scripts", "pgai_agent_kanban", "workflows", "pm-agent", "config")
_WAKE_DRIVER = _TEAM_DIR / "scripts" / "lib" / "wake_driver.sh"
_FINALIZE = _TEAM_DIR / "scripts" / "lib" / "wake_driver_finalize.sh"
_WAKE_PROJECT = _TEAM_DIR / "scripts" / "lib" / "wake_project.sh"

_PROJECT = "nudge-probe"
_AGENTS = ("pm", "coder", "writer", "tester", "cm", "overwatch")
_TASK = "CODER-20260101-001-nudged-block"
_WAKE_TIMEOUT_S = 240
_SWEEP_WAIT_S = 180

_NUDGE_LINE = "nudging OVERWATCH: overwatch-sweep.sh --only block-triage"
_SWEEP_DONE = "overwatch-sweep: complete"

# The stub provider: answers --version, and on a dispatch rewrites the
# task's status.md (path taken from the real prompt's "Read the status at
# <path>" line) to the state named in its mode file -- BLOCKED with a Block
# Record, as an agent's own block writes it, or DONE.
_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    set -u
    for a in "$@"; do
      [[ "$a" == "--version" ]] && { echo "2.1.251 (Claude Code)"; exit 0; }
    done
    prompt="" prev=""
    for a in "$@"; do [[ "$prev" == "-p" ]] && prompt="$a"; prev="$a"; done
    status="$(printf '%s\\n' "$prompt" | grep -oE 'Read the status at [^[:space:]]+' | head -n1 | awk '{print $NF}')"
    mode="$(cat "__MODE_FILE__")"
    if [[ -n "$status" && -f "$status" ]]; then
      if [[ "$mode" == "block" ]]; then
        cat > "$status" <<'EOF'
    # Status

    ## State
    BLOCKED

    ## Summary
    Stub agent blocked the task.

    ## Blockers
    stub agent could not continue

    ## Needs Human
    yes

    ## Block Record
    reason_code: agent_unclassified
    owner_role: CODER
    attempts: 0
    first_blocked_at: 2026-01-01T10:00:00+00:00
    last_blocked_at: 2026-01-01T10:00:00+00:00
    cooldown_until: none
    evidence: none
    resume: fresh
    EOF
      else
        printf '# Status\\n\\n## State\\nDONE\\n\\n## Summary\\nStub agent finished.\\n\\n## Blockers\\nnone\\n\\n## Needs Human\\nno\\n' > "$status"
      fi
    fi
    echo '{"type":"result","result":"stub","usage":{"input_tokens":1,"output_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}'
    exit 0
""")


class _Sandbox:
    """A throwaway kanban root with one document-workflow project and one
    BACKLOG coder task.

    *workflow* "release" makes it a release-workflow project instead, whose
    dev_tree_path is a plain directory, not a git repository, so the task's
    worktree cannot be created and the wake blocks it before dispatch.
    """

    def __init__(self, tmp_path: pathlib.Path, mode: str, workflow: str = "document") -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "kanban"
        self.project = self.root / "projects" / _PROJECT
        self.tasks = self.project / "tasks"
        self.queues = self.tasks / "queues"
        self.bin_dir = tmp_path / "stub-bin"
        self.mode_file = tmp_path / "stub-mode"
        self.workflow = workflow
        self._build(mode)

    def _build(self, mode: str) -> None:
        scratch = self.tmp_path / "scratch"
        scratch.mkdir(parents=True)
        self.queues.mkdir(parents=True)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "locks").mkdir(parents=True)
        (self.root / "kanban.cfg").write_text(
            f"[paths]\ntmp_root = {scratch}\ntmp_subdir = pgai_kanban_tmp\n\n"
            "[chain]\npm_mode = manual\n\n[wake]\nmax_tasks_per_wake = 1\n",
            encoding="utf-8",
        )
        (self.root / "projects.cfg").write_text(
            f"[project:{_PROJECT}]\npriority=1\nenabled=true\n", encoding="utf-8"
        )
        project_cfg = f"[project]\nproject_name = {_PROJECT}\nworkflow_type = {self.workflow}\n"
        if self.workflow == "release":
            not_a_repo = self.tmp_path / "dev-tree-not-a-repo"
            not_a_repo.mkdir()
            project_cfg += f"dev_tree_path = {not_a_repo}\nbranch_prefix = ai_\n"
        (self.project / "project.cfg").write_text(project_cfg, encoding="utf-8")
        (self.project / "overwatch").mkdir(parents=True)
        for agent in _AGENTS:
            (self.queues / f"{agent}_backlog.md").write_text(f"# {agent} backlog\n\n", encoding="utf-8")
        with (self.queues / "coder_backlog.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- [ ] {_TASK} — fixture task\n")
        task_dir = self.tasks / _TASK
        task_dir.mkdir(parents=True)
        if self.workflow == "release":
            readme_tail = "## Workflow Type\nrelease\n\n## Source Branch\nai_rc/v1.0.0\n"
        else:
            readme_tail = (
                "## Workflow Type\ndocument\n\n## Git Repo\nnone\n\n"
                "## Working Directory\nnone\n\n## Source Branch\nnone\n"
            )
        (task_dir / "README.md").write_text(
            f"# Task: {_TASK}\n\n## Task ID\n{_TASK}\n\n## Role\nCODER\n\n" + readme_tail,
            encoding="utf-8",
        )
        (task_dir / "status.md").write_text(
            f"# Status\n\n## Task\n{_TASK}\n\n## State\nBACKLOG\n\n## Summary\nnone\n\n"
            "## Blockers\nnone\n\n## Needs Human\nno\n",
            encoding="utf-8",
        )
        for name in _MIRRORED_DIRS:
            shutil.copytree(_TEAM_DIR / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        self.bin_dir.mkdir()
        self.mode_file.write_text(mode, encoding="utf-8")
        stub = self.bin_dir / "claude"
        stub.write_text(_STUB.replace("__MODE_FILE__", str(self.mode_file)), encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    @property
    def status_text(self) -> str:
        return (self.tasks / _TASK / "status.md").read_text(encoding="utf-8")

    @property
    def trigger_log(self) -> pathlib.Path:
        return self.root / "logs" / "overwatch-trigger.log"

    @property
    def actions(self) -> str:
        path = self.project / "overwatch" / "actions.log"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @property
    def sweep_log(self) -> str:
        path = self.project / "logs" / "overwatch" / "sweep.log"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def wake(self) -> subprocess.CompletedProcess:
        """One real coder wake firing against this root."""
        (self.tmp_path / "home").mkdir(exist_ok=True)
        env = build_scrubbed_env(
            home_dir=self.tmp_path / "home",
            extra_path_dirs=[self.bin_dir, pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.tmp_path / "scratch"),
                "PAUSE_BETWEEN_TASKS": "0",
            },
        )
        cwd = self.tmp_path / "foreign_cwd"
        cwd.mkdir(exist_ok=True)
        return subprocess.run(
            ["bash", str(self.root / "scripts" / "wake" / "claude.sh"), "--agent=coder"],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=_WAKE_TIMEOUT_S,
        )

    def wait_for_sweep(self) -> str:
        """Block until the nudged sweep reports completion; return the log."""
        deadline = time.time() + _SWEEP_WAIT_S
        while time.time() < deadline:
            if self.trigger_log.is_file() and _SWEEP_DONE in self.trigger_log.read_text(encoding="utf-8"):
                return self.trigger_log.read_text(encoding="utf-8")
            time.sleep(0.5)
        text = self.trigger_log.read_text(encoding="utf-8") if self.trigger_log.is_file() else "(no trigger log)"
        raise AssertionError(f"the nudged sweep did not complete within {_SWEEP_WAIT_S}s:\n{text}")


def _output(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[ \t]*\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# 1. A chain that blocks a task nudges after its lock release
# ---------------------------------------------------------------------------


def test_blocking_chain_nudges_after_lock_release_and_sweep_takes_the_lock(tmp_path):
    box = _Sandbox(tmp_path, mode="block")
    result = box.wake()
    out = result.stdout + result.stderr
    assert result.returncode == 0, _output(result)
    assert _section(box.status_text, "State") == "BLOCKED", _output(result)

    # The nudge line follows the chain's own "done" line, which precedes the
    # end-of-chain lock release; the nudge is the last thing the chain does.
    lines = out.splitlines()
    nudge = [i for i, line in enumerate(lines) if _NUDGE_LINE in line]
    done = [i for i, line in enumerate(lines) if f"project {_PROJECT}: done:" in line]
    assert len(nudge) == 1, _output(result)
    assert done and done[-1] < nudge[0], _output(result)

    log = box.wait_for_sweep()
    detail = f"\ntrigger log:\n{log}\nactions:\n{box.actions}\nstatus:\n{box.status_text}"
    assert "only block-triage" in log, detail
    # The woken run took the repo lock: no contention line and no refusal.
    assert "repo lock held" not in log and "held by" not in log, detail
    assert "flock contended" not in log, detail
    assert "block-triage-refused" not in box.actions, detail
    assert "block-triage:agent_unclassified:escalate" in box.actions, detail
    assert _section(box.status_text, "OVERWATCH Diagnosis"), detail
    assert _section(box.status_text, "Needs Human") == "yes", detail
    # Only the block-triage check ran.
    ran = re.findall(r"\tcheck-(?:ok|error)\tnone\t(\S+)", box.sweep_log)
    assert ran == ["block-triage"], box.sweep_log


# ---------------------------------------------------------------------------
# 2. A chain that blocked nothing fires no nudge
# ---------------------------------------------------------------------------


def test_chain_that_blocked_nothing_fires_no_nudge(tmp_path):
    box = _Sandbox(tmp_path, mode="done")
    result = box.wake()
    assert result.returncode == 0, _output(result)
    assert _section(box.status_text, "State") == "DONE", _output(result)
    assert _NUDGE_LINE not in result.stdout + result.stderr, _output(result)
    assert not box.trigger_log.exists(), box.trigger_log.read_text(encoding="utf-8")
    assert "block-triage" not in box.actions


# ---------------------------------------------------------------------------
# 2b. A block made before dispatch nudges too, after the lock release
# ---------------------------------------------------------------------------

# Appended to the sandbox's own copy of wake_project.sh, so it replaces the
# real fire_block_triage_nudge for that wake: records each call, and whether
# the wake shell still had the project's repo lock file open at that moment
# (run_project_chain's _project_lock_file, read through dynamic scoping),
# compared by device and inode -- the lock is taken on a file linked into
# place, so an open descriptor's path need not be the lock path.
_NUDGE_STUB = textwrap.dedent("""\

    fire_block_triage_nudge() {
      local _held=no _fd _want
      _want="$(stat -c '%d:%i' "${_project_lock_file:-/nonexistent}" 2>/dev/null)" || _want=""
      if [[ -n "$_want" ]]; then
        for _fd in /proc/$$/fd/*; do
          [[ "$(stat -L -c '%d:%i' "$_fd" 2>/dev/null)" == "$_want" ]] && _held=yes
        done
      fi
      printf 'NUDGE project=%s lock_file=%s lock_held=%s\\n' "$1" "${_project_lock_file:-}" "$_held" \\
        >> "${KANBAN_ROOT}/nudge-calls.log"
    }
""")


def test_pre_dispatch_block_nudges_once_after_the_lock_release(tmp_path):
    box = _Sandbox(tmp_path, mode="done", workflow="release")
    with (box.root / "scripts" / "lib" / "wake_project.sh").open("a", encoding="utf-8") as lib:
        lib.write(_NUDGE_STUB)
    provider_log = tmp_path / "provider-calls.log"
    stub = box.bin_dir / "claude"
    stub.write_text(
        stub.read_text(encoding="utf-8").replace(
            "set -u\n", f"set -u\necho \"$*\" >> {provider_log}\n", 1
        ),
        encoding="utf-8",
    )

    result = box.wake()
    out = result.stdout + result.stderr
    assert result.returncode == 0, _output(result)
    assert _section(box.status_text, "State") == "BLOCKED", _output(result)
    assert "worktree_create_failed" in _section(box.status_text, "Block Record"), box.status_text
    dispatched = provider_log.read_text(encoding="utf-8") if provider_log.exists() else ""
    assert "-p" not in dispatched.split(), f"the provider was dispatched:\n{dispatched}"

    calls = (box.root / "nudge-calls.log").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1, f"expected one nudge call, got {calls}\n{_output(result)}"
    assert calls[0].startswith(f"NUDGE project={_PROJECT} lock_file=/"), calls
    assert calls[0].endswith(" lock_held=no"), calls
    assert "create_task_worktree failed" in out, _output(result)


# ---------------------------------------------------------------------------
# 3. fire_block_triage_nudge
# ---------------------------------------------------------------------------


def _nudge_root(tmp_path: pathlib.Path, sweep_body: "str | None") -> pathlib.Path:
    """A minimal kanban root whose scripts/overwatch-sweep.sh is a stub
    printing its arguments (None: no sweep script at all)."""
    root = tmp_path / "kanban"
    (root / "scripts" / "wake").mkdir(parents=True)
    if sweep_body is not None:
        sweep = root / "scripts" / "overwatch-sweep.sh"
        sweep.write_text("#!/usr/bin/env bash\n" + sweep_body + "\n", encoding="utf-8")
        sweep.chmod(0o755)
    return root


def _run_nudge(tmp_path: pathlib.Path, root: pathlib.Path, agent: str):
    """Fire the nudge from a shell that exits the way a wake does: through
    cleanup_on_exit, which kills every job still running."""
    script = textwrap.dedent(f"""\
        set -euo pipefail
        log() {{ echo "LOG: $*"; }}
        source scripts/lib/script_common.sh
        trap cleanup_on_exit EXIT
        source "{_WAKE_PROJECT}"
        AGENT={agent}
        KANBAN_ROOT="{root}"
        SCRIPT_DIR="{root}/scripts/wake"
        fire_block_triage_nudge "{_PROJECT}"
        echo "NUDGE_RC=$?"
        echo "WAIT=${{BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS}}"
    """)
    return run_bash(tmp_path, script, timeout=30)


def _trigger_log_with(root: pathlib.Path, needle: str, timeout: float = 15.0) -> str:
    """The trigger log once it contains *needle* (the sweep runs detached)."""
    log = root / "logs" / "overwatch-trigger.log"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log.is_file() and needle in log.read_text(encoding="utf-8"):
            return log.read_text(encoding="utf-8")
        time.sleep(0.1)
    text = log.read_text(encoding="utf-8") if log.is_file() else "(no trigger log)"
    raise AssertionError(f"{needle!r} never reached the trigger log:\n{text}")


def test_nudge_runs_only_block_triage_with_the_lock_wait(tmp_path):
    root = _nudge_root(tmp_path, 'echo "SWEEP_ARGS=$*"')
    result = _run_nudge(tmp_path, root, "coder")
    assert "NUDGE_RC=0" in result.stdout, result.stderr
    wait = re.search(r"WAIT=(\d+)", result.stdout).group(1)
    assert wait == "60"
    log = _trigger_log_with(root, "SWEEP_ARGS=")
    assert log.count("SWEEP_ARGS=") == 1, log
    assert f"SWEEP_ARGS=--only block-triage --lock-wait {wait}" in log, log
    assert _NUDGE_LINE in result.stdout


def test_nudged_sweep_outlives_the_wake_that_fired_it(tmp_path):
    # The stub sweep is still running when the caller exits through
    # cleanup_on_exit; it must not be reaped as one of the caller's jobs.
    root = _nudge_root(tmp_path, 'sleep 2; echo "SWEEP_FINISHED"')
    result = _run_nudge(tmp_path, root, "coder")
    assert result.returncode == 0, result.stderr
    assert "NUDGE_RC=0" in result.stdout, result.stderr
    _trigger_log_with(root, "SWEEP_FINISHED")


def test_nudge_is_a_no_op_for_overwatch(tmp_path):
    root = _nudge_root(tmp_path, 'echo "SWEEP_ARGS=$*"')
    result = _run_nudge(tmp_path, root, "overwatch")
    assert "NUDGE_RC=0" in result.stdout, result.stderr
    time.sleep(1)
    assert not (root / "logs" / "overwatch-trigger.log").exists()
    assert _NUDGE_LINE not in result.stdout


def test_missing_sweep_leaves_the_caller_unaffected(tmp_path):
    root = _nudge_root(tmp_path, None)
    result = _run_nudge(tmp_path, root, "coder")
    assert result.returncode == 0, result.stderr
    assert "NUDGE_RC=0" in result.stdout, result.stderr


def test_failing_sweep_leaves_the_caller_unaffected(tmp_path):
    root = _nudge_root(tmp_path, "exit 42")
    result = _run_nudge(tmp_path, root, "coder")
    assert result.returncode == 0, result.stderr
    assert "NUDGE_RC=0" in result.stdout, result.stderr


# ---------------------------------------------------------------------------
# 4. Structure
# ---------------------------------------------------------------------------


def _function_body(path: pathlib.Path, name: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return lines[start:end + 1]


def test_finalize_blocked_arm_records_the_block_and_fires_nothing():
    body = _function_body(_FINALIZE, "wd_finalize_task")
    arm_start = next(i for i, line in enumerate(body) if line.strip() == "BLOCKED)")
    arm_end = next(i for i in range(arm_start, len(body)) if body[i].strip() == ";;")
    arm = "\n".join(body[arm_start:arm_end + 1])
    assert "_chain_blocked_a_task=true" in arm
    code = "\n".join(line for line in arm.splitlines() if not line.strip().startswith("#"))
    assert "wake-now.sh" not in code and "overwatch-sweep.sh" not in code and "nohup" not in code


def test_chain_nudges_only_after_its_lock_release_and_only_when_flagged():
    body = _function_body(_WAKE_DRIVER, "run_project_chain")
    code = [line for line in body if not line.strip().startswith("#")]
    nudge = [i for i, line in enumerate(code) if "fire_block_triage_nudge" in line]
    assert len(nudge) == 1, "run_project_chain must fire the nudge exactly once"
    last_release = max(
        i for i, line in enumerate(code)
        if "pgai_lock_release " in line or "pgai_lock_abandon " in line
    )
    assert nudge[0] > last_release, "the nudge must follow the end-of-chain lock release"
    assert '_chain_blocked_a_task' in code[nudge[0] - 1], "the nudge must be guarded by the chain's block flag"
    assert any("local _chain_blocked_a_task=false" in line for line in code)
