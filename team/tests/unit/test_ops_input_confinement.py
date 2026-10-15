"""
test_ops_input_confinement.py — the ops layer refuses a key, project name,
workflow type or project.cfg value that could escape the kanban root, run a
script from outside it, or inject config keys, whichever surface calls it.

Every board is a throwaway kanban root under ``tmp_path`` (the README's
live-install-safe shape: the kanban-root and temp-dir variables point into
the sandbox, the pre-resolved project variables are cleared), and beside it
-- OUTSIDE the root -- sit a sentinel file, a sentinel directory holding a
``status.md``, and a ready-looking workflow plugin. Each refused call must
raise its dual-channel refusal and leave everything under ``tmp_path``
byte-for-byte unchanged.

Coverage:

  1. Item resolution (``ops.resolve.resolve_item``, the one funnel for
     close, wontdo, delete, reset and show): a ``../``-style, absolute or
     glob-shaped key, or a traversal project, is refused before any
     filesystem access (proved with every path probe made to fail); a key
     is matched literally in the prefix glob; a match that a symlink
     carries outside the project tree is refused before it is read; the
     resolve CLI shim the bash scripts call refuses the same keys.
  2. The registry verbs (``ops.projects``): remove-project with ``..`` and
     force deletes nothing, add-project with ``../x`` changes neither
     projects.cfg nor the outside directory, and both refuse before any
     filesystem access; create-project refuses a path-shaped workflow type
     even when a ready plugin is planted at the path it names, and a
     project.cfg value holding a newline or other control character, a
     relative dev tree or one with a ``..`` segment -- writing no project.
     ``release`` and every other ready plugin, and registrable names that
     do not start with a letter, behave as before.
  3. cancel-rc (``cm.cancel.cancel_rc``): a traversal or flag-shaped
     project refuses before any project.cfg or projects.cfg is read and
     before any git command runs.
  4. The CLI scripts reach the same refusals through the ops functions:
     delete.sh, remove-project.sh, add-project.sh, create-project.sh and
     cm/cancel-rc.sh, each run against the sandbox.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.cm import cancel  # noqa: E402
from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.ops import projects as ops_projects  # noqa: E402
from pgai_agent_kanban.ops import read as ops_read  # noqa: E402
from pgai_agent_kanban.ops import resolve as ops_resolve  # noqa: E402
from pgai_agent_kanban.ops import write as ops_write  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import OpsError  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPTS = _TEAM_DIR / "scripts"
_PROJECT = "proj"
_TASK = "CODER-20260101-001-alpha"
_SENTINEL_TEXT = "sentinel: must never be read, rewritten or deleted\n"

# From <root>/projects/proj/tasks/ four '..' segments reach the directory
# holding both the kanban root and the sentinels.
_KEY_TO_SENTINEL_DIR = "../../../../sentinel_dir"
_KEY_TO_SENTINEL_FILE = "../../../../sentinel"

_READY_PLUGIN_CFG = "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = none\nagents = pm,coder\n"


def _status_md(task_id: str, state: str) -> str:
    return (
        f"# Status\n\n## Task\n{task_id}\n\n## State\n{state}\n\n## Summary\nnone\n\n"
        "## Artifacts\nnone\n\n## Blockers\nnone\n\n## Needs Human\nno\n"
    )


# ---------------------------------------------------------------------------
# Sandbox: outside sentinels, a kanban root beside them
# ---------------------------------------------------------------------------


@pytest.fixture
def outer(tmp_path: pathlib.Path) -> pathlib.Path:
    """The directory holding the kanban root and, beside it, the sentinels."""
    base = tmp_path / "outer"
    base.mkdir()
    (base / "sentinel.md").write_text(_SENTINEL_TEXT, encoding="utf-8")
    sentinel_dir = base / "sentinel_dir"
    sentinel_dir.mkdir()
    (sentinel_dir / "status.md").write_text(_status_md("SENTINEL", "DONE"), encoding="utf-8")
    (sentinel_dir / "keep.txt").write_text(_SENTINEL_TEXT, encoding="utf-8")
    # A ready-looking workflow plugin outside the root, at the path a
    # '../../tmp/x' workflow type names from <root>/workflows.
    plugin = base / "tmp" / "x"
    plugin.mkdir(parents=True)
    (plugin / "workflow.cfg").write_text(_READY_PLUGIN_CFG, encoding="utf-8")
    (plugin / "workflow.sh").write_text("#!/bin/bash\necho planted\n", encoding="utf-8")
    return base


@pytest.fixture
def root(outer: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A kanban root inside *outer*: one registered project holding one DONE task."""
    board = outer / "kanban"
    project = board / "projects" / _PROJECT
    queues = project / "tasks" / "queues"
    queues.mkdir(parents=True)
    (board / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n", encoding="utf-8")
    (queues / "coder_backlog.md").write_text(f"# CODER Backlog\n\n- [x] {_TASK}\n", encoding="utf-8")
    task_dir = project / "tasks" / _TASK
    (task_dir / "artifacts").mkdir(parents=True)
    (task_dir / "README.md").write_text(f"# Task: {_TASK}\n", encoding="utf-8")
    (task_dir / "status.md").write_text(_status_md(_TASK, "DONE"), encoding="utf-8")
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (project / "release-state.md").write_text(
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        encoding="utf-8",
    )
    (board / "tmp").mkdir()
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(board))
    monkeypatch.setenv("KANBAN_ROOT", str(board))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(board / "tmp"))
    for name in ("PGAI_PROJECT_NAME", "PGAI_PROJECT_ROOT", "PGAI_TASKS_DIR"):
        monkeypatch.delenv(name, raising=False)
    return board


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _snapshot(base: pathlib.Path) -> dict[str, bytes | None]:
    """Every path under *base*, relative: file bytes, a link's target, None for a directory."""
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            snapshot[str(path.relative_to(base))] = os.readlink(path).encode()
        elif path.is_dir():
            snapshot[str(path.relative_to(base))] = None
        else:
            snapshot[str(path.relative_to(base))] = path.read_bytes()
    return snapshot


@pytest.fixture
def no_filesystem():
    """Return a context manager inside which every path probe, read, write or delete fails the test.

    Entered after the board and the context are built, and left before any
    assertion, so a refused call inside it proves it refused before it
    looked at the filesystem at all.
    """

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"a refused call touched the filesystem: {args!r}")

    @contextlib.contextmanager
    def guard():
        with pytest.MonkeyPatch.context() as patch:
            for name in ("is_dir", "is_file", "exists", "glob", "iterdir", "read_text", "read_bytes",
                         "open", "unlink", "rmdir", "mkdir", "write_text", "resolve"):
                patch.setattr(pathlib.Path, name, _fail)
            for name in ("listdir", "scandir", "remove", "unlink", "makedirs", "mkdir", "rename", "replace"):
                patch.setattr(os, name, _fail)
            for name in ("isdir", "isfile", "exists"):
                patch.setattr(os.path, name, _fail)
            patch.setattr(shutil, "rmtree", _fail)
            patch.setattr(subprocess, "run", _fail)
            patch.setattr(subprocess, "Popen", _fail)
            yield

    return guard


def _contract(exc: BaseException) -> dict[str, str]:
    triple = parse_error_contract(str(exc))
    assert triple is not None, f"not a dual-channel refusal: {exc}"
    assert "<" not in triple["remedy"], f"remedy carries a placeholder: {triple['remedy']}"
    return triple


# ---------------------------------------------------------------------------
# 1. Item resolution
# ---------------------------------------------------------------------------

_ITEM_CALLS = {
    "delete": lambda ctx, project, key: ops_write.delete_item(ctx, project, key, force=True),
    "close": lambda ctx, project, key: ops_write.close_item(ctx, project, key),
    "wontdo": lambda ctx, project, key: ops_write.wontdo_item(ctx, project, key),
    "reset": lambda ctx, project, key: ops_write.reset_item(ctx, project, key, force=True),
    "show": lambda ctx, project, key: ops_read.get_task_status(ctx, project, key),
}


@pytest.mark.parametrize("verb", sorted(_ITEM_CALLS))
@pytest.mark.parametrize("key", [_KEY_TO_SENTINEL_DIR, _KEY_TO_SENTINEL_FILE])
def test_a_traversal_key_is_refused_before_anything_is_read(root, no_filesystem, verb, key) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(OpsError) as caught:
        _ITEM_CALLS[verb](ctx, _PROJECT, key)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "resolve_item.key_invalid"
    assert "'key'" in triple["message"]


@pytest.mark.parametrize("verb", sorted(_ITEM_CALLS))
@pytest.mark.parametrize("key", [_KEY_TO_SENTINEL_DIR, _KEY_TO_SENTINEL_FILE])
def test_a_traversal_key_changes_nothing_on_disk(root, outer, verb, key) -> None:
    before = _snapshot(outer)

    with pytest.raises(OpsError) as caught:
        _ITEM_CALLS[verb](_ctx(root), _PROJECT, key)

    assert _contract(caught.value)["error"] == "resolve_item.key_invalid"
    assert _snapshot(outer) == before


@pytest.mark.parametrize(
    "key",
    ["**", "*", "CODER-2026*", "CODER-?", "[C]ODER", "/etc/passwd", "/", "-rf", "a\nb", ".", ".."],
    ids=["double-star", "star", "trailing-star", "question", "bracket", "absolute", "root",
         "leading-dash", "newline", "dot", "dotdot"],
)
def test_a_glob_absolute_or_flag_shaped_key_is_refused_without_a_walk(root, outer, no_filesystem, key) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(OpsError) as caught:
        ops_resolve.resolve_item(ctx, _PROJECT, key)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "resolve_item.key_invalid"


@pytest.mark.parametrize("project", ["..", "../..", "../outer", "/", "-x", "a b\n"])
def test_a_traversal_project_is_refused_before_any_path_is_built(root, no_filesystem, project) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(OpsError) as caught:
        ops_resolve.resolve_item(ctx, project, _TASK)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "resolve_item.project_invalid"
    assert "'project'" in triple["message"]


def test_a_symlink_out_of_the_project_tree_is_refused_before_it_is_read(root, outer) -> None:
    link = root / "projects" / _PROJECT / "tasks" / "CODER-20260101-002-link"
    link.symlink_to(outer / "sentinel_dir", target_is_directory=True)
    before = _snapshot(outer)

    for verb in sorted(_ITEM_CALLS):
        with pytest.raises(OpsError) as caught:
            _ITEM_CALLS[verb](_ctx(root), _PROJECT, "CODER-20260101-002")
        triple = _contract(caught.value)
        assert triple["error"] == "resolve_item.path_outside_tree", verb
        assert str(outer / "sentinel_dir") in triple["message"]
        assert triple["remedy"].startswith("ls -ld ")

    assert _snapshot(outer) == before


def test_a_symlinked_intake_file_out_of_the_project_is_refused(root, outer) -> None:
    (root / "projects" / _PROJECT / "bugs" / "BUG-0001-x.md").symlink_to(outer / "sentinel.md")

    with pytest.raises(OpsError) as caught:
        ops_write.delete_item(_ctx(root), _PROJECT, "BUG-0001", force=True)

    assert _contract(caught.value)["error"] == "resolve_item.path_outside_tree"
    assert (outer / "sentinel.md").read_text(encoding="utf-8") == _SENTINEL_TEXT


def test_the_prefix_glob_matches_the_key_literally(tmp_path) -> None:
    (tmp_path / "a[1]-x").mkdir()
    (tmp_path / "a1-x").mkdir()

    matches = ops_resolve._collect_prefix_matches(tmp_path, "a[1]", suffix="", is_dir=True)

    assert matches == [tmp_path / "a[1]-x"]


def test_valid_keys_resolve_and_close_exactly_as_before(root) -> None:
    project = root / "projects" / _PROJECT
    (project / "bugs" / "BUG-0042-thing.md").write_text("# BUG-0042\n\n## Status\nopen\n", encoding="utf-8")
    ctx = _ctx(root)

    assert ops_resolve.resolve_item(ctx, _PROJECT, _TASK).path == project / "tasks" / _TASK
    assert ops_resolve.resolve_item(ctx, _PROJECT, "CODER-20260101-001").path == project / "tasks" / _TASK
    assert ops_resolve.resolve_item(ctx, _PROJECT, "BUG-0042").item_type == "bug"

    ops_write.close_item(ctx, _PROJECT, "BUG-0042")
    assert ops_resolve.resolve_item(ctx, _PROJECT, "BUG-0042").state == "done"


def test_an_item_inside_a_symlinked_project_root_still_resolves(root, outer) -> None:
    # The confinement compares realpaths, so a project directory that is
    # itself a link to storage elsewhere keeps working.
    storage = outer / "storage"
    shutil.move(str(root / "projects" / _PROJECT), str(storage))
    (root / "projects" / _PROJECT).symlink_to(storage, target_is_directory=True)

    result = ops_resolve.resolve_item(_ctx(root), _PROJECT, _TASK)

    assert result.state == "DONE"


@pytest.mark.parametrize("key", [_KEY_TO_SENTINEL_DIR, "**", "/etc"])
def test_the_resolve_cli_shim_refuses_the_same_keys(root, capsys, key) -> None:
    code = ops_resolve._cli_main([str(root / "projects" / _PROJECT), key])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert _contract(captured.err.strip())["error"] == "resolve_item.key_invalid"


def test_the_resolve_cli_shim_refuses_a_symlink_out_of_the_tree(root, outer, capsys) -> None:
    (root / "projects" / _PROJECT / "tasks" / "CODER-20260101-002-link").symlink_to(outer / "sentinel_dir")

    code = ops_resolve._cli_main([str(root / "projects" / _PROJECT), "CODER-20260101-002"])

    assert code == 1
    assert _contract(capsys.readouterr().err.strip())["error"] == "resolve_item.path_outside_tree"


# ---------------------------------------------------------------------------
# 2. The registry verbs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["..", "../..", "../outer", ".", "-x", "a/b"])
def test_remove_project_with_a_traversal_name_refuses_before_any_filesystem_access(
    root, no_filesystem, name
) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(ops_projects.ProjectValueRefused) as caught:
        ops_projects.remove_project(ctx, name, force=True)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "remove_project.project_invalid"
    assert caught.value.field == "project"
    assert ops_projects.projects_exit_code(caught.value) == 1


def test_remove_project_dotdot_force_leaves_the_kanban_root_and_outside_intact(root, outer) -> None:
    before = _snapshot(outer)

    with pytest.raises(ops_projects.ProjectValueRefused):
        ops_projects.remove_project(_ctx(root), "..", force=True)

    assert _snapshot(outer) == before


@pytest.mark.parametrize("name", ["../x", "../../outer", "..", "a.b", "a b", "x\n[project:evil]"])
def test_add_project_with_a_traversal_name_changes_neither_registry_nor_outside(
    root, outer, name
) -> None:
    (outer / "x").mkdir()
    before = _snapshot(outer)

    with pytest.raises(ops_projects.ProjectValueRefused) as caught:
        ops_projects.add_project(_ctx(root), name)

    triple = _contract(caught.value)
    assert triple["error"] == "add_project.project_invalid"
    assert _snapshot(outer) == before


def test_add_project_refuses_before_any_filesystem_access(root, no_filesystem) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(ops_projects.ProjectValueRefused):
        ops_projects.add_project(ctx, "../x")


@pytest.mark.parametrize("name", ["2048-game", "My_App", "a"])
def test_registrable_names_still_add_and_remove(root, capsys, name) -> None:
    (root / "projects" / name).mkdir()

    ops_projects.add_project(_ctx(root), name)
    assert f"[project:{name}]" in (root / "projects.cfg").read_text(encoding="utf-8")

    ops_projects.remove_project(_ctx(root), name, force=True)
    assert f"[project:{name}]" not in (root / "projects.cfg").read_text(encoding="utf-8")
    assert not (root / "projects" / name).exists()


def test_a_path_shaped_workflow_type_is_refused_even_with_a_ready_plugin_planted_there(root, outer) -> None:
    (root / "workflows" / "release").mkdir(parents=True)
    (root / "workflows" / "release" / "workflow.cfg").write_text(_READY_PLUGIN_CFG, encoding="utf-8")
    (root / "workflows" / "release" / "workflow.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    # From <root>/workflows, '../../tmp/x' is <outer>/tmp/x: the planted plugin.
    assert (root / "workflows" / ".." / ".." / "tmp" / "x" / "workflow.sh").is_file()
    before = _snapshot(outer)

    with pytest.raises(ops_projects.ProjectValueRefused) as caught:
        ops_projects.create_project(_ctx(root), "alpha", workflow_type="../../tmp/x")

    triple = _contract(caught.value)
    assert triple["error"] == "create_project.workflow_type_invalid"
    assert caught.value.field == "workflow_type"
    assert _snapshot(outer) == before
    assert not (root / "projects" / "alpha").exists()


def test_the_workflow_check_never_joins_a_name_that_is_not_listed(tmp_path) -> None:
    # Called on its own (no create_project value check in front of it), a
    # path-shaped type is still never looked up at the path it names.
    plugins = tmp_path / "kanban" / "workflows"
    (plugins / "release").mkdir(parents=True)
    (plugins / "release" / "workflow.cfg").write_text(_READY_PLUGIN_CFG, encoding="utf-8")
    (plugins / "release" / "workflow.sh").write_text("", encoding="utf-8")
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "workflow.cfg").write_text(_READY_PLUGIN_CFG, encoding="utf-8")
    (tmp_path / "x" / "workflow.sh").write_text("", encoding="utf-8")

    with pytest.raises(ops_projects.ProjectRefusal) as caught:
        ops_projects._check_workflow(str(tmp_path / "kanban"), str(tmp_path), "../../x")

    assert "unknown workflow type '../../x'; discovered ready types are: release" in str(caught.value)


def _ready_types() -> list[str]:
    return ops_projects._ready_workflow_types(str(_TEAM_DIR / "workflows"))


@pytest.mark.parametrize("workflow", _ready_types())
def test_release_and_every_other_ready_plugin_still_create_projects(root, capsys, workflow) -> None:
    assert "release" in _ready_types()

    outcome = ops_projects.create_project(_ctx(root), "alpha", workflow_type=workflow)

    assert outcome.changed
    cfg = (root / "projects" / "alpha" / "project.cfg").read_text(encoding="utf-8")
    assert f"workflow_type = {workflow}\n" in cfg


_BAD_VALUES = [
    ("dev_tree", "/srv/x\n[release]\nmax_patch = 999", "control character (U+000A)"),
    ("dev_tree", "/srv/x\rmore", "control character (U+000D)"),
    ("dev_tree", "srv/relative", "is not an absolute path"),
    ("dev_tree", "./here", "is not an absolute path"),
    ("dev_tree", "/srv/../etc", "contains a '..' segment"),
    ("git_repo", "git@example.com:x.git\nbranch_prefix = evil", "control character (U+000A)"),
    ("git_repo", "u\x1b[31m", "control character (U+001B)"),
    ("git_remote", "origin\npush_to_remote = true", "control character (U+000A)"),
    ("git_remote", "origin\tx", "control character (U+0009)"),
]


@pytest.mark.parametrize("field, value, reason", _BAD_VALUES)
def test_a_project_cfg_value_that_could_inject_keys_is_refused_and_writes_no_project(
    root, outer, field, value, reason
) -> None:
    before = _snapshot(outer)

    with pytest.raises(ops_projects.ProjectValueRefused) as caught:
        ops_projects.create_project(_ctx(root), "alpha", **{field: value})

    triple = _contract(caught.value)
    assert triple["error"] == f"create_project.{field}_invalid"
    assert f"'{field}'" in triple["message"]
    assert reason in triple["message"]
    assert caught.value.field == field
    assert ops_projects.projects_exit_code(caught.value) == 1
    assert _snapshot(outer) == before


def test_the_value_checks_run_before_any_filesystem_access(root, no_filesystem) -> None:
    ctx = _ctx(root)
    with no_filesystem(), pytest.raises(ops_projects.ProjectValueRefused):
        ops_projects.create_project(ctx, "alpha", dev_tree="/x\n[release]")


def test_valid_path_values_are_still_written_verbatim(root, capsys) -> None:
    ops_projects.create_project(
        _ctx(root), "alpha", dev_tree="/srv/develop/my..app", git_repo="git@example.com:o/a.git",
        git_remote="upstream",
    )

    cfg = (root / "projects" / "alpha" / "project.cfg").read_text(encoding="utf-8")
    assert "dev_tree_path = /srv/develop/my..app\n" in cfg
    assert "git_repo_url = git@example.com:o/a.git\n" in cfg
    assert "git_remote_name = upstream\n" in cfg


# ---------------------------------------------------------------------------
# 3. cancel-rc
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("project", ["../..", "..", "--operator-approved", "a\nb", "*"])
def test_cancel_rc_refuses_an_unchecked_project_before_reading_config_or_running_git(
    root, outer, monkeypatch, project
) -> None:
    # A project.cfg at the traversal target would choose the repository.
    (outer / "project.cfg").write_text(f"[project]\ndev_tree_path = {outer}\n", encoding="utf-8")

    def _fail(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"cancel_rc read config or ran git for a refused project: {args!r}")

    monkeypatch.setattr(cancel._push_decision, "resolve_push_decision", _fail)
    monkeypatch.setattr(cancel.ini, "read_line_value", _fail)
    monkeypatch.setattr(cancel, "_read_cfg_key", _fail)
    monkeypatch.setattr(cancel, "_project_cfg_file", _fail)
    monkeypatch.setattr(cancel, "_run_git", _fail)
    monkeypatch.setattr(subprocess, "run", _fail)
    before = _snapshot(outer)

    with pytest.raises(cancel.CancelRcError) as caught:
        cancel.cancel_rc(_ctx(root), project, "v1.2.3", yes=True)

    assert caught.value.error == "cancel_rc.project_invalid"
    assert cancel.cancel_exit_code(caught.value) == 1
    assert _contract(caught.value.text.strip())["error"] == "cancel_rc.project_invalid"
    assert _snapshot(outer) == before


# ---------------------------------------------------------------------------
# 4. The CLI scripts reach the same refusals
# ---------------------------------------------------------------------------


def _run(tmp_path: pathlib.Path, root: pathlib.Path, script: str, *args: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", str(_SCRIPTS / script), *args],
        capture_output=True, text=True, env=env, cwd=str(root),
        stdin=subprocess.DEVNULL, timeout=120, check=False,
    )


@pytest.mark.parametrize(
    "script, args, error",
    [
        ("delete.sh", ["--project", _PROJECT, "--key", _KEY_TO_SENTINEL_DIR, "--force"], "resolve_item.key_invalid"),
        ("close.sh", ["--project", _PROJECT, "--key", _KEY_TO_SENTINEL_DIR], "resolve_item.key_invalid"),
        ("remove-project.sh", ["--project", "..", "--force"], "remove_project.project_invalid"),
        ("add-project.sh", ["--project", "../x"], "add_project.project_invalid"),
        ("create-project.sh", ["--project", "alpha", "--workflow-type", "../../tmp/x"],
         "create_project.workflow_type_invalid"),
        ("create-project.sh", ["--project", "alpha", "--dev-tree", "relative/path"],
         "create_project.dev_tree_invalid"),
        ("cm/cancel-rc.sh", ["--project", "../..", "--key", "v1.2.3", "--yes"], "cancel_rc.project_invalid"),
    ],
    ids=["delete", "close", "remove-project", "add-project", "create-workflow", "create-dev-tree", "cancel-rc"],
)
def test_the_cli_scripts_refuse_through_the_ops_functions(tmp_path, root, outer, script, args, error) -> None:
    (outer / "x").mkdir()
    before = _snapshot(outer)

    run = _run(tmp_path, root, script, *args)

    assert run.returncode == 1, (run.stdout, run.stderr)
    assert f"error:   {error}" in run.stderr, run.stderr
    after = {path: blob for path, blob in _snapshot(outer).items() if not path.startswith("kanban/tmp")}
    assert after == {path: blob for path, blob in before.items() if not path.startswith("kanban/tmp")}
