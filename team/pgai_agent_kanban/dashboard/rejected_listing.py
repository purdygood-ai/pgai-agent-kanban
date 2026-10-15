"""
rejected_listing.py — the inventory of intake files quarantined by discovery.

Discovery moves an intake file whose name it keeps rejecting into the
project's ``rejected/`` directory, beside a ``<name>.reason`` sidecar that
records the intake directory it came from; with no project name it falls
back to the intake directory's own ``.rejected/`` (``priority/.rejected/``,
``bugs/.rejected/``). ``pgai_agent_kanban.ops.read.list_rejected`` reads
both and groups the files by the intake directory they came from. This
module names the reason a file was quarantined and renders the inventory
``list-rejected.sh`` prints and ``GET /rejected`` returns in its envelope's
``stdout``::

    project=<name> dir=bugs
      BUG-12-example.md  (quarantined 2026-05-17T12:31)
      Reason: filename does not match expected pattern (^BUG-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\\.md$)

    To recover: scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]

A section's ``dir`` is ``priority``, ``bugs`` or ``requirements``, or
``rejected`` for a file whose origin cannot be told. With no quarantined
file anywhere it prints one line,
``(no quarantined files found across all projects)``.

Colour is off when ``TERM`` is ``dumb`` or unset, or ``NO_COLOR`` is exactly
``1`` (see :func:`listing_color_enabled`).
"""

from __future__ import annotations

import dataclasses
import re
from typing import Mapping, Sequence

from pgai_agent_kanban.lib.ledger_filename_contract import ledger_filename_re
from pgai_agent_kanban.lib.palette import ansi_code

__all__ = [
    "RECOVER_HINT",
    "RejectedFile",
    "RejectedSection",
    "listing_color_enabled",
    "rejection_reason",
    "render_rejected_listing",
]

#: The command the listing closes with when it names any file, as an
#: installed kanban root lays it out.
RECOVER_HINT = "scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]"

_NOTHING_FOUND = "(no quarantined files found across all projects)"

# Intake filename prefix -> the full-name pattern discovery accepts for it:
# the ledger filename contract, exactly four digits, an optional date and a
# lowercase slug.
_ACCEPTED_PATTERNS: dict[str, re.Pattern[str]] = {
    "PRIORITY-": ledger_filename_re("PRIORITY-"),
    "BUG-": ledger_filename_re("BUG-"),
}


@dataclasses.dataclass(frozen=True)
class RejectedFile:
    """One quarantined file.

    Attributes:
        name:           The file name.
        quarantined_at: Its mtime as local ``YYYY-MM-DDTHH:MM``, or ``unknown``.
        reason:         Why it was quarantined (:func:`rejection_reason`).
    """

    name: str
    quarantined_at: str
    reason: str


@dataclasses.dataclass(frozen=True)
class RejectedSection:
    """The quarantined files of one project's intake directory.

    Attributes:
        project:   The project name.
        directory: The intake directory the files came from -- ``priority``,
                   ``bugs`` or ``requirements`` -- or ``rejected`` when that
                   cannot be told.
        files:     The files, in name order; never empty.
    """

    project: str
    directory: str
    files: tuple[RejectedFile, ...]


def rejection_reason(name: str) -> str:
    """Return why discovery quarantined the file called *name*.

    A ``PRIORITY-`` or ``BUG-`` file whose name does not match the pattern
    discovery accepts is named with that pattern; one that does match was
    quarantined for a reason only the logs record; any other name is not an
    intake file at all.
    """
    for prefix, pattern in _ACCEPTED_PATTERNS.items():
        if name.startswith(prefix):
            if pattern.match(name):
                return "pattern matches — unknown rejection reason (see logs)"
            return f"filename does not match expected pattern ({pattern.pattern})"
    return "unknown prefix — not a BUG- or PRIORITY- file"


def listing_color_enabled(environ: Mapping[str, str]) -> bool:
    """Whether the listing is coloured: not when ``TERM`` is dumb or unset, nor when ``NO_COLOR`` is ``1``."""
    return environ.get("TERM", "dumb") != "dumb" and environ.get("NO_COLOR", "") != "1"


def render_rejected_listing(sections: Sequence[RejectedSection], *, use_color: bool) -> str:
    """Render *sections* as the inventory text (see the module docstring).

    Args:
        sections:  What ``ops.read.list_rejected`` found, in display order.
        use_color: Whether ANSI colour codes are emitted.

    Returns:
        The inventory, every line newline-terminated.
    """
    bold, red, dim, reset = (ansi_code(name, use_color) for name in ("bold", "red", "dim", "reset"))
    if not sections:
        return f"{dim}{_NOTHING_FOUND}{reset}\n"
    lines: list[str] = []
    for section in sections:
        lines.append(f"{bold}{red}project={section.project} dir={section.directory}{reset}")
        for entry in section.files:
            lines.append(f"  {entry.name}  (quarantined {entry.quarantined_at})")
            lines.append(f"  {dim}Reason: {entry.reason}{reset}")
        lines.append("")
    lines.append(f"{dim}To recover: {bold}{RECOVER_HINT}{reset}")
    return "\n".join(lines) + "\n"
