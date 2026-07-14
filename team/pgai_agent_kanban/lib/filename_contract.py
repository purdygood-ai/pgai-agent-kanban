"""
filename_contract.py — Single-source requirements filename patterns.

Both intake routing (ops/write.py) and discovery eligibility
(_disc_list_all_eligible_requirements in scripts/lib/discovery.sh) import
from this module.  The contract is:

  INTAKE_REQUIREMENTS_RE
      Accepts any requirements filename that intake routes to requirements/.
      Covers both semver-shaped filenames (v1.20.10-slug.md) and
      label-shaped filenames (v20260712-pvg-fieldtest.md).  Matches
      what was previously written as r'^v[0-9].*\\.md$' inside write.py
      and inside _INTAKE_ROUTES.

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

Structural guarantee (BUG-0045 acceptance criterion 3): the filename
pattern literals appear in exactly one place — this module.  Both
consumers import; neither duplicates a local definition.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Intake requirements filename pattern (the looser rule).
#
# Matches: v20260712-pvg-fieldtest.md, v1.20.10-slug.md, v0.0.1-foo.md
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
# Does NOT match: v20260712-pvg-fieldtest.md (no dots), v1.md (no slug)
#
# Used by discovery's semver path to assert the file has a dotted-version
# name before attempting to parse a semver Target Version from the header.
# The pattern is intentionally byte-identical to the pre-fix BUNDLE_RE
# semver arm so the semver path's behavior is unchanged.
# ---------------------------------------------------------------------------
SEMVER_REQUIREMENTS_RE: re.Pattern[str] = re.compile(
    r"^v[0-9]+\.[0-9]+\.[0-9]+-.+\.md$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Skip reason constants — named strings so callers and tests can use the
# same literals without repeating them.
# ---------------------------------------------------------------------------

#: Emitted when a file passes intake routing but fails the semver filename
#: shape on a semver project.  BUG-0045 acceptance criterion 2 requires
#: this to be a named (non-silent) skip.
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

    Shape matches the BUG-0042 logging contract:
        ``discovery: skipping {name}: {reason}``

    Args:
        name:   Bare filename or path-relative name of the skipped file.
        reason: Terse reason code or phrase (e.g.
                ``SKIP_REASON_SEMVER_SHAPE_REQUIRED``).

    Returns:
        The formatted log line (without trailing newline).
    """
    return f"discovery: skipping {name}: {reason}"
