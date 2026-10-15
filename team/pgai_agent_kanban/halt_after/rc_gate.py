#!/usr/bin/env python3
"""
rc_gate.py — RC-close gate for HALT-AFTER promotion.

drain.check_drain() answers "is the per-event condition satisfied right
now"; this module answers the separate question "may that satisfaction act
right now". The two are decoupled on purpose: a role-token drain (coder,
writer, tester, cm, pm) can become satisfied at any point in an open RC --
CODER's queue can go empty for a moment between tasks while a release is
still in flight on the branch -- and promoting on that moment would freeze
the chain with a half-built RC and no agent left to finish it. That is
exactly the failure this module exists to prevent.

The invariant is universal across every HALT-AFTER token: bare ``rc``,
versioned ``rc:vX.Y.Z``, and all five role tokens wait for the project's
Active RC to read "none" before a satisfied drain is allowed to promote.
No token promotes immediately while an RC is open.

Deferring a satisfied drain must never lose it.  Once a wake evaluation
observes the drain condition true while an RC is open, it writes a latch
file (``HALT-AFTER.rc-gate``, sibling to the sentinel itself) recording the
verbatim token that was satisfied.  Every subsequent evaluation that finds
the latch present skips re-deriving the condition entirely -- it only
checks whether the RC has since closed -- so a role-token drain that later
flips back to "not satisfied" (a new task lands for that role) cannot undo
a promotion that already earned its place in line.

A latch is honoured only while the HALT-AFTER sentinel it was latched
against still names the same token.  When the operator re-arms HALT-AFTER
with a different token, or removes it, the latch is stale: evaluate()
removes it and evaluates the current sentinel afresh, so a stale token never
promotes a HALT at the wrong time.  Re-arming through the operator verb
(``ops.write.halt_after``) clears the latch at write time, and the CLI entry
point (``python3 -m halt_after``) clears it whenever the sentinel is absent
or unreadable as a token; ``clear_latch`` is the one way both do it.
Otherwise the latch is removed only by promote() succeeding.

Two call sites invoke evaluate():

  - The wake loop, once per project per wake
    (team/scripts/lib/wake_project.sh, _check_halt_after_for_project),
    via the ``python3 -m halt_after`` CLI.
  - CM, synchronously at ship time (team/scripts/lib/cm/release_finalize.sh,
    Step 12), right after Active RC is rewritten to "none" and Last
    Released is updated.  The version-pinned rc:vX.Y.Z token in particular
    needs this synchronous spend: a follow-on RC can open before the wake
    loop's next firing, and once it does, Active RC reads a new version
    again and the wake-loop's own RC-close window has already closed
    ("Back-to-back RCs do not defer the drain" -- team/SOP.md, HALT-AFTER
    family). Calling the exact same evaluator from both places keeps this
    to one implementation instead of two that could drift.
"""

import dataclasses
import enum
import logging
import pathlib

from pgai_agent_kanban.cm.read_state_field import read_state_field

from .drain import check_drain
from .promote import promote
from .token import parse_token

logger = logging.getLogger(__name__)

_LATCH_FILENAME = "HALT-AFTER.rc-gate"
_SENTINEL_FILENAME = "HALT-AFTER"


class RcGateOutcome(enum.Enum):
    """The three things one evaluate() call can conclude."""

    PROMOTED = "promoted"
    DEFERRED = "deferred"
    NOT_DRAINED = "not-drained"


@dataclasses.dataclass(frozen=True)
class RcGateResult:
    """The outcome of one evaluate() call, plus the event it acted on.

    ``event`` is the canonical token that was (or will be, once the RC
    closes) promoted -- the latched token when a latch already existed,
    otherwise the token check_drain was called with.
    """

    outcome: RcGateOutcome
    event: str


def _latch_path(project_root: "pathlib.Path") -> "pathlib.Path":
    return project_root / _LATCH_FILENAME


def clear_latch(project_root: "str | pathlib.Path") -> bool:
    """Remove the rc-gate latch beside *project_root*'s HALT-AFTER, if any.

    Returns True when a latch was removed, False when there was none or it
    could not be removed (the failure is logged; a latch that survives is
    still checked against the sentinel by evaluate()).
    """
    latch_path = _latch_path(pathlib.Path(project_root))
    try:
        latch_path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("rc_gate: could not remove latch file %s: %s", latch_path, exc)
        return False
    return True


def _current_sentinel_token(project_root: "pathlib.Path") -> "str | None":
    """The canonical token the HALT-AFTER sentinel names now, or None.

    None when the sentinel is absent, unreadable, or not a valid token.
    """
    try:
        content = (project_root / _SENTINEL_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_token(content)


def rc_is_open(release_state_root: "str | pathlib.Path") -> bool:
    """Return True when the project has an in-flight RC.

    Reads ``## Active RC`` from *release_state_root*/release-state.md.
    Anything other than the literal (case-insensitive) ``none`` counts as
    open -- including a missing file or missing field, which
    ``read_state_field`` already normalises to the string ``"none"`` (so a
    project with no release-state.md at all reads as RC-closed, matching
    the conservative default drain.py itself uses for "nothing has shipped
    yet").
    """
    release_state_root = pathlib.Path(release_state_root)
    active_rc = read_state_field(str(release_state_root / "release-state.md"), "Active RC")
    return active_rc.strip().lower() != "none"


def evaluate(
    project_root: "str | pathlib.Path",
    release_state_root: "str | pathlib.Path",
    event: str,
) -> RcGateResult:
    """Evaluate one HALT-AFTER wake cycle for *event*, gated on RC close.

    Args:
        project_root:       Filesystem path to the project root -- where the
                            HALT-AFTER sentinel, the HALT file, and the
                            rc-gate latch file all live.
        release_state_root: Filesystem path to the directory containing
                            release-state.md (may differ from project_root
                            for a root-scope sentinel; see drain.check_drain
                            for the same split).
        event:              The canonical token parsed from the HALT-AFTER
                            file's contents (token.parse_token's output).
                            Ignored when a latch from an earlier evaluation
                            is already on disk and still matches the
                            sentinel -- the latched token wins, per the
                            "deferring must not lose it" invariant. A latch
                            whose token is not the one the HALT-AFTER file
                            names now (re-armed or removed) is stale: it is
                            removed and *event* is evaluated afresh.

    Returns:
        An RcGateResult naming what happened and which event it concerns.

    Raises:
        ValueError: propagated from check_drain for an unsupported token
                    (only when no latch is present -- a latched call never
                    re-derives the condition, so it never re-raises this).
    """
    project_root = pathlib.Path(project_root)
    release_state_root = pathlib.Path(release_state_root)
    latch_path = _latch_path(project_root)

    if latch_path.exists():
        latched_event = latch_path.read_text(encoding="utf-8").strip() or event
        current_token = _current_sentinel_token(project_root)
        if current_token != latched_event:
            logger.info(
                "rc_gate.evaluate: latch for event=%r is stale — HALT-AFTER now names %r — "
                "removing the latch and evaluating the current sentinel",
                latched_event,
                current_token if current_token is not None else "nothing",
            )
            clear_latch(project_root)
            return _evaluate_unlatched(project_root, release_state_root, event)
        if rc_is_open(release_state_root):
            logger.info(
                "rc_gate.evaluate: drain already latched for event=%r; "
                "RC still open — promotion stays deferred",
                latched_event,
            )
            return RcGateResult(RcGateOutcome.DEFERRED, latched_event)
        logger.info(
            "rc_gate.evaluate: RC closed — promoting latched drain for event=%r",
            latched_event,
        )
        promote(project_root, latched_event)
        try:
            latch_path.unlink()
        except OSError as exc:
            logger.warning(
                "rc_gate.evaluate: could not remove latch file %s after promotion: %s",
                latch_path,
                exc,
            )
        return RcGateResult(RcGateOutcome.PROMOTED, latched_event)

    return _evaluate_unlatched(project_root, release_state_root, event)


def _evaluate_unlatched(
    project_root: "pathlib.Path",
    release_state_root: "pathlib.Path",
    event: str,
) -> RcGateResult:
    """evaluate() with no (valid) latch on disk: derive the drain afresh."""
    latch_path = _latch_path(project_root)
    drained = check_drain(event, project_root, release_state_root)
    if not drained:
        return RcGateResult(RcGateOutcome.NOT_DRAINED, event)

    if rc_is_open(release_state_root):
        logger.info(
            "rc_gate.evaluate: drain satisfied for event=%r but Active RC is "
            "open — deferring promotion to RC close and latching the result",
            event,
        )
        try:
            latch_path.write_text(event + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "rc_gate.evaluate: could not write latch file %s: %s — "
                "the satisfied drain may be lost if the RC closes before "
                "the condition is re-observed satisfied",
                latch_path,
                exc,
            )
        return RcGateResult(RcGateOutcome.DEFERRED, event)

    promote(project_root, event)
    return RcGateResult(RcGateOutcome.PROMOTED, event)
