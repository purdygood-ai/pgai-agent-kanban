"""
test_release_finalize_push_narration.py
=======================================
The release's Step 13a (tag) and Step 9 (main) narration lines are chosen
by the same push_to_remote flag that gates Step 14's push, so each lane
reads the truth about who publishes the release:

- push_to_remote = true: both lines say the ref is local for now and that
  this run pushes it in Step 14; neither names finalize-release.sh (the
  operator's manual publish, which a pushing lane never needs). Step 14
  then really does push to the fixture remote.
- push_to_remote = false: both lines say the tag and main stay local and
  give the manual push command; neither names finalize-release.sh, which
  contacts no remote on that lane, as the push step. Step 14 pushes
  nothing, and the printed command, run as printed against a bare fixture
  remote, publishes main and the tag.

cm_release_finalize runs for real under set -euo pipefail through the
harness in test_release_finalize_post_tag_failures.py (throwaway kanban
root, local repository, bare fixture remote for the pushing case, agent
git-guard names scrubbed so the push reaches the fixture remote).
"""

from __future__ import annotations

import re
import shlex
import subprocess

from tests.unit.test_release_finalize_post_tag_failures import (  # noqa: F401
    _VERSION,
    _base_env,
    _diag,
    _finalize,
    _git,
    _run,
)

_LOCAL_ONLY = (
    "NOT pushing to origin (push_to_remote = false): the tag and main stay local "
    f"until pushed by hand with 'git push origin main {_VERSION}'"
)


def _line(stdout: str, prefix: str) -> str:
    """The one stdout line starting with *prefix*; fails naming what was seen."""
    hits = [line for line in stdout.splitlines() if line.startswith(prefix)]
    assert len(hits) == 1, f"expected exactly one {prefix!r} line, got {hits!r}\n--- stdout ---\n{stdout}"
    return hits[0]


def _sha(where, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=where, check=True, capture_output=True, text=True, env=_base_env(),
    ).stdout.strip()


def test_push_lane_narration_names_this_runs_push(tmp_path, _git):
    # Shape "accept": a bare remote named origin with no hook, so Step 14's
    # push succeeds.
    result, project_root, calls, _repo = _finalize(tmp_path, remote="accept")

    assert result.returncode == 0, _diag(result, calls)
    tag_line = _line(result.stdout, "[Step 13a]")
    main_line = _line(result.stdout, "[Step 9]")
    assert tag_line == f"[Step 13a] Tag {_VERSION} created locally — this run pushes it to origin in Step 14.", tag_line
    assert main_line == "[Step 9] main is staged locally — this run pushes it to origin in Step 14.", main_line
    for line in (tag_line, main_line):
        assert "finalize-release.sh" not in line, line
        assert "NOT pushing" not in line, line
    # The narration's promise is kept: Step 14 ran its push, not the skip.
    assert "[push_to_remote=false] skipping origin push" not in result.stdout, _diag(result, calls)
    assert f"Tag {_VERSION} created locally and pushed to origin" in result.stdout, _diag(result, calls)


def test_local_only_lane_narration_says_the_refs_stay_local_and_gives_the_manual_push(tmp_path, _git):
    result, project_root, calls = _finalize(tmp_path)

    assert result.returncode == 0, _diag(result, calls)
    tag_line = _line(result.stdout, "[Step 13a]")
    main_line = _line(result.stdout, "[Step 9]")
    assert tag_line == f"[Step 13a] Tag {_VERSION} created locally — {_LOCAL_ONLY}.", tag_line
    assert main_line == f"[Step 9] main is staged locally — {_LOCAL_ONLY}.", main_line
    # finalize-release.sh contacts no remote on this lane, so it is never named as the push step.
    for line in (tag_line, main_line):
        assert "finalize-release.sh" not in line, line
    assert "this run pushes it" not in result.stdout, _diag(result, calls)
    assert "skipping origin push for finalize-lane: auto-push of main and tags (Step 14)" in result.stdout, (
        _diag(result, calls)
    )


def test_local_only_lane_manual_push_command_publishes_main_and_the_tag(tmp_path, _git):
    result, _project_root, calls = _finalize(tmp_path)
    assert result.returncode == 0, _diag(result, calls)

    quoted = re.search(r"pushed by hand with '([^']+)'", _line(result.stdout, "[Step 9]"))
    assert quoted, _diag(result, calls)
    command = shlex.split(quoted.group(1))

    # The harness repository is tmp_path/dev_repo; give it a bare origin and
    # run the narrated command exactly as printed.
    repo = tmp_path / "dev_repo"
    bare = tmp_path / "manual-remote.git"
    _run("git", "init", "-q", "--bare", str(bare), cwd=tmp_path)
    _run("git", "remote", "add", "origin", str(bare), cwd=repo)
    pushed = subprocess.run(command, cwd=repo, capture_output=True, text=True, env=_base_env())
    assert pushed.returncode == 0, f"{command!r}\n{pushed.stdout}\n{pushed.stderr}"

    assert _sha(bare, "refs/heads/main") == _sha(repo, "main")
    assert _sha(bare, f"refs/tags/{_VERSION}^{{}}") == _sha(repo, f"{_VERSION}^{{}}")
