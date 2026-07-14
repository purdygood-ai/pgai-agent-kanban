#!/usr/bin/env python3
"""
lint_api_parity.py
==================
Pre-flight lint that asserts every operator script the API fronts has full
flag-parity with the corresponding request body model.

For each (script, body-model) pair defined in _API_PARITY_MAP:

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

Exit codes
----------
  0   All pairs are in parity; lint is green.
  1   One or more parity violations found; see stdout for details.
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
import re
import sys
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
}

# ---------------------------------------------------------------------------
# API-script parity map.
#
# Format: list of (script_filename, BodyModelClass) pairs.
#
# Only scripts that are directly fronted by an API endpoint are listed here.
# Scripts not fronted by the API (e.g. pm-agent.sh, wake-batch.sh) are
# intentionally absent — their absence is not a parity violation.
#
# Populated after imports below; kept here as a forward-reference comment.
# ---------------------------------------------------------------------------
_API_PARITY_MAP: list[tuple[str, Any]] = []   # populated in _build_parity_map()


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
# route from being added without the universal fields required by the v1.20.7
# API contracts.
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
# Build the parity map (imports the routers.operations module)
# ---------------------------------------------------------------------------


def _build_parity_map(team_dir: Path) -> list[tuple[Path, Any]]:
    """Construct the (script_path, BodyModel) pairs list.

    Imports the operations router module from the team directory so we get
    the live Pydantic model classes (not re-implemented copies).

    Returns a list of (absolute script Path, body model class) pairs.
    """
    import importlib
    import importlib.util

    # Locate the pgai_agent_kanban package under team/.
    pkg_dir = team_dir / "pgai_agent_kanban"
    if not pkg_dir.is_dir():
        raise RuntimeError(
            f"pgai_agent_kanban package not found under: {team_dir}\n"
            "Use --team-dir to specify the correct team/ root."
        )

    # Add team/ to sys.path so the package is importable.
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

    scripts_dir = team_dir / "scripts"

    def _sp(name: str) -> Path:
        p = scripts_dir / name
        if not p.exists():
            raise RuntimeError(
                f"Script not found: {p}\n"
                "Verify the scripts directory is complete."
            )
        return p

    # Build pairs: (script path, body model class)
    # Each pair is: script whose operator flags the API endpoint must mirror.
    # Scripts with no operator flags exposed in the body (halt-global, unhalt-global)
    # are omitted — their body models intentionally carry no fields.
    pairs: list[tuple[Path, Any]] = [
        # halt / unhalt share the same body (HaltBody: project only).
        (_sp("halt.sh"),               ops_mod.HaltBody),
        (_sp("unhalt.sh"),             ops_mod.HaltBody),
        (_sp("halt-after.sh"),         ops_mod.HaltAfterBody),
        # halt-global and unhalt-global: body is intentionally empty; omit.
        (_sp("reset.sh"),              ops_mod.ResetBody),
        (_sp("close.sh"),              ops_mod.CloseBody),
        (_sp("wontdo.sh"),             ops_mod.WontdoBody),
        (_sp("delete.sh"),             ops_mod.DeleteBody),
        (_sp("intake.sh"),             ops_mod.IntakeBody),
        (_sp("unwind-rc.sh"),          ops_mod.UnwindRcBody),
        (_sp("set-version-ceiling.sh"), ops_mod.SetVersionCeilingBody),
        (_sp("switch-provider.sh"),    ops_mod.SwitchProviderBody),
        (_sp("create-project.sh"),     ops_mod.CreateProjectBody),
        (_sp("add-project.sh"),        ops_mod.AddProjectBody),
        (_sp("remove-project.sh"),     ops_mod.RemoveProjectBody),
        (_sp("ship-rc.sh"),            ops_mod.ShipRcBody),
    ]
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
            f"\nlint_api_parity: FAIL — {len(all_violations)} total violation(s):\n",
            flush=True,
        )
        for v in all_violations:
            print(f"  {v}", flush=True)
        print(
            "\nFix: add the missing field to the corresponding model in "
            "team/pgai_agent_kanban/api/routers/operations.py.",
            flush=True,
        )
        return 1

    print(
        f"lint_api_parity: ok — {len(pairs)} script/model pair(s) checked, "
        "all flags covered; all op models have dry_run; all response models have warnings.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
