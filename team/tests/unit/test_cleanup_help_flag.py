"""
test_cleanup_help_flag.py
==========================
Behavioral unit tests for team/scripts/cleanup/cleanup.sh's --help flag.

Locks two contracts:

  1. --help exits 0 and names every flag the script accepts (--project,
     --dry-run, --trivial-only, --temp-only), without requiring --project
     or PGAI_PROJECT_NAME — the check that would normally fire ("no project
     specified") must not fire when --help is present.
  2. --help short-circuits before any filesystem action: no log file
     created, no directory created, nothing under the sandboxed kanban
     root changes at all.

A companion test locks the pre-existing refusal behavior for an
unrecognized flag, including when combined with --help, so a future edit
to the argument loop cannot let --help swallow bad input into a usage
print with exit 0.

Every invocation runs with both PGAI_AGENT_KANBAN_ROOT_PATH and KANBAN_ROOT
redirected to an empty tmp_path sandbox (mirroring the sandboxing
convention in lint_doc_command_truth.py's _make_help_sandbox_root) so a
regression that makes --help fall through to real startup work is
contained to the sandbox, never the live install.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_CLEANUP_SCRIPT = "scripts/cleanup/cleanup.sh"


def _snapshot(root: pathlib.Path) -> set[str]:
    """Return the set of relative paths for every file under *root*."""
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
    }


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_help_flag_exits_zero_and_documents_every_flag(tmp_path: pathlib.Path) -> None:
    """--help exits 0 and names all four accepted flags, with no --project given."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --help",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    usage = result.stdout
    for expected in ("--project", "--dry-run", "--trivial-only", "--temp-only", "--help"):
        assert expected in usage, f"usage text missing {expected!r}:\n{usage}"
    # The "no project specified" refusal must not fire under --help.
    assert "no project specified" not in (result.stdout + result.stderr)


def test_help_flag_touches_nothing_under_the_kanban_root(tmp_path: pathlib.Path) -> None:
    """--help against a populated kanban root leaves it byte-identical.

    No log file created, no directory created, no existing file modified —
    the constraint enforced here: a --help that runs the
    cleanup first and prints usage after is worse than no --help at all.
    """
    sandbox = tmp_path / "kanban_root"
    (sandbox / "logs" / "agents").mkdir(parents=True)
    (sandbox / "logs" / "agents" / "existing.log").write_text("pre-existing content\n")
    (sandbox / "projects" / "demo" / "tasks").mkdir(parents=True)

    before = _snapshot(sandbox)
    before_marker = (sandbox / "logs" / "agents" / "existing.log").read_text()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --help",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 0
    after = _snapshot(sandbox)
    assert before == after, f"kanban root changed under --help: added={after - before}, removed={before - after}"
    assert (sandbox / "logs" / "agents" / "existing.log").read_text() == before_marker


def test_short_help_flag_behaves_the_same_as_long_form(tmp_path: pathlib.Path) -> None:
    """-h is accepted as a synonym for --help."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} -h",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\nstderr: {result.stderr!r}"
    )
    assert "--temp-only" in result.stdout


# ---------------------------------------------------------------------------
# Unrecognized-flag refusal is preserved
# ---------------------------------------------------------------------------


def test_unknown_flag_alone_still_refuses(tmp_path: pathlib.Path) -> None:
    """An unrecognized flag still exits 1 (pre-existing refusal, unchanged)."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --bogus",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 1
    assert "unknown argument" in result.stderr


def test_help_combined_with_unknown_flag_does_not_swallow_the_error(tmp_path: pathlib.Path) -> None:
    """--help must not turn a bad invocation into a usage print with exit 0."""
    sandbox = tmp_path / "kanban_root"
    sandbox.mkdir()

    result = run_bash(
        tmp_path,
        f"bash {_CLEANUP_SCRIPT} --help --bogus",
        extra_env=_sandbox_env(sandbox),
    )

    assert result.returncode == 1, (
        "an unrecognized flag combined with --help must still refuse, "
        f"not print usage with exit 0; got {result.returncode}"
    )
    assert "unknown argument" in result.stderr
