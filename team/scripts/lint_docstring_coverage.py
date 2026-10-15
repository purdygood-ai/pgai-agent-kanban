#!/usr/bin/env python3
"""
lint_docstring_coverage.py
==========================
Reports kanban Python code that a reader cannot learn from its own text:
a module with no module docstring, a public function, class or public
method with no docstring, and a package ``add_argument`` call with no
``help=``.

A docstring is where a name says what it returns or does; a ``help=`` is
where a command-line option says what it means when someone runs a module
by hand from an error remedy or a doc. Both are cheap to write while the
code is fresh and expensive to reconstruct later, so this lint names every
gap and the tree stays at zero.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Blocking Severity: only real
damage blocks"). A missing docstring or option help is a documentation
finding with zero runtime-failure risk: the lint narrates each finding,
writes them to ``--findings-file`` for the gated runner's aggregate, and
ALWAYS exits 0 -- it never fails a runner and never blocks a release.

WHAT IS SCANNED
---------------
Every ``*.py`` file under these trees (relative to ``--root``):

  * ``team/pgai_agent_kanban/``
  * ``team/pm-agent/``
  * ``team/scripts/``

except test code and fixtures: a file with a directory named ``tests``,
``test`` or ``fixtures`` anywhere below ``team/``, and any file named
``test_*.py``, ``*_test.py`` or ``conftest.py``.

WHAT COUNTS AS A FINDING
------------------------
``missing-module-docstring``
    The module's first statement is not a string literal. An ``__init__.py``
    whose description sits in a ``#`` comment block is reported: a comment is
    not what ``help()`` or ``pydoc`` show. A file that is empty (or holds
    only blank lines and comments) is reported too -- it is still a module.

``missing-docstring``
    A public name with no docstring. Public names are:

      * a top-level ``def``, ``async def`` or ``class`` whose name does not
        start with ``_``;
      * a method (``def`` / ``async def``) of a public top-level class whose
        name does not start with ``_`` -- dunders such as ``__init__`` are
        skipped, and so are a property's ``@<name>.setter`` /
        ``@<name>.deleter`` (the getter carries the docstring) and
        ``@overload`` stubs.

    The finding names the dotted name (``Class.method``) and the line of the
    ``def`` / ``class`` keyword.

``add-argument-without-help`` (package code only)
    An ``<parser>.add_argument(...)`` call under ``team/pgai_agent_kanban/``
    with no ``help=`` keyword. ``help=argparse.SUPPRESS`` counts as help (it
    is a deliberate choice), and so does a call that passes ``**kwargs``,
    which may carry one. The finding names the option strings.

OUTPUT
------
One line per finding, then a totals line:

    docstring_coverage: <path>:<line> <name> -- <kind>
    lint_docstring_coverage: totals: <N> file(s) scanned; <F> finding(s)

``<name>`` is the dotted public name, ``<module>`` for a module docstring,
or the option strings (``--project``) for an ``add_argument`` call.

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``file``, ``line``, ``name``, ``summary``,
``remedy``) in the shape ``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

USAGE
-----
  python3 team/scripts/lint_docstring_coverage.py [--root PATH]
      [--paths PATH ...] [--findings-file PATH]

  --root PATH           Repository root (default: two levels above this
                        script's directory). Paths are reported and scoped
                        relative to it.
  --paths PATH ...      Scan exactly these files instead of the three trees.
                        The scope rules above still apply: a file outside
                        the three trees, a test or fixture file, or a file
                        that is not *.py is skipped.
  --findings-file PATH  Write findings as a JSON array to PATH.

  The gated unit runner (team/scripts/run-unit-tests.sh) runs it on every
  certification run; run it by hand from the repository root with:

  python3 team/scripts/lint_docstring_coverage.py

EXIT CODES
----------
  0   Always, whatever was found (quality class). An argument the parser
      rejects exits 2 through argparse before any scan.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Iterator, NamedTuple

LINT_NAME = "docstring_coverage"
SCAN_TREES = ("team/pgai_agent_kanban", "team/pm-agent", "team/scripts")
# add_argument help= is required in package code: operators and error
# remedies run its modules directly as package modules (``python3 -m``).
HELP_TREES = ("team/pgai_agent_kanban",)
EXCLUDED_DIR_NAMES = frozenset({"tests", "test", "fixtures"})
REMEDY = "python3 team/scripts/lint_docstring_coverage.py"

KIND_MODULE = "missing-module-docstring"
KIND_NAME = "missing-docstring"
KIND_HELP = "add-argument-without-help"

FIXES = {
    KIND_MODULE: "open the module with a docstring that says what it holds",
    KIND_NAME: "add a docstring that says what it returns or does",
    KIND_HELP: "add help= describing the option and its default",
}


class Finding(NamedTuple):
    """One public name, module or ``add_argument`` call missing its text."""

    file: str
    line: int
    name: str
    kind: str

    @property
    def summary(self) -> str:
        """One-line description: ``<name> -- <kind>``."""
        return f"{self.name} -- {self.kind}"

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity`` (always ``quality``), ``kind``,
        ``file``, ``line``, ``name``, ``summary`` and ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "name": self.name,
            "summary": f"{self.file}:{self.line} {self.summary}: {FIXES[self.kind]}",
            "remedy": REMEDY,
        }


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def display_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* in posix form, or as given outside it."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _under(rel: str, trees: tuple[str, ...]) -> bool:
    """True when the posix path *rel* sits inside one of *trees*."""
    return any(rel == tree or rel.startswith(tree + "/") for tree in trees)


def in_scope(rel: str) -> bool:
    """True for a non-test, non-fixture ``*.py`` file inside a scanned tree."""
    if not rel.endswith(".py") or not _under(rel, SCAN_TREES):
        return False
    parts = Path(rel).parts
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return False
    name = parts[-1]
    return not (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


def tree_files(root: Path) -> list[Path]:
    """Every in-scope ``*.py`` file under the three scanned trees, sorted."""
    found: list[Path] = []
    for tree in SCAN_TREES:
        top = root / tree
        if not top.is_dir():
            continue
        for path in top.rglob("*.py"):
            if path.is_file() and in_scope(display_path(path, root)):
                found.append(path)
    return sorted(found)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _decorator_names(node: ast.AST) -> list[str]:
    """Return each decorator of *node* as dotted text (``prop.setter``)."""
    names: list[str] = []
    for deco in getattr(node, "decorator_list", []):
        target = deco.func if isinstance(deco, ast.Call) else deco
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
        names.append(".".join(reversed(parts)))
    return names


def _is_accessor_or_overload(node: ast.AST) -> bool:
    """True for a property setter/deleter or an ``@overload`` stub."""
    for name in _decorator_names(node):
        if name.endswith(".setter") or name.endswith(".deleter"):
            return True
        if name in ("overload", "typing.overload"):
            return True
    return False


def public_names(tree: ast.Module) -> Iterator[tuple[str, ast.AST]]:
    """Yield ``(dotted_name, node)`` for every public top-level name and method."""
    for node in tree.body:
        if isinstance(node, _DEF_TYPES) and not node.name.startswith("_"):
            yield node.name, node
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            yield node.name, node
            for member in node.body:
                if (
                    isinstance(member, _DEF_TYPES)
                    and not member.name.startswith("_")
                    and not _is_accessor_or_overload(member)
                ):
                    yield f"{node.name}.{member.name}", member


def _has_help(call: ast.Call) -> bool:
    """True when an ``add_argument`` call passes ``help=`` or ``**kwargs``."""
    return any(kw.arg in ("help", None) for kw in call.keywords)


def _option_label(call: ast.Call) -> str:
    """Return the call's option strings (``--project/-p``) or ``add_argument``."""
    names = [
        arg.value
        for arg in call.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    return "/".join(names) if names else "add_argument"


def add_argument_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield every ``<expr>.add_argument(...)`` call in *tree*."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            yield node


def scan_source(text: str, rel: str) -> list[Finding]:
    """Return the findings for module source *text*, reported as path *rel*.

    A file that does not parse yields no finding here: a syntax error is
    breakage class, reported by ``team/scripts/check_breakage_class.py``.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    findings: list[Finding] = []
    if ast.get_docstring(tree, clean=False) is None:
        findings.append(Finding(rel, 1, "<module>", KIND_MODULE))
    for name, node in public_names(tree):
        if ast.get_docstring(node, clean=False) is None:
            findings.append(Finding(rel, node.lineno, name, KIND_NAME))
    if _under(rel, HELP_TREES):
        for call in add_argument_calls(tree):
            if not _has_help(call):
                findings.append(Finding(rel, call.lineno, _option_label(call), KIND_HELP))
    findings.sort(key=lambda f: (f.line, f.name))
    return findings


def scan_file(path: Path, rel: str) -> list[Finding]:
    """Return the findings for the file at *path*, reported as *rel*."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"lint_{LINT_NAME}: skipped {rel}: {exc}", file=sys.stderr)
        return []
    return scan_source(text, rel)


def run(root: Path, paths: list[Path] | None = None) -> tuple[int, list[Finding]]:
    """Scan the three trees under *root* (or exactly *paths*).

    Returns ``(files_scanned, findings)``.
    """
    candidates = tree_files(root) if paths is None else paths
    findings: list[Finding] = []
    scanned = 0
    for path in candidates:
        if not path.is_file():
            print(f"lint_{LINT_NAME}: skipped {path}: not a file", file=sys.stderr)
            continue
        rel = display_path(path, root)
        if not in_scope(rel):
            continue
        scanned += 1
        findings.extend(scan_file(path, rel))
    return scanned, findings


def main(argv: list[str] | None = None) -> int:
    """Run the lint from the command line; always returns 0 (quality class).

    Prints one ``docstring_coverage: <path>:<line> <name> -- <kind>`` line
    per finding and a totals line, and writes ``--findings-file`` when
    given. Argument errors exit 2 through argparse.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Report modules, public functions, classes and methods without a "
            "docstring, and package add_argument calls without help= "
            "(quality class; always exits 0)."
        )
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="repository root (default: this checkout)",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        metavar="PATH",
        help="scan exactly these files instead of the three trees",
    )
    parser.add_argument(
        "--findings-file",
        default=None,
        metavar="PATH",
        help="write findings as a JSON array to PATH (default: none written)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    paths = [Path(p) for p in args.paths] if args.paths else None
    scanned, findings = run(root, paths)
    for finding in findings:
        print(f"{LINT_NAME}: {finding.file}:{finding.line} {finding.summary}")
    print(f"lint_{LINT_NAME}: totals: {scanned} file(s) scanned; {len(findings)} finding(s)")

    if args.findings_file:
        out = Path(args.findings_file)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps([f.to_dict() for f in findings], indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            print(f"lint_{LINT_NAME}: could not write findings file {out}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
