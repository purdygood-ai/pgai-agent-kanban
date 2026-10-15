"""
test_coder_completion_empty_commit_gate_bash.py
=================================================
Behavioral fixtures for coder_writer_completion_gate's empty-commit check
(team/scripts/lib/wake_lifecycle.sh), which runs alongside the gate's
pre-existing merge and cleanliness checks.

Detection was never reimplemented here -- the check shells out to the real
team/scripts/lint_rc_empty_commits.py (its own find_empty_commits logic),
scoped via "<source_branch>^..<feature_branch>" to just the commits the
claiming task's own feature branch introduced. This module exercises the
genuine production function body (extracted via the same awk brace-matching
technique test_coder_completion_merge_gate_bash.py established) against
real git repositories, mirroring that module's structure.

Fixtures exercised:
  (A) A feature branch carrying one real commit and one empty non-merge
      commit, merged --no-ff into the source branch (the branch ref still
      present, matching gate-time ordering) -> DONE-claim REFUSED, naming
      the empty commit; Needs Human escalated.
  (B) A feature branch carrying only real (non-empty) commits, merged the
      same way -> DONE-claim ACCEPTED (the check must not false-positive
      on ordinary work).
  (C) Best-effort skip: another task's merge lands on the source branch
      AFTER the claiming task's own merge (source_branch's tip is no
      longer the claiming task's merge commit) -> the empty-commit check
      is skipped rather than risk attributing another task's commits; the
      skip is narrated via log(), and the gate still accepts the
      (otherwise clean) DONE-claim on the merge/cleanliness checks alone.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

from tests.fixtures.log_stub import make_log_stub_fragment
import pytest
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="coder-completion-empty-commit-gate-bash")

_WAKE_TASK_STATE_LIB = pathlib.Path("scripts/lib/wake_task_state.sh")
_WAKE_LIFECYCLE_LIB = pathlib.Path("scripts/lib/wake_lifecycle.sh")

# Real, checked-in team/scripts/ directory -- holds error_contract.sh (the
# dual-channel emitter) and lint_rc_empty_commits.py (the detection logic
# the gate calls rather than reimplements). This file lives at
# team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"


def _extract_functions_snippet(func_names: list[str]) -> str:
    """Pull named top-level function bodies out of wake_task_state.sh /
    wake_lifecycle.sh via awk brace-counting -- mirrors
    test_coder_completion_merge_gate_bash.py's own extraction helper, kept
    local here so this module has no import-time coupling to that one."""
    alternation = "|".join(re.escape(name) for name in func_names)
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
        ' '{_WAKE_TASK_STATE_LIB}' '{_WAKE_LIFECYCLE_LIB}')"
    """)


def _script_header(log_file: pathlib.Path) -> str:
    funcs = ["normalize_status_file", "set_state", "set_block", "coder_writer_completion_gate"]
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {make_log_stub_fragment(log_file)}
        REPO_TEAM_DIR="{_REAL_REPO_TEAM_SCRIPTS_DIR}"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/atomic_write.sh"
        {_extract_functions_snippet(funcs)}
    """)


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _read(status_file: pathlib.Path) -> str:
    return status_file.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _build_status_file(tmp_path: pathlib.Path, task_id: str) -> pathlib.Path:
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        textwrap.dedent(f"""\
            # Status

            ## Task
            {task_id}

            ## State
            DONE

            ## Summary
            Work merged --no-ff into the source branch; feature branch deleted.

            ## Artifacts
            none

            ## Blockers
            none

            ## Needs Human
            no

            ## Next Recommended Step
            TBD

            ## Instruction Conflicts
            none
        """),
        encoding="utf-8",
    )
    return status_file


def _build_repo(
    tmp_path: pathlib.Path,
    task_id: str,
    source_branch: str = "ai_rc/v9.9.9",
    *,
    plant_empty_commit: bool,
) -> tuple[pathlib.Path, str, pathlib.Path]:
    """Build a dev tree on *source_branch*, a task worktree on a feature
    branch with one real commit (and, when *plant_empty_commit*, one
    trailing ``--allow-empty`` commit too), then merge --no-ff into
    *source_branch* inside the task worktree, mirroring CODER.md PHASE 2
    Step 7. The feature branch ref is left in place (Step 8 -- deletion --
    has not run yet), matching gate-time ordering.

    Returns (dev_tree, feature_branch, worktree_path).
    """
    feature_branch = f"ai_feature/{task_id}"
    dev_tree = tmp_path / "dev"
    dev_tree.mkdir()
    _git(dev_tree, "init", "-q")
    _git(dev_tree, "config", "user.email", "test@example.com")
    _git(dev_tree, "config", "user.name", "Test")
    _git(dev_tree, "-c", "commit.gpgsign=false", "checkout", "-q", "-b", source_branch)
    (dev_tree / "README.md").write_text("initial\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "initial")

    worktree_path = tmp_path / "worktree"
    _git(dev_tree, "worktree", "add", "-q", "-b", feature_branch, str(worktree_path), source_branch)
    _git(dev_tree, "checkout", "-q", "--detach")

    (worktree_path / "feature-work.txt").write_text("feature work\n", encoding="utf-8")
    _git(worktree_path, "add", ".")
    _git(worktree_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "feature work")

    if plant_empty_commit:
        _git(
            worktree_path,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "oops -- stray empty commit",
        )

    _git(worktree_path, "checkout", "-q", source_branch)
    _git(
        worktree_path,
        "-c",
        "commit.gpgsign=false",
        "merge",
        "-q",
        "--no-ff",
        "-m",
        f"Merge {feature_branch} into {source_branch}",
        feature_branch,
    )

    return dev_tree, feature_branch, worktree_path


# ---------------------------------------------------------------------------
# (A) Empty non-merge commit on the feature branch -> REFUSED.
# ---------------------------------------------------------------------------


def test_empty_commit_on_feature_branch_refuses_done_claim(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-201-fixture-empty"
    source_branch = "ai_rc/v9.9.9"
    _dev_tree, feature_branch, worktree_path = _build_repo(
        tmp_path, task_id, source_branch, plant_empty_commit=True
    )
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)

    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=[BLOCKED]" in result.stdout, (
        f"an empty non-merge commit on the feature branch must refuse the DONE-claim\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED"
    blockers = _field(status_text, "Blockers")
    assert feature_branch in blockers, f"remedy must name the feature branch\n{blockers}"
    assert "empty" in blockers.lower(), f"remedy must state the empty-commit failure\n{blockers}"
    assert _field(status_text, "Needs Human").lower() == "yes"
    assert "error:   wake.completion-gate.empty-commit" in blockers, (
        f"Blockers must carry the house-shape error key\n{blockers}"
    )
    assert "message: " in blockers and "remedy:  " in blockers, (
        f"Blockers must carry the house-shape message/remedy fields\n{blockers}"
    )


# ---------------------------------------------------------------------------
# (B) No empty commits -> ACCEPTED (no false positive on ordinary work).
# ---------------------------------------------------------------------------


def test_no_empty_commits_accepts_done_claim(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-202-fixture-clean"
    source_branch = "ai_rc/v9.9.9"
    _dev_tree, feature_branch, worktree_path = _build_repo(
        tmp_path, task_id, source_branch, plant_empty_commit=False
    )
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)

    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"ordinary work with no empty commits must not be refused\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert _field(_read(status_file), "State") == "DONE"


# ---------------------------------------------------------------------------
# (C) Another task's merge landed on source_branch since -> check skipped,
#     best-effort, not blocking.
# ---------------------------------------------------------------------------


def test_empty_commit_check_skips_when_source_branch_advanced_since(
    tmp_path: pathlib.Path,
) -> None:
    task_id = "CODER-20260101-203-fixture-advanced"
    source_branch = "ai_rc/v9.9.9"
    _dev_tree, feature_branch, worktree_path = _build_repo(
        tmp_path, task_id, source_branch, plant_empty_commit=True
    )

    # A second, unrelated commit lands directly on source_branch after this
    # task's own merge -- source_branch's tip is no longer this task's own
    # merge commit, so the empty-commit check cannot safely isolate this
    # feature branch's commits and must skip rather than guess.
    (worktree_path / "unrelated.txt").write_text("someone else's work\n", encoding="utf-8")
    _git(worktree_path, "add", ".")
    _git(worktree_path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "unrelated follow-on commit")

    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)

    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"the empty-commit check must skip (not block) once source_branch's "
        f"tip is no longer this task's own merge commit\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "empty-commit check skipped" in log_text, (
        f"the skip must be narrated via log(), not silent\nlog:\n{log_text}"
    )
