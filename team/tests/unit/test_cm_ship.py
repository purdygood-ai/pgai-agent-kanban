"""
test_cm_ship.py — Unit tests for pgai_agent_kanban.cm.ship: the one
implementation behind team/scripts/ship-rc.sh and POST /operations/ship-rc.

Coverage:

  1. Refusals decided before any process starts: a malformed version, an
     unregistered project, a branch prefix that cannot name a branch. Each
     names its machine key and exit code 1.
  2. A dry run prints the plan and runs no git command.
  3. ``push_to_remote = false`` keeps every git command away from origin
     (no ls-remote, fetch, fast-forward from origin or push), while the
     default sends each of them -- the same ship, two policies. A project
     the shared push resolver cannot resolve (no project.cfg, no
     projects.cfg registration, or a project.cfg without dev_tree_path
     while PGAI_DEV_TREE_PATH or a repository override names a repository)
     ships locally the same way, after one skipped-unresolved-project line
     naming the missing piece.
  4. A failing git step stops the ship at that step: nothing after it runs.
  5. The post-ship reset: only the RC headings release-state.md carries
     become ``none``; Last Released rises to the shipped version and never
     falls; every appended block is kept; a missing file or a failed write
     is a warning, never a failed ship. A HALT-AFTER the RC close satisfies
     is spent (promoted to HALT) at either scope, project scope first; an
     unsatisfied one is left armed.
  6. The hook phases: a hook's merged output is prefixed line by line (a
     last line without a newline stays without one), a hook with no
     interpreter line runs as a bash script, a failing post-tag hook is a
     warning, a failing pre-tag hook stops the ship, and a failing
     pre-squash hook is a HALT recorded in the refusal ledger.
  7. The CLI entry: a refusal's text and exit code reach stderr and the
     process status.

git is replaced in process (``cancel._run_git``, which ship_rc's git steps
call) and hook resolution with a stub, so no repository is needed and
nothing reaches a remote; the end-to-end runs on real sandbox repositories,
compared with the recorded pre-port behaviour, are
tests/integration/test_cm_ship_rc.py. Every kanban root is a throwaway
directory under tmp_path.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import Optional

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import cancel, ship, write_rc_state  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402

_PROJECT = "ship-unit"
_VERSION = "v2.1.0"
_RC = f"rc/{_VERSION}"
_STATE = (
    "# Release State\n\n## Active RC\n{active}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
    "{opened_by}## Last Released\nv2.0.3\n\n"
    "## Operator Override\n- kept as written\n"
)
_OPENED_BY = "## RC Opened By Task\nCM-20260101-000-open\n\n"


class FakeGit:
    """Stands in for ``cancel._run_git``: every call succeeds unless named in *fail*, and is recorded."""

    def __init__(self, fail: Optional[str] = None) -> None:
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, repo, args):
        args = tuple(args)
        self.calls.append(args)
        if self.fail and args and args[0] == self.fail:
            return subprocess.CompletedProcess(args, 1, "", f"fatal: {self.fail} refused\n")
        if args[:3] == ("rev-parse", "--verify", f"refs/tags/{_VERSION}"):
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[:2] == ("status", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def origin_calls(self) -> list[tuple[str, ...]]:
        return [
            call for call in self.calls
            if call[0] in ("fetch", "push", "ls-remote") or any(arg.startswith("origin/") for arg in call)
        ]


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    git = FakeGit()
    monkeypatch.setattr(cancel, "_run_git", git)
    return git


@pytest.fixture
def no_hooks(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every phase resolves to "none configured"; the phases asked for are recorded."""
    phases: list = []

    def _none(release, phase, say_raw, warn_raw):
        phases.append(phase)
        say_raw(f"{phase} hook: none configured\n")
        return None, True

    monkeypatch.setattr(ship, "_resolve_hook", _none)
    return phases


def _seed(
    tmp_path: pathlib.Path,
    *,
    push: Optional[str] = None,
    prefix: str = "",
    state: Optional[str] = _STATE.format(active=_VERSION, opened_by=_OPENED_BY),
) -> OpsContext:
    """A kanban root with one project in the middle of RC _VERSION."""
    root = tmp_path / "root"
    proj = root / "projects" / _PROJECT
    proj.mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    cfg = f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {tmp_path / 'repo'}\nbranch_prefix = {prefix}\n"
    if push is not None:
        cfg += f"push_to_remote = {push}\n"
    (proj / "project.cfg").write_text(cfg, encoding="utf-8")
    if state is not None:
        (proj / "release-state.md").write_text(state, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    return OpsContext(kanban_root=root)


def _ship(ctx: OpsContext, project: str = _PROJECT, version: str = _VERSION, **kwargs):
    out: list[str] = []
    err: list[str] = []
    try:
        outcome = ship.ship_rc(ctx, project, version, out=out.append, err=err.append, **kwargs)
    except ship.ShipRcError as exc:
        return exc, "".join(out), "".join(err)
    return outcome, "".join(out), "".join(err)


# ---------------------------------------------------------------------------
# 1. Refusals before any process
# ---------------------------------------------------------------------------


def test_malformed_version_refuses_before_any_process(tmp_path: pathlib.Path) -> None:
    ctx = _seed(tmp_path)
    with pytest.MonkeyPatch.context() as mp:
        def _forbidden(*args, **kwargs):
            raise AssertionError(f"no process may start: {args!r}")
        mp.setattr(subprocess, "run", _forbidden)
        mp.setattr(subprocess, "Popen", _forbidden)
        result, out, err = _ship(ctx, version="2.1")
    assert isinstance(result, ship.ShipRcError)
    assert (result.error, result.exit_code) == ("ship_rc.refused", 1)
    assert result.text == "ERROR: invalid version format: '2.1'\nExpected format: vX.Y.Z (e.g. v0.5.0)\n"
    assert out == err == ""


def test_unregistered_project_prints_the_resolver_block(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    result, out, _err = _ship(ctx, project="no-such-project")
    assert isinstance(result, ship.ShipRcError)
    assert result.error == "project_resolve.not_found"
    assert parse_error_contract(result.text.split("\n\nERROR:")[0])["error"] == "project_resolve.not_found"
    assert result.text.endswith(
        "\n\nERROR: project context is required.\n"
        "  Pass --project <name>, or set PGAI_PROJECT_NAME=<name> in the environment.\n"
    )
    assert out == "" and fake_git.calls == []


def test_unusable_branch_prefix_refuses(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path, prefix="bad prefix")
    result, out, _err = _ship(ctx)
    assert isinstance(result, ship.ShipRcError)
    assert result.error == "ship_rc.refused"
    assert "invalid branch_prefix 'bad prefix'" in result.text
    assert out == "" and fake_git.calls == []


# ---------------------------------------------------------------------------
# 2. Dry run
# ---------------------------------------------------------------------------


def test_dry_run_prints_the_plan_and_runs_no_git(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path, prefix="ai_")
    outcome, out, err = _ship(ctx, project="ship", dry_run=True)
    assert outcome == ship.ShipOutcome(_PROJECT, _VERSION, f"ai_{_RC}", "ai_main", f"ai_{_VERSION}", "planned")
    assert out == (
        "ship-rc dry-run: no git operations will be performed.\n"
        f"  Project:   {_PROJECT}\n  Version:   {_VERSION}\n  RC branch: ai_{_RC}\n"
        f"  Main:      ai_main\n  Tag:       ai_{_VERSION}\n  (dry-run: exiting before any git step)\n"
    )
    assert err == "" and fake_git.calls == []


# ---------------------------------------------------------------------------
# 3. push_to_remote
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("push", ["false", '"false"'], ids=["bare", "quoted"])
def test_local_only_project_sends_nothing_to_origin(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list, push: str
) -> None:
    ctx = _seed(tmp_path, push=push)
    outcome, out, _err = _ship(ctx)
    assert isinstance(outcome, ship.ShipOutcome), outcome
    assert outcome.outcome == "shipped" and outcome.pushed is False
    assert fake_git.origin_calls() == [], "push_to_remote=false must keep every git command off origin"
    assert ("tag", _VERSION) in fake_git.calls and ("branch", "-D", _RC) in fake_git.calls
    assert "[ship-rc] Push policy: push_to_remote=false — skipping origin operations. Local ship only.\n" in out
    for kind, command in (
        ("check", f"git ls-remote --exit-code --heads origin {_RC}"),
        ("fetch", "git fetch origin --tags"),
        ("pull", "git merge --ff-only origin/main"),
        ("push", f"git push origin main refs/tags/{_VERSION}"),
        ("push", f"git push origin --delete {_RC}"),
    ):
        assert f"[push_to_remote=false] skipping origin {kind} for {_PROJECT}: {command}\n" in out
    assert no_hooks == ["pre-squash", "pre-tag", "post-tag"]


@pytest.mark.parametrize("push", [None, "true", "False", ""], ids=["absent", "true", "capitalised", "blank"])
def test_any_other_policy_ships_through_origin(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list, push: Optional[str]
) -> None:
    ctx = _seed(tmp_path, push=push)
    outcome, out, _err = _ship(ctx)
    assert isinstance(outcome, ship.ShipOutcome), outcome
    assert outcome.pushed is True
    assert fake_git.origin_calls() == [
        ("ls-remote", "--exit-code", "--heads", "origin", _RC),
        ("fetch", "origin", "--tags"),
        ("merge", "--ff-only", "origin/main"),
        ("push", "origin", "main", f"refs/tags/{_VERSION}"),
        ("push", "origin", "--delete", _RC),
    ]
    assert "push_to_remote=false" not in out


_SKIP = "skipped-unresolved-project"


@pytest.mark.parametrize(
    ("shape", "token"),
    [
        ("no-project-cfg", "missing project-cfg"),
        ("no-registry", "missing registration"),
        ("no-dev-tree-path", "missing dev-tree-path"),
    ],
)
@pytest.mark.parametrize("via", ["environment", "repo-root"])
def test_an_unresolved_project_ships_locally_without_contacting_a_remote(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fake_git: FakeGit, no_hooks: list,
    shape: str, token: str, via: str,
) -> None:
    """Neither PGAI_DEV_TREE_PATH nor a repository override makes a project pushable."""
    ctx = _seed(tmp_path, push="true")
    cfg = ctx.project_root(_PROJECT) / "project.cfg"
    if shape == "no-project-cfg":
        cfg.unlink()
    elif shape == "no-registry":
        (ctx.kanban_root / "projects.cfg").unlink()
    else:
        cfg.write_text(f"[project]\nproject_name = {_PROJECT}\nbranch_prefix =\n", encoding="utf-8")
    repo = str(tmp_path / "repo")
    kwargs = {}
    if via == "environment":
        monkeypatch.setenv("PGAI_DEV_TREE_PATH", repo)
    else:
        monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)
        kwargs["repo_root"] = repo

    outcome, out, _err = _ship(ctx, **kwargs)

    assert isinstance(outcome, ship.ShipOutcome), outcome
    assert outcome.outcome == "shipped" and outcome.pushed is False
    assert fake_git.origin_calls() == []
    assert ("tag", _VERSION) in fake_git.calls
    skips = [line for line in out.splitlines() if _SKIP in line]
    assert len(skips) == 1, out
    assert skips[0].startswith(f"[ship-rc] {_SKIP}: project '{_PROJECT}': {token}: "), skips[0]
    assert skips[0].endswith("; nothing pushed and no remote contacted"), skips[0]
    assert "Push policy" not in out
    assert (
        f"[push_to_remote=false] skipping origin push for {_PROJECT}: git push origin main refs/tags/{_VERSION}\n"
    ) in out


def test_a_registered_project_named_by_prefix_still_pushes(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list
) -> None:
    """The push decision reads the resolved name, never the prefix as typed."""
    ctx = _seed(tmp_path)
    outcome, out, _err = _ship(ctx, project="ship")
    assert isinstance(outcome, ship.ShipOutcome) and outcome.pushed is True, outcome
    assert ("push", "origin", "main", f"refs/tags/{_VERSION}") in fake_git.calls
    assert _SKIP not in out


# ---------------------------------------------------------------------------
# 4. A failing git step stops the ship
# ---------------------------------------------------------------------------


def test_failing_git_step_stops_before_the_next(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, no_hooks: list
) -> None:
    git = FakeGit(fail="tag")
    monkeypatch.setattr(cancel, "_run_git", git)
    ctx = _seed(tmp_path)
    result, out, err = _ship(ctx)
    assert isinstance(result, ship.ShipRcError)
    assert (result.error, result.exit_code) == ("ship_rc.git_failed", 1)
    assert result.text == (
        f"\nERROR: git operation failed at step: git tag {_VERSION}\n"
        "Halting before any further git operations.\n"
        "Recover manually by inspecting git state and re-running, or revert as needed.\n"
    )
    assert err == "fatal: tag refused\n"
    assert git.calls[-1] == ("tag", _VERSION), "nothing may run after the failed step"
    assert no_hooks == ["pre-squash", "pre-tag"]
    assert f"## Active RC\n{_VERSION}\n" in (ctx.release_state_path(_PROJECT)).read_text(encoding="utf-8")
    assert "[Post-ship]" not in out


# ---------------------------------------------------------------------------
# 5. The post-ship release-state.md reset
# ---------------------------------------------------------------------------


def test_reset_touches_only_the_rc_headings_the_file_carries(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list
) -> None:
    ctx = _seed(tmp_path, state=_STATE.format(active=_VERSION, opened_by=""))
    outcome, out, _err = _ship(ctx)
    assert isinstance(outcome, ship.ShipOutcome), outcome
    assert ctx.release_state_path(_PROJECT).read_text(encoding="utf-8") == (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        f"## Last Released\n{_VERSION}\n\n## Operator Override\n- kept as written\n"
    )
    assert (
        f"  Active RC         -> none  (was: {_VERSION})\n"
        "  RC Opened At      -> none  (was: 2026-01-01T00:00:00+00:00)\n"
        "  RC Opened By Task -> none  (was: )\n"
        f"  Last Released     -> {_VERSION}  (was: v2.0.3)\n"
    ) in out


def test_already_reset_file_is_left_as_written(tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list) -> None:
    text = (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        f"## RC Opened By Task\nnone\n\n## Last Released\n{_VERSION}\n"
    )
    ctx = _seed(tmp_path, state=text)
    _outcome, out, _err = _ship(ctx)
    assert "  release-state.md: already reset — nothing to do.\n" in out
    assert ctx.release_state_path(_PROJECT).read_text(encoding="utf-8") == text


def _state(active: str, last_released: Optional[str]) -> str:
    text = f"# Release State\n\n## Active RC\n{active}\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n"
    if last_released is not None:
        text += f"\n## Last Released\n{last_released}\n"
    return text


@pytest.mark.parametrize("before", ["v1.0.0", "none", None, "v1.0.9"])
def test_reset_raises_last_released_to_the_shipped_version(
    tmp_path: pathlib.Path, before: Optional[str]
) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_state("v1.1.0", before), encoding="utf-8")
    out: list[str] = []

    ship._reset_release_state(path, "v1.1.0", out.append, out.append)

    assert ship._field_as_written(path, "Last Released") == "v1.1.0", path.read_text(encoding="utf-8")
    assert ship._field_as_written(path, "Active RC") == "none"


@pytest.mark.parametrize("higher", ["v1.2.0", "v1.10.0", "v2.0.0"])
def test_reset_keeps_a_last_released_higher_than_the_shipped_version(tmp_path: pathlib.Path, higher: str) -> None:
    path = tmp_path / "release-state.md"
    path.write_text(_state("v1.1.0", higher), encoding="utf-8")
    out: list[str] = []

    ship._reset_release_state(path, "v1.1.0", out.append, out.append)

    assert ship._field_as_written(path, "Last Released") == higher
    assert ship._field_as_written(path, "Active RC") == "none"
    assert f"  Last Released     kept at {higher} (not lower than v1.1.0)" in out


def test_version_pinned_halt_after_drains_after_the_reset(tmp_path: pathlib.Path) -> None:
    from pgai_agent_kanban.halt_after.drain import check_drain

    path = tmp_path / "release-state.md"
    path.write_text(_state("v1.1.0", "v1.0.0"), encoding="utf-8")
    assert check_drain("rc:v1.1.0", tmp_path) is False

    ship._reset_release_state(path, "v1.1.0", lambda line: None, lambda text: None)

    assert check_drain("rc:v1.1.0", tmp_path) is True


@pytest.mark.parametrize("scope", ["project", "root"])
def test_ship_spends_a_halt_after_the_release_satisfies(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list, scope: str
) -> None:
    ctx = _seed(tmp_path)
    scope_root = ctx.project_root(_PROJECT) if scope == "project" else ctx.kanban_root
    (scope_root / "HALT-AFTER").write_text(f"rc:{_VERSION}\n", encoding="utf-8")

    outcome, out, err = _ship(ctx)

    assert isinstance(outcome, ship.ShipOutcome) and outcome.outcome == "shipped", (out, err)
    assert not (scope_root / "HALT-AFTER").exists(), out
    assert (scope_root / "HALT").is_file(), out
    assert f"HALT-AFTER sentinel spent at RC close: until {_VERSION} (scope: {scope_root})" in out


def test_ship_leaves_an_unsatisfied_halt_after_armed(tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list) -> None:
    ctx = _seed(tmp_path)
    sentinel = ctx.project_root(_PROJECT) / "HALT-AFTER"
    sentinel.write_text("rc:v9.0.0\n", encoding="utf-8")

    outcome, out, _err = _ship(ctx)

    assert isinstance(outcome, ship.ShipOutcome) and outcome.outcome == "shipped"
    assert sentinel.is_file()
    assert not (ctx.project_root(_PROJECT) / "HALT").exists()
    assert "HALT-AFTER until v9.0.0 is not yet satisfied" in out


def test_ship_without_a_halt_after_says_there_is_nothing_to_spend(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list
) -> None:
    ctx = _seed(tmp_path)

    _outcome, out, _err = _ship(ctx)

    assert "  no HALT-AFTER sentinel present at either scope; nothing to spend.\n" in out
    assert not (ctx.project_root(_PROJECT) / "HALT").exists()


def test_release_tag_is_the_prefixed_version(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path, prefix="ai_")

    outcome, _out, _err = _ship(ctx, dry_run=True)

    assert outcome.release_tag == f"ai_{_VERSION}"


def test_missing_release_state_is_a_warning(tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list) -> None:
    ctx = _seed(tmp_path, state=None)
    outcome, out, err = _ship(ctx)
    assert isinstance(outcome, ship.ShipOutcome) and outcome.outcome == "shipped"
    path = ctx.release_state_path(_PROJECT)
    assert err == f"  WARNING: release-state.md not found: {path}\n  Skipping reconciliation.\n"
    assert not path.exists()
    assert out.endswith(f"  - {_RC} deleted from origin and locally\n")


def test_failed_reset_is_a_warning_and_the_ship_completes(
    tmp_path: pathlib.Path, fake_git: FakeGit, no_hooks: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _refuse(path, updates):
        raise OSError("read-only file system")

    monkeypatch.setattr(write_rc_state, "update_release_state_fields", _refuse)
    ctx = _seed(tmp_path)
    outcome, out, err = _ship(ctx)
    assert isinstance(outcome, ship.ShipOutcome) and outcome.outcome == "shipped"
    assert err.startswith("  WARNING: release-state.md reconciliation failed: read-only file system\n")
    assert "Release complete." in out


# ---------------------------------------------------------------------------
# 6. Hook phases
# ---------------------------------------------------------------------------


def _release(ctx: OpsContext, repo: pathlib.Path) -> "ship._Release":
    return ship._Release(ctx, _PROJECT, _VERSION, str(repo), _RC, ctx.project_root(_PROJECT))


def _hook(path: pathlib.Path, body: str, shebang: bool = True) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(("#!/usr/bin/env bash\n" if shebang else "") + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_hook_output_is_prefixed_line_by_line_and_runs_in_the_repository(tmp_path: pathlib.Path) -> None:
    ctx = _seed(tmp_path)
    repo = tmp_path / "repo"
    hook = _hook(
        tmp_path / "hooks" / "h.sh",
        'echo "cwd=$(pwd) rc=$PGAI_RC_BRANCH v=$PGAI_TARGET_VERSION"\necho oops >&2\nprintf tail\n',
    )
    out: list[str] = []
    code = ship._run_hook(_release(ctx, repo), "cm-release-pre-tag", hook, out.append, out.append)
    assert code == 0
    assert "".join(out) == (
        "[ship-rc] running hook cm-release-pre-tag...\n"
        f"[hook cm-release-pre-tag] cwd={repo} rc={_RC} v={_VERSION}\n"
        "[hook cm-release-pre-tag] oops\n"
        "[hook cm-release-pre-tag] tail"
    )


def test_hook_without_an_interpreter_line_runs_as_a_bash_script(tmp_path: pathlib.Path) -> None:
    ctx = _seed(tmp_path)
    hook = _hook(tmp_path / "hooks" / "plain.sh", "echo \"bash=${BASH_VERSION:+yes}\"\nexit 3\n", shebang=False)
    out: list[str] = []
    code = ship._run_hook(_release(ctx, tmp_path / "repo"), "cm-release-pre-tag", hook, out.append, out.append)
    assert code == 3
    assert out[-1] == "[hook cm-release-pre-tag] bash=yes\n"


def _phase(ctx: OpsContext, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, phase: str, code: int):
    hook = _hook(tmp_path / "hooks" / f"{phase}.sh", f"echo ran\nexit {code}\n")
    monkeypatch.setattr(ship, "_resolve_hook", lambda release, p, say_raw, warn_raw: (hook, True))
    out: list[str] = []
    err: list[str] = []
    try:
        ship._hook_phase(_release(ctx, tmp_path / "repo"), phase, out.append, err.append)
    except ship.ShipRcError as exc:
        return exc, "".join(out), "".join(err)
    return None, "".join(out), "".join(err)


def test_failing_post_tag_hook_is_a_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raised, out, err = _phase(_seed(tmp_path), tmp_path, monkeypatch, "post-tag", 5)
    assert raised is None
    assert err == "[ship-rc] WARNING: hook cm-release-post-tag failed (rc=5), continuing per --no-block\n"
    assert "completed successfully" not in out


def test_failing_pre_tag_hook_stops_the_ship(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raised, _out, err = _phase(_seed(tmp_path), tmp_path, monkeypatch, "pre-tag", 4)
    assert isinstance(raised, ship.ShipRcError)
    assert (raised.error, raised.exit_code, raised.text) == ("ship_rc.hook_failed", 1, "")
    assert err == (
        "[ship-rc] ERROR: hook cm-release-pre-tag failed (rc=4), blocking release\n"
        "[ship-rc] ERROR: see [hook cm-release-pre-tag] lines above for the hook's output\n"
    )


def test_failing_pre_squash_hook_is_a_recorded_halt(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _seed(tmp_path)
    raised, out, _err = _phase(ctx, tmp_path, monkeypatch, "pre-squash", 9)
    assert isinstance(raised, ship.ShipRcError)
    assert raised.error == "ship-rc.pre-squash-hook-failed" and raised.exit_code == 1
    block = parse_error_contract(raised.text.split("\n\nHALT:")[0])
    assert block["error"] == "ship-rc.pre-squash-hook-failed"
    assert "(rc=1)" in block["message"] and _VERSION in block["message"]
    assert raised.text.startswith("\n[ship-rc] HALT\n")
    assert "[hook cm-release-pre-squash] ran\n" in out
    ledger = (ctx.project_root(_PROJECT) / "refusal-ledger.md").read_text(encoding="utf-8")
    assert "- Error: ship-rc.pre-squash-hook-failed" in ledger


# ---------------------------------------------------------------------------
# 7. CLI entry
# ---------------------------------------------------------------------------


def test_cli_refusal_reaches_stderr_and_the_exit_code(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture) -> None:
    ctx = _seed(tmp_path)
    code = ship._cli_main(["ship_rc", "--kanban-root", str(ctx.kanban_root), "--project", _PROJECT, "--key", "v1"])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err == "ERROR: invalid version format: 'v1'\nExpected format: vX.Y.Z (e.g. v0.5.0)\n"


def test_cli_rejects_an_unknown_verb(capsys: pytest.CaptureFixture) -> None:
    assert ship._cli_main(["unship_rc"]) == 1
    assert capsys.readouterr().err == "cm: VERB must be one of: ship_rc\n"
