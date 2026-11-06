"""
test_dev_tree_resolver_agreement.py
===================================
One dev_tree_path resolver, two tiers, every reader agreeing.

A sandbox kanban root holds three projects, each relying on one step of the
chain project.cfg_example documents:

  * ``own``  -- names its own ``[project] dev_tree_path``;
  * ``env``  -- names none; ``PGAI_DEV_TREE_PATH`` is set;
  * ``kcfg`` -- names none; the environment is clear; ``kanban.cfg [paths]
    dev_tree_path`` names the tree.

The chain readers -- the Python resolver, reset's worktree check
(ops.write), block requeue (overwatch.block_requeue), the RC-state checks
(ops.rc_state), ship-rc and unwind-rc (cm.ship, cm.unwind), the PM
materializer, and in bash pp_resolve_dev_tree and OVERWATCH's
overwatch_dev_tree -- all answer the same tree for every project.

load_config (config_loader.sh) exports the global tier as PGAI_DEV_TREE_PATH,
and that export is resolve_global_dev_tree's answer for the same kanban.cfg.

The project-tier readers -- project_dev_tree, bash pp_dev_tree_path, the
project summary's release-notes lookup, and the version-of-record reader
(lib.released_version) -- answer the project's own tree for ``own`` and
nothing for the other two: a project naming no repository of its own never
reads another project's release history.

The two last-released resolvers -- bash pp_last_released_version and Python
released_version.last_released_version -- give the same answer on a git dev
tree holding no tag that matches the branch prefix: release-state.md's
``## Last Released`` when it is a valid vX.Y.Z, else v0.0.0, and the matching
tag whenever one exists.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.cm import ship, unwind
from pgai_agent_kanban.lib import dev_tree, released_version
from pgai_agent_kanban.lib.dev_tree import __main__ as dev_tree_cli
from pgai_agent_kanban.ops import rc_state, write
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.overwatch import block_requeue
from pgai_agent_kanban.reports import project_summary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

try:
    import pm_agent.pm_materialize as pm
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    import pm_materialize as pm  # type: ignore[no-redef]

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROJECT_PATHS = _TEAM_DIR / "scripts" / "lib" / "project_paths.sh"
_OVERWATCH_CONFIG = _TEAM_DIR / "scripts" / "lib" / "overwatch_config.sh"
_DEV_TREE_SH = _TEAM_DIR / "scripts" / "lib" / "dev_tree.sh"
_CONFIG_LOADER = _TEAM_DIR / "scripts" / "lib" / "config_loader.sh"

_PROJECTS = ("own", "env", "kcfg")


@pytest.fixture
def sandbox(tmp_path: pathlib.Path):
    root = tmp_path / "kanban"
    trees = {name: tmp_path / f"tree-{name}" for name in _PROJECTS}
    for tree in trees.values():
        tree.mkdir()
    (root / "projects.cfg").parent.mkdir(parents=True)
    (root / "projects.cfg").write_text(
        "".join(f"[project:{name}]\npriority={i}\n\n" for i, name in enumerate(_PROJECTS, 1)), encoding="utf-8"
    )
    for name in _PROJECTS:
        own = f"dev_tree_path = {trees['own']}\n" if name == "own" else "dev_tree_path =\n"
        project = root / "projects" / name
        project.mkdir(parents=True)
        (project / "project.cfg").write_text(
            f"[project]\nproject_name = {name}\nworkflow_type = release\nbranch_prefix = ai_\n{own}", encoding="utf-8"
        )
    return root, trees


def _env_for(name: str, root: pathlib.Path, trees: dict) -> dict[str, str]:
    """The environment and kanban.cfg each project's case runs under."""
    if name == "kcfg":
        (root / "kanban.cfg").write_text(f"[paths]\ndev_tree_path = {trees['kcfg']}\n", encoding="utf-8")
    else:
        (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    return {"PGAI_DEV_TREE_PATH": str(trees["env"])} if name == "env" else {}


def _bash(root: pathlib.Path, tmp_path: pathlib.Path, env: dict, script: str) -> str:
    result = subprocess.run(
        ["bash", "-c", script],
        env=build_scrubbed_env(
            home_dir=tmp_path, extra_env={"KANBAN_ROOT": str(root), "PGAI_AGENT_KANBAN_ROOT_PATH": str(root), **env}
        ),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("name", _PROJECTS)
def test_every_chain_reader_answers_the_same_tree(sandbox, tmp_path, monkeypatch, name) -> None:
    root, trees = sandbox
    env = _env_for(name, root, trees)
    monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    project_root = root / "projects" / name
    ctx = OpsContext(kanban_root=root)
    expected = str(trees[name])

    answers = {
        "lib.dev_tree.resolve_dev_tree": dev_tree.resolve_dev_tree(root, project_root),
        "ops.write reset": str(write._read_dev_tree_path(project_root)),
        "overwatch.block_requeue": str(block_requeue.configured_dev_tree(project_root)),
        "ops.rc_state": rc_state._dev_tree(root, project_root / "project.cfg", dict(env)),
        "cm.ship": ship._repo_root(ctx, name),
        "cm.unwind": unwind._repo_root(ctx, name),
        "pm_materialize": pm._resolve_dev_tree_path(str(project_root)),
        "bash pp_resolve_dev_tree": _bash(
            root, tmp_path, env, f"source '{_PROJECT_PATHS}'; pp_resolve_dev_tree {name}"
        ),
        "bash overwatch_dev_tree": _bash(
            root, tmp_path, env, f"source '{_OVERWATCH_CONFIG}'; overwatch_dev_tree {name}"
        ),
    }

    assert answers == {reader: expected for reader in answers}


@pytest.mark.parametrize("name", _PROJECTS)
def test_project_tier_readers_never_borrow_another_tree(sandbox, tmp_path, monkeypatch, name) -> None:
    root, trees = sandbox
    env = _env_for(name, root, trees)
    monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    project_root = root / "projects" / name
    expected = str(trees["own"]) if name == "own" else ""
    summary_tree = project_summary._dev_tree_path(project_root)

    answers = {
        "lib.dev_tree.project_dev_tree": dev_tree.project_dev_tree(project_root),
        "reports.project_summary": str(summary_tree) if summary_tree else "",
        "bash pp_dev_tree_path": _bash(root, tmp_path, env, f"source '{_PROJECT_PATHS}'; pp_dev_tree_path {name}"),
    }

    assert answers == {reader: expected for reader in answers}
    # The version of record reads no tree it does not own: a project with no
    # tree of its own falls back to release-state, here nothing released.
    if name != "own":
        assert released_version.last_released_version(root, name) == released_version.NOTHING_RELEASED


def test_global_tier_prefers_the_environment_over_kanban_cfg(tmp_path) -> None:
    (tmp_path / "kanban.cfg").write_text("[paths]\ndev_tree_path = '/from/cfg'\n", encoding="utf-8")

    assert dev_tree.global_dev_tree(tmp_path, {"PGAI_DEV_TREE_PATH": "/from/env"}) == "/from/env"
    assert dev_tree.global_dev_tree(tmp_path, {}) == "/from/cfg"
    assert dev_tree.global_dev_tree(tmp_path / "absent", {}) == ""


def test_global_tier_reads_an_explicit_kanban_cfg(tmp_path) -> None:
    other = tmp_path / "elsewhere.cfg"
    other.write_text("[paths]\ndev_tree_path = /from/other\n", encoding="utf-8")

    assert dev_tree.global_dev_tree(tmp_path, {}, kanban_cfg=other) == "/from/other"
    assert dev_tree.global_dev_tree(tmp_path, {}, kanban_cfg=tmp_path / "absent.cfg") == ""


def test_command_line_prints_the_global_tier(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "kanban.cfg").write_text("[paths]\ndev_tree_path = \"/from/cfg\"\n", encoding="utf-8")
    monkeypatch.delenv("PGAI_DEV_TREE_PATH", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(tmp_path))

    assert dev_tree_cli.main(["global"]) == 0
    assert capsys.readouterr().out == "/from/cfg"
    assert dev_tree_cli.main(["global", "--kanban-cfg", str(tmp_path / "absent.cfg")]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("env", "cfg_text", "explicit_cfg"),
    [
        ({"PGAI_DEV_TREE_PATH": "/from/env"}, "[paths]\ndev_tree_path = /from/cfg\n", False),
        ({}, "[paths]\ndev_tree_path = '/from/cfg'\n", False),
        ({}, "[paths]\ndev_tree_path = /from/cfg  # inline note\n", True),
        ({}, "[paths]\n", False),
    ],
)
def test_bash_global_tier_answers_what_the_python_resolver_answers(tmp_path, env, cfg_text, explicit_cfg) -> None:
    """resolve_global_dev_tree (lib/dev_tree.sh) runs the Python resolver, so
    the two agree on every input, quoted and commented values included."""
    root = tmp_path / "kanban"
    root.mkdir()
    cfg = (tmp_path / "explicit.cfg") if explicit_cfg else (root / "kanban.cfg")
    cfg.write_text(cfg_text, encoding="utf-8")
    call = f"resolve_global_dev_tree '{cfg}'" if explicit_cfg else "resolve_global_dev_tree"

    answer = _bash(root, tmp_path, env, f"source '{_DEV_TREE_SH}'; {call}")

    assert answer == dev_tree.global_dev_tree(root, env, kanban_cfg=cfg if explicit_cfg else None)


def _load_config_dev_tree(root: pathlib.Path, tmp_path: pathlib.Path, env: dict, cfg: pathlib.Path) -> str:
    """PGAI_DEV_TREE_PATH as load_config exports it for *cfg*."""
    env = {"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"), **env}
    return _bash(
        root, tmp_path, env, f"source '{_CONFIG_LOADER}' && load_config '{cfg}' && printf '%s' \"$PGAI_DEV_TREE_PATH\""
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param('dev_tree_path = "/srv/dev/tree"\n', "/srv/dev/tree", id="quoted"),
        pytest.param("dev_tree_path = /srv/dev/tree\n", "/srv/dev/tree", id="unquoted"),
        pytest.param("dev_tree_path = /srv/dev/tree  # inline note\n", "/srv/dev/tree", id="inline-comment"),
        pytest.param("# dev_tree_path = /srv/dev/tree\n", "", id="commented-out"),
    ],
)
def test_load_config_exports_what_the_global_resolver_answers(tmp_path, line, expected) -> None:
    root = tmp_path / "kanban"
    root.mkdir()
    cfg = root / "kanban.cfg"
    cfg.write_text(f"[paths]\n{line}", encoding="utf-8")

    exported = _load_config_dev_tree(root, tmp_path, {}, cfg)
    resolved = _bash(root, tmp_path, {}, f"source '{_DEV_TREE_SH}'; resolve_global_dev_tree '{cfg}'")

    assert exported == resolved == expected


def _loader_without_pp_run_ops(tmp_path: pathlib.Path) -> pathlib.Path:
    """A copy of config_loader.sh and the libs it sources, without pp_run_ops.sh:
    neither the dev-tree resolver nor the [wake] default accessors can run."""
    lib = tmp_path / "lib"
    lib.mkdir()
    for name in ("config_loader.sh", "ini_parser.sh", "error_contract.sh", "refusal_ledger.sh", "temp.sh"):
        (lib / name).write_text((_CONFIG_LOADER.parent / name).read_text(encoding="utf-8"), encoding="utf-8")
    return lib / "config_loader.sh"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        pytest.param({}, "", id="configured-value-warned"),
        pytest.param({"PGAI_DEV_TREE_PATH": "/from/env"}, "/from/env", id="environment-stands"),
    ],
)
def test_load_config_names_a_dev_tree_it_could_not_resolve(tmp_path, env, expected) -> None:
    """When the resolver cannot run, an exported PGAI_DEV_TREE_PATH stands as
    given, and a configured [paths] dev_tree_path is named in a WARNING rather
    than dropped without a trace; load_config still loads."""
    root = tmp_path / "kanban"
    root.mkdir()
    cfg = root / "kanban.cfg"
    cfg.write_text('[paths]\ndev_tree_path = "/srv/dev/tree"\n', encoding="utf-8")
    loader = _loader_without_pp_run_ops(tmp_path)
    wake = {"MAX_RUNTIME_SECONDS": "14400", "MAX_TASK_SECONDS": "5400"}
    script = f"source '{loader}' && load_config '{cfg}' && printf 'TREE=[%s]' \"$PGAI_DEV_TREE_PATH\""

    result = subprocess.run(
        ["bash", "-c", script],
        env=build_scrubbed_env(
            home_dir=tmp_path,
            extra_env={
                "KANBAN_ROOT": str(root),
                "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
                "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
                **wake,
                **env,
            },
        ),
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"TREE=[{expected}]" in result.stdout, result.stdout
    warned = [line for line in result.stderr.splitlines() if "WARNING: [paths] dev_tree_path" in line]
    if expected:
        assert not warned, result.stderr
    else:
        assert len(warned) == 1, result.stderr
        assert str(cfg) in warned[0] and "pgai_agent_kanban.lib.dev_tree global" in warned[0], warned[0]


# ---------------------------------------------------------------------------
# Last-released resolvers: bash and Python agree on a tree with no matching tag
# ---------------------------------------------------------------------------

_RELEASE_STATE = "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"


_GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=Test", "-c", "commit.gpgsign=false")


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _released_project(tmp_path: pathlib.Path, tags: tuple[str, ...], state_text: str | None) -> pathlib.Path:
    """A sandbox root with one ``ai_``-prefixed project on a throwaway git tree; returns the root."""
    root = tmp_path / "kanban"
    tree = tmp_path / "tree"
    tree.mkdir()
    _git(tree, "init", "-q", "-b", "ai_main")
    (tree / "README.md").write_text("x\n", encoding="utf-8")
    _git(tree, "add", ".")
    _git(tree, "commit", "-q", "-m", "initial")
    for tag in tags:
        _git(tree, "tag", "-a", tag, "-m", tag)
    project = root / "projects" / "released"
    project.mkdir(parents=True)
    (project / "project.cfg").write_text(
        f"[project]\nproject_name = released\nworkflow_type = release\nbranch_prefix = ai_\ndev_tree_path = {tree}\n",
        encoding="utf-8",
    )
    if state_text is not None:
        (project / "release-state.md").write_text(state_text, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("tags", "state_text", "expected"),
    [
        pytest.param(("v9.9.9",), _RELEASE_STATE + "## Last Released\nv3.5.0\n", "v3.5.0", id="no-tag-state-v3.5.0"),
        pytest.param(("v9.9.9",), None, "v0.0.0", id="no-tag-no-state-file"),
        pytest.param(("v9.9.9",), _RELEASE_STATE, "v0.0.0", id="no-tag-no-heading"),
        pytest.param(("v9.9.9",), _RELEASE_STATE + "## Last Released\nnone\n", "v0.0.0", id="no-tag-state-none"),
        pytest.param(("ai_v1.5.0",), _RELEASE_STATE + "## Last Released\nv3.5.0\n", "v1.5.0", id="matching-tag-wins"),
    ],
)
def test_last_released_resolvers_agree_without_a_matching_tag(
    tmp_path, monkeypatch, tags, state_text, expected
) -> None:
    root = _released_project(tmp_path, tags, state_text)
    monkeypatch.setenv("PGAI_GIT_LOCAL_ONLY", "1")

    script = f"source '{_PROJECT_PATHS}'; pp_last_released_version released"

    answers = {
        "bash pp_last_released_version": _bash(root, tmp_path, {"PGAI_GIT_LOCAL_ONLY": "1"}, script),
        "lib.released_version.last_released_version": released_version.last_released_version(root, "released"),
    }

    assert answers == {reader: expected for reader in answers}
