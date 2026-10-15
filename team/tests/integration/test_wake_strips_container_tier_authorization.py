"""
test_wake_strips_container_tier_authorization.py
================================================
The container tier is operator-invoked only, so the wake never hands its
authorization (PGAI_CONTAINER_TIER) to an agent: wake_bootstrap_init
removes it after shell-env, claude-env, secrets and kanban.cfg have been
sourced (team/scripts/lib/container_tier.sh, container_tier_scrub_env).

Driven through the real wake entry point (scripts/wake/claude.sh) against a
synthetic kanban root, from a scratch cwd, under a rebuilt environment --
see tests.integration.wake_entrypoint_acceptance_harness. The stub provider
CLI's `--version` answer is the observation channel: it is a real child of
the wake process and prints the value it inherited, which the wake's own
"provider CLI version:" log line carries into the captured output.
"""

from __future__ import annotations

import pathlib
import re

from tests.integration.wake_entrypoint_acceptance_harness import (
    build_stub_provider_cli,
    build_synthetic_kanban_root,
    make_foreign_cwd,
    run_wake_entrypoint,
    scrubbed_wake_env,
)

_AGENT = "writer"
_PROBE_VAR = "PGAI_CONTAINER_TIER"
_PROBE_RE = re.compile(r"provider CLI version: wake-entry-point-probe-stub (.+)")


def _run_wake(tmp_path: pathlib.Path, *, shell_env_line: str = "", extra_env: dict | None = None):
    kanban_root = build_synthetic_kanban_root(tmp_path, agent=_AGENT)
    if shell_env_line:
        shell_env = kanban_root / "shell-env"
        existing = shell_env.read_text(encoding="utf-8") if shell_env.exists() else ""
        shell_env.write_text(existing + shell_env_line + "\n", encoding="utf-8")
    stub_bin_dir, marker_file = build_stub_provider_cli(tmp_path, probe_env_vars=(_PROBE_VAR,))
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = tmp_path / "pgai_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    env = scrubbed_wake_env(
        kanban_root,
        temp_dir=temp_dir,
        home_dir=home_dir,
        stub_bin_dir=stub_bin_dir,
        extra_env=extra_env,
    )
    result = run_wake_entrypoint(
        kanban_root, cwd=make_foreign_cwd(tmp_path), env=env, agent=_AGENT
    )
    assert result.returncode == 0, f"wake exited {result.returncode}:\n{result.combined}"
    assert not marker_file.exists(), "the stub provider saw a real task dispatch"
    match = _PROBE_RE.search(result.combined)
    assert match, f"no provider-identity probe line in the wake output:\n{result.combined}"
    return match.group(1).strip()


def test_authorization_exported_by_the_invoking_shell_never_reaches_the_agent(
    tmp_path: pathlib.Path,
) -> None:
    observed = _run_wake(tmp_path, extra_env={_PROBE_VAR: "1"})
    assert observed == f"{_PROBE_VAR}=<unset>", observed


def test_authorization_exported_by_shell_env_never_reaches_the_agent(
    tmp_path: pathlib.Path,
) -> None:
    observed = _run_wake(tmp_path, shell_env_line=f"export {_PROBE_VAR}=1")
    assert observed == f"{_PROBE_VAR}=<unset>", observed
