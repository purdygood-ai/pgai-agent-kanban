"""
helpers.py — shared integration test helpers.

These helpers support real end-to-end integration tests by providing a thin
wrapper around subprocess invocations of kanban entrypoints against a
temporary kanban tree.

Integration tests use these helpers to:
  - Run bash entrypoints (wake scripts, operator commands, install scripts)
    against a real temp tree and capture stdout, stderr, exit code.
  - Inspect on-disk state after a real flow runs.

All paths used here are under pytest's tmp_path or the framework temp root
(PGAI_AGENT_KANBAN_TEMP_DIR).  No bare /tmp, no HOME references.

Usage example in an integration test:

    from tests.integration.helpers import run_entrypoint

    def test_show_command_prints_active_rc(two_project_root, tmp_path):
        result = run_entrypoint(
            ["bash", str(scripts_dir / "operator.sh"), "show"],
            kanban_root=two_project_root,
        )
        assert result.returncode == 0
        assert "Active RC" in result.stdout
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence


@dataclass
class EntrypointResult:
    """Result of running a kanban entrypoint subprocess.

    Attributes:
        returncode: The process exit code.
        stdout:     Captured standard output (decoded, UTF-8).
        stderr:     Captured standard error (decoded, UTF-8).
        env:        The environment dict that was passed to the subprocess.
    """

    returncode: int
    stdout: str
    stderr: str
    env: dict = field(default_factory=dict)


def run_entrypoint(
    cmd: Sequence[str],
    kanban_root: pathlib.Path,
    *,
    extra_env: Optional[dict] = None,
    cwd: Optional[pathlib.Path] = None,
    timeout: int = 60,
) -> EntrypointResult:
    """Run a kanban entrypoint subprocess against a real temporary kanban tree.

    Sets PGAI_AGENT_KANBAN_ROOT_PATH (and PGAI_TASKS_DIR) to point at
    *kanban_root* so the subprocess operates against the caller's temp tree,
    not the live install.  Additional env vars from *extra_env* are merged on
    top (extra_env values win).

    Args:
        cmd:         Command and arguments to execute (e.g.
                     ["bash", "/path/to/team/scripts/wake-batch.sh",
                      "--agent=pm"]).
        kanban_root: Path to the temporary kanban root the subprocess should
                     operate against.  Must exist.  Typically created by the
                     installed_root or two_project_root fixture.
        extra_env:   Optional additional environment variables to set for the
                     subprocess.  Keys present here override the base env.
        cwd:         Working directory for the subprocess.  Defaults to
                     *kanban_root*.
        timeout:     Subprocess timeout in seconds.  Defaults to 60.

    Returns:
        EntrypointResult with returncode, stdout, stderr, and the env dict used.

    Raises:
        subprocess.TimeoutExpired: If the subprocess does not complete within
            *timeout* seconds.

    Design notes:
        - The base environment inherits from os.environ so the subprocess
          can find bash, python3, etc. on PATH.
        - PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_TASKS_DIR are always
          overridden by this helper.  The caller's monkeypatch or autouse
          fixture values are also overridden by kanban_root, so the subprocess
          sees a consistent root.
        - PGAI_CRONTAB_CMD is forwarded when set in os.environ (the
          integration runner sets this seam before launching pytest, so
          subprocesses that source install scripts pick up the stub
          automatically).
        - All temp paths created inside the subprocess must land under
          PGAI_AGENT_KANBAN_TEMP_DIR, which this helper inherits from the
          calling process's environment.
    """
    base_env = dict(os.environ)

    # Redirect the subprocess to the caller's temp tree, not the live install.
    tasks_dir = str(kanban_root / "tasks")
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_TASKS_DIR"] = tasks_dir

    # Clear queue-dir and release-state overrides that the parent conftest's
    # autouse fixture may have set — the subprocess should derive these from
    # PGAI_AGENT_KANBAN_ROOT_PATH.
    base_env.pop("PGAI_QUEUE_DIR", None)
    base_env.pop("PGAI_RELEASE_STATE_PATH", None)

    # Merge caller-supplied overrides (caller wins).
    if extra_env:
        base_env.update(extra_env)

    effective_cwd = cwd if cwd is not None else kanban_root

    completed = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(effective_cwd),
        timeout=timeout,
    )

    return EntrypointResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        env=base_env,
    )
