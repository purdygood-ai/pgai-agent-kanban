"""
test_release_finalize_post_tag_failures.py
==========================================
Once the release tag is cut, a failing bookkeeping step never ends the
release silently. cm_release_finalize (team/scripts/lib/cm/release_finalize.sh)
runs here for real under set -euo pipefail, in a throwaway kanban root and
a local repository with no remote, with two programs replaced by stubs:

- Step 13c's promote_bundled_items.py exits 1: finalize logs a warning and
  carries on -- Step 13d's shipped-state write, Step 16b's Fixed In stamp
  and Step 16c's archive all still run, and the release exits 0.
- Step 12's release-state writer (write_rc_state.py release-state) exits 1:
  finalize raises the project HALT through cm_halt with the
  cm-release.release-state-write-failed key, and its remedy carries the
  exact write_rc_state.py release-state command that closes the RC, then
  the installed unhalt script under the kanban root; cm_halt's resume line
  names that same script. The HALT's closing footer names the tag and never
  says the release was not shipped; a HALT raised before the tag still
  says it was not.
- A remote that refuses every push (a pre-receive hook on a bare fixture
  repository): Step 10's RC-branch delete and Step 14's main and tag push
  reach the remote and its hook rejects them, and the release still exits
  0 with no HALT, the cm-release.trigger-6.push-lag notice and one Push Lag
  Event in release-state.md naming all three refs.
- The release tag already on the remote and not local: Step 13 writes one
  operator inbox entry naming the tag and exits 6 with no HALT, no local
  tag and no release-state write.

The stage is sourced through a scratch scripts tree whose lib/ entries link
back to this tree's own files, so the Step 13c lookup (anchored to the stage
file's own location) finds the stub; every library is this tree's own. Steps 16b and
16c are replaced by marker functions after sourcing -- they prove the step
was reached, not what it does (their own suites cover that).

Every git command and the release run itself start from the inherited
environment with the agent-scoped names removed (_AGENT_GIT_NAMES and
scrub_agent_scoped_env), so inside an agent wake the release's pushes reach
the fixture remote instead of being refused by the git boundary guard, and
the guard records nothing in the running task's folder.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env
from tests.unit.shell_harness import run_bash

_TEAM = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "finalize-lane"
_VERSION = "v1.4.0"

# Names that route the fixture's git through the agent guard (which refuses
# the release's pushes before they reach the fixture remote) or point a
# script at the running task.
_AGENT_GIT_NAMES = (
    "PGAI_AGENT_ROLE",
    "PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE",
    "PGAI_GIT_LOCAL_ONLY",
    "PGAI_TASK_ID",
)

_REAL_LIBS = (
    "scripts/lib/error_contract.sh",
    "scripts/lib/pp_run_ops.sh",
    "scripts/lib/project_paths.sh",
    "scripts/lib/cm/release_preflight.sh",
    "scripts/lib/cm/release_ship_posture.sh",
    "scripts/lib/cm/release_campaign_spend.sh",
    "scripts/lib/cm/release_halt_after_spend.sh",
    "scripts/lib/cm/release_origin_summary.sh",
)

_WRITER_STUB = """import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("write_rc_state " + " ".join(sys.argv[1:]) + "\\n")
if sys.argv[1] == "release-state":
    sys.exit(int(os.environ.get("STUB_RELEASE_STATE_RC", "0")))
sys.exit(0)
"""

_PROMOTE_STUB = """import os, sys
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as log:
    log.write("promote " + " ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("STUB_PROMOTE_RC", "0")))
"""


@pytest.fixture
def _git() -> None:
    require_binary("git", label="release-finalize-post-tag-failures")


def _q(value) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


def _base_env() -> dict:
    """The inherited environment without the agent-scoped names."""
    env = dict(os.environ)
    for name in _AGENT_GIT_NAMES:
        env.pop(name, None)
    return scrub_agent_scoped_env(env)


def _run(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True, env=_base_env())


def _build(tmp_path: pathlib.Path):
    root = tmp_path / "kanban"
    project_root = root / "projects" / _PROJECT
    for sub in ("bugs", "tasks", "requirements"):
        (project_root / sub).mkdir(parents=True)
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (project_root / "project.cfg").write_text("[project]\npush_to_remote = false\n", encoding="utf-8")
    (project_root / "release-state.md").write_text(
        f"# Release State\n\n## Active RC\n{_VERSION}\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (project_root / "requirements" / f"{_VERSION}-bundle.md").write_text(
        f"# {_VERSION} bundle\n\n## Status\nrunning\n",
        encoding="utf-8",
    )
    writer = root / "pgai_agent_kanban" / "cm" / "write_rc_state.py"
    writer.parent.mkdir(parents=True)
    writer.write_text(_WRITER_STUB, encoding="utf-8")

    # A scratch scripts tree whose lib/ entries link to this tree's own, so
    # the stage and everything it sources by relative path resolve, while
    # Step 13c's lookup lands on the stub in the scratch scripts/cm/.
    real_lib = _TEAM / "scripts" / "lib"
    stage_dir = tmp_path / "stage" / "scripts" / "lib" / "cm"
    stage_dir.mkdir(parents=True)
    for entry in real_lib.iterdir():
        if entry.name != "cm":
            (stage_dir.parent / entry.name).symlink_to(entry)
    for entry in (real_lib / "cm").iterdir():
        (stage_dir / entry.name).symlink_to(entry)
    promote = tmp_path / "stage" / "scripts" / "cm" / "promote_bundled_items.py"
    promote.parent.mkdir(parents=True)
    promote.write_text(_PROMOTE_STUB, encoding="utf-8")

    repo = tmp_path / "dev_repo"
    repo.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Fixture", cwd=repo)
    _run("git", "config", "user.email", "fixture@example.invalid", cwd=repo)
    (repo / "VERSION").write_text(f"{_VERSION}\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=repo)
    _run("git", "commit", "-q", "-m", "release", cwd=repo)
    _run("git", "branch", f"rc/{_VERSION}", cwd=repo)
    return root, project_root, stage_dir, repo


def _add_remote(tmp_path: pathlib.Path, repo: pathlib.Path, shape: str) -> pathlib.Path:
    """A bare fixture remote named origin holding main and the RC branch.

    *shape* ``reject``: a pre-receive hook refuses every later push, and
    local main carries the release's own commit the remote lacks, so the
    main push has something to send and the hook rejects it.
    ``tag``: the remote already holds the release tag, the local repository does not.
    """
    bare = tmp_path / "remote.git"
    _run("git", "init", "-q", "--bare", str(bare), cwd=tmp_path)
    _run("git", "remote", "add", "origin", str(bare), cwd=repo)
    _run("git", "push", "-q", "origin", "main", f"rc/{_VERSION}", cwd=repo)
    _run("git", "fetch", "-q", "origin", cwd=repo)
    if shape == "reject":
        (repo / "CHANGELOG.md").write_text(f"# {_VERSION}\n", encoding="utf-8")
        _run("git", "add", "-A", cwd=repo)
        _run("git", "commit", "-q", "-m", f"release {_VERSION}", cwd=repo)
        hook = bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'fixture remote: rejected' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
    elif shape == "tag":
        _run("git", "push", "-q", "origin", f"main:refs/tags/{_VERSION}", cwd=repo)
    return bare


def _finalize(
    tmp_path: pathlib.Path,
    *,
    release_state_rc: int = 0,
    promote_rc: int = 0,
    pre_tag_divergence: bool = False,
    remote: str = "",
):
    root, project_root, stage_dir, repo = _build(tmp_path)
    if remote:
        _add_remote(tmp_path, repo, remote)
    if pre_tag_divergence:
        # The release tag already exists on the parent of main's HEAD.
        (repo / "later.txt").write_text("later\n", encoding="utf-8")
        _run("git", "add", "-A", cwd=repo)
        _run("git", "commit", "-q", "-m", "later", cwd=repo)
        _run("git", "tag", _VERSION, "HEAD~1", cwd=repo)
    log = tmp_path / "stub.log"
    script = "\n".join(
        [
            "set -euo pipefail",
            "unset PGAI_PROJECT_ROOT PGAI_TASKS_DIR PGAI_PROJECT_NAME",
            *(f"source {lib}" for lib in _REAL_LIBS),
            f"source {_q(stage_dir / 'release_finalize.sh')}",
            'git_step() { shift; "$@"; }',
            "_noop_hook_step() { :; }",
            "_cm_release_stamp_fixed_in() { echo STEP-16B-RAN; _CM_FIXED_IN_OUTCOME='Fixed In stamp stubbed'; }",
            "_cm_release_archive_finished_items() { echo STEP-16C-RAN; }",
            f"PROJECT_NAME={_q(_PROJECT)} ACTIVE_RC={_q(_VERSION)} REPO_ROOT={_q(repo)}",
            f"MAIN_BRANCH=main RC_BRANCH=rc/{_VERSION} _CM_PROJECT_ROOT={_q(project_root)}",
            'RELEASE_STATE="$_CM_PROJECT_ROOT/release-state.md" PROJECT_HALT_FILE="$_CM_PROJECT_ROOT/HALT"',
            f"PROJECT_HOOKS_DIR=\"$_CM_PROJECT_ROOT/hooks\" _CM_PUSH_TO_REMOTE={'true' if remote else 'false'}"
            " _CM_AUTORESOLVE_LOG=''",
            f"_CLEAN_RELEASE_VERSION={_q(_VERSION)}",
            "cm_release_finalize _noop_hook_step _noop_hook_step",
        ]
    )
    (tmp_path / "scratch").mkdir(exist_ok=True)
    env = {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        "STUB_LOG": str(log),
        "STUB_RELEASE_STATE_RC": str(release_state_rc),
        "STUB_PROMOTE_RC": str(promote_rc),
    }
    result = run_bash(tmp_path, script, base_env=_base_env(), extra_env=env, timeout=120)
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    if remote:
        return result, project_root, calls, repo
    return result, project_root, calls


def _diag(result, calls: str = "") -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n--- stub calls ---\n{calls}"


def test_failed_bundle_promotion_warns_and_the_release_finishes(tmp_path, _git):
    result, project_root, calls = _finalize(tmp_path, promote_rc=1)

    assert result.returncode == 0, _diag(result, calls)
    assert "promote " in calls, _diag(result, calls)
    assert "WARNING: promote_bundled_items.py exited 1" in result.stderr, _diag(result, calls)
    assert "The release continues." in result.stderr
    assert "write_rc_state ship " in calls, "Step 13d still records the shipped state\n" + _diag(result, calls)
    assert "STEP-16B-RAN" in result.stdout, _diag(result, calls)
    assert "STEP-16C-RAN" in result.stdout, _diag(result, calls)
    assert "Local release preparation complete." in result.stdout
    assert not (project_root / "HALT").exists()


def test_failed_release_state_write_halts_with_the_write_as_remedy(tmp_path, _git):
    result, project_root, calls = _finalize(tmp_path, release_state_rc=1)

    assert result.returncode != 0, _diag(result, calls)
    halt = project_root / "HALT"
    assert halt.is_file(), "the failure stops the project through cm_halt\n" + _diag(result, calls)
    halt_text = halt.read_text(encoding="utf-8")
    assert "cm-release.release-state-write-failed" in halt_text, halt_text
    expected_remedy = (
        f"write_rc_state.py release-state {project_root / 'release-state.md'} --active-rc none "
        f"--rc-opened-at none --rc-opened-by-task none --last-released {_VERSION}"
    )
    assert expected_remedy in halt_text, halt_text
    # The remedy then lifts the HALT through the installed script under the
    # kanban root (install.sh puts it in <root>/scripts/).
    unhalt = f"{project_root.parent.parent / 'scripts' / 'unhalt.sh'} --project {_PROJECT}"
    assert f"--last-released {_VERSION} && {unhalt}" in halt_text, halt_text
    assert "cm-release.release-state-write-failed" in result.stderr, _diag(result, calls)
    assert expected_remedy in result.stderr, _diag(result, calls)
    assert f"Resolve the issue above, then run: {unhalt} (removes" in result.stderr, _diag(result, calls)
    assert "promote " not in calls, "nothing after the failed write runs\n" + _diag(result, calls)
    assert "STEP-16B-RAN" not in result.stdout
    assert "NOT been shipped" not in result.stderr, _diag(result, calls)
    assert f"The release IS tagged ({_VERSION}); do not cancel it" in result.stderr, _diag(result, calls)


def test_halt_before_the_tag_still_says_the_release_did_not_ship(tmp_path, _git):
    # A local tag on a commit other than main's HEAD trips trigger 5b in
    # Step 13, before the tag is cut and verification completes.
    result, project_root, calls = _finalize(tmp_path, pre_tag_divergence=True)

    assert result.returncode != 0, _diag(result, calls)
    assert "cm-release.trigger-5b.tag-points-at-wrong-commit" in result.stderr, _diag(result, calls)
    assert "The release has NOT been shipped." in result.stderr, _diag(result, calls)
    assert "IS tagged" not in result.stderr, _diag(result, calls)


def test_successful_steps_record_release_state_and_promotion(tmp_path, _git):
    result, project_root, calls = _finalize(tmp_path)

    assert result.returncode == 0, _diag(result, calls)
    assert f"write_rc_state release-state {project_root / 'release-state.md'}" in calls
    assert "promote " in calls
    assert "WARNING: promote_bundled_items.py exited" not in result.stderr
    assert not (project_root / "HALT").exists()


def test_a_refused_push_after_the_tag_is_push_lag_not_a_halt(tmp_path, _git):
    result, project_root, calls, repo = _finalize(tmp_path, remote="reject")

    assert result.returncode == 0, _diag(result, calls)
    assert not (project_root / "HALT").exists(), _diag(result, calls)
    assert "Local release preparation complete." in result.stdout, _diag(result, calls)
    assert "cm-release.trigger-6.push-lag" in result.stderr, _diag(result, calls)
    # The refusal is the fixture remote's own hook, not the agent git guard.
    assert "fixture remote: rejected" in result.stderr, _diag(result, calls)
    state = (project_root / "release-state.md").read_text(encoding="utf-8")
    assert state.count("## Push Lag Event") == 1, state
    for lagging in (
        "main (push failed)",
        f"refs/tags/{_VERSION} (push failed)",
        f"rc/{_VERSION} (still on origin; deletion failed)",
    ):
        assert f"  - {lagging}" in state, state
    assert f"push-watchdog.sh --project {_PROJECT}" in state, state
    assert f"rc/{_VERSION} still on origin (its deletion was refused" in result.stdout, _diag(result, calls)
    assert "write_rc_state release-state " in calls, "Step 12 still closes the RC\n" + _diag(result, calls)
    assert "STEP-16C-RAN" in result.stdout


def test_a_release_tag_already_on_the_remote_is_an_inbox_entry_not_a_halt(tmp_path, _git):
    result, project_root, calls, repo = _finalize(tmp_path, remote="tag")

    assert result.returncode == 6, _diag(result, calls)
    assert not (project_root / "HALT").exists(), _diag(result, calls)
    assert "cm-release.trigger-7.tag-exists-on-remote" in result.stderr, _diag(result, calls)
    inbox = (project_root / "operator-inbox.md").read_text(encoding="utf-8")
    assert inbox.count("### INBOX-") == 1, inbox
    assert f"Release tag {_VERSION} is already on origin" in inbox, inbox
    tags = subprocess.run(["git", "tag", "--list"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert tags.strip() == "", "no local tag is cut"
    assert "write_rc_state" not in calls, "release-state is never written\n" + _diag(result, calls)
    assert "HALT Event" not in (project_root / "release-state.md").read_text(encoding="utf-8")
