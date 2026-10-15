"""team/pm-agent/lib/acceptance_policy.py
Targeted-test and pre-merge policy for materialized CODER and WRITER tickets.

A ticket's acceptance criteria and constraints often arrive carrying a
full gated-runner invocation ("`bash team/scripts/run-unit-tests.sh`
exits 0"). Worker tickets run only the tests for the files they change;
the full gated suites run once per RC in the TESTER task. Before merging,
a worker ticket also runs the runner's cheap whole-tree pre-merge checks.
This module enforces both in code, independent of how the plan text was
worded.

Pure Python, no I/O. The materializer calls ``apply_acceptance_policy``
while emitting each ticket and is responsible for narrating the result
and recording it in the ticket's ``## Notes``.

Public API
----------
apply_acceptance_policy(role, acceptance_criteria, constraints) -> PolicyResult
    For a CODER or WRITER role: every acceptance criterion or constraint
    that names ``run-unit-tests.sh`` or ``run-integration-tests.sh``
    without ``--files`` is replaced by ``TARGETED_RUN_CRITERION`` (the
    ``PRE_MERGE_CRITERION`` text itself excepted), and
    ``TARGETED_RUN_CONSTRAINT`` is appended to the constraints unless an
    identical constraint is already present. ``PRE_MERGE_CRITERION`` is
    then placed last in the acceptance criteria, exactly once: any copy
    the plan already supplied is dropped and the canonical text appended
    after the plan-authored criteria. Any other role is a no-op.
    Applying the policy to its own output changes nothing.

format_policy_notes(result) -> str
    The ``## Notes`` paragraph recording a result's rewrites, or an empty
    string when nothing was rewritten.

TARGETED_RUN_CRITERION, TARGETED_RUN_CONSTRAINT
    The fixed replacement criterion and the fixed constraint text.

PRE_MERGE_CRITERION
    The fixed acceptance criterion every CODER and WRITER ticket carries:
    the unit runner's ``--pre-merge`` run exits 0 before the ticket merges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Roles whose tickets carry the targeted-test and pre-merge policy. TESTER
# runs the full gated suites by design; CM and HUMAN tickets never invoke a
# runner.
POLICY_ROLES = frozenset({"CODER", "WRITER"})

TARGETED_RUN_CRITERION = (
    "`bash team/scripts/run-unit-tests.sh --files <the test files this ticket "
    "adds or changes>` (and the integration runner the same way, if integration "
    "tests were touched) exits 0."
)

TARGETED_RUN_CONSTRAINT = (
    "Run only the tests for the files you changed using `--files`. "
    "The full gated suites run once per RC in the TESTER task."
)

# Names a runner without ``--files`` but is not a full-suite run: the
# pre-merge checks are the cheap whole-tree subset a change passes before it
# merges. Exempt from the full-runner rewrite by exact (whitespace-normalized)
# text only, so every other runner criterion is rewritten as before.
PRE_MERGE_CRITERION = (
    "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging."
)

# Either gated runner, by script name. Liberal on the separator so a
# hand-typed "run_unit_tests.sh" is caught as well.
_RUNNER_RE = re.compile(r"run[-_](?:unit|integration)[-_]tests\.sh", re.IGNORECASE)

# The targeted-run flag as a standalone option token ("--files", "--files=").
_FILES_FLAG_RE = re.compile(r"(?<![\w-])--files(?![\w-])")

_WHITESPACE_RE = re.compile(r"\s+")

FIELD_ACCEPTANCE = "acceptance criterion"
FIELD_CONSTRAINT = "constraint"


@dataclass(frozen=True)
class PolicyRewrite:
    """One criterion or constraint the policy replaced.

    field:      FIELD_ACCEPTANCE or FIELD_CONSTRAINT.
    original:   the text as the plan supplied it.
    merged:     True when the replacement duplicated an item already in
                the list, so the item was dropped rather than repeated.
    """

    field: str
    original: str
    merged: bool = False


@dataclass(frozen=True)
class PolicyResult:
    """Outcome of applying the policy to one ticket.

    acceptance_criteria / constraints: the lists to render in the README.
    rewrites:          every replacement made, in input order (acceptance
                       criteria first, then constraints).
    constraint_added:  True when TARGETED_RUN_CONSTRAINT was appended.
    """

    acceptance_criteria: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    rewrites: list = field(default_factory=list)
    constraint_added: bool = False


def invokes_full_runner(text: str) -> bool:
    """Return True when *text* names a gated runner without ``--files``."""
    if not isinstance(text, str):
        return False
    return bool(_RUNNER_RE.search(text)) and not _FILES_FLAG_RE.search(text)


def _normalize(text: str) -> str:
    """Collapse whitespace so equality ignores spacing and line wrapping."""
    return _WHITESPACE_RE.sub(" ", text).strip()


def _is_pre_merge_criterion(item) -> bool:
    """Return True when *item* is PRE_MERGE_CRITERION, ignoring spacing."""
    return isinstance(item, str) and _normalize(item) == _normalize(PRE_MERGE_CRITERION)


def _rewrite_items(items, field_name):
    """Rewrite full-runner items in *items*; return (new_items, rewrites).

    PRE_MERGE_CRITERION is passed through as written: it names a runner
    without ``--files`` but asks for the pre-merge run, not a full suite.
    """
    out = []
    seen = set()
    rewrites = []
    for item in items:
        if invokes_full_runner(item) and not _is_pre_merge_criterion(item):
            merged = _normalize(TARGETED_RUN_CRITERION) in seen
            rewrites.append(PolicyRewrite(field=field_name, original=item, merged=merged))
            if merged:
                continue
            item = TARGETED_RUN_CRITERION
        out.append(item)
        if isinstance(item, str):
            seen.add(_normalize(item))
    return out, rewrites


def apply_acceptance_policy(role, acceptance_criteria, constraints) -> PolicyResult:
    """Apply the targeted-test and pre-merge policy to one ticket.

    *role* is matched case-insensitively against POLICY_ROLES. For any other
    role the lists come back as unchanged copies with no rewrites, so a
    ticket outside the policy renders exactly as it would without it.

    For a policy role the acceptance criteria always end with exactly one
    PRE_MERGE_CRITERION. Placing it is not a rewrite: it is not listed in
    ``rewrites`` and adds nothing to the notes.

    The input lists are never mutated. ``None`` is treated as an empty list.
    """
    acceptance = list(acceptance_criteria or [])
    constraint_list = list(constraints or [])

    if str(role or "").strip().upper() not in POLICY_ROLES:
        return PolicyResult(acceptance_criteria=acceptance, constraints=constraint_list)

    new_acceptance, acc_rewrites = _rewrite_items(acceptance, FIELD_ACCEPTANCE)
    new_constraints, con_rewrites = _rewrite_items(constraint_list, FIELD_CONSTRAINT)

    # Last and exactly once, whether or not (and wherever) the plan wrote it.
    new_acceptance = [c for c in new_acceptance if not _is_pre_merge_criterion(c)]
    new_acceptance.append(PRE_MERGE_CRITERION)

    fixed = _normalize(TARGETED_RUN_CONSTRAINT)
    constraint_added = not any(
        isinstance(c, str) and _normalize(c) == fixed for c in new_constraints
    )
    if constraint_added:
        new_constraints.append(TARGETED_RUN_CONSTRAINT)

    return PolicyResult(
        acceptance_criteria=new_acceptance,
        constraints=new_constraints,
        rewrites=acc_rewrites + con_rewrites,
        constraint_added=constraint_added,
    )


def format_policy_notes(result: PolicyResult) -> str:
    """Render *result*'s rewrites as a ``## Notes`` paragraph.

    Returns an empty string when the result holds no rewrites, so a ticket
    the policy did not rewrite keeps its notes exactly as supplied.
    """
    if not result.rewrites:
        return ""
    lines = [
        "Targeted-test policy: full gated-runner invocations were rewritten "
        "to targeted `--files` runs."
    ]
    for rw in result.rewrites:
        suffix = " (duplicate of an earlier rewrite, dropped)" if rw.merged else ""
        lines.append(f'- {rw.field}{suffix}: "{_normalize(rw.original)}"')
    return "\n".join(lines)
