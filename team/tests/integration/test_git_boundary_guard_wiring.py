"""
test_git_boundary_guard_wiring.py
===================================
End-to-end coverage for the "CM is the ONLY toucher of any remote" guard's
wake-dispatch wiring (team/scripts/lib/wake_guard_wiring.sh's
wd_install_git_boundary_guard, called from
team/scripts/lib/wake_driver_invoke.sh's wd_invoke_task), asserting from
OUTSIDE the wake pipeline that a real task dispatch actually installs the
shim.

RELATIONSHIP TO THE OTHER TWO GUARD SUITES
-------------------------------------------
Two other suites already cover this boundary at narrower scope, and this
file deliberately does not re-derive either:

  - team/tests/unit/test_git_boundary_guard.py exercises the guard
    primitive itself (team/scripts/lib/git_boundary_guard.sh, the
    executable shim team/scripts/wake/git-guard/git) as a standalone
    subprocess -- refuse/pass-through/record behavior in isolation from
    wake dispatch entirely.
  - team/tests/unit/test_wake_guard_wiring.py sources
    wake_guard_wiring.sh directly and calls wd_install_git_boundary_guard
    under a stubbed bash harness -- proof the mechanism's own logic is
    correct in isolation.

Neither proves the block actually RUNS, in order, at the point in the real
wake pipeline where a live dispatch would reach it, with the real
task-dispatch entry point (`scripts/wake/claude.sh`) exporting real values
into a real child process's environment. That is this file's only job: a
real `bash scripts/wake/claude.sh --agent=<role>` subprocess, against a
real throwaway kanban root, dispatching one real queued task through the
REAL wd_invoke_task code path, with a PATH-shadowing stub `claude` (see
tests/fixtures/claude_provider_stub.py) capturing the exact environment
the agent process itself received -- never re-implementing or extracting
the guard's decision logic as a stand-in for it.

Acceptance criterion exercised: the exported PATH carries the shim
directory first for a non-CM dispatch, and never carries it at all for a
CM dispatch -- the wake-time half of the "CM is the ONLY toucher of any
remote" boundary (team/SOP-core.md, "Git Repositories: Roles and
Boundaries").

Design notes
------------
- Every project registered here uses the document workflow (git_mode=none),
  so no dev tree or git worktree lifecycle is required to dispatch a task
  through wd_invoke_task -- mirrors test_provider_runtime_integration.py's
  own choice for the same reason.
- The kanban root built below is a full, self-contained copy of this dev
  tree's scripts/, pgai_agent_kanban/, workflows/, pm-agent/, and config/
  directories (the same set install.sh mirrors into a live install), so
  the wake script invoked as a subprocess is byte-identical to production.
- This test SUITE ITSELF runs inside a live pgai-agent-kanban wake context
  (this dev tree doubles as a CODER worktree), so the pytest process's own
  environment may carry PGAI_*/KANBAN_ROOT*/wake-tunable variables from the
  surrounding live install. _clean_base_env() strips every name under a
  known leak prefix (mirrors test_provider_runtime_integration.py's own
  _LEAK_PREFIXES) before any subprocess env is built, and every PATH
  assertion below checks for THIS test's own throwaway shim-directory path
  (unique under tmp_path) rather than any generic "does PATH look
  guard-shaped" heuristic -- so an unrelated guard directory the invoking
  shell happens to already carry can never produce a false pass or a false
  failure.
- All temp paths use pytest's tmp_path. No bare /tmp paths.

Naming: function and helper names describe behavior under test, never a
task or bug ID (roles/TESTER.md, "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

from tests.fixtures.claude_provider_stub import call_env, install_claude_provider_stub

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_WAKE_CLAUDE_REL = pathlib.Path("scripts") / "wake" / "claude.sh"
_SUBPROCESS_TIMEOUT_SECONDS = 60

# Env vars this test SUITE's own process may have inherited from the live
# kanban wake context it runs inside -- stripped from every subprocess env
# built below so a host operator's tunables never leak into a throwaway
# root under test. Mirrors test_provider_runtime_integration.py's own
# _LEAK_PREFIXES (same rationale, same list).
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


def _write_guard_probe_task(tasks_dir: pathlib.Path, task_id: str, role: str) -> None:
    """A minimal document-workflow (git_mode=none) task README + BACKLOG
    status.md for *role*. document workflow means no worktree is created
    for this dispatch, so the task can move BACKLOG -> WORKING -> DONE
    through nothing but the provider stub -- exactly the shape
    test_provider_runtime_integration.py's own _write_stub_task uses,
    generalized to a caller-supplied role so both a non-CM dispatch and a
    CM dispatch can be built from the same helper."""
    task_dir = tasks_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "README.md").write_text(
        f"# Task: {task_id}\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Role\n{role}\n\n"
        "## Workflow Type\ndocument\n\n"
        "## Working Directory\nnone\n\n"
        "## Source Branch\nnone\n",
        encoding="utf-8",
    )
    (task_dir / "status.md").write_text(
        f"# Status\n\n## Task\n{task_id}\n\n## State\nBACKLOG\n\n## Blockers\nnone\n",
        encoding="utf-8",
    )


def _build_guard_probe_root(
    root: pathlib.Path,
    project_name: str,
    *,
    tmp_scratch: pathlib.Path,
    agent: str,
    role: str,
    task_id: str,
) -> pathlib.Path:
    """Build a full, self-contained kanban root at *root*, registering one
    document-workflow project (git_mode=none) with a single task of
    *role* queued BACKLOG in the *agent* backlog -- every other agent's
    backlog is present but empty, matching how a real install looks with
    exactly one pending dispatch. [paths] tmp_root is pinned to
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
        "workflow_type = document\n",
        encoding="utf-8",
    )

    queues_dir = proj / "tasks" / "queues"
    queues_dir.mkdir(parents=True, exist_ok=True)
    for other_agent in ("pm", "po", "tester", "cm", "writer", "coder"):
        if other_agent == agent:
            continue
        (queues_dir / f"{other_agent}_backlog.md").write_text(
            f"# {other_agent} backlog\n\nNo tasks queued by this fixture.\n", encoding="utf-8"
        )

    _write_guard_probe_task(proj / "tasks", task_id, role)
    (queues_dir / f"{agent}_backlog.md").write_text(
        f"# {agent} backlog\n\n- [ ] {task_id} — guard-wiring probe task\n", encoding="utf-8"
    )

    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "locks").mkdir(parents=True, exist_ok=True)

    # Full, real copies -- the same set install.sh mirrors into a live
    # install -- so the wake script invoked as a subprocess is
    # byte-identical to production, and self-locates its own sibling
    # scripts/wake/git-guard/ shim directory exactly as it would on a real
    # host.
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
    environment."""
    return subprocess.run(
        ["bash", str(root / _WAKE_CLAUDE_REL), f"--agent={agent}"],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_non_cm_dispatch_puts_shim_first_on_path_and_exports_guard_variables(
    tmp_path: pathlib.Path,
) -> None:
    """A real WRITER dispatch (non-CM) reaches wd_invoke_task's guard-wiring
    block: the shim directory sibling to this root's own scripts/wake/
    directory is prepended to PATH ahead of everything else, and both
    PGAI_AGENT_ROLE and PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE are exported
    into the AGENT PROCESS's own environment (proven by the stub `claude`
    dumping its received env, not by inspecting a wake-script variable)."""
    project_name = "guard-wiring-non-cm"
    task_id = "WRITER-20260101-001-guard-wiring-probe"
    tmp_scratch = tmp_path / "kanban_scratch"
    root = tmp_path / "kanban_root"
    _build_guard_probe_root(
        root, project_name, tmp_scratch=tmp_scratch, agent="writer", role="WRITER", task_id=task_id
    )

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir)

    env = _clean_base_env()
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["MAX_TASK_SECONDS"] = "20"
    env["KILL_GRACE_SECONDS"] = "5"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = _run_wake(root, "writer", env)
    assert result.returncode == 0, (
        f"wake firing exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    status_text = (root / "projects" / project_name / "tasks" / task_id / "status.md").read_text(
        encoding="utf-8"
    )
    assert "## State\nDONE" in status_text, (
        f"Expected the probe task to reach DONE through the stub provider.\nstatus.md:\n{status_text}"
    )

    agent_env = call_env(control_dir, 1)

    expected_shim_dir = str(root / "scripts" / "wake" / "git-guard")
    path_entries = agent_env.get("PATH", "").split(os.pathsep)
    assert path_entries and path_entries[0] == expected_shim_dir, (
        f"Expected the guard shim directory ({expected_shim_dir}) to be the FIRST "
        f"PATH entry the agent process received for a non-CM dispatch; got PATH="
        f"{agent_env.get('PATH', '')!r}"
    )
    assert agent_env.get("PGAI_AGENT_ROLE") == "writer", (
        f"Expected PGAI_AGENT_ROLE=writer in the agent process's environment; "
        f"got {agent_env.get('PGAI_AGENT_ROLE')!r}"
    )

    evidence_file = agent_env.get("PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE")
    assert evidence_file, "Expected a non-empty PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE export"
    expected_logs_dir = str(root / "projects" / project_name / "tasks" / task_id / "logs")
    assert str(pathlib.Path(evidence_file).parent) == expected_logs_dir, (
        f"Expected the evidence file to live under the task's own logs/ directory "
        f"({expected_logs_dir}); got {evidence_file!r}"
    )


def test_cm_dispatch_excludes_shim_from_path(tmp_path: pathlib.Path) -> None:
    """A real CM dispatch never gains the guard shim directory on PATH --
    CM's git resolution, and therefore every CM release operation, stays
    byte-identical to a pre-guard install -- while PGAI_AGENT_ROLE is still
    exported (positive, explicit signal for every role, including cm; it
    is what lets the guard's own role=="cm" pass-through path do the work,
    rather than a PATH-based exemption)."""
    project_name = "guard-wiring-cm"
    task_id = "CM-20260101-001-guard-wiring-probe"
    tmp_scratch = tmp_path / "kanban_scratch"
    root = tmp_path / "kanban_root"
    _build_guard_probe_root(
        root, project_name, tmp_scratch=tmp_scratch, agent="cm", role="CM", task_id=task_id
    )

    bin_dir = tmp_path / "stub-bin"
    control_dir = tmp_path / "stub-control"
    install_claude_provider_stub(bin_dir, control_dir)

    env = _clean_base_env()
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(root)
    env["MAX_TASK_SECONDS"] = "20"
    env["KILL_GRACE_SECONDS"] = "5"
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    result = _run_wake(root, "cm", env)
    assert result.returncode == 0, (
        f"wake firing exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    status_text = (root / "projects" / project_name / "tasks" / task_id / "status.md").read_text(
        encoding="utf-8"
    )
    assert "## State\nDONE" in status_text, (
        f"Expected the probe task to reach DONE through the stub provider.\nstatus.md:\n{status_text}"
    )

    agent_env = call_env(control_dir, 1)

    expected_shim_dir = str(root / "scripts" / "wake" / "git-guard")
    path_entries = agent_env.get("PATH", "").split(os.pathsep)
    assert expected_shim_dir not in path_entries, (
        f"Expected the guard shim directory ({expected_shim_dir}) to be absent from a "
        f"CM dispatch's PATH; got PATH={agent_env.get('PATH', '')!r}"
    )
    assert agent_env.get("PGAI_AGENT_ROLE") == "cm", (
        f"Expected PGAI_AGENT_ROLE=cm in the agent process's environment even though "
        f"the shim is not installed for this role; got {agent_env.get('PGAI_AGENT_ROLE')!r}"
    )
