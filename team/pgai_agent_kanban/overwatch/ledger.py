"""
ledger.py — The append-only auto-resolutions ledger.

Every time OVERWATCH's resolution stage (a separate module — it executes
the remediation; this module only records it) executes a matrix-prescribed
resolution, it writes one entry here: timestamp, RC, attempt N -> N+1,
trigger, findings (bug IDs), the matrix row matched, the actions taken, and
evidence PATHS (never duplicated report/bug content — this ledger is the
index, TESTER reports and bug files remain the record, per
team/roles/OVERWATCH.md's "Reversible-only" authority-boundary clause).

Two operations, both atomic (temp file in the same directory + os.replace,
same pattern as pgai_agent_kanban.ops.write._atomic_write):

  - append_entry(ledger_path, entry) — writes a brand-new entry. Nothing
    already in the file is touched; the write either lands the whole file
    (old content + new entry) or does not happen at all.
  - backfill_outcome(ledger_path, entry_id, outcome) — updates exactly one
    entry's Outcome field in place, once, from "pending" to a terminal
    value ("SHIPPED attempt N" / "CEILING-BLOCKED"). No other field in any
    entry is ever rewritten or deleted. Calling it twice with the same
    outcome is a no-op (idempotent); calling it with a different outcome
    than what is already recorded raises LedgerError rather than silently
    overwriting history.

Decide-and-log atomicity ("never log-after") is the CALLER's
responsibility to sequence: the execution stage must call append_entry()
as part of the same operation as performing the remediation, not as an
afterthought once the remediation has already "succeeded" by some other
measure. What THIS module guarantees is that the write of a single entry's
text is atomic — a crash mid-write leaves the ledger exactly as it was
before the call, never a half-written entry. Either ordering the caller
chooses (log-then-act or act-then-log) is a valid outcome, and OVERWATCH's
next sweep reconciles a killed-mid-execution resolution either way.

Usage (CLI):
    python3 ledger.py append <ledger_path> <entry.json>
    python3 ledger.py backfill-outcome <ledger_path> <entry_id> <outcome>
    python3 ledger.py backfill-family <ledger_path> <family_key> <outcome>
    python3 ledger.py list <ledger_path>

Usage (import):
    from pgai_agent_kanban.overwatch.ledger import LedgerEntry, append_entry, backfill_outcome
    entry = LedgerEntry(
        timestamp="2026-07-27T18:03:00Z",
        rc="v2.0.1",
        attempt_from=1,
        attempt_to=2,
        trigger="ship-policy-halt",
        findings=["BUG-NNNN-slug.md"],
        matrix_row="CANCEL-AND-REBUNDLE",
        actions_taken=["cm-cancel-rc --yes v2.0.1", "wontdo <task-id>"],
        evidence_paths=["/kanban/projects/p/tasks/CM-.../artifacts/report.md"],
        family_key="family-abc123",
    )
    entry_id = append_entry(ledger_path, entry)
    ...
    backfill_outcome(ledger_path, entry_id, "SHIPPED attempt 2")
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, fields
from typing import Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LedgerError(Exception):
    """Raised for structural problems with the ledger file or a lookup that
    cannot be satisfied: the ledger is missing when a backfill is attempted,
    an entry_id has no matching entry, or a backfill would silently
    overwrite an already-recorded, DIFFERENT outcome value.
    """


# ---------------------------------------------------------------------------
# Entry model
# ---------------------------------------------------------------------------


_PENDING = "pending"

_ENTRY_HEADER_RE = re.compile(
    r"^##\s+Entry:\s*(?P<timestamp>\S+)\s+—\s+(?P<rc>\S+)\s+—\s+attempt\s+"
    r"(?P<attempt_from>\d+)\s*(?:->|→)\s*(?P<attempt_to>\d+)\s*$"
)
_ENTRY_ID_RE = re.compile(r"^<!--\s*entry-id:\s*(?P<entry_id>\S+)\s*-->\s*$")
_FAMILY_KEY_RE = re.compile(r"^<!--\s*family-key:\s*(?P<family_key>\S+)\s*-->\s*$")
_TRIGGER_RE = re.compile(r"^-\s*Trigger:\s*(?P<value>.*)$")
_FINDINGS_RE = re.compile(r"^-\s*Findings:\s*(?P<value>.*)$")
_MATRIX_ROW_RE = re.compile(r"^-\s*Matrix Row Matched:\s*(?P<value>.*)$")
_OUTCOME_RE = re.compile(r"^-\s*Outcome:\s*(?P<value>.*)$")
_ACTIONS_HEADER_RE = re.compile(r"^-\s*Actions Taken:\s*$")
_EVIDENCE_HEADER_RE = re.compile(r"^-\s*Evidence:\s*$")
_BULLET_RE = re.compile(r"^\s{2,}-\s*(?P<value>.*)$")


@dataclass
class LedgerEntry:
    """One resolution event.

    Required fields: timestamp, RC, attempt N to N+1, trigger, findings
    (bug IDs), matrix row matched, actions taken, evidence PATHS.
    ``family_key`` and ``entry_id`` support later lookup/backfill;
    ``entry_id`` is derived deterministically from the other fields when
    not supplied. ``outcome`` starts unset (rendered as "pending") and is
    set only via backfill_outcome()/backfill_family_outcome().
    """

    timestamp: str
    rc: str
    attempt_from: int
    attempt_to: int
    trigger: str
    matrix_row: str
    findings: list = field(default_factory=list)
    actions_taken: list = field(default_factory=list)
    evidence_paths: list = field(default_factory=list)
    family_key: Optional[str] = None
    entry_id: Optional[str] = None
    outcome: Optional[str] = None

    def resolved_entry_id(self) -> str:
        """Return entry_id, computing a deterministic default if unset.

        The default is a short sha256 digest over the fields that make a
        resolution event unique (rc, attempt transition, timestamp) — stable
        across re-serialization, independent of Python dict/hash ordering.
        """
        if self.entry_id:
            return self.entry_id
        basis = f"{self.rc}|{self.attempt_from}|{self.attempt_to}|{self.timestamp}"
        digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
        return f"entry-{digest}"

    @classmethod
    def from_dict(cls, data: dict) -> "LedgerEntry":
        """Construct from a plain dict (e.g. parsed JSON), ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["entry_id"] = self.resolved_entry_id()
        return data


# ---------------------------------------------------------------------------
# Formatting (entry -> markdown block)
# ---------------------------------------------------------------------------


def _format_list_field(label: str, values: list) -> str:
    lines = [f"- {label}:"]
    for value in values:
        lines.append(f"  - {value}")
    if not values:
        lines.append("  - none")
    return "\n".join(lines)


def format_entry(entry: LedgerEntry) -> str:
    """Render *entry* as a self-contained markdown block (no leading/trailing
    blank lines — the caller controls spacing between entries).
    """
    entry_id = entry.resolved_entry_id()
    findings_display = ", ".join(entry.findings) if entry.findings else "none"
    outcome_display = entry.outcome if entry.outcome else _PENDING
    lines = [
        f"## Entry: {entry.timestamp} — {entry.rc} — attempt "
        f"{entry.attempt_from} → {entry.attempt_to}",
        f"<!-- entry-id: {entry_id} -->",
        f"<!-- family-key: {entry.family_key or 'none'} -->",
        f"- Trigger: {entry.trigger}",
        f"- Findings: {findings_display}",
        f"- Matrix Row Matched: {entry.matrix_row}",
        _format_list_field("Actions Taken", entry.actions_taken),
        _format_list_field("Evidence", entry.evidence_paths),
        f"- Outcome: {outcome_display}",
    ]
    return "\n".join(lines)


def _ledger_header(ledger_path: str) -> str:
    """Standard file header written once, when the ledger does not yet exist.

    Project name is derived from the ledger path's parent directory basename
    (the standard projects/<name>/auto-resolutions.md convention); falls
    back to a generic title if the path does not follow that shape.
    """
    project_name = os.path.basename(os.path.dirname(os.path.abspath(ledger_path)))
    title = f"# Auto-Resolutions Ledger — {project_name}" if project_name else "# Auto-Resolutions Ledger"
    return (
        f"{title}\n\n"
        "Append-only. One entry per OVERWATCH autonomous resolution event. "
        "Entries are written atomically, alongside the action they "
        "describe. The `Outcome` field is "
        "backfilled once, at the bundle family's terminal state (`SHIPPED "
        "attempt N` or `CEILING-BLOCKED`) — no other field is ever "
        "rewritten or deleted. Evidence lines are paths only: this ledger "
        "is the index, not the record — TESTER reports and bug files "
        "remain the source of truth for their own content.\n"
    )


# ---------------------------------------------------------------------------
# Atomic write primitive (same pattern as pgai_agent_kanban.ops.write._atomic_write)
# ---------------------------------------------------------------------------


def _atomic_write(target: str, content: str) -> None:
    """Write *content* to *target* atomically: a temp file in the same
    directory, written and closed, then renamed over the target with
    os.replace (atomic on POSIX). Mirrors
    pgai_agent_kanban.ops.write._atomic_write's pattern exactly.
    """
    target_dir = os.path.dirname(os.path.abspath(target))
    os.makedirs(target_dir, exist_ok=True)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".ledger_tmp_", dir=target_dir)
        os.close(fd)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, target)
        tmp_path = None
    except OSError as exc:
        raise LedgerError(f"ledger.py: failed writing {target}: {exc}") from exc
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# append_entry
# ---------------------------------------------------------------------------


def append_entry(ledger_path: str, entry: LedgerEntry) -> str:
    """Append *entry* to the ledger at *ledger_path*, creating the file (with
    its standard header) if it does not yet exist. The write is atomic: a
    temp file is written in the same directory and renamed over the target,
    so the ledger is never observed half-written.

    Returns the entry's entry_id (computed if the caller did not supply one)
    so the caller can pass it to backfill_outcome() later.
    """
    existing = ""
    if os.path.isfile(ledger_path):
        with open(ledger_path, "r", encoding="utf-8") as handle:
            existing = handle.read()

    block = format_entry(entry)
    if existing.strip():
        new_content = existing.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new_content = _ledger_header(ledger_path) + "\n" + block + "\n"

    _atomic_write(ledger_path, new_content)
    return entry.resolved_entry_id()


# ---------------------------------------------------------------------------
# Parsing (ledger -> structured entries, for backfill lookups and for the
# read-only tmux/browser surfaces this ledger feeds)
# ---------------------------------------------------------------------------


def _split_entry_blocks(text: str) -> list:
    """Split the ledger's raw text into (header_line_index, block_text) pairs,
    one per "## Entry: ..." section. Returns [] when there are no entries yet.
    """
    lines = text.splitlines()
    header_indices = [i for i, line in enumerate(lines) if _ENTRY_HEADER_RE.match(line)]
    blocks = []
    for idx, start in enumerate(header_indices):
        end = header_indices[idx + 1] if idx + 1 < len(header_indices) else len(lines)
        blocks.append((start, "\n".join(lines[start:end])))
    return blocks


def _parse_block(block_text: str) -> dict:
    lines = block_text.splitlines()
    header_match = _ENTRY_HEADER_RE.match(lines[0]) if lines else None
    result = {
        "timestamp": header_match.group("timestamp") if header_match else None,
        "rc": header_match.group("rc") if header_match else None,
        "attempt_from": int(header_match.group("attempt_from")) if header_match else None,
        "attempt_to": int(header_match.group("attempt_to")) if header_match else None,
        "entry_id": None,
        "family_key": None,
        "trigger": None,
        "findings": [],
        "matrix_row": None,
        "actions_taken": [],
        "evidence_paths": [],
        "outcome": None,
    }

    i = 0
    while i < len(lines):
        line = lines[i]
        entry_id_match = _ENTRY_ID_RE.match(line)
        family_match = _FAMILY_KEY_RE.match(line)
        trigger_match = _TRIGGER_RE.match(line)
        findings_match = _FINDINGS_RE.match(line)
        matrix_match = _MATRIX_ROW_RE.match(line)
        outcome_match = _OUTCOME_RE.match(line)

        if entry_id_match:
            result["entry_id"] = entry_id_match.group("entry_id")
        elif family_match:
            value = family_match.group("family_key")
            result["family_key"] = None if value == "none" else value
        elif trigger_match:
            result["trigger"] = trigger_match.group("value").strip()
        elif findings_match:
            value = findings_match.group("value").strip()
            result["findings"] = [] if value == "none" else [v.strip() for v in value.split(",")]
        elif matrix_match:
            result["matrix_row"] = matrix_match.group("value").strip()
        elif outcome_match:
            value = outcome_match.group("value").strip()
            result["outcome"] = None if value == _PENDING else value
        elif _ACTIONS_HEADER_RE.match(line):
            i += 1
            while i < len(lines) and _BULLET_RE.match(lines[i]):
                value = _BULLET_RE.match(lines[i]).group("value").strip()
                if value != "none":
                    result["actions_taken"].append(value)
                i += 1
            continue
        elif _EVIDENCE_HEADER_RE.match(line):
            i += 1
            while i < len(lines) and _BULLET_RE.match(lines[i]):
                value = _BULLET_RE.match(lines[i]).group("value").strip()
                if value != "none":
                    result["evidence_paths"].append(value)
                i += 1
            continue
        i += 1

    return result


def parse_entries(ledger_path: str) -> list:
    """Parse the ledger at *ledger_path* into a list of entry dicts, in file
    (append) order. Returns [] when the file does not exist or has no
    entries yet (a fresh ledger with only the header, or no file at all).

    This is the read path the tmux (T8) and browser (T9) Auto-Resolutions
    surfaces are expected to use — it never duplicates report content, only
    what this module itself wrote.
    """
    if not os.path.isfile(ledger_path):
        return []
    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return [_parse_block(block_text) for _start, block_text in _split_entry_blocks(text)]


def find_entry_ids_by_family(ledger_path: str, family_key: str) -> list:
    """Return the entry_ids (in file order) of every entry tagged with
    *family_key*. Empty list when the ledger has no entries for that family
    (including when the ledger does not exist yet).
    """
    return [
        entry["entry_id"]
        for entry in parse_entries(ledger_path)
        if entry.get("family_key") == family_key
    ]


# ---------------------------------------------------------------------------
# backfill_outcome / backfill_family_outcome
# ---------------------------------------------------------------------------


def backfill_outcome(ledger_path: str, entry_id: str, outcome: str) -> bool:
    """Set the Outcome field of the entry identified by *entry_id* to
    *outcome*, in place. The write is atomic (whole-file temp+rename), and
    no field of any OTHER entry is touched.

    Returns True when the file was rewritten (the entry was "pending" and is
    now set to *outcome*). Returns False when the entry already carries
    exactly *outcome* (idempotent no-op — safe to call again after a crash
    or retry). Raises LedgerError when:
      - the ledger file does not exist,
      - no entry with *entry_id* is found,
      - the entry already carries a DIFFERENT outcome than *outcome* (backfill
        never silently overwrites a previously-recorded terminal state).
    """
    if not os.path.isfile(ledger_path):
        raise LedgerError(f"ledger.py: cannot backfill — ledger not found: {ledger_path}")

    with open(ledger_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    lines = text.splitlines()
    blocks = _split_entry_blocks(text)

    target_start = None
    target_end = None
    for idx, (start, block_text) in enumerate(blocks):
        if re.search(
            rf"^<!--\s*entry-id:\s*{re.escape(entry_id)}\s*-->\s*$",
            block_text,
            re.MULTILINE,
        ):
            target_start = start
            target_end = blocks[idx + 1][0] if idx + 1 < len(blocks) else len(lines)
            break

    if target_start is None:
        raise LedgerError(f"ledger.py: no entry found with entry_id={entry_id!r} in {ledger_path}")

    block_lines = lines[target_start:target_end]
    current_outcome = None
    outcome_line_idx = None
    for offset, line in enumerate(block_lines):
        match = _OUTCOME_RE.match(line)
        if match:
            value = match.group("value").strip()
            current_outcome = None if value == _PENDING else value
            outcome_line_idx = offset
            break

    if outcome_line_idx is None:
        raise LedgerError(
            f"ledger.py: entry {entry_id!r} has no Outcome line — ledger file is malformed"
        )

    if current_outcome == outcome:
        return False
    if current_outcome is not None:
        raise LedgerError(
            f"ledger.py: entry {entry_id!r} already has Outcome={current_outcome!r}; "
            f"refusing to overwrite with {outcome!r} (backfill is single-shot)"
        )

    block_lines[outcome_line_idx] = f"- Outcome: {outcome}"
    lines[target_start:target_end] = block_lines
    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write(ledger_path, new_text)
    return True


def backfill_family_outcome(ledger_path: str, family_key: str, outcome: str) -> list:
    """Backfill *outcome* onto every still-pending entry tagged *family_key*.

    A bundle family may accumulate more than one resolution entry across
    several cancel/rebundle cycles before it either ships or hits the retry
    ceiling; once the terminal state is known, every entry in that family's
    story gets the same terminal Outcome. Already-backfilled entries
    (matching a prior call) are skipped rather than re-raising — this
    function is the batch-idempotent counterpart of backfill_outcome().

    Returns the list of entry_ids that were actually rewritten (pending ->
    outcome). An empty list means either the family has no entries, or every
    entry was already backfilled to this exact outcome.
    """
    updated = []
    for entry_id in find_entry_ids_by_family(ledger_path, family_key):
        if backfill_outcome(ledger_path, entry_id, outcome):
            updated.append(entry_id)
    return updated


# ---------------------------------------------------------------------------
# Path convention helper
# ---------------------------------------------------------------------------


def ledger_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard ledger path: <kanban_root>/projects/<project_name>/auto-resolutions.md."""
    return os.path.join(kanban_root, "projects", project_name, "auto-resolutions.md")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Append or read the append-only auto-resolutions ledger. "
            "'append' writes a new entry from a LedgerEntry JSON file. "
            "'backfill-outcome' / 'backfill-family' set the Outcome field of "
            "one entry or every pending entry in a family. 'list' prints all "
            "entries as JSON."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_append = subparsers.add_parser("append", help="Append a new entry from JSON.")
    p_append.add_argument("ledger_path")
    p_append.add_argument("entry_json", help="Path to a LedgerEntry JSON file.")

    p_backfill = subparsers.add_parser("backfill-outcome", help="Backfill one entry's Outcome.")
    p_backfill.add_argument("ledger_path")
    p_backfill.add_argument("entry_id")
    p_backfill.add_argument("outcome")

    p_backfill_family = subparsers.add_parser(
        "backfill-family", help="Backfill Outcome for every pending entry in a family."
    )
    p_backfill_family.add_argument("ledger_path")
    p_backfill_family.add_argument("family_key")
    p_backfill_family.add_argument("outcome")

    p_list = subparsers.add_parser("list", help="Print all entries as JSON.")
    p_list.add_argument("ledger_path")

    args = parser.parse_args()

    if args.command == "append":
        data = json.loads(open(args.entry_json, "r", encoding="utf-8").read())
        entry = LedgerEntry.from_dict(data)
        entry_id = append_entry(args.ledger_path, entry)
        sys.stdout.write(json.dumps({"entry_id": entry_id}) + "\n")
    elif args.command == "backfill-outcome":
        changed = backfill_outcome(args.ledger_path, args.entry_id, args.outcome)
        sys.stdout.write(json.dumps({"changed": changed}) + "\n")
    elif args.command == "backfill-family":
        updated = backfill_family_outcome(args.ledger_path, args.family_key, args.outcome)
        sys.stdout.write(json.dumps({"updated_entry_ids": updated}) + "\n")
    elif args.command == "list":
        entries = parse_entries(args.ledger_path)
        sys.stdout.write(json.dumps(entries, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
