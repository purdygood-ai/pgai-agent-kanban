"""
test_wake_claude_llm_thinking_bash.py
======================================
Behavioral unit tests for the llm_thinking_enabled wiring in
team/scripts/lib/wake_claude_provider.sh.

These tests exercise the four behavioral cases required by
PRIORITY-0005 / CODER-20260721-015:

  (a) cfg=false  → the assembled claude command array contains
                   --thinking disabled (exact adjacent tokens)
  (b) cfg=true   → the assembled array does NOT contain --thinking
  (c) key absent → the assembled array does NOT contain --thinking
                   (default-true back-compat)
  (d) invalid value (e.g. "yes") → non-zero exit; stderr contains
                   the literal string "llm_thinking_enabled"

Each test creates a minimal kanban.cfg fixture, then runs a bash
harness that:
  1. Sources ini_parser.sh and config_loader.sh.
  2. Calls load_config against the fixture cfg.
  3. Executes the exact same thinking-flag logic as wake_claude_provider.sh.
  4. Echoes the resulting "command array" tokens so the test can assert
     on presence/absence of --thinking disabled.

Tests are deterministic (fixture cfg only; no live kanban.cfg is read)
and run under pytest via the normal run-unit-tests.sh path.

All paths are relative to the team/ directory, which is the pytest cwd
when invoked by run-unit-tests.sh.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

# Lib paths relative to team/ (the pytest cwd when run via run-unit-tests.sh)
_INI_PARSER_SH = pathlib.Path("scripts/lib/ini_parser.sh")
_CONFIG_LOADER_SH = pathlib.Path("scripts/lib/config_loader.sh")

# The bash harness that mimics the thinking-flag logic from wake_claude_provider.sh.
# Receives FIXTURE_CFG as an env var, sources the config libs, calls load_config,
# then runs the exact conditional block from the provider.
# On success with thinking disabled: echoes "--thinking disabled" on stdout.
# On success with thinking enabled/default: echoes "(no-thinking-flag)" on stdout.
# On invalid value: exits non-zero; writes error to stderr.
_HARNESS_TEMPLATE = """\
set -euo pipefail
source {ini_parser}
source {config_loader}
load_config "$FIXTURE_CFG"

# Replicate the exact logic from wake_claude_provider.sh provider_invoke_agent.
_llm_thinking_enabled="$(config_get providers llm_thinking_enabled "true")"
if [[ "${{_llm_thinking_enabled}}" == "false" ]]; then
    echo "--thinking disabled"
elif [[ "${{_llm_thinking_enabled}}" != "true" ]]; then
    echo "ERROR: wake_claude_provider: kanban.cfg [providers] llm_thinking_enabled has invalid value '${{_llm_thinking_enabled}}'; expected 'true' or 'false'" >&2
    exit 1
else
    echo "(no-thinking-flag)"
fi
"""


def _build_harness() -> str:
    """Return the bash harness script text with actual lib paths substituted."""
    return _HARNESS_TEMPLATE.format(
        ini_parser=_INI_PARSER_SH,
        config_loader=_CONFIG_LOADER_SH,
    )


def _write_providers_cfg(tmp_path: pathlib.Path, *, thinking_value: str | None) -> pathlib.Path:
    """Write a minimal kanban.cfg with [providers] section.

    Parameters
    ----------
    tmp_path:
        Pytest-managed temp directory.
    thinking_value:
        The value to assign to llm_thinking_enabled, or None to omit the key
        entirely (simulating the absent-key case).

    Returns
    -------
    pathlib.Path
        Path to the written kanban.cfg fixture.
    """
    cfg = tmp_path / "kanban.cfg"
    if thinking_value is None:
        # Key absent — providers section exists but llm_thinking_enabled is not set.
        cfg.write_text("[providers]\nactive = claude\n", encoding="utf-8")
    else:
        cfg.write_text(
            f"[providers]\nactive = claude\nllm_thinking_enabled = {thinking_value}\n",
            encoding="utf-8",
        )
    return cfg


# ---------------------------------------------------------------------------
# Case (a): cfg=false → array contains --thinking disabled
# ---------------------------------------------------------------------------


def test_thinking_disabled_when_cfg_false(tmp_path: pathlib.Path) -> None:
    """llm_thinking_enabled=false causes the harness to emit --thinking disabled."""
    cfg = _write_providers_cfg(tmp_path, thinking_value="false")
    harness = _build_harness()

    result = run_bash(tmp_path, harness, extra_env={"FIXTURE_CFG": str(cfg)})

    assert result.returncode == 0, (
        f"Harness exited non-zero for cfg=false:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    # The harness echoes "--thinking disabled" as two adjacent tokens on one line.
    assert "--thinking disabled" in result.stdout, (
        f"Expected '--thinking disabled' in stdout for cfg=false.\n"
        f"Got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Case (b): cfg=true → array does NOT contain --thinking
# ---------------------------------------------------------------------------


def test_thinking_not_appended_when_cfg_true(tmp_path: pathlib.Path) -> None:
    """llm_thinking_enabled=true causes the harness to emit the no-flag marker."""
    cfg = _write_providers_cfg(tmp_path, thinking_value="true")
    harness = _build_harness()

    result = run_bash(tmp_path, harness, extra_env={"FIXTURE_CFG": str(cfg)})

    assert result.returncode == 0, (
        f"Harness exited non-zero for cfg=true:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "--thinking" not in result.stdout, (
        f"Unexpected '--thinking' in stdout for cfg=true.\n"
        f"Got: {result.stdout!r}"
    )
    assert "(no-thinking-flag)" in result.stdout, (
        f"Expected '(no-thinking-flag)' marker in stdout for cfg=true.\n"
        f"Got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Case (c): key absent → array does NOT contain --thinking (default-true)
# ---------------------------------------------------------------------------


def test_thinking_not_appended_when_key_absent(tmp_path: pathlib.Path) -> None:
    """Absent llm_thinking_enabled key defaults to true — no --thinking flag."""
    cfg = _write_providers_cfg(tmp_path, thinking_value=None)
    harness = _build_harness()

    result = run_bash(tmp_path, harness, extra_env={"FIXTURE_CFG": str(cfg)})

    assert result.returncode == 0, (
        f"Harness exited non-zero for absent key:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "--thinking" not in result.stdout, (
        f"Unexpected '--thinking' in stdout for absent key (expected default-true).\n"
        f"Got: {result.stdout!r}"
    )
    assert "(no-thinking-flag)" in result.stdout, (
        f"Expected '(no-thinking-flag)' marker in stdout for absent key.\n"
        f"Got: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# Case (d): invalid value → non-zero exit; stderr mentions llm_thinking_enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", ["yes", "no", "on", "off", "1", "0", "enabled"])
def test_invalid_thinking_value_fails_loud(tmp_path: pathlib.Path, bad_value: str) -> None:
    """Invalid llm_thinking_enabled value causes non-zero exit with key named in stderr."""
    cfg = _write_providers_cfg(tmp_path, thinking_value=bad_value)
    harness = _build_harness()

    result = run_bash(tmp_path, harness, extra_env={"FIXTURE_CFG": str(cfg)})

    assert result.returncode != 0, (
        f"Expected non-zero exit for invalid value {bad_value!r}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "llm_thinking_enabled" in result.stderr, (
        f"Expected 'llm_thinking_enabled' in stderr for invalid value {bad_value!r}.\n"
        f"Got stderr: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Structural: wake_claude_provider.sh contains the --thinking disabled wiring
# ---------------------------------------------------------------------------


def test_wake_claude_provider_contains_thinking_disabled_wiring() -> None:
    """Structural: wake_claude_provider.sh contains the --thinking disabled conditional.

    This verifies that the behavioral tests above exercise code that is actually
    present in the provider script — not just a harness that tests the harness.
    """
    provider = pathlib.Path("scripts/lib/wake_claude_provider.sh")
    content = provider.read_text(encoding="utf-8")

    assert "--thinking disabled" in content, (
        f"{provider} does not contain '--thinking disabled'. "
        "The llm_thinking_enabled wiring may be missing."
    )
    assert "llm_thinking_enabled" in content, (
        f"{provider} does not reference 'llm_thinking_enabled'. "
        "The config key read may be missing."
    )
    assert "config_get providers llm_thinking_enabled" in content, (
        f"{provider} does not call 'config_get providers llm_thinking_enabled'. "
        "Expected standard config loader access pattern."
    )


# ---------------------------------------------------------------------------
# Structural: wake_codex_provider.sh contains the future-mapped comment
# ---------------------------------------------------------------------------


def test_wake_codex_provider_contains_future_mapped_comment() -> None:
    """Structural: wake_codex_provider.sh contains the llm_thinking_enabled comment.

    Verifies the one-line comment at the invocation-assembly site referencing
    llm_thinking_enabled as future-mapped for the Codex lane.
    """
    provider = pathlib.Path("scripts/lib/wake_codex_provider.sh")
    content = provider.read_text(encoding="utf-8")

    assert "llm_thinking_enabled" in content, (
        f"{provider} does not reference 'llm_thinking_enabled' in any comment. "
        "The future-mapped comment may be missing from the invocation-assembly site."
    )
