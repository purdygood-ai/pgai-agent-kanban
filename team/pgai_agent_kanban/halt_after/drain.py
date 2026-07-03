#!/usr/bin/env python3
"""
drain.py — Per-event drain-condition evaluator for the HALT-AFTER mechanism.

Dispatches on the event token and returns True when the drain condition is
satisfied (i.e. no in-flight work remains for that event type).

Drain conditions:

  rc            : bare token — captures the in-flight RC at first evaluation.
                  If Active RC is not "none", rewrites the HALT-AFTER file to
                  ``rc:vX.Y.Z`` (the arm-time version) and returns False (not
                  yet drained).  If Active RC is "none" (no RC in flight),
                  treats the drain condition as already satisfied and calls
                  promote() immediately: creates HALT, removes
                  HALT-AFTER, writes the audit entry, and returns True.  The
                  operator intent for ``HALT-AFTER rc`` is "stop after the
                  current RC finishes"; when there is no current RC, the stop
                  takes effect immediately rather than spinning forever.
  rc:vX.Y.Z     : version-pinned token — drain is satisfied when
                  release-state.md "Last Released" is present and semver >=
                  the captured version (latching/monotonic).
  pm      : drained iff no status.md under project_root/tasks/ has Role=PM
            with State in {WORKING, BACKLOG, WAITING}.  Both actively-running
            tasks (WORKING) and ready-to-run tasks (BACKLOG, WAITING) must be
            absent before the drain is considered satisfied.  This prevents
            false-positive drain when the agent has a queued task it has not
            yet started, or when a second task is queued and
            the first just finished.
            Note: WAITING tasks are treated as pending regardless of whether
            their prerequisites are satisfied (conservative approximation —
            never halts early when a WAITING task exists for the role).
  coder   : same broadened condition as pm, for Role=CODER, PLUS the
            PM-pending guard: drain is also blocked when any PM-role
            task is in BACKLOG, WAITING, or WORKING (PM may be decomposing
            work for the target agent that has not yet materialized in the
            task queue).
  writer  : same broadened condition as pm, with the PM-pending guard.
  tester  : same broadened condition as pm, with the PM-pending guard.
  cm      : same broadened condition as pm, with the PM-pending guard.

While the drain condition is NOT yet satisfied, the chain runs normally.
HALT-AFTER does not gate wakes during drain — it only triggers promotion when
drain completes.
"""

import importlib.util
import logging
import pathlib
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semver helper — loaded from team/pm-agent/lib/semver.py via importlib so
# that this sub-package does not require pm-agent on sys.path.  The file path
# is resolved relative to this module's own location: drain.py lives at
# team/pgai_agent_kanban/halt_after/drain.py, so three .parent steps reach
# team/, then we descend into pm-agent/lib/semver.py.
# ---------------------------------------------------------------------------

def _load_semver_ge():
    """Return the ``ge`` function from team/pm-agent/lib/semver.py.

    Uses importlib to load semver.py from its known location relative to this
    module.  Falls back to a minimal inline implementation if the file is
    missing (defensive — the file should always be present in the dev tree).
    """
    _semver_path = (
        pathlib.Path(__file__).parent.parent.parent  # → team/
        / "pm-agent" / "lib" / "semver.py"
    )
    if _semver_path.exists():
        spec = importlib.util.spec_from_file_location("semver", _semver_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.ge
    # Inline fallback (should not be reached in normal operation).
    logger.warning(
        "drain: semver.py not found at %s — using inline fallback", _semver_path
    )

    def _ge_fallback(a, b):
        """Minimal X.Y.Z semver >= that strips an optional leading 'v'."""
        def _parse(v):
            s = str(v).strip().lstrip("vV")
            parts = s.split(".")
            return tuple(int(p) for p in parts[:3])
        return _parse(a) >= _parse(b)

    return _ge_fallback


# Module-level singleton — loaded once at import time.
_semver_ge = _load_semver_ge()


def _normalize_version(version_str: str) -> str:
    """Normalize a version string for semver comparison.

    Strips:
    - Leading/trailing whitespace
    - Case-insensitive ``rc/`` prefix (e.g. ``rc/v0.44.2`` → ``v0.44.2``)
    - Leading ``v`` or ``V`` prefix so the result is ``X.Y.Z`` for the semver
      helper (which also accepts the ``v`` prefix, but explicit normalization
      makes the intent clear).

    Returns the empty string when the input is empty, 'none', or unparseable.
    """
    s = version_str.strip()
    if not s or s.lower() == "none":
        return ""
    # Strip rc/ prefix (case-insensitive)
    if s.lower().startswith("rc/"):
        s = s[3:]
    # Strip leading v/V
    if s and s[0] in ("v", "V"):
        s = s[1:]
    return s

# Patterns for reading status.md fields.  Using loose \s+ to be resilient to
# minor whitespace variation between task folder formats.
_STATE_PATTERN = re.compile(r"^##\s+State\s*$", re.MULTILINE)
_ROLE_PATTERN = re.compile(r"^##\s+Role\s*$", re.MULTILINE)

_HALT_AFTER_FILENAME = "HALT-AFTER"


def _read_state_field(path: pathlib.Path, heading: str) -> str:
    """Read the first non-blank, non-comment value after *heading* in *path*.

    Mirrors the behaviour of team.pgai_agent_kanban.cm.read_state_field but
    is re-implemented here to keep the halt_after sub-package self-contained
    (no import from cm, which avoids a circular-dep risk and keeps the public
    surface small).

    Returns the literal string ``"none"`` when the file is absent, the heading
    is not found, or no qualifying value follows the heading.
    """
    if not path.exists():
        return "none"
    lines = path.read_text(encoding="utf-8").splitlines()
    # Build the exact heading line to match, e.g. "## Active RC"
    target = f"## {heading.strip()}"
    in_section = False
    for line in lines:
        if in_section:
            v = line.strip()
            if v and not v.startswith("#"):
                return v
            # blank lines are skipped; another heading ends the section
            if v.startswith("#"):
                break
        elif line.strip() == target:
            in_section = True
    return "none"


def _parse_status_md(text: str) -> "dict[str, str]":
    """Extract State and Role from the text of a status.md file.

    Returns a dict with keys 'State' and 'Role', each holding the first
    non-blank, non-heading value after the respective ``## <key>`` heading.
    Missing fields return the empty string.
    """
    result: dict[str, str] = {"State": "", "Role": ""}
    lines = text.splitlines()
    for key in ("State", "Role"):
        target = f"## {key}"
        in_section = False
        for line in lines:
            if in_section:
                v = line.strip()
                if v and not v.startswith("#"):
                    result[key] = v
                    break
                if v.startswith("#"):
                    break
            elif line.strip() == target:
                in_section = True
    return result


def _has_working_role(project_root: pathlib.Path, role: str) -> bool:
    """Return True if any task status.md reports State=WORKING and Role=<role>.

    Scans all ``status.md`` files under ``<project_root>/tasks/`` (any depth).
    The comparison is case-insensitive for robustness.
    """
    tasks_dir = project_root / "tasks"
    if not tasks_dir.exists():
        logger.debug("drain._has_working_role: tasks dir not found: %s", tasks_dir)
        return False

    role_upper = role.upper()
    for status_path in tasks_dir.rglob("status.md"):
        try:
            text = status_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("drain: could not read %s; skipping", status_path)
            continue

        fields = _parse_status_md(text)
        if (
            fields["State"].upper() == "WORKING"
            and fields["Role"].upper() == role_upper
        ):
            logger.debug(
                "drain: found WORKING %s task at %s", role, status_path
            )
            return True

    return False


# States that indicate the agent has actionable work it has not yet completed.
# BACKLOG tasks are always ready to be picked up.
# WAITING tasks are treated as pending regardless of prerequisite status —
# this is a conservative approximation that prevents premature drain.  If a
# WAITING task turns out to be permanently blocked it would still prevent drain,
# but that is safer than halting before the agent has done expected work.
# An operator can remove a stuck WAITING task to unblock drain.
_PENDING_STATES = frozenset({"BACKLOG", "WAITING"})


def _has_pending_role(project_root: pathlib.Path, role: str) -> bool:
    """Return True if any task status.md reports a pending state and Role=<role>.

    A pending task is one with State in {BACKLOG, WAITING}: the agent has not
    yet started it, but it is queued and the agent would pick it up.  BACKLOG
    tasks are immediately actionable; WAITING tasks are treated as actionable
    (conservative approximation — see module docstring).

    This supplements :func:`_has_working_role` to prevent the false-positive
    drain that occurs when the agent's task has not yet transitioned to WORKING.

    Scans all ``status.md`` files under ``<project_root>/tasks/`` (any depth).
    The comparison is case-insensitive for robustness.
    """
    tasks_dir = project_root / "tasks"
    if not tasks_dir.exists():
        logger.debug("drain._has_pending_role: tasks dir not found: %s", tasks_dir)
        return False

    role_upper = role.upper()
    for status_path in tasks_dir.rglob("status.md"):
        try:
            text = status_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("drain: could not read %s; skipping", status_path)
            continue

        fields = _parse_status_md(text)
        if (
            fields["State"].upper() in _PENDING_STATES
            and fields["Role"].upper() == role_upper
        ):
            logger.debug(
                "drain: found pending (%s) %s task at %s",
                fields["State"],
                role,
                status_path,
            )
            return True

    return False


# States that count as "PM is still doing work" for the PM-pending guard.
# Same set as _PENDING_STATES plus WORKING — the full in-flight set.
_PM_IN_FLIGHT_STATES = frozenset({"BACKLOG", "WAITING", "WORKING"})


def _has_in_flight_pm(project_root: pathlib.Path) -> bool:
    """Return True if any PM-role task is still in flight for the project.

    "In flight" means State in {BACKLOG, WAITING, WORKING}: PM has not yet
    finished decomposing or has queued work that downstream agents have not
    received.  Per-agent drain tokens (coder, writer, tester, cm, pm) must
    not fire while PM is still active, because PM may be about to materialize
    tasks into the target agent's queue.

    Scans all ``status.md`` files under ``<project_root>/tasks/`` (any depth).
    The comparison is case-insensitive for robustness.
    """
    tasks_dir = project_root / "tasks"
    if not tasks_dir.exists():
        logger.debug("drain._has_in_flight_pm: tasks dir not found: %s", tasks_dir)
        return False

    for status_path in tasks_dir.rglob("status.md"):
        try:
            text = status_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("drain: could not read %s; skipping", status_path)
            continue

        fields = _parse_status_md(text)
        if (
            fields["State"].upper() in _PM_IN_FLIGHT_STATES
            and fields["Role"].upper() == "PM"
        ):
            logger.debug(
                "drain: found in-flight PM task (State=%s) at %s; "
                "per-agent drain blocked",
                fields["State"],
                status_path,
            )
            return True

    return False


def check_drain(
    event: str,
    project_root: "str | pathlib.Path",
    release_state_root: "str | pathlib.Path | None" = None,
) -> bool:
    """Return True when the drain condition for *event* is satisfied.

    A satisfied drain condition means no in-flight work remains for the named
    event — the wake script should proceed with auto-promotion.

    For the ``rc`` token family:

    - ``"rc:vX.Y.Z"`` (version-pinned): drained when release-state.md
      "Last Released" is present and semver >= the captured version (latching /
      monotonic — immune to window-miss and monotonically advancing releases).
    - ``"rc"`` (bare, no version): reads "Active RC" from release-state.md.
      If an RC is in flight, captures it as the arm-time version by rewriting
      the HALT-AFTER file to ``rc:vX.Y.Z`` and returns False (not yet drained).
      If Active RC is "none", promotes immediately: calls promote() to create
      HALT, remove HALT-AFTER, and write the audit entry, then returns True
      (invariant: the sentinel must never sit in a state where it neither
      promotes nor visibly errors).

    Args:
        event:              A canonical (lowercase) event token: 'rc', 'rc:vX.Y.Z',
                            'pm', 'coder', 'writer', 'tester', or 'cm'.
        project_root:       Filesystem path to the project root.  May be a string
                            or a ``pathlib.Path``.  Used for HALT-AFTER rewrite and
                            task scanning (role drain conditions).
        release_state_root: Optional path to the directory containing
                            ``release-state.md``.  When provided, this path is used
                            instead of *project_root* for the ``rc`` drain check.
                            Pass this when the HALT-AFTER sentinel lives at a
                            different scope than the project root (e.g. a root-scope
                            HALT-AFTER at ``$KANBAN_ROOT`` while release-state.md is
                            at ``$KANBAN_ROOT/projects/<name>/release-state.md``).
                            Defaults to *project_root* when absent or ``None``.

    Returns:
        ``True`` when the drain condition is satisfied.
        ``False`` when in-flight work is still present, or when the bare ``rc``
        token has just captured its arm-time version.

    Raises:
        ValueError: when *event* is not a supported token.
    """
    project_root = pathlib.Path(project_root)
    # Use release_state_root for release-state.md lookups when provided;
    # otherwise fall back to project_root (backward-compatible default).
    _release_state_dir = (
        pathlib.Path(release_state_root)
        if release_state_root is not None
        else project_root
    )

    if event == "rc" or event.startswith("rc:"):
        # release-state.md may live at a different path than the HALT-AFTER
        # sentinel (e.g. root-scope HALT-AFTER at $KANBAN_ROOT while
        # release-state.md is at $KANBAN_ROOT/projects/<name>/release-state.md).
        # _release_state_dir was resolved from the caller-supplied
        # release_state_root (or defaults to project_root when absent).
        release_state = _release_state_dir / "release-state.md"

        if event.startswith("rc:"):
            # Version-pinned: drain when Last Released >= captured version (semver,
            # latching/monotonic).  Uses semver >= comparison against the Last
            # Released field written by release.sh.
            captured_version = event[3:]  # strip "rc:" prefix
            last_released_raw = _read_state_field(release_state, "Last Released")
            # Normalize both sides: strip rc/ prefix and leading v/V.
            # _normalize_version returns "" for empty/none values.
            captured_norm = _normalize_version(captured_version)
            released_norm = _normalize_version(last_released_raw)
            if not released_norm:
                # Empty or missing Last Released — no release has shipped yet;
                # do not drain (preserve pre-existing behaviour for absent field).
                drained = False
            else:
                try:
                    drained = _semver_ge(released_norm, captured_norm)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "drain.check_drain rc (version-pinned): semver comparison "
                        "failed for released=%r captured=%r: %s — treating as not drained",
                        released_norm,
                        captured_norm,
                        exc,
                    )
                    drained = False
            logger.debug(
                "drain.check_drain rc (version-pinned): captured=%r "
                "last_released=%r captured_norm=%r released_norm=%r drained=%s",
                captured_version,
                last_released_raw,
                captured_norm,
                released_norm,
                drained,
            )
            return drained

        # Bare "rc" token — capture the arm-time version
        active_rc = _read_state_field(release_state, "Active RC")
        if active_rc.lower() == "none":
            # No RC in flight — the operator's intent for
            # HALT-AFTER rc is "stop after the current RC finishes".  When
            # there is no current RC, the drain condition is already satisfied
            # and we must promote immediately rather than returning False
            # forever.  Returning False here was the root cause: the token
            # could never capture a version (no RC to capture) and could never
            # progress, so it spun "drain not yet satisfied" on every wake
            # while the chain continued to run — silently ignoring the
            # operator's stop order.
            logger.info(
                "drain.check_drain rc (bare): active_rc=none — "
                "no RC in flight; promoting to HALT immediately"
            )
            from pgai_agent_kanban.halt_after.promote import promote  # noqa: PLC0415
            promote(project_root, "rc")
            return True

        # An RC is in flight — capture it by rewriting the HALT-AFTER file
        # Strip the "rc/" prefix from the branch name to get the version tag
        version_tag = active_rc
        if version_tag.lower().startswith("rc/"):
            version_tag = version_tag[3:]

        halt_after_path = project_root / _HALT_AFTER_FILENAME
        new_content = f"rc:{version_tag}\n"
        try:
            halt_after_path.write_text(new_content, encoding="utf-8")
            logger.info(
                "drain.check_drain rc (bare): captured arm-time version %r; "
                "rewrote HALT-AFTER to %r",
                version_tag,
                new_content.strip(),
            )
        except OSError as exc:
            logger.warning(
                "drain.check_drain rc (bare): could not rewrite HALT-AFTER at %s: %s",
                halt_after_path,
                exc,
            )
        # Not yet drained — we just armed it with the version
        return False

    if event in ("pm", "coder", "writer", "tester", "cm"):
        still_working = _has_working_role(project_root, event)
        has_pending = _has_pending_role(project_root, event)
        # For per-agent tokens (coder, writer, tester, cm, pm), also block
        # drain whenever a PM-role task is in flight (BACKLOG, WAITING, or
        # WORKING).  PM may be decomposing work that will soon appear in the
        # target agent's queue; draining while that work is pending would cause
        # a false-positive HALT before the agent has had a chance to run the
        # decomposed tasks.
        pm_in_flight = _has_in_flight_pm(project_root)
        drained = not still_working and not has_pending and not pm_in_flight
        logger.debug(
            "drain.check_drain %s: still_working=%s has_pending=%s "
            "pm_in_flight=%s drained=%s",
            event,
            still_working,
            has_pending,
            pm_in_flight,
            drained,
        )
        return drained

    raise ValueError(
        f"check_drain: unsupported event token {event!r}. "
        f"Supported: rc, rc:vX.Y.Z, pm, coder, writer, tester, cm."
    )
