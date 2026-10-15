"""
test_provider_runtime_integration.py
=====================================
End-to-end integration evidence for the provider runtime's wake-pipeline
guarantees: three facts that a unit suite cannot establish because each
one is a claim about the ASSEMBLED wake pipeline, not about a single
function:

  1. The derived CLI background-wait ceiling (and an operator override of
     it) reaches the environment the AGENT PROCESS itself receives, under a
     genuinely cron-shaped environment -- no interactive shell, no
     inherited PGAI_* exports, no sourced profile.
  2. A provider-safeguard refusal on the first invocation of an optional
     telemetry block is recoverable: the wake retries once without the
     block, narrates the skip, and the task still reaches its normal
     terminal state.
  3. The per-firing identity header (provider CLI version + effective
     model) is present in the wake log for a firing that finds work AND
     for a firing that finds none.

Every fixture below drives the REAL production entry point -- a real
`bash scripts/wake/claude.sh --agent=writer` subprocess against a real,
throwaway kanban root -- rather than calling internal shell functions
directly, following the same "extraction and harness" convention as
test_wake_contention_retry.py and test_overwatch_wake_driven_fixtures.py.
The `claude` CLI itself is never invoked: a PATH-shadowing stub binary
(tests/fixtures/claude_provider_stub.py, built on the same interception
technique as git_network_stub.py) stands in for it, so every assertion
below is about the REAL provider_dispatch / wake_driver_invoke.sh code
path reaching a REAL child process -- just not the real `claude` CLI. No
network call, no token, no real provider invocation happens anywhere in
this file.

Every project registered here uses the document workflow (git_mode=none),
so no dev tree or git repository is required -- the "writer" agent
dispatches through the exact same provider_dispatch / wake_driver_invoke.sh
machinery a CODER/WRITER release-workflow task would, without needing a
git worktree lifecycle this file has no reason to exercise.

Two genuine production defects surfaced while building this suite (see
each test's docstring for detail) were fixed in the same task, not worked
around: (a) claude-env was dead code on the wake path -- env_bootstrap.sh's
idempotency guard always short-circuited before reaching it, because
wake_bootstrap_init always pre-exports PGAI_AGENT_KANBAN_ROOT_PATH before
sourcing env_bootstrap.sh; (b) provider_dispatch's own internal `set +e` /
`set -e` toggling (a process-global flag in bash, not function-scoped)
silently reactivated errexit on return, which aborted the background
watchdog subshell on any failed provider invocation before the safeguard
retry's own check ever ran -- meaning the retry could never fire for a
real refusal. Both fixes are minimal and additive to the files tickets
1-5 shipped (team/scripts/lib/wake_bootstrap.sh,
team/scripts/lib/wake_driver_invoke.sh); see the inline comments at each
fix site for the full mechanism.

Design notes
------------
- Every kanban root built here is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/, and config/
  directories (the same set install.sh mirrors into a live install), so
  the wake script invoked as a subprocess is byte-identical to production.
- This test SUITE ITSELF runs inside a live pgai-agent-kanban wake context
  (this dev tree doubles as a CODER worktree), so the pytest process's own
  environment carries PGAI_*/CLAUDE_*/KANBAN_ROOT*/wake-tunable variables
  from the surrounding live install. Every subprocess env built below
  strips those (see docs/test-authoring-guidelines.md, Anti-pattern 2)
  rather than inheriting them, so no live-install state leaks into the
  throwaway roots under test.
- The cron-context fixture (criteria 1-2) resolves its kanban root purely
  through $HOME, matching wake/claude.sh's own
  TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
  default -- the environment carries zero PGAI_* exports, reproducing the
  minimal-config cron scenario the release exists to harden.
- All temp paths use pytest's tmp_path. No bare /tmp.
"""

from __future__ import annotations

import glob
import os
import pathlib
import shutil
import subprocess

import pytest

from tests.fixtures.claude_provider_stub import (
    call_count,
    call_env,
    install_claude_provider_stub,
    set_stub_mode,
)

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_SUBPROCESS_TIMEOUT_SECONDS = 60

# Env vars this test SUITE's own process may have inherited from the live
# kanban wake context it runs inside -- stripped from every subprocess env
# built below so a host operator's PGAI_WRITER_MODEL, STOP_FILE, dev-tree
# path, etc. never leak into a throwaway root under test.
_LEAK_PREFIXES = (
    "PGAI_", "PP_", "KANBAN_ROOT", "CLAUDE_", "AI_AGENT", "STOP_FILE",
    "TMPDIR", "MAX_TASK_SECONDS", "MAX_RUNTIME_SECONDS", "MAX_TASKS_PER_WAKE",
    "MAX_PROJECT_SECONDS", "KILL_GRACE_SECONDS", "PAUSE_BETWEEN_TASKS",
    "STOP_ON_BLOCKED", "AI_AUTH_MODE", "BASH_MAX_TIMEOUT_MS", "DASHBOARD_",
    "PM_MAX_TASKS",
)


def _clean_base_env() -> dict[str, str]:
    """The host process env with every var this suite's own live-wake
    context may have exported stripped out. See module docstring."""
    return {
        k: v
        for k, v in os.environ.items()
        if not any(k == p or k.startswith(p) for p in _LEAK_PREFIXES)
    }


# ---------------------------------------------------------------------------
# Kanban root builder -- shared by every fixture below.
# ---------------------------------------------------------------------------


def _write_stub_task(tasks_dir: pathlib.Path, task_id: str) -> pathlib.Path:
    """A minimal WRITER/document-workflow task README + BACKLOG status.md.
    document workflow (git_mode=none) means no worktree is created for this
    dispatch -- the completion gate that would otherwise apply to a
    coder/writer task passes trivially with no branch to verify (see
    wake_lifecycle.sh's coder_writer_completion_gate), so a task can move
    BACKLOG -> WORKING -> DONE through nothing but the provider stub."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        "## Role\nWRITER\n\n"
        "## Workflow Type\ndocument\n\n"
        "## Working Directory\nnone\n\n"
        "## Source Branch\nnone\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )
    return task_dir


def _build_kanban_root(
    root: pathlib.Path,
    project_name: str,
    *,
    tmp_scratch: pathlib.Path,
    queued_task_id: str | None = None,
    reasoning_trace: bool = False,
    training_agents: str = "",
) -> pathlib.Path:
    """Build a full, self-contained kanban root at *root*, registering one
    document-workflow project (git_mode=none -- no dev tree or git repo
    required) named *project_name*. When *queued_task_id* is given, a
    single WRITER task is queued BACKLOG in the writer backlog; otherwise
    the writer backlog is empty. [paths] tmp_root is pinned to
    *tmp_scratch* so config_loader.sh's own temp-dir default never falls
    back to the shared system-wide framework temp root.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        "[paths]\n"
        f"tmp_root = {tmp_scratch}\n"
        "tmp_subdir = pgai_kanban_tmp\n\n"
        "[chain]\npm_mode = automatic\n\n"
        "[wake]\nmax_tasks_per_wake = 5\n",
        encoding="utf-8",
    )
    (root / "projects.cfg").write_text(
        f"[project:{project_name}]\npriority=1\nenabled=true\n\n", encoding="utf-8"
    )

    proj = root / "projects" / project_name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.cfg").write_text(
        "[project]\n"
        f"project_name = {project_name}\n"
        "workflow_type = document\n\n"
        "[training]\n"
        f"reasoning_trace = {'true' if reasoning_trace else 'false'}\n"
        f"training_agents = {training_agents}\n",
        encoding="utf-8",
    )
    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    for agent in ("pm", "tester", "cm"):
        (queues_dir / f"{agent}_backlog.md").write_text(
            f"# {agent} backlog\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
        )
    if queued_task_id:
        _write_stub_task(proj / "tasks", queued_task_id)
        (queues_dir / "writer_backlog.md").write_text(
            f"# writer backlog\n\n- [ ] {queued_task_id} — stub task\n", encoding="utf-8"
        )
    else:
        (queues_dir / "writer_backlog.md").write_text(
            "# writer backlog\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
        )

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Full, real copies -- the same set install.sh mirrors into a live
    # install -- so the wake script invoked as a subprocess is
    # byte-identical to production.
    shutil.copytree(_TEAM_DIR / "scripts", root / "scripts")
    shutil.copytree(_TEAM_DIR / "pgai_agent_kanban", root / "pgai_agent_kanban")
    shutil.copytree(_TEAM_DIR / "workflows", root / "workflows")
    shutil.copytree(_TEAM_DIR / "pm-agent", root / "pm-agent")
    shutil.copytree(_TEAM_DIR / "config", root / "config")

    return root


def _run_wake(
    root: pathlib.Path, agent: str, env: dict[str, str], *, timeout: float = _SUBPROCESS_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess:
    """Invoke the REAL production entry point: bash scripts/wake/claude.sh
    --agent=<agent>, against *root*, with *env* as the complete subprocess
    environment (never merged with the pytest process's own env implicitly
    -- callers build a deliberate env from scratch or from
    _clean_base_env())."""
    return subprocess.run(
        ["bash", str(root / _WAKE_CLAUDE_REL), f"--agent={agent}"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _latest_batch_log(root: pathlib.Path, agent: str = "writer") -> str:
    """The wake batch log file this firing wrote, read as text. This is
    where the safeguard-refusal narration and skip phrase land -- they are
    written via `tee -a "$LOG_FILE" >/dev/null`, so they never reach the
    subprocess's own captured stdout/stderr; only the on-disk log file
    carries them."""
    files = sorted(glob.glob(str(root / "logs" / "agents" / f"{agent}-batch-*.log")))
    assert files, f"no {agent} wake batch log found under {root}/logs/agents/"
    return pathlib.Path(files[-1]).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Cron-context environment builder (criteria 1-2).
# ---------------------------------------------------------------------------


def _cron_shaped_root(tmp_path: pathlib.Path, project_name: str, *, queued_task_id: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a kanban root at <homedir>/pgai_agent_kanban -- matching
    wake/claude.sh's own TEAM_ROOT default resolution
    (TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}") --
    so the cron env below can resolve the root through nothing but $HOME.
    Zero PGAI_* exports required: this is the minimal-config cron scenario
    an operator who never created a shell-env file actually runs under.
    Returns (home_dir, root)."""
    home_dir = tmp_path / "homedir"
    root = home_dir / "pgai_agent_kanban"
    tmp_scratch = tmp_path / "kanban_scratch"
    _build_kanban_root(root, project_name, tmp_scratch=tmp_scratch, queued_task_id=queued_task_id)
    return home_dir, root


def _cron_env(home_dir: pathlib.Path, stub_bin_dir: pathlib.Path) -> dict[str, str]:
    """A genuinely cron-shaped environment: stripped to the minimum a real
    cron firing gets. No interactive shell (this is a subprocess.run, not a
    pty). No inherited PGAI_* exports -- built from scratch, not from
    os.environ. No sourced profile -- HOME points at a throwaway directory
    with no .bashrc/.bash_profile, and nothing here reads /etc/profile or
    /etc/environment. The kanban root resolves purely via wake/claude.sh's
    own $HOME/pgai_agent_kanban default.

    LANG is set to a UTF-8 locale -- a real crontab virtually always
    inherits one from /etc/environment or the crond process's own
    defaults. Omitting it here would make `wc -m` (used by an unrelated
    dashboard-config validation outside this test's scope) count the
    multi-byte default status glyph as multiple bytes under the bare C/
    POSIX locale this stripped environment would otherwise default to,
    failing config load for a reason orthogonal to what this fixture
    tests.
    """
    real_python312 = shutil.which("python3.12") or shutil.which("python3")
    assert real_python312, "no python3.12 (or python3) binary found on the host PATH"
    py_shim = stub_bin_dir / "python3"
    if not py_shim.exists():
        py_shim.symlink_to(real_python312)
    cron_path = os.pathsep.join(
        [str(stub_bin_dir), "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    )
    return {
        "PATH": cron_path,
        "HOME": str(home_dir),
        "LOGNAME": "cronuser",
        "SHELL": "/bin/sh",
        "LANG": "C.UTF-8",
    }


# ---------------------------------------------------------------------------
# Criteria 1-2: derived ceiling and operator override reach the agent
# process's own environment, under a cron-shaped environment.
# ---------------------------------------------------------------------------


def test_cron_wake_exports_derived_ceiling_to_the_agent_process(tmp_path: pathlib.Path) -> None:
    """A wake firing under a cron-shaped stripped environment (no
    interactive shell, no inherited PGAI_* exports, no sourced profile)
    derives BASH_MAX_TIMEOUT_MS from the kanban's own per-task budget
    (MAX_TASK_SECONDS=5400 default + KILL_GRACE_SECONDS=30 default -> the
    documented 5430000 the derivation predicts) and that value is present
    in the environment the AGENT PROCESS itself receives -- not merely
    assigned somewhere in the wake script. Asserted by having the stub
    `claude` binary (the literal agent process provider_dispatch execs)
    dump its own received environment to a file, then reading that file
    back -- never inferring the value from the wake script's own log
    output.
    """
    project_name = "cron-ceiling-default"
    task_id = "WRITER-20260101-001-cron-ceiling-default"
    home_dir, root = _cron_shaped_root(tmp_path, project_name, queued_task_id=task_id)

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir)

    result = _run_wake(root, "writer", _cron_env(home_dir, bin_dir))

    assert result.returncode == 0, (
        f"wake firing exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert call_count(control_dir) == 1, (
        "Expected exactly one stub invocation (one queued task, no safeguard retry configured)."
    )

    agent_env = call_env(control_dir, 1)
    assert agent_env.get("BASH_MAX_TIMEOUT_MS") == "5430000", (
        "Expected the derived ceiling (MAX_TASK_SECONDS=5400 + KILL_GRACE_SECONDS=30, "
        "in ms) in the AGENT PROCESS's own environment; got "
        f"BASH_MAX_TIMEOUT_MS={agent_env.get('BASH_MAX_TIMEOUT_MS')!r}.\n"
        f"Full stub-received env: {agent_env}"
    )

    status_text = (root / "projects" / project_name / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## State\nDONE" in status_text, (
        f"Expected the task to reach DONE via the stub agent turn.\nstatus.md:\n{status_text}"
    )


def test_cron_wake_honours_claude_env_ceiling_override_in_the_agent_process(tmp_path: pathlib.Path) -> None:
    """Same cron-shaped environment and same wake firing as the derivation
    test above, except a claude-env file in the temporary kanban root sets
    BASH_MAX_TIMEOUT_MS to an operator-chosen value -- distinct from both
    the CLI's own 600000ms default and the 5430000ms derived default, so a
    match can only mean the override actually won. Asserts the OPERATOR
    OVERRIDE, not the derivation, is what reaches the agent process.

    This test caught a genuine defect while under construction:
    env_bootstrap.sh's own claude-env sourcing block was unreachable from
    the wake path -- wake_bootstrap_init always pre-exports
    PGAI_AGENT_KANBAN_ROOT_PATH before sourcing env_bootstrap.sh (necessary
    so the BASH_SOURCE-derivation walk does not land on scripts/lib/
    instead of the kanban root), which trips env_bootstrap.sh's own
    idempotency guard and returns before ever reaching its claude-env
    block -- dead code on every wake firing, cron or otherwise. Fixed by
    adding a direct claude-env re-source in wake_bootstrap.sh (mirroring
    the existing shell-env re-source immediately above it) with the same
    syntax-check-before-source contract; see team/scripts/lib/
    wake_bootstrap.sh and the CENSUS-ANCHOR wake-bootstrap-claude-env-01
    comment at the fix site for the full mechanism.
    """
    project_name = "cron-ceiling-override"
    task_id = "WRITER-20260101-002-cron-ceiling-override"
    home_dir, root = _cron_shaped_root(tmp_path, project_name, queued_task_id=task_id)

    override_ms = "777777"
    (root / "claude-env").write_text(
        f'export BASH_MAX_TIMEOUT_MS="{override_ms}"\n', encoding="utf-8"
    )

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir)

    result = _run_wake(root, "writer", _cron_env(home_dir, bin_dir))

    assert result.returncode == 0, (
        f"wake firing exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert call_count(control_dir) == 1

    agent_env = call_env(control_dir, 1)
    assert agent_env.get("BASH_MAX_TIMEOUT_MS") == override_ms, (
        f"Expected the claude-env operator override ({override_ms}) in the AGENT "
        f"PROCESS's own environment, not the derived default; got "
        f"BASH_MAX_TIMEOUT_MS={agent_env.get('BASH_MAX_TIMEOUT_MS')!r}.\n"
        f"Full stub-received env: {agent_env}"
    )
    assert agent_env.get("BASH_MAX_TIMEOUT_MS") != "5430000", (
        "The derived default leaked through despite the operator override being present."
    )


# ---------------------------------------------------------------------------
# Criterion 3: a provider-safeguard refusal on the optional telemetry block
# is retried once and narrated; the wake completes with the task reaching
# its normal terminal state.
# ---------------------------------------------------------------------------


def test_safeguard_refusal_retries_once_and_task_reaches_normal_terminal_state(
    tmp_path: pathlib.Path,
) -> None:
    """A wake firing dispatches a WRITER task with the documentation-
    feedback telemetry block active (project.cfg [training]
    reasoning_trace=true, training_agents=writer). The provider stub
    refuses the FIRST invocation with a safeguard-shaped error and
    succeeds on the SECOND. Asserts: the wake firing exits 0, the task
    reaches DONE (its normal terminal state -- not BLOCKED, not left in
    WORKING), the wake log carries the verbatim provider error text, and
    the wake log carries the exact narration phrase "skipping trace
    injection: provider safeguard". Exactly two stub invocations occur --
    the retry fires exactly once, never a third attempt.

    This test caught a second genuine defect while under construction:
    provider_dispatch (provider_claude.sh) brackets its own CLI invocation
    with its own internal `set +e` / `set -e` pair. Because `set -e`/
    `set +e` are process-global flags in bash with no function-call
    scoping, provider_dispatch's trailing `set -e` silently reactivated
    errexit for every command running after it returned -- including the
    very next line, a provider_parse_output call that fails on any refused
    invocation by design. Under the reactivated errexit, that expected
    failure aborted the whole background watchdog subshell immediately,
    before the retry wrapper's own safeguard check
    (_wd_is_safeguard_refusal) ever ran -- so the retry could never fire
    for any real refusal, only for the mocked-away scenario a
    function-level unit test exercises. Fixed with a defensive `set +e`
    re-assertion in _wd_run_provider_and_capture_tokens
    (wake_driver_invoke.sh) immediately after the provider_dispatch call;
    see the inline comment at the fix site for the full mechanism.
    """
    project_name = "safeguard-retry"
    task_id = "WRITER-20260101-003-safeguard-retry"
    tmp_scratch = tmp_path / "kanban_scratch"
    root = tmp_path / "kanban_root"
    _build_kanban_root(
        root,
        project_name,
        tmp_scratch=tmp_scratch,
        queued_task_id=task_id,
        reasoning_trace=True,
        training_agents="writer",
    )

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir)
    set_stub_mode(control_dir, "safeguard-then-success")

    env = _clean_base_env()
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    # Bound the per-task watchdog well under the subprocess timeout -- the
    # stub completes near-instantly either way, this only guards against a
    # genuine hang turning into a long wait.
    env["MAX_TASK_SECONDS"] = "20"
    env["KILL_GRACE_SECONDS"] = "5"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = _run_wake(root, "writer", env)

    assert result.returncode == 0, (
        f"wake firing exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    status_text = (root / "projects" / project_name / "tasks" / task_id / "status.md").read_text(encoding="utf-8")
    assert "## State\nDONE" in status_text, (
        f"Expected the task to reach its normal terminal state (DONE) after the "
        f"safeguard refusal was retried away.\nstatus.md:\n{status_text}"
    )

    log_text = _latest_batch_log(root)
    assert "Error: request blocked by safety system before dispatch." in log_text, (
        f"Expected the verbatim provider error in the wake log.\nlog:\n{log_text}"
    )
    assert "skipping trace injection: provider safeguard" in log_text, (
        f"Expected the exact skip narration phrase in the wake log.\nlog:\n{log_text}"
    )

    assert call_count(control_dir) == 2, (
        f"Expected exactly two stub invocations (the refused first attempt, then "
        f"exactly one retry) -- got {call_count(control_dir)}."
    )


# ---------------------------------------------------------------------------
# Criterion 4: the per-firing identity header (CLI version + model) is
# present in the wake log, for a firing that finds work AND for a firing
# that finds none.
# ---------------------------------------------------------------------------


def test_identity_header_present_for_firing_with_and_without_work(tmp_path: pathlib.Path) -> None:
    """The PROVIDER-IDENTITY-LOG block in wake/claude.sh runs once per
    firing, unconditionally, before wake_common_preflight/wake_common_run
    -- so it fires whether or not the firing goes on to find any queued
    work. Two independent firings against two independent throwaway roots
    (never sharing queue state -- see docs/test-authoring-guidelines.md,
    Anti-pattern 3) each assert both the "provider CLI version:" and
    "model:" lines are present, with the exact values this fixture
    controls (the stub's --version banner and an explicit
    PGAI_WRITER_MODEL), in that firing's own wake log.
    """
    stub_version = "9.9.9 (Integration Test Stub)"
    model_name = "stub-model-x"

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir, version=stub_version)

    def _env_for(root: pathlib.Path) -> dict[str, str]:
        env = _clean_base_env()
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
        env["PGAI_WRITER_MODEL"] = model_name
        env["MAX_TASK_SECONDS"] = "20"
        env["KILL_GRACE_SECONDS"] = "5"
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    # --- Firing 1: finds no work (empty writer backlog) ---
    no_work_root = tmp_path / "kanban_root_no_work"
    _build_kanban_root(
        no_work_root, "identity-no-work", tmp_scratch=tmp_path / "kanban_scratch_no_work", queued_task_id=None
    )
    no_work_result = _run_wake(no_work_root, "writer", _env_for(no_work_root))
    assert no_work_result.returncode == 0, (
        f"no-work firing exited {no_work_result.returncode}.\n"
        f"stdout:\n{no_work_result.stdout}\nstderr:\n{no_work_result.stderr}"
    )
    no_work_log = _latest_batch_log(no_work_root)
    assert f"provider CLI version: {stub_version}" in no_work_log, (
        f"Expected the CLI version line in the no-work firing's log.\nlog:\n{no_work_log}"
    )
    assert f"model: {model_name}" in no_work_log, (
        f"Expected the model line in the no-work firing's log.\nlog:\n{no_work_log}"
    )

    # --- Firing 2: finds work (one queued WRITER task) ---
    work_task_id = "WRITER-20260101-004-identity-log"
    work_root = tmp_path / "kanban_root_work"
    _build_kanban_root(
        work_root, "identity-work", tmp_scratch=tmp_path / "kanban_scratch_work", queued_task_id=work_task_id
    )
    work_result = _run_wake(work_root, "writer", _env_for(work_root))
    assert work_result.returncode == 0, (
        f"work-found firing exited {work_result.returncode}.\n"
        f"stdout:\n{work_result.stdout}\nstderr:\n{work_result.stderr}"
    )
    work_log = _latest_batch_log(work_root)
    assert f"provider CLI version: {stub_version}" in work_log, (
        f"Expected the CLI version line in the work-found firing's log.\nlog:\n{work_log}"
    )
    assert f"model: {model_name}" in work_log, (
        f"Expected the model line in the work-found firing's log.\nlog:\n{work_log}"
    )

    status_text = (
        work_root / "projects" / "identity-work" / "tasks" / work_task_id / "status.md"
    ).read_text(encoding="utf-8")
    assert "## State\nDONE" in status_text, (
        f"Expected the work-found firing to actually complete the queued task.\n"
        f"status.md:\n{status_text}"
    )
