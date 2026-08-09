"""
resolution_stage.py — OVERWATCH's autonomous block-resolution execution stage.

Wires the existing on-BLOCK trigger (scripts/wake/claude.sh and codex.sh,
"on-BLOCK overwatch trigger") to the resolution matrix
(pgai_agent_kanban.overwatch.resolution_matrix) and the append-only ledger
(pgai_agent_kanban.overwatch.ledger). This module is the call site both
wake provider siblings invoke, once per project, on every OVERWATCH
firing (see "run_overwatch_resolution_stage" in team/scripts/lib/
wake_common.sh) — which is what the on-BLOCK trigger reaches within seconds
of a fresh BLOCKED task, per team/roles/OVERWATCH.md's "Autonomous Block
Resolution" section.

On every call this module does exactly one of two things:

  - EXECUTE — the current block matches the shipped CANCEL-AND-REBUNDLE row.
    Performs the operator's documented cancel drill through the existing
    tools (team/scripts/cm/cancel-rc.sh --yes, wontdo_item, close_item,
    reset_item), and writes ONE ledger entry atomically with the decision
    (decide-and-log, never log-after — the ledger entry is appended BEFORE
    the drill's actions run, so a kill mid-drill leaves either no entry at
    all (killed before the decision was reached) or an entry whose
    actions_taken list already names everything the drill was about to do
    (killed during or after the drill) — OVERWATCH's next sweep reconciles
    a partially-applied drill against this record).
  - DECLINE — anything else (no ship-policy HALT present, an operator HALT
    present alongside a ship-policy HALT, an unmatched/unknown block shape,
    a ceiling-config or evidence error). No cancel/reversible drill action
    is ever taken on a decline; the decision is narrated as one line in the
    project's OVERWATCH action log ($KANBAN_ROOT/projects/<project>/
    overwatch/actions.log) so the decline is auditable without EVER writing
    a NEW ledger entry (the ledger records only resolutions actually
    executed, per ledger.py's own docstring). The one exception: when the
    decline is specifically because the bundle family has reached the
    retry ceiling, this module backfills the family's existing ledger
    entries' Outcome to CEILING-BLOCKED (closing out that family's story,
    per ledger.py's own "terminal state" contract) and narrates a richer
    decline reason carrying the family's full retry history ("attempt
    3/3; prior attempts cancelled for: ..."), a strictly richer human
    briefing than a single-failure view. This backfill never creates a
    ledger entry and never touches any other family's entries.

This module never overrides a content verdict (SOP.md / OVERWATCH.md
authority boundary): it only executes the ONE prescribed, fully-reversible
remediation the matrix names, or does nothing at all.

Usage (CLI, the shape both wake siblings invoke via pp_run_ops):
    python3 -m pgai_agent_kanban.overwatch.resolution_stage \
        --project <name> --kanban-root <path>

Usage (import):
    from pgai_agent_kanban.overwatch.resolution_stage import run_resolution_stage
    outcome = run_resolution_stage(project_name, kanban_root)
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from pgai_agent_kanban.cm.bundle_family import compute_bundle_family
from pgai_agent_kanban.cm.read_state_field import read_state_field
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import OpsError
from pgai_agent_kanban.ops.write import close_item, reset_item, wontdo_item
from pgai_agent_kanban.overwatch.ledger import (
    LedgerEntry,
    LedgerError,
    append_entry,
    backfill_family_outcome,
    ledger_path_for_project,
)
from pgai_agent_kanban.overwatch.resolution_matrix import (
    CANCEL_AND_REBUNDLE,
    HALT_HARD,
    BlockEvidence,
    ResolutionMatrixError,
    _CM_HALT_HEADER_RE,  # single-sourced ship-policy HALT header check
    resolve_block,
)

# ---------------------------------------------------------------------------
# Outcome model
# ---------------------------------------------------------------------------

DECLINED = "declined"
EXECUTED = "executed"
NO_OP = "no-op"


@dataclass
class ResolutionStageOutcome:
    """What one resolution-stage call did, for callers (tests, CLI) to inspect."""

    status: str  # NO_OP | DECLINED | EXECUTED
    reason: str
    ledger_entry_id: Optional[str] = None
    actions_taken: list = field(default_factory=list)
    action_errors: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# HALT file classification (single-sourced against resolution_matrix's own
# CM-header regex so "what counts as a ship-policy HALT" is defined once)
# ---------------------------------------------------------------------------


def classify_halt_file(path: str) -> str:
    """Return "ship-policy", "operator", or "absent" for the HALT file at *path*.

    "ship-policy" — the file exists and its content matches the CM HALT
    header CM.md documents ("# HALT created by CM at ..."). "operator" — the
    file exists but the header does not match (an operator's own HALT, or
    any other content). "absent" — the file does not exist.
    """
    if not path or not os.path.isfile(path):
        return "absent"
    try:
        content = open(path, "r", encoding="utf-8").read()
    except OSError:
        return "operator"
    return "ship-policy" if _CM_HALT_HEADER_RE.search(content) else "operator"


def _locate_halt_files(kanban_root: str, project_name: str) -> tuple:
    """Return (project_halt_path, global_halt_path) — the two conventional
    HALT locations this kanban uses (SOP.md "HALT Flags"; unwind-rc.sh
    pre-flight check 2 recognizes both). Paths are returned unconditionally;
    callers check existence via classify_halt_file().
    """
    project_halt = os.path.join(kanban_root, "projects", project_name, "HALT")
    global_halt = os.path.join(kanban_root, "HALT")
    return project_halt, global_halt


# ---------------------------------------------------------------------------
# Action log (Python counterpart of overwatch_log_action in
# team/scripts/lib/overwatch_protocol.sh — same tab-separated format, same
# target file, so operator tooling reads one uniform log regardless of
# whether an entry came from a Tier-1 bash check or this Python stage)
# ---------------------------------------------------------------------------


def log_action(
    kanban_root: str,
    project_name: str,
    name: str,
    target: str,
    action: str,
    reason: str,
    backup_path: str = "none",
) -> None:
    """Append one line to the project's OVERWATCH action log.

    Mirrors overwatch_log_action's record format exactly: tab-separated
    timestamp, name, target, action, backup_path, reason. Creates the
    project's overwatch/ state directory if it does not yet exist (the
    bootstrap OVERWATCH.md pre-flight Step 3 normally performs, defensively
    repeated here since this module may run before any bash-side bootstrap).
    """
    state_dir = os.path.join(kanban_root, "projects", project_name, "overwatch")
    os.makedirs(state_dir, exist_ok=True)
    log_file = os.path.join(state_dir, "actions.log")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = "\t".join(
        [timestamp, name, target, action, backup_path or "none", reason or ""]
    )
    with open(log_file, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# ---------------------------------------------------------------------------
# project.cfg reading (dev_tree_path, branch_prefix) — Python counterpart of
# project_paths.sh's pp_dev_tree_path / pp_branch_prefix, minimal by design:
# only the two keys this module needs.
# ---------------------------------------------------------------------------


def _read_project_cfg(project_root: str) -> dict:
    cfg_path = os.path.join(project_root, "project.cfg")
    result = {"dev_tree_path": "", "branch_prefix": ""}
    if not os.path.isfile(cfg_path):
        return result
    parser = configparser.ConfigParser()
    try:
        parser.read(cfg_path, encoding="utf-8")
    except configparser.Error:
        return result
    if parser.has_option("project", "dev_tree_path"):
        result["dev_tree_path"] = parser.get("project", "dev_tree_path").strip()
    if parser.has_option("project", "branch_prefix"):
        result["branch_prefix"] = parser.get("project", "branch_prefix").strip().strip('"')
    return result


# ---------------------------------------------------------------------------
# RC task-set / TESTER-report discovery (mirrors the same "does this task's
# README reference this RC version" scan team/scripts/cm/cancel-rc.sh Step 6
# and team/scripts/unwind-rc.sh's inventory already use)
# ---------------------------------------------------------------------------


def _task_state(status_file: str) -> str:
    try:
        text = open(status_file, "r", encoding="utf-8").read()
    except OSError:
        return ""
    m = re.search(r"## State\n(?:[ \t]*\n)*([^\n]+)", text)
    return m.group(1).strip() if m else ""


def find_rc_task_ids(tasks_dir: str, version: str) -> list:
    """Return task IDs (any role) whose README references *version* and whose
    current state is not DONE or WONT-DO — the RC's still-active task set.
    """
    task_ids = []
    if not version or not os.path.isdir(tasks_dir):
        return task_ids
    for entry in sorted(os.listdir(tasks_dir)):
        task_dir = os.path.join(tasks_dir, entry)
        if not os.path.isdir(task_dir) or entry == "queues":
            continue
        readme = os.path.join(task_dir, "README.md")
        status_file = os.path.join(task_dir, "status.md")
        if not os.path.isfile(readme):
            continue
        try:
            readme_text = open(readme, "r", encoding="utf-8").read()
        except OSError:
            continue
        if version not in readme_text:
            continue
        state = _task_state(status_file) if os.path.isfile(status_file) else ""
        if state in ("DONE", "WONT-DO"):
            continue
        task_ids.append(entry)
    return task_ids


def find_tester_report(tasks_dir: str, version: str) -> Optional[str]:
    """Best-effort: locate the artifacts/report.md of the most recent DONE
    TESTER-* task whose README references *version*. Returns None when no
    such task is found — the caller's fail-closed evidence extraction (see
    resolution_matrix.py's _fact_findings_all_filed) then defaults to False,
    which is the conservative, correct outcome when no report can be traced.
    """
    if not version or not os.path.isdir(tasks_dir):
        return None
    candidates = []
    for entry in sorted(os.listdir(tasks_dir)):
        if not entry.startswith("TESTER-"):
            continue
        task_dir = os.path.join(tasks_dir, entry)
        readme = os.path.join(task_dir, "README.md")
        status_file = os.path.join(task_dir, "status.md")
        report = os.path.join(task_dir, "artifacts", "report.md")
        if not (os.path.isfile(readme) and os.path.isfile(status_file) and os.path.isfile(report)):
            continue
        try:
            readme_text = open(readme, "r", encoding="utf-8").read()
        except OSError:
            continue
        if version not in readme_text:
            continue
        if _task_state(status_file) != "DONE":
            continue
        candidates.append(entry)
    if not candidates:
        return None
    # Task IDs embed YYYYMMDD-NNN; lexical order == chronological order.
    latest = sorted(candidates)[-1]
    return os.path.join(tasks_dir, latest, "artifacts", "report.md")


# ---------------------------------------------------------------------------
# The drill: cm-cancel-rc --yes -> wontdo the RC task set -> close the
# bundle intake item wont-do -> reset the cargo bugs to open -> clear ONLY
# the ship-policy HALT it is resolving
# ---------------------------------------------------------------------------


def _run_cm_cancel_rc(kanban_root: str, project_name: str, version: str) -> tuple:
    """Invoke team/scripts/cm/cancel-rc.sh --yes as a subprocess.

    Returns (ok, output). Never raises — a failure here is recorded as an
    action error and does not abort the rest of the drill (the ledger entry
    already named every intended action; OVERWATCH's next sweep reconciles
    a partially-applied drill).
    """
    script = os.path.join(kanban_root, "scripts", "cm", "cancel-rc.sh")
    cmd = [script, "--project", project_name, "--key", version, "--yes"]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"cm-cancel-rc.sh could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def execute_drill(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    version: str,
    rc_task_ids: list,
    bundle_key: Optional[str],
    bug_keys: list,
    ship_halt_path: str,
) -> tuple:
    """Perform the five-step cancel drill. Returns (completed, errors) where
    completed is the list of action descriptions that actually succeeded and
    errors is the list of action descriptions that failed (non-fatal — every
    step is attempted regardless of an earlier step's failure, mirroring
    cm-cancel-rc.sh's own idempotent, best-effort recovery posture).
    """
    completed: list = []
    errors: list = []

    ok, output = _run_cm_cancel_rc(kanban_root, project_name, version)
    step = f"cm-cancel-rc.sh --project {project_name} --key {version} --yes"
    if ok:
        completed.append(step)
    else:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")

    for task_id in rc_task_ids:
        step = f"wontdo: {task_id}"
        try:
            wontdo_item(ctx, project_name, task_id)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")

    if bundle_key:
        step = f"close (wont-do): {bundle_key}"
        try:
            close_item(ctx, project_name, bundle_key, state="wont-do", note="OVERWATCH auto-resolution: CANCEL-AND-REBUNDLE")
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")
    else:
        errors.append("close (wont-do): no bundle intake item located — skipped")

    for bug_key in bug_keys:
        step = f"reset (open): {bug_key}"
        try:
            reset_item(ctx, project_name, bug_key)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")

    step = f"cleared ship-policy HALT: {ship_halt_path}"
    try:
        # Re-verify content immediately before removal — closes the race
        # window between evidence-gathering and execution where an operator
        # could have replaced the file's content in between.
        if classify_halt_file(ship_halt_path) == "ship-policy":
            os.remove(ship_halt_path)
            completed.append(step)
        else:
            errors.append(f"{step} SKIPPED: file no longer carries the CM header at execution time")
    except OSError as exc:
        errors.append(f"{step} FAILED: {exc}")

    return completed, errors


# ---------------------------------------------------------------------------
# The ceiling — the terminal path complementing the matched-resolution
# path above. Reads the retry counter's history (compute_bundle_family)
# and the ceiling already resolved into result.facts by
# resolution_matrix.py to produce a HALT-hard decline whose block text is
# a strictly richer briefing than a single-failure view: the bundle
# family's full attempt history.
# ---------------------------------------------------------------------------


def _is_ceiling_reached(facts) -> bool:
    """True when the HALT-hard decline is specifically because the bundle
    family's attempt count has reached (or passed) the retry ceiling, as
    opposed to any other unmatched/unknown block shape.

    Both attempt and ceiling must be known — an undeterminable attempt count
    already fails closed to attempts_below_ceiling=False (see
    resolution_matrix._fact_attempts_below_ceiling), but that is a distinct
    "insufficient evidence" decline, not "ceiling reached"; this predicate
    only fires when the resolver can actually name the number that hit the
    wall.
    """
    return (
        facts.attempt is not None
        and facts.ceiling is not None
        and facts.attempt >= facts.ceiling
    )


def _format_ceiling_block_text(facts, family: dict) -> str:
    """Build the ceiling decline's block text: the full attempt history for
    the bundle family ("attempt 3/3; prior attempts cancelled for: ...")
    — a strictly richer human briefing than a single-failure view.
    """
    cancelled_rcs = [
        str(record.get("rc") or "unknown")
        for record in family.get("history", [])
        if record.get("outcome") == "cancelled"
    ]
    history_clause = (
        f"prior attempts cancelled for: {', '.join(cancelled_rcs)}"
        if cancelled_rcs
        else "prior attempts cancelled for: none recorded"
    )
    family_key = family.get("family_key") or "untracked (no bundled cargo)"
    return (
        f"ceiling reached: attempt {facts.attempt}/{facts.ceiling}; {history_clause} "
        f"(bundle family {family_key}; trigger_class={facts.trigger_class})"
    )


def _backfill_ceiling_ledger(ledger_path: str, family_key: Optional[str]) -> list:
    """Close out the bundle family's ledger story at the ceiling: backfill
    every still-pending entry for *family_key* to Outcome=CEILING-BLOCKED
    (ledger.py's own documented terminal state for a family that never
    shipped). Never creates a new ledger entry — only the matched-resolution
    path (execute_drill's caller) writes new entries.

    Best-effort and non-fatal: a family with no ledger yet (untracked cargo,
    or a ceiling reached before any resolution was ever executed for it) or
    a ledger.py structural problem must not turn a clean HALT-hard decline
    into an unhandled exception — the decline already stands on its own;
    OVERWATCH's next sweep can reconcile a missed backfill same as it
    reconciles a killed-mid-drill execution.
    """
    if not family_key or not os.path.isfile(ledger_path):
        return []
    try:
        return backfill_family_outcome(ledger_path, family_key, "CEILING-BLOCKED")
    except LedgerError:
        return []


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_resolution_stage(project_name: str, kanban_root: str) -> ResolutionStageOutcome:
    """Evaluate the current block state for *project_name* and either execute
    the matrix-prescribed drill or decline. See module docstring for the
    full decision shape.
    """
    ctx = OpsContext(kanban_root=kanban_root)
    project_root = str(ctx.project_root(project_name))

    project_halt, global_halt = _locate_halt_files(kanban_root, project_name)
    project_class = classify_halt_file(project_halt)
    global_class = classify_halt_file(global_halt)

    if project_class == "absent" and global_class == "absent":
        # No HALT at all — this OVERWATCH firing was not triggered by a
        # ship-policy block (or any block requiring a HALT). Nothing to
        # evaluate; stay silent (every OVERWATCH firing reaches this call,
        # including the vast majority that have no ship-policy HALT to
        # consider — logging a decline for every one of those would drown
        # the action log in noise for a condition that is not a decision).
        return ResolutionStageOutcome(status=NO_OP, reason="no HALT file present")

    ship_candidates = [
        p for p, c in ((project_halt, project_class), (global_halt, global_class)) if c == "ship-policy"
    ]
    operator_candidates = [
        p for p, c in ((project_halt, project_class), (global_halt, global_class)) if c == "operator"
    ]

    if not ship_candidates:
        # A HALT is present but none of the present HALT file(s) carry the
        # CM ship-policy header — nothing for this stage to resolve. This is
        # the ordinary "operator halted the chain" case; OVERWATCH must not
        # touch it.
        reason = "HALT present but not a ship-policy HALT (no CM header found)"
        log_action(kanban_root, project_name, "resolution-stage", project_name, "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    if operator_candidates:
        # Acceptance criterion: "Only the ship-policy HALT being resolved is
        # cleared; an operator-authored HALT present alongside blocks
        # resolution with a narrated reason." An operator HALT sitting
        # alongside a ship-policy one is inviolate — decline outright,
        # before even consulting the matrix, and touch nothing.
        reason = (
            "operator-authored HALT present alongside the ship-policy HALT "
            f"({', '.join(operator_candidates)}) — declining"
        )
        log_action(kanban_root, project_name, "resolution-stage", project_name, "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    ship_halt_path = ship_candidates[0]

    release_state_md = os.path.join(project_root, "release-state.md")
    version = read_state_field(release_state_md, "Active RC")
    if version in ("none", ""):
        reason = "ship-policy HALT present but Active RC is none — nothing to resolve"
        log_action(kanban_root, project_name, "resolution-stage", project_name, "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    cfg = _read_project_cfg(project_root)
    dev_tree_path = cfg["dev_tree_path"] or None
    branch_prefix = cfg["branch_prefix"]
    rc_branch = f"{branch_prefix}rc/{version}"
    base_branch = f"{branch_prefix}main"

    tasks_dir = os.path.join(project_root, "tasks")
    release_state_json = os.path.join(project_root, "release-state", f"{version}.json")
    tester_report_path = find_tester_report(tasks_dir, version)

    evidence = BlockEvidence(
        halt_file_path=ship_halt_path,
        tester_report_path=tester_report_path,
        release_state_path=release_state_json if os.path.isfile(release_state_json) else None,
        dev_tree_path=dev_tree_path,
        target_version=version,
        rc_branch=rc_branch,
        base_branch=base_branch,
        project_tasks_dir=tasks_dir,
        project_name=project_name,
        kanban_root=kanban_root,
    )

    try:
        result = resolve_block(evidence, kanban_root=kanban_root)
    except ResolutionMatrixError as exc:
        reason = f"resolution matrix error: {exc}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    requirements_dir = str(ctx.requirements_dir(project_name))
    state_dir = os.path.join(project_root, "release-state")
    ledger_path = ledger_path_for_project(kanban_root, project_name)

    if result.resolution == HALT_HARD:
        if _is_ceiling_reached(result.facts):
            # The ceiling: no cancel/reversible action is taken —
            # the block is terminal. The only additions over a plain decline
            # are narration (the full attempt history in the block text) and
            # closing out the bundle family's ledger story.
            family = compute_bundle_family(requirements_dir, version, state_dir)
            reason = _format_ceiling_block_text(result.facts, family)
            _backfill_ceiling_ledger(ledger_path, family.get("family_key"))
        else:
            reason = (
                f"no matrix row matched (trigger_class={result.facts.trigger_class}, "
                f"findings_all_filed={result.facts.findings_all_filed}, "
                f"shipped_or_tagged={result.facts.shipped_or_tagged}, "
                f"approval_gate_present={result.facts.approval_gate_present}, "
                f"operator_commits_in_lineage={result.facts.operator_commits_in_lineage}, "
                f"attempts_below_ceiling={result.facts.attempts_below_ceiling})"
            )
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    # --- Matched: CANCEL-AND-REBUNDLE ---
    family = compute_bundle_family(requirements_dir, version, state_dir)

    bundle_key = None
    if family.get("bundle_file"):
        bundle_key = os.path.splitext(os.path.basename(family["bundle_file"]))[0]

    bug_keys = [os.path.splitext(c)[0] for c in family.get("cargo", []) if c.startswith("BUG-")]
    rc_task_ids = find_rc_task_ids(tasks_dir, version)

    attempt_from = result.facts.attempt if result.facts.attempt is not None else 1
    attempt_to = attempt_from + 1

    actions_taken = [f"cm-cancel-rc.sh --project {project_name} --key {version} --yes"]
    actions_taken += [f"wontdo: {task_id}" for task_id in rc_task_ids]
    actions_taken.append(
        f"close (wont-do): {bundle_key}" if bundle_key else "close (wont-do): none — no bundle file located"
    )
    actions_taken += [f"reset (open): {bug_key}" for bug_key in bug_keys]
    actions_taken.append(f"cleared ship-policy HALT: {ship_halt_path}")

    evidence_paths = [p for p in (ship_halt_path, tester_report_path, evidence.release_state_path) if p]

    entry = LedgerEntry(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        rc=version,
        attempt_from=attempt_from,
        attempt_to=attempt_to,
        trigger=result.facts.trigger_class,
        matrix_row=result.resolution,
        findings=bug_keys,
        actions_taken=actions_taken,
        evidence_paths=evidence_paths,
        family_key=family.get("family_key"),
    )

    # Decide-and-log: the ledger entry is written BEFORE the drill's actions
    # run ("never log-after"). A kill between this call and the actions
    # below leaves the entry present with its intended actions already
    # recorded — OVERWATCH's next sweep reconciles.
    entry_id = append_entry(ledger_path, entry)

    completed, errors = execute_drill(
        ctx=ctx,
        kanban_root=kanban_root,
        project_name=project_name,
        version=version,
        rc_task_ids=rc_task_ids,
        bundle_key=bundle_key,
        bug_keys=bug_keys,
        ship_halt_path=ship_halt_path,
    )

    reason = f"CANCEL-AND-REBUNDLE matched; ledger entry {entry_id}"
    if errors:
        reason += f"; {len(errors)} action(s) reported errors (see below)"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:cancel-and-rebundle",
        reason,
    )
    for err in errors:
        log_action(
            kanban_root,
            project_name,
            "resolution-stage",
            f"{project_name}:{version}",
            "auto-fix-error",
            err,
        )

    return ResolutionStageOutcome(
        status=EXECUTED,
        reason=reason,
        ledger_entry_id=entry_id,
        actions_taken=completed,
        action_errors=errors,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "OVERWATCH's autonomous block-resolution execution stage. "
            "Evaluates the current HALT/block state for a project and either "
            "executes the matrix-prescribed cancel-and-rebundle drill or "
            "declines, narrating the decision to the OVERWATCH action log."
        )
    )
    parser.add_argument("--project", required=True, help="Project name as registered in projects.cfg.")
    parser.add_argument("--kanban-root", required=True, help="Kanban root path.")
    args = parser.parse_args()

    outcome = run_resolution_stage(args.project, args.kanban_root)
    sys.stdout.write(f"resolution-stage: status={outcome.status} reason={outcome.reason}\n")
    if outcome.action_errors:
        for err in outcome.action_errors:
            sys.stderr.write(f"resolution-stage: action error: {err}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
