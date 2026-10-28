"""
bug_filing.py -- shared primitives for autonomous bug filers.

Factors the parts of an autonomous bug filer that do not vary by caller
out of any one filer into a single implementation, per
docs/coding-standards.md directive 1 ("do not duplicate an operation that
already exists").  The filers -- pgai_agent_kanban.lint.bug_filer (the
hygiene-lint aggregate filer), pgai_agent_kanban.deep_verify.bug_filer (the
DEEP-VERIFY tier's cold-build filer) and
pgai_agent_kanban.cm.ship_policy_bug_filer (the CM ship-policy filer) --
import from here:

  - claiming an atomic BUG-NNNN slot (team/pm-agent's own
    claim_next_bug_id / release_bug_id_claim, self-located since pm-agent/
    is a sibling package, "each tree finds itself" -- team/roles/CODER.md);
  - scanning a bugs/ directory for live bugs (iter_live_bugs);
  - the bug record shape (BugRecord, render_bug_record): the
    BUG-TEMPLATE.md header and sections every filer writes, followed by the
    filer's own dedup-key section and any extra sections it carries;
  - the existing-bug lookup (find_existing_bug): a live bug whose recorded
    dedup-key section equals the key a filer is about to record;
  - the file-or-cite sequence (file_or_cite_record): cite a live bug that
    records the same key or reports the same defect, else claim a slot and
    write the record.

A filer supplies only its kind-specific fields: the slug, the sentences in
each section, its dedup-key heading and value, and its extra sections.

Also shared: the one same-defect rule (find_same_defect) every path that
files a NEW bug report by hand or by agent consults -- the intake deposit
path refuses a draft it matches, and bug filers and the discovery re-file
detector import it rather than restating it.  A draft reports the same
defect as a live bug (status open, running or needs-operator) when its
body cites that bug as the defect it reports, or when its slug differs
from that bug's slug only by re-file markers at its start or end (a
re-file word, an ordinal of two or more, or a trailing counter).  A
filer's own dedup key is checked first; this rule is the general one,
consulted after the key finds nothing and before a slot is claimed
(find_same_defect_for_draft).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import os
import re
from pathlib import Path
from typing import Iterator

from pgai_agent_kanban.lib.terminal_states import ACTIVE_INTAKE_STATES
from pgai_agent_kanban.task_status import section_lines, section_value

_BUG_FILENAME_RE = re.compile(r"^(BUG-\d{4,})-.+\.md$", re.IGNORECASE)

# Statuses that keep a bug live for dedup: the active intake states
# (lib.terminal_states) -- not yet picked up (open), bundled into an RC
# (running), or waiting on an operator answer (needs-operator).  Each still
# covers its defect.  Only a closed bug (done, wont-do, duplicate) is no
# longer live and never suppresses a fresh filing for the same defect.
ELIGIBLE_DEDUP_STATUSES = frozenset(ACTIVE_INTAKE_STATES)


def read_status(text: str) -> str:
    """Return the lowercased '## Status' value of a bug file's text, or
    "open" when no Status value is present -- a malformed bug file is
    treated as live (not silently excluded from dedup), never as a signal
    that its defect is resolved. The value is read by
    task_status.section_value (first token, lowercased)."""
    tokens = section_value(text, "Status").split()
    return tokens[0].lower() if tokens else "open"


@dataclasses.dataclass(frozen=True)
class LiveBug:
    """One live bug file: its ID, file name, path, '## Status' and text."""

    bug_id: str
    name: str
    path: str
    status: str
    text: str


def iter_live_bugs(bugs_dir: str) -> Iterator[LiveBug]:
    """Yield a LiveBug for every 'BUG-NNNN-*.md' file directly inside
    *bugs_dir* whose '## Status' is live (ELIGIBLE_DEDUP_STATUSES), in
    filename-sorted order for deterministic dedup scanning.

    A missing *bugs_dir* yields nothing -- a fresh project's first filing
    has no bugs/ directory yet, which is not an error.  An unreadable file
    is skipped.
    """
    if not os.path.isdir(bugs_dir):
        return
    for name in sorted(os.listdir(bugs_dir)):
        match = _BUG_FILENAME_RE.match(name)
        if not match:
            continue
        path = os.path.join(bugs_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        status = read_status(text)
        if status not in ELIGIBLE_DEDUP_STATUSES:
            continue
        yield LiveBug(match.group(1).upper(), name, path, status, text)


def iter_open_or_running_bugs(bugs_dir: str) -> Iterator[tuple]:
    """Yield (bug_id, text) for every live bug in *bugs_dir* -- status
    open, running or needs-operator -- in filename-sorted order
    (iter_live_bugs).  A missing *bugs_dir* yields nothing.
    """
    for bug in iter_live_bugs(bugs_dir):
        yield bug.bug_id, bug.text


# ---------------------------------------------------------------------------
# Same-defect rule
# ---------------------------------------------------------------------------

#: The explicit re-file vocabulary.  One of these words at the start or end
#: of a slug marks the slug as a re-file of the slug without it
#: (``lock-leak-again``, ``redux-lock-leak``).  The same word in the middle
#: of a slug is part of the defect's name.
REFILE_WORDS = frozenset({
    "again", "redux", "recurring",
    "repeat", "repeats", "repeated", "repeating",
    "refile", "refiles", "refiled", "refiling",
})

#: Ordinals of two or more.  One at the start or end of a slug marks a
#: re-file (``second-lock-leak``, ``lock-leak-third``).  ``first`` is not a
#: re-file marker, and neither are ``still``, ``once`` or ``another``: in a
#: slug they name part of the defect (``docs-still-halt-on-errors``).
REFILE_ORDINALS = frozenset({
    "second", "third", "fourth", "fifth",
    "sixth", "seventh", "eighth", "ninth", "tenth",
})

# A numeric ordinal (2nd, 3rd, 11th); a re-file marker at either end of a
# slug when its number is two or more.
_NUMERIC_ORDINAL_RE = re.compile(r"^([0-9]+)(?:st|nd|rd|th)$")

# A bare counter of one to three digits (the "-3" in "lock-leak-3"); a
# re-file marker only as the last word of a slug, and at most one per slug.
# A number anywhere else names the defect (an exit code, a column count).
# An eight-digit date is not a counter; the filename's date field is
# removed before slugs compare.
_TRAILING_COUNTER_RE = re.compile(r"^[0-9]{1,3}$")

# BUG-NNNN[-YYYYMMDD]-<slug>.md, read liberally (any case); group 1 is the
# slug.  The date field is optional and taken only when a slug follows it.
_BUG_SLUG_RE = re.compile(r"^BUG-[0-9]{4,}(?:-[0-9]{8})?-(.+?)\.md$", re.IGNORECASE)

# A phrase that says the bug a draft reports IS an existing bug, followed
# by that bug's ID -- optionally after markdown punctuation (``**Duplicate
# Of:** BUG-NNNN``, a heading, backticks) and up to three plain words
# ("same defect as the open BUG-NNNN").  Matched case-insensitively.
_CITE_LEAD = (
    r"same\s+(?:defect|bug|issue|failure|fault|problem)\s+as"
    r"|(?:a\s+)?duplicate\s+of|dupe\s+of"
    r"|re-?fil(?:e|ed|es|ing)(?:\s+(?:of|from))?"
    r"|(?:recurrence|reappearance|reoccurrence|repeat)\s+of"
    r"|re-?open(?:s|ed|ing)?(?:\s+of)?"
)
_CITE_AFTER_PHRASE_RE = re.compile(
    rf"\b(?:{_CITE_LEAD})[\s*_:`#>()\[\]-]*(?:[A-Za-z][A-Za-z'-]*\s+){{0,3}}"
    r"[`*_(\[]*(BUG-[0-9]{4,})\b",
    re.IGNORECASE,
)
# The ID first, then a phrase saying that bug is still happening:
# "BUG-NNNN still reproduces", "BUG-NNNN is still broken", "BUG-NNNN
# recurs again".  A recurrence verb counts only with "still" or "again":
# alone it may be a hypothetical ("when BUG-NNNN recurs ...").  A status
# remark ("BUG-NNNN is still open") is not a citation.
_RECURRENCE_VERB = r"recur\w*|reappear\w*|resurfac\w*"
_CITE_BEFORE_PHRASE_RE = re.compile(
    r"\b(BUG-[0-9]{4,})\b[`*_)\]]*[\s:,]+(?:(?:is|has|remains)\s+)?"
    rf"(?:still\s+(?:unfixed|reproduc\w*|happen\w*|fail\w*|occur\w*|broken|{_RECURRENCE_VERB})"
    rf"|(?:{_RECURRENCE_VERB})\s+again)\b",
    re.IGNORECASE,
)
# A negation shortly before a citation phrase, in the same sentence, turns
# it into a denial ("this is not the same defect as BUG-NNNN").
_NEGATION_RE = re.compile(r"\b(?:not|never|unlike|no\s+longer)\b|n't\b", re.IGNORECASE)
_NEGATION_WINDOW = 40
_SENTENCE_BREAK_RE = re.compile(r"[.!?;\n]")
# Text never read for citations: HTML comments (template guidance) and
# fenced code blocks (quoted logs and command output).
_UNREAD_BLOCK_RE = re.compile(r"<!--.*?-->|^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.DOTALL | re.MULTILINE)


def _canonical_bug_id(raw: str) -> str:
    """Return a bug ID read in any letter case as ``BUG-NNNN``, zero-padded to four digits."""
    return f"BUG-{int(raw[len('BUG-'):]):04d}"


def _negated(text: str, start: int) -> bool:
    """True when a negation sits shortly before *start* in the same sentence."""
    window = text[max(0, start - _NEGATION_WINDOW):start]
    breaks = list(_SENTENCE_BREAK_RE.finditer(window))
    if breaks:
        window = window[breaks[-1].end():]
    return bool(_NEGATION_RE.search(window))


def cited_defect_ids(text: str) -> list:
    """Return the bug IDs *text* cites as the defect it reports, in order.

    A citation is an explicit statement that the defect IS another bug:
    a lead phrase followed by the ID ("same defect as BUG-NNNN", "duplicate
    of BUG-NNNN", "**Re-file of:** BUG-NNNN", "recurrence of BUG-NNNN",
    "reopens BUG-NNNN"), or the ID followed by a phrase saying it is still
    happening ("BUG-NNNN still reproduces", "BUG-NNNN is still broken",
    "BUG-NNNN recurs again").  An ID mentioned any other way -- "fails as
    BUG-NNNN before it", "see also BUG-NNNN", "fixed for BUG-NNNN", a status
    remark ("BUG-NNNN is still open"), a hypothetical ("when BUG-NNNN
    recurs") -- is not a citation, and a citation phrase negated earlier in
    its sentence ("not the same defect as BUG-NNNN") is not one either.  HTML comments and
    fenced code blocks are not read.  IDs are returned as ``BUG-NNNN``,
    each once.
    """
    body = _UNREAD_BLOCK_RE.sub(lambda m: " " * len(m.group(0)), text)
    found: list = []
    hits = []
    for regex in (_CITE_AFTER_PHRASE_RE, _CITE_BEFORE_PHRASE_RE):
        for match in regex.finditer(body):
            if _negated(body, match.start()):
                continue
            hits.append((match.start(1), _canonical_bug_id(match.group(1).upper())))
    for _pos, bug_id in sorted(hits):
        if bug_id not in found:
            found.append(bug_id)
    return found


def bug_slug(filename: str) -> "str | None":
    """Return the lower-cased slug of a ``BUG-NNNN[-YYYYMMDD]-<slug>.md`` name,
    or None when *filename* does not have that shape."""
    match = _BUG_SLUG_RE.match(os.path.basename(filename))
    return match.group(1).lower() if match else None


def _slug_words(slug: str) -> list:
    """*slug*'s lower-cased words, split on any run of non-alphanumerics."""
    return [w for w in re.split(r"[^a-z0-9]+", slug.lower()) if w]


def _is_refile_marker(word: str) -> bool:
    """True for a re-file word, an ordinal word of two or more, or a
    numeric ordinal of two or more -- the markers read at either end of a
    slug.  A bare counter is read separately, at the end only."""
    if word in REFILE_WORDS or word in REFILE_ORDINALS:
        return True
    match = _NUMERIC_ORDINAL_RE.match(word)
    return bool(match) and int(match.group(1)) >= 2


def slug_core(slug: str) -> tuple:
    """Return *slug*'s words with its re-file markers removed.

    Markers are read only at the ends of the slug: leading re-file words
    and ordinals (``second-lock-leak``), then trailing re-file words,
    ordinals and at most one bare counter (``lock-leak-again``,
    ``lock-leak-3``).  Every word between them -- a number, ``still``,
    ``first``, even ``again`` -- is part of the defect's name.  Words are
    split on any run of non-alphanumeric characters, so ``lock_leak--again``
    and ``lock-leak-again`` read alike.
    """
    words = _slug_words(slug)
    start, end = 0, len(words)
    while start < end and _is_refile_marker(words[start]):
        start += 1
    counter_taken = False
    while end > start:
        word = words[end - 1]
        if _is_refile_marker(word):
            end -= 1
        elif not counter_taken and _TRAILING_COUNTER_RE.match(word):
            counter_taken = True
            end -= 1
        else:
            break
    return tuple(words[start:end])


def slugs_differ_only_by_count_words(slug_a: str, slug_b: str) -> bool:
    """True when two slugs differ, and only by re-file markers at their ends.

    ``lock-leak`` and ``lock-leak-again``, ``second-lock-leak`` and
    ``lock-leak-3`` match (slug_core says which words are markers).
    ``intake-exits-1-on-bad-name`` and ``intake-exits-2-on-bad-name`` do
    not: a number mid-slug names the defect.  Identical slugs do not match
    either: they do not differ, and two drafts filed under one name are
    numbered apart by the claim, not refused.  Neither do slugs that are
    nothing but markers.
    """
    if _slug_words(slug_a) == _slug_words(slug_b):
        return False
    core_a, core_b = slug_core(slug_a), slug_core(slug_b)
    return bool(core_a) and core_a == core_b


@dataclasses.dataclass(frozen=True)
class SameDefect:
    """A live bug a new draft reports the same defect as.

    Fields:
        bug:  The live bug (LiveBug) the draft matches.
        rule: ``"cites"`` -- the draft's body cites the bug as its defect --
              or ``"slug"`` -- the slugs differ only by re-file markers.
        why:  One plain sentence naming the match, for a refusal message.
    """

    bug: LiveBug
    rule: str
    why: str


def find_same_defect(bugs_dir: str, filename: str, text: str) -> "SameDefect | None":
    """Return the live bug a new draft reports the same defect as, or None.

    The one same-defect rule.  A draft named *filename* (a
    ``BUG-NNNN[-YYYYMMDD]-<slug>.md`` basename) with body *text* matches a
    live bug in *bugs_dir* -- status open, running or needs-operator
    (iter_live_bugs) -- when:

      - its body cites that bug as the defect it reports
        (cited_defect_ids); a cited bug that is closed (done, wont-do,
        duplicate) or absent is not a match, or
      - its slug differs from that bug's slug only by re-file markers at
        its start or end (slugs_differ_only_by_count_words; REFILE_WORDS
        and REFILE_ORDINALS list the words, and a trailing counter such as
        ``-3`` counts too).

    A citation wins over a slug match, and the first cited live bug wins
    among citations; slug matches are taken in filename order.  Reads
    *bugs_dir* only; writes nothing.
    """
    live = list(iter_live_bugs(bugs_dir))
    by_id = {}
    for bug in live:
        by_id.setdefault(_canonical_bug_id(bug.bug_id), bug)
    for cited in cited_defect_ids(text):
        bug = by_id.get(cited)
        if bug is not None:
            return _cites_match(bug)
    slug = bug_slug(filename)
    if slug is None:
        return None
    for bug in live:
        match = _slug_match(slug, bug)
        if match is not None:
            return match
    return None


def _cites_match(bug: LiveBug) -> SameDefect:
    """The ``cites`` SameDefect naming *bug*."""
    return SameDefect(bug, "cites", f"its body cites {bug.bug_id} as the defect it reports")


def _slug_match(slug: str, bug: LiveBug) -> "SameDefect | None":
    """The ``slug`` SameDefect naming *bug* when *slug* differs from its
    slug only by re-file markers, else None."""
    other = bug_slug(bug.name)
    if other is None or not slugs_differ_only_by_count_words(slug, other):
        return None
    return SameDefect(
        bug, "slug", f"its slug '{slug}' differs from '{other}' only by a re-file word, ordinal or trailing counter",
    )


def same_defect_matches(bugs: "list[LiveBug]") -> "list[tuple[LiveBug, SameDefect]]":
    """Return every ``(bug, match)`` pair among *bugs* where *bug* reports the
    same defect as ``match.bug`` -- find_same_defect's rule applied to each
    member of a set of live bugs in turn, against the others.

    For each bug, in the order given: one ``cites`` match for each other
    bug its body cites as its defect (every file carrying a cited ID, in
    citation order), then one ``slug`` match for each other bug whose slug
    differs from its own only by re-file markers (in the order given).  A bug never matches itself, and a pair matched by citation is
    not matched again by slug.  The relation is not symmetric: a citation
    runs one way, so a bug cited by another appears only as ``match.bug``.
    Reads nothing from disk; *bugs* is typically ``list(iter_live_bugs(dir))``.
    """
    by_id: dict = {}
    for bug in bugs:
        by_id.setdefault(_canonical_bug_id(bug.bug_id), []).append(bug)
    found: list = []
    for bug in bugs:
        matched = {bug.path}
        for cited in cited_defect_ids(bug.text):
            for other in by_id.get(cited, ()):
                if other.path not in matched:
                    matched.add(other.path)
                    found.append((bug, _cites_match(other)))
        slug = bug_slug(bug.name)
        if slug is None:
            continue
        for other in bugs:
            if other.path in matched:
                continue
            match = _slug_match(slug, other)
            if match is not None:
                found.append((bug, match))
    return found


#: The ID an autonomous filer names its draft with when it consults the
#: same-defect rule before claiming a real BUG-NNNN slot.  Only the slug
#: and body take part in the match; the number is never read.
DRAFT_BUG_ID = "BUG-0000"


def find_same_defect_for_draft(bugs_dir: str, slug: str, text: str) -> "SameDefect | None":
    """Return the live bug an autonomous filer's unclaimed draft reports the
    same defect as, or None.

    find_same_defect for a filer that has not claimed its BUG-NNNN slot
    yet: the draft is named ``<DRAFT_BUG_ID>-<slug>.md`` and *text* is the
    report it would write.  A filer calls this after its own dedup key
    found nothing and before claim_next_bug_id, and cites the returned
    bug's ID instead of filing -- so a match leaves no claim, no ledger
    line and no file behind.  Reads *bugs_dir* only; writes nothing.
    """
    return find_same_defect(bugs_dir, f"{DRAFT_BUG_ID}-{slug}.md", text)


# Loaded bug_scanner modules, keyed by the resolved path of the
# bug_scanner.py file they were executed from. Private to this module:
# nothing is registered in sys.modules, so no other import can shadow or be
# shadowed by these copies.
_BUG_SCANNER_CACHE: dict = {}


def own_pm_agent_dir() -> Path:
    """Return the pm-agent/ directory beside this module's own package root.

    pgai_agent_kanban/lib/bug_filing.py -> pgai_agent_kanban/ -> <root>/,
    and pm-agent/ is a sibling of pgai_agent_kanban/ in both layouts: the
    dev tree (team/pm-agent/) and the live install (<root>/pm-agent/).
    Each tree finds itself; no repository checkout is ever consulted.
    """
    return Path(__file__).resolve().parents[2] / "pm-agent"


def load_bug_scanner(pm_agent_dir=None):
    """Load ``<pm_agent_dir>/lib/bug_scanner.py`` by file path and return
    the module.

    *pm_agent_dir* defaults to own_pm_agent_dir(). The file is executed
    through importlib's file-location loader rather than imported by the
    dotted name ``lib.bug_scanner``: the top-level name ``lib`` is
    ambiguous (scripts/lib/ is a namespace directory of the same name),
    so a name-based import resolves to whichever ``lib`` happens to be
    first on sys.path or already cached in sys.modules. Loading by path
    depends on neither, and leaves sys.path and sys.modules untouched.

    The loaded module is cached per resolved file path, so repeated calls
    return the same module object.

    Raises ImportError, naming the path it looked at, when the file is
    missing or cannot be loaded.
    """
    base = Path(pm_agent_dir) if pm_agent_dir is not None else own_pm_agent_dir()
    path = (base / "lib" / "bug_scanner.py").resolve()
    cached = _BUG_SCANNER_CACHE.get(path)
    if cached is not None:
        return cached
    if not path.is_file():
        raise ImportError(
            f"bug_scanner not found at {path}: expected pm-agent/lib/bug_scanner.py "
            f"beside the pgai_agent_kanban package. Check that "
            f"{path.parent.parent} is a complete pm-agent directory "
            f"(reinstall with install.sh if it is missing).",
            path=str(path),
        )
    spec = importlib.util.spec_from_file_location("pm_agent_bug_scanner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}", path=str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _BUG_SCANNER_CACHE[path] = module
    return module


def import_bug_scanner():
    """Return the atomic BUG-NNNN claim helpers from this tree's own
    pm-agent/lib/bug_scanner.py. Never re-implement the claim logic in a
    filer -- see module docstring.

    The helpers come from load_bug_scanner(), which self-locates pm-agent/
    beside this package (dev tree or live install) and loads bug_scanner.py
    by file path. The result is therefore the same whatever top-level
    ``lib`` package is on sys.path or cached in sys.modules, and calling
    this changes neither.

    Returns (claim_next_bug_id, release_bug_id_claim).
    """
    scanner = load_bug_scanner()
    return scanner.claim_next_bug_id, scanner.release_bug_id_claim


# ---------------------------------------------------------------------------
# Bug records: the shared shape, the existing-bug lookup, file-or-cite
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BugRecord:
    """One autonomously filed bug report, everything but its BUG-NNNN ID.

    render_bug_record turns it into the file text; file_or_cite_record
    files it or cites the live bug that already covers it.  Section bodies
    are given without a trailing newline.

    Fields:
        slug:           The file name's slug, ``BUG-NNNN-<slug>.md``.
        filed_by:       The whole ``**Filed By:**`` value: the filer's label
                        token, a separator and one plain sentence.
        filed_at:       The ``**Date:**`` value, YYYY-MM-DD.
        severity:       The ``**Severity:**`` value (low, medium, high,
                        critical).
        category:       The ``## Category`` value.
        symptom, expected, actual, reproduction, files_involved,
        hypothesis:     The bodies of the BUG-TEMPLATE.md sections of the
                        same names (``## Hypothesis (optional)`` for the
                        last).
        key_heading:    The heading of the filer's dedup-key section,
                        written first after the closing rule.
        key:            The dedup key the section records.  A string is
                        written as is and matched against the section's
                        value (its first non-blank line).  A tuple of
                        strings is written one per line, in order, and
                        matched as a set against the section's non-blank
                        lines; an empty tuple writes an empty section and
                        matches nothing.
        extra_sections: ``(heading, body)`` pairs written after the key
                        section, in order.
    """

    slug: str
    filed_by: str
    filed_at: str
    severity: str
    category: str
    symptom: str
    expected: str
    actual: str
    reproduction: str
    files_involved: str
    hypothesis: str
    key_heading: str
    key: "str | tuple"
    extra_sections: tuple = ()


def render_bug_record(bug_id: str, record: BugRecord) -> str:
    """Return the text of *record* filed as *bug_id*.

    The BUG-TEMPLATE.md shape: the title and ``**Bug ID:**`` lines naming
    ``<bug_id>-<slug>``, the Filed By, Date and Severity lines, ``## Status``
    open (an autonomous filing is always new), ``## Category``, a rule, the
    six template sections, a second rule, then the dedup-key section and
    the extra sections.  Sections are separated by one blank line and the
    text ends with a single newline.
    """
    stem = f"{bug_id}-{record.slug}"
    key_body = record.key if isinstance(record.key, str) else "\n".join(record.key)
    template_sections = (
        ("Symptom", record.symptom),
        ("Expected", record.expected),
        ("Actual", record.actual),
        ("Reproduction", record.reproduction),
        ("Files Involved", record.files_involved),
        ("Hypothesis (optional)", record.hypothesis),
    )
    trailing_sections = ((record.key_heading, key_body),) + tuple(record.extra_sections)
    return (
        f"# {stem}\n\n"
        f"**Bug ID:** {stem}\n"
        f"**Filed By:** {record.filed_by}\n"
        f"**Date:** {record.filed_at}\n"
        f"**Severity:** {record.severity}\n\n"
        "## Status\nopen\n\n"
        f"## Category\n{record.category}\n\n"
        "---\n\n"
        + "".join(f"## {heading}\n{body}\n\n" for heading, body in template_sections)
        + "---\n\n"
        + "\n\n".join(f"## {heading}\n{body}" for heading, body in trailing_sections)
        + "\n"
    )


def find_existing_bug(bugs_dir: str, heading: str, key: "str | tuple | frozenset") -> "str | None":
    """Return the BUG-NNNN ID of the first live bug in *bugs_dir* -- status
    open, running or needs-operator, in filename order (iter_live_bugs) --
    whose ``## <heading>`` section records *key*, or None.

    A string *key* matches the section's value (its first non-blank line,
    task_status.section_value).  A tuple or set of strings matches when the
    section's non-blank lines, as a set, equal it; an empty one matches
    nothing.  A missing *bugs_dir* matches nothing.  Reads only.
    """
    if isinstance(key, str):
        def recorded(text: str) -> bool:
            return section_value(text, heading) == key
    else:
        wanted = frozenset(key)
        if not wanted:
            return None

        def recorded(text: str) -> bool:
            return frozenset(section_lines(text, heading)) == wanted
    for bug in iter_live_bugs(bugs_dir):
        if recorded(bug.text):
            return bug.bug_id
    return None


@dataclasses.dataclass(frozen=True)
class FiledRecord:
    """The outcome of one file_or_cite_record call.

    Fields:
        outcome:  ``"filed"`` (a new bug file was written) or
                  ``"duplicate"`` (a live bug already covers the record;
                  nothing was written).
        bug_id:   ``BUG-NNNN``: the filed bug, or the live bug cited.
        bug_path: The filed file's path (``"filed"`` only); None for
                  ``"duplicate"``.
    """

    outcome: str
    bug_id: str
    bug_path: "str | None"


def file_or_cite_record(bugs_dir: str, record: BugRecord, *, claimed_by: str) -> FiledRecord:
    """File *record* into *bugs_dir*, or cite the live bug that covers it.

    A live bug covers the record when it records the same dedup key
    (find_existing_bug on ``record.key_heading`` and ``record.key``) or,
    failing that, when the same-defect rule matches the drafted record to
    it (find_same_defect_for_draft).  A citation claims no slot and writes
    nothing.  Otherwise a BUG-NNNN slot is claimed atomically in
    *claimed_by*'s name (import_bug_scanner), the rendered record is
    written to the claimed path, and the claim is released.  Creates
    *bugs_dir* when it does not exist.  Writes nothing outside *bugs_dir*.
    """
    existing = find_existing_bug(bugs_dir, record.key_heading, record.key)
    if existing is None:
        same = find_same_defect_for_draft(bugs_dir, record.slug, render_bug_record(DRAFT_BUG_ID, record))
        existing = same.bug.bug_id if same is not None else None
    if existing is not None:
        return FiledRecord(outcome="duplicate", bug_id=existing, bug_path=None)

    os.makedirs(bugs_dir, exist_ok=True)
    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()
    bug_id, bug_path, lock_path = claim_next_bug_id(bugs_dir, record.slug, claimed_by=claimed_by)
    try:
        Path(bug_path).write_text(render_bug_record(bug_id, record), encoding="utf-8")
    finally:
        release_bug_id_claim(lock_path)
    return FiledRecord(outcome="filed", bug_id=bug_id, bug_path=str(bug_path))
