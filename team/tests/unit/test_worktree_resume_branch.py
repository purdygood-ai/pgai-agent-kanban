"""
test_worktree_resume_branch.py
==============================
create_task_worktree (team/scripts/lib/worktree.sh) takes a resume mode,
and the wake's worktree stage (wd_setup_task_worktree,
team/scripts/lib/wake_driver_worktree.sh) passes it from the task's
``## Block Record`` ``resume:`` field:

* ``branch`` with the feature branch present -- the worktree is attached to
  that branch unchanged: HEAD is the branch's existing tip, every commit on
  it is kept, and the traced git calls carry no ``branch -D`` and no ``-b``.
* ``fresh``, or no Block Record at all -- a stale feature branch is
  force-deleted and re-created from the source branch, exactly as before
  resume modes existed.
* ``branch`` with no such branch -- the worktree is created fresh from the
  source branch, one stderr line names the missing branch, and nothing is
  deleted.

Every fixture is a throwaway git repository and a sandbox kanban root under
pytest's tmp_path, with the worktree base pinned under it
(PGAI_WORKTREE_BASE). A ``git`` wrapper placed first on PATH records every
git invocation's arguments, so "no branch -D and no -b" is asserted on what
actually ran, not on the source text. No fixture touches the live install,
the operator's dev tree or a provider.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import dataclasses
import pathlib
import shlex
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_record import update_block_record, write_block_record
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB = _TEAM_DIR / "scripts" / "lib"
_TASK_ID = "CODER-20260101-001-example-task"
_FEATURE_BRANCH = f"ai_feature/{_TASK_ID}"
_RC_BRANCH = "ai_rc/v9.9.9"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Sandbox:
    """A sandbox kanban root, a throwaway dev-tree repo and a traced git."""

    root: pathlib.Path

    @property
    def repo(self) -> pathlib.Path:
        return self.root / "repo"

    @property
    def base(self) -> pathlib.Path:
        return self.root / "tmp" / "worktrees"

    @property
    def worktree(self) -> pathlib.Path:
        return self.base / _TASK_ID

    @property
    def task_dir(self) -> pathlib.Path:
        return self.root / "projects" / "demo" / "tasks" / _TASK_ID

    @property
    def status(self) -> pathlib.Path:
        return self.task_dir / "status.md"

    @property
    def bin_dir(self) -> pathlib.Path:
        return self.root / "bin"

    @property
    def trace(self) -> pathlib.Path:
        return self.root / "git-trace.log"


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _make_sandbox(tmp_path: pathlib.Path, *, feature_commits: int = 0) -> Sandbox:
    """Build the sandbox: a repo with main and the RC branch, optionally a
    feature branch *feature_commits* commits ahead of the RC branch, a task
    folder with a plain status.md, and a git wrapper that logs each call."""
    sb = Sandbox(tmp_path / "kanban")
    for d in (sb.repo, sb.base, sb.task_dir / "logs", sb.root / "home", sb.bin_dir):
        d.mkdir(parents=True)
    _git(sb.repo, "init", "-q", "-b", "main")
    _git(sb.repo, "config", "user.email", "fixture@example.invalid")
    _git(sb.repo, "config", "user.name", "fixture")
    (sb.repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(sb.repo, "add", "base.txt")
    _git(sb.repo, "commit", "-q", "-m", "base")
    _git(sb.repo, "branch", _RC_BRANCH)
    if feature_commits:
        _git(sb.repo, "branch", _FEATURE_BRANCH, _RC_BRANCH)
        # Commit on the feature branch through a temporary worktree so the
        # repo's own checkout stays on main, as a parked dev tree is.
        scratch = sb.root / "scratch-wt"
        _git(sb.repo, "worktree", "add", "-q", str(scratch), _FEATURE_BRANCH)
        for n in range(feature_commits):
            (scratch / f"work{n}.txt").write_text(f"{n}\n", encoding="utf-8")
            _git(scratch, "add", f"work{n}.txt")
            _git(scratch, "commit", "-q", "-m", f"work {n}")
        _git(sb.repo, "worktree", "remove", "--force", str(scratch))
    sb.status.write_text(
        f"# Status\n\n## Task\n{_TASK_ID}\n\n## State\nBACKLOG\n\n## Needs Human\nno\n",
        encoding="utf-8",
    )
    (sb.task_dir / "README.md").write_text(
        f"# Task\n\n## Task ID\n{_TASK_ID}\n\n## Role\nCODER\n\n## Workflow Type\nrelease\n\n"
        f"## Source Branch\n{_RC_BRANCH}\n",
        encoding="utf-8",
    )
    # The wrapper drops its own directory from PATH and execs whichever git
    # comes next on the scrubbed PATH, so it never resolves back to itself
    # (nor to any other wrapper the invoking shell put on its own PATH).
    assert shutil.which("git"), "git is required for these fixtures"
    wrapper = sb.bin_dir / "git"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(sb.trace))}\n"
        f"PATH=\"${{PATH#{shlex.quote(str(sb.bin_dir))}:}}\"\n"
        "exec git \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return sb


def _leave_worktree_behind_then_remove_its_directory(sb: Sandbox) -> None:
    """Simulate a previous attempt whose worktree directory is gone: create a
    registered worktree on the feature branch, then delete its directory
    without telling git, so a stale registration still claims the branch."""
    _git(sb.repo, "worktree", "add", "-q", str(sb.worktree), _FEATURE_BRANCH)
    shutil.rmtree(sb.worktree)


def _env(sb: Sandbox) -> dict:
    return build_scrubbed_env(
        home_dir=sb.root / "home",
        extra_path_dirs=[sb.bin_dir],
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sb.root / "tmp"),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sb.root),
            "PGAI_WORKTREE_BASE": str(sb.base),
        },
    )


def _run(sb: Sandbox, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""\
        set -uo pipefail
        cd '{_TEAM_DIR}'
        source '{_LIB}/temp.sh'
        source '{_LIB}/worktree.sh'
        """) + body
    # The trace records only the calls made by the code under test.
    sb.trace.write_text("", encoding="utf-8")
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=120, env=_env(sb))


def _create(sb: Sandbox, *mode: str) -> subprocess.CompletedProcess:
    mode_arg = " ".join(shlex.quote(m) for m in mode)
    return _run(sb, textwrap.dedent(f"""\
        create_task_worktree '{_TASK_ID}' '{_RC_BRANCH}' '{_FEATURE_BRANCH}' '{sb.repo}' {mode_arg}
        printf 'RC=[%s]\\n' "$?"
        """))


def _git_calls(sb: Sandbox) -> list[list[str]]:
    return [shlex.split(line) for line in sb.trace.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_no_branch_delete_or_create(sb: Sandbox) -> None:
    for call in _git_calls(sb):
        assert not ("branch" in call and ("-D" in call or "--delete" in call)), f"a branch was deleted: git {call}"
        assert "-b" not in call and "-B" not in call, f"a branch was (re)created: git {call}"


def _assert_branch_recreated_from_rc(sb: Sandbox) -> None:
    calls = _git_calls(sb)
    expected = ["-C", str(sb.repo), "worktree", "add", "-b", _FEATURE_BRANCH, str(sb.worktree), _RC_BRANCH]
    assert expected in calls, f"expected git {' '.join(expected)}; got {calls}"
    assert _git(sb.worktree, "rev-parse", "HEAD") == _git(sb.repo, "rev-parse", _RC_BRANCH)
    assert _git(sb.worktree, "rev-parse", "--abbrev-ref", "HEAD") == _FEATURE_BRANCH


# ---------------------------------------------------------------------------
# create_task_worktree: branch resume mode keeps the existing branch
# ---------------------------------------------------------------------------


def test_branch_mode_attaches_existing_branch_with_both_commits_after_its_directory_was_removed(
    tmp_path: pathlib.Path,
) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)
    _leave_worktree_behind_then_remove_its_directory(sb)

    result = _create(sb, "branch")

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert result.stdout.splitlines()[0] == str(sb.worktree), "stdout must carry only the worktree path"
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before, "worktree HEAD is not the existing branch tip"
    assert _git(sb.worktree, "rev-parse", "--abbrev-ref", "HEAD") == _FEATURE_BRANCH, "worktree is not on the branch"
    assert _git(sb.repo, "rev-list", "--count", f"{_RC_BRANCH}..{_FEATURE_BRANCH}") == "2", "commits were lost"
    assert (sb.worktree / "work0.txt").exists() and (sb.worktree / "work1.txt").exists()
    assert _git(sb.repo, "rev-parse", _FEATURE_BRANCH) == tip_before, "the branch was moved"
    _assert_no_branch_delete_or_create(sb)
    assert any(c[:2] == ["-C", str(sb.repo)] and c[2:4] == ["worktree", "add"] for c in _git_calls(sb))


def test_branch_mode_reattaches_when_a_registered_worktree_directory_is_still_present(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)
    _git(sb.repo, "worktree", "add", "-q", str(sb.worktree), _FEATURE_BRANCH)

    result = _create(sb, "branch")

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before
    _assert_no_branch_delete_or_create(sb)


@pytest.mark.parametrize("spelling", ["Branch", " BRANCH ", "bogus-mode"])
def test_branch_mode_spellings_and_unknown_values_never_delete_the_branch(tmp_path: pathlib.Path, spelling: str) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)

    result = _create(sb, spelling)

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before
    _assert_no_branch_delete_or_create(sb)
    if spelling == "bogus-mode":
        assert "unrecognised resume mode 'bogus-mode'" in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# create_task_worktree: fresh mode and no mode are unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [(), ("fresh",), ("",)], ids=["no-mode-argument", "fresh", "empty"])
def test_fresh_or_absent_mode_force_deletes_a_stale_branch_and_recreates_it_from_the_source_branch(
    tmp_path: pathlib.Path, mode: tuple[str, ...]
) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)

    result = _create(sb, *mode)

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "already exists; deleting before re-create" in result.stderr, result.stderr
    calls = _git_calls(sb)
    assert ["-C", str(sb.repo), "branch", "-D", _FEATURE_BRANCH] in calls, f"no branch -D of the stale branch: {calls}"
    assert ["-C", str(sb.repo), "worktree", "add", "-b", _FEATURE_BRANCH, str(sb.worktree), _RC_BRANCH] in calls, calls
    _assert_branch_recreated_from_rc(sb)


# ---------------------------------------------------------------------------
# create_task_worktree: branch mode with no such branch
# ---------------------------------------------------------------------------


def test_branch_mode_without_the_branch_creates_it_fresh_and_names_it_once_with_nothing_deleted(
    tmp_path: pathlib.Path,
) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=0)
    branches_before = _git(sb.repo, "branch", "--format=%(refname)")

    result = _create(sb, "branch")

    assert "RC=[0]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    # Count the library's own log lines; git's "Preparing worktree (new
    # branch ...)" progress line on stderr is git's, not a log line.
    naming = [
        line for line in result.stderr.splitlines()
        if line.startswith("worktree.sh:") and _FEATURE_BRANCH in line
    ]
    assert len(naming) == 1, f"expected exactly one log line naming the missing branch; got {naming}"
    assert "does not exist" in naming[0] and _RC_BRANCH in naming[0], naming[0]
    for call in _git_calls(sb):
        assert not ("branch" in call and ("-D" in call or "--delete" in call)), f"something was deleted: git {call}"
    after = set(_git(sb.repo, "branch", "--format=%(refname)").splitlines())
    assert set(branches_before.splitlines()) <= after, "a pre-existing branch disappeared"
    _assert_branch_recreated_from_rc(sb)


# ---------------------------------------------------------------------------
# wd_setup_task_worktree passes the Block Record's resume mode
# ---------------------------------------------------------------------------


def _setup_stage(sb: Sandbox) -> subprocess.CompletedProcess:
    """Drive the real wd_setup_task_worktree with the real create_task_worktree
    and Block Record reader; only collaborators outside the sandbox are stubbed.
    TEAM_ROOT under tmp_path makes the stage skip dev-tree parking."""
    return _run(sb, textwrap.dedent(f"""\
        set -e
        export LOG_FILE='{sb.root}/wake.log'
        log() {{ echo "[test] wake: $*" | tee -a "$LOG_FILE" >&2; }}
        source '{_LIB}/pp_run_ops.sh'
        source '{_LIB}/block_record.sh'
        source '{_LIB}/wake_driver_task_setup.sh'
        source '{_LIB}/wake_driver_worktree.sh'
        task_id='{_TASK_ID}'; task_readme='{sb.task_dir}/README.md'; task_status='{sb.status}'
        TEAM_ROOT='{sb.root}'; KANBAN_ROOT='{sb.root}'; LAST_TASK_FINAL_STATE=""
        SUBAGENT=coder; role=CODER; _CURRENT_PROJECT=demo
        PP_dev_tree_path='{sb.repo}'
        _task_feature_branch=""; _task_worktree_path=""; _task_workflow_type=""
        _task_source_branch=""; _canonical_dev_tree_path='{sb.repo}'; _park_resting_branch=""; role_reminder=""
        pp_prefix_branch() {{ echo "ai_$2"; }}
        _update_readme_feature_branch() {{ :; }}
        wf_load_plugin() {{ return 0; }}
        wf_git_mode() {{ echo branch; }}
        set_state() {{ echo "UNEXPECTED set_state $*" >&2; }}
        set_block() {{ :; }}; normalize_status_file() {{ :; }}; mark_backlog() {{ :; }}
        wd_setup_task_worktree
        printf 'FINAL=[%s]\\nPATH_OUT=[%s]\\n' "$LAST_TASK_FINAL_STATE" "$_task_worktree_path"
        """))


def _record(sb: Sandbox, resume: str) -> None:
    write_block_record(sb.status, "per_task_timeout", "CODER", ["worktree=gone", f"branch={_FEATURE_BRANCH}", "ahead=2"])
    if resume != "fresh":
        update_block_record(sb.status, resume=resume)


def test_wake_stage_resumes_the_branch_when_the_block_record_says_branch(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)
    _leave_worktree_behind_then_remove_its_directory(sb)
    _record(sb, "branch")

    result = _setup_stage(sb)

    assert result.returncode == 0, result.stderr
    assert "FINAL=[]" in result.stdout and f"PATH_OUT=[{sb.worktree}]" in result.stdout, (
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Block Record resume mode: branch" in result.stderr, result.stderr
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before
    assert _git(sb.repo, "rev-list", "--count", f"{_RC_BRANCH}..{_FEATURE_BRANCH}") == "2"
    _assert_no_branch_delete_or_create(sb)


@pytest.mark.parametrize("record", ["none", "fresh"], ids=["no-block-record", "resume-fresh"])
def test_wake_stage_recreates_the_branch_without_a_record_or_with_resume_fresh(
    tmp_path: pathlib.Path, record: str
) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    if record != "none":
        _record(sb, record)

    result = _setup_stage(sb)

    assert result.returncode == 0, result.stderr
    assert "FINAL=[]" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "resume mode" not in result.stderr, "fresh mode must log nothing new"
    assert ["-C", str(sb.repo), "branch", "-D", _FEATURE_BRANCH] in _git_calls(sb)
    _assert_branch_recreated_from_rc(sb)


def test_wake_stage_keeps_the_branch_when_the_block_record_cannot_be_read(tmp_path: pathlib.Path) -> None:
    sb = _make_sandbox(tmp_path, feature_commits=2)
    tip_before = _git(sb.repo, "rev-parse", _FEATURE_BRANCH)
    with sb.status.open("a", encoding="utf-8") as fh:
        fh.write("\n## Block Record\nreason_code: per_task_timeout\nresume: sideways\n")

    result = _setup_stage(sb)

    assert result.returncode == 0, result.stderr
    assert "could not read the Block Record resume mode" in result.stderr, result.stderr
    assert _git(sb.worktree, "rev-parse", "HEAD") == tip_before
    _assert_no_branch_delete_or_create(sb)
