"""
auto_ship_notes.py -- the auto-ship line in a release's ``## Known Issues``.

When the project's ship posture lets a release ship through a ship-policy
trigger that would otherwise HALT it (team/scripts/lib/cm/
release_ship_posture.sh), the release notes say so. Each stop the posture
shipped through puts one line under ``## Known Issues``::

    Auto-shipped under ship_posture=always (would have halted: trigger 2 — cm-release.trigger-2.systemic-risk-high)

The line names the trigger number and the stable error key of the HALT the
release would otherwise have written, so a reader of the notes sees what
was waved through without opening release-state.md (whose ``## Auto-Ship
Event`` block records the same stop in full).

This module owns the line's wording and where it goes. Both release-notes
paths call it after they have written the notes file and before the file
is committed: the Step 4e stamp of WRITER-authored notes
(release_ship_policy.sh) and the Step 11a generator (release_notes.sh).

Placement rules (``place_auto_ship_lines``):

- The lines open the ``## Known Issues`` body, one per line, in the order
  given, ahead of whatever the section already says (the non-functional
  warning, a known-issue block, an author's own text), separated from it by
  one blank line.
- A body that says nothing -- empty, or only ``None`` / ``None.`` (any case)
  around HTML comments -- is replaced by the lines.
- With no ``## Known Issues`` heading at all, a new section is appended at
  the end of the file.
- A line the section already carries is not added again, so a re-run leaves
  the file byte-identical.
- The whitespace between the heading and the body, and between the body and
  the next heading, is kept as the file had it.

Usage (import):
    from pgai_agent_kanban.cm.auto_ship_notes import auto_ship_line, place_auto_ship_lines
    line = auto_ship_line("2", "cm-release.trigger-2.systemic-risk-high")
    new_text = place_auto_ship_lines(notes_text, [line])

Usage (CLI, consumed by release_ship_posture.sh's cm_ship_posture_stamp_notes):
    python3 -m pgai_agent_kanban.cm.auto_ship_notes <notes_file> --stop TRIGGER ERROR_KEY [--stop TRIGGER ERROR_KEY ...]
    Rewrites <notes_file> in place (atomically, keeping its file mode) and
    prints one indented line saying whether it placed the lines or found
    them already there. Exits 0 on success, 1 when the file cannot be read
    or written (the reason on stderr; the file is untouched), 2 on a usage
    error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Iterable, Optional

# Run by file path (``python3 .../auto_ship_notes.py``) or loaded from a file
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

from pgai_agent_kanban.lib.atomic_write import atomic_write  # noqa: E402

__all__ = ["auto_ship_line", "place_auto_ship_lines", "stamp_notes_file", "main"]

_KNOWN_ISSUES_RE = re.compile(r"^##[ \t]+Known[ \t]+Issues[ \t]*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^##[ \t]+\S", re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_EMPTY_BODY_RE = re.compile(r"none\.?", re.IGNORECASE)


def auto_ship_line(trigger: str, error_key: str) -> str:
    """The one Known Issues line for a stop the ship posture shipped through."""
    return (
        f"Auto-shipped under ship_posture=always "
        f"(would have halted: trigger {trigger} — {error_key})"
    )


def _body_says_nothing(body: str) -> bool:
    """True when *body* is empty or only "None"/"None." around HTML comments."""
    content = _HTML_COMMENT_RE.sub("", body).strip()
    return not content or bool(_EMPTY_BODY_RE.fullmatch(content))


def place_auto_ship_lines(text: str, lines: Iterable[str]) -> str:
    """Return *text* with *lines* opening its ``## Known Issues`` section.

    See the module docstring for the placement rules. Returns *text*
    unchanged when every line is already in the section or *lines* is empty.
    """
    wanted = [line for line in lines if line]
    heading = _KNOWN_ISSUES_RE.search(text)
    if heading is None:
        if not wanted:
            return text
        prefix = text if not text or text.endswith("\n") else text + "\n"
        return prefix + "\n## Known Issues\n\n" + "\n".join(wanted) + "\n"

    body_start = heading.end()
    following = _NEXT_HEADING_RE.search(text, body_start)
    body_end = following.start() if following else len(text)
    body = text[body_start:body_end]

    present = {line.strip() for line in body.splitlines()}
    missing = [line for line in wanted if line not in present]
    if not missing:
        return text

    stripped = body.strip()
    lead = body[: len(body) - len(body.lstrip())] or "\n"
    trail = body[len(body.rstrip()):] if stripped else ""
    if not trail:
        trail = "\n\n" if following else "\n"
    if "\n" not in lead:
        lead = "\n"
    new_content = "\n".join(missing)
    if not _body_says_nothing(body):
        new_content += "\n\n" + stripped
    return text[:body_start] + lead + new_content + trail + text[body_end:]


def stamp_notes_file(notes_file: pathlib.Path, stops: Iterable[tuple]) -> bool:
    """Place one auto-ship line per ``(trigger, error_key)`` stop in *notes_file*.

    Returns True when the file was rewritten, False when it already carried
    every line. Raises OSError when the file cannot be read or written.
    """
    text = notes_file.read_text(encoding="utf-8")
    updated = place_auto_ship_lines(text, [auto_ship_line(t, k) for t, k in stops])
    if updated == text:
        return False
    atomic_write(notes_file, updated, prefix=".auto_ship_notes_tmp_", preserve_mode=True)
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.auto_ship_notes",
        description=(
            "Put one 'Auto-shipped under ship_posture=always (would have halted: "
            "trigger N — <error key>)' line per ship-policy stop under a "
            "release-notes file's '## Known Issues' section, in place."
        ),
        epilog=(
            "Exit codes: 0 placed or already present; 1 the notes file could not "
            "be read or written (left untouched); 2 usage error."
        ),
    )
    parser.add_argument("notes_file", help="Release-notes file to rewrite in place.")
    parser.add_argument(
        "--stop",
        nargs=2,
        action="append",
        metavar=("TRIGGER", "ERROR_KEY"),
        required=True,
        help=(
            "One stop the ship posture shipped through: its trigger number and "
            "the error key of the HALT it would have written. Repeat for each "
            "stop, in the order they happened."
        ),
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """Run the auto-ship notes CLI: stamp one Known Issues line per ``--stop``.

    ``python3 -m pgai_agent_kanban.cm.auto_ship_notes NOTES_FILE --stop TRIGGER
    ERROR_KEY [--stop ...]``. Exit codes: 0 the lines were placed or were
    already present; 1 the notes file could not be read or written (it is left
    untouched); 2 usage error (argparse).
    """
    args = _build_parser().parse_args(argv)
    notes_file = pathlib.Path(args.notes_file)
    try:
        changed = stamp_notes_file(notes_file, [tuple(stop) for stop in args.stop])
    except OSError as exc:
        print(
            f"auto_ship_notes: could not update {notes_file}: {exc}. The file is "
            "unchanged; add the line(s) under '## Known Issues' by hand.",
            file=sys.stderr,
        )
        return 1
    if changed:
        print(f"  Stamped auto-ship line(s) under ## Known Issues in release-notes/{notes_file.name}", flush=True)
    else:
        print(f"  release-notes/{notes_file.name} already carries the auto-ship line(s) under ## Known Issues", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
