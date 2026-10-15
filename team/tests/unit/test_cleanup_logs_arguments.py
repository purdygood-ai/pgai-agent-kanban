"""
test_cleanup_logs_arguments.py
==============================
Behavioral unit tests for team/scripts/cleanup/cleanup_logs.sh's command line.

The script prunes small (<1024 bytes), stale (>1 day old) ``*.log`` files from
``$KANBAN_ROOT/logs/agents``. These tests lock its argument contract:

  1. ``--help`` (and ``-h``) prints usage naming every flag, exits 0, and
     deletes nothing.
  2. An unknown flag or a stray positional argument is refused with exit 2
     and the dual-channel error block, and deletes nothing.
  3. ``--dry-run`` lists the file that would be deleted without deleting it.
  4. A no-argument run deletes a stale small log, exactly as it always has,
     and ``DRY_RUN=1`` still previews instead of deleting.
  5. A root with no ``logs/agents`` directory is refused with exit 1 and the
     dual-channel error block, creating nothing; its remedy, run as printed,
     succeeds when the root is the default one.

Every invocation runs against a tmp_path sandbox root with
PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT and PGAI_AGENT_KANBAN_TEMP_DIR
repointed and the pre-resolved project exports cleared, so nothing reaches
the live install.
"""

from __future__ import annotations

import os
import pathlib
import time

from pgai_agent_kanban.lib.error_contract import parse_error_contract
from tests.unit.shell_harness import run_bash

_SCRIPT = "scripts/cleanup/cleanup_logs.sh"


def _sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a sandbox root holding one stale, small sentinel log file."""
    root = tmp_path / "kanban_root"
    log_dir = root / "logs" / "agents"
    log_dir.mkdir(parents=True)
    (root / "tmp").mkdir()
    sentinel = log_dir / "stale-small.log"
    sentinel.write_text("tiny\n")
    three_days_ago = time.time() - 3 * 86400
    os.utime(sentinel, (three_days_ago, three_days_ago))
    return root, sentinel


def _run(tmp_path: pathlib.Path, root: pathlib.Path, args: str, **env: str):
    extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    extra.update(env)
    script = (
        "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_PROJECT_NAME DRY_RUN\n"
        + "".join(f"export {k}={v!r}\n" for k, v in env.items())
        + f"bash {_SCRIPT} {args}"
    )
    return run_bash(tmp_path, script, extra_env=extra)


def test_help_exits_zero_names_every_flag_and_deletes_nothing(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "--help")
    assert result.returncode == 0, result.stderr
    for flag in ("--dry-run", "--help"):
        assert flag in result.stdout, f"usage missing {flag!r}:\n{result.stdout}"
    assert sentinel.exists(), "--help ran the prune and deleted the sentinel log"


def test_short_help_behaves_like_long_help(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "-h")
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert sentinel.exists()


def test_unknown_flag_exits_two_and_deletes_nothing(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "--bogus")
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "error:   cleanup_logs.unknown_argument" in result.stderr
    assert "--bogus" in result.stderr
    assert sentinel.exists(), "an unknown flag ran the prune and deleted the sentinel log"


def test_stray_positional_exits_two_and_deletes_nothing(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "now")
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert sentinel.exists()


def test_dry_run_lists_the_file_without_deleting_it(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert f"would delete: {sentinel}" in result.stdout
    assert sentinel.exists()


def test_dry_run_environment_variable_still_previews(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    result = _run(tmp_path, root, "", DRY_RUN="1")
    assert result.returncode == 0, result.stderr
    assert f"would delete: {sentinel}" in result.stdout
    assert sentinel.exists()


def test_no_argument_run_deletes_the_stale_small_log(tmp_path: pathlib.Path) -> None:
    root, sentinel = _sandbox(tmp_path)
    fresh = sentinel.parent / "fresh-small.log"
    fresh.write_text("tiny\n")
    result = _run(tmp_path, root, "")
    assert result.returncode == 0, result.stderr
    assert not sentinel.exists(), "the no-argument run no longer prunes stale small logs"
    assert fresh.exists(), "a fresh log was pruned; only files older than a day qualify"
    assert "pruned 1 small log files" in result.stdout


def _root_without_log_dir(home: pathlib.Path, name: str) -> pathlib.Path:
    """Build a sandbox root under *home* that has no logs/agents directory."""
    root = home / name
    (root / "tmp").mkdir(parents=True)
    return root


def test_missing_log_dir_is_refused_dual_channel_and_creates_nothing(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    root = _root_without_log_dir(home, "elsewhere")
    result = _run(tmp_path, root, "", HOME=str(home))
    assert result.returncode == 1, (result.stdout, result.stderr)
    triple = parse_error_contract(result.stderr)
    assert triple is not None, result.stderr
    assert triple["error"] == "cleanup_logs.log_dir_not_found"
    assert str(root / "logs" / "agents") in triple["message"]
    assert f"KANBAN_ROOT={home / 'pgai_agent_kanban'}" in triple["remedy"], triple["remedy"]
    assert triple["remedy"].endswith("cleanup_logs.sh --dry-run"), triple["remedy"]
    assert "<" not in triple["remedy"], f"the remedy carries a placeholder: {triple['remedy']}"
    assert not (root / "logs").exists(), "the refused run created the log directory"


def test_missing_log_dir_remedy_at_the_default_root_runs_as_printed(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    root = _root_without_log_dir(home, "pgai_agent_kanban")
    refused = _run(tmp_path, root, "", HOME=str(home))
    assert refused.returncode == 1, (refused.stdout, refused.stderr)
    triple = parse_error_contract(refused.stderr)
    assert triple is not None, refused.stderr
    command = triple["remedy"].split("preview the prune: ", 1)[1]
    assert command.startswith("mkdir -p "), triple["remedy"]

    rerun = run_bash(
        tmp_path,
        "unset PGAI_TASKS_DIR PGAI_PROJECT_ROOT PGAI_PROJECT_NAME DRY_RUN\n" + command,
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
            "HOME": str(home),
        },
    )
    assert rerun.returncode == 0, (command, rerun.stdout, rerun.stderr)
    assert (root / "logs" / "agents").is_dir()
    assert "pruned 0 small log files" in rerun.stdout
