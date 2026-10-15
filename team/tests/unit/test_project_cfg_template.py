"""
test_project_cfg_template.py — the project.cfg template is the one source of
every project's project.cfg.

Coverage:

  1. A project.cfg written by create_project carries every key the kanban
     reads -- ops.projects.PROJECT_CFG_KEYS, the shared list, never a copy
     typed here -- under its reader's section, active or as "# key = value";
     every key has a comment block stating its default (or "Inherits ..." /
     "No in-code default"); the file has no version tag, no $HOME, no
     example project name and no active temp_dir; and its section headers
     and key names follow the template's, in the template's order.
  2. The shared list covers every key project_paths.sh's Key Registry names.
  3. add_project writes a missing project.cfg from the same template (and
     narrates one line saying so); an existing project.cfg, a legacy
     PROJECT.cfg and a dry run leave the directory as it was.
  4. Uncommenting "stale_lock_reap_after_wake_periods = 7" in a generated
     project.cfg, or in a copy of project.cfg_example, makes the real
     pp_stale_lock_reap_after_wake_periods return 7: the key sits under the
     [locks] header its reader uses.
  5. project.cfg_example itself: an active [locks] header, no active value
     holding $ or ~, temp_dir commented, the commented ship-posture lines at
     their defaults, no develop branch / stale plugin list / stale path /
     version tag, workflow_type naming every ready workflow plugin, and
     role lists that include po and overwatch.
  6. The template is reachable in both layouts: the repository-root
     project.cfg_example resolves to team/config/project.cfg_example, and a
     package copied the way install.sh flattens team/ renders from its own
     config/ directory.

Every kanban root is a throwaway directory under tmp_path; bash runs get an
explicit, minimal environment (build_scrubbed_env) pointing all root
variables at it.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_REPO_ROOT = _TEAM_DIR.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.ops import projects as ops_projects  # noqa: E402
from pgai_agent_kanban.ops.context import OpsContext  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_TEMPLATE = ops_projects.PROJECT_CFG_TEMPLATE
_ROOT_EXAMPLE = _REPO_ROOT / "project.cfg_example"
_KEYS = ops_projects.PROJECT_CFG_KEYS
_KEY_NAMES = {key for _, key in _KEYS}
_PROJECT_PATHS = _TEAM_DIR / "scripts" / "lib" / "project_paths.sh"

_SECTION_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")
_ACTIVE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=(.*)$")
# A documented-but-unset key: exactly "# key = value" (or "# key =").
_COMMENTED_RE = re.compile(r"^#\s?([a-z][a-z0-9_]*)\s*=\s*(\S*)\s*$")
_DEFAULT_STATEMENT_RE = re.compile(r"Default:|Inherits|No in-code default")
_VERSION_TAG_RE = re.compile(r"\(v2\.|\bv[0-9]+\.[0-9]+")
# Example project names the template once carried; none may reach a project.cfg.
_EXAMPLE_NAMES = ("my-project", "pgai-agent-kanban", "myorg")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key_lines(text: str) -> list[tuple[int, str, str, bool]]:
    """``(line index, section, key, active)`` for every key line of a cfg text.

    Active lines are every ``key = value``; commented ones are the
    ``# key = value`` lines naming a key in the shared list (prose that only
    mentions a key never matches: it does not start with the key).
    """
    found = []
    section = ""
    for index, line in enumerate(text.splitlines()):
        header = _SECTION_RE.match(line)
        if header:
            section = header.group(1)
            continue
        active = _ACTIVE_RE.match(line)
        if active and not line.lstrip().startswith(("#", ";")):
            found.append((index, section, active.group(1), True))
            continue
        commented = _COMMENTED_RE.match(line)
        if commented and commented.group(1) in _KEY_NAMES:
            found.append((index, section, commented.group(1), False))
    return found


def _structure(text: str) -> list[tuple[str, ...]]:
    """The section headers and key names of a cfg text, in order."""
    out: list[tuple[str, ...]] = []
    lines = text.splitlines()
    keys = {index: key for index, _, key, _ in _key_lines(text)}
    for index, line in enumerate(lines):
        header = _SECTION_RE.match(line)
        if header:
            out.append(("section", header.group(1)))
        elif index in keys:
            out.append(("key", keys[index]))
    return out


def _comment_block(lines: list[str], index: int) -> list[str]:
    """The run of comment lines directly above line *index*."""
    block = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        block.append(lines[cursor])
        cursor -= 1
    return list(reversed(block))


def _value_of(text: str, section: str, key: str) -> tuple[str, bool]:
    """The value and active flag of *section*/*key*'s one line in *text*."""
    lines = text.splitlines()
    hits = [(i, active) for i, s, k, active in _key_lines(text) if (s, k) == (section, key)]
    assert len(hits) == 1, f"[{section}] {key} appears {len(hits)} times"
    index, active = hits[0]
    pattern = _ACTIVE_RE if active else _COMMENTED_RE
    match = pattern.match(lines[index])
    assert match is not None
    return match.group(2).strip(), active


def _assert_well_formed_project_cfg(text: str) -> None:
    """The checks every project.cfg written from the template passes."""
    lines = text.splitlines()
    found = _key_lines(text)
    by_key: dict[str, list[tuple[int, str, bool]]] = {}
    for index, section, key, active in found:
        by_key.setdefault(key, []).append((index, section, active))

    for section, key in _KEYS:
        assert key in by_key, f"[{section}] {key} is read by the kanban but missing from the file"
        assert len(by_key[key]) == 1, f"{key} has {len(by_key[key])} key lines; expected exactly one"
        _, where, _ = by_key[key][0]
        assert where == section, f"{key} sits under [{where}]; its reader looks in [{section}]"

    for index, _, key, _ in found:
        block = _comment_block(lines, index)
        assert any(_DEFAULT_STATEMENT_RE.search(line) for line in block), (
            f"{key}'s comment block states no default ('Default:', 'Inherits ...' or "
            f"'No in-code default'):\n" + "\n".join(block)
        )

    for number, line in enumerate(lines, 1):
        assert not _VERSION_TAG_RE.search(line), f"line {number} carries a version tag: {line!r}"
    assert "$HOME" not in text
    for example in _EXAMPLE_NAMES:
        assert example not in text, f"example project name {example!r} reached the file"
    assert ops_projects.PROJECT_NAME_PLACEHOLDER not in text
    assert not any(active for _, section, key, active in found if key == "temp_dir"), "temp_dir is active"


def _root(tmp_path: pathlib.Path, name: str = "root") -> pathlib.Path:
    root = tmp_path / name
    (root / "tmp").mkdir(parents=True)
    return root


def _ctx(root: pathlib.Path) -> OpsContext:
    return OpsContext(kanban_root=root)


def _bash(tmp_path: pathlib.Path, root: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env,
        cwd=str(_TEAM_DIR), stdin=subprocess.DEVNULL, timeout=60,
    )


def _uncomment_stale_lock_seven(text: str) -> str:
    out, count = re.subn(
        r"^# stale_lock_reap_after_wake_periods = \S+$",
        "stale_lock_reap_after_wake_periods = 7",
        text, flags=re.MULTILINE,
    )
    assert count == 1, "expected exactly one commented stale_lock_reap_after_wake_periods line"
    return out


# ---------------------------------------------------------------------------
# 1-2. create_project writes every read key, documented, in template order
# ---------------------------------------------------------------------------


def test_create_project_writes_every_read_key_documented_under_its_section(tmp_path, capsys):
    root = _root(tmp_path)

    ops_projects.create_project(_ctx(root), "sample-lane")

    text = (root / "projects" / "sample-lane" / "project.cfg").read_text(encoding="utf-8")
    _assert_well_formed_project_cfg(text)
    assert _value_of(text, "project", "project_name") == ("sample-lane", True)
    assert _value_of(text, "project", "dev_tree_path") == ("", True)
    assert _value_of(text, "versioning", "max_major") == ("0", True)
    assert "IMPORTANT: dev_tree_path and git_repo_url are intentionally empty." in text
    capsys.readouterr()


def test_create_project_substitutes_only_the_project_specific_values(tmp_path, capsys):
    root = _root(tmp_path)
    dev_tree = tmp_path / "checkout"
    dev_tree.mkdir()

    ops_projects.create_project(
        _ctx(root), "flagged-lane", workflow_type="document", max_patch=3, max_minor=4, max_major=1,
        git_remote="upstream", dev_tree=str(dev_tree), git_repo="git@git.example.com:org/flagged.git",
    )

    text = (root / "projects" / "flagged-lane" / "project.cfg").read_text(encoding="utf-8")
    _assert_well_formed_project_cfg(text)
    expected = {
        ("project", "project_name"): "flagged-lane",
        ("project", "workflow_type"): "document",
        ("project", "git_remote_name"): "upstream",
        ("project", "dev_tree_path"): str(dev_tree),
        ("project", "git_repo_url"): "git@git.example.com:org/flagged.git",
        ("versioning", "max_patch"): "3",
        ("versioning", "max_minor"): "4",
        ("versioning", "max_major"): "1",
    }
    for (section, key), value in expected.items():
        assert _value_of(text, section, key) == (value, True), key
    template = _TEMPLATE.read_text(encoding="utf-8").replace(ops_projects.PROJECT_NAME_PLACEHOLDER, "flagged-lane")
    for section, key in _KEYS:
        if (section, key) not in expected:
            assert _value_of(text, section, key) == _value_of(template, section, key), key
    assert "were supplied via --dev-tree/--git-repo" in text
    capsys.readouterr()


def test_generated_project_cfg_follows_the_template_sections_and_keys_in_order(tmp_path, capsys):
    root = _root(tmp_path)

    ops_projects.create_project(_ctx(root), "ordered-lane")

    text = (root / "projects" / "ordered-lane" / "project.cfg").read_text(encoding="utf-8")
    template = _TEMPLATE.read_text(encoding="utf-8")
    assert _structure(text) == _structure(template)
    assert [("key", key) for _, key in _KEYS] == [item for item in _structure(template) if item[0] == "key"]
    capsys.readouterr()


def test_generated_body_is_the_template_body_with_the_name_substituted(tmp_path, capsys):
    root = _root(tmp_path)

    ops_projects.create_project(_ctx(root), "body-lane")

    text = (root / "projects" / "body-lane" / "project.cfg").read_text(encoding="utf-8")
    template = _TEMPLATE.read_text(encoding="utf-8")
    body = lambda cfg: cfg[cfg.index("\n# ---"):]  # noqa: E731 -- from the first section banner on
    expected = body(template).replace(ops_projects.PROJECT_NAME_PLACEHOLDER, "body-lane")
    expected = expected.replace("\nproject_name =\n", "\nproject_name = body-lane\n")
    assert body(text) == expected
    capsys.readouterr()


def test_the_shared_key_list_covers_the_project_paths_key_registry():
    source = _PROJECT_PATHS.read_text(encoding="utf-8")
    registry = source.split("# Key Registry", 1)[1].split("\n\n", 1)[0]
    section = ""
    declared = set()
    for line in registry.splitlines():
        header = re.match(r"^#\s+\[([a-z_]+)\]\s*$", line)
        if header:
            section = header.group(1)
            continue
        entry = re.match(r"^#\s{3}([a-z][a-z0-9_]*)\s+(?:OPTIONAL|REQUIRED)\b", line)
        if entry and section:
            declared.add((section, entry.group(1)))

    assert declared, "parsed no keys out of project_paths.sh's Key Registry"
    assert declared <= set(_KEYS), sorted(declared - set(_KEYS))
    assert len(set(_KEYS)) == len(_KEYS), "PROJECT_CFG_KEYS lists a key twice"


def test_a_template_without_a_substituted_key_or_absent_is_refused_naming_it(tmp_path):
    broken = tmp_path / "project.cfg_example"
    broken.write_text(
        _TEMPLATE.read_text(encoding="utf-8").replace("\nworkflow_type = release\n", "\n"), encoding="utf-8"
    )
    with pytest.raises(ops_projects.IoError) as caught:
        ops_projects.render_project_cfg(name="x", generated_by="create-project.sh", template=broken)
    assert "[project] workflow_type" in str(caught.value) and str(broken) in str(caught.value)

    missing = tmp_path / "absent.cfg_example"
    with pytest.raises(ops_projects.IoError) as caught:
        ops_projects.render_project_cfg(name="x", generated_by="create-project.sh", template=missing)
    assert str(missing) in str(caught.value)


# ---------------------------------------------------------------------------
# 3. add_project writes a missing project.cfg, never an existing one
# ---------------------------------------------------------------------------


def test_add_project_writes_a_missing_project_cfg_and_narrates_one_line(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "projects" / "adopted").mkdir(parents=True)

    outcome = ops_projects.add_project(_ctx(root), "adopted")

    out = capsys.readouterr().out
    text = (root / "projects" / "adopted" / "project.cfg").read_text(encoding="utf-8")
    _assert_well_formed_project_cfg(text)
    assert _value_of(text, "project", "project_name") == ("adopted", True)
    assert "# Generated by add-project.sh on " in text
    written = [line for line in out.splitlines() if line.startswith("  + ") and "project.cfg" in line]
    assert written == [
        "  + wrote project.cfg from the project.cfg template (none present); "
        "set dev_tree_path and git_repo_url in it"
    ]
    assert outcome.changed


def test_add_project_on_a_registered_project_without_project_cfg_writes_one(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "projects" / "listed").mkdir(parents=True)
    (root / "projects" / "listed" / "README.md").write_text("# listed\n", encoding="utf-8")
    ops_projects.add_project(_ctx(root), "listed")
    (root / "projects" / "listed" / "project.cfg").unlink()
    capsys.readouterr()

    ops_projects.add_project(_ctx(root), "listed")

    out = capsys.readouterr().out
    assert "nothing to do" not in out
    _assert_well_formed_project_cfg((root / "projects" / "listed" / "project.cfg").read_text(encoding="utf-8"))


@pytest.mark.parametrize("existing", ["project.cfg", "PROJECT.cfg"])
def test_add_project_leaves_an_existing_project_config_byte_identical(tmp_path, capsys, existing):
    root = _root(tmp_path)
    project = root / "projects" / "kept"
    project.mkdir(parents=True)
    original = b"[project]\nproject_name = kept\n# hand-edited \xc3\xa9 \n\nodd line without equals\n"
    (project / existing).write_bytes(original)

    ops_projects.add_project(_ctx(root), "kept")
    ops_projects.add_project(_ctx(root), "kept", priority=9)

    out = capsys.readouterr().out
    assert (project / existing).read_bytes() == original
    assert "project.cfg" not in out
    assert sorted(p.name for p in project.iterdir() if p.name.lower() == "project.cfg") == [existing]


def test_add_project_dry_run_writes_no_project_cfg(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "projects" / "planned").mkdir(parents=True)

    ops_projects.add_project(_ctx(root), "planned", dry_run=True)

    out = capsys.readouterr().out
    assert "  Will write project.cfg from the project.cfg template (none present)" in out
    assert not (root / "projects" / "planned" / "project.cfg").exists()


# ---------------------------------------------------------------------------
# 4. The [locks] key reaches its reader once uncommented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["generated", "example-copy"])
def test_uncommented_stale_lock_override_reaches_its_reader(tmp_path, capsys, source):
    root = _root(tmp_path)
    ops_projects.create_project(_ctx(root), "lock-lane")
    capsys.readouterr()
    cfg = root / "projects" / "lock-lane" / "project.cfg"
    text = cfg.read_text(encoding="utf-8") if source == "generated" else _ROOT_EXAMPLE.read_text(encoding="utf-8")
    cfg.write_text(_uncomment_stale_lock_seven(text), encoding="utf-8")

    run = _bash(tmp_path, root, "source scripts/lib/project_paths.sh && pp_stale_lock_reap_after_wake_periods lock-lane")

    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "7"


def test_commented_stale_lock_override_leaves_the_default_in_force(tmp_path, capsys):
    root = _root(tmp_path)
    ops_projects.create_project(_ctx(root), "quiet-lane")
    capsys.readouterr()

    run = _bash(tmp_path, root, "source scripts/lib/project_paths.sh && pp_stale_lock_reap_after_wake_periods quiet-lane")

    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "3"


# ---------------------------------------------------------------------------
# 5. project.cfg_example's own content
# ---------------------------------------------------------------------------


def _example() -> str:
    return _ROOT_EXAMPLE.read_text(encoding="utf-8")


def test_example_puts_the_stale_lock_key_under_an_active_locks_header():
    hits = [(section, active) for _, section, key, active in _key_lines(_example())
            if key == "stale_lock_reap_after_wake_periods"]
    assert hits == [("locks", False)]
    assert re.search(r"^\[locks\]$", _example(), re.MULTILINE)


def test_example_active_values_hold_no_variables_or_tilde():
    lines = _example().splitlines()
    for index, _, key, active in _key_lines(_example()):
        if active:
            value = _ACTIVE_RE.match(lines[index]).group(2)
            assert "$" not in value and "~" not in value, f"{key} = {value.strip()}"


def test_example_leaves_temp_dir_commented():
    assert _value_of(_example(), "project", "temp_dir")[1] is False


def test_example_shows_the_ship_posture_defaults_commented():
    assert _value_of(_example(), "release", "ship_posture") == ("matrix", False)
    assert _value_of(_example(), "release", "ship_posture_confirm_remote") == ("false", False)


@pytest.mark.parametrize(
    "pattern",
    [r"\bdevelop\b", r"feature, prose", r"team/templates/project\.cfg\.example", r"\bv[0-9]+\.[0-9]+", r"\(v2\."],
)
def test_example_carries_no_stale_text(pattern):
    for number, line in enumerate(_example().splitlines(), 1):
        assert not re.search(pattern, line), f"line {number}: {line!r}"


def test_example_workflow_type_names_every_ready_workflow_plugin():
    ready = sorted(
        cfg.parent.name for cfg in (_TEAM_DIR / "workflows").glob("*/workflow.cfg")
        if re.search(r"^\s*status\s*=\s*ready\s*$", cfg.read_text(encoding="utf-8"), re.MULTILINE)
    )
    lines = _example().splitlines()
    index = next(i for i, _, key, _ in _key_lines(_example()) if key == "workflow_type")
    block = " ".join(_comment_block(lines, index))
    assert ready, "found no ready workflow plugin under team/workflows"
    for name in ready:
        assert re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", block), f"{name} missing from: {block}"


@pytest.mark.parametrize("key", ["verbose_agents", "training_agents"])
def test_example_role_lists_include_po_and_overwatch(key):
    lines = _example().splitlines()
    index = next(i for i, _, k, _ in _key_lines(_example()) if k == key)
    accepted = [line for line in _comment_block(lines, index) if "Accepted:" in line]
    assert accepted, f"{key} has no 'Accepted:' line"
    roles = set(re.findall(r"[a-z]+", accepted[0].split("Accepted:", 1)[1]))
    assert {"pm", "coder", "writer", "tester", "cm", "po", "overwatch"} <= roles


def test_example_is_itself_well_formed_apart_from_its_placeholder():
    text = _example().replace(ops_projects.PROJECT_NAME_PLACEHOLDER, "sample")
    _assert_well_formed_project_cfg(text)


# ---------------------------------------------------------------------------
# 6. Reachable in the source-tree and the installed layout
# ---------------------------------------------------------------------------


def test_root_example_is_the_canonical_template():
    assert _TEMPLATE == _TEAM_DIR / "config" / "project.cfg_example"
    assert _ROOT_EXAMPLE.resolve() == _TEMPLATE.resolve()


def test_an_installed_package_renders_from_its_own_config_directory(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban", installed / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (installed / "config").mkdir()
    shutil.copyfile(_TEMPLATE, installed / "config" / "project.cfg_example")
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from pgai_agent_kanban.ops import projects as p;"
        "print(p.PROJECT_CFG_TEMPLATE);"
        "print(p.render_project_cfg(name='installed-lane', generated_by='create-project.sh'), end='')"
    )

    run = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(installed)],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=60,
    )

    assert run.returncode == 0, run.stderr
    path, _, text = run.stdout.partition("\n")
    assert pathlib.Path(path) == installed / "config" / "project.cfg_example"
    _assert_well_formed_project_cfg(text)
