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

Published-baseline immutability (rule e):
  e. Every baseline file docs/api/baselines/icd-v<X.Y.Z>.json whose version is
     below the current ICD version (icd.json info.version, compared with the
     framework semver helper, never string order) is byte-identical to the
     copy on the project's prefixed main branch (``<branch_prefix>main`` from
     project.cfg — the last released tree).  A published contract never
     mutates; a regeneration that rewrote an older baseline is reported here
     naming the file.  The comparison is skipped, with a one-line note, when
     the baselines directory is not inside a git work tree, when the prefixed
     main branch does not exist locally, or for a baseline the main branch
     does not carry yet (the version this release introduces).  Quality
     class: the gated runners record the finding and continue.

Support-table mirror check:
  The "Support table" in docs/operator-api.md (the markdown table under the
  "Support table" heading whose header row carries a Version column) mirrors
  docs/api/baselines/SUPPORTED.  Every version SUPPORTED lists must have a row
  there with a Status other than ``retired``, and every row whose Status is
  not ``retired`` must name a version SUPPORTED lists.  The check reports, by
  name and naming both files:
    - missing: a version SUPPORTED lists that has no table row;
    - extra:   a non-retired table row whose version SUPPORTED does not list;
    - retired: a row marked ``retired`` whose version SUPPORTED still lists.
  A missing document, or one without the support table, is reported too.
  SUPPORTED is read liberally here: blank lines and ``#`` comment lines are
  skipped, and a leading ``v`` on a version is ignored on either side.  The
  compatibility rules above read SUPPORTED exactly as before.  Quality class
  (CONVERT): documentation drift carries zero runtime-failure risk, so the
  gated runners record the finding and continue; it never changes the
  return value of check_compat(), only this script's own exit status.

On any violation, the script exits 1 and prints a message naming the specific
break and stating the policy:
  "breaking changes require a major ICD version and an operator-approved RC"
A support-table drift finding instead prints its causes, the fix, and the
mirror rule.

Wired into both gated runners (run-unit-tests.sh and run-integration-tests.sh)
as a pre-flight check, following the lint_icd_freshness pattern.

Usage:
    python3 team/scripts/lint_icd_compat.py [--icd PATH] [--baselines-dir PATH]
                                            [--main-ref REF] [--support-doc PATH]

Options:
    --icd PATH           Path to the current ICD artifact.
                         Default: docs/api/icd.json relative to the dev tree root.
    --baselines-dir PATH Path to the baselines directory containing SUPPORTED and
                         the frozen baseline files.
                         Default: docs/api/baselines/ relative to the dev tree root.
    --main-ref REF       Git ref holding the last released copy of every baseline,
                         for the published-baseline immutability check.
                         Default: <branch_prefix>main, the prefix read from
                         project.cfg via pgai_agent_kanban.lib.branch_prefix.
    --support-doc PATH   Markdown document holding the support table that
                         mirrors SUPPORTED, for the support-table mirror check.
                         Default: docs/operator-api.md relative to the dev tree
                         root.
    --help, -h           Show this help and exit.

Exit codes:
    0   All supported baselines are compatible with the current ICD, no
        published baseline differs from the main-branch copy, and the support
        table mirrors SUPPORTED.
    1   Compatibility violation, empty SUPPORTED manifest, missing baseline file,
        a published baseline that differs from the main-branch copy, or
        support-table drift (a missing, extra, or wrongly retired version, or
        no support table found).
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
import re
import subprocess
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
_DEFAULT_SUPPORT_DOC = _DEV_TREE_ROOT / "docs" / "operator-api.md"

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
# Published-baseline immutability (rule e)
# ---------------------------------------------------------------------------


def _import_semver():
    """Import the framework's semver helper (``pgai_agent_kanban.lib.semver``).

    Version ordering always goes through this helper — ``1.10.0`` sorts below
    ``1.9.0`` as a string and above it under semver.  The package lives in
    this script's own team/ directory, so team/ is located from ``__file__``.

    Returns:
        The ``pgai_agent_kanban.lib.semver`` module.
    """
    team_str = str(_TEAM_DIR)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    from pgai_agent_kanban.lib import semver

    return semver


def _resolve_main_ref() -> str:
    """Return the project's prefixed main branch name (``<branch_prefix>main``).

    The prefix comes from project.cfg through the shared reader in
    ``pgai_agent_kanban.lib.branch_prefix`` — never a hardcoded ``ai_main``.
    When that module cannot be imported the reader's own shipped default
    prefix is used, so the check still names a real branch.

    Returns:
        Branch name such as ``"ai_main"``.
    """
    team_str = str(_TEAM_DIR)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    from pgai_agent_kanban.lib.branch_prefix import prefixed_main_branch

    return prefixed_main_branch()


def _git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in *cwd*, never raising on a non-zero exit.

    Args:
        args: Arguments after ``git``.
        cwd:  Working directory for the invocation.

    Returns:
        The completed process; ``returncode`` distinguishes a missing ref or
        object from success.  Output is captured as bytes so a baseline's
        exact on-branch bytes can be compared.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
    )


def check_published_baselines(
    current_version: str,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
    main_ref: str | None = None,
) -> list[str]:
    """Report every baseline below *current_version* that differs from *main_ref*.

    A baseline file whose version is below the current ICD version was
    published by an earlier release, and the copy on the prefixed main branch
    is the last released tree — so the two must be byte-identical.  A
    difference means a regeneration rewrote a published contract.

    The check degrades to a one-line note (no violation) when it cannot
    compare: *baselines_dir* is not inside a git work tree, *main_ref* does
    not resolve, or the main branch does not carry the file (the version this
    release introduces).  A version that is not below *current_version* is
    not this rule's business — the generator's own refusal guards the current
    version.

    Args:
        current_version: The current ICD version (``info.version`` of icd.json).
        baselines_dir:   Directory holding the frozen baseline files.
        main_ref:        Git ref to compare against.  ``None`` resolves
                         ``<branch_prefix>main`` from project.cfg.

    Returns:
        List of violation strings, one per mutated baseline, each naming the
        file, the ref, and the restoring command.  Empty when clean or skipped.
    """
    semver = _import_semver()
    baselines_dir = pathlib.Path(baselines_dir)
    if not baselines_dir.is_dir():
        return []

    toplevel_proc = _git(["rev-parse", "--show-toplevel"], cwd=baselines_dir)
    if toplevel_proc.returncode != 0:
        print(
            f"lint_icd_compat: note — published-baseline check skipped: "
            f"{baselines_dir} is not inside a git work tree"
        )
        return []
    repo_root = pathlib.Path(toplevel_proc.stdout.decode("utf-8").strip())

    ref = main_ref if main_ref is not None else _resolve_main_ref()
    if _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_root).returncode != 0:
        print(
            f"lint_icd_compat: note — published-baseline check skipped: "
            f"git ref {ref!r} does not exist in {repo_root}"
        )
        return []

    try:
        current_parsed = semver.parse(current_version)
    except ValueError:
        print(
            f"lint_icd_compat: note — published-baseline check skipped: current ICD "
            f"version {current_version!r} is not X.Y.Z"
        )
        return []

    violations: list[str] = []
    for baseline_file in sorted(baselines_dir.glob("icd-v*.json")):
        token = semver.from_filename(baseline_file.name)
        if not token:
            continue
        if not semver.lt(semver.parse(token), current_parsed):
            continue  # current or newer: not a published-and-superseded baseline
        try:
            rel_path = baseline_file.resolve().relative_to(repo_root.resolve())
        except ValueError:
            continue  # outside the work tree the toplevel names; nothing to compare
        rel = rel_path.as_posix()
        show = _git(["show", f"{ref}:{rel}"], cwd=repo_root)
        if show.returncode != 0:
            continue  # not on the main branch yet: introduced by this release
        if show.stdout != baseline_file.read_bytes():
            violations.append(
                f"  published baseline {rel} (version {token.lstrip('vV')}, below current "
                f"ICD version {current_version}) differs from its copy on {ref}; a "
                f"published contract never mutates — restore it with "
                f"'git -C {repo_root} checkout {ref} -- {rel}'"
            )
    return violations


# ---------------------------------------------------------------------------
# Support-table mirror check
# ---------------------------------------------------------------------------

# The heading that introduces the support table, at any markdown level.
_SUPPORT_TABLE_HEADING_RE = re.compile(r"^\s*#{1,6}\s+Support\s+table\s*$", re.IGNORECASE)
# Any markdown heading: the support table's section ends at the next one.
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")
# A markdown table row: a line whose first non-blank character is a pipe.
_TABLE_ROW_RE = re.compile(r"^\s*\|")
# A header/body separator row such as |---|:---:|---|.
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*$")
# The Status value that marks a row as no longer protected by SUPPORTED.
_RETIRED_STATUS = "retired"


def _normalize_version(text: str) -> str:
    """Return *text* as a bare version token: trimmed, unquoted, no leading v.

    Args:
        text: A version as written in SUPPORTED or a table cell, e.g.
              ``" `v1.13.1` "``.

    Returns:
        The bare token, e.g. ``"1.13.1"``; empty when *text* holds none.
    """
    token = text.strip().strip("`").strip()
    if token[:1] in ("v", "V"):
        token = token[1:]
    return token


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell texts.

    Args:
        line: One table line, e.g. ``"| 1.13.0 | supported | v2.28.0 |"``.

    Returns:
        The cell texts without the outer pipes, e.g.
        ``["1.13.0", "supported", "v2.28.0"]``.
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def read_manifest_versions(supported_file: pathlib.Path) -> list[str]:
    """Return the versions a SUPPORTED manifest lists, in file order.

    Liberal reader for the support-table mirror check: blank lines and lines
    whose first non-blank character is ``#`` are skipped, surrounding
    whitespace is ignored, and a leading ``v`` is dropped.  The compatibility
    rules keep their own reading of the manifest in check_compat().

    Args:
        supported_file: Path to the SUPPORTED manifest.

    Returns:
        Version tokens such as ``["1.0.0", "1.1.0"]``.

    Raises:
        OSError: when the file cannot be read.
    """
    versions: list[str] = []
    for raw in supported_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = _normalize_version(line)
        if token:
            versions.append(token)
    return versions


def parse_support_table(doc_path: pathlib.Path) -> list[tuple[str, str, int]] | None:
    """Return the rows of the support table in *doc_path*.

    The support table is the first markdown table, after a heading reading
    "Support table" (any level, any case) and before the next heading, whose
    header row has a ``Version`` column.  The ``Status`` column is optional;
    without one every row reads as ``supported``.

    Args:
        doc_path: Markdown document holding the table (docs/operator-api.md).

    Returns:
        One ``(version, status, line_number)`` tuple per body row, in table
        order: *version* bare (no leading ``v``), *status* lower-cased, and
        *line_number* 1-indexed.  ``None`` when no such heading or table
        exists.

    Raises:
        OSError: when the file cannot be read.
    """
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    in_section = False
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if _SUPPORT_TABLE_HEADING_RE.match(line):
            in_section = True
            idx += 1
            continue
        if in_section and _ANY_HEADING_RE.match(line):
            in_section = False
        if in_section and _TABLE_ROW_RE.match(line):
            header = [cell.lower() for cell in _split_table_row(line)]
            if "version" not in header:
                # Some other table inside the section: skip past it.
                while idx < len(lines) and _TABLE_ROW_RE.match(lines[idx]):
                    idx += 1
                continue
            version_col = header.index("version")
            status_col = header.index("status") if "status" in header else None
            rows: list[tuple[str, str, int]] = []
            idx += 1
            while idx < len(lines) and _TABLE_ROW_RE.match(lines[idx]):
                row_line = lines[idx]
                idx += 1
                if _TABLE_SEPARATOR_RE.match(row_line):
                    continue
                cells = _split_table_row(row_line)
                if version_col >= len(cells):
                    continue
                version = _normalize_version(cells[version_col])
                if not version:
                    continue
                status = "supported"
                if status_col is not None and status_col < len(cells):
                    status = cells[status_col].strip().strip("`").strip().lower()
                rows.append((version, status, idx))
            return rows
        idx += 1
    return None


def find_support_table_drift(
    doc_path: pathlib.Path = _DEFAULT_SUPPORT_DOC,
    supported_file: pathlib.Path = _DEFAULT_BASELINES_DIR / "SUPPORTED",
) -> list[tuple[str, str, str]]:
    """Find every way the support table in *doc_path* fails to mirror SUPPORTED.

    Args:
        doc_path:       Markdown document holding the support table.
        supported_file: The SUPPORTED manifest the table mirrors.

    Returns:
        One ``(kind, version, cause)`` tuple per finding.  *kind* is
        ``missing``, ``extra``, ``retired``, or ``no-table``; *version* is the
        bare version (empty for ``no-table``); *cause* is the printable line,
        naming the version and both files.  Empty when the table mirrors the
        manifest, and also when SUPPORTED itself is absent — check_compat()
        already reports that.
    """
    if not supported_file.is_file():
        return []
    if not doc_path.is_file():
        return [(
            "no-table",
            "",
            f"  support-table document {doc_path} not found; it must carry the "
            f"support table mirroring {supported_file}",
        )]

    manifest = read_manifest_versions(supported_file)
    rows = parse_support_table(doc_path)
    if rows is None:
        return [(
            "no-table",
            "",
            f"  no support table found in {doc_path}: expected a 'Support table' "
            f"heading followed by a table with a Version column mirroring "
            f"{supported_file}",
        )]

    manifest_set = set(manifest)
    table_versions = {version for version, _status, _line in rows}
    findings: list[tuple[str, str, str]] = []

    for version in manifest:
        if version not in table_versions:
            findings.append((
                "missing",
                version,
                f"  missing: version {version} is listed in {supported_file} but "
                f"has no row in the support table in {doc_path}",
            ))

    for version, status, line_no in rows:
        if status == _RETIRED_STATUS:
            if version in manifest_set:
                findings.append((
                    "retired",
                    version,
                    f"  retired: version {version} is marked '{_RETIRED_STATUS}' in "
                    f"the support table at {doc_path}:{line_no} but is still listed "
                    f"in {supported_file}",
                ))
        elif version not in manifest_set:
            findings.append((
                "extra",
                version,
                f"  extra: version {version} has a '{status}' row in the support "
                f"table at {doc_path}:{line_no} but is not listed in {supported_file}",
            ))
    return findings


def check_support_table(
    doc_path: pathlib.Path = _DEFAULT_SUPPORT_DOC,
    supported_file: pathlib.Path = _DEFAULT_BASELINES_DIR / "SUPPORTED",
) -> list[str]:
    """Return the cause lines of find_support_table_drift(), in the same order.

    Args:
        doc_path:       Markdown document holding the support table.
        supported_file: The SUPPORTED manifest the table mirrors.

    Returns:
        One cause line per finding; empty when the table mirrors SUPPORTED.
    """
    return [
        cause
        for _kind, _version, cause in find_support_table_drift(
            doc_path=doc_path, supported_file=supported_file
        )
    ]


def report_support_table_drift(
    doc_path: pathlib.Path = _DEFAULT_SUPPORT_DOC,
    supported_file: pathlib.Path = _DEFAULT_BASELINES_DIR / "SUPPORTED",
) -> bool:
    """Run find_support_table_drift() and print its result.

    Clean: one ``ok`` line on stdout.  Drift: a remedy block on stderr — the
    causes, the numbered fix, and the mirror rule.

    Args:
        doc_path:       Markdown document holding the support table.
        supported_file: The SUPPORTED manifest the table mirrors.

    Returns:
        True when the table mirrors the manifest; False on any drift.
    """
    findings = find_support_table_drift(doc_path=doc_path, supported_file=supported_file)
    if not findings:
        if supported_file.is_file():
            print(
                f"lint_icd_compat: ok — the support table in {doc_path} mirrors "
                f"{supported_file}"
            )
        return True

    # CONVERT: documentation drift carries zero runtime-failure risk; the
    # gated runners record this finding and continue.
    print(
        f"lint_icd_compat: FAIL — the support table in {doc_path} does not "
        f"mirror {supported_file}",
        file=sys.stderr,
    )
    print("Cause(s):", file=sys.stderr)
    for _kind, _version, cause in findings:
        print(cause, file=sys.stderr)
    kinds = {kind for kind, _version, _cause in findings}
    missing = [version for kind, version, _cause in findings if kind == "missing"]
    print("To fix:", file=sys.stderr)
    print(f"  1. Edit the 'Support table' section of {doc_path}:", file=sys.stderr)
    if "no-table" in kinds:
        print(
            "       restore the 'Support table' heading and its "
            "'| Version | Status | Since |' table, one row per version in "
            f"{supported_file}.",
            file=sys.stderr,
        )
    if missing:
        print(
            "       add one row per missing version, filling Since with the "
            "kanban release that first published that baseline (format vX.Y.Z):",
            file=sys.stderr,
        )
        for version in missing:
            print(f"         | {version} | supported | vX.Y.Z |", file=sys.stderr)
    if "extra" in kinds:
        print(
            f"       set an extra row's Status to '{_RETIRED_STATUS}' when its "
            f"version was retired, or add its line to {supported_file} when it "
            f"is meant to be supported.",
            file=sys.stderr,
        )
    if "retired" in kinds:
        print(
            "       set a wrongly retired row's Status back to 'supported', or "
            f"remove its line from {supported_file} to finish the retirement.",
            file=sys.stderr,
        )
    print(
        f"  2. python3 {_THIS_FILE} --baselines-dir {supported_file.parent} "
        f"--support-doc {doc_path}",
        file=sys.stderr,
    )
    print(
        "Rule: the support table lists every version in SUPPORTED as "
        "supported, and every row not marked retired names a version "
        "SUPPORTED lists.",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# Top-level compat check
# ---------------------------------------------------------------------------


def check_compat(
    icd_path: pathlib.Path = _DEFAULT_ICD_PATH,
    baselines_dir: pathlib.Path = _DEFAULT_BASELINES_DIR,
    main_ref: str | None = None,
) -> bool:
    """Assert the current ICD is a compatible superset of all supported baselines
    and that no published baseline has been rewritten since the last release.

    Args:
        icd_path:      Path to the current ICD artifact (docs/api/icd.json).
        baselines_dir: Path to the baselines directory containing SUPPORTED and
                       the frozen baseline files.
        main_ref:      Git ref for the published-baseline check (rule e).
                       ``None`` resolves ``<branch_prefix>main`` from project.cfg.

    Returns:
        True when all checks pass; False when any violation is found, the
        manifest is empty, a baseline file is missing, or a published baseline
        differs from its main-branch copy.
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

    # Rule e: every baseline below the current version is byte-identical to
    # the last released tree.  Quality class — the gated runners record the
    # finding and continue; this script's own exit 1 is for direct invocation.
    current_version = str((current_doc.get("info") or {}).get("version") or "")
    published_violations = check_published_baselines(
        current_version, baselines_dir=baselines_dir, main_ref=main_ref
    )
    if published_violations:
        print(
            "lint_icd_compat: FAIL — a published baseline was rewritten after "
            "release (published contracts are immutable):",
            file=sys.stderr,
        )
        for v in published_violations:
            print(v, file=sys.stderr)
        all_ok = False

    if all_ok:
        print(
            f"lint_icd_compat: ok — current ICD is a compatible superset of "
            f"all {len(versions)} supported baseline(s): {', '.join(versions)}; "
            f"no published baseline below {current_version} differs from the "
            f"main-branch copy"
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
            "ICD version listed in docs/api/baselines/SUPPORTED, and that every "
            "baseline below the current ICD version is byte-identical to its copy "
            "on the prefixed main branch, and that the support table in "
            "docs/operator-api.md mirrors SUPPORTED (reports missing, extra, and "
            "wrongly retired versions by name).  Exits 0 when all checks pass, "
            "1 when any violation or drift is found."
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
    parser.add_argument(
        "--main-ref",
        metavar="REF",
        help=(
            "Git ref holding the last released copy of every baseline, for the "
            "published-baseline immutability check.  Default: <branch_prefix>main "
            "with the prefix read from project.cfg."
        ),
    )
    parser.add_argument(
        "--support-doc",
        metavar="PATH",
        help=(
            "Markdown document holding the support table that mirrors "
            "SUPPORTED, for the support-table mirror check.  Default: "
            "docs/operator-api.md relative to the dev tree root."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code (0 = compatible and mirrored, 1 = violation/missing/drift,
        2 = usage error).
    """
    args = _parse_args(argv)
    icd_path = pathlib.Path(args.icd) if args.icd else _DEFAULT_ICD_PATH
    baselines_dir = (
        pathlib.Path(args.baselines_dir)
        if args.baselines_dir
        else _DEFAULT_BASELINES_DIR
    )

    support_doc = (
        pathlib.Path(args.support_doc) if args.support_doc else _DEFAULT_SUPPORT_DOC
    )

    ok = check_compat(
        icd_path=icd_path, baselines_dir=baselines_dir, main_ref=args.main_ref
    )
    # The mirror check runs whatever check_compat() found, so one run reports
    # every finding.  It is kept out of check_compat() so that function's
    # return value still speaks for the compatibility rules alone.
    mirrored = report_support_table_drift(
        doc_path=support_doc, supported_file=baselines_dir / "SUPPORTED"
    )
    return 0 if ok and mirrored else 1


if __name__ == "__main__":
    sys.exit(main())
