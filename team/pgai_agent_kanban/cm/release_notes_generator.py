"""
release_notes_generator.py -- the generator behind a release's
``release-notes/<version>.md``.

The CM release-notes stage (team/scripts/lib/cm/release_notes.sh,
``cm_release_generate_notes``) calls this module through ``pp_run_ops`` when
no notes file for the release is committed yet. It writes the notes file
with nine sections, every one present even when empty::

    ## Breaking Changes   ## Upgrade Notes   ## Features
    ## Bug Fixes          ## Deprecations    ## Documentation
    ## Other Changes      ## Bugs Skipped    ## Known Issues

Where the bullets come from, in priority order:

1. A requirements bundle file whose ``## Bundled Items`` section lists item
   paths as ``(`path`)``. Each item is placed by its own ``## Category``
   (see ``CATEGORY_SECTION``); a bug report (``BUG-NNNN-*.md``) always lands
   under Bug Fixes, whatever its Category names. Each bullet describes the
   item's substance (``get_item_title``), never its bare internal ledger ID.
2. A bundle file with no bundled items is an operator-authored item in its
   own right: its Category and H1 title make one bullet. A requirements
   document (``## Category`` Requirement) is a feature, under Features.
3. With no bundle file, the RC branch's commit subjects since the merge
   base, all under Other Changes.

``## Documentation`` also lists every WRITER ticket that shipped in the
release: each task folder under TASKS_DIR whose README names Role WRITER
and the release's own ``## Release Version``, and whose status.md reads
State DONE, adds one bullet -- the lead sentence of the task's ``## Goal``,
scrubbed (``shipped_documentation``). A lead sentence that, scrubbed, names
only the items it cites gives way to the task's own title, and failing that
to a plain statement that documentation shipped, so no bullet is scrub
placeholders alone. Task folders are read in name order.

``## Bugs Skipped`` and ``## Known Issues`` are rendered from blocks the
ship-policy stage already built and scrubbed; this module places them as
given and never recomputes them. The one derived body is Known Issues
under status KNOWN-BUGS: it lists the Bugs Skipped rows rated medium or
higher (or carrying no recognised rating), or, when every row is rated
low, says the table above lists them (``known_bugs_issues``). Under
KNOWN-BUGS with no Bugs Skipped rows, and under FUNCTIONAL, it reads
"None".

Every rendered substance passes through the shared internal-ID scrub
(``_strip_internal_ids`` in team/scripts/lib/cm/internal_id_scrub.py), so no
internal bug, priority, task-ID or requirements-document path reaches the
notes. ``_load_internal_id_scrub`` says how the scrub module is found.

Usage (CLI, consumed by release_notes.sh's cm_release_generate_notes)::

    python3 -m pgai_agent_kanban.cm.release_notes_generator \\
        BUNDLE_FILE REPO_ROOT ACTIVE_RC STATUS RELEASE_DATE PROJECT_NAME \\
        MERGE_BASE RC_BRANCH [KNOWN_ISSUE_BLOCK [BUGS_SKIPPED_BLOCK [TASKS_DIR]]]

    BUNDLE_FILE        requirements bundle path, or "none"
    REPO_ROOT          repository root; the notes go to
                       REPO_ROOT/release-notes/ACTIVE_RC.md
    ACTIVE_RC          release version string, e.g. v1.2.3
    STATUS             FUNCTIONAL, KNOWN-BUGS, KNOWN-ISSUE or NON-FUNCTIONAL
    RELEASE_DATE       date printed under the title, e.g. 2026-01-31
    PROJECT_NAME       project name printed in the title
    MERGE_BASE         commit the RC branch diverged from, or "none"
    RC_BRANCH          RC branch whose commit subjects feed the fallback
    KNOWN_ISSUE_BLOCK  Known Issues body for a KNOWN-ISSUE or NON-FUNCTIONAL
                       ship (optional; empty when absent)
    BUGS_SKIPPED_BLOCK rendered Bugs Skipped table (optional; empty renders
                       "None")
    TASKS_DIR          the project's task-folder directory, read for the
                       WRITER tickets listed under Documentation (optional;
                       "none", empty or absent lists none)

    Writes the notes file, prints one indented line naming it, and exits 0.
    Exits 2 with a usage line on stderr when fewer than eight arguments are
    given; -h or --help prints this usage and exits 0.

Usage (import)::

    from pgai_agent_kanban.cm.release_notes_generator import get_item_title
    title = get_item_title("/path/to/BUG-0001-example.md")
"""

import importlib.util
import os
import pathlib
import re
import subprocess
import sys

# Run by file path (``python3 .../release_notes_generator.py``) or loaded from
# a file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package import
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the import resolves from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.read_state_field import read_state_field  # noqa: E402
from pgai_agent_kanban.cm.release_version import release_version_of  # noqa: E402
from pgai_agent_kanban.lib.defer_posture import severity_rank  # noqa: E402
from pgai_agent_kanban.task_status import read_section  # noqa: E402

__all__ = [
    "CATEGORY_SECTION",
    "SECTION_ORDER",
    "read_item_category",
    "get_bug_substance",
    "get_priority_substance",
    "get_item_title",
    "shipped_documentation",
    "known_bugs_issues",
    "collect_sections",
    "render_notes",
    "generate_release_notes",
    "main",
]

_SCRUB_MODULE_FILENAME = "internal_id_scrub.py"
_SCRUB_DIR_ENV = "PGAI_CM_INTERNAL_ID_SCRUB_DIR"


def _load_internal_id_scrub():
    """Load internal_id_scrub.py, the scrub shared with the known-issue
    stage, and return the module.

    The scrub ships beside the CM bash stages in scripts/lib/cm/, not inside
    this package. Its directory is the one named by the
    PGAI_CM_INTERNAL_ID_SCRUB_DIR environment variable, which
    cm_release_generate_notes sets to its own directory; without it, this
    module finds its own tree's copy: the package root holding
    pgai_agent_kanban/ also holds scripts/lib/cm/, in the repository's
    team/ directory and in an installed root alike. The file is loaded by
    path, so sys.path is left untouched. A missing file raises ImportError
    naming the path that was tried."""
    scrub_dir = os.environ.get(_SCRUB_DIR_ENV, "")
    if not scrub_dir:
        scrub_dir = str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lib" / "cm")
    scrub_path = pathlib.Path(scrub_dir) / _SCRUB_MODULE_FILENAME
    if not scrub_path.is_file():
        raise ImportError(
            f"release_notes_generator: {scrub_path} not found. The internal-ID "
            f"scrub ships beside the CM bash stages; point {_SCRUB_DIR_ENV} at "
            "the directory holding it, e.g. "
            f"{_SCRUB_DIR_ENV}=team/scripts/lib/cm in a repository checkout."
        )
    spec = importlib.util.spec_from_file_location("internal_id_scrub", scrub_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INTERNAL_ID_SCRUB = _load_internal_id_scrub()
_strip_internal_ids = _INTERNAL_ID_SCRUB._strip_internal_ids
_strip_provenance_refs = _INTERNAL_ID_SCRUB._strip_provenance_refs

# Category -> section mapping.
CATEGORY_SECTION = {
    "breaking": "Breaking Changes",
    "feature": "Features",
    "requirement": "Features",
    "bugfix": "Bug Fixes",
    "deprecation": "Deprecations",
    "removal": "Deprecations",
    "docs": "Documentation",
    "misc": "Other Changes",
}
SECTION_ORDER = [
    "Breaking Changes",
    "Upgrade Notes",
    "Features",
    "Bug Fixes",
    "Deprecations",
    "Documentation",
    "Other Changes",
    "Bugs Skipped",
    "Known Issues",
]


def read_item_category(item_path):
    """Read ## Category field from an item file. Default: 'misc'.

    The section is read by task_status.read_section with end_at_subheading;
    its first non-blank line is the value, lower-cased, and a '#' line
    before any value means there is none.
    """
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return "misc"
    body = read_section(text, "Category", end_at_subheading=True) or ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            break  # a comment line before any value — no value found
        if stripped:
            return stripped.lower()
    return "misc"


_H1_LINE_RE = re.compile(r"^#\s+(.*)$")

# A bundled item's own filename is the cheapest reliable signal for what
# kind of ledger entry it is: bug and priority files are always named
# BUG-NNNN-slug.md / PRIORITY-NNNN-slug.md by the filing contract each
# template enforces (see team/templates/project/release/{BUG,PRIORITY}-
# TEMPLATE.md). Both templates' own H1 line IS the bare internal ID
# ("# BUG-NNNN-slug"), which is exactly the leak this dispatch avoids by
# routing those two kinds through their own substance-extraction path
# instead of the generic H1 lookup below.
_BUG_FILENAME_RE = re.compile(r"^BUG-\d+", re.IGNORECASE)
_PRIORITY_FILENAME_RE = re.compile(r"^PRIORITY-\d+", re.IGNORECASE)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# A leading Markdown list marker ("- ", "* ", "+ ", "1. ") already on a line.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _bullet(text):
    """One Markdown bullet for *text*, never a second marker on top of its own."""
    return "- " + _LIST_MARKER_RE.sub("", text)


def _parse_section(text, heading):
    """Return the stripped body of a '## <heading>' section in *text*, or
    '' when the section is absent or empty. The section is read by
    task_status.read_section: it stops at the next '##' heading (a '###'
    subheading stays in the body) or end of file."""
    return read_section(text, heading) or ""


# A Markdown code span, as the scrub defines it: punctuation inside one never
# ends a sentence, and the scrub's tidy leaves its content as written.
_CODE_SPAN_RE = _INTERNAL_ID_SCRUB._CODE_SPAN_RE
_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")


def _first_sentence(text):
    """Collapse whitespace (including newlines) to single spaces and return
    the first sentence, or the whole flattened string when no sentence-
    ending punctuation is found. Punctuation inside a backtick code span
    (``my-app build . --target 2``) never ends the sentence."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    spans = [match.span() for match in _CODE_SPAN_RE.finditer(flat)]
    for match in _SENTENCE_END_RE.finditer(flat):
        if not any(start <= match.start() < end for start, end in spans):
            return flat[: match.start() + 1].strip()
    return flat


def _substance_from_section(text, heading):
    """Extract a '## <heading>' section, strip HTML comments, reduce to one
    clean sentence, and scrub any internal identifiers from the result."""
    raw = _LIST_MARKER_RE.sub("", _HTML_COMMENT_RE.sub("", _parse_section(text, heading)))
    return _strip_internal_ids(_first_sentence(raw))


# An opening code-fence line: three or more backticks or tildes, optionally
# indented and optionally followed by an info string ("```text").
_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})")
# A Markdown heading line nested inside a section body ("### Detail").
_SUBHEADING_RE = re.compile(r"^\s*#{1,6}\s")
# Abbreviations whose trailing '.' sits mid-sentence, never at its end.
_NON_TERMINAL_ABBREVIATIONS = frozenset({"e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl."})
_TRAILING_WORD_RE = re.compile(r"[A-Za-z.]+$")


def _parse_section_outside_fences(text, heading):
    """Return the body of a '## <heading>' section in *text*, or '' when it
    is absent. Like _parse_section, but read with task_status.read_section's
    fence reading: a '##' line inside a fenced code block is part of the
    body rather than the start of the next section, so a quoted Markdown
    excerpt (a pasted '## Bug Fixes' block, say) does not cut the section
    short."""
    return read_section(text, heading, fences=True) or ""


def _lead_paragraph(text):
    """Return the first prose paragraph of a section body as one line.

    The paragraph ends at a blank line, a code fence, a nested heading, or
    the start of a list item, so a lead sentence introducing a fenced log
    excerpt or a bulleted list ("... fails three tests:") is taken without
    the material it introduces. Fenced blocks are skipped whole and never
    contribute text; when the body opens with a fence or a heading, the
    first prose after it is the lead. When the body opens with a list item,
    that item alone (its marker removed) is the lead. Whitespace, including
    newlines, collapses to single spaces."""
    paragraph = []
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            continue
        opener = _FENCE_OPEN_RE.match(line)
        if opener:
            if paragraph:
                break
            fence = opener.group(1)
            continue
        if not stripped or _SUBHEADING_RE.match(line):
            if paragraph:
                break
            continue
        if _LIST_MARKER_RE.match(line):
            if paragraph:
                break
            stripped = _LIST_MARKER_RE.sub("", line).strip()
        paragraph.append(stripped)
    return " ".join(" ".join(paragraph).split())


def _is_abbreviation_end(flat, index):
    """True when the '.' at *index* in *flat* closes a known abbreviation
    ('e.g.', 'i.e.', 'etc.', ...) rather than a sentence."""
    match = _TRAILING_WORD_RE.search(flat[: index + 1])
    return bool(match) and match.group(0).lower() in _NON_TERMINAL_ABBREVIATIONS


def _lead_sentence(flat):
    """Return the first complete sentence of the one-line string *flat*.

    A '.', '!' or '?' followed by whitespace or the end of the string ends
    the sentence only when it sits outside any backtick code span and any
    open '(' or '[' group, and does not close a known abbreviation — so the
    returned sentence keeps its parentheses and backticks balanced whenever
    the source text had them balanced. A sentence that ends in ':' or ';'
    (a lead-in whose list or code block was dropped) or in no terminal
    punctuation at all is closed with '.'."""
    depth = 0
    in_code = False
    sentence = flat
    for index, char in enumerate(flat):
        if char == "`":
            in_code = not in_code
        elif in_code:
            continue
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif (
            char in ".!?"
            and depth == 0
            and (index + 1 == len(flat) or flat[index + 1].isspace())
            and not (char == "." and _is_abbreviation_end(flat, index))
        ):
            sentence = flat[: index + 1]
            break
    sentence = sentence.rstrip().rstrip(":;,").rstrip()
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def get_bug_substance(text):
    """Build a substance description for a bug item from its own Symptom
    text — the content-described-notes policy: release notes state WHAT was
    wrong, never the bare internal ledger ID that the bug template uses as
    its own H1 line.

    Renders 'Fixed: <symptom>', where <symptom> is the first sentence of
    the Symptom section's lead paragraph (see _lead_paragraph and
    _lead_sentence): one line, no fenced text, no list items, abbreviations
    such as 'e.g.' kept intact, and any internal identifier scrubbed.

    The Fix section is never a source. A filed bug's Fix is a proposed
    remedy written before the work was done, often a menu of options, so it
    does not say what shipped. Files Involved is never a source either: it
    lists candidate edits, not what landed. A bug with no usable Symptom
    prose, including a Fix-only bug, renders a generic, ID-free sentence."""
    symptom = _parse_section_outside_fences(text, "Symptom")
    lead = _lead_paragraph(_HTML_COMMENT_RE.sub("", symptom))
    symptom_text = _strip_internal_ids(_lead_sentence(lead)) if lead else ""
    if symptom_text:
        return f"Fixed: {symptom_text}"
    return "Fixed an internal defect (no Symptom/Fix text recorded in the bug report)."


def get_priority_substance(text):
    """Build a substance description for a priority item from its own Title
    (falling back to Description), matching the same no-bare-ID policy
    get_bug_substance applies to bugs."""
    title_text = _substance_from_section(text, "Title")
    if title_text:
        return title_text
    description_text = _substance_from_section(text, "Description")
    if description_text:
        return description_text
    return "Completed a requested enhancement (no Title/Description text recorded)."


def get_item_title(item_path):
    """Return a substance description for a bundled item, never a bare
    internal ledger ID.

    - BUG-NNNN-*.md files: description sourced from the lead sentence of
      the bug's own Symptom section, never its Fix section (see
      get_bug_substance). The template's H1 line is the bug ID itself, so
      bug files never take the generic H1 path below.
    - PRIORITY-NNNN-*.md files: description sourced from the item's own
      Title (falling back to Description); see get_priority_substance. Same
      reasoning: the template's H1 line is the priority ID itself.
    - Everything else (operator-authored bundle titles, misc items): the
      first true H1 line (a single leading '#' followed by whitespace),
      stripped of any internal identifier that happens to appear in it, or
      the file stem when no H1 line is present. Deeper headings ('##',
      '###', etc.) never match."""
    filename = pathlib.Path(item_path).name
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return pathlib.Path(item_path).stem

    if _BUG_FILENAME_RE.match(filename):
        return get_bug_substance(text)
    if _PRIORITY_FILENAME_RE.match(filename):
        return get_priority_substance(text)

    for line in text.splitlines():
        stripped = line.strip()
        match = _H1_LINE_RE.match(stripped)
        if match:
            cleaned = _strip_internal_ids(match.group(1).strip())
            if cleaned:
                return cleaned
            break
    return pathlib.Path(item_path).stem


# An absolute path into one account's home directory or the scratch root
# ("/home/<name>/...", "/tmp/..."), bare or backticked. A task Goal can cite
# one; it means nothing on a reader's machine, so a Documentation line names
# it generically instead.
_LOCAL_PATH_RE = re.compile(r"`?(?<![\w.~/-])/(?:home|tmp)/[^\s`'\")\]]+`?")
_LOCAL_PATH_REPLACEMENT_TEXT = "a local path"


def _first_word_upper(value):
    """The first run of letters in *value* (bold and backtick markers
    ignored), upper-cased; '' when there is none."""
    match = re.search(r"[A-Za-z]+", re.sub(r"[*`]", " ", value or ""))
    return match.group(0).upper() if match else ""


# The phrase the shared scrub puts where an internal ID stood.
_ID_PLACEHOLDER_TEXT = _INTERNAL_ID_SCRUB._ID_REPLACEMENT_TEXT
# A parenthesised or bracketed aside with no aside nested inside it.
_ASIDE_RE = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")
# Where a sentence's main clause ends: a comma, semicolon, colon or dash.
_MAIN_CLAUSE_END_RE = re.compile(r"[,;:]|\s*[–—]\s*|\s-+\s")
_CLAUSE_WORD_RE = re.compile(r"[A-Za-z0-9][\w./'-]*")
# Words that only say documentation exists or join the items it is for. A
# main clause holding nothing else beside the placeholder names no subject.
_EMPTY_CLAUSE_WORDS = frozenset(
    {
        "documentation",
        "document",
        "documents",
        "documented",
        "documenting",
        "docs",
        "doc",
        "paired",
        "pair",
        "matching",
        "companion",
        "a",
        "an",
        "the",
        "this",
        "these",
        "that",
        "those",
        "its",
        "their",
        "each",
        "both",
        "all",
        "and",
        "or",
        "plus",
        "for",
        "of",
        "on",
        "about",
        "covering",
        "to",
        "with",
        "per",
        "from",
        "in",
        "by",
    }
)
# A task README's H1 line opens with this label ("# Task: <title>").
_TASK_TITLE_LABEL_RE = re.compile(r"^task\s*:\s*", re.IGNORECASE)
# The bullet for a shipped WRITER ticket neither of whose sources names
# what it documents once scrubbed.
_DOCUMENTATION_SHIPPED_TEXT = "Further documentation updates shipped in this release."


def _is_placeholder_only(sentence):
    """True when the scrubbed *sentence* names nothing but the items the
    scrub replaced. Its main clause -- the text before the first comma,
    semicolon, colon or dash, once parenthesised and bracketed asides are
    removed -- holds only the scrub's placeholder, words naming
    documentation itself and connecting words: "Paired documentation for
    another item." is placeholder-only, "Document what another item changed
    for operators." is not. A sentence without the placeholder never is."""
    if _ID_PLACEHOLDER_TEXT not in sentence:
        return False
    clause, removed = sentence, 1
    while removed:
        clause, removed = _ASIDE_RE.subn(" ", clause)
    clause = _MAIN_CLAUSE_END_RE.split(clause, maxsplit=1)[0]
    words = _CLAUSE_WORD_RE.findall(clause.replace(_ID_PLACEHOLDER_TEXT, " "))
    return all(word.lower().rstrip(".!?") in _EMPTY_CLAUSE_WORDS for word in words)


def _task_title(readme_text):
    """The task README's first H1 line without its ``Task:`` label, or ''."""
    for line in readme_text.splitlines():
        match = _H1_LINE_RE.match(line.strip())
        if match:
            return _TASK_TITLE_LABEL_RE.sub("", match.group(1).strip()).strip()
    return ""


def _documentation_sentence(goal_lead, readme_text):
    """The Documentation line for one shipped WRITER ticket: the first of
    the Goal's lead sentence and the README's task title that is not
    placeholder-only once scrubbed (see _is_placeholder_only), each cut to
    one sentence with internal IDs, requirements paths and local absolute
    paths scrubbed; when neither names its subject, a plain statement that
    documentation shipped."""
    for source in (goal_lead, _task_title(readme_text)):
        if not source:
            continue
        sentence = _strip_internal_ids(_LOCAL_PATH_RE.sub(_LOCAL_PATH_REPLACEMENT_TEXT, _lead_sentence(source)))
        if sentence and not _is_placeholder_only(sentence):
            return sentence
    return _DOCUMENTATION_SHIPPED_TEXT


def shipped_documentation(tasks_dir, active_rc):
    """Return one Documentation bullet per WRITER ticket that shipped in
    *active_rc*, in task-folder name order.

    A folder under *tasks_dir* counts when its README.md names ``## Role``
    WRITER and a ``## Release Version`` equal to *active_rc* (read through
    release_version.release_version_of, so ``ai_v1.2.3`` and ``1.2.3`` match
    ``v1.2.3``), and its status.md reads ``## State`` DONE. The bullet is the
    lead sentence of the README's ``## Goal`` (see _lead_paragraph and
    _lead_sentence), with internal IDs, requirements paths and local
    absolute paths scrubbed. A lead sentence that, scrubbed, names only the
    items it cites ("Paired documentation for another item.") gives way to
    the README's task title, scrubbed the same way, and failing that to a
    plain statement that documentation shipped (see
    _documentation_sentence), so no bullet is scrub placeholders alone. A
    ticket with no Goal prose adds nothing, and a sentence already listed is
    not listed twice. *tasks_dir* "none", empty, or not a directory lists
    nothing."""
    wanted = release_version_of(active_rc or "")
    if not tasks_dir or tasks_dir == "none" or not wanted:
        return []
    root = pathlib.Path(tasks_dir)
    if not root.is_dir():
        return []
    bullets = []
    for task_dir in sorted(root.iterdir()):
        readme = task_dir / "README.md"
        status = task_dir / "status.md"
        if not (readme.is_file() and status.is_file()):
            continue
        if _first_word_upper(read_state_field(str(readme), "Role")) != "WRITER":
            continue
        if release_version_of(read_state_field(str(readme), "Release Version")) != wanted:
            continue
        if _first_word_upper(read_state_field(str(status), "State")) != "DONE":
            continue
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        lead = _lead_paragraph(_HTML_COMMENT_RE.sub("", _parse_section_outside_fences(text, "Goal")))
        if not lead:
            continue
        sentence = _documentation_sentence(lead, text)
        bullet = _bullet(sentence)
        if sentence and bullet not in bullets:
            bullets.append(bullet)
    return bullets


# A Markdown table separator cell ("---", ":--:").
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
# A cell boundary: a '|' the Bugs Skipped renderer did not escape.
_TABLE_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
# The lowest severity rank (defer_posture.severity_rank) Known Issues lists.
_KNOWN_ISSUE_MIN_RANK = severity_rank("medium")


def _table_rows(block):
    """The data rows of the Markdown table in *block*, each a list of its
    cells with '\\|' unescaped. Rows above the last separator line (the
    header) are dropped; a block with no separator is all data rows."""
    rows = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in _TABLE_CELL_SPLIT_RE.split(stripped.strip("|"))]
        if all(_TABLE_SEPARATOR_CELL_RE.match(cell) for cell in cells if cell):
            rows = []  # everything so far was the header
            continue
        rows.append(cells)
    return rows


def known_bugs_issues(bugs_skipped_block):
    """Return the Known Issues lines for a KNOWN-BUGS release, built from
    its rendered Bugs Skipped table, or [] when the table has no rows.

    Every row rated medium or higher -- or carrying a severity that names no
    recognised rung, which cannot be shown to be low -- becomes one bullet:
    its substance, its public ID in bold ahead of it when one is disclosed,
    and its severity. When every row is rated low, one sentence points at
    the table instead. Cells come from the table as given; the Bugs Skipped
    renderer already scrubbed them."""
    rows = [row for row in _table_rows(bugs_skipped_block) if row and row[0]]
    if not rows:
        return []
    bullets = []
    for row in rows:
        substance = row[0]
        public_id = row[1] if len(row) > 1 else ""
        severity = row[2] if len(row) > 2 else ""
        rank = severity_rank(severity)
        if rank is not None and rank < _KNOWN_ISSUE_MIN_RANK:
            continue
        rating = f"severity {severity.lower()}" if rank is not None else "severity not rated"
        if public_id and public_id.lower() != "internal-only":
            bullets.append(f"- **{public_id}** — {substance} ({rating})")
        else:
            bullets.append(f"- {substance} ({rating})")
    if not bullets:
        return ["This release ships with low-severity bugs unfixed; the Bugs " "Skipped table above lists them."]
    return [
        "This release ships with known bugs that did not block shipping. "
        "Each is listed under Bugs Skipped above and tracked for a fix in "
        "a later release:"
    ] + bullets


def collect_sections(bundle_file_arg, repo_root, merge_base, rc_branch, tasks_dir="none", active_rc=""):
    """Return ``{section name: [bullet, ...]}`` for every name in
    SECTION_ORDER, filled from the bundle file (a path, or "none") or, when
    no bundle supplied any item, from the RC branch's commit subjects since
    *merge_base* (a sha, or "none"). Commit subjects that only record RC
    bookkeeping ("Set Active RC...", "Sync release-state...") are left out.
    Documentation then gains the WRITER tickets under *tasks_dir* that
    shipped in *active_rc* (see shipped_documentation), whichever source
    filled the rest.
    """
    sections = {s: [] for s in SECTION_ORDER}

    bundle_used = False

    if bundle_file_arg != "none":
        bundle_path = pathlib.Path(bundle_file_arg)
        if bundle_path.exists():
            bundle_text = bundle_path.read_text(encoding="utf-8")
            # The ## Bundled Items section, read the way lib/bundle_items
            # parse_bundled_item_paths reads it (a '###' line ends the list).
            bundled = read_section(bundle_text, "Bundled Items", end_at_subheading=True) or ""
            item_paths = []
            for line in bundled.splitlines():
                m = re.search(r"\(\`([^`]+)\`\)", line)
                if m:
                    item_paths.append(m.group(1).strip())
            if item_paths:
                bundle_used = True
                for item_path in item_paths:
                    p = pathlib.Path(item_path)
                    # A bug report is a bug fix whatever subsystem its Category names.
                    section_name = (
                        "Bug Fixes"
                        if _BUG_FILENAME_RE.match(p.name)
                        else CATEGORY_SECTION.get(read_item_category(item_path), "Other Changes")
                    )
                    title = get_item_title(item_path)
                    sections[section_name].append(_bullet(title))
            else:
                # Bundle file found but has no ## Bundled Items entries —
                # treat as operator-authored; use the bundle title itself.
                bundle_used = True
                category = read_item_category(bundle_file_arg)
                section_name = CATEGORY_SECTION.get(category, "Other Changes")
                title = get_item_title(bundle_file_arg)
                sections[section_name].append(_bullet(title))

    if not bundle_used:
        # Fallback: collect git commit subjects, all go to "Other Changes".
        if merge_base != "none":
            try:
                result = subprocess.run(
                    ["git", "-C", repo_root, "log", "--no-merges", "--format=%s", f"{merge_base}..{rc_branch}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for subj in result.stdout.splitlines():
                    subj = subj.strip()
                    if not subj:
                        continue
                    if subj.startswith("Set Active RC"):
                        continue
                    if subj.startswith("Sync release-state"):
                        continue
                    sections["Other Changes"].append(_bullet(subj))
            except OSError:
                pass

    for bullet in shipped_documentation(tasks_dir, active_rc):
        if bullet not in sections["Documentation"]:
            sections["Documentation"].append(bullet)

    return sections


def render_notes(project_name, active_rc, release_date, status, sections, known_issue_block="", bugs_skipped_block=""):
    """Return the release-notes file text: title, date, Status, then every
    section in SECTION_ORDER, each followed by one blank line. A section
    with no bullets reads "None"; Upgrade Notes always reads "None". Known
    Issues follows *status*: the warning and *known_issue_block* for
    NON-FUNCTIONAL and KNOWN-ISSUE, the open rows of *bugs_skipped_block*
    for KNOWN-BUGS (see known_bugs_issues), "None" otherwise."""
    lines_out = []
    lines_out.append(f"# Release Notes: {project_name} {active_rc}")
    lines_out.append("")
    lines_out.append(f"**Release Date:** {release_date}")
    lines_out.append("**Released By:** scripts/cm/release.sh (automated)")
    lines_out.append("")
    lines_out.append("## Status")
    lines_out.append(status)
    lines_out.append("")

    for section in SECTION_ORDER:
        lines_out.append(f"## {section}")
        if section == "Upgrade Notes":
            lines_out.append("None")
        elif section == "Bugs Skipped":
            # Rendered from _BUGS_SKIPPED_NOTES_BLOCK (cm_build_bugs_skipped_ship_block,
            # release_known_issue.sh), passed through as-is -- not recomputed here. The
            # block already carries its own table header/separator and every cell has
            # already passed the internal-ID scrub, so this generator's only job is to
            # place it (or an explicit empty state when there is nothing to list, same
            # convention every other section in this file uses).
            if bugs_skipped_block:
                lines_out.append(bugs_skipped_block)
            else:
                lines_out.append("None")
        elif section == "Known Issues":
            if status == "NON-FUNCTIONAL" and known_issue_block:
                lines_out.append(known_issue_block)  # warning plus the bugs behind an auto-ship
            elif status == "NON-FUNCTIONAL":
                lines_out.append(
                    "This release is shipped non-functional. "
                    "Do not use in production. Fix expected in the next patch."
                )
                lines_out.append("The findings behind this status are tracked on the " "project's internal bug ledger.")
            elif status == "KNOWN-ISSUE":
                lines_out.append(
                    "This release ships with a known issue that did not "
                    "block shipping. It is tracked for a fix in the next "
                    "release:"
                )
                if known_issue_block:
                    lines_out.append(known_issue_block)
                else:
                    lines_out.append(
                        "- An unresolved defect (internal-only, not yet " "disclosed) — No workaround available."
                    )
            elif status == "KNOWN-BUGS" and known_bugs_issues(bugs_skipped_block):
                lines_out.extend(known_bugs_issues(bugs_skipped_block))
            else:
                lines_out.append("None")
        else:
            items = sections[section]
            if items:
                lines_out.extend(items)
            else:
                lines_out.append("None")
        lines_out.append("")

    return "\n".join(lines_out)


def generate_release_notes(
    bundle_file_arg,
    repo_root,
    active_rc,
    status,
    release_date,
    project_name,
    merge_base,
    rc_branch,
    known_issue_block="",
    bugs_skipped_block="",
    tasks_dir="none",
):
    """Write REPO_ROOT/release-notes/ACTIVE_RC.md (creating release-notes/
    first) and return its path. Arguments are the CLI's positional
    arguments, in the same order (see the module docstring)."""
    out_dir = pathlib.Path(repo_root) / "release-notes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{active_rc}.md"

    sections = collect_sections(
        bundle_file_arg, repo_root, merge_base, rc_branch, tasks_dir=tasks_dir, active_rc=active_rc
    )
    out_file.write_text(
        render_notes(project_name, active_rc, release_date, status, sections, known_issue_block, bugs_skipped_block),
        encoding="utf-8",
    )
    return out_file


_REQUIRED_ARGS = 8
_USAGE = (
    "usage: python3 -m pgai_agent_kanban.cm.release_notes_generator "
    "BUNDLE_FILE REPO_ROOT ACTIVE_RC STATUS RELEASE_DATE PROJECT_NAME "
    "MERGE_BASE RC_BRANCH [KNOWN_ISSUE_BLOCK [BUGS_SKIPPED_BLOCK [TASKS_DIR]]]"
)


def main(argv=None):
    """CLI entry point; see the module docstring for the argument contract.
    Returns the process exit code."""
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(args) < _REQUIRED_ARGS:
        print(
            f"{_USAGE}\nrelease_notes_generator: expected at least "
            f"{_REQUIRED_ARGS} arguments, got {len(args)}. Run with --help "
            "for what each argument holds.",
            file=sys.stderr,
        )
        return 2
    known_issue_block = args[8] if len(args) > 8 else ""
    bugs_skipped_block = args[9] if len(args) > 9 else ""
    tasks_dir = args[10] if len(args) > 10 else "none"
    active_rc = args[2]
    generate_release_notes(*args[:8], known_issue_block, bugs_skipped_block, tasks_dir)
    print(f"  Release notes written to: release-notes/{active_rc}.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
