#!/usr/bin/env python3
"""
lint_api_parity.py
==================
BLOCK-WORTHY gate (team/SOP-core.md "Gates and Refusals"; parity lint and
schema validation block because a missing REST twin is real API damage)
that walks team/config/operator_verbs.manifest -- the operator verb census
-- against the tree it describes, plus the legacy flag/body-model parity
checks this script has always run. Sourced through
pgai_agent_kanban.lib.operator_verb_manifest.load_manifest(), never by
re-parsing the manifest file a second time.

Census-walk checks, each finding naming the
verb/script/route it concerns:

  1. route_missing_script  -- an active verb's census row declares a REST
     route but the script column names a file that does not exist on disk.
  2. route_missing_census  -- a REST route actually registered on the API
     and fronting a real operator script (not one of the documented
     out-of-census reads.py convenience scripts named in
     team/config/operator_verb_census_exempt.txt) has no matching
     (rest_method, rest_route) census row at all.
  3. schema_mismatch       -- an active verb's response, resolved
     statically from its route handler's own return statement(s), does not
     match the key set its census row's schema_ref declares
     (ShellEnvelope / VerdictsResponse / AgentMetricsResponse). A route
     shape this lightweight scanner cannot statically resolve is skipped,
     never guessed at.
  4. script_missing_census -- an operator script under one of the census's
     own script roots (team/scripts/, team/scripts/dashboard/,
     team/scripts/cm/) that declares OPERATOR_VALID_FLAGS (or hand-parses
     its own arguments per operator_verb_manifest.NO_OPERATOR_VALID_FLAGS_
     ARRAY) has no active verb naming it and is not listed in the
     checked-in exempt sidecar.
  5. action_fixture_missing -- an active "action"-class verb has no
     effect-parity or refusal-parity case in the action-parity ledger
     (team/tests/fixtures/action_parity_cases.py, the tuple
     team/tests/integration/test_action_parity_fixtures.py parametrizes
     over), so nothing proves its REST twin writes what the CLI writes or
     refuses what the CLI refuses. Its mirror, action_fixture_unknown_verb,
     is a ledger case naming a verb with no active action row -- a fixture
     that would run against nothing the census knows.
  6. verb_missing_route    -- an active verb's census row declares no REST
     twin at all (rest_method / rest_route "ABSENT"): a script an operator
     can run today that no HTTP caller can reach. Only a "planned" row --
     no script yet, so nothing to twin -- may carry ABSENT REST columns
     (the manifest's own header states the rule). Remedy: ship the route
     and fill in the row's rest_method, rest_route, rest_body_model and
     schema_ref with real values; a row for a script that does not exist
     yet is "planned", not "active".
  7. read_fixture_missing   -- an active "read"-class verb has no
     golden-pair case in the read-parity ledger
     (team/tests/fixtures/read_parity_cases.py, the tuple
     team/tests/integration/test_read_parity_fixtures.py parametrizes
     over), so nothing proves its REST twin returns the same resolved data
     the CLI renders -- one shared resolver, two renderers. Its mirror,
     read_fixture_unknown_verb, is a ledger case naming a verb with no
     active read row -- a golden pair that would run against nothing the
     census knows.

Legacy checks (unchanged from this script's earlier flag-parity-only
form), for each (script, body-model) pair sourced from the manifest via
_build_parity_map():

  1. Extract the operator flags the script accepts by parsing its
     ``OPERATOR_VALID_FLAGS=(...)`` declaration (not body greps).
  2. Collect the field names declared on the Pydantic body model.
  3. Apply the documented equivalences:
       - intake script's positional ``file`` argument → body fields
         ``filename`` and ``content`` (both must be present; neither is
         flagged as drift).
       - Script flag names use hyphens; body field names use underscores.
         A script flag ``dry-run`` is equivalent to body field ``dry_run``.
       - The ``help`` / ``h`` flags are meta-flags; they are never exposed
         in a body model and are excluded from the check.
     After normalising both sets, assert that the body field set is a
     superset of the script flag set — i.e. every operator flag has a
     corresponding body field.
  4. Report each missing field as:
       <script-filename>: flag '--<flag>' not found in <ModelClass> body fields
  Plus: every operation request body model declares ``dry_run``, and every
  response model discovered under a ``*Response``/``*Envelope`` name
  declares ``warnings``.

Exit codes
----------
  0   Census-walk and legacy checks both green.
  1   One or more violations found (either class); see stdout for details.
  2   Usage error or internal failure (e.g., scripts dir or routers module not found).

Usage
-----
  python3 scripts/lint_api_parity.py [--team-dir PATH] [--verbose]

  --team-dir PATH   Root of the team/ directory (default: parent of this
                    script's directory, i.e. team/ relative to team/scripts/).
  --verbose, -v     Print each script/model pair as it is checked.

Examples
--------
  # From repo root:
  python3 team/scripts/lint_api_parity.py

  # With explicit path:
  python3 team/scripts/lint_api_parity.py --team-dir /path/to/team
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Meta-flags excluded from parity checks.
# These are present in every script's OPERATOR_VALID_FLAGS but correspond to
# no body field — they control the CLI, not the operation.
# ---------------------------------------------------------------------------
_META_FLAGS: frozenset[str] = frozenset({"help", "h"})

# ---------------------------------------------------------------------------
# Documented equivalences.
#
# Each entry maps a script flag name to the body field name(s) that satisfy it.
# A script flag listed here is satisfied when ALL of body_fields are present
# in the model — none of them is treated as drift.
#
# Rules applied (from requirements):
#   1. intake script's positional ``file`` argument → body fields
#      ``filename`` and ``content`` (both must be present; neither is drift).
#   2. create-project.sh accepts ``--dev-tree-path`` and ``--git-repo-url``
#      as aliases for ``--dev-tree`` and ``--git-repo`` (both rejected by the
#      script with a clear message); the body exposes only the primary names
#      ``dev_tree`` and ``git_repo``.  The alias flags map to the same body
#      field as the primary flag.
# ---------------------------------------------------------------------------
_FLAG_EQUIVALENCES: dict[str, list[str]] = {
    # intake: positional file path → filename + content body fields
    "file": ["filename", "content"],
    # create-project.sh alias flags → primary body field names
    "dev-tree-path": ["dev_tree"],
    "git-repo-url":  ["git_repo"],
}

# ---------------------------------------------------------------------------
# Per-script static flag overrides.
#
# Used when a script does not use OPERATOR_VALID_FLAGS (e.g. ship-rc.sh uses
# a hand-written case statement).  Maps script filename → list of operator
# flag names the script accepts (excluding meta-flags help/h).
# ---------------------------------------------------------------------------
_STATIC_FLAG_OVERRIDES: dict[str, list[str]] = {
    # ship-rc.sh uses a manual case parser; flags: project, key, dry-run, help.
    # 'help' is in _META_FLAGS and skipped automatically; confirm is API-only.
    "ship-rc.sh": ["project", "key", "dry-run"],
    # cm/cancel-rc.sh uses a manual case parser; flags: project, key, yes,
    # force, help -- the same list its manifest row carries (the
    # reconciliation lint trusts that column for hand-parsed scripts; see
    # operator_verb_manifest.NO_OPERATOR_VALID_FLAGS_ARRAY).
    "cancel-rc.sh": ["project", "key", "yes", "force"],
}

# ---------------------------------------------------------------------------
# API-script parity map.
#
# Format: list of (script_filename, BodyModelClass) pairs.
#
# Sourced from team/config/operator_verbs.manifest (via
# pgai_agent_kanban.lib.operator_verb_manifest), not hardcoded here — see
# _build_parity_map(). A verb participates in this list exactly when its
# manifest row's rest_body_model column is a real Pydantic class name (not
# "NONE" or "ABSENT"); that is the same 15-pair set this map used to
# hardcode (halt/unhalt sharing HaltBody, halt-global/unhalt-global omitted
# because their body is accepted-but-ignored, and every other operations.py
# verb one-to-one). Scripts not fronted by the API (e.g. pm-agent.sh,
# wake-batch.sh) are simply absent from the manifest's REST columns — their
# absence here is not a parity violation.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Flag extraction
# ---------------------------------------------------------------------------


def _extract_operator_valid_flags(script_path: Path) -> list[str]:
    """Return the operator flags for a script.

    Resolution order:
    1. If the script filename has an entry in ``_STATIC_FLAG_OVERRIDES``,
       return that list directly (used for scripts without OPERATOR_VALID_FLAGS).
    2. Otherwise, parse ``OPERATOR_VALID_FLAGS=(...)`` from the script source.

    Raises ValueError if neither the static override nor the array declaration
    is found.
    """
    script_name = script_path.name
    if script_name in _STATIC_FLAG_OVERRIDES:
        return list(_STATIC_FLAG_OVERRIDES[script_name])

    source = script_path.read_text(encoding="utf-8", errors="replace")

    # Match: OPERATOR_VALID_FLAGS=( ... )
    # The array body may span multiple lines (bash allows continuation).
    # We look for the opening paren after the assignment and collect
    # everything up to the balancing closing paren.
    pat = re.compile(
        r"OPERATOR_VALID_FLAGS\s*=\s*\(",   # assignment start
        re.MULTILINE,
    )
    m = pat.search(source)
    if not m:
        raise ValueError(
            f"OPERATOR_VALID_FLAGS declaration not found in {script_path}"
        )

    # Walk forward from the opening paren to find the balanced close paren.
    start = m.end()  # position just after the opening '('
    depth = 1
    pos = start
    while pos < len(source) and depth > 0:
        ch = source[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        pos += 1

    array_body = source[start : pos - 1]  # content between outer ( )

    # Tokenise: strip quotes, split on whitespace.
    tokens: list[str] = []
    # Match bare words, single-quoted strings, or double-quoted strings.
    token_pat = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")
    for tm in token_pat.finditer(array_body):
        token = tm.group(1) or tm.group(2) or tm.group(3)
        if token and not token.startswith("#"):
            tokens.append(token)

    # Filter out comment fragments: after a '#' token, remaining tokens on
    # the same "line" are comments.  Since we already skip '#'-prefixed tokens
    # above, also drop pure-whitespace artefacts.
    flags = [t for t in tokens if t]
    return flags


# ---------------------------------------------------------------------------
# Body model field extraction
# ---------------------------------------------------------------------------


def _model_fields(model_class: Any) -> set[str]:
    """Return the set of field names declared on a Pydantic BaseModel subclass."""
    # Pydantic v1: model_class.__fields__ is a dict of field name → FieldInfo.
    # Pydantic v2: model_class.model_fields is the equivalent.
    if hasattr(model_class, "model_fields"):
        return set(model_class.model_fields.keys())
    if hasattr(model_class, "__fields__"):
        return set(model_class.__fields__.keys())
    raise TypeError(f"Cannot extract fields from {model_class!r}")


# ---------------------------------------------------------------------------
# Parity check core
# ---------------------------------------------------------------------------


def _normalise_flag(flag: str) -> str:
    """Convert a script flag name to its body-field equivalent (hyphen → underscore)."""
    return flag.replace("-", "_")


def check_parity(
    script_path: Path,
    model_class: Any,
    verbose: bool = False,
) -> list[str]:
    """Check parity between script flags and body model fields.

    Returns a list of violation strings (empty when in parity).
    """
    script_name = script_path.name

    try:
        raw_flags = _extract_operator_valid_flags(script_path)
    except ValueError as exc:
        return [f"{script_name}: ERROR extracting flags — {exc}"]

    body_fields = _model_fields(model_class)
    model_name = model_class.__name__

    if verbose:
        print(
            f"  checking {script_name} ↔ {model_name}\n"
            f"    script flags:  {sorted(raw_flags)}\n"
            f"    body fields:   {sorted(body_fields)}",
            flush=True,
        )

    violations: list[str] = []

    for flag in raw_flags:
        # Skip meta-flags — never modelled in a body.
        if flag in _META_FLAGS:
            continue

        # Normalise: hyphen → underscore.
        normalised = _normalise_flag(flag)

        # Check documented equivalences first.
        if normalised in _FLAG_EQUIVALENCES or flag in _FLAG_EQUIVALENCES:
            lookup = normalised if normalised in _FLAG_EQUIVALENCES else flag
            required_body_fields = _FLAG_EQUIVALENCES[lookup]
            missing_equiv = [f for f in required_body_fields if f not in body_fields]
            if missing_equiv:
                violations.append(
                    f"{script_name}: flag '--{flag}' has equivalence "
                    f"{required_body_fields!r} but body {model_name} is missing "
                    f"field(s): {missing_equiv!r}"
                )
            # Either way this flag is handled via equivalence — do not also
            # check the direct normalised name.
            continue

        # Direct check: normalised flag name must appear as a body field.
        if normalised not in body_fields:
            violations.append(
                f"{script_name}: flag '--{flag}' not found in {model_name} body fields"
            )

    return violations


# ---------------------------------------------------------------------------
# Field-presence lint helpers (class-closers)
#
# These two helpers are the class-closer checks that prevent a future verb or
# route from being added without the universal fields every operator API
# contract requires.
#
# check_op_model_has_dry_run  — verifies a single operation request body model
#     declares a ``dry_run`` field.  Used by lint_all_op_models_dry_run() to
#     iterate every *Body class discovered in the operations module.
#
# check_response_model_has_warnings  — verifies a single response Pydantic
#     model declares a ``warnings`` field.  Used by
#     lint_all_response_models_warnings() to iterate any response model classes
#     discovered in the API package.  The current codebase has no explicit
#     Pydantic response models (responses use inline dicts in _make_envelope),
#     so lint_all_response_models_warnings() passes trivially on the current
#     tree; the helper exists so a future developer who adds a Pydantic response
#     class without ``warnings`` is caught immediately.
# ---------------------------------------------------------------------------


def check_op_model_has_dry_run(model_class: Any) -> list[str]:
    """Assert that an operation request body model declares ``dry_run``.

    Every operation POST endpoint must accept a ``dry_run`` field so callers
    can safely probe the mutation surface without side effects.  A model
    missing ``dry_run`` is a parity violation — the dispatch layer's universal
    short-circuit cannot honour a flag the model does not declare.

    Args:
        model_class: A Pydantic BaseModel subclass representing an operation
                     request body.

    Returns:
        A list of violation strings.  Empty when ``dry_run`` is present.
        One entry (naming the class) when ``dry_run`` is absent.
    """
    fields = _model_fields(model_class)
    if "dry_run" not in fields:
        return [
            f"{model_class.__name__}: missing required field 'dry_run' "
            f"(all operation request models must declare dry_run)"
        ]
    return []


def check_response_model_has_warnings(model_class: Any) -> list[str]:
    """Assert that a response Pydantic model declares ``warnings``.

    Every response — operation envelope and read response — must carry a
    ``warnings`` field so callers can surface unknown inputs, diagnostics, and
    near-miss typo suggestions.  A model missing ``warnings`` violates the
    universal response contract.

    Args:
        model_class: A Pydantic BaseModel subclass representing an API
                     response body.

    Returns:
        A list of violation strings.  Empty when ``warnings`` is present.
        One entry (naming the class) when ``warnings`` is absent.
    """
    fields = _model_fields(model_class)
    if "warnings" not in fields:
        return [
            f"{model_class.__name__}: missing required field 'warnings' "
            f"(all response models must declare warnings)"
        ]
    return []


# ---------------------------------------------------------------------------
# Field-presence lint runners (discover and iterate all relevant model classes)
# ---------------------------------------------------------------------------


def lint_all_op_models_dry_run(team_dir: Path) -> list[str]:
    """Lint every operation request body model in the operations router.

    Auto-discovers all Pydantic BaseModel subclasses whose names end in
    ``Body`` from the ``pgai_agent_kanban.api.routers.operations`` module and
    asserts each declares a ``dry_run`` field.

    A new verb that adds a ``FooBody`` class without ``dry_run`` is caught
    automatically — no manual registration needed.

    Args:
        team_dir: Absolute path to the ``team/`` directory.

    Returns:
        List of violation strings.  Empty when all Body models carry
        ``dry_run``.

    Raises:
        RuntimeError: When the operations module cannot be imported.
    """
    import importlib
    from pydantic import BaseModel as PydanticBaseModel

    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)

    try:
        ops_mod = importlib.import_module("pgai_agent_kanban.api.routers.operations")
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import pgai_agent_kanban.api.routers.operations: {exc}"
        ) from exc

    violations: list[str] = []
    for attr_name in dir(ops_mod):
        if not attr_name.endswith("Body"):
            continue
        obj = getattr(ops_mod, attr_name)
        try:
            if not (isinstance(obj, type) and issubclass(obj, PydanticBaseModel)):
                continue
            if obj is PydanticBaseModel:
                continue
        except TypeError:
            continue
        violations.extend(check_op_model_has_dry_run(obj))
    return violations


def lint_all_response_models_warnings(team_dir: Path) -> list[str]:
    """Lint every response Pydantic model in the API package for ``warnings``.

    Discovers Pydantic BaseModel subclasses whose names end in ``Response`` or
    ``Envelope`` from the operations and reads modules and asserts each declares
    a ``warnings`` field.

    The current codebase uses inline dicts (not Pydantic classes) for all
    responses, so this runner finds zero models and returns zero violations on
    the current tree — which is correct.  It fires when a future developer adds
    a Pydantic response class without ``warnings``.

    Args:
        team_dir: Absolute path to the ``team/`` directory.

    Returns:
        List of violation strings.  Empty when all response models carry
        ``warnings`` (including the trivial case of zero response models).

    Raises:
        RuntimeError: When the operations module cannot be imported.
    """
    import importlib
    from pydantic import BaseModel as PydanticBaseModel

    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)

    try:
        ops_mod = importlib.import_module("pgai_agent_kanban.api.routers.operations")
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import pgai_agent_kanban.api.routers.operations: {exc}"
        ) from exc

    # Collect response-shaped model classes from both the operations and reads
    # modules.  The naming convention for response models is names ending in
    # ``Response`` or ``Envelope``.
    _RESPONSE_SUFFIXES = ("Response", "Envelope")

    violations: list[str] = []
    for mod in (ops_mod,):
        for attr_name in dir(mod):
            if not any(attr_name.endswith(s) for s in _RESPONSE_SUFFIXES):
                continue
            obj = getattr(mod, attr_name)
            try:
                if not (isinstance(obj, type) and issubclass(obj, PydanticBaseModel)):
                    continue
                if obj is PydanticBaseModel:
                    continue
            except TypeError:
                continue
            violations.extend(check_response_model_has_warnings(obj))
    return violations


# ---------------------------------------------------------------------------
# Census-walk gate (BLOCK-WORTHY) -- see the module docstring's "Census-walk
# checks" section for the failure classes. Everything below builds and
# runs those checks; main() wires the combined result into this
# script's overall exit code.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CensusFinding:
    """One census-walk gate finding.

    Attributes:
        kind: machine-stable finding class -- one of "route_missing_script",
            "route_missing_census", "schema_mismatch",
            "script_missing_census", "action_fixture_missing",
            "action_fixture_unknown_verb", "verb_missing_route",
            "read_fixture_missing", "read_fixture_unknown_verb".
        text: human-readable line naming the verb/script/route the finding
            concerns -- this is both what main() prints and what satisfies
            the requirements document's "every refusal names the verb"
            acceptance criterion.
    """

    kind: str
    text: str


# Response shapes this gate knows how to validate, keyed by the manifest's
# own schema_ref column. "ABSENT" (a planned row's marker) is never looked
# up here -- check_response_schema skips it explicitly.
_SCHEMA_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "ShellEnvelope": frozenset({"exit_code", "stdout", "stderr", "warnings"}),
    # The shell envelope extended, on a refused call, with the script's own
    # dual-channel triple as top-level keys (routers/operations.py's
    # _make_dual_channel_envelope and _refusal_response) -- the union of
    # the success shape (the four ShellEnvelope keys) and the refusal shape
    # (those four plus error/message/remedy; an API-layer 422 carries the
    # triple plus warnings only), so the required set is the union of all
    # seven, matching the read-verb unions below.
    "DualChannelShellEnvelope": frozenset(
        {"exit_code", "stdout", "stderr", "error", "message", "remedy", "warnings"}
    ),
    # GET /verdicts' full vocabulary: {"verdicts", "warnings"} with no
    # ?details=true, plus "posture" / "release_locks" / "queue_tails" when
    # it is given (see get_verdicts' own docstring) -- this checker unions
    # every literal return statement's keys, so the required set here is
    # the union across BOTH shapes, not either one alone.
    "VerdictsResponse": frozenset(
        {"verdicts", "posture", "release_locks", "queue_tails", "warnings"}
    ),
    # GET /agent-metrics: the requested span and what it selected (span,
    # shipped_count, excluded_count) beside the per-agent rows.
    "AgentMetricsResponse": frozenset(
        {"project", "window", "span", "shipped_count", "excluded_count", "agents", "warnings"}
    ),
    # GET /release-state's union of BOTH shapes it can return: the success
    # body (project + the four validated fields + warnings) and the
    # refusal body (project + error/message/remedy + warnings) -- see
    # get_release_state_route's own docstring.
    "ReleaseStateResponse": frozenset(
        {
            "project",
            "active_rc",
            "rc_opened_at",
            "rc_opened_by_task",
            "last_released",
            "error",
            "message",
            "remedy",
            "warnings",
        }
    ),
    # GET /deep-verify-status's union of BOTH shapes: the success body
    # (project + mode/entries/latest + warnings) and the refusal body
    # (project + error/message/remedy + warnings) -- see
    # get_deep_verify_status_route's own docstring.
    "DeepVerifyStatusResponse": frozenset(
        {
            "project",
            "mode",
            "entries",
            "latest",
            "error",
            "message",
            "remedy",
            "warnings",
        }
    ),
}

# The three script roots team/config/operator_verbs.manifest's own "script"
# column is rooted in (see that file's header). check_scripts_have_census_row
# walks exactly these three, non-recursively -- the same universe the
# manifest documents about itself, never a broader guess.
_CENSUS_SCRIPT_ROOTS: tuple[str, ...] = ("scripts", "scripts/dashboard", "scripts/cm")

# Checked-in sidecar (relative to team/) naming scripts that are
# operator-invocable but deliberately outside the operator-verb census's
# current scope -- either a reads.py convenience read that fronts a script
# without being part of the curated verb estate (documented precedent:
# lint_operator_verb_manifest.py's check_operations_reverse scopes the
# reverse direction to /operations/* for the identical reason), or a
# CLI-only tool not yet given a census row. Both
# check_app_routes_have_census_row (class 2) and check_scripts_have_census_row
# (class 4) read it -- one sidecar, not two, since it is the same underlying
# "not yet a census verb" fact either check would otherwise flag.
_CENSUS_EXEMPT_RELPATH = "config/operator_verb_census_exempt.txt"


def _load_census_exempt(team_dir: Path) -> frozenset[str]:
    """Return the script paths (relative to team/, e.g. "scripts/foo.sh")
    the checked-in sidecar at *_CENSUS_EXEMPT_RELPATH* exempts.

    A missing sidecar reads as an empty exemption set -- best-effort, like
    every other checked-in allowlist in this tree -- rather than a hard
    failure; the file ships with the lint, so its absence in a shipped
    tree surfaces as ordinary check-4 findings, not a crash.
    """
    path = team_dir / _CENSUS_EXEMPT_RELPATH
    if not path.is_file():
        return frozenset()
    entries: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.add(line)
    return frozenset(entries)


def check_route_script_existence(verbs: dict, team_dir: Path) -> list[CensusFinding]:
    """Class 1: every active, routed verb's script must exist on disk.

    Scoped to verbs whose census row actually declares a REST twin
    (rest_method/rest_route not both "ABSENT"). An active row with no
    REST twin is check_active_verbs_have_route's (class 6) finding, not
    this one's: there is no route to check the script against, and
    reporting the row here as well would name the same gap twice.
    """
    findings: list[CensusFinding] = []
    for verb in verbs.values():
        if verb.status != "active":
            continue
        if verb.rest_method == "ABSENT" and verb.rest_route == "ABSENT":
            continue
        if not verb.script:
            continue
        script_path = team_dir / verb.script
        if not script_path.is_file():
            findings.append(
                CensusFinding(
                    "route_missing_script",
                    f"verb '{verb.name}': census declares REST route "
                    f"{verb.rest_method} {verb.rest_route} backed by script "
                    f"'{verb.script}', which does not exist at {script_path}",
                )
            )
    return findings


def check_app_routes_have_census_row(
    verbs: dict,
    declared_routes: list[Any],
    census_exempt: frozenset[str],
) -> list[CensusFinding]:
    """Class 2: every registered, script-fronted REST route must be named
    by some active verb's (rest_method, rest_route) census row.

    *declared_routes* is lint_operator_verb_manifest.py's own
    ``_all_router_routes()`` output (reused rather than re-scanned) -- each
    entry's ``fronted_script`` is the basename of the operator script the
    route shells out to via ``_script_path(request, "<script>.sh")``, or
    None for a route that shells out to nothing (a pure-Python read).
    Scoped to fronted_script is not None and not in *census_exempt*: a
    route with no fronted script, or one whose fronted script is a
    documented out-of-census convenience read, is not part of the operator
    verb estate this check enforces.
    """
    findings: list[CensusFinding] = []
    declared_pairs = {
        (v.rest_method, v.rest_route)
        for v in verbs.values()
        if not (v.rest_method == "ABSENT" and v.rest_route == "ABSENT")
    }
    for decl in declared_routes:
        if decl.fronted_script is None:
            continue
        if decl.fronted_script in census_exempt:
            continue
        if (decl.method, decl.route) not in declared_pairs:
            findings.append(
                CensusFinding(
                    "route_missing_census",
                    f"route {decl.method} {decl.route} (fronting script "
                    f"'{decl.fronted_script}') is registered on the API but "
                    "no active verb's census row declares it",
                )
            )
    return findings


def _direct_returns(func_node: ast.AST) -> list[ast.Return]:
    """Return the ast.Return nodes belonging directly to *func_node*'s own
    body -- never a nested function or lambda's -- matching Python's own
    scoping rules."""
    returns: list[ast.Return] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested scope's own returns belong to it, not us.
            if isinstance(child, ast.Return):
                returns.append(child)
            _walk(child)

    _walk(func_node)
    return returns


def _dict_literal_keys(node: ast.AST) -> frozenset[str] | None:
    """Return the top-level string keys of *node* when it is a dict literal
    with every key a string constant; None otherwise (a dynamic key, or not
    a dict literal at all, defeats static resolution)."""
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for k in node.keys:
        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
            return None
        keys.add(k.value)
    return frozenset(keys)


def _find_assigned_value(func_node: ast.AST, var_name: str) -> ast.AST | None:
    """Return the expression assigned to *var_name* by a single, simple
    ``var_name = <expr>`` assignment directly in *func_node*'s own body
    (never a nested scope's).

    Returns None when there is no such assignment, or more than one --
    the multiple-assignment case is genuinely ambiguous for a bare static
    scan (which branch's value reaches the return depends on control flow
    this scanner does not model), so it is left unresolved rather than
    guessed at.
    """
    candidates: list[ast.AST] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(child, ast.Assign)
                and len(child.targets) == 1
                and isinstance(child.targets[0], ast.Name)
                and child.targets[0].id == var_name
            ):
                candidates.append(child.value)
            _walk(child)

    _walk(func_node)
    if len(candidates) == 1:
        return candidates[0]
    return None


# Sentinel distinguishing "this expression resolves to a real, empty key
# set" (impossible for the shapes this module recognises, but kept for
# clarity) from "this scanner does not understand this shape" -- the
# latter must abort the whole containing function's resolution rather
# than silently contribute zero keys, or a genuinely unresolvable branch
# would read as a false pass.
_UNRESOLVED = object()


def _resolve_expr_response_keys(
    expr: ast.AST,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    functions_by_name: dict[str, ast.FunctionDef],
    seen: frozenset[str],
) -> frozenset[str] | object:
    """Resolve one expression to the response-body keys it contributes.

    Returns ``frozenset()`` (empty) for a bare ``None`` constant -- the
    "this branch produces no envelope of its own" signal a dry-run
    short-circuit helper's non-dry-run path uses (see
    ``_dry_run_envelope``): it is not a failure, just a branch that
    contributes nothing, and the function's OTHER return path is what
    actually answers the request in that case. Returns ``_UNRESOLVED`` for
    any shape this scanner does not recognise.
    """
    if isinstance(expr, ast.Constant) and expr.value is None:
        return frozenset()

    direct = _dict_literal_keys(expr)
    if direct is not None:
        return direct

    if isinstance(expr, ast.Name):
        assigned = _find_assigned_value(func_node, expr.id)
        if assigned is None:
            return _UNRESOLVED
        return _resolve_expr_response_keys(assigned, func_node, functions_by_name, seen)

    if isinstance(expr, ast.Call):
        func_expr = expr.func
        if isinstance(func_expr, ast.Name):
            callee_name = func_expr.id
        elif isinstance(func_expr, ast.Attribute):
            callee_name = func_expr.attr
        else:
            callee_name = None

        if callee_name == "JSONResponse":
            for kw in expr.keywords:
                if kw.arg == "content":
                    content_keys = _dict_literal_keys(kw.value)
                    return content_keys if content_keys is not None else _UNRESOLVED
            return _UNRESOLVED

        if callee_name and callee_name in functions_by_name and callee_name not in seen:
            # Pass `seen` unchanged (not seen | {callee_name}):
            # _resolve_function_response_keys adds its own name to the set
            # for ITS OWN inner recursion, after its cycle-guard check --
            # pre-adding it here would make that check see its own name
            # and report a false cycle on entry.
            nested = _resolve_function_response_keys(
                functions_by_name[callee_name],
                functions_by_name,
                _seen=seen,
            )
            return frozenset(nested) if nested is not None else _UNRESOLVED

    return _UNRESOLVED


def _resolve_function_response_keys(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    functions_by_name: dict[str, ast.FunctionDef],
    *,
    _seen: frozenset[str] = frozenset(),
) -> frozenset[str] | None:
    """Statically resolve the set of top-level keys *func_node* returns to
    its caller.

    Recognises: a literal ``return JSONResponse(content={...}, ...)``; a
    bare literal ``return {...}``; ``return None`` (contributes no keys --
    see ``_resolve_expr_response_keys``); ``return <local_var>`` where
    ``<local_var>`` was assigned exactly once earlier in the same function
    (resolved the same way, recursively); and one (cycle-guarded) level of
    delegation to another function defined in the same file -- the
    ``return _make_envelope(...)`` / ``return short_circuit`` (where
    ``short_circuit = _dry_run_envelope(...)``) patterns this API's routers
    actually use.

    Returns None -- "cannot statically verify" -- when any direct return
    resolves to an unrecognised shape, or when there is no return
    statement at all: check_response_schema skips a route it cannot verify
    rather than reporting a false mismatch.
    """
    name = func_node.name
    if name in _seen:
        return None
    seen_next = _seen | {name}

    resolved_keys: set[str] = set()
    saw_a_return = False
    for ret in _direct_returns(func_node):
        saw_a_return = True
        if ret.value is None:
            return None  # a bare `return` with no value -- not modelled.
        keys = _resolve_expr_response_keys(ret.value, func_node, functions_by_name, seen_next)
        if keys is _UNRESOLVED:
            return None
        resolved_keys |= keys  # type: ignore[arg-type]

    if not saw_a_return or not resolved_keys:
        return None
    return frozenset(resolved_keys)


def _router_file_prefix(module: ast.Module) -> str:
    """Return the ``prefix=`` string a module-level ``APIRouter(...)``
    assignment declares, or "" when there is none (or it is unparseable)."""
    for node in module.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = call.func
        callee_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if callee_name != "APIRouter":
            continue
        for kw in call.keywords:
            if (
                kw.arg == "prefix"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ):
                return kw.value.value
    return ""


def _scan_router_file_for_schema(
    path: Path,
) -> tuple[dict[tuple[str, str], ast.FunctionDef], dict[str, ast.FunctionDef]]:
    """Parse *path* and return (route_map, functions_by_name).

    route_map maps (method, full_route) -> the ast.FunctionDef of every
    ``@router.get``/``@router.post`` decorated handler. functions_by_name
    maps every top-level function's own name to its ast.FunctionDef, so
    _resolve_function_response_keys can follow a one-hop delegation (e.g.
    ``return _make_envelope(...)``) within the same file. A file that fails
    to parse (should not happen in a shipped tree) yields two empty dicts
    rather than raising -- the caller simply finds no routes there.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        module = ast.parse(text, filename=str(path))
    except SyntaxError:
        return {}, {}

    prefix = _router_file_prefix(module)
    functions_by_name: dict[str, ast.FunctionDef] = {}
    route_map: dict[tuple[str, str], ast.FunctionDef] = {}

    for node in module.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        functions_by_name[node.name] = node
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "router"):
                continue
            if dec.func.attr not in ("get", "post"):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            raw_route = dec.args[0].value
            if not isinstance(raw_route, str):
                continue
            route_map[(dec.func.attr.upper(), prefix + raw_route)] = node

    return route_map, functions_by_name


def check_response_schema(verbs: dict, team_dir: Path) -> list[CensusFinding]:
    """Class 3: an active verb's response must validate against its
    declared schema_ref, resolved statically from its route handler's own
    return statement(s) -- see _resolve_function_response_keys.

    Scans team/pgai_agent_kanban/api/reads.py and every *.py file under
    team/pgai_agent_kanban/api/routers/ (the same file set
    lint_operator_verb_manifest.py's own router scanner covers). A route
    this scanner cannot statically resolve, or one no scanned file
    declares at all (a census/app mismatch class 1 or 2 already reports),
    is skipped -- never a false mismatch.
    """
    findings: list[CensusFinding] = []
    api_dir = team_dir / "pgai_agent_kanban" / "api"
    files: list[Path] = []
    reads_py = api_dir / "reads.py"
    if reads_py.is_file():
        files.append(reads_py)
    routers_dir = api_dir / "routers"
    if routers_dir.is_dir():
        files.extend(sorted(routers_dir.glob("*.py")))

    for f in files:
        route_map, functions_by_name = _scan_router_file_for_schema(f)
        for verb in verbs.values():
            if verb.status != "active":
                continue
            if verb.schema_ref not in _SCHEMA_REQUIRED_KEYS:
                continue  # "ABSENT", or a name this gate does not model yet.
            func_node = route_map.get((verb.rest_method, verb.rest_route))
            if func_node is None:
                continue
            produced = _resolve_function_response_keys(func_node, functions_by_name)
            if produced is None:
                continue
            required = _SCHEMA_REQUIRED_KEYS[verb.schema_ref]
            if produced != required:
                findings.append(
                    CensusFinding(
                        "schema_mismatch",
                        f"verb '{verb.name}': REST route {verb.rest_method} "
                        f"{verb.rest_route} response resolves to keys "
                        f"{sorted(produced)} but census declares schema_ref "
                        f"'{verb.schema_ref}', which requires exactly "
                        f"{sorted(required)}",
                    )
                )
    return findings


def check_scripts_have_census_row(
    verbs: dict,
    team_dir: Path,
    ovm_module: Any,
    census_exempt: frozenset[str],
) -> list[CensusFinding]:
    """Class 4: every operator-invocable script under the census's own
    script roots must be named by some active verb's script column, or
    listed in the checked-in census-exempt sidecar as a documented,
    deliberate gap.

    "Operator-invocable" is proven the same way the manifest's own reader
    proves it: the script declares ``OPERATOR_VALID_FLAGS=(...)``, or hand-
    parses its own arguments per
    ``operator_verb_manifest.NO_OPERATOR_VALID_FLAGS_ARRAY``. Scoped
    non-recursively to _CENSUS_SCRIPT_ROOTS -- the same three roots the
    manifest's own "script" column values are rooted in.
    """
    findings: list[CensusFinding] = []
    active_scripts = {v.script for v in verbs.values() if v.status == "active" and v.script}

    for root in _CENSUS_SCRIPT_ROOTS:
        root_path = team_dir / root
        if not root_path.is_dir():
            continue
        for script_path in sorted(root_path.glob("*.sh")):
            rel = f"{root}/{script_path.name}"
            if rel in active_scripts or script_path.name in census_exempt:
                continue
            text = script_path.read_text(encoding="utf-8", errors="replace")
            declares = re.search(r"OPERATOR_VALID_FLAGS\s*=\s*\(", text) is not None
            hand_parsed = rel in ovm_module.NO_OPERATOR_VALID_FLAGS_ARRAY
            if not (declares or hand_parsed):
                continue
            findings.append(
                CensusFinding(
                    "script_missing_census",
                    f"script '{rel}' declares an operator flag vocabulary "
                    "but no active verb in team/config/operator_verbs.manifest "
                    "names it as that verb's script (and it is not listed in "
                    f"team/{_CENSUS_EXEMPT_RELPATH})",
                )
            )
    return findings


# The two parity ledgers (relative to team/): each is the one tuple of
# declared cases its integration runner parametrizes over. Read by import
# rather than by scanning test source, so "declared" here is exactly
# "exercised" there.
_ACTION_PARITY_LEDGER_RELPATH = "tests/fixtures/action_parity_cases.py"
_ACTION_PARITY_LEDGER_MODULE = "tests.fixtures.action_parity_cases"
_READ_PARITY_LEDGER_RELPATH = "tests/fixtures/read_parity_cases.py"
_READ_PARITY_LEDGER_MODULE = "tests.fixtures.read_parity_cases"


def _load_parity_ledger_verbs(
    team_dir: Path, ledger_relpath: str, ledger_module: str, label: str
) -> frozenset[str]:
    """Return the verbs the parity ledger at *ledger_relpath* names at least
    once.

    Imports *ledger_module* with *team_dir* on ``sys.path`` (each ledger is
    pure data plus seed functions -- no pytest, no processes -- by its own
    contract) and reads its ``verbs_with_a_case()``.

    Raises:
        FileNotFoundError: the ledger module does not exist under *team_dir*.
        RuntimeError: it exists but cannot be imported -- an internal
            failure, not a census finding.
    """
    ledger_path = team_dir / ledger_relpath
    if not ledger_path.is_file():
        raise FileNotFoundError(str(ledger_path))
    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    try:
        import importlib

        module = importlib.import_module(ledger_module)
        module = importlib.reload(module)
    except Exception as exc:  # noqa: BLE001 -- any import failure is the same internal fault
        raise RuntimeError(
            f"Cannot import the {label} ledger {ledger_module} from {ledger_path}: {exc}"
        ) from exc
    return frozenset(module.verbs_with_a_case())


def _load_action_parity_verbs(team_dir: Path) -> frozenset[str]:
    """The verbs the action-parity ledger names at least once -- see
    ``_load_parity_ledger_verbs`` for the contract and the failures."""
    return _load_parity_ledger_verbs(
        team_dir, _ACTION_PARITY_LEDGER_RELPATH, _ACTION_PARITY_LEDGER_MODULE, "action-parity"
    )


def _load_read_parity_verbs(team_dir: Path) -> frozenset[str]:
    """The verbs the read-parity ledger names at least once -- see
    ``_load_parity_ledger_verbs`` for the contract and the failures."""
    return _load_parity_ledger_verbs(
        team_dir, _READ_PARITY_LEDGER_RELPATH, _READ_PARITY_LEDGER_MODULE, "read-parity"
    )


def check_action_verbs_have_parity_fixture(
    verbs: dict,
    team_dir: Path,
    ledger_verbs: frozenset[str] | None = None,
) -> list[CensusFinding]:
    """Class 5: every active "action"-class verb has at least one
    effect-parity or refusal-parity case in the action-parity ledger, and
    every ledger case names an active action verb.

    *ledger_verbs* is the set of verbs the ledger names; when None it is
    read from the real ledger module under *team_dir* (tests pass an
    explicit set to seed the failing shapes). A ledger that is missing
    outright is reported once, as a finding naming the expected path --
    every action row is then unfixtured, and one line says why rather than
    twenty-four.
    """
    findings: list[CensusFinding] = []
    action_verbs = {
        name
        for name, v in verbs.items()
        if v.status == "active" and v.verb_class == "action"
    }

    if ledger_verbs is None:
        try:
            ledger_verbs = _load_action_parity_verbs(team_dir)
        except FileNotFoundError as exc:
            findings.append(
                CensusFinding(
                    "action_fixture_missing",
                    f"the action-parity ledger is missing at {exc} -- none of the "
                    f"{len(action_verbs)} active action verb(s) has an effect-parity or "
                    "refusal-parity fixture",
                )
            )
            return findings

    for name in sorted(action_verbs - ledger_verbs):
        findings.append(
            CensusFinding(
                "action_fixture_missing",
                f"verb '{name}': census class 'action' but no effect-parity or "
                f"refusal-parity case names it in team/{_ACTION_PARITY_LEDGER_RELPATH}",
            )
        )
    for name in sorted(ledger_verbs - action_verbs):
        findings.append(
            CensusFinding(
                "action_fixture_unknown_verb",
                f"team/{_ACTION_PARITY_LEDGER_RELPATH} declares a parity case for "
                f"'{name}', which is not an active 'action'-class verb in the census",
            )
        )
    return findings


def check_read_verbs_have_parity_fixture(
    verbs: dict,
    team_dir: Path,
    ledger_verbs: frozenset[str] | None = None,
) -> list[CensusFinding]:
    """Class 7: every active "read"-class verb has at least one golden-pair
    case in the read-parity ledger, and every ledger case names an active
    read verb.

    A golden-pair case runs the real CLI script and the real REST twin over
    the same synthetic board and holds both to the same resolved data --
    the proof that a read verb's two renderers share one resolver. Mirrors
    check_action_verbs_have_parity_fixture (class 5) for the read half of
    the census: *ledger_verbs* is the set of verbs the ledger names; when
    None it is read from the real ledger module under *team_dir* (tests
    pass an explicit set to seed the failing shapes). A ledger that is
    missing outright is reported once, as a finding naming the expected
    path -- every read row is then without a golden pair, and one line
    says why rather than eight.

    The "missing" half is scoped, like class 1, to rows that declare a REST
    twin: a read verb whose rest_method/rest_route are ABSENT has no twin
    to pair the CLI with, and check_active_verbs_have_route (class 6)
    already reports that row -- naming it here as well would name the same
    gap twice. The "unknown" half still recognises every active read row,
    routed or not, so a ledger case for an unrouted verb is not misreported
    as naming a verb the census does not know.
    """
    findings: list[CensusFinding] = []
    read_verbs = {
        name
        for name, v in verbs.items()
        if v.status == "active" and v.verb_class == "read"
    }
    twinned_read_verbs = {
        name
        for name in read_verbs
        if not (verbs[name].rest_method == "ABSENT" and verbs[name].rest_route == "ABSENT")
    }

    if ledger_verbs is None:
        try:
            ledger_verbs = _load_read_parity_verbs(team_dir)
        except FileNotFoundError as exc:
            findings.append(
                CensusFinding(
                    "read_fixture_missing",
                    f"the read-parity ledger is missing at {exc} -- none of the "
                    f"{len(twinned_read_verbs)} active, routed read verb(s) has a "
                    "golden-pair fixture",
                )
            )
            return findings

    for name in sorted(twinned_read_verbs - ledger_verbs):
        findings.append(
            CensusFinding(
                "read_fixture_missing",
                f"verb '{name}': census class 'read' but no golden-pair case names "
                f"it in team/{_READ_PARITY_LEDGER_RELPATH}",
            )
        )
    for name in sorted(ledger_verbs - read_verbs):
        findings.append(
            CensusFinding(
                "read_fixture_unknown_verb",
                f"team/{_READ_PARITY_LEDGER_RELPATH} declares a golden-pair case for "
                f"'{name}', which is not an active 'read'-class verb in the census",
            )
        )
    return findings


def check_active_verbs_have_route(verbs: dict) -> list[CensusFinding]:
    """Class 6: every active verb's census row must declare a REST twin.

    An active row whose rest_method or rest_route is "ABSENT" names a
    script an operator can run today that no HTTP caller can reach -- the
    exact gap this gate exists to refuse, and one no other class can see:
    class 1 has no route to check the script against and skips the row,
    class 4 only asks whether the script has a row at all, and classes 2
    and 3 start from routes that here do not exist. Only a "planned" row
    -- no script yet, so nothing to twin -- may carry ABSENT REST columns
    (the manifest's own header states the rule), so planned rows are
    exempt. The checked-in census-exempt sidecar is not consulted: it
    names scripts that have NO census row, and a row that exists is held
    to the row's own contract.
    """
    findings: list[CensusFinding] = []
    for verb in verbs.values():
        if verb.status != "active":
            continue
        if verb.rest_method != "ABSENT" and verb.rest_route != "ABSENT":
            continue
        script_desc = verb.script or "-"
        findings.append(
            CensusFinding(
                "verb_missing_route",
                f"verb '{verb.name}': census row is active for script "
                f"'{script_desc}' but declares no REST twin (rest_method "
                f"{verb.rest_method}, rest_route {verb.rest_route}) -- only "
                "a planned row may carry ABSENT REST columns",
            )
        )
    return findings


def run_census_gate(team_dir: Path) -> list[CensusFinding]:
    """Run all seven census-walk checks and return the combined findings.

    Imports pgai_agent_kanban.lib.operator_verb_manifest for the census
    itself (never a second hand-parse of the manifest file) and
    lint_operator_verb_manifest's own router-route scanner (never a second
    regex over the same router files for the route inventory class 2
    needs).

    Raises:
        RuntimeError: the manifest or either sibling module cannot be
            loaded -- an internal failure, not a census finding.
    """
    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    scripts_dir_str = str(Path(__file__).resolve().parent)
    if scripts_dir_str not in sys.path:
        sys.path.insert(0, scripts_dir_str)

    try:
        from pgai_agent_kanban.lib import operator_verb_manifest as ovm
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import pgai_agent_kanban.lib.operator_verb_manifest: {exc}"
        ) from exc

    try:
        verbs = ovm.load_manifest()
    except (FileNotFoundError, ovm.ManifestFormatError) as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        import lint_operator_verb_manifest as _lovm
    except ImportError as exc:
        raise RuntimeError(f"Cannot import lint_operator_verb_manifest: {exc}") from exc

    declared_routes = _lovm._all_router_routes(team_dir)
    census_exempt = _load_census_exempt(team_dir)

    findings: list[CensusFinding] = []
    findings.extend(check_route_script_existence(verbs, team_dir))
    findings.extend(check_app_routes_have_census_row(verbs, declared_routes, census_exempt))
    findings.extend(check_response_schema(verbs, team_dir))
    findings.extend(check_scripts_have_census_row(verbs, team_dir, ovm, census_exempt))
    findings.extend(check_action_verbs_have_parity_fixture(verbs, team_dir))
    findings.extend(check_active_verbs_have_route(verbs))
    findings.extend(check_read_verbs_have_parity_fixture(verbs, team_dir))
    return findings


# ---------------------------------------------------------------------------
# Build the parity map (imports the routers.operations module)
# ---------------------------------------------------------------------------


def _build_parity_map(team_dir: Path) -> list[tuple[Path, Any]]:
    """Construct the (script_path, BodyModel) pairs list.

    Sourced from team/config/operator_verbs.manifest (via
    pgai_agent_kanban.lib.operator_verb_manifest.load_manifest()) rather than
    a hardcoded list — the manifest is the single source of truth for the
    verb set (see that module's docstring). Imports the operations router
    module from the team directory so we get the live Pydantic model classes
    (not re-implemented copies).

    A manifest verb participates in this list exactly when its
    ``rest_body_model`` column names a real class (not ``"NONE"`` — a route
    that exists but whose body carries no fields meaningful to flag parity,
    e.g. halt-global/unhalt-global's intentionally-empty body — and not
    ``"ABSENT"`` — no route at all yet). That is the exact same set this
    function used to hardcode: 15 pairs, halt and unhalt sharing HaltBody.

    Returns a list of (absolute script Path, body model class) pairs.
    """
    import importlib

    # Locate the pgai_agent_kanban package under team/.
    pkg_dir = team_dir / "pgai_agent_kanban"
    if not pkg_dir.is_dir():
        raise RuntimeError(
            f"pgai_agent_kanban package not found under: {team_dir}\n"
            "Use --team-dir to specify the correct team/ root."
        )

    # Add team/ to sys.path so the package (and the manifest reader) are importable.
    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)

    try:
        ops_mod = importlib.import_module("pgai_agent_kanban.api.routers.operations")
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import pgai_agent_kanban.api.routers.operations: {exc}\n"
            "Ensure the team/ directory contains the package and all dependencies "
            "are installed."
        ) from exc

    try:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import pgai_agent_kanban.lib.operator_verb_manifest: {exc}"
        ) from exc

    verbs = load_manifest()

    def _sp(relative_to_team: str) -> Path:
        p = team_dir / relative_to_team
        if not p.exists():
            raise RuntimeError(
                f"Script not found: {p}\n"
                "Verify the scripts directory is complete."
            )
        return p

    pairs: list[tuple[Path, Any]] = []
    for verb in verbs.values():
        if verb.status != "active":
            continue
        if verb.rest_body_model in ("NONE", "ABSENT"):
            continue
        model_class = getattr(ops_mod, verb.rest_body_model, None)
        if model_class is None:
            raise RuntimeError(
                f"Manifest verb '{verb.name}' names rest_body_model "
                f"'{verb.rest_body_model}' but pgai_agent_kanban.api.routers."
                f"operations has no such class."
            )
        pairs.append((_sp(verb.script), model_class))
    return pairs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_api_parity.py",
        description=(
            "Lint that every operator script's accepted flags are fully mirrored "
            "in the corresponding API request body model.  Exits 1 when any script "
            "flag is absent from its body model."
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        help=(
            "Root of the team/ directory.  Default: parent of this script's "
            "directory (team/scripts/ → team/)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each script/model pair and its extracted flag/field sets.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the API-parity lint: the operator verb census walk plus the legacy checks.

    Exit codes: 0 both are green; 1 one or more violations (printed);
    2 usage error or internal failure, such as the scripts directory or the
    routers module not found.
    """
    args = _parse_args(argv)

    # Resolve team_dir.
    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent   # team/scripts/
        team_dir = script_dir.parent                   # team/

    if not team_dir.is_dir():
        print(
            f"ERROR: team directory not found: {team_dir}\n"
            "Use --team-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    print(f"lint_api_parity: scanning against team dir: {team_dir}", flush=True)

    all_violations: list[str] = []

    # --- Check 1: flag-parity (script flags ↔ body model fields) ---
    try:
        pairs = _build_parity_map(team_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for script_path, model_class in pairs:
        violations = check_parity(script_path, model_class, verbose=args.verbose)
        all_violations.extend(violations)

    print(
        f"lint_api_parity: flag-parity — {len(pairs)} script/model pair(s) scanned.",
        flush=True,
    )

    # --- Check 2: every op request body model declares dry_run ---
    try:
        dry_run_violations = lint_all_op_models_dry_run(team_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    all_violations.extend(dry_run_violations)
    print(
        f"lint_api_parity: dry_run field — checked all *Body models "
        f"({len(dry_run_violations)} violation(s)).",
        flush=True,
    )

    # --- Check 3: every response Pydantic model declares warnings ---
    try:
        warnings_violations = lint_all_response_models_warnings(team_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    all_violations.extend(warnings_violations)
    print(
        f"lint_api_parity: warnings field — checked all *Response/*Envelope models "
        f"({len(warnings_violations)} violation(s)).",
        flush=True,
    )

    if all_violations:
        print(
            f"\nlint_api_parity: FAIL — {len(all_violations)} legacy flag/model "
            "violation(s):\n",
            flush=True,
        )
        for v in all_violations:
            print(f"  {v}", flush=True)
        print(
            "\nFix: add the missing field to the corresponding model in "
            "team/pgai_agent_kanban/api/routers/operations.py.",
            flush=True,
        )
    else:
        print(
            f"lint_api_parity: legacy checks ok — {len(pairs)} script/model "
            "pair(s) checked, all flags covered; all op models have dry_run; "
            "all response models have warnings.",
            flush=True,
        )

    # --- Census-walk gate (BLOCK-WORTHY): the REST-parity failure
    # classes -- see the module docstring and run_census_gate()'s own
    # docstring. ---
    try:
        census_findings = run_census_gate(team_dir)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(
        f"lint_api_parity: census-walk — {len(census_findings)} violation(s) found.",
        flush=True,
    )

    if census_findings:
        print(
            f"\nlint_api_parity: FAIL — {len(census_findings)} census-walk "
            "violation(s):\n",
            flush=True,
        )
        for finding in census_findings:
            print(f"  [{finding.kind}] {finding.text}", flush=True)

        error = "api_parity.census_gate_violation"
        message = (
            f"{len(census_findings)} census-walk violation(s) found: the "
            "operator verb census (team/config/operator_verbs.manifest) "
            "disagrees with the scripts, routes, or response shapes the tree "
            "actually ships, or names an active verb with no REST twin; see "
            "the findings printed above for the verb, script, or route each "
            "one names."
        )
        remedy = (
            "fix each verb/script/route named above (add the missing script, "
            "register the missing census row, correct the schema_ref, add "
            "a documented entry to team/config/operator_verb_census_exempt.txt, "
            "declare the missing action-parity case in "
            "team/tests/fixtures/action_parity_cases.py, declare the missing "
            "golden-pair case in team/tests/fixtures/read_parity_cases.py, "
            "or ship the REST "
            "route for an active verb whose row says ABSENT and fill in that "
            "row's rest_method/rest_route/rest_body_model/schema_ref), "
            "then re-run: python3 team/scripts/lint_api_parity.py"
        )
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)

    if all_violations or census_findings:
        # BLOCK-WORTHY (team/SOP-core.md "Gates and Refusals"; team/roles/
        # CODER.md "Authoring a new gate"): a missing or mismatched REST
        # twin is real API damage -- a real stop, not a
        # documentation-accuracy note.
        # CENSUS-ANCHOR: api-parity-census-gate-01
        return 1

    print("lint_api_parity: ok — legacy and census-walk checks both green.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
