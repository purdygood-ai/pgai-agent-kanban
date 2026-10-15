"""
claude_provider_stub.py
========================
Test-only ``claude`` PATH-shadowing stand-in for the provider-runtime
integration lane (v2.14.0-provider-runtime-hardening).

WHY THIS EXISTS
---------------
Three of the release's acceptance facts can only be established by watching
what the ACTUAL Claude CLI invocation would receive and do -- the derived
background-wait ceiling reaching the agent process's own environment, a
provider-safeguard refusal being retried and narrated, and the CLI version
appearing in the per-firing header. None of that is observable by calling
internal shell functions directly; it requires a real
``bash scripts/wake/claude.sh --agent=...`` subprocess whose ``provider_dispatch``
(``team/scripts/lib/provider_claude.sh``) actually execs a binary named
``claude``. This module installs that binary: a PATH-shadowing shell script,
following the same interception technique as ``git_network_stub.py``, so the
wake pipeline's real provider-invocation code path runs unmodified while the
network- and token-costing CLI itself is never reached.

WHAT THE STUB DOES
-------------------
- ``claude --version`` -- prints a fixed, caller-controlled version string
  and exits 0.  Answers the identity-log block's version query
  (``team/scripts/wake/claude.sh``'s ``PROVIDER-IDENTITY-LOG`` block).
- Any other invocation (a task dispatch via ``provider_dispatch``) --
  increments a durable call counter under *control_dir*, dumps its own
  received environment to ``call-<n>-env.txt`` (this is what proves a value
  reached the AGENT PROCESS, not merely a wake-script variable) and its argv
  to ``call-<n>-argv.txt``, then behaves according to the *mode* file under
  *control_dir* (defaults to ``"success"``):

    success                 -- writes ``## State\\nDONE`` into the task's
                                status.md (path extracted from the ``-p``
                                prompt argument's "Read the status at ..."
                                line -- the real prompt template
                                ``_wd_build_task_prompt`` emits), prints a
                                valid ``--output-format json`` payload on
                                stdout, exits 0.
    safeguard-then-success   -- call 1: prints a safeguard-shaped refusal to
                                stderr and exits 1 (no status.md write).
                                call 2 (and beyond): behaves exactly like
                                "success" above.
    usage-limit              -- every call: prints the provider's usage-limit
                                message (the text of ``usage-limit.txt``
                                under *control_dir* when present, else a
                                generic one with no reset time) and exits 1
                                without touching status.md, as the CLI does
                                when the account is out of capacity.

USAGE
-----
    from tests.fixtures.claude_provider_stub import install_claude_provider_stub

    def test_something(tmp_path):
        control_dir = tmp_path / "claude-stub-control"
        env_overrides = install_claude_provider_stub(tmp_path / "stub-bin", control_dir)
        (control_dir / "mode").write_text("safeguard-then-success", encoding="utf-8")
        result = subprocess.run([...], env={**os.environ, **env_overrides})
        assert (control_dir / "call-1-env.txt").exists()
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

_DEFAULT_VERSION = "2.1.251 (Claude Code)"

_SAFEGUARD_REFUSAL_TEXT = (
    "Error: request blocked by safety system before dispatch."
)

_USAGE_LIMIT_TEXT = "Claude usage limit reached."


def install_claude_provider_stub(
    bin_dir: Path,
    control_dir: Path,
    *,
    version: str = _DEFAULT_VERSION,
) -> dict[str, str]:
    """Write a PATH-shadowing ``claude`` stub into *bin_dir*, controlled by
    files under *control_dir*.

    Parameters
    ----------
    bin_dir:
        Directory to create (if absent) and place the stub ``claude`` script
        into.  Caller should pass a path under pytest's ``tmp_path`` -- never
        a bare ``/tmp`` path.
    control_dir:
        Directory (created here if absent) the stub reads its behavior mode
        from and writes call evidence into. Never shared between two
        concurrently-running fixtures within the same test -- each caller
        should pass its own subdirectory.
    version:
        The literal string ``claude --version`` prints. Defaults to a
        realistic Claude CLI version banner.

    Returns
    -------
    dict[str, str]
        Environment overrides -- a single ``PATH`` key with *bin_dir*
        prepended to the inherited ``PATH`` -- for callers to merge into the
        subprocess environment (e.g. ``{**os.environ, **overrides}``).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    control_dir.mkdir(parents=True, exist_ok=True)

    (control_dir / "version.txt").write_text(version + "\n", encoding="utf-8")
    mode_file = control_dir / "mode"
    if not mode_file.exists():
        mode_file.write_text("success\n", encoding="utf-8")

    stub_path = bin_dir / "claude"
    stub_script = f"""#!/usr/bin/env bash
# Test-only claude interceptor (v2.14.0-provider-runtime-hardening
# integration fixture). Never invokes a real provider CLI, never makes a
# network call, never consumes a token.
set -u
CONTROL_DIR={str(control_dir)!r}

# --- --version query: answer and exit, no call-evidence recorded ---
for _arg in "$@"; do
  if [[ "$_arg" == "--version" ]]; then
    cat "$CONTROL_DIR/version.txt"
    exit 0
  fi
done

mkdir -p "$CONTROL_DIR"

# --- Durable per-invocation call counter ---
count_file="$CONTROL_DIR/call_count"
n=0
[[ -f "$count_file" ]] && n="$(cat "$count_file")"
n=$((n + 1))
echo "$n" > "$count_file"

# --- Record the environment THIS process (the agent process) received ---
env > "$CONTROL_DIR/call-${{n}}-env.txt"

# --- Record argv for debugging / assertion ---
{{
  i=0
  for _a in "$@"; do
    printf 'argv[%d]=%s\\n' "$i" "$_a"
    i=$((i + 1))
  done
}} > "$CONTROL_DIR/call-${{n}}-argv.txt"

# --- Extract the -p prompt argument ---
prompt=""
_prev=""
for _a in "$@"; do
  if [[ "$_prev" == "-p" ]]; then
    prompt="$_a"
  fi
  _prev="$_a"
done

mode="success"
[[ -f "$CONTROL_DIR/mode" ]] && mode="$(cat "$CONTROL_DIR/mode")"

if [[ "$mode" == "safeguard-then-success" && "$n" -eq 1 ]]; then
  echo {_SAFEGUARD_REFUSAL_TEXT!r} >&2
  exit 1
fi

if [[ "$mode" == "usage-limit" ]]; then
  if [[ -f "$CONTROL_DIR/usage-limit.txt" ]]; then
    cat "$CONTROL_DIR/usage-limit.txt"
  else
    echo {_USAGE_LIMIT_TEXT!r}
  fi
  exit 1
fi

# --- Success path: locate the task's status.md from the real prompt text
# ("Read the status at <path> to determine ...", emitted verbatim by
# _wd_build_task_prompt in wake_driver_invoke.sh) and mark it DONE, as a
# real agent turn would. ---
status_path="$(printf '%s\\n' "$prompt" | grep -oE 'Read the status at [^[:space:]]+' | head -n1 | awk '{{print $NF}}')"
if [[ -n "$status_path" && -f "$status_path" ]]; then
  cat > "$status_path" <<'STATUSEOF'
# Status

## Task
stub-task

## State
DONE

## Summary
Stub provider completed the task (integration test double; no real CLI invoked).

## Artifacts
none

## Blockers
none

## Needs Human
no

## Next Recommended Step
none

## Instruction Conflicts
none
STATUSEOF
fi

echo '{{"type":"result","result":"stub agent completed the task","usage":{{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}}'
exit 0
"""
    stub_path.write_text(stub_script, encoding="utf-8")
    stub_path.chmod(
        stub_path.stat().st_mode
        | stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
    )

    existing_path = os.environ.get("PATH", "")
    return {"PATH": f"{bin_dir}{os.pathsep}{existing_path}"}


def set_stub_mode(control_dir: Path, mode: str) -> None:
    """Overwrite the behavior mode a previously-installed stub reads.

    *mode* is one of ``"success"`` (default) or ``"safeguard-then-success"``
    -- see this module's docstring for what each does.
    """
    (control_dir / "mode").write_text(mode + "\n", encoding="utf-8")


def call_count(control_dir: Path) -> int:
    """Number of times the stub has been invoked in task-dispatch mode
    (``--version`` queries are not counted)."""
    count_file = control_dir / "call_count"
    if not count_file.exists():
        return 0
    return int(count_file.read_text(encoding="utf-8").strip() or "0")


def call_env(control_dir: Path, n: int) -> dict[str, str]:
    """Parse the environment dump the stub wrote for call *n* (1-indexed)
    into a dict. Raises FileNotFoundError if that call never happened."""
    env_file = control_dir / f"call-{n}-env.txt"
    text = env_file.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key] = value
    return result
