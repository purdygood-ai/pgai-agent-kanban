"""
ledger.py — the append-only per-project deep-verify record.

Every run of team/scripts/deep-verify.sh (on demand today; scheduled and
pre-release triggers join it in later tickets) writes exactly one record
here: the timestamp, the project, the tree state exercised (version and
commit), the verdict, and the per-pair engine/flavor detail behind it. This
is the ledger that answers "when did this project last build its images
cold, and did it work?" from this file alone, without re-running the
thirteen-minute lane.

Record path convention: <kanban_root>/projects/<project_name>/
deep-verify-ledger.md -- a project-scoped kanban process-state file,
sibling to release-state.md and DEFER-LOW-BUGS. It is never committed into
the dev tree or the shipped repository ("Temporary File Hygiene" in
team/roles/CODER.md: verification provenance is kanban state,
not source).

Verdict vocabulary (exactly three values; see team/SOP-core.md "Gates
and Refusals" -- "Unusable build blocks; an unavailable build
environment does not" and
pgai_agent_kanban.deep_verify.classify for the classifier that derives
these from a cold-build lane run):

  built              -- the artefact built.
  failed-to-build    -- the artefact failed to build (a tree defect).
  could-not-attempt  -- the build could not be attempted (an environment
                         gap: no engine, unreachable registry, missing
                         toolchain) -- nothing was learned about whether
                         the images build. An exit-1 record can still name
                         a failing engine-free cold test whose bug the run
                         filed.

Two operations, both atomic (pgai_agent_kanban.lib.atomic_write: temp file
in the same directory + fsync + os.replace):

  - append_record(ledger_path, record) — writes a brand-new record. Nothing
    already in the file is touched; the write either lands the whole file
    (old content + new record) or does not happen at all.
  - No in-place mutation exists (unlike the OVERWATCH ledger's
    backfill_outcome) -- a deep-verify record is complete the moment it is
    written; there is no pending field that a later call fills in.

Read path -- the one reader every other consumer calls, never re-derived:

  - latest_record(ledger_path) — returns the most recent record as a dict,
    or None when the project has never been deep-verified ("never run").
    Raises LedgerError when the ledger file exists with non-whitespace
    content but that content does not parse into at least one well-formed
    record, or when the most recent record is missing a required field --
    a malformed ledger refuses rather than silently reporting "never run"
    or a fabricated verdict. Callers (the pre-release freshness gate, the
    scheduled dispatcher, TESTER's freshness line) all read through this
    one function so "which project state means what" never has a second
    implementation to drift out of sync with this one.

Usage (CLI):
    python3 ledger.py append <ledger_path> <project> <version> <commit> <verdict> <detail>
    python3 ledger.py latest <ledger_path>
    python3 ledger.py latest-fields <ledger_path>
    python3 ledger.py list <ledger_path>

``latest-fields`` prints latest_record_fields(): the most recent record as
``key=value`` lines a bash loop reads (``found=true`` then the six fields,
or ``found=false``); a malformed ledger prints
``deep_verify_ledger: <reason>`` on stderr and exits 1.

Usage (import):
    from pgai_agent_kanban.deep_verify.ledger import (
        DeepVerifyRecord, append_record, latest_record, ledger_path_for_project,
    )
    record = DeepVerifyRecord(
        timestamp="2026-09-03T09:15:22+00:00",
        project="pgai-agent-kanban",
        version="v2.9.12",
        commit="e240ee1",
        verdict="built",
        detail="docker/claude-code: built; podman/claude-code: built",
    )
    append_record(ledger_path, record)
    ...
    latest = latest_record(ledger_path)   # dict, or None if never run
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, fields

# Run by file path (``python3 .../ledger.py``) or loaded from a file outside
# its package, this module has no parent package and the interpreter puts only
# its own directory on sys.path, so the package import below would not resolve.
# Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Raised for structural problems with the ledger file: a record that
    fails to parse in a non-whitespace file, a most-recent record missing a
    required field, or an attempt to append a record with an out-of-
    vocabulary verdict."""


# ---------------------------------------------------------------------------
# Verdict vocabulary (see pgai_agent_kanban.deep_verify.classify for the
# single source of the three literal strings -- imported, never redefined).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - trivial import wiring
    from pgai_agent_kanban.deep_verify.classify import VALID_VERDICTS
except ImportError:  # pragma: no cover - defensive fallback, same values
    VALID_VERDICTS = ("built", "failed-to-build", "could-not-attempt")


# ---------------------------------------------------------------------------
# Record model
# ---------------------------------------------------------------------------

_RECORD_HEADER_RE = re.compile(
    r"^##\s+Deep-Verify Run:\s*(?P<timestamp>\S+)\s+—\s+(?P<project>\S+)\s+—\s+"
    r"(?P<version>\S+)\s*$"
)
_COMMIT_RE = re.compile(r"^-\s*Commit:\s*(?P<value>.*)$")
_VERDICT_RE = re.compile(r"^-\s*Verdict:\s*(?P<value>.*)$")
_DETAIL_RE = re.compile(r"^-\s*Detail:\s*(?P<value>.*)$")


@dataclass
class DeepVerifyRecord:
    """One deep-verify run's outcome.

    Required fields: timestamp (ISO 8601 UTC), project, version, commit,
    verdict (one of VALID_VERDICTS), detail (the per-pair engine/flavor
    detail behind the verdict -- the failing pair and step, the unavailable
    resource, or the pairs that built).
    """

    timestamp: str
    project: str
    version: str
    commit: str
    verdict: str
    detail: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "DeepVerifyRecord":
        """Build a record from a plain dict (parsed JSON, say), ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        """Return the record as a dict keyed by field name: timestamp, project, version, commit, verdict, detail."""
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _single_line(value: str) -> str:
    """Collapse any stray newline -- this ledger's field-parse format is
    one value per line; a multi-line value would corrupt the record shape."""
    return " ".join(str(value).split("\n"))


def format_record(record: DeepVerifyRecord) -> str:
    """Render *record* as a self-contained markdown block (no leading/
    trailing blank lines -- the caller controls spacing between records)."""
    return "\n".join(
        [
            f"## Deep-Verify Run: {record.timestamp} — {record.project} — "
            f"{record.version}",
            f"- Commit: {_single_line(record.commit)}",
            f"- Verdict: {_single_line(record.verdict)}",
            f"- Detail: {_single_line(record.detail)}",
        ]
    )


def _ledger_header(ledger_path: str) -> str:
    """Standard file header written once, when the ledger does not yet
    exist. Project name is derived from the ledger path's parent directory
    basename (the standard projects/<name>/deep-verify-ledger.md
    convention); falls back to a generic title otherwise."""
    project_name = os.path.basename(os.path.dirname(os.path.abspath(ledger_path)))
    title = (
        f"# Deep-Verify Ledger — {project_name}"
        if project_name
        else "# Deep-Verify Ledger"
    )
    return (
        f"{title}\n\n"
        "Append-only. One record per deep-verify run (on demand, scheduled, "
        "or pre-release). Records are written atomically, immediately after "
        "the run completes. Verdict is one of exactly three values: "
        "`built`, `failed-to-build`, `could-not-attempt` -- see "
        "team/SOP-core.md 'Gates and Refusals' -- 'Unusable build blocks; "
        "an unavailable build environment does not'. Read the most "
        "recent record with "
        "pgai_agent_kanban.deep_verify.ledger.latest_record() rather than "
        "re-deriving freshness from this file's raw text.\n"
    )


def _write_ledger_file(target: str, content: str) -> None:
    """Atomically replace *target* with *content* via the shared writer,
    creating its directory first. A write failure is a LedgerError.
    """
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    try:
        atomic_write(target, content, prefix=".deep_verify_ledger_tmp_")
    except OSError as exc:
        raise LedgerError(f"deep_verify.ledger: failed writing {target}: {exc}") from exc


# ---------------------------------------------------------------------------
# append_record
# ---------------------------------------------------------------------------


def append_record(ledger_path: str, record: DeepVerifyRecord) -> None:
    """Append *record* to the ledger at *ledger_path*, creating the file
    (with its standard header) if it does not yet exist. The write is
    atomic: a temp file is written in the same directory and renamed over
    the target, so the ledger is never observed half-written.

    Raises LedgerError when record.verdict is not one of VALID_VERDICTS --
    refusing here is cheaper than a downstream reader silently trusting a
    made-up verdict string.
    """
    if record.verdict not in VALID_VERDICTS:
        raise LedgerError(
            f"deep_verify.ledger: refusing to write verdict {record.verdict!r} -- "
            f"must be one of {VALID_VERDICTS}"
        )

    existing = ""
    if os.path.isfile(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as handle:
            existing = handle.read()

    block = format_record(record)
    if existing.strip():
        new_content = existing.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new_content = _ledger_header(ledger_path) + "\n" + block + "\n"

    _write_ledger_file(ledger_path, new_content)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _split_record_blocks(text: str) -> list:
    """Split *text* into (start_line_index, block_text) pairs, one per
    '## Deep-Verify Run: ...' header. Returns [] when no header matches
    (the caller decides what an empty result means -- see parse_records)."""
    lines = text.splitlines()
    header_indices = [i for i, line in enumerate(lines) if _RECORD_HEADER_RE.match(line)]
    blocks = []
    for idx, start in enumerate(header_indices):
        end = header_indices[idx + 1] if idx + 1 < len(header_indices) else len(lines)
        blocks.append((start, "\n".join(lines[start:end])))
    return blocks


def _parse_block(block_text: str) -> dict:
    lines = block_text.splitlines()
    header_match = _RECORD_HEADER_RE.match(lines[0]) if lines else None
    result = {
        "timestamp": header_match.group("timestamp") if header_match else None,
        "project": header_match.group("project") if header_match else None,
        "version": header_match.group("version") if header_match else None,
        "commit": None,
        "verdict": None,
        "detail": None,
    }
    for line in lines[1:]:
        commit_match = _COMMIT_RE.match(line)
        verdict_match = _VERDICT_RE.match(line)
        detail_match = _DETAIL_RE.match(line)
        if commit_match:
            result["commit"] = commit_match.group("value").strip()
        elif verdict_match:
            result["verdict"] = verdict_match.group("value").strip()
        elif detail_match:
            result["detail"] = detail_match.group("value").strip()
    return result


def parse_records(ledger_path: str) -> list:
    """Parse the ledger at *ledger_path* into a list of record dicts, in
    file (append) order.

    Returns [] when the file does not exist, or exists but is empty/
    whitespace-only (a fresh ledger that has never been written to reads
    the same as no file at all -- both mean "never run").

    Raises LedgerError when the file has non-whitespace content but that
    content contains no recognizable '## Deep-Verify Run: ...' header --
    this is the distinguishing signal between "never run" (no file, or an
    empty one) and "malformed" (a file whose content this parser cannot
    make sense of at all). A per-block missing FIELD (e.g. a Verdict line
    dropped) is NOT raised here -- it surfaces as a None value in the
    returned dict, and it is latest_record()'s job to decide that the most
    recent record is unusable; parse_records() itself never discards data
    a caller might still want to inspect (e.g. an audit tool listing every
    record, including a malformed one, for a human to fix by hand).
    """
    if not os.path.isfile(ledger_path):
        return []
    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return []
    blocks = _split_record_blocks(text)
    if not blocks:
        raise LedgerError(
            f"deep_verify.ledger: {ledger_path} has content but no recognizable "
            "'## Deep-Verify Run: ...' record header -- the file is malformed"
        )
    return [_parse_block(block_text) for _start, block_text in blocks]


def latest_record(ledger_path: str) -> dict | None:
    """Return the most recent record at *ledger_path* as a dict, or None
    when the project has never been deep-verified ("never run" -- no file,
    or an empty one; see parse_records()).

    Raises LedgerError when:
      - the ledger has content that does not parse at all (propagated from
        parse_records()), or
      - the most recent record is missing its timestamp/header, or its
        Verdict value is not one of the three canonical verdicts (absent,
        typo'd, or hand-edited into something else) -- a malformed most-
        recent record refuses rather than silently answering "never run"
        (which would let a promotion proceed on stale silence) or handing
        back a fabricated verdict (which would let a caller trust a value
        this module cannot vouch for).
    """
    records = parse_records(ledger_path)
    if not records:
        return None

    record = records[-1]
    if not record.get("timestamp"):
        raise LedgerError(
            f"deep_verify.ledger: most recent record in {ledger_path} has no "
            "parseable header (timestamp/project/version) -- ledger is malformed"
        )
    if record.get("verdict") not in VALID_VERDICTS:
        raise LedgerError(
            f"deep_verify.ledger: most recent record in {ledger_path} has "
            f"Verdict={record.get('verdict')!r}, not one of {VALID_VERDICTS} -- "
            "ledger is malformed"
        )
    return record


#: The fields latest_record_fields() prints after ``found=true``, in order.
LATEST_FIELD_KEYS = ("timestamp", "project", "version", "commit", "verdict", "detail")


def latest_record_fields(ledger_path: str) -> list:
    """Return the most recent record as ``key=value`` lines.

    ``["found=false"]`` when the project has never been deep-verified;
    otherwise ``found=true`` followed by one line per LATEST_FIELD_KEYS
    entry (an absent field reads empty, a newline inside a value reads as a
    space). Raises LedgerError as latest_record() does.
    """
    record = latest_record(ledger_path)
    if record is None:
        return ["found=false"]
    lines = ["found=true"]
    for key in LATEST_FIELD_KEYS:
        value = record.get(key) or ""
        lines.append(f"{key}={str(value).replace(chr(10), ' ')}")
    return lines


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def ledger_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard ledger path:
    <kanban_root>/projects/<project_name>/deep-verify-ledger.md."""
    return os.path.join(kanban_root, "projects", project_name, "deep-verify-ledger.md")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the deep-verify ledger CLI.

    ``append LEDGER PROJECT VERSION COMMIT VERDICT DETAIL`` writes a record and
    prints ``{"written": true}``; ``latest LEDGER`` prints the newest record as
    JSON (``{"found": false}`` when there is none); ``latest-fields LEDGER``
    prints it as ``key=value`` lines; ``list LEDGER`` prints every record as a
    JSON array. Exit codes: 0 done; 1 a ledger error (a malformed ledger or a
    verdict outside the vocabulary, named on stderr); 2 usage error (argparse).
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Append or read the append-only per-project deep-verify ledger. "
            "'append' writes a new record. 'latest' prints the most recent "
            "record as JSON, or {\"found\": false} when the project has "
            "never been deep-verified. 'list' prints every record as a JSON "
            "array."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_append = subparsers.add_parser("append", help="Append a new record.")
    p_append.add_argument(
        "ledger_path",
        help="path to the project's deep-verify-ledger.md (created on first write)",
    )
    p_append.add_argument("project", help="the project name the run verified")
    p_append.add_argument("version", help="the release version exercised, e.g. v2.24.0")
    p_append.add_argument("commit", help="the commit SHA exercised")
    p_append.add_argument(
        "verdict",
        help="the run's verdict: built, failed-to-build or could-not-attempt",
    )
    p_append.add_argument("detail", help="the per-pair engine/flavor detail behind the verdict")

    p_latest = subparsers.add_parser("latest", help="Print the most recent record as JSON.")
    p_latest.add_argument("ledger_path", help="path to the project's deep-verify-ledger.md")

    p_fields = subparsers.add_parser(
        "latest-fields", help="Print the most recent record as key=value lines (found=true|false first)."
    )
    p_fields.add_argument("ledger_path", help="path to the project's deep-verify-ledger.md")

    p_list = subparsers.add_parser("list", help="Print all records as JSON.")
    p_list.add_argument("ledger_path", help="path to the project's deep-verify-ledger.md")

    args = parser.parse_args()

    if args.command == "latest-fields":
        try:
            lines = latest_record_fields(args.ledger_path)
        except LedgerError as exc:
            print(f"deep_verify_ledger: {exc}", file=sys.stderr)
            return 1
        sys.stdout.write("".join(f"{line}\n" for line in lines))
        return 0

    try:
        if args.command == "append":
            record = DeepVerifyRecord(
                timestamp=_now_iso8601(),
                project=args.project,
                version=args.version,
                commit=args.commit,
                verdict=args.verdict,
                detail=args.detail,
            )
            append_record(args.ledger_path, record)
            sys.stdout.write(json.dumps({"written": True}) + "\n")
        elif args.command == "latest":
            record = latest_record(args.ledger_path)
            if record is None:
                sys.stdout.write(json.dumps({"found": False}) + "\n")
            else:
                payload = dict(record)
                payload["found"] = True
                sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        elif args.command == "list":
            records = parse_records(args.ledger_path)
            sys.stdout.write(json.dumps(records, indent=2, sort_keys=True) + "\n")
    except LedgerError as exc:
        print(f"deep_verify.ledger: {exc}", file=sys.stderr)
        return 1

    return 0


def _now_iso8601() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


if __name__ == "__main__":
    sys.exit(main())
