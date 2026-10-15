#!/usr/bin/env python3
"""
lint_api_route_docs.py
======================
Checks that every REST route the API serves is documented in the operator
API reference, ``docs/operator-api.md``, and that the reference's list of
routes that start a process matches the operator verb census.

The routes are read from the API's own source, never from a list kept
beside it: every ``@app.get``/``@app.post`` and ``@router.get``/
``@router.post`` handler in ``team/pgai_agent_kanban/api/app.py``,
``reads.py`` and ``routers/*.py``, with each router file's ``prefix=``
applied. The router scan is ``lint_api_route_impl.py``'s, imported.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). An undocumented route is a
documentation finding with zero runtime-failure risk: the route still
serves. The lint narrates each finding with a runnable remedy, writes them
to ``--findings-file`` for the gated runner's aggregate, and ALWAYS exits 0.
It is deliberately a sibling of ``lint_api_parity.py`` rather than part of
it: that lint is a BLOCK-WORTHY gate, and a documentation gap must not stop
a runner.

WHAT IS CHECKED
---------------
Paths are compared with path parameters as wildcards: the route
``/projects/{project}/tasks/{key}/readme`` matches a documented
``/projects/{project}/tasks/{key}/readme`` and a curl URL
``/projects/my-app/tasks/my-task/readme`` alike, and ``/dashboard/{pane}``
matches ``/dashboard/queue``. A query string is ignored.

  route_table_row_missing   The route has no row in a hand-written table of
                            the "Endpoint table" section (the ``##`` heading
                            whose title contains "Endpoint table") -- a
                            table row whose first cell holds a code span
                            ``METHOD /path`` -- and no heading of its own in
                            that section carrying the same code span. The
                            generated census reference (between the
                            ``OPERATOR-VERBS-GENERATED`` markers) does not
                            count: it is derived from the census, not
                            written for the reader.
  route_curl_missing        No ``curl`` command anywhere on the page has a
                            URL whose path matches the route. Backslash
                            continuations are joined first.
  process_route_unlisted    The route starts a process -- its census row's
                            ``impl`` is ``shell``, or it is the dashboard
                            pane route ``GET /dashboard/{pane}``, which runs
                            a pane script by design and has no census row --
                            but the "starts a process" list does not name
                            it.
  process_route_not_shell   The "starts a process" list names a route whose
                            census row is not ``impl shell`` (the route runs
                            in process), or one the API does not declare.

The "starts a process" list is every ``METHOD /path`` code span in the
bullet items of the Endpoint table section's introduction, the text before
its first ``###`` subsection.

OUTPUT
------
One line per finding, then a totals line:

    lint_api_route_docs: [<kind>] <summary>
    lint_api_route_docs: totals: <N> route(s), <R> documented row(s), <C> curl URL(s), <P> listed process route(s); <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``route``, ``file``, ``line``,
``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

USAGE
-----
  python3 team/scripts/lint_api_route_docs.py [--root PATH] [--doc PATH]
                                              [--manifest PATH]
                                              [--findings-file PATH]
                                              [--verbose]

Exit codes:
  0  Always (findings are narrated, never fatal). A malformed command line
     still exits 2 (argparse usage validation, not a loop-facing gate).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional

# The router scan lives in lint_api_route_impl.py beside this file; it is
# imported, never copied.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import lint_api_route_impl as _route_scan  # noqa: E402

LINT_NAME = "api_route_docs"
RUN_COMMAND = "python3 team/scripts/lint_api_route_docs.py"
DEFAULT_DOC = "docs/operator-api.md"
DEFAULT_MANIFEST = "team/config/operator_verbs.manifest"

# The app factory module: its routes (``GET /health``) are declared on the
# app inside create_app(), not on a router.
_APP_FILE = Path("pgai_agent_kanban") / "api" / "app.py"

# Routes that start a process by design without a census row of their own:
# the dashboard pane route runs the pane's script and waits for it.
PANE_PROCESS_ROUTES: tuple[tuple[str, str], ...] = (("GET", "/dashboard/{pane}"),)

_METHODS = "GET|POST|PUT|PATCH|DELETE"
_ROUTE_SPAN_RE = re.compile(rf"`\s*({_METHODS})\s+(/[^`\s]*)\s*`")
_ENDPOINT_SECTION_RE = re.compile(r"^##\s+.*\bEndpoint\s+table\b", re.IGNORECASE)
_CURL_SECTION_RE = re.compile(r"^##\s+.*\bcurl\s+examples\b", re.IGNORECASE)
_H2_RE = re.compile(r"^##\s")
_H3_RE = re.compile(r"^###\s")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+")
_GENERATED_BEGIN_RE = re.compile(r"<!--\s*OPERATOR-VERBS-GENERATED:BEGIN")
_GENERATED_END_RE = re.compile(r"<!--\s*OPERATOR-VERBS-GENERATED:END")
_CURL_RE = re.compile(r"(?<![\w`-])curl\s")
_URL_PATH_RE = re.compile(r"https?://[^/\s\"'`]+(/[^\s\"'`?#]*)?")
_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(:path)?\}")


class Finding(NamedTuple):
    """One documentation gap between the API's routes and docs/operator-api.md."""

    kind: str
    route: str  # "METHOD /path", or "" when the finding is not about one route
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``route``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "route": self.route,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": self.remedy,
        }


class Route(NamedTuple):
    """One route the API declares."""

    method: str
    path: str
    file: str  # repository-relative source file
    line: int

    @property
    def label(self) -> str:
        """The route as ``METHOD /path``."""
        return f"{self.method} {self.path}"


class DocRoute(NamedTuple):
    """One ``METHOD /path`` the document names, and the line it is on."""

    method: str
    path: str
    line: int


class DocIndex(NamedTuple):
    """What docs/operator-api.md says about the routes."""

    documented: list[DocRoute]  # hand-written table rows and headings in the Endpoint table section
    curl_paths: list[tuple[str, int]]  # (URL path, line) of every curl command
    process_list: list[DocRoute]  # the "starts a process" bullet list
    endpoint_line: int  # line of the Endpoint table heading (1 when absent)
    curl_line: int  # line of the curl examples heading (endpoint_line when absent)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def route_pattern(path: str) -> "re.Pattern[str]":
    """Full-match pattern for *path* with every ``{param}`` as a one-segment wildcard.

    A ``{param:path}`` parameter matches one or more segments.
    """
    out: list[str] = []
    pos = 0
    for match in _PARAM_RE.finditer(path):
        out.append(re.escape(path[pos:match.start()]))
        out.append(".+" if match.group(2) else "[^/]+")
        pos = match.end()
    out.append(re.escape(path[pos:]))
    return re.compile("".join(out))


def _matches(route: Route, method: Optional[str], doc_path: str) -> bool:
    if method is not None and method != route.method:
        return False
    return bool(route_pattern(route.path).fullmatch(doc_path.rstrip("/") or "/"))


def scan_app_routes(team_dir: Path, root: Path) -> tuple[list[Route], list[Finding]]:
    """Every route the API declares: app.py's own handlers plus every router file's.

    Handlers nested inside a function (``create_app``'s ``@app.get``) count.
    A file that does not parse is a finding naming it; its routes are not
    checked.
    """
    files: list[Path] = []
    if (team_dir / _APP_FILE).is_file():
        files.append(team_dir / _APP_FILE)
    files.extend(_route_scan.router_files(team_dir))
    routes: list[Route] = []
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for path in files:
        rel = _route_scan._rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            findings.append(Finding(
                "parse-error", "", rel, 0,
                f"{rel} could not be parsed ({exc}); its routes were not checked",
                f"python3 -m py_compile {rel}",
            ))
            continue
        prefix = _route_scan._router_prefix(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for method, sub in _route_scan._route_decorations(node):
                key = (method, prefix + sub)
                if key not in seen:
                    seen.add(key)
                    routes.append(Route(method, prefix + sub, rel, node.lineno))
    routes.sort(key=lambda r: (r.path, r.method))
    return routes, findings


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def _strip_generated(lines: list[str]) -> list[str]:
    """Blank every line inside the generated census reference, markers included."""
    out: list[str] = []
    inside = False
    for line in lines:
        if _GENERATED_BEGIN_RE.search(line):
            inside = True
        out.append("" if inside else line)
        if inside and _GENERATED_END_RE.search(line):
            inside = False
    return out


def _doc_route(span: "re.Match[str]", line: int) -> DocRoute:
    path = span.group(2).split("?", 1)[0]
    return DocRoute(span.group(1), path, line)


def parse_doc(lines: list[str]) -> DocIndex:
    """Index the routes, curl URLs and "starts a process" list of an operator API document."""
    lines = _strip_generated(lines)
    endpoint_start: Optional[int] = None
    for index, line in enumerate(lines):
        if _ENDPOINT_SECTION_RE.match(line):
            endpoint_start = index
            break
    if endpoint_start is None:
        start, end = 0, len(lines)
    else:
        start, end = endpoint_start, len(lines)
        for index in range(endpoint_start + 1, len(lines)):
            if _H2_RE.match(lines[index]):
                end = index
                break

    documented: list[DocRoute] = []
    for index in range(start, end):
        line = lines[index]
        if line.lstrip().startswith("|"):
            cells = line.strip().strip("|").split("|")
            first = cells[0] if cells else ""
            span = _ROUTE_SPAN_RE.search(first)
            if span:
                documented.append(_doc_route(span, index + 1))
        elif _HEADING_RE.match(line):
            for span in _ROUTE_SPAN_RE.finditer(line):
                documented.append(_doc_route(span, index + 1))

    process_list: list[DocRoute] = []
    if endpoint_start is not None:
        intro_end = end
        for index in range(endpoint_start + 1, end):
            if _H3_RE.match(lines[index]):
                intro_end = index
                break
        in_bullet = False
        bullet_indent = 0
        for index in range(endpoint_start + 1, intro_end):
            line = lines[index]
            bullet = _BULLET_RE.match(line)
            if bullet:
                in_bullet, bullet_indent = True, len(bullet.group(1))
            elif in_bullet and (not line.strip() or len(line) - len(line.lstrip()) <= bullet_indent):
                in_bullet = False
            if in_bullet:
                for span in _ROUTE_SPAN_RE.finditer(line):
                    process_list.append(_doc_route(span, index + 1))

    curl_paths: list[tuple[str, int]] = []
    pending = ""
    pending_line = 0
    for index, line in enumerate(lines):
        if not pending:
            pending_line = index + 1
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        logical = pending + line
        pending = ""
        if not _CURL_RE.search(logical):
            continue
        for url in _URL_PATH_RE.finditer(logical):
            path = (url.group(1) or "/").rstrip(").,;:")
            curl_paths.append((path or "/", pending_line))

    endpoint_line = endpoint_start + 1 if endpoint_start is not None else 1
    curl_line = endpoint_line
    for index, line in enumerate(lines):
        if _CURL_SECTION_RE.match(line):
            curl_line = index + 1
            break
    return DocIndex(documented, curl_paths, process_list, endpoint_line, curl_line)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _edit_remedy(rel: str, line: int) -> str:
    return f"${{EDITOR:-vi}} +{line} {rel}"


def process_routes(routes: list[Route], verbs: dict, ovm) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Return ``(starts a process, runs in process)`` as ``(method, path)`` sets from the census.

    A census row whose ``impl`` is ``shell`` starts a process; any other
    parsed ``impl`` runs in process. The dashboard pane route starts a
    process when the API declares it. A row whose ``impl`` does not parse is
    left out of both (``lint_operator_verb_manifest.py`` reports it).
    """
    shell: set[tuple[str, str]] = set()
    in_process: set[tuple[str, str]] = set()
    for verb in verbs.values():
        if verb.rest_method in ("ABSENT", "-") or verb.rest_route in ("ABSENT", "-"):
            continue
        try:
            impl = ovm.parse_impl(verb.impl)
        except ValueError:
            continue
        (shell if impl.kind == "shell" else in_process).add((verb.rest_method, verb.rest_route))
    declared = {(r.method, r.path) for r in routes}
    shell.update(key for key in PANE_PROCESS_ROUTES if key in declared)
    return shell, in_process


def run_checks(root: Path, doc_path: Path, manifest_path: Path, ovm) -> tuple[list[Finding], dict]:
    """Run every check; return the findings and the totals."""
    team_dir = root / "team"
    doc_rel = _route_scan._rel(doc_path, root)
    routes, findings = scan_app_routes(team_dir, root)
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        findings.append(Finding(
            "doc-unreadable", "", doc_rel, 0,
            f"{doc_rel} could not be read ({exc}); no route was checked against it",
            f"{RUN_COMMAND} --doc {DEFAULT_DOC}",
        ))
        return findings, {"routes": len(routes), "documented": 0, "curl": 0, "listed": 0}
    index = parse_doc(lines)

    for route in routes:
        if not any(_matches(route, d.method, d.path) for d in index.documented):
            findings.append(Finding(
                "route_table_row_missing", route.label, doc_rel, index.endpoint_line,
                f"{route.label} ({route.file}:{route.line}) has no row in a hand-written table of "
                f"{doc_rel}'s Endpoint table section and no heading of its own there; add a row naming "
                f"`{route.label}`, its CLI twin and its parameters",
                _edit_remedy(doc_rel, index.endpoint_line),
            ))
        if not any(_matches(route, None, path) for path, _line in index.curl_paths):
            findings.append(Finding(
                "route_curl_missing", route.label, doc_rel, index.curl_line,
                f"{route.label} ({route.file}:{route.line}) has no curl example in {doc_rel} whose URL "
                f"matches {route.path} (path parameters as wildcards); add one runnable curl line",
                _edit_remedy(doc_rel, index.curl_line),
            ))

    verbs: dict = {}
    manifest_rel = _route_scan._rel(manifest_path, root)
    try:
        verbs = ovm.load_manifest(manifest_path)
    except (FileNotFoundError, ovm.ManifestFormatError) as exc:
        findings.append(Finding(
            "manifest-unreadable", "", manifest_rel, 0,
            f"{manifest_rel} could not be read ({exc}); the starts-a-process list was not checked",
            "python3 team/scripts/lint_operator_verb_manifest.py",
        ))
    if verbs:
        shell, _in_process = process_routes(routes, verbs, ovm)
        list_line = index.process_list[0].line if index.process_list else index.endpoint_line
        listed: list[tuple[DocRoute, Optional[tuple[str, str]]]] = []
        for doc_route in index.process_list:
            match = next(
                ((r.method, r.path) for r in routes if _matches(r, doc_route.method, doc_route.path)), None
            )
            listed.append((doc_route, match))
        listed_keys = {match for _d, match in listed if match is not None}
        for method, path in sorted(shell, key=lambda k: (k[1], k[0])):
            if (method, path) not in listed_keys:
                label = f"{method} {path}"
                findings.append(Finding(
                    "process_route_unlisted", label, doc_rel, list_line,
                    f"{label} starts a process (impl shell in {manifest_rel}, or the dashboard pane "
                    f"route) but {doc_rel}'s starts-a-process list in the Endpoint table introduction "
                    "does not name it",
                    _edit_remedy(doc_rel, list_line),
                ))
        for doc_route, match in listed:
            label = f"{doc_route.method} {doc_route.path}"
            if match is None:
                why = "the API declares no such route"
            elif match not in shell:
                why = f"its census row in {manifest_rel} is not impl shell, so it runs in process"
            else:
                continue
            findings.append(Finding(
                "process_route_not_shell", label, doc_rel, doc_route.line,
                f"{doc_rel}:{doc_route.line} lists {label} as starting a process, but {why}; "
                "remove it from the list",
                _edit_remedy(doc_rel, doc_route.line),
            ))

    totals = {
        "routes": len(routes),
        "documented": len(index.documented),
        "curl": len(index.curl_paths),
        "listed": len(index.process_list),
    }
    return findings, totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_api_route_docs",
        description=(
            "Check that every REST route the API declares has a hand-written row or heading in "
            "the operator API reference's Endpoint table section and a matching curl example, "
            "and that the reference's starts-a-process list matches the operator verb census. "
            "Quality class: always exits 0."
        ),
        epilog="Exit codes:\n  0  Always (findings are narrated, never fatal).\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--doc", metavar="PATH", default=DEFAULT_DOC,
                        help=f"Operator API reference, relative to --root or absolute (default: {DEFAULT_DOC}).")
    parser.add_argument("--manifest", metavar="PATH", default=DEFAULT_MANIFEST,
                        help=f"Operator verb census, relative to --root or absolute (default: {DEFAULT_MANIFEST}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every route the API declares, documented or not.")
    return parser


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _write_findings(path_str: Optional[str], findings: list[Finding]) -> None:
    if not path_str:
        return
    path = Path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([f.to_dict() for f in findings], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"lint_{LINT_NAME}: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the lint, printing each finding and writing ``--findings-file``. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    doc_path = _resolve(root, args.doc)
    manifest_path = _resolve(root, args.manifest)

    team_str = str(root / "team")
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    findings, totals = run_checks(root, doc_path, manifest_path, ovm)
    _write_findings(args.findings_file, findings)

    if args.verbose:
        routes, _ = scan_app_routes(root / "team", root)
        for route in routes:
            print(f"  [api-route-docs] {route.label} ({route.file}:{route.line})")
    for f in findings:
        print(f"lint_{LINT_NAME}: [{f.kind}] {f.summary}")
    print(
        f"lint_{LINT_NAME}: totals: {totals['routes']} route(s), {totals['documented']} documented "
        f"row(s), {totals['curl']} curl URL(s), {totals['listed']} listed process route(s); "
        f"{len(findings)} finding(s)" + (" (non-blocking)" if findings else "")
    )

    if findings:
        message = (
            f"{len(findings)} REST route documentation finding(s) against {_route_scan._rel(doc_path, root)}; "
            "see the lines printed above"
        )
        remedy = (
            f"add the missing table rows and curl examples to {DEFAULT_DOC} and correct its "
            f"starts-a-process list, then re-run: {RUN_COMMAND}"
        )
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; the lint returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.api-route-docs", message, remedy)
        except ImportError:
            print(f"error:   lint.api-route-docs\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
