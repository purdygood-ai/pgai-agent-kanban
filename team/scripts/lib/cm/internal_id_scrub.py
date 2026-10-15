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

Deliberately excluded: team/pgai_agent_kanban/cm/changelog_writer.py
carries its own, independent implementation of the same idea. Folding it
into this shared definition is a larger change than the scrub's own
callers need.
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
_INTERNAL_ID_RE = re.compile(
    r"\b(?:BUG|PRIORITY)-\d+\b"
    r"|\b(?:CODER|WRITER|TESTER|CM|PM|PO)-"
    r"(?:\d{8}-\d+(?:-[A-Za-z0-9]+)*|\d{1,4}(?!-\d))\b",
    re.IGNORECASE,
)

# A kanban-internal requirements-document path ("requirements/vX.Y.Z-slug.md"),
# with or without enclosing backticks. The document does not ship with the
# tree, so a rendered cell citing it points the reader nowhere; this mirrors
# the lint's unshipped-requirements-path pattern and is replaced by a noun
# phrase that keeps the sentence readable.
_REQUIREMENTS_PATH_RE = re.compile(
    r"`requirements/v\d+\.\d+\.\d+[\w.-]*`"
    r"|(?<![\w.])requirements/v\d+\.\d+\.\d+[\w.-]*",
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
    document path with _REQUIREMENTS_PATH_REPLACEMENT_TEXT), then collapse
    the whitespace the substitution can still leave behind. Defense-in-depth: callers that
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
    # Retained as defense-in-depth, not as the primary emptied-parenthetical
    # handler it used to be: _ID_REPLACEMENT_TEXT is non-empty, so a scrubbed
    # identifier no longer empties its enclosing parenthetical by itself --
    # e.g. "(BUG-NNNN)" now renders "(another item)", which is already
    # readable. This still guards a bare "()" reaching rendered prose from
    # any other empty-content path a caller sends through this same cleanup
    # pipeline.
    cleaned = re.sub(r"\(\s*\)", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    return cleaned.strip()
