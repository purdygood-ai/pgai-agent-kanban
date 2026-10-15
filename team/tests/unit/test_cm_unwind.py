"""
test_cm_unwind.py — Unit tests for pgai_agent_kanban.cm.unwind: the one
implementation behind team/scripts/unwind-rc.sh and POST
/operations/unwind-rc.

Coverage:

  1. Confirmation and force are separate parameters of ``unwind_rc``. With
     no confirmation and no prompt a live call refuses dual-channel before
     reading anything; ``force`` alone is no confirmation unless the caller
     opts into the CLI contract (``force_answers_prompt``); a dry run needs
     none. A declined or unanswered prompt changes nothing.
  2. ``force`` bypasses the Active-RC mismatch check and nothing else: the
     HALT, shipped-tag, project-directory and Active-RC-none checks still
     refuse.
  3. Each refusal names its machine key and the CLI's exit code; a step that
     fails after the backup stops with exit 2.
  4. The writes: a status.md gets WONT-DO and one Cancellation Note and is
     left alone on a re-run; queue lines move to ``[x]`` and intake lines
     back to ``[ ]``; an intake file's ``## Status`` value line becomes
     ``open`` with every other line kept; release-state.md keeps Last
     Released and every appended block.
  5. The readers: ``## Bundled Items`` ids (order and repeats kept, ``.md``
     dropped, section bounded), and the discovery-cache walk's depth limit.
  6. The CLI entry: ``--force`` answers the prompt there, and a refusal's
     text and exit code reach stderr and the process status.
  7. The push gate: origin is asked about (``ls-remote``) and the RC branch
     deleted there (``push --delete``) only when the shared push decision
     answers push. ``push_to_remote = false`` and each unresolved shape (no
     project.cfg, no projects.cfg, no dev_tree_path with PGAI_DEV_TREE_PATH
     set) make neither call in a dry run or a confirmed run, narrate the
     skip once, and still finish every local step.

git is replaced in process (``unwind._git``), so no repository is needed and
nothing reaches a remote; the end-to-end runs on a real sandbox repository,
compared with the recorded pre-port behaviour, are
tests/integration/test_cm_unwind_rc.py. Every kanban root is a throwaway
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

from pgai_agent_kanban.cm import unwind  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402

_PROJECT = "unwind-unit"
_VERSION = "v2.1.0"
_RC = f"rc/{_VERSION}"
_STATE = (
    "# Release State\n\n## Active RC\n{active}\n\n## RC Opened At\n2026-01-01T00:00:00+00:00\n\n"
    "## RC Opened By Task\nCM-20260101-000-open\n\n## Last Released\nv2.0.3\n\n"
    "## Operator Override\n- kept as written\n"
)
_MUTATING = ("checkout", "push", "mv")


class FakeGit:
    """Stands in for ``unwind._git``: local branches and tags are sets, every call is recorded."""

    def __init__(
        self, branches=("main", _RC), tags=(), fail: Optional[str] = None, remote: bool = False
    ) -> None:
        self.branches = set(branches)
        self.tags = set(tags)
        self.fail = fail
        # Whether origin answers ls-remote for the RC branch (it never does
        # unless a test says so).
        self.remote = remote
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, repo, *args, merge_stderr=False):
        self.calls.append(args)
        if self.fail and args and args[0] == self.fail:
            return subprocess.CompletedProcess(args, 1, "fatal: refused\n", "")
        if args[:2] == ("rev-parse", "--verify"):
            ref = args[2]
            if ref.startswith("refs/heads/"):
                return subprocess.CompletedProcess(args, 0 if ref[11:] in self.branches else 1, "", "")
            return subprocess.CompletedProcess(args, 0 if ref[10:] in self.tags else 1, "", "")
        if args[0] == "rev-list":
            return subprocess.CompletedProcess(args, 0, "0123abcd\n", "")
        if args[0] == "ls-remote" and self.remote:
            return subprocess.CompletedProcess(args, 0, f"0123abcd\trefs/heads/{args[-1]}\n", "")
        if args[0] == "ls-remote" or args[:2] == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess(args, 128, "", "")
        if args[0] == "checkout":
            return subprocess.CompletedProcess(args, 0, f"Switched to branch '{args[1]}'\n", "")
        if args[:2] == ("branch", "-D"):
            self.branches.discard(args[2])
            return subprocess.CompletedProcess(args, 0, f"Deleted branch {args[2]} (was 0123abc).\n", "")
        if args == ("branch",):
            listing = "".join(f"  {name}\n" for name in sorted(self.branches))
            return subprocess.CompletedProcess(args, 0, listing, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def network_calls(self) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call and call[0] in ("ls-remote", "push", "fetch", "pull")]

    def mutated(self) -> bool:
        return any(call[0] in _MUTATING or call[:2] == ("branch", "-D") for call in self.calls)


@pytest.fixture
def fake_git(monkeypatch: pytest.MonkeyPatch) -> FakeGit:
    git = FakeGit()
    monkeypatch.setattr(unwind, "_git", git)
    return git


def _seed(tmp_path: pathlib.Path, active: Optional[str] = _VERSION, halt: bool = True) -> OpsContext:
    """A kanban root with one halted project in the middle of RC _VERSION."""
    root = tmp_path / "root"
    proj = root / "projects" / _PROJECT
    for sub in ("tasks/queues", "requirements", "bugs", "priority", "release-state"):
        (proj / sub).mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    (proj / "project.cfg").write_text(
        f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {tmp_path / 'repo'}\n", encoding="utf-8"
    )
    if halt:
        (proj / "HALT").write_text("", encoding="utf-8")
    if active is not None:
        (proj / "release-state.md").write_text(_STATE.format(active=active), encoding="utf-8")
    task = proj / "tasks" / "CODER-20260101-001-task"
    task.mkdir()
    (task / "README.md").write_text(f"# Task\n\n## Release Version\n{_VERSION}\n", encoding="utf-8")
    (task / "status.md").write_text(
        "# Status\n\n## State\nWORKING\n\n## Summary\nunit\n", encoding="utf-8"
    )
    (proj / "tasks" / "queues" / "coder_backlog.md").write_text(
        "# Coder Backlog\n\n## Queue\n- [A] CODER-20260101-001-task — working\n", encoding="utf-8"
    )
    (proj / "tasks" / "queues" / "bug_backlog.md").write_text(
        "# Bug Backlog\n\n## Queue\n- [x] BUG-0001-alpha\n", encoding="utf-8"
    )
    (proj / "bugs" / "BUG-0001-alpha.md").write_text(
        "# BUG-0001-alpha\n\n## Status\nrunning\n\n**Severity:** high\nbody text\n", encoding="utf-8"
    )
    (proj / "requirements" / f"{_VERSION}-bugfix-bundle-20260101.md").write_text(
        "# bundle\n\n## Bundled Items\n- BUG-0001-alpha.md\n\n## Notes\n- BUG-0009-not-bundled\n",
        encoding="utf-8",
    )
    (proj / "release-state" / f"{_VERSION}.json").write_text(
        '{"outcome": "in_progress", "rc": "' + _VERSION + '"}\n', encoding="utf-8"
    )
    return OpsContext(kanban_root=root)


def _files(ctx: OpsContext) -> dict:
    proj = ctx.project_root(_PROJECT)
    return {str(p.relative_to(proj)): p.read_bytes() for p in sorted(proj.rglob("*")) if p.is_file()}


def _run(ctx: OpsContext, tmp_path: pathlib.Path, **kwargs):
    out: list[str] = []
    err: list[str] = []
    kwargs.setdefault("temp_root", str(tmp_path / "tmp"))
    try:
        outcome = unwind.unwind_rc(ctx, _PROJECT, _VERSION, out=out.append, err=err.append, **kwargs)
    except unwind.UnwindRcError as exc:
        return exc, "".join(out), "".join(err)
    return outcome, "".join(out), "".join(err)


# ---------------------------------------------------------------------------
# 1. Confirmation and force are separate
# ---------------------------------------------------------------------------


def test_a_live_call_without_confirmation_refuses_before_reading_anything(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    # No root at all: anything the call read would fail differently.
    ctx = OpsContext(kanban_root=tmp_path / "absent-root")
    result, out, err = _run(ctx, tmp_path)
    assert isinstance(result, unwind.UnwindRcError)
    assert result.error == "unwind_rc.confirmation_required"
    assert unwind.unwind_exit_code(result) == 1
    fields = parse_error_contract(result.text)
    assert fields is not None and fields["error"] == "unwind_rc.confirmation_required"
    assert fields["remedy"] == f"team/scripts/unwind-rc.sh --project {_PROJECT} --key {_VERSION} --confirm"
    assert (out, err, fake_git.calls) == ("", "", [])


def test_force_alone_is_not_a_confirmation(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    before = _files(ctx)
    result, _, _ = _run(ctx, tmp_path, force=True)
    assert isinstance(result, unwind.UnwindRcError)
    assert result.error == "unwind_rc.confirmation_required"
    assert _files(ctx) == before and not fake_git.mutated()


def test_the_cli_contract_lets_force_answer_the_prompt(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    result, out, _ = _run(ctx, tmp_path, force=True, force_answers_prompt=True)
    assert isinstance(result, unwind.UnwindOutcome) and result.outcome == "unwound"
    assert "Flags:      --force (Active-RC mismatch check bypassed)" in out
    assert "--confirm" not in out.split("What will be touched")[0]
    assert "Type 'yes'" not in out


def test_a_dry_run_needs_no_confirmation_and_changes_nothing(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    before = _files(ctx)
    result, out, _ = _run(ctx, tmp_path, dry_run=True)
    assert isinstance(result, unwind.UnwindOutcome) and result.outcome == "planned"
    assert out.endswith("Dry run complete.  No changes were made.\n")
    assert "  Mode:       DRY RUN (no changes will be made)\n" in out
    assert _files(ctx) == before and not fake_git.mutated()
    assert not (tmp_path / "tmp").exists() or not any((tmp_path / "tmp").iterdir())


@pytest.mark.parametrize("answer", [None, "no", "y", "YES"], ids=["unanswered", "no", "y", "upper-case"])
def test_a_prompt_not_answered_yes_changes_nothing(
    tmp_path: pathlib.Path, fake_git: FakeGit, answer: Optional[str]
) -> None:
    ctx = _seed(tmp_path)
    before = _files(ctx)
    asked: list[str] = []
    result, out, _ = _run(ctx, tmp_path, prompt=lambda q: asked.append(q) or answer)
    assert isinstance(result, unwind.UnwindRcError) and result.error == "unwind_rc.declined"
    assert unwind.unwind_exit_code(result) == 1
    assert asked == [f"Proceed with unwind of {_RC}? Type 'yes' to continue: "]
    assert "There is NO undo except the backup created in step (a).\n" in out
    assert ("Unwind aborted. No changes were made." in out) is (answer is not None)
    assert _files(ctx) == before and not fake_git.mutated()


def test_a_padded_yes_answers_the_prompt(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    result, out, _ = _run(ctx, tmp_path, prompt=lambda q: "  yes\t")
    assert isinstance(result, unwind.UnwindOutcome), out
    assert "--- Step a: Backup ---" in out


# ---------------------------------------------------------------------------
# 2. force bypasses the Active-RC mismatch check and nothing else
# ---------------------------------------------------------------------------


def test_a_mismatched_active_rc_refuses_without_force(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path, active="v9.9.9")
    before = _files(ctx)
    result, _, _ = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindRcError) and result.error == "unwind_rc.refused"
    assert "ERROR: Active RC mismatch." in result.text
    assert "release-state.md shows Active RC = 'v9.9.9'" in result.text
    assert _files(ctx) == before and not fake_git.mutated()


def test_force_bypasses_the_mismatch_with_a_warning(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path, active="v9.9.9")
    result, _, err = _run(ctx, tmp_path, confirm=True, force=True)
    assert isinstance(result, unwind.UnwindOutcome), err
    assert err.startswith(
        "WARNING: Active RC mismatch (--force bypassing check).\n"
        f"  Active RC: v9.9.9\n  Requested: {_VERSION}\n\n"
    )


@pytest.mark.parametrize(
    "breakage, message",
    [
        ("halt", "ERROR: no HALT file found."),
        ("tag", f"ERROR: {_VERSION} is a shipped git tag."),
        ("active-none", "ERROR: Active RC is 'none'"),
        ("no-release-state", "ERROR: release-state.md not found:"),
        ("bad-version", "ERROR: invalid version format: 'v2.1'"),
    ],
)
def test_force_does_not_bypass_the_other_checks(
    tmp_path: pathlib.Path, fake_git: FakeGit, breakage: str, message: str
) -> None:
    ctx = _seed(
        tmp_path,
        active={"active-none": "none", "no-release-state": None}.get(breakage, _VERSION),
        halt=breakage != "halt",
    )
    if breakage == "tag":
        fake_git.tags.add(_VERSION)
    version = "v2.1" if breakage == "bad-version" else _VERSION
    before = _files(ctx)
    with pytest.raises(unwind.UnwindRcError) as caught:
        unwind.unwind_rc(
            ctx, _PROJECT, version, confirm=True, force=True,
            temp_root=str(tmp_path / "tmp"), out=lambda _t: None, err=lambda _t: None,
        )
    assert caught.value.error == "unwind_rc.refused"
    assert caught.value.text.startswith(message), caught.value.text
    assert unwind.unwind_exit_code(caught.value) == 1
    assert _files(ctx) == before and not fake_git.mutated()


def test_a_missing_project_directory_refuses(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "HALT").write_text("", encoding="utf-8")
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\n", encoding="utf-8")
    result, _, _ = _run(OpsContext(kanban_root=root), tmp_path, confirm=True, repo_root=str(tmp_path))
    assert isinstance(result, unwind.UnwindRcError)
    assert result.text.startswith(f"ERROR: project directory not found: {root.resolve()}/projects/{_PROJECT}\n")


def test_an_unknown_project_carries_the_resolver_block(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    with pytest.raises(unwind.UnwindRcError) as caught:
        unwind.unwind_rc(ctx, "nobody", _VERSION, confirm=True, out=lambda _t: None, err=lambda _t: None)
    assert caught.value.error == "project_resolve.not_found"
    assert parse_error_contract(caught.value.text)["error"] == "project_resolve.not_found"
    assert caught.value.text.endswith("\n\nERROR: could not resolve project context for 'nobody'.\n")


def test_a_registered_prefix_resolves_to_the_project(tmp_path: pathlib.Path, fake_git: FakeGit) -> None:
    ctx = _seed(tmp_path)
    outcome = unwind.unwind_rc(ctx, "unwind", _VERSION, dry_run=True, out=lambda _t: None, err=lambda _t: None)
    assert outcome.project == _PROJECT


def test_a_failing_checkout_stops_after_the_backup_with_exit_two(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    monkeypatch.setattr(unwind, "_git", FakeGit(fail="checkout"))
    result, out, _ = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindRcError) and unwind.unwind_exit_code(result) == 2
    assert result.text == f"ERROR: could not checkout main in {tmp_path / 'repo'}\n"
    assert "  Backup complete: " in out
    status = ctx.tasks_dir(_PROJECT) / "CODER-20260101-001-task" / "status.md"
    assert "WORKING" in status.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. The writes
# ---------------------------------------------------------------------------


def test_an_unwind_writes_every_store_and_keeps_what_it_does_not_own(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    proj = ctx.project_root(_PROJECT)
    result, out, err = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindOutcome), out + err
    assert result.cancelled_tasks == ("CODER-20260101-001-task",)
    assert _RC not in fake_git.branches

    status = (proj / "tasks" / "CODER-20260101-001-task" / "status.md").read_text(encoding="utf-8")
    assert status.startswith("# Status\n\n## State\nWONT-DO\n\n## Summary\nunit\n\n## Cancellation Note (")
    assert status.count("## Cancellation Note") == 1
    assert "- [x] CODER-20260101-001-task — working\n" in (
        proj / "tasks" / "queues" / "coder_backlog.md"
    ).read_text(encoding="utf-8")
    assert "- [ ] BUG-0001-alpha\n" in (proj / "tasks" / "queues" / "bug_backlog.md").read_text(encoding="utf-8")
    assert (proj / "bugs" / "BUG-0001-alpha.md").read_text(encoding="utf-8") == (
        "# BUG-0001-alpha\n\n## Status\nopen\n\n**Severity:** high\nbody text\n"
    )
    assert (proj / "release-state.md").read_text(encoding="utf-8") == (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv2.0.3\n\n"
        "## Operator Override\n- kept as written\n"
    )
    assert '"outcome": "cancelled"' in (proj / "release-state" / f"{_VERSION}.json").read_text(encoding="utf-8")
    superseded = list((proj / "requirements").glob(f"{_VERSION}-bugfix-bundle-20260101.SUPERSEDED-on-cancel-*.md"))
    assert len(superseded) == 1
    backup = pathlib.Path(result.backup_dir)
    assert backup.parent == tmp_path / "tmp"
    assert (backup / "tasks" / "CODER-20260101-001-task" / "status.md").read_text(encoding="utf-8").count("WORKING") == 1
    assert "BUG-0009" not in out, "only the ## Bundled Items section is read"


def test_a_status_md_already_carrying_a_cancellation_note_is_left_alone(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    status = ctx.tasks_dir(_PROJECT) / "CODER-20260101-001-task" / "status.md"
    text = "# Status\n\n## State\nWONT-DO\n\n## Cancellation Note (earlier)\nkept\n"
    status.write_text(text, encoding="utf-8")
    _, out, _ = _run(ctx, tmp_path, confirm=True)
    assert status.read_text(encoding="utf-8") == text
    assert "  CODER-20260101-001-task — already cancelled (skipping)\n" in out


def test_files_without_their_heading_are_warned_about_and_left_whole(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    status = ctx.tasks_dir(_PROJECT) / "CODER-20260101-001-task" / "status.md"
    status.write_text("# Status\n\nno state here\n", encoding="utf-8")
    bug = ctx.bugs_dir(_PROJECT) / "BUG-0001-alpha.md"
    bug.write_text("# BUG-0001-alpha\n\nno status here\n", encoding="utf-8")
    result, out, err = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindOutcome)
    assert status.read_text(encoding="utf-8") == "# Status\n\nno state here\n"
    assert bug.read_text(encoding="utf-8") == "# BUG-0001-alpha\n\nno status here\n"
    assert f"  WARNING: no `## State` heading found in {status}; left untouched\n" in err
    assert f"  WARNING: could not reset ## Status in {bug}\n" in err
    assert "unwind-rc.sh complete — all steps (a-l) finished." in out


_RUNNING_BUNDLE = (
    "# bundle\n\n## Status\nrunning\n\n## PM Task\n\nPM-20260101-001-decompose\n\n"
    "## Bundled Items\n- BUG-0001-alpha.md\n"
)


def test_the_superseded_bundle_is_closed_to_wont_do_and_keeps_every_other_line(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    requirements = ctx.requirements_dir(_PROJECT)
    (requirements / f"{_VERSION}-bugfix-bundle-20260101.md").write_text(_RUNNING_BUNDLE, encoding="utf-8")

    result, out, err = _run(ctx, tmp_path, confirm=True)

    assert isinstance(result, unwind.UnwindOutcome), out + err
    superseded = list(requirements.glob(f"{_VERSION}-bugfix-bundle-20260101.SUPERSEDED-on-cancel-*.md"))
    assert len(superseded) == 1
    assert superseded[0].read_text(encoding="utf-8") == _RUNNING_BUNDLE.replace(
        "## Status\nrunning\n", "## Status\nwont-do\n"
    )
    assert "could not set ## Status" not in err


def test_a_file_an_earlier_run_superseded_is_closed_on_the_next_run(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)
    requirements = ctx.requirements_dir(_PROJECT)
    (requirements / f"{_VERSION}-bugfix-bundle-20260101.md").unlink()
    earlier = requirements / f"{_VERSION}-bugfix-bundle-20260101.SUPERSEDED-on-cancel-2026-01-01T00:00:00+00:00.md"
    earlier.write_text(_RUNNING_BUNDLE, encoding="utf-8")

    result, out, err = _run(ctx, tmp_path, confirm=True)

    assert isinstance(result, unwind.UnwindOutcome), out + err
    assert f"  Found already-superseded file: {earlier.name}\n" in out
    assert "## Status\nwont-do\n" in earlier.read_text(encoding="utf-8")


def test_a_superseded_bundle_with_no_status_heading_is_warned_about_and_left_whole(
    tmp_path: pathlib.Path, fake_git: FakeGit
) -> None:
    ctx = _seed(tmp_path)

    result, out, err = _run(ctx, tmp_path, confirm=True)

    assert isinstance(result, unwind.UnwindOutcome), out + err
    superseded = next(ctx.requirements_dir(_PROJECT).glob("*.SUPERSEDED-on-cancel-*.md"))
    assert "## Status" not in superseded.read_text(encoding="utf-8")
    assert f"  WARNING: could not set ## Status to wont-do in {superseded}\n" in err
    assert "unwind-rc.sh complete — all steps (a-l) finished." in out


# ---------------------------------------------------------------------------
# 5. The readers
# ---------------------------------------------------------------------------


def test_bundled_items_keeps_order_and_repeats_and_stops_at_the_next_section() -> None:
    text = (
        "## Bundled Items\n"
        "- trigger: BUG-0002-b medium\n"
        "- BUG-0001-a.md (`/x/BUG-0001-a.md`)\n"
        "- BUG-0002-b.md\n"
        "- PRIORITY-0003-c.md\n"
        "## Acceptance\n- BUG-0004-d.md\n"
    )
    assert unwind.bundled_items(text, "BUG") == ["BUG-0002-b", "BUG-0001-a", "BUG-0002-b"]
    assert unwind.bundled_items(text, "PRIORITY") == ["PRIORITY-0003-c"]
    assert unwind.bundled_items("## Other\n- BUG-0001-a.md\n", "BUG") == []


def test_the_discovery_walk_honours_its_depth_limit(tmp_path: pathlib.Path) -> None:
    top = tmp_path / "cache"
    for rel in ("a-v1.0.0", "d/b-v1.0.0", "d/e/c-v1.0.0", "other"):
        (top / rel).parent.mkdir(parents=True, exist_ok=True)
        (top / rel).write_text("", encoding="utf-8")
    names = lambda paths: sorted(pathlib.Path(p).name for p in paths)  # noqa: E731
    assert names(unwind._find_names(top, "*v1.0.0*")) == ["a-v1.0.0", "b-v1.0.0", "c-v1.0.0"]
    assert names(unwind._find_names(top, "*v1.0.0*", max_depth=2)) == ["a-v1.0.0", "b-v1.0.0"]


# ---------------------------------------------------------------------------
# 6. The CLI entry
# ---------------------------------------------------------------------------


def test_the_cli_reports_a_refusal_on_stderr_with_its_exit_code(
    tmp_path: pathlib.Path, fake_git: FakeGit, capsys: pytest.CaptureFixture
) -> None:
    ctx = _seed(tmp_path, halt=False)
    code = unwind._cli_main([
        "unwind_rc", "--kanban-root", str(ctx.kanban_root), "--project", _PROJECT,
        "--key", _VERSION, "--confirm", "--temp-root", str(tmp_path / "tmp"),
    ])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.startswith("ERROR: no HALT file found.\n")
    assert captured.out == ""


def test_the_cli_force_flag_answers_the_prompt(
    tmp_path: pathlib.Path, fake_git: FakeGit, capsys: pytest.CaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _seed(tmp_path)
    monkeypatch.setattr("sys.stdin", None)
    code = unwind._cli_main([
        "unwind_rc", "--kanban-root", str(ctx.kanban_root), "--project", _PROJECT,
        "--key", _VERSION, "--force", "--temp-root", str(tmp_path / "tmp"),
    ])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "Type 'yes'" not in captured.out
    assert "unwind-rc.sh complete — all steps (a-l) finished." in captured.out


def test_the_cli_names_its_verbs_for_an_unknown_one(capsys: pytest.CaptureFixture) -> None:
    assert unwind._cli_main(["not_a_verb"]) == 1
    assert capsys.readouterr().err == "cm: VERB must be one of: unwind_rc\n"


# ---------------------------------------------------------------------------
# 7. The push gate
# ---------------------------------------------------------------------------

_SKIP = "skipped-unresolved-project"
_LOCAL_ONLY_POLICY = (
    "[unwind-rc] Push policy: push_to_remote=false — skipping origin operations. Local cleanup only.\n"
)


@pytest.fixture
def no_inherited_paths(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No operator path variable reaches the call; a test sets the one it means."""
    for var in ("PGAI_DEV_TREE_PATH", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR", "REPO_ROOT"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def _set_cfg(ctx: OpsContext, text: Optional[str]) -> None:
    cfg = ctx.project_root(_PROJECT) / "project.cfg"
    if text is None:
        cfg.unlink()
    else:
        cfg.write_text(text, encoding="utf-8")


def _shape(ctx: OpsContext, tmp_path: pathlib.Path, shape: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Turn the seeded root into *shape*; return the extra unwind_rc arguments it needs."""
    repo = str(tmp_path / "repo")
    if shape == "local-only":
        _set_cfg(ctx, f"[project]\nproject_name = {_PROJECT}\ndev_tree_path = {repo}\npush_to_remote = false\n")
    elif shape == "no-project-cfg":
        _set_cfg(ctx, None)
        return {"repo_root": repo}
    elif shape == "not-registered":
        (ctx.kanban_root / "projects.cfg").unlink()
    elif shape == "no-dev-tree-path":
        _set_cfg(ctx, f"[project]\nproject_name = {_PROJECT}\n")
        monkeypatch.setenv("PGAI_DEV_TREE_PATH", repo)
    elif shape != "push-default":
        raise ValueError(f"unknown push-gate shape {shape!r}")
    return {}


_UNRESOLVED = [
    ("no-project-cfg", "missing project-cfg"),
    ("not-registered", "missing registration"),
    ("no-dev-tree-path", "missing dev-tree-path"),
]


def test_a_project_with_no_push_key_checks_origin_and_deletes_the_rc_branch_there(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    git = FakeGit(remote=True)
    monkeypatch.setattr(unwind, "_git", git)
    result, out, err = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindOutcome), out + err
    ls_remote = ("ls-remote", "--exit-code", "--heads", "origin", _RC)
    assert git.network_calls() == [ls_remote, ls_remote, ("push", "origin", "--delete", _RC)]
    assert f"     - Delete remote branch: origin/{_RC}  [EXISTS]\n" in out
    assert f"  Remote branch origin/{_RC} deleted.\n" in out
    assert _SKIP not in out + err and "Push policy" not in out


def test_push_to_remote_false_contacts_no_remote_and_unwinds_locally(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    _shape(ctx, tmp_path, "local-only", no_inherited_paths)
    git = FakeGit(remote=True)
    monkeypatch.setattr(unwind, "_git", git)
    result, out, err = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindOutcome), out + err
    assert git.network_calls() == []
    assert out.startswith(_LOCAL_ONLY_POLICY)
    assert out.count("Push policy") == 1 and _SKIP not in out + err
    assert f"     - Delete remote branch: origin/{_RC}  [skipped — push_to_remote=false]\n" in out
    assert (
        f"  [push_to_remote=false] skipping origin push for {_PROJECT}: "
        f"git push origin --delete {_RC} (Step c)\n"
    ) in out
    assert _RC not in git.branches
    assert result.cancelled_tasks == ("CODER-20260101-001-task",)
    assert "## Active RC\nnone\n" in ctx.release_state_path(_PROJECT).read_text(encoding="utf-8")


@pytest.mark.parametrize(("shape", "token"), _UNRESOLVED, ids=[shape for shape, _ in _UNRESOLVED])
def test_an_unresolved_project_contacts_no_remote_and_logs_one_skip_line(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch,
    shape: str, token: str,
) -> None:
    ctx = _seed(tmp_path)
    extra = _shape(ctx, tmp_path, shape, no_inherited_paths)
    git = FakeGit(remote=True)
    monkeypatch.setattr(unwind, "_git", git)
    result, out, err = _run(ctx, tmp_path, confirm=True, **extra)
    assert isinstance(result, unwind.UnwindOutcome), out + err
    assert git.network_calls() == []
    skips = [line for line in (out + err).splitlines() if _SKIP in line]
    assert len(skips) == 1, out + err
    assert skips[0].startswith(f"[unwind-rc] {_SKIP}: project '{_PROJECT}': {token}: ")
    assert skips[0].endswith("; nothing pushed and no remote contacted")
    assert out.startswith(skips[0] + "\n") and "Push policy" not in out
    assert f"     - Delete remote branch: origin/{_RC}  [skipped — unresolved project]\n" in out
    assert (
        f"  [unresolved project] skipping origin push for {_PROJECT}: "
        f"git push origin --delete {_RC} (Step c)\n"
    ) in out
    assert _RC not in git.branches
    assert result.cancelled_tasks == ("CODER-20260101-001-task",)
    assert "unwind-rc.sh complete — all steps (a-l) finished." in out


@pytest.mark.parametrize(
    "shape", ["local-only", *[shape for shape, _ in _UNRESOLVED]],
)
def test_a_dry_run_for_a_project_that_does_not_push_makes_no_ls_remote(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    ctx = _seed(tmp_path)
    extra = _shape(ctx, tmp_path, shape, no_inherited_paths)
    git = FakeGit(remote=True)
    monkeypatch.setattr(unwind, "_git", git)
    result, out, _ = _run(ctx, tmp_path, dry_run=True, **extra)
    assert isinstance(result, unwind.UnwindOutcome) and result.outcome == "planned"
    assert git.network_calls() == [] and not git.mutated()
    assert f"     - Delete remote branch: origin/{_RC}  [skipped — " in out


def test_a_dry_run_for_a_project_that_pushes_checks_origin_once(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _seed(tmp_path)
    git = FakeGit(remote=True)
    monkeypatch.setattr(unwind, "_git", git)
    _run(ctx, tmp_path, dry_run=True)
    assert git.network_calls() == [("ls-remote", "--exit-code", "--heads", "origin", _RC)]


def test_the_skip_line_is_printed_before_a_refusal_that_follows_it(
    tmp_path: pathlib.Path, no_inherited_paths: pytest.MonkeyPatch, fake_git: FakeGit
) -> None:
    """No project.cfg and no repository named: the skip line, then the project-config refusal."""
    ctx = _seed(tmp_path)
    _set_cfg(ctx, None)
    result, out, _ = _run(ctx, tmp_path, confirm=True)
    assert isinstance(result, unwind.UnwindRcError) and result.error == "unwind_rc.refused"
    assert out.startswith(f"[unwind-rc] {_SKIP}: project '{_PROJECT}': missing project-cfg: ")
    assert out.count("\n") == 1
    assert fake_git.calls == []
