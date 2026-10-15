"""
changelog_writer.py — CHANGELOG.md regeneration engine.

Regenerates CHANGELOG.md in full from two sources of truth:
  - Per-release notes files under release-notes/vX.Y.Z.md
  - The bug ledger under bugs_dir/, together with the bugs archived from it

Archived bugs. When bugs_dir is a live project's ledger
(<kanban_root>/projects/<project>/bugs), regenerate() also reads the bug
files archived from it (<kanban_root>/archive/<project>/bugs) for every bug
number that has no live file, so a bug referenced by a release-notes entry
or carrying a Public ID renders the same whether it is live or archived,
and a CHANGELOG regenerated after an archive run is byte-identical to one
regenerated before. A live bug file always wins over an archived file with
the same number. A Public ID assigned to an archived bug is written back to
its file in place in the archive. Any other bugs_dir has no archive and is
read alone. The other inputs (release notes, the PUBLISHED manifest and the
published CHANGELOG.md) live in the repository and are never archived.

Each CHANGELOG entry has exactly three content sections:
  ### Implemented   — features from the release notes
  ### Fixed         — resolved bugs from the release notes, classified correctly
  ### Known Issues  — projected from the bug ledger against PUBLISHED versions

Disclosure policy: a bug is disclosed in Known Issues ONLY if it affected a
PUBLISHED version (a version a user could actually have run). Bugs born and
fixed entirely between public tags are never published; they remain in the
internal ledger.

Public IDs use the format KI-<earliest-affected-PUBLISHED-version-no-v>.<counter>
(e.g. KI-1.0.0.1). Once assigned they are read from the bug file and never
recomputed at regeneration.

A Public ID that appears in the published CHANGELOG.md is spent. The
per-version counter is seeded from the union of the IDs persisted in bug
files and the IDs the published artifact already carries, so an ID whose
bug file has since been closed, moved or re-anchored is never handed to
another bug. A published KI line that no surviving bug file carries is kept
verbatim, in the entry and section it was published under — it is history.
A bug file that persists an ID the published artifact carries for a
different title was handed a spent ID: the published line stays as it is
and the bug receives a fresh ID, written back to its file, at the next
regeneration. Which artifact counts as published is the ``published_changelog``
argument of ``regenerate()``; by default it is CHANGELOG.md under repo_root.

Public-ID assignment is gated on ## Affects parsing as a recognisable version
string (e.g. 'v1.2.3', '1.2.3').  Prose values such as
'(internal — plugin era; no KI)' are not version strings; they produce a
warning to stderr naming the offending file and no Public ID is assigned.
Internal bugs should omit the ## Affects section entirely rather than using
a prose placeholder.

Internal BUG-NNNN identifiers never appear in generated CHANGELOG output.

Entry point:
    regenerate(repo_root, released_versions, published_manifest, bugs_dir,
               extra_notes_dir=None, published_changelog=CHECKED_IN_CHANGELOG) -> str

Cleanup entry point:
    remove_bogus_public_ids(bugs_dir, published_manifest) -> list[pathlib.Path]
    Removes Public IDs from bug files whose ## Affects does not parse as a
    version string.  Idempotent: running twice produces the same output as
    running once.  Returns the list of files modified.

Manifest loader:
    load_published_manifest(repo_root) -> list[str]
    Returns [] when release-notes/PUBLISHED is absent (empty-manifest semantics:
    no published releases, every bug entry is internal — the correct reading for
    a managed dev tree that has never published a release).
    Returns [] when the file exists but is empty (same: pre-publication state).
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import defaultdict
from typing import NamedTuple

try:
    from pgai_agent_kanban.lib.public_id import (
        extract_ki_ids,
        ki_line_title,
        leading_ki_id,
        same_ki_title,
    )
except ImportError:
    # Loaded by file path rather than as a package member (an embedded
    # interpreter can do this): put this module's own package root on
    # sys.path so the sibling lib resolves from the same tree, never another.
    _OWN_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]
    _PUBLIC_ID_PATH = _OWN_PACKAGE_ROOT / "pgai_agent_kanban" / "lib" / "public_id.py"
    # A directory named pgai_agent_kanban with no __init__.py — the kanban
    # root, when the working directory is its parent — imports first as an
    # implicit namespace package and stays cached in sys.modules, shadowing
    # the real package on every retry. Drop a cached namespace package (it
    # has no __file__) so the retry can bind the real one; a cached regular
    # package is left alone.
    _cached_pkg = sys.modules.get("pgai_agent_kanban")
    _shadow_dirs: list[str] = []
    if _cached_pkg is not None and getattr(_cached_pkg, "__file__", None) is None:
        _shadow_dirs = [str(p) for p in getattr(_cached_pkg, "__path__", [])]
        for _mod_name in [
            n for n in sys.modules
            if n == "pgai_agent_kanban" or n.startswith("pgai_agent_kanban.")
        ]:
            del sys.modules[_mod_name]
    sys.path.insert(0, str(_OWN_PACKAGE_ROOT))
    try:
        from pgai_agent_kanban.lib.public_id import (  # noqa: E402
            extract_ki_ids,
            ki_line_title,
            leading_ki_id,
            same_ki_title,
        )
    except ImportError as _exc:
        if not _PUBLIC_ID_PATH.is_file():
            # The tree this module runs from is incomplete; say which file
            # is missing rather than leaving a bare module name to decode.
            raise ImportError(
                "changelog_writer needs its sibling shared reader at "
                f"{_PUBLIC_ID_PATH} "
                "— install the whole pgai_agent_kanban package, not cm/ alone"
            ) from _exc
        # The file is there, so the package name bound somewhere else.
        _bound = sys.modules.get("pgai_agent_kanban")
        _bound_where = (
            getattr(_bound, "__file__", None)
            or [str(p) for p in getattr(_bound, "__path__", [])]
            or _shadow_dirs
            or "an unknown location"
        )
        raise ImportError(
            f"changelog_writer could not import its shared reader although "
            f"{_PUBLIC_ID_PATH} is present: the name pgai_agent_kanban "
            f"resolved to {_bound_where} instead of "
            f"{_OWN_PACKAGE_ROOT / 'pgai_agent_kanban'}. A same-named "
            f"directory (often one in the working directory) is shadowing "
            f"the package — run from another directory, or set "
            f"PYTHONPATH={_OWN_PACKAGE_ROOT}"
        ) from _exc

# Version sort key: a total order that never raises, so a malformed Affects
# value or release-notes stem sorts low instead of failing the render. The
# package root is on sys.path by this point (the block above guarantees it).
from pgai_agent_kanban.archive.layout import CATEGORY_BUGS, archive_layout_for_category_dir  # noqa: E402
from pgai_agent_kanban.lib.semver import version_sort_key as _parse_version  # noqa: E402
from pgai_agent_kanban.task_status import read_section  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADER = (
    "# Changelog\n\n"
    "<!-- Auto-generated by changelog_writer.py. Do not edit manually. -->\n"
    "<!-- Known Issues covers defects that existed in PUBLISHED releases only. -->\n"
    "<!-- Internal-only defects are tracked in the bug ledger but not disclosed. -->\n"
)

# Pattern that matches internal bug identifiers — must never appear in output.
_BUG_ID_PATTERN = re.compile(r"BUG-[0-9]")

# The published artifact regenerate() reads by default, relative to repo_root.
CHECKED_IN_CHANGELOG = pathlib.Path("CHANGELOG.md")

# A Public ID split into its anchor version and counter.
_PUBLIC_ID_PARTS_RE = re.compile(r"KI-(\d+\.\d+\.\d+)\.(\d+)$")

# A CHANGELOG entry heading: "## vX.Y.Z — <date>".
_ENTRY_HEADING_RE = re.compile(r"^##\s+(v\d+\.\d+\.\d+)(?:\s|$)")

# A content section heading inside an entry: "### Fixed", "### Known Issues".
_SECTION_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")

# Sections in release notes that contain implemented (feature) content.
_IMPLEMENTED_HEADINGS = frozenset(
    {"features", "what shipped", "implemented"}
)

# Sections in release notes that contain fixed (bug) content.
_FIXED_HEADINGS = frozenset(
    {"bug fixes", "bugs resolved", "fixed"}
)

# Sections in release notes that contain the release summary.
_SUMMARY_HEADINGS = frozenset({"summary"})

# Section headings to skip when extracting content (we compute these ourselves).
_SKIP_HEADINGS = frozenset({
    "known issues",
    "bugs skipped",
    "deprecations",
    "breaking changes",
    "upgrade notes",
    "documentation",
    "other changes",
    "status",
    "release date",
    "released by",
})


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class BugRecord(NamedTuple):
    """Parsed representation of a single bug file."""
    bug_id: str          # e.g. "BUG-0023"
    slug: str            # e.g. "BUG-0023-tmux-split-flag"
    symptom: str         # first non-empty line of ## Symptom section
    status: str          # open / resolved / wontdo / etc.
    affects: str         # version string e.g. "v1.0.0", or "" if absent
    fixed_in: str        # version string e.g. "v1.16.2", or "" if unset
    public_id: str       # e.g. "KI-1.0.0.1", or "" if not yet assigned
    wontdo_reason: str   # reason from ## Status if wontdo, else ""
    file_path: pathlib.Path


class ReleaseEntry(NamedTuple):
    """Parsed content from a single release-notes/vX.Y.Z.md file."""
    version: str             # e.g. "v1.16.2"
    date: str                # e.g. "2026-07-11"
    summary: str             # one-line summary (first sentence of ## Summary)
    implemented_lines: list  # lines from Features / What Shipped sections
    fixed_bug_ids: list      # BUG-NNNN ids resolved in this release
    fixed_other_lines: list  # non-BUG-NNNN fixed items (non-tracked bugs)


# ---------------------------------------------------------------------------
# Semver comparison helper
# ---------------------------------------------------------------------------


def _version_gte(v1: str, v2: str) -> bool:
    """Return True if v1 >= v2 (semver comparison)."""
    return _parse_version(v1) >= _parse_version(v2)


def _version_lt(v1: str, v2: str) -> bool:
    """Return True if v1 < v2 (semver comparison)."""
    return _parse_version(v1) < _parse_version(v2)


def _version_lte(v1: str, v2: str) -> bool:
    """Return True if v1 <= v2 (semver comparison)."""
    return _parse_version(v1) <= _parse_version(v2)


# Pattern that matches a version string: optional 'v' or 'ai_v' prefix,
# followed by at least one digit-dot-digit component (e.g. v1.2.3, 1.0.0).
_VERSION_STRING_RE = re.compile(
    r"^(?:ai_v|v)?[0-9]+(?:\.[0-9]+){1,2}$"
)


def _looks_like_version_string(value: str) -> bool:
    """Return True if value is a recognisable version string.

    Accepts forms such as 'v1.2.3', '1.2.3', 'ai_v1.2.3'.  Prose values
    such as '(internal — plugin era; no KI)' return False.

    This gate prevents changelog_writer from silently treating
    unrecognisable ## Affects content as version (0, 0, 0) and minting
    a Public ID for internal bugs.
    """
    return bool(_VERSION_STRING_RE.match(value.strip()))


def _warn_unparseable_affects(file_path: pathlib.Path, affects: str) -> None:
    """Emit a loud warning when ## Affects is non-empty but not a version string.

    The warning is written to stderr so it surfaces in CI logs and in any
    caller that captures stderr.  It names the bug file so the operator can
    locate and fix the value.
    """
    print(
        f"Warning: {file_path}: Affects value not a known release — "
        f"no Public ID assigned (## Affects: {affects!r})",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Manifest loader
# ---------------------------------------------------------------------------


def load_published_manifest(repo_root: pathlib.Path) -> list[str]:
    """Load the list of publicly-released versions from release-notes/PUBLISHED.

    Returns a list of version strings (e.g. ['v1.0.0', 'v1.0.1']), oldest
    first (the order they appear in the file).

    Returns [] in two cases that share identical semantics — no published
    releases, every bug entry is internal:
      - The file exists but is empty (explicit pre-publication state).
      - The file is absent (implicit pre-publication state: a managed dev
        tree that has never published a release has no PUBLISHED file, and
        an absent manifest is the semantically correct reading for
        local-only projects where nothing has ever been publicly released).

    Callers that want to distinguish "absent" from "empty" may check whether
    the file exists before calling this function; this function does not make
    that distinction because the downstream logic treats both identically.
    """
    manifest_path = pathlib.Path(repo_root) / "release-notes" / "PUBLISHED"
    if not manifest_path.exists():
        return []
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Bug file parser
# ---------------------------------------------------------------------------


def _parse_section_value(text: str, heading: str) -> str:
    """Extract the content under a ## <heading> section from markdown text.

    Returns the stripped content of the section body, or "" if the section
    is absent or empty. Stops at the next ## heading or end of file. The
    body is read by task_status.read_section, the one ## section reader.
    """
    return read_section(text, heading) or ""


def _first_sentence(text: str) -> str:
    """Return the first sentence from a block of text.

    Newlines within the text are collapsed to spaces before scanning so that
    sentences that span multiple lines in the source file are returned as a
    single flat string.  This prevents multi-line symptom descriptions from
    producing rendered CHANGELOG lines with embedded newlines.
    """
    # Collapse all whitespace sequences (including newlines) to a single space
    text = " ".join(text.split())
    if not text:
        return ""
    # Split on period, exclamation, or question mark followed by space or end
    match = re.search(r"[.!?](?:\s|$)", text)
    if match:
        return text[: match.start() + 1].strip()
    # No sentence-ending punctuation — return the whole (now single-line) text
    return text


def _sanitize_symptom_for_title(raw: str) -> str:
    """Sanitize a raw Symptom block so it is safe to render in a Fixed entry.

    Strips content that must never appear in public CHANGELOG output:
      - Triple-backtick code fence blocks (including their contents)
      - Lines that start with ``FAILED`` (pytest failure lines)
      - Lines that start with ``$`` (shell command prompts)

    After stripping, collapses remaining text to a single flat string and
    extracts the first sentence.  Falls back to any non-empty remaining token
    when no sentence boundary is found.

    This sanitization is applied to the Symptom section before it is stored in
    BugRecord.  It guarantees that even an ugly bug file (traceback in Symptom,
    backticks, command lines, multi-paragraph body) produces a clean one-line
    title when rendered in a Fixed entry.
    """
    if not raw:
        return ""

    # Step 1: strip triple-backtick code fence blocks (``` ... ```)
    # Use a non-greedy match across newlines to remove all fence blocks.
    raw = re.sub(r"```.*?```", "", raw, flags=re.DOTALL)

    # Step 2: filter lines that start with FAILED or $ (common diagnostic markers)
    filtered_lines = []
    for line in raw.splitlines():
        stripped = line.lstrip()
        if re.match(r"FAILED\b", stripped, re.IGNORECASE):
            continue
        if stripped.startswith("$ ") or stripped == "$":
            continue
        filtered_lines.append(line)
    cleaned = "\n".join(filtered_lines)

    # Step 3: extract the first sentence from what remains
    result = _first_sentence(cleaned)
    if result:
        return result

    # Fallback: return any non-empty, non-fence token from the filtered text
    for line in filtered_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped

    return ""


def _extract_bug_id(text: str) -> str:
    """Extract the canonical BUG-NNNN identifier from a bullet line.

    Matches BUG-NNNN (4 or more digits) only when the token is:
      - At the very start of the text (after optional whitespace / list markers)
      - Immediately after a '-' or '*' list marker and optional whitespace
      - Wrapped in markdown emphasis (``**BUG-NNNN**`` or ``*BUG-NNNN*``)

    Tokens embedded in free-text prose (e.g. "see BUG-92 for details") are
    deliberately NOT matched so that phantom IDs from inline comments do not
    produce spurious entries.
    """
    # Canonical token: exactly BUG-NNNN where NNNN is 4 or more digits
    canonical = r"BUG-(\d{4,})"

    # Pattern 1: leading position (after optional list marker / whitespace)
    #   e.g. "- BUG-0001 ..." or "* BUG-0001 ..." or "BUG-0001 ..."
    m = re.match(r"^[\s\-\*]*" + canonical, text.strip())
    if m:
        return f"BUG-{int(m.group(1)):04d}"

    # Pattern 2: emphasis — **BUG-NNNN** or *BUG-NNNN*
    m = re.search(r"\*{1,2}" + canonical + r"\*{1,2}", text)
    if m:
        return f"BUG-{int(m.group(1)):04d}"

    return ""


def parse_bug_file(path: pathlib.Path) -> BugRecord:
    """Parse a bug file and return a BugRecord.

    Fields absent from historical files are returned as empty strings.
    The bug_id is inferred from the filename (BUG-NNNN prefix).
    """
    text = path.read_text(encoding="utf-8")

    # Infer bug_id from filename
    filename = path.name  # e.g. "BUG-0023-tmux-split-flag.md"
    bug_id_match = re.match(r"(BUG-\d+)", filename, re.IGNORECASE)
    bug_id = bug_id_match.group(1).upper() if bug_id_match else filename
    # Normalize to BUG-NNNN (4-digit zero-padded)
    num_match = re.match(r"BUG-(\d+)", bug_id)
    if num_match:
        bug_id = f"BUG-{int(num_match.group(1)):04d}"

    slug = path.stem  # e.g. "BUG-0023-tmux-split-flag"

    status_raw = _parse_section_value(text, "Status").lower()
    status = status_raw.split()[0] if status_raw else "open"

    symptom_raw = _parse_section_value(text, "Symptom")
    # Strip HTML comments, then sanitize and extract a clean one-line title.
    # Sanitization removes code fence blocks, FAILED traceback lines, and shell
    # command-prompt lines before extracting the first complete sentence.  This
    # ensures that even an ugly bug file (traceback in Symptom, backticks,
    # multi-paragraph body) produces a clean title rather than dumping diagnostic
    # content into the public CHANGELOG Fixed section.
    symptom_no_comments = re.sub(r"<!--.*?-->", "", symptom_raw, flags=re.DOTALL)
    symptom = _sanitize_symptom_for_title(symptom_no_comments)
    if not symptom:
        # Fallback: first non-empty non-comment line (covers degenerate cases)
        for line in symptom_no_comments.splitlines():
            stripped = line.strip()
            if stripped:
                symptom = stripped
                break

    affects = _parse_section_value(text, "Affects").strip()
    # Strip inline comments from affects
    affects = re.sub(r"<!--.*?-->", "", affects, flags=re.DOTALL).strip()

    fixed_in = _parse_section_value(text, "Fixed In").strip()
    fixed_in = re.sub(r"<!--.*?-->", "", fixed_in, flags=re.DOTALL).strip()

    public_id = _parse_section_value(text, "Public ID").strip()
    public_id = re.sub(r"<!--.*?-->", "", public_id, flags=re.DOTALL).strip()

    # Extract wontdo reason from Status if applicable
    wontdo_reason = ""
    if "wontdo" in status_raw or "won't" in status_raw or "wont" in status_raw:
        # Look for a reason after the status keyword
        reason_match = re.search(
            r"won'?t\s*(?:do|fix)[:\s]+(.+)", status_raw, re.IGNORECASE
        )
        if reason_match:
            wontdo_reason = reason_match.group(1).strip()

    return BugRecord(
        bug_id=bug_id,
        slug=slug,
        symptom=symptom,
        status=status,
        affects=affects,
        fixed_in=fixed_in,
        public_id=public_id,
        wontdo_reason=wontdo_reason,
        file_path=path,
    )


# A bug file name's number, e.g. 23 from "BUG-0023-tmux-split-flag.md".
_BUG_FILE_NUMBER_RE = re.compile(r"BUG-(\d+)", re.IGNORECASE)


def _bug_file_number(path: pathlib.Path) -> int | None:
    """The bug number a BUG-*.md file name carries, or None."""
    match = _BUG_FILE_NUMBER_RE.match(path.name)
    return int(match.group(1)) if match else None


def archived_bug_paths(bugs_dir: pathlib.Path) -> list[pathlib.Path]:
    """The archived bug files of the live ledger *bugs_dir* that have no live file.

    *bugs_dir* is read as ``<kanban_root>/projects/<project>/bugs``; the
    files returned are ``BUG-*.md`` in that same kanban root's
    ``archive/<project>/bugs`` whose bug number matches no live
    ``BUG-*.md`` in *bugs_dir* (a live file always wins). Empty when
    *bugs_dir* is not in that shape or the project has no archived bugs.
    """
    layout = archive_layout_for_category_dir(bugs_dir, CATEGORY_BUGS)
    if layout is None or not layout.bugs.is_dir():
        return []
    live_numbers = {_bug_file_number(p) for p in bugs_dir.glob("BUG-*.md")}
    return [
        p for p in layout.bugs.glob("BUG-*.md")
        if p.is_file() and _bug_file_number(p) not in live_numbers
    ]


def load_bugs(bugs_dir: pathlib.Path, include_archive: bool = False) -> list[BugRecord]:
    """Load all bug files from bugs_dir, sorted by bug_id ascending.

    Files that do not match the BUG-NNNN prefix are silently skipped.

    With *include_archive*, the bugs archived from *bugs_dir* that have no
    live file (:func:`archived_bug_paths`) are loaded too, in the same
    file-name order, so the result is the one the ledger gave before
    those bugs were archived.
    """
    if not bugs_dir.exists():
        return []
    paths = list(bugs_dir.glob("BUG-*.md"))
    if include_archive:
        paths.extend(archived_bug_paths(bugs_dir))
    records = []
    for path in sorted(paths, key=lambda p: p.name):
        try:
            records.append(parse_bug_file(path))
        except Exception as exc:  # noqa: BLE001
            # Log but do not abort — a corrupt bug file should not kill the build
            print(
                f"Warning: could not parse {path}: {exc}", file=sys.stderr
            )
    return records


# ---------------------------------------------------------------------------
# Release notes parser
# ---------------------------------------------------------------------------


def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Parse a markdown file into a dict of {heading_lower: [content_lines]}.

    Headings are matched at H2 (##) level; content is everything between
    consecutive H2 headings (or end of file). The H1 title line is skipped.
    """
    sections: dict[str, list[str]] = {}
    current_heading = None
    current_lines: list[str] = []

    for line in text.splitlines():
        # H1 title — skip
        if re.match(r"^#[^#]", line):
            continue
        # H2 heading
        h2_match = re.match(r"^##\s+(.+)$", line)
        if h2_match:
            if current_heading is not None:
                sections[current_heading] = current_lines
            current_heading = h2_match.group(1).strip().lower()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = current_lines

    return sections


def _clean_content_lines(lines: list[str]) -> list[str]:
    """Strip leading/trailing blank lines from a content block.

    Also removes bare 'None' placeholder lines — these are used in release
    notes sections to indicate "nothing here", and should not be treated as
    actual content items by the writer.
    """
    # Remove leading blank lines
    while lines and not lines[0].strip():
        lines = lines[1:]
    # Remove trailing blank lines
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    # Filter out bare 'None' placeholder values (case-insensitive)
    lines = [ln for ln in lines if ln.strip().lower() != "none"]
    return lines


def _join_bullet_continuations(lines: list[str]) -> list[str]:
    """Collapse multi-line markdown bullet items into single strings.

    A new bullet starts on any line that begins with ``-`` or ``*`` followed
    by whitespace.  Subsequent lines that do NOT start a new bullet are treated
    as continuation text and are joined (with a single space) to the current
    bullet.  Blank lines between bullets are discarded.

    Returns a list where each element is one complete (possibly multi-sentence)
    bullet text, suitable for passing to ``_extract_bug_id``.

    Non-bullet leading lines (e.g. a section opener that contains prose instead
    of a list) are returned as individual items unchanged.
    """
    bullets: list[str] = []
    current: list[str] = []

    _BULLET_RE = re.compile(r"^[\-\*]\s")

    def _flush() -> None:
        if current:
            bullets.append(" ".join(current))
            current.clear()

    for line in lines:
        if not line.strip():
            # Blank line — flush current bullet
            _flush()
            continue
        if _BULLET_RE.match(line):
            # New bullet
            _flush()
            current.append(line.strip())
        else:
            if current:
                # Continuation of the current bullet
                current.append(line.strip())
            else:
                # Plain line before any bullet (e.g. prose-only section body)
                bullets.append(line)

    _flush()
    return bullets


def parse_release_notes(version: str, notes_path: pathlib.Path) -> ReleaseEntry:
    """Parse a release-notes/vX.Y.Z.md file into a ReleaseEntry.

    Extracts:
    - date from the **Release Date:** field
    - summary from ## Summary (first sentence)
    - implemented_lines from ## Features / ## What Shipped / ## Implemented
    - fixed_bug_ids from ## Bug Fixes / ## Bugs Resolved (BUG-NNNN references)
    - fixed_other_lines from the same sections (non-BUG-NNNN items)
    """
    text = notes_path.read_text(encoding="utf-8")

    # Extract release date
    date_match = re.search(r"\*\*Release Date:\*\*\s*(\S+)", text)
    date = date_match.group(1) if date_match else ""

    sections = _parse_markdown_sections(text)

    # Summary
    summary = ""
    for heading in _SUMMARY_HEADINGS:
        if heading in sections:
            raw = "\n".join(sections[heading]).strip()
            summary = _first_sentence(raw)
            break

    # Implemented — collect from all implemented headings.
    # Sort headings alphabetically before iterating to guarantee a stable
    # collection order regardless of frozenset iteration (which is
    # PYTHONHASHSEED-dependent).  Without sorting, two runs with different
    # hash seeds can produce different line orderings when a release notes
    # file contains content under more than one matching heading.
    implemented_lines: list[str] = []
    for heading in sorted(_IMPLEMENTED_HEADINGS):
        if heading in sections:
            lines = _clean_content_lines(list(sections[heading]))
            if lines:
                implemented_lines.extend(lines)

    # Fixed — collect from all fixed headings, split into BUG ids and other.
    # Multi-line bullets (where continuation lines are indented or plain prose
    # following the opening "- " marker) are first joined into a single string
    # so that _extract_bug_id sees the full bullet and inline continuation lines
    # are not mistaken for separate items.
    # Sort headings alphabetically (same reason as for _IMPLEMENTED_HEADINGS
    # above — frozenset iteration order is hash-seed-dependent).
    fixed_bug_ids: list[str] = []
    fixed_other_lines: list[str] = []
    seen_bug_ids: set[str] = set()

    for heading in sorted(_FIXED_HEADINGS):
        if heading in sections:
            lines = _clean_content_lines(list(sections[heading]))
            bullets = _join_bullet_continuations(lines)
            for bullet in bullets:
                if not bullet.strip():
                    continue
                bug_id = _extract_bug_id(bullet)
                if bug_id:
                    if bug_id not in seen_bug_ids:
                        fixed_bug_ids.append(bug_id)
                        seen_bug_ids.add(bug_id)
                else:
                    fixed_other_lines.append(bullet)

    return ReleaseEntry(
        version=version,
        date=date,
        summary=summary,
        implemented_lines=implemented_lines,
        fixed_bug_ids=fixed_bug_ids,
        fixed_other_lines=fixed_other_lines,
    )


# ---------------------------------------------------------------------------
# Known Issues projection
# ---------------------------------------------------------------------------


def _earliest_published_gte(version: str, published: list[str]) -> str:
    """Return the earliest PUBLISHED version >= the given version, or ''."""
    candidates = [p for p in published if _version_gte(p, version)]
    if not candidates:
        return ""
    return min(candidates, key=_parse_version)


def _bug_intersects_published(
    bug: BugRecord, published: list[str]
) -> bool:
    """Return True if the bug's affected range intersects the PUBLISHED list.

    The bug must have a verified ## Affects field that parses as a version
    string (e.g. 'v1.0.0').  Prose values (e.g. '(internal — plugin era;
    no KI)') are not version strings; they never intersect any published range
    and return False without emitting a warning (the warning is emitted by
    the caller that drives Public-ID assignment).

    The affected range is [Affects, Fixed In) — open on the right for open
    bugs.  A bug "intersects published" if there exists any published version P
    such that P >= Affects AND (Fixed In is absent OR P < Fixed In).
    """
    if not bug.affects:
        return False  # Unverified affects — never disclose
    if not _looks_like_version_string(bug.affects):
        return False  # Prose or unrecognised value — never disclose
    for p in published:
        if _version_gte(p, bug.affects):
            if not bug.fixed_in or _version_lt(p, bug.fixed_in):
                return True
    return False


def _assign_public_id(
    bug: BugRecord,
    published: list[str],
    counters: dict[str, int],
) -> str:
    """Determine the Public ID for a disclosable bug.

    If the bug already has ## Public ID set in its file, return it as-is
    (IDs are immutable once assigned).

    Otherwise: find the earliest PUBLISHED version >= bug.affects, compute
    the next counter for that anchor, assign it, write it back to the bug
    file, and return the new ID.

    counters is a mutable dict {anchor_version_no_v: max_counter_seen} that
    the caller maintains across all bugs to track the next free counter.
    """
    if bug.public_id:
        # Already assigned — extract the anchor and update the counter
        # so future bugs in the same anchor get a higher number.
        id_match = re.match(r"KI-(\d+\.\d+\.\d+)\.(\d+)$", bug.public_id)
        if id_match:
            anchor = id_match.group(1)
            n = int(id_match.group(2))
            if counters.get(anchor, 0) < n:
                counters[anchor] = n
        return bug.public_id

    # Compute anchor: earliest PUBLISHED version >= bug.affects
    anchor_with_v = _earliest_published_gte(bug.affects, published)
    if not anchor_with_v:
        # No published version is >= affects — should not reach here
        # (caller already checked intersects_published)
        return ""

    anchor = anchor_with_v.lstrip("v")
    next_n = counters.get(anchor, 0) + 1
    counters[anchor] = next_n
    new_id = f"KI-{anchor}.{next_n}"

    # Persist the Public ID back to the bug file
    _write_public_id_to_bug_file(bug.file_path, new_id)

    return new_id


def _write_public_id_to_bug_file(file_path: pathlib.Path, public_id: str) -> None:
    """Append or update the ## Public ID field in a bug file.

    If ## Public ID section exists but is empty, fill it in.
    If it does not exist, append the section at the end.
    """
    text = file_path.read_text(encoding="utf-8")

    # Check if ## Public ID section exists (possibly with content)
    section_pattern = re.compile(
        r"(^##\s+Public ID\s*$)([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = section_pattern.search(text)
    if match:
        # Section exists — replace its content
        existing_content = match.group(2)
        # Extract just the non-comment part
        content_no_comment = re.sub(
            r"<!--[\s\S]*?-->", "", existing_content
        ).strip()
        if not content_no_comment:
            # Empty body — insert the value after the heading and any comments
            # Find the comment block in the existing content
            comment_match = re.search(r"<!--[\s\S]*?-->", existing_content)
            if comment_match:
                # Insert after the comment
                comment_end = match.start(2) + comment_match.end()
                new_text = (
                    text[: comment_end]
                    + f"\n{public_id}\n"
                    + text[comment_end:]
                )
            else:
                # No comment block — the value goes directly under the
                # heading, with one blank line before whatever section
                # follows, whether or not the heading match consumed its
                # own trailing newline.
                heading_end = match.start(2)
                before = text[:heading_end].rstrip("\n") + "\n"
                after = text[heading_end:].lstrip("\n")
                new_text = before + f"{public_id}\n" + (f"\n{after}" if after else "")
            file_path.write_text(new_text, encoding="utf-8")
        # else: already has content (should not happen if we only write once)
    else:
        # Section absent — append at end of file
        new_text = text.rstrip("\n") + f"\n\n## Public ID\n{public_id}\n"
        file_path.write_text(new_text, encoding="utf-8")


def remove_bogus_public_ids(
    bugs_dir: pathlib.Path,
    published_manifest: list[str],
) -> list[pathlib.Path]:
    """Remove Public IDs from bug files whose ## Affects does not parse as a version.

    This is the idempotent cleanup rider for hand-scrub recovery.  It scans
    every BUG-*.md file in bugs_dir and removes the ## Public ID value from
    any file where the ## Affects section is either absent, empty, or not a
    recognisable version string.

    A file is considered "clean" if it has no Public ID, or if its Affects
    value is a recognisable version string.  Clean files are not modified.

    The operation is idempotent: running it twice on the same directory
    produces the same result as running it once.

    Returns the list of file paths that were modified (Public ID removed).
    """
    bugs_dir = pathlib.Path(bugs_dir)
    modified: list[pathlib.Path] = []

    for path in sorted(bugs_dir.glob("BUG-*.md"), key=lambda p: p.name):
        try:
            rec = parse_bug_file(path)
        except Exception as exc:  # noqa: BLE001
            print(
                f"Warning: could not parse {path} during cleanup: {exc}",
                file=sys.stderr,
            )
            continue

        # No Public ID — nothing to remove.
        if not rec.public_id:
            continue

        # Affects is absent or parses as a valid version — keep the Public ID.
        if not rec.affects or _looks_like_version_string(rec.affects):
            continue

        # Affects is non-empty prose — this Public ID was bogusly minted.
        # Remove the Public ID value from the bug file.
        _remove_public_id_from_bug_file(path)
        modified.append(path)

    return modified


def _remove_public_id_from_bug_file(file_path: pathlib.Path) -> None:
    """Remove the content of the ## Public ID section in a bug file.

    Leaves the ## Public ID heading in place (with blank body) so the
    section structure is preserved.  Idempotent: calling on a file that
    already has a blank ## Public ID is a no-op (no bytes change).
    """
    text = file_path.read_text(encoding="utf-8")

    section_pattern = re.compile(
        r"(^##\s+Public ID\s*\n)([\s\S]*?)(?=^##\s+|\Z)",
        re.MULTILINE,
    )
    match = section_pattern.search(text)
    if not match:
        return  # No ## Public ID section — nothing to do.

    heading = match.group(1)
    body = match.group(2)

    # Strip the non-comment content from the body.
    # Preserve any HTML comment (e.g. the template instruction comment).
    # Strip only lines that carry an actual value.
    body_lines = body.splitlines(keepends=True)
    new_body_lines = []
    for line in body_lines:
        stripped = line.strip()
        # Keep blank lines and comment lines; drop value lines.
        if not stripped or stripped.startswith("<!--"):
            new_body_lines.append(line)
        else:
            # This is the value line — drop it.
            pass

    new_body = "".join(new_body_lines)
    if new_body == body:
        return  # Already clean — byte-identical, no write needed.

    new_text = text[: match.start()] + heading + new_body + text[match.end():]
    file_path.write_text(new_text, encoding="utf-8")


def _render_known_issue(
    bug: BugRecord,
    public_id: str,
    published: list[str],
) -> str:
    """Render a single Known Issue line in the CHANGELOG format.

    Format:
        KI-A.B.C.n — symptom; affects vA · fixed in vP
        KI-A.B.C.n — symptom; affects vA · fix pending next release
        KI-A.B.C.n — symptom; affects vA · open
        KI-A.B.C.n — symptom; affects vA · won't fix — reason
    """
    # Normalize a trailing sentence-ending period off the symptom before
    # appending the "; affects ... · ..." suffix below — otherwise a
    # symptom that is itself a full sentence (e.g. "...one pane).")
    # produces a ".;" double-punctuation artifact once the suffix is
    # concatenated.
    symptom = bug.symptom.rstrip()
    if symptom.endswith("."):
        symptom = symptom[:-1]

    parts = [f"{public_id} — {symptom}"]

    # Affects clause
    affects_str = f"affects {bug.affects}"

    # Fixed-in / status clause
    if "wontdo" in bug.status or "won't" in bug.status or "wont" in bug.status:
        reason = bug.wontdo_reason or "see bug ledger"
        fix_clause = f"won't fix — {reason}"
    elif bug.fixed_in:
        # Translate internal fixed_in to earliest published version >= it
        pub_fix = _earliest_published_gte(bug.fixed_in, published)
        if pub_fix:
            fix_clause = f"fixed in {pub_fix}"
        else:
            fix_clause = "fix pending next release"
    elif bug.status in ("resolved", "closed", "fixed"):
        # Resolved but Fixed In not yet written — treat as pending
        fix_clause = "fix pending next release"
    else:
        fix_clause = "open"

    parts.append(f"; {affects_str} · {fix_clause}")
    return "".join(parts)


def _build_known_issues_projection(
    bugs: list[BugRecord],
    published: list[str],
    entry_version: str,
) -> list[str]:
    """Build the Known Issues lines for a given CHANGELOG entry (version).

    A bug is rendered under the entry for the EARLIEST PUBLISHED version it
    affects. So this function returns lines only for bugs whose earliest
    affected PUBLISHED version is entry_version.

    The counters dict is managed externally (per regeneration call) so that
    Public IDs are assigned consistently across all entries.

    Returns a list of rendered lines (may be empty).
    """
    # This function is called after public IDs have already been assigned.
    # Filter bugs that anchor to this entry's version.
    lines = []
    for bug in bugs:
        if not bug.public_id:
            continue  # Not disclosed (no public ID assigned)
        # Find the anchor version for this bug
        anchor_with_v = _earliest_published_gte(bug.affects, published)
        if anchor_with_v != entry_version:
            continue
        line = _render_known_issue(bug, bug.public_id, published)
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Entry rendering
# ---------------------------------------------------------------------------


def _render_section(title: str, items: list[str], none_text: str = "None") -> str:
    """Render a ### section with a list of items, or a None placeholder."""
    if items:
        body = "\n".join(items)
        return f"### {title}\n{body}"
    else:
        return f"### {title}\n{none_text}"


def _strip_internal_ids(text: str) -> str:
    """Remove any BUG-NNNN references from text.

    Internal bug IDs must never appear in generated CHANGELOG output.
    This is applied as a final safety pass to each section.
    """
    # Replace "BUG-NNNN — " or "**BUG-NNNN**" etc. with just the remainder
    # Pattern: BUG-digits optionally followed by colon/dash/space markers
    cleaned = re.sub(
        r"\*{0,2}BUG-\d+\*{0,2}[:\s–—-]*",
        "",
        text,
    )
    return cleaned


def _extract_ki_id_from_line(line: str) -> str:
    """Extract the KI-X.Y.Z.N identifier from a Bugs-Resolved bullet line.

    Matches bare KI IDs at the canonical leading position in a bullet line
    (the same position _extract_bug_id uses for BUG-NNNN tokens).  Returns
    the KI identifier string (e.g. 'KI-1.0.0.1') or '' if the line does
    not start with a KI token.

    Lines like '- KI-1.0.0.1 — resolved' or '- KI-1.0.0.2' match; prose
    lines that happen to mention a KI identifier mid-sentence do not.
    The shared reader in pgai_agent_kanban.lib.public_id is the one
    implementation; this name is kept for the writer's own call sites.
    """
    return leading_ki_id(line)


def _render_fixed_items(
    entry: ReleaseEntry,
    bugs_by_id: dict[str, BugRecord],
    published: list[str],
    public_id_to_bug: dict[str, BugRecord] | None = None,
) -> list[str]:
    """Render the Fixed section items for a release entry.

    - BUG-NNNN items:
        - Disclosed bug: render as "KI-A.B.C.n — <sanitized title>"
        - Internal-only bug: render as plain sanitized title line (no ID)
    - Non-BUG items that carry a bare KI identifier (e.g. from a Bugs-Resolved
      section listing "KI-1.0.0.1 — resolved"): joined against the bug ledger
      to produce "KI-A.B.C.n — <sanitized title>; affects vA · <status>".
    - Other non-BUG items: render as-is (stripped of any accidental BUG-NNNN refs)
    """
    lines = []
    reverse_index = public_id_to_bug or {}

    for bug_id in entry.fixed_bug_ids:
        bug = bugs_by_id.get(bug_id)
        if bug is None:
            # Bug ID in release notes but no matching bug file.  This most
            # commonly happens when a phantom ID was extracted from inline prose
            # (e.g. "see BUG-92 for context").  Silently drop it so the
            # placeholder never surfaces in CHANGELOG output.
            continue
        if bug.public_id:
            # Disclosed bug — render with public ID and sanitized title
            lines.append(f"{bug.public_id} — {bug.symptom}")
        else:
            # Internal-only — render sanitized title with no identifier
            lines.append(bug.symptom if bug.symptom else "(internal fix)")

    for line in entry.fixed_other_lines:
        # Check whether this line starts with a bare KI identifier.  When a
        # Bugs-Resolved section lists entries like "KI-1.0.0.1 — resolved", join
        # against the bug ledger to produce a proper disclosed-bug Fixed line
        # rather than propagating the bare placeholder text.
        ki_id = _extract_ki_id_from_line(line)
        if ki_id and ki_id in reverse_index:
            bug = reverse_index[ki_id]
            ki_line = _render_known_issue(bug, ki_id, published)
            # The substituted full-disclosure sentence must keep the same
            # leading list marker the concise "- KI-... — resolved" bullet
            # it replaces used, so the item still renders as part of the
            # surrounding bulleted list rather than as a bare paragraph.
            lines.append(f"- {ki_line}")
            continue

        cleaned = _strip_internal_ids(line)
        if cleaned.strip():
            lines.append(cleaned.strip())

    return lines


# ---------------------------------------------------------------------------
# Published artifact: spent Public IDs and lines kept as history
# ---------------------------------------------------------------------------


class PublishedChangelog(NamedTuple):
    """What the published CHANGELOG.md already carries.

    ids:    every Public ID that appears anywhere in the artifact — spent,
            whatever the bug files currently say.
    lines:  {(entry_version, section_title): [(public_id, line), ...]} for
            every line that starts with a Public ID, in artifact order, so a
            line whose bug file is gone can be kept where it was published.
    titles: {public_id: [title, ...]} for those same lines, used to tell a
            bug that legitimately carries an ID from one that was handed an
            ID the artifact already publishes for a different entry.
    """
    ids: set[str]
    lines: dict[tuple[str, str], list[tuple[str, str]]]
    titles: dict[str, list[str]]


_NOTHING_PUBLISHED = PublishedChangelog(set(), {}, {})


def read_published_changelog(path: pathlib.Path | None) -> PublishedChangelog:
    """Read the published CHANGELOG.md at *path*.

    ``None`` or an absent file reads as nothing published — no spent IDs and
    no lines to keep — which is the state of a project that has never
    regenerated its changelog.
    """
    if path is None or not pathlib.Path(path).is_file():
        return _NOTHING_PUBLISHED
    text = pathlib.Path(path).read_text(encoding="utf-8")
    lines: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    titles: dict[str, list[str]] = defaultdict(list)
    entry = ""
    section = ""
    for line in text.splitlines():
        heading = _ENTRY_HEADING_RE.match(line)
        if heading:
            entry, section = heading.group(1), ""
            continue
        sub_heading = _SECTION_HEADING_RE.match(line)
        if sub_heading:
            section = sub_heading.group(1)
            continue
        public_id = leading_ki_id(line)
        if public_id and entry and section:
            lines[(entry, section)].append((public_id, line))
            titles[public_id].append(ki_line_title(line))
    return PublishedChangelog(extract_ki_ids(text), dict(lines), dict(titles))


def _reserve_public_id(counters: dict[str, int], public_id: str) -> None:
    """Raise the anchor's counter to at least the counter *public_id* carries."""
    parts = _PUBLIC_ID_PARTS_RE.match(public_id)
    if parts:
        anchor, n = parts.group(1), int(parts.group(2))
        if counters[anchor] < n:
            counters[anchor] = n


def _next_public_id(counters: dict[str, int], anchor: str, spent: set[str]) -> str:
    """Advance the anchor's counter past every spent ID and return the fresh ID."""
    counters[anchor] += 1
    while f"KI-{anchor}.{counters[anchor]}" in spent:
        counters[anchor] += 1
    return f"KI-{anchor}.{counters[anchor]}"


def _holds_spent_id(bug: BugRecord, published: PublishedChangelog) -> bool:
    """Return True when the bug's persisted ID is published for a different entry.

    Every published line carrying the ID is compared by title with what the
    writer would render for this bug.  A status clause that moved from open
    to fixed is the same entry; a different title is not.
    """
    return any(
        not same_ki_title(title, bug.symptom)
        for title in published.titles.get(bug.public_id, [])
    )


def _kept_published_lines(
    published: PublishedChangelog,
    entry_version: str,
    section: str,
    rendered: list[str],
    carried_ids: set[str],
) -> list[str]:
    """Return the published lines of one section that only the artifact still carries.

    A line is kept when its Public ID is carried by no bug file (the file
    was closed, moved or re-anchored) and the freshly rendered section does
    not already carry that ID (a release-notes bullet may render it as-is).
    Lines come back in published order.
    """
    rendered_ids = {leading_ki_id(line) for line in rendered}
    kept: list[str] = []
    for public_id, line in published.lines.get((entry_version, section), []):
        if public_id in carried_ids or public_id in rendered_ids:
            continue
        rendered_ids.add(public_id)
        kept.append(line)
    return kept


# ---------------------------------------------------------------------------
# Main regeneration entry point
# ---------------------------------------------------------------------------


def regenerate(
    repo_root: pathlib.Path,
    released_versions: list[str],
    published_manifest: list[str],
    bugs_dir: pathlib.Path,
    extra_notes_dir: pathlib.Path | None = None,
    published_changelog: pathlib.Path | None = CHECKED_IN_CHANGELOG,
) -> str:
    """Regenerate the full CHANGELOG.md content as a string.

    Args:
        repo_root: Root of the git repository; release-notes/vX.Y.Z.md files
                   are read from here.
        released_versions: All release versions, newest first (e.g.
                           ['v1.16.3', 'v1.16.2', ..., 'v1.0.0']). Each must
                           have a corresponding release-notes/vX.Y.Z.md file.
        published_manifest: Versions publicly released, oldest first (e.g.
                            ['v1.0.0', 'v1.0.1']). May be empty (valid:
                            pre-publication state).
        bugs_dir: Path to the directory containing BUG-*.md files. When it is
                  a live project's ledger, the bugs archived from it are
                  read too (see the module docstring); a live file wins.
        extra_notes_dir: Optional overlay directory consulted for a version's
                         vX.Y.Z.md file when it is not found under
                         repo_root/release-notes. Lets a caller (a test
                         fixture, most commonly) inject a synthetic
                         release-notes file for a version that does not
                         exist on disk under repo_root, without writing into
                         the real repository tree. Ignored entirely when
                         None (default); production behavior is unchanged
                         when this argument is omitted.
        published_changelog: The already-published artifact whose Public IDs
                         are spent and whose orphaned KI lines are kept as
                         history. A relative path resolves under repo_root;
                         the default is repo_root/CHANGELOG.md. None means
                         nothing has been published: only the IDs persisted
                         in bug files are reserved, and no line is kept.

    Returns:
        The full text of the regenerated CHANGELOG.md.

    Notes:
        - When a disclosed bug lacks ## Public ID, this function assigns one
          and writes it back to the bug file (to ensure determinism on
          subsequent calls). The ID is the anchor's next counter past every
          ID persisted in a bug file or present in published_changelog.
        - A bug whose persisted ## Public ID is published for a different
          title is given a fresh ID the same way; the published line is
          left as it is.
        - A published KI line whose ID no bug file carries is emitted
          verbatim in the entry and section it was published under.
        - BUG-NNNN identifiers never appear in the returned string.
        - The returned string is deterministic: given unchanged inputs and bug
          files, two consecutive calls return byte-identical strings.
    """
    repo_root = pathlib.Path(repo_root)
    bugs_dir = pathlib.Path(bugs_dir)
    if published_changelog is not None:
        published_changelog = repo_root / published_changelog
    published_artifact = read_published_changelog(published_changelog)

    # --- Load and parse bugs ---
    all_bugs = load_bugs(bugs_dir, include_archive=True)
    bugs_by_id: dict[str, BugRecord] = {b.bug_id: b for b in all_bugs}

    # --- Warn on non-empty Affects values that do not parse as a version ---
    # These bugs will never receive a Public ID.  The warning names the file
    # so the operator can correct or remove the malformed Affects value.
    # Internal bugs should omit the ## Affects section entirely.
    for b in all_bugs:
        if b.affects and not _looks_like_version_string(b.affects):
            _warn_unparseable_affects(b.file_path, b.affects)

    # --- Determine which bugs are disclosable ---
    # A bug is disclosable iff:
    #   1. It has a verified ## Affects field that parses as a version string
    #   2. Its affected range intersects the published manifest
    disclosable: list[BugRecord] = [
        b for b in all_bugs
        if _bug_intersects_published(b, published_manifest)
    ]

    # --- Assign Public IDs (modifies bug files on first assignment) ---
    # First pass: reserve every ID already spent.  That is every ID persisted
    # in ANY bug file (not just disclosable ones — a bug may carry a stamped
    # KI Public ID while currently non-disclosable, e.g. its Fixed In is an
    # unparseable prose placeholder) and every ID the published artifact
    # carries, whether or not a bug file still exists for it.  Reserving both
    # is what keeps a new bug from being handed an identifier a reader has
    # already seen mean something else.
    counters: dict[str, int] = defaultdict(int)
    spent_ids: set[str] = set(published_artifact.ids)
    for bug in all_bugs:
        if bug.public_id:
            spent_ids.add(bug.public_id)
            _reserve_public_id(counters, bug.public_id)
    for public_id in published_artifact.ids:
        _reserve_public_id(counters, public_id)

    # A bug persisting an ID the artifact publishes for a different title was
    # handed a spent ID.  The published line is history and stays; the bug is
    # queued for a fresh ID exactly like one that never had an ID.
    reissue = [
        b for b in disclosable
        if b.public_id and _holds_spent_id(b, published_artifact)
    ]

    # Second pass: assign IDs to bugs that lack one, in a stable order
    # (sort by affects version ascending, then by bug_id ascending to break ties).
    needs_id = [b for b in disclosable if not b.public_id] + reissue
    needs_id.sort(key=lambda b: (_parse_version(b.affects), b.bug_id))

    # Rebuild disclosable with updated public_ids (we re-parse after writing).
    new_ids: dict[str, str] = {}
    for bug in needs_id:
        anchor_with_v = _earliest_published_gte(bug.affects, published_manifest)
        if not anchor_with_v:
            continue
        anchor = anchor_with_v.lstrip("v")
        new_id = _next_public_id(counters, anchor, spent_ids)
        spent_ids.add(new_id)
        if bug.public_id:
            _remove_public_id_from_bug_file(bug.file_path)
        _write_public_id_to_bug_file(bug.file_path, new_id)
        new_ids[bug.bug_id] = new_id

    # Reload bugs to pick up newly written Public IDs
    if new_ids:
        all_bugs = load_bugs(bugs_dir, include_archive=True)
        bugs_by_id = {b.bug_id: b for b in all_bugs}
        disclosable = [
            b for b in all_bugs
            if _bug_intersects_published(b, published_manifest)
        ]

    # --- Build reverse index: public_id → BugRecord ---
    # Used by _render_fixed_items to join titles for bare KI IDs sourced from
    # release-notes Bugs-Resolved sections (e.g. "KI-1.0.0.1 — resolved").
    public_id_to_bug: dict[str, BugRecord] = {
        b.public_id: b for b in all_bugs if b.public_id
    }
    carried_ids: set[str] = set(public_id_to_bug)

    # --- Parse release notes ---
    notes_dir = repo_root / "release-notes"
    entries: list[ReleaseEntry] = []
    for version in released_versions:
        notes_path = notes_dir / f"{version}.md"
        if not notes_path.exists() and extra_notes_dir is not None:
            # Fall back to the injected overlay directory (test fixtures only
            # in practice); see the extra_notes_dir docstring above.
            notes_path = extra_notes_dir / f"{version}.md"
        if not notes_path.exists():
            # Skip versions with no notes file (should not happen in practice)
            continue
        entry = parse_release_notes(version, notes_path)
        entries.append(entry)

    # --- Build CHANGELOG content ---
    sections_text: list[str] = []

    for entry in entries:
        # Header line
        date_str = entry.date if entry.date else "unknown"
        entry_header = f"## {entry.version} — {date_str}"

        # Summary line (if present)
        summary_line = entry.summary if entry.summary else ""

        # Implemented section
        impl_lines = entry.implemented_lines
        impl_text = _render_section("Implemented", impl_lines, "None (bugfix release)")

        # Fixed section — classification is load-bearing:
        # a bundle with BUG items must never produce Fixed: None
        fixed_items = _render_fixed_items(
            entry, bugs_by_id, published_manifest, public_id_to_bug
        )
        # A published Fixed line whose bug file is gone is history: keep it.
        fixed_items.extend(
            _kept_published_lines(
                published_artifact, entry.version, "Fixed", fixed_items, carried_ids
            )
        )
        if not fixed_items and entry.fixed_bug_ids:
            # Should never happen: BUG items in release notes but no rendered output.
            # This indicates a bug file lookup failure — emit a placeholder.
            fixed_items = ["(one or more bug fixes — bug files unavailable)"]
        fixed_text = _render_section("Fixed", fixed_items)

        # Known Issues projection — render under earliest affected PUBLISHED version
        ki_lines_for_entry: list[str] = []
        if published_manifest:
            for bug in disclosable:
                if not bug.public_id:
                    continue
                anchor = _earliest_published_gte(bug.affects, published_manifest)
                if anchor == entry.version:
                    ki_line = _render_known_issue(bug, bug.public_id, published_manifest)
                    ki_lines_for_entry.append(ki_line)
        # A published Known Issue whose bug file is gone is history: keep it.
        ki_lines_for_entry.extend(
            _kept_published_lines(
                published_artifact,
                entry.version,
                "Known Issues",
                ki_lines_for_entry,
                carried_ids,
            )
        )
        # Sort by public_id for determinism
        ki_lines_for_entry.sort()
        ki_text = _render_section("Known Issues", ki_lines_for_entry)

        # Detail link
        detail_link = (
            f"*Details: [release-notes/{entry.version}.md]"
            f"(release-notes/{entry.version}.md)*"
        )

        # Assemble entry
        entry_parts = [entry_header]
        if summary_line:
            entry_parts.append("")
            entry_parts.append(summary_line)
        entry_parts.append("")
        entry_parts.append(impl_text)
        entry_parts.append("")
        entry_parts.append(fixed_text)
        entry_parts.append("")
        entry_parts.append(ki_text)
        entry_parts.append("")
        entry_parts.append(detail_link)

        sections_text.append("\n".join(entry_parts))

    # Join all entries with separators
    body = "\n\n---\n\n".join(sections_text)
    if body:
        content = f"{_HEADER}\n---\n\n{body}\n\n---\n"
    else:
        content = f"{_HEADER}\n---\n"

    # Safety pass: verify no BUG-NNNN leaked through
    if _BUG_ID_PATTERN.search(content):
        # Strip any remaining references as a defensive measure,
        # then re-verify (this should not happen if the rendering logic
        # is correct, but we guard against future regressions).
        content = re.sub(
            r"\*{0,2}BUG-\d+\*{0,2}[:\s–—-]*",
            "",
            content,
        )

    return content


# ---------------------------------------------------------------------------
# CLI entry point (for testing from shell)
# ---------------------------------------------------------------------------


def _main() -> None:
    """Minimal CLI: regenerate CHANGELOG.md to stdout.

    Usage: python -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir> \\
               [version1 version2 ...]

    A live project's bugs_dir brings its archived bugs with it (see the
    module docstring).

    Versions are newest-first. If none are provided, they are discovered by
    scanning release-notes/vX.Y.Z.md files in the repo.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Regenerate CHANGELOG.md from release-notes and bug ledger."
    )
    parser.add_argument("repo_root", help="Root of the git repository.")
    parser.add_argument(
        "bugs_dir",
        help="Directory containing BUG-*.md files. For a live project's ledger "
             "(<kanban_root>/projects/<project>/bugs) the bugs archived from it "
             "are read too; a live file wins.",
    )
    parser.add_argument(
        "versions",
        nargs="*",
        help="Release versions newest-first. Auto-discovered if omitted.",
    )
    args = parser.parse_args()

    repo_root = pathlib.Path(args.repo_root)

    # Load published manifest (absent file = empty manifest: no published releases).
    published = load_published_manifest(repo_root)

    # Discover versions if not provided
    if args.versions:
        versions = args.versions
    else:
        notes_dir = repo_root / "release-notes"
        found = sorted(
            notes_dir.glob("v*.md"),
            key=lambda p: _parse_version(p.stem),
            reverse=True,
        )
        versions = [p.stem for p in found]

    bugs_dir = pathlib.Path(args.bugs_dir)
    content = regenerate(repo_root, versions, published, bugs_dir)
    sys.stdout.write(content)


if __name__ == "__main__":
    _main()
