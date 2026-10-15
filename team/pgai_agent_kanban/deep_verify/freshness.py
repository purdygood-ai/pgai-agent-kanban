"""
freshness.py — the pre-release promotion gate's classification: does the
project's deep-verify ledger hold a fresh green record for the exact
version about to be promoted to a public tag?

team/scripts/deep-verify-freshness-check.sh is the operator verb that reads
the project's ledger (via team/scripts/lib/deep_verify_ledger.sh) and calls
this module to decide whether promotion may proceed. This module is a pure
decision layer -- it reads no files and starts no processes, the same shape
classify.py uses for the cold-build verdict decision and schedule.py uses
for the scheduled-dispatch due-check. Its caller supplies every input as a
plain string or an already-parsed record dict; keeping the decision pure is
what makes it unit-testable against injected timestamps instead of the wall
clock.

Freshness policy (the one place it is stated; every refusal site quotes
this same policy back to the operator, and team/scripts/deep-verify-
freshness-check.sh's own header comment must describe it in the same
words):

  A deep-verify record satisfies promotion of version V when ALL of:
    1. The project has a record at all (the ledger is not empty / the
       project has never been deep-verified).
    2. The record's Verdict is exactly "built" -- "failed-to-build" and
       "could-not-attempt" both refuse (team/SOP-core.md, "Gates and
       Refusals" -- "Unusable build blocks; an unavailable build
       environment does not": neither is a trustworthy pass signal, and
       could-not-attempt in particular means nothing was learned about
       the tree at all).
    3. The record's Version field equals V exactly -- a green record for a
       DIFFERENT version does not speak for the tree state being promoted
       now, no matter how recently it ran.
    4. The record's Timestamp is no older than FRESHNESS_WINDOW_DAYS days
       (7 by default -- the same cadence the DEEP-VERIFY tier's own
       "weekly" scheduled mode uses, see schedule.py's WEEKLY_INTERVAL, so
       a record from the last on-time scheduled run is always fresh enough
       on its own) before the check runs.

  Condition 1 failing is reported as RESULT_NEVER_RUN. Condition 2 failing
  is reported as RESULT_FAILED_TO_BUILD or RESULT_COULD_NOT_ATTEMPT
  (distinct results -- the operator's remedy differs: fix the tree defect
  vs. fix the verifying environment). Conditions 3 and/or 4 failing (with
  condition 2 satisfied) is reported as RESULT_STALE, carrying one reason
  string per failing condition so the refusal names both a version
  mismatch and an age problem when both are true at once. Meeting all four
  conditions is RESULT_FRESH.

Usage (import):
    from pgai_agent_kanban.deep_verify.freshness import evaluate_freshness
    decision = evaluate_freshness(
        target_version="v2.9.12",
        record={
            "timestamp": "2026-09-01T03:15:00+00:00",
            "version": "v2.9.12",
            "commit": "e240ee1",
            "verdict": "built",
            "detail": "docker/claude-code: built; podman/claude-code: built",
        },
        now=datetime.datetime(2026, 9, 3, 12, 0, tzinfo=datetime.timezone.utc),
    )
    decision.result   # "fresh"
    decision.age_str  # "2d8h"

    # record=None means "never run" (the ledger's latest_record() returned
    # None -- distinguish this from a malformed ledger, which raises
    # LedgerError before this module is ever reached; the caller refuses
    # that case on its own, without calling here).
    evaluate_freshness(target_version="v2.9.12", record=None, now=...).result
    # -> "never_run"

Usage (CLI, consumed by deep-verify-freshness-check.sh via
team/scripts/lib/pp_run_ops.sh):
    python3 -m pgai_agent_kanban.deep_verify.freshness \\
        <target_version> <found:true|false> <timestamp> <version> <verdict> \\
        <now_iso> [<window_days>]
    Prints "result=<one of VALID_RESULTS>", then "age=<formatted elapsed or
    empty>", then zero or more "reason=<text>" lines (populated only for
    result=stale).
"""

from __future__ import annotations

import datetime
import pathlib
import sys
from dataclasses import dataclass

# Run by file path (``python3 .../freshness.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.timeutil import parse_iso  # noqa: E402

# ---------------------------------------------------------------------------
# Result vocabulary. Imported (never redefined) by anything that needs to
# compare against these values structurally -- the CLI, the bash caller's
# case-dispatch (via literal string matching on the printed result= line),
# and tests.
# ---------------------------------------------------------------------------
RESULT_FRESH = "fresh"
RESULT_NEVER_RUN = "never_run"
RESULT_FAILED_TO_BUILD = "failed_to_build"
RESULT_COULD_NOT_ATTEMPT = "could_not_attempt"
RESULT_STALE = "stale"

VALID_RESULTS = (
    RESULT_FRESH,
    RESULT_NEVER_RUN,
    RESULT_FAILED_TO_BUILD,
    RESULT_COULD_NOT_ATTEMPT,
    RESULT_STALE,
)

# Default freshness window: 7 days, matching the DEEP-VERIFY tier's own
# "weekly" scheduled cadence (pgai_agent_kanban.deep_verify.schedule.
# WEEKLY_INTERVAL) -- a record from the last on-time scheduled run always
# satisfies promotion on its own, with no separate on-demand run required.
DEFAULT_FRESHNESS_WINDOW_DAYS = 7


@dataclass(frozen=True)
class FreshnessDecision:
    """One freshness classification outcome.

    result    -- one of VALID_RESULTS.
    reasons   -- human-readable reasons the record failed to satisfy
                 promotion; non-empty only for RESULT_STALE (1 entry for a
                 version mismatch alone, 1 entry for an age problem alone,
                 2 entries when both are true at once).
    age_str   -- the record's elapsed age relative to *now*, formatted as
                 "<days>d<hours>h" (or "<hours>h" when under a day).
                 Populated whenever a record exists (RESULT_FRESH and
                 RESULT_STALE with a parseable timestamp); empty for
                 RESULT_NEVER_RUN and for the two non-green verdicts (age
                 is irrelevant to those -- the verdict itself refuses,
                 regardless of how recently it ran).
    """

    result: str
    reasons: tuple[str, ...] = ()
    age_str: str = ""


def _format_elapsed(delta: datetime.timedelta) -> str:
    total_hours = int(delta.total_seconds() // 3600)
    days, hours = divmod(total_hours, 24)
    if days:
        return f"{days}d{hours}h"
    return f"{hours}h"


def evaluate_freshness(
    *,
    target_version: str,
    record: dict | None,
    now: datetime.datetime,
    window_days: int = DEFAULT_FRESHNESS_WINDOW_DAYS,
) -> FreshnessDecision:
    """Classify *record* (or its absence) against promotion of
    *target_version*. See the module docstring for the full policy.

    *record* is None for "never run" (the ledger's latest_record() returned
    None). Otherwise a dict with at least "timestamp", "version", and
    "verdict" keys, as produced by pgai_agent_kanban.deep_verify.ledger.
    latest_record() -- that function already guarantees verdict is one of
    ("built", "failed-to-build", "could-not-attempt") and raises LedgerError
    for anything else, so this function trusts *record*["verdict"] without
    re-validating it; a malformed ledger never reaches this function at all
    (the caller refuses on the LedgerError before calling here).

    Never raises: an unparseable timestamp is reported as a RESULT_STALE
    reason (conservative -- refuse rather than silently treating an
    unparseable timestamp as either fresh or expired) rather than
    propagating an exception the bash caller would have to catch.
    """
    if record is None:
        return FreshnessDecision(RESULT_NEVER_RUN)

    verdict = record.get("verdict")
    if verdict == "failed-to-build":
        return FreshnessDecision(RESULT_FAILED_TO_BUILD)
    if verdict == "could-not-attempt":
        return FreshnessDecision(RESULT_COULD_NOT_ATTEMPT)
    # The only verdict left is "built" -- latest_record() refuses any other
    # value before this function is ever reached (see docstring above).

    record_version = record.get("version") or ""
    record_timestamp = record.get("timestamp") or ""

    reasons: list[str] = []
    age_str = ""

    if record_version != target_version:
        reasons.append(
            f"record is for version {record_version!r}, promotion target is "
            f"{target_version!r}"
        )

    try:
        record_time = parse_iso(record_timestamp)
    except ValueError:
        reasons.append(
            f"record timestamp {record_timestamp!r} could not be parsed"
        )
    else:
        elapsed = now - record_time
        age_str = _format_elapsed(elapsed)
        window = datetime.timedelta(days=window_days)
        if elapsed > window:
            reasons.append(
                f"record is {age_str} old, older than the {window_days}-day "
                "freshness window"
            )

    if reasons:
        return FreshnessDecision(RESULT_STALE, tuple(reasons), age_str)

    return FreshnessDecision(RESULT_FRESH, (), age_str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_CLI_HELP = """\
usage: freshness.py <target_version> <found:true|false> <timestamp> <version> <verdict> <now_iso> [<window_days>]

Decide whether the project's latest deep-verify ledger record is a fresh,
green record for the version about to be promoted.

Positional arguments:
  target_version  the version about to be promoted, e.g. v1.2.3
  found           "true" when the ledger holds a record, "false" when it is
                  empty; with "false" the next three values are ignored
                  (pass empty strings)
  timestamp       the record's Timestamp field (ISO-8601)
  version         the record's Version field
  verdict         the record's Verdict field: built, failed-to-build or
                  could-not-attempt
  now_iso         the current time (ISO-8601) the record's age is measured at
  window_days     optional: the freshness window in whole days (default %d)

Prints result=<value>, then age=<value>, then zero or more reason=<value>
lines.

Options:
  -h, --help      print this help and exit 0

Exit codes: 0 decided (the result line carries the verdict); 1 now_iso
does not parse; 2 wrong argument list or a non-integer window_days.
"""


def main(argv: list | None = None) -> int:
    """CLI: freshness.py <target_version> <found:true|false> <timestamp>
    <version> <verdict> <now_iso> [<window_days>]

    When <found> is "false" the remaining record fields are ignored (the
    caller passes empty strings for them). <window_days> is optional and
    defaults to DEFAULT_FRESHNESS_WINDOW_DAYS.

    Prints "result=<value>", then "age=<value>", then zero or more
    "reason=<value>" lines to stdout.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(_CLI_HELP % DEFAULT_FRESHNESS_WINDOW_DAYS, end="")
        return 0
    if len(argv) not in (6, 7):
        print(
            "usage: freshness.py <target_version> <found:true|false> "
            "<timestamp> <version> <verdict> <now_iso> [<window_days>]",
            file=sys.stderr,
        )
        return 2

    target_version, found_raw, timestamp, version, verdict, now_iso = argv[:6]
    window_days = DEFAULT_FRESHNESS_WINDOW_DAYS
    if len(argv) == 7 and argv[6]:
        try:
            window_days = int(argv[6])
        except ValueError:
            print(
                f"freshness.py: window_days must be an integer, got {argv[6]!r}",
                file=sys.stderr,
            )
            return 2

    found = found_raw.strip().lower() == "true"

    try:
        now = parse_iso(now_iso)
    except ValueError as exc:
        print(f"freshness.py: could not parse now={now_iso!r}: {exc}", file=sys.stderr)
        return 1

    record = None
    if found:
        record = {"timestamp": timestamp, "version": version, "verdict": verdict}

    decision = evaluate_freshness(
        target_version=target_version, record=record, now=now, window_days=window_days
    )

    print(f"result={decision.result}")
    print(f"age={decision.age_str}")
    for reason in decision.reasons:
        print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
