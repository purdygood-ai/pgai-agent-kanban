"""
test_config_loader_effort_resolution.py
========================================
Unit tests for the reasoning-effort resolution mechanism in
scripts/lib/config_loader.sh — the [providers] llm_effort global cfg key
and the per-role PGAI_<ROLE>_EFFORT env override.

Resolution mirrors the existing PGAI_<ROLE>_MODEL mechanism's precedence
tier and narration-source discipline (roles/PM.md "Model overrides"),
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
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import re
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
    assert re.search(r"^[ \t]*llm_effort[ \t]*=", text, re.MULTILINE) is None, (
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


# ---------------------------------------------------------------------------
# (6) Each provider's own effort list decides validity at config load.
# ---------------------------------------------------------------------------


def _write_provider_cfg(tmp_path: pathlib.Path, provider: str, llm_effort: str | None) -> pathlib.Path:
    lines = ["[providers]", f"active = {provider}"]
    if llm_effort is not None:
        lines.append(f"llm_effort = {llm_effort}")
    cfg_file = tmp_path / "kanban.cfg"
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg_file


@pytest.mark.parametrize(
    "provider, expected",
    [("claude", "low medium high xhigh max"), ("codex", "minimal low medium high xhigh")],
)
def test_each_provider_declares_its_own_effort_list(provider: str, expected: str) -> None:
    """pgai_provider_effort_values reads the list the provider lib declares."""
    result = _run_bash(
        f"source {str(_CONFIG_LOADER)!r} && pgai_provider_effort_values {provider}", _base_env()
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_a_provider_without_a_lib_declares_no_effort_list() -> None:
    result = _run_bash(
        f"source {str(_CONFIG_LOADER)!r} && pgai_provider_effort_values no-such-provider", _base_env()
    )
    assert result.returncode == 1
    assert result.stdout == ""


@pytest.mark.parametrize(
    "provider, cfg_value, env_value, ok",
    [
        pytest.param("claude", "minimal", None, False, id="claude_cfg_minimal_refused"),
        pytest.param("claude", None, "minimal", False, id="claude_env_minimal_refused"),
        pytest.param("claude", "high", None, True, id="claude_cfg_high_passes"),
        pytest.param("claude", "max", None, True, id="claude_cfg_max_passes"),
        pytest.param("codex", "minimal", None, True, id="codex_cfg_minimal_passes"),
        pytest.param("codex", None, "minimal", True, id="codex_env_minimal_passes"),
        pytest.param("codex", "max", None, False, id="codex_cfg_max_refused"),
    ],
)
def test_effort_is_validated_against_the_active_providers_list(
    tmp_path: pathlib.Path, provider: str, cfg_value: str | None, env_value: str | None, ok: bool,
) -> None:
    """load_config accepts exactly the levels the active provider declares and
    refuses any other dual-channel, naming the supplying key, the value and
    the provider's valid list."""
    cfg_file = _write_provider_cfg(tmp_path, provider, cfg_value)
    env = _base_env()
    if env_value is not None:
        env["PGAI_CODER_EFFORT"] = env_value
    result = _run_bash(
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} && echo \"CODER=$PGAI_CODER_EFFORT\"",
        env,
    )
    value = cfg_value or env_value
    if ok:
        assert result.returncode == 0, result.stderr
        assert f"CODER={value}" in result.stdout
        return
    assert result.returncode != 0, result.stdout
    valid = {"claude": "low medium high xhigh max", "codex": "minimal low medium high xhigh"}[provider]
    assert "error:   config_loader.providers.invalid_effort" in result.stderr, result.stderr
    assert f"'{value}'" in result.stderr
    assert ("[providers] llm_effort" if cfg_value else "PGAI_CODER_EFFORT") in result.stderr
    assert f"valid for {provider}: {valid}" in result.stderr, result.stderr
    assert "remedy:  " in result.stderr


def test_invalid_cfg_effort_remedy_fixes_the_file_when_run_as_printed(tmp_path: pathlib.Path) -> None:
    """The refusal's remedy is a command that, pasted, makes load_config pass."""
    cfg_file = _write_provider_cfg(tmp_path, "claude", "minimal")
    load = f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}"
    refused = _run_bash(load, _base_env())
    assert refused.returncode != 0
    remedy = next(line for line in refused.stderr.splitlines() if line.startswith("remedy:"))
    fixed = _run_bash(remedy.split(":", 1)[1].strip(), _base_env())
    assert fixed.returncode == 0, fixed.stderr
    assert _run_bash(load, _base_env()).returncode == 0


# ---------------------------------------------------------------------------
# (7) A provider that declares no effort list passes the value through.
# ---------------------------------------------------------------------------

_NARRATION = "declares no reasoning-effort levels"


def _isolated_loader_with_provider_lib(tmp_path: pathlib.Path, provider: str, lib_body: str) -> pathlib.Path:
    """Copy config_loader.sh and the siblings it sources into a temp lib dir
    beside a provider_<provider>.sh carrying <lib_body>, and return the copied
    loader. pgai_provider_effort_values reads provider libs from the loader's
    own directory, so this gives a provider whose lib exists but declares no
    PROVIDER_EFFORT_VALUES, without touching the real lib tree."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    for name in ("config_loader.sh", "ini_parser.sh", "error_contract.sh", "refusal_ledger.sh"):
        src = _CONFIG_LOADER.parent / name
        if src.is_file():
            (lib_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (lib_dir / f"provider_{provider}.sh").write_text(lib_body, encoding="utf-8")
    return lib_dir / "config_loader.sh"


@pytest.mark.parametrize(
    "cfg_value, env_value, expected_source",
    [
        pytest.param(None, "high", "PGAI_CODER_EFFORT env", id="env_value_passes_through"),
        pytest.param("high", None, "[providers] llm_effort cfg", id="cfg_value_passes_through"),
        pytest.param("minimal", None, "[providers] llm_effort cfg", id="any_level_passes_through"),
    ],
)
def test_provider_without_a_lib_passes_effort_through_unvalidated(
    tmp_path: pathlib.Path, cfg_value: str | None, env_value: str | None, expected_source: str,
) -> None:
    """An active provider with no provider_<name>.sh lib (the test-only
    harness lane) declares no effort list: load_config succeeds, exports the
    value unchanged, resolve_role_effort keeps its usual value|source answer,
    and stderr carries one narration line naming the provider, the supplying
    key and the value -- never the invalid_effort refusal."""
    cfg_file = _write_provider_cfg(tmp_path, "harness", cfg_value)
    env = _base_env()
    if env_value is not None:
        env["PGAI_CODER_EFFORT"] = env_value
    value = cfg_value or env_value
    # load_config exports the cfg value into PGAI_CODER_EFFORT; clear it for
    # the cfg case so the follow-up resolution reports the cfg tier.
    clear_export = "unset PGAI_CODER_EFFORT && " if cfg_value else ""
    result = _run_bash(
        f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r} "
        f"&& echo \"CODER=$PGAI_CODER_EFFORT\" && {clear_export}"
        f"echo \"RESOLVED=$(resolve_role_effort coder 2>/dev/null)\"",
        env,
    )
    assert result.returncode == 0, result.stderr
    assert "invalid_effort" not in result.stderr, result.stderr
    assert f"CODER={value}" in result.stdout, result.stdout
    assert f"RESOLVED={value}|{expected_source}" in result.stdout, result.stdout
    supplying_key = "[providers] llm_effort" if cfg_value else "PGAI_CODER_EFFORT"
    narration = [
        line for line in result.stderr.splitlines()
        if _NARRATION in line and supplying_key in line
    ]
    assert narration, f"Expected a narration line for {supplying_key}; stderr: {result.stderr!r}"
    assert "'harness'" in narration[0] and f"'{value}'" in narration[0], narration[0]


def test_provider_lib_without_a_declaration_passes_effort_through(tmp_path: pathlib.Path) -> None:
    """A provider lib that exists but has no PROVIDER_EFFORT_VALUES line is
    treated like a provider with no lib: the value loads unvalidated and is
    narrated, rather than every value being refused."""
    loader = _isolated_loader_with_provider_lib(
        tmp_path, "nolist", 'PROVIDER_NAME="nolist"\nPROVIDER_CLI="nolist"\n'
    )
    cfg_file = _write_provider_cfg(tmp_path, "nolist", None)
    env = _base_env()
    env["PGAI_CODER_EFFORT"] = "high"
    result = _run_bash(
        f"source {str(loader)!r} && pgai_provider_effort_values nolist; echo \"LIST_RC=$?\"; "
        f"load_config {str(cfg_file)!r} && echo \"CODER=$PGAI_CODER_EFFORT\"",
        env,
    )
    assert "LIST_RC=1" in result.stdout, result.stdout
    assert result.returncode == 0, result.stderr
    assert "CODER=high" in result.stdout, result.stdout
    assert "invalid_effort" not in result.stderr, result.stderr
    assert any(_NARRATION in line and "'nolist'" in line for line in result.stderr.splitlines()), result.stderr


def test_listed_provider_still_refuses_with_its_list_when_every_role_effort_is_inherited(
    tmp_path: pathlib.Path,
) -> None:
    """The pass-through applies only to a provider declaring no list. With
    every PGAI_<ROLE>_EFFORT inherited as an unlisted level, claude still
    refuses at config load naming its own list, and no pass-through
    narration is printed."""
    cfg_file = _write_provider_cfg(tmp_path, "claude", None)
    env = _base_env()
    for role in _ROLES:
        env[f"PGAI_{role.upper()}_EFFORT"] = "minimal"
    result = _run_bash(f"source {str(_CONFIG_LOADER)!r} && load_config {str(cfg_file)!r}", env)
    assert result.returncode != 0, result.stdout
    assert "error:   config_loader.providers.invalid_effort" in result.stderr, result.stderr
    assert "valid for claude: low medium high xhigh max" in result.stderr, result.stderr
    assert _NARRATION not in result.stderr, result.stderr
