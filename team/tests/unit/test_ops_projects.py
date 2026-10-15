"""
test_ops_projects.py — Unit tests for pgai_agent_kanban.ops.projects: the one
implementation behind team/scripts/create-project.sh, add-project.sh,
remove-project.sh and POST /operations/create-project, /add-project and
/remove-project.

Coverage:

  1. Route/CLI parity (tests/fixtures/route_cli_parity.py): each route calls
     its ops.projects function with no subprocess, each CLI script reaches
     the same function through pp_run_ops, and both surfaces leave the same
     registry bytes on their own board.
  2. CLI behaviour against the committed golden
     (tests/fixtures/golden/registry_verbs_cli.json, captured from the
     scripts before their logic moved to Python, plus the project README
     that create and add seed): for every recorded
     scenario -- create (fresh, dry run, INI / colon-legacy / --no-migrate
     registries, every flag, a fallback-template workflow, every refusal),
     add (new, update, nothing to do, colon-legacy, refusals) and remove
     (unregister only, --force, dry run, absent, colon-legacy) -- stdout,
     stderr, exit code, projects.cfg / release-state.md / queue-file bytes,
     project.cfg's settings (its section headers and active key lines; its
     comments are the project.cfg template's, covered by
     test_project_cfg_template.py) and the directory layout are unchanged.
  3. remove-project's Active-RC guard: refused with and without --force, on
     the CLI and over REST, dual-channel, exit 2, nothing changed; an Active
     RC field that does not validate is refused the same way; an Active RC
     of none lets the call through.
  4. File mode: a rewritten projects.cfg keeps its permission bits.
  5. The functions directly: refusal exit codes, the registry helpers'
     palette and priority answers, the seeded overwatch files' bytes equal
     the bash seeders', and the ops CLI's verb dispatch.
  7. A registry priority that is not a whole number -- a newline that would
     open a [project:...] section of its own above all -- is refused
     (``create_project.priority_invalid`` / ``add_project.priority_invalid``)
     by create-project.sh and add-project.sh and by the functions, and
     nothing is written.
  6. The project README (projects/<name>/README.md): create_project and
     add_project seed it byte-identical to templates/project/README-TEMPLATE.md
     when absent, never overwrite an existing one (a second create or add,
     and refresh_seeded_templates, leave an edited README byte-unchanged),
     a dry run writes none, a missing template only warns, and the create
     output tells the operator to fill it in.

Every board is a throwaway directory under tmp_path; every CLI run gets an
explicit, minimal environment (build_scrubbed_env).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib.error_contract import parse_error_contract  # noqa: E402
from pgai_agent_kanban.lib.palette import load_default_palette  # noqa: E402
from pgai_agent_kanban.ops import projects as ops_projects  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from pgai_agent_kanban.ops.errors import IoError, OpsError  # noqa: E402
from tests.fixtures.golden_board import build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_SCRIPTS = _TEAM_DIR / "scripts"
_GOLDEN_FILE = _TEAM_DIR / "tests" / "fixtures" / "golden" / "registry_verbs_cli.json"
_SEED_FILE = _TEAM_DIR / "config" / "overwatch_resolve_seed.txt"

_INI_CFG = "# registry\n[project:old]\npriority=1\ndashboard_color=#378ADD\n\n[project:two]\npriority = 4\n"
_COLON_CFG = "# legacy header\n# more\nold:1:#378ADD\ntwo:2\n"
_INI_NOCOLOR = "[project:a1]\npriority=3\n\n[project:a2]\npriority=1\n"
_RS_ACTIVE = (
    "# Release State\n\n## Active RC\nv1.2.3\n\n## RC Opened At\n2026-01-01T00:00:00Z\n\n"
    "## RC Opened By Task\nCM-1\n\n## Last Released\nnone\n"
)
_RS_NONE = "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n"
_RS_INVALID = "# Release State\n\n## Active RC\nNONE\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n"

# Workflow plugins under the kanban root, for the workflow-type refusals.
_PLUGINS = {
    "workflows/release/workflow.cfg": "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = rw\nagents = pm,coder\n",
    "workflows/release/workflow.sh": "#!/bin/bash\n",
    "workflows/scaf/workflow.cfg": "[workflow]\nstatus = scaffold\n[capabilities]\ngit_mode = rw\nagents = pm\n",
    "workflows/scaf/workflow.sh": "",
    "workflows/broken/workflow.cfg": "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = zz\nagents = pm\n",
    "workflows/broken/workflow.sh": "",
    "workflows/nosh/workflow.cfg": "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = none\nagents = pm,tester,weird\n",
    "workflows/noagents/workflow.cfg": "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = none\n",
    "workflows/noagents/workflow.sh": "",
    "workflows/custom/workflow.cfg": "[workflow]\nstatus = ready\n[capabilities]\ngit_mode = none\nagents = pm, tester,weird,writer\n",
    "workflows/custom/workflow.sh": "",
}

_DIR = None  # a seed value that makes a directory

# A registered project's own project.cfg: add-project leaves an existing one
# as it is, so these scenarios exercise the registry paths alone.
_OLD_CFG = "[project]\nproject_name = old\n"
_TWO_CFG = "[project]\nproject_name = two\n"

# name -> (seed files, script, args, extra environment)
_SCENARIOS: dict[str, tuple[dict, str, list[str], dict]] = {
    "create-fresh": ({}, "create-project.sh", ["--project", "alpha"], {}),
    "create-fresh-dry": ({}, "create-project.sh", ["--project", "alpha", "--dry-run"], {}),
    "create-ini-dry": ({"projects.cfg": _INI_CFG}, "create-project.sh", ["--project", "alpha", "--dry-run"], {}),
    "create-colon-dry": ({"projects.cfg": _COLON_CFG}, "create-project.sh", ["--project", "alpha", "--dry-run"], {}),
    "create-colon-nomig-dry": (
        {"projects.cfg": _COLON_CFG}, "create-project.sh", ["--project", "alpha", "--dry-run", "--no-migrate"], {},
    ),
    "create-ini": ({"projects.cfg": _INI_CFG}, "create-project.sh", ["--project", "alpha"], {}),
    "create-ini-nocolor": ({"projects.cfg": _INI_NOCOLOR}, "create-project.sh", ["--project", "alpha"], {}),
    "create-colon": ({"projects.cfg": _COLON_CFG}, "create-project.sh", ["--project", "alpha"], {}),
    "create-colon-nomig": ({"projects.cfg": _COLON_CFG}, "create-project.sh", ["--project", "alpha", "--no-migrate"], {}),
    "create-all-flags": (
        {"projects.cfg": _INI_CFG}, "create-project.sh",
        ["--project", "beta", "--workflow-type", "document", "--max-patch", "3", "--max-minor", "4",
         "--max-major", "1", "--git-remote", "up", "--priority", "7", "--color", "#123456",
         "--dev-tree", "/nonexistent/x", "--git-repo", "git@example:x.git"],
        {},
    ),
    "create-aliases": (
        {}, "create-project.sh",
        ["--project", "beta", "--dev-tree", "/a", "--dev-tree-path", "/tmp", "--git-repo-url", "u"], {},
    ),
    "create-gitrepo-only": ({}, "create-project.sh", ["--project", "beta", "--git-repo", "u"], {}),
    "create-testing-only": ({}, "create-project.sh", ["--project", "gamma", "--workflow-type", "testing-only"], {}),
    "create-document-dry": (
        {}, "create-project.sh", ["--project", "gamma", "--workflow-type", "document", "--dry-run"], {},
    ),
    "create-bad-int": ({}, "create-project.sh", ["--project", "alpha", "--max-minor", "x1"], {}),
    "create-bad-int-major": ({}, "create-project.sh", ["--project", "alpha", "--max-major", "-1"], {}),
    "create-bad-color-0x": ({}, "create-project.sh", ["--project", "alpha", "--color", "0x123456"], {}),
    "create-bad-color-short": ({}, "create-project.sh", ["--project", "alpha", "--color", "#abc"], {}),
    "create-bad-color-junk": ({}, "create-project.sh", ["--project", "alpha", "--color", "red"], {}),
    "create-empty-devtree": ({}, "create-project.sh", ["--project", "alpha", "--dev-tree="], {}),
    "create-no-name": ({}, "create-project.sh", [], {}),
    "create-bad-name": ({}, "create-project.sh", ["--project", "1abc"], {}),
    "create-bad-name2": ({}, "create-project.sh", ["--project", "a.b"], {}),
    "create-exists": ({"projects/alpha/x": "x\n"}, "create-project.sh", ["--project", "alpha"], {}),
    "create-unknown-wf": ({}, "create-project.sh", ["--project", "alpha", "--workflow-type", "nope"], {}),
    "create-scaffold-wf": (_PLUGINS, "create-project.sh", ["--project", "alpha", "--workflow-type", "scaf"], {}),
    "create-broken-wf": (_PLUGINS, "create-project.sh", ["--project", "alpha", "--workflow-type", "broken"], {}),
    "create-nosh-wf": (_PLUGINS, "create-project.sh", ["--project", "alpha", "--workflow-type", "nosh"], {}),
    "create-noagents-wf": (_PLUGINS, "create-project.sh", ["--project", "alpha", "--workflow-type", "noagents"], {}),
    "create-custom-wf": (_PLUGINS, "create-project.sh", ["--project", "alpha", "--workflow-type", "custom"], {}),
    "create-help": ({}, "create-project.sh", ["--help"], {}),
    "create-unknown-flag": ({}, "create-project.sh", ["--project", "a", "--bogus"], {}),
    "create-positional": ({}, "create-project.sh", ["alpha"], {}),
    "create-missing-value": ({}, "create-project.sh", ["--project", "a", "--max-minor"], {}),
    "create-env-name": ({}, "create-project.sh", [], {"PGAI_PROJECT_NAME": "envproj"}),
    "add-missing-dir": ({}, "add-project.sh", ["--project", "zed"], {}),
    "add-new-nocfg": ({"projects/zed/": _DIR}, "add-project.sh", ["--project", "zed"], {}),
    "add-new-nocfg-dry": ({"projects/zed/": _DIR}, "add-project.sh", ["--project", "zed", "--dry-run"], {}),
    "add-new-ini": ({"projects/zed/": _DIR, "projects.cfg": _INI_CFG}, "add-project.sh", ["--project", "zed"], {}),
    "add-new-ini-nocolor": (
        {"projects/zed/": _DIR, "projects.cfg": _INI_NOCOLOR}, "add-project.sh", ["--project", "zed"], {},
    ),
    "add-new-ini-prio-color": (
        {"projects/zed/": _DIR, "projects.cfg": _INI_CFG}, "add-project.sh",
        ["--project", "zed", "--priority", "9", "--color", "#ABCDEF"], {},
    ),
    "add-existing-noop": (
        {"projects/two/": _DIR, "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh", ["--project", "two"], {},
    ),
    "add-existing-same": (
        {"projects/two/": _DIR, "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh", ["--project", "two", "--priority", "4"], {},
    ),
    "add-existing-update": (
        {"projects/old/": _DIR, "projects/old/project.cfg": _OLD_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh", ["--project", "old", "--priority", "5"], {},
    ),
    "add-existing-update-color": (
        {"projects/old/": _DIR, "projects/old/project.cfg": _OLD_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh",
        ["--project", "old", "--priority", "5", "--color", "#000000"], {},
    ),
    "add-existing-update-spaced": (
        {"projects/two/": _DIR, "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh", ["--project", "two", "--priority", "5"], {},
    ),
    "add-existing-update-dry": (
        {"projects/old/": _DIR, "projects/old/project.cfg": _OLD_CFG, "projects.cfg": _INI_CFG},
        "add-project.sh",
        ["--project", "old", "--priority", "5", "--dry-run"], {},
    ),
    "add-colon": ({"projects/zed/": _DIR, "projects.cfg": _COLON_CFG}, "add-project.sh", ["--project", "zed"], {}),
    "add-colon-nomig": (
        {"projects/zed/": _DIR, "projects.cfg": _COLON_CFG}, "add-project.sh", ["--project", "zed", "--no-migrate"], {},
    ),
    "add-colon-update": (
        {"projects/two/": _DIR, "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _COLON_CFG},
        "add-project.sh", ["--project", "two", "--priority", "8"], {},
    ),
    "add-colon-update-nomig": (
        {"projects/two/": _DIR, "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _COLON_CFG},
        "add-project.sh",
        ["--project", "two", "--priority", "8", "--no-migrate"], {},
    ),
    "add-colon-dry": (
        {"projects/zed/": _DIR, "projects.cfg": _COLON_CFG}, "add-project.sh", ["--project", "zed", "--dry-run"], {},
    ),
    "add-bad-color": ({"projects/zed/": _DIR}, "add-project.sh", ["--project", "zed", "--color", "#12"], {}),
    "add-no-name": ({}, "add-project.sh", [], {}),
    "add-help": ({}, "add-project.sh", ["-h"], {}),
    "remove-plain": ({"projects/old/x.md": "x\n", "projects.cfg": _INI_CFG}, "remove-project.sh", ["--project", "old"], {}),
    "remove-last": (
        {"projects/two/x.md": "x\n", "projects.cfg": _INI_CFG + "# trailing comment\n"},
        "remove-project.sh", ["--project", "two"], {},
    ),
    "remove-force": (
        {"projects/old/x.md": "x\n", "projects.cfg": _INI_CFG}, "remove-project.sh", ["--project", "old", "--force"], {},
    ),
    "remove-force-nodir": ({"projects.cfg": _INI_CFG}, "remove-project.sh", ["--project", "old", "--force"], {}),
    "remove-dry": (
        {"projects/old/x.md": "x\n", "projects.cfg": _INI_CFG}, "remove-project.sh", ["--project", "old", "--dry-run"], {},
    ),
    "remove-force-dry": (
        {"projects/old/x.md": "x\n", "projects.cfg": _INI_CFG}, "remove-project.sh",
        ["--project", "old", "--force", "--dry-run"], {},
    ),
    "remove-absent": ({"projects.cfg": _INI_CFG}, "remove-project.sh", ["--project", "nobody"], {}),
    "remove-nocfg": ({}, "remove-project.sh", ["--project", "nobody"], {}),
    "remove-colon": (
        {"projects/two/x.md": "x\n", "projects.cfg": _COLON_CFG}, "remove-project.sh", ["--project", "two"], {},
    ),
    "remove-rc-none": (
        {"projects/old/release-state.md": _RS_NONE, "projects.cfg": _INI_CFG}, "remove-project.sh",
        ["--project", "old"], {},
    ),
    "remove-no-name": ({}, "remove-project.sh", [], {}),
    "remove-help": ({}, "remove-project.sh", ["--help"], {}),
}

_SEQUENCES = {
    "create-twice": [
        ("create-project.sh", ["--project", "p1"]),
        ("create-project.sh", ["--project", "p2"]),
        ("add-project.sh", ["--project", "p1", "--priority", "3"]),
        ("remove-project.sh", ["--project", "p1", "--force"]),
        ("create-project.sh", ["--project", "p3"]),
    ],
}

# Files whose bytes the golden records; every other path is recorded by name.
_CONTENT_RE = re.compile(r"(^projects\.cfg$|/project\.cfg$|/release-state\.md$|/tasks/queues/[^/]+\.md$)")
_STAMP_RE = re.compile(r"Generated by create-project.sh on \S+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _golden() -> dict:
    return json.loads(_GOLDEN_FILE.read_text(encoding="utf-8"))


def _seed(root: pathlib.Path, files: dict) -> None:
    (root / "tmp").mkdir(parents=True)
    for rel, content in files.items():
        path = root / rel
        if content is _DIR:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def _env(tmp_path: pathlib.Path, root: pathlib.Path, extra: dict | None = None) -> dict:
    env_extra = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
    }
    env_extra.update(extra or {})
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env=env_extra)


def _run(tmp_path, root, script, args, extra=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPTS / script), *args],
        capture_output=True, text=True, env=_env(tmp_path, root, extra),
        cwd=str(root), stdin=subprocess.DEVNULL, timeout=120,
    )


def _norm(text: str, root: pathlib.Path) -> str:
    text = text.replace(str(_TEAM_DIR), "<TREE>").replace(str(root), "<ROOT>")
    return _STAMP_RE.sub("Generated by create-project.sh on <DATE>", text)


def _cfg_settings(text: str) -> str:
    """A project.cfg's settings: its section headers and active key lines, one per line.

    The golden records these rather than the whole file: the comments are
    the project.cfg template's text, which test_project_cfg_template.py
    checks against the rendered file, so editing a template comment does
    not touch this golden.
    """
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("#", ";"))]
    return "".join(f"{line}\n" for line in lines)


def _tree(root: pathlib.Path) -> dict:
    """Every path under *root* (scratch excluded); recorded files by normalised content."""
    out: dict = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if rel == "tmp" or rel.startswith("tmp/"):
            continue
        if path.is_dir():
            out[rel + "/"] = None
        elif _CONTENT_RE.search(rel):
            text = _norm(path.read_text(encoding="utf-8"), root)
            out[rel] = _cfg_settings(text) if rel.endswith("/project.cfg") else text
        else:
            out[rel] = None
    return out


def _expected_tree(golden: dict, recorded: dict) -> dict:
    blobs = golden["blobs"]
    return {
        path: (blobs[value[len("blob:"):]] if isinstance(value, str) else None)
        for path, value in recorded.items()
    }


def _outcome(run: subprocess.CompletedProcess, root: pathlib.Path) -> dict:
    return {"rc": run.returncode, "stdout": _norm(run.stdout, root), "stderr": _norm(run.stderr, root)}


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


# ---------------------------------------------------------------------------
# 1. Route/CLI parity
# ---------------------------------------------------------------------------


def _parity_boards(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    boards = []
    for name in ("rest-board", "cli-board"):
        board = tmp_path / name
        build_synthetic_board(board)
        boards.append(board)
    return boards[0], boards[1]


def _registry(board: pathlib.Path) -> str:
    return (board / "projects.cfg").read_text(encoding="utf-8")


def test_create_project_route_and_cli_reach_ops_projects_create_project(tmp_path):
    rest_board, cli_board = _parity_boards(tmp_path)
    (cli_board / "tmp").mkdir(exist_ok=True)

    result = assert_route_cli_parity(
        "create-project",
        build_api_client(rest_board),
        json={"project": "newproj", "max_minor": 4, "color": "#123456"},
        cli_args=["--project", "newproj", "--max-minor", "4", "--color", "#123456"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.projects.create_project"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.completed.returncode == 0, result.cli.completed.stderr
    rest_out = result.response.json()["stdout"].replace(str(rest_board), "<B>")
    cli_out = result.cli.completed.stdout.replace(str(cli_board), "<B>")
    assert rest_out == cli_out
    assert _registry(rest_board) == _registry(cli_board)
    assert "[project:newproj]" in _registry(cli_board)
    configs = [
        _STAMP_RE.sub("", (b / "projects" / "newproj" / "project.cfg").read_text(encoding="utf-8")).replace(str(b), "<B>")
        for b in (rest_board, cli_board)
    ]
    assert configs[0] == configs[1]
    assert "max_minor = 4\n" in configs[0]


def test_add_project_route_and_cli_reach_ops_projects_add_project(tmp_path):
    rest_board, cli_board = _parity_boards(tmp_path)
    for board in (rest_board, cli_board):
        (board / "projects" / "adopted").mkdir(parents=True)

    result = assert_route_cli_parity(
        "add-project",
        build_api_client(rest_board),
        json={"project": "adopted", "priority": 6},
        cli_args=["--project", "adopted", "--priority", "6"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.projects.add_project"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.completed.returncode == 0, result.cli.completed.stderr
    assert result.response.json()["stdout"].replace(str(rest_board), "<B>") == (
        result.cli.completed.stdout.replace(str(cli_board), "<B>")
    )
    assert _registry(rest_board) == _registry(cli_board)
    assert "[project:adopted]\npriority=6\n" in _registry(cli_board)


def test_remove_project_route_and_cli_reach_ops_projects_remove_project(tmp_path):
    rest_board, cli_board = _parity_boards(tmp_path)
    for board in (rest_board, cli_board):
        (board / "projects" / "gone").mkdir(parents=True)
        (board / "projects" / "gone" / "README.md").write_text("kept\n", encoding="utf-8")
        with (board / "projects.cfg").open("a", encoding="utf-8") as handle:
            handle.write("\n[project:gone]\npriority=9\n")

    result = assert_route_cli_parity(
        "remove-project",
        build_api_client(rest_board),
        json={"project": "gone"},
        cli_args=["--project", "gone"],
        env=_env(tmp_path, cli_board),
        scratch_dir=tmp_path / "scratch",
    )

    assert result.target.dotted == "pgai_agent_kanban.ops.projects.remove_project"
    assert result.response.status_code == 200, result.response.text
    assert result.cli is not None and result.cli.completed.returncode == 0, result.cli.completed.stderr
    for board in (rest_board, cli_board):
        assert "[project:gone]" not in _registry(board)
        assert (board / "projects" / "gone" / "README.md").is_file(), (
            "without force the project directory stays in place"
        )
    assert _registry(rest_board) == _registry(cli_board)


# ---------------------------------------------------------------------------
# 2. CLI behaviour equals the recorded pre-port behaviour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_cli_matches_the_recorded_behaviour(tmp_path, name):
    golden = _golden()
    recorded = golden["scenarios"][name]
    files, script, args, extra = _SCENARIOS[name]
    root = tmp_path / "root"
    _seed(root, files)

    run = _run(tmp_path, root, script, args, extra)

    assert _outcome(run, root) == {k: recorded[k] for k in ("rc", "stdout", "stderr")}
    assert _tree(root) == _expected_tree(golden, recorded["tree"])


def test_cli_sequence_matches_the_recorded_behaviour(tmp_path):
    golden = _golden()
    recorded = golden["scenarios"]["create-twice"]
    root = tmp_path / "root"
    _seed(root, {})

    outcomes = [_outcome(_run(tmp_path, root, script, args), root) for script, args in _SEQUENCES["create-twice"]]

    assert outcomes == recorded["steps"]
    assert _tree(root) == _expected_tree(golden, recorded["tree"])


def test_every_recorded_scenario_is_exercised():
    recorded = set(_golden()["scenarios"])
    assert recorded == set(_SCENARIOS) | set(_SEQUENCES)


def test_seeded_templates_are_copies_of_the_workflow_templates(tmp_path):
    root = tmp_path / "root"
    _seed(root, {})
    run = _run(tmp_path, root, "create-project.sh", ["--project", "alpha", "--workflow-type", "document"])
    assert run.returncode == 0, run.stderr
    templates = _TEAM_DIR / "templates" / "project" / "document"
    project = root / "projects" / "alpha"
    for source, target in (
        ("BUG-TEMPLATE.md", "bugs/templates/BUG-TEMPLATE.md"),
        ("README-requirements.md", "requirements/README.md"),
        ("BRIEF-EXAMPLE.md", "brief-example.md"),
        ("INTENT-TEMPLATE.md", "INTENT.md"),
    ):
        assert (project / target).read_bytes() == (templates / source).read_bytes(), target


# ---------------------------------------------------------------------------
# 3. remove-project's Active-RC guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force", [False, True], ids=["unregister-only", "force"])
def test_cli_active_rc_refuses_with_and_without_force_and_changes_nothing(tmp_path, force):
    root = tmp_path / "root"
    _seed(root, {"projects/old/release-state.md": _RS_ACTIVE, "projects/old/x.md": "x\n", "projects.cfg": _INI_CFG})

    run = _run(tmp_path, root, "remove-project.sh", ["--project", "old"] + (["--force"] if force else []))

    assert run.returncode == 2, run.stdout + run.stderr
    assert run.stdout == ""
    triple = parse_error_contract(run.stderr)
    assert triple is not None and triple["error"] == "remove_project.active_rc", run.stderr
    assert "Active RC = v1.2.3" in triple["message"]
    assert "with or without --force" in triple["message"]
    assert "team/scripts/cm/cancel-rc.sh --project old --key v1.2.3 --yes" in triple["remedy"]
    assert "team/scripts/remove-project.sh --project old" in triple["remedy"]
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _INI_CFG
    assert (root / "projects" / "old" / "x.md").is_file()


@pytest.mark.parametrize(
    "body",
    [{"project": "old"}, {"project": "old", "force": True, "confirm": "remove-project old"}],
    ids=["unregister-only", "force"],
)
def test_rest_active_rc_refuses_with_and_without_force_and_changes_nothing(tmp_path, body):
    root = tmp_path / "root"
    _seed(root, {"projects/old/release-state.md": _RS_ACTIVE, "projects/old/x.md": "x\n", "projects.cfg": _INI_CFG})

    response = build_api_client(root).post("/operations/remove-project", json=body)

    payload = response.json()
    assert response.status_code == 409, payload
    assert payload["exit_code"] == 2
    assert payload["error"] == "remove_project.active_rc"
    assert "Active RC = v1.2.3" in payload["message"]
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _INI_CFG
    assert (root / "projects" / "old" / "x.md").is_file()


@pytest.mark.parametrize("force", [False, True], ids=["unregister-only", "force"])
def test_an_active_rc_field_that_does_not_validate_is_refused(tmp_path, force):
    root = tmp_path / "root"
    _seed(root, {"projects/old/release-state.md": _RS_INVALID, "projects.cfg": _INI_CFG})

    with pytest.raises(ops_projects.ProjectRefusal) as caught:
        ops_projects.remove_project(_ctx(root), "old", force=force)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "remove_project.release_state_invalid"
    assert "Active RC='NONE'" in triple["message"]
    assert "<" not in triple["remedy"]
    assert ops_projects.projects_exit_code(caught.value) == 2
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _INI_CFG
    assert (root / "projects" / "old").is_dir()


def test_an_active_rc_of_none_lets_force_delete_the_project(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/old/release-state.md": _RS_NONE, "projects.cfg": _INI_CFG})

    outcome = ops_projects.remove_project(_ctx(root), "old", force=True)

    assert outcome.deleted and outcome.changed
    assert not (root / "projects" / "old").exists()
    assert "[project:old]" not in (root / "projects.cfg").read_text(encoding="utf-8")
    assert f"  + deleted: {root}/projects/old\n" in capsys.readouterr().out


def test_unregister_only_needs_no_force_and_keeps_the_directory(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/old/x.md": "x\n", "projects.cfg": _INI_CFG})

    outcome = ops_projects.remove_project(_ctx(root), "old")

    assert outcome.changed and not outcome.deleted
    assert (root / "projects" / "old" / "x.md").is_file()
    assert (root / "projects.cfg").read_text(encoding="utf-8") == "# registry\n[project:two]\npriority = 4\n"
    assert "  - directory will be left in place:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 4. File mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [0o640, 0o644])
@pytest.mark.parametrize(
    "script, args, files",
    [
        ("remove-project.sh", ["--project", "old"], {"projects.cfg": _INI_CFG}),
        ("add-project.sh", ["--project", "old", "--priority", "5"], {"projects/old/": _DIR, "projects.cfg": _INI_CFG}),
        ("add-project.sh", ["--project", "zed"], {"projects/zed/": _DIR, "projects.cfg": _COLON_CFG}),
    ],
    ids=["remove", "update", "migrate"],
)
def test_a_rewritten_projects_cfg_keeps_its_mode(tmp_path, mode, script, args, files):
    root = tmp_path / "root"
    _seed(root, files)
    (root / "projects.cfg").chmod(mode)

    run = _run(tmp_path, root, script, args)

    assert run.returncode == 0, run.stderr
    assert (root / "projects.cfg").read_text(encoding="utf-8") != files["projects.cfg"]
    assert stat.S_IMODE((root / "projects.cfg").stat().st_mode) == mode


# ---------------------------------------------------------------------------
# 5. The functions directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call, exit_code",
    [
        (lambda ctx: ops_projects.create_project(ctx, ""), 1),
        (lambda ctx: ops_projects.create_project(ctx, "alpha", max_patch=-1), 1),
        (lambda ctx: ops_projects.create_project(ctx, "alpha", color="#abc"), 1),
        (lambda ctx: ops_projects.create_project(ctx, "alpha", dev_tree=""), 1),
        (lambda ctx: ops_projects.create_project(ctx, "alpha", git_repo=""), 1),
        (lambda ctx: ops_projects.create_project(ctx, "exists"), 2),
        (lambda ctx: ops_projects.create_project(ctx, "alpha", workflow_type="nope"), 1),
        (lambda ctx: ops_projects.add_project(ctx, ""), 1),
        (lambda ctx: ops_projects.add_project(ctx, "missing"), 2),
        (lambda ctx: ops_projects.add_project(ctx, "exists", color="red"), 1),
        (lambda ctx: ops_projects.remove_project(ctx, ""), 1),
    ],
    ids=[
        "create-no-name", "create-negative-ceiling", "create-short-colour", "create-empty-dev-tree",
        "create-empty-git-repo", "create-existing-dir", "create-unknown-workflow",
        "add-no-name", "add-missing-dir", "add-bad-colour", "remove-no-name",
    ],
)
def test_refusals_carry_the_scripts_exit_codes_and_write_nothing(tmp_path, call, exit_code):
    root = tmp_path / "root"
    _seed(root, {"projects/exists/": _DIR})

    with pytest.raises(ops_projects.ProjectRefusal) as caught:
        call(_ctx(root))

    assert ops_projects.projects_exit_code(caught.value) == exit_code
    assert str(caught.value).startswith("ERROR: ")
    assert not (root / "projects.cfg").exists()
    assert sorted(p.name for p in (root / "projects").iterdir()) == ["exists"]


def test_an_io_failure_exits_1():
    assert ops_projects.projects_exit_code(IoError("disk full")) == 1
    assert ops_projects.projects_exit_code(OpsError("other")) == 1


def test_create_project_reports_what_it_registered(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects.cfg": _INI_NOCOLOR})
    palette = load_default_palette()

    outcome = ops_projects.create_project(_ctx(root), "alpha", priority=0)

    assert outcome.changed and not outcome.dry_run
    assert outcome.priority == "0"
    # a2 (priority 1) holds palette slot 0 and a1 (priority 3) slot 1 implicitly.
    assert outcome.color == palette[2]
    assert f"[project:alpha]\npriority=0\ndashboard_color={palette[2]}\n" in (root / "projects.cfg").read_text(
        encoding="utf-8"
    )
    assert "  + registered in projects.cfg (priority=0, color=" in capsys.readouterr().out


def test_create_project_dry_run_writes_nothing(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {})

    outcome = ops_projects.create_project(_ctx(root), "alpha", dry_run=True, scripts_dir=_SCRIPTS)

    assert outcome.dry_run and not outcome.changed
    assert not (root / "projects").exists() and not (root / "projects.cfg").exists()
    out = capsys.readouterr().out
    assert f"  cp {_SCRIPTS}/../templates/project/release/INTENT-TEMPLATE.md -> {root}/projects/alpha/INTENT.md" in out


def test_add_project_with_an_unchanged_priority_has_nothing_to_do(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/two/README.md": "# two\n", "projects/two/project.cfg": _TWO_CFG, "projects.cfg": _INI_CFG})

    outcome = ops_projects.add_project(_ctx(root), "two", priority=4)

    assert not outcome.changed and outcome.priority == "4"
    assert "  Already registered at priority=4; nothing to do." in capsys.readouterr().out
    assert (root / "projects.cfg").read_text(encoding="utf-8") == _INI_CFG


def test_add_project_refuses_a_registered_project_with_no_priority_line(tmp_path):
    root = tmp_path / "root"
    _seed(root, {"projects/bare/": _DIR, "projects.cfg": "[project:bare]\nenabled=true\n"})

    with pytest.raises(ops_projects.ProjectRefusal) as caught:
        ops_projects.add_project(_ctx(root), "bare", priority=3)

    assert "without a priority line" in str(caught.value)
    assert ops_projects.projects_exit_code(caught.value) == 1


def test_an_unknown_workflow_lists_the_ready_plugins_under_the_kanban_root(tmp_path):
    root = tmp_path / "root"
    _seed(root, _PLUGINS)

    with pytest.raises(ops_projects.ProjectRefusal) as caught:
        ops_projects.create_project(_ctx(root), "alpha", workflow_type="nope")

    assert str(caught.value) == (
        "ERROR: unknown workflow type 'nope'; discovered ready types are: "
        "broken custom noagents nosh release"
    )


def test_seeded_overwatch_files_equal_the_bash_seeders(tmp_path):
    seeders = _SCRIPTS / "lib"
    backlog = subprocess.run(
        ["bash", "-c", f'source "{seeders}/overwatch_backlog_seed.sh"; overwatch_backlog_seed_content'],
        capture_output=True, text=True, env=_env(tmp_path, tmp_path), check=True, timeout=30,
    ).stdout
    resolve = subprocess.run(
        ["bash", "-c", f'source "{seeders}/overwatch_resolve_seed.sh"; overwatch_resolve_seed_comment_block'],
        capture_output=True, text=True, env=_env(tmp_path, tmp_path), check=True, timeout=30,
    ).stdout

    assert ops_projects.queue_file_text("Overwatch Backlog", "overwatch agent") == backlog
    assert resolve == _SEED_FILE.read_text(encoding="utf-8")
    assert resolve.rstrip("\n").endswith("# overwatch_resolve = false")

    root = tmp_path / "root"
    _seed(root, {})
    ops_projects.create_project(_ctx(root), "alpha")
    project = root / "projects" / "alpha"
    assert (project / "tasks" / "queues" / "overwatch_backlog.md").read_text(encoding="utf-8") == backlog
    # The template carries the seed block verbatim as the close of [release],
    # so a new project.cfg and an upgraded one say the same thing.
    release = (project / "project.cfg").read_text(encoding="utf-8").split("\n[release]\n", 1)[1].split("\n[", 1)[0]
    assert resolve.rstrip("\n") + "\n" in release


def test_the_bash_resolve_seeder_names_a_missing_seed_file(tmp_path):
    lib = tmp_path / "tree" / "scripts" / "lib"
    lib.mkdir(parents=True)
    shutil.copy(_SCRIPTS / "lib" / "overwatch_resolve_seed.sh", lib)

    run = subprocess.run(
        ["bash", "-c", f'source "{lib}/overwatch_resolve_seed.sh"; overwatch_resolve_seed_comment_block'],
        capture_output=True, text=True, env=_env(tmp_path, tmp_path), timeout=30,
    )

    assert run.returncode == 1
    assert "seed block file not found" in run.stderr and "overwatch_resolve_seed.txt" in run.stderr


def test_the_colon_registry_palette_fallback_follows_the_bash_helper(tmp_path):
    # A colon-legacy registry whose entries follow comment lines: the bash
    # palette-index helper only visits the first len(rows) line numbers, so
    # a project without an explicit colour reads palette slot 0.
    root = tmp_path / "root"
    _seed(root, {"projects.cfg": "# header\n# more\nfirst:1\nsecond:2\n"})
    registry = ops_projects._Registry(root)
    palette = load_default_palette()

    assert registry.format() == "colon-legacy"
    assert registry.color("second") == palette[0]
    assert registry.priority("second") == "2"
    assert registry.next_priority() == 3
    # Effective colours count implicit slots: first holds 0, second holds 1.
    assert registry.next_color() == palette[2]


def test_an_ini_header_with_no_sections_reads_as_ini(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    registry = ops_projects._Registry(root)
    registry.ensure()

    assert registry.format() == "ini"
    assert registry.next_priority() == 1
    assert not registry.has("anything")


@pytest.mark.parametrize("verb", ops_projects.CLI_VERBS)
def test_the_ops_entry_point_routes_registry_verbs_here(tmp_path, verb):
    root = tmp_path / "root"
    _seed(root, {})
    env = _env(tmp_path, root, {"PYTHONPATH": str(_TEAM_DIR)})

    run = subprocess.run(
        [sys.executable, "-m", "pgai_agent_kanban.ops", verb, f"--kanban-root={root}", "--project="],
        capture_output=True, text=True, env=env, cwd=str(root), timeout=60,
    )

    assert run.returncode == 1, run.stdout + run.stderr
    assert "ERROR: project name is required (--project <name>)" in run.stderr


def test_the_cli_shim_refuses_an_unknown_verb(capsys):
    assert ops_projects._cli_main(["nonsense"]) == 1
    assert "VERB must be one of: create_project, add_project, remove_project" in capsys.readouterr().err


def test_the_cli_shim_prints_a_dual_channel_refusal_through_the_emitter(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/old/release-state.md": _RS_ACTIVE, "projects.cfg": _INI_CFG})

    code = ops_projects._cli_main(["remove_project", f"--kanban-root={root}", "--project=old", "--force"])

    assert code == 2
    err = capsys.readouterr().err
    assert parse_error_contract(err)["error"] == "remove_project.active_rc"
    assert os.path.isdir(root / "projects" / "old")


# ---------------------------------------------------------------------------
# 6. The project README: seeded once, never overwritten
# ---------------------------------------------------------------------------

_README_TEMPLATE = _TEAM_DIR / "templates" / "project" / "README-TEMPLATE.md"
_EDITED_README = "# Project: alpha\n\nOur own runners and contracts.\n"


def test_create_project_seeds_the_project_readme_from_the_template(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {})

    ops_projects.create_project(_ctx(root), "alpha", scripts_dir=_SCRIPTS)

    readme = root / "projects" / "alpha" / "README.md"
    assert readme.read_bytes() == _README_TEMPLATE.read_bytes()
    assert "  + seeded README.md from the project README template" in capsys.readouterr().out


def test_the_create_output_tells_the_operator_to_fill_in_the_project_readme(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {})

    ops_projects.create_project(_ctx(root), "alpha", scripts_dir=_SCRIPTS)

    lines = capsys.readouterr().out.splitlines()
    steps = lines[lines.index("Next steps:"):]
    assert any(line.strip().startswith("2. Fill in the project README") for line in steps), steps
    assert f"     $EDITOR {root}/projects/alpha/README.md" in steps


def test_create_project_dry_run_lists_the_readme_copy_and_writes_none(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {})

    ops_projects.create_project(_ctx(root), "alpha", dry_run=True, scripts_dir=_SCRIPTS)

    assert not (root / "projects").exists()
    out = capsys.readouterr().out
    assert f"  cp {_SCRIPTS}/../templates/project/README-TEMPLATE.md -> {root}/projects/alpha/README.md" in out


@pytest.mark.parametrize("registry", [None, _INI_CFG], ids=["unregistered", "registered"])
def test_add_project_seeds_a_missing_project_readme(tmp_path, capsys, registry):
    root = tmp_path / "root"
    files = {"projects/two/": _DIR}
    if registry is not None:
        files["projects.cfg"] = registry
    _seed(root, files)

    outcome = ops_projects.add_project(_ctx(root), "two", scripts_dir=_SCRIPTS)

    readme = root / "projects" / "two" / "README.md"
    assert outcome.changed
    assert readme.read_bytes() == _README_TEMPLATE.read_bytes()
    out = capsys.readouterr().out
    assert "  + seeded README.md from the project README template; fill in this project's rules:" in out
    assert f"     $EDITOR {readme}" in out
    assert "[project:two]" in (root / "projects.cfg").read_text(encoding="utf-8")


@pytest.mark.parametrize("registry", [None, _INI_CFG], ids=["unregistered", "registered"])
def test_add_project_leaves_an_existing_project_readme_byte_unchanged(tmp_path, capsys, registry):
    root = tmp_path / "root"
    files = {"projects/two/README.md": _EDITED_README}
    if registry is not None:
        files["projects.cfg"] = registry
    _seed(root, files)
    readme = root / "projects" / "two" / "README.md"
    mtime = readme.stat().st_mtime_ns

    ops_projects.add_project(_ctx(root), "two", scripts_dir=_SCRIPTS)

    assert readme.read_text(encoding="utf-8") == _EDITED_README
    assert readme.stat().st_mtime_ns == mtime
    assert "README" not in capsys.readouterr().out


def test_add_project_dry_run_reports_the_readme_seed_and_writes_none(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/two/": _DIR})

    outcome = ops_projects.add_project(_ctx(root), "two", dry_run=True, scripts_dir=_SCRIPTS)

    assert outcome.dry_run and not outcome.changed
    assert not (root / "projects" / "two" / "README.md").exists()
    assert "  Will seed README.md from the project README template (none present)" in capsys.readouterr().out


def test_an_edited_readme_survives_a_second_create_an_add_and_a_refresh(tmp_path):
    from pgai_agent_kanban.ops import seeded_templates

    root = tmp_path / "root"
    _seed(root, {})
    ops_projects.create_project(_ctx(root), "alpha", scripts_dir=_SCRIPTS)
    readme = root / "projects" / "alpha" / "README.md"
    readme.write_text(_EDITED_README, encoding="utf-8")

    with pytest.raises(ops_projects.ProjectRefusal):
        ops_projects.create_project(_ctx(root), "alpha", scripts_dir=_SCRIPTS)
    assert readme.read_text(encoding="utf-8") == _EDITED_README

    ops_projects.add_project(_ctx(root), "alpha", scripts_dir=_SCRIPTS)
    ops_projects.add_project(_ctx(root), "alpha", priority=5, scripts_dir=_SCRIPTS)
    assert readme.read_text(encoding="utf-8") == _EDITED_README

    seeded_templates.refresh_seeded_templates(root, scripts_dir=_SCRIPTS)
    assert readme.read_text(encoding="utf-8") == _EDITED_README
    assert not list(readme.parent.glob("README.md.*"))


def test_a_missing_readme_template_warns_and_the_project_is_still_added(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/two/": _DIR})
    bare_scripts = tmp_path / "bare-install" / "scripts"
    bare_scripts.mkdir(parents=True)

    outcome = ops_projects.add_project(_ctx(root), "two", scripts_dir=bare_scripts)

    assert outcome.changed
    assert not (root / "projects" / "two" / "README.md").exists()
    assert "[project:two]" in (root / "projects.cfg").read_text(encoding="utf-8")
    err = capsys.readouterr().err
    assert f"WARNING: project README template not found: {bare_scripts}/../templates/project/README-TEMPLATE.md" in err


# ---------------------------------------------------------------------------
# 7. A priority that is not a whole number is refused and writes nothing
# ---------------------------------------------------------------------------

_INJECTED_PRIORITY = "2\n[project:injected]\npriority=1"


@pytest.mark.parametrize(
    ("script", "args", "files", "error_key"),
    [
        ("create-project.sh", ["--project", "fresh", "--priority", _INJECTED_PRIORITY],
         {"projects.cfg": _INI_CFG}, "create_project.priority_invalid"),
        ("add-project.sh", ["--project", "two", "--priority", _INJECTED_PRIORITY],
         {"projects/two/": _DIR, "projects.cfg": _INI_CFG}, "add_project.priority_invalid"),
        ("add-project.sh", ["--project", "newone", "--priority", "high"],
         {"projects/newone/": _DIR, "projects.cfg": _INI_CFG}, "add_project.priority_invalid"),
    ],
    ids=["create-newline", "add-newline", "add-word"],
)
def test_cli_refuses_a_priority_that_is_not_a_whole_number(tmp_path, script, args, files, error_key):
    root = tmp_path / "root"
    _seed(root, files)
    before = _tree(root)

    run = _run(tmp_path, root, script, args)

    assert run.returncode == 1, _outcome(run, root)
    assert f"error:   {error_key}" in run.stderr, run.stderr
    assert "remedy:" in run.stderr and "--priority 2" in run.stderr, run.stderr
    assert _tree(root) == before, "a refused priority must write nothing"
    assert "[project:injected]" not in (root / "projects.cfg").read_text(encoding="utf-8")


def test_functions_refuse_a_priority_with_a_newline_before_touching_the_root(tmp_path):
    root = tmp_path / "root"
    _seed(root, {"projects/exists/": _DIR})

    for call, verb in (
        (lambda: ops_projects.create_project(_ctx(root), "fresh", priority=_INJECTED_PRIORITY), "create_project"),
        (lambda: ops_projects.add_project(_ctx(root), "exists", priority=_INJECTED_PRIORITY), "add_project"),
    ):
        with pytest.raises(ops_projects.ProjectValueRefused) as caught:
            call()
        assert caught.value.field == "priority"
        assert caught.value.contract["error"] == f"{verb}.priority_invalid"
        assert ops_projects.projects_exit_code(caught.value) == 1

    assert not (root / "projects.cfg").exists()
    assert sorted(p.name for p in (root / "projects").iterdir()) == ["exists"]


def test_an_integer_priority_is_still_accepted(tmp_path, capsys):
    root = tmp_path / "root"
    _seed(root, {"projects/exists/": _DIR})

    outcome = ops_projects.add_project(_ctx(root), "exists", priority=-3)

    assert outcome.priority == "-3"
