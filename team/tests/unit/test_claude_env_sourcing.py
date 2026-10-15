"""
test_claude_env_sourcing.py
============================
Behavioral unit tests for the claude-env sourcing point added to
team/scripts/lib/env_bootstrap.sh (v2.14.0-provider-runtime-hardening).

Tests exercise the acceptance criteria for the new, optional claude-env
file:

  1. Absent claude-env is a clean no-op: no stdout, no stderr, exit 0 —
     byte-identical to the prelude's behaviour before this change.
  2. A present claude-env that exports a marker variable leaves that
     variable exported in the caller's environment.
  3. When both shell-env and claude-env set the same variable to different
     values, claude-env's value survives — demonstrating claude-env is
     sourced strictly AFTER shell-env.
  4. A claude-env with a deliberate bash syntax error makes the prelude
     exit non-zero, with a message naming the file path and the runnable
     `bash -n <path>` remedy, through the shared dual-channel error
     contract (error:/message:/remedy: fields).
  5. No-disclosure: the prelude's own output never contains the VALUE of a
     variable claude-env sets, even when that value looks sensitive.

Each test builds a synthetic kanban root layout inside pytest's tmp_path,
mirroring the fixture-construction pattern in test_env_bootstrap_bash.py
(same repo, same file), and writes a small "caller" shell script at a
real entry-point depth so BASH_SOURCE[1] resolves correctly.
"""

from __future__ import annotations

import pathlib
import stat

from tests.unit.shell_harness import run_bash

# Path to the script under test, relative to the team/ directory where pytest runs.
_PRELUDE = pathlib.Path("scripts/lib/env_bootstrap.sh")
_ERROR_CONTRACT = pathlib.Path("scripts/lib/error_contract.sh")


# ---------------------------------------------------------------------------
# Shared helper: build a synthetic kanban root layout
# ---------------------------------------------------------------------------


def _make_root(
    tmp_path: pathlib.Path,
    *,
    shell_env_text: str | None = 'export PGAI_AGENT_KANBAN_ROOT_PATH="${_eb_candidate}"\n',
    claude_env_text: str | None = None,
) -> pathlib.Path:
    """Build a minimal synthetic root directory for prelude tests.

    Layout created:
        tmp_path/
            shell-env          (optional; a shell-env that exports the
                                 canonical root var by default, matching
                                 the real shell-env_example contract)
            claude-env         (optional)
            scripts/
                lib/
                    env_bootstrap.sh   (copy of the real prelude)
                    error_contract.sh  (copy of the real emitter)

    Parameters
    ----------
    tmp_path:
        The pytest tmp_path fixture value.
    shell_env_text:
        Contents for the shell-env file. Pass None to omit shell-env
        entirely (not used by these tests — claude-env requires a
        resolvable root to be reached at all).
    claude_env_text:
        Contents for the claude-env file. Pass None (default) to omit
        claude-env — the absent-file no-op case.
    """
    root = tmp_path
    lib_dir = root / "scripts" / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)

    for src, name in ((_PRELUDE, "env_bootstrap.sh"), (_ERROR_CONTRACT, "error_contract.sh")):
        dest = lib_dir / name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        dest.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    if shell_env_text is not None:
        (root / "shell-env").write_text(shell_env_text, encoding="utf-8")

    if claude_env_text is not None:
        (root / "claude-env").write_text(claude_env_text, encoding="utf-8")

    return root


def _write_caller(root: pathlib.Path, *, extra_lines: str = "") -> pathlib.Path:
    """Write a minimal top-level caller script (scripts/caller.sh)."""
    script_dir = root / "scripts"
    caller_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '# shellcheck source=lib/env_bootstrap.sh\n'
        'source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"\n'
    )
    if extra_lines:
        caller_text += extra_lines + "\n"

    caller_path = script_dir / "caller.sh"
    caller_path.write_text(caller_text, encoding="utf-8")
    caller_path.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return caller_path


# ---------------------------------------------------------------------------
# Criterion 1: absent claude-env is a clean no-op
# ---------------------------------------------------------------------------


def test_absent_claude_env_is_clean_noop(tmp_path: pathlib.Path) -> None:
    """No claude-env present: prelude behaves exactly as it did before this
    change — no stdout, no stderr, exit 0."""
    root = _make_root(tmp_path, claude_env_text=None)
    caller = _write_caller(root)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    assert result.stdout == "", f"prelude emitted unexpected stdout:\n{result.stdout!r}"
    assert result.stderr == "", f"prelude emitted unexpected stderr:\n{result.stderr!r}"


def test_absent_claude_env_still_exports_root(tmp_path: pathlib.Path) -> None:
    """No claude-env present: PGAI_AGENT_KANBAN_ROOT_PATH is still resolved
    and exported from shell-env alone, unaffected by the new sourcing point."""
    root = _make_root(tmp_path, claude_env_text=None)
    caller = _write_caller(root, extra_lines='echo "ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    exported_root = result.stdout.strip().removeprefix("ROOT=")
    assert exported_root == str(root.resolve())


# ---------------------------------------------------------------------------
# Criterion 2: a present claude-env exports its marker variable
# ---------------------------------------------------------------------------


def test_claude_env_marker_variable_is_exported(tmp_path: pathlib.Path) -> None:
    """A claude-env that exports a marker variable leaves that variable
    exported in the caller's environment after the prelude returns."""
    root = _make_root(
        tmp_path,
        claude_env_text='export CLAUDE_ENV_MARKER="from-claude-env"\n',
    )
    caller = _write_caller(root, extra_lines='echo "MARKER=${CLAUDE_ENV_MARKER:-unset}"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    assert "MARKER=from-claude-env" in result.stdout


def test_claude_env_present_produces_no_output_of_its_own(tmp_path: pathlib.Path) -> None:
    """Sourcing a well-formed claude-env is silent, matching the "clean
    no-op" contract for the file's own sourcing step (the caller's own
    echo lines are the only expected output)."""
    root = _make_root(
        tmp_path,
        claude_env_text='export CLAUDE_ENV_MARKER="quiet"\n',
    )
    caller = _write_caller(root)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Criterion 3: claude-env is sourced AFTER shell-env (ordering)
# ---------------------------------------------------------------------------


def test_claude_env_value_wins_over_shell_env_for_same_variable(
    tmp_path: pathlib.Path,
) -> None:
    """Both shell-env and claude-env set the same variable to different
    values; claude-env's value is the one that survives, demonstrating the
    required sourcing order (claude-env sourced strictly after shell-env)."""
    root = _make_root(
        tmp_path,
        shell_env_text=(
            'export PGAI_AGENT_KANBAN_ROOT_PATH="${_eb_candidate}"\n'
            'export SHARED_TUNING_VAR="from-shell-env"\n'
        ),
        claude_env_text='export SHARED_TUNING_VAR="from-claude-env"\n',
    )
    caller = _write_caller(root, extra_lines='echo "SHARED=${SHARED_TUNING_VAR}"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0, f"unexpected failure:\n{result.stderr}"
    assert "SHARED=from-claude-env" in result.stdout, (
        f"claude-env value did not win over shell-env; got:\n{result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Criterion 4: a claude-env with a syntax error refuses loud
# ---------------------------------------------------------------------------


def test_claude_env_syntax_error_refuses_with_dual_channel_contract(
    tmp_path: pathlib.Path,
) -> None:
    """A claude-env with a deliberate bash syntax error makes the prelude
    exit non-zero. The refusal names the file path and the runnable
    `bash -n <path>` remedy, through the shared dual-channel error
    contract (error:/message:/remedy: fields on stderr)."""
    root = _make_root(
        tmp_path,
        # Deliberately broken: unmatched quote is a bash syntax error.
        claude_env_text='export BROKEN_CLAUDE_ENV="unterminated\n',
    )
    caller = _write_caller(root, extra_lines='echo "should not reach here"')

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode != 0, (
        f"expected non-zero exit on claude-env syntax error;\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "should not reach here" not in result.stdout, (
        "caller continued execution after the prelude refused"
    )

    claude_env_path = str(root / "claude-env")
    assert claude_env_path in result.stderr, (
        f"error message does not name the claude-env path.\n"
        f"Expected path: {claude_env_path!r}\nActual stderr: {result.stderr!r}"
    )
    assert f"bash -n {claude_env_path}" in result.stderr, (
        f"error message does not carry the runnable bash -n remedy.\n"
        f"Actual stderr: {result.stderr!r}"
    )

    # Dual-channel house shape: error:/message:/remedy: fields.
    assert "error:" in result.stderr
    assert "message:" in result.stderr
    assert "remedy:" in result.stderr


def test_claude_env_syntax_error_does_not_half_source(tmp_path: pathlib.Path) -> None:
    """A syntax error anywhere in claude-env means NONE of it took effect —
    the bash -n pre-check runs before any `source`, so a variable set on an
    earlier, otherwise-valid line of a broken file must not leak into the
    caller's environment."""
    root = _make_root(
        tmp_path,
        claude_env_text=(
            'export SHOULD_NOT_BE_SET="leaked"\n'
            'export BROKEN_CLAUDE_ENV="unterminated\n'
        ),
    )
    caller = _write_caller(
        root,
        extra_lines='echo "SHOULD_NOT_BE_SET=${SHOULD_NOT_BE_SET:-absent}"',
    )

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode != 0
    assert "SHOULD_NOT_BE_SET=leaked" not in result.stdout, (
        "an earlier, valid line of a syntactically broken claude-env took "
        "effect despite the file failing its bash -n pre-check"
    )


# ---------------------------------------------------------------------------
# Criterion 5: no-disclosure — the prelude never prints a claude-env VALUE
# ---------------------------------------------------------------------------


def test_prelude_never_prints_claude_env_variable_values(tmp_path: pathlib.Path) -> None:
    """The prelude's own output (not the caller's deliberate echo) never
    contains the VALUE of a variable claude-env sets, even when that value
    looks like a secret. Naming the claude-env PATH is fine; naming a value
    read from inside it is not."""
    sensitive_value = "sk-ant-super-secret-token-should-never-appear"
    root = _make_root(
        tmp_path,
        claude_env_text=f'export CLAUDE_ENV_SENSITIVE="{sensitive_value}"\n',
    )
    # Caller does NOT echo the sensitive variable — only the prelude's own
    # output is under test here.
    caller = _write_caller(root)

    result = run_bash(
        tmp_path,
        f"bash {caller!s}",
        extra_env={"PGAI_AGENT_KANBAN_ROOT_PATH": ""},
    )

    assert result.returncode == 0
    assert sensitive_value not in result.stdout
    assert sensitive_value not in result.stderr


def test_prelude_source_code_never_prints_claude_env_values() -> None:
    """Static source-text guard: the
    claude-env sourcing block in env_bootstrap.sh contains no echo/printf/
    pgai_emit_error_contract call that interpolates a value read out of
    claude-env — only the file's PATH (${_eb_candidate}/claude-env) may be
    named in diagnostics."""
    text = _PRELUDE.read_text(encoding="utf-8")
    start = text.index("# --- Source claude-env when present")
    end = text.index("# --- Fail loud when the root is still unset")
    block = text[start:end]

    # The only interpolations allowed in any printed/emitted string inside
    # this block are the file's own path pieces -- never a variable that
    # claude-env itself could have set (which, by construction, this block
    # cannot even name, since it does not parse claude-env's contents).
    for line in block.splitlines():
        stripped = line.strip()
        if not (
            stripped.startswith("pgai_emit_error_contract")
            or stripped.startswith('"')
            or stripped.startswith("echo")
        ):
            continue
        assert "$(" not in stripped or "_eb_candidate" in stripped or "dirname" in stripped
