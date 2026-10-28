"""
test_operator_remedy_installed_script.py
========================================
Behavioral tests that an operator verb's refusal remedy names the script an
installed kanban carries under its root -- ``<root>/scripts/<script>`` --
shell-quoted, so the printed command runs as typed from any directory. An
installed kanban has no ``team/`` directory, so a remedy naming
``team/scripts/<script>`` fails as printed.

Every run is against a throwaway kanban root whose path contains a space
(the case shell-quoting exists for), laid out as installed: its
``scripts/`` is a symlink to this tree's ``team/scripts/`` and each verb is
run through it, so a script that names itself by its own location (one
that refuses before the root is resolved) also lands under the root. The
environment is built from an explicit, minimal set of names
(tests/fixtures/scrubbed_subprocess_env.py) with the five inherited root
names set or cleared together, so the operator's live install is never
reached (project README, "Live-install safety"). Each refusal fires before
the verb touches anything under the root.

Coverage:

  (1) why.sh --details with no project resolvable refuses with
      why.details_requires_project, and shlex.split of the remedy's command
      part has <root>/scripts/why.sh as its first word.
  (2) The same holds for the other argument-shape refusals of the bash
      operator verbs: verdict-report.sh, defer.sh, undefer.sh, wake-now.sh
      and verify-rc-state.sh.
"""

from __future__ import annotations

import pathlib
import shlex
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPTS_DIR = _TEAM_DIR / "scripts"


@pytest.fixture()
def spaced_root(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """A throwaway kanban root with a space in its path and an empty
    registry, plus a scratch HOME and temp directory outside it."""
    root = tmp_path / "kanban root"
    home = tmp_path / "home"
    temp_dir = tmp_path / "scratch"
    for directory in (root, home, temp_dir):
        directory.mkdir()
    (root / "projects.cfg").write_text("", encoding="utf-8")
    (root / "scripts").symlink_to(_SCRIPTS_DIR, target_is_directory=True)
    return {"root": root, "home": home, "temp_dir": temp_dir}


def _run(script: str, args: list[str], spaced_root: dict[str, pathlib.Path]) -> subprocess.CompletedProcess:
    """Run *script* through *spaced_root*'s ``scripts/`` against that root.
    Neither PGAI_PROJECT_NAME, PGAI_TASKS_DIR nor PGAI_PROJECT_ROOT is set,
    so no project is resolvable."""
    extra_env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(spaced_root["root"]),
        "KANBAN_ROOT": str(spaced_root["root"]),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(spaced_root["temp_dir"]),
    }
    return subprocess.run(
        ["bash", str(spaced_root["root"] / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=spaced_root["home"], extra_env=extra_env),
        cwd=str(spaced_root["temp_dir"]),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _remedy_command_words(stderr: str, lead: str) -> list[str]:
    """The shell words of the command that follows *lead* in the remedy line."""
    remedy = [line for line in stderr.splitlines() if line.startswith("remedy:")]
    assert remedy, f"no remedy line: {stderr!r}"
    assert lead in remedy[0], f"remedy does not carry {lead!r}: {remedy[0]!r}"
    return shlex.split(remedy[0].split(lead, 1)[1].rstrip("."))


# ---------------------------------------------------------------------------
# (1) why.sh --details with no project resolvable
# ---------------------------------------------------------------------------


def test_why_details_with_no_project_names_the_installed_why_sh_shell_quoted(spaced_root) -> None:
    result = _run("why.sh", ["--details"], spaced_root)

    assert result.returncode == 1, result.stderr
    assert "error:   why.details_requires_project" in result.stderr, result.stderr
    words = _remedy_command_words(result.stderr, "e.g. ")
    assert words == [f"{spaced_root['root']}/scripts/why.sh", "--project", "pgai-agent-kanban", "--details"], words
    assert "team/scripts/" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# (2) The other bash operator verbs' argument-shape refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script, args, error_key, lead",
    [
        ("verdict-report.sh", ["--details"], "verdict_report.details_requires_project", "e.g. "),
        ("defer.sh", [], "defer.project_required", "e.g. "),
        ("undefer.sh", [], "undefer.project_required", "e.g. "),
        ("wake-now.sh", [], "wake_now.agent_required", "Run "),
        ("wake-now.sh", ["--agent", "no-such-agent"], "wake_now.unknown_agent", "Run "),
        ("verify-rc-state.sh", [], "verify_rc_state.project_required", "e.g. "),
        ("verify-rc-state.sh", ["alpha", "beta"], "verify_rc_state.unexpected_positional_argument", "e.g. "),
    ],
    ids=[
        "verdict-report-details",
        "defer-no-project",
        "undefer-no-project",
        "wake-now-no-agent",
        "wake-now-unknown-agent",
        "verify-rc-state-no-project",
        "verify-rc-state-two-projects",
    ],
)
def test_operator_verb_refusal_names_its_installed_script_shell_quoted(
    spaced_root, script, args, error_key, lead
) -> None:
    result = _run(script, args, spaced_root)

    assert result.returncode != 0, result.stderr
    assert f"error:   {error_key}" in result.stderr, result.stderr
    words = _remedy_command_words(result.stderr, lead)
    assert words[0] == f"{spaced_root['root']}/scripts/{script}", words
    assert "team/scripts/" not in result.stderr, result.stderr
