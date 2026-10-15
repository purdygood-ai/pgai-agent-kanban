"""
test_cm_cancel.py — Unit tests for pgai_agent_kanban.cm.cancel: the one
implementation behind team/scripts/cm/cancel-rc.sh and POST
/operations/cancel-rc.

Coverage:

  1. Confirmation and force are separate parameters of ``cancel_rc`` and
     separate fields of the request body. With neither ``yes`` nor a prompt
     the call refuses dual-channel before running git or touching a file;
     ``force`` alone is no confirmation. A declined or unanswered prompt
     changes nothing.
  2. ``force`` bypasses the Active-RC mismatch check and nothing else: the
     named RC's branch and per-RC record are cleaned up, release-state.md
     (describing another RC) is left byte-for-byte, and the current-branch
     check still refuses.
  3. The push decision: a registered project with push_to_remote true under
     the cm role runs the remote steps in the script's order (fetch, remote
     delete, fast-forward from origin); false runs none of them. An
     unresolved project -- no project.cfg, not registered in projects.cfg,
     or a project.cfg without dev_tree_path while PGAI_DEV_TREE_PATH is set
     -- runs no remote step at all (not even ls-remote) and narrates one
     skipped-unresolved-project line naming the missing piece.
  4. A failing git step ends the cancel with git's own exit code and its
     stderr passed through, before release-state.md is touched.
  5. The pending-task listing and the post-cancel reminders.
  5b. A re-run on an already-cancelled RC still marks a per-RC record left
     ``in_progress`` cancelled and archives it, and leaves an archived
     record (empty slot) untouched.
  6. The project.cfg readers answer as project_paths.sh does, and
     push_to_remote answers through the shared push-decision resolver.

git is replaced in process (``cancel._run_git``), so no repository is
needed and nothing reaches a remote; the end-to-end runs on a real sandbox
repository are tests/integration/test_cm_cancel_rc.py. Every kanban root is
a throwaway directory under tmp_path.
"""

from __future__ import annotations

import inspect
import pathlib
import subprocess
import sys
from typing import Optional

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import cancel  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402

_PROJECT = "cancel-unit"
_VERSION = "v2.1.0"
_RC = f"rc/{_VERSION}"
_STATE = (
    "# Release State\n\n## Active RC\n{active}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
    "## RC Opened By Task\nCM-20260101-000-open\n\n## Last Released\nv2.0.3\n\n"
    "## Operator Override\n- kept as written\n"
)
_MUTATING = ("fetch", "push", "branch", "checkout", "merge")
_SKIP = "skipped-unresolved-project"


class FakeGit:
    """Answers the git calls cancel_rc makes and records each one."""

    def __init__(self, *, current: str = "main", local: bool = True, remote: bool = False,
                 fail: Optional[tuple[str, int]] = None) -> None:
        self.current = current
        self.local = local
        self.remote = remote
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, repo: str, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        verb = args[0]
        if self.fail and verb == self.fail[0]:
            return subprocess.CompletedProcess(args, self.fail[1], "", f"fatal: {verb} failed\n")
        if verb == "symbolic-ref":
            return subprocess.CompletedProcess(args, 0, f"{self.current}\n", "")
        if verb == "rev-parse":
            return subprocess.CompletedProcess(args, 0 if self.local else 128, "", "")
        if verb == "ls-remote":
            return subprocess.CompletedProcess(args, 0 if self.remote else 2, "", "")
        if verb == "branch":
            self.local = False
            return subprocess.CompletedProcess(args, 0, f"Deleted branch {args[2]} (was 1234abc).\n", "")
        if verb == "checkout":
            self.current = args[1]
            return subprocess.CompletedProcess(args, 0, "", f"Switched to branch '{args[1]}'\n")
        return subprocess.CompletedProcess(args, 0, "", "")

    @property
    def mutations(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] in _MUTATING]


@pytest.fixture
def board(tmp_path: pathlib.Path) -> pathlib.Path:
    """A kanban root whose project has an active RC _VERSION and push_to_remote off."""
    return _board(tmp_path / "root")


def _register(root: pathlib.Path, *names: str) -> None:
    """Write projects.cfg with one [project:<name>] section per name."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.cfg").write_text(
        "".join(f"[project:{name}]\npriority=1\n\n" for name in names), encoding="utf-8"
    )


def _board(root: pathlib.Path, *, active: str = _VERSION, push: str = "false") -> pathlib.Path:
    proj = root / "projects" / _PROJECT
    (proj / "tasks" / "queues").mkdir(parents=True)
    _register(root, _PROJECT)
    (proj / "release-state").mkdir()
    (proj / "project.cfg").write_text(
        f"[project]\ndev_tree_path = {root / 'repo'}\nbranch_prefix =\npush_to_remote = {push}\n",
        encoding="utf-8",
    )
    (proj / "release-state.md").write_text(_STATE.format(active=active), encoding="utf-8")
    (proj / "release-state" / f"{_VERSION}.json").write_text(
        '{\n  "outcome": "in_progress",\n  "rc": "' + _VERSION + '"\n}\n', encoding="utf-8"
    )
    return root


def _add_task(root: pathlib.Path, task_id: str, release: str, state: Optional[str]) -> None:
    folder = root / "projects" / _PROJECT / "tasks" / task_id
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(f"# {task_id}\n\n## Release Version\n{release}\n", encoding="utf-8")
    block = f"## State\n{state}\n\n" if state is not None else ""
    (folder / "status.md").write_text(f"# Status\n\n{block}## Summary\nx\n", encoding="utf-8")


def _files(root: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Streams:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.err: list[str] = []

    @property
    def stdout(self) -> str:
        return "".join(self.out)

    @property
    def stderr(self) -> str:
        return "".join(self.err)


def _cancel(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch, git: FakeGit, **kwargs):
    """Run cancel_rc against *root* with *git*; return (outcome or error, streams)."""
    monkeypatch.setattr(cancel, "_run_git", git)
    streams = Streams()
    try:
        result = cancel.cancel_rc(
            OpsContext(kanban_root=root), _PROJECT, kwargs.pop("version", _VERSION),
            out=streams.out.append, err=streams.err.append, **kwargs,
        )
    except cancel.CancelRcError as exc:
        result = exc
    return result, streams


@pytest.fixture(autouse=True)
def _no_agent_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PGAI_AGENT_ROLE", raising=False)


# ---------------------------------------------------------------------------
# 1. Confirmation and force are separate
# ---------------------------------------------------------------------------


def test_yes_and_force_are_separate_parameters_and_body_fields() -> None:
    from pgai_agent_kanban.api.routers.operations import CancelRcBody

    params = inspect.signature(cancel.cancel_rc).parameters
    for name in ("yes", "force"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is False, name
    fields = CancelRcBody.model_fields
    assert {"yes", "force"} <= set(fields)
    assert fields["yes"].default is False and fields["force"].default is False


@pytest.mark.parametrize("force", [False, True], ids=["plain", "force-alone"])
def test_no_confirmation_refuses_before_anything_runs(board, monkeypatch, force) -> None:
    before = _files(board)
    git = FakeGit()
    result, streams = _cancel(board, monkeypatch, git, force=force)

    assert isinstance(result, cancel.CancelRcError)
    assert result.exit_code == 1
    triple = parse_error_contract(result.text)
    assert triple is not None and triple["error"] == "cancel_rc.confirmation_required", result.text
    assert _VERSION in triple["message"] and _PROJECT in triple["message"]
    assert triple["remedy"] == f"team/scripts/cm/cancel-rc.sh --project {_PROJECT} --key {_VERSION}"
    assert git.calls == [], "no git command may run without confirmation"
    assert streams.stdout == "" and streams.stderr == ""
    assert _files(board) == before


@pytest.mark.parametrize("answer", ["n", "", "nope", None], ids=["n", "empty", "nope", "end-of-input"])
def test_a_declined_or_unanswered_prompt_changes_nothing(board, monkeypatch, answer) -> None:
    before = _files(board)
    asked: list[str] = []
    git = FakeGit()

    def prompt(question: str) -> Optional[str]:
        asked.append(question)
        return answer

    result, streams = _cancel(board, monkeypatch, git, prompt=prompt)

    assert isinstance(result, cancel.CancelRcError) and result.exit_code == 1
    assert asked == [f"Proceed with cancellation of {_RC}? [y/N] "]
    assert "This will:\n  - Delete local branch rc/v2.1.0\n" in streams.stdout
    declined_line = "Cancelled. No changes made.\n"
    assert streams.stdout.endswith(declined_line) == (answer is not None), streams.stdout
    assert git.mutations == []
    assert _files(board) == before


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES\t"])
def test_a_yes_answer_proceeds(board, monkeypatch, answer) -> None:
    git = FakeGit()
    result, _ = _cancel(board, monkeypatch, git, prompt=lambda _q: answer)
    assert isinstance(result, cancel.CancelOutcome), result
    assert ["branch", "-D", _RC] in git.calls


# ---------------------------------------------------------------------------
# 2. force bypasses the Active-RC mismatch check and nothing else
# ---------------------------------------------------------------------------


def test_mismatch_without_force_is_refused_and_changes_nothing(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="v9.9.9")
    before = _files(root)
    git = FakeGit()
    result, _ = _cancel(root, monkeypatch, git, yes=True)

    assert isinstance(result, cancel.CancelRcError) and result.exit_code == 1
    assert result.text == (
        "ERROR: Active RC mismatch.\n"
        "  release-state.md shows Active RC = 'v9.9.9'\n"
        f"  You requested cancel of: '{_VERSION}'\n"
        "  Only the active RC can be cancelled.\n"
    )
    assert git.mutations == []
    assert _files(root) == before


def test_force_cleans_up_the_named_rc_and_leaves_release_state_alone(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="v9.9.9")
    state_file = root / "projects" / _PROJECT / "release-state.md"
    state_before = state_file.read_bytes()
    git = FakeGit()
    result, streams = _cancel(root, monkeypatch, git, yes=True, force=True)

    assert isinstance(result, cancel.CancelOutcome), result
    assert result.release_state_reset is False
    assert git.mutations == [["branch", "-D", _RC]]
    assert state_file.read_bytes() == state_before
    assert "WARNING: Active RC mismatch (--force bypassing check)." in streams.stderr
    assert f"  Active RC is v9.9.9, not {_VERSION} — release-state.md left unchanged (--force).\n" in streams.stdout
    state_dir = root / "projects" / _PROJECT / "release-state"
    assert not (state_dir / f"{_VERSION}.json").exists()
    assert len(list((state_dir / "history").glob(f"{_VERSION}-cancelled-*.json"))) == 1


def test_force_does_not_bypass_the_current_branch_check(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="v9.9.9")
    git = FakeGit(current="feature/elsewhere")
    result, _ = _cancel(root, monkeypatch, git, yes=True, force=True)
    assert isinstance(result, cancel.CancelRcError)
    assert result.text.startswith("ERROR: must be on 'main' or 'rc/v2.1.0' to cancel this RC\n")
    assert git.mutations == []


def test_force_on_the_active_rc_is_an_ordinary_cancel(board, monkeypatch) -> None:
    result, _ = _cancel(board, monkeypatch, FakeGit(), yes=True, force=True)
    assert isinstance(result, cancel.CancelOutcome) and result.release_state_reset is True
    state = (board / "projects" / _PROJECT / "release-state.md").read_text(encoding="utf-8")
    assert state == (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv2.0.3\n\n"
        "## Operator Override\n- kept as written\n"
    )


@pytest.mark.parametrize(
    "last_released_block, expected_block, warned",
    [
        pytest.param("## Last Released\n\n", "## Last Released\n\n", True, id="empty-field-left-as-written"),
        pytest.param("## Last Released\n", "## Last Released\n", True, id="empty-field-directly-above-block"),
        pytest.param("", "## Last Released\nnone\n\n", False, id="absent-heading-written-as-none"),
    ],
)
def test_last_released_write_back_never_takes_another_blocks_value(
    board, monkeypatch, last_released_block: str, expected_block: str, warned: bool
) -> None:
    """An empty ## Last Released stays empty through the reset, with a
    warning, instead of receiving the appended block's first line."""
    state_file = board / "projects" / _PROJECT / "release-state.md"
    state_file.write_text(
        f"# Release State\n\n## Active RC\n{_VERSION}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
        f"## RC Opened By Task\nCM-20260101-000-open\n\n{last_released_block}"
        "## Operator Override\n- kept as written\n",
        encoding="utf-8",
    )
    result, streams = _cancel(board, monkeypatch, FakeGit(), yes=True)

    assert isinstance(result, cancel.CancelOutcome) and result.release_state_reset is True, result
    state = state_file.read_text(encoding="utf-8")
    assert state == (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        f"## RC Opened By Task\nnone\n\n{expected_block}"
        "## Operator Override\n- kept as written\n"
    ), state
    assert ("the Last Released field has no value" in streams.stderr) is warned, streams.stderr


# ---------------------------------------------------------------------------
# 3. push_to_remote
# ---------------------------------------------------------------------------


def test_push_true_under_the_cm_role_runs_the_remote_steps_in_order(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", push='"true"')
    monkeypatch.setenv("PGAI_AGENT_ROLE", "CM")
    git = FakeGit(current=_RC, remote=True)
    result, streams = _cancel(root, monkeypatch, git, yes=True)

    assert isinstance(result, cancel.CancelOutcome), result
    assert git.calls == [
        ["fetch", "origin"],
        ["symbolic-ref", "--short", "HEAD"],
        ["rev-parse", "--verify", f"refs/heads/{_RC}"],
        ["ls-remote", "--exit-code", "--heads", "origin", _RC],
        ["push", "origin", "--delete", _RC],
        ["checkout", "main"],
        ["merge", "--ff-only", "origin/main"],
        ["branch", "-D", _RC],
        ["checkout", "main"],
        ["merge", "--ff-only", "origin/main"],
    ]
    assert streams.stdout.startswith(
        "[cm-cancel-rc] Push policy: push_to_remote=true — origin branch delete will proceed.\n"
        "Fetching origin...\n"
    )
    assert f"Deleting {_RC} from origin...\n  Deleted {_RC} from origin.\n" in streams.stdout


def test_push_true_under_another_role_skips_only_the_fetch(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", push="true")
    monkeypatch.setenv("PGAI_AGENT_ROLE", "tester")
    git = FakeGit(remote=True)
    result, streams = _cancel(root, monkeypatch, git, yes=True)
    assert isinstance(result, cancel.CancelOutcome), result
    assert ["fetch", "origin"] not in git.calls
    assert ["push", "origin", "--delete", _RC] in git.calls
    assert "PGAI_AGENT_ROLE=tester — skipping git fetch origin" in streams.stdout


def _origin_calls(git: FakeGit) -> list[list[str]]:
    return [c for c in git.calls if c[0] in ("fetch", "push", "ls-remote") or any("origin" in a for a in c)]


def _unresolve(root: pathlib.Path, shape: str) -> None:
    """Turn _board's registered project into one the push resolver cannot resolve."""
    cfg = root / "projects" / _PROJECT / "project.cfg"
    if shape == "no-project-cfg":
        cfg.unlink()
    elif shape == "not-registered":
        _register(root, "another-project")
    elif shape == "no-dev-tree-path":
        cfg.write_text("[project]\nbranch_prefix =\n", encoding="utf-8")
    else:
        raise ValueError(shape)


_UNRESOLVED_SHAPES = [
    ("no-project-cfg", "missing project-cfg"),
    ("not-registered", "missing registration"),
    ("no-dev-tree-path", "missing dev-tree-path"),
]


@pytest.mark.parametrize(("shape", "token"), _UNRESOLVED_SHAPES)
def test_an_unresolved_project_cancels_locally_without_contacting_a_remote(
    tmp_path, monkeypatch, shape, token
) -> None:
    """A repository named directly (REPO_ROOT) and PGAI_DEV_TREE_PATH never make a project pushable."""
    root = _board(tmp_path / "root", push="true")
    _unresolve(root, shape)
    monkeypatch.setenv("PGAI_AGENT_ROLE", "cm")
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(root / "repo"))
    git = FakeGit(current=_RC, remote=True)
    result, streams = _cancel(root, monkeypatch, git, yes=True, repo_root=str(root / "repo"))

    assert isinstance(result, cancel.CancelOutcome), (result, streams.stdout, streams.stderr)
    assert _origin_calls(git) == []
    skips = [line for line in streams.stdout.splitlines() if _SKIP in line]
    assert len(skips) == 1, streams.stdout
    assert skips[0].startswith(f"[cm-cancel-rc] {_SKIP}: project '{_PROJECT}': {token}: "), skips[0]
    assert skips[0].endswith("; nothing pushed and no remote contacted"), skips[0]
    assert "Push policy" not in streams.stdout
    assert streams.stdout.startswith(skips[0] + "\n")
    assert f"[push_to_remote=false] skipping origin push for {_PROJECT}: git push origin --delete {_RC}" in streams.stdout
    assert ["branch", "-D", _RC] in git.calls


@pytest.mark.parametrize(("shape", "token"), [s for s in _UNRESOLVED_SHAPES if s[0] != "not-registered"])
def test_an_unresolved_project_without_a_repository_logs_its_skip_line_then_refuses(
    tmp_path, monkeypatch, shape, token
) -> None:
    """The decision is taken before the dev tree is resolved, so the line precedes the refusal."""
    root = _board(tmp_path / "root", push="true")
    _unresolve(root, shape)
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(root / "repo"))
    git = FakeGit()
    result, streams = _cancel(root, monkeypatch, git, yes=True)

    assert isinstance(result, cancel.CancelRcError) and result.exit_code == 1, result
    assert git.calls == []
    assert streams.stdout.startswith(f"[cm-cancel-rc] {_SKIP}: project '{_PROJECT}': {token}: "), streams.stdout
    assert streams.stdout.count(_SKIP) == 1


def test_push_false_runs_no_origin_step(board, monkeypatch) -> None:
    monkeypatch.setenv("PGAI_AGENT_ROLE", "cm")
    git = FakeGit(current=_RC, remote=True)
    result, streams = _cancel(board, monkeypatch, git, yes=True)
    assert isinstance(result, cancel.CancelOutcome), result
    assert _origin_calls(git) == []
    assert "push_to_remote=false — skipping git fetch origin (local-only install).\n" in streams.stdout
    assert streams.stdout.startswith("[cm-cancel-rc] Push policy: push_to_remote=false")
    assert _SKIP not in streams.stdout


# ---------------------------------------------------------------------------
# 4. A failing git step
# ---------------------------------------------------------------------------


def test_a_failing_git_step_ends_with_gits_exit_code(board, monkeypatch) -> None:
    state_file = board / "projects" / _PROJECT / "release-state.md"
    state_before = state_file.read_bytes()
    git = FakeGit(fail=("branch", 128))
    result, streams = _cancel(board, monkeypatch, git, yes=True)

    assert isinstance(result, cancel.CancelRcError)
    assert (result.exit_code, result.error, result.text) == (128, "cancel_rc.git_failed", "")
    assert streams.stderr.endswith("fatal: branch failed\n")
    assert state_file.read_bytes() == state_before
    assert cancel.cancel_exit_code(result) == 128


# ---------------------------------------------------------------------------
# 5. Pending tasks and reminders
# ---------------------------------------------------------------------------


def test_pending_tasks_skip_finished_and_hidden_folders_in_code_point_order(board) -> None:
    _add_task(board, "Z-later", _VERSION, "BACKLOG")
    _add_task(board, "A-first", _VERSION, "WORKING")
    _add_task(board, "B-done", _VERSION, "DONE")
    _add_task(board, "C-dropped", _VERSION, "WONT-DO")
    _add_task(board, "D-lower", _VERSION, "wont-do")
    _add_task(board, "E-no-state", _VERSION, None)
    _add_task(board, "F-other-rc", "v2.2.0", "BACKLOG")
    _add_task(board, ".hidden", _VERSION, "BACKLOG")
    (board / "projects" / _PROJECT / "tasks" / "G-no-readme").mkdir()

    pending = cancel.pending_tasks(OpsContext(kanban_root=board), _PROJECT, _VERSION)
    assert pending == ["A-first (WORKING)", "E-no-state ()", "Z-later (BACKLOG)"]


def test_the_reminders_print_whether_or_not_tasks_are_pending(board, monkeypatch) -> None:
    result, streams = _cancel(board, monkeypatch, FakeGit(), yes=True)
    assert isinstance(result, cancel.CancelOutcome) and result.pending == ()
    out = streams.stdout
    assert f"No pending tasks found for {_VERSION} in kanban root.\n" in out
    assert "Reminder: mark these tasks as WONT-DO" not in out
    assert (
        f"  close.sh --project {_PROJECT} --key {_VERSION}-bugfix-bundle-<YYYYMMDD> --state wont-do\n"
    ) in out
    assert "  Leaving the bundle live re-arms the discovery re-selection path.\n" in out
    assert out.endswith("You may now open a new RC with scripts/cm/open-rc.sh <version>.\n")


def test_nothing_left_to_cancel_is_a_clean_no_op(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="none")
    (root / "projects" / _PROJECT / "release-state" / f"{_VERSION}.json").unlink()
    before = _files(root)
    git = FakeGit(local=False)
    result, streams = _cancel(root, monkeypatch, git, yes=True)
    assert isinstance(result, cancel.CancelOutcome) and result.outcome == "already_cancelled"
    assert streams.stderr == "INFO: RC cancellation already complete (idempotent run).\n"
    assert git.mutations == []
    assert _files(root) == before


# ---------------------------------------------------------------------------
# 6. project.cfg readers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("push_to_remote = false", False),
        ('push_to_remote = "false"', False),
        ("push_to_remote = false   # local only", False),
        ("push_to_remote = False", True),
        ("push_to_remote = true", True),
        ("push_to_remote =", True),
        (None, True),
    ],
)
def test_push_to_remote_reads_as_project_paths_does(tmp_path, line, expected) -> None:
    proj = tmp_path / "projects" / _PROJECT
    proj.mkdir(parents=True)
    _register(tmp_path, _PROJECT)
    (proj / "project.cfg").write_text(
        f"[project]\ndev_tree_path = {tmp_path / 'repo'}\n" + (line + "\n" if line else ""), encoding="utf-8"
    )
    assert cancel.push_to_remote(OpsContext(kanban_root=tmp_path), _PROJECT) is expected


@pytest.mark.parametrize(("shape", "token"), _UNRESOLVED_SHAPES)
def test_push_to_remote_is_false_for_an_unresolved_project(tmp_path, monkeypatch, shape, token) -> None:
    """Even with push_to_remote = true written and PGAI_DEV_TREE_PATH set."""
    root = _board(tmp_path / "root", push="true")
    _unresolve(root, shape)
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(root / "repo"))
    ctx = OpsContext(kanban_root=root)
    assert cancel.push_to_remote(ctx, _PROJECT) is False
    decision = cancel.push_decision(ctx, _PROJECT)
    assert decision.decision == "unresolved"
    assert decision.status_line.startswith(f"{_SKIP}: project '{_PROJECT}': {token}: ")


def test_legacy_project_cfg_bare_keys_are_read(tmp_path) -> None:
    proj = tmp_path / "projects" / _PROJECT
    proj.mkdir(parents=True)
    _register(tmp_path, _PROJECT)
    (proj / "PROJECT.cfg").write_text(
        f"dev_tree_path={tmp_path / 'repo'}\npush_to_remote='false'\nbranch_prefix=\"ai_\"\n", encoding="utf-8"
    )
    ctx = OpsContext(kanban_root=tmp_path)
    assert cancel.push_to_remote(ctx, _PROJECT) is False
    assert cancel.branch_prefix(ctx, _PROJECT) == "ai_"


def test_an_invalid_branch_prefix_is_refused(tmp_path) -> None:
    proj = tmp_path / "projects" / _PROJECT
    proj.mkdir(parents=True)
    (proj / "project.cfg").write_text("[project]\nbranch_prefix = ai/\n", encoding="utf-8")
    with pytest.raises(cancel.CancelRcError) as caught:
        cancel.branch_prefix(OpsContext(kanban_root=tmp_path), _PROJECT)
    assert caught.value.exit_code == 1
    assert caught.value.text.splitlines() == [
        "project_paths.sh: validate_branch_prefix: invalid branch_prefix 'ai/' (allowed characters: "
        "a-z A-Z 0-9 _ -; spaces, slashes, dots, and other special characters are not permitted)",
        f"project_paths.sh: pp_branch_prefix: invalid branch_prefix in config for project '{_PROJECT}'",
    ]


# ---------------------------------------------------------------------------
# 5b. Re-run after a cancel that stopped part-way
# ---------------------------------------------------------------------------


def test_rerun_marks_a_leftover_in_progress_record_cancelled(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="none")
    state_dir = root / "projects" / _PROJECT / "release-state"

    result, streams = _cancel(root, monkeypatch, FakeGit(local=False), yes=True)

    assert isinstance(result, cancel.CancelOutcome), streams.stderr
    assert result.outcome == "already_cancelled"
    assert "RC cancellation already complete" in streams.stderr
    assert not (state_dir / f"{_VERSION}.json").exists(), "the slot must be freed"
    archived = list((state_dir / "history").glob(f"{_VERSION}-cancelled-*.json"))
    assert len(archived) == 1, streams.stdout
    assert '"outcome": "cancelled"' in archived[0].read_text(encoding="utf-8")
    assert "marking it cancelled" in streams.stdout


def test_rerun_leaves_an_already_archived_record_unchanged(tmp_path, monkeypatch) -> None:
    root = _board(tmp_path / "root", active="none")
    state_dir = root / "projects" / _PROJECT / "release-state"
    (state_dir / "history").mkdir()
    archived = state_dir / "history" / f"{_VERSION}-cancelled-20260101T000000Z.json"
    archived.write_text('{\n  "outcome": "cancelled"\n}\n', encoding="utf-8")
    (state_dir / f"{_VERSION}.json").unlink()
    before = _files(root)

    result, streams = _cancel(root, monkeypatch, FakeGit(local=False), yes=True)

    assert isinstance(result, cancel.CancelOutcome) and result.outcome == "already_cancelled"
    assert _files(root) == before
    assert "Nothing to do. Exiting cleanly." in streams.stdout
