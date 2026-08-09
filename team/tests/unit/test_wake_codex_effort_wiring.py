"""
test_wake_codex_effort_wiring.py
===================================
Behavioral unit tests for the reasoning-effort wiring in the Codex wake
lane (v2.1.0 ticket 4, CODER-20260728-013):

  - team/scripts/lib/wake_codex_provider.sh's provider_invoke_agent appends
    ``-c model_reasoning_effort=<value>`` to the assembled codex command
    only when a non-empty effort is resolved, narrates the value and its
    winning source (mirroring the existing model-narration line), and
    threads the resolved effort into the token-capture call so the cost
    artifact records it.
  - team/scripts/wake/codex.sh resolves the effort (and its narration
    source) via config_loader.sh's resolve_role_effort(), mirroring the
    existing model-resolution block, and passes both through to
    provider_invoke_agent.

Tests source the REAL production wake_codex_provider.sh and invoke the real
provider_invoke_agent function directly (not a reimplementation), with a
fake ``codex`` executable placed first on PATH that records its argv and
emits a minimal JSONL event stream (turn.completed usage block). This
exercises the real command-assembly, narration, and token-capture wiring
end-to-end in a subprocess -- matching the "test by running, not by
reasoning" bar.

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
) -> tuple[subprocess.CompletedProcess, pathlib.Path, pathlib.Path, pathlib.Path]:
    """Invoke the real provider_invoke_agent function via a bash subprocess.

    Returns (result, argv_capture_file, log_file, artifact_dir).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_fake_codex(bin_dir)

    cfg = _write_minimal_cfg(tmp_path)
    log_file = tmp_path / "wake.log"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    argv_capture_file = tmp_path / "argv.txt"
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)

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
    return result, argv_capture_file, log_file, artifact_dir


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
    result, argv_capture_file, _log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_effort="high",
        effort_source="PGAI_CODER_EFFORT env",
    )

    assert result.returncode == 0, (
        f"provider_invoke_agent harness failed:\n"
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
    result, argv_capture_file, _log_file, _artifact_dir = _invoke_provider(
        tmp_path,
        selected_model="gpt-5.3-codex",
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
# Structural: codex.sh resolves effort and threads it through to the
# provider call, mirroring the model-resolution block.
# ---------------------------------------------------------------------------


def test_codex_sh_resolves_effort_via_resolve_role_effort() -> None:
    """Structural: codex.sh calls resolve_role_effort() to resolve the
    reasoning-effort value and its narration source, mirroring the model
    resolution block."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    assert "resolve_role_effort" in content, (
        f"{_CODEX_SH} does not call resolve_role_effort(). "
        "The effort-resolution wiring may be missing."
    )


def test_codex_sh_passes_effort_to_provider_invoke_agent() -> None:
    """Structural: both provider_invoke_agent call sites in codex.sh pass
    the resolved effort value and source through as positional arguments."""
    content = _CODEX_SH.read_text(encoding="utf-8")
    call_count = content.count('"${selected_effort:-}" \\')
    assert call_count == 2, (
        f"Expected both provider_invoke_agent call sites in {_CODEX_SH} to "
        f"pass selected_effort; found {call_count} occurrence(s)."
    )
    assert content.count('"${effort_source:-}" \\') == 2, (
        f"Expected both provider_invoke_agent call sites in {_CODEX_SH} to "
        "pass effort_source."
    )


def test_wake_codex_provider_contains_effort_wiring() -> None:
    """Structural: wake_codex_provider.sh contains the -c model_reasoning_
    effort assembly, narration, and token-capture threading."""
    content = _PROVIDER.read_text(encoding="utf-8")
    assert "model_reasoning_effort" in content, (
        f"{_PROVIDER} does not contain 'model_reasoning_effort'. "
        "The effort override wiring may be missing."
    )
    assert "selected_effort" in content, (
        f"{_PROVIDER} does not reference 'selected_effort'."
    )
    assert '"${selected_effort:-}"' in content, (
        f"{_PROVIDER} does not thread selected_effort into a downstream call "
        "(expected the token-capture merge call to receive it)."
    )
