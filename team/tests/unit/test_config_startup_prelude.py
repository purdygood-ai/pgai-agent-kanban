"""
test_config_startup_prelude.py
==============================
Unit tests for config_startup_prelude in team/scripts/lib/config_loader.sh —
the startup step the kanban entry-point scripts share: source
~/.config/pgai-kanban.cfg when it exists, then export PGAI_KANBAN_PM_MODE
from kanban.cfg [chain] pm_mode (default automatic) when it is unset.

  - Precedence: an exported PGAI_KANBAN_PM_MODE beats kanban.cfg; a value
    the user config sets beats kanban.cfg; kanban.cfg beats the default;
    the default applies when the key is absent; nothing is exported when
    kanban.cfg is absent; the user config runs before kanban.cfg is read.
  - A second call in one shell is a no-op; a child shell runs its own.
  - The step returns 0 under strict mode with nothing to source.
  - Every converted entry point calls it once, after <root>/bashrc and
    <root>/env, and carries no copy of either step — checked statically
    over each script, and behaviourally by running a read-only one.
  - The dashboard status bar shows the user config's pm_mode and keeps that
    file's stderr quiet; a dashboard renderer runs the user config.

Every subprocess runs against a throwaway kanban root under tmp_path with
HOME pointed inside it, so the operator's real user config never leaks in.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CONFIG_LOADER = _SCRIPTS_DIR / "lib" / "config_loader.sh"

# The entry points that run the shared startup step.
_CONVERTED_SCRIPTS = (
    "cm/cancel-rc.sh",
    "cm/finalize-release.sh",
    "cm/finalize.sh",
    "cm/open-doc.sh",
    "cm/open-rc.sh",
    "cm/release.sh",
    "cleanup/cleanup.sh",
    "lib/wake_bootstrap.sh",
    "log-filter.sh",
    "pm-agent.sh",
    "po-agent.sh",
    "recover-rejected.sh",
    "run-cold-build-tests.sh",
    "run-gated-tests.sh",
    "run-integration-tests.sh",
    "run-three-host-matrix.sh",
    "run-unit-tests.sh",
    "ship-rc.sh",
    "unwind-rc.sh",
    "upgrade.sh",
    "verify-rc-state.sh",
    "dashboard/attach.sh",
    "dashboard/attention.sh",
    "dashboard/column-render.sh",
    "dashboard/create.sh",
    "dashboard/data.sh",
    "dashboard/detach.sh",
    "dashboard/human-review.sh",
    "dashboard/kill.sh",
    "dashboard/legend-render.sh",
    "dashboard/metadata.sh",
    "dashboard/queue-pane.sh",
    "dashboard/show-active.sh",
    "dashboard/show-attention.sh",
    "dashboard/show-header.sh",
    "dashboard/show-multi.sh",
    "dashboard/show-progress.sh",
    "dashboard/show-status-window.sh",
    "dashboard/status-bottom.sh",
    "dashboard/status-right.sh",
)

_CALL_RE = re.compile(r"^\s*config_startup_prelude\b")
# The hand-copied pm_mode read the step replaces. Split in two so a tree
# grep for the copy finds only config_loader.sh, not this test.
_PM_MODE_COPY = "PGAI_KANBAN_PM_MODE:-" "$(read_ini"
_BASHRC_RE = re.compile(r"source\s+\"\$\w+/bashrc\"")
_ENV_RE = re.compile(r"source\s+\"\$\w+/env\"")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Sandbox:
    """A throwaway kanban root with HOME inside it."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "root"
        self.home = tmp_path / "home"
        (self.root / "tmp").mkdir(parents=True)
        (self.home / ".config").mkdir(parents=True)

    @property
    def user_cfg(self) -> pathlib.Path:
        return self.home / ".config" / "pgai-kanban.cfg"

    def write_kanban_cfg(self, body: str) -> None:
        (self.root / "kanban.cfg").write_text(body, encoding="utf-8")

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {
            "KANBAN_ROOT": str(self.root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.root / "tmp"),
        }
        base.update(extra or {})
        return build_scrubbed_env(home_dir=self.home, extra_env=base)

    def run(self, body: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        script = f"source {str(_CONFIG_LOADER)!r}\n{body}"
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True, text=True, env=self.env(extra_env), timeout=30,
        )


def _pm_mode_after_prelude(sb: _Sandbox, extra_env: dict[str, str] | None = None) -> str:
    """PGAI_KANBAN_PM_MODE as a child process sees it after the step."""
    result = sb.run(
        'config_startup_prelude "$KANBAN_ROOT"\n'
        'bash -c \'printf "%s" "${PGAI_KANBAN_PM_MODE-<unset>}"\'',
        extra_env,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def sb(tmp_path: pathlib.Path) -> _Sandbox:
    return _Sandbox(tmp_path)


# ---------------------------------------------------------------------------
# PGAI_KANBAN_PM_MODE precedence
# ---------------------------------------------------------------------------

def test_kanban_cfg_value_exported_when_unset(sb: _Sandbox) -> None:
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    assert _pm_mode_after_prelude(sb) == "manual"


def test_default_automatic_when_key_absent(sb: _Sandbox) -> None:
    sb.write_kanban_cfg("[paths]\ndev_tree_path =\n")
    assert _pm_mode_after_prelude(sb) == "automatic"


def test_nothing_exported_without_kanban_cfg(sb: _Sandbox) -> None:
    assert _pm_mode_after_prelude(sb) == "<unset>"


def test_exported_value_beats_kanban_cfg(sb: _Sandbox) -> None:
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    assert _pm_mode_after_prelude(sb, {"PGAI_KANBAN_PM_MODE": "automatic"}) == "automatic"


def test_user_cfg_value_beats_kanban_cfg_and_is_exported(sb: _Sandbox) -> None:
    # A plain (non-exported) assignment in the user config: the step reads
    # kanban.cfg only after sourcing it, and exports whatever value wins.
    sb.write_kanban_cfg("[chain]\npm_mode = automatic\n")
    sb.user_cfg.write_text("PGAI_KANBAN_PM_MODE=manual\n", encoding="utf-8")
    assert _pm_mode_after_prelude(sb) == "manual"


def test_user_cfg_is_sourced_before_kanban_cfg_is_read(sb: _Sandbox, tmp_path: pathlib.Path) -> None:
    seen = tmp_path / "seen"
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    sb.user_cfg.write_text(
        f'printf "%s" "${{PGAI_KANBAN_PM_MODE-<unset>}}" > {str(seen)!r}\n', encoding="utf-8"
    )
    assert _pm_mode_after_prelude(sb) == "manual"
    assert seen.read_text() == "<unset>"


def test_root_defaults_to_kanban_root_path(sb: _Sandbox) -> None:
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    result = sb.run('config_startup_prelude\nprintf "%s" "$PGAI_KANBAN_PM_MODE"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "manual"


# ---------------------------------------------------------------------------
# User config sourcing and idempotency
# ---------------------------------------------------------------------------

def test_user_cfg_assignments_reach_the_caller(sb: _Sandbox) -> None:
    sb.user_cfg.write_text("OPERATOR_TUNABLE=from-user-cfg\n", encoding="utf-8")
    result = sb.run('config_startup_prelude "$KANBAN_ROOT"\nprintf "%s" "${OPERATOR_TUNABLE-}"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "from-user-cfg"


def test_second_call_is_a_no_op(sb: _Sandbox, tmp_path: pathlib.Path) -> None:
    counter = tmp_path / "sourced"
    sb.user_cfg.write_text(f'echo x >> {str(counter)!r}\n', encoding="utf-8")
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    other = tmp_path / "other"
    other.mkdir()
    (other / "kanban.cfg").write_text("[chain]\npm_mode = automatic\n", encoding="utf-8")
    result = sb.run(
        'config_startup_prelude "$KANBAN_ROOT"\n'
        'unset PGAI_KANBAN_PM_MODE\n'
        f'config_startup_prelude {str(other)!r}\n'
        'printf "%s" "${PGAI_KANBAN_PM_MODE-<unset>}"'
    )
    assert result.returncode == 0, result.stderr
    assert counter.read_text().count("x") == 1
    assert result.stdout == "<unset>"


def test_child_shell_runs_its_own_step(sb: _Sandbox, tmp_path: pathlib.Path) -> None:
    counter = tmp_path / "sourced"
    sb.user_cfg.write_text(f'echo x >> {str(counter)!r}\n', encoding="utf-8")
    result = sb.run(
        'config_startup_prelude "$KANBAN_ROOT"\n'
        f'bash -c "source {_CONFIG_LOADER} && config_startup_prelude"'
    )
    assert result.returncode == 0, result.stderr
    assert counter.read_text().count("x") == 2


def test_returns_zero_under_strict_mode_with_nothing_to_source(sb: _Sandbox) -> None:
    result = sb.run('set -euo pipefail\nconfig_startup_prelude "$KANBAN_ROOT"\necho reached')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "reached"
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# Converted entry points
# ---------------------------------------------------------------------------

def _live_lines(path: pathlib.Path) -> list[tuple[int, str]]:
    return [
        (n, line)
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not line.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("rel", _CONVERTED_SCRIPTS)
def test_entry_point_calls_step_once_after_bashrc_and_env(rel: str) -> None:
    lines = _live_lines(_SCRIPTS_DIR / rel)
    calls = [n for n, line in lines if _CALL_RE.search(line)]
    assert len(calls) == 1, f"{rel}: expected one config_startup_prelude call, found lines {calls}"
    for pattern in (_BASHRC_RE, _ENV_RE):
        for n, line in lines:
            if pattern.search(line):
                assert n < calls[0], (
                    f"{rel}:{n} sources {line.strip()!r} after the startup step at line "
                    f"{calls[0]}; the user config must stay after <root>/bashrc and <root>/env"
                )


@pytest.mark.parametrize("rel", _CONVERTED_SCRIPTS)
def test_entry_point_carries_no_copy_of_the_step(rel: str) -> None:
    for n, line in _live_lines(_SCRIPTS_DIR / rel):
        assert _PM_MODE_COPY not in line, f"{rel}:{n}: {line.strip()}"
        assert ".config/pgai-kanban.cfg" not in line, f"{rel}:{n}: {line.strip()}"


def test_entry_point_sources_user_cfg_after_bashrc_and_env(sb: _Sandbox) -> None:
    # verify-rc-state.sh is read-only; against an empty root it stops at
    # project resolution, after its startup block has run.
    trace = sb.root / "trace"
    (sb.root / "bashrc").write_text(f"echo bashrc >> {str(trace)!r}\n", encoding="utf-8")
    (sb.root / "env").write_text(f"echo env >> {str(trace)!r}\n", encoding="utf-8")
    sb.user_cfg.write_text(f"echo user-cfg >> {str(trace)!r}\n", encoding="utf-8")
    subprocess.run(
        ["bash", str(_SCRIPTS_DIR / "verify-rc-state.sh"), "no-such-project"],
        capture_output=True, text=True, env=sb.env(), timeout=60,
    )
    assert trace.read_text().split() == ["bashrc", "env", "user-cfg"]


def _write_demo_project(sb: _Sandbox) -> None:
    """A one-project registry the read-only dashboard renderers can resolve."""
    (sb.root / "projects.cfg").write_text("[project:demo]\npriority = 1\n", encoding="utf-8")
    proj = sb.root / "projects" / "demo"
    (proj / "tasks" / "queues").mkdir(parents=True)
    (proj / "project.cfg").write_text(
        "[project]\nproject_name = demo\nworkflow_type = release\n", encoding="utf-8"
    )


def test_status_bar_shows_user_cfg_pm_mode_and_keeps_its_stderr_quiet(sb: _Sandbox) -> None:
    # The status-bar scripts run the step quietly: the user config's stderr
    # never reaches the tmux status line, and its value beats kanban.cfg.
    _write_demo_project(sb)
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    sb.user_cfg.write_text(
        "echo user-cfg-noise >&2\nexport PGAI_KANBAN_PM_MODE=automatic\n", encoding="utf-8"
    )
    result = subprocess.run(
        ["bash", str(_SCRIPTS_DIR / "dashboard" / "status-right.sh"), str(sb.root), "demo"],
        capture_output=True, text=True, env=sb.env(), timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "PM:auto" in result.stdout
    assert "user-cfg-noise" not in result.stderr


def test_dashboard_renderer_runs_the_user_cfg(sb: _Sandbox) -> None:
    # show-attention.sh did not source the user config before it called the
    # step; a read-only render now runs it.
    _write_demo_project(sb)
    sb.write_kanban_cfg("[chain]\npm_mode = manual\n")
    trace = sb.root / "trace"
    sb.user_cfg.write_text(f"echo user-cfg >> {str(trace)!r}\n", encoding="utf-8")
    subprocess.run(
        ["bash", str(_SCRIPTS_DIR / "dashboard" / "show-attention.sh"),
         "--kanban-root", str(sb.root), "--project", "demo"],
        capture_output=True, text=True, env=sb.env(), timeout=60,
    )
    assert "user-cfg" in trace.read_text().split()
