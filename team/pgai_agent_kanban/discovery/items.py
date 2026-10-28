#!/usr/bin/env python3
"""
items.py — intake-item scans for the discovery pipeline's bash stages.

Each command prints exactly what its bash caller captures and writes its
narration to stderr; none of them moves, renames or quarantines a file (the
bash stage does that from the output).

Commands (``python3 -m pgai_agent_kanban.discovery.items <command> ...``):

  scan DIR ID_PREFIX [REJECTED_FILE] [REQUIREMENTS_DIR]
      Print one absolute path per line for each item in DIR that is a
      candidate for the next bundle, in file-name order. An item is a
      candidate when its file name matches the canonical pattern for
      ID_PREFIX (``BUG-`` or ``PRIORITY-``; ``PRIORITY-`` also accepts a
      ``vX.Y.Z-bugfix-<slug>.md`` name) and its ``## Status`` is ``open``
      or absent. A ``running`` item that no bundle in REQUIREMENTS_DIR
      lists at all is orphaned -- the bundle that set it running was
      archived, purged or deleted -- and is offered again, with a
      ``WARNING:`` stderr line naming it. Every other item is left out:
      a ``needs-operator`` item (waiting on an operator answer) and one in
      a closed state (``done``, ``wont-do``, ``duplicate``) with a
      ``skipping <name>: ...`` stderr line; a ``running`` item every
      listing bundle has closed -- carried, which is how a release leaves
      an item it shipped without fixing (see
      pgai_agent_kanban.lib.bundle_items) -- with a ``skipping <name>:
      carried ...`` stderr line, since the discovery pass converts it to
      ``needs-operator`` (pgai_agent_kanban.discovery.carried) rather than
      bundling it again; and a ``running`` item a live bundle lists,
      silently. The scan itself writes no item file. A name
      that does not match is reported on stderr as
      ``ERROR: rejecting <name>: <reason>`` and, when REJECTED_FILE is
      given, appended there as ``<name>\\t<reason>``. The backlog cache is
      not consulted: its markers are derived from the same headers.
      REQUIREMENTS_DIR defaults to the ``requirements`` directory beside
      DIR.
  record-rejection STATE_FILE NAME COUNT WHEN [REASON]
      Rewrite STATE_FILE with NAME's row set to ``NAME\\tCOUNT\\tWHEN\\tREASON``
      (appended last), keeping every other row as written.
  forget-rejection STATE_FILE NAME
      Rewrite STATE_FILE without NAME's row. A missing file is left missing.
  locate-quarantined PROJECT_ROOT REJECTED_DIR NAME
      Print ``<path>\t<restore-dir>`` for the quarantined file called NAME:
      where it sits now -- REJECTED_DIR (the project's rejected/ directory)
      first, then the legacy priority/.rejected/, bugs/.rejected/ and
      requirements/.rejected/ under PROJECT_ROOT -- and the intake directory
      recovery moves it back into, read from its .reason sidecar
      (original_dir, then original_type) or else its BUG-/PRIORITY- prefix.
      <restore-dir> is empty when its origin cannot be told. Prints nothing
      when no quarantined file has that name (see
      pgai_agent_kanban.dashboard._rejected.find_quarantined).
  collisions BUGS_DIR SCANNER_DIR
      Print one ``WARNING: <message>`` line on stderr per BUG-NNNN number
      two bug files share, using detect_duplicate_bug_ids from the
      lib/bug_scanner.py under SCANNER_DIR (the pm-agent directory).
  order FILES
      Print the newline-separated item lines in FILES (each a path,
      optionally followed by a tab and an annotation) with every item whose
      file carries a filled ``## Known Issue Carry Forward`` field first,
      each group in its given order. Blank lines are dropped.
  severity FILE
      Print the text after ``**Severity:**`` in FILE, stripped, or an empty
      line when there is none.
  bug-ages
      Read one bug file path per line on stdin and print
      ``<path>\t<age>\t<date>`` for each, in input order: <date> is the
      text after the file's ``**Date:**`` header line, stripped, and <age>
      is the whole number of days from that date to today (UTC). Only the
      leading ``YYYY-MM-DD`` of <date> is read, so a full ISO timestamp
      works too. <age> is ``-`` when the header is missing, the date
      cannot be parsed, or the file cannot be read (never empty, so a bash
      ``read`` splitting on tabs keeps the columns). Blank input lines are
      skipped.
  task-state STATUS_FILE
      Print the ``## State`` value of STATUS_FILE upper-cased, or
      ``UNPARSEABLE`` when the section is absent or empty.
  task-states TASKS_DIR
      Read one task ID per line on stdin and print ``<ID>\t<STATE>`` for
      each, in input order: STATE is what ``task-state`` prints for
      ``TASKS_DIR/<ID>/status.md``, or ``NO-FOLDER`` when that file does not
      exist. Blank input lines are skipped. One process answers a whole
      queue, which keeps the discovery chain-busy check to a few seconds on
      a project with hundreds of queue entries.

Every command exits 0 on success; a wrong argument count prints the usage
on stderr and exits 2. ``--help`` prints this text and exits 0.
"""

from __future__ import annotations

import datetime
import pathlib
import re
import sys
import warnings
from typing import Optional, TextIO

# Run by file path (``python3 .../items.py``) or loaded from a file outside
# its package, this module has no parent package and the interpreter puts
# only its own directory on sys.path, so the package import below would not
# resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.dashboard._rejected import find_quarantined  # noqa: E402
from pgai_agent_kanban.lib import bundle_items  # noqa: E402
from pgai_agent_kanban.lib.bug_filing import load_bug_scanner  # noqa: E402
from pgai_agent_kanban.lib.ledger_filename_contract import (  # noqa: E402
    build_remedy as _ledger_build_remedy,
    ledger_filename_re as _ledger_filename_re,
)
from pgai_agent_kanban.lib.terminal_states import (  # noqa: E402
    NEEDS_OPERATOR_STATE,
    TERMINAL_STATES,
    canonical_state,
)
from pgai_agent_kanban.task_status import read_section, section_value  # noqa: E402

__all__ = [
    "bug_ages",
    "bug_filed_date",
    "bug_severity",
    "forget_rejection",
    "order_bundle_items",
    "record_rejection",
    "report_bug_id_collisions",
    "scan_unhandled_items",
    "task_state",
    "task_states",
]

# TESTER Path C filenames produced in priority/: v<X.Y.Z>-bugfix-<slug>.md,
# accepted in addition to PRIORITY- names when the prefix is "PRIORITY-".
_PATH_C_RE = re.compile(r'^v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+\.md$', re.IGNORECASE)

# BUG- and PRIORITY- are siblings: both use the exact-4-digit,
# date-optional-AFTER-the-number form from ledger_filename_contract. Any
# other prefix falls back to the historical loose pattern.
_LEDGER_PREFIXES = {"BUG-", "PRIORITY-"}


def scan_unhandled_items(
    dir_path: pathlib.Path,
    id_prefix: str,
    rejected_file: str = "",
    requirements_dir: Optional[pathlib.Path] = None,
    *,
    err: TextIO = sys.stderr,
) -> list[str]:
    """Return the absolute paths of the bundling candidates in *dir_path*.

    See the ``scan`` command in the module docstring for the rules. Writes
    rejection and skip narration to *err*, and ``<name>\\t<reason>`` lines to
    *rejected_file* when it is non-empty.
    """
    if requirements_dir is None:
        requirements_dir = dir_path.parent / "requirements"
    listings = bundle_items.bundle_listings(requirements_dir)
    carried = bundle_items.carried_names_from(listings)
    listed = frozenset(listings)

    if id_prefix.upper() in _LEDGER_PREFIXES:
        canonical_re = _ledger_filename_re(id_prefix)
        path_c_re_active = _PATH_C_RE if id_prefix.upper() == "PRIORITY-" else None
    else:
        canonical_re = re.compile(rf'^{re.escape(id_prefix)}\d{{4,}}-.+\.md$', re.IGNORECASE)
        path_c_re_active = None
    expected_pattern = f"{id_prefix}NNNN-<slug>.md"

    rejected_fh = open(rejected_file, "w", encoding="utf-8") if rejected_file else None
    results: list[str] = []
    try:
        for entry in sorted(dir_path.iterdir()):
            if not entry.is_file() or not entry.name.endswith(".md"):
                continue
            if entry.name == "README.md":
                continue
            if "templates" in entry.parts or "rejected" in entry.parts:
                continue

            is_path_c = path_c_re_active is not None and path_c_re_active.match(entry.name)
            if not canonical_re.match(entry.name) and not is_path_c:
                # Refuse-and-prescribe: name the defect and the next free
                # number; never rename. stderr only -- stdout is the
                # validated-items stream the caller bundles.
                if id_prefix.upper() in _LEDGER_PREFIXES:
                    rejection_reason = _ledger_build_remedy(entry.name, id_prefix, dir_path)
                else:
                    rejection_reason = f"malformed filename (expected pattern: {expected_pattern})"
                print(f"ERROR: rejecting {entry.name}: {rejection_reason}", file=err)
                if rejected_fh is not None:
                    rejected_fh.write(entry.name + "\t" + rejection_reason + "\n")
                continue

            # The ## Status header is the sole gate; a missing header reads
            # as open. The closed states (lib.terminal_states: done,
            # wont-do, duplicate, with the retired "superseded" read as
            # wont-do) are resolved for good, and needs-operator waits on
            # an operator answer that sets it back to open. running is in
            # flight -- unless every bundle listing the item has closed,
            # which means a release shipped it unfixed (carried: the
            # discovery pass converts it to needs-operator, never bundles
            # it again), or no bundle lists it at all, which means its
            # bundle left requirements/ while it was unfinished (orphaned:
            # offered again).
            text = entry.read_text(encoding="utf-8", errors="replace")
            status_tokens = section_value(text, "Status").split()
            if status_tokens:
                status = status_tokens[0].lower()
                if canonical_state(status) in TERMINAL_STATES:
                    print(f"skipping {entry.name}: terminal state '{status}'", file=err)
                    continue
                if canonical_state(status) == NEEDS_OPERATOR_STATE:
                    print(
                        f"skipping {entry.name}: '{status}' (waiting on an operator-inbox answer,"
                        " which sets it back to open)",
                        file=err,
                    )
                    continue
                if status == "running":
                    if bundle_items.is_carried(status, entry.name, carried):
                        print(
                            f"skipping {entry.name}: carried -- every bundle listing it closed with"
                            f" it still 'running'; the discovery pass converts it to"
                            f" {NEEDS_OPERATOR_STATE} instead of bundling it again",
                            file=err,
                        )
                        continue
                    if bundle_items.is_orphaned(status, entry.name, listed):
                        print(
                            f"WARNING: re-offering {entry.name}: its ## Status is 'running' but no"
                            f" bundle in {requirements_dir} lists it (the bundle that set it"
                            " running was archived, purged or deleted)",
                            file=err,
                        )
                    else:
                        continue
            results.append(str(entry.resolve()))
    finally:
        if rejected_fh is not None:
            rejected_fh.close()
    return results


def record_rejection(state_file: pathlib.Path, filename: str, new_count: str, now: str, reason: str = "") -> None:
    """Set *filename*'s row in the rejection-count TSV, appended last."""
    lines = []
    if state_file.is_file():
        for line in state_file.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] == filename:
                continue
            lines.append(line)
    lines.append(f"{filename}\t{new_count}\t{now}\t{reason}")
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def forget_rejection(state_file: pathlib.Path, filename: str) -> None:
    """Drop *filename*'s row from the rejection-count TSV, if the file exists."""
    if state_file.is_file():
        lines = [
            line for line in state_file.read_text(encoding="utf-8").splitlines()
            if not (line.split("\t")[0] == filename)
        ]
        state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report_bug_id_collisions(bugs_dir: str, scanner_dir: str, *, err: TextIO = sys.stderr) -> None:
    """Write one ``WARNING:`` line to *err* per duplicated BUG number.

    *scanner_dir* is the pm-agent directory; its lib/bug_scanner.py is
    loaded by file path (bug_filing.load_bug_scanner), so the result does
    not depend on which top-level ``lib`` is on sys.path, and sys.path is
    left unchanged.
    """
    try:
        detect_duplicate_bug_ids = load_bug_scanner(scanner_dir).detect_duplicate_bug_ids
    except ImportError as e:
        print(f"WARNING: _disc_check_bug_id_collisions: could not import bug_scanner: {e}", file=err)
        return
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        detect_duplicate_bug_ids(bugs_dir)
    for w in caught:
        print(f"WARNING: {w.message}", file=err)


def _carries_known_issue(line: str) -> bool:
    """True when the item a bundle line names has a filled Known Issue Carry
    Forward field (a non-empty body, as task_status.read_section reads it).
    An unreadable file or an empty field reads as unmarked."""
    try:
        text = pathlib.Path(line.split("\t", 1)[0]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(read_section(text, "Known Issue Carry Forward"))


def order_bundle_items(files_raw: str) -> list[str]:
    """Return the non-blank lines of *files_raw*, marked items first."""
    lines = [line for line in files_raw.splitlines() if line.strip()]
    marked = [line for line in lines if _carries_known_issue(line)]
    unmarked = [line for line in lines if not _carries_known_issue(line)]
    return marked + unmarked


_SEVERITY_RE = re.compile(r'\*\*Severity:\*\*\s*(.+)', re.IGNORECASE)


def bug_severity(path: str) -> str:
    """The ``**Severity:**`` text of the bug file at *path*, or ``""``."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    m = _SEVERITY_RE.search(text)
    return m.group(1).strip() if m else ""


_DATE_LINE_RE = re.compile(r'^[ \t]*\*\*Date:\*\*[ \t]*(.*)$', re.IGNORECASE | re.MULTILINE)
_LEADING_DATE_RE = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})')


def bug_filed_date(path: str) -> tuple[Optional[datetime.date], str]:
    """The date on the ``**Date:**`` header line of the bug file at *path*.

    Returns ``(date, raw)``: *raw* is the header's text, stripped (``""``
    when the line is absent or the file cannot be read); *date* is its
    leading ``YYYY-MM-DD`` parsed, or ``None`` when that is missing or not a
    real calendar date.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None, ""
    m = _DATE_LINE_RE.search(text)
    if not m:
        return None, ""
    raw = m.group(1).strip()
    d = _LEADING_DATE_RE.match(raw)
    if not d:
        return None, raw
    try:
        return datetime.date(int(d.group(1)), int(d.group(2)), int(d.group(3))), raw
    except ValueError:
        return None, raw


def bug_ages(paths: list[str], today: Optional[datetime.date] = None) -> list[tuple[str, Optional[int], str]]:
    """``(path, age_days, raw_date)`` for each non-blank path, in order.

    *age_days* is ``(today - filed date).days`` (UTC today by default), or
    ``None`` when :func:`bug_filed_date` finds no parseable date.
    """
    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
    out: list[tuple[str, Optional[int], str]] = []
    for raw_path in paths:
        path = raw_path.strip()
        if not path:
            continue
        filed, raw = bug_filed_date(path)
        out.append((path, None if filed is None else (today - filed).days, raw))
    return out


def task_state(status_file: str) -> str:
    """The upper-cased ``## State`` body of *status_file*, or UNPARSEABLE."""
    body = read_section(pathlib.Path(status_file).read_text(), "State")
    if body is None:
        return "UNPARSEABLE"
    return body.strip().upper() or "UNPARSEABLE"


def task_states(tasks_dir: str, task_ids: list[str]) -> list[tuple[str, str]]:
    """``(task_id, state)`` for each non-blank id, in order.

    *state* is :func:`task_state` of ``<tasks_dir>/<task_id>/status.md``, or
    ``NO-FOLDER`` when that file does not exist.
    """
    root = pathlib.Path(tasks_dir)
    out: list[tuple[str, str]] = []
    for raw in task_ids:
        task_id = raw.strip()
        if not task_id:
            continue
        status_file = root / task_id / "status.md"
        if not status_file.is_file():
            out.append((task_id, "NO-FOLDER"))
            continue
        out.append((task_id, task_state(str(status_file))))
    return out


_USAGE = {
    "scan": "scan DIR ID_PREFIX [REJECTED_FILE] [REQUIREMENTS_DIR]",
    "record-rejection": "record-rejection STATE_FILE NAME COUNT WHEN [REASON]",
    "forget-rejection": "forget-rejection STATE_FILE NAME",
    "locate-quarantined": "locate-quarantined PROJECT_ROOT REJECTED_DIR NAME",
    "collisions": "collisions BUGS_DIR SCANNER_DIR",
    "order": "order FILES",
    "severity": "severity FILE",
    "bug-ages": "bug-ages  (bug file paths on stdin)",
    "task-state": "task-state STATUS_FILE",
    "task-states": "task-states TASKS_DIR  (task IDs on stdin)",
}
_ARITY = {
    "scan": (2, 4),
    "record-rejection": (4, 5),
    "forget-rejection": (2, 2),
    "locate-quarantined": (3, 3),
    "collisions": (2, 2),
    "order": (1, 1),
    "severity": (1, 1),
    "bug-ages": (0, 0),
    "task-state": (1, 1),
    "task-states": (1, 1),
}


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    command, rest = args[0], args[1:]
    arity = _ARITY.get(command)
    if arity is None or not arity[0] <= len(rest) <= arity[1]:
        print("usage: python3 -m pgai_agent_kanban.discovery.items "
              + (_USAGE.get(command) or "{" + ",".join(_USAGE) + "} ..."), file=sys.stderr)
        return 2
    if command == "scan":
        requirements_dir = pathlib.Path(rest[3]) if len(rest) > 3 and rest[3] else None
        for path in scan_unhandled_items(
            pathlib.Path(rest[0]), rest[1], rest[2] if len(rest) > 2 else "", requirements_dir,
        ):
            print(path)
    elif command == "record-rejection":
        record_rejection(pathlib.Path(rest[0]), rest[1], rest[2], rest[3], rest[4] if len(rest) > 4 else "")
    elif command == "forget-rejection":
        forget_rejection(pathlib.Path(rest[0]), rest[1])
    elif command == "locate-quarantined":
        quarantined = find_quarantined(rest[0], rest[1], rest[2])
        if quarantined is not None:
            print(f"{quarantined.path}\t{quarantined.restore_dir or ''}")
    elif command == "collisions":
        report_bug_id_collisions(rest[0], rest[1])
    elif command == "order":
        for line in order_bundle_items(rest[0]):
            print(line)
    elif command == "severity":
        print(bug_severity(rest[0]))
    elif command == "bug-ages":
        for path, age, raw in bug_ages(sys.stdin.read().splitlines()):
            print(f"{path}\t{'-' if age is None else age}\t{raw}")
    elif command == "task-state":
        print(task_state(rest[0]))
    elif command == "task-states":
        for task_id, state in task_states(rest[0], sys.stdin.read().splitlines()):
            print(f"{task_id}\t{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
