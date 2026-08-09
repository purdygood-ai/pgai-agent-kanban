"""
test_wake_claude_effort_wiring.py
===================================
Behavioral unit tests for the reasoning-effort wiring in the Claude wake
lane (v2.1.0 ticket 3, CODER-20260728-012):

  - team/scripts/lib/wake_claude_provider.sh's provider_invoke_agent appends
    ``--effort <value>`` to the assembled claude command only when a
    non-empty effort is resolved, narrates the value and its winning source
    (mirroring the existing model-narration line), and threads the resolved
    effort into the token-capture call so the cost artifact records it.
  - team/scripts/wake/claude.sh resolves the effort (and its narration
    source) via config_loader.sh's resolve_role_effort(), mirroring the
    existing model-resolution block, and passes both through to
    provider_invoke_agent.

Tests source the REAL production wake_claude_provider.sh and invoke the real
provider_invoke_agent function directly (not a reimplementation), with a
fake ``claude`` executable placed first on PATH that records its argv and
emits a minimal ``--output-format json`` response. This exercises the real
command-assembly, narration, and token-capture wiring end-to-end in a
subprocess -- matching the "test by running, not by reasoning" bar.

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
_PROVIDER = _TEAM_DIR / "scripts" / "lib" / "wake_claude_provider.sh"
_CLAUDE_SH = _TEAM_DIR / "scripts" / "wake" / "claude.sh"

_FAKE_CLAUDE_SCRIPT = """\
#!/usr/bin/env bash
# Fake claude CLI for effort-wiring tests: records argv, emits a minimal
# --output-format json response, exits 0.
printf '%s\\n' "$@" > "$ARGV_CAPTURE_FILE"
cat <<'JSON'
{"type":"result","result":"ok","usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0.001,"modelUsage":{"claude-sonnet-4-6":{"inputTokens":1,"outputTokens":1,"costUSD":0.001}}}
JSON
exit 0
"""


def _write_fake_claude(bin_dir: pathlib.Path) -> pathlib.Path:
    """Install a fake `claude` executable at the front of a PATH bin dir."""
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE_SCRIPT, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _write_minimal_cfg(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal kanban.cfg sufficient for config_loader.sh's load_config."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("[providers]\nactive = claude\n", encoding="utf-8")
    return cfg


def _invoke_provider(
    tmp_path: pathlib.Path,
    *,
    selected_model: str = "",
    model_source: str = "test model source",
    selected_effort: str = "",
    effort_source: str = "",
) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path, pathlib.Path]:
    """Invoke the real provider_invoke_agent function via a bash subprocess.

    Returns (result, argv_capture_file, log_file, artifact_dir).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_claude(bin_dir)

    cfg = _write_minimal_cfg(tmp_path)
    log_file = tmp_path / "wake.log"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    argv_capture_file = tmp_path / "argv.txt"

    harness = f"""
set -uo pipefail
export SCRIPT_DIR={str(_SCRIPT_DIR)!r}
export AGENT=coder
source {str(_INI_PARSER)!r}
source {str(_CONFIG_LOADER)!r}
load_config {str(cfg)!r}
source {str(_PROVIDER)!r}

provider_invoke_agent \\
  "test prompt" \\
  {selected_model!r} \\
  {model_source!r} \\
  {selected_effort!r} \\
  {effort_source!r} \\
  "TASK-EFFORT-TEST" \\
  {str(artifact_dir)!r} \\
  {str(log_file)!r} \\
  "coder"
echo "PROVIDER_AGENT_EXIT_CODE=${{PROVIDER_AGENT_EXIT_CODE}}"
"""

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["ARGV_CAPTURE_FILE"] = str(argv_capture_file)

    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return result, argv_capture_file, log_file, artifact_dir


def _read_argv(argv_capture_file: pathlib.Path) -> list[str]:
    if not argv_capture_file.exists():
        return []
    return argv_capture_file.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# (1) Effort in force -> assembled command includes --effort <value>.
# ---------------------------------------------------------------------------


def test_effort_flag_appended_when_resolved(tmp_path: pathlib.Path) -> None:
    """A non-empty selected_effort appends '--effort <value>' to claude_cmd."""
    result, argv_capture_file, log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="high",
        effort_source="PGAI_CODER_EFFORT env",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert "--effort" in argv, f"Expected '--effort' in assembled argv: {argv}"
    idx = argv.index("--effort")
    assert argv[idx + 1] == "high", (
        f"Expected 'high' immediately after '--effort'; got argv: {argv}"
    )


# ---------------------------------------------------------------------------
# (2) No effort in force -> no --effort flag; command unchanged from today.
# ---------------------------------------------------------------------------


def test_no_effort_flag_when_absent(tmp_path: pathlib.Path) -> None:
    """An empty selected_effort appends no --effort flag at all."""
    result, argv_capture_file, _log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert "--effort" not in argv, (
        f"Unexpected '--effort' in assembled argv when no effort is resolved: {argv}"
    )
    # The assembled command otherwise matches today's known shape exactly
    # (no --model since selected_model is empty in this fixture; default
    # llm_thinking_enabled=true so no --thinking flag either).
    assert argv == [
        "--dangerously-skip-permissions",
        "-p",
        "test prompt",
        "--output-format",
        "json",
    ], f"Assembled command changed shape with effort absent: {argv}"


# ---------------------------------------------------------------------------
# (3) --effort ordering: consistent with --model (before -p and the prompt).
# ---------------------------------------------------------------------------


def test_effort_flag_positioned_before_prompt_like_model_flag(
    tmp_path: pathlib.Path,
) -> None:
    """--effort appears after --model and before -p, mirroring --model's ordering."""
    result, argv_capture_file, _log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_model="opus",
        model_source="test model source",
        selected_effort="high",
        effort_source="test effort source",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    argv = _read_argv(argv_capture_file)
    assert argv == [
        "--model",
        "opus",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
        "-p",
        "test prompt",
        "--output-format",
        "json",
    ], f"Unexpected flag ordering: {argv}"


# ---------------------------------------------------------------------------
# (4) Narration: the wake log names the effort value and its winning source.
# ---------------------------------------------------------------------------


def test_wake_log_narrates_effort_value_and_source(tmp_path: pathlib.Path) -> None:
    """The wake log line names the effort value and the winning source."""
    result, _argv_capture_file, log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="low",
        effort_source="[providers] llm_effort cfg",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    log_text = log_file.read_text(encoding="utf-8")
    assert "effort: low (from [providers] llm_effort cfg)" in log_text, (
        f"Expected effort narration line naming value and source in log; got:\n{log_text}"
    )


def test_wake_log_has_no_effort_narration_when_absent(tmp_path: pathlib.Path) -> None:
    """No effort narration line is emitted when no effort is resolved."""
    result, _argv_capture_file, log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
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
    result, _argv_capture_file, _log_file, artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="high",
        effort_source="PGAI_CODER_EFFORT env",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
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
    result, _argv_capture_file, _log_file, artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="",
        effort_source="",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    tokens_json = artifact_dir / "tokens.json"
    assert tokens_json.exists(), f"Expected tokens.json at {tokens_json}"
    data = json.loads(tokens_json.read_text(encoding="utf-8"))
    assert "effort" not in data, (
        f"Unexpected 'effort' key in tokens.json when no effort was resolved: {data}"
    )


# ---------------------------------------------------------------------------
# Structural: claude.sh resolves effort and threads it through to the
# provider call, mirroring the model-resolution block.
# ---------------------------------------------------------------------------


def test_claude_sh_resolves_effort_via_resolve_role_effort() -> None:
    """Structural: claude.sh calls resolve_role_effort() to resolve the
    reasoning-effort value and its narration source, mirroring the model
    resolution block."""
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    assert "resolve_role_effort" in content, (
        f"{_CLAUDE_SH} does not call resolve_role_effort(). "
        "The effort-resolution wiring may be missing."
    )


def test_claude_sh_passes_effort_to_provider_invoke_agent() -> None:
    """Structural: both provider_invoke_agent call sites in claude.sh pass
    the resolved effort value and source through as positional arguments."""
    content = _CLAUDE_SH.read_text(encoding="utf-8")
    call_count = content.count('"${selected_effort:-}" \\')
    assert call_count == 2, (
        f"Expected both provider_invoke_agent call sites in {_CLAUDE_SH} to "
        f"pass selected_effort; found {call_count} occurrence(s)."
    )
    assert content.count('"${effort_source:-}" \\') == 2, (
        f"Expected both provider_invoke_agent call sites in {_CLAUDE_SH} to "
        "pass effort_source."
    )


def test_wake_claude_provider_contains_effort_wiring() -> None:
    """Structural: wake_claude_provider.sh contains the --effort assembly,
    narration, and token-capture threading."""
    content = _PROVIDER.read_text(encoding="utf-8")
    assert "--effort" in content, (
        f"{_PROVIDER} does not contain '--effort'. The effort flag wiring may be missing."
    )
    assert "selected_effort" in content, (
        f"{_PROVIDER} does not reference 'selected_effort'."
    )
    assert '"${selected_effort:-}"' in content, (
        f"{_PROVIDER} does not thread selected_effort into a downstream call "
        "(expected the token-capture merge call to receive it)."
    )
