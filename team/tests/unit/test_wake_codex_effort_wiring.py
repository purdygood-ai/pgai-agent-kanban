"""
test_wake_codex_effort_wiring.py
===================================
Behavioral unit tests for the reasoning-effort wiring in the Codex wake
lane.

v2.9.20-engine-decomposition split the former
wake_codex_provider.sh's single provider_invoke_agent function into the
four-function provider interface (lib/provider_codex.sh:
provider_env_preflight, provider_model_preflight, provider_dispatch,
provider_parse_output) plus the provider-neutral token-capture orchestrator
already centralized in lib/wake_driver_invoke.sh (shared with the Claude
lane): _wd_run_provider_and_capture_tokens. This suite now exercises:

  - provider_codex.sh's provider_dispatch appends
    ``-c model_reasoning_effort=<value>`` to the assembled codex command
    only when a non-empty effort is resolved, and narrates the value and
    its winning source (mirroring the existing model-narration line).
  - wake_driver_invoke.sh's _wd_run_provider_and_capture_tokens threads the
    resolved effort into the token-capture call so the cost artifact
    records it.
  - team/scripts/wake/codex.sh resolves the effort (and its narration
    source) via the shared wake driver's prep stage
    (wake_driver_prep.sh), calling config_loader.sh's
    resolve_role_effort(), mirroring the existing model-resolution block,
    and passes both through to the provider interface.

Tests source the REAL production provider_codex.sh and wake_driver_invoke.sh
and invoke the real provider_dispatch / _wd_run_provider_and_capture_tokens
functions directly (not a reimplementation), with a fake ``codex``
executable placed first on PATH that records its argv and emits a minimal
JSONL event stream (turn.completed usage block). This exercises the real
command-assembly, narration, and token-capture wiring end-to-end in a
subprocess -- matching the "test by running, not by reasoning" bar.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
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
_PROVIDER = _TEAM_DIR / "scripts" / "lib" / "provider_codex.sh"
_DRIVER_INVOKE = _TEAM_DIR / "scripts" / "lib" / "wake_driver_invoke.sh"
_DRIVER_PREP = _TEAM_DIR / "scripts" / "lib" / "wake_driver_prep.sh"
_CODEX_SH = _TEAM_DIR / "scripts" / "wake" / "codex.sh"

_FAKE_CODEX_SCRIPT = """\
#!/usr/bin/env bash
# Fake codex CLI for effort-wiring tests: records argv, emits a minimal
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


def _write_minimal_cfg(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal kanban.cfg sufficient for config_loader.sh's load_config."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("[providers]\nactive = codex\n", encoding="utf-8")
    return cfg


def _invoke_provider(
    tmp_path: pathlib.Path,
    *,
    selected_model: str = "",
    model_source: str = "test model source",
    selected_effort: str = "",
    effort_source: str = "",
) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path]:
    """Invoke the real provider_dispatch function via a bash subprocess.

    Returns (result, argv_capture_file, log_file).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_codex(bin_dir)
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    cfg = _write_minimal_cfg(tmp_path)
    log_file = tmp_path / "wake.log"
    raw_output_file = tmp_path / "raw_output.jsonl"
    argv_capture_file = tmp_path / "argv.txt"

    harness = f"""
set -uo pipefail
export SCRIPT_DIR={str(_SCRIPT_DIR)!r}
export AGENT=coder
source {str(_INI_PARSER)!r}
source {str(_CONFIG_LOADER)!r}
load_config {str(cfg)!r}
source {str(_PROVIDER)!r}

provider_dispatch \\
  "test prompt" \\
  {selected_model!r} \\
  {model_source!r} \\
  {selected_effort!r} \\
  {effort_source!r} \\
  "TASK-EFFORT-TEST" \\
  {str(log_file)!r} \\
  "coder" \\
  {str(raw_output_file)!r}
echo "PROVIDER_AGENT_EXIT_CODE=${{PROVIDER_AGENT_EXIT_CODE}}"
"""

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["ARGV_CAPTURE_FILE"] = str(argv_capture_file)
    # Isolate from any real ~/.codex/config.toml on the host — codex_read_
    # model_from_config reads $HOME/.codex/config.toml, and this suite must
    # not depend on (or be perturbed by) the operator's real Codex config.
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, argv_capture_file, log_file


def _invoke_and_capture_tokens(
    tmp_path: pathlib.Path,
    *,
    selected_model: str = "",
    model_source: str = "test model source",
    selected_effort: str = "",
    effort_source: str = "",
) -> tuple[subprocess.CompletedProcess, pathlib.Path]:
    """Invoke the real _wd_run_provider_and_capture_tokens orchestrator via a
    bash subprocess, exercising provider_dispatch, provider_parse_output, and
    the tokens.json merge end to end.

    _wd_run_provider_and_capture_tokens reads its inputs from bash dynamic
    scope (process_one_task's own locals in production); the harness sets
    those as plain top-level script variables, which is the documented
    contract in wake_driver_invoke.sh's own header.

    Returns (result, artifact_dir).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_codex(bin_dir)
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

    cfg = _write_minimal_cfg(tmp_path)
    log_file = tmp_path / "wake.log"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    argv_capture_file = tmp_path / "argv.txt"
    temp_root = tmp_path / "kanban_tmp"

    harness = f"""
set -uo pipefail
export SCRIPT_DIR={str(_SCRIPT_DIR)!r}
export AGENT=coder
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_INI_PARSER)!r}
source {str(_CONFIG_LOADER)!r}
load_config {str(cfg)!r}
source {str(_PROVIDER)!r}
source {str(_DRIVER_INVOKE)!r}

# Minimal stand-ins for substrate this narrow unit test does not need:
# log() normally comes from wake_bootstrap.sh; get_release_state_field
# normally reads a per-project release-state.md via wake_autonomous_scan.sh.
# _wd_run_provider_and_capture_tokens already falls back to rc_version="none"
# on a non-zero get_release_state_field, so a stub that always fails is
# behaviorally equivalent here to a real project with no RC in flight.
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE"; }}
get_release_state_field() {{ return 1; }}

prompt="test prompt"
selected_model={selected_model!r}
model_source={model_source!r}
selected_effort={selected_effort!r}
effort_source={effort_source!r}
task_id="TASK-EFFORT-TEST"
task_artifact_dir={str(artifact_dir)!r}
SUBAGENT="coder"
LOG_FILE={str(log_file)!r}

_wd_run_provider_and_capture_tokens
echo "PROVIDER_AGENT_EXIT_CODE=${{PROVIDER_AGENT_EXIT_CODE}}"
"""

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["ARGV_CAPTURE_FILE"] = str(argv_capture_file)
    env["HOME"] = str(fake_home)

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, artifact_dir


def _read_argv(argv_capture_file: pathlib.Path) -> list[str]:
    if not argv_capture_file.exists():
        return []
    return argv_capture_file.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# (1) Effort in force -> assembled command includes -c model_reasoning_effort=<value>.
# ---------------------------------------------------------------------------


def test_effort_override_appended_when_resolved(tmp_path: pathlib.Path) -> None:
    """A non-empty selected_effort appends '-c model_reasoning_effort=<value>'
    to codex_cmd."""
    result, argv_capture_file, _log_file = _invoke_provider(
        tmp_path,
        selected_effort="high",
        effort_source="PGAI_CODER_EFFORT env",
    )

    assert result.returncode == 0, (
        f"provider_dispatch harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert "-c" in argv, f"Expected '-c' in assembled argv: {argv}"
    idx = argv.index("-c")
    assert argv[idx + 1] == "model_reasoning_effort=high", (
        f"Expected 'model_reasoning_effort=high' immediately after '-c'; got argv: {argv}"
    )


# ---------------------------------------------------------------------------
# (2) No effort in force -> no -c override; command unchanged from today.
# ---------------------------------------------------------------------------


def test_no_effort_override_when_absent(tmp_path: pathlib.Path) -> None:
    """An empty selected_effort appends no -c model_reasoning_effort override
    at all — the assembled command is byte-identical to pre-effort behavior."""
    result, argv_capture_file, _log_file = _invoke_provider(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"provider_dispatch harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert "-c" not in argv, (
        f"Unexpected '-c' in assembled argv when no effort is resolved: {argv}"
    )
    # The assembled command otherwise matches today's known shape exactly
    # (no -m since selected_model is empty in this fixture).
    assert argv == [
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "test prompt",
    ], f"Assembled command changed shape with effort absent: {argv}"


# ---------------------------------------------------------------------------
# (3) -c ordering: consistent with -m (before --json).
# ---------------------------------------------------------------------------


def test_effort_override_positioned_before_json_flag_like_model_flag(
    tmp_path: pathlib.Path,
) -> None:
    """-c model_reasoning_effort=<value> appears after -m and before --json,
    mirroring -m's ordering."""
    result, argv_capture_file, _log_file = _invoke_provider(
        tmp_path,
        selected_model="gpt-5.3-codex",
        model_source="test model source",
        selected_effort="high",
        effort_source="test effort source",
    )

    assert result.returncode == 0, (
        f"provider_dispatch harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert argv == [
        "exec",
        "-m",
        "gpt-5.3-codex",
        "-c",
        "model_reasoning_effort=high",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "test prompt",
    ], f"Unexpected flag ordering: {argv}"


# ---------------------------------------------------------------------------
# (4) Narration: the wake log names the effort value and its winning source.
# ---------------------------------------------------------------------------


def test_wake_log_narrates_effort_value_and_source(tmp_path: pathlib.Path) -> None:
    """The wake log line names the effort value and the winning source."""
    result, _argv_capture_file, log_file = _invoke_provider(
        tmp_path,
        selected_effort="low",
        effort_source="[providers] llm_effort cfg",
    )

    assert result.returncode == 0, (
        f"provider_dispatch harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "effort: low (from [providers] llm_effort cfg)" in log_text, (
        f"Expected effort narration line naming value and source in log; got:\n{log_text}"
    )


def test_wake_log_has_no_effort_narration_when_absent(tmp_path: pathlib.Path) -> None:
    """No effort narration line is emitted when no effort is resolved."""
    result, _argv_capture_file, log_file = _invoke_provider(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"provider_dispatch harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "effort:" not in log_text, (
        f"Unexpected effort narration line when no effort is resolved; got:\n{log_text}"
    )


# ---------------------------------------------------------------------------
# (5) Cost artifact: the resolved effort reaches tokens.json.
# ---------------------------------------------------------------------------


def test_tokens_json_records_effort_when_resolved(tmp_path: pathlib.Path) -> None:
    """tokens.json carries the resolved effort as a top-level field."""
    result, artifact_dir = _invoke_and_capture_tokens(
        tmp_path,
        selected_effort="high",
        effort_source="PGAI_CODER_EFFORT env",
    )

    assert result.returncode == 0, (
        f"_wd_run_provider_and_capture_tokens harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    tokens_json = artifact_dir / "tokens.json"
    assert tokens_json.exists(), f"Expected tokens.json at {tokens_json}"
    data = json.loads(tokens_json.read_text(encoding="utf-8"))
    assert data.get("effort") == "high", (
        f"Expected tokens.json 'effort' field to be 'high'; got: {data}"
    )


def test_tokens_json_omits_effort_when_absent(tmp_path: pathlib.Path) -> None:
    """tokens.json carries no 'effort' key when no effort was ever resolved."""
    result, artifact_dir = _invoke_and_capture_tokens(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"_wd_run_provider_and_capture_tokens harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    tokens_json = artifact_dir / "tokens.json"
    assert tokens_json.exists(), f"Expected tokens.json at {tokens_json}"
    data = json.loads(tokens_json.read_text(encoding="utf-8"))
    assert "effort" not in data, (
        f"Unexpected 'effort' key in tokens.json when no effort was resolved: {data}"
    )


# ---------------------------------------------------------------------------
# Structural: codex.sh (via the shared wake driver it now sources) resolves
# effort and threads it through to the provider call, mirroring the
# model-resolution block.
# ---------------------------------------------------------------------------


def test_codex_sh_resolves_effort_via_resolve_role_effort() -> None:
    """Structural: the shared wake driver's prep stage (sourced by both
    claude.sh and codex.sh now) calls
    resolve_role_effort() to resolve the reasoning-effort value and its
    narration source, mirroring the model resolution block."""
    content = _DRIVER_PREP.read_text(encoding="utf-8")
    assert "resolve_role_effort" in content, (
        f"{_DRIVER_PREP} does not call resolve_role_effort(). "
        "The effort-resolution wiring may be missing."
    )


def test_codex_sh_passes_effort_to_provider_invoke_agent() -> None:
    """Structural: _wd_run_provider_and_capture_tokens (wake_driver_invoke.sh,
    shared by both wake providers now) passes the
    resolved effort value and source through as positional arguments at
    every downstream call site."""
    content = _DRIVER_INVOKE.read_text(encoding="utf-8")
    call_count = content.count('"${selected_effort:-}" \\')
    assert call_count == 4, (
        "Expected all four selected_effort-threading call sites in "
        f"wake_driver_invoke.sh; found {call_count} occurrence(s)."
    )
    assert content.count('"${effort_source:-}" \\') == 2, (
        "Expected both provider_dispatch call sites in wake_driver_invoke.sh "
        "to pass effort_source."
    )


def test_wake_codex_provider_contains_effort_wiring() -> None:
    """Structural: provider_codex.sh contains the -c model_reasoning_
    effort assembly and narration."""
    content = _PROVIDER.read_text(encoding="utf-8")
    assert "model_reasoning_effort" in content, (
        f"{_PROVIDER} does not contain 'model_reasoning_effort'. "
        "The effort override wiring may be missing."
    )
    assert "selected_effort" in content, (
        f"{_PROVIDER} does not reference 'selected_effort'."
    )
