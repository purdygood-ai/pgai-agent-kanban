#!/usr/bin/env python3
"""
lint_api_route_impl.py
======================
Checks that an operator verb's REST route and its CLI script perform the
same operation through the same Python function, and ratchets the number of
verbs that do not yet.

The ``impl`` column of ``team/config/operator_verbs.manifest`` declares, per
verb, either ``module:<dotted.module.function>`` or ``shell``. This lint
verifies every ``module:`` row against the tree and holds the count of
``shell`` rows to a recorded baseline that may fall but never rise.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). A route that shells out where
it could call a function carries no runtime-failure risk by itself, so this
lint narrates its findings, writes them to ``--findings-file`` for the gated
runner's aggregate, and ALWAYS exits 0. It never fails a runner or a
release.

WHAT IS CHECKED, PER ``module:`` ROW
------------------------------------
For a row declaring ``module:pkg.mod.fn``:

  route-missing        No ``@router.get``/``@router.post`` handler for the
                       row's method and route exists in
                       ``team/pgai_agent_kanban/api/reads.py`` or
                       ``team/pgai_agent_kanban/api/routers/*.py``.
  route-not-calling    The handler does not call ``fn``. A call counts when
                       the callee resolves, through the file's own imports,
                       to ``pkg.mod.fn`` -- or to ``fn`` re-exported by a
                       package above ``pkg.mod`` (``from ..ops import halt``
                       counts for ``pgai_agent_kanban.ops.write.halt``).
                       Relative imports resolve against the file's own
                       package.
  route-shells-out     The handler calls ``shell_out``, anything in the
                       ``subprocess`` module, ``asyncio.create_subprocess_*``,
                       ``os.system`` or ``os.popen``.
  cli-script-missing   The row's script does not exist under ``team/``.
  cli-not-reaching     The script does not reach ``fn`` through
                       ``team/scripts/lib/pp_run_ops.sh``. Either form
                       counts: a ``pp_run_ops <module> <name>`` call (or the
                       exec form ``pp_run_ops.sh <module> <name>``) whose
                       module is ``pkg.mod`` or a package above it and whose
                       first argument is ``fn``; or a script that uses
                       ``pp_pythonpath`` and imports ``fn`` from ``pkg.mod``
                       or a package above it. Comment lines are ignored.

Both route checks follow calls into functions defined at module level in the
same router file (a handler that delegates to a local helper is judged by
what the helper does), but not into other modules.

DOCUMENTED LAUNCHERS
--------------------
A launcher is a verb whose whole job is to start a process or a lane (for
example ``wake-now``), so its route starts that process by design and there
is no in-process function to share. The launchers file (default
``team/config/api-route-impl-launchers.txt``) lists them, one
``<verb><TAB><reason>`` line each; ``#`` lines are comments and a missing
file declares no launchers. A launcher keeps ``impl shell`` in the
manifest, is printed with its reason on every run, and is reported apart
from the shell rows still to be ported -- the count the convergence target
of zero measures.

  * a line with no reason; the verb stays a
    shell row to port                              -> ``launcher-without-reason``
  * a verb the manifest does not declare            -> ``launcher-unknown-verb``
  * a verb whose impl is not ``shell``              -> ``launcher-not-shell``
  * the same verb on more than one line             -> ``launcher-duplicate``

THE ``shell`` RATCHET
---------------------
The baseline file (default ``team/config/api-route-impl-baseline.txt``)
records the number of ``impl shell`` rows -- launchers included -- as one
``shell_rows<TAB><count>`` line; ``#`` lines are comments. Counting
launchers keeps the ratchet independent of the launchers file: declaring a
launcher moves a verb out of the to-port count but never lowers the number
the baseline holds.

  * more ``impl shell`` rows than the baseline      -> ``shell-rows-above-baseline``
  * fewer ``impl shell`` rows than the baseline:
    the baseline is lowered in the same change      -> ``baseline-tighten``
  * the baseline file is missing or has no
    readable ``shell_rows`` line                   -> ``baseline-missing`` /
                                                      ``baseline-malformed``

A row whose ``impl`` value does not parse is reported as ``impl-malformed``
and is neither checked nor counted; ``lint_operator_verb_manifest.py`` owns
the column's schema and reports it too.

OUTPUT
------
One line per documented launcher, one narration line per finding, then a
totals line:

    lint_api_route_impl: launcher: <verb> -- <reason>
    lint_api_route_impl: totals: <N> verb(s), <M> module row(s), <S> shell
    row(s), <L> launcher(s) (<S+L> impl shell, baseline <B>); <F> finding(s)

``<S>`` excludes launchers: it is the number of rows still to be ported.

``--findings-file PATH`` writes the findings as a JSON array (keys ``lint``,
``severity``, ``kind``, ``verb``, ``file``, ``line``, ``summary``,
``remedy``) in the shape ``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges. The file is always written, as
``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_api_route_impl.py [OPTIONS]

  --root PATH            Repository root (default: two levels above this
                         script's directory).
  --manifest PATH        Manifest file, relative to --root or absolute
                         (default: team/config/operator_verbs.manifest).
  --baseline PATH        Baseline file, relative to --root or absolute
                         (default: team/config/api-route-impl-baseline.txt).
  --launchers PATH       Launchers file, relative to --root or absolute
                         (default: team/config/api-route-impl-launchers.txt).
  --findings-file PATH  Write findings as a JSON array to PATH.
  --emit-baseline        Print the measured baseline in file format to
                         stdout and exit 0 (no checks).
  --verbose, -v          Print every row's verdict, clean or not.

Exit codes:
  0  Always (findings are narrated, never fatal).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional

LINT_NAME = "api_route_impl"
RUN_COMMAND = "python3 team/scripts/lint_api_route_impl.py"
DEFAULT_MANIFEST = "team/config/operator_verbs.manifest"
DEFAULT_BASELINE = "team/config/api-route-impl-baseline.txt"
DEFAULT_LAUNCHERS = "team/config/api-route-impl-launchers.txt"
BASELINE_KEY = "shell_rows"
EMIT_BASELINE_COMMAND = f"{RUN_COMMAND} --emit-baseline > {DEFAULT_BASELINE}"

# Router files scanned for route handlers, relative to team/.
_API_DIR = Path("pgai_agent_kanban") / "api"

# Dotted call targets that start a process. A callee resolving to any of
# these, or to anything under ``subprocess.``, is a shell-out.
_PROCESS_CALLS = frozenset(
    {
        "os.system",
        "os.popen",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
    }
)
_SHELL_OUT_NAME = "shell_out"

_BASELINE_LINE_RE = re.compile(rf"^\s*{BASELINE_KEY}\s*[\t =:]\s*(\d+)\s*$")
# Arguments are separated by blanks or a backslash line continuation, never a
# bare newline -- so a ``source .../pp_run_ops.sh`` line does not swallow the
# call on the line after it.
_ARG_SEP = r"""["']?(?:[ \t]|\\\n)+["']?"""
_PP_RUN_OPS_CALL_RE = re.compile(
    rf"""\bpp_run_ops(?:\.sh)?{_ARG_SEP}([A-Za-z_][\w.]*){_ARG_SEP}([A-Za-z_]\w*)"""
)
_PY_IMPORT_RE = re.compile(r"\bfrom\s+([A-Za-z_][\w.]*)\s+import\s+(\([^)]*\)|[^\n]+)")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    """One finding (what the runner records)."""

    kind: str
    verb: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``verb``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "verb": self.verb,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": self.remedy,
        }


# ---------------------------------------------------------------------------
# Router scanning
# ---------------------------------------------------------------------------


class RouteHandler(NamedTuple):
    """One route handler found in a router file."""

    method: str  # "GET" | "POST"
    route: str  # full path, router prefix included
    file: Path
    rel: str  # repository-relative path of ``file``
    func: ast.FunctionDef | ast.AsyncFunctionDef
    module: "RouterModule"


class RouterModule(NamedTuple):
    """The parts of a router file the call checks need."""

    bindings: dict[str, str]  # local name -> dotted path it was imported as
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]  # module-level defs


def _module_name(path: Path, team_dir: Path) -> str:
    """Dotted module name of *path* relative to team/ (the package root)."""
    rel = path.resolve().relative_to(team_dir.resolve()).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(module_name: str, level: int, target: Optional[str]) -> str:
    """Resolve a ``from <dots><target> import`` against *module_name*'s package."""
    if level == 0:
        return target or ""
    package_parts = module_name.split(".")[:-1]  # the file's own package
    if level > 1:
        package_parts = package_parts[: len(package_parts) - (level - 1)]
    base = ".".join(package_parts)
    if target:
        return f"{base}.{target}" if base else target
    return base


def _bindings(tree: ast.Module, module_name: str) -> dict[str, str]:
    """Map every imported local name in *tree* to the dotted path it names."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    out[head] = head
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(module_name, node.level, node.module)
            for alias in node.names:
                if alias.name == "*":
                    continue
                out[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
    return out


def _router_prefix(tree: ast.Module) -> str:
    """The ``prefix=`` of the file's ``APIRouter(...)`` call, or ""."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "APIRouter":
                continue
            for kw in node.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
    return ""


def _route_decorations(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for dec in func.decorator_list:
        if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
            continue
        if dec.func.attr not in ("get", "post"):
            continue
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            out.append((dec.func.attr.upper(), dec.args[0].value))
    return out


def router_files(team_dir: Path) -> list[Path]:
    """The router files the API ships: reads.py and routers/*.py."""
    api_dir = team_dir / _API_DIR
    files: list[Path] = []
    if (api_dir / "reads.py").is_file():
        files.append(api_dir / "reads.py")
    if (api_dir / "routers").is_dir():
        files.extend(sorted((api_dir / "routers").glob("*.py")))
    return files


def scan_routes(team_dir: Path, root: Path) -> tuple[dict[tuple[str, str], RouteHandler], list[Finding]]:
    """Index every route handler by (method, full route)."""
    handlers: dict[tuple[str, str], RouteHandler] = {}
    findings: list[Finding] = []
    for path in router_files(team_dir):
        rel = _rel(path, root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            findings.append(
                Finding("parse-error", "", rel, 0, f"{rel} could not be parsed ({exc}); its routes were not checked",
                        f"python3 -m py_compile {rel}")
            )
            continue
        module = RouterModule(
            bindings=_bindings(tree, _module_name(path, team_dir)),
            functions={
                n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            },
        )
        prefix = _router_prefix(tree)
        for func in module.functions.values():
            for method, route in _route_decorations(func):
                handlers[(method, prefix + route)] = RouteHandler(method, prefix + route, path, rel, func, module)
    return handlers, findings


# ---------------------------------------------------------------------------
# Call analysis
# ---------------------------------------------------------------------------


class ResolvedCall(NamedTuple):
    """One call in a route or CLI body: its dotted callee (imports resolved), last segment and line."""
    dotted: str  # best-effort dotted path of the callee, imports resolved
    name: str  # the last segment of ``dotted`` (an import alias is resolved away)
    line: int


def _dotted_expr(expr: ast.expr) -> Optional[str]:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = _dotted_expr(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def _resolve_call(node: ast.Call, bindings: dict[str, str]) -> Optional[ResolvedCall]:
    raw = _dotted_expr(node.func)
    if raw is None:
        return None
    head, _, rest = raw.partition(".")
    bound = bindings.get(head, head)
    dotted = f"{bound}.{rest}" if rest else bound
    return ResolvedCall(dotted, dotted.rsplit(".", 1)[-1], node.lineno)


def handler_calls(handler: RouteHandler) -> list[ResolvedCall]:
    """Every call the handler makes, following same-file module-level helpers."""
    seen: set[str] = set()
    calls: list[ResolvedCall] = []
    pending = [handler.func]
    while pending:
        func = pending.pop()
        if func.name in seen:
            continue
        seen.add(func.name)
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            resolved = _resolve_call(node, handler.module.bindings)
            if resolved is None:
                continue
            calls.append(resolved)
            local = handler.module.functions.get(resolved.dotted)
            if local is not None and local.name not in seen:
                pending.append(local)
    return calls


def _is_shell_out(call: ResolvedCall) -> bool:
    return (
        call.name == _SHELL_OUT_NAME
        or call.dotted.startswith("subprocess.")
        or call.dotted in _PROCESS_CALLS
    )


def _reaches_target(dotted: str, name: str, module: str, function: str) -> bool:
    """True when *dotted* names *module*.*function* or a package re-export of it."""
    if name != function:
        return False
    if dotted == f"{module}.{function}":
        return True
    owner = dotted.rsplit(".", 1)[0] if "." in dotted else ""
    return bool(owner) and (module == owner or module.startswith(owner + "."))


# ---------------------------------------------------------------------------
# CLI script analysis
# ---------------------------------------------------------------------------


def _strip_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _module_covers(candidate: str, module: str) -> bool:
    return module == candidate or module.startswith(candidate + ".")


def script_reaches(script_text: str, module: str, function: str) -> bool:
    """True when the script reaches *module*.*function* through pp_run_ops.sh."""
    code = _strip_comment_lines(script_text)
    for match in _PP_RUN_OPS_CALL_RE.finditer(code):
        if match.group(2) == function and _module_covers(match.group(1), module):
            return True
    if "pp_pythonpath" in code:
        for match in _PY_IMPORT_RE.finditer(code):
            if not _module_covers(match.group(1), module):
                continue
            names = re.split(r"[\s,()]+", match.group(2))
            if function in names:
                return True
    return False


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def load_baseline(path: Path, rel: str) -> tuple[Optional[int], list[Finding]]:
    """Read the recorded shell-row count; a finding when it cannot be read."""
    if not path.is_file():
        return None, [
            Finding("baseline-missing", "", rel, 0,
                    f"baseline file {rel} does not exist; the shell-row ratchet was not checked",
                    EMIT_BASELINE_COMMAND)
        ]
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _BASELINE_LINE_RE.match(line)
        if match:
            return int(match.group(1)), []
    return None, [
        Finding("baseline-malformed", "", rel, 0,
                f"baseline file {rel} has no '{BASELINE_KEY}<TAB>count' line; the shell-row ratchet was not checked",
                EMIT_BASELINE_COMMAND)
    ]


def format_baseline(shell_rows: int) -> str:
    """Return the full text of ``team/config/api-route-impl-baseline.txt`` for *shell_rows*.

    A comment header explaining the ratchet and how to regenerate the file,
    then the baseline key and the count, separated by a tab.
    """
    return (
        "# team/config/api-route-impl-baseline.txt\n"
        "#\n"
        "# Baseline for team/scripts/lint_api_route_impl.py: the number of\n"
        "# operator verb manifest rows whose impl column is \"shell\". The count\n"
        "# may only fall. When a change flips rows to \"module:\", lower this\n"
        "# number in the same change (the lint reports a baseline above the\n"
        "# measured count); never raise it. Documented launchers\n"
        f"# ({DEFAULT_LAUNCHERS}) are counted here too.\n"
        "# Regenerate with:\n"
        f"#   {EMIT_BASELINE_COMMAND}\n"
        "#\n"
        f"{BASELINE_KEY}\t{shell_rows}\n"
    )


# ---------------------------------------------------------------------------
# Documented launchers
# ---------------------------------------------------------------------------

# "<verb><blanks><reason>"; the reason is optional here so a line without one
# is read and reported rather than skipped. The verb token is any run of
# non-blank characters, so a misspelt name reaches launcher-unknown-verb.
_LAUNCHER_LINE_RE = re.compile(r"^\s*(\S+)(?:\s+(.*?))?\s*$")


class Launcher(NamedTuple):
    """One documented launcher: a verb whose route starts a process by design."""

    verb: str
    reason: str
    line: int


def load_launchers(path: Path, rel: str) -> tuple[dict[str, Launcher], list[Finding]]:
    """Read the documented launchers; a finding for each unusable line.

    A missing file declares no launchers and is not a finding: every
    ``impl shell`` row then counts as a row to port, which is the stricter
    reading. A line with no reason is reported and left out of the result,
    so its verb stays a row to port. A verb listed twice is reported and its
    first line kept.
    """
    launchers: dict[str, Launcher] = {}
    findings: list[Finding] = []
    if not path.is_file():
        return launchers, findings
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        match = _LAUNCHER_LINE_RE.match(raw)
        if match is None:
            continue
        verb, reason = match.group(1), (match.group(2) or "").strip()
        if not reason:
            findings.append(
                Finding("launcher-without-reason", verb, rel, number,
                        f"launcher '{verb}' at {rel}:{number} has no reason; every launcher carries a written "
                        f"reason, so '{verb}' is counted as a shell row to port",
                        f"{RUN_COMMAND} --verbose")
            )
            continue
        if verb in launchers:
            findings.append(
                Finding("launcher-duplicate", verb, rel, number,
                        f"launcher '{verb}' is listed again at {rel}:{number} (first at line "
                        f"{launchers[verb].line}); the first line is used",
                        f"{RUN_COMMAND} --verbose")
            )
            continue
        launchers[verb] = Launcher(verb, reason, number)
    return launchers, findings


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def check_module_row(
    verb, impl, handlers: dict[tuple[str, str], RouteHandler], team_dir: Path, root: Path, manifest_rel: str
) -> list[Finding]:
    """Every finding for one ``module:`` row."""
    findings: list[Finding] = []
    module, function = impl.module, impl.function
    route_key = (verb.rest_method, verb.rest_route)
    handler = handlers.get(route_key)
    if handler is None:
        findings.append(
            Finding("route-missing", verb.name, manifest_rel, 0,
                    f"verb '{verb.name}' declares impl module:{impl.target} but no router declares "
                    f"{verb.rest_method} {verb.rest_route}",
                    "python3 team/scripts/lint_operator_verb_manifest.py")
        )
    else:
        calls = handler_calls(handler)
        if not any(_reaches_target(c.dotted, c.name, module, function) for c in calls):
            findings.append(
                Finding("route-not-calling", verb.name, handler.rel, handler.func.lineno,
                        f"verb '{verb.name}': {verb.rest_method} {verb.rest_route} ({handler.func.name}) "
                        f"does not call {impl.target}",
                        f"{RUN_COMMAND} --verbose")
            )
        for call in calls:
            if _is_shell_out(call):
                findings.append(
                    Finding("route-shells-out", verb.name, handler.rel, call.line,
                            f"verb '{verb.name}': {verb.rest_method} {verb.rest_route} ({handler.func.name}) "
                            f"calls {call.dotted} at {handler.rel}:{call.line}; a module: route starts no process",
                            f"{RUN_COMMAND} --verbose")
                )

    script_path = team_dir / (verb.script or "")
    script_rel = _rel(script_path, root)
    if not verb.script or not script_path.is_file():
        findings.append(
            Finding("cli-script-missing", verb.name, script_rel, 0,
                    f"verb '{verb.name}': script {script_rel} does not exist",
                    "python3 team/scripts/lint_operator_verb_manifest.py")
        )
    elif not script_reaches(script_path.read_text(encoding="utf-8", errors="replace"), module, function):
        findings.append(
            Finding("cli-not-reaching", verb.name, script_rel, 0,
                    f"verb '{verb.name}': {script_rel} does not reach {impl.target} through pp_run_ops "
                    f"(expected 'pp_run_ops {module} {function}' or a pp_pythonpath import of {function})",
                    f"{RUN_COMMAND} --verbose")
        )
    return findings


def run_checks(
    root: Path, manifest_path: Path, baseline_path: Path, ovm, launchers_path: Optional[Path] = None
) -> tuple[list[Finding], dict]:
    """Run every check; return the findings and the measured totals.

    ``shell_rows`` in the totals counts the ``impl shell`` rows still to be
    ported (launchers excluded); ``impl_shell_rows`` counts every ``impl
    shell`` row and is what the baseline ratchets.
    """
    team_dir = root / "team"
    manifest_rel = _rel(manifest_path, root)
    baseline_rel = _rel(baseline_path, root)
    verbs = ovm.load_manifest(manifest_path)
    handlers, findings = scan_routes(team_dir, root)

    launchers: dict[str, Launcher] = {}
    launchers_rel = ""
    if launchers_path is not None:
        launchers_rel = _rel(launchers_path, root)
        launchers, launcher_findings = load_launchers(launchers_path, launchers_rel)
        findings.extend(launcher_findings)

    module_rows = 0
    shell_rows = 0
    launcher_rows: list[Launcher] = []
    verdicts: list[str] = []
    impl_kinds: dict[str, str] = {}
    for verb in verbs.values():
        try:
            impl = ovm.parse_impl(verb.impl)
        except ValueError as exc:
            findings.append(
                Finding("impl-malformed", verb.name, manifest_rel, 0, f"verb '{verb.name}': {exc}",
                        "python3 team/scripts/lint_operator_verb_manifest.py")
            )
            continue
        impl_kinds[verb.name] = impl.kind
        if impl.kind == "shell" and verb.name in launchers:
            launcher_rows.append(launchers[verb.name])
            verdicts.append(f"{verb.name}: shell, launcher -- {launchers[verb.name].reason}")
        elif impl.kind == "shell":
            shell_rows += 1
            verdicts.append(f"{verb.name}: shell")
        elif impl.kind == "module":
            module_rows += 1
            row_findings = check_module_row(verb, impl, handlers, team_dir, root, manifest_rel)
            findings.extend(row_findings)
            verdicts.append(f"{verb.name}: module:{impl.target} -- {'ok' if not row_findings else 'FINDINGS'}")

    for launcher in launchers.values():
        if launcher.verb not in verbs:
            findings.append(
                Finding("launcher-unknown-verb", launcher.verb, launchers_rel, launcher.line,
                        f"launcher '{launcher.verb}' at {launchers_rel}:{launcher.line} names no verb in "
                        f"{manifest_rel}",
                        "python3 team/scripts/lint_operator_verb_manifest.py")
            )
        elif launcher.verb in impl_kinds and impl_kinds[launcher.verb] != "shell":
            findings.append(
                Finding("launcher-not-shell", launcher.verb, launchers_rel, launcher.line,
                        f"launcher '{launcher.verb}' at {launchers_rel}:{launcher.line} has impl "
                        f"{impl_kinds[launcher.verb]} in {manifest_rel}; only an impl shell row is a launcher",
                        f"{RUN_COMMAND} --verbose")
            )

    impl_shell_rows = shell_rows + len(launcher_rows)
    split = f"{shell_rows} to port, {len(launcher_rows)} launcher(s)"
    baseline, baseline_findings = load_baseline(baseline_path, baseline_rel)
    findings.extend(baseline_findings)
    if baseline is not None:
        if impl_shell_rows > baseline:
            findings.append(
                Finding("shell-rows-above-baseline", "", manifest_rel, 0,
                        f"{impl_shell_rows} manifest row(s) are impl shell ({split}), above the baseline of "
                        f"{baseline} in {baseline_rel}; the count may only fall",
                        f"{RUN_COMMAND} --verbose")
            )
        elif impl_shell_rows < baseline:
            findings.append(
                Finding("baseline-tighten", "", baseline_rel, 0,
                        f"{impl_shell_rows} manifest row(s) are impl shell ({split}), below the baseline of "
                        f"{baseline} in {baseline_rel}; lower the baseline to {impl_shell_rows} in the same change",
                        EMIT_BASELINE_COMMAND)
            )
    totals = {
        "verbs": len(verbs),
        "module_rows": module_rows,
        "shell_rows": shell_rows,
        "launchers": launcher_rows,
        "impl_shell_rows": impl_shell_rows,
        "baseline": baseline,
        "verdicts": verdicts,
    }
    return findings, totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_api_route_impl",
        description=(
            "Check that each operator verb whose manifest impl is module:<function> has a REST "
            "route calling that function without starting a process and a CLI script reaching "
            "it through pp_run_ops, and that the number of impl shell rows does not rise above "
            "the recorded baseline. Documented launchers are printed with their reasons and "
            "reported apart from the shell rows still to port. Quality class: always exits 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Baseline format: one '{BASELINE_KEY}\\t<count>' line; '#' lines are comments.\n"
            "The count is only ever lowered, never raised. It includes launchers.\n"
            "Launchers format: one '<verb>\\t<reason>' line per launcher; '#' lines are\n"
            "comments. A launcher with no reason is reported and counted as a row to port.\n\n"
            "Exit codes:\n  0  Always (findings are narrated, never fatal).\n"
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--manifest", metavar="PATH", default=DEFAULT_MANIFEST,
                        help=f"Manifest file, relative to --root or absolute (default: {DEFAULT_MANIFEST}).")
    parser.add_argument("--baseline", metavar="PATH", default=DEFAULT_BASELINE,
                        help=f"Baseline file, relative to --root or absolute (default: {DEFAULT_BASELINE}).")
    parser.add_argument("--launchers", metavar="PATH", default=DEFAULT_LAUNCHERS,
                        help=f"Launchers file, relative to --root or absolute (default: {DEFAULT_LAUNCHERS}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="Print the measured baseline in file format and exit 0.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every row's verdict, clean or not.")
    return parser


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


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
        print(f"lint_api_route_impl: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    manifest_path = _resolve(root, args.manifest)
    baseline_path = _resolve(root, args.baseline)
    launchers_path = _resolve(root, args.launchers)

    team_str = str(root / "team")
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    from pgai_agent_kanban.lib import operator_verb_manifest as ovm

    try:
        findings, totals = run_checks(root, manifest_path, baseline_path, ovm, launchers_path)
    except (FileNotFoundError, ovm.ManifestFormatError) as exc:
        rel = _rel(manifest_path, root)
        findings = [
            Finding("manifest-unreadable", "", rel, 0, f"manifest could not be read: {exc}",
                    "python3 team/scripts/lint_operator_verb_manifest.py")
        ]
        totals = None

    if args.emit_baseline:
        if totals is None:
            print(f"lint_api_route_impl: {findings[0].summary}", file=sys.stderr)
            return 0
        sys.stdout.write(format_baseline(totals["impl_shell_rows"]))
        return 0

    _write_findings(args.findings_file, findings)

    if args.verbose and totals is not None:
        for verdict in totals["verdicts"]:
            print(f"  [api-route-impl] {verdict}")
    if totals is not None:
        for launcher in totals["launchers"]:
            print(f"lint_api_route_impl: launcher: {launcher.verb} -- {launcher.reason}")
    for f in findings:
        print(f"lint_api_route_impl: [{f.kind}] {f.summary}")
    if totals is not None:
        baseline = totals["baseline"] if totals["baseline"] is not None else "unread"
        print(
            f"lint_api_route_impl: totals: {totals['verbs']} verb(s), {totals['module_rows']} module row(s), "
            f"{totals['shell_rows']} shell row(s), {len(totals['launchers'])} launcher(s) "
            f"({totals['impl_shell_rows']} impl shell, baseline {baseline}); {len(findings)} finding(s)"
        )

    if findings:
        message = (
            f"{len(findings)} route/CLI implementation finding(s) against the operator verb manifest; "
            "see the lines printed above"
        )
        remedy = (
            "route each module row's REST handler and CLI script through the named function, keep "
            f"the shell-row baseline at the measured count, then re-run: {RUN_COMMAND}"
        )
        # CENSUS-ANCHOR: api-route-impl-01
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; the lint returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.api-route-impl", message, remedy)
        except ImportError:
            print(f"error:   lint.api-route-impl\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
