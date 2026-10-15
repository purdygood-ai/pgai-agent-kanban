"""
test_coder_completion_merge_gate_bash.py
=========================================
Behavioral fixtures for the wake reconciliation completion gate that
verifies a CODER/WRITER DONE-claim mechanically instead of trusting the
agent's own status.md write.

Without this gate, a CODER (or WRITER) task could finish with status.md
State=DONE while its feature branch was never merged into the RC branch, or
while the task's worktree still carried uncommitted changes. Nothing at the
wake-reconciliation boundary detected the gap: the queue advanced, and the
missing work surfaced later as a confusing "implemented but absent" TESTER
finding requiring operator archaeology.

This module exercises the gate directly against the real production function
in wake_lifecycle.sh (coder_writer_completion_gate), extracted via the same
awk brace-matching technique test_census_closure.py and
test_transient_retry_record.py established — this avoids the heavy top-level
bootstrap wake_common.sh performs when fully sourced (AGENT, TEAM_ROOT,
SCRIPT_DIR, a real kanban.cfg, Python 3.12+ on PATH), while still exercising
the genuine production function body against real git repositories.

Fixtures exercised:
  (A) Unmerged feature branch -> DONE-claim REFUSED: task lands BLOCKED with
      a prescriptive reason naming both the feature branch and the target
      (source/RC) branch; Needs Human is escalated to yes.
  (B) Merge performed (feature branch still present, now an ancestor of the
      source branch) -> re-completion is ACCEPTED (DONE), byte-identical to
      the golden path.
  (C) Dirty worktree at done-claim (uncommitted change after a real merge)
      -> DONE-claim REFUSED naming the dirty path.
  (D) Golden path: feature branch merged --no-ff into the source branch AND
      deleted (git branch -d, the CODER/WRITER procedure's exact sequence),
      worktree clean -> DONE-claim ACCEPTED, and the gate's log narration is
      exactly one line for the task (no stdout contamination of the return
      value — see _run_gate's log()-redirection note).
  (E) No-worktree task shape (workflow git_mode=none, e.g. document
      workflow) -> passes trivially regardless of subagent.
  (F) Non-CODER/WRITER subagent (e.g. cm) -> passes through unaffected even
      when the feature/source branch arguments would otherwise refuse.
  (G) Real wake reconciliation path: the actual call-site block from
      wake/claude.sh (gate invocation through the case "$final_state"
      dispatch) is extracted and exercised with a stubbed mark_backlog, so
      an unmerged DONE-claim is observed to route to mark_backlog("B") —
      the queue does not advance past it — exactly as production wiring
      would behave.
  (H) Retired diff-scope comment-provenance lint: a done-claim
      whose diff plants a ledger-ID comment (e.g. "per BUG-0042") no longer
      refuses once merge-ancestry and cleanliness both pass — the gate
      accepts it as DONE with no block and no Needs-Human escalation. The
      same litter class is still caught, just not here: it is TESTER's job,
      via the tree-wide provenance lint the gated runners already run at
      verification time.
  (I) Sibling-checkout accept path: the source/RC branch is held by
      a SIBLING worktree at merge time, so the --no-ff merge happens there
      instead of in the claiming task's own worktree; the task's worktree
      only detaches and deletes its now-merged branch. The gate must accept
      this as DONE via merge-evidence read from the source branch's own
      history, independent of the claiming worktree's HEAD state.
  (J) True-negative for the sibling-checkout accept path: the feature branch
      is force-deleted WITHOUT ever being merged (no merge commit on the
      source branch, no ancestor relationship) -> the gate must still
      REFUSE with the existing reflog-inspection remedy text. The widened
      evidence base must not silence a genuine no-merge failure.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from tests.fixtures.log_stub import make_log_stub_fragment
from tests.fixtures.binary_guard import require_binary


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Skip every test in this module when git is not on PATH.

    Every test here builds, inspects, or drives a real git repository; a
    module-wide autouse guard keeps that requirement in one place instead
    of repeating a per-test check at each git-dependent call site.
    """
    require_binary("git", label="coder-completion-merge-gate-bash")

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------

# normalize_status_file / set_state / set_block live in this stage file
# (v2.9.20-engine-decomposition); coder_writer_completion_gate lives in
# wake_lifecycle.sh (same decomposition) but calls those three, so
# extraction must span both.
_WAKE_TASK_STATE_LIB = pathlib.Path("scripts/lib/wake_task_state.sh")
_WAKE_LIFECYCLE_LIB = pathlib.Path("scripts/lib/wake_lifecycle.sh")
# This gate call-site block now lives once in the shared wake driver's
# finalize stage, sourced by both wake/claude.sh and wake/codex.sh --
# process_one_task's inline body was lifted out of wake/claude.sh into this
# shared driver, and wake/codex.sh sources the same driver instead of
# carrying its own inline copy.
_CLAUDE_SH = pathlib.Path("scripts/lib/wake_driver_finalize.sh")

_GATE_CALL_ANCHOR = "# --- CODER/WRITER completion gate ---"

# Real, checked-in team/scripts/ directory (holds error_contract.sh, the
# dual-channel emitter the gate's refusal narrations call). This file lives
# at team/tests/unit/, so three parents up is team/.
_REAL_REPO_TEAM_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"


# ---------------------------------------------------------------------------
# Function extraction (mirrors test_census_closure.py / test_transient_retry_record.py):
# pull named top-level function bodies out of their stage files via awk
# brace-counting, so tests exercise the real production function bodies
# without paying for wake_common.sh's full top-level bootstrap.
# ---------------------------------------------------------------------------


def _extract_functions_snippet(func_names: list[str]) -> str:
    """Return a bash snippet that extracts and loads the named top-level
    functions via awk brace-counting.

    Searches wake_task_state.sh and wake_lifecycle.sh (in that order) —
    normalize_status_file/set_state/set_block live in the former,
    coder_writer_completion_gate in the latter, after
    v2.9.20-engine-decomposition split both out of wake_common.sh's former
    inline body. awk's found/depth state is per-function and closes on the
    function's own closing brace, so scanning multiple files in one
    invocation is safe: a function fully defined in one file never leaks
    state across the file boundary.
    """
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


def _extract_gate_call_block(path: pathlib.Path = _CLAUDE_SH) -> list[str]:
    """Extract the real wake/claude.sh call-site block: from the completion
    gate's anchor comment through the first top-level `esac` that closes the
    case "$final_state" dispatch immediately below it.

    Mirrors the anchor-to-esac extraction technique
    test_wake_dispatch_role_assignment.py uses for the pre-dispatch
    assertion block, so this test exercises the genuine production wiring
    (not a reimplementation of it).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    block: list[str] = []
    for line in lines:
        if _GATE_CALL_ANCHOR in line:
            capturing = True
        if capturing:
            block.append(line)
            if line.strip() == "esac" and len(block) > 5:
                break
    assert block, f"completion gate anchor not found in {path}"
    return block


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _build_repo_with_diverged_feature_branch(
    tmp_path: pathlib.Path,
    task_id: str,
    source_branch: str = "ai_rc/v9.9.9",
) -> tuple[pathlib.Path, str, pathlib.Path]:
    """Build a dev tree on *source_branch*, then a git worktree checked out
    on a feature branch that has one commit ahead of (diverged from, not
    merged into) the source branch.

    Returns (dev_tree, feature_branch, worktree_path).
    """
    feature_branch = f"ai_feature/{task_id}"
    dev_tree = tmp_path / "dev"
    dev_tree.mkdir()
    _git(dev_tree, "init", "-q")
    _git(dev_tree, "config", "user.email", "test@example.com")
    _git(dev_tree, "config", "user.name", "Test")
    _git(dev_tree, "checkout", "-q", "-b", source_branch)
    (dev_tree / "README.md").write_text("initial\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-q", "-m", "initial")

    worktree_path = tmp_path / "worktree"
    _git(dev_tree, "worktree", "add", "-q", "-b", feature_branch, str(worktree_path), source_branch)
    # Park the canonical dev tree off source_branch (mirrors wake/claude.sh's
    # parking step) so the task worktree below is free to check out
    # source_branch itself during the merge — git refuses to have the same
    # branch checked out in two worktrees at once.
    _git(dev_tree, "checkout", "-q", "--detach")

    (worktree_path / "feature-work.txt").write_text("feature work\n", encoding="utf-8")
    _git(worktree_path, "add", ".")
    _git(worktree_path, "commit", "-q", "-m", "feature work")

    return dev_tree, feature_branch, worktree_path


def _merge_feature_into_source(worktree_path: pathlib.Path, feature_branch: str, source_branch: str) -> None:
    """Mirror roles/CODER.md PHASE 2 Step 7: checkout the source branch and
    merge --no-ff the feature branch, inside the task's own worktree."""
    _git(worktree_path, "checkout", "-q", source_branch)
    _git(worktree_path, "merge", "-q", "--no-ff", "-m", f"Merge {feature_branch} into {source_branch}", feature_branch)


def _delete_feature_branch(worktree_path: pathlib.Path, feature_branch: str) -> None:
    """Mirror roles/CODER.md PHASE 2 Step 8: git branch -d (never -D)."""
    _git(worktree_path, "branch", "-d", feature_branch)


def _build_repo_with_sibling_worktree_merge(
    tmp_path: pathlib.Path,
    task_id: str,
    source_branch: str = "ai_rc/v9.9.9",
) -> tuple[pathlib.Path, str, pathlib.Path]:
    """Reproduce the sibling-checkout production shape: the source/RC branch
    is held by a SIBLING worktree at merge time, so the claiming task's own
    --no-ff merge necessarily happens in that sibling worktree, never in the
    task's own worktree.

    Sequence:
      1. A sibling worktree (`worktree_rc`) legitimately holds source_branch
         checked out — standing in for another task's in-flight worktree.
      2. The claiming task's own worktree (`worktree_task`) is created on a
         fresh feature branch off source_branch and gets one commit.
      3. The --no-ff merge is performed INSIDE worktree_rc (the sibling that
         holds source_branch) — worktree_task's HEAD never becomes
         source_branch.
      4. worktree_task detaches (the CODER/WRITER improvisation the bug
         describes: detach so the now-merged feature branch ref can be
         deleted from anywhere).
      5. The feature branch is deleted with plain `-d` (never -D) from
         worktree_rc, which is on source_branch post-merge and therefore
         satisfies -d's merged-into-HEAD safety check.

    Returns (dev_tree, feature_branch, worktree_task_path) — the completion
    gate is exercised against worktree_task_path, exactly as the wake script
    calls it for the claiming task.
    """
    feature_branch = f"ai_feature/{task_id}"
    dev_tree = tmp_path / "dev"
    dev_tree.mkdir()
    _git(dev_tree, "init", "-q")
    _git(dev_tree, "config", "user.email", "test@example.com")
    _git(dev_tree, "config", "user.name", "Test")
    _git(dev_tree, "checkout", "-q", "-b", source_branch)
    (dev_tree / "README.md").write_text("initial\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-q", "-m", "initial")
    # Park the canonical dev tree off source_branch so both worktrees below
    # are free to check out branches without contention.
    _git(dev_tree, "checkout", "-q", "--detach")

    worktree_rc = tmp_path / "worktree-rc"
    _git(dev_tree, "worktree", "add", "-q", str(worktree_rc), source_branch)

    worktree_task = tmp_path / "worktree-task"
    _git(dev_tree, "worktree", "add", "-q", "-b", feature_branch, str(worktree_task), source_branch)

    (worktree_task / "feature-work.txt").write_text("feature work\n", encoding="utf-8")
    _git(worktree_task, "add", ".")
    _git(worktree_task, "commit", "-q", "-m", "feature work")

    # The merge happens in the SIBLING worktree that holds source_branch —
    # worktree_task's own HEAD is never touched by this step.
    _git(worktree_rc, "merge", "-q", "--no-ff", "-m", f"Merge {feature_branch} into {source_branch}", feature_branch)

    # Task's own worktree detaches so the now-merged feature branch ref can
    # be deleted (git refuses to delete a branch checked out anywhere).
    _git(worktree_task, "checkout", "-q", "--detach")

    # Plain -d (never -D): succeeds because the branch is merged into
    # worktree_rc's HEAD (source_branch, post-merge).
    _git(worktree_rc, "branch", "-d", feature_branch)

    return dev_tree, feature_branch, worktree_task


# ---------------------------------------------------------------------------
# status.md fixture builder
# ---------------------------------------------------------------------------


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


def _script_header(
    log_file: pathlib.Path,
    extra_functions: list[str] | None = None,
) -> str:
    """Bash header shared by every direct-function test in this module.

    Sources the faithful log() stub (log()'s stdout-teeing behavior must be
    reproduced faithfully — the gate function itself relies on the exact
    `log ... >/dev/null` idiom to keep its own return-value command
    substitution clean; a stderr-only stub would mask a regression there),
    the real team/scripts/lib/error_contract.sh (the dual-channel emitter the
    gate's refusal narrations call to build their Blockers text — see
    team/scripts/lib/error_contract.sh for the house shape), and the real
    normalize_status_file / set_state / set_block (wake_task_state.sh) /
    coder_writer_completion_gate (wake_lifecycle.sh) extracted from their
    respective stage files.

    Sets REPO_TEAM_DIR to the real, checked-in team/scripts/ directory —
    the global wake_common.sh itself resolves at source time (one level
    above lib/) — so the extracted function bodies see the same value they
    would in production, even though the gate no longer consumes it (the
    diff-scope lint check that once read it via REPO_TEAM_DIR is retired).

    Also sources the real team/scripts/lib/lock_provenance.sh: the
    `_extract_gate_call_block` extraction below (anchor through the first
    top-level `esac`) now also carries the queue-write mutating-phase-
    boundary reverification immediately above that `case` statement, which
    calls the real pgai_lock_reverify_or_narrate — an unmerged prerequisite
    left unsatisfied here would make every `gate_block` caller fail with
    "command not found" regardless of what it is actually testing (see the
    CODER role file's "sweep the dependent fixtures" guidance for a new
    guard on shared infrastructure). The three `gate_block` callers below
    each additionally seed a real, currently-held lock record so that
    reverification passes silently, exactly like the golden single-chain
    path it is standing in for.
    """
    funcs = ["normalize_status_file", "set_state", "set_block", "coder_writer_completion_gate"]
    if extra_functions:
        funcs += [f for f in extra_functions if f not in funcs]
    return textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {make_log_stub_fragment(log_file)}
        REPO_TEAM_DIR="{_REAL_REPO_TEAM_SCRIPTS_DIR}"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/error_contract.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/atomic_write.sh"
        source "{_REAL_REPO_TEAM_SCRIPTS_DIR}/lib/lock_provenance.sh"
        {_extract_functions_snippet(funcs)}
    """)


def _held_lock_fragment(lock_file: pathlib.Path) -> str:
    """Bash fragment seeding a real, currently-held lock record at
    *lock_file* naming THIS script's own pid -- so a `pgai_lock_
    reverify_or_narrate "{{lock_file}}" "$$" ...` call inside an extracted
    `gate_block` (see `_script_header`'s own docstring) reads "still held"
    and passes silently, matching the golden single-chain path every
    `gate_block` caller below is actually testing. A plain provenance
    write (not a real pgai_lock_acquire/flock) is enough: the
    reverification is a cheap read of the file's content, never the flock
    itself."""
    return textwrap.dedent(f"""\
        _project_lock_file="{lock_file}"
        printf 'holder=test\\npid=%s\\nop=wake-chain:test-project\\ntask=none\\n' "$$" > "{lock_file}"
    """)


def _run(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=timeout)


def _read(status_file: pathlib.Path) -> str:
    return status_file.read_text(encoding="utf-8")


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


# ---------------------------------------------------------------------------
# (A) Unmerged feature branch -> DONE-claim REFUSED, prescriptive remedy
#     naming both branches, Needs Human escalated.
# ---------------------------------------------------------------------------


def test_unmerged_feature_branch_refuses_done_claim(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-101-fixture-a"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)

    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[BLOCKED]" in result.stdout, (
        f"unmerged feature branch must refuse the DONE-claim\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED", f"State must be BLOCKED\n{status_text}"
    blockers = _field(status_text, "Blockers")
    assert feature_branch in blockers, f"remedy must name the feature branch\n{blockers}"
    assert source_branch in blockers, f"remedy must name the target (source/RC) branch\n{blockers}"
    assert "not merged" in blockers, f"remedy must state the merge failure\n{blockers}"
    assert _field(status_text, "Needs Human").lower() == "yes", f"Needs Human must escalate\n{status_text}"
    # Dual-channel error contract: a forced gate refusal must emit
    # error/message/remedy in the house shape, with a stable, greppable
    # machine key.
    assert "error:   wake.completion-gate.merge-not-found" in blockers, (
        f"Blockers must carry the house-shape error key\n{blockers}"
    )
    assert "message: " in blockers and "remedy:  " in blockers, (
        f"Blockers must carry the house-shape message/remedy fields\n{blockers}"
    )


# ---------------------------------------------------------------------------
# (B) Merge performed (branch not yet deleted) -> re-completion accepted.
# ---------------------------------------------------------------------------


def test_merge_performed_then_recompletion_is_accepted(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-102-fixture-b"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    # First pass: unmerged -> refused.
    script = _script_header(log_file) + textwrap.dedent(f"""\
        coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}" >/dev/null
    """)
    first = _run(script)
    assert first.returncode == 0
    assert _field(_read(status_file), "State") == "BLOCKED"

    # Operator/agent performs the merge (feature branch left in place —
    # exercises the "ref still exists, now an ancestor" path).
    _merge_feature_into_source(worktree_path, feature_branch, source_branch)

    # Task reset: state flipped back to DONE (mirrors a re-completion claim).
    set_state_script = _script_header(log_file) + textwrap.dedent(f"""\
        set_state "{status_file}" "DONE"
    """)
    reset_result = _run(set_state_script)
    assert reset_result.returncode == 0, f"resetting status to DONE should not fail\nstderr={reset_result.stderr}"

    second_script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    second = _run(second_script)
    assert second.returncode == 0, f"gate call should not error\nstderr={second.stderr}"
    assert "RESULT=[DONE]" in second.stdout, (
        f"a real merge must be accepted on re-completion\nstdout={second.stdout}\nstderr={second.stderr}"
    )
    assert _field(_read(status_file), "State") == "DONE"


# ---------------------------------------------------------------------------
# (C) Dirty worktree at done-claim -> REFUSED, dirty path named.
# ---------------------------------------------------------------------------


def test_dirty_worktree_refuses_done_claim(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-103-fixture-c"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    _merge_feature_into_source(worktree_path, feature_branch, source_branch)
    # Leave an uncommitted change in the worktree after the merge.
    (worktree_path / "stray-uncommitted.txt").write_text("oops\n", encoding="utf-8")

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[BLOCKED]" in result.stdout, (
        f"a dirty worktree at done-claim must refuse\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED"
    blockers = _field(status_text, "Blockers")
    assert "stray-uncommitted.txt" in blockers, f"remedy must name the dirty path\n{blockers}"
    assert str(worktree_path) in blockers, f"remedy must name the worktree\n{blockers}"
    assert _field(status_text, "Needs Human").lower() == "yes"


# ---------------------------------------------------------------------------
# (D) Golden path: merged --no-ff AND branch deleted, clean worktree ->
#     ACCEPTED, one-line log narration, and the return value is not
#     contaminated by the log stub's stdout mirroring.
# ---------------------------------------------------------------------------


def test_golden_path_merged_and_branch_deleted_is_accepted(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-104-fixture-d"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    # Exact CODER/WRITER PHASE 2 sequence: checkout source, merge --no-ff,
    # delete the feature branch with -d (never -D).
    _merge_feature_into_source(worktree_path, feature_branch, source_branch)
    _delete_feature_branch(worktree_path, feature_branch)

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"

    # The captured RESULT must be exactly "DONE" — no log-line contamination
    # from the command substitution (the golden-path regression this design
    # exists to prevent: without `log ... >/dev/null`, this line would read
    # "RESULT=[<log line>\nDONE]" and the caller's case dispatch would fail
    # to match either DONE or BLOCKED).
    assert "RESULT=[DONE]" in result.stdout, (
        f"a clean, fully-merged completion must be accepted with zero friction\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    result_line = [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT=")]
    assert result_line == ["RESULT=[DONE]"], (
        f"the gate's return value must be exactly DONE, not multi-line contaminated\n{result_line}"
    )

    # status.md is untouched on the golden path (State remains whatever the
    # caller already had — this function does not rewrite a passing task).
    assert _field(_read(status_file), "State") == "DONE"

    # Narration: exactly one PASS line for this task in the wake log.
    log_lines = [
        ln for ln in log_file.read_text(encoding="utf-8").splitlines()
        if task_id in ln
    ]
    assert len(log_lines) == 1, f"expected exactly one narration line\nlog:\n{log_lines}"
    assert "PASS" in log_lines[0], f"narration must report PASS\n{log_lines[0]}"
    assert feature_branch in log_lines[0] and source_branch in log_lines[0], (
        f"narration must name both branches\n{log_lines[0]}"
    )


# ---------------------------------------------------------------------------
# (E) No-worktree task shape (workflow git_mode=none) -> passes trivially.
# ---------------------------------------------------------------------------


def test_no_worktree_task_shape_passes_trivially(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-105-fixture-e"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "" "" "")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"a no-worktree task shape must pass trivially\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert _field(_read(status_file), "State") == "DONE"


# ---------------------------------------------------------------------------
# (F) Non-CODER/WRITER subagent -> gate is a no-op, even with arguments that
#     would otherwise refuse.
# ---------------------------------------------------------------------------


def test_non_coder_writer_subagent_is_unaffected(tmp_path: pathlib.Path) -> None:
    task_id = "CM-20260101-106-fixture-f"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "cm" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"CM completion semantics must be untouched by this gate\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert _field(_read(status_file), "State") == "DONE"


# ---------------------------------------------------------------------------
# (G) Real wake reconciliation path: the actual wake/claude.sh call-site
#     block (gate invocation through the case "$final_state" dispatch),
#     exercised with a stubbed mark_backlog. An unmerged DONE-claim must
#     route to mark_backlog("B") — the queue does not advance past it.
# ---------------------------------------------------------------------------


def test_real_wake_path_routes_unmerged_claim_to_blocked_marker(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-107-fixture-g"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    mark_backlog_calls = tmp_path / "mark_backlog_calls.log"

    gate_block = "\n".join(_extract_gate_call_block())

    # The extracted block declares `local _completion_gate_refused` (it is
    # production text lifted verbatim from inside process_one_task, a bash
    # function) — it must run inside a function here too, or bash rejects
    # `local` at script top level. final_state is set as a plain (non-local)
    # variable before the call, so the block's bare `final_state=...`
    # assignment (dynamic scoping, no `local final_state` in the block)
    # still updates the same variable the outer script reads afterward.
    script = _script_header(log_file) + textwrap.dedent(f"""\
        mark_backlog() {{
            printf '%s\\t%s\\n' "$1" "$2" >> "{mark_backlog_calls}"
        }}
        AGENT="overwatch"    # bypass the on-BLOCK overwatch nudge in the extracted block
        KANBAN_ROOT="{tmp_path}"
        {_held_lock_fragment(tmp_path / "repo.lock")}
        task_id="{task_id}"
        task_status="{status_file}"
        SUBAGENT="coder"
        _task_feature_branch="{feature_branch}"
        _task_source_branch="{source_branch}"
        _task_worktree_path="{worktree_path}"
        final_state="DONE"    # the agent's own (unverified) done-claim

        _run_extracted_call_site() {{
        {gate_block}
            printf 'FINAL_STATE=[%s]\\n' "$final_state"
            printf 'GATE_REFUSED=[%s]\\n' "$_completion_gate_refused"
        }}
        _run_extracted_call_site
    """)
    result = _run(script)
    assert result.returncode == 0, f"extracted call-site block should not error\nstderr={result.stderr}"
    assert "FINAL_STATE=[BLOCKED]" in result.stdout, (
        f"an unmerged DONE-claim must be downgraded to BLOCKED\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "GATE_REFUSED=[true]" in result.stdout

    assert mark_backlog_calls.exists(), "mark_backlog must have been invoked"
    calls = mark_backlog_calls.read_text(encoding="utf-8").strip().splitlines()
    assert calls == [f"{task_id}\tB"], (
        f"the queue must be marked BLOCKED ('B'), never advanced to done ('x')\ncalls={calls}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED"
    assert feature_branch in _field(status_text, "Blockers")
    assert source_branch in _field(status_text, "Blockers")


def test_real_wake_path_accepts_golden_path_claim(tmp_path: pathlib.Path) -> None:
    """Same extracted call-site block as the fixture above, but for a
    properly merged + clean completion: mark_backlog must receive the DONE
    marker ('x'), not 'B' — zero added friction on the golden path."""
    task_id = "CODER-20260101-108-fixture-g2"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(tmp_path, task_id)
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    mark_backlog_calls = tmp_path / "mark_backlog_calls.log"

    _merge_feature_into_source(worktree_path, feature_branch, source_branch)
    _delete_feature_branch(worktree_path, feature_branch)

    gate_block = "\n".join(_extract_gate_call_block())

    script = _script_header(log_file) + textwrap.dedent(f"""\
        mark_backlog() {{
            printf '%s\\t%s\\n' "$1" "$2" >> "{mark_backlog_calls}"
        }}
        AGENT="overwatch"
        KANBAN_ROOT="{tmp_path}"
        {_held_lock_fragment(tmp_path / "repo.lock")}
        task_id="{task_id}"
        task_status="{status_file}"
        SUBAGENT="coder"
        _task_feature_branch="{feature_branch}"
        _task_source_branch="{source_branch}"
        _task_worktree_path="{worktree_path}"
        final_state="DONE"

        _run_extracted_call_site() {{
        {gate_block}
            printf 'FINAL_STATE=[%s]\\n' "$final_state"
        }}
        _run_extracted_call_site
    """)
    result = _run(script)
    assert result.returncode == 0, f"extracted call-site block should not error\nstderr={result.stderr}"
    assert "FINAL_STATE=[DONE]" in result.stdout, (
        f"a merged, clean completion must stay DONE\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    calls = mark_backlog_calls.read_text(encoding="utf-8").strip().splitlines()
    assert calls == [f"{task_id}\tx"], (
        f"a golden-path completion must advance the queue to done ('x')\ncalls={calls}"
    )


# ---------------------------------------------------------------------------
# (H) Retired diff-scope comment-provenance lint. The gate used to run a
# third check here that refused a done-claim whose diff
# planted a ledger-ID comment. It is retired: by gate time the merge has
# usually already landed and the feature branch is already deleted, so a
# wording-only finding at this point cost an operator a reword-and-reset for
# litter the TESTER-time path already catches (the gated runners' own
# tree-wide `lint_hygiene.py` provenance-class run, no diff-scope mode). These
# fixtures prove the retirement: a planted ledger-ID comment no longer
# refuses once merge-ancestry and cleanliness both pass, through both the
# direct function call and the real wake/claude.sh call-site block.
# ---------------------------------------------------------------------------


def _build_repo_with_merged_feature_branch_file(
    tmp_path: pathlib.Path,
    task_id: str,
    file_relpath: str,
    file_contents: str,
    source_branch: str = "ai_rc/v9.9.9",
    delete_feature_branch: bool = True,
) -> tuple[pathlib.Path, str, pathlib.Path]:
    """Build a dev tree on *source_branch*, branch a feature branch, commit
    *file_contents* at *file_relpath* on it, then merge --no-ff into
    *source_branch* and (by default) delete the feature branch — the exact
    CODER/WRITER PHASE 2 Steps 7-8 sequence, but with a caller-supplied file
    so tests can plant a ledger-ID comment or any other content as needed.

    Returns (dev_tree, feature_branch, worktree_path).
    """
    feature_branch = f"ai_feature/{task_id}"
    dev_tree = tmp_path / f"dev-{task_id}"
    dev_tree.mkdir()
    _git(dev_tree, "init", "-q")
    _git(dev_tree, "config", "user.email", "test@example.com")
    _git(dev_tree, "config", "user.name", "Test")
    _git(dev_tree, "checkout", "-q", "-b", source_branch)
    (dev_tree / "README.md").write_text("initial\n", encoding="utf-8")
    _git(dev_tree, "add", ".")
    _git(dev_tree, "commit", "-q", "-m", "initial")

    worktree_path = tmp_path / f"worktree-{task_id}"
    _git(dev_tree, "worktree", "add", "-q", "-b", feature_branch, str(worktree_path), source_branch)
    _git(dev_tree, "checkout", "-q", "--detach")

    target = worktree_path / file_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(file_contents, encoding="utf-8")
    _git(worktree_path, "add", ".")
    _git(worktree_path, "commit", "-q", "-m", "feature work")

    _merge_feature_into_source(worktree_path, feature_branch, source_branch)
    if delete_feature_branch:
        _delete_feature_branch(worktree_path, feature_branch)

    return dev_tree, feature_branch, worktree_path


def test_planted_ledger_id_comment_no_longer_refuses_done_claim(tmp_path: pathlib.Path) -> None:
    """A done-claim whose feature-branch diff plants a comment citing an
    internal bug-ID-shaped reference, with the feature branch merged
    --no-ff into the source branch and the worktree clean, is ACCEPTED as
    DONE — no block, no Needs-Human. The retired diff-scope lint no longer
    runs here; only merge-ancestry and cleanliness gate this done-claim."""
    task_id = "CODER-20260101-109-fixture-h1"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_merged_feature_branch_file(
        tmp_path,
        task_id,
        "pkg/module.py",
        "def foo():\n    # per BUG-0042 this needs a fix\n    return 1\n",
    )
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"a planted ledger-ID comment must no longer refuse a merged, clean DONE-claim\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "DONE", f"State must stay DONE (no refusal)\n{status_text}"
    assert _field(status_text, "Needs Human").lower() == "no", (
        f"Needs Human must not be escalated for retired-check litter\n{status_text}"
    )

    log_lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if task_id in ln]
    assert len(log_lines) == 1, f"expected exactly one narration line\nlog:\n{log_lines}"
    assert "PASS" in log_lines[0], f"narration must report PASS, not a refusal\n{log_lines[0]}"


def test_real_wake_path_accepts_planted_ledger_id_comment_claim(tmp_path: pathlib.Path) -> None:
    """Real wake reconciliation path, mirroring the fixture above: the actual
    wake/claude.sh call-site block, exercised with a stubbed mark_backlog. A
    done-claim whose diff plants a ledger-ID comment must route to
    mark_backlog("x") — the queue advances, exactly as a properly merged,
    clean completion would with no litter at all."""
    task_id = "CODER-20260101-110-fixture-h2"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_merged_feature_branch_file(
        tmp_path,
        task_id,
        "pkg/module.py",
        "def foo():\n    # per BUG-0042 this needs a fix\n    return 1\n",
    )
    source_branch = "ai_rc/v9.9.9"
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"
    mark_backlog_calls = tmp_path / "mark_backlog_calls.log"

    gate_block = "\n".join(_extract_gate_call_block())

    script = _script_header(log_file) + textwrap.dedent(f"""\
        mark_backlog() {{
            printf '%s\\t%s\\n' "$1" "$2" >> "{mark_backlog_calls}"
        }}
        AGENT="overwatch"
        KANBAN_ROOT="{tmp_path}"
        {_held_lock_fragment(tmp_path / "repo.lock")}
        task_id="{task_id}"
        task_status="{status_file}"
        SUBAGENT="coder"
        _task_feature_branch="{feature_branch}"
        _task_source_branch="{source_branch}"
        _task_worktree_path="{worktree_path}"
        final_state="DONE"

        _run_extracted_call_site() {{
        {gate_block}
            printf 'FINAL_STATE=[%s]\\n' "$final_state"
        }}
        _run_extracted_call_site
    """)
    result = _run(script)
    assert result.returncode == 0, f"extracted call-site block should not error\nstderr={result.stderr}"
    assert "FINAL_STATE=[DONE]" in result.stdout, (
        f"a planted ledger-ID comment must no longer downgrade a merged, clean DONE-claim\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    calls = mark_backlog_calls.read_text(encoding="utf-8").strip().splitlines()
    assert calls == [f"{task_id}\tx"], (
        f"the queue must advance to done ('x'); the retired check must not hold it back\ncalls={calls}"
    )
    status_text = _read(status_file)
    assert _field(status_text, "State") == "DONE"
    assert _field(status_text, "Needs Human").lower() == "no"


# ---------------------------------------------------------------------------
# (I) Sibling-checkout accept path. The merge happens in a SIBLING
# worktree that legitimately holds source_branch; the claiming task's own
# worktree only detaches and deletes its branch. The gate must accept this
# via merge-evidence read from source_branch's own history.
# ---------------------------------------------------------------------------


def test_sibling_worktree_merge_is_accepted_via_merge_evidence(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-115-fixture-i"
    source_branch = "ai_rc/v9.9.9"
    _dev_tree, feature_branch, worktree_task = _build_repo_with_sibling_worktree_merge(
        tmp_path, task_id, source_branch
    )
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    # Sanity: the claiming task's own worktree is NOT on source_branch — the
    # exact state a fallback demanding source_branch in the claiming
    # worktree would wrongly refuse. symbolic-ref exits
    # non-zero on a detached HEAD (git raises CalledProcessError), which
    # confirms the detached state directly rather than asserting on output.
    with pytest.raises(subprocess.CalledProcessError):
        _git(worktree_task, "symbolic-ref", "--quiet", "--short", "HEAD")

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_task}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[DONE]" in result.stdout, (
        f"a merge performed in a sibling worktree holding the source branch must be accepted "
        f"via merge-evidence, independent of the claiming worktree's own HEAD state\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert _field(_read(status_file), "State") == "DONE"

    log_lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if task_id in ln]
    assert len(log_lines) == 1, f"expected exactly one narration line\nlog:\n{log_lines}"
    assert "PASS" in log_lines[0], f"narration must report PASS\n{log_lines[0]}"
    assert "merge-evidence" in log_lines[0], (
        f"narration must name the merge-evidence acceptance path\n{log_lines[0]}"
    )
    assert feature_branch in log_lines[0] and source_branch in log_lines[0], (
        f"narration must name both branches\n{log_lines[0]}"
    )


# ---------------------------------------------------------------------------
# (J) True-negative for the sibling-checkout accept path. The feature
# branch is force-deleted WITHOUT
# ever being merged -> no merge commit on the source, no ancestor evidence.
# The widened fallback must still refuse with the existing remedy.
# ---------------------------------------------------------------------------


def test_force_deleted_unmerged_branch_still_refuses_done_claim(tmp_path: pathlib.Path) -> None:
    task_id = "CODER-20260101-116-fixture-j"
    source_branch = "ai_rc/v9.9.9"
    _dev_tree, feature_branch, worktree_path = _build_repo_with_diverged_feature_branch(
        tmp_path, task_id, source_branch
    )
    status_file = _build_status_file(tmp_path, task_id)
    log_file = tmp_path / "wake.log"

    # Simulate ref loss with genuinely NO merge evidence: detach (so the
    # branch is no longer checked out here) then force-delete it (-D, never
    # the procedure's own -d) without ever merging into source_branch.
    _git(worktree_path, "checkout", "-q", "--detach")
    _git(worktree_path, "branch", "-D", feature_branch)

    script = _script_header(log_file) + textwrap.dedent(f"""\
        result="$(coder_writer_completion_gate \\
            "{task_id}" "{status_file}" "coder" \\
            "{feature_branch}" "{source_branch}" "{worktree_path}")"
        printf 'RESULT=[%s]\\n' "$result"
    """)
    result = _run(script)
    assert result.returncode == 0, f"gate call should not error\nstderr={result.stderr}"
    assert "RESULT=[BLOCKED]" in result.stdout, (
        f"a force-deleted branch with no merge evidence anywhere in source_branch's history "
        f"must still refuse the DONE-claim\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    status_text = _read(status_file)
    assert _field(status_text, "State") == "BLOCKED"
    blockers = _field(status_text, "Blockers")
    assert feature_branch in blockers, f"remedy must name the feature branch\n{blockers}"
    assert "no longer exists" in blockers, f"remedy must state the ref is gone\n{blockers}"
    assert "the merge cannot be confirmed" in blockers, (
        f"remedy must use the existing reflog-inspection wording (unchanged)\n{blockers}"
    )
    assert "git reflog" in blockers, f"remedy must retain the reflog-inspection instruction\n{blockers}"
    assert _field(status_text, "Needs Human").lower() == "yes"
