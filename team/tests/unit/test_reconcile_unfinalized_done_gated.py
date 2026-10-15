"""
test_reconcile_unfinalized_done_gated.py
=========================================
A DONE the wake never finalized goes through the CODER/WRITER completion
gate before either reconciliation sweep renders it [x].

The wake's finalize (wd_finalize_task) runs coder_writer_completion_gate on
every CODER/WRITER DONE and then writes the task's finalize record
(<task_dir>/logs/finalize.record); a new dispatch removes it before writing
[A]. A task whose ## State reads DONE with no record reached DONE without
that finalize -- its wake was stopped while the agent ran -- so
reconcile_stale_active and reconcile_terminal_worktrees
(wake_reconcile_sweeps.sh) run the same gate, through
_sweep_gate_unfinalized_done, before trusting it.

Fixtures (real git repositories and worktrees under tmp_path; the real
function bodies extracted from their stage files; the real block_record.sh
and error_contract.sh):
  (A) [A] + DONE + no finalize record + an UNMERGED feature branch ->
      reconcile_stale_active leaves the task BLOCKED under [B] with the
      gate's merge-not-found refusal in ## Blockers; never [x].
  (B) The same task WITH a finalize record -> [x], and the gate is not
      re-run (the wake's own finalize already ran it).
  (C) [A] + DONE + no finalize record + the feature branch merged --no-ff
      into the source branch and deleted -> the gate passes and the
      marker becomes [x].
  (D) reconcile_terminal_worktrees, for an unmerged DONE with no record:
      the worktree is kept (the gate's evidence), the task is BLOCKED under
      [B], and nothing is torn down.
  (E) The finalize record helpers: write then present, clear then absent.

Naming: names describe the behavior under test, never bug or task IDs.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.log_stub import make_log_stub_fragment

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
_LIB = _SCRIPTS_DIR / "lib"

# Stage files the extracted function bodies come from.
_SOURCES = (
    _LIB / "wake_task_state.sh",
    _LIB / "wake_reconcile_sweeps.sh",
    _LIB / "wake_task_transition.sh",
    _LIB / "wake_lifecycle.sh",
    _LIB / "wake_readme_meta.sh",
    _LIB / "wake_driver_task_setup.sh",
)
_FUNCTIONS = (
    "normalize_status_file",
    "_status_section_re",
    "_read_status_section",
    "get_state",
    "set_state",
    "set_block",
    "mark_backlog",
    "transition_task_state",
    "_wake_transition_marker_landed",
    "_wake_transition_restore_state",
    "_wake_transition_arm",
    "_wake_transition_disarm",
    "_wake_transition_on_signal",
    "task_liveness_record_path",
    "task_liveness_record",
    "task_liveness_verdict",
    "task_liveness_is_alive",
    "task_finalize_record_path",
    "task_finalize_record_write",
    "task_finalize_record_clear",
    "task_finalize_record_present",
    "_sweep_gate_unfinalized_done",
    "_sweep_mark_gate_refused",
    "_sweep_correct_terminal_marker",
    "reconcile_stale_active",
    "reconcile_terminal_worktrees",
    "coder_writer_completion_gate",
    "get_role_from_readme",
    "get_source_branch_from_readme",
)

_RC_BRANCH = "rc/v9.9.9"
_DEAD_PID = "99999999"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    require_binary("git", label="reconcile-unfinalized-done-gated")


def _extract_functions() -> str:
    alternation = "|".join(re.escape(name) for name in _FUNCTIONS)
    files = " ".join(f"'{path}'" for path in _SOURCES)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0 }}
                    }}
                }}
            }}
        ' {files})"
    """)


def _git(cwd: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


class Board:
    """A throwaway kanban root, dev tree and one CODER task, all under tmp_path."""

    def __init__(self, root: pathlib.Path, task_id: str) -> None:
        self.root = root
        self.task_id = task_id
        self.tasks_root = root / "tasks"
        self.task_dir = self.tasks_root / task_id
        self.status = self.task_dir / "status.md"
        self.backlog = root / "coder_backlog.md"
        self.log_file = root / "wake.log"
        self.dev_tree = root / "dev_tree"
        self.feature = f"feature/{task_id}"
        (self.task_dir / "logs").mkdir(parents=True)
        (self.task_dir / "README.md").write_text(
            f"# Task\n\n## Task ID\n{task_id}\n\n## Role\nCODER\n\n## Source Branch\n{_RC_BRANCH}\n\n"
            "## Workflow Type\nrelease\n",
            encoding="utf-8",
        )
        self.status.write_text(
            f"# Status\n\n## Task\n{task_id}\n\n## State\nDONE\n\n## Summary\nfixture.\n\n## Blockers\nnone\n\n"
            "## Needs Human\nno\n\n## Next Recommended Step\nTBD\n",
            encoding="utf-8",
        )
        self.backlog.write_text(f"# Coder Backlog\n\n- [A] {task_id}\n", encoding="utf-8")
        self.dev_tree.mkdir()
        _git(self.dev_tree, "init", "-q", "-b", "ai_main")
        _git(self.dev_tree, "config", "user.email", "test@example.invalid")
        _git(self.dev_tree, "config", "user.name", "test")
        (self.dev_tree / "base.txt").write_text("base\n", encoding="utf-8")
        _git(self.dev_tree, "add", "base.txt")
        _git(self.dev_tree, "commit", "-q", "-m", "initial")
        _git(self.dev_tree, "branch", _RC_BRANCH)

    def header(self) -> str:
        return textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            export PGAI_AGENT_KANBAN_TEMP_DIR="{self.root / 'kanban_temp'}"
            unset PGAI_AGENT_KANBAN_ROOT_PATH PP_project_name PGAI_WORKTREE_BASE PGAI_PROJECT_NAME
            export KANBAN_ROOT="{self.root}"
            cd "{_SCRIPTS_DIR.parent}"
            source "{_LIB}/pp_run_ops.sh"
            source "{_LIB}/overwatch_protocol.sh"
            source "{_LIB}/error_contract.sh"
            source "{_LIB}/block_record.sh"
            source "{_LIB}/wake_task_id_gate.sh"
            source "{_LIB}/atomic_write.sh"
            source "{_LIB}/temp.sh"
            source "{_LIB}/lock_provenance.sh"
            source "{_LIB}/worktree.sh"
            BACKLOG="{self.backlog}"
            TASKS_ROOT="{self.tasks_root}"
            PP_dev_tree_path="{self.dev_tree}"
            REPO_TEAM_DIR="{_SCRIPTS_DIR}"
            LOG_FILE="{self.log_file}"
            AGENT=coder
            # No project registry in this throwaway root: the feature branch
            # carries no prefix, exactly as a project with an empty one.
            pp_prefix_branch() {{ printf '%s' "$2"; }}

            {make_log_stub_fragment(self.log_file)}

            {_extract_functions()}
        """)

    def run(self, body: str) -> subprocess.CompletedProcess:
        script = self.root / "run_script.sh"
        script.write_text(self.header() + textwrap.dedent(body), encoding="utf-8")
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        return result

    def create_worktree_with_unmerged_work(self) -> str:
        """Create the task's worktree and commit work on its feature branch; return the worktree path."""
        result = self.run(
            f"""\
            wt="$(create_task_worktree "{self.task_id}" "{_RC_BRANCH}" "{self.feature}" "{self.dev_tree}")"
            printf 'WORKTREE=%s\\n' "$wt"
            """
        )
        match = re.search(r"^WORKTREE=(.+)$", result.stdout, re.M)
        assert match, result.stdout
        worktree = pathlib.Path(match.group(1).strip())
        _git(worktree, "config", "user.email", "test@example.invalid")
        _git(worktree, "config", "user.name", "test")
        (worktree / "work.txt").write_text("task work\n", encoding="utf-8")
        _git(worktree, "add", "work.txt")
        _git(worktree, "commit", "-q", "-m", "task work")
        return str(worktree)

    def merge_and_delete_feature(self, worktree: str) -> None:
        wt = pathlib.Path(worktree)
        _git(wt, "checkout", "-q", _RC_BRANCH)
        _git(wt, "merge", "-q", "--no-ff", self.feature, "-m", f"Merge {self.feature}")
        _git(wt, "branch", "-d", self.feature)

    def state(self) -> str:
        m = re.search(r"^## State\s*\n+(\S+)", self.status.read_text(encoding="utf-8"), re.M)
        assert m, self.status.read_text(encoding="utf-8")
        return m.group(1)

    def marker(self) -> str:
        m = re.search(rf"^\s*-\s*\[(.)\]\s+{re.escape(self.task_id)}\b", self.backlog.read_text(encoding="utf-8"), re.M)
        assert m, self.backlog.read_text(encoding="utf-8")
        return m.group(1)

    def log_text(self) -> str:
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""


def _registered_worktrees(dev_tree: pathlib.Path) -> list[str]:
    out = _git(dev_tree, "worktree", "list", "--porcelain")
    return [line[len("worktree "):] for line in out.splitlines() if line.startswith("worktree ")]


def test_unfinalized_done_with_unmerged_branch_is_blocked_not_marked_done(tmp_path: pathlib.Path) -> None:
    board = Board(tmp_path, "CODER-20260101-061-unfinalized-done-unmerged")
    worktree = board.create_worktree_with_unmerged_work()

    board.run("reconcile_stale_active\n")

    assert board.marker() == "B", f"an unmerged done-claim must never become [x]\n{board.backlog.read_text()}"
    assert board.state() == "BLOCKED"
    status_text = board.status.read_text(encoding="utf-8")
    assert "wake.completion-gate.merge-not-found" in status_text, status_text
    assert board.feature in status_text and _RC_BRANCH in status_text, status_text
    assert pathlib.Path(worktree).is_dir(), "the worktree holding the unmerged work must be kept"
    log_text = board.log_text()
    assert "no finalize record" in log_text and "completion gate refused" in log_text, log_text


def test_done_with_a_finalize_record_is_marked_done_without_regating(tmp_path: pathlib.Path) -> None:
    board = Board(tmp_path, "CODER-20260101-062-finalized-done")
    board.create_worktree_with_unmerged_work()
    board.run(f'task_finalize_record_write "{board.task_dir}" DONE\n')

    board.run("reconcile_stale_active\n")

    assert board.marker() == "x"
    assert board.state() == "DONE"
    assert "running the completion gate" not in board.log_text(), board.log_text()


def test_unfinalized_done_whose_branch_was_merged_passes_the_gate(tmp_path: pathlib.Path) -> None:
    board = Board(tmp_path, "CODER-20260101-063-unfinalized-done-merged")
    worktree = board.create_worktree_with_unmerged_work()
    board.merge_and_delete_feature(worktree)

    board.run("reconcile_stale_active\n")

    assert board.marker() == "x", board.log_text()
    assert board.state() == "DONE"
    log_text = board.log_text()
    assert "running the completion gate" in log_text and "completion gate: task" in log_text, log_text
    assert "PASS" in log_text, log_text


def test_terminal_worktree_sweep_keeps_the_worktree_of_a_refused_done(tmp_path: pathlib.Path) -> None:
    board = Board(tmp_path, "CODER-20260101-064-unfinalized-done-terminal-sweep")
    worktree = board.create_worktree_with_unmerged_work()

    board.run(
        f"""\
        task_liveness_record "{board.task_id}" claude {_DEAD_PID} dispatch
        reconcile_terminal_worktrees
        """
    )

    assert worktree in _registered_worktrees(board.dev_tree), "a refused done-claim's worktree must not be torn down"
    assert board.state() == "BLOCKED"
    assert board.marker() == "B"
    assert "worktree removed" not in board.log_text(), board.log_text()


def test_finalize_record_helpers_write_detect_and_clear(tmp_path: pathlib.Path) -> None:
    board = Board(tmp_path, "CODER-20260101-065-finalize-record-helpers")

    result = board.run(
        f"""\
        task_finalize_record_present "{board.task_dir}" && echo "BEFORE=present" || echo "BEFORE=absent"
        task_finalize_record_write "{board.task_dir}" DONE
        task_finalize_record_present "{board.task_dir}" && echo "AFTER_WRITE=present" || echo "AFTER_WRITE=absent"
        task_finalize_record_clear "{board.task_dir}"
        task_finalize_record_present "{board.task_dir}" && echo "AFTER_CLEAR=present" || echo "AFTER_CLEAR=absent"
        task_finalize_record_clear "{board.task_dir}" && echo "CLEAR_AGAIN=ok"
        task_finalize_record_path "" || echo "BLANK=refused"
        """
    )

    assert "BEFORE=absent" in result.stdout
    assert "AFTER_WRITE=present" in result.stdout
    assert "AFTER_CLEAR=absent" in result.stdout
    assert "CLEAR_AGAIN=ok" in result.stdout, "clearing an absent record succeeds"
    assert "BLANK=refused" in result.stdout
