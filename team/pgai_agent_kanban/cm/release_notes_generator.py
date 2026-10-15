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
   own right: its Category and H1 title make one bullet.
3. With no bundle file, the RC branch's commit subjects since the merge
   base, all under Other Changes.

``## Bugs Skipped`` and ``## Known Issues`` are rendered from blocks the
ship-policy stage already built and scrubbed; this module places them as
given and never recomputes them.

Every rendered substance passes through the shared internal-ID scrub
(``_strip_internal_ids`` in team/scripts/lib/cm/internal_id_scrub.py), so no
internal bug, priority, task-ID or requirements-document path reaches the
notes. ``_load_internal_id_scrub`` says how the scrub module is found.

Usage (CLI, consumed by release_notes.sh's cm_release_generate_notes)::

    python3 -m pgai_agent_kanban.cm.release_notes_generator \\
        BUNDLE_FILE REPO_ROOT ACTIVE_RC STATUS RELEASE_DATE PROJECT_NAME \\
        MERGE_BASE RC_BRANCH [KNOWN_ISSUE_BLOCK [BUGS_SKIPPED_BLOCK]]

    BUNDLE_FILE        requirements bundle path, or "none"
    REPO_ROOT          repository root; the notes go to
                       REPO_ROOT/release-notes/ACTIVE_RC.md
    ACTIVE_RC          release version string, e.g. v1.2.3
    STATUS             FUNCTIONAL, KNOWN-ISSUE or NON-FUNCTIONAL
    RELEASE_DATE       date printed under the title, e.g. 2026-01-31
    PROJECT_NAME       project name printed in the title
    MERGE_BASE         commit the RC branch diverged from, or "none"
    RC_BRANCH          RC branch whose commit subjects feed the fallback
    KNOWN_ISSUE_BLOCK  Known Issues body for a KNOWN-ISSUE or NON-FUNCTIONAL
                       ship (optional; empty when absent)
    BUGS_SKIPPED_BLOCK rendered Bugs Skipped table (optional; empty renders
                       "None")

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

__all__ = [
    "CATEGORY_SECTION",
    "SECTION_ORDER",
    "read_item_category",
    "get_bug_substance",
    "get_priority_substance",
    "get_item_title",
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


_strip_internal_ids = _load_internal_id_scrub()._strip_internal_ids

# Category -> section mapping.
CATEGORY_SECTION = {
    "breaking":    "Breaking Changes",
    "feature":     "Features",
    "bugfix":      "Bug Fixes",
    "deprecation": "Deprecations",
    "removal":     "Deprecations",
    "docs":        "Documentation",
    "misc":        "Other Changes",
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
    """Read ## Category field from an item file. Default: 'misc'."""
    try:
        text = pathlib.Path(item_path).read_text(encoding="utf-8")
    except OSError:
        return "misc"
    lines = text.splitlines()
    in_category = False
    for line in lines:
        if re.match(r"^\s*##\s+Category\s*$", line, re.IGNORECASE):
            in_category = True
            continue
        if in_category:
            stripped = line.strip()
            if stripped.startswith("#"):
                break  # next section — no value found
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
    '' when the section is absent or empty. Stops at the next '##' heading
    or end of file. Same section-extraction convention
    team/pgai_agent_kanban/cm/changelog_writer.py uses for bug files; kept
    as this module's own copy so the release-notes output does not move
    when that module's reader changes."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _first_sentence(text):
    """Collapse whitespace (including newlines) to single spaces and return
    the first sentence, or the whole flattened string when no sentence-
    ending punctuation is found."""
    flat = " ".join(text.split())
    if not flat:
        return ""
    match = re.search(r"[.!?](?:\s|$)", flat)
    return flat[: match.start() + 1].strip() if match else flat


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
_NON_TERMINAL_ABBREVIATIONS = frozenset(
    {"e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.", "incl."}
)
_TRAILING_WORD_RE = re.compile(r"[A-Za-z.]+$")
_SECTION_HEADING_RE = re.compile(r"^##\s+")


def _parse_section_outside_fences(text, heading):
    """Return the body of a '## <heading>' section in *text*, or '' when it
    is absent. Like _parse_section, but a '##' line inside a fenced code
    block is part of the body rather than the start of the next section,
    so a quoted Markdown excerpt (a pasted '## Bug Fixes' block, say) does
    not cut the section short."""
    pattern = re.compile(
        r"^##\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE | re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return ""
    body = []
    fence = None
    for line in text[match.end():].splitlines():
        if fence:
            if line.strip().startswith(fence):
                fence = None
        else:
            opener = _FENCE_OPEN_RE.match(line)
            if opener:
                fence = opener.group(1)
            elif _SECTION_HEADING_RE.match(line):
                break
        body.append(line)
    return "\n".join(body).strip()


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


def collect_sections(bundle_file_arg, repo_root, merge_base, rc_branch):
    """Return ``{section name: [bullet, ...]}`` for every name in
    SECTION_ORDER, filled from the bundle file (a path, or "none") or, when
    no bundle supplied any item, from the RC branch's commit subjects since
    *merge_base* (a sha, or "none"). Commit subjects that only record RC
    bookkeeping ("Set Active RC...", "Sync release-state...") are left out.
    """
    sections = {s: [] for s in SECTION_ORDER}

    bundle_used = False

    if bundle_file_arg != "none":
        bundle_path = pathlib.Path(bundle_file_arg)
        if bundle_path.exists():
            bundle_text = bundle_path.read_text(encoding="utf-8")
            # Find ## Bundled Items section
            b_lines = bundle_text.splitlines()
            in_bundled = False
            item_paths = []
            for line in b_lines:
                if re.match(r"^\s*##\s+Bundled Items\s*$", line, re.IGNORECASE):
                    in_bundled = True
                    continue
                if in_bundled:
                    if re.match(r"^\s*##", line):
                        break
                    m = re.search(r"\(\`([^`]+)\`\)", line)
                    if m:
                        item_paths.append(m.group(1).strip())
            if item_paths:
                bundle_used = True
                for item_path in item_paths:
                    p = pathlib.Path(item_path)
                    # A bug report is a bug fix whatever subsystem its Category names.
                    section_name = ("Bug Fixes" if _BUG_FILENAME_RE.match(p.name) else
                                    CATEGORY_SECTION.get(read_item_category(item_path), "Other Changes"))
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
                    ["git", "-C", repo_root, "log", "--no-merges",
                     "--format=%s", f"{merge_base}..{rc_branch}"],
                    capture_output=True, text=True, check=False
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

    return sections


def render_notes(project_name, active_rc, release_date, status, sections,
                 known_issue_block="", bugs_skipped_block=""):
    """Return the release-notes file text: title, date, Status, then every
    section in SECTION_ORDER, each followed by one blank line. A section
    with no bullets reads "None"; Upgrade Notes always reads "None"."""
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
                lines_out.append("This release is shipped non-functional. "
                                 "Do not use in production. Fix expected in the next patch.")
                lines_out.append("The findings behind this status are tracked on the "
                                 "project's internal bug ledger.")
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
                        "- An unresolved defect (internal-only, not yet "
                        "disclosed) — No workaround available."
                    )
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


def generate_release_notes(bundle_file_arg, repo_root, active_rc, status,
                           release_date, project_name, merge_base, rc_branch,
                           known_issue_block="", bugs_skipped_block=""):
    """Write REPO_ROOT/release-notes/ACTIVE_RC.md (creating release-notes/
    first) and return its path. Arguments are the CLI's positional
    arguments, in the same order (see the module docstring)."""
    out_dir = pathlib.Path(repo_root) / "release-notes"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{active_rc}.md"

    sections = collect_sections(bundle_file_arg, repo_root, merge_base, rc_branch)
    out_file.write_text(
        render_notes(project_name, active_rc, release_date, status, sections,
                     known_issue_block, bugs_skipped_block),
        encoding="utf-8",
    )
    return out_file


_REQUIRED_ARGS = 8
_USAGE = (
    "usage: python3 -m pgai_agent_kanban.cm.release_notes_generator "
    "BUNDLE_FILE REPO_ROOT ACTIVE_RC STATUS RELEASE_DATE PROJECT_NAME "
    "MERGE_BASE RC_BRANCH [KNOWN_ISSUE_BLOCK [BUGS_SKIPPED_BLOCK]]"
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
    active_rc = args[2]
    generate_release_notes(*args[:8], known_issue_block, bugs_skipped_block)
    print(f"  Release notes written to: release-notes/{active_rc}.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
