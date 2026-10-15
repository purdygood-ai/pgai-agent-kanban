"""
operations.py — POST (mutation) endpoints for the pgai-agent-kanban operator API.

Implements every mutation endpoint of the operator REST API. Routes marked
"ops:" call that ``pgai_agent_kanban.ops.write`` function in process -- the
one their CLI script reaches through ``pp_run_ops`` -- and start no
subprocess (see "Routes that call the shared ops function" below); every
other route runs its script:

  POST /operations/halt                  → ops: halt            (CLI: halt.sh --project <name>)
  POST /operations/unhalt                → ops: unhalt          (CLI: unhalt.sh --project <name>)
  POST /operations/halt-after            → ops: halt_after      (CLI: halt-after.sh --project <name>
                                                    [--key <token>] [--until vX.Y.Z])
  POST /operations/defer                 → ops: defer           (CLI: defer.sh --project <name> --until vX.Y.Z)
  POST /operations/undefer               → ops: undefer         (CLI: undefer.sh --project <name>)
  POST /operations/deep-verify           → deep-verify.sh --project <name> [--scheduled]
                                                    [--image <name|all> ...]
                                                    (backgrounded -- see "Long-running
                                                    verbs" below; the optional
                                                    ``image`` field is one --image
                                                    per entry -- see "Container
                                                    image selection" below)
  POST /operations/wake-now              → wake-now.sh --agent <role> [--bg]
                                                    OR wake-now.sh --all
  POST /operations/halt-global           → ops: halt_global     (CLI: halt-global.sh)
  POST /operations/unhalt-global         → ops: unhalt_global   (CLI: unhalt-global.sh)
  POST /operations/reset                 → ops: reset_item      (CLI: reset.sh --project <name>
                                                    --key <resolved-key> [--keep-artifacts] [--force])
  POST /operations/close                 → ops: close_item      (CLI: close.sh --project <name> --key <key>
                                                    [--state ...] [--note ...])
  POST /operations/wontdo                → ops: wontdo_item     (CLI: wontdo.sh --project <name> --key <key>)
  POST /operations/delete                → ops: delete_item     (CLI: delete.sh --project <name> --key <key>
                                                    [--force])
  POST /operations/intake                → writes content to a temp file, then
                                           ops: deposit_intake (CLI: intake.sh --project <name> <tempfile>)
  POST /operations/unwind-rc             → cm: unwind.unwind_rc (CLI: unwind-rc.sh
                                           --project <name> --key <version> --confirm [--force])
                                           NOTE: requires confirm == true (or the typed phrase
                                           "unwind-rc <project> <key>", byte-for-byte) in the
                                           body unless dry_run is true. The CLI's interactive
                                           'yes' prompt has no HTTP equivalent, so the body
                                           field IS the confirmation; a missing, false or
                                           wrong ``confirm`` refuses 422 at the API layer
                                           before anything runs. ``force`` is a separate
                                           field that bypasses ONLY the Active-RC mismatch
                                           check and never stands in for ``confirm``.
  POST /operations/set-version-ceiling   → ops: set_version_ceiling (CLI: set-version-ceiling.sh
                                                    --project <name> [--show] [--minor N]
                                                    [--major N] [--no-minor] [--no-major])
  POST /operations/switch-provider       → ops: switch_provider (CLI: switch-provider.sh --provider <name>)
  POST /operations/create-project        → ops: projects.create_project (CLI: create-project.sh
                                                    --project <name> [--workflow-type <type>]
                                                    [--max-patch N] [--max-minor N] [--max-major N]
                                                    [--git-remote <name>] [--priority <int>]
                                                    [--color '#RRGGBB'] [--no-migrate]
                                                    [--dev-tree <path>] [--git-repo <url>])
  POST /operations/add-project           → ops: projects.add_project (CLI: add-project.sh
                                                    --project <name> [--priority <int>]
                                                    [--color '#RRGGBB'] [--no-migrate])
  POST /operations/remove-project        → ops: projects.remove_project (CLI: remove-project.sh
                                                    --project <name> [--force])
                                           NOTE: the default call is the safe mode (unregister
                                           only, the directory stays). force is the destructive
                                           escalation (the directory is deleted too) and
                                           requires confirm == "remove-project <project>"
                                           (byte-for-byte) unless dry_run is true; refused 422
                                           before anything runs. The Active-RC guard holds
                                           with and without force.
  POST /operations/ship-rc               → cm: ship.ship_rc (CLI: ship-rc.sh --project <name>
                                           --key vX.Y.Z [--dry-run])
                                           NOTE: requires confirm == "ship-rc <project> <key>"
                                           in the body (byte-for-byte exact match).
                                           The confirm-gate 422 fires at the API layer BEFORE
                                           ship_rc runs — a wrong or missing confirm string
                                           never reaches it. ship-rc takes no force.
  POST /operations/cold-build-now        → cold-build-now.sh --project <name>
                                                    [--image <name|all> ...]
                                                    (backgrounded -- see "Long-running
                                                    verbs" below; the script is a named
                                                    alias for deep-verify.sh --project;
                                                    ``image`` maps as for deep-verify)
  POST /operations/cancel-rc             → cm: cancel.cancel_rc (CLI: cm/cancel-rc.sh
                                           --project <name> --key vX.Y.Z --yes [--force])
                                           NOTE: requires yes == true in the body unless
                                           dry_run is true. The script's interactive
                                           confirmation prompt has no HTTP equivalent, so
                                           the body field IS the confirmation; a missing or
                                           false ``yes`` refuses 422 at the API layer before
                                           anything runs. ``force`` is a separate field that
                                           bypasses only the Active-RC mismatch check and
                                           never stands in for ``yes``.
  POST /operations/release-locks         → ops: locks.release_lock (CLI: release-locks.sh
                                           --release <name> [--force]; the verb's mutating
                                           mode -- the read-only listing mode is
                                           GET /release-locks in reads.py, ops: locks.list_locks)
  POST /operations/archive               → archive: run.run_archive (CLI: archive.sh
                                           --project <name> [--dry-run] [--count N]
                                           [--restore <ID>] [--reconcile-queues])
                                           NOTE: dry_run does not short-circuit here. It
                                           runs the archive plan and returns the planned
                                           moves per category in stdout -- the same text
                                           archive.sh --dry-run prints -- and moves nothing.

Design:
  - All endpoints are POST only; no GET verbs on this router.
  - Body field names match the underlying script's flag names verbatim where Python
    identifiers permit; hyphenated flag names (dry-run, keep-artifacts) are accepted
    as underscore-form body fields (dry_run, keep_artifacts) and mapped to their
    hyphenated script flag names in the flags dict.
  - Script guards and refusals propagate unchanged — no re-implementation of logic.
  - intake accepts only a bare intake filename (lib.filename_contract's
    intake_destination, the check deposit_intake shares) and refuses a path at
    HTTP 422 before touching the filesystem; it then writes its content to a
    fresh per-request directory under <temp root>/intake (route_temp_root) and
    deposits that file; the dry-run description names it as the positional
    source argument of the equivalent intake.sh call.
  - unwind-rc's HALT + Active-RC pre-flight guards surface via the envelope as
    cm.unwind.unwind_rc prints them (non-zero exit_code, stderr populated).
  - ship-rc's pre-flight checks, git steps and release-hook HALTs surface the
    same way, as cm.ship.ship_rc prints them.
  - reset uses mutually-exclusive kind selectors (key, agent, bug, priority,
    requirement) — exactly one must be supplied; the resolved value is the key
    reset_item resolves, which identifies the item type from what it names.
  - defer's --until is NOT pre-validated at the API layer: an empty or malformed
    value is passed verbatim to ops.write.defer, which refuses it with the same
    dual-channel block defer.sh prints (defer.until_required /
    defer.until_malformed). Only --project gets the usual API-layer
    required-field check. This is deliberate -- the refusal's stable machine
    error key and message must reproduce byte-for-byte over HTTP, which only
    holds if the API never pre-empts the shared function's own validation.

Routes that call the shared ops function (halt, unhalt, halt-after,
halt-global, unhalt-global, defer, undefer, set-version-ceiling,
switch-provider, create-project, add-project, remove-project, release-locks,
cancel-rc, unwind-rc, ship-rc, intake, close, wontdo, delete, reset, archive):
  - The route runs the steps its CLI script runs around the ops call, in the
    script's order: resolve ``project`` through
    ``ops.resolve.resolve_project_name`` (the twin of the scripts'
    ``pp_resolve_project_name``, refusing ``project_resolve.not_found`` /
    ``.ambiguous`` at exit code 1), the script's own pre-checks (halt-after's
    ``until``/``key`` checks, the global verbs' kanban-root check), then the
    ``ops.write`` call inside ``adapter.ops_call``. ``defer`` and ``undefer``
    resolve ``project`` inside the ops function itself (after ``until`` is
    checked, the order defer.sh refuses in), so their routes make the one
    call and report every refusal at exit code 1, as the scripts do.
    ``switch-provider`` has no project; its route makes the one call and
    reports every refusal at exit code 1. ``set-version-ceiling`` resolves
    ``project`` first, then reports the function's refusals at the codes
    ``ops.write.set_version_ceiling_exit_code`` gives (2 for a project with
    no project.cfg, 1 otherwise), as the script does. ``create-project``,
    ``add-project`` and ``remove-project`` name a project that is not (or no
    longer) registered, so they resolve nothing; each makes the one
    ``ops.projects`` call and reports its refusals at the codes
    ``ops.projects.projects_exit_code`` gives (2 for an existing or missing
    project directory and for remove-project's Active-RC guard, 1
    otherwise), as the scripts do. ``release-locks`` is not project-scoped;
    its route makes the one ``ops.locks.release_lock`` call and reports its
    refusals at the codes ``ops.locks.locks_exit_code`` gives (2 ambiguous,
    3 not found, 4 live holder, 5 live holder not released), as the script
    does. Its narration reaches ``stdout`` through a callback rather than
    stream capture (``ops_call(capture=False)``), so a ``force`` call that
    waits for its holder holds no process-wide lock. ``cancel-rc`` makes the
    one ``cm.cancel.cancel_rc`` call the same way -- its stdout and stderr
    through callbacks, since its git steps (a fetch among them) may take a
    while -- and reports its refusals at the codes
    ``cm.cancel.cancel_exit_code`` gives (1 for a refused check, git's own
    code for a failed git step), as the script does. The project name is
    used as given, as cm/cancel-rc.sh uses it. ``unwind-rc`` makes the one
    ``cm.unwind.unwind_rc`` call the same way, its streams through
    callbacks, and reports its refusals at the codes
    ``cm.unwind.unwind_exit_code`` gives (1 for a refused check, 2 for a
    step that failed after the backup), as the script does; unwind_rc
    resolves the project name itself, as unwind-rc.sh's own resolution did.
    ``ship-rc`` makes the one ``cm.ship.ship_rc`` call the same way, its
    streams through callbacks (its git steps push to origin and may take a
    while), and reports every refusal, HALT and failed git step at the code
    ``cm.ship.ship_exit_code`` gives (1), as the script does; ship_rc
    resolves the project name itself.
    ``archive`` makes the one ``archive.run.run_archive`` call, its narration
    and refusals collected through callbacks (a first run on a large
    project moves many items), and reports the exit code run_archive
    returns (1 refused, 2 stopped at a failing item, 3 skipped on a held
    repo lock), as the script does; run_archive resolves the project name
    itself.
  - ``adapter.ops_call`` is the one place an ``OpsError`` becomes envelope
    material: the exit code the ops CLI uses for it
    (``ops.errors.cli_exit_code``), the function's own stderr plus the error
    message, exactly as the CLI prints them. ``_make_dual_channel_envelope``
    then builds the same envelope a script refusal produced.
  - ``stdout`` carries the narration the CLI script prints on success (set /
    already set / cleared / deposited / closed ...), and a failure adds the
    script's own failure line, so the envelope reads as the CLI's output did.
    reset.sh's extra TESTER-worktree teardown after a successful reset has
    nothing left to remove once ``reset_item`` has refused or cleaned up any
    worktree for the task, and is not repeated.
  - The dry-run short-circuit is unchanged: it describes the equivalent CLI
    call and runs nothing.

Long-running verbs (deep-verify, cold-build-now, wake-now):
  - cold-build-now.sh is a named alias that execs deep-verify.sh --project NAME,
    so POST /operations/cold-build-now takes exactly deep-verify's detached
    path below: it returns as soon as the child starts, never with a verdict.
  - deep-verify.sh itself blocks for the duration of the cold-build lane it runs
    (budget: ~13 minutes). An HTTP request must not block that long, so POST /operations/deep-verify
    launches the script detached (subprocess.Popen, not shell_out) and returns
    immediately with a narration that a verdict is NOT yet available -- the
    caller polls the project's deep-verify ledger (or GET /verdicts) for the
    verdict once the backgrounded run completes. dry_run short-circuits exactly
    like every other operation (no subprocess spawned at all).
  - wake-now.sh's --bg/--all modes need the SAME detached treatment as
    deep-verify, for a subtler reason: fire_one_bg() backgrounds the fired
    agent's wake cycle with a bare `&` WITHOUT redirecting its stdout/stderr,
    so the backgrounded grandchild inherits shell_out()'s own capture pipes.
    subprocess.run(capture_output=True) does not return until BOTH ends of
    those pipes close -- and the backgrounded grandchild keeps its end open
    for as long as the fired agent's entire wake cycle takes, even after
    wake-now.sh itself has exited. Naively calling shell_out() on `--bg`/
    `--all` would therefore silently block for the many minutes a wake
    cycle takes, despite wake-now.sh's own PID line implying
    an instant return. POST /operations/wake-now avoids this by launching
    wake-now.sh itself detached (subprocess.Popen, output to a log file
    instead of a pipe) whenever `bg` or `all` is set, and returns as soon as
    the fork completes -- the same "confirm the fork started, do not wait for
    it" contract as deep-verify. Only the plain foreground form (neither
    `bg` nor `all`) uses shell_out(): wake-now.sh execs wake-batch.sh in that
    mode with no background job and no pipe-inheritance hazard, so blocking
    there is a faithful, non-gentler mirror of the CLI's own foreground
    contract -- it blocks for as long as the fired agent's wake cycle takes,
    exactly as running the bare CLI command would.

Container image selection (deep-verify, cold-build-now):
  - The optional ``image`` body field is the REST form of the container-tier
    ``--image`` flag: a list of image names, or the string ``"all"``. Each
    entry becomes one ``--image <entry>`` pair on the dispatched script's
    argv, in order (an entry may itself be a comma-separated list, as on the
    CLI). Names come from ``config/container-images.txt`` -- the manifest
    ``scripts/lib/container_tier.sh`` reads, beside this server's
    ``scripts/`` directory.
  - The selection is checked against that manifest BEFORE dispatch, with the
    harness's own vocabulary (``pgai_agent_kanban.lib.container_images``): an
    undeclared name is refused 422 with the harness's machine key
    ``container_tier.unknown_image`` and nothing is spawned, on a real call
    and on a ``dry_run`` alike. A detached child's own refusal would land in
    a log file no HTTP caller reads, so the check runs one layer earlier.
  - Omitting ``image`` (or sending an empty list) passes no ``--image``: the
    script falls back to ``kanban.cfg [release] container_images``, and with
    that unset it lists the available images and builds nothing.
  - No body field grants the container tier's operator authorization: the
    server's own environment must carry ``PGAI_CONTAINER_TIER=1``, and
    ``--operator-approved`` is never placed on the argv.

Dual-channel refusal envelope (every action route):
  Every route in this router returns the standard envelope on success and,
  on a refused call, the same envelope EXTENDED with the dual-channel triple
  the script (or, on an ops route, the same refusal the script would print)
  carries in stderr, lifted out as top-level JSON keys:
    {"exit_code": int, "stdout": str, "stderr": str,
     "error": str, "message": str, "remedy": str, "warnings": list}
  ``error``/``message``/``remedy`` are parsed from the script's own
  error:/message:/remedy: house-shape block when the script emitted one
  (halt-after.sh, release-locks.sh, defer.sh and every script that resolves
  --project through the shared resolver do); when a script refuses with
  plain stderr text (cm.cancel.cancel_rc, cm.unwind.unwind_rc, cm.ship.ship_rc and the
  registry scripts do), the text becomes ``message`` under a stable
  per-route fallback key ``<verb>.refused`` with the runnable CLI form as
  ``remedy``, so the three keys are always present on a refusal -- never
  invented, always sourced from what the script actually printed.
  API-layer refusals (a missing required field, a project/key/selector
  field that is not one plain name, reset's exactly-one selector rule,
  remove-project's and unwind-rc's confirm gates, ship-rc's confirm gate,
  cancel-rc's confirmation, deep-verify's and cold-build-now's undeclared
  ``image``) carry the same three keys at HTTP 422 through
  ``_refusal_response`` -- never the framework's ``{"detail": [...]}``
  list -- so a client reads one refusal shape whichever layer refused. The
  census names this shape "DualChannelShellEnvelope"
  (team/config/operator_verbs.manifest), the schema every action row
  declares.

Request-field confinement (every action route):
  Every ``project`` and ``key`` body field, and the selector fields that
  name one item (reset's ``agent``/``bug``/``priority``/``requirement``,
  archive's ``restore``, release-locks' ``release``, wake-now's ``agent``),
  passes ``validation.require_component`` right after the route's
  required-field check -- before the dry-run description, project
  resolution, any path, glob, sentinel write or dispatch. A value that is
  not one plain name (``..``, a path separator, NUL or another control
  character such as a newline, a glob character ``* ? [ ]``, a leading
  ``-``) is refused at HTTP 422 by ``_component_refusal`` under
  ``<verb>.<field>_invalid`` (``remove_project.project_invalid``,
  ``halt_after.key_invalid``), naming the field, with a runnable curl
  remedy. An absent optional field is not checked.
  create-project and add-project then apply the registry verbs' own value
  checks (``ops.projects.check_create_values`` / ``check_project_name``,
  the same functions the scripts reach through the ops layer) before the
  dry-run description: a ``workflow_type`` that is not one plain name, a
  ``git_remote``, ``dev_tree`` or ``git_repo`` holding a newline or other
  control character, a relative or ``..``-containing ``dev_tree``, or an
  add-project name a registry section cannot hold, is refused at HTTP 422
  by ``_project_value_refusal`` under ``<verb>.<field>_invalid``
  (``create_project.dev_tree_invalid``), naming the field.

Envelope format (from adapter.py):
  {"exit_code": int, "stdout": str, "stderr": str}

HTTP status mapping:
  exit_code == 0  → 200 OK
  non-zero        → 500 Internal Server Error (body carries error/message/remedy)
  validation fail → 422 Unprocessable Entity (body carries error/message/remedy)

Security note: no authentication or TLS in this release.  Loopback-only binding
is the sole access-control mechanism.
"""

from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import uuid
from typing import Optional, Union

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..dependencies import route_temp_root
from ..adapter import (
    HTTP_UNPROCESSABLE_ENTITY,
    OpsOutcome,
    ShellResult,
    ValidationError,
    build_argv,
    ops_call,
    result_http_status,
    shell_out,
    validate_required,
)
from ...archive import run as archive_run
from ...halt_after.token import describe_token
from ...lib import container_images, semver
from ...cm import cancel as cm_cancel
from ...cm import ship as cm_ship
from ...cm import unwind as cm_unwind
from ...lib.error_contract import format_error_contract, parse_error_contract
from ...lib.filename_contract import (
    INTAKE_FILENAME_NOT_BARE,
    IntakeFilenameRefused,
    intake_destination,
)
from ...ops import locks as ops_locks
from ...ops import projects as ops_projects
from ...ops import resolve as ops_resolve
from ...ops import write as ops_write
from ...ops.context import OpsContext
from ...ops.errors import IoError, Refused
from ..validation import ComponentRefused, require_component

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
        A JSONResponse with the HTTP status ``result_http_status`` maps the
        result to (a refusal's own error key, when its stderr carries one,
        selects 404 / 409 / 422) and the envelope body.
    """
    triple = parse_error_contract(result.stderr) if result.exit_code else None
    return JSONResponse(
        content={
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "warnings": warnings if warnings is not None else [],
        },
        status_code=result_http_status(result, (triple or {}).get("error", "")),
    )


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

    This is the ONE implementation of the dry_run short-circuit for every
    operation route in this module.  When dry_run is True, no subprocess is spawned; the
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
# Backgrounded dispatch (deep-verify, and wake-now's --bg/--all modes -- see
# module docstring's "Long-running verbs" note for why this exists alongside
# shell_out()).
# ---------------------------------------------------------------------------


def _spawn_detached(argv: list[str], log_path: pathlib.Path) -> int:
    """Launch argv detached from the request and return its PID immediately.

    Unlike adapter.shell_out() (which always blocks until the child exits),
    this starts the process with its own session (start_new_session=True, the
    Python equivalent of a double-forked daemon) and returns as soon as the
    fork completes -- the caller never waits on the child. Combined stdout+
    stderr are redirected to log_path so the run's own narration (including,
    for a failed-to-build verdict, the bug it files) is not silently
    discarded just because no HTTP client stayed around to read it. The
    child's stdin is /dev/null, as with shell_out(): a detached script that
    prompts gets EOF and exits rather than sitting on the server's stdin.

    Args:
        argv:     The full argv to execute (script path first).
        log_path: File the child's combined stdout+stderr is written to.
                  Parent directories are created if missing.

    Returns:
        The child process's PID.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(  # noqa: S603 -- argv is server-built, never client-supplied text
            argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


# ---------------------------------------------------------------------------
# Dual-channel refusal envelope (see the module docstring's "Dual-channel
# refusal envelope" note). Two helpers: one for a refusal the API layer
# itself raises before any subprocess runs, one that wraps a completed
# ShellResult and lifts the script's own refusal out of stderr.
# ---------------------------------------------------------------------------


def _refusal_response(
    status_code: int,
    error: str,
    message: str,
    remedy: str,
    warnings: list[str],
) -> JSONResponse:
    """Return an API-layer refusal as a dual-channel JSON body.

    Used where a route refuses BEFORE spawning its script -- a required
    body field is missing, or a confirmation the script would otherwise ask
    for interactively was not given. The body carries the same three keys a
    script-side refusal carries (``error``/``message``/``remedy``) so a
    client parses one refusal shape whichever layer refused; ``exit_code``/
    ``stdout``/``stderr`` are absent because no process ran.

    Args:
        status_code: The HTTP status (422 for every current caller -- the
                     same status the existing action routes use for a
                     validation refusal).
        error:       Stable machine key a client can match on.
        message:     Plain-language statement of what was refused and why.
        remedy:      What to send instead, runnable as printed.
        warnings:    Unknown-body-field warnings already accumulated.
    """
    return JSONResponse(
        content={
            "error": error,
            "message": message,
            "remedy": remedy,
            "warnings": warnings,
        },
        status_code=status_code,
    )


def _make_dual_channel_envelope(
    result: ShellResult,
    warnings: list[str],
    *,
    fallback_error: str,
    fallback_remedy: str,
) -> JSONResponse:
    """Convert a ShellResult into the envelope, adding the refusal triple on a non-zero exit.

    On ``exit_code == 0`` this is byte-for-byte ``_make_envelope``. On a
    non-zero exit the envelope gains top-level ``error``/``message``/
    ``remedy`` keys: the script's own house-shape block, when its stderr
    carries one (``parse_error_contract``), is lifted verbatim; otherwise
    the script's plain stderr text (or stdout, when stderr is empty) becomes
    ``message`` under *fallback_error*, with *fallback_remedy* as the next
    step. Nothing the script printed is dropped -- ``stdout``/``stderr`` are
    still returned in full -- and nothing is re-derived: the triple is
    always what the script said, only re-shaped.

    Args:
        result:          The completed ShellResult.
        warnings:        Warning strings for this response.
        fallback_error:  Stable machine key used only when the script's
                         stderr carries no error:/message:/remedy: block.
        fallback_remedy: Runnable next step used only in that same case.

    Returns:
        A JSONResponse: 200 with the standard four-key envelope on success;
        on a non-zero exit the seven-key refusal envelope with the status
        ``result_http_status`` maps it to -- 404 for something that does not
        exist, 409 for a conflicting state, 422 for an invalid value, 504
        for a shell-out timeout, 500 otherwise.
    """
    if result.exit_code == 0:
        return _make_envelope(result, warnings)

    triple = parse_error_contract(result.stderr)
    if triple is None:
        plain = result.stderr.strip() or result.stdout.strip()
        triple = {
            "error": fallback_error,
            "message": plain or f"script exited {result.exit_code} without printing a reason.",
            "remedy": fallback_remedy,
        }
    return JSONResponse(
        content={
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": triple["error"],
            "message": triple["message"],
            "remedy": triple["remedy"],
            "warnings": warnings,
        },
        status_code=result_http_status(result, triple["error"]),
    )


def _cli_remedy(script_rel: str, planned_argv: list[str]) -> str:
    """Return the runnable CLI form of *planned_argv* as a fallback remedy.

    Used by every ``_make_dual_channel_envelope`` caller for a script that
    refused with plain stderr text and so printed no remedy of its own. The
    command is re-rendered from the argv the route actually built --
    ``team/scripts/`` plus the script's census-relative path
    (``halt.sh``, ``cm/cancel-rc.sh``), then every flag and value the caller
    sent, shell-quoted -- so it is pasteable exactly as printed, with no
    placeholder.
    """
    return (
        "Fix the condition the message names, then re-run the CLI form: "
        + shlex.join([f"team/scripts/{script_rel}", *planned_argv[1:]])
    )


def _missing_field_refusal(
    request: Request,
    verb: str,
    exc: ValidationError,
    example_body: str,
    warnings: list[str],
) -> JSONResponse:
    """Refuse a body whose required field is missing or empty, dual-channel.

    The one place every action route's required-field check lands, so a
    client reads one refusal shape whichever route refused. *verb* is the
    census verb name (``halt``, ``set-version-ceiling``); the machine key is
    ``<verb_snake>.<field>_required`` -- ``halt.project_required``,
    ``ship_rc.confirm_required`` -- with the field taken from the
    ``ValidationError`` ``validate_required`` raised. *example_body* is a
    literal JSON body this route would accept, rendered into a runnable
    curl against the request's own URL. No subprocess has run when this
    is returned.
    """
    verb_key = verb.replace("-", "_")
    if exc.key:
        error = f"{verb_key}.{exc.key}_required"
        message = (
            f"{verb} requires '{exc.key}', which the body did not carry or left "
            "empty; nothing was run."
        )
    else:
        error = f"{verb_key}.required_field_missing"
        message = f"{verb} refused the body before running anything: {exc.detail}"
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # the script would refuse the same absent argument itself, so this
    # refuses one layer earlier, before any subprocess is spawned, in the
    # same three-key shape the script's own refusal would carry.
    # CENSUS-ANCHOR: operations-required-field-missing-01
    return _refusal_response(
        HTTP_UNPROCESSABLE_ENTITY,
        error,
        message,
        _example_curl(request, example_body),
        warnings,
    )


def _component_refusal(
    request: Request,
    verb: str,
    fields: dict[str, object],
    example_body: str,
    warnings: list[str],
) -> JSONResponse | None:
    """Refuse a body whose project, key or selector field is not one plain name.

    The one place every action route's confinement check lands (see
    ``validation.require_component`` for what is refused and why). Each
    entry of *fields* maps a body field name to the value the body carried;
    an absent value (``None`` or ``""``) is skipped, because whether a field
    is required is the route's own ``validate_required`` check, which runs
    first. The first refused field returns HTTP 422 with the dual-channel
    triple ``<verb_snake>.<field>_invalid`` -- ``remove_project.project_invalid``,
    ``close.key_invalid`` -- a message naming the field and what is wrong
    with it, and a runnable curl remedy re-sending *example_body*. Called
    before the dry-run short-circuit, so a refused value is never described,
    resolved, joined into a path, written or dispatched.

    Returns:
        None when every supplied field is a plain name; otherwise the 422
        refusal response.
    """
    for field, value in fields.items():
        if value is None or value == "":
            continue
        try:
            require_component(value, field)
        except ComponentRefused as exc:
            triple = exc.contract(verb, _example_curl(request, example_body))
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the field becomes a path component, a glob prefix, a
            # sentinel line or an argv value, so a value that is not one
            # plain name could reach outside the kanban root or change what
            # runs. Refused here, dual-channel, before anything acts on it.
            # CENSUS-ANCHOR: operations-field-not-component-01
            return _refusal_response(
                HTTP_UNPROCESSABLE_ENTITY,
                triple["error"],
                triple["message"],
                triple["remedy"],
                warnings,
            )
    return None


# Example values the registry routes' value refusals re-send, per field.
_PROJECT_VALUE_EXAMPLES = {
    "project": "my-app",
    "workflow_type": "release",
    "git_remote": "origin",
    "dev_tree": "/srv/develop/my-app",
    "git_repo": "git@example.com:org/my-app.git",
}


def _project_value_refusal(
    request: Request,
    exc: "ops_projects.ProjectValueRefused",
    warnings: list[str],
) -> JSONResponse:
    """Return a registry verb's value refusal as HTTP 422, dual-channel.

    *exc* is what ``ops.projects.check_create_values`` or
    ``check_project_name`` raised -- the same refusal the script prints --
    so ``error`` (``create_project.dev_tree_invalid``,
    ``add_project.project_invalid``) and ``message`` (naming the field) are
    the ops layer's own; the remedy is a runnable curl re-sending the
    request with an example value for that field and ``dry_run``. Returned
    before the dry-run description, so nothing was read, written or run.
    """
    triple = exc.contract
    example = {"project": "my-app", "dry_run": True}
    example[exc.field] = _PROJECT_VALUE_EXAMPLES.get(exc.field, "my-app")
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # the value is about to become a path, a plugin lookup or a project.cfg
    # line, and one that could reach outside the kanban root or inject a
    # config key is refused here, dual-channel, before anything acts on it.
    # CENSUS-ANCHOR: operations-project-value-invalid-01
    return _refusal_response(
        HTTP_UNPROCESSABLE_ENTITY,
        triple["error"],
        triple["message"],
        _example_curl(request, json.dumps(example)),
        warnings,
    )


# ---------------------------------------------------------------------------
# Direct ops calls (the routes whose verb's CLI script reaches an
# ops.write function through pp_run_ops -- see the module docstring's
# "Routes that call the shared ops function" note).
# ---------------------------------------------------------------------------


def _ops_context(request: Request) -> OpsContext:
    """Return the OpsContext for the kanban root this server was started on."""
    return OpsContext(kanban_root=request.app.state.api_cfg.kanban_root)


def _resolve_project(ctx: OpsContext, typed: str) -> tuple[Optional[str], OpsOutcome]:
    """Resolve the body's ``project`` the way the CLI script's first step does.

    Returns ``(name, outcome)``: the registered project name and a clean
    outcome on success, or ``(None, outcome)`` carrying the resolver's
    ``project_resolve.not_found`` / ``project_resolve.ambiguous`` block in
    ``stderr`` at exit code 1 -- byte-for-byte what the script prints and
    exits with when ``pp_resolve_project_name`` refuses.
    """
    with ops_call(exit_code=1) as outcome:
        return ops_resolve.resolve_project_name(ctx, typed), outcome
    return None, outcome


def _contract_refusal(error: str, message: str, remedy: str) -> ShellResult:
    """Return a refusal shaped like a CLI script's ``pgai_emit_error_contract; exit 1``.

    Used where a CLI wrapper refuses on its own before calling the ops
    function; the route reproduces that refusal so an HTTP caller reads the
    same exit code, stderr block, and lifted ``error``/``message``/
    ``remedy`` the script produces.
    """
    return ShellResult(
        exit_code=1,
        stdout="",
        stderr=format_error_contract(error, message, remedy) + "\n",
    )


def _example_curl(request: Request, example_body: str) -> str:
    """Return a runnable curl line re-sending *example_body* to this route.

    Builds the remedy for an API-layer refusal from the request's own URL,
    so the printed command targets the server the caller actually reached
    (never a placeholder host). *example_body* is a literal JSON string the
    caller can paste as-is.
    """
    return (
        f"curl -sS -X POST {request.url} -H 'Content-Type: application/json' "
        f"-d '{example_body}'"
    )


def _routable_intake_example_curl(request: Request, project: str) -> str:
    """Return a runnable curl line re-sending an intake request under a routable name.

    The remedy for every intake refusal that is about the filename itself
    (a path, or a bare name no routing prefix matches): the same route, the
    caller's *project*, and the example name ``BUG-0001-example.md``, which
    routes to ``bugs/``. Nothing in it names a temp file, so it is runnable
    whether or not the refused request wrote anything.
    """
    return _example_curl(
        request,
        json.dumps({
            "project": project,
            "filename": "BUG-0001-example.md",
            "content": "# BUG-0001 example",
        }),
    )


# ---------------------------------------------------------------------------
# Container image selection (deep-verify, cold-build-now -- see the module
# docstring's "Container image selection" note).
# ---------------------------------------------------------------------------


def _image_entries(image: Union[list[str], str, None]) -> list[str]:
    """Return the body's ``image`` value as a list of ``--image`` entries.

    ``None`` (field omitted) and ``[]`` give no entries; a bare string --
    ``"all"``, one name, or a comma-separated list -- is one entry, exactly
    as one ``--image`` value is on the CLI.
    """
    if image is None:
        return []
    if isinstance(image, str):
        return [image]
    return list(image)


def _image_argv(entries: list[str]) -> list[str]:
    """Return one ``--image <entry>`` pair per entry, in order."""
    argv: list[str] = []
    for entry in entries:
        argv.extend(["--image", entry])
    return argv


def _unknown_image_refusal(
    request: Request,
    script_name: str,
    entries: list[str],
    example_body: dict,
    warnings: list[str],
) -> JSONResponse | None:
    """Refuse an ``image`` selection the manifest does not declare, or return None.

    Checks *entries* against ``config/container-images.txt`` beside this
    server's ``scripts/`` directory -- the file the dispatched script's
    ``container_tier_select_images`` reads -- with the same vocabulary
    (``pgai_agent_kanban.lib.container_images``). Returns None when every
    name is declared or ``all`` (or there are no entries), and otherwise a
    422 refusal carrying the harness's own machine key. No subprocess has
    run when this returns.

    Args:
        request:      The incoming request (locates the manifest and builds
                      the curl remedy).
        script_name:  The script the route would dispatch, for the message.
        entries:      The ``--image`` entries the body asked for.
        example_body: The caller's body with ``image`` set to ``"all"`` --
                      the corrected request the remedy re-sends.
        warnings:     Unknown-body-field warnings already accumulated.
    """
    if not entries:
        return None
    manifest = container_images.manifest_path(_scripts_dir(request).parent)
    declared = container_images.declared_container_images(manifest)
    unknown = container_images.undeclared_image_names(entries, declared)
    if not unknown:
        return None
    named = " ".join(f"'{name}'" for name in unknown)
    declared_list = " ".join(declared) if declared else "(none declared)"
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design,
    # the same one container_tier.sh's container-tier-unknown-image-01
    # enforces -- the manifest is the one declared list of images the
    # container tier builds, so an undeclared name is never dispatched.
    # Refused here, dual-channel, because a detached child's own refusal
    # would reach only a log file the HTTP caller never reads.
    # CENSUS-ANCHOR: operations-container-image-unknown-01
    return _refusal_response(
        HTTP_UNPROCESSABLE_ENTITY,
        "container_tier.unknown_image",
        f"{script_name} refused: the 'image' field names {named}, which "
        f"team/config/container-images.txt does not declare. Declared "
        f"images: {declared_list}. Nothing was built, pulled or started, "
        "and no process was spawned.",
        _example_curl(request, json.dumps(example_body)),
        warnings,
    )


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------


class HaltBody(BaseModel):
    """Body for POST /operations/halt and POST /operations/unhalt.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    dry_run: bool = False


class HaltAfterBody(BaseModel):
    """Body for POST /operations/halt-after.

    Mirrors halt-after.sh's flags (``project``, ``key``, ``until``) plus the
    universal ``dry_run``; the CLI's minimal form ``--project P --until vX.Y.Z``
    is therefore the body ``{"project": P, "until": "vX.Y.Z"}``.

    Fields:
        project: Project name (or unambiguous prefix) to arm with the
                 HALT-AFTER signal. Required, as by the script; a body
                 without it is refused dual-channel before anything runs.
        key:     Optional drain event token (``rc``, ``pm``, ``coder``,
                 ``writer``, ``tester``, ``cm``), armed verbatim when given;
                 when absent the CLI's own default, ``rc``, is armed.
        until:   Optional release boundary (e.g. ``v2.20.1``, leading ``v``
                 optional); arms the versioned ``rc:vX.Y.Z`` token directly.
                 rc scope only — refused, as halt-after.sh refuses, when
                 combined with a non-rc ``key`` or when not ``vX.Y.Z``.
        dry_run: When True, short-circuits dispatch — nothing is armed.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    until: str = ""
    dry_run: bool = False


class DeferBody(BaseModel):
    """Body for POST /operations/defer.

    Fields:
        project: Project name to arm the DEFER-LOW-BUGS campaign for. Required
                 at the API layer -- a missing project is rejected with a
                 generic 422 before anything runs, matching the halt/unhalt
                 convention.
        until:   Release boundary the campaign runs until (e.g. ``v2.20.0``).
                 Passed verbatim to ``ops.write.defer``, the function
                 defer.sh reaches. Deliberately NOT validated at the API
                 layer: an empty or malformed value is passed as-is, and the
                 function's dual-channel refusal (``defer.until_required`` /
                 ``defer.until_malformed``) lands in the envelope exactly as
                 defer.sh prints it.
        dry_run: When True, short-circuits dispatch — nothing is armed.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    until: str = ""
    dry_run: bool = False


class UndeferBody(BaseModel):
    """Body for POST /operations/undefer.

    Fields:
        project: Project name to clear the DEFER-LOW-BUGS campaign for.
        dry_run: When True, short-circuits dispatch — nothing is removed.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    dry_run: bool = False


class DeepVerifyBody(BaseModel):
    """Body for POST /operations/deep-verify.

    See the module docstring's "Long-running verbs" note: a real (non-dry-run)
    call launches deep-verify.sh detached and returns immediately -- the
    response never carries a verdict, only confirmation that the run started.

    Fields:
        project:   Project whose ledger the run is filed under. Required
                   unless ``scheduled`` is True with no project supplied (the
                   scheduled fan-out mode, which evaluates every active
                   registered project) -- the same conditional requirement
                   deep-verify.sh itself enforces.
        scheduled: When True, passed as ``--scheduled`` -- the weekly,
                   off-hours dispatch mode. Self-gates on the project's
                   deep-verify ledger age and off-hours window; most calls
                   through this endpoint should leave this False (on-demand
                   mode, matching cold-build-now.sh's own single-purpose
                   contract).
        image:     Optional container image selection for this call -- a
                   list of names from team/config/container-images.txt, or
                   the string ``"all"``. Each entry is passed as one
                   ``--image <entry>``; an undeclared name is refused 422
                   (``container_tier.unknown_image``) before dispatch.
                   Omitted: no ``--image``, so kanban.cfg [release]
                   container_images applies, else nothing is built. Never
                   grants operator authorization.
        dry_run:   When True, short-circuits dispatch — no subprocess spawned
                   and no detached process is started.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    scheduled: bool = False
    image: Optional[Union[list[str], str]] = None
    dry_run: bool = False


class WakeNowBody(BaseModel):
    """Body for POST /operations/wake-now.

    See the module docstring's "Long-running verbs" note: ``bg``/``all`` are
    launched detached (like deep-verify), NOT via the blocking shell_out()
    dispatch every other endpoint uses -- wake-now.sh backgrounds the fired
    agent with a bare ``&`` that inherits shell_out()'s capture pipes rather
    than closing them, so a naive shell_out() call would block for the fired
    agent's entire wake cycle despite wake-now.sh's own near-instant exit.

    Fields:
        agent:   Agent role to wake (``pm``, ``coder``, ``cm``, ``writer``,
                 ``tester``, ``overwatch``). Passed verbatim as ``--agent``.
                 Mutually exclusive with ``all`` -- wake-now.sh itself refuses
                 dual-channel when both are supplied; that refusal propagates
                 unchanged rather than being re-implemented here (foreground
                 mode only -- see ``all`` below for the detached modes).
        all:     When True, passed as ``--all`` -- fires every pipeline agent
                 (pm, coder, cm, writer, tester). Dispatched detached; the
                 response confirms wake-now.sh started, not that any fired
                 agent finished.
        bg:      When True, passed as ``--bg`` -- backgrounds the single named
                 agent's wake cycle. Dispatched detached, same as ``all``.
        dry_run: When True, short-circuits dispatch — no subprocess spawned.
                 Checked before ``bg``/``all`` route to the detached path.
    """

    model_config = ConfigDict(extra="allow")

    agent: Optional[str] = None
    all: bool = False
    bg: bool = False
    dry_run: bool = False


class HaltGlobalBody(BaseModel):
    """Body for POST /operations/halt-global and POST /operations/unhalt-global.

    These operations take no project or key arguments.  The body is accepted but
    ignored, allowing callers to POST an empty JSON object ``{}`` or omit the body.

    Fields:
        dry_run: When True, short-circuits dispatch — the global HALT is not touched.
    """

    model_config = ConfigDict(extra="allow")

    dry_run: bool = False


class ResetBody(BaseModel):
    """Body for POST /operations/reset.

    Exactly one of the kind selectors (``key``, ``agent``, ``bug``, ``priority``,
    ``requirement``) must be supplied.  Supplying two or more returns a 422 that
    names all conflicting fields.  Supplying none also returns a 422.

    The resolved selector value is the key the reset resolves, exactly as
    reset.sh's ``--key``; the item type follows from what it names (agent tasks
    are ``ROLE-YYYYMMDD-NNN`` prefixed; bug intake is ``BUG-*``; priority intake
    is ``PRIORITY-*``; requirement intake is a version string such as ``v0.1.2``).

    Fields:
        project:        Project name.
        key:            Task key or intake identifier (kind-agnostic selector).
        agent:          Agent task ID (e.g. ``CODER-20260101-001-slug``).
        bug:            Bug intake key (e.g. ``BUG-0042``).
        priority:       Priority intake key (e.g. ``PRIORITY-0007``).
        requirement:    Requirement intake version (e.g. ``v0.1.2``).
        keep_artifacts: When True, artifacts/ is kept (reset.sh's ``--keep-artifacts``).
        force:          When True, stale-worktree cleanup (git worktree remove,
                        prune, rm -rf) runs before the normal amnesia reset
                        (reset.sh's ``--force``).  Without force, a detected
                        stale worktree is refused with a non-zero exit and the
                        removal recipe in stderr.  Agent-task resets only.
        dry_run:        When True, short-circuits dispatch — nothing is reset.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    agent: Optional[str] = None
    bug: Optional[str] = None
    priority: Optional[str] = None
    requirement: Optional[str] = None
    keep_artifacts: Optional[bool] = None
    force: Optional[bool] = None
    dry_run: bool = False


class CloseBody(BaseModel):
    """Body for POST /operations/close.

    Fields:
        project: Project name.
        key:     Task key or intake identifier to close.
        state:   Close state override (``done`` or ``wont-do``; default
                 ``done``), as close.sh's ``--state``; intake-only.
        note:    Optional note text, as close.sh's ``--note``; recorded in
                 an intake item's ``## Close Note``.
        dry_run: When True, short-circuits dispatch — nothing is closed.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    state: Optional[str] = None
    note: Optional[str] = None
    dry_run: bool = False


class WontdoBody(BaseModel):
    """Body for POST /operations/wontdo.

    Fields:
        project: Project name.
        key:     Task ID to mark WONT-DO (folder name under tasks/).
        dry_run: When True, short-circuits dispatch — nothing is changed.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    dry_run: bool = False


class DeleteBody(BaseModel):
    """Body for POST /operations/delete.

    Fields:
        project: Project name.
        key:     Item key to delete (task folder name or intake file base name).
        force:   When True, overrides the terminal-state guard (delete.sh's ``--force``).
        dry_run: When True, short-circuits dispatch — nothing is deleted.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    force: Optional[bool] = None
    dry_run: bool = False


class IntakeBody(BaseModel):
    """Body for POST /operations/intake.

    Fields:
        project:  Project name.
        filename: Name for the intake file (used as the temp file name; must follow
                  the intake routing convention: BUG-*, PRIORITY-*, or v[0-9]*.md).
        content:  Text content written into the temp file that is deposited.
        dry_run:  When True, short-circuits dispatch — no temp file is written and
                  nothing is deposited.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    filename: Optional[str] = None
    content: Optional[str] = None
    dry_run: bool = False


class UnwindRcBody(BaseModel):
    """Body for POST /operations/unwind-rc.

    Mirrors unwind-rc.sh's flags, with ``confirm`` carrying the answer to the
    CLI's interactive prompt and ``force`` carrying only the guard bypass --
    two fields, two meanings, never conflated.

    Fields:
        project: Project name (exact, or an unambiguous prefix of a
                 registered name, as the CLI's ``--project``).
        key:     RC version to unwind (e.g. ``v1.2.0``).
        confirm: The confirmation (required unless ``dry_run`` is True):
                 ``true``, or the typed phrase ``unwind-rc <project> <key>``
                 byte-for-byte (no trimming, no case-folding). The CLI asks
                 "Type 'yes' to continue" interactively; an HTTP caller has
                 no terminal to answer, so this field is that answer -- the
                 CLI's ``--confirm``. It bypasses no check. A missing,
                 ``false`` or empty value, or any other string, is refused
                 422 before anything runs.
        force:   When True, bypasses the Active-RC version-mismatch check
                 and nothing else (the CLI's ``--force``). It never stands in
                 for ``confirm``, so a guard-skipping call is always an
                 explicitly confirmed one. Does NOT bypass HALT,
                 project-existence, version-format, or shipped-tag checks.
        dry_run: When True, short-circuits at this layer -- nothing runs; the
                 envelope describes the CLI call a live request is
                 equivalent to. ``confirm`` is not required for a preview.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    confirm: Optional[Union[bool, str]] = None
    dry_run: bool = False
    force: Optional[bool] = None


class SetVersionCeilingBody(BaseModel):
    """Body for POST /operations/set-version-ceiling.

    Mirrors the operator flags of set-version-ceiling.sh verbatim
    (body fields use underscores; script flags use hyphens); the values go to
    ``ops.write.set_version_ceiling`` as the script passes them.

    Fields:
        project:  Project name (required).
        show:     When True, passes ``--show`` to print current ceiling values.
        minor:    Non-negative integer; sets ``max_minor`` in project.cfg.
                  Passed as ``--minor N`` to the script.
        major:    Non-negative integer; sets ``max_major`` in project.cfg.
                  Passed as ``--major N`` to the script.
        no_minor: When True, passes ``--no-minor`` to write ``max_minor = none``
                  (no minor ceiling).
        no_major: When True, passes ``--no-major`` to write ``max_major = none``
                  (no major ceiling).
        dry_run:  When True, short-circuits dispatch — nothing is written.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
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
                  Passed verbatim to ``ops.write.switch_provider``, the
                  function switch-provider.sh reaches, which validates it;
                  this layer only checks that it is present.
        dry_run:  When True, short-circuits dispatch — nothing is written.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    provider: Optional[str] = None
    dry_run: bool = False


class CreateProjectBody(BaseModel):
    """Body for POST /operations/create-project.

    Mirrors every operator flag accepted by create-project.sh verbatim
    (body fields use underscores; script flags use hyphens where applicable);
    the values go to ``ops.projects.create_project`` as the script passes them.

    Note on path flags: ``dev_tree`` (``--dev-tree``) and ``git_repo`` (``--git-repo``)
    are written verbatim into the generated project.cfg's ``dev_tree_path``/
    ``git_repo_url`` fields when supplied. Neither may be empty or hold a
    newline or other control character (nor may ``git_remote``), and
    ``dev_tree`` must be an absolute path with no ``..`` segment; each is
    refused 422 naming the field otherwise. A ``dev_tree`` path that does
    not currently exist on the host produces a WARN in the envelope's
    stderr but the value is still written and the call still exits 0.

    Fields:
        project:       Project name (required).
        workflow_type: Workflow type override (``release`` or ``document``; default: ``release``).
                       Passed as ``--workflow-type``. Must be one plain name,
                       matched by name against the ready workflow plugins.
        max_patch:     max_patch ceiling override.  Passed as ``--max-patch N``.
        max_minor:     max_minor ceiling override.  Passed as ``--max-minor N``.
        max_major:     max_major ceiling override.  Passed as ``--max-major N``.
        git_remote:    git_remote_name override.  Passed as ``--git-remote <name>``.
        priority:      Registry priority integer.  Passed as ``--priority <int>``.
        color:         Registry display color (e.g. ``'#RRGGBB'``).  Passed as ``--color``.
        no_migrate:    When True, suppresses projects.cfg auto-migration.
                       Passed as ``--no-migrate``.
        dry_run:       When True, short-circuits dispatch — nothing is written.
        dev_tree:      Written verbatim into project.cfg's dev_tree_path.  Passed as
                       ``--dev-tree`` if supplied; a nonexistent path WARNs but still writes.
        git_repo:      Written verbatim into project.cfg's git_repo_url.  Passed as
                       ``--git-repo`` if supplied.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
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
    (body fields use underscores; script flags use hyphens where applicable);
    the values go to ``ops.projects.add_project`` as the script passes them.

    Fields:
        project:    Project name (required).  The project directory must already
                    exist under ``$KANBAN_ROOT/projects/<name>/``; add-project.sh
                    only updates the registry, not the directory.
        priority:   Registry priority integer.  Passed as ``--priority <int>``.
        color:      Registry display color (e.g. ``'#RRGGBB'``).  Passed as ``--color``.
        no_migrate: When True, suppresses projects.cfg auto-migration.
                    Passed as ``--no-migrate``.
        dry_run:    When True, short-circuits dispatch — nothing is written.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    priority: Optional[int] = None
    color: Optional[str] = None
    no_migrate: Optional[bool] = None
    dry_run: bool = False


class RemoveProjectBody(BaseModel):
    """Body for POST /operations/remove-project.

    Mirrors every operator flag accepted by remove-project.sh verbatim, plus
    a ``confirm`` string that gates the destructive escalation. The route
    follows the script's own safety ladder: the default call is the safe,
    reversible mode (unregister from projects.cfg, leave the directory in
    place); ``force`` is the destructive mode (also delete the directory)
    and must be confirmed byte-for-byte. A project with an RC in flight is
    refused in both modes.

    Fields:
        project: Project name (required).
        force:   When True, also deletes the project directory (the CLI's
                 ``--force``). The Active-RC guard still holds.
                 Requires ``confirm`` unless ``dry_run`` is True.
        confirm: Confirmation string, required when ``force`` is True and
                 ``dry_run`` is not. Must equal exactly
                 ``remove-project <project>`` (byte-for-byte -- no trimming,
                 no case-folding). A missing or wrong value is refused 422
                 before anything runs. Ignored when ``force`` is not True:
                 the safe mode needs no confirmation.
        dry_run: When True, short-circuits dispatch — nothing is removed;
                 neither ``force`` nor ``confirm`` is required for a preview.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    force: Optional[bool] = None
    confirm: Optional[str] = None
    dry_run: bool = False


class ShipRcBody(BaseModel):
    """Body for POST /operations/ship-rc.

    Mirrors the operator flags accepted by ship-rc.sh, plus a mandatory
    confirm field that implements a double-gate: the caller must echo the
    exact string ``ship-rc <project> <key>`` to proceed. The verb takes no
    ``force``; the confirmation is the only gate this layer adds.

    The confirm-gate fires at the API layer BEFORE ``cm.ship.ship_rc`` runs.
    A wrong or missing confirm string returns 422 and never reaches it.
    The comparison is byte-for-byte exact — no trimming, no case-folding.

    Fields:
        project: Project name (required; exact, or an unambiguous prefix of
                 a registered name, as the CLI's ``--project``).
        key:     Release version in format ``vX.Y.Z`` (required; the CLI's
                 ``--key``).
        confirm: Confirmation string (required).  Must equal exactly
                 ``ship-rc <project> <key>`` (byte-for-byte).  Designed so
                 a future dashboard "Ship" button echoes the string verbatim.
        dry_run: When True, ship_rc prints the plan (project, version,
                 branch and tag names) and returns without any git step --
                 the CLI's ``--dry-run``. ``confirm`` is still required.

    Required fields are declared Optional so a body that omits one is refused
    by the route itself, dual-channel (``_missing_field_refusal``), rather than
    by the framework's ``{"detail": [...]}`` validation list.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    confirm: Optional[str] = None
    dry_run: bool = False


class ColdBuildNowBody(BaseModel):
    """Body for POST /operations/cold-build-now.

    Mirrors cold-build-now.sh's declared flags (``project``) plus the
    container-tier ``--image`` flag it passes through to deep-verify.sh
    (``image``); ``dry_run`` is the universal dispatch-layer preview. See the
    module docstring's "Long-running verbs" note: a real call launches the
    script detached and returns immediately, never with a verdict.

    Fields:
        project: Project whose deep-verify ledger the run is filed under.
                 Required by the script; validated at the API layer so a
                 missing value refuses dual-channel (422) rather than
                 dispatching a child that would refuse where no client can
                 see it.
        image:   Optional container image selection for this call -- a list
                 of names from team/config/container-images.txt, or the
                 string ``"all"``. Each entry is passed as one
                 ``--image <entry>``; an undeclared name is refused 422
                 (``container_tier.unknown_image``) before dispatch.
                 Omitted: no ``--image``, so kanban.cfg [release]
                 container_images applies, else nothing is built. Never
                 grants operator authorization.
        dry_run: When True, short-circuits dispatch — no subprocess spawned
                 and no detached process is started.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    image: Optional[Union[list[str], str]] = None
    dry_run: bool = False


class CancelRcBody(BaseModel):
    """Body for POST /operations/cancel-rc.

    Mirrors cm/cancel-rc.sh's flags (``project``, ``key``, ``yes``,
    ``force``) plus the universal ``dry_run``.

    Fields:
        project: Project name. Required.
        key:     RC version to cancel, ``vX.Y.Z``. Required; its FORMAT is
                 not pre-validated here -- a malformed value is passed on so
                 cancel_rc's own refusal is what the caller sees.
        yes:     The confirmation (the CLI's ``--yes``). The CLI asks
                 interactively without it; an HTTP caller has no terminal
                 to answer, so when ``dry_run`` is not True, ``yes`` must be
                 True or the route refuses (422) before anything runs.
        force:   The CLI's ``--force``: bypass only the Active-RC mismatch
                 check, cleaning up the named RC's branch and per-RC record
                 while leaving release-state.md (which then names another
                 RC) unchanged. A separate parameter from ``yes``: it never
                 confirms anything.
        dry_run: When True, returns the planned argv without changing
                 anything; ``yes`` is not required for a preview.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    key: Optional[str] = None
    yes: bool = False
    force: bool = False
    dry_run: bool = False


class ReleaseLocksBody(BaseModel):
    """Body for POST /operations/release-locks.

    Mirrors release-locks.sh's declared flags (``release``, ``force``) plus
    the universal ``dry_run``. The verb's read-only listing mode (no
    ``--release``) is ``GET /release-locks`` in reads.py, not this route:
    ``release`` is therefore required here -- a POST that names no lock has
    nothing to release.

    Fields:
        release: Name of the lock to release -- its lock-file basename with
                 the trailing ``.lock`` stripped, or an unambiguous prefix.
                 Resolution (exact, then hyphen-boundary prefix), the
                 not-found/ambiguous refusals, and the live-holder refusal
                 are ``ops.locks.release_lock``'s, shared with the script.
        force:   As ``--force``: a live holder is sent SIGTERM and the call
                 waits up to ``kill_grace_seconds`` for it to release the
                 lock itself; its file is never replaced or removed.
        dry_run: When True, returns the planned argv without changing
                 anything.
    """

    model_config = ConfigDict(extra="allow")

    release: Optional[str] = None
    force: bool = False
    dry_run: bool = False


class ArchiveBody(BaseModel):
    """Body for POST /operations/archive.

    Mirrors archive.sh's flags. Required fields are declared Optional so a
    body that omits one is refused by the route itself, dual-channel
    (``_missing_field_refusal``), rather than by the framework's
    ``{"detail": [...]}`` validation list; ``count`` accepts a string too so
    a malformed value reaches run_archive's own ``archive.invalid_count``
    refusal, the one the CLI prints.

    Fields:
        project: Project name (exact, or an unambiguous prefix of a
                 registered name, as the CLI's ``--project``).
        dry_run: When True, returns the planned moves per category in
                 ``stdout`` and moves nothing (the CLI's ``--dry-run``).
        count:   Items kept live per category, overriding ``[archive]
                 archive_count`` (the CLI's ``--count``); decimal digits,
                 ``0`` moves nothing.
        restore: An archived item's ID to move back live with its queue
                 lines and training logs (the CLI's ``--restore``); not
                 combined with ``dry_run``, ``count`` or
                 ``reconcile_queues``.
        reconcile_queues: Instead of an archive run, move every live queue
                 line naming an item that is archived and not live, or a
                 finished (``[x]``) task whose folder is in neither tree,
                 to the archive's queue file of the same name (the CLI's
                 ``--reconcile-queues``); with ``dry_run``, list them and
                 move nothing. Not combined with ``count``.
    """

    model_config = ConfigDict(extra="allow")

    project: Optional[str] = None
    dry_run: bool = False
    count: Optional[Union[int, str]] = None
    restore: Optional[str] = None
    reconcile_queues: bool = False


# ---------------------------------------------------------------------------
# POST /operations/halt
# ---------------------------------------------------------------------------


@router.post(
    "/halt",
    summary="Create the HALT signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the halt operation "
        "(the same text and exit code halt.sh produces)."
    ),
)
def post_halt(body: HaltBody, request: Request) -> JSONResponse:
    """Set the per-project HALT flag, stopping the wake loop cleanly at its next iteration.

    Calls ``pgai_agent_kanban.ops.write.halt`` -- the function halt.sh
    reaches through ``pp_run_ops`` -- after resolving ``project`` exactly
    as halt.sh does; no subprocess is started. ``stdout`` carries the
    narration halt.sh prints (set, or already halted).

    Body fields:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without creating the HALT file.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``halt.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "halt.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure.
    """
    known_fields = list(HaltBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "halt", exc, '{"project": "my-app"}', warnings
        )

    refused = _component_refusal(
        request, "halt", {"project": body.project}, '{"project": "my-app"}', warnings
    )
    if refused is not None:
        return refused

    script = _script_path(request, "halt.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        project_root = ctx.project_root(project)
        halt_path = project_root / "HALT"
        if halt_path.is_file():
            outcome.stdout += (
                f"halt: project {project} already halted ({halt_path} exists; no change made)\n"
            )
        else:
            with ops_call() as outcome:
                ops_write.halt(ctx, project)
            if outcome.ok:
                outcome.stdout += f"halt: HALT signal set for project {project} ({project_root}/HALT)\n"
            else:
                outcome.stderr += f"halt: failed to set HALT signal for project {project}\n"
                outcome.exit_code = 1

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="halt.refused",
        fallback_remedy=_cli_remedy("halt.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/unhalt
# ---------------------------------------------------------------------------


@router.post(
    "/unhalt",
    summary="Remove the HALT signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the unhalt operation "
        "(the same text and exit code unhalt.sh produces)."
    ),
)
def post_unhalt(body: HaltBody, request: Request) -> JSONResponse:
    """Remove the per-project HALT flag, allowing the wake loop to resume.

    Calls ``pgai_agent_kanban.ops.write.unhalt`` -- the function unhalt.sh
    reaches through ``pp_run_ops`` -- after resolving ``project`` exactly
    as unhalt.sh does; no subprocess is started. ``stdout`` carries the
    narration unhalt.sh prints (cleared, or was not halted).

    Body fields:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without removing the HALT file.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` lifted to top-level keys, or the plain stderr text as
      ``message`` under the stable fallback key ``unhalt.refused`` with a
      runnable CLI remedy (``_make_dual_channel_envelope``);
      ``{"error": "unhalt.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.
    """
    known_fields = list(HaltBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "unhalt", exc, '{"project": "my-app"}', warnings
        )

    refused = _component_refusal(
        request, "unhalt", {"project": body.project}, '{"project": "my-app"}', warnings
    )
    if refused is not None:
        return refused

    script = _script_path(request, "unhalt.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        project_root = ctx.project_root(project)
        halt_path = project_root / "HALT"
        if not halt_path.is_file():
            outcome.stdout += (
                f"unhalt: project {project} was not halted (no {halt_path} found; no change made)\n"
            )
        else:
            with ops_call() as outcome:
                ops_write.unhalt(ctx, project)
            if outcome.ok:
                outcome.stdout += (
                    f"unhalt: HALT signal cleared for project {project} ({project_root}/HALT removed)\n"
                )
            else:
                outcome.stderr += f"unhalt: failed to clear HALT signal for project {project}\n"
                outcome.exit_code = 1

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="unhalt.refused",
        fallback_remedy=_cli_remedy("unhalt.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/halt-after
# ---------------------------------------------------------------------------


@router.post(
    "/halt-after",
    summary="Arm the HALT-AFTER soft-drain signal for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the halt-after "
        "operation (the same text and exit code halt-after.sh produces); on a "
        "refused call the body also carries error, message and remedy."
    ),
)
def post_halt_after(body: HaltAfterBody, request: Request) -> JSONResponse:
    """Arm the HALT-AFTER soft-drain signal.

    The signal causes the wake loop to promote to a hard HALT once the named
    drain event is satisfied (e.g. after the current RC ships).

    Calls ``pgai_agent_kanban.ops.write.halt_after`` -- the function
    halt-after.sh reaches through ``pp_run_ops`` -- with the token
    halt-after.sh would write, after the same ``until``/``key`` checks and
    project resolution the script performs, in the same order; no
    subprocess is started.

    Body fields:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``key``     (optional) — drain event token (``rc``, ``pm``, ``coder``,
                               ``writer``, ``tester``, ``cm``). Defaults to
                               ``rc``, the CLI's own default, so
                               ``{"project": P, "until": "vX.Y.Z"}`` arms
                               exactly what ``halt-after.sh --project P
                               --until vX.Y.Z`` arms.
    - ``until``   (optional) — release boundary (e.g. ``v2.20.1``, leading
                               ``v`` optional); arms the versioned
                               ``rc:vX.Y.Z`` token directly. rc scope only.
                               Checked as halt-after.sh checks it, with the
                               same refusals: ``halt-after.until_scope_mismatch``
                               when ``key`` names one of the five role
                               tokens, ``halt-after.until_malformed`` when
                               the value is not ``vX.Y.Z`` -- the same
                               machine key, message, remedy and exit code
                               the CLI prints.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without writing the HALT-AFTER signal.

    Response envelope (exit_code 0):
      ``{"exit_code": 0, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused -- the dual-channel triple in ``stderr`` lifted to
      top-level keys (``_make_dual_channel_envelope``), so an HTTP caller
      reads the same refusal the CLI prints; ``{"error":
      "halt_after.project_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body with no
      ``project``, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.
    """
    known_fields = list(HaltAfterBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError:
        # BLOCK-WORTHY (docs/blocking-site-census.md): the same invariant
        # halt-after.sh's own project_required site enforces -- the
        # HALT-AFTER sentinel is always per-project, so a body naming none
        # has no location to write to. Refused here, dual-channel, so an
        # HTTP caller reads the same refusal shape whichever layer refused,
        # and no subprocess is spawned.
        # CENSUS-ANCHOR: operations-halt-after-project-required-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "halt_after.project_required",
            "halt-after requires 'project': the HALT-AFTER sentinel is always "
            "per-project, so there is nowhere to arm it without one.",
            _example_curl(request, '{"project": "my-app", "until": "v1.2.3"}'),
            warnings,
        )

    refused = _component_refusal(
        request,
        "halt-after",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "until": "v1.2.3"}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "halt-after.sh")
    # An absent key is omitted from the planned argv (build_argv drops None),
    # matching the CLI form a caller would type.
    flags: dict = {"project": body.project, "key": body.key, "until": body.until}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    result = _halt_after_result(request, body.project, body.key or "", body.until or "")
    return _make_dual_channel_envelope(
        result,
        warnings,
        fallback_error="halt_after.refused",
        fallback_remedy=(
            "Fix the condition the message names, then re-run the CLI form: "
            f"team/scripts/halt-after.sh --project {body.project}"
            + (f" --key {body.key}" if body.key else "")
            + (f" --until {body.until}" if body.until else "")
        ),
    )


def _halt_after_result(request: Request, project_raw: str, key_raw: str, until_raw: str) -> ShellResult:
    """Arm HALT-AFTER the way halt-after.sh does, returning its exit code and output.

    The checks run in halt-after.sh's order -- ``until`` with a non-rc
    ``key``, then a malformed ``until``, then project resolution -- each
    refusing with the script's own dual-channel block at exit code 1; then
    ``ops.write.halt_after`` writes the token (``rc:vX.Y.Z`` for a valid
    ``until``, else ``key``, else ``rc``) and ``stdout`` carries the
    script's arm confirmation.
    """
    if until_raw and key_raw and key_raw.lower() != "rc":
        # BLOCK-WORTHY (docs/blocking-site-census.md): --until names a release
        # boundary and the role scopes drain on task state with none to bind
        # to; honouring the request would reinterpret it, as halt-after.sh's
        # own until_scope_mismatch site says.
        # CENSUS-ANCHOR: operations-halt-after-until-scope-mismatch-01
        return _contract_refusal(
            "halt-after.until_scope_mismatch",
            f"halt-after.sh: --until targets a release boundary; scope '{key_raw}' has none.",
            f"Drop --until to arm the {key_raw} scope's own drain (team/scripts/halt-after.sh "
            f"--key {key_raw}), or drop --key {key_raw} to arm the versioned rc scope "
            "(team/scripts/halt-after.sh --project your-project --until vX.Y.Z).",
        )

    until_normalised = ""
    if until_raw:
        # One optional leading 'v', then one optional 'V', exactly as the
        # script strips them, then a strict vX.Y.Z check.
        bare = until_raw[1:] if until_raw.startswith("v") else until_raw
        bare = bare[1:] if bare.startswith("V") else bare
        until_normalised = f"v{bare}"
        try:
            well_formed = until_normalised.isascii() and until_normalised == until_normalised.strip()
            semver.parse(until_normalised)
        except ValueError:
            well_formed = False
        if not well_formed:
            # BLOCK-WORTHY (docs/blocking-site-census.md): a malformed release
            # boundary is refused before anything is written rather than
            # resolved to a guessed version, as halt-after.sh's own
            # until_malformed site refuses.
            # CENSUS-ANCHOR: operations-halt-after-until-malformed-01
            return _contract_refusal(
                "halt-after.until_malformed",
                f"halt-after.sh: --until value '{until_raw}' is not well-formed semver (expected vX.Y.Z).",
                "Re-run with a well-formed version, e.g. --until v2.14.0.",
            )

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, project_raw)
    if project is None:
        return outcome.as_shell_result()

    # "rc" is the drain scope halt-after.sh and ops.write.halt_after both
    # default to when no key is given.
    token = f"rc:{until_normalised}" if until_normalised else (key_raw or "rc")
    project_root = ctx.project_root(project)
    with ops_call() as outcome:
        ops_write.halt_after(ctx, project, token)
    if not outcome.ok:
        outcome.stderr += f"halt-after: failed to arm HALT-AFTER signal for project {project}\n"
        outcome.exit_code = 1
        return outcome.as_shell_result()

    described = describe_token(token)
    if described != token:
        outcome.stdout += (
            f"halt-after: HALT-AFTER signal armed for project {project} "
            f"(token: {token}, {described}, path: {project_root}/HALT-AFTER)\n"
        )
    else:
        outcome.stdout += (
            f"halt-after: HALT-AFTER signal armed for project {project} "
            f"(token: {token}, path: {project_root}/HALT-AFTER)\n"
        )
    return outcome.as_shell_result()


# ---------------------------------------------------------------------------
# POST /operations/defer
# ---------------------------------------------------------------------------


@router.post(
    "/defer",
    summary="Arm the DEFER-LOW-BUGS deferral campaign for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the defer "
        "operation (the same text and exit code defer.sh produces)."
    ),
)
def post_defer(body: DeferBody, request: Request) -> JSONResponse:
    """Arm the DEFER-LOW-BUGS deferral campaign, which lets a deferred bug wait
    instead of opening a release of its own until the named release ships.

    Calls ``pgai_agent_kanban.ops.write.defer`` -- the function defer.sh
    reaches through ``pp_run_ops`` -- which checks ``until``, resolves
    ``project`` and writes the campaign file; no subprocess is started.
    ``stdout`` carries the narration defer.sh prints (armed, or already
    armed).

    Body fields:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``until``   (required by the operation, NOT pre-validated here) — release
                                boundary the campaign runs until (e.g. ``v2.20.0``).
                                A missing or malformed value is passed verbatim
                                to ``ops.write.defer``, whose dual-channel refusal
                                (``defer.until_required`` / ``defer.until_malformed``)
                                lands in the envelope's stderr as defer.sh prints it.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without writing the campaign file.

    Idempotent: re-arming a project already carrying a campaign file with the
    SAME ``until`` target is a no-op — a different target replaces it.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused at exit code 1 -- the dual-channel triple in
      ``stderr`` (``defer.until_malformed``, ``project_resolve.not_found``,
      for two) lifted to top-level keys, or the plain stderr text as
      ``message`` under the stable fallback key ``defer.refused`` with a
      runnable CLI remedy (``_make_dual_channel_envelope``);
      ``{"error": "defer.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before anything runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500) (including the
    ``until``-related refusals), 422 on missing ``project``.
    """
    known_fields = list(DeferBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "defer", exc, '{"project": "my-app", "until": "v1.2.3"}', warnings
        )

    refused = _component_refusal(
        request,
        "defer",
        {"project": body.project},
        '{"project": "my-app", "until": "v1.2.3"}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "defer.sh")
    flags: dict = {"project": body.project, "until": body.until}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    # defer.sh exits 1 on every refusal the function raises.
    with ops_call(exit_code=1) as outcome:
        change = ops_write.defer(ctx, body.project, body.until or "")
    if outcome.ok:
        outcome.stdout += f"{change.narration}\n"

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="defer.refused",
        fallback_remedy=_cli_remedy("defer.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/undefer
# ---------------------------------------------------------------------------


@router.post(
    "/undefer",
    summary="Clear the DEFER-LOW-BUGS deferral campaign for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the undefer "
        "operation (the same text and exit code undefer.sh produces)."
    ),
)
def post_undefer(body: UndeferBody, request: Request) -> JSONResponse:
    """Clear the DEFER-LOW-BUGS deferral campaign for a project.

    Calls ``pgai_agent_kanban.ops.write.undefer`` -- the function undefer.sh
    reaches through ``pp_run_ops`` -- which resolves ``project`` and removes
    the campaign file; no subprocess is started. ``stdout`` carries the
    narration undefer.sh prints (cleared, or nothing to clear).

    Idempotent: a project with no campaign file in force succeeds and reports
    there was nothing to clear, rather than refusing.

    Body fields:

    - ``project`` (required) — project name or unambiguous prefix.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without removing the campaign file.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused at exit code 1 -- the dual-channel triple in
      ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``undefer.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "undefer.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before anything runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0 (including the already-clear case), 500 on
    non-zero, 422 on missing ``project``.
    """
    known_fields = list(UndeferBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "undefer", exc, '{"project": "my-app"}', warnings
        )

    refused = _component_refusal(
        request, "undefer", {"project": body.project}, '{"project": "my-app"}', warnings
    )
    if refused is not None:
        return refused

    script = _script_path(request, "undefer.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    # undefer.sh exits 1 on every refusal the function raises.
    with ops_call(exit_code=1) as outcome:
        change = ops_write.undefer(ctx, body.project)
    if outcome.ok:
        outcome.stdout += f"{change.narration}\n"

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="undefer.refused",
        fallback_remedy=_cli_remedy("undefer.sh", planned_argv),
    )


def _halt_global_result(request: Request) -> ShellResult:
    """Create the global HALT the way halt-global.sh does, returning its exit code and output."""
    ctx = _ops_context(request)
    kanban_root = ctx.kanban_root
    if not kanban_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): the global HALT lives
        # at KANBAN_ROOT/HALT; with no such directory there is nowhere to
        # write it -- halt-global.sh's own kanban_root_missing refusal.
        # CENSUS-ANCHOR: operations-halt-global-kanban-root-missing-01
        return _contract_refusal(
            "halt_global.kanban_root_missing",
            f"halt-global.sh: KANBAN_ROOT does not exist or is not a directory: {kanban_root}.",
            f"Set PGAI_AGENT_KANBAN_ROOT_PATH to an existing kanban root, or create {kanban_root} first.",
        )

    halt_path = kanban_root / "HALT"
    if halt_path.is_file():
        return ShellResult(0, f"halt-global.sh: global HALT already set: {halt_path}\n", "")

    with ops_call() as outcome:
        ops_write.halt_global(ctx)
    if outcome.ok:
        outcome.stdout += f"global HALT set: {halt_path} — all projects will stop at next wake\n"
        return outcome.as_shell_result()
    # BLOCK-WORTHY (docs/blocking-site-census.md): the sentinel write failed,
    # so the operator cannot rely on the HALT being in effect --
    # halt-global.sh's own sentinel_create_failed refusal.
    # CENSUS-ANCHOR: operations-halt-global-sentinel-create-failed-01
    failure = _contract_refusal(
        "halt_global.sentinel_create_failed",
        f"halt-global.sh: failed to create global HALT sentinel at {halt_path}.",
        f"Check that {kanban_root} is writable by the current user, then re-run team/scripts/halt-global.sh.",
    )
    return ShellResult(1, outcome.stdout, outcome.stderr + failure.stderr)


def _unhalt_global_result(request: Request) -> ShellResult:
    """Remove the global HALT the way unhalt-global.sh does, returning its exit code and output."""
    ctx = _ops_context(request)
    kanban_root = ctx.kanban_root
    if not kanban_root.is_dir():
        # BLOCK-WORTHY (docs/blocking-site-census.md): with no kanban root
        # there is no global HALT to check or remove -- unhalt-global.sh's
        # own kanban_root_missing refusal.
        # CENSUS-ANCHOR: operations-unhalt-global-kanban-root-missing-01
        return _contract_refusal(
            "unhalt_global.kanban_root_missing",
            f"unhalt-global.sh: KANBAN_ROOT does not exist or is not a directory: {kanban_root}.",
            f"Set PGAI_AGENT_KANBAN_ROOT_PATH to an existing kanban root, or create {kanban_root} first.",
        )

    halt_path = kanban_root / "HALT"
    if not halt_path.is_file():
        return ShellResult(0, f"unhalt-global.sh: no global HALT was set ({halt_path} not found)\n", "")

    with ops_call() as outcome:
        ops_write.unhalt_global(ctx)
    if outcome.ok:
        outcome.stdout += (
            f"global HALT cleared: {halt_path} removed — all projects will resume at next wake\n"
        )
        return outcome.as_shell_result()
    # BLOCK-WORTHY (docs/blocking-site-census.md): the sentinel removal
    # failed, so the operator cannot rely on the HALT being cleared --
    # unhalt-global.sh's own sentinel_remove_failed refusal.
    # CENSUS-ANCHOR: operations-unhalt-global-sentinel-remove-failed-01
    failure = _contract_refusal(
        "unhalt_global.sentinel_remove_failed",
        f"unhalt-global.sh: failed to remove global HALT sentinel at {halt_path}.",
        f"Check that {kanban_root} is writable by the current user, then re-run team/scripts/unhalt-global.sh.",
    )
    return ShellResult(1, outcome.stdout, outcome.stderr + failure.stderr)


# ---------------------------------------------------------------------------
# POST /operations/halt-global
# ---------------------------------------------------------------------------


@router.post(
    "/halt-global",
    summary="Create the global HALT signal",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the halt-global "
        "operation (the same text and exit code halt-global.sh produces)."
    ),
)
def post_halt_global(request: Request, body: HaltGlobalBody = HaltGlobalBody()) -> JSONResponse:
    """Set the global HALT flag at ``<kanban_root>/HALT``.

    Blocks all projects at the next wake-loop iteration.  Idempotent and
    fully reversible via ``POST /operations/unhalt-global``.

    Calls ``pgai_agent_kanban.ops.write.halt_global`` -- the function
    halt-global.sh reaches through ``pp_run_ops`` -- with the script's own
    kanban-root check and narration around it; no subprocess is started.

    Body fields:

    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without creating the global HALT file.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- the dual-channel triple
      in ``stderr`` (``halt_global.kanban_root_missing`` and the sentinel-write
      failure, as halt-global.sh emits them) lifted to top-level keys, or plain
      stderr text as ``message`` under the stable fallback key
      ``halt_global.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``).
    """
    known_fields = list(HaltGlobalBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "halt-global.sh")
    planned_argv = build_argv(script, {})

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    return _make_dual_channel_envelope(
        _halt_global_result(request),
        warnings,
        fallback_error="halt_global.refused",
        fallback_remedy=_cli_remedy("halt-global.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/unhalt-global
# ---------------------------------------------------------------------------


@router.post(
    "/unhalt-global",
    summary="Remove the global HALT signal",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the unhalt-global "
        "operation (the same text and exit code unhalt-global.sh produces)."
    ),
)
def post_unhalt_global(request: Request, body: HaltGlobalBody = HaltGlobalBody()) -> JSONResponse:
    """Remove the global HALT flag from ``<kanban_root>/HALT``.

    Allows the wake loop to resume processing all projects at the next iteration.
    Idempotent.

    Calls ``pgai_agent_kanban.ops.write.unhalt_global`` -- the function
    unhalt-global.sh reaches through ``pp_run_ops`` -- with the script's own
    kanban-root check and narration around it; no subprocess is started.

    Body fields:

    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without removing the global HALT file.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- the dual-channel triple
      in ``stderr`` (``unhalt_global.kanban_root_missing`` and the sentinel-write
      failure, as unhalt-global.sh emits them) lifted to top-level keys, or plain
      stderr text as ``message`` under the stable fallback key
      ``unhalt_global.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``).
    """
    known_fields = list(HaltGlobalBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    script = _script_path(request, "unhalt-global.sh")
    planned_argv = build_argv(script, {})

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    return _make_dual_channel_envelope(
        _unhalt_global_result(request),
        warnings,
        fallback_error="unhalt_global.refused",
        fallback_remedy=_cli_remedy("unhalt-global.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/reset
# ---------------------------------------------------------------------------


@router.post(
    "/reset",
    summary="Reset a task or intake item to its initial state",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the reset "
        "operation (the same text and exit code reset.sh produces)."
    ),
)
def post_reset(body: ResetBody, request: Request) -> JSONResponse:
    """Reset a task or intake item, returning it to a re-processable state.

    Calls ``pgai_agent_kanban.ops.write.reset_item`` -- the function
    reset.sh reaches through ``pp_run_ops`` -- after resolving ``project``
    exactly as reset.sh does; no subprocess is started. Its refusals
    (WORKING state, ambiguous or unknown key, a stale worktree without
    ``force``) carry the same exit code and stderr text reset.sh produces,
    including the stale-worktree removal recipe. reset.sh's extra step for
    a ``TESTER-`` key -- tearing down a retained TESTER worktree -- is not
    repeated here: ``reset_item`` itself refuses (or, with ``force``,
    removes) any registered or on-disk worktree for the task before it
    resets anything, so after a successful reset there is none left for
    that step to find.

    Body fields (exactly one kind selector is required):

    - ``project``        (required) — project name.
    - ``key``            (optional) — kind-agnostic task or intake key.
    - ``agent``          (optional) — agent task ID (e.g. ``CODER-20260101-001-slug``).
    - ``bug``            (optional) — bug intake key (e.g. ``BUG-0042``).
    - ``priority``       (optional) — priority intake key (e.g. ``PRIORITY-0007``).
    - ``requirement``    (optional) — requirement intake version (e.g. ``v0.1.2``).
    - ``keep_artifacts`` (optional, bool) — when True, passes ``--keep-artifacts``.
    - ``force``          (optional, bool) — when True, passes ``--force`` to perform
                                            stale-worktree cleanup before the reset.
    - ``dry_run``        (optional, bool) — when True, returns the planned action description
                                            without resetting anything.

    Exactly one of ``key``, ``agent``, ``bug``, ``priority``, or ``requirement`` must
    be supplied.  Supplying two or more returns 422 naming the conflicting fields.
    Supplying none returns 422 naming the missing choice.  No silent precedence.

    The resolved value is the key ``reset_item`` resolves; the item type follows
    from what it names (task folder, ``BUG-*``, ``PRIORITY-*``, requirement version).

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``reset.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "reset.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure.
    """
    known_fields = list(ResetBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request,
            "reset",
            exc,
            '{"project": "my-app", "key": "v1.2.3"}',
            warnings,
        )

    refused = _component_refusal(
        request,
        "reset",
        {
            "project": body.project,
            "key": body.key,
            "agent": body.agent,
            "bug": body.bug,
            "priority": body.priority,
            "requirement": body.requirement,
        },
        '{"project": "my-app", "key": "v1.2.3"}',
        warnings,
    )
    if refused is not None:
        return refused

    # Exactly-one-of constraint across the five kind selectors.
    _KIND_FIELDS = ("key", "agent", "bug", "priority", "requirement")
    supplied = [
        field
        for field in _KIND_FIELDS
        if getattr(body, field) not in (None, "")
    ]

    if len(supplied) == 0:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- reset.sh acts on exactly one --key, and with no selector
        # there is no value to forward as it. Refused here, dual-channel,
        # before any subprocess is spawned.
        # CENSUS-ANCHOR: operations-reset-selector-required-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "reset.selector_required",
            f"Exactly one of {{{', '.join(_KIND_FIELDS)}}} must be supplied; "
            "none provided.",
            _example_curl(
                request,
                json.dumps({"project": body.project, "key": "v1.2.3"}),
            ),
            warnings,
        )

    if len(supplied) > 1:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
        # design -- two selectors name two items and no silent precedence
        # picks one. Refused here, dual-channel, before any subprocess runs.
        # CENSUS-ANCHOR: operations-reset-selector-conflict-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "reset.selector_conflict",
            f"Exactly one of {{{', '.join(_KIND_FIELDS)}}} must be supplied; "
            f"got conflicting fields: {', '.join(supplied)}.",
            _example_curl(
                request,
                json.dumps({"project": body.project, supplied[0]: getattr(body, supplied[0])}),
            ),
            warnings,
        )

    # Extract the resolved key value from whichever selector was supplied.
    resolved_key = getattr(body, supplied[0])

    script = _script_path(request, "reset.sh")
    flags: dict = {"project": body.project, "key": resolved_key}
    if body.keep_artifacts:
        flags["keep-artifacts"] = True
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            ops_write.reset_item(
                ctx,
                project,
                resolved_key,
                keep_artifacts=bool(body.keep_artifacts),
                force=bool(body.force),
            )

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="reset.refused",
        fallback_remedy=_cli_remedy("reset.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/close
# ---------------------------------------------------------------------------


@router.post(
    "/close",
    summary="Close or resolve a task or intake item",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the close "
        "operation (the same text and exit code close.sh produces)."
    ),
)
def post_close(body: CloseBody, request: Request) -> JSONResponse:
    """Close or resolve a task or intake item.

    Calls ``pgai_agent_kanban.ops.write.close_item`` -- the function close.sh
    reaches through ``pp_run_ops`` -- after resolving ``project`` exactly as
    close.sh does; no subprocess is started. Refuses when the key cannot be
    identified as a single target (not found or ambiguous) with the exit
    code and stderr text close.sh produces.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — task key or intake identifier.
    - ``state``   (optional) — close state (``done`` or ``wont-do``, the
                               closed states); intake-only; defaults to ``done``.
                               Any other value (the retired ``superseded``
                               included) is refused ``close.invalid_state``.
    - ``note``    (optional) — note text recorded in the intake item's ``## Close Note``.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without closing anything.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``close.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "close.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.
    """
    known_fields = list(CloseBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "close", exc, '{"project": "my-app", "key": "v1.2.3"}', warnings
        )

    refused = _component_refusal(
        request,
        "close",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "v1.2.3"}',
        warnings,
    )
    if refused is not None:
        return refused

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

    # A state outside lib.terminal_states.CLOSED_STATES is refused by
    # close_item itself (close.invalid_state, exit 1), exactly as close.sh
    # reports it; its dual-channel stderr is lifted by the envelope below.
    state = body.state or "done"

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            ops_write.close_item(ctx, project, body.key, state=state, note=body.note or "")
        if outcome.ok:
            outcome.stdout += f"close: {body.key} closed (status: {state})\n"

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="close.refused",
        fallback_remedy=_cli_remedy("close.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/wontdo
# ---------------------------------------------------------------------------


@router.post(
    "/wontdo",
    summary="Mark a task as WONT-DO",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the wontdo "
        "operation (the same text and exit code wontdo.sh produces)."
    ),
)
def post_wontdo(body: WontdoBody, request: Request) -> JSONResponse:
    """Mark a task as WONT-DO (cancelled without completion).

    Calls ``pgai_agent_kanban.ops.write.wontdo_item`` -- the function
    wontdo.sh reaches through ``pp_run_ops`` -- after resolving ``project``
    exactly as wontdo.sh does; no subprocess is started. ``stdout`` and a
    refusal's exit code and ``stderr`` match what wontdo.sh prints.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — task ID (folder name under tasks/).
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without modifying any task state.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``wontdo.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "wontdo.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.
    """
    known_fields = list(WontdoBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "wontdo", exc, '{"project": "my-app", "key": "CODER-20260101-001-slug"}', warnings
        )

    refused = _component_refusal(
        request,
        "wontdo",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "CODER-20260101-001-slug"}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "wontdo.sh")
    flags: dict = {"project": body.project, "key": body.key}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            ops_write.wontdo_item(ctx, project, body.key)
        if outcome.ok:
            outcome.stdout += f"wontdo: task {body.key} marked WONT-DO\n"
        elif outcome.exit_code == 2:
            outcome.stderr += (
                f"wontdo: ambiguous task prefix {body.key} — multiple matches; "
                "task NOT marked WONT-DO\n"
            )
        else:
            outcome.stderr += (
                f"wontdo: failed to mark task {body.key} as WONT-DO (rc={outcome.exit_code})\n"
            )

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="wontdo.refused",
        fallback_remedy=_cli_remedy("wontdo.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/delete
# ---------------------------------------------------------------------------


@router.post(
    "/delete",
    summary="Delete a task or intake item by key",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the delete "
        "operation (the same text and exit code delete.sh produces)."
    ),
)
def post_delete(body: DeleteBody, request: Request) -> JSONResponse:
    """Delete a task or intake item by key.

    Calls ``pgai_agent_kanban.ops.write.delete_item`` -- the function
    delete.sh reaches through ``pp_run_ops`` -- after resolving ``project``
    exactly as delete.sh does; no subprocess is started. It refuses to
    delete an item that is not in a terminal state (DONE or WONT-DO) unless
    ``force`` is True; that refusal, and an ambiguous or unknown key, come
    back with the refusal's HTTP status (404 / 409, else 500), the exit code and stderr text delete.sh produces.

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — item key (task folder name or intake file base name).
    - ``force``   (optional, bool) — when True, overrides the terminal-state guard.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                                      without deleting anything.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``delete.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "delete.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on success. 409 for a non-terminal item without
    ``force`` (exit code 2, under ``delete.refused``) or a key matching more
    than one item; 404 for a key matching nothing; the resolver's 404 / 409
    for an unknown or ambiguous ``project`` (``project_resolve.not_found`` /
    ``project_resolve.ambiguous``); 422 for a body missing a required field
    or a ``project`` / ``key`` that is not one plain name. 500 for anything
    else -- a registered project with no directory, a task folder missing
    its ``status.md``, the filesystem refusing the removal.
    """
    known_fields = list(DeleteBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "delete", exc, '{"project": "my-app", "key": "v1.2.3", "dry_run": true}', warnings
        )

    refused = _component_refusal(
        request,
        "delete",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "v1.2.3", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "delete.sh")
    flags: dict = {"project": body.project, "key": body.key}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            ops_write.delete_item(ctx, project, body.key, force=bool(body.force))
        if outcome.ok:
            outcome.stdout += f"delete: item {body.key} deleted\n"

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="delete.refused",
        fallback_remedy=_cli_remedy("delete.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/intake
# ---------------------------------------------------------------------------


def _refuse_unconfined_intake_path(intake_dir: pathlib.Path, path: pathlib.Path) -> None:
    """Raise Refused unless *path*, resolved, sits strictly under *intake_dir*, resolved.

    Resolution follows every symlink in the part of *path* that exists, so
    a component swapped for a link pointing elsewhere is caught before
    anything is created through it.
    """
    root = intake_dir.resolve()
    resolved = path.resolve()
    if resolved != root and resolved.is_relative_to(root):
        return
    # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by design --
    # the temp file must sit inside the intake temp directory; a path that
    # resolves elsewhere (through a symlink) is never created or written.
    # CENSUS-ANCHOR: operations-intake-temp-path-unconfined-01
    raise Refused(format_error_contract(
        "intake.temp_path_unconfined",
        f"intake refused to write its temp file: {path} resolves to {resolved}, "
        f"outside the intake temp directory {root}. Nothing was written.",
        "Inspect the intake temp directory for a symlink, then re-send the request: "
        + shlex.join(["ls", "-la", str(intake_dir)]),
    ))


def _write_intake_temp_file(intake_dir: pathlib.Path, temp_file: pathlib.Path, content: str) -> None:
    """Write *content* to *temp_file*, a new file confined under *intake_dir*.

    *temp_file* is ``intake_dir / <per-request id> / <bare filename>``.
    The intake directory is created when missing. The per-request directory
    and then the temp file are each checked, resolved, to sit under the
    resolved intake directory before they are created. The per-request
    directory must not exist yet, so a request never writes into a
    directory something else made, and the file is created exclusively, so
    an existing file or a symlink at that name is never followed or
    overwritten.

    Raises:
        IoError: A directory or the file could not be created.
        Refused: The per-request directory or the temp file resolves outside
                 the intake directory (nothing is created there).
    """
    request_dir = temp_file.parent
    try:
        intake_dir.mkdir(parents=True, exist_ok=True)
        _refuse_unconfined_intake_path(intake_dir, request_dir)
        request_dir.mkdir(mode=0o700)
        _refuse_unconfined_intake_path(intake_dir, temp_file)
        with open(temp_file, "x", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        raise IoError(f"intake: cannot write the temp file {temp_file}: {exc}") from exc


@router.post(
    "/intake",
    summary="Deposit a staged intake file into a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the intake "
        "operation (the same text and exit code intake.sh produces)."
    ),
)
def post_intake(body: IntakeBody, request: Request) -> JSONResponse:
    """Write the supplied content to a temp file and deposit it into the project.

    After resolving ``project`` exactly as intake.sh does, the content is
    written to a temp file under the framework temp root
    (``route_temp_root``: the server's configured framework temp root)
    in an ``intake/`` subdirectory, using ``filename`` as the file name, and
    ``pgai_agent_kanban.ops.write.deposit_intake`` -- the function intake.sh
    reaches through ``pp_run_ops`` -- routes it by its name prefix
    (``BUG-*``, ``PRIORITY-*``, ``v[0-9]*.md``) into the matching project
    sub-directory. No subprocess is started; ``stdout`` names the deposited
    path as intake.sh does, and a refusal (unroutable name, target already
    present) carries intake.sh's exit code and stderr text. The temp file
    is left in place per existing intake semantics; cleanup is the
    operator's responsibility.

    Body fields:

    - ``project``  (required) — project name.
    - ``filename`` (required) — intake file name; governs routing inside intake.sh.
    - ``content``  (required) — text content to write into the temp file.
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing the temp file or invoking intake.sh.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused with a non-zero exit code -- a dual-channel triple
      in ``stderr`` (``project_resolve.not_found``, for one) lifted to
      top-level keys, or the plain stderr text as ``message`` under the
      stable fallback key ``intake.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "intake.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before any subprocess runs -- never the framework's
      ``{"detail": [...]}`` list.

    The dry-run description names the temp file as the positional source
    argument of the equivalent intake.sh call.
    """
    known_fields = list(IntakeBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "filename": body.filename, "content": body.content},
            required_keys=["project", "filename", "content"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request,
            "intake",
            exc,
            '{"project": "my-app", "filename": "v1.2.3.md", '
            '"content": "# Requirements v1.2.3"}',
            warnings,
        )

    refused = _component_refusal(
        request,
        "intake",
        {"project": body.project},
        '{"project": "my-app", "filename": "v1.2.3.md", "content": "# Requirements v1.2.3"}',
        warnings,
    )
    if refused is not None:
        return refused

    # The filename is checked before any path is built from it: before the
    # planned argv, the dry-run envelope, any mkdir and any write. (This
    # docstring is the route's published ICD description, so the refusal is
    # documented here and in the module docstring rather than above.)
    unroutable_name = False
    try:
        intake_destination(body.filename)
    except IntakeFilenameRefused as exc:
        if exc.error == INTAKE_FILENAME_NOT_BARE:
            # BLOCK-WORTHY (docs/blocking-site-census.md): an invariant by
            # design -- the filename is joined under the intake temp directory,
            # so a path would write outside it. Refused dual-channel before any
            # filesystem access; the message never echoes the refused value.
            # CENSUS-ANCHOR: operations-intake-filename-not-bare-01
            return _refusal_response(
                HTTP_UNPROCESSABLE_ENTITY,
                exc.error,
                f"intake refused the body: {exc.message} Nothing was written.",
                _routable_intake_example_curl(request, body.project),
                warnings,
            )
        # A bare but unroutable name is refused below by route_intake_name,
        # the check deposit_intake runs, so the caller reads exactly what
        # intake.sh prints -- still before the temp file is written.
        unroutable_name = True

    # The planned temp file sits in a fresh per-request directory, so two
    # requests naming the same file never share or overwrite a temp file.
    script = _script_path(request, "intake.sh")
    temp_intake_dir = route_temp_root(request) / "intake"
    temp_file_path = temp_intake_dir / uuid.uuid4().hex / body.filename
    planned_argv = [script, "--project", body.project, str(temp_file_path)]

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            ops_write.route_intake_name(project, temp_file_path)
            _write_intake_temp_file(temp_intake_dir, temp_file_path, body.content)
            deposited = ops_write.deposit_intake(ctx, project, temp_file_path)
        if outcome.ok:
            outcome.stdout += f"intake: deposited {deposited}\n"

    if unroutable_name:
        # route_intake_name refused before the temp file was written, so the
        # CLI form would name a file that does not exist. The remedy re-sends
        # the request under a routable example name instead, as the
        # bare-filename refusal above does.
        fallback_remedy = (
            "Nothing was written. Re-send the request with a filename that routes, "
            "for example: " + _routable_intake_example_curl(request, project or body.project)
        )
    else:
        fallback_remedy = _cli_remedy("intake.sh", planned_argv)
    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="intake.refused",
        fallback_remedy=fallback_remedy,
    )


# ---------------------------------------------------------------------------
# POST /operations/unwind-rc
# ---------------------------------------------------------------------------


@router.post(
    "/unwind-rc",
    summary="Fully unwind an in-flight release candidate",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from cm.unwind.unwind_rc "
        "(the unwind-rc.sh output); on a refused call the body also carries "
        "error, message and remedy."
    ),
)
def post_unwind_rc(body: UnwindRcBody, request: Request) -> JSONResponse:
    """Unwind an in-flight release candidate across all state stores.

    The REST twin of the ``unwind-rc`` verb. Calls
    ``pgai_agent_kanban.cm.unwind.unwind_rc`` -- the function unwind-rc.sh
    reaches through ``pp_run_ops`` -- in process; the route itself starts no
    process (the git commands the unwind runs are the unwind's own). Its
    stdout and stderr are collected through callbacks, without capturing the
    server's own streams.

    The one API-layer gate is the confirmation. The CLI asks "Type 'yes' to
    continue" before touching anything; an HTTP request has no terminal to
    answer, so the body's ``confirm`` is that answer: ``true``, or the typed
    phrase ``unwind-rc <project> <key>`` byte-for-byte. Anything else is
    refused 422 here, before anything runs. ``force`` is a separate
    escalation that bypasses ONLY the Active-RC version-mismatch check -- it
    still needs ``confirm``, so no call can skip a guard without also having
    been confirmed.

    Every check unwind_rc makes propagates unchanged in the envelope:

    - The project name resolves (``project_resolve.not_found`` /
      ``.ambiguous``, the resolver's own block).
    - HALT must be set (per-project or global) before unwinding.
    - Active RC in release-state.md must match ``key`` (bypassable with ``force``).

    These surface as non-zero exit_code and populated stderr, mapped to the refusal's HTTP status
    (404 / 409 / 422, else 500).

    Body fields:

    - ``project`` (required) — project name.
    - ``key``     (required) — RC version to unwind (e.g. ``v1.2.0``).
    - ``confirm`` (required unless ``dry_run`` is true) — ``true``, or exactly
                  ``unwind-rc <project> <key>``. Missing, ``false`` or empty →
                  422 ``unwind_rc.confirm_required``; any other string → 422
                  ``unwind_rc.confirm_mismatch``. Nothing runs on either.
    - ``force``   (optional, bool) — when True, bypasses the Active-RC version
                                     mismatch check; does NOT bypass HALT or other
                                     guards, and does NOT stand in for ``confirm``.
    - ``dry_run`` (optional, bool) — when True, returns the CLI call a live request
                                      is equivalent to without running anything.
                                      ``confirm`` is not required for a preview.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when
      unwind_rc refused or stopped -- a dual-channel block in ``stderr``
      lifted out as top-level keys, or its plain stderr text as ``message``
      under the stable fallback key ``unwind_rc.refused`` with a runnable
      CLI remedy (``_make_dual_channel_envelope``);
      ``{"error": "unwind_rc.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field or the confirmation, or ``{"error":
      "unwind_rc.confirm_mismatch", ...}`` when ``confirm`` is a string other
      than the typed phrase -- before anything runs, never the framework's
      ``{"detail": [...]}`` list.
    """
    known_fields = list(UnwindRcBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request,
            "unwind-rc",
            exc,
            '{"project": "my-app", "key": "v1.2.3", "confirm": true, "dry_run": true}',
            warnings,
        )

    refused = _component_refusal(
        request,
        "unwind-rc",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "v1.2.3", "confirm": true, "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    typed_phrase = f"unwind-rc {body.project} {body.key}"
    example_body: dict = {"project": body.project, "key": body.key}
    if body.force:
        example_body["force"] = True
    example_body.update({"confirm": True, "dry_run": True})

    # Confirm-gate: a live call must carry the confirmation, whether or not
    # it also escalates with force. A dry-run preview runs nothing and so
    # needs no confirmation.
    if not body.dry_run:
        if body.confirm is None or body.confirm is False or body.confirm == "":
            # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by
            # design -- the CLI's interactive prompt, which an HTTP request
            # cannot answer, raised one layer earlier. A body without
            # 'confirm': true (force included) is refused here, dual-channel,
            # before unwind_rc runs.
            # CENSUS-ANCHOR: operations-unwind-rc-confirm-02
            return _refusal_response(
                HTTP_UNPROCESSABLE_ENTITY,
                "unwind_rc.confirm_required",
                f"unwind-rc would unwind RC {body.key} of project {body.project} across "
                "every state store; the CLI asks for confirmation interactively and an "
                "HTTP call cannot answer that prompt, so 'confirm': true is required in "
                "the body (or 'dry_run': true to preview). 'force' never stands in for it.",
                _example_curl(request, json.dumps(example_body)),
                warnings,
            )
        if body.confirm is not True and body.confirm != typed_phrase:
            # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by
            # design -- a confirm string that is not byte-for-byte the typed
            # phrase is not a confirmation, refused here, dual-channel,
            # before unwind_rc runs.
            # CENSUS-ANCHOR: operations-unwind-rc-confirm-01
            return _refusal_response(
                HTTP_UNPROCESSABLE_ENTITY,
                "unwind_rc.confirm_mismatch",
                f"confirm mismatch: expected '{typed_phrase}', "
                f"got '{body.confirm}'. "
                "The confirm field must be true, or the string "
                "'unwind-rc <project> <key>' exactly.",
                _example_curl(request, json.dumps(example_body)),
                warnings,
            )

    script = _script_path(request, "unwind-rc.sh")
    # The CLI call a confirmed request is equivalent to: --confirm always
    # (the gate above is what authorised it), --force only when asked for.
    flags: dict = {"project": body.project, "key": body.key, "confirm": True}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    # stdout and stderr go to lists rather than through ops_call's stream
    # capture, so the unwind's git steps hold no process-wide lock.
    out: list[str] = []
    err: list[str] = []
    with ops_call(capture=False) as outcome:
        cm_unwind.unwind_rc(
            _ops_context(request),
            body.project,
            body.key,
            confirm=True,
            force=bool(body.force),
            out=out.append,
            err=err.append,
        )
    outcome.stdout = "".join(out)
    outcome.stderr = "".join(err)
    if not outcome.ok:
        outcome.stderr += getattr(outcome.error, "text", "")
        outcome.exit_code = cm_unwind.unwind_exit_code(outcome.error)
    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="unwind_rc.refused",
        fallback_remedy=_cli_remedy("unwind-rc.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/set-version-ceiling
# ---------------------------------------------------------------------------


@router.post(
    "/set-version-ceiling",
    summary="Read or set max_minor / max_major version ceilings for a project",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the set-version-ceiling "
        "operation (the same text and exit code set-version-ceiling.sh produces)."
    ),
)
def post_set_version_ceiling(body: SetVersionCeilingBody, request: Request) -> JSONResponse:
    """Read or mutate max_minor / max_major version ceilings in a project's project.cfg.

    Resolves ``project`` as set-version-ceiling.sh does, then calls
    ``pgai_agent_kanban.ops.write.set_version_ceiling`` -- the function the
    script reaches through ``pp_run_ops`` -- which validates the values and
    shows or edits the ``[versioning]`` section; no subprocess is started.
    ``stdout`` carries the lines the script prints (``max_minor=...`` for
    ``show``: the value in effect, ``13 (default)`` for an absent key, or
    ``none``). The refusals (unknown project, no action, a value that is not
    a non-negative integer, no project.cfg) land in the envelope as the
    script prints them.

    Body fields:

    - ``project``  (required) — project name or unambiguous prefix.
    - ``show``     (optional, bool) — when True, passes ``--show`` to print current values.
    - ``minor``    (optional, int) — sets ``max_minor``; passed as ``--minor N``.
    - ``major``    (optional, int) — sets ``max_major``; passed as ``--major N``.
    - ``no_minor`` (optional, bool) — when True, passes ``--no-minor`` to write
                                       ``max_minor = none`` (no minor ceiling).
    - ``no_major`` (optional, bool) — when True, passes ``--no-major`` to write
                                       ``max_major = none`` (no major ceiling).
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing project.cfg.

    At least one action (``show``, ``minor``, ``major``, ``no_minor``, or
    ``no_major``) is required.  Supplying none returns exit_code 1 with the
    refusal line in stderr (the CLI also prints its usage text after it).

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused -- the dual-channel triple in ``stderr``
      (``project_resolve.not_found``, ``set_version_ceiling.invalid_ceiling``,
      for two) lifted to top-level keys, or the plain stderr text as
      ``message`` under the stable fallback key ``set_version_ceiling.refused``
      with a runnable CLI remedy (``_make_dual_channel_envelope``);
      ``{"error": "set_version_ceiling.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before anything runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure.
    """
    known_fields = list(SetVersionCeilingBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "set-version-ceiling", exc, '{"project": "my-app", "show": true}', warnings
        )

    refused = _component_refusal(
        request,
        "set-version-ceiling",
        {"project": body.project},
        '{"project": "my-app", "show": true}',
        warnings,
    )
    if refused is not None:
        return refused

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

    ctx = _ops_context(request)
    project, outcome = _resolve_project(ctx, body.project)
    if project is not None:
        with ops_call() as outcome:
            report = ops_write.set_version_ceiling(
                ctx,
                project,
                show=bool(body.show),
                minor=body.minor,
                major=body.major,
                no_minor=bool(body.no_minor),
                no_major=bool(body.no_major),
            )
        if outcome.ok:
            if report.lines:
                outcome.stdout += f"{report.narration}\n"
        else:
            outcome.exit_code = ops_write.set_version_ceiling_exit_code(outcome.error)

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="set_version_ceiling.refused",
        fallback_remedy=_cli_remedy("set-version-ceiling.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/switch-provider
# ---------------------------------------------------------------------------


@router.post(
    "/switch-provider",
    summary="Switch the active LLM provider for the kanban framework",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the switch-provider "
        "operation (the same text and exit code switch-provider.sh produces)."
    ),
)
def post_switch_provider(body: SwitchProviderBody, request: Request) -> JSONResponse:
    """Switch the active LLM provider for the kanban framework.

    Calls ``pgai_agent_kanban.ops.write.switch_provider`` -- the function
    switch-provider.sh reaches through ``pp_run_ops`` -- which validates the
    name, checks the provider's CLI is on this server's PATH and rewrites
    kanban.cfg ``[providers] active``; no subprocess is started. ``stdout``
    carries the confirmation the script prints; every refusal reports
    exit_code 1, as the script does.

    Body fields:

    - ``provider`` (required) — LLM provider name (e.g. ``claude``, ``codex``, ``gemini``),
                               any case. An unrecognized name is refused with
                               ``switch_provider.unknown_provider``.
    - ``dry_run``  (optional, bool) — when True, returns the planned action description
                                       without writing kanban.cfg.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused at exit code 1 -- the dual-channel triple in
      ``stderr`` (``switch_provider.unknown_provider``) lifted to top-level
      keys, or the plain stderr text as ``message`` under the stable fallback
      key ``switch_provider.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "switch_provider.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before anything runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500) (unknown provider, CLI not in PATH),
    422 on validation failure (empty provider string).
    """
    known_fields = list(SwitchProviderBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"provider": body.provider}, required_keys=["provider"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "switch-provider", exc, '{"provider": "claude", "dry_run": true}', warnings
        )

    script = _script_path(request, "switch-provider.sh")
    flags: dict = {"provider": body.provider}
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    # switch-provider.sh exits 1 on every refusal the function raises.
    with ops_call(exit_code=1) as outcome:
        change = ops_write.switch_provider(ctx, body.provider)
    if outcome.ok:
        outcome.stdout += f"{change.narration}\n"

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="switch_provider.refused",
        fallback_remedy=_cli_remedy("switch-provider.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/create-project
# ---------------------------------------------------------------------------


@router.post(
    "/create-project",
    summary="Bootstrap a new project in the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the create-project "
        "operation (the same text and exit code create-project.sh produces)."
    ),
)
def post_create_project(body: CreateProjectBody, request: Request) -> JSONResponse:
    """Bootstrap a new project directory and register it in projects.cfg.

    Calls ``pgai_agent_kanban.ops.projects.create_project`` -- the function
    create-project.sh reaches through ``pp_run_ops`` -- which validates the
    values, the name and the workflow type, then lays down the project and
    registers it; no subprocess is started. ``stdout`` carries the plan and
    progress lines the script prints. Its refusals (project directory
    already exists -- exit 2 --, invalid workflow type, bad ceiling or
    colour values, empty path values -- exit 1) land in the envelope as the
    script prints them.

    The ``dev_tree`` and ``git_repo`` body fields are written verbatim into
    the generated project.cfg's ``dev_tree_path``/``git_repo_url`` fields
    when supplied.  A ``dev_tree`` path that does not currently exist on the
    host produces a WARN on stderr but the value is still written and the
    call exits 0.

    Before the dry-run description, the values are checked as the script's
    ops function checks them: ``workflow_type`` must be one plain name;
    ``git_remote``, ``dev_tree`` and ``git_repo`` may hold no newline or
    other control character; ``dev_tree`` must be absolute with no ``..``
    segment. A refused value returns 422 with
    ``create_project.<field>_invalid`` naming the field.

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
    - ``dev_tree``      (optional) — written verbatim to project.cfg's dev_tree_path;
                                      a nonexistent path WARNs but still writes.
    - ``git_repo``      (optional) — written verbatim to project.cfg's git_repo_url.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused -- its plain ``ERROR:`` text as ``message`` under
      the stable fallback key ``create_project.refused`` with a runnable CLI
      remedy (``_make_dual_channel_envelope``);
      ``{"error": "create_project.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, and the same shape under
      ``create_project.<field>_invalid`` for a refused value, before
      anything runs -- never the framework's ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure.
    """
    known_fields = list(CreateProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "create-project", exc, '{"project": "my-app", "dry_run": true}', warnings
        )

    refused = _component_refusal(
        request,
        "create-project",
        {"project": body.project},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused
    try:
        ops_projects.check_create_values(
            project=body.project,
            workflow_type=body.workflow_type or None,
            git_remote=body.git_remote or None,
            dev_tree=body.dev_tree or None,
            git_repo=body.git_repo or None,
        )
    except ops_projects.ProjectValueRefused as exc:
        return _project_value_refusal(request, exc, warnings)

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

    ctx = _ops_context(request)
    with ops_call() as outcome:
        ops_projects.create_project(
            ctx,
            body.project,
            workflow_type=body.workflow_type or None,
            max_patch=body.max_patch,
            max_minor=body.max_minor,
            max_major=body.max_major,
            git_remote=body.git_remote or None,
            priority=body.priority,
            color=body.color or None,
            dev_tree=body.dev_tree or None,
            git_repo=body.git_repo or None,
            no_migrate=bool(body.no_migrate),
        )
    if not outcome.ok:
        outcome.exit_code = ops_projects.projects_exit_code(outcome.error)

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="create_project.refused",
        fallback_remedy=_cli_remedy("create-project.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/add-project
# ---------------------------------------------------------------------------


@router.post(
    "/add-project",
    summary="Register an existing on-disk project directory in the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the add-project "
        "operation (the same text and exit code add-project.sh produces)."
    ),
)
def post_add_project(body: AddProjectBody, request: Request) -> JSONResponse:
    """Register an existing project directory in projects.cfg.

    Calls ``pgai_agent_kanban.ops.projects.add_project`` -- the function
    add-project.sh reaches through ``pp_run_ops``; no subprocess is
    started.  The project directory must already exist.  Besides the
    registry entry, a project directory with no ``README.md`` gets one
    seeded from the project README template, registered already or not;
    an existing ``README.md`` is never overwritten, and nothing else under
    the project directory is touched; ``dry_run`` only reports the seeding.
    ``stdout`` carries the plan and result lines the script prints; a
    project already registered at the same priority with a ``README.md``
    present is "nothing to do" at exit 0.  The refusals (directory
    not found -- exit 2 --, a colour that is not ``#RRGGBB`` -- exit 1)
    land in the envelope as the script prints them.

    Body fields:

    - ``project``    (required) — project name.
    - ``priority``   (optional, int) — registry priority (next available if absent).
    - ``color``      (optional) — registry display color (e.g. ``'#RRGGBB'``).
    - ``no_migrate`` (optional, bool) — when True, suppresses projects.cfg migration.
    - ``dry_run``    (optional, bool) — when True, returns the planned action description
                                         without mutating registry.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused -- its plain ``ERROR:`` text as ``message`` under
      the stable fallback key ``add_project.refused`` with a runnable CLI
      remedy (``_make_dual_channel_envelope``);
      ``{"error": "add_project.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, and the same shape under ``add_project.project_invalid``
      for a ``project`` that is not one name a projects.cfg section can hold
      (letters, digits, ``_`` and ``-``), before anything runs -- never the
      framework's ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure.
    """
    known_fields = list(AddProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "add-project", exc, '{"project": "my-app", "dry_run": true}', warnings
        )

    refused = _component_refusal(
        request,
        "add-project",
        {"project": body.project},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused
    try:
        ops_projects.check_project_name(
            "add_project", body.project, request.app.state.api_cfg.kanban_root
        )
    except ops_projects.ProjectValueRefused as exc:
        return _project_value_refusal(request, exc, warnings)

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

    ctx = _ops_context(request)
    with ops_call() as outcome:
        ops_projects.add_project(
            ctx,
            body.project,
            priority=body.priority,
            color=body.color or None,
            no_migrate=bool(body.no_migrate),
        )
    if not outcome.ok:
        outcome.exit_code = ops_projects.projects_exit_code(outcome.error)

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="add_project.refused",
        fallback_remedy=_cli_remedy("add-project.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/remove-project
# ---------------------------------------------------------------------------


@router.post(
    "/remove-project",
    summary="Unregister a project from the kanban registry",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr of the remove-project "
        "operation (the same text and exit code remove-project.sh produces)."
    ),
)
def post_remove_project(body: RemoveProjectBody, request: Request) -> JSONResponse:
    """Unregister a project from projects.cfg; with force, also delete its directory.

    Calls ``pgai_agent_kanban.ops.projects.remove_project`` -- the function
    remove-project.sh reaches through ``pp_run_ops`` -- and follows its
    safety ladder; no subprocess is started. The default call (no
    ``force``) is the safe mode: the project is unregistered and its
    directory is left in place. ``force`` is the destructive escalation:
    the directory is deleted too. Because that is irreversible, a live
    ``force`` call must carry ``confirm`` equal to exactly
    ``remove-project <project>`` (byte-for-byte -- no trimming, no
    case-folding); a missing or wrong string is refused 422 here before
    anything runs.

    In both modes a project whose release-state.md records an Active RC is
    refused before anything changes: a 500 envelope at exit code 2 carrying
    ``remove_project.active_rc`` (or ``remove_project.release_state_invalid``
    when that field does not validate) -- the same block the CLI prints,
    with or without ``--force``.

    Body fields:

    - ``project`` (required) — project name.
    - ``force``   (optional, bool) — when True, also deletes the project
                  directory. The Active-RC guard still holds. Requires
                  ``confirm`` unless ``dry_run`` is true.
    - ``confirm`` (required when ``force`` is true and ``dry_run`` is not) — must
                  equal exactly ``remove-project <project>``. Any mismatch → 422
                  naming ``confirm``, nothing runs. Ignored when ``force``
                  is not true.
    - ``dry_run`` (optional, bool) — when True, returns the planned action description
                  without removing anything; neither ``force`` nor ``confirm`` is
                  required for a preview.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      operation refused -- the dual-channel triple in ``stderr``
      (``remove_project.active_rc``) lifted to top-level keys, or the plain
      stderr text as ``message`` under the stable fallback key
      ``remove_project.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "remove_project.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, or ``{"error": "remove_project.confirm_mismatch", ...}``
      when ``force`` is true and ``confirm`` is present but wrong -- before
      anything runs, never the framework's ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation failure
    or when the confirm gate refuses a force call.
    """
    known_fields = list(RemoveProjectBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "remove-project", exc, '{"project": "my-app", "dry_run": true}', warnings
        )

    refused = _component_refusal(
        request,
        "remove-project",
        {"project": body.project},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused
    # The name check remove_project itself runs, made before the confirm
    # gate and the dry run, so a dry run refuses exactly the names the real
    # call refuses (422 in both modes).
    try:
        ops_projects.check_project_name(
            "remove_project", body.project, request.app.state.api_cfg.kanban_root
        )
    except ops_projects.ProjectValueRefused as exc:
        return _project_value_refusal(request, exc, warnings)

    # Confirm-gate on the destructive escalation only: a live force call
    # must carry the exact confirm string. The safe default mode and a
    # dry-run preview need no confirmation.
    if body.force and not body.dry_run:
        expected_confirm = f"remove-project {body.project}"
        example_body = json.dumps(
            {"project": body.project, "force": True, "confirm": expected_confirm, "dry_run": True}
        )
        try:
            validate_required({"confirm": body.confirm}, required_keys=["confirm"])
        except ValidationError as exc:
            return _missing_field_refusal(request, "remove-project", exc, example_body, warnings)
        if body.confirm != expected_confirm:
            # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by
            # design -- force deletes the project directory, which cannot be
            # undone, so a confirm string that is not byte-for-byte the
            # expected one is refused here, dual-channel, before anything runs.
            # CENSUS-ANCHOR: operations-remove-project-confirm-01
            return _refusal_response(
                HTTP_UNPROCESSABLE_ENTITY,
                "remove_project.confirm_mismatch",
                f"confirm mismatch: expected '{expected_confirm}', got '{body.confirm}'. "
                "The confirm string must equal 'remove-project <project>' exactly "
                "when force is true.",
                _example_curl(request, example_body),
                warnings,
            )

    script = _script_path(request, "remove-project.sh")
    flags: dict = {"project": body.project}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    ctx = _ops_context(request)
    with ops_call() as outcome:
        ops_projects.remove_project(ctx, body.project, force=bool(body.force))
    if not outcome.ok:
        outcome.exit_code = ops_projects.projects_exit_code(outcome.error)

    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="remove_project.refused",
        fallback_remedy=_cli_remedy("remove-project.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/ship-rc
# ---------------------------------------------------------------------------


@router.post(
    "/ship-rc",
    summary="Ship a release candidate (double-gated)",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from cm.ship.ship_rc "
        "(the ship-rc.sh output); on a refused call the body also carries "
        "error, message and remedy."
    ),
)
def post_ship_rc(body: ShipRcBody, request: Request) -> JSONResponse:
    """Ship a release candidate by squash-merging it directly into main.

    The REST twin of the ``ship-rc`` verb. Calls
    ``pgai_agent_kanban.cm.ship.ship_rc`` -- the function ship-rc.sh reaches
    through ``pp_run_ops`` -- in process; the route itself starts no process
    (the git commands and release hooks the ship runs are the ship's own).
    Its stdout and stderr are collected through callbacks, without capturing
    the server's own streams.

    This is the one origin-touching verb in the operator API.  To prevent
    accidental invocation, the endpoint requires a ``confirm`` string whose
    value must equal exactly ``ship-rc <project> <key>`` (byte-for-byte —
    no trimming, no case-folding).  The confirm-gate fires at the API layer
    BEFORE ship_rc runs: a wrong or missing confirm string returns 422 and
    never reaches it. The verb takes no ``force``.

    Body fields:

    - ``project``  (required) — project name (the CLI's ``--project``).
    - ``key``      (required) — release version in format ``vX.Y.Z``
                               (the CLI's ``--key``).
    - ``confirm``  (required) — must equal exactly ``ship-rc <project> <key>``
                               (byte-for-byte).  Any mismatch → 422 naming
                               ``confirm``; nothing runs.
    - ``dry_run``  (optional, bool) — when True, ship_rc prints the plan and
                               returns 0 without any git step (the CLI's
                               ``--dry-run``), rather than the dispatch-layer
                               short-circuit.

    Response envelope (exit_code 0):
      ``{"exit_code": int, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when
      ship_rc refused, halted or stopped -- a dual-channel block in
      ``stderr`` (the project resolver's, or a release-hook HALT's) lifted
      out as top-level keys, or its plain stderr text as ``message`` under
      the stable fallback key ``ship_rc.refused`` with a runnable CLI remedy
      (``_make_dual_channel_envelope``);
      ``{"error": "ship_rc.<field>_required", "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused a body missing a
      required field, before anything runs -- never the framework's
      ``{"detail": [...]}`` list.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on validation
    failure or confirm-gate mismatch.
    """
    known_fields = list(ShipRcBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key, "confirm": body.confirm},
            required_keys=["project", "key", "confirm"],
        )
    except ValidationError as exc:
        return _missing_field_refusal(
            request,
            "ship-rc",
            exc,
            '{"project": "my-app", "key": "v1.2.3", '
            '"confirm": "ship-rc my-app v1.2.3", "dry_run": true}',
            warnings,
        )

    refused = _component_refusal(
        request,
        "ship-rc",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "v1.2.3", "confirm": "ship-rc my-app v1.2.3", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    # Confirm-gate: byte-for-byte exact match — no trimming, no case-folding.
    expected_confirm = f"ship-rc {body.project} {body.key}"
    if body.confirm != expected_confirm:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by
        # design -- the one origin-touching verb in this API is double-gated,
        # and a confirm string that is not byte-for-byte the expected one
        # is refused here, dual-channel, before ship_rc runs.
        # CENSUS-ANCHOR: operations-ship-rc-confirm-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "ship_rc.confirm_mismatch",
            f"confirm mismatch: expected 'ship-rc {body.project} {body.key}', "
            f"got '{body.confirm}'. "
            "The confirm string must equal 'ship-rc <project> <key>' exactly.",
            _example_curl(
                request,
                json.dumps(
                    {
                        "project": body.project,
                        "key": body.key,
                        "confirm": expected_confirm,
                        "dry_run": True,
                    }
                ),
            ),
            warnings,
        )

    script = _script_path(request, "ship-rc.sh")
    # The CLI call this request is equivalent to, for the fallback remedy.
    flags: dict = {"project": body.project, "key": body.key}
    if body.dry_run:
        flags["dry-run"] = True
    planned_argv = build_argv(script, flags)

    # ship_rc's dry run prints the plan itself (no git step runs), so the
    # dispatch-layer short-circuit is not used. stdout and stderr go to lists
    # rather than through ops_call's stream capture, so the ship's git steps
    # and hooks hold no process-wide lock.
    out: list[str] = []
    err: list[str] = []
    with ops_call(capture=False) as outcome:
        cm_ship.ship_rc(
            _ops_context(request),
            body.project,
            body.key,
            dry_run=body.dry_run,
            out=out.append,
            err=err.append,
        )
    outcome.stdout = "".join(out)
    outcome.stderr = "".join(err)
    if not outcome.ok:
        outcome.stderr += getattr(outcome.error, "text", "")
        outcome.exit_code = cm_ship.ship_exit_code(outcome.error)
    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="ship_rc.refused",
        fallback_remedy=_cli_remedy("ship-rc.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/deep-verify
# ---------------------------------------------------------------------------


@router.post(
    "/deep-verify",
    summary="Run the DEEP-VERIFY tier for a project on demand (backgrounded)",
    response_description=(
        "JSON envelope confirming the backgrounded run started; NEVER a verdict."
    ),
)
def post_deep_verify(body: DeepVerifyBody, request: Request) -> JSONResponse:
    """Start the DEEP-VERIFY tier's on-demand run for a project.

    Unlike every other operation in this router, a real (non-dry-run) call
    does NOT wait for the underlying script to finish: deep-verify.sh itself
    blocks for the duration of the cold-build lane (budget ~13 minutes), and
    holding an HTTP connection open that long is the exact case this endpoint
    exists to avoid. The script is launched detached
    (see ``_spawn_detached`` above) and this handler returns as soon as the
    child process starts. The response confirms the run STARTED; it never
    reports a verdict, because the lane has not produced one yet. Poll the
    project's deep-verify ledger (``$KANBAN_ROOT/projects/<name>/deep-verify-
    ledger.md``) or ``GET /verdicts`` once the backgrounded run completes.

    Body fields:

    - ``project``   (required unless ``scheduled`` is true with no project —
                     the scheduled fan-out mode) — project whose ledger the
                     run is filed under.
    - ``scheduled`` (optional, bool) — passed as ``--scheduled``; self-gates
                     on the project's ledger age and off-hours window.
    - ``image``     (optional, list of names or ``"all"``) — the container
                     images this call builds, from
                     team/config/container-images.txt; one ``--image <entry>``
                     per entry, in order. Omitted: no ``--image``, so
                     kanban.cfg [release] container_images applies, else the
                     script lists the images and builds nothing. See the
                     module docstring's "Container image selection" note.
    - ``dry_run``   (optional, bool) — when True, returns the planned action
                     description; no subprocess (detached or otherwise) is
                     started.

    Response envelope (real invocation):
      ``{"exit_code": 0, "stdout": "deep-verify: started ...", "stderr": "", "warnings": list}``

    Response on a refused call (422, ``project`` missing and ``scheduled`` not true):
      ``{"error": "deep_verify.project_required", "message": str, "remedy": str, "warnings": list}``

    Response on a refused call (422, ``image`` names an undeclared image):
      ``{"error": "container_tier.unknown_image", "message": str, "remedy": str, "warnings": list}``

    HTTP status: 200 once the detached run has started (dispatch success, NOT
    a verdict), 422 when ``project`` is missing and ``scheduled`` is not true
    or when ``image`` names an image the manifest does not declare (both
    refused before anything is spawned, ``dry_run`` included).
    A start failure (e.g. the script is not executable) raises rather than
    returning a fabricated envelope — there is no completed subprocess to
    report exit_code/stdout/stderr from.
    """
    known_fields = list(DeepVerifyBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    if not body.scheduled and not body.project:
        # BLOCK-WORTHY (docs/blocking-site-census.md): the same conditional
        # requirement deep-verify.sh itself enforces -- a non-scheduled run
        # is filed under one named project's ledger. Refused here,
        # dual-channel, so the refusal reaches the HTTP caller instead of a
        # detached child's log nobody is reading.
        # CENSUS-ANCHOR: operations-deep-verify-project-required-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "deep_verify.project_required",
            "'project' is required unless 'scheduled' is true with no "
            "project (the scheduled fan-out mode, which evaluates every "
            "active registered project). Supply 'project', or set "
            "'scheduled': true to fan out.",
            _example_curl(request, '{"project": "my-app", "dry_run": true}'),
            warnings,
        )

    refused = _component_refusal(
        request,
        "deep-verify",
        {"project": body.project},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    image_entries = _image_entries(body.image)
    example_body: dict = {}
    if body.project:
        example_body["project"] = body.project
    if body.scheduled:
        example_body["scheduled"] = True
    example_body["image"] = container_images.SELECT_ALL
    if body.dry_run:
        example_body["dry_run"] = True
    refusal = _unknown_image_refusal(
        request, "deep-verify.sh", image_entries, example_body, warnings
    )
    if refusal is not None:
        return refusal

    script = _script_path(request, "deep-verify.sh")
    flags: dict = {}
    if body.project:
        flags["project"] = body.project
    if body.scheduled:
        flags["scheduled"] = True
    planned_argv = build_argv(script, flags) + _image_argv(image_entries)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    label = body.project or "scheduled-fanout"
    log_path = route_temp_root(request) / "deep_verify" / f"{label}-{uuid.uuid4().hex[:12]}.log"
    pid = _spawn_detached(planned_argv, log_path)

    return JSONResponse(
        content={
            "exit_code": 0,
            "stdout": (
                f"deep-verify: started for {label} (pid {pid}); this run has "
                "not completed and no verdict is available in this response "
                "-- poll the project's deep-verify ledger (or GET /verdicts) "
                f"once it finishes. Combined output is being captured to "
                f"{log_path}."
            ),
            "stderr": "",
            "warnings": warnings,
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# POST /operations/wake-now
# ---------------------------------------------------------------------------


@router.post(
    "/wake-now",
    summary="Fire one or all agents' wake cycle immediately",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from wake-now.sh."
    ),
)
def post_wake_now(body: WakeNowBody, request: Request) -> JSONResponse:
    """Fire one or all pipeline agents' wake cycle outside their cron cadence.

    Delegates to wake-now.sh -- the mutual-exclusivity refusal between
    ``agent`` and ``all``, and the "one of them is required" refusal when
    neither is supplied, are the script's own guards and propagate unchanged
    in the envelope's foreground path rather than being re-implemented here.

    See the module docstring's "Long-running verbs" note for WHY ``bg``/
    ``all`` take a different dispatch path than every other endpoint in this
    router: wake-now.sh's own backgrounding (a bare ``&``) inherits
    shell_out()'s capture pipes instead of closing them, so a plain
    shell_out() call on ``bg``/``all`` would silently block for the fired
    agent's ENTIRE wake cycle -- exactly the many-minutes-blocking case this
    ticket exists to avoid. ``bg``/``all`` therefore launch wake-now.sh
    detached (like POST /operations/deep-verify) and return as soon as the
    fork completes; the response confirms wake-now.sh STARTED, not that any
    fired agent finished. Supplying neither ``bg`` nor ``all`` uses the
    normal blocking shell_out() dispatch -- wake-now.sh execs wake-batch.sh
    directly in that mode (no background job, no pipe-inheritance hazard),
    so this is a faithful, non-gentler mirror of the bare CLI's own
    foreground contract.

    Body fields:

    - ``agent``   (required unless ``all`` is true) — agent role to wake
                  (``pm``, ``coder``, ``cm``, ``writer``, ``tester``,
                  ``overwatch``).
    - ``all``     (optional, bool) — fire every pipeline agent. Detached path.
    - ``bg``      (optional, bool) — background the single named agent's wake
                  cycle. Detached path. Recommended for HTTP callers over
                  foreground mode.
    - ``dry_run`` (optional, bool) — when True, returns the planned action
                  description without firing anything (checked before ``bg``/
                  ``all`` route to the detached path).

    Response envelope:
      - Detached path (``bg`` or ``all``): ``{"exit_code": 0, "stdout":
        "wake-now: started ...", "stderr": "", "warnings": list}`` — always
        200, confirming dispatch started, never that the fired agent(s)
        finished.
      - Foreground path (neither ``bg`` nor ``all``): the normal envelope
        with wake-now.sh's own completed exit_code/stdout/stderr.

    HTTP status: 200 on the detached path or on foreground exit_code 0, 500
    on a non-zero foreground exit_code (including wake-now.sh's own
    agent/all guards).
    """
    known_fields = list(WakeNowBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    refused = _component_refusal(
        request,
        "wake-now",
        {"agent": body.agent},
        '{"agent": "coder", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "wake-now.sh")
    flags: dict = {}
    if body.agent:
        flags["agent"] = body.agent
    if body.all:
        flags["all"] = True
    if body.bg:
        flags["bg"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    if body.all or body.bg:
        label = "all pipeline agents" if body.all else f"agent '{body.agent}'"
        log_path = route_temp_root(request) / "wake_now" / f"{uuid.uuid4().hex[:12]}.log"
        pid = _spawn_detached(planned_argv, log_path)
        return JSONResponse(
            content={
                "exit_code": 0,
                "stdout": (
                    f"wake-now: started for {label} (pid {pid}); this call "
                    "does not wait for the fired agent's wake cycle to "
                    "finish -- combined output (including each fired "
                    f"agent's own PID line) is being captured to {log_path}."
                ),
                "stderr": "",
                "warnings": warnings,
            },
            status_code=200,
        )

    # No [api] shell_out_timeout_seconds bound here: a foreground wake-now is
    # a whole wake batch, which bounds itself ([wake] max_runtime_seconds and
    # the per-task watchdog), and stopping it at the API's bound would cut a
    # dispatched agent off mid-task.
    result = shell_out(script, flags, timeout_seconds=None)
    return _make_dual_channel_envelope(
        result,
        warnings,
        fallback_error="wake_now.refused",
        fallback_remedy=_cli_remedy("wake-now.sh", planned_argv),
    )


# ---------------------------------------------------------------------------
# POST /operations/cold-build-now
# ---------------------------------------------------------------------------


@router.post(
    "/cold-build-now",
    summary="Run the cold-build lane for a project on demand (backgrounded)",
    response_description=(
        "JSON envelope confirming the backgrounded run started; NEVER a verdict. "
        "On a refused call the body carries error, message and remedy."
    ),
)
def post_cold_build_now(body: ColdBuildNowBody, request: Request) -> JSONResponse:
    """Start the cold-build lane for a project, outside any RC.

    The REST twin of the ``cold-build-now`` verb. cold-build-now.sh is a
    named alias that execs ``deep-verify.sh --project NAME`` -- same flag,
    same work, same exit codes -- so this route takes exactly the detached
    path ``POST /operations/deep-verify`` takes (module docstring, "Long-
    running verbs"): the script is launched with ``_spawn_detached`` and
    the handler returns as soon as the child starts. The response confirms
    the run STARTED; it never reports a verdict. Poll the project's
    deep-verify ledger, ``GET /deep-verify-status``, or ``GET /verdicts``
    once the backgrounded run completes. A refusal the script raises after
    dispatch (an unregistered project, say) lands in the captured log file
    the response names and in the ledger -- it cannot be carried in this
    response, exactly as for deep-verify.

    Body fields:

    - ``project`` (required) — project whose ledger the run is filed under.
    - ``image``   (optional, list of names or ``"all"``) — the container
                   images this call builds, from
                   team/config/container-images.txt; one ``--image <entry>``
                   per entry, in order, which cold-build-now.sh passes on to
                   deep-verify.sh. Omitted: no ``--image``, so kanban.cfg
                   [release] container_images applies, else the script lists
                   the images and builds nothing. See the module docstring's
                   "Container image selection" note.
    - ``dry_run`` (optional, bool) — when True, returns the planned action
                   description; no subprocess (detached or otherwise) is
                   started.

    Response envelope (real invocation):
      ``{"exit_code": 0, "stdout": "cold-build-now: started ...", "stderr": "", "warnings": list}``

    Response on a refused call (422, missing ``project``):
      ``{"error": "cold_build_now.project_required", "message": str, "remedy": str, "warnings": list}``

    Response on a refused call (422, ``image`` names an undeclared image):
      ``{"error": "container_tier.unknown_image", "message": str, "remedy": str, "warnings": list}``

    HTTP status: 200 once the detached run has started (dispatch success,
    NOT a verdict); 422 when ``project`` is missing or empty, or when
    ``image`` names an image the manifest does not declare (both refused
    before anything is spawned, ``dry_run`` included). A start failure
    (e.g. the script is not executable) raises rather than returning a
    fabricated envelope.
    """
    known_fields = list(ColdBuildNowBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    if not body.project:
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "cold_build_now.project_required",
            "cold-build-now requires 'project': the cold-build lane is run for "
            "one named project and its verdict is filed under that project's "
            "deep-verify ledger; no project was named.",
            _example_curl(request, '{"project": "my-app", "dry_run": true}'),
            warnings,
        )

    refused = _component_refusal(
        request,
        "cold-build-now",
        {"project": body.project},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    image_entries = _image_entries(body.image)
    example_body: dict = {"project": body.project, "image": container_images.SELECT_ALL}
    if body.dry_run:
        example_body["dry_run"] = True
    refusal = _unknown_image_refusal(
        request, "cold-build-now.sh", image_entries, example_body, warnings
    )
    if refusal is not None:
        return refusal

    script = _script_path(request, "cold-build-now.sh")
    flags: dict = {"project": body.project}
    planned_argv = build_argv(script, flags) + _image_argv(image_entries)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    log_path = (
        route_temp_root(request) / "cold_build_now" / f"{body.project}-{uuid.uuid4().hex[:12]}.log"
    )
    pid = _spawn_detached(planned_argv, log_path)

    return JSONResponse(
        content={
            "exit_code": 0,
            "stdout": (
                f"cold-build-now: started for {body.project} (pid {pid}); this "
                "run has not completed and no verdict is available in this "
                "response -- poll the project's deep-verify ledger (GET "
                "/deep-verify-status or GET /verdicts) once it finishes. "
                f"Combined output is being captured to {log_path}."
            ),
            "stderr": "",
            "warnings": warnings,
        },
        status_code=200,
    )


# ---------------------------------------------------------------------------
# POST /operations/cancel-rc
# ---------------------------------------------------------------------------


@router.post(
    "/cancel-rc",
    summary="Cancel an active release candidate branch and reset release-state.md",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from cm.cancel.cancel_rc "
        "(the cm/cancel-rc.sh output); on a refused call the body also carries "
        "error, message and remedy."
    ),
)
def post_cancel_rc(body: CancelRcBody, request: Request) -> JSONResponse:
    """Cleanly abandon an in-flight RC: delete its branch, reset release-state.md.

    The REST twin of the ``cancel-rc`` verb. Calls
    ``pgai_agent_kanban.cm.cancel.cancel_rc`` -- the function
    cm/cancel-rc.sh reaches through ``pp_run_ops`` -- in process; the route
    itself starts no process (the git commands the cancel runs are the
    cancel's own). Everything the function decides -- version format,
    current-branch check, Active-RC match, idempotent no-op when already
    cancelled -- propagates unchanged: it refuses with plain stderr lines
    (no house-shape block), so on a non-zero exit that text is returned as
    ``message`` under the stable key ``cancel_rc.refused`` with
    ``stdout``/``stderr`` carried in full, byte-for-byte what the CLI
    prints. Both streams are collected through callbacks, without
    capturing the server's own.

    The one API-layer gate is the confirmation. The CLI asks "Proceed?"
    interactively unless ``--yes`` is given; an HTTP request has no
    terminal to answer, so the body's ``yes`` field is that answer. When
    ``dry_run`` is not True, ``yes`` must be True or the route refuses 422
    before anything runs -- the same explicit-opt-in shape
    ``POST /operations/remove-project`` applies to ``force``. The API is
    not gentler than the CLI: the CLI would have stopped at the prompt.

    Body fields:

    - ``project`` (required) — project name (the CLI's ``--project``).
    - ``key``     (required) — RC version to cancel (``vX.Y.Z``; the CLI's
                   ``--key``); its format is checked by cancel_rc, not here.
    - ``yes``     (required unless ``dry_run`` is true) — the confirmation
                   an HTTP caller cannot give interactively (``--yes``).
    - ``force``   (optional, bool) — as ``--force``: bypass only the
                   Active-RC mismatch check. Never a confirmation; ``yes``
                   is still required.
    - ``dry_run`` (optional, bool) — when True, returns the planned action
                   description without changing anything; ``yes`` is not
                   required for a preview.

    Response envelope (exit_code 0):
      ``{"exit_code": 0, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      script refused; ``{"error": str, "message": str, "remedy": str,
      "warnings": list}`` at 422 when this layer refused (missing
      ``project``/``key``, or ``yes`` not given).

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on an API-layer
    refusal.
    """
    known_fields = list(CancelRcBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required(
            {"project": body.project, "key": body.key},
            required_keys=["project", "key"],
        )
    except ValidationError as exc:
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "cancel_rc.required_field_missing",
            f"cancel-rc requires both 'project' and 'key': {exc.detail}",
            _example_curl(
                request, '{"project": "my-app", "key": "v1.2.3", "dry_run": true}'
            ),
            warnings,
        )

    refused = _component_refusal(
        request,
        "cancel-rc",
        {"project": body.project, "key": body.key},
        '{"project": "my-app", "key": "v1.2.3", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "cm/cancel-rc.sh")
    flags: dict = {"project": body.project, "key": body.key}
    if body.yes:
        flags["yes"] = True
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    if not body.yes:
        # BLOCK-WORTHY (docs/blocking-site-census.md): an approval by
        # design. The CLI stops at an interactive "Proceed?" prompt unless
        # --yes is given; over HTTP nobody can answer that prompt, so a
        # request that does not carry the answer is refused here rather
        # than dispatched into a prompt that would fail at EOF with a
        # half-printed plan. Fires before anything runs.
        # CENSUS-ANCHOR: operations-cancel-rc-confirm-01
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "cancel_rc.confirmation_required",
            f"cancel-rc would delete the RC branch for {body.key} in project "
            f"{body.project} and reset its release-state.md; the CLI asks for "
            "confirmation interactively and an HTTP call cannot answer that "
            "prompt, so 'yes': true is required in the body (or 'dry_run': "
            "true to preview).",
            _example_curl(
                request,
                f'{{"project": "{body.project}", "key": "{body.key}", "dry_run": true}}',
            ),
            warnings,
        )

    # stdout and stderr go to lists rather than through ops_call's stream
    # capture, so the cancel's git steps hold no process-wide lock.
    out: list[str] = []
    err: list[str] = []
    with ops_call(capture=False) as outcome:
        cm_cancel.cancel_rc(
            _ops_context(request),
            body.project,
            body.key,
            yes=True,
            force=bool(body.force),
            out=out.append,
            err=err.append,
        )
    outcome.stdout = "".join(out)
    outcome.stderr = "".join(err)
    if not outcome.ok:
        outcome.stderr += getattr(outcome.error, "text", "")
        outcome.exit_code = cm_cancel.cancel_exit_code(outcome.error)
    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="cancel_rc.refused",
        fallback_remedy=(
            "Fix the condition the message names, then re-run the CLI form, "
            "which prints the full plan and prompts before changing anything: "
            f"team/scripts/cm/cancel-rc.sh --project {body.project} --key {body.key}"
        ),
    )


# ---------------------------------------------------------------------------
# POST /operations/release-locks
# ---------------------------------------------------------------------------


@router.post(
    "/release-locks",
    summary="Release one named advisory lock under the kanban root",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from "
        "ops.locks.release_lock (the release-locks.sh --release output); on a "
        "refused call the body also carries error, message and remedy."
    ),
)
def post_release_locks(body: ReleaseLocksBody, request: Request) -> JSONResponse:
    """Release the named lock -- the REST twin of ``release-locks.sh --release NAME``.

    Calls ``pgai_agent_kanban.ops.locks.release_lock`` -- the function
    release-locks.sh reaches through ``pp_run_ops`` -- in process; no
    subprocess is started. The mutating half of the ``release-locks`` verb;
    the read-only listing mode is ``GET /release-locks`` (reads.py). Name
    resolution (exact, then unambiguous hyphen-boundary prefix), the
    refusals and the idempotent already-released no-op are the function's:
    its dual-channel block is lifted into the response's
    ``error``/``message``/``remedy`` keys, so ``release_locks.not_found``
    (exit 3), ``release_locks.ambiguous`` (exit 2),
    ``release_locks.holder_alive`` (exit 4, naming the live holder's holder,
    pid, op and task; the lock is left in place),
    ``release_locks.orphan_holder`` (exit 4: the recorded holder is dead but
    processes the record does not name still hold the lock's flock -- a
    child that inherited its lock descriptor, say; the pids are named and
    the lock is left in place) and ``release_locks.holder_not_released``
    (exit 5) reach the caller with the same machine key and the same
    runnable remedy the CLI prints.

    The function's narration lines are collected as ``stdout`` without
    capturing the server's streams, so a ``force`` call waiting for its
    holder does not hold up other routes. A ``force`` call against an
    orphan-held lock ends in the ``orphan_let_go`` outcome when the orphans
    let go: they were sent SIGTERM, released the flock, and the dead
    holder's file was swapped for an empty one (narrated as "its holders let
    go Ns after SIGTERM").

    Body fields:

    - ``release`` (required) — the lock's name (its file basename with the
                   trailing ``.lock`` stripped) or an unambiguous prefix.
                   The CLI's ``--release``.
    - ``force``   (optional, bool) — as ``--force``: a live holder, or the
                   orphan processes still holding a dead holder's flock, are
                   sent SIGTERM and the call waits up to
                   ``kill_grace_seconds`` for the lock to be let go.
    - ``dry_run`` (optional, bool) — when True, returns the planned action
                   description without changing anything.

    Response envelope (exit_code 0):
      ``{"exit_code": 0, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when the
      script refused; ``{"error": str, "message": str, "remedy": str,
      "warnings": list}`` at 422 when ``release`` is missing.

    HTTP status: 200 on exit_code 0, the refusal's own status on non-zero
    (404 / 409 / 422, else 500), 422 on an API-layer
    refusal.
    """
    known_fields = list(ReleaseLocksBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    if not body.release:
        return _refusal_response(
            HTTP_UNPROCESSABLE_ENTITY,
            "release_locks.release_required",
            "POST /operations/release-locks releases one named lock and no "
            "lock was named in 'release'; to list the locks instead, use GET "
            "/release-locks.",
            _example_curl(request, '{"release": "repo-wake-my-app", "dry_run": true}'),
            warnings,
        )

    refused = _component_refusal(
        request,
        "release-locks",
        {"release": body.release},
        '{"release": "repo-wake-my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "release-locks.sh")
    flags: dict = {"release": body.release}
    if body.force:
        flags["force"] = True
    planned_argv = build_argv(script, flags)

    short_circuit = _dry_run_envelope(body.dry_run, planned_argv, warnings)
    if short_circuit is not None:
        return short_circuit

    # Narration goes to a list rather than through ops_call's stream capture,
    # so a force call waiting on its holder holds no process-wide lock.
    lines: list[str] = []
    with ops_call(capture=False) as outcome:
        ops_locks.release_lock(
            _ops_context(request), body.release, force=bool(body.force), say=lines.append
        )
    outcome.stdout = "".join(f"{line}\n" for line in lines)
    if not outcome.ok:
        outcome.exit_code = ops_locks.locks_exit_code(outcome.error)
    return _make_dual_channel_envelope(
        outcome.as_shell_result(),
        warnings,
        fallback_error="release_locks.refused",
        fallback_remedy=(
            "Run team/scripts/release-locks.sh with no flags to list every lock "
            "by name, then re-run: team/scripts/release-locks.sh --release "
            f"{body.release}"
        ),
    )


# ---------------------------------------------------------------------------
# POST /operations/archive
# ---------------------------------------------------------------------------


@router.post(
    "/archive",
    summary="Archive a project's finished items, preview the run, restore one item, or reconcile queue lines",
    response_description=(
        "JSON envelope with exit_code, stdout, and stderr from "
        "archive.run.run_archive (the archive.sh output); on a refused, "
        "skipped or failed run the body also carries error, message and remedy."
    ),
)
def post_archive(body: ArchiveBody, request: Request) -> JSONResponse:
    """Archive finished items, preview the run, or restore one archived item.

    The REST twin of the ``archive`` verb. Calls
    ``pgai_agent_kanban.archive.run.run_archive`` -- the function archive.sh
    reaches through ``pp_run_ops`` -- in process; no subprocess is started.
    Its narration and refusal lines are collected through callbacks, without
    capturing the server's own streams.

    - With neither ``dry_run`` nor ``restore``: plans, then moves the
      finished items older than the newest ``count`` per category (bugs,
      priority, requirements, tasks) into ``archive/<project>/`` with the
      queue lines and training logs that follow them, under the project's
      repo lock, one ledger row per item. A second call moves nothing.
    - ``dry_run``: runs the same plan and returns it per category in
      ``stdout`` -- the text archive.sh --dry-run prints for the same board --
      and moves nothing. Unlike most routes, the dry run is not a
      description of the CLI call: it is the plan itself.
    - ``restore``: moves the one named item back live, with its queue lines
      and training logs, and records a restore row in the ledger.
    - ``reconcile_queues``: instead of an archive run, moves each live queue
      line naming an item that is archived and not live, or a finished
      (``[x]``) task whose folder is in neither the live project nor the
      archive, to ``archive/<project>/tasks/queues/<same file>``; a line
      naming a live item is never touched and a second call moves nothing.
      With ``dry_run`` it lists those lines (and the ones it leaves, with
      the reason) in ``stdout`` and moves nothing.

    No confirmation is asked for: nothing is deleted, every move is in the
    ledger, and any archived item can be restored.

    Body fields:

    - ``project`` (required) — project name.
    - ``dry_run`` (optional, bool) — preview the plan; move nothing.
    - ``count``   (optional, int or digit string) — items kept live per
                  category; defaults to ``[archive] archive_count``. A value
                  that is not decimal digits is refused
                  ``archive.invalid_count``.
    - ``restore`` (optional) — an archived ID (``BUG-NNNN``,
                  ``PRIORITY-NNNN``, a requirements file name, a task ID).
                  With ``dry_run``, ``count`` or ``reconcile_queues`` it is
                  refused ``archive.conflicting_options``.
    - ``reconcile_queues`` (optional, bool) — reconcile queue lines instead
                  of archiving; with ``count`` it is refused
                  ``archive.conflicting_options``.

    Response envelope (exit_code 0):
      ``{"exit_code": 0, "stdout": str, "stderr": str, "warnings": list}``

    Response on a refused call:
      ``{"exit_code": int, "stdout": str, "stderr": str, "error": str,
      "message": str, "remedy": str, "warnings": list}`` at 404 / 409 / 422 (or 500 for an
      unexpected failure; ``http_status_for``) when
      run_archive refused (exit 1: ``project_resolve.not_found`` /
      ``.ambiguous``, ``archive.invalid_count``,
      ``archive.conflicting_options``, ``archive.project_missing``,
      ``archive.restore_not_archived``, ``archive.restore_live_exists``),
      stopped at a failing item (exit 2, ``archive.run_failed``) or was
      skipped because the repo lock is held (exit 3,
      ``archive.repo_lock_busy``) -- the dual-channel block in ``stderr``
      lifted to top-level keys; ``{"error": "archive.project_required",
      "message": str, "remedy": str, "warnings": list}`` at 422 when the
      body carried no ``project``, before anything runs.
    """
    known_fields = list(ArchiveBody.model_fields.keys())
    warnings = _capture_unknown_fields(body.model_extra or {}, known_fields)

    try:
        validate_required({"project": body.project}, required_keys=["project"])
    except ValidationError as exc:
        return _missing_field_refusal(
            request, "archive", exc, '{"project": "my-app", "dry_run": true}', warnings
        )

    refused = _component_refusal(
        request,
        "archive",
        {"project": body.project, "restore": body.restore},
        '{"project": "my-app", "dry_run": true}',
        warnings,
    )
    if refused is not None:
        return refused

    script = _script_path(request, "archive.sh")
    flags: dict = {"project": body.project}
    if body.dry_run:
        flags["dry-run"] = True
    if body.count is not None and body.count != "":
        flags["count"] = str(body.count)
    if body.restore:
        flags["restore"] = body.restore
    if body.reconcile_queues:
        flags["reconcile-queues"] = True
    planned_argv = build_argv(script, flags)

    out: list[str] = []
    err: list[str] = []
    run = archive_run.run_archive(
        _ops_context(request),
        body.project,
        dry_run=body.dry_run,
        count=body.count,
        restore_id=body.restore or None,
        out=out.append,
        err=err.append,
        reconcile_queues=body.reconcile_queues,
    )
    result = ShellResult(
        exit_code=run.exit_code,
        stdout="".join(f"{line}\n" for line in out),
        stderr="".join(f"{line}\n" for line in err),
    )
    return _make_dual_channel_envelope(
        result,
        warnings,
        fallback_error="archive.refused",
        fallback_remedy=_cli_remedy("archive.sh", planned_argv),
    )
