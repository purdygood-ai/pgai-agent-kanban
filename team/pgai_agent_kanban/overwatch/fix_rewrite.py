"""
fix_rewrite.py — Lane A's Fix-rewrite step for OVERWATCH's autonomous block
resolution.

When a HALTed block classifies as Lane A (pgai_agent_kanban.overwatch.
resolution_matrix.classify_lane() returning LANE_A — a TESTER finding
carrying Regression=yes that traces to a bug this RC's own bundle fixed),
the recovery playbook's first step is rewriting the originating bug's
``## Fix`` section so it reads exactly one way before the bundle is ever
re-decomposed. The regression happened because the original Fix section
admitted two readings and the RC that shipped it picked the wrong one; a
plain re-read of the same ambiguous text would let the next CODER make the
identical mistake. This module is that rewrite step, in isolation — the
unwind, cancel, delete, and HALT-removal steps that follow it in the Lane A
playbook (team/roles/OVERWATCH.md, "Autonomous Block Resolution") are a
separate call site's responsibility and are not performed here.

What ``rewrite_bug_fix_section()`` does, in order:

  1. Confirms the target bug file exists and already carries a ``## Fix``
     section — this step only ever rewrites an existing Fix section; it
     never authors one from nothing (a bug with no Fix section is not
     something Lane A's playbook can be reasoning about a "failed
     approach" for, so this is a fail-loud precondition, not a silent
     no-op).
  2. Backs up the bug file via ``backup_bug_file()`` — OVERWATCH.md's
     always-backup-then-act rule — before any byte of the file changes,
     and records the backup in the project's OVERWATCH action log via
     ``pgai_agent_kanban.overwatch.resolution_stage.log_action`` (the
     single Python-side action-log writer every OVERWATCH stage already
     shares; see that module's own docstring).
  3. Appends — never edits in place — a new, dated, OVERWATCH-attributed
     ``## Fix`` block to the END of the bug file. The original ``## Fix``
     section, and every section after it, keep their exact bytes and
     their exact position: this step only ever grows the file. The
     appended block:
       - names the approach that produced the regression as FORBIDDEN, in
         those words, so the next reader cannot re-derive the same wrong
         reading;
       - states the correct direction in its own right, not merely as
         "the opposite of the forbidden approach" (a reader who skips the
         forbidden-approach paragraph must still get a complete answer);
       - reproduces every ground-truth reference the caller supplies
         (a git ref, the exact shipped text being changed, ...) VERBATIM,
         never paraphrased into a description of where to look — the
         caller is responsible for sourcing that text; this step is
         responsible for never summarizing it away.

Callers resolve the bug's file path from a
``pgai_agent_kanban.overwatch.resolution_matrix.LaneClassification``'s
``regression_bug_id`` first — ``find_bug_file()`` below is the small,
generic lookup that turns a short id (e.g. "BUG-NNNN") into the one bugs/
file whose name carries it, the same shape
``pgai_agent_kanban.overwatch.resolution_stage.find_tester_report()``
already uses for its own id-to-path resolution.

``resolution_stage.py``'s deterministic Lane A drill (``_run_lane_a()``)
never calls this module itself — forbidden_approach and correct_direction
are a judgment call about which fix was actually correct, not a fact the
drill can read off disk, so the drill only ever checks for this step's
*result* (``has_attributed_rewrite()`` below) as a precondition before
running steps (b)-(e). The shipped, non-test caller that actually performs
the rewrite is this module's own CLI (below), wrapped by
``team/scripts/overwatch-attach-fix-rewrite.sh`` — the tool an operator, or
a future reasoning-tier agent, runs to supply that judgment call before
the next OVERWATCH firing.

Usage (import):
    from pgai_agent_kanban.overwatch.fix_rewrite import (
        find_bug_file, rewrite_bug_fix_section,
    )
    bug_path = find_bug_file(bugs_dir, lane_result.regression_bug_id)
    result = rewrite_bug_fix_section(
        bug_path,
        forbidden_approach="...",
        correct_direction="...",
        ground_truth_refs=["commit abcd123: <verbatim log line>"],
        kanban_root=kanban_root,
        project_name=project_name,
    )

Usage (CLI, via the wrapper script):
    team/scripts/overwatch-attach-fix-rewrite.sh \\
        --project <name> --kanban-root <path> --bug-id BUG-NNNN \\
        --forbidden-approach "..." --correct-direction "..." \\
        --ground-truth-ref "commit abcd123: <verbatim log line>"
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

# Run by file path (``python3 .../fix_rewrite.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402

from pgai_agent_kanban.overwatch.resolution_stage import log_action  # noqa: E402

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FixRewriteError(Exception):
    """Raised when the Fix-rewrite step cannot proceed safely — a missing
    bug file, a missing ``## Fix`` section to rewrite, or missing/empty
    required content (forbidden approach, ground-truth references). Every
    case is fail-loud: this step never guesses a forbidden approach or a
    ground-truth reference on the caller's behalf.
    """


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixRewriteResult:
    """What one ``rewrite_bug_fix_section()`` call did, for callers (the
    Lane A executor, tests) to inspect and log.
    """

    bug_path: str
    backup_path: str
    timestamp: str
    appended_block: str


# ---------------------------------------------------------------------------
# Bug-id-to-path lookup (mirrors resolution_stage.find_tester_report()'s
# id-to-path resolution shape for the same reason: a caller holding a short
# id from a classification result should never have to re-derive a bugs/
# directory listing convention of its own)
# ---------------------------------------------------------------------------


def find_bug_file(bugs_dir: str, bug_id: str) -> Optional[str]:
    """Return the path of the file in *bugs_dir* whose basename carries
    *bug_id* (e.g. "BUG-0042"), or None when *bugs_dir* does not exist, no
    file matches, or *bug_id* is empty. When more than one file matches
    (should not happen for a well-formed bugs/ directory, where each id is
    unique), the lexically first match is returned — deterministic rather
    than order-of-listdir-dependent.
    """
    if not bug_id or not os.path.isdir(bugs_dir):
        return None
    prefix = bug_id if bug_id.endswith("-") else f"{bug_id}-"
    candidates = sorted(
        name
        for name in os.listdir(bugs_dir)
        if name.startswith(prefix) or name == f"{bug_id}.md"
    )
    if not candidates:
        return None
    return os.path.join(bugs_dir, candidates[0])


# ---------------------------------------------------------------------------
# Backup (Python counterpart of overwatch_backup_file in
# team/scripts/lib/overwatch_protocol.sh — same destination layout, same
# idempotency contract, so operator tooling and overwatch-revert.sh find a
# Python-side backup exactly where they would find a bash-side one)
# ---------------------------------------------------------------------------


def backup_bug_file(
    bug_path: str,
    kanban_root: str,
    project_name: str,
    timestamp: Optional[str] = None,
) -> str:
    """Copy *bug_path* to the project's timestamped OVERWATCH backup
    directory and return the destination path.

    Destination layout:
        <kanban_root>/projects/<project_name>/overwatch/backups/<timestamp>/<basename>

    Idempotent: if the destination already exists (same timestamp, same
    source basename — the same-firing case), the existing backup is left
    untouched and its path is returned rather than re-copying. Creates the
    backups directory if it does not yet exist (defensive: OVERWATCH.md
    Step 3 normally bootstraps it, but this step must not depend on that
    bootstrap having already run in this process).
    """
    if not os.path.isfile(bug_path):
        raise FixRewriteError(f"cannot back up: source file does not exist: {bug_path}")

    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    backup_dir = os.path.join(kanban_root, "projects", project_name, "overwatch", "backups", ts)
    os.makedirs(backup_dir, exist_ok=True)

    dst_path = os.path.join(backup_dir, os.path.basename(bug_path))
    if os.path.isfile(dst_path):
        return dst_path

    shutil.copy2(bug_path, dst_path)
    return dst_path


def _write_bug_file(target: str, content: str) -> None:
    """Atomically replace *target* with *content* via the shared writer,
    creating its directory first. A crash mid-write never leaves the bug
    file half-rewritten; a write failure propagates as OSError.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    atomic_write(target, content, prefix=".fix_rewrite_tmp_")


# ---------------------------------------------------------------------------
# The rewrite block itself
# ---------------------------------------------------------------------------


# The exact heading rewrite_bug_fix_section() appends — the single source
# both _build_rewrite_block() (the writer) and has_attributed_rewrite()
# (the reader, Lane A's precondition guard) key off, so the two can never
# drift apart.
REWRITE_BLOCK_HEADING = "## Fix (OVERWATCH rewrite)"


def has_attributed_rewrite(text: str) -> bool:
    """Return True when *text* (a bug file's full contents) already
    carries an appended, OVERWATCH-attributed Fix rewrite block.

    This is Lane A's recovery-drill precondition
    (``pgai_agent_kanban.overwatch.resolution_stage._run_lane_a``): steps
    (b)-(e) run only once this is True for the blamed bug, so the
    destructive drill never outruns the reasoning tier's judgment call
    (``team/roles/OVERWATCH.md``, "Lane A — bad-fix regression":
    "Performing that rewrite is the reasoning tier's duty, not the
    drill's."). A plain substring check, not a full section parse —
    ``rewrite_bug_fix_section()`` only ever appends ``REWRITE_BLOCK_HEADING``
    verbatim, so its presence is unambiguous evidence the append already
    ran, and its absence is unambiguous evidence it has not.
    """
    return REWRITE_BLOCK_HEADING in text


def _format_ground_truth(ground_truth_refs: Sequence[str]) -> str:
    return "\n".join(f"- {ref}" for ref in ground_truth_refs)


def _build_rewrite_block(
    *,
    timestamp: str,
    forbidden_approach: str,
    correct_direction: str,
    ground_truth_refs: Sequence[str],
) -> str:
    """Build the appended ``## Fix`` block. Every field is reproduced
    exactly as the caller supplied it — this function formats, it never
    summarizes or truncates the forbidden-approach text, the correct-
    direction text, or any ground-truth reference.
    """
    return (
        "\n---\n\n"
        f"{REWRITE_BLOCK_HEADING}\n"
        "**Attributed To:** OVERWATCH\n"
        f"**Date:** {timestamp}\n\n"
        f"**Forbidden approach — do not repeat:** {forbidden_approach}\n\n"
        "The approach named above was implemented in this RC and produced the "
        "regression that HALTed it. It is FORBIDDEN: the next reader must not "
        "re-derive it from the original Fix section below.\n\n"
        f"**Correct direction:** {correct_direction}\n\n"
        "**Ground truth (verbatim — sourced from the record, not paraphrased):**\n"
        f"{_format_ground_truth(ground_truth_refs)}\n\n"
        "*This section was appended by OVERWATCH per the Lane A recovery "
        "playbook (team/roles/OVERWATCH.md, \"Autonomous Block Resolution\"). "
        "The original `## Fix` section above is preserved unchanged; this "
        "rewrite is the single-reading direction the next fix must follow.*\n"
    )


def _has_fix_section(text: str) -> bool:
    for line in text.splitlines():
        if line.strip() == "## Fix":
            return True
    return False


# ---------------------------------------------------------------------------
# The step
# ---------------------------------------------------------------------------


def rewrite_bug_fix_section(
    bug_path: str,
    *,
    forbidden_approach: str,
    correct_direction: str,
    ground_truth_refs: Sequence[str],
    kanban_root: str,
    project_name: str,
    timestamp: Optional[str] = None,
) -> FixRewriteResult:
    """Append a single-reading, OVERWATCH-attributed ``## Fix`` rewrite to
    *bug_path*, after backing the file up. See this module's docstring for
    the full contract; briefly:

    Args:
        bug_path: Absolute path to the bug file to rewrite. Must already
            exist and carry a ``## Fix`` section.
        forbidden_approach: Names, in enough detail to be unambiguous, the
            approach that was implemented and produced the regression.
            Required — a Lane A rewrite with no named forbidden approach
            has not broken the two-reading cycle (a Fix section that admits
            two readings, re-read the same wrong way; see the module
            docstring).
        correct_direction: States the correct fix on its own terms (not
            merely "the opposite of the forbidden approach").
        ground_truth_refs: One or more verbatim references the correct
            direction depends on — a git ref, the exact shipped text being
            changed, and so on. Required and reproduced exactly as given;
            never summarized.
        kanban_root: The kanban root the project's ``overwatch/`` state
            directory is resolved under.
        project_name: The project whose ``overwatch/`` state directory
            receives the backup and the action-log entry.
        timestamp: ISO 8601 UTC timestamp to date the rewrite with, and to
            group the backup under. Defaults to the current time — tests
            and callers that need a deterministic, repeatable value should
            pass one explicitly.

    Returns:
        A FixRewriteResult naming the bug file, the backup path, the
        timestamp used, and the exact block appended.

    Raises:
        FixRewriteError: *bug_path* does not exist, has no ``## Fix``
            section, or *forbidden_approach* / *ground_truth_refs* is
            empty.
    """
    if not os.path.isfile(bug_path):
        raise FixRewriteError(f"bug file does not exist: {bug_path}")

    original = open(bug_path, "r", encoding="utf-8").read()
    if not _has_fix_section(original):
        raise FixRewriteError(
            f"bug file has no '## Fix' section to rewrite: {bug_path} — this step "
            "only rewrites an existing Fix section, it never authors one from nothing"
        )
    if not forbidden_approach or not forbidden_approach.strip():
        raise FixRewriteError(
            "forbidden_approach is required: the rewrite must name the failed "
            "approach so the next reader cannot re-derive it"
        )
    if not ground_truth_refs:
        raise FixRewriteError(
            "ground_truth_refs must carry at least one verbatim reference (a git "
            "ref or the exact shipped text) — the rewrite must never send the next "
            "reader off to look it up"
        )

    ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    backup_path = backup_bug_file(bug_path, kanban_root, project_name, timestamp=ts)

    project_root = os.path.join(kanban_root, "projects", project_name)
    target = os.path.relpath(bug_path, project_root) if bug_path.startswith(project_root) else bug_path
    log_action(
        kanban_root,
        project_name,
        name="lane-a-fix-rewrite",
        target=target,
        action="auto-fix:fix-rewrite",
        reason="Lane A recovery playbook: rewrote the originating bug's Fix section into a single-reading direction",
        backup_path=backup_path,
    )

    block = _build_rewrite_block(
        timestamp=ts,
        forbidden_approach=forbidden_approach,
        correct_direction=correct_direction,
        ground_truth_refs=ground_truth_refs,
    )
    new_content = original.rstrip("\n") + "\n" + block
    _write_bug_file(bug_path, new_content)

    return FixRewriteResult(
        bug_path=bug_path,
        backup_path=backup_path,
        timestamp=ts,
        appended_block=block,
    )


# ---------------------------------------------------------------------------
# CLI — the shipped, non-test call site. Lane A's deterministic drill
# only ever checks has_attributed_rewrite() for this
# step's result (see module docstring); this CLI is how the judgment call
# actually gets made, by whoever is qualified to make it — an operator, or
# a future reasoning-tier agent — before the next OVERWATCH firing runs
# the drill. Wrapped by team/scripts/overwatch-attach-fix-rewrite.sh, the
# same "thin .sh wrapper around pp_run_ops" shape every other operator
# verb in this package uses (see team/scripts/task-status.sh).
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the Lane A fix-rewrite CLI: append an OVERWATCH rewrite block to one bug file.

    Prints the bug path and its backup on success. Exit codes: 0 appended;
    1 no bug file matches ``--bug-id``, or the rewrite was refused (reason on
    stderr); 2 usage error (argparse).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Attach OVERWATCH's Lane A Fix-rewrite (step (a) of the recovery "
            "playbook, team/roles/OVERWATCH.md \"Lane A -- bad-fix regression\") "
            "to a bug file: appends a dated, OVERWATCH-attributed "
            "'## Fix (OVERWATCH rewrite)' block naming the failed approach as "
            "FORBIDDEN, stating the correct direction, and reproducing "
            "ground-truth references verbatim. forbidden_approach and "
            "correct_direction are never derived automatically -- the caller "
            "supplies the judgment call. Once this has run, "
            "pgai_agent_kanban.overwatch.resolution_stage's Lane A drill will "
            "run steps (b)-(e) on its next firing instead of declining."
        )
    )
    parser.add_argument("--project", required=True, help="Project name as registered in projects.cfg.")
    parser.add_argument("--kanban-root", required=True, help="Kanban root path.")
    parser.add_argument(
        "--bug-id", required=True,
        help="Short bug id (e.g. BUG-0163) to resolve within the project's bugs/ directory.",
    )
    parser.add_argument(
        "--forbidden-approach", required=True,
        help="Names, unambiguously, the approach that was implemented and produced the regression.",
    )
    parser.add_argument(
        "--correct-direction", required=True,
        help="States the correct fix on its own terms, not merely the opposite of the forbidden approach.",
    )
    parser.add_argument(
        "--ground-truth-ref", dest="ground_truth_refs", action="append", default=[],
        help=(
            "A verbatim reference (a git ref, the exact shipped text being changed, ...) the "
            "correct direction depends on. Repeatable; at least one is required."
        ),
    )
    parser.add_argument(
        "--timestamp", default=None,
        help="ISO 8601 UTC timestamp override (tests only; defaults to the current time).",
    )
    args = parser.parse_args()

    bugs_dir = os.path.join(args.kanban_root, "projects", args.project, "bugs")
    bug_path = find_bug_file(bugs_dir, args.bug_id)
    if bug_path is None:
        sys.stderr.write(f"fix-rewrite: no bug file matching '{args.bug_id}' found under {bugs_dir}\n")
        return 1

    try:
        result = rewrite_bug_fix_section(
            bug_path,
            forbidden_approach=args.forbidden_approach,
            correct_direction=args.correct_direction,
            ground_truth_refs=args.ground_truth_refs,
            kanban_root=args.kanban_root,
            project_name=args.project,
            timestamp=args.timestamp,
        )
    except FixRewriteError as exc:
        sys.stderr.write(f"fix-rewrite: {exc}\n")
        return 1

    sys.stdout.write(
        f"fix-rewrite: appended OVERWATCH rewrite to {result.bug_path} (backup: {result.backup_path})\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
