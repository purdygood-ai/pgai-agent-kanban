"""
test_release_archive_step.py
============================
The release finalize stage's last step (Step 16c) archives the released
project's finished items, and only once the RC is closed. It is quality
class: whatever the archive does, the release finishes with its normal exit
status, release-state.md and the tag as they would be without it.

Two layers, kept together because each catches what the other cannot:

  - **The stage** (``TestArchiveStepInFinalize``): the real
    ``cm_release_finalize`` from ``team/scripts/lib/cm/release_finalize.sh``
    runs over a throwaway git repository and a sandbox kanban root holding
    two projects (``tests/fixtures/archive_sandbox.py``). Only collaborators
    from other release stage files are stubbed, as in
    ``tests/unit/test_release_summary_origin_actions.py``. ``pp_run_ops`` is
    wrapped by a spy that records, at the moment the archive is called, whether
    the RC branch still exists and what release-state.md says, then calls
    the real ``pp_run_ops``. Pinned here: the order (the archive sees the RC
    branch gone, Active RC ``none`` and the new Last Released), that only the
    released project is archived (a second project, equally eligible, is
    byte-for-byte unchanged), that a held repo lock and a failing archive are
    each logged while the release exits 0 with release-state.md and the tag
    untouched by the step, and that the step archives nothing when the RC
    is not closed.
  - **The wake chain's own lock** (``TestArchiveStepUnderWakeChainLock``):
    the production topology. CM ships from inside its own wake chain, which
    holds ``locks/repo-wake-<project>.lock`` for its whole run. A parent
    bash takes that lock exactly as ``wake_driver.sh`` does
    (``pgai_lock_acquire``, recording its own pid) and runs Step 16c in a
    child: the archive works under the chain's hold. The same lock held by
    an unrelated process (a sibling, not an ancestor of the release) still
    skips the archive dual-channel and moves nothing.
  - **The entry points** (``TestArchiveThroughReleaseEntryPoints``):
    ``cm/open-rc.sh`` then ``cm/release.sh`` archive the released project's
    finished items and leave a sibling project alone; ``cm/open-rc.sh`` then
    ``cm/cancel-rc.sh`` archive nothing.

Every kanban root here is a sandbox under pytest's ``tmp_path`` and is handed
to the code under test explicitly (``KANBAN_ROOT`` /
``PGAI_AGENT_KANBAN_ROOT_PATH``, and the archive's own ``--kanban-root``).
Nothing here points the archive at the live install.
"""

from __future__ import annotations

import pathlib
import subprocess
from contextlib import contextmanager
from typing import Iterator

import pytest
from pgai_agent_kanban.archive import read_rows
from pgai_agent_kanban.ops.locks import read_provenance, repo_lock_path
from tests.fixtures.archive_sandbox import ArchiveSandbox
from tests.fixtures.binary_guard import require_binary
from tests.integration.test_cm_release_synthetic import (
    _add_sibling_project,
    _build_kanban_root,
    _commit_on_branch,
    _git_branch_exists_local,
    _init_git_repo_with_remote,
    _run_cm_script,
)
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_CM_DIR = _SCRIPTS_DIR / "lib" / "cm"
_RELEASE_FINALIZE_SH = _LIB_CM_DIR / "release_finalize.sh"
_RELEASE_ORIGIN_SUMMARY_SH = _LIB_CM_DIR / "release_origin_summary.sh"
_RELEASE_SHIP_POSTURE_SH = _LIB_CM_DIR / "release_ship_posture.sh"
_LOCK_PROVENANCE_SH = _SCRIPTS_DIR / "lib" / "lock_provenance.sh"
_OPEN_RC_SCRIPT = _SCRIPTS_DIR / "cm" / "open-rc.sh"
_RELEASE_SCRIPT = _SCRIPTS_DIR / "cm" / "release.sh"
_CANCEL_RC_SCRIPT = _SCRIPTS_DIR / "cm" / "cancel-rc.sh"

_VERSION = "v1.2.0"
_PREVIOUS = "v1.1.0"
_RC_BRANCH = f"rc/{_VERSION}"
_MAIN = "main"
_PROJECT = "demo"
_SIBLING = "sibling"
_ARCHIVE_COUNT = 2

# Five done bugs per project with archive_count 2: the three oldest move.
_BUG_NUMBERS = range(1, 6)
_EXPECTED_BUG_IDS = ["BUG-0001", "BUG-0002", "BUG-0003"]


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test here builds and inspects a real git repository."""
    require_binary("git", label="release-archive-step")


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run git with -C *repo* and return stripped stdout; raise on failure."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _section(text: str, heading: str) -> str:
    """The first non-blank line under ``## <heading>`` in *text*, or ''."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            for value in lines[index + 1:]:
                if value.strip():
                    return value.strip()
    return ""


def _ledger_ids(root: pathlib.Path, project: str) -> list[str]:
    """IDs of the ledger rows the archive recorded for *project*, in order."""
    return [row.item_id for row in read_rows(root / "archive" / project / "ARCHIVE-LEDGER.tsv")]


# ---------------------------------------------------------------------------
# The stage: cm_release_finalize over a two-project sandbox
# ---------------------------------------------------------------------------


class _Fixture:
    """A post-squash repo and a two-project sandbox root, ready to finalize.

    The released project (``demo``) has its RC open at v1.2.0. Its tasks
    are four DONE tasks; the oldest belongs to the RC being released, so it
    is only finished -- and only archived -- once release-state.md reads
    Active RC ``none``. The sibling project has the same eligible items and
    must not change.
    """

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.tmp_path = tmp_path
        self.repo = self._build_repo(tmp_path / "repo")
        self.demo = ArchiveSandbox(tmp_path, _PROJECT, active_rc=_VERSION, last_released=_PREVIOUS)
        self.sibling = ArchiveSandbox(tmp_path, _SIBLING, active_rc="none", last_released=_PREVIOUS)
        self.root = self.demo.root
        self.demo.set_archive_count(_ARCHIVE_COUNT)
        for sandbox in (self.demo, self.sibling):
            for number in _BUG_NUMBERS:
                sandbox.add_bug(number, "done")
        self.rc_task = ArchiveSandbox.task_id("CODER", 1)
        self.old_task = ArchiveSandbox.task_id("CODER", 2)
        self.demo.add_task(self.rc_task, "DONE", release_version=_VERSION)
        self.demo.add_task(self.old_task, "DONE", release_version=_PREVIOUS)
        for sequence in (3, 4):
            self.demo.add_task(ArchiveSandbox.task_id("CODER", sequence), "DONE", release_version=_VERSION)
        self.sibling.add_task(ArchiveSandbox.task_id("CODER", 1), "DONE", release_version=_PREVIOUS)
        self.sibling.add_task(ArchiveSandbox.task_id("CODER", 2), "DONE", release_version=_PREVIOUS)
        self.sibling.add_task(ArchiveSandbox.task_id("CODER", 3), "DONE", release_version=_PREVIOUS)
        # The finalizer's own helpers resolve through the kanban root.
        (self.root / "scripts").symlink_to(_SCRIPTS_DIR, target_is_directory=True)
        (self.root / "pgai_agent_kanban").symlink_to(
            _TEAM_DIR / "pgai_agent_kanban", target_is_directory=True
        )
        self.release_state = self.demo.project_root / "release-state.md"
        self.probe = tmp_path / "archive-call-probe.txt"
        self.probe_state = tmp_path / "archive-call-release-state.md"

    @staticmethod
    def _build_repo(repo: pathlib.Path) -> pathlib.Path:
        """main one release commit (VERSION written) ahead of the RC branch."""
        subprocess.run(
            ["git", "init", f"--initial-branch={_MAIN}", str(repo)], capture_output=True, check=True,
        )
        _git(repo, "config", "--local", "user.email", "test@pgai.local")
        _git(repo, "config", "--local", "user.name", "PGAI Test")
        (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-m", "Initial commit")
        _git(repo, "branch", _RC_BRANCH)
        (repo / "VERSION").write_text(_VERSION.lstrip("v") + "\n", encoding="utf-8")
        _git(repo, "add", "VERSION")
        _git(repo, "commit", "-m", "Release commit")
        return repo

    def _preamble(self) -> str:
        """Source the stage and stub the collaborators from other stage files."""
        project_root = self.demo.project_root
        return f"""
set -euo pipefail
unset PYTHONPATH PGAI_TASK_ID
export KANBAN_ROOT={self.root}
source {self.root / 'scripts' / 'lib' / 'pp_run_ops.sh'}
source {_RELEASE_ORIGIN_SUMMARY_SH}
source {_RELEASE_SHIP_POSTURE_SH}
source {_RELEASE_FINALIZE_SH}

cm_halt() {{ echo "HALT: $1" >&2; exit 1; }}
git_step() {{ shift; "$@" || {{ echo "git_step failed: $*" >&2; exit 1; }}; }}
_fixture_pre_tag_hook() {{ :; }}
_fixture_post_tag_hook() {{ :; }}
cm_release_spend_campaign_file() {{ :; }}
cm_release_spend_halt_after_sentinel() {{ :; }}
pp_prefix_tag() {{ echo "$2"; }}
pp_strip_prefix_from_tag() {{ echo "${{2#v}}"; }}
pp_project_root() {{ echo {project_root}; }}
pp_git_remote_name() {{ echo origin; }}

# Spy: when the archive is called, record whether the RC branch still
# exists and what release-state.md says, then run the real pp_run_ops.
eval "_real_pp_run_ops() $(declare -f pp_run_ops | tail -n +2)"
pp_run_ops() {{
  if [[ "${{1:-}}" == "pgai_agent_kanban.archive" ]]; then
    {{
      if git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/heads/$RC_BRANCH" >/dev/null; then
        echo "rc_branch=present"
      else
        echo "rc_branch=absent"
      fi
      echo "args=$*"
    }} > {self.probe}
    cp "$RELEASE_STATE" {self.probe_state}
  fi
  _real_pp_run_ops "$@"
}}

PROJECT_NAME={_PROJECT}
REPO_ROOT={self.repo}
MAIN_BRANCH={_MAIN}
RC_BRANCH={_RC_BRANCH}
ACTIVE_RC={_VERSION}
RELEASE_STATE={self.release_state}
PROJECT_HOOKS_DIR={self.root / 'hooks'}
_CM_PROJECT_ROOT={project_root}
_CM_PUSH_TO_REMOTE=false
_CM_AUTORESOLVE_LOG=""
_CLEAN_RELEASE_VERSION={_VERSION.lstrip('v')}
"""

    def finalize(self) -> subprocess.CompletedProcess:
        """Run the whole finalize stage."""
        script = self._preamble() + "\ncm_release_finalize _fixture_pre_tag_hook _fixture_post_tag_hook\n"
        return run_bash(self.tmp_path, script, cwd=self.tmp_path, timeout=180)

    def archive_step_only(self) -> subprocess.CompletedProcess:
        """Run Step 16c's helper alone, against the fixture as it stands."""
        script = self._preamble() + (
            "\n_cm_release_archive_finished_items\n"
            'echo "outcome=${_CM_ARCHIVE_OUTCOME}"\n'
        )
        return run_bash(self.tmp_path, script, cwd=self.tmp_path, timeout=120)

    def tag_sha(self) -> str:
        return _git(self.repo, "rev-parse", f"{_VERSION}^{{}}")

    def main_sha(self) -> str:
        return _git(self.repo, "rev-parse", _MAIN)


@pytest.fixture
def fixture(tmp_path: pathlib.Path) -> _Fixture:
    return _Fixture(tmp_path)


@contextmanager
def _lock_held_by_unrelated_process(lock_file: pathlib.Path) -> Iterator[int]:
    """Hold *lock_file* from a separate bash process for the duration of the block.

    The holder takes the lock as a wake chain does (``pgai_lock_acquire``,
    holder ``wake-chain``, its own pid) and keeps it until its stdin
    closes. It is started by this test process, as the release under test
    is, so the two are siblings: the holder is never an ancestor of the
    archive. Yields the holder's pid.
    """
    script = f"""
set -euo pipefail
source {_LOCK_PROVENANCE_SH}
holder_fd=""
pgai_lock_acquire {lock_file} wake-chain "$$" "wake-chain:{_PROJECT}" none holder_fd
echo "held $$"
read -r _ignored || true
pgai_lock_release {lock_file} "$holder_fd"
"""
    holder = subprocess.Popen(
        ["bash", "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=lock_file.parent.parent,
    )
    try:
        line = holder.stdout.readline() if holder.stdout else ""
        if not line.startswith("held "):
            holder.kill()
            _, err = holder.communicate(timeout=30)
            raise AssertionError(f"the unrelated holder did not take {lock_file}: {line!r} {err}")
        yield int(line.split()[1])
    finally:
        if holder.poll() is None:
            holder.communicate(input="\n", timeout=30)


@pytest.fixture
def held_repo_lock(fixture: _Fixture) -> Iterator[None]:
    """Hold the released project's repo lock from an unrelated process for the test."""
    with _lock_held_by_unrelated_process(repo_lock_path(fixture.root, _PROJECT)):
        yield


def _assert_release_finished_normally(fixture: _Fixture, result: subprocess.CompletedProcess) -> None:
    """Exit 0, the closing lines printed, the tag cut, the RC closed in release-state.md."""
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "The release is complete locally." in result.stdout, output
    assert fixture.tag_sha() == fixture.main_sha(), output
    state = fixture.release_state.read_text(encoding="utf-8")
    assert _section(state, "Active RC") == "none", state
    assert _section(state, "Last Released") == _VERSION, state


class TestArchiveStepInFinalize:
    """Step 16c inside the real finalize stage."""

    def test_archive_runs_after_rc_close_for_released_project_only(self, fixture: _Fixture) -> None:
        sibling_before = {
            path: data for path, data in fixture.sibling.snapshot().items()
            if path.startswith(f"projects/{_SIBLING}/")
        }

        result = fixture.finalize()
        output = result.stdout + result.stderr
        _assert_release_finished_normally(fixture, result)

        # Order: when the archive was called the RC branch was gone and
        # release-state.md already recorded the release.
        probe = fixture.probe.read_text(encoding="utf-8")
        assert "rc_branch=absent" in probe, probe
        assert f"--project {_PROJECT}" in probe and f"--kanban-root {fixture.root}" in probe, probe
        state_at_call = fixture.probe_state.read_text(encoding="utf-8")
        assert _section(state_at_call, "Active RC") == "none", state_at_call
        assert _section(state_at_call, "Last Released") == _VERSION, state_at_call
        lines = output.splitlines()
        step16b = max(i for i, line in enumerate(lines) if line.startswith("[Step 16b]"))
        step16c = min(i for i, line in enumerate(lines) if line.startswith("[Step 16c]"))
        assert step16b < step16c, output

        # The released project: the three oldest done bugs and both older
        # tasks moved -- including the task of the RC just released, which
        # is only finished once the RC is closed.
        archive = fixture.root / "archive" / _PROJECT
        ledger = _ledger_ids(fixture.root, _PROJECT)
        assert sorted(i for i in ledger if i.startswith("BUG-")) == _EXPECTED_BUG_IDS, ledger
        assert {fixture.rc_task, fixture.old_task} <= set(ledger), ledger
        for bug_id in _EXPECTED_BUG_IDS:
            assert list((archive / "bugs").glob(f"{bug_id}-*.md")), bug_id
            assert not list((fixture.demo.project_root / "bugs").glob(f"{bug_id}-*.md")), bug_id
        assert (archive / "tasks" / fixture.rc_task / "status.md").is_file()
        assert "[Step 16c] Archive finished for demo." in result.stdout, output

        # No other project is touched.
        assert not (fixture.root / "archive" / _SIBLING).exists()
        sibling_after = {
            path: data for path, data in fixture.sibling.snapshot().items()
            if path.startswith(f"projects/{_SIBLING}/")
        }
        assert sibling_after == sibling_before

    @pytest.mark.usefixtures("held_repo_lock")
    def test_held_repo_lock_is_logged_and_release_finishes_normally(self, fixture: _Fixture) -> None:
        bugs_before = sorted(p.name for p in (fixture.demo.project_root / "bugs").iterdir())

        result = fixture.finalize()
        output = result.stdout + result.stderr
        _assert_release_finished_normally(fixture, result)

        assert "archive.repo_lock_busy" in output, output
        assert "[Step 16c] WARNING: archive skipped for demo: the project's repo lock is held by a process outside" in result.stderr, output
        # The step left release-state.md as it found it, and moved nothing.
        assert fixture.release_state.read_bytes() == fixture.probe_state.read_bytes()
        assert sorted(p.name for p in (fixture.demo.project_root / "bugs").iterdir()) == bugs_before
        assert _ledger_ids(fixture.root, _PROJECT) == []

    def test_failing_archive_is_logged_and_release_finishes_normally(self, fixture: _Fixture) -> None:
        # A regular file where the archive tree goes: the first move fails.
        (fixture.root / "archive").write_text("not a directory\n", encoding="utf-8")
        bugs_before = sorted(p.name for p in (fixture.demo.project_root / "bugs").iterdir())

        result = fixture.finalize()
        output = result.stdout + result.stderr
        _assert_release_finished_normally(fixture, result)

        assert "archive.run_failed" in output, output
        assert "[Step 16c] WARNING: archive exited 2 for demo" in result.stderr, output
        assert fixture.release_state.read_bytes() == fixture.probe_state.read_bytes()
        assert sorted(p.name for p in (fixture.demo.project_root / "bugs").iterdir()) == bugs_before

    def test_step_archives_nothing_while_rc_branch_exists(self, fixture: _Fixture) -> None:
        # Release-state already reads closed, but the RC branch is still here.
        fixture.demo.set_release_state("none", _VERSION)
        before = fixture.demo.snapshot()

        result = fixture.archive_step_only()

        assert result.returncode == 0, result.stdout + result.stderr
        assert "outcome=skipped-rc-open" in result.stdout, result.stdout + result.stderr
        assert f"local branch {_RC_BRANCH} present" in result.stderr, result.stderr
        assert not fixture.probe.exists(), "the archive must not be called while the RC is open"
        assert fixture.demo.snapshot() == before

    def test_step_archives_nothing_until_release_state_records_the_release(self, fixture: _Fixture) -> None:
        # The RC branch is gone, but release-state.md still has the RC active.
        _git(fixture.repo, "branch", "-D", _RC_BRANCH)
        before = fixture.demo.snapshot()

        result = fixture.archive_step_only()

        assert result.returncode == 0, result.stdout + result.stderr
        assert "outcome=skipped-rc-open" in result.stdout, result.stdout + result.stderr
        assert f"Active RC '{_VERSION}'" in result.stderr, result.stderr
        assert not fixture.probe.exists(), "the archive must not be called while the RC is open"
        assert fixture.demo.snapshot() == before


# ---------------------------------------------------------------------------
# The wake chain's own lock: the production topology
# ---------------------------------------------------------------------------


def _close_rc(fixture: _Fixture) -> None:
    """Leave the fixture as the finalize stage leaves it just before Step 16c."""
    _git(fixture.repo, "branch", "-D", _RC_BRANCH)
    fixture.demo.set_release_state("none", _VERSION)


def _archive_step_in_child_of_chain(fixture: _Fixture) -> subprocess.CompletedProcess:
    """Take the repo lock in a parent bash as the wake chain does, then run Step 16c in a child.

    The parent records its own pid in the lock, keeps the lock on an open
    fd for the child's whole run (the child inherits it, as a dispatched
    agent does), and afterwards reports whether its record is still the
    one on disk.
    """
    lock_file = repo_lock_path(fixture.root, _PROJECT)
    child = fixture.tmp_path / "release-step-16c.sh"
    child.write_text(
        fixture._preamble() + '\n_cm_release_archive_finished_items\necho "outcome=${_CM_ARCHIVE_OUTCOME}"\n',
        encoding="utf-8",
    )
    parent = f"""
set -euo pipefail
source {_LOCK_PROVENANCE_SH}
chain_fd=""
pgai_lock_acquire {lock_file} wake-chain "$$" "wake-chain:{_PROJECT}" none chain_fd
echo "chain_pid=$$"
step_rc=0
bash {child} || step_rc=$?
echo "step_rc=$step_rc"
if pgai_lock_still_held {lock_file} "$$"; then echo "chain_lock=still-held"; else echo "chain_lock=lost"; fi
pgai_lock_release {lock_file} "$chain_fd"
"""
    return run_bash(fixture.tmp_path, parent, cwd=fixture.tmp_path, timeout=120)


class TestArchiveStepUnderWakeChainLock:
    """Step 16c run inside a wake chain that holds the project's repo lock."""

    def test_archive_runs_under_the_release_chains_own_repo_lock(self, fixture: _Fixture) -> None:
        _close_rc(fixture)
        sibling_before = {
            path: data for path, data in fixture.sibling.snapshot().items()
            if path.startswith(f"projects/{_SIBLING}/")
        }

        result = _archive_step_in_child_of_chain(fixture)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "step_rc=0" in result.stdout, output
        assert "outcome=archived" in result.stdout, output
        assert "archive.repo_lock_busy" not in output, output
        assert "is held by the wake chain this run belongs to" in result.stdout, output
        assert "[Step 16c] Archive finished for demo." in result.stdout, output

        # Finished items beyond archive_count moved under the sandbox's
        # archive/demo/, each with its ledger row.
        archive = fixture.root / "archive" / _PROJECT
        ledger = _ledger_ids(fixture.root, _PROJECT)
        assert sorted(i for i in ledger if i.startswith("BUG-")) == _EXPECTED_BUG_IDS, output
        assert {fixture.rc_task, fixture.old_task} <= set(ledger), output
        for bug_id in _EXPECTED_BUG_IDS:
            assert list((archive / "bugs").glob(f"{bug_id}-*.md")), bug_id
            assert not list((fixture.demo.project_root / "bugs").glob(f"{bug_id}-*.md")), bug_id

        # Ledger rows and moves for the released project only.
        assert not (fixture.root / "archive" / _SIBLING).exists(), output
        sibling_after = {
            path: data for path, data in fixture.sibling.snapshot().items()
            if path.startswith(f"projects/{_SIBLING}/")
        }
        assert sibling_after == sibling_before

        # The archive worked under the chain's hold without taking it over:
        # the chain's own record is still the one on disk afterwards.
        assert "chain_lock=still-held" in result.stdout, output

    def test_lock_held_by_unrelated_process_still_skips_and_moves_nothing(self, fixture: _Fixture) -> None:
        _close_rc(fixture)
        lock_file = repo_lock_path(fixture.root, _PROJECT)

        with _lock_held_by_unrelated_process(lock_file) as holder_pid:
            before = fixture.demo.snapshot()
            result = fixture.archive_step_only()
            after = fixture.demo.snapshot()
            record_during = read_provenance(lock_file)
        output = result.stdout + result.stderr

        # Step 16c still returns success to the release.
        assert result.returncode == 0, output
        assert "outcome=skipped-lock-busy" in result.stdout, output
        # Dual-channel: the stable key, a message naming the holder, a remedy.
        # (Step 16c prefixes the archive's own lines onto stdout.)
        assert "[Step 16c] [archive] error:   archive.repo_lock_busy" in result.stdout, output
        assert "[Step 16c] [archive] message:" in result.stdout, output
        assert f"pid={holder_pid}" in result.stdout, output
        assert "[Step 16c] [archive] remedy:" in result.stdout, output
        assert "process outside this release's own wake chain" in result.stderr, output
        # Nothing moved, no ledger, and the holder's record was not touched.
        assert after == before
        assert not (fixture.root / "archive").exists(), output
        assert f"pid={holder_pid}\n" in record_during + "\n", record_during


# ---------------------------------------------------------------------------
# The entry points: cm/release.sh archives, cm/cancel-rc.sh does not
# ---------------------------------------------------------------------------


_ENTRY_VERSION = "v0.1.0"
_ENTRY_PROJECT = "archive-release-test"
_ENTRY_SIBLING = "archive-sibling-test"


def _write_done_bugs(project_root: pathlib.Path) -> None:
    """Five done bugs, numbered 1 to 5, in the archive sandbox's shape."""
    bugs = project_root / "bugs"
    bugs.mkdir(parents=True, exist_ok=True)
    for number in _BUG_NUMBERS:
        name = f"BUG-{number:04d}-item-{number}"
        (bugs / f"{name}.md").write_text(
            f"# {name}\n\n**Severity:** medium\n\n## Status\ndone\n\n## Symptom\nExample.\n",
            encoding="utf-8",
        )


def _tree(path: pathlib.Path) -> dict[str, bytes]:
    """Every file under *path*, relative, mapped to its bytes."""
    return {
        p.relative_to(path).as_posix(): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


def _open_rc_with_work(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """A kanban root with two projects, the first with an RC open and one commit on it."""
    repo = tmp_path / "dev_repo"
    _init_git_repo_with_remote(repo, tmp_path / "bare_remote.git")
    root = _build_kanban_root(tmp_path, _ENTRY_PROJECT, repo, push_to_remote="false")
    with (root / "kanban.cfg").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[archive]\narchive_count = {_ARCHIVE_COUNT}\n")
    _add_sibling_project(root, _ENTRY_SIBLING, tmp_path / "sibling_repo")
    for project in (_ENTRY_PROJECT, _ENTRY_SIBLING):
        _write_done_bugs(root / "projects" / project)

    opened = _run_cm_script(_OPEN_RC_SCRIPT, [_ENTRY_VERSION], root, _ENTRY_PROJECT, repo_root=repo)
    assert opened.returncode == 0, opened.stdout + opened.stderr
    _commit_on_branch(
        repo, f"rc/{_ENTRY_VERSION}", "feature.txt", "feature\n", "feat: add feature",
        return_branch="main",
    )
    return root, repo


class TestArchiveThroughReleaseEntryPoints:
    """The shipped CM scripts, run as subprocesses against a sandbox root."""

    def test_release_archives_released_project_and_leaves_sibling_alone(self, tmp_path: pathlib.Path) -> None:
        root, repo = _open_rc_with_work(tmp_path)
        sibling_before = _tree(root / "projects" / _ENTRY_SIBLING)

        result = _run_cm_script(_RELEASE_SCRIPT, [], root, _ENTRY_PROJECT, repo_root=repo, timeout=180)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output

        assert not _git_branch_exists_local(repo, f"rc/{_ENTRY_VERSION}"), output
        ledger = _ledger_ids(root, _ENTRY_PROJECT)
        assert sorted(ledger) == _EXPECTED_BUG_IDS, output
        assert "[Step 16c] Archive finished for archive-release-test." in result.stdout, output
        assert not (root / "archive" / _ENTRY_SIBLING).exists(), output
        assert _tree(root / "projects" / _ENTRY_SIBLING) == sibling_before

    def test_cancelled_rc_runs_no_archive(self, tmp_path: pathlib.Path) -> None:
        root, repo = _open_rc_with_work(tmp_path)
        project_before = _tree(root / "projects" / _ENTRY_PROJECT / "bugs")

        result = _run_cm_script(
            _CANCEL_RC_SCRIPT, ["--key", _ENTRY_VERSION, "--yes"], root, _ENTRY_PROJECT, repo_root=repo,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0, output

        assert not _git_branch_exists_local(repo, f"rc/{_ENTRY_VERSION}"), output
        assert not (root / "archive").exists(), output
        assert "[Step 16c]" not in output, output
        assert _tree(root / "projects" / _ENTRY_PROJECT / "bugs") == project_before
