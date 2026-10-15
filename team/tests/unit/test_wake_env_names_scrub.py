"""
test_wake_env_names_scrub.py
============================
Pins that gated test runs, and the fixtures that build a sandboxed wake's
environment, never inherit the [wake] tunables a wake's own config load
exports into every agent it dispatches.

WHY THIS MATTERS
-----------------
team/scripts/lib/config_loader.sh's load_config exports every [wake] key it
resolves (LOCK_WAIT_SECONDS, MAX_TASKS_PER_WAKE, ...), and gives a variable
that is already set precedence over the cfg value -- the documented operator
override. A gated runner started inside an agent wake therefore hands
pytest the live wake's values, and a test whose sandboxed kanban.cfg pins,
say, lock_wait_seconds = 0 sees its wake obey the inherited 120 instead.

WHAT THIS FILE PROVES
----------------------
  1. The loader's CONFIG_LOADER_WAKE_ENV_NAMES array, the Python twin
     WAKE_TUNABLE_ENV_NAMES (tests/fixtures/scrubbed_subprocess_env.py), and
     the names load_config actually exports for [wake] are one and the same
     set -- extracted from the loader's source and confirmed by running it.
  2. Both gated runners carry the scrub statement and the replay-family
     registration, identically, ahead of the replay banner and the pytest
     launch; running those exact extracted statements in a shell that
     exports every name leaves none of them visible to a child, and the
     replay command built afterwards keeps them absent when pasted into a
     shell that exports them all.
  3. Each runner's header comment names every variable it drops.
  4. scrub_wake_tunable_env removes exactly that family and nothing else.

The operator override itself is pinned elsewhere:
test_config_loader_key_validation.py (load_config level) and
tests/integration/test_wake_contention_retry.py (through a real wake).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import (
    WAKE_TUNABLE_ENV_NAMES,
    scrub_wake_tunable_env,
)

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_GATED_RUNNER_LINT_SH = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_RUN_UNIT_SH = _TEAM_DIR / "scripts" / "run-unit-tests.sh"
_RUN_INTEGRATION_SH = _TEAM_DIR / "scripts" / "run-integration-tests.sh"
_RUNNERS = [_RUN_UNIT_SH, _RUN_INTEGRATION_SH]

# load_config's two [wake] export shapes: the integer resolver call, and the
# plain `export NAME="${NAME:-$(read_ini "$cfg" wake key ...)}"` form.
_INTEGER_EXPORT_RE = re.compile(
    r'^\s*_config_loader_export_wake_integer\s+"\$cfg"\s+\w+\s+([A-Z][A-Z0-9_]*)\b', re.MULTILINE
)
_PLAIN_EXPORT_RE = re.compile(
    r'^\s*export\s+([A-Z][A-Z0-9_]*)="\$\{\1:-\$\(read_ini\s+"\$cfg"\s+wake\s', re.MULTILINE
)

_SCRUB_RE = re.compile(
    r'^[ \t]*unset\s+"\$\{CONFIG_LOADER_WAKE_ENV_NAMES\[@\]\}"[ \t]*$', re.MULTILINE
)
_REPLAY_REGISTER_RE = re.compile(
    r'^[ \t]*GATED_RUNNER_REPLAY_ENV_NAMES\+=\(\s*"\$\{CONFIG_LOADER_WAKE_ENV_NAMES\[@\]\}"\s*\)[ \t]*$',
    re.MULTILINE,
)
_REPLAY_BANNER_RE = re.compile(r'^[ \t]*echo "Replay this exact run:', re.MULTILINE)
_PYTEST_INVOCATION_RE = re.compile(
    r'^[ \t]*(?:\S+\s+)?python3 -m pytest "\$\{(?:PYTEST_ARGS|GATED_RUNNER_LANE_ARGS)\[@\]\}"',
    re.MULTILINE,
)


def _bash(script: str, env: dict, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=timeout
    )


def _env_without_wake_names() -> dict:
    env = dict(os.environ)
    for name in WAKE_TUNABLE_ENV_NAMES:
        env.pop(name, None)
    # config_loader's one-character glyph check counts characters, which
    # needs a UTF-8 locale (see test_config_loader_key_validation.py).
    env["LC_ALL"] = "C.UTF-8"
    return env


def _loader_array() -> list[str]:
    result = _bash(
        f'source {str(_CONFIG_LOADER)!r} && printf "%s\\n" "${{CONFIG_LOADER_WAKE_ENV_NAMES[@]}}"',
        _env_without_wake_names(),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def _loader_wake_exports_from_source() -> set[str]:
    text = _CONFIG_LOADER.read_text(encoding="utf-8")
    return set(_INTEGER_EXPORT_RE.findall(text)) | set(_PLAIN_EXPORT_RE.findall(text))


def _probe_script(tmp_path: pathlib.Path) -> pathlib.Path:
    """A python probe that prints, as JSON, which wake names it can see."""
    probe = tmp_path / "probe_wake_names.py"
    probe.write_text(
        "import json, os, sys\n"
        f"names = {list(WAKE_TUNABLE_ENV_NAMES)!r}\n"
        "sys.stdout.write(json.dumps(sorted(n for n in names if n in os.environ)))\n",
        encoding="utf-8",
    )
    return probe


def _env_exporting_every_wake_name() -> dict:
    env = _env_without_wake_names()
    for name in WAKE_TUNABLE_ENV_NAMES:
        env[name] = "99"
    return env


# ---------------------------------------------------------------------------
# 1. One name set: bash array, Python twin, and what load_config exports
# ---------------------------------------------------------------------------


def test_loader_array_matches_every_wake_export_in_load_config() -> None:
    exported = _loader_wake_exports_from_source()
    assert "LOCK_WAIT_SECONDS" in exported and "STOP_FILE" in exported, (
        f"export extraction looks broken (got {sorted(exported)}); the regexes in "
        "this file may need updating for a changed load_config shape"
    )
    array = _loader_array()
    assert len(array) == len(set(array)), f"duplicate name in CONFIG_LOADER_WAKE_ENV_NAMES: {array}"
    assert set(array) == exported, (
        "CONFIG_LOADER_WAKE_ENV_NAMES in config_loader.sh must name exactly the "
        "[wake] variables load_config exports.\n"
        f"missing from the array: {sorted(exported - set(array))}\n"
        f"in the array but not exported: {sorted(set(array) - exported)}"
    )


def test_python_twin_matches_the_loader_array() -> None:
    assert sorted(WAKE_TUNABLE_ENV_NAMES) == sorted(_loader_array()), (
        "WAKE_TUNABLE_ENV_NAMES (tests/fixtures/scrubbed_subprocess_env.py) and "
        "CONFIG_LOADER_WAKE_ENV_NAMES (team/scripts/lib/config_loader.sh) disagree"
    )


def test_load_config_exports_every_listed_name(tmp_path: pathlib.Path) -> None:
    """Running load_config on a cfg with an empty [wake] section exports
    every listed name -- the list carries no name the loader never sets."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("[wake]\n", encoding="utf-8")
    script = (
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg)!r} >/dev/null && "
        'for n in "${CONFIG_LOADER_WAKE_ENV_NAMES[@]}"; do declare -p "$n"; done'
    )
    result = _bash(script, _env_without_wake_names())
    assert result.returncode == 0, result.stderr
    not_exported = [
        line for line in result.stdout.splitlines() if not re.match(r"^declare -\S*x\S* ", line)
    ]
    assert not not_exported, f"listed but not exported by load_config: {not_exported}"


# ---------------------------------------------------------------------------
# 2. Both gated runners scrub the family before the replay banner and pytest
# ---------------------------------------------------------------------------


def _runner_statements(script: pathlib.Path) -> tuple[re.Match, re.Match]:
    text = script.read_text(encoding="utf-8")
    scrub = _SCRUB_RE.search(text)
    register = _REPLAY_REGISTER_RE.search(text)
    assert scrub is not None, (
        f'{script.name} must carry `unset "${{CONFIG_LOADER_WAKE_ENV_NAMES[@]}}"` '
        "ahead of its pytest launch"
    )
    assert register is not None, (
        f'{script.name} must register the wake names in the replay family with '
        '`GATED_RUNNER_REPLAY_ENV_NAMES+=("${CONFIG_LOADER_WAKE_ENV_NAMES[@]}")`'
    )
    return scrub, register


@pytest.mark.parametrize("script", _RUNNERS, ids=lambda p: p.name)
def test_runner_scrubs_wake_names_before_replay_banner_and_pytest(script: pathlib.Path) -> None:
    text = script.read_text(encoding="utf-8")
    scrub, register = _runner_statements(script)
    banner = _REPLAY_BANNER_RE.search(text)
    launch = _PYTEST_INVOCATION_RE.search(text)
    assert banner is not None and launch is not None, (
        f"{script.name}: replay banner or pytest launch line not found -- the "
        "patterns in this file may need updating"
    )
    for label, match in (("scrub", scrub), ("replay registration", register)):
        assert match.start() < banner.start(), f"{script.name}: {label} must precede the replay banner"
        assert match.start() < launch.start(), f"{script.name}: {label} must precede the pytest launch"


def test_both_runners_carry_identical_wake_scrub_statements() -> None:
    unit = [m.group(0).strip() for m in _runner_statements(_RUN_UNIT_SH)]
    integration = [m.group(0).strip() for m in _runner_statements(_RUN_INTEGRATION_SH)]
    assert unit == integration


@pytest.mark.parametrize("script", _RUNNERS, ids=lambda p: p.name)
def test_runner_scrub_clears_every_wake_name_and_replay_keeps_them_absent(
    script: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Run the runner's own extracted statements in a shell exporting every
    wake name (an agent wake's state): a child sees none of them, and the
    replay command built afterwards, pasted into a shell that exports them
    all, still hands its child none of them."""
    scrub, register = _runner_statements(script)
    probe = _probe_script(tmp_path)
    stage1 = _bash(
        "set -euo pipefail\n"
        f"source {str(_CONFIG_LOADER)!r}\n"
        f"source {str(_GATED_RUNNER_LINT_SH)!r}\n"
        f"{scrub.group(0)}\n"
        f"{register.group(0)}\n"
        f"python3 {str(probe)!r}\n"
        "echo\n"
        f"gated_runner_replay_command {('python3 ' + str(probe))!r}\n",
        _env_exporting_every_wake_name(),
    )
    assert stage1.returncode == 0, f"stdout:\n{stage1.stdout}\nstderr:\n{stage1.stderr}"
    seen_line, replay_command = stage1.stdout.split("\n", 1)
    assert json.loads(seen_line) == [], (
        f"{script.name}'s scrub left these wake names visible to pytest: {seen_line}"
    )
    replay_command = replay_command.strip()
    for name in WAKE_TUNABLE_ENV_NAMES:
        assert f"-u {name}" in replay_command, (
            f"replay command must force {name} absent; got: {replay_command}"
        )

    stage2 = _bash(replay_command, _env_exporting_every_wake_name())
    assert stage2.returncode == 0, f"command: {replay_command}\nstderr:\n{stage2.stderr}"
    assert json.loads(stage2.stdout) == [], (
        "the pasted replay command let the operator shell's wake names reach its "
        f"child: {stage2.stdout} (command: {replay_command})"
    )


# ---------------------------------------------------------------------------
# 3. Runner headers name what they drop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", _RUNNERS, ids=lambda p: p.name)
def test_runner_header_names_every_dropped_wake_variable(script: pathlib.Path) -> None:
    header_lines = []
    for line in script.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        header_lines.append(line)
    header = "\n".join(header_lines)
    missing = [name for name in WAKE_TUNABLE_ENV_NAMES if not re.search(rf"\b{name}\b", header)]
    assert not missing, f"{script.name}'s header comment does not name: {missing}"


# ---------------------------------------------------------------------------
# 4. The fixture helper
# ---------------------------------------------------------------------------


def test_scrub_wake_tunable_env_removes_the_family_and_keeps_everything_else() -> None:
    env = {name: "1" for name in WAKE_TUNABLE_ENV_NAMES}
    env.update({"PATH": "/usr/bin", "PGAI_AGENT_KANBAN_ROOT_PATH": "/sandbox", "MAX_TASKS": "4"})
    returned = scrub_wake_tunable_env(env)
    assert returned is env
    assert env == {"PATH": "/usr/bin", "PGAI_AGENT_KANBAN_ROOT_PATH": "/sandbox", "MAX_TASKS": "4"}
