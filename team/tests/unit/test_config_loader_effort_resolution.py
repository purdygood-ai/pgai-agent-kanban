"""
test_config_loader_effort_resolution.py
========================================
Unit tests for the reasoning-effort resolution mechanism in
scripts/lib/config_loader.sh — the [providers] llm_effort global cfg key
and the per-role PGAI_<ROLE>_EFFORT env override.

Resolution mirrors the existing PGAI_<ROLE>_MODEL mechanism's precedence
tier and narration-source discipline (SOP.md "Model Override Mechanism"),
but with a single global cfg key rather than a per-role/per-provider
matrix:

  1. PGAI_<ROLE>_EFFORT already exported (env) — wins.
  2. [providers] llm_effort in kanban.cfg (cfg) — base.
  3. Neither set — absent; no effort resolved (today's behavior, byte-
     identical: no PGAI_<ROLE>_EFFORT var is exported at all).

Acceptance criteria exercised:

  (1) Absent llm_effort AND absent PGAI_<ROLE>_EFFORT -> no effort value
      resolved/exported; behavior unchanged.
  (2) [providers] llm_effort set to a valid value -> resolved for every
      role, source recorded as the cfg key.
  (3) PGAI_<ROLE>_EFFORT set -> overrides the cfg value for that role,
      source recorded as the env var.
  (4) An invalid effort value (from either source) fails loud, naming the
      exact supplying key.
  (5) kanban.cfg_example carries a commented-out [providers] llm_effort
      entry documenting the vendor-default asymmetry and the
      model-dependent-support caveat.

Tests invoke config_loader.sh via subprocess (bash -c "source ... && ..."),
matching the pattern in test_dashboard_status_glyph_config.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see SOP.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"
_KANBAN_CFG_EXAMPLE = _REPO_ROOT / "kanban.cfg_example"

_ROLES = ["pm", "coder", "writer", "tester", "cm", "po"]


def _base_env() -> dict[str, str]:
    """A clean subprocess env with every PGAI_<ROLE>_EFFORT var absent.

    Starting from a copy of the real environment (so PATH etc. resolve),
    then stripping any role-effort vars that might already be exported in
    the test runner's own environment, keeps each test's precedence
    assertions independent of what happens to be exported outside it.
    """
    env = dict(os.environ)
    for role in _ROLES:
        env.pop(f"PGAI_{role.upper()}_EFFORT", None)
        env.pop(f"PGAI_{role.upper()}_MODEL", None)
    return env


def _run_bash(script: str, env: dict[str, str], timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _write_cfg(tmp_path: pathlib.Path, *, llm_effort: str | None) -> pathlib.Path:
    """Write a minimal kanban.cfg with an optional [providers] llm_effort key."""
    lines = ["[providers]", "active = claude"]
    if llm_effort is not None:
        lines.append(f"llm_effort = {llm_effort}")
    cfg_file = tmp_path / "kanban.cfg"
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_file


# ---------------------------------------------------------------------------
# (1) Absent everywhere -> nothing resolved, nothing exported.
# ---------------------------------------------------------------------------


def test_absent_llm_effort_and_env_yields_no_effort_exported(tmp_path: pathlib.Path) -> None:
    """With llm_effort and every PGAI_<ROLE>_EFFORT unset, load_config exports
    no PGAI_<ROLE>_EFFORT var for any role -- byte-identical to today, where
    the var never existed at all."""
    cfg_file = _write_cfg(tmp_path, llm_effort=None)
    env = _base_env()

    checks = " ".join(f'echo "{r}=[${{PGAI_{r.upper()}_EFFORT:-}}]"' for r in _ROLES)
    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} && {checks}"
    result = _run_bash(script, env)

    assert result.returncode == 0, f"load_config failed: {result.stderr!r}"
    for role in _ROLES:
        assert f"{role}=[]" in result.stdout, (
            f"Expected PGAI_{role.upper()}_EFFORT to remain unexported (empty) "
            f"when absent everywhere.\nstdout: {result.stdout!r}"
        )


def test_absent_everywhere_resolve_role_effort_returns_empty_value_and_source(
    tmp_path: pathlib.Path,
) -> None:
    """resolve_role_effort itself reports an empty value and empty source
    when neither the cfg key nor the env var is set."""
    cfg_file = _write_cfg(tmp_path, llm_effort=None)
    env = _base_env()

    script = (
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} "
        f"&& resolve_role_effort coder"
    )
    result = _run_bash(script, env)

    assert result.returncode == 0, f"resolve_role_effort failed: {result.stderr!r}"
    assert result.stdout.strip() == "|", (
        f"Expected empty 'value|source' output when absent everywhere; "
        f"got {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# (2) cfg value resolves for every role, source recorded as the cfg key.
# ---------------------------------------------------------------------------


def test_cfg_llm_effort_resolves_for_every_role_with_cfg_source(tmp_path: pathlib.Path) -> None:
    """A valid [providers] llm_effort value is exported as PGAI_<ROLE>_EFFORT
    for every role, and resolve_role_effort names the cfg key as the source
    when no env override is present."""
    cfg_file = _write_cfg(tmp_path, llm_effort="high")
    env = _base_env()

    exports = " && ".join(f'echo "{r}=[${{PGAI_{r.upper()}_EFFORT:-}}]"' for r in _ROLES)
    # unset PGAI_CODER_EFFORT before the follow-up resolve_role_effort call --
    # load_config's own export loop above already populated it from cfg (the
    # same tier-blurring load_config produces for PGAI_<ROLE>_MODEL); clearing
    # it isolates this check to what a FRESH resolution (no pre-set env var)
    # reports as the winning source.
    script = (
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} && {exports} "
        f"&& unset PGAI_CODER_EFFORT "
        f"&& echo 'SOURCE=['$(resolve_role_effort coder | cut -d'|' -f2)']'"
    )
    result = _run_bash(script, env)

    assert result.returncode == 0, f"load_config failed: {result.stderr!r}"
    for role in _ROLES:
        assert f"{role}=[high]" in result.stdout, (
            f"Expected PGAI_{role.upper()}_EFFORT=high from cfg; "
            f"stdout: {result.stdout!r}"
        )
    assert "SOURCE=[[providers] llm_effort cfg]" in result.stdout, (
        f"Expected the winning source to name '[providers] llm_effort cfg'; "
        f"stdout: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# (3) env override wins over cfg for its role; source names the env var.
# ---------------------------------------------------------------------------


def test_env_override_wins_over_cfg_for_its_role_and_names_env_source(
    tmp_path: pathlib.Path,
) -> None:
    """PGAI_WRITER_EFFORT overrides the cfg value for the writer role only;
    other roles keep resolving from cfg. resolve_role_effort called fresh
    (without the env var pre-set) reports the cfg source; called with the
    env var pre-set, it reports the env source."""
    cfg_file = _write_cfg(tmp_path, llm_effort="high")
    env = _base_env()
    env["PGAI_WRITER_EFFORT"] = "low"

    script = (
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} "
        f"&& echo \"writer=[${{PGAI_WRITER_EFFORT:-}}]\" "
        f"&& echo \"coder=[${{PGAI_CODER_EFFORT:-}}]\" "
        f"&& unset PGAI_CODER_EFFORT "
        f"&& echo 'CODER_SOURCE=['$(resolve_role_effort coder | cut -d'|' -f2)']' "
        f"&& echo 'WRITER_SOURCE=['$(resolve_role_effort writer | cut -d'|' -f2)']'"
    )
    result = _run_bash(script, env)

    assert result.returncode == 0, f"load_config failed: {result.stderr!r}"
    assert "writer=[low]" in result.stdout, (
        f"Expected PGAI_WRITER_EFFORT to keep the env override 'low'; "
        f"stdout: {result.stdout!r}"
    )
    assert "coder=[high]" in result.stdout, (
        f"Expected PGAI_CODER_EFFORT to fall back to cfg 'high'; "
        f"stdout: {result.stdout!r}"
    )
    assert "CODER_SOURCE=[[providers] llm_effort cfg]" in result.stdout, (
        f"Expected coder's winning source to name the cfg key; "
        f"stdout: {result.stdout!r}"
    )
    assert "WRITER_SOURCE=[PGAI_WRITER_EFFORT env]" in result.stdout, (
        f"Expected writer's winning source to name the env var; "
        f"stdout: {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# (4) Invalid value fails loud, naming the exact supplying key.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg_llm_effort, env_overrides, expected_stderr_substrings",
    [
        pytest.param(
            "extreme", {},
            ["[providers] llm_effort", "extreme"],
            id="invalid_cfg_value_names_cfg_key",
        ),
        pytest.param(
            None, {"PGAI_CODER_EFFORT": "bogus"},
            ["PGAI_CODER_EFFORT", "bogus"],
            id="invalid_env_value_names_env_var",
        ),
    ],
)
def test_invalid_effort_value_fails_loud_naming_supplying_key(
    tmp_path: pathlib.Path,
    cfg_llm_effort: str | None,
    env_overrides: dict[str, str],
    expected_stderr_substrings: list[str],
) -> None:
    """An unrecognized effort value — from either the cfg key or the env var —
    causes load_config to exit non-zero with a message naming the exact
    supplying key and the offending value."""
    cfg_file = _write_cfg(tmp_path, llm_effort=cfg_llm_effort)
    env = _base_env()
    env.update(env_overrides)

    script = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}"
    result = _run_bash(script, env)

    assert result.returncode != 0, (
        f"Expected non-zero exit for invalid effort value; "
        f"got returncode={result.returncode}, stderr={result.stderr!r}"
    )
    for substring in expected_stderr_substrings:
        assert substring in result.stderr, (
            f"Expected {substring!r} named in the error; stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# (5) kanban.cfg_example documents the key, commented out by default.
# ---------------------------------------------------------------------------


def test_kanban_cfg_example_documents_commented_out_llm_effort() -> None:
    """kanban.cfg_example carries a commented-out [providers] llm_effort
    entry that records the vendor-default asymmetry and the
    model-dependent-support caveat, so the default stays absent."""
    text = _KANBAN_CFG_EXAMPLE.read_text(encoding="utf-8")

    assert "# llm_effort" in text, (
        "Expected a commented-out '# llm_effort' line in kanban.cfg_example "
        "(default must be absent)."
    )
    assert "llm_effort = " not in text.replace("# llm_effort = ", ""), (
        "llm_effort must not appear as an active (uncommented) key in "
        "kanban.cfg_example — the default is absent."
    )
    assert "Anthropic" in text and "high" in text, (
        "Expected the Anthropic-defaults-to-high vendor note near llm_effort."
    )
    assert "OpenAI" in text and "medium" in text, (
        "Expected the OpenAI-defaults-to-medium vendor note near llm_effort."
    )
    assert "vary by model" in text, (
        "Expected the model-dependent-support caveat near llm_effort."
    )
