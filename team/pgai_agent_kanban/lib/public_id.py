"""
public_id.py — Shared reader for public Known-Issue identifiers.

A Public ID is the identifier a disclosed bug carries in the published
CHANGELOG.md, in the format ``KI-<anchor-version-no-v>.<counter>`` (for
example ``KI-1.0.0.1``). Every surface that has to find these identifiers
in text — the changelog writer seeding its per-version counters from what
is already published, the freshness lint deciding whether a divergent line
is a wholly new entry or a rewrite of an existing one — reads them through
this module rather than carrying its own pattern.

Public API
----------
    extract_ki_ids(text) -> set[str]
        Every ``KI-X.Y.Z.N`` token anywhere in *text*, as full ``KI-``
        strings.

    leading_ki_id(line) -> str
        The identifier a CHANGELOG line starts with (after an optional list
        marker), or ``""`` when the line does not start with one.

    ki_line_title(line) -> str
        The title of a rendered KI line: the text after the identifier's
        separator, minus the trailing ``; affects vX · <status>`` clause.

    same_ki_title(a, b) -> bool
        Whether two titles describe the same entry once rendering noise is
        discounted: whitespace, case, a trailing period, and first-sentence
        truncation (one title is a prefix of the other). A blank title or a
        bare ``resolved`` marker carries no title of its own and is never a
        difference.
"""

from __future__ import annotations

import re

# A Public ID anywhere in a line: KI-<major>.<minor>.<patch>.<counter>.
KI_ID_RE = re.compile(r"\bKI-(\d+\.\d+\.\d+\.\d+)\b")

# A Public ID at the canonical leading position of a CHANGELOG line: after
# optional whitespace and list marker, followed by a separator (em-dash,
# hyphen, or whitespace).  An identifier mentioned mid-sentence does not
# match, so prose that refers to an entry is never mistaken for the entry.
_LEADING_KI_RE = re.compile(
    r"^[\s\-\*]*"
    r"(KI-\d+\.\d+\.\d+\.\d+)"
    r"(?:\s*[—\-]+\s*|\s+)"
)

# The clause the changelog writer appends to a rendered KI line.  The
# title is everything before its LAST occurrence: a title may itself
# contain a semicolon.
_AFFECTS_CLAUSE = "; affects "
_STATUS_SEPARATOR = " · "

# A bare resolution marker carries no title of its own.
_BARE_RESOLVED = "resolved"


def extract_ki_ids(text: str) -> set[str]:
    """Return every Public ID that appears in *text*.

    Scans for all ``KI-X.Y.Z.N`` tokens wherever they sit on a line.

    Args:
        text: CHANGELOG content (or any string) to scan.

    Returns:
        Set of full Public ID strings, e.g. ``{"KI-1.0.0.1", "KI-1.0.0.2"}``.
    """
    return {f"KI-{version_and_counter}" for version_and_counter in KI_ID_RE.findall(text)}


def leading_ki_id(line: str) -> str:
    """Return the Public ID *line* starts with, or ``""``.

    ``- KI-1.0.0.1 — resolved``, ``* KI-1.0.0.2 — text`` and
    ``KI-1.0.0.3 — symptom; affects v1.0.0 · open`` all yield their
    identifier; ``This refers to KI-1.0.0.1 for context`` yields ``""``.
    """
    match = _LEADING_KI_RE.match(line.strip())
    return match.group(1) if match else ""


def ki_line_title(line: str) -> str:
    """Return the title portion of a rendered KI line.

    The title is what follows the identifier's separator, minus the
    trailing ``; affects vX · <status>`` clause when one is present.  A
    line that does not start with a Public ID has no title (``""``).
    """
    stripped = line.strip()
    match = _LEADING_KI_RE.match(stripped)
    if not match:
        return ""
    rest = stripped[match.end():]
    clause_at = rest.rfind(_AFFECTS_CLAUSE)
    if clause_at != -1 and _STATUS_SEPARATOR in rest[clause_at:]:
        rest = rest[:clause_at]
    return rest.strip()


def _normalize_title(title: str) -> str:
    """Collapse the rendering noise a title comparison must discount."""
    collapsed = " ".join(title.split()).casefold()
    if collapsed.endswith("."):
        collapsed = collapsed[:-1]
    if collapsed == _BARE_RESOLVED:
        return ""
    return collapsed


def same_ki_title(a: str, b: str) -> bool:
    """Return True when titles *a* and *b* describe the same entry.

    Whitespace, case and a trailing period are discounted, and a title
    that is a prefix of the other counts as the same (the writer renders
    a first sentence, so a re-cut sentence boundary is not a new entry).
    A blank title or a bare ``resolved`` marker carries no title of its
    own, so it is never evidence of a different entry.
    """
    normalized_a, normalized_b = _normalize_title(a), _normalize_title(b)
    if not normalized_a or not normalized_b:
        return True
    return normalized_a.startswith(normalized_b) or normalized_b.startswith(normalized_a)
