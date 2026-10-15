r"""
test_defer_card_first_line_pipe_safety.py
=========================================
The bugs column's deferral-posture gather stage
(team/scripts/lib/dashboard/column_render_defer.sh) splits the first line of
dashboard_defer_card_ids' output into its mode and error-file fields, and
treats every later line as a deferred bug ID. The renderer that sources it
(team/scripts/dashboard/column-render.sh) runs under `set -euo pipefail`, so
any pipe stage in that parsing that stops reading early -- a reader taking
one line and exiting while the writer still has text to send -- turns the
writer's SIGPIPE into exit 141 and kills the whole column once the defer-card
list outgrows the pipe buffer (64 KiB on Linux).

These tests run both public entry points of the gather stage in a real bash
process under `set -euo pipefail`, with the real defer_scope.sh sourced for
DFS_SEP and load order, and only dashboard_defer_card_ids replaced by a stub
that prints a Python-generated output file well past the pipe buffer. Each
asserts the stage exits 0 and reads the right fields from the first line, and
that every deferred ID survives.

A control test runs the early-closing `printf | head -n1` shape over the same
generated input and asserts it does die with 141, so a passing gather-stage
test means the input really was large enough to trip an early-closing reader.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior; "pipe
safety" names the property, not an issue ID.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_DEFER_SCOPE = _TEAM_DIR / "scripts" / "dashboard" / "lib" / "defer_scope.sh"
_DEFER_GATHER = _TEAM_DIR / "scripts" / "lib" / "dashboard" / "column_render_defer.sh"

# The separator dashboard_defer_card_ids joins first-line fields with
# (DFS_SEP in defer_scope.sh: ASCII Unit Separator).
_SEP = "\x1f"

# Linux's default pipe capacity. The generated output is sized well past it
# so a writer cut off by an early-closing reader cannot finish inside the
# buffer, which makes the failure deterministic rather than a timing race.
_PIPE_BUFFER_BYTES = 64 * 1024
_ID_COUNT = 120_000  # "BUG-NNNNNN\n" * 120k ~= 1.3 MB

_MALFORMED_PATH = "/kanban/projects/demo/DEFER-LOW-BUGS"

# Bash driver shared by the tests. It mirrors column-render.sh's own
# preamble (strict mode, defer_scope.sh then column_render_defer.sh), then
# replaces the producer with a stub that prints the generated file.
_DRIVER_PREAMBLE = r"""
set -euo pipefail
source "$DEFER_SCOPE"
source "$DEFER_GATHER"
dashboard_defer_card_ids() { cat "$CARD_OUTPUT"; }
"""

_GATHER_INPUT_DRIVER = _DRIVER_PREAMBLE + r"""
KANBAN_ROOT=/kanban _RENDER_PROJECT=demo INPUT_DIR=/kanban/projects/demo/bugs
column_render_defer_gather_input
mapfile -t ids <<< "$DEFER_IDS"
printf 'mode=%s\n' "$DEFER_MODE"
printf 'errfile=%s\n' "$DEFER_ERROR_FILE"
printf 'id_count=%s\n' "${#ids[@]}"
printf 'last_id=%s\n' "${ids[-1]}"
"""

_MULTI_LINE_DRIVER = _DRIVER_PREAMBLE + r"""
column_render_defer_multi_line /kanban demo /kanban/projects/demo/bugs > "$LINE_OUT"
"""

# The early-closing reader shape, run over the same generated input.
_EARLY_CLOSING_READER_DRIVER = r"""
set -euo pipefail
out="$(cat "$CARD_OUTPUT")"
mode="$(printf '%s\n' "$out" | head -n1 | cut -d $'\x1f' -f1)"
printf 'mode=%s\n' "$mode"
"""


def _ids() -> list[str]:
    return [f"BUG-{i:06d}" for i in range(_ID_COUNT)]


def _write_card_output(tmp_path: pathlib.Path, first_line: str) -> pathlib.Path:
    """Write a defer-card output (first line, then one bug ID per line)
    larger than the pipe buffer, and return its path."""
    path = tmp_path / "card_output.txt"
    path.write_text(first_line + "\n" + "\n".join(_ids()) + "\n", encoding="utf-8")
    assert path.stat().st_size > _PIPE_BUFFER_BYTES * 4, path.stat().st_size
    return path


def _run(tmp_path: pathlib.Path, script: str, **extra: str) -> subprocess.CompletedProcess:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
            "DEFER_SCOPE": str(_DEFER_SCOPE),
            "DEFER_GATHER": str(_DEFER_GATHER),
            **extra,
        },
    )
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def _fields(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


@pytest.mark.parametrize(
    ("first_line", "expected_mode", "expected_errfile"),
    [
        (f"on{_SEP}", "on", ""),
        (f"malformed{_SEP}{_MALFORMED_PATH}", "malformed", _MALFORMED_PATH),
    ],
    ids=["deferral-on", "malformed-config"],
)
def test_gather_input_reads_first_line_fields_when_output_exceeds_pipe_buffer(
    tmp_path: pathlib.Path, first_line: str, expected_mode: str, expected_errfile: str
) -> None:
    """The single-project gather stage exits 0 under strict mode and sets
    DEFER_MODE / DEFER_ERROR_FILE from the first line, and DEFER_IDS to every
    later line, however far the output runs past the pipe buffer."""
    card_output = _write_card_output(tmp_path, first_line)
    result = _run(tmp_path, _GATHER_INPUT_DRIVER, CARD_OUTPUT=str(card_output))

    assert result.returncode == 0, (
        f"column_render_defer_gather_input exited {result.returncode} under "
        f"set -euo pipefail on a {card_output.stat().st_size}-byte defer-card "
        f"output (141 is SIGPIPE: a pipe reader closed before its writer "
        f"finished). stderr:\n{result.stderr}"
    )
    fields = _fields(result.stdout)
    assert fields.get("mode") == expected_mode, result.stdout[:500]
    assert fields.get("errfile") == expected_errfile, result.stdout[:500]
    assert fields.get("id_count") == str(_ID_COUNT), result.stdout[:500]
    assert fields.get("last_id") == _ids()[-1], result.stdout[:500]


@pytest.mark.parametrize(
    ("first_line", "expected_mode", "expected_errfile"),
    [
        (f"on{_SEP}", "on", ""),
        (f"malformed{_SEP}{_MALFORMED_PATH}", "malformed", _MALFORMED_PATH),
    ],
    ids=["deferral-on", "malformed-config"],
)
def test_multi_line_reads_first_line_fields_when_output_exceeds_pipe_buffer(
    tmp_path: pathlib.Path, first_line: str, expected_mode: str, expected_errfile: str
) -> None:
    """The multi-project gather stage exits 0 under strict mode and echoes
    one "mode<SEP>errfile<SEP>id|id|...|" line carrying every deferred ID,
    however far the output runs past the pipe buffer."""
    card_output = _write_card_output(tmp_path, first_line)
    line_out = tmp_path / "multi_line.txt"
    result = _run(
        tmp_path, _MULTI_LINE_DRIVER, CARD_OUTPUT=str(card_output), LINE_OUT=str(line_out)
    )

    assert result.returncode == 0, (
        f"column_render_defer_multi_line exited {result.returncode} under "
        f"set -euo pipefail on a {card_output.stat().st_size}-byte defer-card "
        f"output (141 is SIGPIPE: a pipe reader closed before its writer "
        f"finished). stderr:\n{result.stderr}"
    )
    line = line_out.read_text(encoding="utf-8")
    assert line.endswith("\n") and line.count("\n") == 1, repr(line[:200])
    mode, errfile, ids_joined = line.rstrip("\n").split(_SEP)
    assert mode == expected_mode
    assert errfile == expected_errfile
    assert ids_joined == "|".join(_ids()) + "|"


def test_early_closing_reader_over_the_same_input_dies_with_sigpipe(
    tmp_path: pathlib.Path,
) -> None:
    """Control: piping the same generated output into a reader that takes one
    line and exits kills the strict-mode shell with 141. This proves the input
    is large enough to trip an early-closing pipe reader, so the gather-stage
    tests above passing is evidence the parsing has no such reader."""
    card_output = _write_card_output(tmp_path, f"on{_SEP}")
    result = _run(tmp_path, _EARLY_CLOSING_READER_DRIVER, CARD_OUTPUT=str(card_output))

    assert result.returncode == 141, (
        f"expected the early-closing reader shape to exit 141 (SIGPIPE) on a "
        f"{card_output.stat().st_size}-byte input, got {result.returncode}; "
        f"the generated input may no longer exceed the pipe buffer. "
        f"stdout: {result.stdout!r} stderr: {result.stderr!r}"
    )
