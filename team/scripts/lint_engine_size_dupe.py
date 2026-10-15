#!/usr/bin/env python3
"""
lint_engine_size_dupe.py
=========================
Quality-class lint (never blocks a runner) that guards the engine
decomposition's two structural invariants:

  1. **Engine size** -- none of the files this decomposition reduced to a
     thin driver, or extracted into a single-concern lib stage, may grow
     back past 800 lines without anyone noticing.
  2. **Function-name duplication, tree-wide** -- no shell function name may
     be defined in two or more files that can end up loaded into the same
     bash process (one sources the other, directly or transitively, or a
     third file sources both). Bash's silent last-definition-wins semantics
     turn that into a load-order-dependent argument-misrouting bug (the
     defect class this lint's narrower predecessor, ``lint_lib_function_
     dedupe.py``, was built to catch for ``lib/*.sh`` alone).

WHY THIS EXISTS
---------------
The engine decomposition split five
monolithic engine files -- ``cm/release.sh`` (~3.9k lines),
``lib/discovery.sh`` (~3.2k), ``lib/wake_common.sh`` (~3.1k),
``dashboard/column-render.sh`` (~2.5k), and the near-twin providers
``wake/claude.sh`` + ``wake/codex.sh`` (~2k each) -- into thin drivers
(<300 lines) composing single-concern lib stages (<800 lines each). Nothing
in the tree stopped any of those files from silently growing back into a
monolith, or a future edit from reintroducing a duplicate helper across two
files that get sourced together. This lint is that guardrail, wired into
both gated runners via ``gated_runner_run_quality_lint`` so a regression
prints in full but never fails the suite.

CHECK 1: ENGINE SIZE
---------------------
Scope is deliberately an explicit, named manifest (``ENGINE_FILES`` below)
of the exact drivers and lib stages the engine decomposition produced
or reduced -- NOT a blanket "every file under team/scripts/must
stay under 800 lines" policy. Several pre-existing, unrelated utility
libraries (``lib/project_paths.sh``, ``lib/projects.sh``, ``lib/temp.sh``,
``lib/token_capture.sh``) already exceed 800 lines today and are out of
scope for this regression guard -- they were never part of the five named
monoliths and policing their size is a different ticket's job. A file in
the manifest that no longer exists (renamed or deleted in a later refactor)
is silently skipped, not an error -- this lint tracks a decomposition's
shape, not a fixed set of paths that must exist forever.

``--size-allow-file`` exempts a manifest entry (by its path relative to
``--scripts-dir``, or by basename) from the ceiling entirely -- for a thin
driver's composition table (the sourcing block enumerating every stage it
composes) or a provider-interface file that legitimately needs more room
than the ceiling allows. No entry needs this exemption on the current tree;
the flag exists for the next engine this pattern gets applied to.

CHECK 2: FUNCTION-NAME DUPLICATION, TREE-WIDE
-----------------------------------------------
Scope is every ``*.sh`` file under ``--scripts-dir``, recursive -- this is
the generalization of ``lint_lib_function_dedupe.py``'s flat, non-recursive
``lib/*.sh`` glob to the whole of ``team/scripts/``. A naive tree-wide scan
by name alone is far too noisy: dozens of independent, never-co-sourced
scripts (``verify-*.sh``, ``install-*.sh``, per-panel ``dashboard/show-*.sh``
helpers) each define their own local ``fail``/``pass``/``warn``/``get_val``
conveniences, and none of those pairings can ever collide at runtime
because the scripts are never loaded into the same bash process.

This lint tells the two cases apart by building the ``source``/``.``
dependency graph across the scanned tree (resolving each file's
self-locating ``$(dirname "${BASH_SOURCE[0]}")``-style prefix against the
sourcing file's own directory) and computing each file's ancestor-or-self
set -- the set of files that can, directly or transitively, source it. Two
files sharing a duplicated function name are a real violation only when
their ancestor-or-self sets intersect, i.e. some file (possibly one of the
two themselves) can reach both -- meaning some single process really can
load both definitions and hit last-definition-wins. Edge resolution is
best-effort and conservative: a ``source`` argument this lint cannot
resolve to a real file on disk is silently skipped (never guessed), so the
graph can under-connect (a missed real edge) but never over-connect (a
false collision). Under-connection trades a small false-negative risk for
a hard guarantee against false positives, which is the right trade for a
lint that must stay quiet on an already-clean tree.

ALLOW-LIST
----------
``--allow-file`` exempts a file (by basename) from the duplication check
entirely -- its own function definitions never count toward a violation,
regardless of what the reachability graph says. Default allow-list:

    provider_claude.sh   -- provider-interface implementation; deliberately
    provider_codex.sh       defines the same four function names as its
                             sibling (only one is ever sourced per
                             invocation).
    harness.sh           -- team/scripts/wake/harness.sh, the documented
                             integration-test stub wake provider. It sources
                             lib/worktree.sh directly and then deliberately
                             shadows create_detached_worktree /
                             teardown_task_worktree with lightweight
                             temp-directory stand-ins (see the file's own
                             "stub" comments) so integration fixtures never
                             touch a real git worktree. This is a genuine
                             same-process duplicate by construction, not an
                             accident -- exactly the provider-interface
                             pattern, applied to a test harness instead of a
                             production dispatcher.

Passing ``--allow-file`` REPLACES the defaults; pass all desired basenames
when using this flag (same convention as ``lint_lib_function_dedupe.py``).

RELATIONSHIP TO lint_lib_function_dedupe.py
---------------------------------------------
This lint supersedes ``lint_lib_function_dedupe.py`` as a gated-runner call
site: both ``run-unit-tests.sh`` and ``run-integration-tests.sh`` invoke
this file instead (its tree-wide, reachability-aware scan is a strict
superset of the old flat ``lib/*.sh`` check in coverage). ``lint_lib_
function_dedupe.py`` itself is UNCHANGED and still exists -- it keeps its
own passing test suite, its own census row (row 40), and its one remaining
caller, ``audit_coding_standards.sh``'s D01 check (a manually-run
compliance audit, not a per-run quality gate) -- rather than being deleted
outright, which would have required rewriting that audit script, its
census row, and its dedicated test file for no behavioral gain. Running
two independent scanners for the *same* concern inside the gated-runner
quality-lint pipeline is exactly the duplication this lint exists to catch;
keeping the narrower tool alive for its own separate, non-gated audit
surface is not that.

EXIT CODES
----------
  0   No violations in either check.
  1   A duplicate-function collision (CHECK 2) was found -- real damage,
      the hard class, returned even when a size finding rode along too.
  2   Usage error, or --scripts-dir does not exist.
  3   Only the size ceiling (CHECK 1) was exceeded -- an advisory reading
      (docs/blocking-site-census.md row 53: CONVERT), never the hard
      class. ``gated_runner_run_quality_lint`` treats any nonzero exit
      identically (a non-fatal quality finding), so this split matters to
      direct callers, not the gated runners.

USAGE
-----
  python3 team/scripts/lint_engine_size_dupe.py [--scripts-dir PATH]
                                                 [--max-lines N]
                                                 [--allow-file FILENAME]
                                                 [--size-allow-file ENTRY]
                                                 [--verbose]

  --scripts-dir PATH     Directory to scan (default: team/scripts/ resolved
                          from this script's own location).
  --max-lines N          Engine-size ceiling in lines (default: 800).
  --allow-file FILENAME  Basename whose functions are exempt from the
                          duplication check. May be repeated. Replaces the
                          default allow-list when passed at least once.
  --size-allow-file ENTRY
                          Manifest entry (relative path or basename) exempt
                          from the size ceiling. May be repeated.
  --verbose, -v          Print per-file scan detail for both checks.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_engine_size_dupe.py

  # Explicit scripts dir (e.g. a seeded test fixture):
  python3 team/scripts/lint_engine_size_dupe.py --scripts-dir /path/to/fixture

  # Custom duplication allow-list (replaces defaults):
  python3 team/scripts/lint_engine_size_dupe.py \\
      --allow-file provider_claude.sh \\
      --allow-file provider_codex.sh
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

# ---------------------------------------------------------------------------
# team/ import path -- needed for the dual-channel error-contract emitter.
# ---------------------------------------------------------------------------
_TEAM_DIR = Path(__file__).resolve().parent.parent  # team/
_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402

# ---------------------------------------------------------------------------
# CHECK 1: engine-size manifest
#
# Exactly the files the engine decomposition produced or reduced to
# a thin driver -- see the module docstring's "CHECK 1" section for why this
# is a named manifest rather than a blanket team/scripts/ policy. Paths are
# relative to --scripts-dir.
# ---------------------------------------------------------------------------
DEFAULT_MAX_LINES = 800

ENGINE_FILES: tuple[str, ...] = (
    # Thin drivers (the five original monoliths, plus the wake/codex.sh twin).
    "cm/release.sh",
    "lib/discovery.sh",
    "lib/wake_common.sh",
    "dashboard/column-render.sh",
    "wake/claude.sh",
    "wake/codex.sh",
    # cm/release.sh's lib stages (the five original stages plus the
    # closing-summary formatter, Fixed-In stage and archive stage
    # release_finalize.sh calls, the ship-posture stage release_ship_policy.sh calls into, the
    # VERSION-file stage release_notes.sh calls, and the --decide-only
    # stage).
    "lib/cm/release_preflight.sh",
    "lib/cm/release_ship_policy.sh",
    "lib/cm/release_ship_posture.sh",
    "lib/cm/release_squash.sh",
    "lib/cm/release_notes.sh",
    "lib/cm/release_version_file.sh",
    "lib/cm/release_finalize.sh",
    "lib/cm/release_origin_summary.sh",
    "lib/cm/release_fixed_in.sh",
    "lib/cm/release_archive.sh",
    "lib/cm/release_decide_only.sh",
    # lib/discovery.sh's lib stages.
    "lib/discovery_guards.sh",
    "lib/discovery_state.sh",
    "lib/discovery_eligibility.sh",
    "lib/discovery_items.sh",
    "lib/discovery_steps.sh",
    "lib/discovery_step_requirements.sh",
    "lib/discovery_bundle.sh",
    # lib/wake_common.sh's nine lib stages.
    "lib/wake_bootstrap.sh",
    "lib/wake_task_state.sh",
    "lib/wake_task_id_gate.sh",  # sourced by wake_task_state.sh, not wake_common.sh directly
    "lib/wake_reconcile_sweeps.sh",
    "lib/wake_task_transition.sh",
    "lib/wake_readme_meta.sh",
    "lib/wake_autonomous_scan.sh",
    "lib/wake_lifecycle.sh",
    "lib/wake_provider_pause.sh",
    "lib/wake_project.sh",
    "lib/wake_blocked_ledger.sh",
    # dashboard/column-render.sh's lib stages.
    "lib/dashboard/column_render_args.sh",
    "lib/dashboard/column_render_config.sh",
    "lib/dashboard/column_render_board.sh",
    "lib/dashboard/column_render_board_serve.sh",
    "lib/dashboard/column_render_data.sh",
    "lib/dashboard/column_render_data_multi.sh",
    "lib/dashboard/column_render_frame.sh",
    "lib/dashboard/column_render_cells.sh",
    "lib/dashboard/column_render_defer.sh",
    # wake/claude.sh + wake/codex.sh's shared driver stages and per-provider
    # implementations of the five-function provider interface.
    "lib/wake_driver.sh",
    "lib/wake_driver_prep.sh",
    "lib/wake_driver_worktree.sh",
    "lib/wake_driver_invoke.sh",
    "lib/wake_driver_finalize.sh",
    "lib/wake_driver_task_setup.sh",
    "lib/wake_driver_discovery.sh",
    "lib/wake_lock_wait.sh",
    "lib/wake_governance_composer.sh",
    "lib/wake_safeguard_refusal.sh",
    "lib/provider_claude.sh",
    "lib/provider_codex.sh",
)

# No manifest entry currently needs a size exemption. The mechanism exists
# for a future thin driver's composition table or provider-interface file
# that legitimately outgrows the ceiling.
DEFAULT_SIZE_ALLOW_FILES: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# CHECK 2: duplication defaults
# ---------------------------------------------------------------------------
_DEFAULT_ALLOW_FILES: frozenset[str] = frozenset({
    "provider_claude.sh",
    "provider_codex.sh",
    "harness.sh",
})

_FUNC_DEF_RE: re.Pattern[str] = re.compile(r"^([a-z][a-z0-9_]*)\(\)")
_SOURCE_PREFIX_RE: re.Pattern[str] = re.compile(r'^\s*(?:source|\.)\s+"')
_VAR_ASSIGN_PREFIX_RE: re.Pattern[str] = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)="'
)
_BARE_VAR_RE: re.Pattern[str] = re.compile(r'^\$([A-Za-z_][A-Za-z0-9_]*)$')


# ---------------------------------------------------------------------------
# Shared scanning primitives
# ---------------------------------------------------------------------------


def find_shell_files(scripts_dir: Path) -> list[Path]:
    """Return every ``*.sh`` file under *scripts_dir*, recursive, sorted."""
    return sorted(p for p in scripts_dir.rglob("*.sh") if p.is_file())


def find_functions_in_file(path: Path) -> list[str]:
    """Return the list of top-level function names defined in *path*.

    A definition is any line matching ``name()`` at column 0, where ``name``
    is ``[a-z][a-z0-9_]*`` (this codebase's shell function naming
    convention). Lines whose first non-whitespace character is ``#`` are
    skipped. Duplicates within the same file are returned as-is.

    Raises FileNotFoundError if *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"script not found: {path}")

    names: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        m = _FUNC_DEF_RE.match(line)
        if m:
            names.append(m.group(1))
    return names


def _scan_double_quoted_value(line: str, start: int) -> str | None:
    """Return the content of a double-quoted value beginning at *start*
    (the index immediately after the opening ``"``), tracking parenthesis
    depth so a nested ``$(...)`` command substitution's own internal
    double-quoted string is not mistaken for the terminator.

    This codebase's universal self-locating idiom, ``"$(dirname "${BASH_
    SOURCE[0]}")/../lib/x.sh"``, opens a NESTED double-quoted string inside
    the ``$(...)`` -- valid bash, since command substitution starts a fresh
    quoting context -- and a naive ``"([^"]*)"`` regex stops at that nested
    quote, returning a truncated, unresolvable fragment. This scan treats
    any ``"`` encountered while depth > 0 as nested content, not a
    terminator; the first ``"`` at depth 0 ends the value.

    Returns None if the value is unterminated (malformed line -- never
    guessed).
    """
    depth = 0
    for i in range(start, len(line)):
        ch = line[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == '"' and depth == 0:
            return line[start:i]
    return None


def _find_quoted_source_argument(line: str) -> str | None:
    """Extract the double-quoted argument to a ``source``/``.`` line, or
    None when the line is not such an invocation (see
    ``_scan_double_quoted_value`` for why a plain regex is not enough)."""
    m = _SOURCE_PREFIX_RE.match(line)
    if not m:
        return None
    return _scan_double_quoted_value(line, m.end())


def _strip_to_literal_suffix(expr: str) -> str | None:
    """Reduce a bash self-locating expression to its literal path suffix.

    Every ``source`` call in this codebase follows one of:
      ``$(dirname "${BASH_SOURCE[0]}")/rel/path.sh``
      ``${SCRIPT_DIR}/rel/path.sh``
      ``${_SOME_LIB_DIR}/name.sh``
    -- a variable/command-substitution prefix (always ending in ``}`` or
    ``)``) followed by a literal relative path. Return the text after the
    rightmost ``}``/``)``, with a leading ``/`` stripped. Returns None when
    no such prefix is found or the remainder does not look like a ``.sh``
    path -- callers must treat None as "cannot resolve," never guess.
    """
    idx = max(expr.rfind("}"), expr.rfind(")"))
    if idx == -1:
        return None
    suffix = expr[idx + 1:].lstrip("/")
    if not suffix or not suffix.endswith(".sh"):
        return None
    return suffix


def _extract_var_suffixes(text: str) -> dict[str, str]:
    """Map local variable names to their literal path suffix, best-effort.

    Handles ``VARNAME="<self-locating-expression>"`` assignments so a later
    ``source "$VARNAME"`` (a bare variable reference with no literal
    component of its own) can still be resolved. Unrecognized assignment
    shapes are simply absent from the returned map.
    """
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        m = _VAR_ASSIGN_PREFIX_RE.match(line)
        if not m:
            continue
        varname = m.group(1)
        rhs = _scan_double_quoted_value(line, m.end())
        if rhs is None:
            continue
        suffix = _strip_to_literal_suffix(rhs)
        if suffix:
            mapping[varname] = suffix
    return mapping


def _extract_source_suffixes(text: str) -> list[str]:
    """Return the literal path suffixes every ``source``/``.`` line in
    *text* resolves to, skipping any that cannot be resolved."""
    var_map = _extract_var_suffixes(text)
    suffixes: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        arg = _find_quoted_source_argument(line)
        if arg is None:
            continue
        bare = _BARE_VAR_RE.match(arg)
        suffix = var_map.get(bare.group(1)) if bare else _strip_to_literal_suffix(arg)
        if suffix:
            suffixes.append(suffix)
    return suffixes


def build_source_graph(scripts_dir: Path, files: list[Path]) -> dict[Path, set[Path]]:
    """Return forward source edges: file -> set of files it sources.

    Only edges that resolve to a real, already-discovered file are kept
    (see ``_strip_to_literal_suffix`` for why an unresolvable ``source``
    argument is dropped rather than guessed).
    """
    file_set = set(files)
    graph: dict[Path, set[Path]] = {f: set() for f in files}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for suffix in _extract_source_suffixes(text):
            candidate = (f.parent / suffix).resolve()
            if candidate in file_set:
                graph[f].add(candidate)
    return graph


def compute_ancestors_or_self(graph: dict[Path, set[Path]]) -> dict[Path, set[Path]]:
    """For each node, the set of nodes (including itself) that can reach it
    via forward ``source`` edges -- i.e. every file whose own execution
    could load this file into the same bash process."""
    reverse: dict[Path, set[Path]] = {n: set() for n in graph}
    for u, targets in graph.items():
        for v in targets:
            reverse[v].add(u)

    ancestors: dict[Path, set[Path]] = {}
    for start in graph:
        seen = {start}
        queue: deque[Path] = deque([start])
        while queue:
            cur = queue.popleft()
            for parent in reverse.get(cur, ()):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        ancestors[start] = seen
    return ancestors


# ---------------------------------------------------------------------------
# CHECK 1: engine size
# ---------------------------------------------------------------------------


def check_engine_sizes(
    scripts_dir: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    allow_files: frozenset[str] | None = None,
    verbose: bool = False,
) -> dict[str, int]:
    """Return manifest entries that exceed *max_lines*, mapped to their
    actual line count.

    A manifest entry that does not exist on disk under *scripts_dir* is
    silently skipped (see module docstring). An entry whose relative path
    or basename is in *allow_files* is skipped regardless of size.
    """
    if allow_files is None:
        allow_files = DEFAULT_SIZE_ALLOW_FILES

    violations: dict[str, int] = {}
    for rel in ENGINE_FILES:
        if rel in allow_files or Path(rel).name in allow_files:
            if verbose:
                print(f"  size: allow-list: skipping {rel}", flush=True)
            continue
        path = scripts_dir / rel
        if not path.is_file():
            if verbose:
                print(f"  size: skip (not found): {rel}", flush=True)
            continue
        line_count = sum(1 for _ in path.read_text(encoding="utf-8", errors="replace").splitlines())
        if verbose:
            print(f"  size: {rel} -- {line_count} lines", flush=True)
        if line_count > max_lines:
            violations[rel] = line_count
    return violations


# ---------------------------------------------------------------------------
# CHECK 2: tree-wide, reachability-aware function-name duplication
# ---------------------------------------------------------------------------


def find_duplicate_functions(
    scripts_dir: Path,
    allow_files: frozenset[str] | None = None,
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Scan every ``*.sh`` file under *scripts_dir* for function names
    defined in two or more files that could be loaded into the same bash
    process (see module docstring's "CHECK 2" section for the reachability
    model).

    Returns a dict mapping each violating function name to the sorted list
    of colliding files' paths, relative to *scripts_dir*. Functions defined
    in only one (non-allow-listed) file, or whose defining files are never
    reachable from a common ancestor, are not included.

    Raises FileNotFoundError if *scripts_dir* does not exist.
    """
    if allow_files is None:
        allow_files = _DEFAULT_ALLOW_FILES

    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"scripts directory not found: {scripts_dir}")

    files = find_shell_files(scripts_dir)

    func_to_files: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        if verbose:
            print(f"  dupe: scanning {f}", flush=True)
        try:
            names = find_functions_in_file(f)
        except OSError as exc:
            print(f"WARNING: could not read {f}: {exc}", file=sys.stderr)
            continue
        for name in names:
            func_to_files[name].append(f)

    graph = build_source_graph(scripts_dir, files)
    ancestors = compute_ancestors_or_self(graph)

    violations: dict[str, list[str]] = {}
    for name, defining_files in func_to_files.items():
        # De-duplicate while preserving order, then drop allow-listed files
        # entirely -- an allow-listed file's own definitions never count
        # toward a violation, regardless of what else defines the same
        # name (this is what makes wake/harness.sh's deliberate shadowing
        # of lib/worktree.sh's create_detached_worktree/teardown_task_
        # worktree fully exempt without also exempting worktree.sh itself,
        # which remains real production code everywhere else it is used).
        seen_files: list[Path] = []
        for f in defining_files:
            if f not in seen_files:
                seen_files.append(f)
        candidates = [f for f in seen_files if f.name not in allow_files]
        if verbose and len(seen_files) != len(candidates):
            print(
                f"  dupe: allow-list: excluding "
                f"{', '.join(sorted(f.name for f in seen_files if f not in candidates))} "
                f"from '{name}' candidacy",
                flush=True,
            )
        if len(candidates) < 2:
            continue

        colliding: set[Path] = set()
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = candidates[i], candidates[j]
                if ancestors[a] & ancestors[b]:
                    colliding.add(a)
                    colliding.add(b)

        if colliding:
            violations[name] = sorted(
                str(p.relative_to(scripts_dir)) for p in colliding
            )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_engine_size_dupe.py",
        description=(
            "Quality-class lint: warns when an engine-decomposition file "
            "exceeds the line ceiling, or when a shell function name is "
            "defined in two or more files reachable from a common bash "
            "process. Never exits fatally on a violation from the caller's "
            "perspective when wrapped through gated_runner_run_quality_lint."
        ),
    )
    parser.add_argument(
        "--scripts-dir",
        metavar="PATH",
        help=(
            "Directory to scan (default: team/scripts/ resolved from this "
            "script's own location)."
        ),
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_LINES,
        help=f"Engine-size ceiling in lines (default: {DEFAULT_MAX_LINES}).",
    )
    parser.add_argument(
        "--allow-file",
        metavar="FILENAME",
        action="append",
        dest="allow_files",
        help=(
            "Basename whose functions are exempt from the duplication "
            "check. May be repeated. Passing this flag once REPLACES the "
            "defaults; list all desired files when using this flag. "
            "Default: provider_claude.sh, provider_codex.sh, harness.sh."
        ),
    )
    parser.add_argument(
        "--size-allow-file",
        metavar="ENTRY",
        action="append",
        dest="size_allow_files",
        help=(
            "Manifest entry (relative path or basename) exempt from the "
            "size ceiling. May be repeated."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-file scan detail for both checks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Returns 0 (clean) or 2 (usage: --scripts-dir not found) unconditionally.
    A violation returns one of two distinct nonzero codes so a caller can
    tell which class fired without re-parsing the printed findings:

      1  a cross-file function-name collision (CHECK 2) was found -- real
         damage (bash's last-sourced-wins semantics silently shadow an
         implementation), a hard failure regardless of what else ran.
      3  only the size ceiling (CHECK 1) was exceeded -- an advisory
         reading, never evidence of breakage
         (docs/blocking-site-census.md row 53: CONVERT).

    ``gated_runner_run_quality_lint`` already discards this process's exit
    code and records a quality finding on any nonzero value, so the split
    changes nothing about the gated runners; it exists for direct callers
    -- notably this module's own test suite -- that need to tell the two
    classes apart.
    """
    args = _parse_args(argv)

    if args.scripts_dir:
        scripts_dir = Path(args.scripts_dir).resolve()
    else:
        scripts_dir = Path(__file__).resolve().parent  # team/scripts/

    if not scripts_dir.is_dir():
        print(
            f"ERROR: scripts directory not found: {scripts_dir}\n"
            "Use --scripts-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    dupe_allow: frozenset[str] = (
        frozenset(args.allow_files) if args.allow_files is not None else _DEFAULT_ALLOW_FILES
    )
    size_allow: frozenset[str] = (
        frozenset(args.size_allow_files)
        if args.size_allow_files is not None
        else DEFAULT_SIZE_ALLOW_FILES
    )

    if args.verbose:
        print(f"lint_engine_size_dupe: scanning {scripts_dir}", flush=True)
        print(f"  duplication allow-list: {', '.join(sorted(dupe_allow)) or '(none)'}", flush=True)
        print(f"  size allow-list: {', '.join(sorted(size_allow)) or '(none)'}", flush=True)

    size_violations = check_engine_sizes(
        scripts_dir, max_lines=args.max_lines, allow_files=size_allow, verbose=args.verbose
    )

    try:
        dupe_violations = find_duplicate_functions(
            scripts_dir, allow_files=dupe_allow, verbose=args.verbose
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not size_violations and not dupe_violations:
        print(
            f"lint_engine_size_dupe: ok -- {len(ENGINE_FILES)} engine file(s) "
            f"within the {args.max_lines}-line ceiling, no cross-file "
            "function-name collisions found.",
            flush=True,
        )
        return 0

    if size_violations:
        print(
            f"\nlint_engine_size_dupe: FAIL -- {len(size_violations)} engine "
            f"file(s) exceed the {args.max_lines}-line ceiling:\n",
            flush=True,
        )
        for rel, count in sorted(size_violations.items()):
            print(f"  {rel} -- {count} lines (ceiling: {args.max_lines})", flush=True)
        print(
            "\nEach engine file must stay a thin driver or a single-concern "
            "lib stage. Fix: split the file further, or add it to the "
            "manifest allow-list (--size-allow-file) if this growth is a "
            "deliberate composition table or provider-interface exception.",
            flush=True,
        )

    if dupe_violations:
        print(
            f"\nlint_engine_size_dupe: FAIL -- {len(dupe_violations)} function "
            "name(s) defined in files that share a bash process:\n",
            flush=True,
        )
        for name, files in sorted(dupe_violations.items()):
            print(f"  {name}() -- defined in: {', '.join(files)}", flush=True)
        print(
            "\nEach function name must be defined in exactly one file within "
            "any given source-reachability set. Fix: remove or rename the "
            "duplicate definition, or add the defining file to the "
            "duplication allow-list (--allow-file) if this is an intentional "
            "provider-interface or test-stub override.",
            flush=True,
        )

    # Precompute the dual-channel message/remedy text (kept out of the
    # refusal's own backward window below, which only needs the
    # classification comment and the emitter call itself close together).
    _finding_message = (
        "the engine-decomposition size/duplication lint found "
        f"{len(size_violations)} oversized engine file(s) and "
        f"{len(dupe_violations)} cross-file function-name collision(s); see "
        "the findings printed above for the exact files and names"
    )
    _finding_remedy = (
        "split the named file further (size) or rename/remove the "
        "duplicate definition (dupe), or run with --verbose for per-file "
        "detail; this is a non-blocking quality finding and does not fail "
        "the gated runner"
    )
    # CENSUS-ANCHOR: engine-size-dupe-01
    # CONVERT (SOP-core.md "Gates and Refusals" / CODER.md "Authoring a
    # new gate"): a quality-class finding with zero runtime-failure risk
    # -- gated_runner_run_quality_lint always returns 0 regardless of
    # this exit code, so the finding prints in full but never fails the runner.
    emit_error_contract("lint.engine-size-dupe", _finding_message, _finding_remedy)
    # Duplication is the hard class: return 1 whenever it fired, even
    # alongside a size finding, so a caller that treats 1 as fatal is never
    # blind to real damage riding along with an advisory reading. A
    # size-only run returns the distinct code 3 (see main()'s docstring).
    if dupe_violations:
        return 1
    return 3


if __name__ == "__main__":
    sys.exit(main())
