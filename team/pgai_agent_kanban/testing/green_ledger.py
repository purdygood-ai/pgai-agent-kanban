"""
green_ledger.py -- read and append the gated runners' per-RC green ledger.

``team/scripts/lib/green_ledger.sh`` owns the ledger's location, the reuse
rules and every ``[green-ledger]`` narration line; its header describes the
record fields and when a recorded run is reused. This module holds the two
steps that parse or write the ledger file itself, which the bash library
calls through ``pp_run_ops``:

* the reuse verdict -- given this run's commit, suite, marker selection and
  findings hash, what the most recent matching record says;
* the record append -- one JSON Lines record per certification run, moving
  a malformed ledger aside first so one bad write does not disable reuse.

A ledger record is a JSON object carrying at least ``commit``, ``suite``,
``exit`` and ``findings_sha``; a non-blank line that is anything else makes
the whole ledger malformed.

Usage:
    python3 -m pgai_agent_kanban.testing.green_ledger verdict -- LEDGER COMMIT SUITE SELECTION FINDINGS_SHA
    python3 -m pgai_agent_kanban.testing.green_ledger record -- LEDGER COMMIT SUITE SELECTION EXIT_CODE FINDINGS_SHA TREE_CLEAN

Verbs:
  verdict  Print one ``<verdict>\\t<detail>`` line and exit 0 (see
           ``reuse_verdict``): ``reuse`` with the record's recorded_at,
           or ``norecord`` / ``mismatch`` / ``corrupt`` with a sentence
           naming why the recorded run cannot be reused.
  record   Append one record (TREE_CLEAN is ``true`` or anything else for
           false) and print ``ok\\t<note>``, where the note is empty or
           names where a malformed ledger was moved; on a write failure,
           or an EXIT_CODE that is not an integer, print
           ``failed\\t<ExceptionName>: <text>``. Exit 0 either way.

``--help`` (on its own or after a verb) prints usage and exits 0.

Public API:
    REQUIRED_FIELDS
    is_ledger_record(record) -> bool
    reuse_verdict(path, commit, suite, selection, findings_sha) -> tuple[str, str]
    append_record(path, commit, suite, selection, exit_code, findings_sha,
                  tree_clean, now=None) -> str
    main(argv=None) -> int
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone
from typing import Optional

# Run by file path, this module has no parent package and only its own
# directory is on sys.path: put the package root this file sits in first,
# so the import below resolves from the same tree, never another. Imported
# as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.subcommand_cli import subcommand_main  # noqa: E402

__all__ = [
    "REQUIRED_FIELDS",
    "append_record",
    "is_ledger_record",
    "main",
    "reuse_verdict",
]

#: Keys every ledger record carries; a line missing any is not a record.
REQUIRED_FIELDS = ("commit", "suite", "exit", "findings_sha")


def is_ledger_record(record: object) -> bool:
    """Return True when *record* is a dict carrying every ``REQUIRED_FIELDS`` key."""
    return isinstance(record, dict) and all(key in record for key in REQUIRED_FIELDS)


def _parse_line(line: str) -> object:
    """Return the JSON value of *line*, or None when it is not JSON."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def reuse_verdict(
    path: str, commit: str, suite: str, selection: str, findings_sha: str
) -> tuple:
    """Decide whether the ledger at *path* holds a green run this run may reuse.

    The most recent record whose ``commit`` and ``suite`` equal *commit* and
    *suite* and whose ``selection`` (empty when absent) equals *selection*
    is the one consulted. Returns ``(verdict, detail)``:

    * ``("reuse", recorded_at)`` -- that record exited 0 on a clean tree
      with the same findings hash; *recorded_at* is the record's own
      timestamp, or ``"at an unknown time"`` when it has none;
    * ``("norecord", text)`` -- no record matches;
    * ``("mismatch", text)`` -- the matching record exited non-zero, ran on
      a tree with uncommitted changes, or carries a different findings hash
      (checked in that order);
    * ``("corrupt", text)`` -- the file cannot be read as UTF-8 text, or a
      non-blank line is not a ledger record (the first such line is named).

    Blank lines are skipped. A missing file reads as unreadable, so callers
    check existence first.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return "corrupt", f"ledger {path} is unreadable ({exc.__class__.__name__})"
    records = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        record = _parse_line(line)
        if not is_ledger_record(record):
            return "corrupt", f"ledger {path} line {number} is not a ledger record"
        records.append(record)
    matching = [
        r for r in records
        if r["commit"] == commit and r["suite"] == suite and r.get("selection", "") == selection
    ]
    if not matching:
        return (
            "norecord",
            f"no recorded {suite} run at commit {commit[:12]} with marker selection \"{selection}\"",
        )
    last = matching[-1]
    recorded_exit = last["exit"]
    recorded_sha = str(last["findings_sha"])
    recorded_at = last.get("recorded_at") or "at an unknown time"
    if recorded_exit != 0:
        return "mismatch", f"the most recent {suite} run at commit {commit[:12]} exited {recorded_exit}"
    if last.get("tree_clean") is not True:
        return (
            "mismatch",
            f"the most recent {suite} run at commit {commit[:12]} ran on a tree with uncommitted changes",
        )
    if recorded_sha != findings_sha:
        return (
            "mismatch",
            f"the lint findings hash differs from the recorded run at commit {commit[:12]} "
            f"({recorded_sha[:12]} recorded, {findings_sha[:12]} now)",
        )
    return "reuse", f"{recorded_at}"


def _ledger_is_malformed(path: str) -> bool:
    """True when *path* cannot be read as UTF-8 or holds a non-blank line that is not a record."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                if not is_ledger_record(_parse_line(line)):
                    return True
    except (OSError, UnicodeDecodeError):
        return True
    return False


def append_record(
    path: str,
    commit: str,
    suite: str,
    selection: str,
    exit_code: "int | str",
    findings_sha: str,
    tree_clean: bool,
    now: Optional[datetime] = None,
) -> str:
    """Append one run's record to the ledger at *path* and return a note.

    The ledger's directory is created when absent. When the existing file is
    malformed (see ``reuse_verdict``) it is first moved to
    ``<path>.malformed``, replacing an older one, and the returned note is
    ``"; the malformed ledger was moved aside to <path>.malformed"``;
    otherwise the note is empty. The record holds ``recorded_at`` (*now*,
    default the current UTC time, ISO 8601 to the second), ``commit``,
    ``suite``, ``selection``, ``exit`` (``int(exit_code)``),
    ``findings_sha`` and ``tree_clean``, serialised with sorted keys as one
    line written with a single ``O_APPEND`` write.

    Raises OSError when the directory, the move or the write fails, and
    ValueError when *exit_code* is not an integer; a malformed ledger has
    already been moved aside when the ValueError is raised.
    """
    note = ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and _ledger_is_malformed(path):
        os.replace(path, path + ".malformed")
        note = f"; the malformed ledger was moved aside to {path}.malformed"
    stamp = now if now is not None else datetime.now(timezone.utc)
    record = {
        "recorded_at": stamp.isoformat(timespec="seconds"),
        "commit": commit,
        "suite": suite,
        "selection": selection,
        "exit": int(exit_code),
        "findings_sha": findings_sha,
        "tree_clean": bool(tree_clean),
    }
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)
    return note


def _cmd_verdict(args: argparse.Namespace) -> int:
    verdict, detail = reuse_verdict(
        args.ledger, args.commit, args.suite, args.selection, args.findings_sha
    )
    print(f"{verdict}\t{detail}")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        note = append_record(
            args.ledger,
            args.commit,
            args.suite,
            args.selection,
            args.exit_code,
            args.findings_sha,
            args.tree_clean == "true",
        )
    except (OSError, ValueError) as exc:
        print(f"failed\t{exc.__class__.__name__}: {exc}")
        return 0
    print(f"ok\t{note}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.testing.green_ledger",
        description="Read the reuse verdict from, or append a record to, the gated runners' green ledger.",
    )
    verbs = parser.add_subparsers(dest="verb", metavar="VERB")
    verbs.required = True

    verdict = verbs.add_parser(
        "verdict",
        help="Print whether the ledger holds a reusable green run.",
        description="Print one '<verdict>\\t<detail>' line: reuse, norecord, mismatch or corrupt.",
    )
    verdict.add_argument("ledger", help="The ledger.jsonl path.")
    verdict.add_argument("commit", help="This run's git HEAD.")
    verdict.add_argument("suite", help="unit or integration.")
    verdict.add_argument("selection", help="The pytest -m marker expression, e.g. 'not quarantine'.")
    verdict.add_argument("findings_sha", help="sha256 of this run's quality-class lint findings.")
    verdict.set_defaults(func=_cmd_verdict)

    record = verbs.add_parser(
        "record",
        help="Append this run's record to the ledger.",
        description="Append one record and print 'ok\\t<note>' or 'failed\\t<reason>'.",
    )
    record.add_argument("ledger", help="The ledger.jsonl path.")
    record.add_argument("commit", help="This run's git HEAD.")
    record.add_argument("suite", help="unit or integration.")
    record.add_argument("selection", help="The pytest -m marker expression, e.g. 'not quarantine'.")
    record.add_argument("exit_code", help="The runner's exit code, e.g. 0.")
    record.add_argument("findings_sha", help="sha256 of this run's quality-class lint findings.")
    record.add_argument("tree_clean", help="'true' when the working tree was clean before pytest.")
    record.set_defaults(func=_cmd_record)

    return parser


main = subcommand_main(_build_parser)


if __name__ == "__main__":
    sys.exit(main())
