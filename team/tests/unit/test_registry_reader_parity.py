"""
test_registry_reader_parity.py
==============================
The bash and Python projects.cfg readers list the same projects for the
same file:

  * ``projects_cfg_list`` (team/scripts/lib/projects.sh), in priority and in
    registration order, against ``pgai_agent_kanban.lib.ini.projects_registry``
    sorted by priority and by section position;
  * ``projects_cfg_has`` answers yes for every name the Python registry
    lists;
  * ``_pp_registered_project_names`` (team/scripts/lib/project_paths.sh), the
    name set project-name resolution checks against, holds the same names.

Each fixture is written byte for byte: a plain registry, a header with
trailing spaces or a tab, CRLF line endings (with and without trailing
whitespace on the header), comment lines (indented, and a commented-out
header), a file that opens with a ';' comment, an indented header, a
project registered by two sections, and a registry whose file order,
priorities and whitespace all differ so the order is compared too.

A ';'-comment-first file and an indented header both read as INI on both
sides, and a project with two sections is listed once by both, the later
section's priority winning.

For a project with two sections the per-field readers agree too: the bash
``projects_cfg_priority``, ``projects_cfg_enabled``, ``projects_cfg_field``,
``projects_cfg_max_rows`` and ``_projects_cfg_read_raw_color``, the Python
``ini.projects_registry`` / ``ini.project_enabled`` and
``pgai_agent_kanban.ops.projects._Registry`` each read a key from the last
section that sets it, and a key only the earlier section sets keeps its
value. The bash bulk loader ``_projects_cfg_load`` fills its
``projects_priority``, ``projects_enabled``, ``projects_description``,
``projects_max_rows`` and ``projects_color`` arrays by the same per-key
merge.

Reading the registry in bash does not need the dashboard palette: with
``config/default_palette.txt`` absent beside the scripts, sourcing
``projects.sh`` still prints the palette's FATAL line, and
``projects_cfg_list`` still lists the registry.

Recognising a [project:...] section and accepting its name are separate
steps on both sides: the bash ``project_section`` / ``project_section_name``
and the Python ``_PROJECT_SECTION_RE`` recognise the same headers and read
the same name from each, and ``projects_cfg_unaccepted_names`` and
``unaccepted_project_names`` name the same sections whose name the registry
refuses ([project:bad.name], [project:bad name], [project:]) -- none of
which ``projects_cfg_list``, ``ops.projects.registered_project_names`` or
``ini.projects_registry`` lists as a project; ``ini`` and ``ops.projects``
hold the same name rule. Such a section ends the project section before it
on every side: a priority or colour key under it changes no project's list
position, priority or colour.

The name rule is the strict project-name rule, defined once as
``PROJECTS_CFG_NAME_RE`` in ``lib/projects_cfg.sh``: ``ini.project_name_rule``
reads that definition, so bash and Python hold the same pattern, and
``ops.projects``' create/add/remove name checks refuse what the readers do
not list. ``My_App`` and ``my_app`` are listed by no reader and named by
both unaccepted-name reporters, while ``my-app`` is listed by every reader.
A header followed by a trailing comment (``[project:alpha] # first``) is no
project in any reader. Without its definition line the Python rule refuses
loud, naming the file.

The Python registry verbs (pgai_agent_kanban.ops.projects) read a header
by the same rule: ``add_project`` finds a project whose header carries
leading or trailing whitespace already registered and leaves projects.cfg
unchanged.

Every bash run is a subprocess against a sandbox kanban root under
``tmp_path`` with an environment built by ``build_scrubbed_env``; nothing
reads or writes the live install.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from pgai_agent_kanban.lib import ini
from pgai_agent_kanban.ops import projects as ops_projects
from pgai_agent_kanban.ops.context import OpsContext
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]  # team/
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"

# name -> projects.cfg bytes
_REGISTRIES: dict[str, bytes] = {
    "plain": b"[project:p1]\npriority = 1\n",
    "header-trailing-spaces": b"[project:p1]  \npriority = 1\n",
    "header-trailing-tab": b"[project:p1]\t\npriority = 1\n",
    "crlf": b"[project:p1]\r\npriority = 1\r\n",
    "crlf-header-trailing-space": b"[project:p1] \r\npriority = 1\r\n",
    "comment-lines": (
        b"# registry\n"
        b"   # indented comment\n"
        b"[project:p1]\n"
        b"priority = 2\n"
        b"# [project:ghost]\n"
        b"\n"
        b"[project:p2]\n"
        b"# priority = 9\n"
        b"priority = 1\n"
    ),
    "semicolon-comment-first": b"; comment\n[project:p1]\npriority = 1\n",
    "indented-header": b"  [project:p1]\npriority = 1\n",
    "indented-header-after-a-field": (
        b"[project:p1]\n"
        b"priority = 2\n"
        b"\t[project:p2]\n"
        b"priority = 1\n"
    ),
    "duplicate-section": b"[project:p1]\npriority = 1\n[project:p1]\npriority = 2\n",
    "duplicate-section-reorders": (
        b"[project:p1]\n"
        b"priority = 1\n"
        b"[project:p2]\n"
        b"priority = 2\n"
        b"[project:p1]\n"
        b"priority = 3\n"
    ),
    "mixed-order-crlf": (
        b"# registry\r\n"
        b"[project:zeta]  \r\n"
        b"priority = 5\r\n"
        b"\r\n"
        b"[project:alpha]\r\n"
        b"priority = 1\r\n"
        b"[project:nopri]\t\r\n"
        b"[project:mid]\r\n"
        b"priority = 5\r\n"
    ),
}

_DRIVER = r"""
set -euo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
echo "priority: $(projects_cfg_list | paste -sd' ')"
echo "registration: $(projects_cfg_list --registration-order | paste -sd' ')"
echo "resolvable: $(_pp_registered_project_names "$KANBAN_ROOT/projects.cfg" | sort | paste -sd' ')"
for name in $HAS_NAMES; do
    if projects_cfg_has "$name"; then echo "has $name: yes"; else echo "has $name: no"; fi
done
"""


def _say(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _root(tmp_path: pathlib.Path, registry: bytes) -> pathlib.Path:
    root = tmp_path / "kanban"
    (root / "projects").mkdir(parents=True)
    (root / "projects.cfg").write_bytes(registry)
    return root


def _bash(tmp_path: pathlib.Path, root: pathlib.Path, script: str, **extra: str) -> subprocess.CompletedProcess:
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch),
            "LIB_DIR": str(_LIB_DIR),
            **extra,
        },
    )
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env,
        cwd=str(cwd), timeout=60, stdin=subprocess.DEVNULL, check=False,
    )


def _field(stdout: str, label: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(f"{label}:"):
            return line[len(label) + 1:].strip()
    raise AssertionError(f"no '{label}:' line in driver output:\n{stdout}")


@pytest.mark.parametrize("fixture", sorted(_REGISTRIES))
def test_bash_and_python_registry_readers_list_the_same_projects(tmp_path: pathlib.Path, fixture: str) -> None:
    root = _root(tmp_path, _REGISTRIES[fixture])
    entries = ini.projects_registry(root)
    by_priority = [entry.name for entry in entries]
    by_position = [entry.name for entry in sorted(entries, key=lambda entry: entry.section_index)]
    assert by_priority, f"fixture {fixture!r} registers no project for the Python reader"

    result = _bash(tmp_path, root, _DRIVER, HAS_NAMES=" ".join(by_priority))

    assert result.returncode == 0, _say(result)
    assert _field(result.stdout, "priority") == " ".join(by_priority), _say(result)
    assert _field(result.stdout, "registration") == " ".join(by_position), _say(result)
    assert _field(result.stdout, "resolvable") == " ".join(sorted(by_priority)), _say(result)
    for name in by_priority:
        assert f"has {name}: yes" in result.stdout, _say(result)


def test_a_header_with_trailing_spaces_lists_its_project(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, b"[project:p1]  \npriority = 1\n")

    result = _bash(tmp_path, root, 'source "$LIB_DIR/projects.sh"; projects_cfg_list')

    assert result.returncode == 0, _say(result)
    assert result.stdout == "p1\n", _say(result)


def test_crlf_values_read_without_the_carriage_return(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, b"[project:p1]\r\npriority = 3\r\nenabled = false\r\n")

    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/projects.sh"\n'
        'printf "priority=[%s]\\n" "$(projects_cfg_field p1 priority)"\n'
        'if projects_cfg_enabled p1; then echo enabled; else echo disabled; fi\n',
    )

    assert result.returncode == 0, _say(result)
    assert "priority=[3]" in result.stdout, _say(result)
    assert "disabled" in result.stdout, _say(result)
    assert ini.project_enabled(root, "p1") is False


def test_add_project_finds_a_header_with_trailing_whitespace_already_registered(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture
) -> None:
    registry = b"[project:p1]  \npriority=1\n"
    root = _root(tmp_path, registry)
    (root / "tmp").mkdir()
    project = root / "projects" / "p1"
    project.mkdir()
    (project / "README.md").write_text("# p1\n", encoding="utf-8")
    (project / "project.cfg").write_text("[project]\nproject_name = p1\n", encoding="utf-8")

    outcome = ops_projects.add_project(OpsContext(kanban_root=root), "p1", priority=1)

    assert not outcome.changed, capsys.readouterr().out
    assert (root / "projects.cfg").read_bytes() == registry


@pytest.mark.parametrize(
    "registry",
    [
        pytest.param(b"; comment\n[project:p1]\npriority = 1\n", id="semicolon-comment-first"),
        pytest.param(b"  [project:p1]\npriority = 1\n", id="indented-header"),
    ],
)
def test_comment_first_and_indented_registries_list_the_project_on_both_sides(
    tmp_path: pathlib.Path, registry: bytes
) -> None:
    root = _root(tmp_path, registry)

    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/projects.sh"\n'
        'echo "format: $(projects_cfg_format "$KANBAN_ROOT/projects.cfg")"\n'
        'echo "list: $(projects_cfg_list | paste -sd" ")"\n'
        '_projects_cfg_load\n',
    )

    assert [entry.name for entry in ini.projects_registry(root)] == ["p1"]
    assert result.returncode == 0, _say(result)
    assert _field(result.stdout, "format") == "ini", _say(result)
    assert _field(result.stdout, "list") == "p1", _say(result)
    assert "colon-format detected" not in result.stderr, _say(result)


def test_a_duplicated_section_is_listed_once_with_the_later_priority(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n[project:p1]\npriority = 2\n")

    entries = ini.projects_registry(root)
    result = _bash(tmp_path, root, 'source "$LIB_DIR/projects.sh"; projects_cfg_list')

    assert [(entry.name, entry.priority) for entry in entries] == [("p1", 2)]
    assert result.returncode == 0, _say(result)
    assert result.stdout == "p1\n", _say(result)


def test_the_shared_loader_still_refuses_a_duplicate_section_elsewhere(tmp_path: pathlib.Path) -> None:
    cfg = tmp_path / "project.cfg"
    cfg.write_text("[project]\nkey = 1\n[project]\nkey = 2\n", encoding="utf-8")

    with pytest.raises(ini.IniError):
        ini.load(cfg)


def test_a_colon_legacy_registry_still_reads_as_colon_legacy(tmp_path: pathlib.Path) -> None:
    root = _root(tmp_path, b"# registry\np1:1\n")

    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/projects.sh"\n'
        'echo "format: $(projects_cfg_format "$KANBAN_ROOT/projects.cfg")"\n'
        'projects_cfg_has p1 && echo "has p1: yes"\n'
        '_projects_cfg_load\n',
    )

    assert result.returncode == 0, _say(result)
    assert _field(result.stdout, "format") == "colon-legacy", _say(result)
    assert "has p1: yes" in result.stdout, _say(result)
    assert "colon-format detected" in result.stderr, _say(result)


def test_registry_reads_without_the_palette_file(tmp_path: pathlib.Path) -> None:
    install = tmp_path / "install"
    lib = install / "scripts" / "lib"
    lib.mkdir(parents=True)
    for script in sorted(_LIB_DIR.glob("*.sh")):
        (lib / script.name).write_bytes(script.read_bytes())
    assert not (install / "config" / "default_palette.txt").exists()
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")

    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/projects.sh"\nprojects_cfg_list\n',
        LIB_DIR=str(lib),
    )

    assert result.returncode == 0, _say(result)
    assert result.stdout == "p1\n", _say(result)
    assert "FATAL: canonical palette definition file not found" in result.stderr, _say(result)
    assert "project_header" not in result.stderr, _say(result)


@pytest.mark.parametrize(
    "registry",
    [
        pytest.param(b"  [project:p1]\npriority=1\n", id="indented-header"),
        pytest.param(b"; registry\n[project:p1]\npriority=1\n", id="semicolon-comment-first"),
    ],
)
def test_add_project_finds_an_indented_or_comment_first_registration(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture, registry: bytes
) -> None:
    root = _root(tmp_path, registry)
    (root / "tmp").mkdir()
    project = root / "projects" / "p1"
    project.mkdir()
    (project / "README.md").write_text("# p1\n", encoding="utf-8")
    (project / "project.cfg").write_text("[project]\nproject_name = p1\n", encoding="utf-8")

    outcome = ops_projects.add_project(OpsContext(kanban_root=root), "p1", priority=1)

    assert not outcome.changed, capsys.readouterr().out
    assert (root / "projects.cfg").read_bytes() == registry


# ---------------------------------------------------------------------------
# Per-field readers on a project registered by two sections: each key reads
# from the last section that sets it, on every bash and Python reader.
# ---------------------------------------------------------------------------

_FIELD_DRIVER = r"""
set -uo pipefail
source "$LIB_DIR/projects.sh"
echo "priority: $(projects_cfg_priority p1)"
if projects_cfg_enabled p1; then echo "enabled-rc: 0"; else echo "enabled-rc: 1"; fi
echo "max_rows: $(projects_cfg_field p1 max_rows)"
echo "description: $(projects_cfg_field p1 description)"
echo "dashboard_max_rows: $(projects_cfg_max_rows p1)"
color="$(_projects_cfg_read_raw_color p1)"; rc=$?
echo "raw_color: [$color] rc=$rc"
echo "color: $(projects_cfg_color p1)"
color="$(_projects_cfg_read_raw_color p2)"; rc=$?
echo "raw_color_p2: [$color] rc=$rc"
color="$(_projects_cfg_read_raw_color unregistered)"; rc=$?
echo "raw_color_unregistered: [$color] rc=$rc"
"""


def _entry(root: pathlib.Path, name: str) -> ini.ProjectEntry:
    entries = [entry for entry in ini.projects_registry(root) if entry.name == name]
    assert len(entries) == 1, f"expected one registry entry for {name!r}, got {entries!r}"
    return entries[0]


def test_a_duplicated_section_reads_priority_enabled_and_max_rows_from_the_later_section(
    tmp_path: pathlib.Path,
) -> None:
    root = _root(
        tmp_path,
        b"[project:p1]\npriority = 1\nenabled = true\nmax_rows = 5\n"
        b"[project:p1]\npriority = 7\nenabled = false\nmax_rows = 9\n",
    )

    result = _bash(tmp_path, root, _FIELD_DRIVER)

    assert result.returncode == 0, _say(result)
    assert _field(result.stdout, "priority") == "7", _say(result)
    assert _field(result.stdout, "enabled-rc") == "1", _say(result)
    assert _field(result.stdout, "max_rows") == "9", _say(result)
    assert [(entry.name, entry.priority) for entry in ini.projects_registry(root)] == [("p1", 7)]
    assert ini.project_enabled(root, "p1") is False
    registry = ops_projects._Registry(root)
    assert registry.priority("p1") == "7"
    assert registry._ini_field("p1", "enabled") == (True, "false")
    assert registry._ini_field("p1", "max_rows") == (True, "9")


def test_a_duplicated_section_reads_dashboard_fields_from_the_later_section(tmp_path: pathlib.Path) -> None:
    root = _root(
        tmp_path,
        b"[project:p1]\ndashboard_max_rows = 10\ndashboard_color = #111111\n"
        b"[project:p2]\npriority = 2\n"
        b"[project:p1]\ndashboard_max_rows = 30\ndashboard_color = #222222\n",
    )

    result = _bash(tmp_path, root, _FIELD_DRIVER)

    assert result.returncode == 0, _say(result)
    assert _field(result.stdout, "dashboard_max_rows") == "30", _say(result)
    assert _field(result.stdout, "raw_color") == "[#222222] rc=0", _say(result)
    assert _field(result.stdout, "color") == "#222222", _say(result)
    # The exit contract holds: registered without a colour is exit 0 and
    # empty, unregistered is exit 1.
    assert _field(result.stdout, "raw_color_p2") == "[] rc=0", _say(result)
    assert _field(result.stdout, "raw_color_unregistered") == "[] rc=1", _say(result)
    entry = _entry(root, "p1")
    assert entry.options["dashboard_max_rows"] == "30"
    assert entry.color == "#222222"
    registry = ops_projects._Registry(root)
    assert registry._ini_field("p1", "dashboard_max_rows") == (True, "30")
    assert registry._ini_field("p1", "dashboard_color") == (True, "#222222")
    assert registry.raw_color("p1") == "#222222"
    assert registry.raw_color("p2") == ""
    assert registry.raw_color("unregistered") is None


def test_a_key_only_the_earlier_duplicated_section_sets_keeps_its_value(tmp_path: pathlib.Path) -> None:
    root = _root(
        tmp_path,
        b"[project:p1]\npriority = 4\nenabled = false\ndescription = early\n"
        b"dashboard_max_rows = 12\ndashboard_color = #333333\n"
        b"[project:p1]\nmax_rows = 9\n",
    )

    result = _bash(tmp_path, root, _FIELD_DRIVER)

    assert result.returncode == 0, _say(result)
    entry = _entry(root, "p1")
    registry = ops_projects._Registry(root)
    assert entry.priority == 4
    assert _field(result.stdout, "priority") == "4", _say(result)
    assert _field(result.stdout, "enabled-rc") == "1", _say(result)
    assert ini.project_enabled(root, "p1") is False
    for key, label in (("description", "description"), ("dashboard_max_rows", "dashboard_max_rows")):
        assert _field(result.stdout, label) == entry.options[key], _say(result)
        assert registry._ini_field("p1", key) == (True, entry.options[key])
    assert _field(result.stdout, "raw_color") == f"[{entry.color}] rc=0", _say(result)
    assert registry.raw_color("p1") == entry.color == "#333333"
    assert _field(result.stdout, "max_rows") == entry.options["max_rows"] == "9", _say(result)


_BULK_DRIVER = r"""
set -uo pipefail
source "$LIB_DIR/projects.sh"
_projects_cfg_load
echo "priority: ${projects_priority[p1]}"
echo "enabled: ${projects_enabled[p1]}"
echo "description: ${projects_description[p1]}"
echo "max_rows: ${projects_max_rows[p1]}"
echo "color: ${projects_color[p1]}"
"""


def test_the_bulk_loader_merges_a_duplicated_section_per_key(tmp_path: pathlib.Path) -> None:
    root = _root(
        tmp_path,
        b"[project:p1]\npriority = 4\nenabled = false\ndescription = early\n"
        b"dashboard_max_rows = 12\ndashboard_color = #333333\n"
        b"[project:p1]\nmax_rows = 9\n",
    )

    result = _bash(tmp_path, root, _BULK_DRIVER)

    assert result.returncode == 0, _say(result)
    entry = _entry(root, "p1")
    bulk = {label: _field(result.stdout, label) for label in ("priority", "enabled", "description", "max_rows", "color")}
    assert bulk == {
        "priority": "4",
        "enabled": "false",
        "description": "early",
        "max_rows": "12",
        "color": "#333333",
    }, _say(result)
    assert bulk["priority"] == str(entry.priority)
    assert bulk["enabled"] == str(ini.project_enabled(root, "p1")).lower()
    assert bulk["description"] == entry.options["description"]
    assert bulk["max_rows"] == entry.options["dashboard_max_rows"]
    assert bulk["color"] == entry.color


# ---------------------------------------------------------------------------
# The [project:NAME] header rule: one per language, and the two agree
# ---------------------------------------------------------------------------

# header line -> the name both rules read from it ("" for not a header)
_HEADER_LINES: dict[bytes, str] = {
    b"[project:p1]": "p1",
    b"[project:p1]  ": "p1",
    b"[project:p1]\t": "p1",
    b"[project:p1]\r": "p1",
    b"[project:p1] \r": "p1",
    b"  [project:p1]": "p1",
    b"\t[project:p1]": "p1",
    b" \t[project:my-app-2] \t\r": "my-app-2",
    b"[project:2nd]": "2nd",
    b"[project:my_app-2]": "",
    b"[project:My-App]": "",
    b"[project:-p1]": "",
    b"[project:alpha] # first": "",
    b"[project:p 1]": "",
    b"[project:]": "",
    b"[project:p1] x": "",
    b"[projects:p1]": "",
    b"[project:p1]]": "",
    b"# [project:p1]": "",
}


def test_bash_and_python_header_rules_read_every_variant_alike(tmp_path: pathlib.Path) -> None:
    """PROJECTS_CFG_AWK_HEADER (the bash rule) and ops.projects._project_header
    (the Python rule) give the same answer for each header variant."""
    lines = list(_HEADER_LINES)
    (tmp_path / "headers.txt").write_bytes(b"\n".join(lines) + b"\n")
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")
    script = (
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'awk "${PROJECTS_CFG_AWK_HEADER}"\'{ h = project_header($0); print (h == "" ? "-" : h) }\' '
        f'"{tmp_path / "headers.txt"}"\n'
    )
    result = _bash(tmp_path, root, script)
    assert result.returncode == 0, _say(result)
    bash_answers = ["" if out == "-" else out for out in result.stdout.splitlines()]

    python_answers = []
    for line in lines:
        header = ops_projects._project_header(line.decode("utf-8"))
        python_answers.append(header if header is not None else "")

    expected = [_HEADER_LINES[line] for line in lines]
    assert bash_answers == expected, list(zip(lines, bash_answers))
    assert python_answers == expected, list(zip(lines, python_answers))


# header line -> the section name both recognisers read from it (None for
# not a [project:...] section at all)
_SECTION_LINES: dict[bytes, "str | None"] = {
    b"[project:p1]": "p1",
    b"  [project:p1] \t\r": "p1",
    b"[project:bad.name]": "bad.name",
    b"[project:bad name]": "bad name",
    b"[project: padded ]": " padded ",
    b"[project:]": "",
    b"[project:p1]]": "p1]",
    b"[project:a/b]  ": "a/b",
    b"[project:p1] x": None,
    b"[projects:p1]": None,
    b"# [project:p1]": None,
}


def test_bash_and_python_recognise_the_same_project_sections(tmp_path: pathlib.Path) -> None:
    """project_section / project_section_name (bash) and _PROJECT_SECTION_RE
    (Python) recognise the same [project:...] headers, whatever their name,
    and read the same name from each."""
    lines = list(_SECTION_LINES)
    (tmp_path / "headers.txt").write_bytes(b"\n".join(lines) + b"\n")
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")
    script = (
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'awk "${PROJECTS_CFG_AWK_HEADER}"\'{ print (project_section($0) ? "+" project_section_name($0) "|" : "-") }\' '
        f'"{tmp_path / "headers.txt"}"\n'
    )
    result = _bash(tmp_path, root, script)
    assert result.returncode == 0, _say(result)
    bash_answers = [None if out == "-" else out[1:-1] for out in result.stdout.splitlines()]

    python_answers = []
    for line in lines:
        match = ops_projects._PROJECT_SECTION_RE.match(line.decode("utf-8"))
        python_answers.append(match.group(1) if match else None)

    expected = [_SECTION_LINES[line] for line in lines]
    assert bash_answers == expected, list(zip(lines, bash_answers))
    assert python_answers == expected, list(zip(lines, python_answers))


def test_bash_and_python_name_the_same_unaccepted_sections(tmp_path: pathlib.Path) -> None:
    """A registry mixing accepted and unaccepted [project:...] sections: both
    sides list only the accepted names as projects and name the unaccepted
    ones alike -- in file order, once each, as written."""
    registry = (
        b"[project:alpha]\npriority = 2\n"
        b"[project:bad.name]\npriority = 1\n"
        b"  [project:bad name] \r\n"
        b"# [project:commented.out]\n"
        b"[project:]\n"
        b"[project:bad.name]\n"
        b"[project:beta]\npriority = 3\n"
    )
    root = _root(tmp_path, registry)
    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'echo "--list"; projects_cfg_list --registration-order\n'
        'echo "--unaccepted"; projects_cfg_unaccepted_names; echo "rc=$?"\n',
    )
    assert result.returncode == 0, _say(result)
    listed, _sep, rest = result.stdout.partition("--unaccepted\n")
    bash_listed = listed.splitlines()[1:]
    bash_unaccepted = rest.splitlines()
    assert bash_unaccepted[-1] == "rc=0", _say(result)
    bash_unaccepted = bash_unaccepted[:-1]

    expected_unaccepted = ["bad.name", "bad name", ""]
    assert bash_listed == ["alpha", "beta"], _say(result)
    assert bash_unaccepted == expected_unaccepted, _say(result)
    assert ops_projects.registered_project_names(root) == ["alpha", "beta"]
    assert ops_projects.unaccepted_project_names(root) == expected_unaccepted

    ini_listed = [entry.name for entry in ini.projects_registry(root)]
    assert ini_listed == ["alpha", "beta"]
    for name in ops_projects.unaccepted_project_names(root):
        assert name not in ini_listed, (name, ini_listed)


def test_ini_projects_registry_lists_what_the_other_registry_readers_list(tmp_path: pathlib.Path) -> None:
    """[project:bad.name] and [project:bad name], each with a priority that
    would sort it between or ahead of the accepted projects, are listed by
    none of ini.projects_registry, ops.projects.registered_project_names and
    projects_cfg_list."""
    root = _root(
        tmp_path,
        b"[project:alpha]\npriority = 1\n\n"
        b"[project:bad.name]\npriority = 9\n\n"
        b"[project:bad name]\npriority = 5\n\n"
        b"[project:beta]\npriority = 2\n",
    )
    result = _bash(tmp_path, root, 'source "$LIB_DIR/projects.sh"; projects_cfg_list')
    assert result.returncode == 0, _say(result)

    ini_listed = [entry.name for entry in ini.projects_registry(root)]
    assert ini_listed == ops_projects.registered_project_names(root) == result.stdout.split()
    assert ini_listed == ["alpha", "beta"]


def test_ini_and_ops_projects_accept_the_same_section_names() -> None:
    """ini.projects_registry's name rule is the one ops.projects applies: the
    same answer for every recognised section name."""
    names = [n for n in _SECTION_LINES.values() if n is not None]
    names += ["My_App", "my_app", "my-app", "2nd", "-p1"]
    for name in names:
        ini_accepts = ini.is_project_name(name)
        ops_accepts = ops_projects._project_header(f"[project:{name}]") is not None
        assert ini_accepts == ops_accepts, name


def test_python_name_rule_is_the_bash_definition(tmp_path: pathlib.Path) -> None:
    """ini.project_name_rule is read from PROJECTS_CFG_NAME_RE in
    lib/projects_cfg.sh: the sourced bash variable and the Python pattern are
    the same text."""
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")
    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'printf "%s\\n" "$PROJECTS_CFG_NAME_RE"\n',
    )
    assert result.returncode == 0, _say(result)
    assert result.stdout.splitlines() == [ini.project_name_rule().pattern], _say(result)
    assert ini.PROJECT_NAME_RULE_FILE == _LIB_DIR / "projects_cfg.sh"


@pytest.mark.parametrize(
    "content",
    [None, b"#!/usr/bin/env bash\n# PROJECTS_CFG_NAME_RE is documented here but not assigned\n"],
    ids=["file-missing", "no-assignment"],
)
def test_python_name_rule_refuses_without_its_definition(tmp_path: pathlib.Path, content: "bytes | None") -> None:
    """A tree whose projects_cfg.sh is missing, or holds no
    PROJECTS_CFG_NAME_RE assignment, raises ProjectNameRuleError naming the
    file -- never a guessed rule."""
    rule_file = tmp_path / "projects_cfg.sh"
    if content is not None:
        rule_file.write_bytes(content)
    with pytest.raises(ini.ProjectNameRuleError) as raised:
        ini._load_project_name_rule(str(rule_file))
    text = str(raised.value)
    assert "ini.project_name_rule_missing" in text
    assert str(rule_file) in text


def test_a_header_with_a_trailing_comment_is_no_project_in_any_reader(tmp_path: pathlib.Path) -> None:
    """'[project:alpha] # first' then '[project:beta]': ini.projects_registry,
    ops.projects.registered_project_names and projects_cfg_list all list only
    beta, and alpha's priority moves nothing. Neither unaccepted-name
    reporter names the commented header: it is no [project:...] section."""
    root = _root(
        tmp_path,
        b"[project:alpha] # first\npriority = 1\n\n[project:beta]\npriority = 2\n",
    )
    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'echo "list: $(projects_cfg_list | paste -sd" ")"\n'
        'echo "unaccepted: $(projects_cfg_unaccepted_names | paste -sd"|")"\n',
    )
    assert result.returncode == 0, _say(result)

    assert [(e.name, e.priority) for e in ini.projects_registry(root)] == [("beta", 2)]
    assert ops_projects.registered_project_names(root) == ["beta"]
    assert _field(result.stdout, "list") == "beta", _say(result)
    assert _field(result.stdout, "unaccepted") == "", _say(result)
    assert ops_projects.unaccepted_project_names(root) == []


_NAME_RULE_DRIVER = r"""
set -uo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
echo "list: $(projects_cfg_list | paste -sd' ')"
echo "registration: $(projects_cfg_list --registration-order | paste -sd' ')"
echo "resolvable: $(_pp_registered_project_names "$KANBAN_ROOT/projects.cfg" | sort | paste -sd' ')"
echo "unaccepted: $(projects_cfg_unaccepted_names | paste -sd'|')"
for name in My_App my_app my-app; do
    if projects_cfg_has "$name"; then echo "has $name: yes"; else echo "has $name: no"; fi
    echo "priority $name: $(projects_cfg_priority "$name")"
done
declare -A projects_priority projects_color projects_max_rows projects_enabled projects_description
_projects_cfg_parse_ini "$(projects_cfg_path)" 2>/dev/null
echo "bulk: $(printf '%s\n' "${!projects_enabled[@]}" | sort | paste -sd' ')"
"""


def test_mixed_case_and_underscore_names_are_rejected_by_every_reader(tmp_path: pathlib.Path) -> None:
    """[project:My_App] and [project:my_app] fail the strict project-name
    rule: no reader lists them (projects_cfg_list, projects_cfg_has, the
    bulk loader, project-name resolution, ini.projects_registry,
    ops.projects.registered_project_names) and both unaccepted-name
    reporters name them, in file order. [project:my-app] is listed by every
    reader."""
    root = _root(
        tmp_path,
        b"[project:My_App]\npriority = 1\n\n"
        b"[project:my_app]\npriority = 2\n\n"
        b"[project:my-app]\npriority = 3\n",
    )
    result = _bash(tmp_path, root, _NAME_RULE_DRIVER)
    assert result.returncode == 0, _say(result)

    for label in ("list", "registration", "resolvable", "bulk"):
        assert _field(result.stdout, label) == "my-app", (label, _say(result))
    assert "has my-app: yes" in result.stdout, _say(result)
    assert "has My_App: no" in result.stdout, _say(result)
    assert "has my_app: no" in result.stdout, _say(result)
    assert _field(result.stdout, "priority my-app") == "3", _say(result)
    assert _field(result.stdout, "priority My_App") == "", _say(result)
    assert _field(result.stdout, "unaccepted") == "My_App|my_app", _say(result)

    assert [e.name for e in ini.projects_registry(root)] == ["my-app"]
    assert ops_projects.registered_project_names(root) == ["my-app"]
    assert ops_projects.unaccepted_project_names(root) == ["My_App", "my_app"]


@pytest.mark.parametrize("name", ["My_App", "my_app"])
def test_registry_verbs_refuse_a_name_the_readers_would_not_list(tmp_path: pathlib.Path, name: str) -> None:
    """ops.projects' add/remove and create name checks take the same rule:
    a name no reader lists is refused, my-app is accepted."""
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")
    for verb in ("add_project", "remove_project"):
        with pytest.raises(ops_projects.ProjectValueRefused) as raised:
            ops_projects.check_project_name(verb, name, root)
        assert f"{verb}.project_invalid" in str(raised.value)
        ops_projects.check_project_name(verb, "my-app", root)
    with pytest.raises(ops_projects.ProjectValueRefused) as raised:
        ops_projects.check_create_values(project=name, kanban_root=root)
    assert "create_project.project_invalid" in str(raised.value)
    ops_projects.check_create_values(project="my-app", kanban_root=root)


_REJECTED_SECTIONS_DRIVER = r"""
set -uo pipefail
source "$LIB_DIR/project_paths.sh"
source "$LIB_DIR/projects.sh"
echo "priority: $(projects_cfg_list | paste -sd' ')"
echo "registration: $(projects_cfg_list --registration-order | paste -sd' ')"
echo "priority alpha: $(projects_cfg_priority alpha)"
echo "priority beta: $(projects_cfg_priority beta)"
echo "color alpha: $(projects_cfg_color alpha)"
echo "color beta: $(projects_cfg_color beta)"
echo "unaccepted: $(projects_cfg_unaccepted_names | paste -sd'|')"
"""

# The two accepted sections, alone and with rejected [project:...] sections
# between them that set a priority and a colour of their own.
_ACCEPTED_ONLY = b"[project:alpha]\npriority = 1\n\n[project:beta]\npriority = 2\n"
_WITH_REJECTED_SECTIONS = (
    b"[project:alpha]\n"
    b"priority = 1\n"
    b"\n"
    b"[project:bad.name]\n"
    b"priority = 9\n"
    b"dashboard_color = #ABCDEF\n"
    b"\n"
    b"[project:bad name]\n"
    b"priority = 5\n"
    b"\n"
    b"[project:beta]\n"
    b"priority = 2\n"
)


def _rejected_sections_reading(tmp_path: pathlib.Path, registry: bytes) -> tuple[dict[str, str], dict]:
    """The bash driver's fields and the Python readers' answers for *registry*."""
    root = _root(tmp_path, registry)
    result = _bash(tmp_path, root, _REJECTED_SECTIONS_DRIVER)
    assert result.returncode == 0, _say(result)
    labels = (
        "priority", "registration", "priority alpha", "priority beta",
        "color alpha", "color beta", "unaccepted",
    )
    bash = {label: _field(result.stdout, label) for label in labels}
    registry_reader = ops_projects._Registry(root)
    python = {
        "listed": ops_projects.registered_project_names(root),
        "priority alpha": registry_reader.priority("alpha"),
        "color alpha": ops_projects.project_color(root, "alpha"),
        "color beta": ops_projects.project_color(root, "beta"),
        "dashboard": ops_projects.dashboard_registry_rows(root, default_max_rows="20"),
        "unaccepted": ops_projects.unaccepted_project_names(root),
        "ini": [(entry.name, entry.priority, entry.color) for entry in ini.projects_registry(root)],
    }
    return bash, python


def test_a_rejected_project_section_ends_the_section_before_it_on_both_sides(tmp_path: pathlib.Path) -> None:
    """[project:bad.name] and [project:bad name] end alpha's section: their
    priority and colour keys belong to no project, so both readers (and
    ini.projects_registry) list alpha before beta, alpha keeps priority 1,
    and every colour reads as it does with the rejected sections removed. The rejected names are still
    reported as unaccepted."""
    baseline_bash, baseline_python = _rejected_sections_reading(tmp_path / "accepted-only", _ACCEPTED_ONLY)
    bash, python = _rejected_sections_reading(tmp_path / "with-rejected", _WITH_REJECTED_SECTIONS)

    assert bash["priority"] == "alpha beta", bash
    assert bash["registration"] == "alpha beta", bash
    assert python["listed"] == ["alpha", "beta"], python
    assert bash["priority alpha"] == "1", bash
    assert bash["priority beta"] == "2", bash
    assert python["priority alpha"] == "1", python

    for name in ("alpha", "beta"):
        label = f"color {name}"
        assert bash[label] == baseline_bash[label], (bash, baseline_bash)
        assert python[label] == baseline_python[label] == bash[label], (python, baseline_python, bash)
        assert bash[label] != "#ABCDEF", bash
    assert python["dashboard"] == baseline_python["dashboard"], (python, baseline_python)
    assert python["ini"] == baseline_python["ini"], (python, baseline_python)
    assert [(name, priority) for name, priority, _color in python["ini"]] == [("alpha", 1), ("beta", 2)], python

    assert bash["unaccepted"] == "bad.name|bad name", bash
    assert python["unaccepted"] == ["bad.name", "bad name"], python
    assert baseline_bash["unaccepted"] == "" and baseline_python["unaccepted"] == []


def test_a_registry_with_only_accepted_names_names_no_unaccepted_section(tmp_path: pathlib.Path) -> None:
    """Accepted names only (and an absent projects.cfg): both sides name
    nothing, and the bash reader still exits 0."""
    root = _root(tmp_path, b"[project:alpha]\npriority = 1\n[project:0beta]\n")
    script = (
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'projects_cfg_unaccepted_names; echo "rc=$?"\n'
        'rm -f "$KANBAN_ROOT/projects.cfg"; projects_cfg_unaccepted_names; echo "rc=$?"\n'
    )
    assert ops_projects.unaccepted_project_names(root) == []
    result = _bash(tmp_path, root, script)
    assert result.returncode == 0, _say(result)
    assert result.stdout.splitlines() == ["rc=0", "rc=0"], _say(result)
    assert ops_projects.unaccepted_project_names(root) == []


def test_project_name_resolution_reads_the_registry_through_projects_cfg_list(tmp_path: pathlib.Path) -> None:
    """_pp_registered_project_names carries no registry parser of its own: with
    projects_cfg_list replaced, it reports what the replacement lists."""
    root = _root(tmp_path, b"[project:p1]\npriority = 1\n")
    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/project_paths.sh"\n'
        'source "$LIB_DIR/projects.sh"\n'
        'projects_cfg_list() { echo from-the-one-reader; }\n'
        '_pp_registered_project_names "$KANBAN_ROOT/projects.cfg"\n',
    )
    assert result.returncode == 0, _say(result)
    assert result.stdout.splitlines() == ["from-the-one-reader"], _say(result)


def test_project_name_resolution_loads_the_registry_reader_on_first_use(tmp_path: pathlib.Path) -> None:
    """With only project_paths.sh sourced, prefix resolution still reads the
    registry (lib/projects.sh is sourced on demand)."""
    root = _root(tmp_path, b"[project:alpha-one]\npriority = 1\n[project:beta]\npriority = 2\n")
    result = _bash(
        tmp_path, root,
        'source "$LIB_DIR/project_paths.sh"\n'
        'pp_resolve_project_name alpha\n'
        'declare -F projects_cfg_list >/dev/null && echo leaked || echo contained\n',
    )
    assert result.returncode == 0, _say(result)
    assert result.stdout.splitlines() == ["alpha-one", "contained"], _say(result)
