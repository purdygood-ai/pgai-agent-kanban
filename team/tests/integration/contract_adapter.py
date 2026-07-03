"""
contract_adapter.py — swappable invocation adapter for operation contract tests.

This module provides the thin adapter layer that lets contract tests drive
kanban operations without coupling the assertions to the CLI invocation
mechanism.  Today every operation is invoked via its bash script; later a
REST adapter can be slotted in without touching any assertion.

## Adapter contract

An invocation adapter must satisfy the ``OperationAdapter`` Protocol:

    def invoke(
        self,
        operation: str,
        args: list[str],
        kanban_root: pathlib.Path,
        *,
        extra_env: dict | None = None,
        timeout: int = 30,
    ) -> "OperationResult": ...

Where ``OperationResult`` is:

    returncode: int    — process/adapter exit code
    stdout:     str    — captured standard output
    stderr:     str    — captured standard error
    env:        dict   — effective environment (for diagnostics)

## Adding a REST adapter

To run the same contract assertions against a REST surface:

    class RestAdapter:
        \"\"\"Invokes operations via the (future) kanban REST API.\"\"\"

        def __init__(self, base_url: str, api_key: str) -> None:
            self._base_url = base_url
            self._api_key = api_key

        def invoke(
            self,
            operation: str,
            args: list[str],
            kanban_root: pathlib.Path,
            *,
            extra_env: dict | None = None,
            timeout: int = 30,
        ) -> OperationResult:
            # Call the REST endpoint, inspect the HTTP response, inspect on-disk
            # state (kanban_root still applies if the server runs locally), and
            # return an OperationResult whose returncode is 0 on success.
            ...

Then parameterize the fixture:

    @pytest.fixture(params=["cli"])  # add "rest" when REST is available
    def invoker(request, ...):
        if request.param == "cli":
            return CliAdapter()
        if request.param == "rest":
            return RestAdapter(base_url="http://localhost:8080", api_key="...")

The assertions in contract test files never change — only the adapter added.

## Operation names and their CLI scripts

Operation names are canonical identifiers for kanban operations.  The
``CliAdapter`` maps each name to the corresponding bash script under
``team/scripts/``.

    "reset"       → scripts/reset.sh
    "close"       → scripts/close.sh
    "wontdo"      → scripts/wontdo.sh
    "delete"      → scripts/delete.sh
    "show"        → scripts/show.sh
    "halt"        → scripts/halt.sh
    "halt-global" → scripts/halt-global.sh
    "unhalt"      → scripts/unhalt.sh
    "unhalt-global" → scripts/unhalt-global.sh
    "intake"      → scripts/intake.sh

Unknown operations raise ``ValueError`` (fail fast; bad operation name is a
test-authoring error, not a behavior-under-test).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Result type — shared by all adapters.
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Mapping from canonical operation name to the bash script that implements it.
_CLI_SCRIPT_MAP: dict[str, pathlib.Path] = {
    "reset": _SCRIPTS_DIR / "reset.sh",
    "close": _SCRIPTS_DIR / "close.sh",
    "wontdo": _SCRIPTS_DIR / "wontdo.sh",
    "delete": _SCRIPTS_DIR / "delete.sh",
    "show": _SCRIPTS_DIR / "show.sh",
    "halt": _SCRIPTS_DIR / "halt.sh",
    "halt-global": _SCRIPTS_DIR / "halt-global.sh",
    "unhalt": _SCRIPTS_DIR / "unhalt.sh",
    "unhalt-global": _SCRIPTS_DIR / "unhalt-global.sh",
    "intake": _SCRIPTS_DIR / "intake.sh",
}


@dataclass
class OperationResult:
    """Result returned by any invocation adapter after running an operation.

    Attributes:
        returncode: Exit code of the operation (0 = success; non-zero = error/refusal).
        stdout:     Combined standard output captured from the operation.
        stderr:     Combined standard error captured from the operation.
        env:        The effective environment dict used for the invocation (for
                    diagnostics — not typically asserted on by contract tests).
    """

    returncode: int
    stdout: str
    stderr: str
    env: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CLI adapter — the default.
# ---------------------------------------------------------------------------


class CliAdapter:
    """Invokes kanban operations via their bash CLI scripts under team/scripts/.

    This is the default adapter used in all contract tests.  It drives the
    operation by running the corresponding bash script as a subprocess, with
    PGAI_AGENT_KANBAN_ROOT_PATH redirected to the caller's temp tree so the
    command never touches the live install.

    The PYTHONPATH is extended to include the team/ directory so that scripts
    that call ``python3 -m pgai_agent_kanban.ops`` can resolve the package.

    Usage::

        adapter = CliAdapter()
        result = adapter.invoke(
            "reset",
            ["--project", "project_a", "--key", "CODER-XXXX-T001-some-task"],
            kanban_root=two_project_root,
        )
        assert result.returncode == 0

    Args can include any flags the CLI script accepts.  The first positional
    element of *args* is forwarded directly to the script; no quoting magic
    is applied.
    """

    def invoke(
        self,
        operation: str,
        args: Sequence[str],
        kanban_root: pathlib.Path,
        *,
        extra_env: Optional[dict] = None,
        timeout: int = 30,
    ) -> OperationResult:
        """Run *operation* against *kanban_root* and return the result.

        Args:
            operation:   Canonical operation name (e.g. "reset", "close",
                         "wontdo", "delete").  Must be a key in
                         ``_CLI_SCRIPT_MAP``; raises ``ValueError`` if not.
            args:        CLI arguments forwarded to the bash script (e.g.
                         ``["--project", "project_a", "--key", task_id]``).
            kanban_root: Temp kanban root the subprocess operates against.
                         PGAI_AGENT_KANBAN_ROOT_PATH is set to this path.
            extra_env:   Optional additional environment variables.  Values
                         here override the base environment.
            timeout:     Subprocess timeout in seconds.

        Returns:
            ``OperationResult`` with returncode, stdout, stderr, and env.

        Raises:
            ValueError: If *operation* is not a recognized operation name.
            subprocess.TimeoutExpired: If the subprocess exceeds *timeout*.
        """
        if operation not in _CLI_SCRIPT_MAP:
            raise ValueError(
                f"Unknown operation {operation!r}. "
                f"Valid operations: {sorted(_CLI_SCRIPT_MAP)}"
            )
        script = _CLI_SCRIPT_MAP[operation]

        base_env = dict(os.environ)
        base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)

        # PYTHONPATH must include team/ for scripts that run
        # `python3 -m pgai_agent_kanban.ops`.
        existing_pp = base_env.get("PYTHONPATH", "")
        team_dir_str = str(_TEAM_DIR)
        if team_dir_str not in existing_pp.split(os.pathsep):
            base_env["PYTHONPATH"] = (
                team_dir_str
                + (os.pathsep + existing_pp if existing_pp else "")
            )

        # Prevent the live-install dev-tree path from leaking into the subprocess.
        base_env.pop("PGAI_DEV_TREE_PATH", None)

        if extra_env:
            base_env.update(extra_env)

        completed = subprocess.run(
            ["bash", str(script)] + list(args),
            capture_output=True,
            text=True,
            env=base_env,
            cwd=str(kanban_root),
            timeout=timeout,
        )

        return OperationResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            env=base_env,
        )
