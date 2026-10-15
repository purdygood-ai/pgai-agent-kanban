"""
number_format.py -- Single home for the dashboard's thousands-separated
number formats and their established '--' placeholder for absent data.

  format_int(value)          -> "1,234,567"   (counts and totals)
  format_one_decimal(value)  -> "41,000.0"    (averages, exactly one decimal)

Every dashboard renderer that prints a token count, a task count, or an
average of either must import these rather than re-implementing
"{:,}".format(...) or "{:,.1f}".format(...) locally -- directive 3 in docs/coding-standards.md
("Put shared code in a lib/ package and import it; never copy it.").
The rc-metrics renderers (pgai_agent_kanban.lib.metrics.history) and the
agent-metrics renderer (pgai_agent_kanban.lib.metrics.agents) import it.

IMPORT PATHS
============

From the package (every Python renderer in pgai_agent_kanban, and any
script that reaches the package through pp_run_ops / pp_pythonpath):

    from pgai_agent_kanban.lib.number_format import format_int, format_one_decimal

Same-directory import from team/scripts/lib/ (or anything that has put that
directory on sys.path): team/scripts/lib/number_format.py re-exports these
two functions from this module, so both spellings name the same functions:

    from number_format import format_int, format_one_decimal

CLI (manual smoke-test / operator use; the team/scripts/lib/ copy of the
entry point runs this module's main()):

    python3 team/scripts/lib/number_format.py 5644879
    python3 team/scripts/lib/number_format.py ""
    python3 team/scripts/lib/number_format.py --one-decimal 41000
    python3 team/scripts/lib/number_format.py --help

Exit codes (CLI only):
    0 -- printed the formatted value
    1 -- usage error (wrong argument count)
"""

from __future__ import annotations

import sys


def format_int(value: object) -> str:
    """Return `value` rendered as a thousands-separated integer string.

    Returns the shared absent-data placeholder '--' when `value` is None,
    or is a string that is empty (or all whitespace) after stripping.

    When `value` cannot be parsed as a number (a non-numeric string), the
    original string form of `value` is returned unchanged rather than
    '--', so a parse failure shows the raw input instead of masking it
    behind a placeholder.

    Accepts int, float, or str (or None). A float is truncated toward zero
    via int(float(value)) before formatting, so history.csv's
    numeric-string columns (which may carry a trailing ".0" from CSV
    round-tripping) format as whole numbers.
    """
    if value is None:
        return "--"
    s = str(value).strip()
    if not s:
        return "--"
    try:
        return "{:,}".format(int(float(s)))
    except (ValueError, TypeError):
        return s


def format_one_decimal(value: object) -> str:
    """Return `value` rendered with thousands separators and exactly one
    decimal place: 2 -> "2.0", 4.7 -> "4.7", 41000 -> "41,000.0".

    Shares format_int()'s absent-data contract: None, or a string that is
    empty (or all whitespace) after stripping, renders the placeholder
    '--'; a non-numeric string is returned unchanged.

    This function formats; it does not choose a rounding rule. An average
    is first rounded to one decimal by the shared display rule,
    pgai_agent_kanban.lib.agent_metrics.round_one_decimal() -- the same
    rule the REST twin applies to its JSON numbers -- and the rounded
    value is passed here, where the printed digits are then exact. A value
    with more than one decimal passed directly is printed at the nearest
    one-decimal representation of its binary value.
    """
    if value is None:
        return "--"
    s = str(value).strip()
    if not s:
        return "--"
    try:
        return "{:,.1f}".format(float(s))
    except (ValueError, TypeError):
        return s


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: format a single value passed as the sole argument
    (as an integer, or with --one-decimal as a one-decimal average)."""
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    formatter = format_int
    if args and args[0] == "--one-decimal":
        formatter = format_one_decimal
        args = args[1:]

    if len(args) != 1:
        print(
            "usage: number_format.py <value>\n"
            "       number_format.py --one-decimal <value>\n"
            "       number_format.py --help",
            file=sys.stderr,
        )
        return 1

    print(formatter(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
