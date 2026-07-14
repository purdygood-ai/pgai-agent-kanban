"""
operations.py — POST (mutation) endpoints for the pgai-agent-kanban operator API.

Implements every mutation endpoint defined in the v1.2.0 and v1.5.0 operator-REST-API requirements:

  POST /operations/halt                  → halt.sh --project <name>
  POST /operations/unhalt                → unhalt.sh --project <name>
  POST /operations/halt-after            → halt-after.sh --project <name> --key <token>
  POST /operations/halt-global           → halt-global.sh
  POST /operations/unhalt-global         → unhalt-global.sh
  POST /operations/reset                 → reset.sh --project <name> --key <resolved-key>
                                                    [--keep-artifacts]
  POST /operations/close                 → close.sh --project <name> --key <key> [--state ...] [--note ...] [--dry-run]
  POST /operations/wontdo                → wontdo.sh --project <name> --key <key>
  POST /operations/delete                → delete.sh --project <name> --key <key> [--force] [--dry-run]
  POST /operations/intake                → writes content to a temp file, then:
                                           intake.sh --project <name> <tempfile>
  POST /operations/unwind-rc             → unwind-rc.sh --project <name> --key <version> [--dry-run] [--force]
  POST /operations/set-version-ceiling   → set-version-ceiling.sh --project <name>
                                                    [--show] [--minor N] [--major N]
                                                    [--no-minor] [--no-major] [--dry-run]
  POST /operations/switch-provider       → switch-provider.sh --provider <name>
  POST /operations/create-project        → create-project.sh --project <name>
                                                    [--workflow-type <type>] [--max-patch N]
                                                    [--max-minor N] [--max-major N]
                                                    [--git-remote <name>] [--priority <int>]
                                                    [--color '#RRGGBB'] [--no-migrate] [--dry-run]
                                                    [--dev-tree <path>] [--git-repo <url>]
  POST /operations/add-project           → add-project.sh --project <name>
                                                    [--priority <int>] [--color '#RRGGBB']
                                                    [--no-migrate] [--dry-run]
  POST /operations/remove-project        → remove-project.sh --project <name>
                                                    [--force] [--dry-run]
                                           NOTE: force must be true when dry_run is false
                                           (API-layer guard fires 422 before any subprocess).
  POST /operations/ship-rc               → ship-rc.sh --project <name> --key vX.Y.Z [--dry-run]
                                           NOTE: requires confirm == "ship-rc <project> <key>"
                                           in the body (byte-for-byte exact match).
                                           The confirm-gate 422 fires at the API layer BEFORE
                                           ship-rc.sh is invoked — a wrong or missing confirm
                                           string never reaches the script.

Design:
  - All endpoints are POST only; no GET verbs on this router.
  - Body field names match the underlying script's flag names verbatim where Python
    identifiers permit; hyphenated flag names (dry-run, keep-artifacts) are accepted
    as underscore-form body fields (dry_run, keep_artifacts) and mapped to their
    hyphenated script flag names in the flags dict.
  - Script guards and refusals propagate unchanged — no re-implementation of logic.
  - intake writes its content to a temp file under PGAI_AGENT_KANBAN_TEMP_DIR before
    invoking intake.sh; the temp file path is passed as a positional argument (not
    --file) matching the script's positional-arg convention.
  - unwind-rc's HALT + Active-RC pre-flight guards surface via the envelope as the
    script emits them (non-zero exit_code, stderr populated).
  - reset uses mutually-exclusive kind selectors (key, agent, bug, priority,
    requirement) — exactly one must be supplied; the resolved value is forwarded as
    --key to reset.sh, which identifies the item type from the key prefix.

Envelope format (from adapter.py):
  {"exit_code": int, "stdout": str, "stderr": str}

HTTP status mapping:
  exit_code == 0  → 200 OK
  non-zero        → 500 Internal Server Error
  validation fail → 422 Unprocessable Entity

Security note: no authentication or TLS in this release.  Loopback-only binding
is the sole access-control mechanism.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..adapter import (
    HTTP_UNPROCESSABLE_ENTITY,
    ShellResult,
    ValidationError,
    build_argv,
    http_status_for,
    shell_out,
    validate_required,
)

__all__ = ["router"]

router = APIRouter(prefix="/operations", tags=["operations"])


# ---------------------------------------------------------------------------
# Internal helpers (mirroring reads.py conventions)
# ---------------------------------------------------------------------------


def _scripts_dir(request: Request) -> pathlib.Path:
    """Return the scripts/ directory path from the app's ApiConfig."""
    kanban_root: pathlib.Path = request.app.state.api_cfg.kanban_root
    return kanban_root / "scripts"


def _script_path(request: Request, relative: str) -> str:
    """Resolve a script path relative to scripts/ and return it as a string."""
    return str(_scripts_dir(request) / relative)


def _make_envelope(result: ShellResult, warnings: list[str] | None = None) -> JSONResponse:
    """Convert a ShellResult into the standard JSON envelope response.

    The ``warnings`` field is always present in the response.  It is an empty
    list on clean responses.  Operation routes populate it when unknown body
    fields are detected (body-field capture is implemented in a later ticket;
    the field is wired here so the envelope contract is satisfied now).

    Args:
        result:   The ShellResult envelope from the adapter.
        warnings: Warning strings for this response.  Defaults to an empty list.

    Returns:
        A JSONResponse with the appropriate HTTP status and envelope body.
    """
    return JSONResponse(
        content={
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "warnings": warnings if warnings is not None else [],
        },
        status_code=http_status_for(result.exit_code),
    )


def _kanban_temp_root() -> pathlib.Path:
    """Return the framework temp root directory.

    Resolution matches the bash pgai_mktemp convention in team/scripts/lib/temp.sh:
      1. PGAI_AGENT_KANBAN_TEMP_DIR env var if set.
      2. /tmp/pgai_kanban_tmp as the hard fallback.
    """
    env_val = os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "").strip()
    return pathlib.Path(env_val) if env_val else pathlib.Path("/tmp/pgai_kanban_tmp")


# ---------------------------------------------------------------------------
# Unknown-body-field capture helpers
# ---------------------------------------------------------------------------


def _levenshtein_distance(a: str, b: str) -> int:
    """Return the Levenshtein (edit) distance between two strings.

    Used to detect near-miss field names so that typos such as ``forse``
    are matched against ``dry_run`` and surfaced with 'did you mean' wording.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Integer edit distance (insertions + deletions + substitutions).
    """
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    # dp[i][j] = edit distance between a[:i] and b[:j]
    dp = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, len_b + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(prev[j] + 1, dp[j - 1] + 1, prev[j - 1] + cost)
    return dp[len_b]



# Common alternative names and typo targets for known API fields.  When a submitted
# unknown field is within edit-distance 2 of an alias, the canonical field name is
# surfaced in the 'did you mean' message.  This lets ``forse`` (distance-1 typo of
# ``force``) suggest ``dry_run``, which serves the same safety-override intent.
_FIELD_ALIASES: dict[str, list[str]] = {
    "dry_run": ["dryrun", "dry-run", "force", "dryRun"],
}


def _capture_unknown_fields(
    model_extra: dict,
    known_fields: list[str],
) -> list[str]:
    """Build warning strings for any unknown body fields found in model_extra.

    For each extra key, checks whether it is a near-miss of a known field or a
    known alias of a field (case-insensitive exact match or Levenshtein distance
    ≤ 2).  Near-misses produce 'did you mean' wording; unrelated fields produce
    plain warnings.

    The alias table in ``_FIELD_ALIASES`` extends matching so that common typos
    such as ``forse`` (distance 1 from the alias ``force``) correctly suggest
    ``dry_run``, which is the declared canonical field.

    Both forms warn and do not block execution — the operation still runs.

    Args:
        model_extra:  Dict of extra (unknown) fields from the parsed body
                      (``body.model_extra`` on a Pydantic v2 model with
                      ``extra='allow'``).
        known_fields: List of declared field names for this model.

    Returns:
        A list of warning strings, one per unknown field.  Empty when
        model_extra is empty.
    """
    warnings: list[str] = []
    known_lower = {f.lower(): f for f in known_fields}

    for bad_name in model_extra:
        bad_lower = bad_name.lower()

        # Case-insensitive exact match against a known field.
        candidate = known_lower.get(bad_lower)
        if candidate is not None:
            warnings.append(
                f"unknown field: {bad_name} — did you mean {candidate}? (operation executed)"
            )
            continue

        # Levenshtein near-miss: edit distance ≤ 2 against any known field.
        near_misses = [
            f
            for f in known_fields
            if _levenshtein_distance(bad_lower, f.lower()) <= 2
        ]
        if near_misses:
            best = min(
                near_misses,
                key=lambda f: _levenshtein_distance(bad_lower, f.lower()),
            )
            warnings.append(
                f"unknown field: {bad_name} — did you mean {best}? (operation executed)"
            )
            continue

        # Alias-based near-miss: edit distance ≤ 2 against a known alias.
        # Only applicable when the canonical field is declared for this model.
        alias_candidate: str | None = None
        alias_best_dist = 3  # exclusive upper bound
        for canonical, aliases in _FIELD_ALIASES.items():
            if canonical not in known_fields:
                continue
            for alias in aliases:
                d = _levenshtein_distance(bad_lower, alias.lower())
                if d <= 2 and d < alias_best_dist:
                    alias_best_dist = d
                    alias_candidate = canonical

        if alias_candidate is not None:
            warnings.append(
                f"unknown field: {bad_name} — did you mean {alias_candidate}? (operation executed)"
            )
        else:
            warnings.append(f"unknown field: {bad_name}")

    return warnings


# ---------------------------------------------------------------------------
# Dispatch-layer dry_run short-circuit
# ---------------------------------------------------------------------------


def _dry_run_envelope(
    dry_run: bool,
    planned_argv: list[str],
    warnings: list[str],
) -> JSONResponse | None:
    """Return a short-circuit dry-run envelope when dry_run is True.

    This is the ONE implementation of the dry_run short-circuit for all 17
    operation routes.  When dry_run is True, no subprocess is spawned; the
    function returns a standard JSON envelope with exit_code=0, a stdout
    description of the planned action, and the supplied warnings list.

    When dry_run is False, the function returns None and the caller proceeds
    to invoke the subprocess normally.

    Args:
        dry_run:      When True, produce the dry-run envelope.
        planned_argv: The fully-resolved argv that WOULD have been executed.
                      Used to build the stdout description.
        warnings:     Warnings already accumulated for this request (unknown
                      body fields, etc.).  Included verbatim in the envelope.

    Returns:
        A JSONResponse (HTTP 200) when dry_run is True.
        None when dry_run is False.
    """
    if not dry_run:
        return None

    script_name = pathlib.Path(planned_argv[0]).name if planned_argv else "unknown"
    args_preview = " ".join(planned_argv[1:]) if len(planned_argv) > 1 else ""
    stdout_desc = f"dry-run: would execute {script_name}"
    if args_preview:
        stdout_desc += f" {args_preview}"

    return JSONResponse(
        content={
            "exit_code": 0,
            "stdout": stdout_desc,
            "stderr": "",
            "warnings": warnings,
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class HaltBody(BaseModel):
    """Body for POST /operations/halt and POST /operations/unhalt."""

    model_config = ConfigDict(extra="allow")

    project: str
    dry_run: bool = False


class HaltAfterBody(BaseModel):
    """Body for POST /operations/halt-after.

    Fields:
        project: Project name to arm with the HALT-AFTER signal.
        key:     Drain event token (e.g. ``rc``, ``pm``, ``coder``).
                 Passed verbatim as ``--key`` to halt-after.sh.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    dry_run: bool = False


class HaltGlobalBody(BaseModel):
    """Body for POST /operations/halt-global and POST /operations/unhalt-global.

    These scripts accept no project or key arguments.  The body is accepted but
    ignored, allowing callers to POST an empty JSON object ``{}`` or omit the body.

    Fields:
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
    """

    model_config = ConfigDict(extra="allow")

    dry_run: bool = False


class ResetBody(BaseModel):
    """Body for POST /operations/reset.

    Exactly one of the kind selectors (``key``, ``agent``, ``bug``, ``priority``,
    ``requirement``) must be supplied.  Supplying two or more returns a 422 that
    names all conflicting fields.  Supplying none also returns a 422.

    The resolved selector value is forwarded as ``--key`` to reset.sh.  reset.sh
    determines the item type from the key prefix (agent tasks are ``ROLE-YYYYMMDD-NNN``
    prefixed; bug intake is ``BUG-*``; priority intake is ``PRIORITY-*``; requirement
    intake is a version string such as ``v0.1.2``).

    Fields:
        project:        Project name.
        key:            Task key or intake identifier (kind-agnostic selector).
        agent:          Agent task ID (e.g. ``CODER-20260101-001-slug``).
        bug:            Bug intake key (e.g. ``BUG-0042``).
        priority:       Priority intake key (e.g. ``PRIORITY-0007``).
        requirement:    Requirement intake version (e.g. ``v0.1.2``).
        keep_artifacts: When True, passes ``--keep-artifacts`` to reset.sh.
        dry_run:        When True, short-circuits dispatch — no subprocess spawned.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: Optional[str] = None
    agent: Optional[str] = None
    bug: Optional[str] = None
    priority: Optional[str] = None
    requirement: Optional[str] = None
    keep_artifacts: Optional[bool] = None
    dry_run: bool = False


class CloseBody(BaseModel):
    """Body for POST /operations/close.

    Fields:
        project: Project name.
        key:     Task key or intake identifier to close.
        state:   Close state override (``done``, ``wont-do``, ``superseded``).
                 Passed as ``--state`` to close.sh; intake-only.
        note:    Optional note text.  Passed as ``--note`` to close.sh.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Also passes ``--dry-run`` to close.sh when not short-circuited
                 (only used when dry_run support is later removed from dispatch).
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    state: Optional[str] = None
    note: Optional[str] = None
    dry_run: bool = False


class WontdoBody(BaseModel):
    """Body for POST /operations/wontdo.

    Fields:
        project: Project name.
        key:     Task ID to mark WONT-DO (folder name under tasks/).
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    dry_run: bool = False


class DeleteBody(BaseModel):
    """Body for POST /operations/delete.

    Fields:
        project: Project name.
        key:     Item key to delete (task folder name or intake file base name).
        force:   When True, passes ``--force`` to delete.sh, overriding terminal-state guard.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Also passes ``--dry-run`` to delete.sh when not short-circuited.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    force: Optional[bool] = None
    dry_run: bool = False


class IntakeBody(BaseModel):
    """Body for POST /operations/intake.

    Fields:
        project:  Project name.
        filename: Name for the intake file (used as the temp file name; must follow
                  the intake routing convention: BUG-*, PRIORITY-*, or v[0-9]*.md).
        content:  Text content to write into the temp file before invoking intake.sh.
        dry_run:  When True, short-circuits dispatch — no subprocess spawned and no
                  temp file is written.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    filename: str
    content: str
    dry_run: bool = False


class UnwindRcBody(BaseModel):
    """Body for POST /operations/unwind-rc.

    Fields:
        project: Project name.
        key:     RC version to unwind (e.g. ``v1.2.0``).
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Also passes ``--dry-run`` to unwind-rc.sh when not short-circuited.
        force:   When True, passes ``--force`` to unwind-rc.sh, bypassing Active-RC
                 version-mismatch check.  Does NOT bypass HALT, project-existence,
                 version-format, or shipped-tag checks.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    dry_run: bool = False
    force: Optional[bool] = None


class SetVersionCeilingBody(BaseModel):
    """Body for POST /operations/set-version-ceiling.

    Mirrors the operator flags of set-version-ceiling.sh verbatim
    (body fields use underscores; script flags use hyphens).

    Fields:
        project:  Project name (required).
        show:     When True, passes ``--show`` to print current ceiling values.
        minor:    Non-negative integer; sets ``max_minor`` in project.cfg.
                  Passed as ``--minor N`` to the script.
        major:    Non-negative integer; sets ``max_major`` in project.cfg.
                  Passed as ``--major N`` to the script.
        no_minor: When True, passes ``--no-minor`` to remove ``max_minor``.
        no_major: When True, passes ``--no-major`` to remove ``max_major``.
        dry_run:  When True, short-circuits dispatch — no subprocess spawned.
                  Also passes ``--dry-run`` to the script when not short-circuited.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    show: Optional[bool] = None
    minor: Optional[int] = None
    major: Optional[int] = None
    no_minor: Optional[bool] = None
    no_major: Optional[bool] = None
    dry_run: bool = False


class SwitchProviderBody(BaseModel):
    """Body for POST /operations/switch-provider.

    Fields:
        provider: LLM provider name (e.g. ``claude``, ``codex``, ``gemini``).
                  Passed verbatim as ``--provider`` to switch-provider.sh.
                  Provider validation is performed by the script; no server-side
                  re-implementation of the allowed-value list.
        dry_run:  When True, short-circuits dispatch — no subprocess spawned.
    """

    model_config = ConfigDict(extra="allow")

    provider: str
    dry_run: bool = False


class CreateProjectBody(BaseModel):
    """Body for POST /operations/create-project.

    Mirrors every operator flag accepted by create-project.sh verbatim
    (body fields use underscores; script flags use hyphens where applicable).

    Note on path flags: ``dev_tree`` (``--dev-tree``) and ``git_repo`` (``--git-repo``)
    are included for API-parity completeness.  create-project.sh deliberately rejects
    these flags with a clear message pointing operators at manual project.cfg editing.
    If supplied, the script's refusal propagates unchanged in the envelope (HTTP 500,
    exit_code non-zero).  No server-side re-implementation of that guard.

    Fields:
        project:       Project name (required).
        workflow_type: Workflow type override (``release`` or ``document``; default: ``release``).
                       Passed as ``--workflow-type``.
        max_patch:     max_patch ceiling override.  Passed as ``--max-patch N``.
        max_minor:     max_minor ceiling override.  Passed as ``--max-minor N``.
        max_major:     max_major ceiling override.  Passed as ``--max-major N``.
        git_remote:    git_remote_name override.  Passed as ``--git-remote <name>``.
        priority:      Registry priority integer.  Passed as ``--priority <int>``.
        color:         Registry display color (e.g. ``'#RRGGBB'``).  Passed as ``--color``.
        no_migrate:    When True, suppresses projects.cfg auto-migration.
                       Passed as ``--no-migrate``.
        dry_run:       When True, short-circuits dispatch — no subprocess spawned.
                       Also passes ``--dry-run`` to the script when not short-circuited.
        dev_tree:      Rejected by create-project.sh with a clear message; included for
                       parity lint.  Passed as ``--dev-tree`` if supplied; script refuses.
        git_repo:      Rejected by create-project.sh with a clear message; included for
                       parity lint.  Passed as ``--git-repo`` if supplied; script refuses.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    workflow_type: Optional[str] = None
    max_patch: Optional[int] = None
    max_minor: Optional[int] = None
    max_major: Optional[int] = None
    git_remote: Optional[str] = None
    priority: Optional[int] = None
    color: Optional[str] = None
    no_migrate: Optional[bool] = None
    dry_run: bool = False
    dev_tree: Optional[str] = None
    git_repo: Optional[str] = None


class AddProjectBody(BaseModel):
    """Body for POST /operations/add-project.

    Mirrors every operator flag accepted by add-project.sh verbatim
    (body fields use underscores; script flags use hyphens where applicable).

    Fields:
        project:    Project name (required).  The project directory must already
                    exist under ``$KANBAN_ROOT/projects/<name>/``; add-project.sh
                    only updates the registry, not the directory.
        priority:   Registry priority integer.  Passed as ``--priority <int>``.
        color:      Registry display color (e.g. ``'#RRGGBB'``).  Passed as ``--color``.
        no_migrate: When True, suppresses projects.cfg auto-migration.
                    Passed as ``--no-migrate``.
        dry_run:    When True, short-circuits dispatch — no subprocess spawned.
                    Also passes ``--dry-run`` to the script when not short-circuited.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    priority: Optional[int] = None
    color: Optional[str] = None
    no_migrate: Optional[bool] = None
    dry_run: bool = False


class RemoveProjectBody(BaseModel):
    """Body for POST /operations/remove-project.

    Mirrors every operator flag accepted by remove-project.sh verbatim.

    API-layer force guard: when ``dry_run`` is not True, ``force`` must be
    explicitly True.  The guard fires at the API layer before any subprocess is
    spawned, returning 422 with a message naming ``force``.  This matches the
    spirit of remove-project.sh's interactive safety: the API is not gentler
    than the script.

    Fields:
        project: Project name (required).
        force:   When True, passes ``--force`` to remove-project.sh, which also
                 deletes the project directory.  Required (must be True) when
                 ``dry_run`` is not True — the API refuses with 422 otherwise.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Also passes ``--dry-run`` to the script when not short-circuited.
                 When True, ``force`` is not required.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    force: Optional[bool] = None
    dry_run: bool = False


class ShipRcBody(BaseModel):
    """Body for POST /operations/ship-rc.

    Mirrors the operator flags accepted by ship-rc.sh, plus a mandatory
    confirm field that implements a double-gate: the caller must echo the
    exact string ``ship-rc <project> <key>`` to proceed.

    The confirm-gate fires at the API layer BEFORE ship-rc.sh is invoked.
    A wrong or missing confirm string returns 422 and never reaches the
    script.  The comparison is byte-for-byte exact — no trimming, no
    case-folding.

    Fields:
        project: Project name (required).  Passed as ``--project`` to ship-rc.sh.
        key:     Release version in format ``vX.Y.Z`` (required).
                 Passed as ``--key`` to ship-rc.sh.
        confirm: Confirmation string (required).  Must equal exactly
                 ``ship-rc <project> <key>`` (byte-for-byte).  Designed so
                 a future dashboard "Ship" button echoes the string verbatim.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Also passes ``--dry-run`` to ship-rc.sh when not short-circuited.
                 The script prints the plan and exits 0 without git operations.
    """

    model_config = ConfigDict(extra="allow")

    project: str
    key: str
    confirm: str
    dry_run: bool = False


# ---------------------------------------------------------------------------
# POST /operations/halt
# ---------------------------------------------------------------------------


@router.post(
    "/halt",
    summary="Create the HALT signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from halt.sh."
    ),
)
def post_halt(body: HaltBody, request: Request) -> JSONResponse:
    """Set the per-project HALT flag, stopping the wake loop cleanly at its next iteration.

    Body fields:

    - ``project`` (required) — project name.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without creating the HALT file.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure.
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(HaltBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "halt.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/unhalt
# ---------------------------------------------------------------------------


@router.post(
    "/unhalt",
    summary="Remove the HALT signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from unhalt.sh."
    ),
)
def post_unhalt(body: HaltBody, request: Request) -> JSONResponse:
    """Remove the per-project HALT flag, allowing the wake loop to resume.

    Body fields:

    - ``project`` (required) — project name.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without removing the HALT file.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(HaltBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "unhalt.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/halt-after
# ---------------------------------------------------------------------------


@router.post(
    "/halt-after",
    summary="Arm the HALT-AFTER soft-drain signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from halt-after.sh."
    ),
)
def post_halt_after(body: HaltAfterBody, request: Request) -> JSONResponse:
    """Arm the HALT-AFTER soft-drain signal.

    The signal causes the wake loop to promote to a hard HALT once the named
    drain event is satisfied (e.g. after the current RC ships).

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — drain event token (``rc``, ``pm``, ``coder``,
                               ``writer``, ``tester``, ``cm``).
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without writing the HALT-AFTER signal.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(HaltAfterBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "halt-after.sh")
    flags: dict = {"project": body.project, "key": body.key}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/halt-global
# ---------------------------------------------------------------------------


@router.post(
    "/halt-global",
    summary="Create the global HALT signal",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from halt-global.sh."
    ),
)
def post_halt_global(request: Request, body: HaltGlobalBody = HaltGlobalBody()) -> JSONResponse:
    """Set the global HALT flag at ``<kanban_root>/HALT``.

    Blocks all projects at the next wake-loop iteration.  Idempotent and
    fully reversible via ``POST /operations/unhalt-global``.

    Body fields:

    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without creating the global HALT file.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    known_fields = list(HaltGlobalBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "halt-global.sh")
    planned_argv = build_argv(script, {})

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, {})
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/unhalt-global
# ---------------------------------------------------------------------------


@router.post(
    "/unhalt-global",
    summary="Remove the global HALT signal",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from unhalt-global.sh."
    ),
)
def post_unhalt_global(request: Request, body: HaltGlobalBody = HaltGlobalBody()) -> JSONResponse:
    """Remove the global HALT flag from ``<kanban_root>/HALT``.

    Allows the wake loop to resume processing all projects at the next iteration.
    Idempotent.

    Body fields:

    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without removing the global HALT file.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    known_fields = list(HaltGlobalBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "unhalt-global.sh")
    planned_argv = build_argv(script, {})

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, {})
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/reset
# ---------------------------------------------------------------------------


@router.post(
    "/reset",
    summary="Reset a task or intake item to its initial state",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from reset.sh."
    ),
)
def post_reset(body: ResetBody, request: Request) -> JSONResponse:
    """Reset a task or intake item, returning it to a re-processable state.

    The script refuses on filesystem races (WORKING state, ambiguous key)
    and those refusals propagate unchanged in the envelope.

    Body fields (exactly one kind selector is required):

    - ``project``        (required) — project name.
    - ``key``            (optional) — kind-agnostic task or intake key.
    - ``agent``          (optional) — agent task ID (e.g. ``CODER-20260101-001-slug``).
    - ``bug``            (optional) — bug intake key (e.g. ``BUG-0042``).
    - ``priority``       (optional) — priority intake key (e.g. ``PRIORITY-0007``).
    - ``requirement``    (optional) — requirement intake version (e.g. ``v0.1.2``).
    - ``keep_artifacts`` (optional, bool) — when True, passes ``--keep-artifacts``.
    - ``dry_run``        (optional, bool) — when True, returns the planned action description
                                            without resetting anything.

    Exactly one of ``key``, ``agent``, ``bug``, ``priority``, or ``requirement`` must
    be supplied.  Supplying two or more returns 422 naming the conflicting fields.
    Supplying none returns 422 naming the missing choice.  No silent precedence.

    The resolved value is forwarded as ``--key`` to reset.sh; reset.sh determines the
    item type from the key prefix.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure.
    """
    # Validate project is present.
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    # Exactly-one-of constraint across the five kind selectors.
    _KIND_FIELDS = ("key", "agent", "bug", "priority", "requirement")
    supplied = [
        field
        for field in _KIND_FIELDS
        if getattr(body, field) not in (None, "")
    ]

    if len(supplied) == 0:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Exactly one of {{{', '.join(_KIND_FIELDS)}}} must be supplied; "
                "none provided."
            ),
        )

    if len(supplied) > 1:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"Exactly one of {{{', '.join(_KIND_FIELDS)}}} must be supplied; "
                f"got conflicting fields: {', '.join(supplied)}."
            ),
        )

    # Extract the resolved key value from whichever selector was supplied.
    resolved_key = getattr(body, supplied[0])

    known_fields = list(ResetBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "reset.sh")
    flags: dict = {"project": body.project, "key": resolved_key}
    if body.keep_artifacts:
        flags["keep-artifacts"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/close
# ---------------------------------------------------------------------------


@router.post(
    "/close",
    summary="Close or resolve a task or intake item",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from close.sh."
    ),
)
def post_close(body: CloseBody, request: Request) -> JSONResponse:
    """Close or resolve a task or intake item.

    Refuses when the key cannot be identified as a single target (not found or
    ambiguous); the refusal propagates unchanged in the envelope.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — task key or intake identifier.
    - ``state``   (optional) — close state (``done``, ``wont-do``, ``superseded``);
                               intake-only; passed as ``--state``.
    - ``note``    (optional) — note text; passed as ``--note``.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without closing anything.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(CloseBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "close.sh")
    flags: dict = {"project": body.project, "key": body.key}
    if body.state:
        flags["state"] = body.state
    if body.note:
        flags["note"] = body.note
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/wontdo
# ---------------------------------------------------------------------------


@router.post(
    "/wontdo",
    summary="Mark a task as WONT-DO",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from wontdo.sh."
    ),
)
def post_wontdo(body: WontdoBody, request: Request) -> JSONResponse:
    """Mark a task as WONT-DO (cancelled without completion).

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — task ID (folder name under tasks/).
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without modifying any task state.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(WontdoBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "wontdo.sh")
    flags: dict = {"project": body.project, "key": body.key}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/delete
# ---------------------------------------------------------------------------


@router.post(
    "/delete",
    summary="Delete a task or intake item by key",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from delete.sh."
    ),
)
def post_delete(body: DeleteBody, request: Request) -> JSONResponse:
    """Delete a task or intake item by key.

    The script refuses to delete items that are not in a terminal state (DONE or
    WONT-DO) unless ``force`` is True.  On ambiguous keys, the script emits its
    refusal message in stderr and exits non-zero; that propagates unchanged as
    HTTP 500 in the envelope.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — item key (task folder name or intake file base name).
    - ``force``   (optional, bool) — when True, passes ``--force`` (override terminal-state guard).
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without deleting anything.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on success, 500 on refusal or non-zero exit, 422 on validation failure.
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(DeleteBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "delete.sh")
    flags: dict = {"project": body.project, "key": body.key}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/intake
# ---------------------------------------------------------------------------


@router.post(
    "/intake",
    summary="Deposit a staged intake file into a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from intake.sh."
    ),
)
def post_intake(body: IntakeBody, request: Request) -> JSONResponse:
    """Write the supplied content to a temp file and deposit it via intake.sh.

    The temp file is written under the framework temp root (``PGAI_AGENT_KANBAN_TEMP_DIR``
    or ``/tmp/pgai_kanban_tmp`` as fallback) in an ``intake/`` subdirectory, using
    ``filename`` as the file name.  intake.sh then routes the file by its name prefix
    (``BUG-*``, ``PRIORITY-*``, ``v[0-9]*.md``) and deposits it into the appropriate
    project sub-directory.  The temp file is left in place per existing intake semantics;
    cleanup is the operator's responsibility.

    Body fields:

    - ``project``  (required) — project name.
    - ``filename`` (required) — intake file name; governs routing inside intake.sh.
    - ``content``  (required) — text content to write into the temp file.
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing the temp file or invoking intake.sh.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    The temp file path appears in the invocation argv (as a positional argument to
    intake.sh, not via ``--file``), consistent with the script's positional-arg mode.
    """
    try:
        validate_required(
            {"project": body.project, "filename": body.filename, "content": body.content},
            required_keys=["project", "filename", "content"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(IntakeBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    # Build the planned argv for dry-run description (temp file path placeholder used).
    script = _script_path(request, "intake.sh")
    temp_intake_dir = _kanban_temp_root() / "intake"
    temp_file_path = temp_intake_dir / body.filename
    planned_argv = [script, "--project", body.project, str(temp_file_path)]

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    # Write content to a temp file under the framework temp root.
    temp_intake_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path.write_text(body.content, encoding="utf-8")

    # intake.sh takes the source file as a positional argument after flags:
    #   intake.sh --project <name> <tempfile>
    argv = [script, "--project", body.project, str(temp_file_path)]
    completed = subprocess.run(argv, capture_output=True, text=True)
    result = ShellResult(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/unwind-rc
# ---------------------------------------------------------------------------


@router.post(
    "/unwind-rc",
    summary="Fully unwind an in-flight release candidate",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from unwind-rc.sh."
    ),
)
def post_unwind_rc(body: UnwindRcBody, request: Request) -> JSONResponse:
    """Unwind an in-flight release candidate across all state stores.

    Pre-flight guards enforced by unwind-rc.sh propagate unchanged in the envelope:

    - HALT must be set (per-project or global) before unwinding.
    - Active RC in release-state.md must match ``key`` (bypassable with ``force``).

    These guards surface as non-zero exit_code and populated stderr, mapped to HTTP 500.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — RC version to unwind (e.g. ``v1.2.0``).
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without making any changes.
    - ``force``   (optional, bool) — when True, passes ``--force`` (bypass Active-RC version
                                     mismatch check; does NOT bypass HALT or other guards).

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(UnwindRcBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "unwind-rc.sh")
    flags: dict = {"project": body.project, "key": body.key}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/set-version-ceiling
# ---------------------------------------------------------------------------


@router.post(
    "/set-version-ceiling",
    summary="Read or set max_minor / max_major version ceilings for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from set-version-ceiling.sh."
    ),
)
def post_set_version_ceiling(body: SetVersionCeilingBody, request: Request) -> JSONResponse:
    """Read or mutate max_minor / max_major version ceilings in a project's project.cfg.

    Thin passthrough to set-version-ceiling.sh.  All script guards (project not
    found, missing action, invalid integer value) propagate unchanged in the envelope.

    Body fields:

    - ``project``  (required) — project name.
    - ``show``     (optional, bool) — when True, passes ``--show`` to print current values.
    - ``minor``    (optional, int) — sets ``max_minor``; passed as ``--minor N``.
    - ``major``    (optional, int) — sets ``max_major``; passed as ``--major N``.
    - ``no_minor`` (optional, bool) — when True, passes ``--no-minor`` to remove ``max_minor``.
    - ``no_major`` (optional, bool) — when True, passes ``--no-major`` to remove ``max_major``.
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing project.cfg.

    The script requires at least one action (``show``, ``minor``, ``major``,
    ``no_minor``, or ``no_major``).  Supplying none returns a non-zero exit_code
    with the script's own refusal in stderr.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure.
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(SetVersionCeilingBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "set-version-ceiling.sh")
    flags: dict = {"project": body.project}
    if body.show:
        flags["show"] = True
    if body.minor is not None:
        flags["minor"] = str(body.minor)
    if body.major is not None:
        flags["major"] = str(body.major)
    if body.no_minor:
        flags["no-minor"] = True
    if body.no_major:
        flags["no-major"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/switch-provider
# ---------------------------------------------------------------------------


@router.post(
    "/switch-provider",
    summary="Switch the active LLM provider for the kanban framework",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from switch-provider.sh."
    ),
)
def post_switch_provider(body: SwitchProviderBody, request: Request) -> JSONResponse:
    """Switch the active LLM provider for the kanban framework.

    Thin passthrough to switch-provider.sh.  Provider name validation (allowed
    values, CLI binary presence check) is performed entirely by the script;
    no server-side re-implementation of that logic.  Script refusals propagate
    unchanged in the envelope as non-zero exit_code.

    Body fields:

    - ``provider`` (required) — LLM provider name (e.g. ``claude``, ``codex``, ``gemini``).
                               Passed verbatim as ``--provider`` to switch-provider.sh.
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing kanban.cfg.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero (unknown provider, CLI not in PATH),
    422 on validation failure (empty provider string).
    """
    try:
        validate_required({"provider": body.provider}, required_keys=["provider"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(SwitchProviderBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "switch-provider.sh")
    flags: dict = {"provider": body.provider}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/create-project
# ---------------------------------------------------------------------------


@router.post(
    "/create-project",
    summary="Bootstrap a new project in the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from create-project.sh."
    ),
)
def post_create_project(body: CreateProjectBody, request: Request) -> JSONResponse:
    """Bootstrap a new project directory and register it in projects.cfg.

    Thin passthrough to create-project.sh.  All script guards (project already
    exists, invalid workflow type, bad ceiling values, rejected path flags)
    propagate unchanged in the envelope as non-zero exit_code.

    The ``dev_tree`` and ``git_repo`` body fields are forwarded to the script
    when supplied; create-project.sh rejects them with a clear message pointing
    at manual project.cfg editing — that refusal propagates unchanged in the
    envelope.  No server-side re-implementation of that guard.

    Body fields:

    - ``project``       (required) — project name.
    - ``workflow_type`` (optional) — ``release`` or ``document``; default ``release``.
    - ``max_patch``     (optional, int) — max_patch ceiling override.
    - ``max_minor``     (optional, int) — max_minor ceiling override.
    - ``max_major``     (optional, int) — max_major ceiling override.
    - ``git_remote``    (optional) — git_remote_name override; default ``origin``.
    - ``priority``      (optional, int) — registry priority (next available if absent).
    - ``color``         (optional) — registry display color (e.g. ``'#RRGGBB'``).
    - ``no_migrate``    (optional, bool) — when True, suppresses projects.cfg migration.
    - ``dry_run``       (optional, bool) — when True, returns the planned action description
                                            without creating anything.
    - ``dev_tree``      (optional) — parity-lint placeholder; script rejects this flag.
    - ``git_repo``      (optional) — parity-lint placeholder; script rejects this flag.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure.
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(CreateProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "create-project.sh")
    flags: dict = {"project": body.project}
    if body.workflow_type:
        flags["workflow-type"] = body.workflow_type
    if body.max_patch is not None:
        flags["max-patch"] = str(body.max_patch)
    if body.max_minor is not None:
        flags["max-minor"] = str(body.max_minor)
    if body.max_major is not None:
        flags["max-major"] = str(body.max_major)
    if body.git_remote:
        flags["git-remote"] = body.git_remote
    if body.priority is not None:
        flags["priority"] = str(body.priority)
    if body.color:
        flags["color"] = body.color
    if body.no_migrate:
        flags["no-migrate"] = True
    if body.dev_tree:
        flags["dev-tree"] = body.dev_tree
    if body.git_repo:
        flags["git-repo"] = body.git_repo
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/add-project
# ---------------------------------------------------------------------------


@router.post(
    "/add-project",
    summary="Register an existing on-disk project directory in the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from add-project.sh."
    ),
)
def post_add_project(body: AddProjectBody, request: Request) -> JSONResponse:
    """Register an existing project directory in projects.cfg.

    Thin passthrough to add-project.sh.  The project directory must already
    exist; add-project.sh does not create anything on disk — it only updates
    the registry.  Script guards (directory not found, already registered)
    propagate unchanged in the envelope as non-zero exit_code.

    Body fields:

    - ``project``    (required) — project name.
    - ``priority``   (optional, int) — registry priority (next available if absent).
    - ``color``      (optional) — registry display color (e.g. ``'#RRGGBB'``).
    - ``no_migrate`` (optional, bool) — when True, suppresses projects.cfg migration.
    - ``dry_run``    (optional, bool) — when True, returns the planned action description
                                         without mutating registry.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure.
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(AddProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "add-project.sh")
    flags: dict = {"project": body.project}
    if body.priority is not None:
        flags["priority"] = str(body.priority)
    if body.color:
        flags["color"] = body.color
    if body.no_migrate:
        flags["no-migrate"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/remove-project
# ---------------------------------------------------------------------------


@router.post(
    "/remove-project",
    summary="Unregister a project from the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from remove-project.sh."
    ),
)
def post_remove_project(body: RemoveProjectBody, request: Request) -> JSONResponse:
    """Unregister a project from projects.cfg; with force, also delete its directory.

    API-layer force guard: when ``dry_run`` is not True, ``force`` must be
    explicitly True.  This guard fires before any subprocess is spawned and
    returns 422 with a detail message naming ``force``.  The API is not
    gentler than the underlying script — explicit opt-in is required for any
    live removal.

    Script guards (Active RC in flight, project absent) propagate unchanged in
    the envelope as non-zero exit_code when the force guard is satisfied.

    Body fields:

    - ``project`` (required) — project name.
    - ``force``   (optional, bool) — when True, passes ``--force`` to the script,
                  which also deletes the project directory.  Required (must be True)
                  when ``dry_run`` is not True.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                  without removing anything.  When True, ``force`` is not required.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation failure
    or when force guard is triggered.
    """
    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    known_fields = list(RemoveProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    # Force guard: refuse live removal without explicit force confirmation.
    # Fires at the API layer before any subprocess is spawned.
    if not body.dry_run and not body.force:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                "remove-project requires 'force: true' when 'dry_run' is not true. "
                "Set force to true to confirm deletion, or set dry_run to true to preview."
            ),
        )

    script = _script_path(request, "remove-project.sh")
    flags: dict = {"project": body.project}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)


# ---------------------------------------------------------------------------
# POST /operations/ship-rc
# ---------------------------------------------------------------------------


@router.post(
    "/ship-rc",
    summary="Ship a release candidate (double-gated)",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from ship-rc.sh."
    ),
)
def post_ship_rc(body: ShipRcBody, request: Request) -> JSONResponse:
    """Ship a release candidate by squash-merging it directly into main.

    This is the one origin-touching verb in the operator API.  To prevent
    accidental invocation, the endpoint requires a ``confirm`` string whose
    value must equal exactly ``ship-rc <project> <key>`` (byte-for-byte —
    no trimming, no case-folding).  The confirm-gate fires at the API layer
    BEFORE ship-rc.sh is invoked: a wrong or missing confirm string returns
    422 and never reaches the script.

    Correct confirm string → thin passthrough to ship-rc.sh.

    Body fields:

    - ``project``  (required) — project name.  Passed as ``--project``.
    - ``key``      (required) — release version in format ``vX.Y.Z``.
                               Passed as ``--key``.
    - ``confirm``  (required) — must equal exactly ``ship-rc <project> <key>``
                               (byte-for-byte).  Any mismatch → 422 naming
                               ``confirm``, no subprocess spawned.
    - ``dry_run``  (optional, bool) — when True, passes ``--dry-run`` to ship-rc.sh
                               (native dry-run: the script prints the plan and exits 0
                               without performing any git operations).  ship-rc.sh has
                               native dry-run support so the flag passes through rather
                               than using the dispatch-layer short-circuit.

    Response envelope:
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    HTTP status: 200 on exit_code 0, 500 on non-zero, 422 on validation
    failure or confirm-gate mismatch.
    """
    try:
        validate_required(
            {"project": body.project, "key": body.key, "confirm": body.confirm},
            required_keys=["project", "key", "confirm"],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc

    # Confirm-gate: byte-for-byte exact match — no trimming, no case-folding.
    # Fires at the API layer before any subprocess is spawned.
    expected_confirm = f"ship-rc {body.project} {body.key}"
    if body.confirm != expected_confirm:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=(
                f"confirm mismatch: expected 'ship-rc {body.project} {body.key}', "
                f"got '{body.confirm}'. "
                "The confirm string must equal 'ship-rc <project> <key>' exactly."
            ),
        )

    known_fields = list(ShipRcBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "ship-rc.sh")
    flags: dict = {"project": body.project, "key": body.key}
    # ship-rc.sh has native dry-run support: pass --dry-run through to the script.
    # Observable contract is identical to the dispatch-layer short-circuit (exit_code=0,
    # stdout describes the plan, no git operations), satisfying the universal dry_run contract.
    if body.dry_run:
        flags["dry-run"] = True

    result = shell_out(script, flags)
    return _make_envelope(result, warnings)
