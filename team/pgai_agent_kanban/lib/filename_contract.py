"""
filename_contract.py — Single-source requirements filename patterns.

Both intake routing (ops/write.py) and discovery eligibility
(_disc_list_all_eligible_requirements in scripts/lib/discovery.sh) import
from this module.  The contract is:

  INTAKE_REQUIREMENTS_RE
      Accepts any requirements filename that intake routes to requirements/.
      Covers both semver-shaped filenames (v1.20.10-slug.md) and
      label-shaped filenames (v20260712-my-app-fieldtest.md).  The
      pattern is r'^v[0-9].*\\.md$'; the intake routing table
      (INTAKE_ROUTES, below) routes every matching name to requirements/.

  SEMVER_REQUIREMENTS_RE
      Accepts only dotted-semver-shaped filenames: vX.Y.Z-slug.md.
      Used by the discovery eligibility helper to confirm a file's name
      satisfies the semver project filename shape before admitting it
      to the semver processing path.

  is_intake_filename(name)
      Return True when ``name`` matches the intake requirements pattern.

  is_semver_filename(name)
      Return True when ``name`` matches the dotted-semver shape.

  filename_semantics_eligible(name, version_semantics)
      Return (eligible: bool, skip_reason: str | None).
      eligible=True  → the file should enter the eligibility pass for
                        the given version_semantics.
      eligible=False → skip; skip_reason names the reason for the
                        caller's log line.

  skip_log_line(name, reason)
      Format the standard skip log line consumed by discovery callers.
      Shape: "discovery: skipping {name}: {reason}"

  INTAKE_ROUTES
      The intake routing table: (pattern, project sub-directory) pairs,
      matched in order against a bare filename.

  intake_destination(name)
      The one intake filename check.  Returns the project sub-directory a
      bare intake filename routes to, or raises IntakeFilenameRefused when
      the name is not a bare file name (path separator, ``..``, absolute,
      encoded separator, NUL) or matches no routing prefix.  Both the REST
      intake route (``POST /operations/intake``) and the CLI path
      (``intake.sh`` -> ``ops.write.deposit_intake``) import it, so the two
      refuse exactly the same names.

Structural guarantee: the filename pattern literals appear in exactly one
place — this module.  Both consumers import; neither duplicates a local
definition.
"""

from __future__ import annotations

import re

# The path-confinement check has one owner (docs/coding-standards.md,
# directive 13); the intake filename check below reuses it rather than
# carrying a second copy.  api.validation imports nothing beyond the
# standard library, so this adds no web-framework dependency here.
from pgai_agent_kanban.api.validation import has_traversal

# ---------------------------------------------------------------------------
# Intake requirements filename pattern (the looser rule).
#
# Matches: v20260712-my-app-fieldtest.md, v1.20.10-slug.md, v0.0.1-foo.md
# Does NOT match: BUG-*, PRIORITY-*, plain README.md, etc.
#
# This is the authoritative intake routing pattern.  intake.sh routes
# requirements files on this rule; any file that passes intake's front
# door must be shaped like this.
# ---------------------------------------------------------------------------
INTAKE_REQUIREMENTS_RE: re.Pattern[str] = re.compile(r"^v[0-9].*\.md$")

# ---------------------------------------------------------------------------
# Semver requirements filename pattern (the stricter rule).
#
# Matches: v1.20.10-slug.md, v0.0.1-foo-bar.md
# Does NOT match: v20260712-my-app-fieldtest.md (no dots), v1.md (no slug)
#
# Used by discovery's semver path to assert the file has a dotted-version
# name before attempting to parse a semver Target Version from the header.
# The pattern requires the full dotted vX.Y.Z prefix plus a slug, so every
# name it admits carries a version the semver path can parse.
# ---------------------------------------------------------------------------
SEMVER_REQUIREMENTS_RE: re.Pattern[str] = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+-.+\.md$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Intake routing table.
#
# Matched in order against a bare filename (case-sensitive):
#   BUG-*       -> bugs/
#   PRIORITY-*  -> priority/
#   v[0-9]*.md  -> requirements/   (INTAKE_REQUIREMENTS_RE)
# ops/write.py's deposit_intake routes on this table; the operator API's
# intake route checks names against it before writing anything.
# ---------------------------------------------------------------------------
INTAKE_ROUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^BUG-"), "bugs"),
    (re.compile(r"^PRIORITY-"), "priority"),
    (INTAKE_REQUIREMENTS_RE, "requirements"),
)

#: Machine key: the intake filename is a path, not a bare file name.
INTAKE_FILENAME_NOT_BARE = "intake.filename_not_bare"

#: Machine key: the intake filename is bare but matches no routing prefix.
INTAKE_FILENAME_UNROUTABLE = "intake.filename_unroutable"


class IntakeFilenameRefused(ValueError):
    """Raised by ``intake_destination`` for a name intake must not accept.

    Attributes:
        error:   Stable machine key -- ``INTAKE_FILENAME_NOT_BARE`` or
                 ``INTAKE_FILENAME_UNROUTABLE``.
        message: Plain-language reason.  For ``INTAKE_FILENAME_NOT_BARE``
                 the message never repeats the refused value, so a caller
                 can return it without echoing a caller-supplied path.

    Each caller supplies its own runnable remedy: the right next command
    differs between the REST route (a corrected request) and the CLI
    (a renamed staged file).
    """

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


# ---------------------------------------------------------------------------
# Skip reason constants — named strings so callers and tests can use the
# same literals without repeating them.
# ---------------------------------------------------------------------------

#: Emitted when a file passes intake routing but fails the semver filename
#: shape on a semver project.  This must be a named (non-silent) skip so the
#: reason surfaces in the discovery log instead of the file silently
#: disappearing from processing.
SKIP_REASON_SEMVER_SHAPE_REQUIRED = "semver-shape-required"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def is_intake_filename(name: str) -> bool:
    """Return True when *name* matches the intake requirements filename pattern.

    Args:
        name: Bare filename (not a full path).

    Returns:
        True when the filename is acceptable to intake as a requirements file.
    """
    return bool(INTAKE_REQUIREMENTS_RE.match(name))


def is_semver_filename(name: str) -> bool:
    """Return True when *name* has the dotted-semver shape vX.Y.Z-slug.md.

    Args:
        name: Bare filename (not a full path).

    Returns:
        True when the filename satisfies the semver project filename contract.
    """
    return bool(SEMVER_REQUIREMENTS_RE.match(name))


def filename_semantics_eligible(
    name: str,
    version_semantics: str,
) -> tuple[bool, str | None]:
    """Check whether *name* is eligible for the given *version_semantics*.

    The check is applied AFTER common prefix filtering (i.e. the file has
    already passed the common ``BUNDLE_RE``/BUG-/PRIORITY- gate for
    non-requirements entries; for requirements entries this is the first
    gate).

    Rules:
      - ``semver``: file must match SEMVER_REQUIREMENTS_RE.  If not,
        eligible=False and skip_reason=SKIP_REASON_SEMVER_SHAPE_REQUIRED.
      - ``label`` / ``none`` / any other value: any file that passes
        INTAKE_REQUIREMENTS_RE is eligible (no shape restriction).

    Args:
        name:               Bare filename (not a full path).
        version_semantics:  The project's declared version semantics
                            (``"semver"``, ``"label"``, ``"none"``, etc.).

    Returns:
        A ``(eligible, skip_reason)`` tuple.  When ``eligible`` is True,
        ``skip_reason`` is None.  When ``eligible`` is False, ``skip_reason``
        is a non-empty string suitable for embedding in a skip log line.
    """
    semantics = version_semantics.strip().lower()
    if semantics == "semver":
        if not is_semver_filename(name):
            return False, SKIP_REASON_SEMVER_SHAPE_REQUIRED
    # label / none / anything else: accept any intake-shaped filename
    return True, None


def skip_log_line(name: str, reason: str) -> str:
    """Format the canonical discovery skip log line.

    Shape matches the discovery logging contract:
        ``discovery: skipping {name}: {reason}``

    Args:
        name:   Bare filename or path-relative name of the skipped file.
        reason: Terse reason code or phrase (e.g.
                ``SKIP_REASON_SEMVER_SHAPE_REQUIRED``).

    Returns:
        The formatted log line (without trailing newline).
    """
    return f"discovery: skipping {name}: {reason}"


def intake_destination(name: str) -> str:
    """Return the project sub-directory a bare intake filename routes to.

    The one intake filename check, shared by the REST intake route and the
    CLI path (``deposit_intake``) so both accept and refuse the same names.
    It inspects the string only -- it never touches the filesystem -- so a
    caller runs it before creating, writing or naming any path.

    A name is accepted only when it is a bare file name (``has_traversal``
    finds no ``..``, no ``/`` or ``\\`` -- which also rules out every
    absolute path -- no URL-encoded separator or dot, and no NUL) AND it
    matches one of ``INTAKE_ROUTES`` (``BUG-*``, ``PRIORITY-*``,
    ``v[0-9]*.md``).

    Args:
        name: The intake filename exactly as supplied.

    Returns:
        ``"bugs"``, ``"priority"`` or ``"requirements"``.

    Raises:
        IntakeFilenameRefused: ``error`` is ``INTAKE_FILENAME_NOT_BARE`` when
            the name is a path, checked first; ``INTAKE_FILENAME_UNROUTABLE``
            when a bare name matches no routing prefix.
    """
    if has_traversal(name):
        raise IntakeFilenameRefused(
            INTAKE_FILENAME_NOT_BARE,
            "the intake filename must be a bare file name such as "
            "BUG-0001-example.md: no path separator ('/' or '\\'), no '..', "
            "no absolute path, and no URL-encoded separator or NUL byte.",
        )
    for pattern, subdir in INTAKE_ROUTES:
        if pattern.match(name):
            return subdir
    raise IntakeFilenameRefused(
        INTAKE_FILENAME_UNROUTABLE,
        f"cannot route '{name}': filename does not match "
        "BUG-*, PRIORITY-*, or vX.Y.Z-* intake patterns",
    )
