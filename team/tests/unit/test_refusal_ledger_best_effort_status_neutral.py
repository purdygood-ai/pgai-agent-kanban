"""
test_refusal_ledger_best_effort_status_neutral.py
==================================================
Behavioral unit test for refusal_ledger_record_best_effort
(team/scripts/lib/refusal_ledger.sh) -- the safe entry point
pgai_emit_error_contract calls on every refusal. The function is
documented "Always returns 0; never prints to stdout, and to stderr only
the unregistered-project narration", which matters specifically
because every caller that reaches it does so from inside a
`set -euo pipefail` shell: the ledger write is a diagnostic side effect,
never a control-flow decision, so its own success or failure must be
invisible to the caller.

A shell running WITHOUT `set -e` cannot exercise this contract at all --
a bare non-zero return from an unguarded simple command is silently
ignored there regardless of whether the callee neutralizes its own exit
status. The only way to prove "the caller survives" is to actually run
the caller under `set -e`, which is what every test below does.

Coverage:
  - a deliberately failing ledger write (KANBAN_ROOT pointed at a
    directory that does not exist, the same guard refusal_ledger_record
    uses to refuse fabricating the kanban root) does not abort a
    `set -euo pipefail` caller: the statement immediately after the call
    still executes.
  - the helper's own return status, captured with `$?` in that same
    strict-mode shell, is always 0 -- proving the neutralization happens
    inside the helper, not by accident of caller structure.
  - the helper never writes to stdout or stderr even when the underlying
    write fails, matching the documented "never prints" half of the
    contract.
  - the no-project no-op path (documented as a deliberate silent skip)
    is likewise status-neutral under `set -e`.
  - a project the sandbox projects.cfg does not register: the helper
    writes nothing, creates no directory, narrates exactly one line on
    stderr, and stays status-neutral under `set -e`; the raw
    refusal_ledger_record returns 3 for the same call, and the emitter
    pgai_emit_error_contract still prints its block after the narration.

All tests use tmp_path for KANBAN_ROOT; no live filesystem paths.
"""

from __future__ import annotations

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/refusal_ledger.sh"


def _strict_script(call: str) -> str:
    """A `set -euo pipefail` shell that sources the library, makes *call*,
    captures its exit status immediately via `$?`, and echoes both a
    status marker and a reached-the-end marker -- so a caller that dies
    partway through never prints either, while a caller that survives
    prints both in order."""
    return (
        "set -euo pipefail\n"
        f"source {_LIB}\n"
        f"{call}\n"
        "rc=$?\n"
        'echo "HELPER_RC=$rc"\n'
        'echo "AFTER_CALL_REACHED"\n'
    )


def test_survives_strict_mode_caller_when_ledger_write_fails(tmp_path) -> None:
    """A `set -euo pipefail` caller continues past the call, and the
    helper's own captured exit status is 0, when the underlying ledger
    write fails (KANBAN_ROOT points at a directory that does not exist --
    the same deterministic trigger the guard on refusal_ledger_record
    uses to refuse fabricating the kanban root)."""
    missing_root = tmp_path / "does-not-exist"
    result = run_bash(
        tmp_path,
        _strict_script(
            "refusal_ledger_record_best_effort "
            "'test.marker' 'a diagnostic message' 'a diagnostic remedy' "
            "'demo-project' 'test:surface'"
        ),
        extra_env={"KANBAN_ROOT": str(missing_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert result.stdout == "HELPER_RC=0\nAFTER_CALL_REACHED\n"


def test_prints_nothing_when_ledger_write_fails(tmp_path) -> None:
    """The diagnostic refusal_ledger_record would print on failure never
    reaches the caller's stdout or stderr -- recording stays silent even
    when it fails, matching the documented 'never prints' contract."""
    missing_root = tmp_path / "does-not-exist"
    result = run_bash(
        tmp_path,
        _strict_script(
            "refusal_ledger_record_best_effort "
            "'test.marker' 'a diagnostic message' 'a diagnostic remedy' "
            "'demo-project' 'test:surface'"
        ),
        extra_env={"KANBAN_ROOT": str(missing_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert result.stderr == ""


def test_no_project_scope_no_op_is_also_status_neutral(tmp_path) -> None:
    """The documented no-project silent skip is itself status-neutral
    under `set -e` -- an empty project argument and no ambient
    PGAI_PROJECT_NAME must not abort the caller either."""
    result = run_bash(
        tmp_path,
        _strict_script(
            "refusal_ledger_record_best_effort "
            "'test.marker' 'a diagnostic message' 'a diagnostic remedy' "
            "'' 'test:surface'"
        ),
        extra_env={"PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert result.stdout == "HELPER_RC=0\nAFTER_CALL_REACHED\n"


def _registered_sandbox(tmp_path):
    """A sandbox kanban root whose projects.cfg registers only real-project."""
    root = tmp_path / "sandbox_root"
    root.mkdir()
    (root / "projects.cfg").write_text("[project:real-project]\npriority=1\n", encoding="utf-8")
    return root


def test_unregistered_project_writes_nothing_narrates_and_is_status_neutral(tmp_path) -> None:
    root = _registered_sandbox(tmp_path)
    result = run_bash(
        tmp_path,
        _strict_script(
            "refusal_ledger_record_best_effort "
            "'test.marker' 'a diagnostic message' 'a diagnostic remedy' "
            "'no-such-project' 'test:surface'"
        ),
        extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert result.stdout == "HELPER_RC=0\nAFTER_CALL_REACHED\n"
    assert result.stderr.count("\n") == 1, result.stderr
    assert result.stderr.startswith("refusal_ledger: ")
    assert "'no-such-project'" in result.stderr
    assert str(root / "projects.cfg") in result.stderr
    assert sorted(p.name for p in root.iterdir()) == ["projects.cfg"]


def test_raw_record_returns_unregistered_status_for_unregistered_project(tmp_path) -> None:
    root = _registered_sandbox(tmp_path)
    result = run_bash(
        tmp_path,
        f"source {_LIB}\n"
        "rc=0\n"
        "refusal_ledger_record a test.marker 'a message' 'a remedy' test:surface || rc=$?\n"
        'echo "RC=$rc UNREG=$REFUSAL_LEDGER_UNREGISTERED_RC"\n',
        extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.stdout == "RC=3 UNREG=3\n"
    assert "'a'" in result.stderr
    assert not (root / "projects").exists()


def test_registered_project_still_records_through_the_helper(tmp_path) -> None:
    root = _registered_sandbox(tmp_path)
    result = run_bash(
        tmp_path,
        _strict_script(
            "refusal_ledger_record_best_effort "
            "'test.marker' 'a diagnostic message' 'a diagnostic remedy' "
            "'real-project' 'test:surface'"
        ),
        extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.stdout == "HELPER_RC=0\nAFTER_CALL_REACHED\n"
    assert result.stderr == ""
    ledger = root / "projects" / "real-project" / "refusal-ledger.md"
    assert "- Error: test.marker" in ledger.read_text(encoding="utf-8")


def test_emitter_prints_its_block_after_the_narration(tmp_path) -> None:
    """pgai_emit_error_contract, the writer's caller, keeps its exit status
    and its exact block; the narration precedes the block, so the parsed
    remedy is the remedy alone."""
    root = _registered_sandbox(tmp_path)
    result = run_bash(
        tmp_path,
        "set -euo pipefail\n"
        "source scripts/lib/error_contract.sh\n"
        "pgai_emit_error_contract test.marker 'a message' 'a remedy' a\n"
        'echo "EMIT_RC=$?"\n',
        extra_env={"KANBAN_ROOT": str(root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert result.stdout == "EMIT_RC=0\n"
    narration, block = result.stderr.split("error:", 1)
    assert narration.startswith("refusal_ledger: ") and "'a'" in narration
    assert "error:" + block == "error:   test.marker\nmessage: a message\nremedy:  a remedy\n"
    assert parse_error_contract(result.stderr)["remedy"] == "a remedy"
    assert not (root / "projects").exists()
