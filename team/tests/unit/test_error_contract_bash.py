"""
test_error_contract_bash.py
============================
Behavioral unit tests for team/scripts/lib/error_contract.sh — the shared
dual-channel error-contract primitive bash fail-loud sites source.

Tests source the shell script and invoke each function via the bash
harness, asserting on stdout/stderr and exit codes. The core behavioral
requirement is the house shape: exactly three lines, in error/message/
remedy order, colon-aligned so the value column starts at the same
character position on every line, with the machine key emitted verbatim so
it stays greppable.
"""

from __future__ import annotations

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/error_contract.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources error_contract.sh then calls func_call.

    Unsets PGAI_PROJECT_NAME and KANBAN_ROOT first: pgai_emit_error_contract
    performs best-effort refusal-ledger recording keyed off those two
    ambient variables (see error_contract.sh header, "Refusal-ledger
    recording"), and a real agent's shell routinely exports both pointed at
    the LIVE kanban install. Without this neutralization, running this
    file's tests from such a shell would try to write a real refusal-ledger
    record into the live install. Tests that specifically exercise the
    refusal-ledger wiring set KANBAN_ROOT/PGAI_PROJECT_NAME back to an
    isolated tmp_path explicitly (see the "refusal-ledger wiring" section
    below) — this default keeps every other test in this file hermetic.
    """
    return f"unset PGAI_PROJECT_NAME KANBAN_ROOT; source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# pgai_format_error_contract: house shape (stdout)
# ---------------------------------------------------------------------------


def test_format_prints_house_shape_to_stdout(tmp_path) -> None:
    """pgai_format_error_contract echoes the three-line block to stdout."""
    result = run_bash(
        tmp_path,
        _source(
            "pgai_format_error_contract 'gate.rc-branch-mismatch' "
            "'the release stopped because the checkout was not on the release branch' "
            "'git checkout rc/v1.2.3 && re-run the release script'"
        ),
    )
    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").split("\n")
    assert len(lines) == 3
    assert lines[0] == "error:   gate.rc-branch-mismatch"
    assert (
        lines[1]
        == "message: the release stopped because the checkout was not on the release branch"
    )
    assert lines[2] == "remedy:  git checkout rc/v1.2.3 && re-run the release script"


def test_format_machine_key_is_verbatim_and_greppable(tmp_path) -> None:
    """The error field carries the exact machine key string, unaltered."""
    result = run_bash(
        tmp_path,
        _source(
            "pgai_format_error_contract 'hook.pre-squash-nonexecutable' 'msg text' 'remedy text'"
        ),
    )
    assert result.returncode == 0
    error_line = result.stdout.split("\n", 1)[0]
    assert error_line == "error:   hook.pre-squash-nonexecutable"


def test_format_fields_are_colon_aligned_to_same_value_column(tmp_path) -> None:
    """error:, message:, and remedy: labels pad to the same value column."""
    # Values are distinct sentinel tokens that do not appear inside their
    # own label word (unlike "k"/"m"/"r", which would collide with the
    # first letters of "error"/"message"/"remedy" and give a false match).
    result = run_bash(
        tmp_path,
        _source("pgai_format_error_contract AAA_ERR BBB_MSG CCC_REM"),
    )
    assert result.returncode == 0
    lines = result.stdout.rstrip("\n").split("\n")
    starts = [
        line.index(value)
        for line, value in zip(lines, ("AAA_ERR", "BBB_MSG", "CCC_REM"))
    ]
    assert starts[0] == starts[1] == starts[2]


def test_format_field_order_is_fixed_error_message_remedy(tmp_path) -> None:
    """Field order in the rendered block is always error, then message, then remedy."""
    result = run_bash(
        tmp_path,
        _source("pgai_format_error_contract ERR_KEY MSG_TEXT REMEDY_TEXT"),
    )
    assert result.returncode == 0
    out = result.stdout
    assert out.index("ERR_KEY") < out.index("MSG_TEXT") < out.index("REMEDY_TEXT")


# ---------------------------------------------------------------------------
# pgai_format_error_contract: validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        "'' 'a message' 'a remedy'",
        "'an-error' '' 'a remedy'",
        "'an-error' 'a message' ''",
    ],
)
def test_format_refuses_blank_field(tmp_path, args: str) -> None:
    """A blank error, message, or remedy is refused (nonzero exit, no stdout)."""
    result = run_bash(tmp_path, _source(f"pgai_format_error_contract {args}"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "required and must be non-empty" in result.stderr


# ---------------------------------------------------------------------------
# pgai_emit_error_contract: stderr output
# ---------------------------------------------------------------------------


def test_emit_prints_house_shape_to_stderr_not_stdout(tmp_path) -> None:
    """pgai_emit_error_contract writes the formatted block to stderr, leaving stdout empty."""
    result = run_bash(
        tmp_path,
        _source("pgai_emit_error_contract gate.x 'message text' 'remedy text'"),
    )
    assert result.returncode == 0
    assert result.stdout == ""
    lines = result.stderr.rstrip("\n").split("\n")
    assert lines == ["error:   gate.x", "message: message text", "remedy:  remedy text"]


def test_emit_refuses_blank_field_and_returns_nonzero(tmp_path) -> None:
    """pgai_emit_error_contract propagates the same validation as the format function."""
    result = run_bash(
        tmp_path,
        _source("pgai_emit_error_contract '' 'message text' 'remedy text'"),
    )
    assert result.returncode == 1
    assert "required and must be non-empty" in result.stderr


# ---------------------------------------------------------------------------
# pgai_emit_error_contract: refusal-ledger wiring
#
# These tests deliberately set KANBAN_ROOT to an isolated tmp_path via
# extra_env (never the ambient value) so they stay hermetic regardless of
# the invoking shell's environment. They use _source_scoped() rather than
# _source(): _source() unsets KANBAN_ROOT/PGAI_PROJECT_NAME up front (the
# right default for every OTHER test in this file, which does not want
# ledger recording to fire at all), which would erase the very env these
# tests pass in through extra_env before pgai_emit_error_contract ever runs.
# ---------------------------------------------------------------------------


def _source_scoped(func_call: str) -> str:
    """Like _source(), but without the KANBAN_ROOT/PGAI_PROJECT_NAME unset
    -- these tests pass both explicitly via extra_env and need them to
    reach pgai_emit_error_contract."""
    return f"source {_LIB} && {func_call}"


def _ledger_path(kanban_root, project: str):
    return kanban_root / "projects" / project / "refusal-ledger.md"


def test_emit_with_project_records_a_refusal_ledger_entry(tmp_path) -> None:
    """A refusal emitted with a project in scope (4th positional argument)
    leaves a record naming the machine key, message, remedy, timestamp, and
    raising surface."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    result = run_bash(
        tmp_path,
        _source_scoped(
            "pgai_emit_error_contract gate.ledger 'ledger message' 'ledger remedy' demo-project"
        ),
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0

    ledger_file = _ledger_path(kanban_root, "demo-project")
    assert ledger_file.exists()
    text = ledger_file.read_text(encoding="utf-8")
    assert text.count("## Refusal:") == 1
    assert "- Error: gate.ledger" in text
    assert "- Message: ledger message" in text
    assert "- Remedy: ledger remedy" in text


def test_emit_with_ambient_project_name_records_without_explicit_argument(tmp_path) -> None:
    """PGAI_PROJECT_NAME (ambient) is honored when the 4th argument is omitted."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    result = run_bash(
        tmp_path,
        _source_scoped("pgai_emit_error_contract gate.env 'env message' 'env remedy'"),
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": "env-project"},
    )
    assert result.returncode == 0
    assert _ledger_path(kanban_root, "env-project").exists()


def test_second_emit_replaces_first_ledger_entry(tmp_path) -> None:
    """A second refusal for the same project REPLACES the first: the record
    still holds exactly one entry and it is the newer one."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    env = {"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""}

    run_bash(
        tmp_path,
        _source_scoped("pgai_emit_error_contract gate.first 'first message' 'first remedy' demo-project"),
        extra_env=env,
    )
    run_bash(
        tmp_path,
        _source_scoped(
            "pgai_emit_error_contract gate.second 'second message' 'second remedy' demo-project"
        ),
        extra_env=env,
    )

    text = _ledger_path(kanban_root, "demo-project").read_text(encoding="utf-8")
    assert text.count("## Refusal:") == 1
    assert "gate.first" not in text
    assert "- Error: gate.second" in text


def test_emit_without_project_scope_records_nothing(tmp_path) -> None:
    """No explicit project and no ambient PGAI_PROJECT_NAME: recording is
    skipped entirely -- no project directory is even created."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    result = run_bash(
        tmp_path,
        _source_scoped("pgai_emit_error_contract gate.noproj 'no project message' 'no project remedy'"),
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert not (kanban_root / "projects").exists()


def test_emit_with_unwritable_ledger_path_preserves_exit_status_and_stderr(tmp_path) -> None:
    """With the ledger path made unwritable, a refusal still exits with its
    original status and still prints its original stderr text unchanged --
    the diagnostic never becomes a gate."""
    kanban_root = tmp_path / "kanban_root"
    projects_dir = kanban_root / "projects"
    projects_dir.mkdir(parents=True)
    projects_dir.chmod(0)
    try:
        result = run_bash(
            tmp_path,
            _source_scoped(
                "pgai_emit_error_contract gate.unwritable 'unwritable message' "
                "'unwritable remedy' locked-project"
            ),
            extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
        )
    finally:
        projects_dir.chmod(0o755)

    assert result.returncode == 0
    assert result.stdout == ""
    lines = result.stderr.rstrip("\n").split("\n")
    assert lines == [
        "error:   gate.unwritable",
        "message: unwritable message",
        "remedy:  unwritable remedy",
    ]


def test_python_emitted_record_parses_identically_via_bash_reader(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal emitted through the Python emitter produces a record the
    bash reader (refusal_ledger.sh's refusal_ledger_read_latest) parses
    identically to a bash-emitted one."""
    import io

    from pgai_agent_kanban.lib.error_contract import emit_error_contract

    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))

    buf = io.StringIO()
    emit_error_contract(
        "gate.parity",
        "parity message",
        "parity remedy",
        file=buf,
        project="parity-project",
    )

    result = run_bash(
        tmp_path,
        "source scripts/lib/refusal_ledger.sh && refusal_ledger_read_latest parity-project",
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    stdout = result.stdout
    assert "found=true" in stdout
    assert "error=gate.parity" in stdout
    assert "message=parity message" in stdout
    assert "remedy=parity remedy" in stdout
    # Context is stamped by whichever process actually performs the write
    # (here, the Python emitter's own subprocess -- this test itself is
    # running under pytest, so PYTEST_CURRENT_TEST is set), and the bash
    # reader surfaces the same value the Python writer stamped.
    assert "context=test" in stdout


# ---------------------------------------------------------------------------
# refusal-ledger wiring: context stamping
#
# Both bindings write through the same underlying Python CLI (this file's
# module docstring is authoritative on why: refusal_ledger.sh's
# refusal_ledger_record shells out to
# "python3 -m pgai_agent_kanban.lib.refusal_ledger record", the same code
# path pgai_emit_error_contract's Python sibling calls directly), so a
# refusal emitted from bash inherits this process's own PYTEST_CURRENT_TEST
# exactly as a Python-emitted one does -- these tests prove that end to end
# through the bash emitter specifically, rather than relying on the parity
# test above (which drives the write from the Python side).
# ---------------------------------------------------------------------------


def test_bash_emitted_refusal_records_context_test_while_pytest_current_test_is_set(
    tmp_path,
) -> None:
    """A refusal emitted via the bash emitter, while this test's own
    PYTEST_CURRENT_TEST is ambient (pytest sets it for every test; no
    monkeypatching needed here), records context "test"."""
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    run_bash(
        tmp_path,
        _source_scoped(
            "pgai_emit_error_contract gate.ctx 'ctx message' 'ctx remedy' demo-project"
        ),
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )

    result = run_bash(
        tmp_path,
        "source scripts/lib/refusal_ledger.sh && refusal_ledger_read_latest demo-project",
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert "context=test" in result.stdout


def test_bash_emitted_refusal_records_context_install_when_pytest_current_test_is_absent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With PYTEST_CURRENT_TEST cleared from the test process's own
    environment before the bash subprocess is spawned -- simulating a
    refusal raised by the install's own operation, outside any pytest run
    -- the bash emitter's write records context "install"."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    run_bash(
        tmp_path,
        _source_scoped(
            "pgai_emit_error_contract gate.ctx 'ctx message' 'ctx remedy' demo-project"
        ),
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )

    result = run_bash(
        tmp_path,
        "source scripts/lib/refusal_ledger.sh && refusal_ledger_read_latest demo-project",
        extra_env={"KANBAN_ROOT": str(kanban_root), "PGAI_PROJECT_NAME": ""},
    )
    assert result.returncode == 0
    assert "context=install" in result.stdout
