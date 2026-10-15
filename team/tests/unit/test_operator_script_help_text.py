"""
test_operator_script_help_text.py
=================================
Behavioral tests for operator-script ``--help`` text: every flag a script
accepts is named in its help (operator-only and internal flags under their
own heading), help printed from a header comment block stops at the first
non-comment line, and scripts refuse arguments they do not define.

Also covers ``team/scripts/lib/header_help.sh``'s ``header_help_print``, the
shared helper that prints a script's header block as its help.

Every script invocation runs ``--help`` (or an argument the script refuses
before doing any work) against a throwaway kanban root built in
``tmp_path``; the tests that matter for side effects snapshot that root
before and after and require it unchanged. The installers and the upgrade
script are only ever run with ``--help``.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_HEADER_HELP_LIB = _SCRIPTS_DIR / "lib" / "header_help.sh"


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    """Map every file under *root* to its bytes (directories map to b'')."""
    return {
        str(p.relative_to(root)): (p.read_bytes() if p.is_file() else b"")
        for p in sorted(root.rglob("*"))
    }


def _sandbox_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a small kanban root with a project, a bug and a log file."""
    root = tmp_path / "kanban_root"
    (root / "projects" / "demo" / "bugs").mkdir(parents=True)
    (root / "projects" / "demo" / "bugs" / "BUG-0001-example.md").write_text(
        "# BUG-0001-example\n\n## Status\nrunning\n", encoding="utf-8"
    )
    (root / "projects.cfg").write_text("[demo]\nenabled = true\n", encoding="utf-8")
    (root / "logs").mkdir()
    (root / "logs" / "existing.log").write_text("pre-existing\n", encoding="utf-8")
    (root / "temp").mkdir()
    return root


def _sandbox_env(root: pathlib.Path) -> dict[str, str]:
    return {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "temp"),
    }


def _run_script(tmp_path: pathlib.Path, script: str, *args: str):
    """Run ``scripts/<script> <args>`` against a fresh sandbox root.

    Returns ``(result, root, before_snapshot)``.
    """
    root = _sandbox_root(tmp_path)
    before = _snapshot(root)
    quoted = " ".join(f"'{a}'" for a in args)
    result = run_bash(
        tmp_path,
        f"unset PGAI_PROJECT_NAME PGAI_PROJECT_ROOT PGAI_TASKS_DIR; "
        f"bash scripts/{script} {quoted} </dev/null",
        extra_env=_sandbox_env(root),
    )
    return result, root, before


def _help_text(tmp_path: pathlib.Path, script: str) -> str:
    """Return the ``--help`` output (stdout and stderr) of *script*; assert exit 0."""
    result, _root, _before = _run_script(tmp_path, script, "--help")
    assert result.returncode == 0, (
        f"{script} --help exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    return result.stdout + result.stderr


def _section(text: str, heading: str) -> str:
    """Return the lines of *text* from the line holding *heading* to the next
    blank line — the block a heading introduces."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if heading in line:
            block = []
            for body in lines[index + 1:]:
                if not body.strip():
                    break
                block.append(body)
            return "\n".join(block)
    raise AssertionError(f"heading {heading!r} not found in:\n{text}")


def _flag_described(block: str, flag: str) -> bool:
    """True when *flag* starts an entry in *block* followed by description text,
    on the same line or the next one."""
    lines = block.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*" + re.escape(flag) + r"(?![\w-])(.*)$", line)
        if not match:
            continue
        rest = re.sub(r"^\s*(?:(?:<[^>]+>|[A-Z]+)(?=\s|$))?", "", match.group(1)).strip()
        if rest:
            return True
        if index + 1 < len(lines) and lines[index + 1].strip():
            return True
    return False


# ---------------------------------------------------------------------------
# Operator-only and internal flags are named, with descriptions
# ---------------------------------------------------------------------------


def test_release_help_describes_operator_only_flags(tmp_path: pathlib.Path) -> None:
    """cm/release.sh --help lists --force, --reason and --overwatch-lane-b-ship,
    each with a description, under an Operator-only block."""
    block = _section(_help_text(tmp_path, "cm/release.sh"), "Operator-only options:")
    for flag in ("--force", "--reason", "--overwatch-lane-b-ship"):
        assert _flag_described(block, flag), f"{flag} not described under Operator-only options:\n{block}"


def test_release_source_no_longer_requires_byte_identical_help() -> None:
    """The comment that kept --force out of the help is gone. (A remedy
    asking for an INTENT.md entry byte-for-byte identical to --reason is a
    different requirement and stays.)"""
    text = (_SCRIPTS_DIR / "cm" / "release.sh").read_text(encoding="utf-8")
    assert "Deliberately undocumented" not in text
    assert "byte-for-byte identical to this script" not in text
    assert not re.search(r"Undocumented in _cm_release_usage", text)


def test_upgrade_help_lists_phase_two_flags_under_internal(tmp_path: pathlib.Path) -> None:
    """upgrade.sh --help names the phase-2 re-exec flags under an Internal block."""
    block = _section(_help_text(tmp_path, "upgrade.sh"), "INTERNAL (phase-2 re-exec only")
    for flag in ("--phase2", "--phase2-protocol", "--kanban-root", "--backup"):
        assert _flag_described(block, flag), f"{flag} not described under INTERNAL:\n{block}"


@pytest.mark.parametrize("script", ["install-crontab.sh", "install-pseudocron.sh"])
def test_installer_help_names_the_non_interactive_alias(tmp_path: pathlib.Path, script: str) -> None:
    """Both installers name --non-interactive as an alias of --yes."""
    text = _help_text(tmp_path, script)
    assert re.search(r"--non-interactive\s+Alias of --yes", text), text


def test_wake_codex_help_names_max_tasks(tmp_path: pathlib.Path) -> None:
    """wake/codex.sh --help names --max-tasks, as wake/claude.sh does."""
    text = _help_text(tmp_path, "wake/codex.sh")
    assert _flag_described(_section(text, "Options:"), "--max-tasks=N"), text


# ---------------------------------------------------------------------------
# log-filter.sh
# ---------------------------------------------------------------------------


def test_log_filter_help_is_the_header_only(tmp_path: pathlib.Path) -> None:
    """log-filter.sh --help names --kanban-root, describes the suppress
    patterns as an environment variable and stops before the code."""
    text = _help_text(tmp_path, "log-filter.sh")
    assert "--kanban-root" in text
    assert "shellcheck" not in text
    assert "config.cfg" not in text
    env_block = _section(text, "Environment:")
    assert "PGAI_DASHBOARD_LOG_SUPPRESS_PATTERNS" in env_block


def test_log_filter_refuses_an_unknown_option(tmp_path: pathlib.Path) -> None:
    """An option log-filter.sh does not define is refused dual-channel with a
    usage line and a runnable remedy, before any input is read."""
    result, root, before = _run_script(tmp_path, "log-filter.sh", "--bogus")
    assert result.returncode != 0
    assert "error:   log_filter.unknown_option" in result.stderr, result.stderr
    assert "Usage: log-filter.sh" in result.stderr
    assert re.search(r"remedy:\s+\S+/log-filter\.sh --help", result.stderr), result.stderr
    assert result.stdout == ""
    assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# Scripts that could not answer --help
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize(
    "script, usage_marker",
    [
        ("migrate/bug-status-done.sh", "--dry-run"),
        ("cleanup/test-cleanup.sh", "--delete"),
    ],
)
def test_help_prints_usage_and_changes_nothing(
    tmp_path: pathlib.Path, script: str, usage_marker: str, flag: str
) -> None:
    """--help/-h exits 0, prints the usage block and leaves the root unchanged."""
    result, root, before = _run_script(tmp_path, script, flag)
    assert result.returncode == 0, f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    assert "Usage:" in result.stdout, result.stdout
    assert usage_marker in result.stdout
    assert "set -euo pipefail" not in result.stdout
    assert _snapshot(root) == before, "--help changed the kanban root"


def test_bug_status_done_help_does_not_promote_bugs(tmp_path: pathlib.Path) -> None:
    """The sandbox bug stays 'running' after --help: the migration never ran."""
    _result, root, _before = _run_script(tmp_path, "migrate/bug-status-done.sh", "--help")
    bug = root / "projects" / "demo" / "bugs" / "BUG-0001-example.md"
    assert "running" in bug.read_text(encoding="utf-8")


def test_test_cleanup_refuses_unknown_arguments(tmp_path: pathlib.Path) -> None:
    """test-cleanup.sh refuses an argument it does not define, dual-channel,
    instead of falling through to a dry run."""
    result, root, before = _run_script(tmp_path, "cleanup/test-cleanup.sh", "--delte")
    assert result.returncode == 1, f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    assert "error:   test_cleanup.unknown_argument" in result.stderr
    assert "--delte" in result.stderr
    # No scan ran: the dry-run and delete paths both print to stdout.
    assert result.stdout == "", result.stdout
    assert _snapshot(root) == before


# ---------------------------------------------------------------------------
# Help printed from the header block reaches its end and no further
# ---------------------------------------------------------------------------


def _header_lines(script: pathlib.Path) -> list[str]:
    """The header comment block as header_help_print renders it."""
    rendered: list[str] = []
    for index, line in enumerate(script.read_text(encoding="utf-8").splitlines()):
        if index == 0 and line.startswith("#!"):
            continue
        if not line.startswith("#"):
            break
        if re.match(r"^#\s*shellcheck\s", line):
            continue
        rendered.append(re.sub(r"^# ?", "", line))
    return rendered


@pytest.mark.parametrize(
    "script",
    [
        "log-filter.sh",
        "list-stale-requirements.sh",
        "kanban-status.sh",
        "migrate/projects-cfg.sh",
        "migrate/bug-status-done.sh",
        "cleanup/test-cleanup.sh",
        "dashboard/show-multi.sh",
        "dashboard/show-header.sh",
        "dashboard/show-active.sh",
        "dashboard/show-progress.sh",
    ],
)
def test_header_help_is_the_whole_header_block(tmp_path: pathlib.Path, script: str) -> None:
    """--help prints exactly the header comment block: nothing cut off its
    end, no code line after it."""
    text = _help_text(tmp_path, script)
    assert text.splitlines() == _header_lines(_SCRIPTS_DIR / script)
    assert "set -euo pipefail" not in text


def test_show_multi_help_reaches_its_environment_section(tmp_path: pathlib.Path) -> None:
    text = _help_text(tmp_path, "dashboard/show-multi.sh")
    assert "NO_COLOR" in text and "TERM=dumb" in text


def test_no_script_header_names_config_cfg() -> None:
    """No script under team/scripts/ describes its configuration as coming
    from the retired config.cfg."""
    offenders = [
        f"{path.relative_to(_SCRIPTS_DIR)}:{number}"
        for path in sorted(_SCRIPTS_DIR.rglob("*.sh"))
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if "via config.cfg" in line
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# header_help_print
# ---------------------------------------------------------------------------


def _call_helper(tmp_path: pathlib.Path, body: str, pipe: str = "") -> object:
    script = tmp_path / "fixture.sh"
    script.write_text(body, encoding="utf-8")
    return run_bash(
        tmp_path,
        f"set -euo pipefail; source '{_HEADER_HELP_LIB}'; "
        f"header_help_print '{script}'{pipe}; echo \"rc=$?\" >&2",
    )


def test_header_help_print_stops_at_first_non_comment_line(tmp_path: pathlib.Path) -> None:
    """Shebang and shellcheck directives are dropped, a bare '#' is a blank
    line, and nothing after the first non-comment line is printed."""
    result = _call_helper(
        tmp_path,
        "#!/usr/bin/env bash\n"
        "# fixture.sh — one line\n"
        "#\n"
        "#   indented usage\n"
        "# shellcheck disable=SC2034\n"
        "#no-space\n"
        "\n"
        "# a later comment, not help\n"
        "echo body\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["fixture.sh — one line", "", "  indented usage", "no-space"]
    assert "rc=0" in result.stderr


def test_header_help_print_tolerates_an_early_closing_reader(tmp_path: pathlib.Path) -> None:
    """A reader that closes the pipe early (`--help | head -1`) is not an
    error, even under pipefail with a long header."""
    long_header = "#!/usr/bin/env bash\n" + "".join(f"# line {n} {'x' * 200}\n" for n in range(2000))
    result = _call_helper(tmp_path, long_header + "true\n", pipe=" | head -1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("line 0 ")


def test_header_help_print_requires_a_path(tmp_path: pathlib.Path) -> None:
    result = run_bash(
        tmp_path,
        f"source '{_HEADER_HELP_LIB}'; header_help_print; echo \"rc=$?\"",
    )
    assert "rc=2" in result.stdout
    assert "a script path is required" in result.stderr
