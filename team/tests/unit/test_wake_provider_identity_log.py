"""
test_wake_provider_identity_log.py
===================================
Behavioral fixtures for the per-firing provider-identity log block
(v2.14.0-provider-runtime-hardening: "log the CLI version and model string
in every wake firing's header block").

Goal exercised: when the provider changes behaviour (a new wait ceiling, a
new refusal, a new validation), the logs must answer "what CLI build and
model were we running when it started". The per-firing header block in
team/scripts/wake/claude.sh and team/scripts/wake/codex.sh (the block that
already logs "auth mode: ...") is extended with two more fields:
"provider CLI version: ..." and "model: ...".

Acceptance criteria exercised:

  (1) The block runs once per firing, unconditionally, before any task is
      dispatched — so an idle firing (no work found) still emits both
      fields. Verified structurally: the block sits between the AI_AUTH_MODE
      sanity check and the `wake_common_preflight` / `wake_common_run` calls
      that gate task dispatch, and it is not nested inside any function or
      loop construct in either wake script.

  (2) A wake firing that processes N tasks emits the block exactly once —
      it lives in top-level script code that runs once per process, not
      inside process_one_task or run_project_chain (which loop per task /
      per project).

  (3) With the provider binary absent from PATH, the firing completes
      normally (exit 0) and the version field carries an explicit "unknown"
      marker rather than being blank or absent. Same for a version query
      that fails for any other reason (non-zero exit, empty output).

  (4) The same field names ("provider CLI version:", "model:") appear in
      both team/scripts/wake/claude.sh and team/scripts/wake/codex.sh
      output, so a single grep pattern matches both providers. Verified by
      sibling-parity: the block is byte-identical between the two scripts
      (identified by the PROVIDER-IDENTITY-LOG-BEGIN/END sentinel comments,
      mirroring the extraction convention already used for the
      BG-WAIT-CEILING-DERIVATION block in wake_bootstrap.sh and the
      wake-stamp block in wake_driver_invoke.sh).

  (5) The pre-existing "auth mode: ..." line is untouched — still present,
      still first, still unconditional.

  (6) No credential, token, or API key value appears in the new log output
      (the block never reads ANTHROPIC_API_KEY / OPENAI_API_KEY / any
      secrets-file value).

  (7) The model field reflects the same PGAI_<ROLE>_MODEL resolution tier
      the per-task model-selection block (wake_driver_prep.sh) reads from,
      and falls back to an explicit "<provider CLI default>" marker
      (never blank) when that tier is unset — the effective per-task
      override, which is not knowable until a specific task README is
      read, is out of scope for a block that runs before any task is
      selected.

The extraction-and-harness pattern here mirrors the project's established
convention for exercising bash logic without sourcing an entire wake
script end to end (see test_wake_model_stamp.py's "sibling parity" tests
and test_wake_dispatch_role_assignment.py's anchor-extraction harness).

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, pytest's cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_CLAUDE_SH = pathlib.Path("scripts/wake/claude.sh")
_CODEX_SH = pathlib.Path("scripts/wake/codex.sh")

_BLOCK_BEGIN = "# PROVIDER-IDENTITY-LOG-BEGIN"
_BLOCK_END = "# PROVIDER-IDENTITY-LOG-END"

_AUTH_MODE_LINE = 'log "auth mode: ${AI_AUTH_MODE:-oauth}"'
# The actual call site (top-level statement, no arguments) — distinct from
# the mentions of the same name in this file's own header comments (which
# describe wake_common.sh's public API and would give a false "before" match
# if searched for by name alone).
_PREFLIGHT_CALL = "\nwake_common_preflight\n"


# ---------------------------------------------------------------------------
# Extraction helper: pull the sentinel-marked block out of a wake sibling.
# ---------------------------------------------------------------------------


def _extract_identity_block(path: pathlib.Path) -> str:
    """Return the text between (and including) the BEGIN/END sentinels."""
    text = path.read_text(encoding="utf-8")
    begin = text.index(_BLOCK_BEGIN)
    end = text.index(_BLOCK_END) + len(_BLOCK_END)
    return text[begin:end]


def _run_identity_block(
    block: str,
    tmp_path: pathlib.Path,
    *,
    provider_cli: str = "fakecli",
    agent: str = "coder",
    agent_model_env: dict[str, str] | None = None,
    bin_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    """Run the extracted identity block under bash with a stubbed log().

    Mirrors the faithful log() stub shape from team/tests/fixtures/log_stub.py
    (echo to stdout — the extracted block never assumes a real $LOG_FILE).
    """
    harness = f"""
set -euo pipefail
log() {{ echo "LOG: $*"; }}
PROVIDER_CLI={provider_cli!r}
AGENT={agent!r}
{block}
"""
    env = dict(os.environ)
    # Start from a clean slate for every PGAI_<ROLE>_MODEL var so ambient
    # test-runner env (e.g. this very agent's own PGAI_CODER_MODEL) never
    # leaks into the fixture.
    for key in list(env):
        if key.startswith("PGAI_") and key.endswith("_MODEL"):
            del env[key]
    if agent_model_env:
        env.update(agent_model_env)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    # else: leave PATH as inherited. The fake binary name ("fakecli" by
    # default) is not expected to exist anywhere on a real host's PATH, so
    # this deterministically exercises the binary-absent fallback while
    # still letting bash/timeout/head/tr (needed by the block itself and
    # by this harness's own subprocess.run) resolve normally.

    return subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _write_fake_cli(bin_dir: pathlib.Path, *, name: str, version_output: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / name
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--version" ]]; then\n'
        f'  echo {version_output!r}\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# (1) Sibling parity: byte-identical block between claude.sh and codex.sh.
# ---------------------------------------------------------------------------


def test_identity_block_is_byte_identical_across_providers() -> None:
    """The extracted block is byte-identical in both wake siblings, so the
    same field names ("provider CLI version:", "model:") land in both
    providers' logs — a single grep pattern matches both."""
    claude_block = _extract_identity_block(_CLAUDE_SH)
    codex_block = _extract_identity_block(_CODEX_SH)
    assert claude_block == codex_block, (
        "PROVIDER-IDENTITY-LOG block differs between claude.sh and codex.sh; "
        "the two providers would emit different field names or shapes, "
        "breaking a single cross-provider grep pattern.\n\n"
        f"claude.sh block:\n{claude_block}\n\ncodex.sh block:\n{codex_block}"
    )


# ---------------------------------------------------------------------------
# (2) Structural: block sits between auth-mode log and preflight/run calls,
#     unconditionally, once per script — not inside any function/loop.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_runs_after_auth_mode_and_before_preflight(
    script_path: pathlib.Path,
) -> None:
    """The identity block is positioned after the auth-mode line (which it
    must not disturb) and before wake_common_preflight/wake_common_run
    (which gate task dispatch) — proving it runs once, unconditionally,
    before any task-loop work begins."""
    content = script_path.read_text(encoding="utf-8")

    assert _AUTH_MODE_LINE in content, (
        f"{script_path}: pre-existing auth mode: line is missing or was "
        "altered. It must remain unchanged."
    )
    assert _BLOCK_BEGIN in content and _BLOCK_END in content, (
        f"{script_path}: PROVIDER-IDENTITY-LOG sentinel markers not found."
    )
    assert _PREFLIGHT_CALL in content, (
        f"{script_path}: expected call to {_PREFLIGHT_CALL} not found."
    )

    auth_pos = content.index(_AUTH_MODE_LINE)
    block_begin_pos = content.index(_BLOCK_BEGIN)
    block_end_pos = content.index(_BLOCK_END)
    preflight_pos = content.index(_PREFLIGHT_CALL)

    assert auth_pos < block_begin_pos, (
        f"{script_path}: identity block must appear after the auth mode: line."
    )
    assert block_end_pos < preflight_pos, (
        f"{script_path}: identity block must appear before {_PREFLIGHT_CALL} "
        "(i.e. before task dispatch begins), so it fires on every firing "
        "including idle ones and exactly once regardless of task count."
    )

    # process_one_task and run_project_chain — the per-task / per-project
    # loop bodies — live in the separately sourced lib/wake_driver.sh, and
    # are reached only through the wake_common_preflight / wake_common_run
    # calls the identity block precedes (asserted above). Neither call
    # appears a second time in this file, so there is exactly one call
    # path from "identity block ran" to "task loop may run", proving the
    # block itself cannot execute more than once per firing regardless of
    # how many tasks that loop goes on to process.
    assert content.count(_PREFLIGHT_CALL) == 1, (
        f"{script_path}: expected exactly one top-level call to "
        f"wake_common_preflight; found {content.count(_PREFLIGHT_CALL)}."
    )


# ---------------------------------------------------------------------------
# (3) Field names present, dual-channel-free (no credentials touched).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_declares_both_field_names(script_path: pathlib.Path) -> None:
    """Both new fields ("provider CLI version:" and "model:") are present
    in the log-line templates, and the block never reads a credential
    env var (ANTHROPIC_API_KEY / OPENAI_API_KEY / any *_TOKEN / *_SECRET)."""
    block = _extract_identity_block(script_path)
    assert "provider CLI version:" in block, (
        f"{script_path}: missing 'provider CLI version:' field in the "
        "identity block."
    )
    assert 'log "model:' in block, (
        f"{script_path}: missing 'model:' field in the identity block."
    )
    for forbidden in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "_TOKEN", "_SECRET"):
        assert forbidden not in block, (
            f"{script_path}: identity block references {forbidden!r}, which "
            "risks leaking credential material into the log."
        )


# ---------------------------------------------------------------------------
# (4) Behavioral: CLI present -> both fields populated with real values.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_logs_version_and_model_when_cli_present(
    script_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """When the provider CLI answers --version, the header block logs the
    real version string and a non-blank model field."""
    block = _extract_identity_block(script_path)
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, name="fakecli", version_output="1.2.3 (Fake CLI)")

    result = _run_identity_block(block, tmp_path, bin_dir=bin_dir)

    assert result.returncode == 0, (
        f"Identity block exited non-zero with CLI present:\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "LOG: provider CLI version: 1.2.3 (Fake CLI)" in result.stdout, (
        f"Expected real version string in log output; got:\n{result.stdout}"
    )
    assert "LOG: model: <provider CLI default>" in result.stdout, (
        f"Expected the provider-CLI-default fallback marker for the model "
        f"field with no PGAI_CODER_MODEL configured; got:\n{result.stdout}"
    )


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_logs_configured_model_when_role_env_set(
    script_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """When PGAI_<ROLE>_MODEL is configured (the same tier
    wake_driver_prep.sh's model-selection block reads from), the header
    block logs that value rather than the provider-default marker."""
    block = _extract_identity_block(script_path)
    bin_dir = tmp_path / "bin"
    _write_fake_cli(bin_dir, name="fakecli", version_output="1.2.3 (Fake CLI)")

    result = _run_identity_block(
        block,
        tmp_path,
        bin_dir=bin_dir,
        agent_model_env={"PGAI_CODER_MODEL": "claude-sonnet-5"},
    )

    assert result.returncode == 0
    assert "LOG: model: claude-sonnet-5 (configured default" in result.stdout, (
        f"Expected configured PGAI_CODER_MODEL value in log output; got:\n"
        f"{result.stdout}"
    )


# ---------------------------------------------------------------------------
# (5) Behavioral: CLI absent from PATH -> explicit unknown marker, exit 0.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_logs_unknown_marker_when_cli_absent(
    script_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """With the provider binary absent from PATH, the block completes
    normally (never fails the wake) and the version field carries an
    explicit unknown marker rather than being blank or absent."""
    block = _extract_identity_block(script_path)

    result = _run_identity_block(block, tmp_path, bin_dir=None)

    assert result.returncode == 0, (
        f"Identity block must never fail the wake when the CLI binary is "
        f"missing; got exit {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "LOG: provider CLI version: unknown" in result.stdout, (
        f"Expected an explicit 'unknown' marker (not blank/absent) for the "
        f"version field when the CLI binary is missing; got:\n{result.stdout}"
    )
    # The model field must still be logged even though the version query failed.
    assert "LOG: model:" in result.stdout, (
        f"Expected the model field to still be logged when the version "
        f"query fails; got:\n{result.stdout}"
    )


@pytest.mark.parametrize("script_path", [_CLAUDE_SH, _CODEX_SH])
def test_identity_block_logs_unknown_marker_when_version_flag_unsupported(
    script_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """A CLI binary that exists but exits non-zero / prints nothing for
    --version also lands on the explicit unknown marker (not silently
    empty), since 'unparseable output' is named explicitly as a case that
    must fall back rather than propagate a blank field."""
    block = _extract_identity_block(script_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "fakecli"
    fake.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    result = _run_identity_block(block, tmp_path, bin_dir=bin_dir)

    assert result.returncode == 0
    assert "LOG: provider CLI version: unknown" in result.stdout, (
        f"Expected unknown marker when --version exits non-zero with no "
        f"output; got:\n{result.stdout}"
    )
