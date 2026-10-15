"""
test_discovery_item_path_guard.py
=================================
Unit tests for the item-path guard shared by discovery_step_bugs and
discovery_step_priority in team/scripts/lib/discovery_steps.sh
(_disc_scan_output_has_item_paths), driven under the same strict mode the
production caller runs it in: pm-agent.sh sets ``set -euo pipefail`` before
it calls discovery_run_pipeline.

The guard's verdict must depend only on its input. Under ``pipefail`` a
check written as a producer piped into an early-exiting reader (``grep -q``)
is not: the reader may exit on the first match while the producer is still
writing, the producer takes SIGPIPE (141), and ``pipefail`` turns the match
into a no-match. Three behaviours are pinned here:

  1. A two-line input where every line is an absolute path is accepted,
     silently.
  2. An input holding no absolute path is refused with return 1, and the
     log channel names the count and quotes every rejected line.
  3. The two-line all-path input is still accepted when every pipe reader
     exits after its first line and every multi-line writer is held back
     until that reader has gone. The scheduling is forced with a FIFO
     handshake rather than widened with a sleep or with load, so the verdict
     is the same on an idle host and a saturated one: a guard that pipes its
     input into an early-exiting reader fails this test every run, and a
     guard that inspects its input without a pipe passes it every run.

Every test drives the real bash function through the shared shell harness.

Naming: test names describe the behaviour under test, not the ticket that
motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import textwrap

from tests.unit.shell_harness import run_bash

_STATE_LIB = "scripts/lib/discovery_state.sh"
_STEPS_LIB = "scripts/lib/discovery_steps.sh"

# The production strict mode, plus a log() that writes to stderr with echo so
# the scheduling shims below never see the guard's own narration.
_PRELUDE = textwrap.dedent(f"""\
    set -euo pipefail
    source {_STATE_LIB}
    source {_STEPS_LIB}
    log() {{ echo "$*" >&2; }}
    """)

_TWO_PATHS = "/abs/project_a/bugs/BUG-0001-one.md\n/abs/project_a/bugs/BUG-0002-two.md"


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _guard_call(scan_file: pathlib.Path, step_name: str) -> str:
    """Bash that captures *scan_file* the way the steps capture the finder's
    stdout (command substitution, trailing newline stripped) and calls the
    guard inside an ``if``, as discovery_step_bugs and discovery_step_priority
    do, printing a one-word verdict on stdout."""
    return textwrap.dedent(f"""\
        _scan="$(cat '{scan_file}')"
        if _disc_scan_output_has_item_paths '{step_name}' "$_scan"; then
            echo accepted
        else
            echo rejected
        fi
        """)


def _write_scan(tmp_path: pathlib.Path, scan_output: str) -> pathlib.Path:
    scan_file = tmp_path / "scan-output.txt"
    scan_file.write_text(scan_output, encoding="utf-8")
    return scan_file


class TestGuardVerdictUnderStrictMode:
    def test_two_line_all_path_input_is_accepted_silently(self, tmp_path):
        scan_file = _write_scan(tmp_path, _TWO_PATHS)

        result = run_bash(tmp_path, _PRELUDE + _guard_call(scan_file, "discovery_step_bugs"))

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "accepted", (
            "a scan output whose every line is an absolute path must pass the "
            "guard\n" + _diagnostic(result)
        )
        assert "rejected non-path line" not in result.stderr, _diagnostic(result)
        assert "skipping bundle" not in result.stderr, _diagnostic(result)

    def test_non_path_input_is_refused_and_the_rejected_line_is_narrated(self, tmp_path):
        stray = "WARNING: something upstream echoed this"
        scan_file = _write_scan(tmp_path, stray)

        result = run_bash(
            tmp_path, _PRELUDE + _guard_call(scan_file, "discovery_step_priority")
        )

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "rejected", (
            "a scan output with no line starting with '/' must be refused\n"
            + _diagnostic(result)
        )
        assert (
            "discovery_step_priority: no valid item paths after filtering; skipping bundle"
            in result.stderr
        ), _diagnostic(result)
        assert "1 non-path line(s)" in result.stderr, _diagnostic(result)
        assert f"discovery_step_priority: rejected non-path line: '{stray}'" in result.stderr, (
            _diagnostic(result)
        )

    def test_guard_returns_one_when_called_bare_under_errexit(self, tmp_path):
        # Outside an `if`, errexit is live inside the function body: the
        # refusal must come from the guard's own `return 1`, not from a
        # helper command tripping `set -e` part-way through the narration.
        scan_file = _write_scan(tmp_path, "not a path\nnor this")
        script = _PRELUDE + textwrap.dedent(f"""\
            _scan="$(cat '{scan_file}')"
            _disc_scan_output_has_item_paths 'discovery_step_bugs' "$_scan"
            echo unreachable
            """)

        result = run_bash(tmp_path, script)

        assert result.returncode == 1, _diagnostic(result)
        assert "unreachable" not in result.stdout, _diagnostic(result)
        assert "2 non-path line(s)" in result.stderr, _diagnostic(result)
        for line in ("not a path", "nor this"):
            assert f"rejected non-path line: '{line}'" in result.stderr, _diagnostic(result)


def _reader_exits_first_shims(fifo: pathlib.Path) -> str:
    """Bash that forces the losing schedule for a producer piped into
    ``grep -q``, deterministically.

    ``printf '%s\\n' <multi-line value>`` (the shape a pipeline guard writes
    with) is replaced by a writer that emits the first line, then waits on
    the handshake before emitting the rest. ``grep`` is replaced by a reader
    that takes one line, closes its stdin, posts the handshake, and reports
    whether that line starts with '/'. The handshake is a FIFO held open
    read-write by the calling shell on fd 7, so neither side ever blocks on
    open; the writer's wait is bounded (``read -t``) so a guard that calls
    printf without grep cannot hang the test. Every other printf call passes
    straight through to the builtin.
    """
    return textwrap.dedent(f"""\
        mkfifo '{fifo}'
        exec 7<>'{fifo}'
        printf() {{
            if [[ $# -eq 2 && "$1" == '%s\\n' && "$2" == *$'\\n'* ]]; then
                builtin printf '%s\\n' "${{2%%$'\\n'*}}"
                read -r -t 10 -u 7 _handshake || true
                builtin printf '%s\\n' "${{2#*$'\\n'}}"
            else
                builtin printf "$@"
            fi
        }}
        grep() {{
            local _first=""
            IFS= read -r _first || true
            exec 0<&-
            echo reader-gone >&7
            [[ "$_first" == /* ]]
        }}
        """)


class TestGuardVerdictIndependentOfWriteScheduling:
    def test_all_path_input_is_accepted_when_the_reader_exits_before_the_writer_finishes(
        self, tmp_path
    ):
        scan_file = _write_scan(tmp_path, _TWO_PATHS)
        fifo = tmp_path / "handshake.fifo"
        script = _PRELUDE + _reader_exits_first_shims(fifo) + _guard_call(
            scan_file, "discovery_step_bugs"
        )

        result = run_bash(tmp_path, script)

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "accepted", (
            "the guard refused a valid two-path scan output when a pipe reader "
            "exited before the writer's second line -- its verdict depends on "
            "write scheduling (SIGPIPE under pipefail), so a loaded host can "
            "make discovery skip a valid bundle\n" + _diagnostic(result)
        )
        assert "rejected non-path line" not in result.stderr, _diagnostic(result)

    def test_scheduling_shims_reproduce_the_pipefail_loss_for_a_piped_check(self, tmp_path):
        # Calibration: the shims above must really force the losing schedule,
        # or the test before this one would pass for the wrong reason. A
        # pipeline check of the exact shape a pipe-based guard uses must lose
        # its match to SIGPIPE under them, every run.
        fifo = tmp_path / "handshake.fifo"
        two_paths_ansi_c = _TWO_PATHS.replace("\n", "\\n")
        script = (
            "set -euo pipefail\n"
            + _reader_exits_first_shims(fifo)
            + textwrap.dedent(f"""\
                _scan=$'{two_paths_ansi_c}'
                if printf '%s\\n' "$_scan" | grep -q '^/'; then
                    echo "matched"
                else
                    echo "lost PIPESTATUS=${{PIPESTATUS[*]}}"
                fi
                """)
        )

        result = run_bash(tmp_path, script)

        assert result.returncode == 0, _diagnostic(result)
        assert result.stdout.strip() == "lost PIPESTATUS=141 0", (
            "the reader-exits-first shims no longer force SIGPIPE on the "
            "writer, so the scheduling test proves nothing\n" + _diagnostic(result)
        )
