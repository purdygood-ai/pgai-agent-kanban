"""
schedule.py — the DEEP-VERIFY tier's scheduled-dispatch due-check.

team/scripts/deep-verify.sh --scheduled is the entry point a cron/pseudocron
job invokes on a cadence neither scheduler can express directly for a
per-project weekly cost gate: cron's day-of-week field can name "Sunday",
but pseudocron's job table matches on minute-of-hour alone (see
docs/pseudocron.md) and fires the same command every hour that minute
comes around. So the due-check has to live here, inside the dispatch path,
not in either schedule file: a scheduled invocation asks "has a week
elapsed since the last recorded run for this project, and are we in the
off-hours window?", runs the cold-build lane when the answer is yes, and
narrates one line and exits cleanly when it is not.

This module is a pure decision layer -- it reads no files and starts no
processes. Its caller (deep-verify.sh) supplies every input as a plain
string: the project's resolved deep_verify mode, the ledger's most recent
timestamp (or empty for "never run"), the current time, and the raw
off-hours-window config value. Keeping the decision pure is what makes it
unit-testable against injected timestamps instead of the wall clock -- the
same shape classify.py uses for the cold-build verdict decision.

Two independent knobs, both read from [release] -- project.cfg first, then
kanban.cfg, then the built-in default -- by
team/scripts/lib/project_paths.sh (pp_deep_verify_mode / pp_deep_verify_
offhours_window) before this module ever sees them:

  deep_verify                  -- "weekly" or "off" (VALID_MODES). Absent
                                   or blank resolves to DEFAULT_MODE
                                   ("weekly" -- see pp_deep_verify_mode's
                                   own docstring for why absence must not
                                   silently mean "off": a lane nobody
                                   remembers to turn on is a lane that
                                   never runs).
  deep_verify_offhours_window  -- "HH:MM-HH:MM", the daily window during
                                   which a scheduled run may fire (the
                                   schedule knob that lets an operator
                                   stagger multiple projects' weekly runs
                                   across different hours). Absent or
                                   unparseable falls back to
                                   DEFAULT_OFFHOURS_WINDOW.

Usage (import):
    from pgai_agent_kanban.deep_verify.schedule import evaluate_due
    decision = evaluate_due(
        project="demo",
        mode="weekly",
        last_run_iso="2026-08-26T03:15:00+00:00",
        now=datetime.datetime(2026, 9, 3, 3, 15, tzinfo=datetime.timezone.utc),
        offhours_window_raw="03:00-03:59",
    )
    decision.due        # True -- 8 days have elapsed
    decision.narration  # one human-readable line naming the project

Usage (CLI, consumed by deep-verify.sh via team/scripts/lib/pp_run_ops.sh):
    python3 -m pgai_agent_kanban.deep_verify.schedule \\
        <project> <mode> <last_run_iso_or_empty> <now_iso> <offhours_window_raw_or_empty>
    Prints exactly two lines: "due=true|false" then "narration=<text>".
"""

from __future__ import annotations

import datetime
import pathlib
import re
import sys
from dataclasses import dataclass

# Run by file path (``python3 .../schedule.py``) or loaded from a file
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
# Mode vocabulary. Kept here (not re-derived in project_paths.sh) as the one
# place that names the two accepted values -- pp_deep_verify_mode enforces
# the same vocabulary in bash before a value ever reaches this module, but
# the literal strings are defined once, here, and imported by anything that
# needs to compare against them structurally (tests, future callers).
# ---------------------------------------------------------------------------
MODE_WEEKLY = "weekly"
MODE_OFF = "off"
VALID_MODES = (MODE_WEEKLY, MODE_OFF)

# A deep_verify value absent or blank in both project.cfg and kanban.cfg
# [release] resolves to "weekly", not "off" -- see this module's docstring
# and pp_deep_verify_mode's own
# comment in team/scripts/lib/project_paths.sh for the shared rationale: a
# lane that only runs when someone remembers to run it is a lane that
# stops running.
DEFAULT_MODE = MODE_WEEKLY

WEEKLY_INTERVAL = datetime.timedelta(days=7)

# Off-hours default: a single-hour slot that does not collide with the
# shipped cron template's cleanup jobs (Sunday 04:00 full cleanup, daily
# 04:15 trivial-log purge, daily 04:30 log cleanup -- see
# team/scripts/cron-suggested.template.txt).
DEFAULT_OFFHOURS_WINDOW = "03:00-03:59"

_WINDOW_RE = re.compile(
    r"^(?P<start_h>[01]?\d|2[0-3]):(?P<start_m>[0-5]\d)-"
    r"(?P<end_h>[01]?\d|2[0-3]):(?P<end_m>[0-5]\d)$"
)


class InvalidDeepVerifyMode(ValueError):
    """Raised by resolve_mode() when a non-blank deep_verify value is
    neither 'weekly' nor 'off'. The caller must not guess a mode from an
    unrecognized value -- this exception is how that refusal surfaces to
    Python callers; the CLI (main()) turns it into a stderr diagnostic and
    a non-zero exit for the bash caller."""


@dataclass(frozen=True)
class DueDecision:
    """One scheduled-dispatch due-check outcome.

    due        -- True when the scheduled invocation should run the cold
                  lane now. False means "cost nothing this cycle": no lane
                  invocation, no ledger write.
    narration  -- a single human-readable line naming the project and
                  either what is happening (due) or when it next comes due
                  (not due). Always non-empty; the caller prints it
                  verbatim as the one-line cron/pseudocron log entry this
                  ticket's constraints require.
    """

    due: bool
    narration: str


def resolve_mode(raw: str | None) -> str:
    """Resolve a raw [release] deep_verify value -- the one in force after
    project.cfg, then kanban.cfg, is consulted -- to 'weekly' or 'off'.
    Blank/None resolves to DEFAULT_MODE. Any other non-blank value
    raises InvalidDeepVerifyMode -- there is no silent-guess path here, by
    design: a typo must never silently defer deep verification."""
    value = (raw or "").strip()
    if not value:
        return DEFAULT_MODE
    if value not in VALID_MODES:
        raise InvalidDeepVerifyMode(
            f"deep_verify: unrecognized value {value!r} -- must be one of "
            f"{VALID_MODES!r} (blank/absent defaults to {DEFAULT_MODE!r})"
        )
    return value


def parse_offhours_window(raw: str | None) -> tuple[datetime.time, datetime.time]:
    """Parse an 'HH:MM-HH:MM' window string into (start_time, end_time).
    Blank/None resolves to DEFAULT_OFFHOURS_WINDOW. Raises ValueError for a
    non-blank value that does not match the HH:MM-HH:MM shape."""
    value = (raw or "").strip() or DEFAULT_OFFHOURS_WINDOW
    match = _WINDOW_RE.match(value)
    if not match:
        raise ValueError(
            f"deep_verify_offhours_window: {value!r} does not match the "
            "required HH:MM-HH:MM shape"
        )
    start = datetime.time(int(match.group("start_h")), int(match.group("start_m")))
    end = datetime.time(int(match.group("end_h")), int(match.group("end_m")))
    return start, end


def resolve_offhours_window(raw: str | None) -> tuple[tuple[datetime.time, datetime.time], str | None]:
    """Forgiving wrapper over parse_offhours_window(): a malformed non-blank
    window value falls back to DEFAULT_OFFHOURS_WINDOW rather than refusing
    the whole scheduled invocation -- the window is a scheduling nicety
    ("the schedule knob"), not the invariant deep_verify's mode vocabulary
    is (see resolve_mode's docstring for why THAT value refuses instead of
    falling back). Returns (window, warning) where warning is None on a
    clean parse and a human-readable diagnostic string when the fallback
    fired."""
    try:
        return parse_offhours_window(raw), None
    except ValueError as exc:
        return parse_offhours_window(None), (
            f"{exc}; falling back to the default window {DEFAULT_OFFHOURS_WINDOW}"
        )


def _within_window(now: datetime.datetime, window: tuple[datetime.time, datetime.time]) -> bool:
    start, end = window
    current = now.timetz().replace(tzinfo=None) if now.tzinfo is not None else now.time()
    if start <= end:
        return start <= current <= end
    # Overnight window (e.g. 23:30-01:00). The shipped default never
    # crosses midnight, but a project-level override might.
    return current >= start or current <= end


def _format_elapsed(delta: datetime.timedelta) -> str:
    total_hours = int(delta.total_seconds() // 3600)
    days, hours = divmod(total_hours, 24)
    if days:
        return f"{days}d{hours}h"
    return f"{hours}h"


def evaluate_due(
    *,
    project: str,
    mode: str,
    last_run_iso: str | None,
    now: datetime.datetime,
    offhours_window_raw: str | None,
) -> DueDecision:
    """Decide whether a scheduled invocation for *project* should run the
    cold lane now. *mode* must already be a validated 'weekly' or 'off'
    (resolve_mode()) -- this function does not re-validate it, so an
    out-of-vocabulary mode is a caller bug, not a runtime refusal here.

    Evaluation order for mode == 'weekly':
      1. Off-hours window -- a scheduled invocation outside the window is
         not due, regardless of ledger age. This is what lets the same
         due-check work correctly under pseudocron, which cannot express
         an hour-of-day restriction in its job table and so would
         otherwise fire this check every hour, all day, every day.
      2. Ledger age -- never run, or the most recent run is >= 7 days old:
         due. Otherwise: not due, with the exact next-due timestamp named.
    """
    if mode == MODE_OFF:
        return DueDecision(
            False,
            f"deep-verify: project={project} scheduled run skipped (deep_verify=off)",
        )

    window, _window_warning = resolve_offhours_window(offhours_window_raw)
    if not _within_window(now, window):
        start, end = window
        return DueDecision(
            False,
            f"deep-verify: project={project} scheduled run not due "
            f"(outside off-hours window {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
            f"UTC; checked at {now.strftime('%H:%M')} UTC)",
        )

    if not last_run_iso:
        return DueDecision(
            True,
            f"deep-verify: project={project} never deep-verified before; running now",
        )

    last_run = parse_iso(last_run_iso)
    elapsed = now - last_run
    if elapsed >= WEEKLY_INTERVAL:
        return DueDecision(
            True,
            f"deep-verify: project={project} last run {_format_elapsed(elapsed)} ago "
            "(>= 7 days); running now",
        )

    next_due = last_run + WEEKLY_INTERVAL
    return DueDecision(
        False,
        f"deep-verify: project={project} scheduled run not due (last run "
        f"{_format_elapsed(elapsed)} ago; next due "
        f"{next_due.strftime('%Y-%m-%dT%H:%M:%S+00:00')})",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


_CLI_HELP = """\
usage: schedule.py <project> <mode> <last_run_iso_or_empty> <now_iso> <offhours_window_raw_or_empty>

Decide whether a scheduled deep-verify run is due for one project now.

Positional arguments (all five required, in this order; pass an empty
string for an "or_empty" value that is absent):
  project                       the project name, used in the narration
  mode                          the project's resolved deep_verify mode:
                                weekly or off (empty means weekly)
  last_run_iso_or_empty         the ledger's most recent run timestamp
                                (ISO-8601), or empty when never run
  now_iso                       the current time (ISO-8601)
  offhours_window_raw_or_empty  the project's deep_verify_offhours_window
                                value, HH:MM-HH:MM, or empty for the default

Prints exactly two lines: due=true|false, then narration=<text>.

Options:
  -h, --help                    print this help and exit 0

Exit codes: 0 decided; 1 an invalid mode or an unparseable now_iso;
2 wrong argument list.
"""


def main(argv: list | None = None) -> int:
    """CLI: schedule.py <project> <mode> <last_run_iso_or_empty> <now_iso> <offhours_window_raw_or_empty>

    Prints exactly two lines to stdout: "due=true|false" then
    "narration=<text>". *mode* is expected to already be validated by the
    caller (deep-verify.sh calls pp_deep_verify_mode first and never
    reaches this CLI for an invalid value) -- this defensive re-check
    exists only so a direct CLI invocation fails loud instead of guessing.
    """
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(_CLI_HELP, end="")
        return 0
    if len(argv) != 5:
        print(
            "usage: schedule.py <project> <mode> <last_run_iso_or_empty> "
            "<now_iso> <offhours_window_raw_or_empty>",
            file=sys.stderr,
        )
        return 2

    project, mode_raw, last_run_iso, now_iso, offhours_window_raw = argv

    try:
        mode = resolve_mode(mode_raw)
    except InvalidDeepVerifyMode as exc:
        print(f"schedule.py: {exc}", file=sys.stderr)
        return 1

    try:
        now = parse_iso(now_iso)
    except ValueError as exc:
        print(f"schedule.py: could not parse now={now_iso!r}: {exc}", file=sys.stderr)
        return 1

    decision = evaluate_due(
        project=project,
        mode=mode,
        last_run_iso=last_run_iso or None,
        now=now,
        offhours_window_raw=offhours_window_raw or None,
    )

    print(f"due={'true' if decision.due else 'false'}")
    print(f"narration={decision.narration}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
