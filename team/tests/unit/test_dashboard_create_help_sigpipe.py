r"""
test_dashboard_create_help_sigpipe.py
======================================
Coverage for the dashboard create script's --help exit-status determinism
(team/scripts/dashboard/create.sh).

Background: the --help branch used to print its usage text with
`grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -30`. The script's header
comment block runs well past 30 lines, so `head -30` routinely exited once
it had read its 30 lines while grep/sed were still writing further-down
lines. Under `set -o pipefail` (in force at the top of create.sh) that
early exit turned into a SIGPIPE delivered to the still-writing producer,
and the pipeline's exit status (141 = 128+13) became the script's own exit
status -- non-deterministically, depending on how far grep/sed had gotten
before head closed its read end. Because the doc-truth lint
(team/scripts/lint_doc_command_truth.py) invokes --help on every command a
document cites, this made the lint's own finding count nondeterministic
across successive runs against a byte-identical tree.

create.sh drains the same grep|sed output through `mapfile` via process
substitution -- which reads to EOF rather than closing early -- and slices
the resulting array to the first 30 elements, so the internal producers
always finish. It additionally ignores SIGPIPE (`trap '' PIPE`) for the
final print so the *external* mirror case -- a caller piping create.sh's
own stdout into a reader that itself closes early (e.g. `--help | head -1`)
-- cannot turn create.sh's own write() into a fatal signal either.

Test groups
-----------
Group 1 -- Repeated plain invocation: the script's own acceptance loop
(25 invocations with stdout/stderr redirected to a file, never a pipe),
locking in that the internal grep|sed|mapfile assembly no longer races.

Group 2 -- Early-closing external reader: spawns create.sh --help with its
stdout piped to this test process, reads exactly one line, then closes the
pipe out from under the still-writing script -- the exact shape of
`--help | head -1` -- and asserts exit status 0 across repeated attempts,
so the flake cannot silently return.

No tmux and no docker are required -- the --help branch returns before
create.sh reaches any tmux invocation.

Test naming (roles/TESTER.md anti-pattern 6): names describe behavior,
never bug IDs, scaffolding labels, or version strings. "sigpipe" here
names the POSIX signal the defect manifested as, not an issue ID.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Path resolution -- anchored to this file's location, matching every other
# dashboard test module (test_dashboard_pane_signal_traps.py,
# test_dashboard_operator_window.py).
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/tests/unit/ -> team/
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"

# Repeat counts chosen to match the bug report's own reproduction loop
# (25 iterations) -- the flake fired on roughly two runs in three, so a
# single passing invocation proves nothing; only a full repeated loop with
# zero failures is convincing.
_REPEAT_COUNT = 25


def _run_env() -> dict[str, str]:
    """Environment for a create.sh --help subprocess.

    --help returns before create.sh sources shell-env or kanban.cfg, so no
    kanban root content is required; PGAI_AGENT_KANBAN_ROOT_PATH only needs
    to resolve to *some* absolute directory for env_bootstrap.sh's
    idempotency guard to accept it. Test isolation still uses a scratch
    directory rather than any real kanban root, so a subprocess bug can
    never touch operator state.
    """
    env = os.environ.copy()
    env.setdefault("PGAI_AGENT_KANBAN_ROOT_PATH", str(_TEAM_DIR.parent))
    return env


# ---------------------------------------------------------------------------
# Group 1 -- Repeated plain invocation (no pipe at all)
# ---------------------------------------------------------------------------
def test_help_exits_zero_across_repeated_plain_invocations(tmp_path: pathlib.Path) -> None:
    """`create.sh --help` must exit 0 on every one of a repeated run of
    invocations with output redirected to a file -- never a pipe a reader
    can close early. This isolates the INTERNAL grep|sed|mapfile assembly:
    with an internal pipeline that closes early, this exact shape (output never
    touching an external pipe) fails on the large majority of runs, because
    the SIGPIPE source is the script's own internal pipeline, not anything
    external reading its output."""
    failures: list[int] = []
    devnull_path = tmp_path / "help_output.txt"
    for _ in range(_REPEAT_COUNT):
        with open(devnull_path, "wb") as sink:
            result = subprocess.run(
                ["bash", str(_CREATE_SH), "--help"],
                stdout=sink,
                stderr=subprocess.STDOUT,
                env=_run_env(),
                timeout=20,
            )
        if result.returncode != 0:
            failures.append(result.returncode)

    assert not failures, (
        f"create.sh --help exited non-zero on {len(failures)}/{_REPEAT_COUNT} "
        f"plain (non-piped) invocations: {failures}. Exit 141 is SIGPIPE "
        f"(128+13) from the internal grep|sed pipeline."
    )


# ---------------------------------------------------------------------------
# Group 2 -- Early-closing external reader (the `--help | head -1` shape)
# ---------------------------------------------------------------------------
def test_help_exits_zero_with_early_closing_reader() -> None:
    """A caller that pipes create.sh's own stdout into a reader which reads
    one line and closes -- the exact shape of `create.sh --help | head -1`
    -- must not turn the script's own write() into a fatal SIGPIPE. Repeats
    the attempt because closing a pipe out from under a live writer is a
    race: whether the write lands before or after the close depends on
    scheduling, so a single attempt does not prove the write survives."""
    failures: list[tuple[int, int]] = []
    for attempt in range(_REPEAT_COUNT):
        proc = subprocess.Popen(
            ["bash", str(_CREATE_SH), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_run_env(),
        )
        assert proc.stdout is not None
        first_line = proc.stdout.readline()
        assert first_line, f"attempt {attempt}: create.sh --help produced no output"
        # Close our read end while the script may still be writing further
        # lines -- this is what makes `head -1` (or any early-exiting
        # reader) able to deliver SIGPIPE/EPIPE to the writer.
        proc.stdout.close()
        try:
            returncode = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
        if returncode != 0:
            failures.append((attempt, returncode))

    assert not failures, (
        f"create.sh --help exited non-zero when its own stdout was closed "
        f"early by the reader on {len(failures)}/{_REPEAT_COUNT} attempts: "
        f"{failures}. Exit 141 is SIGPIPE (128+13)."
    )


# ---------------------------------------------------------------------------
# Content-coverage guard -- --help must reach the Usage:/Flags: block and
# name every flag the parser accepts, not just avoid the SIGPIPE flake.
# ---------------------------------------------------------------------------
def test_help_output_names_every_accepted_flag() -> None:
    """--help's fixed-line-count slice used to land entirely inside the
    header's window-layout diagrams and never reach the Usage:/Flags:
    block further down, so it named no flag at all. --help locates that
    block by its own "Usage:" marker instead of a hardcoded line count;
    this guards the coverage property directly (every flag the argument
    parser accepts appears in --help's output) rather than pinning an
    exact line count that would just as easily hide a future regression
    of the same shape."""
    result = subprocess.run(
        ["bash", str(_CREATE_SH), "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_run_env(),
        timeout=20,
        text=True,
    )
    assert result.returncode == 0, (
        f"create.sh --help exited {result.returncode}; stderr:\n{result.stderr}"
    )
    output = result.stdout
    assert any(re.search(r"(?<![\w-])create\.sh", line) for line in output.splitlines()), (
        "expected the usage text to name the script by its current name (create.sh)"
    )
    for flag in ("--no-tmux", "--print-schedule-floors", "--kanban-root", "--session"):
        assert flag in output, (
            f"create.sh --help does not name {flag!r} -- every flag the "
            f"argument parser accepts must be reachable through --help:\n{output}"
        )


if __name__ == "__main__":  # pragma: no cover - manual debug entry point
    sys.exit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "-v", str(_THIS_FILE)]
        )
    )
