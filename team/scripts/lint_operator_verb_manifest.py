#!/usr/bin/env python3
"""
lint_operator_verb_manifest.py
===============================
Reconciliation check for team/config/operator_verbs.manifest: the manifest
is the single source of truth for the operator verb set, and this lint is
what keeps it honest against
the tree it describes. It never edits anything; it only reports drift.

CENSUS CLASS: CONVERT (team/SOP-core.md, "Gates and Refusals"). Manifest
drift -- a stale flag list, a REST twin pointing at a route that moved --
carries zero runtime-failure risk: nothing crashes, no data is corrupted,
the loop is not moved backwards. It is a documentation-accuracy finding,
not a build break, so it warns, files, and continues rather than blocking.

Five checks, run every time:

  1. Manifest -> tree, script existence: every "active" verb's declared
     script must exist on disk.
  2. Manifest -> tree, flag parity: for every "active" verb whose script
     declares OPERATOR_VALID_FLAGS (team/pgai_agent_kanban/lib/
     operator_verb_manifest.NO_OPERATOR_VALID_FLAGS_ARRAY names the three
     scripts that do not), the manifest's ``flags`` column and the script's
     actual OPERATOR_VALID_FLAGS array must be the same set. A flag in one
     but not the other is reported by name, naming the script.
  3. Manifest -> tree, REST twin existence: for every verb whose REST
     columns are not all "ABSENT", the (method, route) pair must be
     declared by some ``@router.get``/``@router.post`` route in the scanned
     router files (team/pgai_agent_kanban/api/reads.py and
     team/pgai_agent_kanban/api/routers/*.py).
  4. Tree -> manifest, operations.py coverage: every ``POST /operations/*``
     route that fronts an operator script (calls
     ``_script_path(request, "<script>.sh")``) must be named by some
     "active" verb's ``script`` column. This direction is scoped to
     operations.py by design -- team/pgai_agent_kanban/api/reads.py fronts a
     much wider set of read-only convenience endpoints (metrics, logs,
     traces, board, project registry reads) that are not part of the
     operator VERB set this manifest catalogues; only the verbs this
     manifest actually declares (show, why, ...) have their REST columns
     checked, via check 3 above, not the whole of reads.py's route surface.
  5. Manifest schema, impl column: every row's ``impl`` value parses under
     operator_verb_manifest.parse_impl() -- ``module:<dotted.function>`` or
     ``shell`` on an active row, ``-`` on a planned row. A missing column, a
     value outside that grammar, or a kind that does not fit the row's
     status is reported naming the verb and the accepted forms. Whether a
     ``module:`` row's route and script really share the function is
     team/scripts/lint_api_route_impl.py's question, not this lint's.

Exit codes
----------
  0   No drift found.
  1   One or more drift findings; see stdout for details.
  2   Usage error or internal failure (manifest/team dir not found).

Usage
-----
  python3 scripts/lint_operator_verb_manifest.py [--team-dir PATH] [--manifest PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Router route scanning
# ---------------------------------------------------------------------------

_ROUTER_PREFIX_RE = re.compile(r"APIRouter\([^)]*\bprefix\s*=\s*\"([^\"]*)\"")
_ROUTE_DECORATOR_RE = re.compile(r'@router\.(get|post)\(\s*\n?\s*"([^"]+)"')
_SCRIPT_PATH_CALL_RE = re.compile(r'_script_path\(\s*request\s*,\s*"([^"]+)"\s*\)')


class _RouteDecl(NamedTuple):
    method: str  # "GET" | "POST"
    route: str  # full path, prefix included
    fronted_script: str | None  # basename of the .sh script it shells out
    # to via _script_path(request, "..."), or None when the route calls no
    # operator script (a pure-Python read, e.g. GET /projects).


def _router_prefix(text: str) -> str:
    m = _ROUTER_PREFIX_RE.search(text)
    return m.group(1) if m else ""


def _scan_router_file(path: Path) -> list[_RouteDecl]:
    """Return every @router.get/@router.post route declared in *path*."""
    text = path.read_text(encoding="utf-8", errors="replace")
    prefix = _router_prefix(text)

    matches = list(_ROUTE_DECORATOR_RE.finditer(text))
    decls: list[_RouteDecl] = []
    for i, m in enumerate(matches):
        method = m.group(1).upper()
        raw_route = m.group(2)
        full_route = prefix + raw_route
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        script_match = _SCRIPT_PATH_CALL_RE.search(body)
        fronted_script = None
        if script_match:
            fronted_script = Path(script_match.group(1)).name
        decls.append(_RouteDecl(method=method, route=full_route, fronted_script=fronted_script))
    return decls


def _all_router_routes(team_dir: Path) -> list[_RouteDecl]:
    """Scan every router file this API ships for declared routes.

    Scans team/pgai_agent_kanban/api/reads.py and every *.py file under
    team/pgai_agent_kanban/api/routers/.
    """
    api_dir = team_dir / "pgai_agent_kanban" / "api"
    files = []
    reads_py = api_dir / "reads.py"
    if reads_py.is_file():
        files.append(reads_py)
    routers_dir = api_dir / "routers"
    if routers_dir.is_dir():
        files.extend(sorted(routers_dir.glob("*.py")))

    decls: list[_RouteDecl] = []
    for f in files:
        decls.extend(_scan_router_file(f))
    return decls


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_script_existence(verbs: dict, team_dir: Path) -> list[str]:
    """Check 1: every active verb's declared script exists on disk."""
    violations: list[str] = []
    for verb in verbs.values():
        if verb.status != "active":
            continue
        script_path = team_dir / verb.script
        if not script_path.is_file():
            violations.append(
                f"verb '{verb.name}': manifest declares script '{verb.script}' "
                f"which does not exist at {script_path}"
            )
    return violations


def check_flag_parity(verbs: dict, team_dir: Path, ovm_module) -> list[str]:
    """Check 2: manifest flags == script's actual OPERATOR_VALID_FLAGS, per verb."""
    violations: list[str] = []
    for verb in verbs.values():
        if verb.status != "active":
            continue
        if verb.script in ovm_module.NO_OPERATOR_VALID_FLAGS_ARRAY:
            continue  # hand-parsed script; manifest is trusted, not re-derived.

        script_path = team_dir / verb.script
        if not script_path.is_file():
            continue  # already reported by check_script_existence.

        try:
            actual_flags = set(ovm_module.extract_operator_valid_flags(script_path))
        except ValueError as exc:
            violations.append(f"verb '{verb.name}': {exc}")
            continue

        manifest_flags = set(verb.flags)

        for flag in sorted(actual_flags - manifest_flags):
            violations.append(
                f"verb '{verb.name}': script '{verb.script}' declares flag "
                f"'--{flag}' which the manifest does not list"
            )
        for flag in sorted(manifest_flags - actual_flags):
            violations.append(
                f"verb '{verb.name}': manifest declares flag '--{flag}' but "
                f"script '{verb.script}' does not accept it"
            )
    return violations


def check_rest_twin_forward(verbs: dict, declared_routes: list[_RouteDecl]) -> list[str]:
    """Check 3: every declared (non-ABSENT) REST twin resolves to a real route."""
    violations: list[str] = []
    declared_pairs = {(d.method, d.route) for d in declared_routes}
    for verb in verbs.values():
        if verb.rest_method == "ABSENT" and verb.rest_route == "ABSENT":
            continue
        pair = (verb.rest_method, verb.rest_route)
        if pair not in declared_pairs:
            violations.append(
                f"verb '{verb.name}': manifest declares REST twin "
                f"{verb.rest_method} {verb.rest_route} but no router "
                f"declares that route"
            )
    return violations


def check_operations_reverse(verbs: dict, declared_routes: list[_RouteDecl]) -> list[str]:
    """Check 4: every operations.py route fronting a script names a manifest verb."""
    violations: list[str] = []
    active_scripts = {
        Path(v.script).name for v in verbs.values() if v.status == "active" and v.script
    }
    for decl in declared_routes:
        if not decl.route.startswith("/operations/"):
            continue
        if decl.fronted_script is None:
            continue
        if decl.fronted_script not in active_scripts:
            violations.append(
                f"router route {decl.method} {decl.route} fronts script "
                f"'{decl.fronted_script}' which the manifest does not name "
                f"as any verb's script"
            )
    return violations


def check_impl_schema(verbs: dict, ovm_module) -> list[str]:
    """Check 5: every row's impl value is well-formed and fits its status."""
    violations: list[str] = []
    for verb in verbs.values():
        try:
            parsed = ovm_module.parse_impl(verb.impl)
        except ValueError as exc:
            violations.append(f"verb '{verb.name}': {exc}")
            continue
        if verb.status == "active" and parsed.kind == "planned":
            violations.append(
                f"verb '{verb.name}': impl '-' is only valid on a planned row; "
                "an active row carries 'module:<dotted.module.function>' or 'shell'"
            )
        elif verb.status == "planned" and parsed.kind != "planned":
            violations.append(
                f"verb '{verb.name}': a planned row carries impl '-'; "
                f"got {verb.impl!r}"
            )
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_operator_verb_manifest.py",
        description=(
            "Reconciliation check: reports every disagreement between "
            "team/config/operator_verbs.manifest and the tree it describes "
            "(script existence, flag parity, REST twin existence in both "
            "directions, impl column schema)."
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        help="Root of the team/ directory (default: parent of this script's directory).",
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        help="Path to the manifest file (default: team/config/operator_verbs.manifest).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each verb as it is checked.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the operator verb manifest drift lint.

    Exit codes: 0 no drift; 1 one or more drift findings (printed); 2 usage
    error or internal failure, such as the manifest or team directory not
    found.
    """
    args = _parse_args(argv)

    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        team_dir = script_dir.parent  # team/

    if not team_dir.is_dir():
        print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
        return 2

    team_str = str(team_dir)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)

    try:
        from pgai_agent_kanban.lib import operator_verb_manifest as ovm
    except ImportError as exc:
        print(
            f"ERROR: cannot import pgai_agent_kanban.lib.operator_verb_manifest: {exc}",
            file=sys.stderr,
        )
        return 2

    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    scanned_manifest_path = (
        manifest_path if manifest_path is not None else ovm.default_manifest_path()
    )

    try:
        verbs = ovm.load_manifest(manifest_path)
    except (FileNotFoundError, ovm.ManifestFormatError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.verbose:
        for name in verbs:
            print(f"  verb: {name}", flush=True)

    print(
        f"lint_operator_verb_manifest: scanning against team dir: {team_dir} "
        f"({len(verbs)} verb(s) declared)",
        flush=True,
    )

    declared_routes = _all_router_routes(team_dir)

    all_violations: list[str] = []
    all_violations.extend(check_script_existence(verbs, team_dir))
    all_violations.extend(check_flag_parity(verbs, team_dir, ovm))
    all_violations.extend(check_rest_twin_forward(verbs, declared_routes))
    all_violations.extend(check_operations_reverse(verbs, declared_routes))
    all_violations.extend(check_impl_schema(verbs, ovm))

    if all_violations:
        print(
            f"\nlint_operator_verb_manifest: FAIL — {len(all_violations)} "
            "drift finding(s):\n",
            flush=True,
        )
        for v in all_violations:
            print(f"  {v}", flush=True)
        error = "operator_verb_manifest.drift"
        message = (
            f"the operator verb manifest ({scanned_manifest_path}) "
            f"disagrees with the tree in {len(all_violations)} place(s); "
            "see the findings printed above"
        )
        rerun_manifest_flag = (
            f" --manifest {scanned_manifest_path}" if args.manifest else ""
        )
        remedy = (
            "update team/config/operator_verbs.manifest (or the script/router "
            "it disagrees with) so the two describe the same verb, then "
            f"re-run: python3 team/scripts/lint_operator_verb_manifest.py{rerun_manifest_flag}"
        )
        # CONVERT: manifest drift carries zero runtime-failure risk -- see
        # the module docstring's "CENSUS CLASS" note.
        # CENSUS-ANCHOR: operator-verb-manifest-drift-01
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract(error, message, remedy)
        except ImportError:
            print(f"error:   {error}\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
        return 1

    print(
        f"lint_operator_verb_manifest: ok — {len(verbs)} verb(s) checked, "
        f"{len(declared_routes)} router route(s) scanned, 0 drift finding(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
