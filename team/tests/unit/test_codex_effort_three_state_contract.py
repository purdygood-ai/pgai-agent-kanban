"""
test_codex_effort_three_state_contract.py
============================================
Acceptance-gate suite for the reasoning-effort controls on the Codex wake
lane (v2.1.0-reasoning-effort-controls, Acceptance items 1-4).

Where team/tests/unit/test_wake_codex_effort_wiring.py (CODER-20260728-013)
exercises provider_invoke_agent's command assembly with a hand-picked
selected_effort/effort_source pair, this suite ties the REAL resolution
function -- config_loader.sh's resolve_role_effort() -- to the REAL command
assembly in one process, so the three-state contract (absent / valid /
invalid) and the env-beats-cfg precedence are proven end to end from cfg/env
input through to the assembled codex command, the wake-log narration, and
the cost artifact -- not just at the command-assembly layer in isolation.

Tests source the REAL production config_loader.sh and wake_codex_
provider.sh and invoke the real resolve_role_effort() and
provider_invoke_agent() functions directly (not a reimplementation), with a
fake ``codex`` executable placed first on PATH that records its argv and
emits a minimal JSONL event stream (turn.completed usage block).

Naming: function names describe behavior under test, never bug IDs or task
IDs (see SOP.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPT_DIR = _TEAM_DIR / "scripts" / "wake"
_INI_PARSER = _TEAM_DIR / "scripts" / "lib" / "ini_parser.sh"
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_PROVIDER = _TEAM_DIR / "scripts" / "lib" / "wake_codex_provider.sh"

# The assembled command shape with no model and no effort resolved --
# captured from the pre-effort behavior (identical to the baseline asserted
# in test_wake_codex_effort_wiring.py::test_no_effort_override_when_absent).
# Compared verbatim below so an absent-effort regression that changes flag
# shape, order, or count is caught, not just "no -c override present".
_BASELINE_ABSENT_EFFORT_ARGV = [
    "exec",
    "--json",
    "--dangerously-bypass-approvals-and-sandbox",
    "test prompt",
]

_FAKE_CODEX_SCRIPT = """\
#!/usr/bin/env bash
# Fake codex CLI for effort-contract tests: records argv, emits a minimal
# JSONL event stream (turn.completed usage block), exits 0.
printf '%s\\n' "$@" > "$ARGV_CAPTURE_FILE"
cat <<'JSONL'
{"type":"thread.started"}
{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1,"cached_input_tokens":0}}
JSONL
exit 0
"""


def _write_fake_codex(bin_dir: pathlib.Path) -> pathlib.Path:
    """Install a fake `codex` executable at the front of a PATH bin dir."""
    fake = bin_dir / "codex"
    fake.write_text(_FAKE_CODEX_SCRIPT, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _write_cfg(tmp_path: pathlib.Path, *, llm_effort: str | None) -> pathlib.Path:
    """A minimal kanban.cfg, with an optional [providers] llm_effort key."""
    lines = ["[providers]", "active = codex"]
    if llm_effort is not None:
        lines.append(f"llm_effort = {llm_effort}")
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


def _base_env(bin_dir: pathlib.Path, fake_home: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_CODER_EFFORT", None)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    # Isolate from any real ~/.codex/config.toml on the host --
    # codex_read_model_from_config reads $HOME/.codex/config.toml, and this
    # suite must not depend on (or be perturbed by) the operator's real
    # Codex config.
    env["HOME"] = str(fake_home)
    return env


def _resolve_and_invoke(
    tmp_path: pathlib.Path,
    *,
    llm_effort: str | None,
    env_effort: str | None,
) -> subprocess.CompletedProcess:
    """Resolve reasoning-effort via the REAL resolve_role_effort() (fed by a
    real kanban.cfg and an optional real env var), then feed the resolved
    value/source straight into the REAL provider_invoke_agent() -- proving
    the resolution-to-dispatch pipeline end to end in one subprocess.

    Returns the CompletedProcess. Callers inspect stdout markers
    (RESOLVE_EXIT=, ARGV file, log file, artifact dir) to make assertions.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_codex(bin_dir)
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    cfg = _write_cfg(tmp_path, llm_effort=llm_effort)
    log_file = tmp_path / "wake.log"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    argv_capture_file = tmp_path / "argv.txt"

    env = _base_env(bin_dir, fake_home)
    if env_effort is not None:
        env["PGAI_CODER_EFFORT"] = env_effort
    env["ARGV_CAPTURE_FILE"] = str(argv_capture_file)

    # load_config's own per-role export loop populates PGAI_CODER_EFFORT from
    # cfg as a side effect (mirroring PGAI_<ROLE>_MODEL). When this test is
    # NOT exercising a genuine operator-set env override, that cfg-derived
    # export must be cleared before the harness's own resolve_role_effort
    # call below -- otherwise resolve_role_effort would see the now-set var
    # and misreport the source as "env" even though it originated from cfg
    # (the documented tier-blurring behavior the model mechanism already
    # has; see CODER-20260728-010's test suite for the same discipline).
    _unset_cfg_derived_export = "" if env_effort is not None else "unset PGAI_CODER_EFFORT\n"

    harness = f"""
set -uo pipefail
export SCRIPT_DIR={str(_SCRIPT_DIR)!r}
export AGENT=coder
source {str(_INI_PARSER)!r}
source {str(_CONFIG_LOADER)!r}
load_config {str(cfg)!r}
{_unset_cfg_derived_export}
_resolved="$(resolve_role_effort coder)"
_resolve_exit=$?
echo "RESOLVE_EXIT=${{_resolve_exit}}"
if [[ ${{_resolve_exit}} -ne 0 ]]; then
  exit ${{_resolve_exit}}
fi
selected_effort="${{_resolved%%|*}}"
effort_source="${{_resolved#*|}}"
echo "RESOLVED_EFFORT=[${{selected_effort}}]"
echo "RESOLVED_SOURCE=[${{effort_source}}]"

source {str(_PROVIDER)!r}
provider_invoke_agent \\
  "test prompt" \\
  "" \\
  "test model source" \\
  "${{selected_effort}}" \\
  "${{effort_source}}" \\
  "TASK-EFFORT-CONTRACT-TEST" \\
  {str(artifact_dir)!r} \\
  {str(log_file)!r} \\
  "coder"
echo "PROVIDER_AGENT_EXIT_CODE=${{PROVIDER_AGENT_EXIT_CODE}}"
"""

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result


def _read_argv(tmp_path: pathlib.Path) -> list[str]:
    argv_capture_file = tmp_path / "argv.txt"
    if not argv_capture_file.exists():
        return []
    return argv_capture_file.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# State 1 of 3: absent -> no -c model_reasoning_effort override;
# byte-identical to baseline.
# ---------------------------------------------------------------------------


def test_absent_effort_state_matches_pre_change_baseline_command(
    tmp_path: pathlib.Path,
) -> None:
    """With neither [providers] llm_effort nor PGAI_CODER_EFFORT set,
    resolve_role_effort resolves nothing, no -c model_reasoning_effort
    override is appended, and the assembled command is byte-identical to
    the pre-effort baseline."""
    result = _resolve_and_invoke(tmp_path, llm_effort=None, env_effort=None)

    assert result.returncode == 0, (
        f"harness failed:\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "RESOLVE_EXIT=0" in result.stdout
    assert "RESOLVED_EFFORT=[]" in result.stdout

    argv = _read_argv(tmp_path)
    assert argv == _BASELINE_ABSENT_EFFORT_ARGV, (
        f"Absent-effort command diverged from the pre-change baseline.\n"
        f"Expected: {_BASELINE_ABSENT_EFFORT_ARGV}\nGot: {argv}"
    )

    tokens_json = tmp_path / "artifacts" / "tokens.json"
    assert tokens_json.exists()
    data = json.loads(tokens_json.read_text(encoding="utf-8"))
    assert "effort" not in data, f"Unexpected 'effort' key when absent: {data}"


# ---------------------------------------------------------------------------
# State 2 of 3: valid -> override present with the value, cost artifact
# carries it.
# ---------------------------------------------------------------------------


def test_valid_cfg_effort_state_appends_override_and_records_cost_artifact(
    tmp_path: pathlib.Path,
) -> None:
    """A valid [providers] llm_effort value resolves via the cfg tier,
    appends '-c model_reasoning_effort=<value>' to the assembled command,
    narrates the value and cfg source on the wake log, and lands on the
    tokens.json cost artifact."""
    result = _resolve_and_invoke(tmp_path, llm_effort="high", env_effort=None)

    assert result.returncode == 0, (
        f"harness failed:\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "RESOLVED_EFFORT=[high]" in result.stdout
    assert "RESOLVED_SOURCE=[[providers] llm_effort cfg]" in result.stdout

    argv = _read_argv(tmp_path)
    assert "-c" in argv, f"Expected '-c' in assembled argv: {argv}"
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high"

    log_text = (tmp_path / "wake.log").read_text(encoding="utf-8")
    assert "effort: high (from [providers] llm_effort cfg)" in log_text, (
        f"Expected effort narration naming value and cfg source; got:\n{log_text}"
    )

    tokens_json = tmp_path / "artifacts" / "tokens.json"
    data = json.loads(tokens_json.read_text(encoding="utf-8"))
    assert data.get("effort") == "high", f"Expected effort='high'; got: {data}"


# ---------------------------------------------------------------------------
# State 3 of 3: invalid -> fail loud, naming the exact supplying key.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "llm_effort, env_effort, expected_stderr_substrings",
    [
        pytest.param(
            "ludicrous", None,
            ["[providers] llm_effort", "ludicrous"],
            id="invalid_cfg_value_names_cfg_key",
        ),
        pytest.param(
            None, "bogus",
            ["PGAI_CODER_EFFORT", "bogus"],
            id="invalid_env_value_names_env_var",
        ),
    ],
)
def test_invalid_effort_state_fails_loud_naming_the_key(
    tmp_path: pathlib.Path,
    llm_effort: str | None,
    env_effort: str | None,
    expected_stderr_substrings: list[str],
) -> None:
    """An unrecognized effort value -- from either the cfg key or the env var
    -- makes resolve_role_effort fail before any provider dispatch happens,
    naming the exact supplying key and the offending value in the error."""
    result = _resolve_and_invoke(tmp_path, llm_effort=llm_effort, env_effort=env_effort)

    assert result.returncode != 0, (
        f"Expected non-zero exit for invalid effort value; "
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    for substring in expected_stderr_substrings:
        assert substring in result.stderr, (
            f"Expected {substring!r} named in the error; stderr: {result.stderr!r}"
        )
    # No provider dispatch happened -- no argv was ever captured.
    assert not (tmp_path / "argv.txt").exists(), (
        "provider_invoke_agent must not run when effort resolution fails."
    )


# ---------------------------------------------------------------------------
# Env-beats-cfg precedence, proven through the full resolution-to-dispatch
# pipeline, with narration naming the winning source.
# ---------------------------------------------------------------------------


def test_env_override_beats_cfg_and_narrates_the_winning_source(
    tmp_path: pathlib.Path,
) -> None:
    """When both [providers] llm_effort and PGAI_CODER_EFFORT are set to
    different valid values, the env value wins end to end: it is the value
    resolve_role_effort returns, the value appended to the assembled
    command, and the value narrated on the wake log -- with the source
    explicitly naming the env var, not the cfg key."""
    result = _resolve_and_invoke(tmp_path, llm_effort="medium", env_effort="high")

    assert result.returncode == 0, (
        f"harness failed:\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "RESOLVED_EFFORT=[high]" in result.stdout, (
        "Expected the env value ('high') to win over the cfg value ('medium')."
    )
    assert "RESOLVED_SOURCE=[PGAI_CODER_EFFORT env]" in result.stdout, (
        "Expected the winning source to name the env var, not the cfg key."
    )

    argv = _read_argv(tmp_path)
    assert "-c" in argv
    assert argv[argv.index("-c") + 1] == "model_reasoning_effort=high", (
        f"Expected the assembled command to carry the env value 'high', not "
        f"the cfg value 'medium': {argv}"
    )

    log_text = (tmp_path / "wake.log").read_text(encoding="utf-8")
    assert "effort: high (from PGAI_CODER_EFFORT env)" in log_text, (
        f"Expected the wake log to narrate the env source as the winner; got:\n{log_text}"
    )
    assert "from [providers] llm_effort cfg" not in log_text, (
        f"Wake log incorrectly narrated the losing cfg source; got:\n{log_text}"
    )
