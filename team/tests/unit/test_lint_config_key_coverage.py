"""
test_lint_config_key_coverage.py
================================
team/scripts/lint_config_key_coverage.py cross-checks the kanban.cfg keys
the code reads, the keys kanban.cfg_example scaffolds and the keys the
loader's Key Registry accepts. It reports (a) a read key the example does
not scaffold, (b) an example key nothing reads and (c) a registry key
nothing reads, and always exits 0 (quality class). It applies the same
cross-check to project.cfg -- against the project.cfg template and the text
create_project renders -- and checks each template key's stated default,
section and commented value against the Key Registries, and reports a key
read only into an export nothing consumes. Python reads are call sites.

Fixtures are synthetic repository trees under pytest's tmp_path: a
kanban.cfg_example, a config_loader.sh carrying the registry, and reader
scripts; the project.cfg fixtures add a template, a project_paths.sh
registry and, where needed, a fixture create_project generator. Each of
those produces exactly the one finding it is built for. The install test
runs the real install.sh into a throwaway root and checks the kanban.cfg it
seeds against the keys the lint finds read in this checkout.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# This file lives at team/tests/unit/; team/ is three parents up.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_config_key_coverage.py"
_INSTALL_SH = _REPO_ROOT / "install.sh"

_spec = importlib.util.spec_from_file_location("lint_config_key_coverage", _LINT_SCRIPT)
assert _spec is not None and _spec.loader is not None
lint = importlib.util.module_from_spec(_spec)
sys.modules["lint_config_key_coverage"] = lint
_spec.loader.exec_module(lint)


_EXAMPLE = """\
# kanban.cfg_example fixture
[wake]
# Tasks per wake. Default: 5
max_tasks_per_wake = 5
# Stop file path. Default: unset
# stop_file =

[dashboard]
# Session name. Default: pgai
session_name = pgai
"""

_REGISTRY = """\
#!/usr/bin/env bash
# Key Registry
# [wake]
#   max_tasks_per_wake      OPTIONAL  5
#   stop_file               OPTIONAL  ""
# [dashboard]
#   session_name            OPTIONAL  pgai
# [paths]
#   old_spelling            OPTIONAL  30  (DEPRECATED spelling of [wake] something)
_CONFIG_LOADER_KNOWN_KEYS=(
    wake:max_tasks_per_wake wake:stop_file
    dashboard:session_name
    paths:old_spelling
)
load_config() {
    local cfg="$1"
    export MAX_TASKS_PER_WAKE="$(read_ini "$cfg" wake max_tasks_per_wake 5)"
    export STOP_FILE="$(read_ini "$cfg" wake stop_file "")"
    legacy="$(read_ini "$cfg" paths old_spelling "")"
}
"""

_READER = """\
#!/usr/bin/env bash
# read_ini "$KANBAN_ROOT/kanban.cfg" commented never_counts 1
name="$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard session_name pgai)"
"""

# Reads the variables load_config exports, as the wake scripts do in a real
# tree; without a reader each export would be a read_but_unconsumed finding.
_CONSUMER = """\
#!/usr/bin/env bash
echo "wake: ${MAX_TASKS_PER_WAKE} tasks, stop file ${STOP_FILE}"
"""


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _tree(tmp_path: pathlib.Path, *, example: str = _EXAMPLE, registry: str = _REGISTRY,
          extra: "dict[str, str] | None" = None) -> pathlib.Path:
    """Build a minimal repository tree with the three key sources."""
    root = tmp_path / "repo"
    _write(root / "kanban.cfg_example", example)
    _write(root / "team" / "scripts" / "lib" / "config_loader.sh", registry)
    _write(root / "team" / "scripts" / "reader.sh", _READER)
    _write(root / "team" / "scripts" / "consumer.sh", _CONSUMER)
    for rel, text in (extra or {}).items():
        _write(root / rel, text)
    return root


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _run_cli(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, timeout=120, cwd=str(root),
    )


def _kinds(root: pathlib.Path) -> "set[tuple[str, str, str]]":
    return {(f.kind, f.section, f.key) for f in lint.run(root).findings}


def test_consistent_tree_reports_zero_findings_and_exits_zero(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path)
    findings_file = tmp_path / "out" / "findings.json"
    result = _run_cli(root, "--findings-file", str(findings_file))
    assert result.returncode == 0, _diagnostic(result)
    assert "0 finding(s)" in result.stdout, _diagnostic(result)
    assert json.loads(findings_file.read_text(encoding="utf-8")) == []


def test_read_key_absent_from_example_is_reported_at_its_read_site(tmp_path: pathlib.Path) -> None:
    root = _tree(tmp_path, extra={
        "team/scripts/extra.sh": (
            "#!/usr/bin/env bash\n"
            'rows="$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard rows_per_column 21)"\n'
        ),
    })
    findings_file = tmp_path / "out" / "findings.json"
    result = _run_cli(root, "--findings-file", str(findings_file))
    assert result.returncode == 0, _diagnostic(result)
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert [(f["kind"], f["section"], f["key"], f["file"], f["line"]) for f in findings] == [
        ("read_not_scaffolded", "dashboard", "rows_per_column", "team/scripts/extra.sh", 2),
    ]
    assert findings[0]["severity"] == "quality" and findings[0]["lint"] == "config_key_coverage"
    # The remedy opens the example at the [dashboard] header (line 8): runnable, no placeholder.
    assert findings[0]["remedy"] == "${EDITOR:-vi} +8 kanban.cfg_example"
    assert "team/scripts/extra.sh:2: read_not_scaffolded" in result.stdout, _diagnostic(result)


def test_example_key_no_code_reads_is_reported(tmp_path: pathlib.Path) -> None:
    example = _EXAMPLE + "\n# Unused tunable. Default: 3\nrefresh_seconds = 3\n"
    root = _tree(tmp_path, example=example)
    assert _kinds(root) == {("example_not_read", "dashboard", "refresh_seconds")}
    result = _run_cli(root)
    assert result.returncode == 0, _diagnostic(result)
    assert "kanban.cfg_example:13: example_not_read" in result.stdout, _diagnostic(result)


def test_registry_key_no_code_reads_is_reported(tmp_path: pathlib.Path) -> None:
    registry = _REGISTRY.replace("dashboard:session_name\n", "dashboard:session_name chain:cron_stagger\n")
    root = _tree(tmp_path, registry=registry)
    assert _kinds(root) == {("registry_not_read", "chain", "cron_stagger")}
    findings = lint.run(root).findings
    assert findings[0].file == "team/scripts/lib/config_loader.sh"
    assert findings[0].remedy == f"${{EDITOR:-vi}} +{findings[0].line} team/scripts/lib/config_loader.sh"


def test_commented_example_line_scaffolds_a_key_and_comment_prose_does_not(tmp_path: pathlib.Path) -> None:
    # stop_file is scaffolded only as "# stop_file =" and is read: no finding.
    # The commented-out read_ini in reader.sh is not a read site: no finding.
    # "# default = words of prose" is not a key line: no example_not_read.
    example = _EXAMPLE.replace("# Session name.", "# default = model used for any role below.\n# Session name.")
    assert _kinds(_tree(tmp_path, example=example)) == set()


def test_deprecated_registry_key_is_not_reported_as_unscaffolded(tmp_path: pathlib.Path) -> None:
    # paths:old_spelling is read and absent from the example, but its
    # registry comment marks it DEPRECATED.
    assert _kinds(_tree(tmp_path)) == set()
    undeprecated = _REGISTRY.replace("(DEPRECATED spelling of [wake] something)", "")
    assert _kinds(_tree(tmp_path / "b", registry=undeprecated)) == {
        ("read_not_scaffolded", "paths", "old_spelling"),
    }


def test_wrapper_key_resolves_through_literal_callers(tmp_path: pathlib.Path) -> None:
    registry = _REGISTRY.replace(
        '    export MAX_TASKS_PER_WAKE="$(read_ini "$cfg" wake max_tasks_per_wake 5)"\n',
        '    _export_wake_integer "$cfg" max_tasks_per_wake MAX_TASKS_PER_WAKE 5\n'
        '    _export_wake_integer "$cfg" pause_seconds PAUSE_SECONDS 5\n',
    ) + (
        "_export_wake_integer() {\n"
        '    local cfg="$1" key="$2" env_name="$3" default="$4"\n'
        '    value="$(read_ini "$cfg" wake "$key" "")"\n'
        "}\n"
    )
    root = _tree(tmp_path, registry=registry)
    findings = lint.run(root).findings
    assert [(f.kind, f.section, f.key, f.file) for f in findings] == [
        ("read_not_scaffolded", "wake", "pause_seconds", "team/scripts/lib/config_loader.sh"),
    ]
    # Drop the caller: max_tasks_per_wake is then read by nothing.
    no_caller = registry.replace('    _export_wake_integer "$cfg" max_tasks_per_wake MAX_TASKS_PER_WAKE 5\n', "")
    assert ("example_not_read", "wake", "max_tasks_per_wake") in _kinds(_tree(tmp_path / "b", registry=no_caller))


def test_registry_array_is_not_evidence_for_a_wholly_variable_read(tmp_path: pathlib.Path) -> None:
    # A top-level "$key" read counts a key only when the file's code names
    # it; the registry array naming wake:max_tasks_per_wake does not.
    registry = _REGISTRY.replace(
        '    export MAX_TASKS_PER_WAKE="$(read_ini "$cfg" wake max_tasks_per_wake 5)"\n', "",
    ) + 'value="$(read_ini "$cfg" wake "$key" "")"\n'
    assert ("registry_not_read", "wake", "max_tasks_per_wake") in _kinds(_tree(tmp_path, registry=registry))
    named = registry + "key=max_tasks_per_wake\n"
    assert ("registry_not_read", "wake", "max_tasks_per_wake") not in _kinds(_tree(tmp_path / "b", registry=named))


def test_template_section_reads_the_whole_key_family(tmp_path: pathlib.Path) -> None:
    example = _EXAMPLE + "\n[models.claude]\n# Model. Default: unset\ndefault =\npm =\n"
    registry = _REGISTRY.replace("paths:old_spelling\n", "paths:old_spelling models.claude:default models.claude:pm\n")
    loader = (
        "#!/usr/bin/env bash\n"
        "load_models() {\n"
        '    local _section="models.${provider}"\n'
        '    fallback="$(read_ini "$cfg" "$_section" "default" "")"\n'
        "    for _role in pm coder; do\n"
        '        model="$(read_ini "$cfg" "$_section" "$_role" "")"\n'
        "    done\n"
        "}\n"
    )
    root = _tree(tmp_path, example=example, registry=registry, extra={"team/scripts/lib/models.sh": loader})
    assert _kinds(root) == set()
    assert {("models.claude", "default"), ("models.claude", "pm")} <= lint.run(root).kanban_read_keys


def test_project_cfg_reader_satisfies_registry_but_is_not_a_kanban_read(tmp_path: pathlib.Path) -> None:
    registry = _REGISTRY.replace("paths:old_spelling\n", "paths:old_spelling release:offhours_window\n")
    reader = (
        "#!/usr/bin/env bash\n"
        'raw="$(_pp_read_cfg_key "$cfg_file" release offhours_window "")"\n'
        'name="$(read_ini "$project_cfg" project project_name "")"\n'
    )
    root = _tree(tmp_path, registry=registry, extra={"team/scripts/lib/paths.sh": reader})
    result = lint.run(root)
    assert {(f.kind, f.section, f.key) for f in result.findings} == set()
    assert ("release", "offhours_window") not in result.kanban_read_keys
    assert ("release", "offhours_window") in result.read_keys


def test_python_module_reading_kanban_cfg_marks_its_keys_read(tmp_path: pathlib.Path) -> None:
    # The read is tied to its call site: the parser is loaded from a path
    # that names kanban.cfg, and the section comes from a module constant.
    example = _EXAMPLE + "\n[api]\n# Port. Default: 8300\nport = 8300\n"
    registry = _REGISTRY.replace("paths:old_spelling\n", "paths:old_spelling api:port\n")
    module = (
        '"""Reads [api] port from kanban.cfg -- this docstring is not evidence."""\n'
        "from pgai_agent_kanban.lib import ini\n"
        '_SECTION = "api"\n'
        "def port(kanban_root):\n"
        '    parser = ini.load(kanban_root / "kanban.cfg")\n'
        '    return parser.get(_SECTION, "port")\n'
    )
    root = _tree(tmp_path, example=example, registry=registry,
                 extra={"team/pgai_agent_kanban/api/config.py": module})
    assert _kinds(root) == set()
    assert ("api", "port") in lint.run(root).kanban_read_keys
    # A kanban.cfg literal elsewhere in the module does not make an unrelated
    # parser a kanban.cfg reader: module-wide mentions do not count.
    unrelated = module.replace('ini.load(kanban_root / "kanban.cfg")', 'ini.load(kanban_root / "settings.ini")')
    unrelated += 'CFG = "kanban.cfg"\n'
    root_b = _tree(tmp_path / "b", example=example, registry=registry,
                   extra={"team/pgai_agent_kanban/api/config.py": unrelated})
    assert ("example_not_read", "api", "port") in _kinds(root_b)


def test_test_trees_are_not_scanned(tmp_path: pathlib.Path) -> None:
    fixture = (
        "#!/usr/bin/env bash\n"
        'x="$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard fixture_only 1)"\n'
    )
    root = _tree(tmp_path, extra={"team/tests/unit/fixture.sh": fixture})
    assert _kinds(root) == set()


def test_missing_example_narrates_and_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path)
    assert result.returncode == 0, _diagnostic(result)
    assert "no kanban.cfg_example" in result.stdout, _diagnostic(result)


def _comment_block(lines: "list[str]", key_line: int) -> "list[str]":
    """Comment lines documenting the key on 1-based *key_line*.

    Walks up from the key over blank lines and sibling key lines (a group of
    keys sharing one comment), then collects the contiguous comment run,
    stopping at the section header.
    """
    block: "list[str]" = []
    index = key_line - 2
    while index >= 0:
        text = lines[index]
        if lint._SECTION_RE.match(text):
            break
        is_comment = text.lstrip().startswith(("#", ";")) and not lint._COMMENTED_KEY_RE.match(text)
        if is_comment:
            block.append(text)
        elif block:
            break
        index -= 1
    return block


def test_fresh_install_seeds_every_read_key_with_a_comment_naming_its_default(tmp_path: pathlib.Path) -> None:
    # Every key this checkout reads from kanban.cfg (minus registry-DEPRECATED
    # fallbacks, which are deliberately not scaffolded) must reach a fresh
    # install's kanban.cfg, documented by a comment block naming its default.
    root = tmp_path / "root"
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
        },
    )
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    result = subprocess.run(
        ["bash", str(_INSTALL_SH), "--force", "--no-system-cron", "--stamp-version", "v0.0.0-sandbox-test"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(tmp_path),
    )
    seeded = root / "kanban.cfg"
    assert result.returncode == 0 and seeded.is_file(), _diagnostic(result)

    _, deprecated = lint.parse_registry(_REPO_ROOT / lint.REGISTRY_REL)
    read_keys = lint.run(_REPO_ROOT).kanban_read_keys - deprecated
    assert len(read_keys) > 50, f"the lint found implausibly few kanban.cfg reads: {sorted(read_keys)}"

    scaffolded = lint.parse_example(seeded)
    lines = seeded.read_text(encoding="utf-8").splitlines()
    missing = sorted(f"[{s}] {k}" for s, k in read_keys if (s, k) not in scaffolded)
    assert not missing, f"keys read from kanban.cfg but absent from the seeded {seeded}: {missing}"
    undocumented = sorted(
        f"[{s}] {k} (line {scaffolded[(s, k)]})" for s, k in read_keys
        if not any("default" in text.lower() for text in _comment_block(lines, scaffolded[(s, k)]))
    )
    assert not undocumented, f"keys whose comment block in the seeded kanban.cfg names no default: {undocumented}"


# ---------------------------------------------------------------------------
# project.cfg, defaults, placement, commented values, unconsumed exports and
# call-site Python reads. Each fixture adds a project.cfg template, the
# project_paths.sh Key Registry and a reader to the kanban.cfg tree above,
# and produces exactly the one finding it is built for.
# ---------------------------------------------------------------------------

_PROJECT_REGISTRY = """\
#!/usr/bin/env bash
# Key Registry
# [project]
#   widget                  OPTIONAL  ""
# [versioning]
#   max_patch               OPTIONAL  {max_patch}
# [locks]
#   stale_lock_reap_after_wake_periods  OPTIONAL  3
# [release]
#   ship_posture            OPTIONAL  matrix

_pp_read_cfg_key() {{
    read_ini "$1" "$2" "$3" "${{4:-}}"
}}
"""

_GENERATOR = '''\
"""Fixture generator: renders a project.cfg that carries no keys."""


def render_project_cfg(*, name, generated_by, **_values):
    """Return the fixture project.cfg text for *name*."""
    return f"# project.cfg for {name}, written by {generated_by}\\n[project]\\n"
'''


def _project_tree(
    tmp_path: pathlib.Path, *, template: str, reader: str, max_patch_default: str = "21",
    generator: "str | None" = None, extra: "dict[str, str] | None" = None,
) -> pathlib.Path:
    """The kanban.cfg fixture tree plus a project.cfg template, its registry and *reader*."""
    files = {
        "team/config/project.cfg_example": template,
        "team/scripts/lib/project_paths.sh": _PROJECT_REGISTRY.format(max_patch=max_patch_default),
        "team/scripts/project_reader.sh": "#!/usr/bin/env bash\n" + reader,
    }
    if generator is not None:
        files["team/pgai_agent_kanban/__init__.py"] = '"""Fixture package."""\n'
        files["team/pgai_agent_kanban/ops/__init__.py"] = '"""Fixture package."""\n'
        files["team/pgai_agent_kanban/ops/projects.py"] = generator
    files.update(extra or {})
    return _tree(tmp_path, extra=files)


def _cli_findings(root: pathlib.Path, tmp_path: pathlib.Path) -> "list[dict]":
    """Run the CLI with --findings-file; assert exit 0; return the findings."""
    findings_file = tmp_path / "cli-out" / "findings.json"
    result = _run_cli(root, "--findings-file", str(findings_file))
    assert result.returncode == 0, _diagnostic(result)
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert f"{len(findings)} finding(s)" in result.stdout, _diagnostic(result)
    return findings


def _entries(findings: "list[dict]") -> "list[tuple[str, str]]":
    return [(f["kind"], f["key"]) for f in findings]


_WIDGET_TEMPLATE = "[project]\n# widget: the widget name.\n# Default: empty\nwidget =\n"


def test_project_key_absent_from_the_create_project_output_is_reported(tmp_path: pathlib.Path) -> None:
    root = _project_tree(
        tmp_path, template=_WIDGET_TEMPLATE, generator=_GENERATOR,
        reader='name="$(_pp_read_cfg_key "$cfg_file" project widget "")"\n',
    )
    findings = _cli_findings(root, tmp_path)
    assert _entries(findings) == [("project_cfg_read_not_scaffolded", "widget")], findings
    assert findings[0]["file"] == "team/scripts/project_reader.sh" and findings[0]["line"] == 2
    assert "create_project output" in findings[0]["summary"], findings[0]
    # The remedy opens the generator at its render function: runnable, no placeholder.
    assert findings[0]["remedy"] == "${EDITOR:-vi} +4 team/pgai_agent_kanban/ops/projects.py"
    # The render wrote no bytecode into the scanned tree.
    assert not list((root / "team").rglob("__pycache__")), "the generator render wrote into the tree"

    # A generator that renders the template carries the key: clean.
    renders_template = _GENERATOR.replace(
        'return f"# project.cfg for {name}, written by {generated_by}\\n[project]\\n"',
        "import pathlib\n"
        "    return (pathlib.Path(__file__).resolve().parents[2] / 'config' / 'project.cfg_example').read_text()",
    )
    root_b = _project_tree(
        tmp_path / "b", template=_WIDGET_TEMPLATE, generator=renders_template,
        reader='name="$(_pp_read_cfg_key "$cfg_file" project widget "")"\n',
    )
    assert _cli_findings(root_b, tmp_path / "b") == []


def test_project_key_absent_from_the_template_is_reported(tmp_path: pathlib.Path) -> None:
    root = _project_tree(
        tmp_path, template="[project]\n",
        reader='name="$(_pp_read_cfg_key "$cfg_file" project widget "")"\n',
    )
    assert _entries(_cli_findings(root, tmp_path)) == [("project_cfg_read_not_scaffolded", "widget")]


def test_template_key_nothing_reads_from_project_cfg_is_reported(tmp_path: pathlib.Path) -> None:
    root = _project_tree(tmp_path, template=_WIDGET_TEMPLATE, reader="")
    assert _entries(_cli_findings(root, tmp_path)) == [("project_cfg_example_not_read", "widget")]


def test_stated_default_disagreeing_with_the_registry_is_reported(tmp_path: pathlib.Path) -> None:
    template = "[versioning]\n# max_patch: the patch ceiling.\n# Default: 21\nmax_patch = 21\n"
    reader = 'raw="$(_pp_read_cfg_key "$cfg_file" versioning max_patch "")"\n'
    root = _project_tree(tmp_path, template=template, reader=reader, max_patch_default='""')
    findings = _cli_findings(root, tmp_path)
    assert _entries(findings) == [("default_mismatch", "max_patch")], findings
    assert "'21'" in findings[0]["summary"] and "''" in findings[0]["summary"], findings[0]
    # The same template against a registry default of 21 is clean.
    assert _cli_findings(_project_tree(tmp_path / "b", template=template, reader=reader), tmp_path / "b") == []


def test_key_with_no_stated_default_is_reported(tmp_path: pathlib.Path) -> None:
    template = "[project]\n# widget: the widget name.\nwidget =\n"
    root = _project_tree(
        tmp_path, template=template, reader='name="$(_pp_read_cfg_key "$cfg_file" project widget "")"\n',
    )
    assert _entries(_cli_findings(root, tmp_path)) == [("missing_default", "widget")]


def test_commented_key_under_the_wrong_section_is_reported(tmp_path: pathlib.Path) -> None:
    # The [locks] banner was written but its header was not, so the key
    # sits under [release], where its reader never looks.
    template = (
        "[release]\n"
        "# --- [locks] — advisory-lock staleness ---\n\n"
        "# stale_lock_reap_after_wake_periods: wake periods before a lock is overdue.\n"
        "# Default: 3\n"
        "# stale_lock_reap_after_wake_periods = 3\n"
    )
    reader = 'raw="$(_pp_read_cfg_key "$cfg_file" locks stale_lock_reap_after_wake_periods "")"\n'
    findings = _cli_findings(_project_tree(tmp_path, template=template, reader=reader), tmp_path)
    assert _entries(findings) == [("wrong_section", "stale_lock_reap_after_wake_periods")], findings
    assert findings[0]["section"] == "release" and findings[0]["line"] == 6, findings[0]


def test_commented_value_that_is_not_the_default_is_reported(tmp_path: pathlib.Path) -> None:
    template = "[release]\n# ship_posture: matrix or always.\n# Default: matrix\n# ship_posture = always\n"
    reader = 'raw="$(_pp_read_cfg_key "$cfg_file" release ship_posture "")"\n'
    findings = _cli_findings(_project_tree(tmp_path, template=template, reader=reader), tmp_path)
    assert _entries(findings) == [("commented_value_not_default", "ship_posture")], findings
    shows_default = template.replace("# ship_posture = always", "# ship_posture = matrix")
    root_b = _project_tree(tmp_path / "b", template=shows_default, reader=reader)
    assert _cli_findings(root_b, tmp_path / "b") == []


def test_key_read_only_into_an_export_nothing_reads_is_reported(tmp_path: pathlib.Path) -> None:
    reader = 'export PP_widget="$(_pp_read_cfg_key "$cfg_file" project widget "")"\n'
    findings = _cli_findings(_project_tree(tmp_path, template=_WIDGET_TEMPLATE, reader=reader), tmp_path)
    assert _entries(findings) == [("read_but_unconsumed", "widget")], findings
    assert "PP_widget" in findings[0]["summary"], findings[0]
    consumed = _project_tree(
        tmp_path / "b", template=_WIDGET_TEMPLATE, reader=reader,
        extra={"team/scripts/widget_user.sh": '#!/usr/bin/env bash\necho "${PP_widget}"\n'},
    )
    assert _cli_findings(consumed, tmp_path / "b") == []


_API_EXAMPLE = _EXAMPLE + "\n[api]\n# Port. Default: 8300\nport = 8300\n"
_API_REGISTRY = _REGISTRY.replace("paths:old_spelling\n", "paths:old_spelling api:port\n")


def test_python_function_receiving_the_cfg_path_as_a_parameter_counts_as_a_read(tmp_path: pathlib.Path) -> None:
    module = (
        '"""Reads [api] port from whichever cfg file the caller passes."""\n'
        "from pgai_agent_kanban.lib import ini\n"
        "def read_port(cfg_path):\n"
        '    return ini.get_value(cfg_path, "api", "port")\n'
    )
    root = _tree(tmp_path, example=_API_EXAMPLE, registry=_API_REGISTRY,
                 extra={"team/pgai_agent_kanban/api/port.py": module})
    result = lint.run(root)
    assert {(f.kind, f.section, f.key) for f in result.findings} == set()
    assert ("api", "port") in result.read_keys
    # Without the reader the key is reported unread, so the parameter read is what counted.
    assert ("example_not_read", "api", "port") in _kinds(_tree(tmp_path / "b", example=_API_EXAMPLE,
                                                              registry=_API_REGISTRY))


def test_module_naming_kanban_cfg_but_reading_project_cfg_is_not_a_kanban_cfg_read(tmp_path: pathlib.Path) -> None:
    module = (
        '"""Mentions kanban.cfg, reads project.cfg."""\n'
        "from pgai_agent_kanban.lib import ini\n"
        'KANBAN_CFG_NAME = "kanban.cfg"\n'
        "def project_port(project_root):\n"
        '    return ini.get_value(project_root / "project.cfg", "api", "port")\n'
    )
    root = _tree(tmp_path, example=_API_EXAMPLE, registry=_API_REGISTRY,
                 extra={"team/pgai_agent_kanban/api/port.py": module})
    result = lint.run(root)
    assert ("api", "port") not in result.kanban_read_keys
    assert ("api", "port") in (result.project_read_keys or set())


def test_python_tiered_reader_reads_both_files(tmp_path: pathlib.Path) -> None:
    # The project.cfg-then-kanban.cfg loop every shared-section resolver uses.
    module = (
        '"""Tiered [api] port resolver."""\n'
        "from pgai_agent_kanban.lib import ini\n"
        "def port(root, project):\n"
        '    for source in (root / "projects" / project / "project.cfg", root / "kanban.cfg"):\n'
        '        raw = ini.read_line_value(source, "api", "port")\n'
        "        if raw:\n"
        "            return raw\n"
    )
    root = _tree(tmp_path, example=_API_EXAMPLE, registry=_API_REGISTRY,
                 extra={"team/pgai_agent_kanban/api/port.py": module})
    result = lint.run(root)
    assert ("api", "port") in result.kanban_read_keys
    assert ("api", "port") in (result.project_read_keys or set())


def test_real_tree_runs_every_project_cfg_check() -> None:
    # The template, the registry and the generator are all found, and the
    # generator renders; findings on the tree are reported, not asserted
    # away (they are documentation work for a later ticket).
    result = lint.run(_REPO_ROOT)
    assert result.notes == [], result.notes
    assert not [f for f in result.findings if f.kind == "project_cfg_render_failed"], result.findings
    assert len(result.project_read_keys or set()) > 25, sorted(result.project_read_keys or set())
