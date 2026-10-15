#!/usr/bin/env python3
"""
lint_python_duplicates.py
=========================
A ratchet lint that keeps new duplicated Python out of the kanban's Python
trees: copy-pasted function bodies, and private copies of helpers that have
exactly one owning module.

The coding standard is that a helper lives in exactly one module and every
caller imports it. The tree already carries copies, so this lint does not
demand zero: it compares every finding with an allowlist recorded from the
tree as it stood, and reports anything new.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). A duplicated helper carries no
runtime-failure risk by itself, so this lint narrates its findings, writes
them to ``--findings-file`` for the gated runner's aggregate, and ALWAYS
exits 0. It never fails a runner or a release.

WHAT IS SCANNED
---------------
Every ``*.py`` file under the scan roots (default ``team/pgai_agent_kanban``,
``team/scripts`` and ``team/pm-agent``, recursively). A file is skipped when
any directory between its scan root and the file is named ``tests``,
``test`` or ``__pycache__``. A file that does not parse is reported as
``parse-error`` and otherwise skipped.

FINDING FAMILIES
----------------
Every finding belongs to one family. The deny-list families name a helper
that may be defined only in its owning module (paths relative to the
repository root):

  family             what is reported                     owning module
  -----------------  -----------------------------------  ---------------------------------------
  atomic-write       def of ``_atomic_write``             team/pgai_agent_kanban/lib/atomic_write.py
  traversal          def of ``_has_traversal``            team/pgai_agent_kanban/api/validation.py
  version-parse      def of ``_parse_version``            team/pgai_agent_kanban/lib/semver.py
  iso-parse          def of ``_parse_iso``                team/pgai_agent_kanban/lib/timeutil.py
  read-section       def of ``read_section``              team/pgai_agent_kanban/task_status.py
  ini-instantiation  a ``configparser.*Parser(...)`` call team/pgai_agent_kanban/lib/ini.py
  state-regex        a regex matching a ``## State``      team/pgai_agent_kanban/task_status.py
                     heading

A deny-listed name matches with or without leading underscores, so
``atomic_write``, ``_atomic_write`` and ``__atomic_write`` are one helper;
``_atomic_write_json`` is a different name and does not match. Functions,
methods and nested functions all count as definitions.

``ini-instantiation`` counts calls of a ``*Parser`` class reached through the
``configparser`` module under any import alias (``configparser.ConfigParser()``,
``_cp.RawConfigParser()``) or imported from it by name
(``from configparser import ConfigParser``).

``state-regex`` counts string literals naming a ``## State`` heading
(``##``, any run of spaces or regex whitespace tokens, then ``State``) that
are used as a regex: the pattern argument of an ``re`` module call
(``re.compile``, ``re.search``, ``re.sub`` ...), or any literal that carries
regex syntax: an escape such as ``\\s`` or ``\\n``, a leading ``^``, a
trailing ``$``, a ``(?`` group, or a character class holding a negation or an
escape (``[^\\n]``, ``[ \\t]``).
Docstrings, and plain text such as ``"## State\\n"`` written to a file, are
not counted.

The last family is not a deny-list:

  duplicate-body     a function whose AST-normalised body is identical to
                     at least one other function's anywhere in the scan

Normalisation: the body's docstring is dropped, the function's parameters
are renamed by position (so parameter names and type annotations do not
matter), and the body is dumped without line or column attributes (so
whitespace, comments and formatting do not matter). The function's own name
is not part of the hash. Bodies smaller than ``--min-nodes`` AST nodes
(default 25) are ignored: one-line accessors and ``pass`` stubs are not the
duplication this lint is after. A function already reported under a
deny-list family, or a deny-listed name inside its owning module (the
canonical copy), is not also reported as ``duplicate-body``; the other
members of its group still are.

THE ALLOWLIST AND THE RATCHET RULE
----------------------------------
``team/config/python-duplicates-allowlist.txt`` holds one line per finding
key:

    <family>\\t<path>\\t<symbol>\\t<count>

``<path>`` is relative to the repository root. ``<symbol>`` is the
dotted qualified name of the defining function (``Class.method``,
``outer.inner``) for the definition families and ``duplicate-body``, and the
qualified name of the ENCLOSING function (``<module>`` at top level) for
``ini-instantiation`` and ``state-regex``. ``<count>`` is how many findings
share that key -- usually 1. Fields may be separated by any run of
whitespace; blank lines and lines starting with ``#`` are ignored.

  * A key not in the allowlist.                      -> finding ``unlisted``
  * A key above its recorded count.                  -> finding ``above``
  * A key below its recorded count, or an entry
    that no longer matches anything, must be
    tightened (lowered or removed) in the same
    change.                                          -> finding ``below``
  * An unparseable allowlist line.                   -> finding ``allowlist-malformed``

The ratchet only turns one way: an entry is lowered or removed when a copy
is replaced by an import, and never raised. ``--emit-allowlist`` prints the
measured keys in allowlist format, for writing a tightened entry; it is not
a licence to add one.

OUTPUT
------
One narration line per ratchet finding, then a per-family census line and a
totals line:

    lint_python_duplicates: families: atomic-write=<n> traversal=<n> ...
    lint_python_duplicates: totals: <N> file(s) scanned, <M> raw finding(s)
    (<G> duplicate-body group(s)); <F> ratchet finding(s)

``--findings-file PATH`` writes the ratchet findings as a JSON array (one
object per finding, keys ``lint``, ``severity``, ``kind``, ``family``,
``file``, ``line``, ``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s ``gated_runner_merge_findings_file``
merges. The file is always written, as ``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_python_duplicates.py [OPTIONS]

  --root PATH            Repository root (default: two levels above this
                         script's directory).
  --scan-root PATH       Directory to scan, relative to --root or absolute.
                         Repeatable; replaces the default roots when given.
  --allowlist PATH       Allowlist file, relative to --root or absolute
                         (default: team/config/python-duplicates-allowlist.txt).
  --min-nodes N          Smallest body, in AST nodes, considered for
                         duplicate-body (default: 25).
  --findings-file PATH   Write ratchet findings as a JSON array to PATH.
  --emit-allowlist       Print the measured keys in allowlist format to
                         stdout and exit 0 (no ratchet check).
  --verbose, -v          Print every raw finding, allowlisted or not.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NamedTuple

# team/ import path -- needed for the dual-channel error-contract emitter.
_TEAM_DIR = Path(__file__).resolve().parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

LINT_NAME = "python_duplicates"
DEFAULT_SCAN_ROOTS = ("team/pgai_agent_kanban", "team/scripts", "team/pm-agent")
DEFAULT_ALLOWLIST = "team/config/python-duplicates-allowlist.txt"
DEFAULT_MIN_NODES = 25
SKIP_DIR_NAMES = frozenset({"tests", "test", "__pycache__"})
RUN_COMMAND = "python3 team/scripts/lint_python_duplicates.py"
MODULE_SYMBOL = "<module>"


class DenyName(NamedTuple):
    """A helper name that may be defined only in its owning module."""

    family: str
    name: str
    owner: str  # repository-relative path of the owning module


# Deny-listed helper definitions. Matched on the name with leading
# underscores stripped.
DENY_NAMES: tuple[DenyName, ...] = (
    DenyName("atomic-write", "_atomic_write", "team/pgai_agent_kanban/lib/atomic_write.py"),
    DenyName("traversal", "_has_traversal", "team/pgai_agent_kanban/api/validation.py"),
    DenyName("version-parse", "_parse_version", "team/pgai_agent_kanban/lib/semver.py"),
    DenyName("iso-parse", "_parse_iso", "team/pgai_agent_kanban/lib/timeutil.py"),
    DenyName("read-section", "read_section", "team/pgai_agent_kanban/task_status.py"),
)
INI_FAMILY = "ini-instantiation"
INI_OWNER = "team/pgai_agent_kanban/lib/ini.py"
STATE_FAMILY = "state-regex"
STATE_OWNER = "team/pgai_agent_kanban/task_status.py"
DUP_FAMILY = "duplicate-body"

# Order of the per-family census line.
FAMILIES: tuple[str, ...] = tuple(d.family for d in DENY_NAMES) + (INI_FAMILY, STATE_FAMILY, DUP_FAMILY)

OWNER_OF_FAMILY: dict[str, str] = {d.family: d.owner for d in DENY_NAMES}
OWNER_OF_FAMILY[INI_FAMILY] = INI_OWNER
OWNER_OF_FAMILY[STATE_FAMILY] = STATE_OWNER

_DENY_BY_BASE: dict[str, DenyName] = {d.name.lstrip("_"): d for d in DENY_NAMES}

# `re` module functions whose first argument (or pattern=) is a regex.
_RE_FUNCS = frozenset(
    {"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer", "split"}
)

# A `## State` heading inside a string: `##`, then any run of literal spaces,
# tabs, or regex whitespace tokens (\s, [ \t], quantifiers), then `State` as a
# whole word.
_STATE_HEADING_RE = re.compile(r"##(?:[ \t]|\\s|\\t|\[[ \\t]*\]|[+*?]|\{\d*,?\d*\})*State\b")

# Regex syntax that marks a literal as a pattern rather than plain text.
# Plain markdown (a template carrying "- [ ] ..." checkboxes) never counts: a
# character class needs a negation or an escape inside it.
_REGEX_SYNTAX_RE = re.compile(r"\\[sSnwWdDbBAZ]|^\^|\$$|\(\?|\[\^|\[[^\]\n]*\\[^\]\n]*\]")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class RawFinding(NamedTuple):
    """One measured occurrence, before the allowlist is applied."""

    family: str
    file: str  # repository-relative path
    symbol: str  # qualified name (see module docstring)
    line: int
    detail: str = ""  # human-readable extra (duplicate peers, call text)


class Finding(NamedTuple):
    """One ratchet finding (what the runner records)."""

    kind: str
    family: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``family``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "family": self.family,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": self.remedy,
        }


class FuncInfo(NamedTuple):
    """A function definition considered for duplicate-body."""

    file: str
    qualname: str
    line: int
    digest: str
    excluded: bool  # deny-listed definition (reported elsewhere or canonical)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    names = [p.arg for p in (*a.posonlyargs, *a.args)]
    if a.vararg:
        names.append(a.vararg.arg)
    names.extend(p.arg for p in a.kwonlyargs)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


class _ParamRenamer(ast.NodeTransformer):
    """Rename references to a function's parameters to positional tokens."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def visit_Name(self, node: ast.Name) -> ast.AST:
        new = self.mapping.get(node.id)
        if new is not None:
            return ast.copy_location(ast.Name(id=new, ctx=node.ctx), node)
        return node


def _count_nodes(nodes: list[ast.stmt]) -> int:
    return sum(1 for stmt in nodes for _ in ast.walk(stmt))


def body_digest(fn: ast.FunctionDef | ast.AsyncFunctionDef, min_nodes: int) -> str | None:
    """Return the normalised-body hash of *fn*, or None when it is too small."""
    body = _strip_docstring(list(fn.body))
    if not body or _count_nodes(body) < min_nodes:
        return None
    mapping = {name: f"_p{i}" for i, name in enumerate(_param_names(fn))}
    # Deep copy: the renamer rewrites nodes in place.
    module = ast.Module(body=copy.deepcopy(body), type_ignores=[])
    module = _ParamRenamer(mapping).visit(module)
    dumped = ast.dump(module, annotate_fields=False, include_attributes=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def _is_state_regex_literal(value: str, in_re_call: bool) -> bool:
    if not _STATE_HEADING_RE.search(value):
        return False
    return in_re_call or bool(_REGEX_SYNTAX_RE.search(value))


class _FileVisitor(ast.NodeVisitor):
    """Collect deny-list hits and function bodies from one module."""

    def __init__(self, rel: str, min_nodes: int) -> None:
        self.rel = rel
        self.min_nodes = min_nodes
        self.scope: list[str] = []
        self.raw: list[RawFinding] = []
        self.funcs: list[FuncInfo] = []
        self.configparser_aliases: set[str] = set()
        self.parser_class_names: set[str] = set()
        self.re_aliases: set[str] = set()
        self.re_func_names: set[str] = set()
        self.docstring_ids: set[int] = set()
        self.re_pattern_ids: set[int] = set()

    # -- scope ----------------------------------------------------------
    def _symbol(self) -> str:
        return ".".join(self.scope) if self.scope else MODULE_SYMBOL

    def _note_docstring(self, body: list[ast.stmt]) -> None:
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            self.docstring_ids.add(id(body[0].value))

    def visit_Module(self, node: ast.Module) -> None:
        self._note_docstring(node.body)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._note_docstring(node.body)
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._note_docstring(node.body)
        self.scope.append(node.name)
        qualname = self._symbol()
        deny = _DENY_BY_BASE.get(node.name.lstrip("_"))
        excluded = deny is not None
        if deny is not None and self.rel != deny.owner:
            self.raw.append(RawFinding(deny.family, self.rel, qualname, node.lineno))
        digest = body_digest(node, self.min_nodes)
        if digest is not None:
            self.funcs.append(FuncInfo(self.rel, qualname, node.lineno, digest, excluded))
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    # -- imports --------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "configparser":
                self.configparser_aliases.add(alias.asname or "configparser")
            elif alias.name == "re":
                self.re_aliases.add(alias.asname or "re")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "configparser":
            for alias in node.names:
                if alias.name.endswith("Parser"):
                    self.parser_class_names.add(alias.asname or alias.name)
        elif node.module == "re":
            for alias in node.names:
                if alias.name in _RE_FUNCS:
                    self.re_func_names.add(alias.asname or alias.name)

    # -- calls ----------------------------------------------------------
    def _is_configparser_call(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Attribute) and func.attr.endswith("Parser"):
            base = func.value
            if isinstance(base, ast.Name):
                return base.id in self.configparser_aliases or "configparser" in base.id.lower()
            return False
        if isinstance(func, ast.Name):
            return func.id in self.parser_class_names
        return False

    def _is_re_call(self, func: ast.expr) -> bool:
        if isinstance(func, ast.Attribute) and func.attr in _RE_FUNCS:
            return isinstance(func.value, ast.Name) and (
                func.value.id in self.re_aliases or func.value.id == "re"
            )
        if isinstance(func, ast.Name):
            return func.id in self.re_func_names
        return False

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_configparser_call(node.func) and self.rel != INI_OWNER:
            self.raw.append(
                RawFinding(INI_FAMILY, self.rel, self._symbol(), node.lineno, ast.unparse(node.func))
            )
        if self._is_re_call(node.func):
            pattern = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "pattern":
                    pattern = kw.value
            if pattern is not None:
                for sub in ast.walk(pattern):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        self.re_pattern_ids.add(id(sub))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if (
            isinstance(node.value, str)
            and id(node) not in self.docstring_ids
            and self.rel != STATE_OWNER
            and _is_state_regex_literal(node.value, id(node) in self.re_pattern_ids)
        ):
            self.raw.append(RawFinding(STATE_FAMILY, self.rel, self._symbol(), node.lineno))


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    """*path* relative to *root* in POSIX form (``../`` steps when outside it)."""
    return Path(os.path.relpath(path.resolve(), root.resolve())).as_posix()


def iter_scan_files(scan_root: Path) -> list[Path]:
    """Every non-test ``*.py`` file under *scan_root*, sorted."""
    out = []
    for p in sorted(scan_root.rglob("*.py")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in p.relative_to(scan_root).parts[:-1]):
            continue
        out.append(p)
    return out


class ScanResult(NamedTuple):
    """One scan's totals: files scanned, raw findings, duplicate groups found, and files that failed to parse."""
    files: int
    raw: list[RawFinding]
    dup_groups: int
    parse_errors: list[Finding]


def scan_module(tree: ast.Module, rel: str, min_nodes: int) -> _FileVisitor:
    """Visit one parsed module and return the visitor holding its results.

    Import aliases are collected first, so a call is recognised even when
    the import sits textually after it (a function-local import, say).
    """
    visitor = _FileVisitor(rel, min_nodes)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            visitor.visit_Import(node)
        elif isinstance(node, ast.ImportFrom):
            visitor.visit_ImportFrom(node)
    visitor.visit(tree)
    return visitor


def scan(root: Path, scan_roots: list[Path], min_nodes: int) -> ScanResult:
    """Scan every file under *scan_roots*; return raw findings."""
    raw: list[RawFinding] = []
    funcs: list[FuncInfo] = []
    parse_errors: list[Finding] = []
    seen: set[Path] = set()
    files = 0
    for scan_root in scan_roots:
        if not scan_root.is_dir():
            continue
        for path in iter_scan_files(scan_root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            rel = _rel(path, root)
            files += 1
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            except (SyntaxError, ValueError) as exc:
                parse_errors.append(
                    Finding(
                        kind="parse-error",
                        family="",
                        file=rel,
                        line=getattr(exc, "lineno", 0) or 0,
                        summary=f"{rel} does not parse as Python and was not scanned: {exc}",
                        remedy=f"python3 -m py_compile {rel}",
                    )
                )
                continue
            visitor = scan_module(tree, rel, min_nodes)
            raw.extend(visitor.raw)
            funcs.extend(visitor.funcs)

    by_digest: dict[str, list[FuncInfo]] = defaultdict(list)
    for f in funcs:
        by_digest[f.digest].append(f)
    groups = 0
    for members in by_digest.values():
        if len(members) < 2:
            continue
        groups += 1
        for m in members:
            if m.excluded:
                continue
            peers = [f"{p.file}:{p.qualname}" for p in members if p is not m]
            raw.append(RawFinding(DUP_FAMILY, m.file, m.qualname, m.line, ", ".join(sorted(peers))))
    raw.sort(key=lambda r: (FAMILIES.index(r.family), r.file, r.line, r.symbol))
    return ScanResult(files, raw, groups, parse_errors)


def measured_keys(raw: list[RawFinding]) -> Counter:
    """Count raw findings per ``(family, file, symbol)`` key."""
    return Counter((r.family, r.file, r.symbol) for r in raw)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path, allow_rel: str) -> tuple[dict[tuple[str, str, str], tuple[int, int]], list[Finding]]:
    """Read the allowlist into ``{(family, path, symbol): (count, lineno)}``.

    Liberal reader: any whitespace run separates fields; blank and ``#``
    lines are skipped. Unparseable lines become findings and are ignored.
    """
    entries: dict[tuple[str, str, str], tuple[int, int]] = {}
    findings: list[Finding] = []
    if not path.is_file():
        findings.append(
            Finding(
                kind="allowlist-missing",
                family="",
                file=allow_rel,
                line=0,
                summary=f"allowlist {allow_rel} not found; every finding is reported as unlisted",
                remedy=f"{RUN_COMMAND} --emit-allowlist",
            )
        )
        return entries, findings
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 4 or fields[0] not in FAMILIES or not fields[3].isdigit():
            findings.append(
                Finding(
                    kind="allowlist-malformed",
                    family="",
                    file=allow_rel,
                    line=lineno,
                    summary=(
                        "allowlist line is not '<family> <path> <symbol> <count>' with a known "
                        f"family and was ignored: {raw!r}"
                    ),
                    remedy=f"{RUN_COMMAND} --emit-allowlist",
                )
            )
            continue
        entries[(fields[0], fields[1], fields[2])] = (int(fields[3]), lineno)
    return entries, findings


def format_allowlist(counts: Counter) -> str:
    """Render measured keys in allowlist format, sorted by family then path."""
    rows = [
        f"{fam}\t{path}\t{sym}\t{n}"
        for (fam, path, sym), n in sorted(counts.items(), key=lambda kv: (FAMILIES.index(kv[0][0]), kv[0][1], kv[0][2]))
    ]
    return "\n".join(rows) + ("\n" if rows else "")


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _describe(r: RawFinding) -> str:
    owner = OWNER_OF_FAMILY.get(r.family)
    if r.family == DUP_FAMILY:
        return f"{r.file}:{r.line} {r.symbol} has the same normalised body as {r.detail}"
    if r.family == INI_FAMILY:
        return (
            f"{r.file}:{r.line} instantiates {r.detail}() in {r.symbol}; INI parsers are "
            f"created only in {owner}"
        )
    if r.family == STATE_FAMILY:
        return (
            f"{r.file}:{r.line} carries a '## State' regex in {r.symbol}; status.md is read "
            f"only through {owner}"
        )
    return f"{r.file}:{r.line} defines {r.symbol}; this helper may be defined only in {owner}"


def _remedy(family: str) -> str:
    owner = OWNER_OF_FAMILY.get(family)
    if owner:
        return f"import the helper from {owner} instead of defining a copy, then re-run: {RUN_COMMAND}"
    return f"move the shared body into one module and import it from both callers, then re-run: {RUN_COMMAND}"


def compare(
    raw: list[RawFinding],
    allowlist: dict[tuple[str, str, str], tuple[int, int]],
    allowlist_rel: str,
) -> list[Finding]:
    """Compare measured keys with the allowlist; return ratchet findings."""
    counts = measured_keys(raw)
    by_key: dict[tuple[str, str, str], list[RawFinding]] = defaultdict(list)
    for r in raw:
        by_key[(r.family, r.file, r.symbol)].append(r)

    findings: list[Finding] = []
    for key, items in by_key.items():
        fam, path, sym = key
        n = counts[key]
        entry = allowlist.get(key)
        if entry is None:
            for r in items:
                findings.append(
                    Finding("unlisted", fam, path, r.line, f"[{fam}] {_describe(r)}", _remedy(fam))
                )
        elif n > entry[0]:
            findings.append(
                Finding(
                    "above",
                    fam,
                    path,
                    items[-1].line,
                    (
                        f"[{fam}] {path} {sym} has {n} finding(s), above its allowlist entry of "
                        f"{entry[0]}: " + "; ".join(_describe(r) for r in items)
                    ),
                    _remedy(fam),
                )
            )
        elif n < entry[0]:
            findings.append(
                Finding(
                    "below",
                    fam,
                    path,
                    0,
                    (
                        f"[{fam}] {path} {sym} has {n} finding(s), below its allowlist entry of "
                        f"{entry[0]}: tighten the entry (set it to '{fam}\t{path}\t{sym}\t{n}' "
                        f"in {allowlist_rel})"
                    ),
                    f"{RUN_COMMAND} --emit-allowlist",
                )
            )
    for key, (allowed, lineno) in sorted(allowlist.items()):
        if key in counts:
            continue
        fam, path, sym = key
        findings.append(
            Finding(
                "below",
                fam,
                path,
                0,
                (
                    f"[{fam}] allowlist entry '{path} {sym}' ({allowed}, {allowlist_rel} line "
                    f"{lineno}) no longer matches anything: tighten the entry (remove it)"
                ),
                f"{RUN_COMMAND} --emit-allowlist",
            )
        )
    findings.sort(key=lambda f: (f.kind, f.family, f.file, f.line))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_python_duplicates",
        description=(
            "Ratchet lint: find duplicated Python function bodies (AST-normalised hash) "
            "and deny-listed helpers defined outside their owning module, and compare "
            "them with the allowlist. Quality class: always exits 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Allowlist format: one '<family>\\t<path>\\t<symbol>\\t<count>' line per key,\n"
            "path relative to the repository root. Families: " + ", ".join(FAMILIES) + ".\n"
            "Entries are only ever lowered or removed, never raised.\n\n"
            "Exit codes:\n  0  Always (findings are narrated, never fatal).\n"
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--scan-root", metavar="PATH", action="append", default=None,
                        help=("Directory to scan, relative to --root or absolute; repeatable "
                              f"(default: {', '.join(DEFAULT_SCAN_ROOTS)})."))
    parser.add_argument("--allowlist", metavar="PATH", default=DEFAULT_ALLOWLIST,
                        help=f"Allowlist file, relative to --root or absolute (default: {DEFAULT_ALLOWLIST}).")
    parser.add_argument("--min-nodes", metavar="N", type=int, default=DEFAULT_MIN_NODES,
                        help=f"Smallest body in AST nodes considered for duplicate-body (default: {DEFAULT_MIN_NODES}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write ratchet findings as a JSON array to PATH (always written).")
    parser.add_argument("--emit-allowlist", action="store_true",
                        help="Print measured keys in allowlist format and exit 0.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every raw finding, allowlisted or not.")
    return parser


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _write_findings(path_str: str | None, findings: list[Finding]) -> None:
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
        print(f"lint_python_duplicates: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    scan_roots = [_resolve(root, v) for v in (args.scan_root or DEFAULT_SCAN_ROOTS)]
    allowlist_path = _resolve(root, args.allowlist)
    allowlist_rel = _rel(allowlist_path, root)

    findings: list[Finding] = []
    for sr in scan_roots:
        if not sr.is_dir():
            findings.append(
                Finding(
                    kind="scan-root-missing",
                    family="",
                    file=_rel(sr, root),
                    line=0,
                    summary=f"scan root {sr} does not exist; it was not scanned",
                    remedy=f"{RUN_COMMAND} --scan-root team/pgai_agent_kanban",
                )
            )

    result = scan(root, scan_roots, args.min_nodes)
    counts = measured_keys(result.raw)

    if args.emit_allowlist:
        sys.stdout.write(format_allowlist(counts))
        return 0

    if args.verbose:
        for r in result.raw:
            print(f"  [python-duplicates] [{r.family}] {_describe(r)}")

    entries, allow_findings = load_allowlist(allowlist_path, allowlist_rel)
    findings.extend(result.parse_errors)
    findings.extend(allow_findings)
    findings.extend(compare(result.raw, entries, allowlist_rel))
    _write_findings(args.findings_file, findings)

    for f in findings:
        print(f"lint_python_duplicates: [{f.kind}] {f.summary}")

    per_family = Counter(r.family for r in result.raw)
    print("lint_python_duplicates: families: " + " ".join(f"{fam}={per_family.get(fam, 0)}" for fam in FAMILIES))
    print(
        f"lint_python_duplicates: totals: {result.files} file(s) scanned, {len(result.raw)} raw "
        f"finding(s) ({result.dup_groups} duplicate-body group(s)); {len(findings)} ratchet finding(s)"
    )

    if findings:
        message = (
            f"{len(findings)} duplicated-Python finding(s) against {allowlist_rel}; "
            "see the lines printed above"
        )
        remedy = (
            "replace each new copy with an import from its owning module, tighten any "
            f"entry reported below its count, then re-run: {RUN_COMMAND}"
        )
        # CENSUS-ANCHOR: python-duplicates-01
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; the lint returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.python-duplicates", message, remedy)
        except ImportError:
            print(f"error:   lint.python-duplicates\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
