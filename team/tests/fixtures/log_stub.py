"""
log_stub.py
===========
Test-fidelity helper: a faithful stub of the production log() bash function.

WHY THIS EXISTS (BUG-0161)
--------------------------
The production log() in team/scripts/lib/wake_common.sh is:

    log() {
      echo "[$(date -Iseconds)] wake(${AGENT}): $*" | tee -a "$LOG_FILE"
    }

The ``tee`` writes to both stdout (the terminal / capturing shell) AND the
log file.  When a caller uses command substitution to capture the output of
a function that calls log() internally, log output leaks into the captured
value:

    result=$(some_function_that_calls_log)

This is the command-substitution contamination bug that BUG-0161 describes.

A previous test stub implemented log() as stderr-only:

    log() { echo "[mock]: $*" >&2; }

That stub passed all tests because capturing stdout of the wrapper function
did not pick up any log output.  But the real log() DOES write to stdout,
so the test masked the bug rather than reproducing it.

This module provides a faithful Bash-embedded log() stub that:
  1. Writes to stdout (via tee or echo >&1), reproducing the contamination
     path that the old stderr-only stub did not exercise.
  2. Writes to a log file (append), matching the real tee -a behaviour.
  3. Is embeddable in shell heredocs and subprocess calls from Python tests.

HOW TO USE
----------
Embedded in a shell script under test (most common):

    from team.tests.fixtures.log_stub import make_log_stub_fragment

    shell_fragment = make_log_stub_fragment(log_file_path)
    # Prepend to the shell script string you're testing:
    script = shell_fragment + "\\n" + real_script_under_test

As a pytest fixture (request in a test):

    def test_capture_contamination(log_stub_fragment, tmp_path):
        # log_stub_fragment is a callable: log_stub_fragment(log_file_path)
        fragment = log_stub_fragment(tmp_path / "wake.log")
        # inject into script...

To verify stdout contamination is reproducible:

    import subprocess, sys
    from team.tests.fixtures.log_stub import make_log_stub_fragment

    log_file = tmp_path / "test.log"
    fragment = make_log_stub_fragment(log_file)
    # A shell function that calls log() then echoes a clean value:
    script = fragment + '''
    produce_value() {
        log "computing value"
        echo "clean_result"
    }
    output=$(produce_value)
    echo "CAPTURED: $output"
    '''
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    # The CAPTURED line will contain the log prefix AND "clean_result"
    # because log() tees to stdout — this is the contamination.
    assert "[log-stub]" in result.stdout  # contamination reproduced

WHAT THE STUB DOES AND DOES NOT DO
-----------------------------------
Does:
  - Write a timestamped line to stdout (reproducing tee -a stdout path)
  - Append the same line to the log file (reproducing tee -a file path)
  - Accept $* (multiple arguments, joined by space)

Does not:
  - Reproduce the exact timestamp format of the real log() (uses [log-stub]
    prefix instead of [$(date -Iseconds)] for determinism in test assertions)
  - Rotate or bound the log file (tests are responsible for temp-file cleanup)
  - Reproduce the AGENT variable interpolation (tests supply a fixed prefix)

PYTHON-SIDE HELPER: LogStubCapture
-----------------------------------
For tests that need to assert on what was logged, LogStubCapture provides a
context manager that runs a shell script with the faithful stub injected and
captures both stdout and the log-file content:

    from team.tests.fixtures.log_stub import LogStubCapture

    with LogStubCapture(tmp_path) as cap:
        cap.run_script(shell_script_text)
    assert "my message" in cap.stdout
    assert "my message" in cap.log_content
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import textwrap
from typing import Optional

import pytest


# ---------------------------------------------------------------------------
# Bash fragment builder
# ---------------------------------------------------------------------------


def make_log_stub_fragment(log_file: pathlib.Path) -> str:
    """Return a Bash fragment that defines a faithful log() stub.

    The stub mirrors the production log() from wake_common.sh:
      - Writes the message to stdout (the contamination path)
      - Appends the message to *log_file* (the file path)

    The fragment can be prepended to any shell script under test.  It uses
    a fixed prefix "[log-stub]" instead of an actual timestamp so test
    assertions are deterministic.

    Args:
        log_file:  Absolute path to the log file the stub should append to.
                   The file is created (touched) if it does not exist.

    Returns:
        A multi-line Bash string (no trailing newline) that can be pasted
        or concatenated into a shell script.  The fragment is safe under
        ``set -euo pipefail``.

    Example:
        log_file = tmp_path / "wake.log"
        fragment = make_log_stub_fragment(log_file)
        script = fragment + "\\nlog 'hello world'"
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        assert "[log-stub]" in result.stdout   # stdout contamination reproduced
        assert "hello world" in result.stdout

    BUG-0161: the previous stderr-only stub did not produce stdout output,
    so command-substitution contamination tests always passed even when the
    real code was broken.  This stub faithfully reproduces the tee-to-stdout
    path so the contamination is detectable.
    """
    log_file_str = str(log_file.resolve())
    return textwrap.dedent(f"""\
        # --- faithful log() stub (BUG-0161 test fidelity) ---
        # Mirrors production log() from team/scripts/lib/wake_common.sh:
        #   log() {{ echo "[...] wake($AGENT): $*" | tee -a "$LOG_FILE"; }}
        # Key: tee writes to BOTH stdout and the log file.  An stderr-only stub
        # would not reproduce the command-substitution contamination bug.
        __LOG_STUB_FILE="{log_file_str}"
        touch "${{__LOG_STUB_FILE}}"
        log() {{
            local _msg="[log-stub] $*"
            # tee: stdout (for contamination reproduction) + log file
            printf '%s\\n' "$_msg" | tee -a "${{__LOG_STUB_FILE}}"
        }}
        # --- end faithful log() stub ---
    """)


# ---------------------------------------------------------------------------
# Context-manager helper: LogStubCapture
# ---------------------------------------------------------------------------


class LogStubCapture:
    """Context manager: run a shell script with the faithful log() stub injected.

    Usage:

        with LogStubCapture(tmp_path) as cap:
            cap.run_script("log 'hello'; echo 'world'")
        assert "[log-stub] hello" in cap.stdout
        assert "[log-stub] hello" in cap.log_content
        assert "world" in cap.stdout

    The stub is automatically prepended to every script passed to run_script().
    Multiple run_script() calls within a single context share the same log file
    (each call appends to it).

    Attributes:
        stdout      — combined stdout of the last run_script() call
        stderr      — combined stderr of the last run_script() call
        returncode  — exit code of the last run_script() call
        log_content — full content of the stub log file after all run_script() calls
        log_file    — pathlib.Path to the stub log file
    """

    def __init__(self, tmp_dir: pathlib.Path, log_filename: str = "stub.log") -> None:
        """
        Args:
            tmp_dir:      A writable temporary directory (e.g. pytest's tmp_path).
            log_filename: Name of the log file to create under tmp_dir.
        """
        self._tmp_dir = tmp_dir
        self.log_file = tmp_dir / log_filename
        self.stdout = ""
        self.stderr = ""
        self.returncode = -1
        self.log_content = ""

    def __enter__(self) -> "LogStubCapture":
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.touch()
        return self

    def __exit__(self, *_args: object) -> None:
        # Read final log content for assertions.
        if self.log_file.exists():
            self.log_content = self.log_file.read_text(encoding="utf-8")

    def run_script(self, script: str) -> "LogStubCapture":
        """Run *script* under bash with the faithful log() stub prepended.

        The stub writes to both stdout and self.log_file.  stdout from the
        entire script (stub log lines + real echo lines) is captured in
        self.stdout.

        Args:
            script:  Bash script text to execute.  Should not redefine log().

        Returns:
            self (for chaining)

        Raises:
            Nothing — the returncode is stored in self.returncode.  Callers
            that want to assert on success should check self.returncode == 0.
        """
        fragment = make_log_stub_fragment(self.log_file)
        full_script = fragment + "\n" + textwrap.dedent(script)
        result = subprocess.run(
            ["bash", "-c", full_script],
            capture_output=True,
            text=True,
        )
        self.stdout = result.stdout
        self.stderr = result.stderr
        self.returncode = result.returncode
        # Update log_content after each run (log file is appended to)
        if self.log_file.exists():
            self.log_content = self.log_file.read_text(encoding="utf-8")
        return self


# ---------------------------------------------------------------------------
# pytest fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def log_stub_fragment() -> "LogStubFragmentFactory":
    """Pytest fixture: return a factory for the faithful log() stub fragment.

    The factory is a callable that takes a log_file path and returns the
    Bash fragment string:

        def test_contamination(log_stub_fragment, tmp_path):
            log_file = tmp_path / "wake.log"
            fragment = log_stub_fragment(log_file)
            script = fragment + "\\noutput=$(log 'hello')\\necho \"GOT: $output\""
            result = subprocess.run(["bash", "-c", script],
                                    capture_output=True, text=True)
            # "hello" appears inside $output because log() tees to stdout
            assert "[log-stub]" in result.stdout

    BUG-0161: the previous stderr-only stub would have produced empty $output
    in the scenario above, hiding the contamination bug.  This stub faithfully
    reproduces the tee-to-stdout path.
    """
    return make_log_stub_fragment


class LogStubFragmentFactory:
    """Type alias returned by the log_stub_fragment fixture.

    Not instantiated directly — see the log_stub_fragment fixture.
    """

    def __call__(self, log_file: pathlib.Path) -> str: ...


@pytest.fixture
def log_stub_capture(tmp_path: pathlib.Path) -> "LogStubCapture":
    """Pytest fixture: return a LogStubCapture context manager.

    Yields a LogStubCapture that writes its log file under tmp_path.

    Example:

        def test_stdout_and_log(log_stub_capture):
            with log_stub_capture as cap:
                cap.run_script("log 'test message'")
            assert "[log-stub] test message" in cap.stdout
            assert "[log-stub] test message" in cap.log_content

    BUG-0161: use this fixture to assert that a script's log() calls appear
    in BOTH stdout and the log file, confirming the contamination path is
    reproduced.
    """
    return LogStubCapture(tmp_path)
