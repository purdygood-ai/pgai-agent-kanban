"""
release_version.py — the one normaliser for a task README's ``## Release Version``.

Three readers tie a task to a release by its README's
``## Release Version`` field, and all read it through this module so they
agree on what a written value means:

  - the CM ship-policy stage's TESTER-task lookup
    (team/scripts/lib/cm/release_ship_policy_report.sh), which picks the
    TESTER task for the RC being decided; and
  - the bundled-item promotion at release time
    (team/scripts/cm/promote_bundled_items.py), which counts a DONE task as
    evidence that an item's fix shipped only when the task belongs to the
    version being shipped; and
  - the archive plan (pgai_agent_kanban/archive/plan.py), which orders a
    role's tasks by release version and holds those of the Active RC.

Public API
----------
    version_token(raw) -> str
        The field as written, reduced to one comparable token: bold markers,
        backticks and HTML comments dropped, the first word taken and
        lowercased, a bare ``X.Y.Z`` read as ``vX.Y.Z``. ``"none"`` when the
        value is absent or reads ``none``; any other word is returned as
        written (lowercased) so a caller can report it.

    release_version_of(raw) -> str
        ``version_token`` plus a tolerated branch prefix: ``ai_v1.2.3`` and
        ``ai_1.2.3`` both read as ``v1.2.3``. Returns ``""`` when the value is
        absent, ``none``, or not a release version in any of those forms.

    task_release_version(readme_path) -> str
        ``release_version_of`` applied to a task README's
        ``## Release Version`` field, read through
        :func:`pgai_agent_kanban.cm.read_state_field.read_state_field` (the
        single field reader every CM lookup uses).

Usage (import):
    from pgai_agent_kanban.cm.release_version import release_version_of
    release_version_of("**ai_v1.2.3**")   # -> "v1.2.3"
    release_version_of("none")            # -> ""
"""

from __future__ import annotations

import os
import re

from pgai_agent_kanban.cm.read_state_field import read_state_field

#: A normalised release version: ``vX.Y.Z``.
VERSION_SHAPE_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# A token ending in a release version with anything glued in front of it --
# a branch prefix such as "ai_" -- captured lazily so the shortest prefix
# wins and "v12.3.4" is never split into "v1" + "2.3.4".
_PREFIXED_VERSION_RE = re.compile(r"^.*?(v?\d+\.\d+\.\d+)$")


def version_token(raw: str) -> str:
    """Reduce a written ``## Release Version`` value to one comparable token.

    Args:
        raw: The field value as written (may carry ``**``, backticks, an
            inline HTML comment, or trailing annotation text).

    Returns:
        ``"vX.Y.Z"`` when the first word is version-shaped (a bare ``X.Y.Z``
        gains the ``v``); ``"none"`` when the value is empty or reads
        ``none``; otherwise the first word, lowercased, as written -- so a
        caller can name the unexpected value in its own narration.
    """
    cleaned = re.sub(r"[*`]", " ", re.sub(r"<!--.*?-->", " ", raw)).strip()
    m = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", cleaned)
    word = m.group(1).lower().rstrip(".") if m else ""
    if not word or word == "none":
        return "none"
    if re.match(r"^\d+\.\d+\.\d+$", word):
        word = "v" + word
    return word


def release_version_of(raw: str) -> str:
    """The release version a written value names, branch prefix tolerated.

    Args:
        raw: A ``## Release Version`` value or a version argument, in any
            form :func:`version_token` accepts, optionally carrying a branch
            prefix (``ai_v1.2.3``).

    Returns:
        ``"vX.Y.Z"``, or ``""`` when *raw* names no release version.
    """
    token = version_token(raw or "")
    if token == "none":
        return ""
    if VERSION_SHAPE_RE.match(token):
        return token
    m = _PREFIXED_VERSION_RE.match(token)
    if not m:
        return ""
    version = m.group(1)
    return version if version.startswith("v") else "v" + version


def task_release_version(readme_path: "str | os.PathLike[str]") -> str:
    """The release version a task README's ``## Release Version`` names.

    Args:
        readme_path: Path to a task folder's README.md.

    Returns:
        ``"vX.Y.Z"``, or ``""`` when the file or field is missing, reads
        ``none``, or names no release version.
    """
    return release_version_of(read_state_field(os.fspath(readme_path), "Release Version"))
