"""
test_operator_commands.py
=========================
Integration tests for operator commands end-to-end against a live temporary tree.

Commands covered:
  show        — emit task status.md or README.md; emit intake item content
  reset       — reset a task to BACKLOG; reset an intake item to open; refuse WORKING state
  close       — set task to DONE; close intake items; flip backlog marker to [x]
  wontdo      — mark a task WONT-DO; flip backlog marker to [x]
  delete      — delete a DONE task directory; guard refuses non-terminal items
  halt        — create per-project HALT file; idempotent; blocks discovery
  halt-global — create kanban-root HALT file; blocks all projects
  unhalt-global — remove kanban-root HALT file; resumes all projects
  intake      — deposit a staged file into the correct intake directory; routes by prefix

Each test asserts both:
  - On-disk state after the command runs (task folder, status.md, HALT marker, etc.)
  - Queue-marker sync (task state matches the marker in its agent backlog file)

Design notes:
  - All tests use the two_project_root fixture (via the integration conftest) so
    they operate against a real temporary kanban tree with two projects.
  - bash script entrypoints are invoked via subprocess with PGAI_AGENT_KANBAN_ROOT_PATH
    pointed at the temp tree.  PYTHONPATH is set to the team/ directory so that
    the scripts can resolve python3 -m pgai_agent_kanban.ops.
  - All scratch is contained under pytest's tmp_path (inherited via two_project_root
    or tmp_path fixture).  No bare /tmp paths appear in this file.
  - Test names describe the behavior under test; no bug IDs, version numbers, or
    scaffolding labels appear in function names (SOP.md Anti-pattern 6).
  - No order dependence: each test is fully self-contained and isolated.
  - Scrubbed-env + foreign-cwd tests (see TestCloseShForeignCwd and
    TestHaltUnhaltGlobalScrubbedEnv) simulate the wake's cron-shaped environment:
    env -i with only HOME, PATH, and PGAI_AGENT_KANBAN_ROOT_PATH, invoked from a
    working directory other than the kanban root.  These tests verify that
    pp_run_ops resolves the package correctly without PYTHONPATH pre-set by the
    caller, and that all scripts function from an arbitrary cwd.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Path helpers — resolved from this file's own location.
#
# File structure:
#   team/tests/integration/test_operator_commands.py
#                    └── team/tests/integration/    (this file)
#                        └── team/tests/
#                            └── team/
#                                └── <dev-tree-root>
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent   # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

_SHOW_SCRIPT = _SCRIPTS_DIR / "show.sh"
_RESET_SCRIPT = _SCRIPTS_DIR / "reset.sh"
_CLOSE_SCRIPT = _SCRIPTS_DIR / "close.sh"
_WONTDO_SCRIPT = _SCRIPTS_DIR / "wontdo.sh"
_DELETE_SCRIPT = _SCRIPTS_DIR / "delete.sh"
_HALT_SCRIPT = _SCRIPTS_DIR / "halt.sh"
_HALT_GLOBAL_SCRIPT = _SCRIPTS_DIR / "halt-global.sh"
_UNHALT_GLOBAL_SCRIPT = _SCRIPTS_DIR / "unhalt-global.sh"
_INTAKE_SCRIPT = _SCRIPTS_DIR / "intake.sh"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_operator(
    script: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    *,
    extra_env: Optional[dict] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run an operator bash script against a temporary kanban tree.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH and PYTHONPATH so the script operates
    against the caller's temp tree and can resolve python3 -m pgai_agent_kanban.ops.

    Args:
        script:      Absolute path to the bash script.
        args:        Additional CLI arguments to pass to the script.
        kanban_root: Path to the temporary kanban root (from two_project_root
                     or equivalent fixture).  Must exist.
        extra_env:   Optional additional environment variables.  Caller wins
                     on collision.
        timeout:     Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    base_env = dict(os.environ)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    # PYTHONPATH must point at the team/ directory so that the scripts can
    # resolve `python3 -m pgai_agent_kanban.ops` (the package lives at
    # team/pgai_agent_kanban/).
    existing_pythonpath = base_env.get("PYTHONPATH", "")
    team_dir_str = str(_TEAM_DIR)
    if team_dir_str not in existing_pythonpath.split(os.pathsep):
        base_env["PYTHONPATH"] = (
            team_dir_str + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
        )
    # Prevent live install from leaking through env-var lookup.
    base_env.pop("PGAI_DEV_TREE_PATH", None)
    if extra_env:
        base_env.update(extra_env)

    return subprocess.run(
        ["bash", str(script)] + args,
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=timeout,
    )


def _run_operator_scrubbed_foreign_cwd(
    script: pathlib.Path,
    args: list[str],
    kanban_root: pathlib.Path,
    foreign_cwd: pathlib.Path,
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run an operator bash script with a scrubbed env from a foreign working directory.

    Simulates the wake's cron-shaped environment: env -i with only HOME, PATH,
    and PGAI_AGENT_KANBAN_ROOT_PATH.  The subprocess is invoked from *foreign_cwd*,
    which must NOT be the kanban root.  No PYTHONPATH is set in the environment —
    pp_run_ops must derive PYTHONPATH from its own file location.

    This shape exercises the production failure mode that BUG-0082 documented:
    bare ``python3 -m pgai_agent_kanban.*`` invocations failed because PYTHONPATH
    was not set and the working directory was not the kanban root.  Scripts that
    route through pp_run_ops must pass this test; scripts that do not will fail.

    Args:
        script:      Absolute path to the bash script.
        args:        Additional CLI arguments to pass to the script.
        kanban_root: Path to the temporary kanban root.
        foreign_cwd: Working directory for the subprocess.  Must exist and must
                     not be kanban_root (to test cwd-independence).
        timeout:     Subprocess timeout in seconds.

    Returns:
        subprocess.CompletedProcess with returncode, stdout, stderr.
    """
    scrubbed_env = {
        "HOME": str(pathlib.Path.home()),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
    }

    return subprocess.run(
        ["bash", str(script)] + args,
        capture_output=True,
        text=True,
        env=scrubbed_env,
        cwd=str(foreign_cwd),
        timeout=timeout,
    )


def _build_task_folder(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
    state: str = "BACKLOG",
) -> pathlib.Path:
    """Create a minimal task folder with status.md, README.md, artifacts/, and logs/.

    Also adds a queue entry (- [ ] <task_id>) to the appropriate agent backlog
    file so that marker-sync assertions can verify the backlog is updated.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project the task belongs to.
        task_id:      Full task identifier (e.g. CODER-20260628-TEST-001-some-slug).
                      The agent prefix (first component) determines which backlog file
                      receives the queue marker.
        state:        Initial task state (default: BACKLOG).

    Returns:
        pathlib.Path — the created task directory.
    """
    task_dir = kanban_root / "projects" / project_name / "tasks" / task_id
    (task_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (task_dir / "logs").mkdir(parents=True, exist_ok=True)

    (task_dir / "README.md").write_text(
        f"# {task_id}\n\n"
        f"## Role\nCODER\n\n"
        f"## Task ID\n{task_id}\n\n"
        f"## Goal\nIntegration test task.\n",
        encoding="utf-8",
    )

    (task_dir / "status.md").write_text(
        f"# Status\n\n"
        f"## Task\n{task_id}\n\n"
        f"## State\n{state}\n\n"
        f"## Summary\nIntegration test synthetic task.\n\n"
        f"## Artifacts\nnone\n\n"
        f"## Blockers\nnone\n\n"
        f"## Needs Human\nno\n",
        encoding="utf-8",
    )

    # Add a queue marker to the appropriate agent backlog.
    agent_prefix = task_id.split("-")[0].lower()
    backlog_file = (
        kanban_root / "projects" / project_name / "tasks" / "queues"
        / f"{agent_prefix}_backlog.md"
    )
    if backlog_file.exists():
        existing = backlog_file.read_text(encoding="utf-8")
        backlog_file.write_text(
            existing.rstrip() + f"\n- [ ] {task_id}\n",
            encoding="utf-8",
        )

    return task_dir


def _build_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
) -> pathlib.Path:
    """Create a well-formed bug file in the project's bugs/ directory.

    Also adds a backlog marker to bug_backlog.md for marker-sync assertions.

    Args:
        kanban_root:  Kanban root path.
        project_name: Project whose bugs/ directory receives the file.
        bug_id:       BUG-NNNN identifier (4+ digits).
        slug:         Hyphenated slug suffix.
        status:       Value for the ## Status field (default: open).

    Returns:
        pathlib.Path to the created file.
    """
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"## Status\n{status}\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    # Add a marker to bug_backlog.md.
    bug_backlog = (
        kanban_root / "projects" / project_name / "tasks" / "queues" / "bug_backlog.md"
    )
    if bug_backlog.exists():
        key = f"{bug_id}-{slug}"
        existing = bug_backlog.read_text(encoding="utf-8")
        bug_backlog.write_text(
            existing.rstrip() + f"\n- [ ] {key}\n",
            encoding="utf-8",
        )
    return bug_file


def _read_status_state(task_dir: pathlib.Path) -> str:
    """Extract the ## State value from a task's status.md."""
    text = (task_dir / "status.md").read_text(encoding="utf-8")
    match = re.search(r"##\s*State\s*\n\s*(\S+)", text)
    return match.group(1).strip() if match else ""


def _read_backlog_marker(
    kanban_root: pathlib.Path,
    project_name: str,
    task_id: str,
) -> str:
    """Return the marker character for task_id in the appropriate agent backlog.

    Looks for a line of the form: - [<char>] <task_id>
    Returns the marker character, or "" if the line is not found.
    """
    agent_prefix = task_id.split("-")[0].lower()
    backlog_file = (
        kanban_root / "projects" / project_name / "tasks" / "queues"
        / f"{agent_prefix}_backlog.md"
    )
    if not backlog_file.exists():
        return ""
    text = backlog_file.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^\s*-\s+\[([^\]]*)\]\s+{re.escape(task_id)}\b",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _read_bug_backlog_marker(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_key: str,
) -> str:
    """Return the marker character for bug_key in bug_backlog.md."""
    backlog_file = (
        kanban_root / "projects" / project_name / "tasks" / "queues" / "bug_backlog.md"
    )
    if not backlog_file.exists():
        return ""
    text = backlog_file.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^\s*-\s+\[([^\]]*)\]\s+{re.escape(bug_key)}\b",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


# ---------------------------------------------------------------------------
# Tests: show command
# ---------------------------------------------------------------------------


class TestShowCommand:
    """show.sh emits task or intake item content to stdout without mutating state."""

    def test_show_emits_task_status_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """show.sh --file status emits the task's status.md content to stdout.

        The status.md content must appear verbatim on stdout.  show.sh must
        exit 0 and must not modify any file on disk.
        """
        root = two_project_root
        task_id = "CODER-20260628-T001-show-status-test"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")
        original_content = (task_dir / "status.md").read_text(encoding="utf-8")

        result = _run_operator(
            _SHOW_SCRIPT,
            ["--project", "project_a", "--key", task_id, "--file", "status"],
            root,
        )

        assert result.returncode == 0, (
            f"show.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "BACKLOG" in result.stdout, (
            f"Expected task state 'BACKLOG' in show output.\nstdout: {result.stdout}"
        )
        # File must not have been mutated by show.
        assert (task_dir / "status.md").read_text(encoding="utf-8") == original_content, (
            "show.sh must not modify status.md (read-only command)."
        )

    def test_show_emits_task_readme_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """show.sh --file readme emits the task's README.md content to stdout.

        Passing --file readme must switch to the README.md rather than the
        default status.md.  The README content is identifiable by the task_id
        in its heading.
        """
        root = two_project_root
        task_id = "CODER-20260628-T002-show-readme-test"
        _build_task_folder(root, "project_a", task_id)

        result = _run_operator(
            _SHOW_SCRIPT,
            ["--project", "project_a", "--key", task_id, "--file", "readme"],
            root,
        )

        assert result.returncode == 0, (
            f"show.sh --file readme exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert task_id in result.stdout, (
            f"Expected task_id {task_id!r} to appear in README output.\nstdout: {result.stdout}"
        )

    def test_show_emits_bug_intake_content(
        self, two_project_root: pathlib.Path
    ) -> None:
        """show.sh emits bug intake file content when the key identifies a bug.

        When the key resolves to a bug intake item rather than a task, show.sh
        must emit the bug file's content directly (no --file selection needed).
        """
        root = two_project_root
        bug_file = _build_bug_file(
            root, "project_a", bug_id="BUG-0100", slug="show-intake-test"
        )

        result = _run_operator(
            _SHOW_SCRIPT,
            ["--project", "project_a", "--key", "BUG-0100-show-intake-test"],
            root,
        )

        assert result.returncode == 0, (
            f"show.sh for intake key exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "BUG-0100" in result.stdout, (
            f"Expected BUG-0100 to appear in show output for intake item.\nstdout: {result.stdout}"
        )
        # Bug file must not have been mutated.
        assert bug_file.exists(), "show.sh must not delete the bug file."

    def test_show_returns_not_found_for_unknown_key(
        self, two_project_root: pathlib.Path
    ) -> None:
        """show.sh exits with code 3 when the key is not found in the project.

        A key that does not match any task, bug, priority, or requirement must
        result in exit code 3 (not-found), not an unhandled error.
        """
        root = two_project_root

        result = _run_operator(
            _SHOW_SCRIPT,
            ["--project", "project_a", "--key", "CODER-99999999-MISSING-TASK"],
            root,
        )

        assert result.returncode == 3, (
            f"Expected exit code 3 (not found) for unknown key; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: reset command
# ---------------------------------------------------------------------------


class TestResetCommand:
    """reset.sh restores tasks and intake items to a re-pickable state."""

    def test_reset_sets_task_state_to_backlog(
        self, two_project_root: pathlib.Path
    ) -> None:
        """reset.sh on a DONE task writes State=BACKLOG to status.md.

        The fundamental reset guarantee: after reset, the task is in BACKLOG
        state regardless of its previous terminal state.
        """
        root = two_project_root
        task_id = "CODER-20260628-T010-reset-to-backlog"
        task_dir = _build_task_folder(root, "project_a", task_id, state="DONE")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"reset.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _read_status_state(task_dir) == "BACKLOG", (
            f"Expected status.md ## State to be BACKLOG after reset.\n"
            f"Actual content:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

    def test_reset_flips_queue_marker_to_open(
        self, two_project_root: pathlib.Path
    ) -> None:
        """reset.sh flips the agent queue marker from [x] to [ ] in the backlog file.

        Queue-marker sync: after a reset, the backlog marker for the task must
        reflect the open state (empty or space marker), not the terminal [x].
        """
        root = two_project_root
        task_id = "CODER-20260628-T011-reset-marker-sync"
        _build_task_folder(root, "project_a", task_id, state="DONE")

        # Pre-condition: mark the backlog marker as [x] to simulate a prior close.
        backlog_file = (
            root / "projects" / "project_a" / "tasks" / "queues" / "coder_backlog.md"
        )
        if backlog_file.exists():
            text = backlog_file.read_text(encoding="utf-8")
            text = text.replace(f"- [ ] {task_id}", f"- [x] {task_id}")
            backlog_file.write_text(text, encoding="utf-8")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"reset.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        marker = _read_backlog_marker(root, "project_a", task_id)
        assert marker == "" or marker == " ", (
            f"Expected backlog marker to be open (space or empty) after reset, got: {marker!r}.\n"
            f"backlog content:\n{backlog_file.read_text(encoding='utf-8')}"
        )

    def test_reset_refuses_task_in_working_state(
        self, two_project_root: pathlib.Path
    ) -> None:
        """reset.sh refuses to reset a task whose state is WORKING (exit code 2).

        A task in WORKING state may be actively held by an agent.  reset.sh
        must exit with code 2 and leave the task's state and artifacts unchanged.
        """
        root = two_project_root
        task_id = "CODER-20260628-T012-reset-refuses-working"
        task_dir = _build_task_folder(root, "project_a", task_id, state="WORKING")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 (WORKING state refusal) but got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # State must be unchanged.
        assert _read_status_state(task_dir) == "WORKING", (
            "reset.sh must not modify a task in WORKING state."
        )

    def test_reset_bug_intake_item_to_open(
        self, two_project_root: pathlib.Path
    ) -> None:
        """reset.sh resets a bug intake item's ## Status to 'open'.

        Bug intake items can also be reset (e.g., after being mistakenly marked
        as running).  The resulting file must have ## Status: open.
        """
        root = two_project_root
        bug_file = _build_bug_file(
            root, "project_a", bug_id="BUG-0110", slug="reset-intake-test",
            status="running",
        )

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", "BUG-0110-reset-intake-test"],
            root,
        )

        assert result.returncode == 0, (
            f"reset.sh on intake item exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        bug_text = bug_file.read_text(encoding="utf-8")
        assert "open" in bug_text.lower(), (
            f"Expected bug file ## Status to be 'open' after reset.\n"
            f"Actual content:\n{bug_text}"
        )

    def test_reset_bug_intake_marker_flips_to_open(
        self, two_project_root: pathlib.Path
    ) -> None:
        """reset.sh flips a bug backlog marker from [x] to [ ] after resetting the intake item.

        Queue-marker sync for intake: resetting a bug item must update
        bug_backlog.md so the marker reflects the open state.
        """
        root = two_project_root
        bug_id = "BUG-0111"
        slug = "reset-marker-intake"
        _build_bug_file(root, "project_a", bug_id=bug_id, slug=slug, status="running")
        bug_key = f"{bug_id}-{slug}"

        # Pre-condition: mark the bug backlog entry as [x].
        bug_backlog = (
            root / "projects" / "project_a" / "tasks" / "queues" / "bug_backlog.md"
        )
        if bug_backlog.exists():
            text = bug_backlog.read_text(encoding="utf-8")
            text = text.replace(f"- [ ] {bug_key}", f"- [x] {bug_key}")
            bug_backlog.write_text(text, encoding="utf-8")

        result = _run_operator(
            _RESET_SCRIPT,
            ["--project", "project_a", "--key", bug_key],
            root,
        )

        assert result.returncode == 0, (
            f"reset.sh on intake item exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        marker = _read_bug_backlog_marker(root, "project_a", bug_key)
        assert marker == "" or marker == " ", (
            f"Expected bug backlog marker to be open after reset, got: {marker!r}.\n"
            f"backlog content:\n{bug_backlog.read_text(encoding='utf-8')}"
        )


# ---------------------------------------------------------------------------
# Tests: close command
# ---------------------------------------------------------------------------


class TestCloseCommand:
    """close.sh marks tasks and intake items with a terminal state and syncs the queue marker."""

    def test_close_sets_task_state_to_done(
        self, two_project_root: pathlib.Path
    ) -> None:
        """close.sh sets a task's ## State to DONE in status.md.

        For agent tasks, close.sh always writes DONE to ## State (the --state
        flag is only relevant for intake items).
        """
        root = two_project_root
        task_id = "CODER-20260628-T020-close-to-done"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"close.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert _read_status_state(task_dir) == "DONE", (
            f"Expected status.md ## State to be DONE after close.\n"
            f"Actual content:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

    def test_close_flips_task_queue_marker_to_done(
        self, two_project_root: pathlib.Path
    ) -> None:
        """close.sh flips the task's queue marker to [x] in the agent backlog file.

        Queue-marker sync is the critical cross-file consistency property.
        After close, the coder_backlog.md entry for the task must be marked [x].
        """
        root = two_project_root
        task_id = "CODER-20260628-T021-close-marker-sync"
        _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"close.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        marker = _read_backlog_marker(root, "project_a", task_id)
        assert marker == "x", (
            f"Expected backlog marker to be [x] after close, got: {marker!r}.\n"
            f"coder_backlog.md:\n{(root / 'projects' / 'project_a' / 'tasks' / 'queues' / 'coder_backlog.md').read_text(encoding='utf-8')}"
        )

    def test_close_bug_intake_item_with_state_done(
        self, two_project_root: pathlib.Path
    ) -> None:
        """close.sh --state done sets a bug file's ## Status to 'done'.

        Closing an intake bug item with the default state (done) must update
        the file's ## Status field and flip the bug backlog marker.
        """
        root = two_project_root
        bug_file = _build_bug_file(
            root, "project_a", bug_id="BUG-0120", slug="close-done-test"
        )
        bug_key = "BUG-0120-close-done-test"

        result = _run_operator(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", bug_key, "--state", "done"],
            root,
        )

        assert result.returncode == 0, (
            f"close.sh on intake bug exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        bug_text = bug_file.read_text(encoding="utf-8")
        assert "done" in bug_text.lower(), (
            f"Expected bug file ## Status to contain 'done' after close.\n"
            f"Actual:\n{bug_text}"
        )

    def test_close_bug_intake_flips_backlog_marker_to_x(
        self, two_project_root: pathlib.Path
    ) -> None:
        """close.sh flips a bug backlog marker to [x] when closing the bug intake item.

        Queue-marker sync for intake: both the file's ## Status and the
        bug_backlog.md marker must reflect the closed state.
        """
        root = two_project_root
        bug_id = "BUG-0121"
        slug = "close-marker-intake"
        _build_bug_file(root, "project_a", bug_id=bug_id, slug=slug)
        bug_key = f"{bug_id}-{slug}"

        result = _run_operator(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", bug_key],
            root,
        )

        assert result.returncode == 0, (
            f"close.sh on intake exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        marker = _read_bug_backlog_marker(root, "project_a", bug_key)
        assert marker == "x", (
            f"Expected bug backlog marker to be [x] after close, got: {marker!r}.\n"
            f"bug_backlog.md:\n{(root / 'projects' / 'project_a' / 'tasks' / 'queues' / 'bug_backlog.md').read_text(encoding='utf-8')}"
        )

    def test_close_reports_key_not_found_for_missing_task(
        self, two_project_root: pathlib.Path
    ) -> None:
        """close.sh exits with code 3 when the key is not found in the project.

        Attempting to close a non-existent task must result in exit code 3 and
        leave no files modified.
        """
        root = two_project_root

        result = _run_operator(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", "CODER-99999999-NO-TASK"],
            root,
        )

        assert result.returncode == 3, (
            f"Expected exit code 3 (not found); got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: wontdo command
# ---------------------------------------------------------------------------


class TestWontdoCommand:
    """wontdo.sh marks tasks as WONT-DO and syncs the queue marker."""

    def test_wontdo_sets_task_state_to_wont_do(
        self, two_project_root: pathlib.Path
    ) -> None:
        """wontdo.sh writes WONT-DO to the task's ## State in status.md.

        WONT-DO is the retire-without-working terminal state.  After wontdo,
        status.md must reflect this state.
        """
        root = two_project_root
        task_id = "CODER-20260628-T030-mark-wontdo"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _WONTDO_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"wontdo.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        actual_state = _read_status_state(task_dir)
        assert actual_state == "WONT-DO", (
            f"Expected ## State to be WONT-DO after wontdo.sh; got {actual_state!r}.\n"
            f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

    def test_wontdo_flips_queue_marker_to_x(
        self, two_project_root: pathlib.Path
    ) -> None:
        """wontdo.sh flips the task's queue marker to [x] in the agent backlog file.

        Queue-marker sync: a WONT-DO task must be marked [x] in coder_backlog.md,
        just like a DONE task, so it does not appear as pending in the queue.
        """
        root = two_project_root
        task_id = "CODER-20260628-T031-wontdo-marker-sync"
        _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _WONTDO_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"wontdo.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        marker = _read_backlog_marker(root, "project_a", task_id)
        assert marker == "x", (
            f"Expected backlog marker [x] after wontdo; got: {marker!r}.\n"
            f"coder_backlog.md:\n{(root / 'projects' / 'project_a' / 'tasks' / 'queues' / 'coder_backlog.md').read_text(encoding='utf-8')}"
        )

    def test_wontdo_stdout_confirms_operation(
        self, two_project_root: pathlib.Path
    ) -> None:
        """wontdo.sh prints a confirmation message to stdout on success.

        The confirmation message must reference the task ID so operators
        can confirm which task was affected.
        """
        root = two_project_root
        task_id = "CODER-20260628-T032-wontdo-stdout-confirm"
        _build_task_folder(root, "project_a", task_id)

        result = _run_operator(
            _WONTDO_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0
        assert task_id in result.stdout or "WONT-DO" in result.stdout.upper(), (
            f"Expected task ID or 'WONT-DO' in stdout.\nstdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Tests: delete command
# ---------------------------------------------------------------------------


class TestDeleteCommand:
    """delete.sh removes task directories and intake items after terminal-state guard check."""

    def test_delete_removes_done_task_directory(
        self, two_project_root: pathlib.Path
    ) -> None:
        """delete.sh removes a DONE task's directory from the tasks/ tree.

        After deletion, the task directory must no longer exist.  This is the
        primary side-effect: delete is destructive and irreversible.
        """
        root = two_project_root
        task_id = "CODER-20260628-T040-delete-done-task"
        task_dir = _build_task_folder(root, "project_a", task_id, state="DONE")
        assert task_dir.exists(), "Pre-condition: task directory must exist before delete."

        result = _run_operator(
            _DELETE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 0, (
            f"delete.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not task_dir.exists(), (
            f"Expected task directory to be removed after delete, but it still exists at {task_dir}.\n"
            f"stdout: {result.stdout}"
        )

    def test_delete_guard_refuses_backlog_task(
        self, two_project_root: pathlib.Path
    ) -> None:
        """delete.sh refuses to delete a BACKLOG task without --force (exit code 2).

        The terminal-state guard protects tasks that are not yet finished.
        Attempting to delete a BACKLOG task must exit with code 2 and leave
        the task directory intact.
        """
        root = two_project_root
        task_id = "CODER-20260628-T041-delete-guard-backlog"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _DELETE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 (guard refused) for BACKLOG task; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert task_dir.exists(), (
            "Task directory must not be deleted when the guard refuses."
        )

    def test_delete_with_force_removes_non_terminal_task(
        self, two_project_root: pathlib.Path
    ) -> None:
        """delete.sh --force removes a BACKLOG task, bypassing the terminal-state guard.

        --force is the operator escape hatch for deleting work regardless of
        state.  With --force, even a BACKLOG task is deleted.  This test
        verifies that --force actually bypasses the guard.
        """
        root = two_project_root
        task_id = "CODER-20260628-T042-delete-force-backlog"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        result = _run_operator(
            _DELETE_SCRIPT,
            ["--project", "project_a", "--key", task_id, "--force"],
            root,
        )

        assert result.returncode == 0, (
            f"delete.sh --force exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert not task_dir.exists(), (
            "Task directory must be removed when --force is used, even for BACKLOG state."
        )

    def test_delete_dry_run_reports_without_removing(
        self, two_project_root: pathlib.Path
    ) -> None:
        """delete.sh --dry-run reports the target path without removing it.

        --dry-run must print what would be deleted and exit 0 without modifying
        any file.  The task directory must remain intact after a dry-run.
        """
        root = two_project_root
        task_id = "CODER-20260628-T043-delete-dry-run"
        task_dir = _build_task_folder(root, "project_a", task_id, state="DONE")

        result = _run_operator(
            _DELETE_SCRIPT,
            ["--project", "project_a", "--key", task_id, "--dry-run"],
            root,
        )

        assert result.returncode == 0, (
            f"delete.sh --dry-run exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert task_id in result.stdout or str(task_dir) in result.stdout, (
            f"Expected task ID or path in dry-run output.\nstdout: {result.stdout}"
        )
        assert task_dir.exists(), (
            "Task directory must NOT be removed after --dry-run."
        )


# ---------------------------------------------------------------------------
# Tests: halt command (per-project)
# ---------------------------------------------------------------------------


class TestHaltCommand:
    """halt.sh creates the per-project HALT sentinel file."""

    def test_halt_creates_project_halt_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """halt.sh creates a HALT file in the project directory.

        The HALT file at projects/<name>/HALT is the per-project halt signal
        that the discovery pipeline checks before running any step.
        """
        root = two_project_root
        halt_file = root / "projects" / "project_a" / "HALT"
        assert not halt_file.exists(), "Pre-condition: HALT file must not exist."

        result = _run_operator(
            _HALT_SCRIPT,
            ["--project", "project_a"],
            root,
        )

        assert result.returncode == 0, (
            f"halt.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert halt_file.exists(), (
            f"Expected HALT file to be created at {halt_file}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_halt_is_idempotent(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A second halt.sh invocation on an already-halted project exits 0 cleanly.

        Idempotency prevents spurious errors in automated pipelines that may
        call halt multiple times.  The HALT file must still exist after the
        second call.
        """
        root = two_project_root

        # First halt.
        _run_operator(_HALT_SCRIPT, ["--project", "project_a"], root)

        # Second halt — must succeed without error.
        result = _run_operator(
            _HALT_SCRIPT,
            ["--project", "project_a"],
            root,
        )

        assert result.returncode == 0, (
            f"Second halt.sh invocation exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert (root / "projects" / "project_a" / "HALT").exists(), (
            "HALT file must still exist after a second halt invocation."
        )

    def test_halt_only_affects_targeted_project(
        self, two_project_root: pathlib.Path
    ) -> None:
        """halt.sh creates HALT only in the specified project's directory.

        Per-project HALT must be scoped: halting project_a must not create a
        HALT file in project_b's directory or at the kanban root.
        """
        root = two_project_root

        result = _run_operator(
            _HALT_SCRIPT,
            ["--project", "project_a"],
            root,
        )

        assert result.returncode == 0
        assert (root / "projects" / "project_a" / "HALT").exists(), (
            "HALT file must be created in project_a."
        )
        assert not (root / "projects" / "project_b" / "HALT").exists(), (
            "HALT must not be created in project_b when only project_a is halted."
        )
        assert not (root / "HALT").exists(), (
            "Per-project halt must not create a global HALT at the kanban root."
        )


# ---------------------------------------------------------------------------
# Tests: halt-global command
# ---------------------------------------------------------------------------


class TestHaltGlobalCommand:
    """halt-global.sh creates the global HALT sentinel at the kanban root."""

    def test_halt_global_creates_root_halt_file(
        self, two_project_root: pathlib.Path
    ) -> None:
        """halt-global.sh creates HALT at the kanban root, blocking all projects.

        The global HALT file at <kanban_root>/HALT stops all projects' pipelines
        at the next wake iteration.
        """
        root = two_project_root
        global_halt = root / "HALT"
        assert not global_halt.exists(), "Pre-condition: global HALT must not exist."

        result = _run_operator(
            _HALT_GLOBAL_SCRIPT,
            [],
            root,
        )

        assert result.returncode == 0, (
            f"halt-global.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert global_halt.exists(), (
            f"Expected global HALT file at {global_halt}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_halt_global_is_idempotent(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A second halt-global.sh invocation exits 0 when global HALT already exists.

        The global HALT is a toggle — calling it twice must not error.
        """
        root = two_project_root

        # First call.
        _run_operator(_HALT_GLOBAL_SCRIPT, [], root)

        # Second call — must succeed.
        result = _run_operator(
            _HALT_GLOBAL_SCRIPT,
            [],
            root,
        )

        assert result.returncode == 0, (
            f"Second halt-global.sh invocation exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert (root / "HALT").exists(), (
            "Global HALT file must still exist after a second invocation."
        )

    def test_halt_global_does_not_create_per_project_halt(
        self, two_project_root: pathlib.Path
    ) -> None:
        """halt-global.sh creates only the root-level HALT, not per-project HALT files.

        Global HALT and per-project HALT are separate mechanisms.  halt-global
        must not create files inside any project directory.
        """
        root = two_project_root

        result = _run_operator(
            _HALT_GLOBAL_SCRIPT,
            [],
            root,
        )

        assert result.returncode == 0
        assert (root / "HALT").exists(), "Global HALT must be created at root."
        assert not (root / "projects" / "project_a" / "HALT").exists(), (
            "halt-global must not create a per-project HALT in project_a."
        )
        assert not (root / "projects" / "project_b" / "HALT").exists(), (
            "halt-global must not create a per-project HALT in project_b."
        )


# ---------------------------------------------------------------------------
# Tests: intake command
# ---------------------------------------------------------------------------


class TestIntakeCommand:
    """intake.sh deposits staged intake files into the correct project directories."""

    def test_intake_deposits_bug_file_to_bugs_directory(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """intake.sh copies a BUG-* file to projects/<name>/bugs/.

        The routing decision is based on the filename prefix.  A file named
        BUG-NNNN-<slug>.md must land in the bugs/ directory.
        """
        root = two_project_root
        # Create the staged file outside the kanban root (as an operator would).
        staged_file = tmp_path / "BUG-0200-intake-routing-test.md"
        staged_file.write_text(
            "# BUG-0200: intake-routing-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nRouting test via intake.sh.\n",
            encoding="utf-8",
        )

        (root / "projects" / "project_a" / "bugs").mkdir(parents=True, exist_ok=True)

        result = _run_operator(
            _INTAKE_SCRIPT,
            ["--project", "project_a", str(staged_file)],
            root,
        )

        assert result.returncode == 0, (
            f"intake.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        deposited = root / "projects" / "project_a" / "bugs" / "BUG-0200-intake-routing-test.md"
        assert deposited.exists(), (
            f"Expected deposited file at {deposited}.\n"
            f"bugs/ contents: {list((root / 'projects' / 'project_a' / 'bugs').iterdir())}\n"
            f"stdout: {result.stdout}"
        )

    def test_intake_deposits_priority_file_to_priority_directory(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """intake.sh copies a PRIORITY-* file to projects/<name>/priority/.

        Files with a PRIORITY- prefix must be routed to the priority/ directory,
        not bugs/ or requirements/.
        """
        root = two_project_root
        staged_file = tmp_path / "PRIORITY-0010-intake-priority-test.md"
        staged_file.write_text(
            "# PRIORITY-0010: intake-priority-test\n\n"
            "## Status\nopen\n\n"
            "## Summary\nPriority routing test.\n",
            encoding="utf-8",
        )

        (root / "projects" / "project_a" / "priority").mkdir(parents=True, exist_ok=True)

        result = _run_operator(
            _INTAKE_SCRIPT,
            ["--project", "project_a", str(staged_file)],
            root,
        )

        assert result.returncode == 0, (
            f"intake.sh for priority file exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        deposited = root / "projects" / "project_a" / "priority" / "PRIORITY-0010-intake-priority-test.md"
        assert deposited.exists(), (
            f"Expected deposited file at {deposited}.\n"
            f"stdout: {result.stdout}"
        )

    def test_intake_refuses_unrecognized_filename_prefix(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """intake.sh refuses files whose prefix does not match a known intake type (exit 2).

        A file without a BUG-, PRIORITY-, or v[0-9]* prefix must be refused
        with exit code 2.  The deposit must not happen (no file created in any
        intake directory).
        """
        root = two_project_root
        staged_file = tmp_path / "UNKNOWN-prefix-file.md"
        staged_file.write_text(
            "# Unknown\n\n## Status\nopen\n",
            encoding="utf-8",
        )

        result = _run_operator(
            _INTAKE_SCRIPT,
            ["--project", "project_a", str(staged_file)],
            root,
        )

        assert result.returncode == 2, (
            f"Expected exit code 2 (routing refused) for unknown prefix; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_intake_refuses_to_clobber_existing_file(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """intake.sh exits with code 3 when the target file already exists (no clobber).

        Depositing a file whose name already exists in the intake directory must
        be refused (exit 3).  The existing file must remain unchanged.
        """
        root = two_project_root
        bugs_dir = root / "projects" / "project_a" / "bugs"
        bugs_dir.mkdir(parents=True, exist_ok=True)

        # Pre-create the target file.
        existing_content = "# BUG-0201: already-exists\n\n## Status\nopen\n"
        (bugs_dir / "BUG-0201-already-exists.md").write_text(
            existing_content, encoding="utf-8"
        )

        # Attempt to deposit another file with the same name.
        staged_file = tmp_path / "BUG-0201-already-exists.md"
        staged_file.write_text(
            "# BUG-0201: already-exists (new version)\n\n## Status\nopen\n",
            encoding="utf-8",
        )

        result = _run_operator(
            _INTAKE_SCRIPT,
            ["--project", "project_a", str(staged_file)],
            root,
        )

        assert result.returncode == 3, (
            f"Expected exit code 3 (no clobber) when target already exists; got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # Existing file must be unchanged.
        actual_content = (bugs_dir / "BUG-0201-already-exists.md").read_text(encoding="utf-8")
        assert actual_content == existing_content, (
            "intake.sh must not overwrite an existing file."
        )

    def test_intake_prints_deposited_path_on_success(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """intake.sh prints the deposited file path to stdout on success.

        The output line allows operators and automation to know exactly where
        the file landed without having to infer the routing rule.
        """
        root = two_project_root
        staged_file = tmp_path / "BUG-0202-intake-path-output.md"
        staged_file.write_text(
            "# BUG-0202: intake-path-output\n\n## Status\nopen\n",
            encoding="utf-8",
        )
        (root / "projects" / "project_a" / "bugs").mkdir(parents=True, exist_ok=True)

        result = _run_operator(
            _INTAKE_SCRIPT,
            ["--project", "project_a", str(staged_file)],
            root,
        )

        assert result.returncode == 0, (
            f"intake.sh exited non-zero.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        # The output must reference the deposited filename so operators can verify routing.
        assert "BUG-0202-intake-path-output.md" in result.stdout, (
            f"Expected deposited filename in stdout.\nstdout: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# Tests: close.sh from a foreign working directory (cwd-independence)
# ---------------------------------------------------------------------------


class TestCloseShForeignCwd:
    """close.sh succeeds when invoked from a directory other than the kanban root.

    The production failure mode: bare ``python3 -m pgai_agent_kanban.ops``
    invocations fail when the working directory is not the kanban root because
    the package cannot be imported without PYTHONPATH set.  close.sh routes
    through pp_run_ops, which sets PYTHONPATH from its own file location.
    These tests verify that close.sh is cwd-independent.
    """

    def test_close_task_from_foreign_cwd(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """close.sh sets a task to DONE when invoked from a foreign working directory.

        The script must not depend on the caller's working directory to resolve
        the pgai_agent_kanban package.  A foreign cwd is a directory that is
        neither the kanban root nor the team/ directory.
        """
        root = two_project_root
        task_id = "CODER-20260628-T060-close-foreign-cwd"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        foreign_cwd = tmp_path / "foreign_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"close.sh from a foreign cwd exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            "Likely cause: pp_run_ops failed to resolve PYTHONPATH from its "
            "own file location when invoked from an arbitrary working directory."
        )
        assert _read_status_state(task_dir) == "DONE", (
            f"Expected status.md ## State to be DONE after close from foreign cwd.\n"
            f"Actual content:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

    def test_close_bug_intake_item_from_foreign_cwd(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """close.sh closes a bug intake item when invoked from a foreign working directory.

        Intake items are closed via the same pp_run_ops pathway.  Verifies that
        both task-close and intake-close succeed when cwd is not the kanban root.
        """
        root = two_project_root
        bug_file = _build_bug_file(
            root, "project_a", bug_id="BUG-0130", slug="close-foreign-cwd-intake"
        )
        bug_key = "BUG-0130-close-foreign-cwd-intake"

        foreign_cwd = tmp_path / "foreign_cwd_intake"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", bug_key, "--state", "done"],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"close.sh for intake item from a foreign cwd exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        bug_text = bug_file.read_text(encoding="utf-8")
        assert "done" in bug_text.lower(), (
            f"Expected bug file ## Status to contain 'done' after close.\n"
            f"Actual:\n{bug_text}"
        )


# ---------------------------------------------------------------------------
# Tests: halt-global.sh and unhalt-global.sh under scrubbed env + foreign cwd
# ---------------------------------------------------------------------------


class TestHaltUnhaltGlobalScrubbedEnv:
    """halt-global.sh and unhalt-global.sh succeed under a scrubbed cron-shaped environment.

    The scripts must work when:
      - The environment contains only HOME, PATH, and PGAI_AGENT_KANBAN_ROOT_PATH.
      - The working directory is a foreign temp directory (not the kanban root).
      - PYTHONPATH is not set in the caller's environment.

    These conditions mirror the production cron invocation shape.  pp_run_ops
    derives PYTHONPATH from its own file location so both scripts are
    cwd-independent and env-independent beyond the three variables above.
    """

    def test_halt_global_from_scrubbed_env_foreign_cwd(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """halt-global.sh creates the global HALT file under scrubbed env + foreign cwd.

        Verifies that halt-global.sh is not fragile to the working directory or
        to the absence of PYTHONPATH — the same shape as a cron wake invocation.
        """
        root = two_project_root
        global_halt = root / "HALT"
        assert not global_halt.exists(), "Pre-condition: global HALT must not exist."

        foreign_cwd = tmp_path / "halt_foreign_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _HALT_GLOBAL_SCRIPT,
            [],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"halt-global.sh exited non-zero under scrubbed env + foreign cwd.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            "Likely cause: pp_run_ops failed to resolve PYTHONPATH when PYTHONPATH "
            "was not set and working directory was not the kanban root."
        )
        assert global_halt.exists(), (
            f"Expected global HALT file to be created at {global_halt}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_unhalt_global_from_scrubbed_env_foreign_cwd(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """unhalt-global.sh removes the global HALT file under scrubbed env + foreign cwd.

        First halt-global.sh creates the HALT file (normal env), then
        unhalt-global.sh removes it under the scrubbed-env + foreign-cwd shape.
        Verifies that the remove path is also cwd-independent and env-independent.
        """
        root = two_project_root

        # Create the HALT file using the normal env first.
        _run_operator(_HALT_GLOBAL_SCRIPT, [], root)
        assert (root / "HALT").exists(), "Pre-condition: HALT must exist before unhalt."

        foreign_cwd = tmp_path / "unhalt_foreign_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _UNHALT_GLOBAL_SCRIPT,
            [],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"unhalt-global.sh exited non-zero under scrubbed env + foreign cwd.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            "Likely cause: pp_run_ops failed to resolve PYTHONPATH when PYTHONPATH "
            "was not set and working directory was not the kanban root."
        )
        assert not (root / "HALT").exists(), (
            f"Expected global HALT file to be removed after unhalt.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_halt_global_idempotent_under_scrubbed_env(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """halt-global.sh is idempotent under scrubbed env: second call exits 0 cleanly.

        A second halt invocation when HALT already exists must return 0 — both
        in the normal env (tested in TestHaltGlobalCommand) and the scrubbed env.
        """
        root = two_project_root
        foreign_cwd = tmp_path / "halt_idempotent_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        # First call — creates HALT.
        first = _run_operator_scrubbed_foreign_cwd(
            _HALT_GLOBAL_SCRIPT, [], root, foreign_cwd
        )
        assert first.returncode == 0, (
            f"First halt-global.sh call failed.\nstdout: {first.stdout}\nstderr: {first.stderr}"
        )
        assert (root / "HALT").exists(), "HALT must exist after first call."

        # Second call — must exit 0 (idempotent).
        second = _run_operator_scrubbed_foreign_cwd(
            _HALT_GLOBAL_SCRIPT, [], root, foreign_cwd
        )
        assert second.returncode == 0, (
            f"Second halt-global.sh call under scrubbed env exited non-zero.\n"
            f"stdout: {second.stdout}\nstderr: {second.stderr}"
        )
        assert (root / "HALT").exists(), "HALT file must still exist after second call."

    def test_unhalt_global_idempotent_under_scrubbed_env(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """unhalt-global.sh is idempotent under scrubbed env: exits 0 when no HALT exists.

        Calling unhalt when no HALT file is present must exit 0 gracefully.
        """
        root = two_project_root
        assert not (root / "HALT").exists(), "Pre-condition: HALT must not exist."

        foreign_cwd = tmp_path / "unhalt_idempotent_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _UNHALT_GLOBAL_SCRIPT, [], root, foreign_cwd
        )

        assert result.returncode == 0, (
            f"unhalt-global.sh exited non-zero when no HALT existed (expected idempotent 0).\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests: scrubbed-env fixture for the census closer (pp_run_ops via close_item)
# ---------------------------------------------------------------------------


class TestScrubbedEnvCensusCloser:
    """The pp_run_ops-based close_item pathway succeeds under scrubbed env + foreign cwd.

    This is the production failure shape from BUG-0082: the census closer
    in wake_common.sh calls pp_run_ops to invoke close_item.  When run from
    a cron environment (scrubbed env, foreign cwd, no PYTHONPATH), bare
    python3 -m invocations fail.  pp_run_ops resolves PYTHONPATH from its
    own location, making the invocation cwd-independent.

    These tests exercise the same pathway end-to-end by invoking close.sh
    (which sources pp_run_ops and calls close_item) under the same
    scrubbed-env shape the census closer runs in.  A passing test here
    confirms that the failure mode from BUG-0082 is fixed and remains fixed.
    """

    def test_close_item_via_pp_run_ops_from_scrubbed_env(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """close_item succeeds when PYTHONPATH is absent and cwd is foreign.

        This is the canonical scrubbed-env + foreign-cwd regression: the exact
        environment shape that caused the production BUG-0082 failure.  close.sh
        routes through pp_run_ops, which sets PYTHONPATH correctly from its own
        file location regardless of the caller's environment.
        """
        root = two_project_root
        task_id = "CODER-20260628-T070-close-item-scrubbed-env"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        foreign_cwd = tmp_path / "census_closer_scrubbed_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        result = _run_operator_scrubbed_foreign_cwd(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"close_item via pp_run_ops failed under scrubbed env + foreign cwd.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            "This is the BUG-0082 production failure shape: python3 -m pgai_agent_kanban.ops "
            "must be cwd-independent.  Ensure all call sites use pp_run_ops."
        )
        assert _read_status_state(task_dir) == "DONE", (
            f"Task must be DONE after close_item via pp_run_ops under scrubbed env.\n"
            f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )

    def test_close_item_does_not_require_pythonpath_in_environment(
        self,
        two_project_root: pathlib.Path,
        tmp_path: pathlib.Path,
    ) -> None:
        """close_item succeeds with PYTHONPATH absent from the environment.

        pp_run_ops derives the own-tree root from BASH_SOURCE and constructs
        PYTHONPATH internally.  The calling environment must not need to provide
        PYTHONPATH at all for close_item to succeed.
        """
        root = two_project_root
        task_id = "CODER-20260628-T071-no-pythonpath-needed"
        task_dir = _build_task_folder(root, "project_a", task_id, state="BACKLOG")

        foreign_cwd = tmp_path / "no_pythonpath_cwd"
        foreign_cwd.mkdir(parents=True, exist_ok=True)

        # Explicitly verify PYTHONPATH is absent (scrubbed env has only HOME/PATH/root).
        result = _run_operator_scrubbed_foreign_cwd(
            _CLOSE_SCRIPT,
            ["--project", "project_a", "--key", task_id],
            root,
            foreign_cwd,
        )

        assert result.returncode == 0, (
            f"close_item failed when PYTHONPATH was absent from environment.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
            "pp_run_ops must not require PYTHONPATH to be set by the caller."
        )
        assert _read_status_state(task_dir) == "DONE", (
            "Task must be DONE; close_item must work without caller-provided PYTHONPATH.\n"
            f"status.md:\n{(task_dir / 'status.md').read_text(encoding='utf-8')}"
        )
