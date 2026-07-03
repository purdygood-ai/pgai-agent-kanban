"""
shell_harness.py — shared helper for unit tests that invoke shell scripts.

WHY THIS EXISTS
---------------
Several modules under team/scripts/lib/ contain pure helper functions (parsers,
resolvers, semver logic) that can be unit-tested by sourcing the script and
invoking the function via bash.  The test_*_bash.py pattern does this:

    result = run_bash_function(tmp_path, "source lib/semver.sh && semver_parse 1.2.3")
    assert result.stdout.strip() == "1 2 3"

This helper formalises the pattern from TESTING.md failure mode 1:
  "When the production entry point is a bash script under strict mode,
   the test must invoke that script — not a Python shortcut around it."

The helper does NOT introduce magic: it runs bash, captures output, and
returns a plain dataclass.  Tests are responsible for constructing the
script text and interpreting the result.

TEMP-PATH CONTRACT
------------------
All temp paths required by the harness are created under tmp_path (the pytest
fixture), which the parent conftest.py already redirects to
$PGAI_AGENT_KANBAN_TEMP_DIR/tests/.  No bare /tmp paths are used anywhere.

ENV HYGIENE
-----------
By default, run_bash() inherits the test process's environment (which the
parent conftest's autouse _block_live_kanban_writes fixture has already
sanitised — PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_TASKS_DIR are redirected to a
safe temp root).  Callers can supply extra env overrides via the ``extra_env``
parameter.

USAGE
-----
Invoke a shell function after sourcing a library:

    from team.tests.unit.shell_harness import run_bash

    def test_semver_parse_returns_tuple(tmp_path):
        lib = pathlib.Path("team/scripts/lib/semver.sh")
        result = run_bash(
            tmp_path,
            f"source {lib!s} && semver_parse 1.2.3",
        )
        assert result.returncode == 0
        assert "1 2 3" in result.stdout

Run a full script:

    def test_script_exits_nonzero_on_bad_input(tmp_path):
        result = run_bash(
            tmp_path,
            "bash team/scripts/some-script.sh --invalid-flag",
        )
        assert result.returncode != 0
        assert "ERROR" in result.stderr
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BashResult:
    """Captured output from a bash invocation.

    Attributes:
        stdout      Full standard output from the subprocess (decoded as UTF-8).
        stderr      Full standard error from the subprocess (decoded as UTF-8).
        returncode  Exit status of the bash process.
    """

    stdout: str
    stderr: str
    returncode: int


def run_bash(
    tmp_path: Path,
    script: str,
    *,
    extra_env: Optional[dict[str, str]] = None,
    stdin: Optional[str] = None,
    timeout: int = 30,
) -> BashResult:
    """Run *script* under bash and return captured output.

    The subprocess inherits the test process's environment (already sanitised
    by the autouse _block_live_kanban_writes fixture in team/tests/conftest.py)
    with any ``extra_env`` overrides applied on top.  PGAI_AGENT_KANBAN_TEMP_DIR
    is passed through so that scripts sourcing temp.sh route temp files under
    the framework root rather than bare /tmp.

    Parameters
    ----------
    tmp_path:
        The pytest ``tmp_path`` fixture value for the calling test.  Any
        temp files this helper needs to create are placed here.  Do NOT
        pass a raw /tmp path.
    script:
        Bash script text to execute.  May contain any valid bash code,
        including ``source`` calls, here-docs, and subshells.
    extra_env:
        Optional dict of environment variables to set (or override) in the
        subprocess.  Keys and values are both strings.
    stdin:
        Optional string to pass to the subprocess on stdin.
    timeout:
        Maximum seconds to wait for the subprocess to finish.
        Default: 30 seconds.  Raise ``subprocess.TimeoutExpired`` if exceeded.

    Returns
    -------
    BashResult
        Captured stdout, stderr, and exit code.

    Raises
    ------
    subprocess.TimeoutExpired
        If the subprocess does not finish within *timeout* seconds.

    Notes
    -----
    - The script is run under ``bash -c``.  It is the caller's responsibility
      to add ``set -euo pipefail`` to the script text if strict mode is needed
      (some tests deliberately test failure paths that would abort under -e).
    - This helper is intentionally minimal.  It does not inject log stubs or
      set kanban env vars beyond what the calling test inherits.  Use
      ``extra_env`` for test-specific overrides and
      ``team/tests/fixtures/log_stub.py`` for log() injection.
    """
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=stdin,
    )
    return BashResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
