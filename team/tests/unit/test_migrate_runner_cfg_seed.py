"""
test_migrate_runner_cfg_seed.py
===============================
Unit tests for team/scripts/migrations/migrate_v2.53.1_to_v2.53.2.sh and
the module it calls, pgai_agent_kanban.lib.cfg_key_seed.

The migration seeds the kanban's own [testing] runner commands into the
project.cfg of each registered project whose dev tree is the kanban's own
source tree -- the repository the script sits in -- whatever name it is
registered under, only where the keys are absent, and writes no other file.
Each test builds a throwaway kanban root under tmp_path, runs the real
script with an explicit minimal environment pointing every root variable at
it, and reads the files back. Unless a case says otherwise the root's
kanban.cfg names this repository as the configured dev tree, as an install
built from it does, so a project with no dev_tree_path of its own resolves
to it.

Test names describe behavior; the script's own file name carries versions
by the migrations naming convention (team/scripts/migrations/README.md).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import cfg_key_seed  # noqa: E402
from tests.fixtures.binary_guard import require_binary  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

try:
    import pm_agent.pm_materialize as pm
except ImportError:
    sys.path.insert(0, str(_TEAM_DIR / "pm-agent"))
    import pm_materialize as pm  # type: ignore[no-redef]

_MIGRATION = _TEAM_DIR / "scripts" / "migrations" / "migrate_v2.53.1_to_v2.53.2.sh"
# The kanban's own source tree: the repository the migration script sits in.
_SOURCE_TREE = _TEAM_DIR.parent
_KANBAN_PROJECT = "pgai-agent-kanban"

KANBAN_TARGETED = (
    "bash team/scripts/run-unit-tests.sh --files {files} && "
    "bash team/scripts/run-integration-tests.sh --files {files}"
)
KANBAN_PRE_MERGE = "bash team/scripts/run-unit-tests.sh --pre-merge"

_KANBAN_CFG = """\
[project]
project_name = pgai-agent-kanban
workflow_type = release

[debug]
verbose_mode = false
"""

_OTHER_CFG = """\
[project]
project_name = my-app
dev_tree_path = /srv/develop/my-app

[training]
reasoning_trace = false
"""


def _own_tree_cfg(name: str, dev_tree: pathlib.Path) -> str:
    """A project.cfg naming *dev_tree* as the project's own dev tree."""
    return f"[project]\nproject_name = {name}\ndev_tree_path = {dev_tree}\n"


def _git_repo(path: pathlib.Path) -> pathlib.Path:
    """An initialised git repository at *path* with one empty commit."""
    git = require_binary("git", label="migration-dev-tree-match").path
    path.mkdir(parents=True)
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], [*ident, "commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run([git, "-C", str(path), *args], check=True, capture_output=True, timeout=60)
    return path


def _build_root(tmp_path: pathlib.Path, projects: dict, configured_dev_tree=_SOURCE_TREE) -> pathlib.Path:
    """A kanban root registering *projects* ({name: project.cfg text or None}).

    *configured_dev_tree* is written as kanban.cfg [paths] dev_tree_path;
    None leaves the key out, so only a project's own dev_tree_path counts.
    """
    root = tmp_path / "kanban_root"
    (root / "logs").mkdir(parents=True)
    paths = "[paths]\n" + (f"dev_tree_path = {configured_dev_tree}\n" if configured_dev_tree else "")
    (root / "kanban.cfg").write_text(paths, encoding="utf-8")
    registry = "".join(f"[project:{name}]\npriority={i}\n\n" for i, name in enumerate(projects, 1))
    (root / "projects.cfg").write_text(registry, encoding="utf-8")
    for name, text in projects.items():
        project_dir = root / "projects" / name
        project_dir.mkdir(parents=True)
        if text is not None:
            (project_dir / "project.cfg").write_text(text, encoding="utf-8")
    return root


def _cfg(root: pathlib.Path, name: str) -> pathlib.Path:
    return root / "projects" / name / "project.cfg"


def _run(
    root: pathlib.Path,
    tmp_path: pathlib.Path,
    *args: str,
    env_extra=None,
    script: pathlib.Path = _MIGRATION,
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
            **(env_extra or {}),
        },
    )
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )


def _ok(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return result.stdout


def test_seeds_the_kanban_project_and_leaves_the_other_project_byte_identical(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG, "my-app": _OTHER_CFG})
    other_before = _cfg(root, "my-app").read_bytes()

    out = _ok(_run(root, tmp_path))

    kanban_after = _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8")
    assert kanban_after == (
        _KANBAN_CFG
        + "\n[testing]\n"
        + f"targeted_test_command = {KANBAN_TARGETED}\n"
        + f"pre_merge_command = {KANBAN_PRE_MERGE}\n"
    )
    assert _cfg(root, "my-app").read_bytes() == other_before
    assert "[seeded]  pgai-agent-kanban: added [testing] targeted_test_command" in out


def test_second_run_changes_nothing(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG, "my-app": _OTHER_CFG})
    _ok(_run(root, tmp_path))
    first = {name: _cfg(root, name).read_bytes() for name in (_KANBAN_PROJECT, "my-app")}

    out = _ok(_run(root, tmp_path))

    assert {name: _cfg(root, name).read_bytes() for name in first} == first
    assert "[seeded]" not in out
    assert out.count("already set") == 2


def test_seeded_values_give_the_kanban_tickets_todays_criteria(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG})
    _ok(_run(root, tmp_path))

    config = pm.resolve_test_runner_config(str(root / "projects" / _KANBAN_PROJECT))
    assert config.targeted_criterion() == (
        "`bash team/scripts/run-unit-tests.sh --files <the test files this ticket "
        "adds or changes>` (and the integration runner the same way, if integration "
        "tests were touched) exits 0."
    )
    assert config.pre_merge_criterion() == ("`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging.")


def test_adds_keys_to_an_existing_testing_section_after_its_last_key(tmp_path) -> None:
    text = "[testing]\nsomething_else = 1\n\n[debug]\nverbose_mode = false\n"
    root = _build_root(tmp_path, {_KANBAN_PROJECT: text})
    _ok(_run(root, tmp_path))
    assert _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8") == (
        "[testing]\nsomething_else = 1\n"
        f"targeted_test_command = {KANBAN_TARGETED}\n"
        f"pre_merge_command = {KANBAN_PRE_MERGE}\n"
        "\n[debug]\nverbose_mode = false\n"
    )


def test_a_key_already_present_is_kept_even_when_blank(tmp_path) -> None:
    text = "[testing]\npre_merge_command =\n# targeted_test_command = commented, not set\n"
    root = _build_root(tmp_path, {_KANBAN_PROJECT: text})
    _ok(_run(root, tmp_path))
    after = _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8")
    assert after == (
        "[testing]\npre_merge_command =\n"
        f"targeted_test_command = {KANBAN_TARGETED}\n"
        "# targeted_test_command = commented, not set\n"
    )


@pytest.mark.parametrize(
    "projects",
    [
        {"my-app": _OTHER_CFG},
        {_KANBAN_PROJECT: None, "my-app": _OTHER_CFG},
    ],
    ids=["kanban-not-registered", "kanban-without-project-cfg"],
)
def test_writes_nothing_when_the_kanban_project_cannot_be_seeded(tmp_path, projects) -> None:
    root = _build_root(tmp_path, projects)
    other_before = _cfg(root, "my-app").read_bytes()
    # An unregistered directory of the same name is not a target either.
    stray = root / "projects" / _KANBAN_PROJECT
    stray.mkdir(parents=True, exist_ok=True)

    out = _ok(_run(root, tmp_path))

    assert "[skip]" in out
    assert _cfg(root, "my-app").read_bytes() == other_before
    assert not _cfg(root, _KANBAN_PROJECT).exists()


def test_project_flag_naming_another_project_writes_nothing(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG, "my-app": _OTHER_CFG})
    out = _ok(_run(root, tmp_path, "--project", "my-app"))
    assert _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8") == _KANBAN_CFG
    assert _cfg(root, "my-app").read_text(encoding="utf-8") == _OTHER_CFG
    assert "[skip]  my-app: dev tree /srv/develop/my-app is not the kanban's source tree" in out


def test_an_exported_project_name_does_not_narrow_the_run(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG, "my-app": _OTHER_CFG})
    _ok(_run(root, tmp_path, env_extra={"PGAI_PROJECT_NAME": "my-app"}))
    assert "targeted_test_command = " in _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8")
    assert _cfg(root, "my-app").read_text(encoding="utf-8") == _OTHER_CFG


def test_dry_run_previews_and_writes_nothing(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG})
    out = _ok(_run(root, tmp_path, "--dry-run"))
    assert _cfg(root, _KANBAN_PROJECT).read_text(encoding="utf-8") == _KANBAN_CFG
    assert out.count("[would-seed]") == 2


def test_seeding_keeps_the_file_permission_bits(tmp_path) -> None:
    root = _build_root(tmp_path, {_KANBAN_PROJECT: _KANBAN_CFG})
    _cfg(root, _KANBAN_PROJECT).chmod(0o640)
    _ok(_run(root, tmp_path))
    assert _cfg(root, _KANBAN_PROJECT).stat().st_mode & 0o777 == 0o640


def test_missing_kanban_root_is_refused(tmp_path) -> None:
    result = _run(tmp_path / "absent", tmp_path)
    assert result.returncode == 3
    assert "migration.kanban_root_missing" in result.stderr


def test_seed_module_refuses_a_missing_file(tmp_path, capsys) -> None:
    code = cfg_key_seed.main(["--cfg", str(tmp_path / "none.cfg"), "--key", "testing", "k", "v"])
    assert code == cfg_key_seed.EXIT_NOT_A_FILE
    assert "cfg_key_seed.not_a_file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Which project is the kanban's own: its dev tree, never its name
# ---------------------------------------------------------------------------


def test_seeds_the_source_tree_registered_under_any_name(tmp_path) -> None:
    self_cfg = _own_tree_cfg("kanban-self", _SOURCE_TREE)
    root = _build_root(
        tmp_path,
        {"kanban-self": self_cfg, "my-app": _OTHER_CFG},
        configured_dev_tree=None,
    )

    out = _ok(_run(root, tmp_path))

    assert _cfg(root, "kanban-self").read_text(encoding="utf-8") == (
        self_cfg
        + "\n[testing]\n"
        + f"targeted_test_command = {KANBAN_TARGETED}\n"
        + f"pre_merge_command = {KANBAN_PRE_MERGE}\n"
    )
    assert _cfg(root, "my-app").read_text(encoding="utf-8") == _OTHER_CFG
    assert "[seeded]  kanban-self: added [testing] targeted_test_command" in out
    assert "[seeded]  kanban-self: added [testing] pre_merge_command" in out


def test_a_project_on_another_git_tree_is_left_byte_identical(tmp_path) -> None:
    other_tree = _git_repo(tmp_path / "other-app")
    root = _build_root(
        tmp_path,
        {
            "kanban-self": _own_tree_cfg("kanban-self", _SOURCE_TREE),
            "other-app": _own_tree_cfg("other-app", other_tree),
        },
    )
    other_before = _cfg(root, "other-app").read_bytes()

    out = _ok(_run(root, tmp_path))

    assert _cfg(root, "other-app").read_bytes() == other_before
    assert f"[skip]  other-app: dev tree {other_tree} is not the kanban's source tree" in out
    assert "targeted_test_command = " in _cfg(root, "kanban-self").read_text(encoding="utf-8")


def test_only_another_git_tree_registered_writes_nothing(tmp_path) -> None:
    other_tree = _git_repo(tmp_path / "other-app")
    root = _build_root(
        tmp_path,
        {"other-app": _own_tree_cfg("other-app", other_tree)},
        configured_dev_tree=None,
    )
    before = _cfg(root, "other-app").read_bytes()

    out = _ok(_run(root, tmp_path, "--project", "other-app"))

    assert _cfg(root, "other-app").read_bytes() == before
    assert "[seeded]" not in out
    assert "nothing to seed" in out


def test_a_symlink_to_the_source_tree_is_the_same_tree(tmp_path) -> None:
    link = tmp_path / "kanban-link"
    link.symlink_to(_SOURCE_TREE, target_is_directory=True)
    root = _build_root(tmp_path, {"kanban-self": _own_tree_cfg("kanban-self", link)}, configured_dev_tree=None)

    out = _ok(_run(root, tmp_path))

    assert "[seeded]  kanban-self: added [testing] pre_merge_command" in out


def test_another_checkout_of_the_same_repository_is_the_same_tree(tmp_path) -> None:
    """Run from a linked worktree, as a version-targeted upgrade runs from a
    clean tag worktree: the project whose dev tree is that repository's main
    checkout is the kanban's own; a separate repository is not."""
    repo = _git_repo(tmp_path / "dev-tree")
    worktree = tmp_path / "tag-worktree"
    git = require_binary("git", label="migration-dev-tree-match").path
    subprocess.run(
        [git, "-C", str(repo), "worktree", "add", "-q", "--detach", str(worktree)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    # The worktree carries the script and links to the libraries and the
    # package it calls, laid out as the source tree lays them out.
    migrations = worktree / "team" / "scripts" / "migrations"
    migrations.mkdir(parents=True)
    script = migrations / _MIGRATION.name
    script.write_bytes(_MIGRATION.read_bytes())
    (worktree / "team" / "scripts" / "lib").symlink_to(_TEAM_DIR / "scripts" / "lib", target_is_directory=True)
    (worktree / "team" / "pgai_agent_kanban").symlink_to(_TEAM_DIR / "pgai_agent_kanban", target_is_directory=True)
    unrelated = _git_repo(tmp_path / "unrelated")
    root = _build_root(
        tmp_path,
        {
            "kanban-self": _own_tree_cfg("kanban-self", repo),
            "unrelated": _own_tree_cfg("unrelated", unrelated),
        },
        configured_dev_tree=None,
    )
    unrelated_before = _cfg(root, "unrelated").read_bytes()

    out = _ok(_run(root, tmp_path, script=script))

    assert "[seeded]  kanban-self: added [testing] targeted_test_command" in out
    assert _cfg(root, "unrelated").read_bytes() == unrelated_before
