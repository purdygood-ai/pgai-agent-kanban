"""
test_measured_pace_open_rc_step.py
===================================
Behavioral tests for the open-rc step that regenerates the measured-pace
blocks on a freshly cut RC branch:

  team/scripts/lib/cm/measured_pace_refresh.sh   (cm_refresh_measured_pace_docs)
  team/scripts/cm/open-rc.sh                     (calls it after cutting the branch)

Behaviors locked in:

  1. Against a sandbox kanban root whose newest shipped rollup is newer than
     the blocks in a scratch repository, the step re-renders README.md and
     HOW_TO.md, after which ``generate_measured_pace_docs.py --freshness``
     reports no stale block, and the result is committed on the RC branch
     (so the next RC inherits it) while main is left alone.
  2. A scratch project whose README.md carries no measured-pace markers
     changes no file, makes no commit, and the caller keeps running.
  3. A generator that exits non-zero narrates, leaves both docs at HEAD,
     makes no commit, and the caller (under ``set -euo pipefail``) keeps
     running.
  4. Docs with an operator's uncommitted edit are left untouched.
  5. Blocks that are already current produce no commit.
  6. open-rc.sh runs the step after the RC branch is cut and before the push.
  7. A generator that exits non-zero, or a commit of the regenerated blocks
     that fails, leaves exactly one operator inbox entry naming both docs and
     the re-render command, and the caller keeps running; a step that
     commits leaves no entry.
  8. A missing generator leaves exactly one operator inbox entry naming the
     missing path, both docs and the re-render command, the caller keeps
     running, and a second run meeting the same missing generator adds no
     second entry.

Every test builds its own throwaway kanban root and scratch git repository
under pytest's tmp_path, and runs the step in a subprocess whose
environment sets or clears all five kanban-root variables together. Nothing
touches the live install or the real dev tree's branches, and no remote is
contacted.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_GENERATOR = _TEAM_DIR / "scripts" / "generate_measured_pace_docs.py"
_HELPER = _TEAM_DIR / "scripts" / "lib" / "cm" / "measured_pace_refresh.sh"
_OPEN_RC = _TEAM_DIR / "scripts" / "cm" / "open-rc.sh"

_PROJECT = "demo-project"
_RC_BRANCH = "rc/v1.0.2"
_BEGIN = "<!-- MEASURED-PACE-GENERATED:BEGIN -->"
_END = "<!-- MEASURED-PACE-GENERATED:END -->"


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test here drives a real scratch git repository."""
    require_binary("git", label="measured-pace-open-rc-step")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _rollup(rc: str, wall_time_minutes: float, tasks_by_agent: dict[str, int]) -> dict:
    """One shipped per-RC rollup with the fields the generator reads."""
    return {
        "rc": rc,
        "outcome": "shipped",
        "wall_time_minutes": wall_time_minutes,
        "tasks": {"total": sum(tasks_by_agent.values()), "by_agent": dict(tasks_by_agent)},
        "tokens": {"by_agent": {agent: {} for agent in tasks_by_agent}},
    }


_OLD_ROLLUP = _rollup("v1.0.0", 60.0, {"pm": 1, "coder": 2, "tester": 1, "cm": 2})
_NEW_ROLLUP = _rollup("v1.0.1", 90.0, {"pm": 1, "coder": 3, "tester": 1, "cm": 1})


def _write_rollup(rc_dir: pathlib.Path, rollup: dict) -> None:
    rc_dir.mkdir(parents=True, exist_ok=True)
    (rc_dir / f"{rollup['rc']}.json").write_text(json.dumps(rollup), encoding="utf-8")


def _git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def _doc_with_block(title: str) -> str:
    return f"# {title}\n\nIntro prose.\n\n{_BEGIN}\nplaceholder\n{_END}\n\nTrailing prose.\n"


class _Sandbox:
    """A throwaway kanban root plus a scratch repository on an RC branch."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path / "kanban_root"
        self.rc_dir = self.root / "projects" / _PROJECT / "metrics" / "rc"
        self.repo = tmp_path / "dev_tree"
        self.home = tmp_path / "home"
        self.scratch = tmp_path / "scratch"
        for d in (self.root, self.repo, self.home, self.scratch):
            d.mkdir(parents=True, exist_ok=True)
        (self.root / "projects" / _PROJECT).mkdir(parents=True, exist_ok=True)

    @property
    def readme(self) -> pathlib.Path:
        return self.repo / "README.md"

    @property
    def how_to(self) -> pathlib.Path:
        return self.repo / "HOW_TO.md"

    def env(self) -> dict:
        # All five kanban-root variables set or cleared together: the three
        # roots point into the sandbox, and the two pre-resolved exports are
        # absent because build_scrubbed_env never constructs them.
        return build_scrubbed_env(
            home_dir=self.home,
            extra_path_dirs=[pathlib.Path(sys.executable).parent],
            extra_env={
                "KANBAN_ROOT": str(self.root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(self.root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(self.scratch),
            },
        )

    def init_repo(self, readme: str, how_to: str | None) -> None:
        _git(self.repo, "init", "-q", "--initial-branch=main")
        _git(self.repo, "config", "--local", "user.email", "test@pgai.local")
        _git(self.repo, "config", "--local", "user.name", "PGAI Test")
        _git(self.repo, "config", "--local", "commit.gpgsign", "false")
        self.readme.write_text(readme, encoding="utf-8")
        paths = ["README.md"]
        if how_to is not None:
            self.how_to.write_text(how_to, encoding="utf-8")
            paths.append("HOW_TO.md")
        _git(self.repo, "add", *paths)
        _git(self.repo, "commit", "-q", "-m", "Initial docs")

    def render_blocks_from(self, rollups: list[dict]) -> None:
        """Render both docs from exactly `rollups` and commit on main."""
        rc_dir = self.scratch / "render-rc"
        for rollup in rollups:
            _write_rollup(rc_dir, rollup)
        subprocess.run(
            [
                sys.executable,
                str(_GENERATOR),
                "--rc-dir",
                str(rc_dir),
                "--readme",
                str(self.readme),
                "--how-to",
                str(self.how_to),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        _git(self.repo, "commit", "-q", "-am", "Render blocks")

    def cut_rc_branch(self) -> None:
        _git(self.repo, "checkout", "-q", "-b", _RC_BRANCH)

    def run_step(self, generator: pathlib.Path | None = None) -> subprocess.CompletedProcess:
        """Source the helper under the caller's strict mode, run the step,
        then print a sentinel proving the caller kept running."""
        gen_arg = f' "{generator}"' if generator is not None else ""
        script = (
            "set -euo pipefail\n"
            f'source "{_HELPER}"\n'
            f'cm_refresh_measured_pace_docs "{self.repo}" "{_PROJECT}" "{self.root}"{gen_arg}\n'
            "echo CALLER-CONTINUED\n"
        )
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=120,
        )

    def freshness(self) -> tuple[subprocess.CompletedProcess, list]:
        findings = self.scratch / "findings.json"
        result = subprocess.run(
            [
                sys.executable,
                str(_GENERATOR),
                "--freshness",
                "--project",
                _PROJECT,
                "--kanban-root",
                str(self.root),
                "--readme",
                str(self.readme),
                "--how-to",
                str(self.how_to),
                "--findings-file",
                str(findings),
            ],
            capture_output=True,
            text=True,
            env=self.env(),
            timeout=60,
        )
        return result, json.loads(findings.read_text(encoding="utf-8"))


@pytest.fixture
def stale_sandbox(tmp_path: pathlib.Path) -> _Sandbox:
    """Blocks rendered from v1.0.0 only, while v1.0.1 has since shipped."""
    sb = _Sandbox(tmp_path)
    sb.init_repo(_doc_with_block("Readme"), _doc_with_block("How to"))
    sb.render_blocks_from([_OLD_ROLLUP])
    _write_rollup(sb.rc_dir, _OLD_ROLLUP)
    _write_rollup(sb.rc_dir, _NEW_ROLLUP)
    sb.cut_rc_branch()
    return sb


# ---------------------------------------------------------------------------
# 1 -- stale blocks are regenerated and committed on the RC branch
# ---------------------------------------------------------------------------


def test_step_regenerates_stale_blocks_and_commits_them_on_the_rc_branch(stale_sandbox):
    sb = stale_sandbox
    before, before_findings = sb.freshness()
    assert before.returncode == 0
    assert len(before_findings) == 2 and {f["kind"] for f in before_findings} == {
        "stale_block"
    }, f"fixture precondition: both blocks should start stale.\n{before.stdout}"
    main_head = _git(sb.repo, "rev-parse", "main")
    rc_head_before = _git(sb.repo, "rev-parse", _RC_BRANCH)

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert f"on {_RC_BRANCH}" in result.stdout, result.stdout

    after, after_findings = sb.freshness()
    assert after.returncode == 0
    assert after_findings == [], f"blocks still stale after the step:\n{after.stdout}"
    assert "STALE" not in after.stdout

    # Committed on the RC branch, one commit touching exactly the two docs;
    # the work tree is clean and main has not moved.
    assert _git(sb.repo, "rev-parse", f"{_RC_BRANCH}~1") == rc_head_before
    changed = set(_git(sb.repo, "diff", "--name-only", f"{_RC_BRANCH}~1", _RC_BRANCH).splitlines())
    assert changed == {"README.md", "HOW_TO.md"}
    assert _git(sb.repo, "status", "--porcelain") == ""
    assert _git(sb.repo, "rev-parse", "main") == main_head

    # What the next RC inherits is the committed blob, not the work tree.
    for doc in ("README.md", "HOW_TO.md"):
        committed = _git(sb.repo, "show", f"{_RC_BRANCH}:{doc}")
        assert f"({_OLD_ROLLUP['rc']} .. {_NEW_ROLLUP['rc']})" in committed, committed
        assert "Trailing prose." in committed, "prose outside the markers must survive"


def test_step_makes_no_commit_when_blocks_are_already_current(stale_sandbox):
    sb = stale_sandbox
    assert sb.run_step().returncode == 0
    head = _git(sb.repo, "rev-parse", "HEAD")

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert "already current" in result.stdout
    assert "CALLER-CONTINUED" in result.stdout
    assert _git(sb.repo, "rev-parse", "HEAD") == head


# ---------------------------------------------------------------------------
# 2 -- a project without the markers changes no file
# ---------------------------------------------------------------------------


def test_project_without_markers_changes_no_file_and_caller_continues(tmp_path):
    sb = _Sandbox(tmp_path)
    readme = "# Plain project\n\nNo generated blocks here.\n"
    how_to = "# How to\n\nNothing generated.\n"
    sb.init_repo(readme, how_to)
    _write_rollup(sb.rc_dir, _NEW_ROLLUP)
    sb.cut_rc_branch()
    head = _git(sb.repo, "rev-parse", "HEAD")

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    narration = [line for line in result.stdout.splitlines() if line.startswith("[measured-pace]")]
    assert len(narration) == 1 and "skipped" in narration[0], result.stdout
    assert sb.readme.read_text(encoding="utf-8") == readme
    assert sb.how_to.read_text(encoding="utf-8") == how_to
    assert _git(sb.repo, "rev-parse", "HEAD") == head
    assert _git(sb.repo, "status", "--porcelain") == ""


def test_project_with_no_how_to_changes_no_file(tmp_path):
    sb = _Sandbox(tmp_path)
    readme = _doc_with_block("Readme")
    sb.init_repo(readme, None)
    _write_rollup(sb.rc_dir, _NEW_ROLLUP)
    sb.cut_rc_branch()
    head = _git(sb.repo, "rev-parse", "HEAD")

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert "skipped" in result.stdout
    assert sb.readme.read_text(encoding="utf-8") == readme
    assert _git(sb.repo, "rev-parse", "HEAD") == head


# ---------------------------------------------------------------------------
# 3 -- a failing generator narrates and does not stop the caller
# ---------------------------------------------------------------------------


def test_generator_exiting_nonzero_narrates_restores_docs_and_caller_continues(stale_sandbox):
    sb = stale_sandbox
    # A generator that half-writes a doc and then fails: the step must put
    # both docs back at HEAD rather than leave a partial render behind.
    failing = sb.scratch / "failing_generator.py"
    failing.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "readme = args[args.index('--readme') + 1]\n"
        "open(readme, 'a').write('partial render\\n')\n"
        "print('render failed', file=sys.stderr)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    head = _git(sb.repo, "rev-parse", "HEAD")

    result = sb.run_step(generator=failing)

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert "generator exited 3" in result.stderr, result.stderr
    assert _git(sb.repo, "rev-parse", "HEAD") == head
    assert _git(sb.repo, "status", "--porcelain") == ""


def test_unresolvable_project_rollups_narrate_and_change_no_file(stale_sandbox):
    sb = stale_sandbox
    head = _git(sb.repo, "rev-parse", "HEAD")
    script = (
        "set -euo pipefail\n"
        f'source "{_HELPER}"\n'
        f'cm_refresh_measured_pace_docs "{sb.repo}" "no-such-project" "{sb.root}"\n'
        "echo CALLER-CONTINUED\n"
    )

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=sb.env(),
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert "WARNING: generator exited" in result.stderr, result.stderr
    assert _git(sb.repo, "rev-parse", "HEAD") == head
    assert _git(sb.repo, "status", "--porcelain") == ""


# ---------------------------------------------------------------------------
# 4 -- an operator's uncommitted edit is never overwritten or committed
# ---------------------------------------------------------------------------


def test_uncommitted_doc_edit_is_left_untouched(stale_sandbox):
    sb = stale_sandbox
    edited = sb.readme.read_text(encoding="utf-8") + "\nOperator's unsaved note.\n"
    sb.readme.write_text(edited, encoding="utf-8")
    head = _git(sb.repo, "rev-parse", "HEAD")
    how_to_before = sb.how_to.read_text(encoding="utf-8")

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert "uncommitted changes" in result.stdout
    assert sb.readme.read_text(encoding="utf-8") == edited
    assert sb.how_to.read_text(encoding="utf-8") == how_to_before
    assert _git(sb.repo, "rev-parse", "HEAD") == head


# ---------------------------------------------------------------------------
# 5 -- open-rc.sh runs the step between cutting the branch and pushing it
# ---------------------------------------------------------------------------


def test_open_rc_runs_the_step_after_cutting_the_branch_and_before_the_push():
    # Static guard: open-rc.sh is a CM release script that reaches for the
    # remote by design, so it is not driven end to end here; the behavioral
    # tests above drive the step it calls. This pins only the call order the
    # behavior depends on: the RC branch must be checked out when the step
    # commits, and the commit must exist before the branch is pushed.
    lines = [ln.strip() for ln in _OPEN_RC.read_text(encoding="utf-8").splitlines()]
    code = [ln for ln in lines if ln and not ln.startswith("#")]

    def first_index(predicate) -> int:
        for i, ln in enumerate(code):
            if predicate(ln):
                return i
        raise AssertionError("open-rc.sh no longer has the expected line")

    sourced = first_index(lambda ln: ln.startswith("source") and "lib/cm/measured_pace_refresh.sh" in ln)
    cut = first_index(lambda ln: "checkout -b" in ln and "$RC_BRANCH" in ln)
    step = first_index(lambda ln: ln.startswith("cm_refresh_measured_pace_docs "))
    push = first_index(lambda ln: "push -u" in ln and "$RC_BRANCH" in ln)
    assert sourced < cut < step < push, (
        "open-rc.sh must source the measured-pace step, cut the RC branch, run " "the step, then push -- in that order"
    )


# ---------------------------------------------------------------------------
# 7 -- a failed regenerate or commit leaves one operator inbox entry
# ---------------------------------------------------------------------------


def _open_inbox_entries(sb: _Sandbox) -> list:
    inbox = sb.root / "projects" / _PROJECT / "operator-inbox.md"
    if not inbox.exists():
        return []
    open_body = inbox.read_text(encoding="utf-8").split("## Open", 1)[1].split("\n## ", 1)[0]
    return open_body.split("### INBOX-")[1:]


def _failing_generator(sb: _Sandbox) -> pathlib.Path:
    failing = sb.scratch / "failing_generator.py"
    failing.write_text("import sys\nprint('render failed', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    return failing


def _refuse_commits(sb: _Sandbox) -> None:
    hook = sb.repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'commit refused by the test hook' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


@pytest.mark.parametrize(
    ("failure", "cause"),
    [("generator", "the generator exited 3"), ("commit", "committing the regenerated blocks failed")],
)
def test_failed_refresh_leaves_one_inbox_entry_naming_the_docs_and_the_rerun(stale_sandbox, failure, cause):
    sb = stale_sandbox
    generator = None
    if failure == "generator":
        generator = _failing_generator(sb)
    else:
        _refuse_commits(sb)
    head = _git(sb.repo, "rev-parse", "HEAD")

    result = sb.run_step(generator=generator)

    assert result.returncode == 0, result.stderr
    assert "CALLER-CONTINUED" in result.stdout
    assert "WARNING" in result.stderr, result.stderr
    assert "Operator inbox: INBOX-0001" in result.stdout, result.stdout + result.stderr
    entries = _open_inbox_entries(sb)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert f"**Source:** rc:{_RC_BRANCH.split('/', 1)[1]}" in entry, entry
    assert cause in entry, entry
    assert str(sb.readme) in entry and str(sb.how_to) in entry, entry
    assert f"--project {_PROJECT} --kanban-root {sb.root}" in entry, entry
    assert _git(sb.repo, "rev-parse", "HEAD") == head
    assert _git(sb.repo, "status", "--porcelain") == ""


def test_refresh_that_commits_leaves_no_inbox_entry(stale_sandbox):
    sb = stale_sandbox

    result = sb.run_step()

    assert result.returncode == 0, result.stderr
    assert f"on {_RC_BRANCH}" in result.stdout, result.stdout
    assert not (sb.root / "projects" / _PROJECT / "operator-inbox.md").exists()


def test_missing_generator_leaves_one_inbox_entry_naming_its_path_across_reruns(stale_sandbox):
    sb = stale_sandbox
    missing = sb.scratch / "absent" / "generate_measured_pace_docs.py"
    head = _git(sb.repo, "rev-parse", "HEAD")

    first = sb.run_step(generator=missing)
    second = sb.run_step(generator=missing)

    for result in (first, second):
        assert result.returncode == 0, result.stderr
        assert "CALLER-CONTINUED" in result.stdout
        assert f"generator not found at {missing}" in result.stderr, result.stderr
        assert "Operator inbox: INBOX-0001" in result.stdout, result.stdout + result.stderr
    entries = _open_inbox_entries(sb)
    assert len(entries) == 1, entries
    entry = entries[0]
    assert f"**Source:** rc:{_RC_BRANCH.split('/', 1)[1]}" in entry, entry
    assert f"the generator {missing} is missing" in entry, entry
    assert str(sb.readme) in entry and str(sb.how_to) in entry, entry
    assert f"python3 {missing} --project {_PROJECT} --kanban-root {sb.root}" in entry, entry
    assert "<" not in entry.split("**How to answer:**", 1)[-1], "the by-hand command carries no placeholder"
    assert _git(sb.repo, "rev-parse", "HEAD") == head
    assert _git(sb.repo, "status", "--porcelain") == ""
