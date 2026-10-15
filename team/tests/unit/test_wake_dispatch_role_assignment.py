"""
test_wake_dispatch_role_assignment.py
======================================
Behavioral fixtures for wake dispatch's task-role selection.

Wake dispatch reads a task's ## Role and ## Assigned Agent fields to decide
which subagent executes a task. Two properties are exercised here:

  (1) Assigned Agent, when present and valid, selects the executing
      subagent even when it differs from Role (Role remains the capability
      descriptor, not the dispatch key). A task queued under one agent's
      backlog but carrying an Assigned Agent for a different, matching
      agent dispatches to that agent's subagent — never silently to coder.

  (2) A missing or invalid ## Role never falls back to coder. Dispatch
      blocks the task instead, naming the reason, and performs no subagent
      invocation.

  (3) An Assigned Agent that names an unrecognized role, or that does not
      match the queue the task was pulled from, also blocks dispatch
      rather than guessing which agent should run it.

  (4) A normal, well-formed task (no Assigned Agent, valid Role matching
      the queue it was pulled from) dispatches exactly as it always has —
      no behavior change for the common case.

Two extraction levels are tested, mirroring the project's established
pattern for exercising bash logic without sourcing an entire wake script:
  - The README-parsing helpers (get_role_from_readme,
    get_assigned_agent_from_readme, role_is_valid_agent) are extracted
    from scripts/lib/wake_readme_meta.sh by function name and exercised
    directly.
  - The pre-dispatch assertion block itself is extracted from
    scripts/wake/claude.sh by its anchor comment and exercised in a
    stubbed harness that records BLOCKED-path calls (set_state, set_block,
    mark_backlog) without touching a real status.md.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_WAKE_README_META_LIB = pathlib.Path("scripts/lib/wake_readme_meta.sh")
_WAKE_TASK_STATE_LIB = pathlib.Path("scripts/lib/wake_task_state.sh")
# This pre-dispatch block now lives once in the shared wake driver's prep
# stage, sourced by both wake/claude.sh and wake/codex.sh (v2.9.20-engine-
# decomposition: process_one_task's inline body was lifted
# out of wake/claude.sh; wake/codex.sh now sources the
# same shared driver instead of carrying its own inline copy). Both
# constants below point at the one shared file the block now lives in.
_CLAUDE_SH = pathlib.Path("scripts/lib/wake_driver_prep.sh")
_CODEX_SH = pathlib.Path("scripts/lib/wake_driver_prep.sh")

# Anchor comment that begins the pre-dispatch assertion block in a wake
# provider script. Both claude.sh and codex.sh carry the identical block
# under this anchor.
_ASSERTION_ANCHOR = "# --- Pre-dispatch assertion ---"


def _awk_extract_function(name: str, source: pathlib.Path) -> str:
    """Return an awk program (as a bash command substitution) that extracts
    a single bash function definition by name from *source*, matched by
    brace depth so the extraction stops at the function's own closing brace.

    Mirrors the extraction technique already used elsewhere in this suite
    to source individual wake_common.sh functions without triggering the
    full file's module-level bootstrap side effects.
    """
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^{name}\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0; exit }}
                    }}
                }}
            }}
        ' '{source}')"
    """)


def _extract_pre_dispatch_block(path: pathlib.Path) -> list[str]:
    """Extract the pre-dispatch assertion block from a wake provider script.

    The block starts at the anchor comment and ends at the first top-level
    ``esac`` that closes the subagent-selection case statement (two-space
    indent, matching process_one_task's body indentation).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    capturing = False
    block: list[str] = []
    for line in lines:
        if _ASSERTION_ANCHOR in line:
            capturing = True
        if capturing:
            block.append(line)
            if line.strip() == "esac" and len(block) > 5:
                break
    assert block, f"Pre-dispatch assertion anchor not found in {path}"
    return block


def _write_readme(
    tmp_path: pathlib.Path,
    *,
    role: str | None,
    assigned_agent: str | None,
    name: str = "README.md",
) -> pathlib.Path:
    """Write a minimal task README with the given ## Role / ## Assigned Agent.

    role=None omits the ## Role section entirely. assigned_agent=None omits
    the ## Assigned Agent section entirely (distinct from an explicit "none").
    """
    lines = ["# Task: synthetic fixture", "", "## Task ID", "SYNTH-000-fixture", ""]
    if role is not None:
        lines += ["## Role", role, ""]
    if assigned_agent is not None:
        lines += ["## Assigned Agent", assigned_agent, ""]
    readme = tmp_path / name
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return readme


# ---------------------------------------------------------------------------
# Level 1: README-parsing helpers, exercised directly.
# ---------------------------------------------------------------------------


def _run_readme_helpers(tmp_path: pathlib.Path, readme: pathlib.Path) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {_awk_extract_function("get_role_from_readme", _WAKE_README_META_LIB)}
        {_awk_extract_function("get_assigned_agent_from_readme", _WAKE_README_META_LIB)}
        {_awk_extract_function("role_is_valid_agent", _WAKE_README_META_LIB)}

        role="$(get_role_from_readme "{readme}")"
        assigned="$(get_assigned_agent_from_readme "{readme}")"
        echo "ROLE=[${{role}}]"
        echo "ASSIGNED=[${{assigned}}]"
        role_is_valid_agent "$role" && echo "ROLE_VALID=1" || echo "ROLE_VALID=0"
    """)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=15
    )


def test_get_role_from_readme_returns_empty_when_role_section_absent(
    tmp_path: pathlib.Path,
) -> None:
    """A README with no ## Role section yields an empty role, not a default."""
    readme = _write_readme(tmp_path, role=None, assigned_agent="none")
    result = _run_readme_helpers(tmp_path, readme)
    assert result.returncode == 0, result.stderr
    assert "ROLE=[]" in result.stdout, result.stdout
    assert "ROLE_VALID=0" in result.stdout, result.stdout


def test_get_role_from_readme_returns_raw_value_when_role_invalid(
    tmp_path: pathlib.Path,
) -> None:
    """An unrecognized ## Role value is returned as-is, not normalized to CODER."""
    readme = _write_readme(tmp_path, role="NOT-A-REAL-ROLE", assigned_agent="none")
    result = _run_readme_helpers(tmp_path, readme)
    assert result.returncode == 0, result.stderr
    assert "ROLE=[NOT-A-REAL-ROLE]" in result.stdout, result.stdout
    assert "ROLE_VALID=0" in result.stdout, result.stdout


def test_get_assigned_agent_from_readme_treats_none_as_absent(
    tmp_path: pathlib.Path,
) -> None:
    """An explicit 'none' Assigned Agent value parses the same as an absent one."""
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="none")
    result = _run_readme_helpers(tmp_path, readme)
    assert result.returncode == 0, result.stderr
    assert "ASSIGNED=[]" in result.stdout, result.stdout


def test_get_assigned_agent_from_readme_returns_uppercased_value(
    tmp_path: pathlib.Path,
) -> None:
    """A present, non-none Assigned Agent value is returned uppercased."""
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="writer")
    result = _run_readme_helpers(tmp_path, readme)
    assert result.returncode == 0, result.stderr
    assert "ASSIGNED=[WRITER]" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# Level 2: the pre-dispatch assertion block itself, extracted from claude.sh.
# ---------------------------------------------------------------------------


def _run_dispatch_assertion(
    tmp_path: pathlib.Path,
    readme: pathlib.Path,
    *,
    queue_agent: str,
    source: pathlib.Path = _CLAUDE_SH,
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the extracted pre-dispatch assertion block against *readme*.

    *source* selects which wake provider script the assertion block is
    extracted from (claude.sh by default; pass _CODEX_SH to exercise the
    codex provider's copy of the same block).

    Stubs log/set_state/set_block/normalize_status_file/mark_backlog so the
    block's BLOCKED path can be observed without a real status.md. The
    block's BLOCKED write is the real block_task_with_record
    (wake_task_state.sh); its State and section writers are stubbed to the
    same SET_STATE / SET_BLOCK call-log lines. Returns the completed process
    and the recorded call-log contents.
    """
    calls_file = tmp_path / "calls.log"
    calls_file.write_text("", encoding="utf-8")

    block_lines = _extract_pre_dispatch_block(source)
    block_text = "\n".join(block_lines)

    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {_awk_extract_function("get_role_from_readme", _WAKE_README_META_LIB)}
        {_awk_extract_function("get_assigned_agent_from_readme", _WAKE_README_META_LIB)}
        {_awk_extract_function("role_is_valid_agent", _WAKE_README_META_LIB)}
        {_awk_extract_function("block_task_with_record", _WAKE_TASK_STATE_LIB)}

        CALLS_FILE="{calls_file}"
        log() {{ echo "LOG:$*" >> "$CALLS_FILE"; }}
        set_state() {{ echo "SET_STATE:$2" >> "$CALLS_FILE"; }}
        set_block() {{ echo "SET_BLOCK:$2:$3" >> "$CALLS_FILE"; }}
        set_state_or_insert() {{ echo "SET_STATE:$2" >> "$CALLS_FILE"; }}
        set_block_or_append() {{ echo "SET_BLOCK:$2:$3" >> "$CALLS_FILE"; }}
        block_record_owner_role() {{ echo CODER; }}
        block_record_evidence() {{ echo none; }}
        block_record_write() {{ echo "BLOCK_RECORD:$2" >> "$CALLS_FILE"; }}
        normalize_status_file() {{ echo "NORMALIZE" >> "$CALLS_FILE"; }}
        mark_backlog() {{ echo "MARK_BACKLOG:$1:$2" >> "$CALLS_FILE"; }}

        task_id="SYNTH-000-fixture"
        task_readme="{readme}"
        task_status="{tmp_path}/status.md"
        AGENT="{queue_agent}"
        LAST_TASK_FINAL_STATE=""
        SUBAGENT=""

        _run_dispatch() {{
          local role assigned_agent invalid_assignment_reason dispatch_role
{block_text}
        }}
        _run_dispatch

        echo "SUBAGENT=[${{SUBAGENT}}]"
        echo "FINAL_STATE=[${{LAST_TASK_FINAL_STATE}}]"
    """)

    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=15
    )
    calls = calls_file.read_text(encoding="utf-8")
    return result, calls


# ---------------------------------------------------------------------------
# Level 2 + 2b: both wake providers share one pre-dispatch assertion block,
# so every scenario is
# exercised against both siblings via a `source` parametrization rather than
# duplicating each fixture per provider.
# ---------------------------------------------------------------------------

_PROVIDER_SOURCES = [_CLAUDE_SH, _CODEX_SH]
_PROVIDER_IDS = ["claude", "codex"]


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_assigned_agent_selects_subagent_over_mismatched_role(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A task assigned to WRITER with Role=CODER, queued under writer,
    dispatches to the writer subagent — not coder — with no BLOCKED call, on
    both wake providers.

    Assigned Agent, when present and valid, selects the executing subagent;
    Role remains the capability descriptor and need not match it.
    """
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="WRITER")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="writer", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[writer]" in result.stdout, result.stdout
    assert "FINAL_STATE=[]" in result.stdout, (
        f"No BLOCKED transition expected for a matching Assigned Agent.\n{result.stdout}"
    )
    assert "SET_STATE:BLOCKED" not in calls, calls
    assert calls == "", f"No BLOCKED-path calls expected; got: {calls!r}"


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_missing_role_blocks_with_named_reason_and_no_dispatch(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A task with no ## Role section is BLOCKED with a named reason on both
    wake providers; no subagent is selected and dispatch never proceeds.

    "defaulting to coder" must never appear in the call log — this is the
    exact regression case where codex.sh previously logged that
    fallthrough and dispatched as coder instead of blocking.
    """
    readme = _write_readme(tmp_path, role=None, assigned_agent="none")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="coder", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[]" in result.stdout, (
        f"No subagent should be selected on a blocked dispatch.\n{result.stdout}"
    )
    assert "FINAL_STATE=[BLOCKED]" in result.stdout, result.stdout
    assert "SET_STATE:BLOCKED" in calls, calls
    assert "SET_BLOCK:Blockers:INVALID_TASK_ASSIGNMENT" in calls, calls
    assert "SET_BLOCK:Needs Human:yes" in calls, calls
    assert "MARK_BACKLOG:SYNTH-000-fixture:B" in calls, calls
    assert "defaulting to coder" not in calls, (
        f"wake providers must never fail open to coder; got: {calls!r}"
    )


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_invalid_role_value_blocks_with_named_reason(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A ## Role value that is not one of the six recognized roles blocks
    dispatch (on both providers) rather than defaulting to coder.
    """
    readme = _write_readme(tmp_path, role="NOT-A-REAL-ROLE", assigned_agent="none")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="coder", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[]" in result.stdout, result.stdout
    assert "FINAL_STATE=[BLOCKED]" in result.stdout, result.stdout
    assert "SET_BLOCK:Blockers:INVALID_TASK_ASSIGNMENT" in calls, calls


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_invalid_assigned_agent_value_blocks_dispatch(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A ## Assigned Agent value that names no recognized role blocks
    dispatch (on both providers), even though ## Role itself is valid.
    """
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="NOT-A-REAL-ROLE")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="coder", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[]" in result.stdout, result.stdout
    assert "FINAL_STATE=[BLOCKED]" in result.stdout, result.stdout
    assert "SET_BLOCK:Blockers:INVALID_TASK_ASSIGNMENT" in calls, calls


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_assigned_agent_mismatched_with_queue_blocks_dispatch(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A valid ## Assigned Agent that does not match the queue the task was
    pulled from blocks dispatch (on both providers) instead of guessing
    which agent should run it.
    """
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="WRITER")
    # Task sits in the coder queue but claims Assigned Agent WRITER.
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="coder", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[]" in result.stdout, result.stdout
    assert "FINAL_STATE=[BLOCKED]" in result.stdout, result.stdout
    assert "SET_BLOCK:Blockers:INVALID_TASK_ASSIGNMENT" in calls, calls


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
def test_normal_task_dispatches_by_role_with_no_assigned_agent(
    tmp_path: pathlib.Path, source: pathlib.Path
) -> None:
    """A well-formed task with no Assigned Agent dispatches by Role exactly
    as before, on both providers — the common case is unaffected by the
    added assertion.
    """
    readme = _write_readme(tmp_path, role="CODER", assigned_agent="none")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent="coder", source=source
    )

    assert result.returncode == 0, result.stderr
    assert "SUBAGENT=[coder]" in result.stdout, result.stdout
    assert "FINAL_STATE=[]" in result.stdout, result.stdout
    assert calls == "", f"No BLOCKED-path calls expected for a normal task; got: {calls!r}"


@pytest.mark.parametrize("source", _PROVIDER_SOURCES, ids=_PROVIDER_IDS)
@pytest.mark.parametrize(
    "role,queue_agent,expected_subagent",
    [
        ("WRITER", "writer", "writer"),
        ("CM", "cm", "cm"),
        ("TESTER", "tester", "tester"),
        ("PO", "po", "po"),
        ("PM", "pm", "pm"),
    ],
)
def test_normal_task_dispatches_correct_subagent_for_each_role(
    tmp_path: pathlib.Path, source: pathlib.Path, role: str, queue_agent: str, expected_subagent: str
) -> None:
    """Every recognized role dispatches to its matching subagent (on both
    providers) when queued under its own agent and carrying no Assigned
    Agent override.
    """
    readme = _write_readme(tmp_path, role=role, assigned_agent="none")
    result, calls = _run_dispatch_assertion(
        tmp_path, readme, queue_agent=queue_agent, source=source
    )

    assert result.returncode == 0, result.stderr
    assert f"SUBAGENT=[{expected_subagent}]" in result.stdout, result.stdout
    assert calls == "", f"No BLOCKED-path calls expected; got: {calls!r}"
