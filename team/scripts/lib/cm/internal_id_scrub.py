"""internal_id_scrub.py -- the single definition of the internal
ledger/task-ID scrub every CM release-notes renderer shares.

WHY THIS EXISTS
---------------
Two modules put free text into release notes, and both scrub it with
this file's _strip_internal_ids:

- team/pgai_agent_kanban/cm/release_notes_generator.py, the release-notes
  generator team/scripts/lib/cm/release_notes.sh runs at Step 11a. It
  loads this file and re-exports _strip_internal_ids.
- team/pgai_agent_kanban/cm/release_notes_blocks.py
  (pgai_agent_kanban.cm.release_notes_blocks), whose ``known-issues`` and
  ``bugs-skipped`` commands render the "## Known Issues" and
  "## Bugs Skipped" blocks for build_known_issue_notes_block and
  build_bugs_skipped_notes_block in
  team/scripts/lib/cm/release_known_issue.sh. It imports
  _strip_internal_ids from the generator, so it uses this same file.

Keeping _INTERNAL_ID_RE and _strip_internal_ids here means a fix to the
pattern or the cleanup logic has exactly one place to land.

HOW THIS FILE IS FOUND
----------------------
This module lives beside the CM bash stages in scripts/lib/cm/, not inside
the pgai_agent_kanban package, so nothing imports it by package name.
release_notes_generator.py's _load_internal_id_scrub loads it by path from
the directory the PGAI_CM_INTERNAL_ID_SCRUB_DIR environment variable names
or, when that is unset, from its own tree's scripts/lib/cm/. The bash
callers set that variable to their own directory, located with
`dirname "${BASH_SOURCE[0]}"` -- cm_release_generate_notes in
release_notes.sh, and the two block builders in release_known_issue.sh --
so each tree loads the copy shipped beside it, in the dev-tree layout
(team/scripts/lib/cm/) and the flattened installed layout
(scripts/lib/cm/) alike.

team/pgai_agent_kanban/cm/changelog_writer.py keeps its own BUG-NNNN
removal (the CHANGELOG deletes those ids rather than substituting, and its
PRIORITY citations render as written), and runs _strip_provenance_refs from
this file for commit ids, RC branch names and task ids, loaded through the
release-notes generator's loader.
"""

import re

# Internal ledger/task identifiers that must never survive into rendered
# release notes, even as an incidental mention inside a Symptom, Fix, or
# free-text rationale paragraph (e.g. "...see BUG-0011 for the earlier
# attempt"). This mirrors the token set
# team/scripts/lint_hygiene.py's provenance class enforces against
# release-notes/ prose, so the generators that use this scrub produce
# lint-clean output by construction rather than relying on the lint to
# catch a leak after the fact.
#
# The task-ID alternative's trailing `(?:-[A-Za-z0-9]+)*` consumes the
# kebab-case slug that follows a real task ID's numeric head -- a task ID
# has the shape PREFIX-YYYYMMDD-NNN-some-kebab-slug, and without this
# group the closing `\b` is satisfied by the hyphen that starts the slug,
# so the match stops at the numeric head and strands the slug in the
# rendered sentence. The BUG-/PRIORITY-NNNN alternative is deliberately
# left unchanged -- those tokens carry no trailing slug in prose, and
# widening that alternative would start scrubbing the sanctioned, exempted
# bug/priority citations release notes exist to name.
#
# The task-ID alternative also covers the abbreviated <ROLE>-NNN form agents
# write in prose ("raised by ROLE-NNN's status Summary"), with the same
# 1-4 digit bound and (?!-\d) guard the lint's task-ID pattern uses, so the
# short branch never claims the first segment of a longer identifier.
_LEDGER_ID_PATTERN = r"\b(?:BUG|PRIORITY)-\d+\b"
_TASK_ID_PATTERN = r"\b(?:CODER|WRITER|TESTER|CM|PM|PO)-" r"(?:\d{8}-\d+(?:-[A-Za-z0-9]+)*|\d{1,4}(?!-\d))\b"
_INTERNAL_ID_RE = re.compile(f"{_LEDGER_ID_PATTERN}|{_TASK_ID_PATTERN}", re.IGNORECASE)
_TASK_ID_RE = re.compile(_TASK_ID_PATTERN, re.IGNORECASE)

# A git commit id: 7 to 40 lowercase hexadecimal characters, optionally a
# ".." or "..." range of two, bare, backticked, or after the word "commit"
# (which is removed with it). A bare id must mix digits and letters, so an
# all-letter word ("defaced") and an all-digit number (a date, a count)
# both survive; after "commit" any id holding a digit is taken. The
# surrounding guards keep the match off hex runs inside paths, versions,
# UUIDs, URLs and longer digests.
_HEX_RUN = r"[0-9a-f]{7,40}(?:\.{2,3}[0-9a-f]{7,40})?"
_HAS_DIGIT = r"(?=[0-9a-f]*\d)"
_HAS_LETTER = r"(?=[0-9a-f]*[a-f])"
_HEX_LEFT = r"(?<![\w./:#@-])"
_HEX_RIGHT = r"(?![\w/@-]|\.\w)"
_COMMIT_ID_RE = re.compile(
    rf"\b(?i:commit)\s+(?:`{_HAS_DIGIT}{_HEX_RUN}`|{_HEX_LEFT}{_HAS_DIGIT}{_HEX_RUN}{_HEX_RIGHT})"
    rf"|`{_HAS_DIGIT}{_HAS_LETTER}{_HEX_RUN}`"
    rf"|{_HEX_LEFT}{_HAS_DIGIT}{_HAS_LETTER}{_HEX_RUN}{_HEX_RIGHT}"
)

# A release-candidate branch name: rc/vX.Y.Z with or without a branch prefix
# ("ai_rc/v1.2.3", "rc/v1.2.3"), bare or backticked, plus any "-suffix".
# A version on its own ("v1.2.3") is not a branch name and survives.
_RC_BRANCH_CORE = r"(?:[A-Za-z0-9][\w-]*?[_-])?rc/v\d+\.\d+\.\d+(?:[-_][\w.-]*\w)?"
_RC_BRANCH_RE = re.compile(rf"`{_RC_BRANCH_CORE}`|(?<![\w./-]){_RC_BRANCH_CORE}(?![\w/-]|\.\d)")
# A Markdown code span: a run of backticks closed by a run of the same
# length. An unmatched backtick opens nothing and stays literal text. The
# release-notes generator's first-sentence split reads this same pattern.
_CODE_SPAN_RE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")

# What replaces a branch name. A branch name in prose is nearly always the
# object of "on"/"at" ("fails on ai_rc/vX.Y.Z"), so a noun phrase keeps the
# sentence whole where deletion would strand the preposition.
_RC_BRANCH_REPLACEMENT_TEXT = "the release candidate"

# A kanban-internal requirements-document path ("requirements/vX.Y.Z-slug.md"),
# with or without enclosing backticks. The document does not ship with the
# tree, so a rendered cell citing it points the reader nowhere; this mirrors
# the lint's unshipped-requirements-path pattern and is replaced by a noun
# phrase that keeps the sentence readable.
_REQUIREMENTS_PATH_RE = re.compile(
    r"`requirements/v\d+\.\d+\.\d+[\w.-]*`" r"|(?<![\w.])requirements/v\d+\.\d+\.\d+[\w.-]*",
    re.IGNORECASE,
)
_REQUIREMENTS_PATH_REPLACEMENT_TEXT = "the release's requirements document"

# What replaces a scrubbed identifier, chosen over outright deletion.
#
# Deleting the identifier and patching the surrounding whitespace/punctuation
# kept the token out of rendered prose but left the sentence around it
# grammatically broken: "Deferred because CODER-YYYYMMDD-NNN-some-slug
# already covers the area." collapsed to "Deferred because already covers
# the area." -- clean of the identifier, but a published sentence that does
# not parse. An internal identifier in free-text prose always functions as a noun phrase
# naming a specific ticket -- the object of "see", the subject of "already
# covers", the value of a "Source Task:" field -- so substituting a neutral
# noun phrase in its place preserves whatever grammatical role the
# identifier played, instead of deleting the role along with the token.
# "another item" was chosen over a more specific phrase (e.g. "a related
# ticket") because it reads naturally in every sentence position this
# module's own tests exercise -- standalone, mid-clause after a
# conjunction, inside a parenthetical, and immediately before a closing
# period -- and it carries zero information about which ticket, or what
# kind of ticket, was removed: it neither reintroduces the token nor hints
# at it, satisfying the same non-disclosure guarantee the deletion
# approach provided.
_ID_REPLACEMENT_TEXT = "another item"


def _strip_internal_ids(text):
    """Replace any bare internal ledger/task identifier in *text* with the
    neutral stand-in _ID_REPLACEMENT_TEXT (and any internal requirements-
    document path with _REQUIREMENTS_PATH_REPLACEMENT_TEXT, and any RC
    branch name with _RC_BRANCH_REPLACEMENT_TEXT), delete any git commit id,
    then collapse the whitespace and empty parentheses the substitution can
    still leave behind. Defense-in-depth: callers that
    dispatch by filename (a bug or priority item's own H1 line IS its
    ledger ID) already avoid quoting a file's own ID; this scrub catches
    another ticket mentioned inline in free-text prose -- a Symptom, a Fix,
    or a hand-typed rationale -- which is exactly where a pasted finding
    sentence can carry a task ID inline.

    Substituting rather than deleting means the sentence around the
    identifier keeps its grammar; see _ID_REPLACEMENT_TEXT's comment for
    why. The identifier itself never survives, whichever way it is
    replaced."""
    cleaned = _REQUIREMENTS_PATH_RE.sub(_REQUIREMENTS_PATH_REPLACEMENT_TEXT, text)
    cleaned = _INTERNAL_ID_RE.sub(_ID_REPLACEMENT_TEXT, cleaned)
    cleaned = _substitute_branches_and_commits(cleaned)
    return _tidy(cleaned)


def _substitute_branches_and_commits(text):
    """*text* with each RC branch name replaced by
    _RC_BRANCH_REPLACEMENT_TEXT and each commit id (with a leading
    "commit") deleted; no whitespace cleanup."""
    cleaned = _RC_BRANCH_RE.sub(_RC_BRANCH_REPLACEMENT_TEXT, text)
    return _COMMIT_ID_RE.sub("", cleaned)


def _tidy(text):
    """Collapse what a substitution or deletion can leave behind, outside
    code spans, then strip. A code span is kept byte for byte: the space in
    `my-app build . --target 2` is part of the command."""
    pieces = []
    last = 0
    for span in _CODE_SPAN_RE.finditer(text):
        pieces.append(_tidy_prose(text[last : span.start()]))
        pieces.append(span.group(0))
        last = span.end()
    pieces.append(_tidy_prose(text[last:]))
    return "".join(pieces).strip()


def _tidy_prose(text):
    """*text* without an empty "()", a separator stranded against a
    parenthesis ("(, x)", "(x, )"), doubled whitespace, or whitespace
    before punctuation."""
    # A deleted commit id is the usual way a parenthetical empties --
    # "(commit abc1234)" or "(abc1234)" -- so the empty pair goes with it.
    cleaned = re.sub(r"\(\s*[,;]?\s*\)", "", text)
    cleaned = re.sub(r"\(\s*[,;]\s*", "(", cleaned)
    cleaned = re.sub(r"\s*[,;]\s*\)", ")", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return re.sub(r"\s+([,.;:)])", r"\1", cleaned)


def _strip_provenance_refs(text):
    """Remove commit ids, replace RC branch names, and replace task ids
    with _ID_REPLACEMENT_TEXT in *text*; BUG-/PRIORITY- citations are left
    as they are. Text holding none of the three is returned unchanged,
    byte for byte, so a caller with its own formatting keeps it; otherwise
    the result is tidied like _strip_internal_ids's."""
    cleaned = _TASK_ID_RE.sub(_ID_REPLACEMENT_TEXT, text)
    cleaned = _substitute_branches_and_commits(cleaned)
    return text if cleaned == text else _tidy(cleaned)
