#!/usr/bin/env python3
"""
lint_engine_size_dupe.py
=========================
Quality-class lint (never blocks a runner, always exits 0 on findings)
that guards the engine decomposition's two structural invariants, and
reports the Python copy a shared helper was meant to replace:

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
  3. **Python def duplication** -- no module-level Python function may be
     defined, under the same name and with the same body, in two or more
     modules of the package: the second copy is a helper that belongs in
     one module and is imported.

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
both gated runners, which merge its per-instance findings with
``gated_runner_merge_findings_file``, so a regression prints in full and is
recorded but never fails the suite.

CHECK 1: ENGINE SIZE
---------------------
Scope is deliberately an explicit, named manifest (``ENGINE_FILES`` below)
of the exact drivers and lib stages the engine decomposition produced
or reduced -- NOT a blanket "every file under team/scripts/ must
stay under 800 lines" policy, and nothing outside ``--scripts-dir`` is
checked at all. Files over 800 lines that are not in the manifest are out
of scope for this regression guard; the known large ones, and why each
sits outside it:

  - ``team/pm-agent/pm_materialize.py`` -- the PM plan materializer, the
    largest module in the tree; it lives outside ``--scripts-dir`` and was
    never one of the decomposed engines, so its size is unpoliced here.
  - ``team/pgai_agent_kanban/api/routers/operations.py`` -- the REST
    mutation router, one handler per operator verb; package code outside
    ``--scripts-dir``.
  - ``team/pgai_agent_kanban/ops/write.py`` -- the ops library's write
    operations; package code outside ``--scripts-dir``.
  - ``lib/project_paths.sh``, ``lib/projects.sh``, ``lib/temp.sh`` --
    pre-existing utility libraries, never part of the five monoliths.
  - The standalone lints and test runners (``lint_*.py``,
    ``run-unit-tests.sh``, ``run-integration-tests.sh``) -- single-purpose
    tools, not engine drivers or stages.

A file in the manifest that no longer exists (renamed or deleted in a
later refactor) is silently skipped, not an error -- this lint tracks a
decomposition's shape, not a fixed set of paths that must exist forever.

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

CHECK 3: PYTHON DEF DUPLICATION
-------------------------------
Scope is every non-test ``*.py`` file under ``--python-dir`` (default: the
``pgai_agent_kanban/`` package beside ``--scripts-dir``, skipped when there
is none), recursive; a directory named ``tests``, ``test`` or
``__pycache__`` is left out. Only module-level ``def``/``async def`` count,
not methods or nested functions. Two definitions are the same when they
share a name and their bodies normalise to the same hash under
``lint_python_duplicates.py``'s ``body_digest`` (docstring dropped,
parameters renamed by position, formatting and comments ignored) -- the
rule is imported from that lint, never restated. A body that is only
``pass`` or ``...`` is a stub, not a copy, and is skipped. One finding is
recorded per name and body shared by two or more modules. A file that does
not parse is warned about on stderr and skipped.

``lint_python_duplicates.py`` ratchets large copied bodies under any name
against an allowlist; this check has no allowlist and no size floor, so a
copied one-line helper is reported on every run until it is replaced by an
import.

ALLOW-LIST
----------
``--allow-file`` exempts a shell file (by basename) from the CHECK 2
duplication check entirely -- its own function definitions never count
toward a violation, regardless of what the reachability graph says.
Default allow-list:

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
function_dedupe.py`` itself still exists -- it keeps its
own passing test suite, its own census row (row 40), and its one remaining
caller, ``audit_coding_standards.sh``'s D01 check (a manually-run
compliance audit, not a per-run quality gate) -- rather than being deleted
outright, which would have required rewriting that audit script, its
census row, and its dedicated test file for no behavioral gain. Running
two independent scanners for the *same* concern inside the gated-runner
quality-lint pipeline is exactly the duplication this lint exists to catch;
keeping the narrower tool alive for its own separate, non-gated audit
surface is not that.

FINDINGS
--------
CONVERT (docs/blocking-site-census.md row 53; team/SOP-core.md "Blocking
Severity"): a size reading is advisory, and a collision is a latent
shadowing hazard the run that finds it does not suffer, as is a copied
Python helper. Each oversized engine file, each colliding shell function
name and each copied Python def is printed and recorded as one finding,
whose ``kind`` tells the classes apart without re-parsing the output:
``engine-size`` (``file``, ``lines``, ``ceiling``), ``duplicate-function``
(``function``, ``files``) or ``duplicate-python-def`` (``function``,
``files``). With --findings-file the findings are written as a JSON array
(the shape pgai_agent_kanban.lint.findings defines); the gated runners
merge it per instance. The pre-merge engine's engine-size ratchet fails on
an ``engine-size`` or ``duplicate-function`` entry and narrates a
``duplicate-python-def`` one without failing.

EXIT CODES
----------
  0   Scan ran, whatever it found (every class is a finding).
  2   Usage error, or --scripts-dir (or a --python-dir passed) does not
      exist.

USAGE
-----
  python3 team/scripts/lint_engine_size_dupe.py [--scripts-dir PATH]
                                                 [--python-dir PATH]
                                                 [--max-lines N]
                                                 [--allow-file FILENAME]
                                                 [--size-allow-file ENTRY]
                                                 [--findings-file PATH]
                                                 [--verbose]

  --scripts-dir PATH     Directory to scan (default: team/scripts/ resolved
                          from this script's own location).
  --python-dir PATH      Python package to scan for copied defs (default:
                          pgai_agent_kanban/ beside --scripts-dir, skipped
                          when absent).
  --max-lines N          Engine-size ceiling in lines (default: 800).
  --allow-file FILENAME  Basename whose shell functions are exempt from
                          the duplication check. May be repeated. Replaces the
                          default allow-list when passed at least once.
  --size-allow-file ENTRY
                          Manifest entry (relative path or basename) exempt
                          from the size ceiling. May be repeated.
  --findings-file PATH   Write the findings as a JSON array to PATH (parent
                          directories created; written even when empty).
  --verbose, -v          Print per-file scan detail for both checks.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_engine_size_dupe.py

  # Explicit scripts dir (e.g. a seeded test fixture):
  python3 team/scripts/lint_engine_size_dupe.py --scripts-dir /path/to/fixture

  # Explicit Python package to scan for copied defs:
  python3 team/scripts/lint_engine_size_dupe.py \\
      --python-dir team/pgai_agent_kanban

  # Custom duplication allow-list (replaces defaults):
  python3 team/scripts/lint_engine_size_dupe.py \\
      --allow-file provider_claude.sh \\
      --allow-file provider_codex.sh

  # Record findings for a downstream merge:
  python3 team/scripts/lint_engine_size_dupe.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/engine_size_dupe.json"
"""

from __future__ import annotations

import argparse
import ast
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
from pgai_agent_kanban.lint.findings import (  # noqa: E402
    display_path,
    quality_finding,
    write_findings_file,
)

# The body normaliser and the non-test file walk live in
# lint_python_duplicates.py beside this file; they are imported, never
# copied, so the two lints agree on what "the same body" means.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import lint_python_duplicates as _python_dupes  # noqa: E402

_LINT_NAME = "engine_size_dupe"

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
    # VERSION-file stage release_notes.sh calls, the --decide-only
    # stage, and the squash-conflict / push-lag / tag-on-remote stage
    # release_squash.sh and release_finalize.sh call).
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
    "lib/cm/release_resolve.sh",
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
    "lib/wake_agent_block_scan.sh",  # sourced by wake_driver_finalize.sh
    "lib/wake_driver_task_setup.sh",
    "lib/wake_driver_discovery.sh",
    "lib/wake_driver_idle.sh",
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
# CHECK 3: Python def duplication
# ---------------------------------------------------------------------------

#: The package scanned by default, as a sibling of --scripts-dir.
DEFAULT_PYTHON_PACKAGE = "pgai_agent_kanban"


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when *fn*'s body, docstring aside, is only ``pass`` or ``...``."""
    return all(
        isinstance(stmt, ast.Pass)
        or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and (stmt.value.value is Ellipsis or isinstance(stmt.value.value, str))
        )
        for stmt in fn.body
    )


def find_duplicate_python_defs(
    python_dir: Path,
    verbose: bool = False,
) -> list[tuple[str, list[str]]]:
    """Scan every non-test ``*.py`` file under *python_dir* for module-level
    functions defined in two or more modules under the same name with the
    same normalised body (see the module docstring's "CHECK 3" section).

    Returns one ``(name, files)`` pair per copied definition, *files* being
    the sorted paths of the defining modules relative to *python_dir*;
    pairs are sorted by name, then files. Two different bodies sharing a
    name are two pairs only when each has its own copy.

    Raises FileNotFoundError if *python_dir* does not exist.
    """
    if not python_dir.is_dir():
        raise FileNotFoundError(f"python directory not found: {python_dir}")

    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path in _python_dupes.iter_scan_files(python_dir):
        if verbose:
            print(f"  python: scanning {path}", flush=True)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except (OSError, SyntaxError, ValueError) as exc:
            print(f"WARNING: could not parse {path}: {exc}", file=sys.stderr)
            continue
        rel = path.relative_to(python_dir).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or _is_stub(node):
                continue
            digest = _python_dupes.body_digest(node, 1)
            if digest is not None:
                groups[(node.name, digest)].add(rel)

    return sorted(
        (name, sorted(files)) for (name, _digest), files in groups.items() if len(files) > 1
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_engine_size_dupe.py",
        description=(
            "Quality-class lint: warns when an engine-decomposition file "
            "exceeds the line ceiling, when a shell function name is "
            "defined in two or more files reachable from a common bash "
            "process, or when a module-level Python function is defined "
            "with the same name and body in two or more modules. Each is a "
            "finding; the lint exits 0 whatever it finds (2 on a usage error)."
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
        "--python-dir",
        metavar="PATH",
        help=(
            "Python package to scan for module-level functions copied under "
            f"the same name (default: {DEFAULT_PYTHON_PACKAGE}/ beside "
            "--scripts-dir, skipped when absent; a PATH passed must exist)."
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
            "Basename whose shell functions are exempt from the duplication "
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
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created; written even when empty). Findings are printed either way."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-file scan detail for every check.",
    )
    return parser.parse_args(argv)


def _findings(
    size_violations: dict[str, int],
    dupe_violations: dict[str, list[str]],
    *,
    scripts_dir: Path,
    max_lines: int,
    python_dupes: list[tuple[str, list[str]]] | None = None,
    python_dir: Path | None = None,
) -> list[dict]:
    """One quality finding per oversized engine file (``kind``
    ``engine-size``), per colliding function name (``kind``
    ``duplicate-function``) and per copied Python def (``kind``
    ``duplicate-python-def``). Each remedy names the exact files and ends
    with the re-check: how to split a file or which definition survives is
    a design choice no command can make."""
    repo_root = _TEAM_DIR.parent
    relint = f'python3 "{Path(__file__).resolve()}" --scripts-dir "{scripts_dir}"'
    if python_dir is not None:
        relint += f' --python-dir "{python_dir}"'
    findings = []
    for rel, count in sorted(size_violations.items()):
        path = display_path(scripts_dir / rel, repo_root)
        findings.append(
            quality_finding(
                _LINT_NAME,
                f"{path} is {count} lines, over the {max_lines}-line engine ceiling",
                f"split {path} into single-concern lib stages of at most {max_lines} "
                f"lines each, or exempt it with --size-allow-file {rel} if it is a "
                f"composition table, then re-check: {relint}",
                file=path,
                kind="engine-size",
                lines=count,
                ceiling=max_lines,
            )
        )
    for name, files in sorted(dupe_violations.items()):
        paths = [display_path(scripts_dir / rel, repo_root) for rel in files]
        findings.append(
            quality_finding(
                _LINT_NAME,
                f"{name}() is defined in {len(paths)} files that share a bash process: "
                f"{', '.join(paths)}",
                f"keep one {name}() definition and remove or rename it in "
                f"{', '.join(paths[1:])}, then re-check: {relint}",
                file=paths[0],
                kind="duplicate-function",
                function=name,
                files=paths,
            )
        )
    for name, files in python_dupes or ():
        paths = [display_path(python_dir / rel, repo_root) for rel in files]
        findings.append(
            quality_finding(
                _LINT_NAME,
                f"{name}() is defined with the same body in {len(paths)} modules: {', '.join(paths)}",
                f"keep one {name}() (in a lib/ module when several packages use it), import it in place "
                f"of the copy in {', '.join(paths[1:])}, then re-check: {relint}",
                file=paths[0],
                kind="duplicate-python-def",
                function=name,
                files=paths,
            )
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Returns 0 whatever was found, or 2 on a usage error (--scripts-dir, or
    a --python-dir passed, not found). Each oversized engine file, each
    cross-file function-name collision and each copied Python def is a
    quality finding, printed and written to --findings-file; a caller tells
    the classes apart by each finding's ``kind`` (``engine-size``,
    ``duplicate-function`` or ``duplicate-python-def``), not by the exit
    code.
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

    if args.python_dir:
        python_dir: Path | None = Path(args.python_dir).resolve()
        if not python_dir.is_dir():
            print(
                f"ERROR: python directory not found: {python_dir}\n"
                "Pass an existing package directory to --python-dir, or omit it.",
                file=sys.stderr,
            )
            return 2
    else:
        python_dir = scripts_dir.parent / DEFAULT_PYTHON_PACKAGE
        if not python_dir.is_dir():
            python_dir = None

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
        print(f"  python package: {python_dir or '(none beside the scripts dir)'}", flush=True)

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

    python_dupes = find_duplicate_python_defs(python_dir, verbose=args.verbose) if python_dir else []

    findings = _findings(
        size_violations, dupe_violations, scripts_dir=scripts_dir, max_lines=args.max_lines,
        python_dupes=python_dupes, python_dir=python_dir,
    )
    write_findings_file(args.findings_file, findings, lint="lint_engine_size_dupe")

    if not size_violations and not dupe_violations and not python_dupes:
        print(
            f"lint_engine_size_dupe: ok -- {len(ENGINE_FILES)} engine file(s) "
            f"within the {args.max_lines}-line ceiling, no cross-file "
            "function-name collisions"
            + (", no copied Python defs" if python_dir else "")
            + " found.",
            flush=True,
        )
        return 0

    if size_violations:
        print(
            f"\nlint_engine_size_dupe: {len(size_violations)} engine "
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
            f"\nlint_engine_size_dupe: {len(dupe_violations)} function "
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

    if python_dupes:
        print(
            f"\nlint_engine_size_dupe: {len(python_dupes)} Python function(s) "
            "defined with the same name and body in two or more modules:\n",
            flush=True,
        )
        for name, files in python_dupes:
            print(f"  {name}() -- defined in: {', '.join(files)}", flush=True)
        print(
            "\nA helper lives in one module and every caller imports it. Fix: "
            "keep one definition (in a lib/ module when several packages use "
            "it) and import it in place of each copy.",
            flush=True,
        )

    # Precompute the dual-channel message/remedy text (kept out of the
    # refusal's own backward window below, which only needs the
    # classification comment and the emitter call itself close together).
    _finding_message = (
        "the engine-decomposition size/duplication lint found "
        f"{len(size_violations)} oversized engine file(s), "
        f"{len(dupe_violations)} cross-file function-name collision(s) and "
        f"{len(python_dupes)} copied Python def(s); see "
        "the findings printed above for the exact files and names"
    )
    # CENSUS-ANCHOR: engine-size-dupe-01
    # CONVERT (SOP-core.md "Gates and Refusals" / CODER.md "Authoring a
    # new gate"): a quality-class finding with zero runtime-failure risk
    # -- recorded per instance as findings; the lint returns 0 below.
    emit_error_contract("lint.engine-size-dupe", _finding_message, findings[0]["remedy"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
