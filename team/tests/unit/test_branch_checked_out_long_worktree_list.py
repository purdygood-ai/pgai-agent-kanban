r"""
test_branch_checked_out_long_worktree_list.py
=============================================
team/scripts/cleanup-stale-artifact-only-branches.sh decides whether a
branch is safe to delete with ``branch_is_checked_out``, which looks for
``refs/heads/<branch>`` in the branch lines of ``git worktree list
--porcelain``. The script runs under ``set -euo pipefail``. If that list
were piped straight into ``grep -q``, grep would exit at its first match
while the producer was still writing; once the producer's output outgrows
the pipe buffer (64 KiB on Linux) it dies of SIGPIPE, pipefail turns the
pipeline non-zero, and a checked-out branch reads as not checked out.

These tests run the script's own ``branch_is_checked_out`` definition in a
real bash process under ``set -euo pipefail``, with ``git`` replaced by a
shell function that prints a generated worktree listing: the wanted branch
first, then far more than a pipe buffer of other branch lines. They assert
the match is still reported, and that an absent branch still reads as not
checked out.

A control test runs the early-closing piped shape over the same generated
listing and asserts it reports a miss, so a passing test above means the
listing really was large enough to trip an early-closing reader.

The script is not sourceable (it parses arguments and acts at top level),
so the function is lifted from the script text and evaluated alone; nothing
here runs git, touches a repository, or reads the live install.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_SCRIPT = _TEAM_DIR / "scripts" / "cleanup-stale-artifact-only-branches.sh"

# Linux's default pipe capacity. The listing is sized well past it so a
# producer cut off by an early-closing reader cannot finish inside the
# buffer, which makes the failure deterministic rather than a timing race.
_PIPE_BUFFER_BYTES = 64 * 1024
_FILLER_WORKTREES = 20_000

_WANTED_BRANCH = "ai_feature/wanted-task"

# The function definition, from its opening line to the first line that is
# a lone closing brace. Liberal about spacing around the parentheses.
_FUNCTION_RE = re.compile(r"^branch_is_checked_out\s*\(\s*\)\s*\{.*?^\}\s*$", re.M | re.S)

# Strict mode as the script sets it, a git stand-in that prints the
# generated listing for "git worktree list --porcelain", then the function.
_DRIVER = r"""
set -euo pipefail
git() { cat "$WORKTREE_LISTING"; }
eval "$FUNCTION_SOURCE"
if branch_is_checked_out "$BRANCH"; then echo checked-out; else echo not-checked-out; fi
"""

# The early-closing piped shape, run over the same listing.
_PIPED_CONTROL_DRIVER = r"""
set -euo pipefail
git() { cat "$WORKTREE_LISTING"; }
if git worktree list --porcelain | awk '/^branch /{print $2}' | grep -qxF "refs/heads/${BRANCH}"; then
    echo checked-out
else
    echo not-checked-out
fi
"""


def _function_source() -> str:
    """The script's own branch_is_checked_out definition."""
    match = _FUNCTION_RE.search(_SCRIPT.read_text(encoding="utf-8"))
    assert match is not None, f"branch_is_checked_out() not found in {_SCRIPT}"
    return match.group(0)


def _write_listing(tmp_path: pathlib.Path) -> pathlib.Path:
    """A porcelain worktree listing: the wanted branch first, then filler
    worktrees whose branch lines alone run far past the pipe buffer."""
    blocks = [f"worktree /work/wanted\nHEAD {'a' * 40}\nbranch refs/heads/{_WANTED_BRANCH}\n"]
    blocks += [
        f"worktree /work/filler-{n}\nHEAD {'b' * 40}\nbranch refs/heads/ai_feature/filler-task-{n:06d}\n"
        for n in range(_FILLER_WORKTREES)
    ]
    path = tmp_path / "worktree-list.txt"
    path.write_text("\n".join(blocks), encoding="utf-8")
    branch_bytes = sum(len(f"refs/heads/ai_feature/filler-task-{n:06d}\n") for n in range(_FILLER_WORKTREES))
    assert branch_bytes > _PIPE_BUFFER_BYTES * 4, branch_bytes
    return path


def _run(tmp_path: pathlib.Path, script: str, branch: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
            "WORKTREE_LISTING": str(_write_listing(tmp_path)),
            "FUNCTION_SOURCE": _function_source(),
            "BRANCH": branch,
        },
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120, check=False
    )


def test_checked_out_branch_is_found_when_the_worktree_list_exceeds_the_pipe_buffer(
    tmp_path: pathlib.Path,
) -> None:
    result = _run(tmp_path, _DRIVER, _WANTED_BRANCH)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "checked-out", (
        "branch_is_checked_out reported a checked-out branch as free when the worktree "
        f"list was longer than a pipe buffer.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


def test_absent_branch_is_not_checked_out_in_a_long_worktree_list(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, _DRIVER, "ai_feature/no-such-task")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "not-checked-out"


def test_control_piped_early_closing_grep_misses_the_match(tmp_path: pathlib.Path) -> None:
    """The listing is large enough that the piped shape reports a miss."""
    result = _run(tmp_path, _PIPED_CONTROL_DRIVER, _WANTED_BRANCH)

    assert result.stdout.strip() == "not-checked-out", (
        "the piped control found the branch, so the generated listing did not outgrow "
        f"the pipe buffer and the tests above prove nothing.\nstdout: {result.stdout!r}"
    )
