"""
test_debug_log_routing.py
==========================
Behavioral fixtures for an earlier defect: debug-log routing per project context.

When a wake dispatch carries project context (the normal multi-project path),
debug logs must land under ``projects/<name>/logs/debug/<agent>.log`` — not
under the root ``logs/debug/`` tier.  Root ``logs/debug/`` is reserved for
genuinely project-less invocations only.

Acceptance criteria exercised:

  (1) Project-scoped routing: a project-dispatched debug-log write lands
      under ``projects/<name>/logs/debug/<agent>.log``, and root
      ``logs/debug/`` gains NO new files during the run (mtime assertion
      on both paths).

  (2) Project-less routing: when no project context is present, the write
      lands in root ``logs/debug/<agent>.log`` (reserved tier proven live).

  (3) Sibling parity: the debug-log routing block is byte-identical across
      ``scripts/wake/claude.sh`` and ``scripts/wake/codex.sh``.  The
      routing block is identified by a known comment anchor, extracted from
      each sibling, and compared.

All tests use synthetic environments and never touch the live kanban root.
The routing logic is exercised via a self-contained bash snippet that mirrors
the exact logic from the production wake scripts.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths relative to team/ (pytest cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")

# Anchor comment that begins the debug-log routing block in both siblings.
_ROUTING_ANCHOR = "# --- Per-agent debug log path"

# The routing logic extracted as a template, wrapped in a function so that
# the ``local`` keyword (used in the production code) is valid.
# Mirrors the exact logic from the production wake scripts.
_ROUTING_SNIPPET_FUNC = textwrap.dedent("""\
    _run_routing() {
      local _proj_debug_root="${KANBAN_ROOT}/logs/debug"
      if [[ -n "${_CURRENT_PROJECT:-}" ]]; then
        _proj_debug_root="${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug"
      fi
      local task_debug_log
      case "$SUBAGENT" in
        pm)       task_debug_log="${_proj_debug_root}/pm.log" ;;
        po)       task_debug_log="${_proj_debug_root}/po.log" ;;
        coder)    task_debug_log="${_proj_debug_root}/coder.log" ;;
        writer)   task_debug_log="${_proj_debug_root}/writer.log" ;;
        tester)   task_debug_log="${_proj_debug_root}/tester.log" ;;
        cm)       task_debug_log="${_proj_debug_root}/cm.log" ;;
        overwatch) task_debug_log="${_proj_debug_root}/overwatch.log" ;;
        *)        task_debug_log="${_proj_debug_root}/${SUBAGENT}.log" ;;
      esac
      if [[ "${_debug_gate}" == "true" ]]; then
        mkdir -p "$(dirname "$task_debug_log")"
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [test_task_id] wake: dispatching to ${SUBAGENT} (role=CODER project=${_CURRENT_PROJECT:-unknown})" >> "$task_debug_log"
      fi
      # Export result for caller to inspect.
      RESOLVED_DEBUG_LOG="$task_debug_log"
    }
    _run_routing
""")


# ---------------------------------------------------------------------------
# Helper: extract the debug-routing block from a wake sibling.
# Extraction starts at the anchor comment and runs until the closing 'fi'
# of the _debug_gate guard block (which is the last line of the routing block).
# ---------------------------------------------------------------------------

def _extract_routing_block(path: pathlib.Path) -> list[str]:
    """Extract the debug-log routing block from a wake provider sibling.

    The block starts at the ``# --- Per-agent debug log path`` comment and
    ends at the first top-level ``fi`` that closes the ``_debug_gate`` guard.
    Both anchor and terminal line are inclusive.

    Returns the extracted lines as a list (one element per source line).
    Raises AssertionError when the anchor is not found.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    block: list[str] = []

    for line in lines:
        if _ROUTING_ANCHOR in line:
            capturing = True
        if capturing:
            block.append(line)
            # The block ends at the closing 'fi' of the _debug_gate guard
            # (two-space indent, matching the body indentation of process_one_task).
            if line.strip() == "fi" and len(block) > 5:
                break

    return block


# ---------------------------------------------------------------------------
# (1) Project-scoped routing: write lands under projects/<name>/logs/debug/
#     and root logs/debug/ gains NO new files.
# ---------------------------------------------------------------------------


def test_project_scoped_debug_log_routes_to_project_dir(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture (1): project-dispatched debug write lands under projects/<name>/logs/debug.

    The root logs/debug/ directory must gain NO new files during the run.
    Verified via mtime assertion: a sentinel file is placed in root logs/debug/
    before the routing snippet runs; after it runs, that directory should have
    no file with a newer mtime (no write occurred at the root tier).
    """
    kanban_root = tmp_path / "kanban"
    project_name = "test-project"

    # Pre-create root logs/debug/ so its mtime is recorded BEFORE the run.
    root_debug_dir = kanban_root / "logs" / "debug"
    root_debug_dir.mkdir(parents=True, exist_ok=True)
    # Place a sentinel with a known, stable mtime.
    sentinel = root_debug_dir / ".sentinel"
    sentinel.touch()
    # Record the directory mtime before the routing snippet runs.
    mtime_before = root_debug_dir.stat().st_mtime_ns

    # Run the routing snippet with project context and gate=true.
    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export _CURRENT_PROJECT="{project_name}"
        export SUBAGENT="coder"
        export _debug_gate="true"

        {_ROUTING_SNIPPET_FUNC}

        # Emit the resolved path so the test can assert on it.
        echo "$RESOLVED_DEBUG_LOG"
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Routing snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    resolved_path = result.stdout.strip()

    # The resolved path must be under the project debug dir.
    expected_prefix = str(kanban_root / "projects" / project_name / "logs" / "debug")
    assert resolved_path.startswith(expected_prefix), (
        f"Debug log resolved to wrong path for a project-scoped task.\n"
        f"Expected path under: {expected_prefix}\n"
        f"Got: {resolved_path}"
    )

    # The log file must exist (the gate was true, so the write happened).
    expected_log = kanban_root / "projects" / project_name / "logs" / "debug" / "coder.log"
    assert expected_log.exists(), (
        f"Expected project-scoped debug log to be created at {expected_log}; "
        "it does not exist after the routing snippet ran."
    )
    assert expected_log.stat().st_size > 0, (
        f"Project-scoped debug log {expected_log} exists but is empty."
    )

    # Root logs/debug/ must have gained NO new files.
    # The directory mtime changes when a new file is created inside it.
    # Because we only pre-created the sentinel, any new file would update the mtime.
    mtime_after = root_debug_dir.stat().st_mtime_ns
    assert mtime_after == mtime_before, (
        f"Root logs/debug/ mtime changed during a project-scoped run.\n"
        f"mtime before: {mtime_before}\n"
        f"mtime after:  {mtime_after}\n"
        "A write to root logs/debug/ occurred when it should not have."
    )


def test_project_scoped_root_debug_dir_gains_no_new_files(
    tmp_path: pathlib.Path,
) -> None:
    """Supplemental check: no files are created at root logs/debug/ for project runs.

    Enumerates root logs/debug/ before and after and asserts the set of files
    is unchanged (complementary to the mtime check above).
    """
    kanban_root = tmp_path / "kanban"
    project_name = "alpha"

    root_debug_dir = kanban_root / "logs" / "debug"
    root_debug_dir.mkdir(parents=True, exist_ok=True)
    # Capture the file set before the snippet runs.
    files_before = set(root_debug_dir.iterdir())

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export _CURRENT_PROJECT="{project_name}"
        export SUBAGENT="writer"
        export _debug_gate="true"

        {_ROUTING_SNIPPET_FUNC}
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Routing snippet failed: {result.stderr}"
    )

    files_after = set(root_debug_dir.iterdir())
    new_files = files_after - files_before
    assert not new_files, (
        f"Root logs/debug/ gained new file(s) during a project-scoped run:\n"
        + "\n".join(str(f) for f in sorted(new_files))
    )


# ---------------------------------------------------------------------------
# (2) Project-less routing: write lands in root logs/debug/ (reserved tier).
# ---------------------------------------------------------------------------


def test_project_less_debug_log_routes_to_root_dir(
    tmp_path: pathlib.Path,
) -> None:
    """Fixture (2): a project-less invocation writes root logs/debug/<agent>.log.

    When _CURRENT_PROJECT is unset (empty), the reserved root tier must receive
    the write.  This proves the root tier is live and correctly targeted for
    genuinely project-less contexts (e.g. pm-hallucination-capture.sh).
    """
    kanban_root = tmp_path / "kanban"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        # _CURRENT_PROJECT deliberately unset — simulate project-less invocation.
        unset _CURRENT_PROJECT
        export SUBAGENT="pm"
        export _debug_gate="true"

        {_ROUTING_SNIPPET_FUNC}

        echo "$RESOLVED_DEBUG_LOG"
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Routing snippet failed (exit {result.returncode}).\n"
        f"stderr: {result.stderr}"
    )

    resolved_path = result.stdout.strip()

    # The resolved path must be under root logs/debug/.
    expected_prefix = str(kanban_root / "logs" / "debug")
    assert resolved_path.startswith(expected_prefix), (
        f"Project-less debug log was NOT routed to root logs/debug/.\n"
        f"Expected path under: {expected_prefix}\n"
        f"Got: {resolved_path}"
    )
    # Must NOT be under any projects/ subdirectory.
    assert "/projects/" not in resolved_path, (
        f"Project-less debug log was incorrectly routed to a project directory: "
        f"{resolved_path}"
    )

    # The log file must exist at the root tier.
    expected_log = kanban_root / "logs" / "debug" / "pm.log"
    assert expected_log.exists(), (
        f"Root-tier debug log must be created at {expected_log} for a "
        "project-less invocation; it does not exist."
    )
    assert expected_log.stat().st_size > 0, (
        f"Root-tier debug log {expected_log} exists but is empty."
    )


def test_project_less_empty_string_routes_to_root_dir(
    tmp_path: pathlib.Path,
) -> None:
    """Empty _CURRENT_PROJECT (not just unset) also routes to root tier.

    The routing guard is ``[[ -n "${_CURRENT_PROJECT:-}" ]]``, so an empty
    string is equivalent to unset — both must produce root-tier writes.
    """
    kanban_root = tmp_path / "kanban"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export _CURRENT_PROJECT=""
        export SUBAGENT="tester"
        export _debug_gate="true"

        {_ROUTING_SNIPPET_FUNC}

        echo "$RESOLVED_DEBUG_LOG"
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Routing snippet failed: {result.stderr}"
    )

    resolved_path = result.stdout.strip()
    expected_prefix = str(kanban_root / "logs" / "debug")
    assert resolved_path.startswith(expected_prefix), (
        f"Empty _CURRENT_PROJECT should route to root logs/debug/.\n"
        f"Expected prefix: {expected_prefix}\n"
        f"Got: {resolved_path}"
    )


# ---------------------------------------------------------------------------
# Gate disabled: when _debug_gate is false, no files are created at either tier.
# ---------------------------------------------------------------------------


def test_gate_false_no_files_created(tmp_path: pathlib.Path) -> None:
    """When the debug gate is false, neither tier receives a write.

    The gate guards all disk I/O in the routing block, so a false gate must
    leave both root logs/debug/ and any project logs/debug/ untouched.
    """
    kanban_root = tmp_path / "kanban"
    project_name = "gated-project"

    script = textwrap.dedent(f"""\
        set -euo pipefail
        export KANBAN_ROOT="{kanban_root}"
        export _CURRENT_PROJECT="{project_name}"
        export SUBAGENT="coder"
        export _debug_gate="false"

        {_ROUTING_SNIPPET_FUNC}
    """)

    result = run_bash(tmp_path, script, timeout=10)
    assert result.returncode == 0, (
        f"Routing snippet failed: {result.stderr}"
    )

    root_debug = kanban_root / "logs" / "debug"
    proj_debug = kanban_root / "projects" / project_name / "logs" / "debug"

    assert not root_debug.exists() or list(root_debug.iterdir()) == [], (
        f"Root debug dir received files when gate was false: "
        f"{list(root_debug.iterdir())}"
    )
    assert not proj_debug.exists() or list(proj_debug.iterdir()) == [], (
        f"Project debug dir received files when gate was false: "
        f"{list(proj_debug.iterdir())}"
    )


# ---------------------------------------------------------------------------
# (3) Sibling parity: debug routing block is byte-identical in both siblings.
# ---------------------------------------------------------------------------


def test_sibling_debug_routing_blocks_are_byte_identical() -> None:
    """Fixture (3): the debug-log routing block must be byte-identical across
    scripts/wake/claude.sh and scripts/wake/codex.sh.

    Extraction: locate the ``# --- Per-agent debug log path`` comment in each
    file and capture through the closing ``fi`` of the ``_debug_gate`` guard.
    The two blocks must match byte-for-byte.
    """
    claude_block = _extract_routing_block(_CLAUDE_SH)
    codex_block = _extract_routing_block(_CODEX_SH)

    assert claude_block, (
        f"Debug routing block not found in {_CLAUDE_SH}. "
        f"Expected a comment containing '{_ROUTING_ANCHOR}'."
    )
    assert codex_block, (
        f"Debug routing block not found in {_CODEX_SH}. "
        f"Expected a comment containing '{_ROUTING_ANCHOR}'."
    )
    assert claude_block == codex_block, (
        "Debug routing blocks differ between siblings.\n"
        "claude.sh block:\n" + "\n".join(claude_block) + "\n\n"
        "codex.sh block:\n" + "\n".join(codex_block)
    )


def test_sibling_debug_routing_anchor_present_in_claude() -> None:
    """Structural: the debug routing anchor comment is present in claude.sh."""
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert _ROUTING_ANCHOR in content, (
        f"Debug routing anchor '{_ROUTING_ANCHOR}' not found in {_CLAUDE_SH}. "
        "The per-project debug log routing block must be present."
    )


def test_sibling_debug_routing_anchor_present_in_codex() -> None:
    """Structural: the debug routing anchor comment is present in codex.sh."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert _ROUTING_ANCHOR in content, (
        f"Debug routing anchor '{_ROUTING_ANCHOR}' not found in {_CODEX_SH}. "
        "The per-project debug log routing block must be present."
    )


def test_sibling_debug_routing_project_condition_present_in_claude() -> None:
    """Structural: the project-context condition is present in claude.sh.

    The routing guard ``if [[ -n "${_CURRENT_PROJECT:-}" ]]`` must appear in
    the routing block so the per-project path switch is actually tested.
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert '_CURRENT_PROJECT' in content, (
        f"_CURRENT_PROJECT reference not found in {_CLAUDE_SH}. "
        "The routing block must check this variable for per-project path selection."
    )


def test_sibling_debug_routing_project_condition_present_in_codex() -> None:
    """Structural: the project-context condition is present in codex.sh."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert '_CURRENT_PROJECT' in content, (
        f"_CURRENT_PROJECT reference not found in {_CODEX_SH}. "
        "The routing block must check this variable for per-project path selection."
    )


def test_sibling_routing_uses_project_debug_subpath_in_claude() -> None:
    """Structural: claude.sh routes to projects/<name>/logs/debug when project is set.

    The routing code must contain the path template
    ``${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug`` — not merely a
    generic ``/logs/debug`` or root-only path.
    """
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    expected_fragment = "projects/${_CURRENT_PROJECT}/logs/debug"
    assert expected_fragment in content, (
        f"claude.sh does not contain the per-project debug path template.\n"
        f"Expected: {expected_fragment!r}\n"
        "The routing block must construct the per-project path when _CURRENT_PROJECT is set."
    )


def test_sibling_routing_uses_project_debug_subpath_in_codex() -> None:
    """Structural: codex.sh routes to projects/<name>/logs/debug when project is set."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    expected_fragment = "projects/${_CURRENT_PROJECT}/logs/debug"
    assert expected_fragment in content, (
        f"codex.sh does not contain the per-project debug path template.\n"
        f"Expected: {expected_fragment!r}\n"
        "The routing block must construct the per-project path when _CURRENT_PROJECT is set."
    )
