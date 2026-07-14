#!/usr/bin/env python3
"""
lint_icd_compat.py
==================
Pre-flight gate that asserts the current docs/api/icd.json is a compatible
superset of every ICD version listed in docs/api/baselines/SUPPORTED.

Compatible-superset means all four rules hold for each supported baseline:
  a. Every baseline path+method exists in the current ICD.
  b. No baseline response field has been removed (object properties compared
     recursively where schemas are declared via $ref or inline).
  c. No request field that was optional in the baseline became required, and
     no new required request field was added on a baseline endpoint.
  d. All baseline enum values are still present.

An empty SUPPORTED manifest is a lint error (fail-loud) — support status must
be explicit, never inferred.  A version listed in SUPPORTED whose baseline file
is missing is also a lint error.

On any violation, the script exits 1 and prints a message naming the specific
break and stating the policy:
  "breaking changes require a major ICD version and an operator-approved RC"

Wired into both gated runners (run-unit-tests.sh and run-integration-tests.sh)
as a pre-flight check, following the lint_icd_freshness pattern.

Usage:
    python3 team/scripts/lint_icd_compat.py [--icd PATH] [--baselines-dir PATH]

Options:
    --icd PATH           Path to the current ICD artifact.
                         Default: docs/api/icd.json relative to the dev tree root.
    --baselines-dir PATH Path to the baselines directory containing SUPPORTED and
                         the frozen baseline files.
                         Default: docs/api/baselines/ relative to the dev tree root.
    --help, -h           Show this help and exit.

Exit codes:
    0   All supported baselines are compatible with the current ICD.
    1   Compatibility violation, empty SUPPORTED manifest, or missing baseline file.
    2   Usage error or internal failure.

Environment:
    PGAI_AGENT_KANBAN_TEMP_DIR  Framework temp root (used by test helpers that
                                 call this module).  Not used by the script itself,
                                 but tests read it for scratch paths.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Dev-tree root resolution.
# This file lives at team/scripts/lint_icd_compat.py.
# Two parent levels: scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent           # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent              # project_root/
_DEFAULT_ICD_PATH = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_DEFAULT_BASELINES_DIR = _DEV_TREE_ROOT / "docs" / "api" / "baselines"

# Policy message appended to every violation.
_POLICY_MSG = (
    "breaking changes require a major ICD version and an operator-approved RC"
)


# ---------------------------------------------------------------------------
# $ref resolution helpers
# ---------------------------------------------------------------------------


def _resolve_ref(ref: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Resolve a JSON Schema $ref within the same document.

    Only local $ref values of the form '#/components/schemas/<name>' are
    supported.  All others return an empty dict (treated as an unresolvable
    schema — the check degrades gracefully rather than crashing).

    Args:
        ref: The $ref string (e.g. '#/components/schemas/AddProjectBody').
        doc: The full OpenAPI document in which to resolve the reference.

    Returns:
        The resolved schema dict, or an empty dict when resolution fails.
    """
    if not ref.startswith("#/"):
        return {}
    parts = ref.lstrip("#/").split("/")
    node: Any = doc
    for part in parts:
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def _unwrap_schema(schema: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Resolve a $ref if present, otherwise return the schema as-is.

    Args:
        schema: A JSON Schema object (may contain '$ref').
        doc:    The full OpenAPI document for resolving references.

    Returns:
        The resolved schema dict.
    """
    ref = schema.get("$ref")
    if ref:
        return _resolve_ref(ref, doc)
    return schema


# ---------------------------------------------------------------------------
# Property extraction helpers
# ---------------------------------------------------------------------------


def _get_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the 'properties' dict for a schema, or an empty dict.

    Handles both plain 'properties' and 'anyOf'/'oneOf' wrappers.

    Args:
        schema: A JSON Schema object.

    Returns:
        The 'properties' mapping, or an empty dict when absent.
    """
    return schema.get("properties") or {}


def _get_required(schema: dict[str, Any]) -> set[str]:
    """Return the set of required field names for a schema.

    Args:
        schema: A JSON Schema object.

    Returns:
        Set of required field names (may be empty).
    """
    return set(schema.get("required") or [])


# ---------------------------------------------------------------------------
# Compatibility checks
# ---------------------------------------------------------------------------


def _check_paths(
    baseline_doc: dict[str, Any],
    current_doc: dict[str, Any],
    baseline_version: str,
) -> list[str]:
    """Assert every baseline path+method exists in the current ICD.

    Args:
        baseline_doc:     Parsed baseline ICD.
        current_doc:      Parsed current ICD.
        baseline_version: Version string (for error messages).

    Returns:
        List of violation strings (empty when all paths present).
    """
    violations: list[str] = []
    baseline_paths = baseline_doc.get("paths") or {}
    current_paths = current_doc.get("paths") or {}

    for path, methods in baseline_paths.items():
        if path not in current_paths:
            violations.append(
                f"  path '{path}' (baseline v{baseline_version}) removed from current ICD; "
                f"{_POLICY_MSG}"
            )
            continue
        for method in methods:
            if method not in current_paths[path]:
                violations.append(
                    f"  method '{method.upper()} {path}' (baseline v{baseline_version}) "
                    f"removed from current ICD; {_POLICY_MSG}"
                )
    return violations


def _check_response_fields(
    baseline_doc: dict[str, Any],
    current_doc: dict[str, Any],
    baseline_version: str,
) -> list[str]:
    """Assert no baseline response field has been removed.

    Compares object properties recursively where schemas are declared.
    Properties that appear in the baseline but not in the current schema
    are violations.

    Args:
        baseline_doc:     Parsed baseline ICD.
        current_doc:      Parsed current ICD.
        baseline_version: Version string (for error messages).

    Returns:
        List of violation strings (empty when all fields present).
    """
    violations: list[str] = []
    baseline_paths = baseline_doc.get("paths") or {}
    current_paths = current_doc.get("paths") or {}

    for path, methods in baseline_paths.items():
        if path not in current_paths:
            # Already reported by _check_paths; skip here.
            continue
        for method, baseline_op in methods.items():
            if method not in current_paths[path]:
                continue
            current_op = current_paths[path][method]

            # Inspect 200 response schema.
            b_resp = (baseline_op.get("responses") or {}).get("200", {})
            c_resp = (current_op.get("responses") or {}).get("200", {})
            b_schema_raw = (
                b_resp.get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            c_schema_raw = (
                c_resp.get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )

            b_schema = _unwrap_schema(b_schema_raw, baseline_doc)
            c_schema = _unwrap_schema(c_schema_raw, current_doc)

            violations.extend(
                _compare_properties(
                    b_schema,
                    c_schema,
                    baseline_doc,
                    current_doc,
                    context=f"response of {method.upper()} {path} (baseline v{baseline_version})",
                )
            )
    return violations


def _compare_properties(
    b_schema: dict[str, Any],
    c_schema: dict[str, Any],
    b_doc: dict[str, Any],
    c_doc: dict[str, Any],
    context: str,
) -> list[str]:
    """Recursively compare object properties between baseline and current schema.

    Args:
        b_schema: Baseline schema object.
        c_schema: Current schema object.
        b_doc:    Full baseline document (for $ref resolution).
        c_doc:    Full current document (for $ref resolution).
        context:  Human-readable context string for error messages.

    Returns:
        List of violation strings.
    """
    violations: list[str] = []
    b_props = _get_properties(b_schema)
    c_props = _get_properties(c_schema)

    if not b_props:
        # Baseline schema has no declared properties — nothing to protect.
        return violations

    for field_name, b_field_schema in b_props.items():
        if field_name not in c_props:
            violations.append(
                f"  field '{field_name}' removed from {context}; {_POLICY_MSG}"
            )
            continue

        # Recurse into nested objects.
        b_sub = _unwrap_schema(b_field_schema, b_doc)
        c_sub = _unwrap_schema(c_props[field_name], c_doc)
        if b_sub.get("type") == "object" or b_sub.get("properties"):
            violations.extend(
                _compare_properties(
                    b_sub,
                    c_sub,
                    b_doc,
                    c_doc,
                    context=f"field '{field_name}' in {context}",
                )
            )
    return violations


def _check_request_fields(
    baseline_doc: dict[str, Any],
    current_doc: dict[str, Any],
    baseline_version: str,
) -> list[str]:
    """Assert no optional baseline request field became required and no new required field added.

    Rules checked:
      - A field that was optional in the baseline must not be required in the current ICD.
      - A field not present at all in the baseline must not be required in the current ICD
        on a baseline endpoint.

    Args:
        baseline_doc:     Parsed baseline ICD.
        current_doc:      Parsed current ICD.
        baseline_version: Version string (for error messages).

    Returns:
        List of violation strings.
    """
    violations: list[str] = []
    baseline_paths = baseline_doc.get("paths") or {}
    current_paths = current_doc.get("paths") or {}

    for path, methods in baseline_paths.items():
        if path not in current_paths:
            continue
        for method, baseline_op in methods.items():
            if method not in current_paths[path]:
                continue
            current_op = current_paths[path][method]

            b_rb = baseline_op.get("requestBody", {})
            c_rb = current_op.get("requestBody", {})

            # Resolve request body schemas.
            b_rb_schema_raw = (
                b_rb.get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            c_rb_schema_raw = (
                c_rb.get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )

            b_schema = _unwrap_schema(b_rb_schema_raw, baseline_doc)
            c_schema = _unwrap_schema(c_rb_schema_raw, current_doc)

            b_required = _get_required(b_schema)
            c_required = _get_required(c_schema)
            b_props = _get_properties(b_schema)

            # Rule 1: optional baseline field became required.
            for field in b_props:
                if field not in b_required and field in c_required:
                    violations.append(
                        f"  field '{field}' on {method.upper()} {path} was optional in "
                        f"baseline v{baseline_version} but is now required; {_POLICY_MSG}"
                    )

            # Rule 2: new required field added on a baseline endpoint.
            for field in c_required:
                if field not in b_props:
                    violations.append(
                        f"  new required field '{field}' added to {method.upper()} {path} "
                        f"(not present in baseline v{baseline_version}); {_POLICY_MSG}"
                    )

            # Also check path parameters for new required ones.
            b_params = {
                p["name"]: p
                for p in (baseline_op.get("parameters") or [])
                if isinstance(p, dict) and "name" in p
            }
            c_params = {
                p["name"]: p
                for p in (current_op.get("parameters") or [])
                if isinstance(p, dict) and "name" in p
            }
            for pname, cparam in c_params.items():
                if pname not in b_params and cparam.get("required", False):
                    violations.append(
                        f"  new required parameter '{pname}' added to "
                        f"{method.upper()} {path} "
                        f"(not present in baseline v{baseline_version}); {_POLICY_MSG}"
                    )

    return violations


def _check_enum_values(
    baseline_doc: dict[str, Any],
    current_doc: dict[str, Any],
    baseline_version: str,
) -> list[str]:
    """Assert all baseline enum values are still present in the current ICD.

    Checks enums in:
      - Component schemas (request body fields)
      - Path parameter schemas
      - Query parameter schemas

    Args:
        baseline_doc:     Parsed baseline ICD.
        current_doc:      Parsed current ICD.
        baseline_version: Version string (for error messages).

    Returns:
        List of violation strings.
    """
    violations: list[str] = []

    # Check component schemas.
    b_schemas = (baseline_doc.get("components") or {}).get("schemas") or {}
    c_schemas = (current_doc.get("components") or {}).get("schemas") or {}

    for schema_name, b_schema in b_schemas.items():
        c_schema = c_schemas.get(schema_name, {})
        violations.extend(
            _compare_enums_in_schema(
                b_schema,
                c_schema,
                context=f"schema '{schema_name}' (baseline v{baseline_version})",
            )
        )

    # Check path/query parameters within paths.
    baseline_paths = baseline_doc.get("paths") or {}
    current_paths = current_doc.get("paths") or {}

    for path, methods in baseline_paths.items():
        if path not in current_paths:
            continue
        for method, baseline_op in methods.items():
            if method not in current_paths[path]:
                continue
            current_op = current_paths[path][method]

            b_params = baseline_op.get("parameters") or []
            c_params_by_name = {
                p["name"]: p
                for p in (current_op.get("parameters") or [])
                if isinstance(p, dict) and "name" in p
            }
            for b_param in b_params:
                if not isinstance(b_param, dict):
                    continue
                pname = b_param.get("name", "")
                b_schema = b_param.get("schema", {})
                b_enum = b_schema.get("enum")
                if not b_enum:
                    continue
                c_param = c_params_by_name.get(pname, {})
                c_enum = c_param.get("schema", {}).get("enum") or []
                for val in b_enum:
                    if val not in c_enum:
                        violations.append(
                            f"  enum value '{val}' removed from parameter '{pname}' "
                            f"on {method.upper()} {path} "
                            f"(baseline v{baseline_version}); {_POLICY_MSG}"
                        )

    return violations


def _compare_enums_in_schema(
    b_schema: dict[str, Any],
    c_schema: dict[str, Any],
    context: str,
) -> list[str]:
    """Recursively compare enum values in schema properties.

    Args:
        b_schema: Baseline schema dict.
        c_schema: Current schema dict.
        context:  Human-readable context string for error messages.

    Returns:
        List of violation strings.
    """
    violations: list[str] = []

    # Direct enum on this schema.
    b_enum = b_schema.get("enum")
    if b_enum is not None:
        c_enum = c_schema.get("enum") or []
        for val in b_enum:
            if val not in c_enum:
                violations.append(
                    f"  enum value '{val}' removed from {context}; {_POLICY_MSG}"
                )

    # Recurse into properties.
    b_props = b_schema.get("properties") or {}
    c_props = c_schema.get("properties") or {}
    for field, b_field_schema in b_props.items():
        c_field_schema = c_props.get(field) or {}
        # Unwrap anyOf/oneOf wrappers (common for nullable fields).
        b_inner = _pick_enum_bearing_subschema(b_field_schema)
        c_inner = _pick_enum_bearing_subschema(c_field_schema)
        violations.extend(
            _compare_enums_in_schema(
                b_inner,
                c_inner,
                context=f"field '{field}' in {context}",
            )
        )

    return violations


def _pick_enum_bearing_subschema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the subschema that carries enum values, unwrapping anyOf/oneOf.

    FastAPI emits nullable enums as:
        {"anyOf": [{"enum": [...]}, {"type": "null"}]}
    This helper finds the subschema that actually has an enum list.

    Args:
        schema: A JSON Schema dict.

    Returns:
        The subschema bearing 'enum', or the original schema when no wrapper present.
    """
    if "enum" in schema:
        return schema
    for key in ("anyOf", "oneOf"):
        for sub in schema.get(key) or []:
            if isinstance(sub, dict) and "enum" in sub:
                return sub
    return schema


# ---------------------------------------------------------------------------
# Top-level compat check
# ---------------------------------------------------------------------------


def check_compat(
    icd_path: pathlib.Path = _DEFAULT_ICD_PATH,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
) -> bool:
    """Assert the current ICD is a compatible superset of all supported baselines.

    Args:
        icd_path:      Path to the current ICD artifact (docs/api/icd.json).
        baselines_dir: Path to the baselines directory containing SUPPORTED and
                       the frozen baseline files.

    Returns:
        True when all checks pass; False when any violation is found or the
        manifest is empty or a baseline file is missing.
    """
    # --- Read SUPPORTED manifest ---
    supported_file = baselines_dir / "SUPPORTED"
    if not supported_file.exists():
        print(
            f"lint_icd_compat: FAIL — SUPPORTED manifest not found at {supported_file}; "
            f"support status must be explicit; {_POLICY_MSG}",
            file=sys.stderr,
        )
        return False

    raw_versions = supported_file.read_text(encoding="utf-8").splitlines()
    versions = [v.strip() for v in raw_versions if v.strip()]
    if not versions:
        print(
            f"lint_icd_compat: FAIL — SUPPORTED manifest at {supported_file} is empty; "
            f"an empty manifest is not valid — support status must be explicit; {_POLICY_MSG}",
            file=sys.stderr,
        )
        return False

    # --- Load current ICD ---
    if not icd_path.exists():
        print(
            f"lint_icd_compat: FAIL — current ICD not found at {icd_path}",
            file=sys.stderr,
        )
        return False

    try:
        current_doc = json.loads(icd_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"lint_icd_compat: FAIL — cannot parse current ICD at {icd_path}: {exc}",
            file=sys.stderr,
        )
        return False

    all_ok = True
    for version in versions:
        baseline_file = baselines_dir / f"icd-v{version}.json"
        if not baseline_file.exists():
            print(
                f"lint_icd_compat: FAIL — baseline file missing for supported version "
                f"'{version}': expected {baseline_file}; {_POLICY_MSG}",
                file=sys.stderr,
            )
            all_ok = False
            continue

        try:
            baseline_doc = json.loads(baseline_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(
                f"lint_icd_compat: FAIL — cannot parse baseline {baseline_file}: {exc}",
                file=sys.stderr,
            )
            all_ok = False
            continue

        violations: list[str] = []
        violations.extend(_check_paths(baseline_doc, current_doc, version))
        violations.extend(_check_response_fields(baseline_doc, current_doc, version))
        violations.extend(_check_request_fields(baseline_doc, current_doc, version))
        violations.extend(_check_enum_values(baseline_doc, current_doc, version))

        if violations:
            print(
                f"lint_icd_compat: FAIL — current ICD is not a compatible superset of "
                f"baseline v{version}:",
                file=sys.stderr,
            )
            for v in violations:
                print(v, file=sys.stderr)
            all_ok = False

    if all_ok:
        print(
            f"lint_icd_compat: ok — current ICD is a compatible superset of "
            f"all {len(versions)} supported baseline(s): {', '.join(versions)}"
        )
    return all_ok


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_icd_compat.py",
        description=(
            "Assert that docs/api/icd.json is a compatible superset of every "
            "ICD version listed in docs/api/baselines/SUPPORTED.  Exits 0 when "
            "all baselines are compatible, 1 when any violation is found."
        ),
    )
    parser.add_argument(
        "--icd",
        metavar="PATH",
        help=(
            "Path to the current ICD artifact.  Default: docs/api/icd.json "
            "relative to the dev tree root."
        ),
    )
    parser.add_argument(
        "--baselines-dir",
        metavar="PATH",
        help=(
            "Path to the baselines directory containing SUPPORTED and the frozen "
            "baseline files.  Default: docs/api/baselines/ relative to the dev "
            "tree root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code (0 = compatible, 1 = violation/missing, 2 = usage error).
    """
    args = _parse_args(argv)
    icd_path = pathlib.Path(args.icd) if args.icd else _DEFAULT_ICD_PATH
    baselines_dir = (
        pathlib.Path(args.baselines_dir)
        if args.baselines_dir
        else _DEFAULT_BASELINES_DIR
    )

    ok = check_compat(icd_path=icd_path, baselines_dir=baselines_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
