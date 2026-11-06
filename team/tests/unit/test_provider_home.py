"""
test_provider_home.py
=====================
pgai_agent_kanban.lib.provider_home -- the one provider-home rule:

  1. provider_home_dir: claude reads a non-empty CLAUDE_CONFIG_DIR (absolute
     or relative, kept as given), else $HOME/.claude; every other provider
     reads $HOME/.<provider>; the kind is the last component; HOME falls
     back to the account's home directory.
  2. The command line prints the directory with no trailing newline and
     exits 0, answers --help with exit 0, and exits 2 on a missing
     argument -- run by module name and by file path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import provider_home
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_MODULE_FILE = _TEAM_DIR / "pgai_agent_kanban" / "lib" / "provider_home.py"


@pytest.mark.parametrize(
    "provider,kind,env,expected",
    [
        ("claude", "skills", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg/claude"}, "/cfg/claude/skills"),
        ("claude", "agents", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg/claude"}, "/cfg/claude/agents"),
        ("claude", "skills", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "rel/cfg"}, "rel/cfg/skills"),
        ("claude", "skills", {"HOME": "/h"}, "/h/.claude/skills"),
        ("claude", "agents", {"HOME": "/h", "CLAUDE_CONFIG_DIR": ""}, "/h/.claude/agents"),
        ("codex", "skills", {"HOME": "/h", "CLAUDE_CONFIG_DIR": "/cfg/claude"}, "/h/.codex/skills"),
        ("codex", "agents", {"HOME": "/h"}, "/h/.codex/agents"),
    ],
    ids=[
        "claude-config-dir-skills",
        "claude-config-dir-agents",
        "claude-config-dir-relative",
        "claude-default",
        "claude-config-dir-empty",
        "codex-ignores-config-dir",
        "codex-agents",
    ],
)
def test_provider_home_rule(provider: str, kind: str, env: dict, expected: str) -> None:
    assert str(provider_home.provider_home_dir(provider, kind, env)) == expected


def test_home_unset_falls_back_to_the_account_home() -> None:
    assert provider_home.provider_home_dir("codex", "skills", {}) == pathlib.Path.home() / ".codex" / "skills"


def test_reads_the_process_environment_by_default(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert provider_home.provider_home_dir("claude", "agents") == tmp_path / ".claude" / "agents"


def _cli(tmp_path: pathlib.Path, *args: str, by_path: bool = False, extra_env: dict | None = None):
    """Run the module's command line from a working directory outside the tree."""
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env=extra_env)
    if not by_path:
        env["PYTHONPATH"] = str(_TEAM_DIR)
    entry = [str(_MODULE_FILE)] if by_path else ["-m", "pgai_agent_kanban.lib.provider_home"]
    return subprocess.run(
        [sys.executable, *entry, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("by_path", [False, True], ids=["module", "by-path"])
def test_cli_prints_the_directory_without_a_newline(tmp_path: pathlib.Path, by_path: bool) -> None:
    result = _cli(tmp_path, "claude", "skills", by_path=by_path, extra_env={"CLAUDE_CONFIG_DIR": "/cfg/claude"})
    assert (result.returncode, result.stdout, result.stderr) == (0, "/cfg/claude/skills", "")


def test_cli_help_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _cli(tmp_path, "--help")
    assert result.returncode == 0
    assert "PROVIDER" in result.stdout and "KIND" in result.stdout


def test_cli_missing_argument_is_a_usage_error(tmp_path: pathlib.Path) -> None:
    result = _cli(tmp_path, "claude")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "KIND" in result.stderr
