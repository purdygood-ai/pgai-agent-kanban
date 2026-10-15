#!/usr/bin/env python3
"""
lint_container_engine_free.py
==============================
Fatal (BLOCK-WORTHY) lint: no test module in the per-RC sweep set may
invoke or probe a container engine (``docker``/``podman``). Runaway build
and container-launch cost on every RC is real damage, so this check fails
the gated runner rather than filing a quality finding -- see
``run-unit-tests.sh``'s and ``run-integration-tests.sh``'s call sites for
the fatal wiring, the classification comment, the dual-channel error
contract, and the census anchor this module's docstring is required to
accompany.

WHAT COUNTS AS "INVOKES OR PROBES"
------------------------------------
Detected via Python's ``ast`` module (never text/regex, so comments,
docstrings, and unrelated string literals cannot trip it) against every
``.py`` file under the scanned sweep directories:

  1. ``subprocess.run`` / ``.Popen`` / ``.call`` / ``.check_call`` /
     ``.check_output`` whose first positional argument is a list/tuple
     literal starting with the string ``"docker"`` or ``"podman"``, or a
     plain string literal whose first whitespace-separated token is one of
     those two names (the ``shell=True`` form).
  2. ``shutil.which("docker")`` / ``shutil.which("podman")`` -- probing for
     the engine's presence.
  3. ``os.system(...)`` whose command string's first token is ``docker`` or
     ``podman``.

WHY THIS DOES NOT FLAG A DELIBERATELY-ABSENT-BINARY TEST
-----------------------------------------------------------
Some tests in the per-RC sweep set legitimately construct a call shaped
exactly like the ones above to prove a *fail-loud* contract when a binary
is missing (e.g. "an unguarded call to docker fails loud when docker is
absent") -- see ``test_binary_guard_contract.py``. Such a call can never
reach a real container engine because the SAME enclosing test function
first redirects ``PATH`` to a location under the test's own control:
``monkeypatch.setenv("PATH", ...)`` or an ``os.environ["PATH"] = ...``
assignment, executed (by source line order) before the flagged call. A
flagged call is exempted when its enclosing function contains such a
PATH-redirection statement at an earlier line -- structurally, that call
cannot resolve the real system binary, so it does not "invoke or probe a
container engine" in the sense this lint exists to catch (real build or
container-launch cost). A call with no such redirection anywhere earlier
in its enclosing function is reported.

SCOPE (per-RC sweep set, never the deep-verify tier)
-------------------------------------------------------
Default scanned directories are exactly what ``run-unit-tests.sh`` and
``run-integration-tests.sh`` collect: ``team/tests/unit/``,
``team/tests/integration/``, and ``team/pgai_agent_kanban/api/tests/``.
``team/tests/cold/`` (the DEEP-VERIFY tier, entitled to use a container
engine) is never scanned by default, and any ``fixtures/`` or
``__pycache__`` path segment nested below a scanned root is skipped -- the
fixtures directory holds deliberate bad-example files exercised only by
this lint's own unit tests (see
``team/tests/unit/test_lint_container_engine_free.py``), matching the
convention ``lint_test_anti_patterns.py`` already uses.

``team/tests/unit/test_entrypoint_shipped_image.py`` is always excluded
(``_DEFAULT_IGNORED_SUFFIXES``), regardless of ``--sweep-dir``: it is not
"cold" -- it stays collected by ``run-unit-tests.sh``'s ``tests/unit/``
glob for every other purpose, matched there by an explicit
``--ignore=tests/unit/test_entrypoint_shipped_image.py`` pytest argument
-- but it legitimately builds real images and starts real containers
through its own dedicated on-demand docker-gated runner
(``team/scripts/run-gated-tests.sh``). This lint's exclusion must agree
with that same pytest-level ``--ignore=``, or the two would disagree about
whether the unmodified tree is clean.

EXIT CODES
----------
  0   No violations in any scanned sweep directory.
  1   One or more violations found; see stdout for the file, line, and a
      runnable remedy.
  2   Usage error -- a ``--sweep-dir`` path does not exist.

USAGE
-----
  python3 team/scripts/lint_container_engine_free.py [--sweep-dir PATH ...] [--verbose]

  --sweep-dir PATH   Repeatable. Directory to scan recursively for ``.py``
                     files (default: the three per-RC sweep directories
                     named above, resolved from this script's own
                     location). Tests point this at fixture roots under
                     team/tests/fixtures/container_engine_free/.
  --verbose, -v      Print each file as it is scanned.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Detection constants
# ---------------------------------------------------------------------------

_ENGINE_NAMES = {"docker", "podman"}
_SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
_SKIPPED_PATH_SEGMENTS = {"fixtures", "__pycache__", "cold"}

# Modules deliberately excluded from every scan regardless of --sweep-dir,
# named by their path suffix relative to team/ (posix-style, forward
# slashes). Each entry already has its own dedicated on-demand docker-gated
# runner -- unlike the deep-verify tier, it is not "cold": it is still
# collected by run-unit-tests.sh's tests/unit/ glob for every OTHER purpose,
# so it cannot simply move to team/tests/cold/ (see run-unit-tests.sh's own
# --ignore= comment for the full reasoning). This lint's scan must agree
# with that same --ignore= exclusion, or the two would disagree about
# whether the unmodified tree is clean.
_DEFAULT_IGNORED_SUFFIXES = ("tests/unit/test_entrypoint_shipped_image.py",)


@dataclass
class _Finding:
    """One violation: a location, a concrete cause, and a runnable remedy."""

    path: Path
    line: int
    cause: str
    remedy: str


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def find_sweep_files(sweep_dir: Path) -> list[Path]:
    """Return every ``.py`` file under *sweep_dir*, recursive, sorted for
    deterministic output, skipping any fixtures/__pycache__/cold path
    segment found BELOW *sweep_dir* (a segment that is part of *sweep_dir*
    itself -- e.g. a caller deliberately pointing --sweep-dir at a
    fixtures/ root for this lint's own unit tests -- is never skipped;
    only a nested occurrence discovered while walking is)."""
    return sorted(
        p
        for p in sweep_dir.rglob("*.py")
        if p.is_file()
        and not (_SKIPPED_PATH_SEGMENTS & set(p.relative_to(sweep_dir).parts[:-1]))
    )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.AST | None:
    cur = parents.get(node)
    while cur is not None and not isinstance(
        cur, (ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        cur = parents.get(cur)
    return cur


def _call_func_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return None


def _first_token(text: str) -> str | None:
    parts = text.split()
    return parts[0] if parts else None


def _engine_name_from_call(node: ast.Call) -> str | None:
    """Return the engine name ("docker"/"podman") this subprocess-family
    call invokes, or None if it does not match the detected shape."""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        el0 = first.elts[0]
        if isinstance(el0, ast.Constant) and isinstance(el0.value, str):
            return el0.value if el0.value in _ENGINE_NAMES else None
        return None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        token = _first_token(first.value)
        return token if token in _ENGINE_NAMES else None
    return None


def _function_scrubs_path_before(func_node: ast.AST, line_no: int) -> bool:
    """True when *func_node* contains, at a line strictly before *line_no*,
    a statement that redirects PATH to a value the test itself controls:
    ``monkeypatch.setenv("PATH", ...)`` or ``os.environ["PATH"] = ...``."""
    for n in ast.walk(func_node):
        n_line = getattr(n, "lineno", None)
        if n_line is None or n_line >= line_no:
            continue
        if isinstance(n, ast.Call):
            fn = n.func
            if (
                isinstance(fn, ast.Attribute)
                and fn.attr == "setenv"
                and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "PATH"
            ):
                return True
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if not isinstance(target, ast.Subscript):
                    continue
                val = target.value
                is_os_environ = (
                    isinstance(val, ast.Attribute) and val.attr == "environ"
                )
                key = target.slice
                # Python >= 3.9: target.slice is the key expression directly.
                if (
                    is_os_environ
                    and isinstance(key, ast.Constant)
                    and key.value == "PATH"
                ):
                    return True
    return False


# ---------------------------------------------------------------------------
# Per-file scan
# ---------------------------------------------------------------------------


def scan_file(path: Path) -> list[_Finding]:
    """Return one finding per un-exempted container-engine invocation or
    probe found in *path*."""
    findings: list[_Finding] = []
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        # Not this lint's concern -- the breakage-class gate (fatal,
        # earlier in the runner) already refuses a file that fails to
        # parse; treat an unparsable file as nothing-to-report here.
        return findings

    parents = _build_parent_map(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line_no = getattr(node, "lineno", None)
        if line_no is None:
            continue

        engine: str | None = None
        kind = ""
        func_name = _call_func_name(node)

        if func_name in _SUBPROCESS_FUNCS:
            engine = _engine_name_from_call(node)
            kind = "invocation"
        elif func_name == "which" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and arg0.value in _ENGINE_NAMES:
                engine = arg0.value
                kind = "probe"
        elif func_name == "system" and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                token = _first_token(arg0.value)
                if token in _ENGINE_NAMES:
                    engine = token
                    kind = "invocation"

        if engine is None:
            continue

        func_node = _enclosing_function(node, parents)
        if func_node is not None and _function_scrubs_path_before(
            func_node, line_no
        ):
            # PATH is redirected to a test-owned location earlier in the
            # same function -- this call structurally cannot reach the
            # real system binary (see module docstring).
            continue

        findings.append(
            _Finding(
                path=path,
                line=line_no,
                cause=(
                    f"{path}:{line_no} -- {kind} of container engine "
                    f"'{engine}' in the per-RC sweep set; no test collected "
                    "by run-unit-tests.sh or run-integration-tests.sh may "
                    "invoke or probe a container engine (runaway build/"
                    "container cost on every RC)"
                ),
                remedy=(
                    f"Relocate {path} (and its fixtures) to team/tests/cold/ "
                    "so team/scripts/deep-verify.sh runs it instead -- see "
                    "team/tests/cold/ for the existing relocated modules -- "
                    "or, if this call is a deliberate absent-binary test, "
                    "redirect PATH to a directory this test controls (e.g. "
                    "monkeypatch.setenv(\"PATH\", ...)) before the call so it "
                    "cannot reach a real engine"
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _print_findings(findings: list[_Finding], lint_script: Path) -> None:
    print(
        f"\nlint_container_engine_free: FAIL -- {len(findings)} "
        "violation(s) found -- see the per-RC sweep set for the offending "
        "file(s).\n",
        flush=True,
    )
    print("Cause(s):", flush=True)
    for finding in findings:
        print(f"  - {finding.cause}", flush=True)
    print("", flush=True)
    print("To fix:", flush=True)
    for i, finding in enumerate(findings, start=1):
        print(f"  {i}. {finding.remedy}", flush=True)
    print(
        f"  {len(findings) + 1}. python3 \"{lint_script}\"   # re-run this "
        "lint to confirm",
        flush=True,
    )
    print("", flush=True)
    print(
        "Rule: no test module collected by run-unit-tests.sh or "
        "run-integration-tests.sh may invoke or probe a container engine "
        "-- the deep-verify tier (team/tests/cold/) is the only lane "
        "entitled to do that.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_sweep_dirs() -> list[Path]:
    script_dir = Path(__file__).resolve().parent  # team/scripts/
    team_dir = script_dir.parent  # team/
    return [
        team_dir / "tests" / "unit",
        team_dir / "tests" / "integration",
        team_dir / "pgai_agent_kanban" / "api" / "tests",
    ]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_container_engine_free.py",
        description=(
            "AST-based lint over every .py file under --sweep-dir: refuses "
            "any subprocess invocation, shell command, or shutil.which() "
            "probe of a docker/podman container engine, except a call "
            "structurally proven unreachable by an earlier PATH redirect "
            "in the same test function. Default sweep dirs are the three "
            "directories run-unit-tests.sh and run-integration-tests.sh "
            "collect; team/tests/cold/ is never scanned."
        ),
    )
    parser.add_argument(
        "--sweep-dir",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "Directory to scan recursively for .py files. Repeatable. "
            "Default: team/tests/unit/, team/tests/integration/, and "
            "team/pgai_agent_kanban/api/tests/ resolved from this script's "
            "own location."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each file as it is scanned.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 (clean), 1 (violation(s) found), or 2 (usage)."""
    args = _parse_args(argv)

    if args.sweep_dir:
        sweep_dirs = [Path(d).resolve() for d in args.sweep_dir]
    else:
        sweep_dirs = _default_sweep_dirs()

    for sweep_dir in sweep_dirs:
        if not sweep_dir.is_dir():
            print(
                f"ERROR: sweep directory not found: {sweep_dir}\n"
                "Use --sweep-dir to specify an alternative path.",
                file=sys.stderr,
            )
            return 2

    files: list[Path] = []
    for sweep_dir in sweep_dirs:
        files.extend(find_sweep_files(sweep_dir))
    files = sorted(
        f
        for f in set(files)
        if not f.as_posix().endswith(_DEFAULT_IGNORED_SUFFIXES)
    )

    print(
        f"lint_container_engine_free: scanning {len(files)} file(s) under "
        f"{', '.join(str(d) for d in sweep_dirs)}",
        flush=True,
    )

    findings: list[_Finding] = []
    for filepath in files:
        if args.verbose:
            print(f"  scanning: {filepath}", flush=True)
        findings.extend(scan_file(filepath))

    findings.sort(key=lambda f: (str(f.path), f.line))

    if findings:
        _print_findings(findings, Path(__file__).resolve())
        return 1

    print(
        f"lint_container_engine_free: OK -- {len(files)} file(s) checked, "
        "0 violations.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
