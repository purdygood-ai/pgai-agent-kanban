"""
ledger_filename_contract.py — Single-source filename gate for BUG-/PRIORITY- ledger entries.

Both the filing-time high-water scan (team/pm-agent/lib/bug_scanner.py) and the
discovery scan-and-select layer (_disc_find_unhandled_items in
scripts/lib/discovery.sh) import from this module.  BUG- and PRIORITY- are
sibling ledger namespaces: same canonical shape, same defect classes, same
high-water arithmetic.  This module is where that shared contract lives so
neither consumer can drift from the other.

The canon (exactly one shape per prefix, matching discovery's historical
``expected_pattern``):

    <PREFIX>-NNNN[-YYYYMMDD]-<slug>.md

  - ``NNNN``     — exactly 4 digits, zero-padded.  Not lexically compared;
                   every consumer of the numeric field must parse it with
                   ``int()``.
  - ``YYYYMMDD`` — optional 8-digit date segment, permitted ONLY immediately
                   after the number (historical compatibility with the
                   dated long form some ledger entries used).
  - ``<slug>``   — kebab-case: first character alphanumeric, remainder
                   alphanumeric or hyphen.

Two malformation classes are diagnosed explicitly because they are the ones
weak models actually produce:

  - **date-first**    — e.g. ``PRIORITY-20260803-001-<slug>.md``.  The
                         TASK-ID convention (e.g. ``TESTER-20260803-001``)
                         bleeding into the ledger namespace.
  - **under-padded**  — e.g. ``PRIORITY-036-<slug>.md``.  Visually plausible,
                         arithmetically poisonous: a lexical comparison of
                         "036" against "0036" sorts backwards.

A third class, **over-padded** (5+ digit numeric field), is diagnosed for
completeness but has not been observed in production.

Public API
----------
    ledger_filename_re(prefix)                  -> re.Pattern[str]
    classify_defect(name, prefix)                -> str | None
    next_free_number(dir_path, prefix)           -> int
    build_remedy(name, prefix, dir_path)         -> str

Structural guarantee: the filename pattern literal for BUG-/PRIORITY- ledger
entries appears in exactly one place — this module.  Consumers import; they
do not duplicate a local definition (the historical loose ``{4,}`` pattern
this replaces is what let date-first names slip through the discovery gate).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

# ---------------------------------------------------------------------------
# Canonical shape components
# ---------------------------------------------------------------------------

_NUMBER_FIELD = r"[0-9]{4}"
_DATE_FIELD = r"[0-9]{8}"
_SLUG_FIELD = r"[a-z0-9][a-z0-9-]*"

# ---------------------------------------------------------------------------
# Defect classification constants
# ---------------------------------------------------------------------------

DEFECT_DATE_FIRST = "date-first"
DEFECT_UNDER_PADDED = "under-padded"
DEFECT_OVER_PADDED = "over-padded"
DEFECT_MALFORMED = "malformed"


def _normalize_prefix(prefix: str) -> str:
    """Return *prefix* upper-cased with any trailing hyphen stripped.

    Accepts both "BUG-" and "BUG" style inputs so callers do not need to
    remember which form to pass.
    """
    return prefix.rstrip("-").upper()


def ledger_filename_re(prefix: str) -> "re.Pattern[str]":
    """Return the compiled canonical filename regex for a ledger prefix.

    Args:
        prefix: "BUG", "BUG-", "PRIORITY", or "PRIORITY-" (case-insensitive).

    Returns:
        A compiled, case-insensitive regex matching exactly the canonical
        ``<PREFIX>-NNNN[-YYYYMMDD]-<slug>.md`` shape.
    """
    prefix_u = re.escape(_normalize_prefix(prefix))
    return re.compile(
        rf"^{prefix_u}-{_NUMBER_FIELD}(?:-{_DATE_FIELD})?-{_SLUG_FIELD}\.md$",
        re.IGNORECASE,
    )


def classify_defect(name: str, prefix: str) -> Optional[str]:
    """Return the defect class for a filename that fails the ledger gate.

    Args:
        name:   Bare filename (not a full path).
        prefix: Ledger prefix ("BUG" or "PRIORITY", with or without trailing "-").

    Returns:
        ``None`` if *name* actually satisfies :func:`ledger_filename_re`
        (i.e. the caller should not have needed a diagnosis).  Otherwise one
        of ``DEFECT_DATE_FIRST``, ``DEFECT_UNDER_PADDED``,
        ``DEFECT_OVER_PADDED``, or ``DEFECT_MALFORMED``.
    """
    if ledger_filename_re(prefix).match(name):
        return None

    prefix_u = re.escape(_normalize_prefix(prefix))

    # date-first: an 8-digit segment immediately after the prefix, itself
    # followed by a further numeric field (the "sequence" the model tacked
    # on after the date) — e.g. PRIORITY-20260803-001-<slug>.md.
    if re.match(rf"^{prefix_u}-{_DATE_FIELD}-[0-9]+-.+\.md$", name, re.IGNORECASE):
        return DEFECT_DATE_FIRST

    # under-padded: 1-3 digit numeric field where 4 are required.
    if re.match(rf"^{prefix_u}-[0-9]{{1,3}}-.+\.md$", name, re.IGNORECASE):
        return DEFECT_UNDER_PADDED

    # over-padded: 5+ digit numeric field.
    if re.match(rf"^{prefix_u}-[0-9]{{5,}}(?:-{_DATE_FIELD})?-.+\.md$", name, re.IGNORECASE):
        return DEFECT_OVER_PADDED

    return DEFECT_MALFORMED


def next_free_number(dir_path: Union[str, Path], prefix: str) -> int:
    """Scan *dir_path* for gate-valid ``<prefix>-NNNN-*.md`` files and return
    the next free sequence number.

    Only filenames that pass :func:`ledger_filename_re` participate in the
    scan — malformed names (date-first, under/over-padded, or any other
    shape) are excluded so they cannot poison the high-water mark. The
    4-digit field is always parsed with ``int()`` (never compared as a
    string), so "0009" and "0036" in the same directory correctly yield 37,
    not a lexical artifact.

    Args:
        dir_path: Directory to scan (bugs/ or priority/). Missing or
                   non-directory paths yield 1 (an empty ledger's first slot).
        prefix:    Ledger prefix ("BUG" or "PRIORITY", with or without
                   trailing "-").

    Returns:
        The next free 1-based sequence number (int, not zero-padded).
    """
    prefix_u = _normalize_prefix(prefix)
    pattern = ledger_filename_re(prefix)
    num_re = re.compile(rf"^{re.escape(prefix_u)}-([0-9]{{4}})-", re.IGNORECASE)

    highest = 0
    d = Path(dir_path)
    if d.is_dir():
        for entry in d.iterdir():
            if not entry.is_file():
                continue
            if not pattern.match(entry.name):
                continue
            m = num_re.match(entry.name)
            if not m:
                continue
            n = int(m.group(1))
            if n > highest:
                highest = n
    return highest + 1


def build_remedy(name: str, prefix: str, dir_path: Union[str, Path]) -> str:
    """Build the prescriptive refusal message for a rejected ledger filename.

    Names the specific defect (date-first vs under-padded vs other) and the
    next free number, so the filer can retry with the corrected name in a
    single round trip. Never suggests auto-renaming — filing agents retry
    cheaply; renaming a file another process may still be writing is its own
    hazard.

    Args:
        name:     Bare filename that failed the ledger gate.
        prefix:   Ledger prefix ("BUG" or "PRIORITY", with or without
                  trailing "-").
        dir_path: Directory *name* was found in, used to compute the next
                  free number from the directory's other gate-valid entries.

    Returns:
        A one-line, human-actionable remedy string. Does not repeat *name*
        (callers already embed it in the surrounding "rejecting <name>: ..."
        log line).
    """
    prefix_u = _normalize_prefix(prefix)
    defect = classify_defect(name, prefix)
    next_free = next_free_number(dir_path, prefix)
    next_id = f"{prefix_u}-{next_free:04d}"

    if defect == DEFECT_DATE_FIRST:
        return (
            f"ledger names are number-first; task keys use dates, ledger "
            f"records do not. Expected {prefix_u}-NNNN-<slug>; next free "
            f"number is {next_id}."
        )

    if defect == DEFECT_UNDER_PADDED:
        m = re.match(
            rf"^{re.escape(prefix_u)}-([0-9]{{1,3}})-.+\.md$", name, re.IGNORECASE
        )
        raw_num = m.group(1) if m else "?"
        try:
            padded_guess = f"{prefix_u}-{int(raw_num):04d}"
        except ValueError:
            padded_guess = f"{prefix_u}-????"
        return (
            f"'{raw_num}' is under-padded; did you mean {padded_guess}? "
            f"Next free number is {next_id}."
        )

    if defect == DEFECT_OVER_PADDED:
        return (
            f"numeric field is not exactly 4 digits (expected "
            f"{prefix_u}-NNNN-<slug>); next free number is {next_id}."
        )

    # DEFECT_MALFORMED or anything unclassified.
    return (
        f"malformed filename (expected pattern: {prefix_u}-NNNN-<slug>.md, "
        f"optionally {prefix_u}-NNNN-YYYYMMDD-<slug>.md); next free number "
        f"is {next_id}."
    )
