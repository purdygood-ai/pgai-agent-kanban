"""
halt_provenance.py — who wrote a project HALT: the chain or an operator.

OVERWATCH may clear a HALT the chain wrote once its cause is resolved, and
must never touch one an operator wrote. This module is the one reader that
tells them apart. It reads the HALT file's body and, for a HALT with no
body, the sentinel-writes log (``pgai_agent_kanban.lib.sentinel_provenance``).
It reads only and never writes, and anything it cannot prove was written by
the chain reads as the operator's.

The provenance block
--------------------
Every chain HALT writer puts these comment lines in the file it creates,
after its own first line (which stays the human-readable header the HALT
readers display)::

    # halt-writer: <writer>
    # halt-reason: <one line: why the chain stopped>
    # halt-trigger: <CM trigger number>        (CM trigger HALTs only)

The chain writers (CHAIN_WRITERS):

    cm-release            cm_halt in team/scripts/lib/cm/release_preflight.sh;
                          the reason is the trigger's error key, e.g.
                          ``cm-release.trigger-5.squash-conflict``
    halt-after-promotion  pgai_agent_kanban.halt_after.promote
    overwatch-repair      overwatch_write_repair_halt in
                          team/scripts/lib/overwatch_protocol.sh
    overwatch-runaway     pgai_agent_kanban.overwatch.runaway, when a
                          runaway counter passes its threshold; the
                          reason names the counter
    zero-progress-cap     reserved for a zero-progress-cap HALT writer

Bash writers print the lines themselves; Python writers call
:func:`provenance_lines`. A Python writer that ends its body with a
``# Resume:`` line builds the command with :func:`unhalt_command` (project
scope) or :func:`unhalt_global_command` (the kanban root), which name the
installed script under the kanban root. Any Python message or remedy that
tells the operator to halt or unhalt builds its command the same way, with
:func:`halt_command` / :func:`halt_global_command` for the halt side, and a
remedy that names any other operator script builds it with
:func:`installed_script_command`. The operator writers (``halt.sh`` /
``halt-global.sh``, through ``ops.write.halt`` / ``halt_global``) create an
empty HALT and write no block.

Classification
--------------
:func:`classify_halt` answers in this order:

1. No file -> ``absent``.
2. A file that cannot be read -> ``operator``.
3. A ``# halt-writer:`` line naming a chain writer -> ``chain``, with its
   reason and trigger. A writer outside CHAIN_WRITERS -> ``operator``.
4. Bodies written before the block existed are still read as the chain's:
   the CM header ``# HALT created by CM at ...`` (reason from its
   ``# error:`` or ``# Reason:`` line) and the OVERWATCH repair marker
   ``# overwatch-repair-halt: ... fn=<fn>``.
5. Any other non-blank body -> ``operator``.
6. A blank body is looked up in ``<kanban_root>/logs/sentinel-writes.log``:
   the latest record for the path has ``op=promote`` and its digest still
   matches the file -> ``chain`` (an empty HALT-AFTER promotion);
   ``op=halt`` or ``halt_global`` -> ``operator``; no record, or any other
   record -> ``operator``.

The CM trigger number comes from the ``# halt-trigger:`` line, else from the
reason (``trigger-5`` / ``trigger 5``); a letter suffix is kept (``5b``).

The fleet-scoped HALT directly under the kanban root has only operator
writers (an operator, the global halt, and a HALT-AFTER an operator armed
at the root), so it is always ``operator``, whatever its body says; the
writer and reason found in it are still reported.

Lines are matched liberally: any spacing around ``#`` and ``:``, any letter
case in the key, ``_`` or ``-`` in a writer name, CRLF endings.

Public API
----------
    ORIGIN_CHAIN, ORIGIN_OPERATOR, ORIGIN_ABSENT
    WRITER_CM_RELEASE, WRITER_HALT_AFTER_PROMOTION, WRITER_OVERWATCH_REPAIR,
    WRITER_OVERWATCH_RUNAWAY, WRITER_ZERO_PROGRESS_CAP, CHAIN_WRITERS
    provenance_lines(writer, reason, trigger=None) -> list[str]
    installed_script_command(kanban_root, script, *args) -> str
    halt_command(kanban_root, project) -> str
    halt_global_command(kanban_root) -> str
    unhalt_command(kanban_root, project) -> str
    unhalt_global_command(kanban_root) -> str
    trigger_number(text) -> str | None
    classify_halt(halt_path, *, kanban_root=None) -> HaltProvenance

Command line (reads only)::

    python3 -m pgai_agent_kanban.overwatch.halt_provenance classify HALT_PATH \\
        [--kanban-root DIR] [--json]

Prints ``origin<TAB>writer<TAB>reason<TAB>trigger<TAB>source`` (``-`` for a
field with no value), or the full result as JSON with ``--json``, and exits
0. Exit 2 is a usage error.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import shlex
import sys
from pathlib import Path
from typing import Optional, Union

# Started by file path (python3 .../overwatch/halt_provenance.py) the module
# has no parent package and only its own directory on sys.path: put this
# tree's own package root first so the package imports below resolve from
# the same tree, never another. Imported as a package member, sys.path is
# untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.sentinel_provenance import (  # noqa: E402
    kanban_root_for_scope,
    latest_sentinel_record,
    record_describes_sentinel,
)
from pgai_agent_kanban.lib.text import one_line  # noqa: E402

__all__ = [
    "CHAIN_WRITERS",
    "ORIGIN_ABSENT",
    "ORIGIN_CHAIN",
    "ORIGIN_OPERATOR",
    "WRITER_CM_RELEASE",
    "WRITER_HALT_AFTER_PROMOTION",
    "WRITER_OVERWATCH_REPAIR",
    "WRITER_OVERWATCH_RUNAWAY",
    "WRITER_ZERO_PROGRESS_CAP",
    "HaltProvenance",
    "classify_halt",
    "halt_command",
    "halt_global_command",
    "installed_script_command",
    "provenance_lines",
    "trigger_number",
    "unhalt_command",
    "unhalt_global_command",
]

#: The HALT was written by the chain; OVERWATCH may clear it once its cause is resolved.
ORIGIN_CHAIN = "chain"
#: The HALT is an operator's, or its writer cannot be proven; never cleared automatically.
ORIGIN_OPERATOR = "operator"
#: No HALT file at the path.
ORIGIN_ABSENT = "absent"

WRITER_CM_RELEASE = "cm-release"
WRITER_HALT_AFTER_PROMOTION = "halt-after-promotion"
WRITER_OVERWATCH_REPAIR = "overwatch-repair"
WRITER_OVERWATCH_RUNAWAY = "overwatch-runaway"
WRITER_ZERO_PROGRESS_CAP = "zero-progress-cap"

#: Writer names a ``# halt-writer:`` line may carry for the HALT to read as the chain's.
CHAIN_WRITERS = frozenset({
    WRITER_CM_RELEASE,
    WRITER_HALT_AFTER_PROMOTION,
    WRITER_OVERWATCH_REPAIR,
    WRITER_OVERWATCH_RUNAWAY,
    WRITER_ZERO_PROGRESS_CAP,
})

#: The sentinel-writes ops whose record makes an empty HALT the chain's.
_CHAIN_LOG_OPS = frozenset({"promote"})

_HALT_NAME = "HALT"

#: A HALT is a few lines; reading more than this proves nothing more.
_READ_CAP = 65536

_WRITER_KEY = "halt-writer"
_REASON_KEY = "halt-reason"
_TRIGGER_KEY = "halt-trigger"


def _key_line_re(key: str) -> "re.Pattern[str]":
    """``# <key>: <value>`` on a line of its own, liberal in spacing and case."""
    return re.compile(r"^[ \t]*#[ \t]*" + re.escape(key) + r"[ \t]*:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE)


_WRITER_RE = _key_line_re(_WRITER_KEY)
_REASON_RE = _key_line_re(_REASON_KEY)
_TRIGGER_RE = _key_line_re(_TRIGGER_KEY)

# The header cm_halt has always written, and CM.md's hand-written HALT
# template carries (the same pattern the resolution stage classifies on).
_CM_HEADER_RE = re.compile(r"^[ \t]*#[ \t]*HALT created by CM at\b", re.IGNORECASE | re.MULTILINE)
_CM_ERROR_RE = _key_line_re("error")
_CM_LEGACY_REASON_RE = _key_line_re("reason")

# overwatch_write_repair_halt's creator marker.
_REPAIR_MARKER_RE = re.compile(
    r"^[ \t]*#[ \t]*overwatch-repair-halt[ \t]*:.*?\bfn=(\S+)", re.IGNORECASE | re.MULTILINE
)

# "trigger-5", "trigger 5", "trigger_10", "trigger-5b" -> the number with its suffix.
_TRIGGER_NUMBER_RE = re.compile(r"\btrigger[ \t_-]*#?(\d+[a-z]?)(?![A-Za-z0-9])", re.IGNORECASE)

_NO_VALUE = frozenset({"", "-", "none", "n/a"})


@dataclasses.dataclass(frozen=True)
class HaltProvenance:
    """What :func:`classify_halt` found for one HALT path.

    Attributes:
        path:    The HALT path asked about, as given.
        origin:  ``chain``, ``operator`` or ``absent``.
        writer:  The writer named in the body (a chain writer, an unknown
                 name, or None), or the sentinel-writes ``op`` for a
                 body-less HALT with a record.
        reason:  Why the HALT was written, when the body or the log says.
        trigger: The CM trigger number (``"5"``, ``"5b"``) for a CM HALT
                 that names one; None otherwise.
        source:  Where the answer came from: ``body``, ``cm-header``,
                 ``repair-marker``, ``sentinel-log``, ``unrecognized-body``,
                 ``no-record``, ``unreadable`` or ``absent``.
        detail:  One plain sentence saying why the origin is what it is.
        fleet_scope: True for the kanban root's own HALT, which is always
                 ``operator``.
    """

    path: str
    origin: str
    writer: Optional[str]
    reason: Optional[str]
    trigger: Optional[str]
    source: str
    detail: str
    fleet_scope: bool = False

    @property
    def is_chain(self) -> bool:
        """True only when the chain wrote this HALT."""
        return self.origin == ORIGIN_CHAIN

    def to_dict(self) -> dict:
        """The result as a JSON-ready dict (``is_chain`` included)."""
        data = dataclasses.asdict(self)
        data["is_chain"] = self.is_chain
        return data


def _normalize_writer(value: str) -> str:
    """A writer name in canonical form: lowercase, ``-`` for ``_`` / ``.`` / spaces."""
    return re.sub(r"[\s_.]+", "-", value.strip().lower()).strip("-")


def _value(text: str, pattern: "re.Pattern[str]") -> Optional[str]:
    """The first non-empty value *pattern* captures in *text*, else None."""
    for match in pattern.finditer(text):
        value = match.group(1).strip()
        if value:
            return value
    return None


def trigger_number(text: Optional[str]) -> Optional[str]:
    """The CM trigger number *text* names, or None.

    Reads an error key (``cm-release.trigger-5.squash-conflict`` -> ``"5"``,
    ``cm-release.trigger-5b.tag-points-at-wrong-commit`` -> ``"5b"``) or a
    phrase (``trigger 9`` -> ``"9"``). The letter suffix is lowercased.
    """
    if not text:
        return None
    match = _TRIGGER_NUMBER_RE.search(text)
    return match.group(1).lower() if match else None


def provenance_lines(writer: str, reason: str, trigger: Optional[str] = None) -> list:
    """The provenance block a chain HALT writer puts in its HALT body.

    Args:
        writer:  One of CHAIN_WRITERS.
        reason:  Why the chain stopped; folded onto one line.
        trigger: The CM trigger number, when the HALT has one; a value of
                 ``none`` or blank writes no trigger line.

    Returns:
        The lines, without newlines, in the order the module docstring
        shows.

    Raises:
        ValueError: *writer* is not a chain writer, or *reason* is blank.
    """
    name = _normalize_writer(writer)
    if name not in CHAIN_WRITERS:
        raise ValueError(
            f"provenance_lines: writer {writer!r} is not a chain HALT writer; "
            f"use one of {', '.join(sorted(CHAIN_WRITERS))}"
        )
    line = one_line(reason)
    if not line:
        raise ValueError("provenance_lines: reason is blank; say why the chain stopped")
    lines = [f"# {_WRITER_KEY}: {name}", f"# {_REASON_KEY}: {line}"]
    number = one_line(trigger or "")
    if number.lower() not in _NO_VALUE:
        lines.append(f"# {_TRIGGER_KEY}: {number}")
    return lines


def _installed_script(kanban_root: Union[str, "os.PathLike[str]"], script: str) -> str:
    """*script* as installed under *kanban_root* (``<root>/scripts/<script>``), shell-quoted.

    ``install.sh`` puts the operator scripts in ``<kanban root>/scripts/``;
    the repository's ``team/`` prefix does not exist on an installed root.
    """
    return shlex.quote(str(Path(kanban_root) / "scripts" / script))


def installed_script_command(kanban_root: Union[str, "os.PathLike[str]"], script: str, *args: str) -> str:
    """The command that runs *script* as installed under *kanban_root*, runnable as printed.

    Names ``<kanban root>/scripts/<script>`` -- *script* is relative to
    ``scripts/`` and may name a subdirectory (``cm/cancel-rc.sh``) -- then
    each of *args* in order. Every part is shell-quoted, so the command runs
    from any directory when *kanban_root* is absolute.
    ``installed_script_command(root, "halt.sh", "--project", name)`` is the
    same text :func:`halt_command` gives.
    """
    return " ".join([_installed_script(kanban_root, script), *(shlex.quote(str(arg)) for arg in args)])


def halt_command(kanban_root: Union[str, "os.PathLike[str]"], project: str) -> str:
    """The command that halts *project* alone, runnable as printed.

    Names the installed script under *kanban_root* --
    ``<kanban root>/scripts/halt.sh --project <name>`` -- so it works from
    any directory; both parts are shell-quoted.
    """
    return f"{_installed_script(kanban_root, 'halt.sh')} --project {shlex.quote(project)}"


def halt_global_command(kanban_root: Union[str, "os.PathLike[str]"]) -> str:
    """The command that halts every project at once, runnable as printed.

    Names the installed script under *kanban_root* --
    ``<kanban root>/scripts/halt-global.sh`` -- shell-quoted.
    """
    return _installed_script(kanban_root, "halt-global.sh")


def unhalt_command(kanban_root: Union[str, "os.PathLike[str]"], project: str) -> str:
    """The command that removes *project*'s HALT, runnable as printed.

    Names the installed script under *kanban_root* --
    ``<kanban root>/scripts/unhalt.sh --project <name>`` -- so it works from
    any directory; both parts are shell-quoted.
    """
    return f"{_installed_script(kanban_root, 'unhalt.sh')} --project {shlex.quote(project)}"


def unhalt_global_command(kanban_root: Union[str, "os.PathLike[str]"]) -> str:
    """The command that removes the fleet-scoped HALT, runnable as printed.

    Names the installed script under *kanban_root* --
    ``<kanban root>/scripts/unhalt-global.sh`` -- shell-quoted.
    """
    return _installed_script(kanban_root, "unhalt-global.sh")


def _from_body(text: str) -> Optional[tuple]:
    """``(writer, reason, trigger, source)`` the body names, or None when it
    carries no provenance this module recognizes."""
    writer = _value(text, _WRITER_RE)
    if writer is not None:
        name = _normalize_writer(writer)
        reason = _value(text, _REASON_RE)
        trigger = _value(text, _TRIGGER_RE)
        if trigger is not None and trigger.lower() in _NO_VALUE:
            trigger = None
        if trigger is None and name == WRITER_CM_RELEASE:
            trigger = trigger_number(reason)
        return name, reason, trigger.lower() if trigger else None, "body"
    if _CM_HEADER_RE.search(text):
        reason = _value(text, _CM_ERROR_RE) or _value(text, _CM_LEGACY_REASON_RE)
        return WRITER_CM_RELEASE, reason, trigger_number(reason), "cm-header"
    marker = _REPAIR_MARKER_RE.search(text)
    if marker:
        return WRITER_OVERWATCH_REPAIR, f"OVERWATCH repair in progress ({marker.group(1)})", None, "repair-marker"
    return None


def _from_log(halt: Path, root: Path, path_text: str, fleet: bool) -> HaltProvenance:
    """Classify a body-less HALT from its latest sentinel-writes record."""
    record = latest_sentinel_record(halt, kanban_root=root)
    if record is None:
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, None, None, None, "no-record",
            "the HALT is empty and the sentinel-writes log has no record for it, "
            "so it is read as the operator's",
            fleet,
        )
    op = record.get("op", "").strip().lower() or None
    if op in _CHAIN_LOG_OPS and not fleet:
        if record_describes_sentinel(record, halt):
            return HaltProvenance(
                path_text, ORIGIN_CHAIN, WRITER_HALT_AFTER_PROMOTION,
                "HALT-AFTER drain completed (recorded as promote in the sentinel-writes log)",
                None, "sentinel-log",
                "the HALT is empty and its latest sentinel-writes record is the HALT-AFTER promotion",
                fleet,
            )
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, op, None, None, "sentinel-log",
            "the latest sentinel-writes record names the promotion but describes other bytes "
            "than the HALT holds, so it is read as the operator's",
            fleet,
        )
    if fleet:
        detail = "the kanban root's own HALT is always the operator's"
    else:
        detail = (
            f"the HALT is empty and its latest sentinel-writes record is op={op or '-'}, "
            "so it is read as the operator's"
        )
    return HaltProvenance(path_text, ORIGIN_OPERATOR, op, None, None, "sentinel-log", detail, fleet)


def classify_halt(
    halt_path: Union[str, "os.PathLike[str]"],
    *,
    kanban_root: Union[None, str, "os.PathLike[str]"] = None,
) -> HaltProvenance:
    """Say whether the chain or an operator wrote the HALT at *halt_path*.

    The rule is the module docstring's "Classification". Reads only;
    never raises for an unreadable file or log (both read as the
    operator's).

    Args:
        halt_path:   The HALT file, e.g. ``<kanban_root>/projects/<name>/HALT``.
        kanban_root: The kanban root whose ``logs/`` holds the
                     sentinel-writes log; when None it is derived from the
                     HALT's directory (``<root>/projects/<name>`` -> ``<root>``).

    Returns:
        A :class:`HaltProvenance`.
    """
    path_text = os.fspath(halt_path)
    halt = Path(os.path.normpath(os.path.abspath(path_text)))
    if kanban_root is not None:
        root = Path(os.path.normpath(os.path.abspath(os.fspath(kanban_root))))
    else:
        root = kanban_root_for_scope(halt.parent)
    fleet = halt.name == _HALT_NAME and halt.parent == root

    if not halt.is_file():
        return HaltProvenance(path_text, ORIGIN_ABSENT, None, None, None, "absent", "no HALT file is present", fleet)
    try:
        with open(halt, "rb") as handle:
            raw = handle.read(_READ_CAP)
    except OSError as exc:
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, None, None, None, "unreadable",
            f"the HALT cannot be read ({exc}), so it is read as the operator's",
            fleet,
        )
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")

    if not text.strip():
        return _from_log(halt, root, path_text, fleet)

    found = _from_body(text)
    if found is None:
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, None, None, None, "unrecognized-body",
            "the HALT body names no chain writer, so it is read as the operator's",
            fleet,
        )
    writer, reason, trigger, source = found
    if fleet:
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, writer, reason, trigger, source,
            "the kanban root's own HALT is always the operator's", fleet,
        )
    if writer not in CHAIN_WRITERS:
        return HaltProvenance(
            path_text, ORIGIN_OPERATOR, writer, reason, trigger, source,
            f"the HALT body names writer {writer!r}, which is not a chain writer, "
            "so it is read as the operator's",
            fleet,
        )
    return HaltProvenance(
        path_text, ORIGIN_CHAIN, writer, reason, trigger, source,
        f"the HALT body names the chain writer {writer}", fleet,
    )


def _field(value: Optional[str]) -> str:
    """One tab-separated output field: ``-`` for no value, never a tab or newline."""
    return one_line(value) if value else "-"


def main(argv: Optional[list] = None) -> int:
    """Run the CLI: ``classify HALT_PATH`` prints the HALT's provenance.

    Exit codes: 0 answered (including ``absent``); 2 usage error.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.overwatch.halt_provenance",
        description="Say whether the chain or an operator wrote a HALT file (reads only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    classify_p = sub.add_parser(
        "classify",
        help="Print origin, writer, reason, trigger and source for a HALT file.",
    )
    classify_p.add_argument("halt_path", help="the HALT file, e.g. $PGAI_AGENT_KANBAN_ROOT_PATH/projects/demo/HALT")
    classify_p.add_argument(
        "--kanban-root",
        default=None,
        help="the kanban root whose logs/ holds the sentinel-writes log (default: derived from the HALT's directory)",
    )
    classify_p.add_argument("--json", action="store_true", help="print the full result as one JSON object")
    args = parser.parse_args(argv)

    result = classify_halt(args.halt_path, kanban_root=args.kanban_root)
    if args.json:
        print(json.dumps(result.to_dict(), sort_keys=True))
    else:
        print("\t".join([
            result.origin, _field(result.writer), _field(result.reason),
            _field(result.trigger), result.source,
        ]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
