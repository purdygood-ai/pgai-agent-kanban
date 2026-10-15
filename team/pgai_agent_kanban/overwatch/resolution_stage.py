"""
resolution_stage.py — OVERWATCH's autonomous block-resolution execution stage.

Wires OVERWATCH's block handling to the resolution matrix
(pgai_agent_kanban.overwatch.resolution_matrix) and the append-only ledger
(pgai_agent_kanban.overwatch.ledger). This module is the call site both
wake provider siblings invoke, once per project, on every OVERWATCH
firing (see "run_overwatch_resolution_stage" in team/scripts/lib/
wake_project.sh), and the stage block triage hands a ship_policy_halt block
to (pgai_agent_kanban.overwatch.block_triage) — which is how a fresh
ship-policy block reaches it within seconds: the on-BLOCK nudge a wake chain
fires after releasing its repo lock runs block triage
(fire_block_triage_nudge, team/scripts/lib/wake_project.sh).

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

This module never overrides a content verdict (SOP-core.md / OVERWATCH.md
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
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Run by file path (``python3 .../resolution_stage.py``) or loaded from a file
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

from pgai_agent_kanban.cm.bundle_family import compute_bundle_family  # noqa: E402
from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.lib import ini  # noqa: E402
from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402
from pgai_agent_kanban.ops.write import close_item, delete_item, reset_item, wontdo_item  # noqa: E402
from pgai_agent_kanban.overwatch.intent import read_project_intent  # noqa: E402
from pgai_agent_kanban.overwatch.ledger import (  # noqa: E402
    LedgerEntry,
    LedgerError,
    append_entry,
    backfill_family_outcome,
    ledger_path_for_project,
    parse_entries,
)
from pgai_agent_kanban.overwatch.resolution_matrix import (  # noqa: E402
    CANCEL_AND_REBUNDLE,
    HALT_HARD,
    LANE_A,
    LANE_B,
    LANE_C,
    BlockEvidence,
    LaneClassification,
    ResolutionMatrixError,
    _CM_HALT_HEADER_RE,  # single-sourced ship-policy HALT header check
    cargo_bug_ids,
    classify_lane,
    matrix_findings,
    resolve_block,
    resolve_overwatch_resolve,
)
from pgai_agent_kanban.reports.tester_report import parse_report  # noqa: E402
from pgai_agent_kanban.task_status import read_state  # noqa: E402

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
    HALT locations this kanban uses (SOP-core.md "HALT Flags"; unwind-rc.sh
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
    try:
        parser = ini.load(cfg_path)
    except ini.IniError:
        return result
    if parser.has_option("project", "dev_tree_path"):
        result["dev_tree_path"] = parser.get("project", "dev_tree_path").strip()
    if parser.has_option("project", "branch_prefix"):
        result["branch_prefix"] = parser.get("project", "branch_prefix").strip().strip('"')
    return result


# ---------------------------------------------------------------------------
# RC task-set / TESTER-report discovery: does this task's README reference
# this RC version -- as a whole version token, never a substring, so
# v1.4.1 never matches a README that names only v1.4.10 or v1.4.1.2
# ---------------------------------------------------------------------------


def references_version(text: str, version: str) -> bool:
    """Whether *text* names *version* as a whole version token.

    The version may sit after a branch or tag prefix (``ai_v1.4.1``,
    ``ai_rc/v1.4.1``) or before a suffix that is not another version
    component (``v1.4.1-bugfix-bundle.md``, ``v1.4.1.`` ending a sentence);
    it never matches inside a longer version (``v1.4.10``, ``v1.4.1.2``,
    ``v11.4.1``).
    """
    if not version:
        return False
    pattern = r"(?<![0-9A-Za-z.])" + re.escape(version) + r"(?![0-9]|\.[0-9])"
    return re.search(pattern, text) is not None


def find_rc_task_ids(tasks_dir: str, version: str) -> list:
    """Return task IDs (any role) whose README references *version* and whose
    current state is not DONE or WONT-DO — the RC's still-active task set.
    The README must name the version as a whole token (references_version).
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
        if not references_version(readme_text, version):
            continue
        state = read_state(status_file) if os.path.isfile(status_file) else ""
        if state in ("DONE", "WONT-DO"):
            continue
        task_ids.append(entry)
    return task_ids


def find_tester_report(tasks_dir: str, version: str) -> Optional[str]:
    """Best-effort: locate the artifacts/report.md of the most recent DONE
    TESTER-* task whose README references *version* as a whole token
    (references_version). Returns None when no
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
        if not references_version(readme_text, version):
            continue
        if read_state(status_file) != "DONE":
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


def _cancel_rc_step(project_name: str, version: str) -> str:
    """The action description for the drill's cancel step — one spelling
    shared by the ledger's intended-actions list, execute_drill()'s
    completed/errors lists, and the caller's did-the-cancel-land check."""
    return f"scripts/cm/cancel-rc.sh --project {project_name} --key {version} --yes"


def _run_cm_cancel_rc(kanban_root: str, project_name: str, version: str) -> tuple:
    """Invoke team/scripts/cm/cancel-rc.sh --yes as a subprocess.

    Returns (ok, output). Never raises — a failure comes back as
    (False, output), and both callers stop there: execute_drill() runs none
    of its status reverts, and execute_lane_a_drill() neither deletes the
    bundle document nor clears the ship-policy HALT.
    """
    script = os.path.join(kanban_root, "scripts", "cm", "cancel-rc.sh")
    cmd = [script, "--project", project_name, "--key", version, "--yes"]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"scripts/cm/cancel-rc.sh could not be executed: {exc}"
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
    """Perform the five-step cancel drill, all-or-nothing on the cancel.

    Returns (completed, errors): completed lists the action descriptions
    that succeeded, errors the ones that failed or were skipped.

    Step 1, scripts/cm/cancel-rc.sh, is the gate for everything after it. The four
    later steps (wont-do the RC task set, close the bundle intake item
    wont-do, reset the cargo bugs to open, clear the ship-policy HALT) only
    make sense for an RC that really was cancelled:

      - Cancel failed (any non-zero exit, e.g. the project lock was held and
        the script reported LOCKED): no later step runs. The RC task set,
        the bundle intake item and every cargo bug keep the statuses they
        had (bundle and bugs stay "running"), so a later ship of the same
        RC still promotes them to done. The ship-policy HALT is also left
        in place: the RC is still active and still under the ship-policy
        verdict, so the HALT keeps it in front of the operator and stops
        the chain from shipping it. The next OVERWATCH sweep sees the same
        HALT and Active RC and re-runs this drill from the top. errors
        carries two entries: the cancel failure with the tail of the
        script's output, then one entry stating that the status-revert
        steps were not run and what was left unchanged.
      - Cancel succeeded: the four later steps each run best-effort and
        independently — a failure in one is recorded in errors and the rest
        are still attempted, so a partially-applied drill is fully visible
        rather than silently truncated.
    """
    completed: list = []
    errors: list = []

    ok, output = _run_cm_cancel_rc(kanban_root, project_name, version)
    step = _cancel_rc_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            "status-revert steps not run because the RC cancel failed: "
            f"{len(rc_task_ids)} RC task(s), bundle intake item "
            f"{bundle_key or 'none'} and {len(bug_keys)} cargo bug(s) left "
            f"unchanged; ship-policy HALT left in place ({ship_halt_path})"
        )
        return completed, errors
    completed.append(step)

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
# Three-lane classification — the shared record every lane
# writes, plus the Lane A recovery drill, the Lane B force-ship playbook,
# and the Lane C escalation path.
# Lane A's recovery-playbook steps (b)-(e) — unwind-rc, cancel-rc, delete
# the bundle doc, clear the ship-policy HALT — are executed here (see the
# "Lane A recovery playbook" section below), gated on step (a), the
# Fix-rewrite (pgai_agent_kanban.overwatch.fix_rewrite.
# rewrite_bug_fix_section()), already being present and
# attributed on the blamed bug: _run_lane_a() below declines steps (b)-(e)
# rather than assume the rewrite already ran. Lane B's
# force-ship playbook is executed here too — see "Lane B
# force-ship playbook" below: it forces cm/release.sh past the ship-policy stop it is
# resolving, with the matched INTENT.md entry's own text as --reason, then
# clears that ship-policy HALT on success. All of this runs ONLY when
# overwatch_resolve is true; with the flag false, run_resolution_stage()
# takes none of the code paths below: an unmatched block is only narrated
# and declined.
# ---------------------------------------------------------------------------


def _atomic_append_release_state_event(release_state_path: str, block: str) -> None:
    """Append *block* to the project's release-state.md, atomically
    (pgai_agent_kanban.lib.atomic_write: temp file in the same directory +
    fsync + os.replace). Creates the file with a minimal header if it does
    not yet exist (defensive; release-state.md is normally created by
    scripts/cm/open-rc.sh well before OVERWATCH ever fires). Never touches any
    byte already in the file — this is what makes the Decision event
    append-only: prior events, and every other section of
    release-state.md, are carried through unchanged.
    """
    existing = ""
    if os.path.isfile(release_state_path):
        with open(release_state_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    else:
        existing = "# Release State\n"

    new_content = existing.rstrip("\n") + "\n\n" + block.strip("\n") + "\n"

    os.makedirs(os.path.dirname(os.path.abspath(release_state_path)), exist_ok=True)
    atomic_write(release_state_path, new_content, prefix=".release_state_tmp_")


def _format_bullets(values: list) -> str:
    if not values:
        return "  - none"
    return "\n".join(f"  - {value}" for value in values)


def write_decision_event(
    release_state_path: str,
    *,
    timestamp: str,
    version: str,
    lane: str,
    evidence_refs: list,
    verbs_run: list,
    outcome: str,
) -> None:
    """Append one "## OVERWATCH Decision" event to *release_state_path* —
    the shared record every lane (A, B, C) writes when the three-lane
    classifier runs. Mirrors the "## Operator Override" event shape
    team/scripts/lib/cm/release_preflight.sh's
    _cm_record_operator_override_event documents (Timestamp/Version/...
    fields), extended with the lane, the evidence references, and the
    verbs run. Append-only: never edits or removes a
    prior event (see _atomic_append_release_state_event above).
    """
    block = (
        "## OVERWATCH Decision\n"
        f"Timestamp: {timestamp}\n"
        f"Version: {version}\n"
        f"Lane: {lane}\n"
        "Evidence:\n"
        f"{_format_bullets(evidence_refs)}\n"
        "Verbs Run:\n"
        f"{_format_bullets(verbs_run)}\n"
        f"Outcome: {outcome}\n"
    )
    _atomic_append_release_state_event(release_state_path, block)


# ---------------------------------------------------------------------------
# In-progress resolution marker — the
# ONLY evidence a live surface has that a lane is actively executing right
# now, as opposed to merely HALTed with nothing happening. Written
# atomically immediately before the one drill/ship subprocess call a lane
# actually runs (never before that point — a marker for a lane that never
# attempts anything would itself be the "implies a resolution that did not
# run" failure this marker exists to rule out), and
# removed immediately after write_decision_event() records the outcome, in
# both the success and error-carrying branches. Both
# pgai_agent_kanban.ops.verdict (the lane verdict behind why.sh) and
# pgai_agent_kanban.dashboard.scan_attention.scan_overwatch_section read
# this same file, so why.sh and the attention window can never disagree
# about whether a lane is currently resolving.
#
# A stale marker left behind by a kill mid-drill is a legitimate,
# intentional leftover, not a bug: it is exactly the surfaced evidence a
# silent kill should leave, mirroring the ledger's own "decide-and-log"
# ordering and its documented "OVERWATCH's next sweep reconciles a
# partially-applied drill against this record" recovery story.
# ---------------------------------------------------------------------------


def _resolving_marker_path(project_root: str) -> str:
    return os.path.join(project_root, "overwatch", "resolving.md")


def write_resolving_marker(
    project_root: str, *, lane: str, attempt: int, version: str, timestamp: str
) -> None:
    """Atomically write the in-progress resolution marker, overwriting any
    prior marker. At most one resolution runs per project at a time —
    every OVERWATCH entry point that reaches this stage (the wake's
    run_overwatch_resolution_stage, team/scripts/lib/wake_project.sh) holds
    OVERWATCH's per-project firing lock (overwatch_run_with_firing_lock,
    team/scripts/lib/overwatch_lib.sh) for the stage's duration — so
    overwriting rather than refusing on an existing marker is safe.
    """
    marker_path = _resolving_marker_path(project_root)
    state_dir = os.path.dirname(marker_path)
    os.makedirs(state_dir, exist_ok=True)
    content = (
        f"lane: {lane}\n"
        f"attempt: {attempt}\n"
        f"version: {version}\n"
        f"started_at: {timestamp}\n"
    )
    atomic_write(marker_path, content, prefix=".resolving_tmp_")


def clear_resolving_marker(project_root: str) -> None:
    """Remove the in-progress marker if present. Best-effort: a missing
    marker (already cleared, or a code path that never wrote one) is not
    an error.
    """
    marker_path = _resolving_marker_path(project_root)
    try:
        os.remove(marker_path)
    except OSError:
        pass


def _format_lane_c_read(lane_result: LaneClassification, version: str) -> str:
    """Build Lane C's one-paragraph OVERWATCH read: names the cause (why
    classification landed on the default lane) and at least one option, so
    the operator opening this block starts oriented instead of from zero.
    """
    return (
        f"OVERWATCH read: {version} is HALTed and classifies as Lane C — {lane_result.reason} "
        "Options: (1) review the cited TESTER report and its filed bugs directly and "
        "resolve manually, clearing the HALT when done; (2) if this state is deliberate, "
        "add a dated entry to this project's INTENT.md describing it in enough detail to "
        "satisfy the full-explanation match test, then re-fire OVERWATCH; or (3) if a fix "
        "landed for a genuine bad-fix regression, correct the originating bug's Fix section "
        "and let discovery re-bundle."
    )


# ---------------------------------------------------------------------------
# Lane A recovery playbook — steps (b)-(f): unwind-rc, cancel-rc,
# delete the bundle doc, clear the ship-policy HALT, and (f) no action —
# discovery re-decomposes the rewritten bug on its own clock. Step (a), the
# Fix-rewrite (pgai_agent_kanban.overwatch.fix_rewrite.
# rewrite_bug_fix_section()), is this deterministic
# stage's precondition, not its own action: _run_lane_a() below declines
# steps (b)-(e) whenever the blamed bug does not yet carry an attributed
# rewrite, rather than assume one already ran — the
# forbidden-approach/correct-direction judgment call is the reasoning
# tier's, performed out-of-band (team/scripts/overwatch-attach-fix-rewrite.sh,
# or a hand-edit carrying the same shape) against the same regression_bug_id
# before this drill is ever reached.
#
# Single-attempt guard: a Lane A execution is recorded in the shared
# auto-resolutions ledger exactly like a CANCEL-AND-REBUNDLE entry —
# matrix_row=LANE_A_MATRIX_ROW, findings=[regression_bug_id]. A SECOND HALT
# that classifies Lane A against the SAME regression_bug_id finds that
# entry already recorded and escalates to Lane C instead of running the
# drill again. "The same finding class" means, for this drill, the same
# originating bug being blamed for the regression.
# ---------------------------------------------------------------------------

LANE_A_TRIGGER = "lane-a-bad-fix-regression"
LANE_A_MATRIX_ROW = "LANE-A-RECOVERY"


def _run_unwind_rc(kanban_root: str, project_name: str, version: str) -> tuple:
    """Invoke team/scripts/unwind-rc.sh --project --key --force as a
    subprocess. --force is required for this non-interactive caller: it
    bypasses BOTH the Active-RC-version mismatch pre-flight check (this
    stage already verified Active RC == *version* before ever reaching
    Lane A — see run_resolution_stage) AND the interactive confirmation
    prompt, which would otherwise block forever with no stdin attached.
    Mirrors _run_cm_cancel_rc's contract exactly: never raises, returns
    (ok, output).
    """
    script = os.path.join(kanban_root, "scripts", "unwind-rc.sh")
    cmd = [script, "--project", project_name, "--key", version, "--force"]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"unwind-rc.sh could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def _lane_a_unwind_step(project_name: str, version: str) -> str:
    """The action description for Lane A's unwind step — one spelling shared
    by _run_lane_a()'s intended-actions list, execute_lane_a_drill()'s
    completed/errors lists, and _run_lane_a()'s did-the-unwind-land check."""
    return f"unwind-rc.sh --project {project_name} --key {version} --force"


def _lane_a_cancel_step(project_name: str, version: str) -> str:
    """The action description for Lane A's cancel step — shared exactly as
    _lane_a_unwind_step() is."""
    return f"cancel-rc.sh --project {project_name} --key {version} --yes"


def _resolve_current_bundle_key(
    project_root: str, version: str, bundle_key: Optional[str]
) -> Optional[str]:
    """Resolve the requirements/ item key Lane A's bundle-deletion step
    should delete. unwind-rc.sh's own Step f may already have renamed the
    bundle file to "<stem>.SUPERSEDED-on-cancel-<ts>.md" (the same glob
    unwind-rc.sh itself uses to detect an already-superseded file on a
    re-run) by the time this function runs — this is a read-only directory
    listing, never a mutation, so it stays inside the "every mutation goes
    through an operator verb or the ops write layer" constraint. Falls back
    to *bundle_key* unchanged when no superseded variant is found on disk.
    Returns None when neither exists — nothing to delete.
    """
    requirements_dir = os.path.join(project_root, "requirements")
    if os.path.isdir(requirements_dir):
        for name in sorted(os.listdir(requirements_dir)):
            if (
                name.startswith(f"{version}-")
                and ".SUPERSEDED-on-cancel-" in name
                and name.endswith(".md")
            ):
                return name[: -len(".md")]
    if bundle_key and os.path.isfile(os.path.join(requirements_dir, f"{bundle_key}.md")):
        return bundle_key
    return None


def _lane_a_steps_not_run_note(
    failed_step: str,
    skipped_steps: list,
    project_root: str,
    version: str,
    bundle_key: Optional[str],
    ship_halt_path: str,
) -> str:
    """The second errors entry execute_lane_a_drill() returns when an RC
    step fails: names the step that failed, every later step that was not
    run, and the bundle document and ship-policy HALT left in place. The
    bundle document is named by its current on-disk key (unwind-rc.sh may
    already have renamed it), read-only — this never touches the file.
    """
    current_key = _resolve_current_bundle_key(project_root, version, bundle_key)
    return (
        f"later steps not run because the {failed_step} failed: "
        f"{', '.join(skipped_steps)}; bundle doc {current_key or 'none located'} "
        f"left in place; ship-policy HALT left in place ({ship_halt_path})"
    )


def execute_lane_a_drill(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    bundle_key: Optional[str],
    ship_halt_path: str,
) -> tuple:
    """Perform Lane A recovery-playbook steps (b)-(e), in order: unwind-rc,
    cancel-rc, delete the bundle doc, clear the ship-policy HALT.

    Returns (completed, errors) — same shape as execute_drill(): completed
    lists the action descriptions that succeeded, errors the ones that
    failed or were not run.

    The drill is all-or-nothing on its two RC steps, unwind-rc then
    cancel-rc, exactly as execute_drill() is on its cancel. Deleting the
    bundle document and clearing the ship-policy HALT only make sense for
    an RC that really was unwound and cancelled; done to an RC that is
    still active, they leave it with no bundle record and nothing stopping
    it from shipping.

      - unwind-rc failed (any non-zero exit, e.g. the project lock was held
        and the script reported LOCKED): nothing after it runs — not
        cancel-rc either. cancel-rc resets the Active RC field, and once
        that field reads none this stage declines every later sweep, so
        cancelling an RC whose unwind did not complete would strand its
        task set, queue entries and bundle half-reverted with nothing left
        to drive a retry.
      - cancel-rc failed: the bundle-document deletion and the HALT clear
        do not run.

    On either failure the bundle document and the ship-policy HALT are left
    exactly where they were, so the RC stays in front of the operator and
    the chain cannot ship it. errors carries two entries: the failed step
    with the tail of the script's output, then one entry naming the later
    steps that were not run and what was left in place.

    When both RC steps succeed, the bundle-document deletion and the HALT
    clear each run best-effort and independently: a failure in one is
    recorded in errors and the other is still attempted. Step (f)
    (discovery re-decomposing) is deliberately no action here — discovery
    picks the rewritten bug up on its own clock.
    """
    completed: list = []
    errors: list = []

    ok, output = _run_unwind_rc(kanban_root, project_name, version)
    step = _lane_a_unwind_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            _lane_a_steps_not_run_note(
                "RC unwind",
                [_lane_a_cancel_step(project_name, version), "delete bundle doc", "clear ship-policy HALT"],
                project_root,
                version,
                bundle_key,
                ship_halt_path,
            )
        )
        return completed, errors
    completed.append(step)

    ok, output = _run_cm_cancel_rc(kanban_root, project_name, version)
    step = _lane_a_cancel_step(project_name, version)
    if not ok:
        errors.append(f"{step} FAILED: {output.strip()[-500:]}")
        errors.append(
            _lane_a_steps_not_run_note(
                "RC cancel",
                ["delete bundle doc", "clear ship-policy HALT"],
                project_root,
                version,
                bundle_key,
                ship_halt_path,
            )
        )
        return completed, errors
    completed.append(step)

    resolved_key = _resolve_current_bundle_key(project_root, version, bundle_key)
    if resolved_key:
        step = f"delete bundle doc: {resolved_key}"
        try:
            delete_item(ctx, project_name, resolved_key, force=True)
            completed.append(step)
        except OpsError as exc:
            errors.append(f"{step} FAILED: {exc}")
    else:
        errors.append("delete bundle doc: no bundle intake item located — skipped")

    step = f"cleared ship-policy HALT: {ship_halt_path}"
    try:
        # Re-verify content immediately before removal — the same race-
        # closing re-check execute_drill()'s own HALT-clear step performs.
        if classify_halt_file(ship_halt_path) == "ship-policy":
            os.remove(ship_halt_path)
            completed.append(step)
        else:
            errors.append(f"{step} SKIPPED: file no longer carries the CM header at execution time")
    except OSError as exc:
        errors.append(f"{step} FAILED: {exc}")

    return completed, errors


def _lane_a_prior_attempts(ledger_path: str, regression_bug_id: Optional[str]) -> list:
    """Every ledger entry already recorded for a Lane A recovery attempt on
    *regression_bug_id* — entries tagged matrix_row=LANE_A_MATRIX_ROW
    carrying findings=[regression_bug_id]. File (append) order, oldest
    first. Empty on the first attempt: no ledger yet, or no entry for this
    exact bug id.
    """
    if not regression_bug_id or not os.path.isfile(ledger_path):
        return []
    return [
        entry
        for entry in parse_entries(ledger_path)
        if entry.get("matrix_row") == LANE_A_MATRIX_ROW
        and regression_bug_id in (entry.get("findings") or [])
    ]


def _format_lane_a_escalation(
    lane_result: LaneClassification, prior_attempt: dict, version: str
) -> str:
    """Build the single-attempt guard's Lane C escalation text: names both
    attempts' evidence so the operator sees the whole story, not the
    latest slice of it.
    """
    prior_ts = prior_attempt.get("timestamp") or "unknown timestamp"
    prior_rc = prior_attempt.get("rc") or "unknown RC"
    prior_evidence = ", ".join(prior_attempt.get("evidence_paths") or []) or "none recorded"
    return (
        f"OVERWATCH read: {version} HALTed again on the same finding class as a prior Lane A "
        f"attempt — both trace to {lane_result.regression_bug_id}. Single-attempt guard: Lane A "
        f"already ran once, at {prior_ts} for {prior_rc} (evidence: {prior_evidence}); it does "
        "not run a second time on the same finding class. Escalating to Lane C with both "
        "attempts' evidence. Options: (1) review both TESTER reports and the rewritten Fix "
        f"section on {lane_result.regression_bug_id} directly, then resolve manually; (2) hand-"
        "author a corrected Fix and let discovery re-bundle; (3) if this state is deliberate, "
        "add a dated entry to this project's INTENT.md and re-fire OVERWATCH."
    )


def _format_lane_a_rewrite_missing(
    lane_result: LaneClassification,
    bug_path: Optional[str],
    bugs_dir: str,
    version: str,
    kanban_root: str,
    project_name: str,
) -> str:
    """Build the dual-channel refusal text for the rewrite-precondition
    guard (see _run_lane_a's CENSUS-ANCHOR comment): a stable machine key,
    a plain-language explanation, and a runnable remedy naming the CLI
    that actually performs step (a).

    The remedy substitutes every value this stage already resolved
    (--project, --kanban-root, --bug-id) verbatim — it names
    --forbidden-approach/--correct-direction/--ground-truth-ref only by
    flag, never with an angle-bracket placeholder, because their content is
    the reasoning tier's judgment call, not a value this deterministic
    stage could ever resolve on the caller's behalf (lint_runnable_remedy.py
    flags a literal '<...>' template slot; naming a flag without one is not
    a template — the caller still has to type real text after it).
    """
    from pgai_agent_kanban.lib.error_contract import format_error_contract

    bug_ref = bug_path or f"(no file matching {lane_result.regression_bug_id!r} found under {bugs_dir})"
    bug_id_arg = lane_result.regression_bug_id or "(unresolved — inspect the TESTER report directly)"
    return format_error_contract(
        "overwatch.lane-a-rewrite-missing",
        f"{version} classifies as Lane A (regression traced to "
        f"{lane_result.regression_bug_id}) but the blamed bug does not yet carry an "
        f"attributed OVERWATCH Fix rewrite ({bug_ref}) — the recovery drill's steps "
        "(b)-(e) do not run without it.",
        "attach the rewrite first: team/scripts/overwatch-attach-fix-rewrite.sh "
        f"--project {project_name} --kanban-root {kanban_root} --bug-id {bug_id_arg}, adding "
        "--forbidden-approach, --correct-direction, and one or more --ground-truth-ref flags "
        "naming the actual failed approach, the actual correct fix, and a verbatim ground-truth "
        "reference respectively (run the script with --help for each flag's contract) — or "
        "hand-append the same '## Fix (OVERWATCH rewrite)' shape directly. Then let the next "
        "OVERWATCH firing re-classify this block.",
    )


def _run_lane_a(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    lane_result: LaneClassification,
    family: dict,
    ship_halt_path: str,
    evidence_refs: list,
    timestamp: str,
) -> ResolutionStageOutcome:
    """Lane A's full handling: the single-attempt guard first, then — on a
    first attempt only — the recovery drill itself. Called only from
    _run_lane_classification when lane_result.lane == LANE_A.
    """
    ledger_path = ledger_path_for_project(kanban_root, project_name)
    prior_attempts = _lane_a_prior_attempts(ledger_path, lane_result.regression_bug_id)

    if prior_attempts:
        # Single-attempt guard: escalate to Lane C rather than run the
        # drill a second time. No operator verb is invoked on this path —
        # the block is left exactly as classify_lane and the earlier
        # matrix consultation found it.
        prior = prior_attempts[-1]
        combined_evidence = list(evidence_refs)
        for path in prior.get("evidence_paths") or []:
            if path not in combined_evidence:
                combined_evidence.append(path)

        outcome_text = _format_lane_a_escalation(lane_result, prior, version)
        write_decision_event(
            os.path.join(project_root, "release-state.md"),
            timestamp=timestamp,
            version=version,
            lane=LANE_C,
            evidence_refs=combined_evidence,
            verbs_run=["Needs Human raised (Lane A single-attempt guard escalation)"],
            outcome=outcome_text,
        )
        reason = f"Lane A single-attempt guard escalated to Lane C: {outcome_text}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    # Rewrite precondition: step (a) — deferred import to avoid a circular
    # top-level import (fix_rewrite.py imports log_action from this module;
    # see fix_rewrite.py's own module docstring for the full contract).
    from pgai_agent_kanban.overwatch.fix_rewrite import find_bug_file, has_attributed_rewrite

    bugs_dir = os.path.join(project_root, "bugs")
    bug_path = find_bug_file(bugs_dir, lane_result.regression_bug_id) if lane_result.regression_bug_id else None
    rewrite_present = False
    if bug_path:
        try:
            rewrite_present = has_attributed_rewrite(open(bug_path, "r", encoding="utf-8").read())
        except OSError:
            rewrite_present = False

    if not rewrite_present:
        # BLOCK-WORTHY: the destructive drill (unwind/cancel/delete-bundle/
        # clear-HALT) must never run without the reasoning tier's attributed
        # Fix rewrite already on the blamed bug — an approval-by-design
        # invariant (team/roles/OVERWATCH.md, "Performing that rewrite is
        # the reasoning tier's duty, not the drill's"), not a judgment call
        # this deterministic stage can make for itself. No ledger entry is
        # written on this path — a decline never counts as an "attempt" the
        # single-attempt guard above should later see, so the next firing
        # re-checks freely once the rewrite is attached.
        # CENSUS-ANCHOR: lane-a-rewrite-precondition-01
        outcome_text = _format_lane_a_rewrite_missing(
            lane_result, bug_path, bugs_dir, version, kanban_root, project_name
        )
        write_decision_event(
            os.path.join(project_root, "release-state.md"),
            timestamp=timestamp,
            version=version,
            lane=LANE_A,
            evidence_refs=evidence_refs,
            verbs_run=["declined pending attributed Fix rewrite (step (a) precondition unmet)"],
            outcome=outcome_text,
        )
        reason = f"Lane A declined — attributed Fix rewrite precondition unmet: {outcome_text}"
        log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
        return ResolutionStageOutcome(status=DECLINED, reason=reason)

    bundle_key = None
    if family.get("bundle_file"):
        bundle_key = os.path.splitext(os.path.basename(family["bundle_file"]))[0]

    intended_actions = [
        _lane_a_unwind_step(project_name, version),
        _lane_a_cancel_step(project_name, version),
        f"delete bundle doc: {bundle_key}" if bundle_key else "delete bundle doc: none — no bundle file located",
        f"cleared ship-policy HALT: {ship_halt_path}",
    ]

    entry = LedgerEntry(
        timestamp=timestamp,
        rc=version,
        attempt_from=0,
        attempt_to=1,
        trigger=LANE_A_TRIGGER,
        matrix_row=LANE_A_MATRIX_ROW,
        findings=[lane_result.regression_bug_id] if lane_result.regression_bug_id else [],
        actions_taken=intended_actions,
        evidence_paths=evidence_refs,
        family_key=family.get("family_key"),
    )
    # Decide-and-log: the ledger entry is written BEFORE the drill's actions
    # run, naming everything the drill is about to do — same "never
    # log-after" ordering run_resolution_stage's CANCEL-AND-REBUNDLE path
    # uses (see that function's own comment on this).
    entry_id = append_entry(ledger_path, entry)

    # In-progress marker: written only now that the drill is actually about
    # to run (never earlier — the single-attempt-guard escalation branch
    # above never reaches this line, so it never writes one). Cleared right
    # after write_decision_event() below records the outcome, in both the
    # clean and errors-present branches.
    write_resolving_marker(
        project_root,
        lane=LANE_A,
        attempt=entry.attempt_to,
        version=version,
        timestamp=timestamp,
    )

    completed, errors = execute_lane_a_drill(
        ctx=ctx,
        kanban_root=kanban_root,
        project_name=project_name,
        project_root=project_root,
        version=version,
        bundle_key=bundle_key,
        ship_halt_path=ship_halt_path,
    )

    if errors:
        outcome_text = (
            f"Lane A recovery drill executed for regression traced to "
            f"{lane_result.regression_bug_id} with {len(errors)} action(s) reporting errors: "
            + "; ".join(errors)
        )
    else:
        outcome_text = (
            f"Lane A recovery drill executed cleanly for regression traced to "
            f"{lane_result.regression_bug_id}: RC unwound and cancelled, bundle doc deleted, "
            "ship-policy HALT cleared."
        )

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=LANE_A,
        evidence_refs=evidence_refs,
        verbs_run=completed,
        outcome=outcome_text,
    )
    clear_resolving_marker(project_root)

    reason = f"Lane A recovery drill executed; ledger entry {entry_id}"
    # The drill stops at a failed RC step. Say which one on the summary line
    # itself, so an operator reading only this line knows why the bundle
    # document and the HALT are still there.
    for rc_step, label in (
        (_lane_a_unwind_step(project_name, version), "RC unwind"),
        (_lane_a_cancel_step(project_name, version), "RC cancel"),
    ):
        if rc_step not in completed:
            reason += (
                f"; {label} FAILED, later steps not run (bundle doc and "
                "ship-policy HALT left in place)"
            )
            break
    if errors:
        reason += f"; {len(errors)} action(s) reported errors (see below)"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:lane-a-recovery",
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
# Lane B force-ship playbook — the one action Lane B's own
# classification (pgai_agent_kanban.overwatch.resolution_matrix.classify_lane,
# LANE_B branch) prescribes: force cm/release.sh past the ship-policy stop it
# is resolving, with the matched INTENT.md entry's own text as --reason
# (never a generated summary of it), then clear that ship-policy HALT on
# success. No ledger entry is written here — Lane B is a one-time ship, not
# a recyclable bundle family the retry ceiling tracks, so
# pgai_agent_kanban.overwatch.ledger's schema (attempt/family bookkeeping
# built for CANCEL-AND-REBUNDLE) does not apply. The "## Operator Override"
# event cm/release.sh itself writes (team/scripts/lib/cm/
# release_operator_override.sh, _cm_apply_matrix_trigger) and the
# "## OVERWATCH Decision" event write_decision_event() appends below are
# Lane B's complete permanent record.
# ---------------------------------------------------------------------------


def _run_cm_release_force(kanban_root: str, project_name: str, reason: str) -> tuple:
    """Invoke team/scripts/cm/release.sh --project <p> --force --reason
    <reason> --overwatch-lane-b-ship as a subprocess.

    --overwatch-lane-b-ship is the discriminator release.sh's own
    operator-force-wake-context refusal narrowly admits; passing
    it here proves nothing by itself — release.sh independently
    re-verifies *reason* against the resolved project's live INTENT.md
    (its own "_cm_release_lane_b_intent_verified" helper, reading through
    this same package's pgai_agent_kanban.overwatch.intent.parse_entries)
    before honouring the override. See release.sh's
    "operator-force-wake-context-01" / "overwatch-lane-b-intent-unmatched-01"
    CENSUS-ANCHOR sites and docs/blocking-site-census.md for the full
    contract.

    No --project version argument is passed: release.sh derives the
    version to release from the project's own release-state.md "## Active
    RC" field at its own preflight, exactly as an unforced invocation does
    — this call relies on that field still naming the same RC this stage
    is resolving, which holds because nothing between the original
    ship-policy HALT and this call has touched release-state.md.

    PGAI_AGENT_WAKE_ACTIVE is deliberately NOT set here: it is inherited
    from the ambient environment exactly like _run_unwind_rc /
    _run_cm_cancel_rc's own env handling, because this stage only ever
    runs inside an OVERWATCH wake, which already carries the signal.

    Never raises; returns (ok, output).
    """
    script = os.path.join(kanban_root, "scripts", "cm", "release.sh")
    cmd = [
        script,
        "--project", project_name,
        "--force",
        "--reason", reason,
        "--overwatch-lane-b-ship",
    ]
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = kanban_root
    env["KANBAN_ROOT"] = kanban_root
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    except OSError as exc:
        return False, f"cm/release.sh --overwatch-lane-b-ship could not be executed: {exc}"
    ok = result.returncode == 0
    output = (result.stdout or "") + (result.stderr or "")
    return ok, output


def _run_lane_b(
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    lane_result: LaneClassification,
    ship_halt_path: str,
    evidence_refs: list,
    timestamp: str,
) -> ResolutionStageOutcome:
    """Lane B's full handling: force-ship through cm/release.sh, then clear
    the ship-policy HALT this stage is resolving on success. Called only
    from _run_lane_classification when lane_result.lane == LANE_B.

    No entry means no force, ever, from OVERWATCH's own judgment: this
    function is only ever reached with lane_result.intent_declaration set,
    because classify_lane() only returns LANE_B when
    find_lane_b_declaration() found a fully-explaining entry — there is no
    path here that invents a reason.
    """
    # In-progress marker: Lane B has no retry ceiling / attempt bookkeeping
    # (module docstring above — "a one-time ship, not a recyclable bundle
    # family the retry ceiling tracks"), so attempt is always 1 here. Written
    # right before the one subprocess call this playbook makes; cleared
    # right after write_decision_event() records the outcome below, in both
    # the ok and FAILED branches.
    write_resolving_marker(
        project_root, lane=LANE_B, attempt=1, version=version, timestamp=timestamp
    )

    reason_text = lane_result.intent_declaration or ""
    ok, output = _run_cm_release_force(kanban_root, project_name, reason_text)
    verb = (
        f"cm/release.sh --project {project_name} --force --reason "
        "<INTENT.md entry, verbatim> --overwatch-lane-b-ship"
    )

    if ok:
        # The ship-policy HALT this stage is resolving was written by an
        # EARLIER, unforced release.sh invocation (the one that originally
        # HALTed) — the forced invocation above never touches it, so it is
        # still on disk after a successful ship and must be cleared here.
        # Re-verify content immediately before removal — the same
        # race-closing re-check execute_drill() / execute_lane_a_drill()'s
        # own HALT-clear step performs.
        halt_step = f"cleared ship-policy HALT: {ship_halt_path}"
        try:
            if classify_halt_file(ship_halt_path) == "ship-policy":
                os.remove(ship_halt_path)
            else:
                halt_step += " SKIPPED: file no longer carries the CM header at execution time"
        except OSError as exc:
            halt_step += f" FAILED: {exc}"
        verbs_run = [verb, halt_step]
        outcome = (
            "classified Lane B (evidence fully explained by a declared INTENT.md entry); "
            f"forced ship succeeded — reason recorded verbatim: {reason_text!r}."
        )
        status = EXECUTED
        action_errors: list = []
    else:
        verbs_run = [f"{verb} FAILED"]
        outcome = (
            "classified Lane B (evidence fully explained by a declared INTENT.md entry); "
            f"forced ship FAILED: {output.strip()[-500:]}"
        )
        status = DECLINED
        action_errors = [output.strip()[-500:]]

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=LANE_B,
        evidence_refs=evidence_refs,
        verbs_run=verbs_run,
        outcome=outcome,
    )
    clear_resolving_marker(project_root)

    reason = f"lane {LANE_B} classified: {lane_result.reason}; ship {'succeeded' if ok else 'FAILED'}"
    log_action(
        kanban_root,
        project_name,
        "resolution-stage",
        f"{project_name}:{version}",
        "auto-fix:lane-b-ship" if ok else "declined",
        reason,
    )
    return ResolutionStageOutcome(
        status=status,
        reason=reason,
        actions_taken=verbs_run if ok else [],
        action_errors=action_errors,
    )


def _run_lane_classification(
    ctx: OpsContext,
    kanban_root: str,
    project_name: str,
    project_root: str,
    version: str,
    evidence: BlockEvidence,
    ship_halt_path: str,
    tester_report_path: Optional[str],
) -> ResolutionStageOutcome:
    """Gather Lane A/B/C evidence, classify, record the shared Decision
    event, and execute Lane C's escalation. Called from run_resolution_stage
    only when the pre-existing matrix consultation declined to HALT_HARD,
    the block is not at the retry ceiling, and overwatch_resolve is true.
    """
    findings = matrix_findings(
        parse_report(pathlib.Path(tester_report_path))
    ) if tester_report_path and os.path.isfile(tester_report_path) else []

    requirements_dir = str(ctx.requirements_dir(project_name))
    state_dir = os.path.join(project_root, "release-state")
    family = compute_bundle_family(requirements_dir, version, state_dir)
    cargo_ids = cargo_bug_ids(family.get("cargo", []))

    intent_entries = read_project_intent(kanban_root, project_name)
    intent_declarations = [entry.declaration for entry in intent_entries]

    evidence_texts = [finding.get("body") for finding in findings]
    evidence_texts += [evidence.target_version, evidence.rc_branch, evidence.base_branch]

    lane_result = classify_lane(findings, cargo_ids, intent_declarations, evidence_texts)

    intent_path = os.path.join(project_root, "INTENT.md")
    evidence_refs = [
        p for p in (ship_halt_path, tester_report_path, evidence.release_state_path, intent_path) if p
    ]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if lane_result.lane == LANE_A:
        return _run_lane_a(
            ctx=ctx,
            kanban_root=kanban_root,
            project_name=project_name,
            project_root=project_root,
            version=version,
            lane_result=lane_result,
            family=family,
            ship_halt_path=ship_halt_path,
            evidence_refs=evidence_refs,
            timestamp=timestamp,
        )

    if lane_result.lane == LANE_B:
        return _run_lane_b(
            kanban_root=kanban_root,
            project_name=project_name,
            project_root=project_root,
            version=version,
            lane_result=lane_result,
            ship_halt_path=ship_halt_path,
            evidence_refs=evidence_refs,
            timestamp=timestamp,
        )

    verbs_run = ["Needs Human raised (existing default — no new mechanism)"]
    outcome = _format_lane_c_read(lane_result, version)

    write_decision_event(
        os.path.join(project_root, "release-state.md"),
        timestamp=timestamp,
        version=version,
        lane=lane_result.lane,
        evidence_refs=evidence_refs,
        verbs_run=verbs_run,
        outcome=outcome,
    )

    reason = f"lane {lane_result.lane} classified: {lane_result.reason}"
    log_action(kanban_root, project_name, "resolution-stage", f"{project_name}:{version}", "declined", reason)
    return ResolutionStageOutcome(status=DECLINED, reason=reason)


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
        branch_prefix=branch_prefix,
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
            # An unmatched (non-ceiling) block gets a second look —
            # the three-lane classifier — but ONLY when this project has
            # opted in via overwatch_resolve. A malformed config value fails
            # closed to False rather than raising here: this call must never
            # grant new autonomous authority from a config-read error, and
            # the decline narration below already covers that path.
            try:
                overwatch_resolve = resolve_overwatch_resolve(project_name, kanban_root)
            except ResolutionMatrixError:
                overwatch_resolve = False

            if overwatch_resolve:
                return _run_lane_classification(
                    ctx=ctx,
                    kanban_root=kanban_root,
                    project_name=project_name,
                    project_root=project_root,
                    version=version,
                    evidence=evidence,
                    ship_halt_path=ship_halt_path,
                    tester_report_path=tester_report_path,
                )

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

    actions_taken = [_cancel_rc_step(project_name, version)]
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
    if _cancel_rc_step(project_name, version) not in completed:
        # The drill stopped at the cancel. Say so on the summary line itself
        # so an operator reading only this line knows why nothing reverted.
        reason += (
            "; RC cancel FAILED, status-revert steps not run (RC task set, "
            "bundle and cargo bug statuses unchanged; ship-policy HALT left "
            "in place)"
        )
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
    """Run the resolution stage once for ``--project`` under ``--kanban-root``.

    Prints ``resolution-stage: status=<status> reason=<reason>`` and any action
    errors on stderr. Exit codes: 0 whatever the stage decided (the outcome is
    in the printed status); 2 usage error (argparse).
    """
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
