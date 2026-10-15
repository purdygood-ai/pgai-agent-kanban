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

CWD CONTRACT
------------
run_bash() never lets the subprocess inherit whatever directory the pytest
*interpreter* happened to be started from -- docs/test-authoring-guidelines.md
("State your working directory; never inherit it") forbids that outright,
because it makes a suite's verdict a property of the invocation instead of
the tree.  Every existing caller writes its script-under-test path relative
to team/ (e.g. "scripts/lib/semver.sh"), the documented assumption being that
pytest's own working directory is team/ -- see run-unit-tests.sh,
run-integration-tests.sh and verify-test-order-independence.sh, which all
``cd`` into team/ before invoking pytest.  run_bash() defaults its
subprocess's cwd to that same team/ directory, computed from this file's own
location (Path(__file__).resolve().parents[2]) rather than read from an
environment variable or the interpreter's inherited cwd, so every caller's
relative script path keeps resolving the same way regardless of where the
pytest *process* was launched from.  A caller with a genuinely different
working-directory need may still pass ``cwd=`` explicitly.

ENV HYGIENE
-----------
By default, run_bash() inherits the test process's environment (which the
parent conftest's autouse _block_live_kanban_writes fixture has already
sanitised — PGAI_AGENT_KANBAN_ROOT_PATH and PGAI_TASKS_DIR are redirected to a
safe temp root).  Callers can supply extra env overrides via the ``extra_env``
parameter.

A caller whose verdict must not depend on the invoking shell passes
``base_env`` -- typically built with
``tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env`` -- and the
subprocess then starts from exactly that mapping instead of from
``os.environ`` (docs/test-authoring-guidelines.md, "Subprocess environments
are built from an explicit set, not inherited").  ``extra_env`` is still
layered on top.  Omitting ``base_env`` keeps the inherited behavior above
unchanged for every existing caller.

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
from typing import Mapping, Optional


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


def _team_root() -> Path:
    """Self-locate the team/ directory this file lives under.

    team/tests/unit/shell_harness.py -> parents[2] is team/.  This mirrors
    every caller's own convention of resolving library and script paths
    relative to team/ (see CWD CONTRACT above), and is computed the same
    way holder-subprocess fixtures already self-locate their repo root
    (e.g. test_lock_contention_staleness_reap.py's ``_repo_root()``).
    """
    return Path(__file__).resolve().parents[2]


def run_bash(
    tmp_path: Path,
    script: str,
    *,
    extra_env: Optional[dict[str, str]] = None,
    stdin: Optional[str] = None,
    timeout: int = 30,
    cwd: Optional[Path] = None,
    base_env: Optional[Mapping[str, str]] = None,
) -> BashResult:
    """Run *script* under bash and return captured output.

    The subprocess inherits the test process's environment (already sanitised
    by the autouse _block_live_kanban_writes fixture in team/tests/conftest.py)
    -- or starts from *base_env* when one is given -- with any ``extra_env``
    overrides applied on top.  PGAI_AGENT_KANBAN_TEMP_DIR
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
    cwd:
        Working directory for the subprocess.  Defaults to this file's own
        self-located team/ directory (see CWD CONTRACT above) rather than
        inheriting the pytest interpreter's cwd -- every existing caller's
        script paths are already written relative to team/, so the default
        preserves their behavior while fixing the class of bug where pytest
        is invoked from somewhere else.  Pass an explicit ``cwd`` only when
        a test's own scenario calls for a different working directory.
    base_env:
        Optional complete starting environment for the subprocess.  When
        given, it replaces the inherited ``os.environ`` copy outright -- a
        name absent from *base_env* (and from *extra_env*) is absent in the
        child, whatever the invoking shell exported.  Default ``None``
        inherits ``os.environ`` as described above.

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
      set kanban env vars beyond what the calling test inherits (or passes
      in *base_env*).  Use
      ``extra_env`` for test-specific overrides and
      ``team/tests/fixtures/log_stub.py`` for log() injection.
    """
    env = dict(base_env) if base_env is not None else dict(os.environ)
    if extra_env:
        env.update(extra_env)

    resolved_cwd = cwd if cwd is not None else _team_root()

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=stdin,
        cwd=str(resolved_cwd),
    )
    return BashResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )
