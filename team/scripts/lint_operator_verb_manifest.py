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

Six checks, run every time:

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
     declared by some route in the API's route inventory -- the same
     inventory check 6 reads (any HTTP method, ``api_route`` and
     ``add_api_route``, in team/pgai_agent_kanban/api/app.py, reads.py and
     routers/*.py).
  4. Tree -> manifest, operations.py coverage: every ``/operations/*``
     route, of any method, whose handler fronts an operator script (calls
     ``_script_path(request, "<script>.sh")``) must be named by some
     "active" verb's ``script`` column. This direction is scoped to
     operations.py by design -- team/pgai_agent_kanban/api/reads.py fronts a
     much wider set of read-only convenience endpoints (metrics, logs,
     traces, board, project registry reads) that are not part of the
     operator VERB set this manifest catalogues. Those routes are held to
     check 6 instead.
  5. Manifest schema, impl column: every row's ``impl`` value parses under
     operator_verb_manifest.parse_impl() -- ``module:<dotted.function>`` or
     ``shell`` on an active row, ``-`` on a planned row. A missing column, a
     value outside that grammar, or a kind that does not fit the row's
     status is reported naming the verb and the accepted forms. Whether a
     ``module:`` row's route and script really share the function is
     team/scripts/lint_api_route_impl.py's question, not this lint's.
  6. Tree -> manifest, route coverage: every route the operator API
     registers -- each ``@app``/``@router`` method decorator (GET, POST,
     PUT, PATCH, DELETE, ...), ``api_route`` decorator and ``add_api_route``
     call in team/pgai_agent_kanban/api/app.py, reads.py and routers/*.py,
     router ``prefix=`` applied, read by
     lint_api_route_docs.scan_app_routes() --
     must be covered, whether or not it shells out to a script. A route is
     covered by the manifest row whose (rest_method, rest_route) it is, or
     else by a route entry (``GET /board``) in
     team/config/operator_verb_census_exempt.txt with its reason on the
     comment line directly above it. A script-basename entry in that file
     never covers a route. Reported, each naming method and path: a route
     nothing covers; a route entry for a route the API does not register;
     a route entry with no reason above it; a route entry for a route a
     manifest row already twins; the same route entered twice.

``--verbose`` prints every registered route with what covers it: the
manifest verb, or the exempt entry and its line number.

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
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Router route scanning
# ---------------------------------------------------------------------------

_SCRIPT_PATH_CALL_RE = re.compile(r'_script_path\(\s*request\s*,\s*"([^"]+)"\s*\)')


class _RouteDecl(NamedTuple):
    method: str  # an HTTP method, upper case: "GET", "POST", "PUT", ...
    route: str  # full path, prefix included
    fronted_script: str | None  # basename of the .sh script it shells out
    # to via _script_path(request, "..."), or None when the route calls no
    # operator script (a pure-Python read, e.g. GET /projects).


def _handler_sources(path: Path) -> dict[int, str]:
    """Map each function's ``def`` line in *path* to its source text.

    A route the shared scanner reports from a decorator carries its
    handler's ``def`` line, so this finds the handler body that check 4
    reads for a ``_script_path(request, "...")`` call. A file that does not
    parse maps nothing (the scanner already reports it).
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError):
        return {}
    sources: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sources[node.lineno] = ast.get_source_segment(text, node) or ""
    return sources


def _all_router_routes(team_dir: Path) -> list[_RouteDecl]:
    """Every route the API declares, with the operator script each fronts.

    The inventory is :func:`_registered_routes` -- the shared
    ``lint_api_route_docs.scan_app_routes`` scanner check 6 uses, which sees
    every HTTP method, ``api_route`` and ``add_api_route`` -- so checks 3, 4
    and 6 read one route inventory. ``fronted_script`` is read from the
    handler's own body; a route registered by an ``add_api_route`` call has
    no handler body here and fronts no script.
    """
    registered, _violations = _registered_routes(team_dir)
    repo_root = team_dir.parent
    sources_by_file: dict[str, dict[int, str]] = {}
    decls: list[_RouteDecl] = []
    for route in registered:
        if route.file not in sources_by_file:
            sources_by_file[route.file] = _handler_sources(repo_root / route.file)
        body = sources_by_file[route.file].get(route.line, "")
        script_match = _SCRIPT_PATH_CALL_RE.search(body)
        fronted_script = Path(script_match.group(1)).name if script_match else None
        decls.append(_RouteDecl(method=route.method, route=route.route, fronted_script=fronted_script))
    return decls


# ---------------------------------------------------------------------------
# Registered route inventory and the exempt file's route entries
# ---------------------------------------------------------------------------

# The exempt sidecar, relative to team/. lint_api_parity.py reads its
# script-basename entries; check 6 reads its route entries.
_CENSUS_EXEMPT_RELPATH = Path("config") / "operator_verb_census_exempt.txt"

# A route entry: an HTTP method, whitespace, then a path. Anything else that
# is not blank or a comment is a script-basename entry, which check 6 ignores.
_ROUTE_ENTRY_RE = re.compile(
    r"^\s*(GET|POST|PUT|PATCH|DELETE|HEAD)\s+(/\S*)\s*$", re.IGNORECASE
)
# A reason line: a comment carrying at least one word character, so a bare
# "# ------" separator does not count as a reason.
_REASON_LINE_RE = re.compile(r"^\s*#.*\w")


class _RegisteredRoute(NamedTuple):
    method: str  # "GET" | "POST"
    route: str  # full path, router prefix included
    file: str  # repository-relative source file
    line: int  # line of the handler's ``def``


class _RouteExemption(NamedTuple):
    method: str
    route: str
    line: int  # 1-based line in the exempt file
    has_reason: bool  # a comment with text sits on the nearest non-blank line above


def _registered_routes(team_dir: Path) -> tuple[list[_RegisteredRoute], list[str]]:
    """Every route the API registers, and any file that could not be parsed.

    Reuses lint_api_route_docs.scan_app_routes() -- the scan of app.py's own
    handlers (``GET /health`` inside create_app()) plus reads.py and
    routers/*.py with each router's prefix applied -- rather than a second
    scanner. Returns (routes, violations); a router file that does not parse
    is a violation naming it, since its routes were not checked.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import lint_api_route_docs  # a sibling script, found through scripts_dir

    routes, findings = lint_api_route_docs.scan_app_routes(team_dir, team_dir.parent)
    registered = [_RegisteredRoute(r.method, r.path, r.file, r.line) for r in routes]
    violations = [f"route inventory: {f.summary}" for f in findings]
    return registered, violations


def _load_route_exemptions(path: Path) -> list[_RouteExemption]:
    """Return the route entries (``METHOD /path``) of the exempt file at *path*.

    A missing file has no entries. Methods are upper-cased; script-basename
    entries, blank lines and comments are skipped.
    """
    if not path.is_file():
        return []
    entries: list[_RouteExemption] = []
    previous = ""  # the nearest non-blank line above the current one
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        m = _ROUTE_ENTRY_RE.match(raw)
        if m:
            entries.append(
                _RouteExemption(
                    method=m.group(1).upper(),
                    route=m.group(2),
                    line=lineno,
                    has_reason=bool(_REASON_LINE_RE.match(previous)),
                )
            )
        previous = raw
    return entries


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


def check_route_coverage(
    verbs: dict,
    routes: list[_RegisteredRoute],
    exemptions: list[_RouteExemption],
    exempt_rel: str,
) -> tuple[list[str], list[str]]:
    """Check 6: every registered route is a manifest twin or a reasoned route entry.

    Returns (violations, coverage), where *coverage* is one line per
    registered route naming what covers it -- what ``--verbose`` prints.
    *exempt_rel* names the exempt file in messages.
    """
    violations: list[str] = []
    coverage: list[str] = []
    twins = {
        (v.rest_method, v.rest_route): v.name
        for v in verbs.values()
        if not (v.rest_method == "ABSENT" and v.rest_route == "ABSENT")
    }
    registered = {(r.method, r.route) for r in routes}

    entries: dict[tuple[str, str], _RouteExemption] = {}
    for entry in exemptions:
        pair = (entry.method, entry.route)
        label = f"{entry.method} {entry.route}"
        if pair in entries:
            violations.append(
                f"exempt entry '{label}' ({exempt_rel}:{entry.line}) repeats the "
                f"entry on line {entries[pair].line}; keep one"
            )
            continue
        entries[pair] = entry
        if pair not in registered:
            violations.append(
                f"exempt entry '{label}' ({exempt_rel}:{entry.line}) names a route "
                "the API does not register; remove it or correct the path"
            )
        elif pair in twins:
            violations.append(
                f"exempt entry '{label}' ({exempt_rel}:{entry.line}) names the REST "
                f"twin of manifest verb '{twins[pair]}', which already covers it; "
                "remove the entry"
            )
        if not entry.has_reason:
            violations.append(
                f"exempt entry '{label}' ({exempt_rel}:{entry.line}) has no reason; "
                "add a comment saying why the route has no manifest row on the "
                "line directly above it"
            )

    for r in sorted(routes, key=lambda r: (r.route, r.method)):
        pair = (r.method, r.route)
        label = f"{r.method} {r.route}"
        if pair in twins:
            coverage.append(f"{label} -- manifest verb '{twins[pair]}'")
        elif pair in entries:
            coverage.append(f"{label} -- exempt entry, {exempt_rel}:{entries[pair].line}")
        else:
            coverage.append(f"{label} -- NOT COVERED")
            violations.append(
                f"route {label} ({r.file}:{r.line}) is registered on the API but no "
                "manifest row declares it as a REST twin and "
                f"{exempt_rel} has no '{label}' entry"
            )
    return violations, coverage


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
            "directions, impl column schema, and coverage of every registered "
            "API route by a manifest row or a reasoned exempt route entry)."
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
        help="Print each verb as it is checked, and every registered route "
        "with what covers it.",
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
    registered_routes, inventory_violations = _registered_routes(team_dir)
    route_exemptions = _load_route_exemptions(team_dir / _CENSUS_EXEMPT_RELPATH)
    coverage_violations, coverage = check_route_coverage(
        verbs, registered_routes, route_exemptions, f"team/{_CENSUS_EXEMPT_RELPATH}"
    )

    if args.verbose:
        for line in coverage:
            print(f"  route: {line}", flush=True)

    all_violations: list[str] = []
    all_violations.extend(check_script_existence(verbs, team_dir))
    all_violations.extend(check_flag_parity(verbs, team_dir, ovm))
    all_violations.extend(check_rest_twin_forward(verbs, declared_routes))
    all_violations.extend(check_operations_reverse(verbs, declared_routes))
    all_violations.extend(check_impl_schema(verbs, ovm))
    all_violations.extend(inventory_violations)
    all_violations.extend(coverage_violations)

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
            "it disagrees with) so the two describe the same verb -- or, for a "
            "route no verb twins, add a reasoned route entry to "
            "team/config/operator_verb_census_exempt.txt -- then "
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
        f"{len(declared_routes)} router route(s) scanned, "
        f"{len(registered_routes)} registered route(s) covered, 0 drift finding(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
