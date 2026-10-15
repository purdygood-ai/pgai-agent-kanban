#!/usr/bin/env python3
"""
release_notes_blocks.py — the known-issue ship's release-notes renderers and stamps.

The CM known-issue-ship stage (team/scripts/lib/cm/release_known_issue.sh)
calls these four commands through ``pp_run_ops``; each prints exactly what
that stage used to print and writes only the files named on its command
line.

Commands (``python3 -m pgai_agent_kanban.cm.release_notes_blocks <command> ...``):

  known-issues BUG_FILE [BUG_FILE ...]
      Print the "## Known Issues" block: one bullet per readable bug file,
      ``- **<Public ID>** — <substance> — No workaround available.`` when the
      bug carries a ``KI-`` public ID, otherwise ``- <substance>
      (internal-only, not yet disclosed) — No workaround available.``. The
      substance is the first sentence of the bug's ``## Symptom`` (HTML
      comments removed) with every internal ID scrubbed out.

  bugs-skipped BUG_FILE RATIONALE [BUG_FILE RATIONALE ...]
      Print one ``| substance | public ID | SEVERITY | rationale |`` table row
      per readable bug file. Every cell is flattened to one line with ``|``
      escaped; the public ID reads ``internal-only`` when absent, the
      severity is the first word of ``## Severity`` upper-cased
      (``UNSPECIFIED`` when absent), and the rationale is scrubbed. An odd
      argument count is refused with exit 1.

  stamp-status NOTES_FILE TARGET_STATUS [KNOWN_ISSUE_BLOCK] [BUGS_SKIPPED_BLOCK]
      Replace the ``## Status`` PENDING-RELEASE placeholder with
      TARGET_STATUS (pgai_agent_kanban.cm.notes_status_section), and in the
      same write replace the ``## Known Issues`` body with KNOWN_ISSUE_BLOCK
      (only for KNOWN-ISSUE or NON-FUNCTIONAL with a non-empty block) and the
      ``## Bugs Skipped`` body with a non-empty BUGS_SKIPPED_BLOCK; a missing
      section is appended. Prints one ``Stamped ...`` line per record. Exits
      1 with a WARNING on stderr, writing nothing, when the placeholder is
      not found.

  carry-forward ACTIVE_RC BUG_FILE [BUG_FILE ...]
      Write ACTIVE_RC under each bug file's ``## Known Issue Carry Forward``
      heading, adding the section when absent and never replacing a value
      already there (an HTML comment in the section is kept and the value
      goes after it). Prints one ``[Step 4e]`` summary line.

The internal-ID scrub is the one ``internal_id_scrub.py`` shared with the
release-notes generator, loaded the way that generator loads it
(PGAI_CM_INTERNAL_ID_SCRUB_DIR, else this tree's scripts/lib/cm/).

A wrong argument count prints the usage on stderr and exits 2; ``--help``
prints this text and exits 0.
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Sequence

# Run by file path (``python3 .../release_notes_blocks.py``) this module has
# no parent package and only its own directory is on sys.path, so the
# package imports below would not resolve. Put this file's own package root
# -- the directory holding the pgai_agent_kanban/ this file sits in -- at the
# front of sys.path so they resolve from the same tree, never another.
# Imported as a package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.notes_status_section import stamp_status_placeholder  # noqa: E402
from pgai_agent_kanban.cm.release_notes_generator import (  # noqa: E402
    _first_sentence,
    _parse_section,
    _strip_internal_ids,
)

__all__ = [
    "bugs_skipped_rows",
    "known_issues_block",
    "stamp_carry_forward",
    "stamp_release_notes",
]

_NO_SYMPTOM = "An unresolved defect (no Symptom text recorded in the bug report)."
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_STATUSES = ("KNOWN-ISSUE", "NON-FUNCTIONAL")
_CARRY_FORWARD_RE = re.compile(r"(^##\s+Known Issue Carry Forward\s*$)([\s\S]*?)(?=^##\s+|\Z)", re.MULTILINE)


def _substance(text: str) -> str:
    """The first sentence of a bug's ``## Symptom``, comments removed and IDs scrubbed."""
    raw = _COMMENT_RE.sub("", _parse_section(text, "Symptom"))
    return _strip_internal_ids(_first_sentence(raw)) or _NO_SYMPTOM


def _public_id(text: str) -> str:
    """A bug's ``## Public ID`` value, backticks trimmed and IDs scrubbed."""
    return _strip_internal_ids(_parse_section(text, "Public ID").strip("` \n"))


def _first_token(text: str) -> str:
    """The first whitespace-delimited word of *text*, or ``""``."""
    stripped = text.strip()
    return stripped.split()[0] if stripped else ""


def _table_cell(value: str) -> str:
    """*value* as one table cell: whitespace collapsed to single spaces, ``|`` escaped."""
    return " ".join(value.split()).replace("|", "\\|")


def _read(path: str) -> "str | None":
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def known_issues_block(bug_files: Sequence[str]) -> str:
    """The "## Known Issues" bullets for *bug_files*, one per readable file, newline-joined."""
    lines = []
    for path in bug_files:
        text = _read(path)
        if text is None:
            continue
        desc = _substance(text)
        public_id = _public_id(text)
        if public_id and re.match(r"^KI-", public_id, re.IGNORECASE):
            lines.append(f"- **{public_id}** — {desc} — No workaround available.")
        else:
            lines.append(f"- {desc} (internal-only, not yet disclosed) — No workaround available.")
    return "\n".join(lines)


def bugs_skipped_rows(pairs: Sequence[str]) -> str:
    """The "## Bugs Skipped" table rows for flat ``bug_file, rationale`` *pairs*, newline-joined.

    Raises:
        ValueError: when *pairs* has an odd length.
    """
    if len(pairs) % 2:
        raise ValueError("bug_file/rationale pairs expected (got an odd number of arguments)")
    rows = []
    for index in range(0, len(pairs), 2):
        text = _read(pairs[index])
        if text is None:
            continue
        public_id = _public_id(text)
        severity = _strip_internal_ids(_first_token(_parse_section(text, "Severity"))) or "unspecified"
        rows.append(
            f"| {_table_cell(_substance(text))} | {_table_cell(public_id or 'internal-only')} | "
            f"{_table_cell(severity.upper())} | {_table_cell(_strip_internal_ids(pairs[index + 1]))} |"
        )
    return "\n".join(rows)


def _replace_or_append(text: str, heading: str, block: str) -> str:
    """*text* with the body of ``## <heading>`` replaced by *block*, or the section appended."""
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*$)([\s\S]*?)(?=^##\s+|\Z)", re.MULTILINE)
    match = pattern.search(text)
    if match:
        return text[: match.start(2)] + "\n\n" + block + "\n\n" + text[match.end(2):]
    if not text.endswith("\n"):
        text += "\n"
    return text + f"\n## {heading}\n\n{block}\n"


def stamp_release_notes(
    notes_file: str, target_status: str, known_issue_block: str = "", bugs_skipped_block: str = ""
) -> "list[str] | None":
    """Stamp *notes_file* in one write; return the narration lines, or None when there is no placeholder.

    See the ``stamp-status`` command for what is replaced. Nothing is written
    when None is returned.
    """
    notes_path = pathlib.Path(notes_file)
    stamped = stamp_status_placeholder(notes_path.read_text(encoding="utf-8"), target_status)
    if stamped is None:
        return None
    lines = [f"  Stamped ## Status: {target_status} in release-notes/{notes_path.name}"]
    if target_status in _BLOCK_STATUSES and known_issue_block:
        stamped = _replace_or_append(stamped, "Known Issues", known_issue_block)
        lines.append(f"  Stamped ## Known Issues in release-notes/{notes_path.name}")
    if bugs_skipped_block:
        stamped = _replace_or_append(stamped, "Bugs Skipped", bugs_skipped_block)
        lines.append(f"  Stamped ## Bugs Skipped in release-notes/{notes_path.name}")
    notes_path.write_text(stamped, encoding="utf-8")
    return lines


def stamp_carry_forward(active_rc: str, bug_files: Sequence[str]) -> str:
    """Write *active_rc* as each bug file's carry-forward marker; return the summary line."""
    stamped = already_set = 0
    for bug_file in bug_files:
        bug_path = pathlib.Path(bug_file)
        text = bug_path.read_text(encoding="utf-8")
        match = _CARRY_FORWARD_RE.search(text)
        if match is None:
            new_text = text.rstrip("\n") + f"\n\n## Known Issue Carry Forward\n{active_rc}\n"
        else:
            body = match.group(2)
            if re.sub(r"<!--[\s\S]*?-->", "", body).strip():
                already_set += 1
                continue
            comment = re.search(r"<!--[\s\S]*?-->", body)
            if comment:
                at = match.start(2) + comment.end()
                new_text = text[:at] + f"\n{active_rc}\n" + text[at:]
            else:
                at = match.start(2)
                new_text = text[:at] + f"\n{active_rc}\n" + text[at:].lstrip("\n")
        bug_path.write_text(new_text, encoding="utf-8")
        stamped += 1
    if stamped == 0 and already_set > 0:
        return (
            "[Step 4e] step 4e: already complete, continuing — known-issue carry-forward marker "
            f"already set on {already_set} bug(s)."
        )
    if stamped == 0:
        return "[Step 4e] Known-issue carry-forward marker: nothing stamped (no bug files resolved)."
    return f"[Step 4e] Known-issue carry-forward marker: {stamped} stamped, {already_set} already set."


_USAGE = (
    "usage: python3 -m pgai_agent_kanban.cm.release_notes_blocks\n"
    "         known-issues BUG_FILE [BUG_FILE ...]\n"
    "       | bugs-skipped BUG_FILE RATIONALE [BUG_FILE RATIONALE ...]\n"
    "       | stamp-status NOTES_FILE TARGET_STATUS [KNOWN_ISSUE_BLOCK] [BUGS_SKIPPED_BLOCK]\n"
    "       | carry-forward ACTIVE_RC BUG_FILE [BUG_FILE ...]"
)


def main(argv: "Sequence[str] | None" = None) -> int:
    """CLI entry point; see the module docstring. Returns the exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = (args[0], args[1:]) if args else ("", [])
    if command == "known-issues":
        print(known_issues_block(rest))
        return 0
    if command == "bugs-skipped":
        try:
            print(bugs_skipped_rows(rest))
        except ValueError as exc:
            print(f"ERROR: build_bugs_skipped_notes_block requires <bug_file> <rationale> pairs ({exc}).",
                  file=sys.stderr)
            return 1
        return 0
    if command == "stamp-status" and 2 <= len(rest) <= 4:
        lines = stamp_release_notes(*rest)
        if lines is None:
            print("WARNING: PENDING-RELEASE placeholder not found under ## Status heading.", file=sys.stderr)
            return 1
        print("\n".join(lines), flush=True)
        return 0
    if command == "carry-forward" and rest:
        print(stamp_carry_forward(rest[0], rest[1:]), flush=True)
        return 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
