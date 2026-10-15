"""
block_stage_deferral.py — how OVERWATCH's block triage hands a
``ship_policy_halt`` block to the existing resolution stage
(:mod:`pgai_agent_kanban.overwatch.resolution_stage`) and reads back what
the stage decided.

Deferral grants no new authority. The stage decides, under its own rules
and its own retry ceiling, exactly as it does on every OVERWATCH wake; this
module only calls it and reports the outcome. It writes nothing itself:
every write a deferral causes (the stage's action-log line, its
``## OVERWATCH Decision`` event in release-state.md, its ledger entry, and
whatever its drill does) is the stage's own.

Readiness (read only, :func:`ship_policy_halt_state`)
    The project's own ``HALT`` file must carry the CM ship-policy header,
    classified by the stage's own ``classify_halt_file``. Absent, or an
    operator's HALT, there is nothing for the stage to resolve and the block
    stays with a human. The triage envelope also runs under
    ``overwatch_halt_first_fix``, which leaves a HALT that already exists
    alone, so the file the stage reads is the one CM wrote.

Verification (:func:`defer_to_stage`)
    The stage's Decision event: the number of ``## OVERWATCH Decision``
    events in the project's release-state.md is read before and after the
    call. The deferral is verified when the stage executed and appended a
    Decision event. A stage that declines (or does nothing) leaves the block
    with a human; a stage that executed without appending a Decision event
    is reported unverified, naming the ledger entry it returned.

Usage (import):
    from pgai_agent_kanban.overwatch.block_stage_deferral import (
        ship_policy_halt_state, defer_to_stage,
    )
    ready, why = ship_policy_halt_state(kanban_root, project)
    result = defer_to_stage(kanban_root, project)
    result.verified, result.detail
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Run by file path (``python3 .../block_stage_deferral.py``) or loaded from a
# file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package imports
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the imports resolve from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.overwatch.resolution_stage import (  # noqa: E402
    classify_halt_file,
    run_resolution_stage,
)

#: The stage outcome that means it acted (the stage's own EXECUTED value).
STAGE_EXECUTED = "executed"

#: The HALT classification the stage resolves (classify_halt_file's value).
SHIP_POLICY = "ship-policy"

#: A Decision event heading in release-state.md (liberal spacing).
_DECISION_EVENT_RE = re.compile(r"^##[ \t]+OVERWATCH[ \t]+Decision[ \t]*$", re.MULTILINE)

#: ``(project_name, kanban_root) -> outcome`` with ``.status``, ``.reason``
#: and ``.ledger_entry_id`` — the stage's ``run_resolution_stage``.
StageRunner = Callable[[str, str], Any]


@dataclasses.dataclass(frozen=True)
class StageDeferral:
    """What one deferral to the resolution stage found.

    Fields:
        status           the stage's outcome status (executed, declined,
                         no-op), or ``failed`` when the call raised.
        reason           the stage's own reason text.
        events_before    Decision events in release-state.md before the call.
        events_after     ... and after it.
        ledger_entry_id  the ledger entry the stage returned, if any.
    """

    status: str
    reason: str
    events_before: int
    events_after: int
    ledger_entry_id: Optional[str] = None

    @property
    def decision_event_appended(self) -> bool:
        """True when the stage call added at least one Decision event to release-state.md."""
        return self.events_after > self.events_before

    @property
    def verified(self) -> bool:
        """The stage executed and recorded a Decision event."""
        return self.status == STAGE_EXECUTED and self.decision_event_appended

    @property
    def detail(self) -> str:
        """One line for the action log."""
        event = (
            f"Decision event appended ({self.events_after - self.events_before} new)"
            if self.decision_event_appended
            else "no Decision event appended"
        )
        if self.verified:
            verdict = "verified by the stage's Decision event"
        elif self.status == STAGE_EXECUTED:
            ledger = f" (ledger entry {self.ledger_entry_id})" if self.ledger_entry_id else ""
            verdict = f"unverified: the stage executed{ledger} without a Decision event"
        else:
            verdict = "the stage did not resolve the block, so it stays with a human"
        return f"resolution stage {self.status}: {self.reason}; {event}; {verdict}"


def release_state_path(kanban_root: "Path | str", project: str) -> Path:
    """The project's release-state.md, where the stage appends its Decision events."""
    return Path(kanban_root) / "projects" / project / "release-state.md"


def count_decision_events(path: "Path | str") -> int:
    """``## OVERWATCH Decision`` events in *path* (0 when it is absent or unreadable)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return len(_DECISION_EVENT_RE.findall(text))


def ship_policy_halt_state(kanban_root: "Path | str", project: str) -> tuple[bool, str]:
    """``(ready, why)``: whether the project's HALT is a ship-policy HALT
    the stage can resolve. Reads only."""
    halt = Path(kanban_root) / "projects" / project / "HALT"
    kind = classify_halt_file(str(halt))
    if kind == SHIP_POLICY:
        return True, f"ship-policy HALT present at {halt}"
    if kind == "absent":
        return False, f"no HALT at {halt}, so the resolution stage has nothing to resolve"
    return False, f"the HALT at {halt} is not a ship-policy HALT (no CM header), so the resolution stage leaves it alone"


def defer_to_stage(
    kanban_root: "Path | str", project: str, *, runner: Optional[StageRunner] = None
) -> StageDeferral:
    """Run the resolution stage for *project* once and report its decision.

    *runner* defaults to the stage's ``run_resolution_stage``. A runner that
    raises is reported as ``failed`` (the block stays with a human); it is
    never retried here.
    """
    if runner is None:
        runner = run_resolution_stage

    state = release_state_path(kanban_root, project)
    before = count_decision_events(state)
    try:
        outcome = runner(project, str(kanban_root))
    # The stage is existing code this module does not own; anything it
    # raises is reported, and the block stays with a human, rather than
    # stopping the sweep for every other task.
    except Exception as exc:  # noqa: BLE001
        return StageDeferral(
            status="failed",
            reason=f"the resolution stage raised {type(exc).__name__}: {exc}",
            events_before=before,
            events_after=count_decision_events(state),
        )
    return StageDeferral(
        status=str(getattr(outcome, "status", "unknown")),
        reason=str(getattr(outcome, "reason", "")),
        events_before=before,
        events_after=count_decision_events(state),
        ledger_entry_id=getattr(outcome, "ledger_entry_id", None),
    )
