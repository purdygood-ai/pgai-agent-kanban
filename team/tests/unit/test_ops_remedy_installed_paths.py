"""
test_ops_remedy_installed_paths.py
==================================
Behavioral tests that operator-facing refusals -- built in Python
(``ops.read.get_queues`` / ``no_project_refusal``, ``ops.rc_state``,
``cm.fixed_in_restamp``, ``task_status``, ``overwatch.resolution_stage``)
and in bash (``restamp-fixed-in.sh``, ``lib/discovery_guards.sh``) -- print
a remedy naming the script an installed kanban carries under its root --
``<root>/scripts/<script>`` -- shell-quoted, so the printed command runs as
typed from any directory, and a sweep of every package module's string
literals for the repository script path, and of every remedy a package
module or an operator script emits for any repository path, and that the
DEFER-LOW-BUGS campaign file ``defer.sh`` writes names the installed scripts
that arm, clear and read it. An installed kanban has no ``team/`` directory,
so a remedy naming ``team/scripts/<script>`` or ``team/templates/<file>``
fails as printed.

Every run drives the real script against a throwaway kanban root whose path
contains a space (the case shell-quoting exists for), laid out as installed:
its ``scripts/``, ``pgai_agent_kanban/``, ``pm-agent/`` and ``templates/`` are
symlinks to this tree's copies, and each script is run through the root's
``scripts/``.
The environment is built from an explicit, minimal set of names
(tests/fixtures/scrubbed_subprocess_env.py) with the five inherited root
names set or cleared together, so the operator's live install is never
reached (project README, "Live-install safety"). Each refusal fires before
anything under the root is written; the campaign-header case writes only
the campaign file under the throwaway root.

Coverage:

  (1) show-queues.sh with no project and one registered project refuses
      with show_queues.no_project; the remedy's command starts with
      <root>/scripts/dashboard/show-queues.sh.
  (2) show-queues.sh with no project and an empty registry refuses with
      show_queues.no_project; the remedy's two commands start with
      <root>/scripts/create-project.sh and
      <root>/scripts/dashboard/show-queues.sh.
  (3) show-queues.sh --project for an unknown project on a root with no
      projects.cfg refuses with show_queues.project_directory_missing; the
      remedy's command starts with <root>/scripts/create-project.sh.
  (4) verify-rc-state.sh for an unknown project on a root with no
      projects.cfg refuses with verify_rc_state.project_directory_missing;
      the remedy's command starts with <root>/scripts/create-project.sh.
  (5) restamp-fixed-in.sh with a malformed version refuses with
      fixed_in_restamp.version_malformed; the remedy's command starts with
      <root>/scripts/restamp-fixed-in.sh.
  (6) No string literal in any module under pgai_agent_kanban/ (tests
      excluded) other than a docstring names the relative repository path
      ``team/scripts/``, outside the listed source-tree exemptions; a
      seeded literal is reported, docstrings, comments and an absolute
      ``<tree>/team/scripts/`` path are not.
  (7) No remedy any operator script under scripts/ passes to the bash
      error-contract emitter -- given inline or through a variable the
      script assigns -- names a relative ``team/`` path, outside the listed
      exemptions; a seeded remedy of either shape, ``team/scripts/`` or
      ``team/templates/``, is reported. Every exemption still matches a
      site, so none outlives its reason.
  (8) restamp-fixed-in.sh with no --project refuses with
      fixed_in_restamp.project_required; the remedy's command starts with
      <root>/scripts/restamp-fixed-in.sh.
  (9) discovery's active-RC guard refuses with discovery.active-rc-in-flight;
      the remedy's command starts with <root>/scripts/unwind-rc.sh.
 (10) task-status.sh read with no target refuses with
      task-status.missing-target; the remedy's command starts with
      <root>/scripts/task-status.sh.
 (11) The Lane A rewrite-missing refusal's remedy starts with
      <root>/scripts/overwatch-attach-fix-rewrite.sh.
 (12) No remedy a package module builds -- a ``remedy=`` keyword, the
      remedy argument of an error-contract call, a ``"remedy"`` dict value,
      a value assigned to a ``*remedy*`` name, or a ``*remedy*`` function's
      return -- names a relative ``team/`` path, outside the listed
      exemptions; a seeded ``team/templates/`` remedy of each shape is
      reported, a message, a docstring and an absolute path are not.
 (13) task-status.sh against a missing task folder refuses with
      task-status.missing-status-file, and against a status.md with no
      ``## Summary`` with task-status.missing-section; each remedy names
      the installed template <root>/templates/task/task-status/status.md,
      which exists.
 (14) defer.sh arms a campaign on the throwaway root; the campaign file's
      comment header names no ``team/`` path, and names scripts/defer.sh,
      scripts/undefer.sh and scripts/lib/defer_posture.sh, each of which
      exists under the root.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import shlex
import subprocess
import sys
import types

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402
from tests.unit.shell_harness import run_bash  # noqa: E402

#: Directories an installed kanban carries at its root, linked from this tree.
_INSTALLED_DIRS = ("scripts", "pgai_agent_kanban", "pm-agent", "templates")

#: The project the empty-registry remedy suggests registering.
_EXAMPLE_PROJECT = "pgai-agent-kanban"


@pytest.fixture()
def spaced_root(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """A throwaway kanban root with a space in its path, laid out as
    installed, plus a scratch HOME and temp directory outside it. No
    projects.cfg is written; each test writes the registry it needs."""
    root = tmp_path / "kanban root"
    home = tmp_path / "home"
    temp_dir = tmp_path / "scratch"
    for directory in (root, home, temp_dir):
        directory.mkdir()
    (root / "projects").mkdir()
    for name in _INSTALLED_DIRS:
        (root / name).symlink_to(_TEAM_DIR / name, target_is_directory=True)
    return {"root": root, "home": home, "temp_dir": temp_dir}


def _run(script: str, args: list[str], spaced_root: dict[str, pathlib.Path]) -> subprocess.CompletedProcess:
    """Run *script* through *spaced_root*'s ``scripts/`` against that root,
    from a directory outside it. Neither PGAI_PROJECT_NAME, PGAI_TASKS_DIR
    nor PGAI_PROJECT_ROOT is set."""
    extra_env = {
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(spaced_root["root"]),
        "KANBAN_ROOT": str(spaced_root["root"]),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(spaced_root["temp_dir"]),
    }
    return subprocess.run(
        ["bash", str(spaced_root["root"] / "scripts" / script), *args],
        capture_output=True,
        text=True,
        env=build_scrubbed_env(home_dir=spaced_root["home"], extra_env=extra_env),
        cwd=str(spaced_root["temp_dir"]),
        stdin=subprocess.DEVNULL,
        timeout=60,
    )


def _remedy_after(stderr: str, lead: str) -> str:
    """The remedy line's text after *lead*, its trailing full stop dropped."""
    remedy = [line for line in stderr.splitlines() if line.startswith("remedy:")]
    assert remedy, f"no remedy line: {stderr!r}"
    assert lead in remedy[0], f"remedy does not carry {lead!r}: {remedy[0]!r}"
    return remedy[0].split(lead, 1)[1].rstrip(".")


def _installed(spaced_root: dict[str, pathlib.Path], script: str) -> str:
    """*script*'s path under the sandbox root, as the ops layer resolves it."""
    return str(spaced_root["root"].resolve() / "scripts" / script)


def _assert_refused(result: subprocess.CompletedProcess, error_key: str) -> None:
    assert result.returncode != 0, result.stderr
    assert f"error:   {error_key}" in result.stderr, result.stderr
    assert "team/scripts/" not in result.stderr, result.stderr


# ---------------------------------------------------------------------------
# (1) show-queues.sh, no project, one registered project
# ---------------------------------------------------------------------------


def test_show_queues_no_project_names_installed_show_queues_for_the_registered_project(spaced_root) -> None:
    (spaced_root["root"] / "projects.cfg").write_text("[project:demo]\npriority=1\n", encoding="utf-8")

    result = _run("dashboard/show-queues.sh", [], spaced_root)

    _assert_refused(result, "show_queues.no_project")
    command = _remedy_after(result.stderr, "(demo): ").split(" (REST:", 1)[0]
    assert shlex.split(command) == [_installed(spaced_root, "dashboard/show-queues.sh"), "--project", "demo"]


# ---------------------------------------------------------------------------
# (2) show-queues.sh, no project, empty registry
# ---------------------------------------------------------------------------


def test_show_queues_no_project_on_empty_registry_names_installed_create_and_show_scripts(spaced_root) -> None:
    (spaced_root["root"] / "projects.cfg").write_text("", encoding="utf-8")

    result = _run("dashboard/show-queues.sh", [], spaced_root)

    _assert_refused(result, "show_queues.no_project")
    words = shlex.split(_remedy_after(result.stderr, "then name it: "))
    assert words == [
        _installed(spaced_root, "create-project.sh"), "--project", _EXAMPLE_PROJECT,
        "&&",
        _installed(spaced_root, "dashboard/show-queues.sh"), "--project", _EXAMPLE_PROJECT,
    ], words


# ---------------------------------------------------------------------------
# (3)-(4) an unknown project on a root with no projects.cfg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script, args, error_key",
    [
        ("dashboard/show-queues.sh", ["--project", "ghost"], "show_queues.project_directory_missing"),
        ("verify-rc-state.sh", ["ghost"], "verify_rc_state.project_directory_missing"),
    ],
    ids=["show-queues", "verify-rc-state"],
)
def test_unknown_project_directory_names_installed_create_project(spaced_root, script, args, error_key) -> None:
    result = _run(script, args, spaced_root)

    _assert_refused(result, error_key)
    words = shlex.split(_remedy_after(result.stderr, "create it first with "))
    assert words == [_installed(spaced_root, "create-project.sh"), "--project", "ghost"], words


# ---------------------------------------------------------------------------
# (5) restamp-fixed-in.sh, malformed version
# ---------------------------------------------------------------------------


def test_restamp_fixed_in_malformed_version_names_installed_restamp_script(spaced_root) -> None:
    (spaced_root["root"] / "projects.cfg").write_text("[project:demo]\npriority=1\n", encoding="utf-8")
    (spaced_root["root"] / "projects" / "demo" / "bugs").mkdir(parents=True)

    result = _run("restamp-fixed-in.sh", ["--project", "demo", "--version", "latest", "BUG-0101"], spaced_root)

    _assert_refused(result, "fixed_in_restamp.version_malformed")
    words = shlex.split(_remedy_after(result.stderr, "remedy:"))
    assert words == [
        _installed(spaced_root, "restamp-fixed-in.sh"), "--project", "demo",
        "--version", "v1.4.0", "BUG-0101", "BUG-0102",
    ], words




# ---------------------------------------------------------------------------
# (6)-(7) Sweep: every package module and every operator script
# ---------------------------------------------------------------------------

#: The relative repository path form an installed kanban does not have. An
#: absolute ``<tree>/team/scripts/`` (a dev tree's own path, run from that
#: dev tree) is a real path and is not matched.
_REPO_SCRIPTS_RE = re.compile(r"(?<![\w}/.$-])team/scripts/")

#: Any relative repository path -- ``team/scripts/``, ``team/templates/``,
#: ``team/config/`` -- the form no remedy may name, by the same boundary.
_REPO_PATH_RE = re.compile(r"(?<![\w}/.$-])team/")

_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

#: Text that names the repository path on purpose: it addresses a repository
#: checkout, not an installed kanban's operator. (path under team/, fragment
#: of the literal or remedy, why). A fragment of None exempts the whole file.
_SOURCE_TREE_TEXT = (
    ("pgai_agent_kanban/testing/pre_merge.py", None,
     "the pre-merge runner runs only in a repository checkout and names the files to commit there"),
    ("pgai_agent_kanban/testing/affected.py", None,
     "the affected-test selector reads a repository diff; its help names source paths"),
    ("pgai_agent_kanban/api/generate_icd.py", "team/scripts/generate-icd.sh",
     "the ICD generator regenerates docs/api/ in a repository checkout, run from the repository root"),
    ("pgai_agent_kanban/api/reads.py", "team/scripts/dashboard/agent-metrics.sh",
     "OpenAPI description naming the source script a route mirrors"),
    ("pgai_agent_kanban/api/reads.py", "team/scripts/why.sh",
     "OpenAPI description naming the source script a route mirrors"),
    ("pgai_agent_kanban/cm/release_notes_generator.py", "team/scripts/lib/cm in a repository checkout",
     "an example labelled as the repository checkout's value"),
    ("pgai_agent_kanban/cm/ship_policy_bug_filer.py", "team/scripts/lib/cm/",
     "a filed bug's Files Involved: the source files its fixer edits"),
    ("pgai_agent_kanban/deep_verify/bug_filer.py", "team/scripts/deep-verify.sh",
     "a filed bug's reproduction, read by its fixer in a repository checkout"),
    ("pgai_agent_kanban/lint/bug_filer.py", "team/scripts/lint_hygiene.py",
     "a filed bug's reproduction: the lint scans a repository checkout"),
    ("scripts/upgrade.sh", "checkout HEAD -- team/scripts/lib/version_stamp.sh",
     "a git pathspec inside the dev tree upgrade.sh installs from"),
    ("scripts/lib/wake_driver_invoke.sh", "(team/scripts/lib/wake_driver_invoke.sh)",
     "names the source file whose text a developer rewords"),
    ("pgai_agent_kanban/lib/palette.py", "team/config/default_palette.txt",
     "a missing palette key is fixed in the repository's palette file; install.sh replaces the installed copy"),
    ("scripts/lib/dashboard_palette.sh", "team/config/default_palette.txt (dev tree) or config/default_palette.txt",
     "names the repository and the installed palette file side by side"),
    ("scripts/lib/dashboard_palette.sh", "%${key} entry to team/config/default_palette.txt",
     "a missing palette key is fixed in the repository's palette file; install.sh replaces the installed copy"),
    ("scripts/run-unit-tests.sh", "relocate each named file to team/tests/cold/",
     "relocating a test module is an edit to the repository checkout the gated runner tests"),
    ("scripts/run-integration-tests.sh", "relocate each named file to team/tests/cold/",
     "relocating a test module is an edit to the repository checkout the gated runner tests"),
    ("scripts/run-unit-tests.sh", "team/config/operator_verb_census_exempt.txt",
     "the API-parity census exemption list is edited in the repository checkout the gated runner tests"),
)


def _exempt(rel_path: str, text: str) -> bool:
    return any(
        path == rel_path and (fragment is None or fragment in text)
        for path, fragment, _why in _SOURCE_TREE_TEXT
    )


def _repo_path_literals(source: str) -> list[tuple[int, str]]:
    """(line, text) of every string literal in *source* naming ``team/scripts/``.

    Every string constant is read, f-string parts included; module, class
    and function docstrings are skipped, since they may describe the source
    tree. Comments never reach the syntax tree.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    return sorted(
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and _REPO_SCRIPTS_RE.search(node.value)
    )


def _package_modules() -> list[pathlib.Path]:
    return sorted(
        path for path in _PACKAGE_DIR.rglob("*.py")
        if "tests" not in path.relative_to(_PACKAGE_DIR).parts
    )


#: The Python error-contract emitters; their third positional argument is the remedy.
_PY_EMITTERS = frozenset({"emit_error_contract", "format_error_contract"})

#: A machine error key (``task-status.missing-section``,
#: ``process_reap.cwd_scope_invalid``): a call that passes one positionally
#: carries the contract's (error, message, remedy) triple from there on.
_ERROR_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:[.:][a-z0-9_-]+)+$")


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _remedy_expressions(tree: ast.AST):
    """Every expression in *tree* that builds a refusal's remedy.

    A ``remedy=`` keyword of any call; the third positional argument of an
    error-contract emitter, or of any call whose positional arguments run
    (error key, message, remedy); the value of a ``"remedy"`` dict key; a
    value assigned to a name or attribute containing ``remedy``; and what a
    function whose name contains ``remedy`` returns.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield from (kw.value for kw in node.keywords if kw.arg == "remedy")
            if _call_name(node.func) in _PY_EMITTERS:
                if len(node.args) > 2:
                    yield node.args[2]
                continue
            for index, arg in enumerate(node.args):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and _ERROR_KEY_RE.match(arg.value):
                    if len(node.args) > index + 2:
                        yield node.args[index + 2]
                    break
        elif isinstance(node, ast.Dict):
            yield from (
                value for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and key.value == "remedy"
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and any("remedy" in _call_name(t).lower() for t in targets):
                yield node.value
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "remedy" in node.name.lower():
            yield from (sub.value for sub in ast.walk(node) if isinstance(sub, ast.Return) and sub.value is not None)


def _python_repo_path_remedies(source: str) -> list[tuple[int, str]]:
    """(line, text) of every string constant a remedy in *source* is built from that names a ``team/`` path.

    f-string parts are read one by one; an interpolated root in front of
    ``team/`` (an absolute dev-tree path) is not matched.
    """
    return sorted({
        (node.lineno, node.value)
        for expression in _remedy_expressions(ast.parse(source))
        for node in ast.walk(expression)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _REPO_PATH_RE.search(node.value)
    })


def _load_emitter_scanner() -> types.ModuleType:
    """lint_dual_channel_pair.py, whose call-site discovery the census lints share."""
    spec = importlib.util.spec_from_file_location("lint_dual_channel_pair", _SCRIPTS_DIR / "lint_dual_channel_pair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_EMITTERS = _load_emitter_scanner()
_REMEDY_INDEX = _EMITTERS.FIELD_NAMES.index("remedy")
_BARE_VARIABLE_RE = re.compile(r"^\$\{?([A-Za-z_]\w*)\}?$")


def _emitted_remedies(text: str) -> list[tuple[int, str]]:
    """(line, text) of every remedy *text*'s bash error-contract emitter calls carry.

    A remedy given as one bare variable is followed to each value the script
    assigns that variable, comments stripped.
    """
    remedies: list[tuple[int, str]] = []
    for match in _EMITTERS._BASH_FUNC_RE.finditer(text):
        start = text.rfind("\n", 0, match.start()) + 1
        end = text.find("\n", match.start())
        line = _EMITTERS._strip_bash_comment(text[start:end if end != -1 else len(text)])
        column = match.start() - start
        if column >= len(line) or _EMITTERS._is_availability_probe_operand(line[:column]):
            continue
        tokens = _EMITTERS._tokenize_bash_args(_EMITTERS._extract_bash_statement(text, match.end()))
        if not tokens or len(tokens) <= _REMEDY_INDEX:
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        remedy = tokens[_REMEDY_INDEX]
        remedies.append((line_no, remedy))
        variable = _BARE_VARIABLE_RE.match(remedy)
        if variable:
            assignment = re.compile(rf"^\s*(?:local\s+)?{re.escape(variable.group(1))}=(.*)$", re.MULTILINE)
            remedies.extend((line_no, _EMITTERS._strip_bash_comment(value).strip()) for value in assignment.findall(text))
    return remedies


def _repo_path_remedies(text: str) -> list[tuple[int, str]]:
    return [(line, remedy) for line, remedy in _emitted_remedies(text) if _REPO_PATH_RE.search(remedy)]


def _operator_scripts() -> list[pathlib.Path]:
    return sorted(_SCRIPTS_DIR.rglob("*.sh"))


_FIX_HINT = (
    "an installed kanban has no team/ directory -- build the command with "
    "halt_provenance.installed_script_command(kanban_root, script, *args) in Python, or "
    "$(printf '%q' \"${KANBAN_ROOT}/scripts/<script>\") in bash, and name any other file "
    "under the resolved kanban root (<root>/templates/..., not team/templates/...); text that "
    "addresses a repository checkout on purpose goes in _SOURCE_TREE_TEXT with its reason"
)


def test_no_package_module_string_names_the_repository_script_path() -> None:
    modules = _package_modules()
    assert len(modules) > 100, f"the sweep found only {len(modules)} package modules under {_PACKAGE_DIR}"
    found = [
        f"{rel}:{line}"
        for path in modules
        for rel in [str(path.relative_to(_TEAM_DIR))]
        for line, text in _repo_path_literals(path.read_text(encoding="utf-8"))
        if not _exempt(rel, text)
    ]
    assert not found, f"string literals naming team/scripts/ at {found}; {_FIX_HINT}"


def test_no_operator_script_remedy_names_the_repository_script_path() -> None:
    scripts = _operator_scripts()
    assert len(scripts) > 100, f"the sweep found only {len(scripts)} scripts under {_SCRIPTS_DIR}"
    assert sum(1 for path in scripts if _emitted_remedies(path.read_text(encoding="utf-8"))) > 50
    found = [
        f"{rel}:{line}"
        for path in scripts
        for rel in [str(path.relative_to(_TEAM_DIR))]
        for line, remedy in _repo_path_remedies(path.read_text(encoding="utf-8", errors="replace"))
        if not _exempt(rel, remedy)
    ]
    assert not found, f"emitted remedies naming a team/ path at {found}; {_FIX_HINT}"


def test_no_package_module_remedy_names_a_repository_path() -> None:
    modules = _package_modules()
    remedies = sum(1 for path in modules for _expr in _remedy_expressions(ast.parse(path.read_text(encoding="utf-8"))))
    assert remedies > 100, f"the sweep found only {remedies} remedy expressions under {_PACKAGE_DIR}"
    found = [
        f"{rel}:{line}"
        for path in modules
        for rel in [str(path.relative_to(_TEAM_DIR))]
        for line, text in _python_repo_path_remedies(path.read_text(encoding="utf-8"))
        if not _exempt(rel, text)
    ]
    assert not found, f"package remedies naming a team/ path at {found}; {_FIX_HINT}"


def test_every_source_tree_exemption_still_matches_a_site() -> None:
    for rel, fragment, why in _SOURCE_TREE_TEXT:
        path = _TEAM_DIR / rel
        assert path.is_file(), f"exempted file {rel} ({why}) no longer exists; drop its entry"
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            hits = [*_repo_path_literals(text), *_python_repo_path_remedies(text)]
        else:
            hits = _repo_path_remedies(text)
        assert any(fragment is None or fragment in hit for _line, hit in hits), (
            f"exemption {rel} {fragment!r} ({why}) matches nothing; drop its entry"
        )


def test_repository_path_sweep_reports_a_seeded_literal_and_skips_docstrings_and_comments() -> None:
    seeded = (
        '"""Behind team/scripts/x.sh."""\n'
        "# Reached through team/scripts/x.sh.\n"
        "def f(root):\n"
        '    """Runs team/scripts/x.sh."""\n'
        '    return f"team/scripts/x.sh --root {root}"\n'
        '_SCRIPT = "team/scripts/y.sh"\n'
        '_DEV = f"{root}/team/scripts/z.sh"\n'
    )
    assert [line for line, _text in _repo_path_literals(seeded)] == [5, 6]


def test_remedy_sweep_reports_a_seeded_bash_remedy_inline_or_through_a_variable() -> None:
    seeded = (
        "#!/usr/bin/env bash\n"
        "# pgai_emit_error_contract k m 'team/scripts/comment.sh'\n"
        'pgai_emit_error_contract "x.inline" "message" "run: team/scripts/x.sh --project demo"\n'
        '_example="team/scripts/y.sh --project demo"  # example invocation\n'
        'pgai_emit_error_contract "x.variable" "message" "${_example}"\n'
        'pgai_emit_error_contract "x.installed" "message team/scripts/" "$(printf \'%q\' "${KANBAN_ROOT}/scripts/x.sh")"\n'
        'pgai_emit_error_contract "x.dev-tree" "message" "\'${DEV_TREE}/team/scripts/upgrade.sh\' --dev-tree x"\n'
        'pgai_emit_error_contract "x.template" "no team/templates/ here" "copy team/templates/task/status.md first"\n'
        'pgai_emit_error_contract "x.installed-template" "message" "copy ${KANBAN_ROOT}/templates/task/status.md first"\n'
    )
    assert _repo_path_remedies(seeded) == [
        (3, "run: team/scripts/x.sh --project demo"),
        (5, '"team/scripts/y.sh --project demo"'),
        (8, "copy team/templates/task/status.md first"),
    ]


def test_python_remedy_sweep_reports_each_seeded_remedy_shape_and_skips_messages() -> None:
    seeded = (
        '"""Copy team/templates/a.md."""\n'
        "def f(root, key):\n"
        '    """Copies team/templates/b.md."""\n'
        '    TaskStatusError(error_key="x.k", message="no team/templates/c.md", remedy=f"copy team/templates/d.md to {key}")\n'
        '    emit_error_contract("x.k", "no team/templates/e.md", "copy team/templates/f.md")\n'
        '    _refuse("x.scope_bad", "no team/templates/g.md", "copy team/templates/h.md")\n'
        '    payload = {"message": "team/templates/i.md", "remedy": "copy team/templates/j.md"}\n'
        '    _REMEDY = "copy team/templates/k.md"\n'
        '    installed = TaskStatusError(error_key="x.k", message="m", remedy=f"copy {root}/team/templates/l.md")\n'
        '    return TaskStatusError(error_key="x.k", message="m", remedy=f"copy {root}/templates/m.md")\n'
        "def _status_remedy():\n"
        '    return "copy team/templates/n.md"\n'
    )
    assert [re.search(r"team/templates/(\w+\.md)", text).group(1) for _line, text in _python_repo_path_remedies(seeded)] == [
        "d.md", "f.md", "h.md", "j.md", "k.md", "n.md",
    ]


# ---------------------------------------------------------------------------
# (8) restamp-fixed-in.sh, no project
# ---------------------------------------------------------------------------


def test_restamp_fixed_in_without_a_project_names_installed_restamp_script(spaced_root) -> None:
    (spaced_root["root"] / "projects.cfg").write_text("[project:demo]\npriority=1\n", encoding="utf-8")

    result = _run("restamp-fixed-in.sh", ["--version", "v1.4.0", "BUG-0101"], spaced_root)

    _assert_refused(result, "fixed_in_restamp.project_required")
    words = shlex.split(_remedy_after(result.stderr, "remedy:"))
    assert words == [
        str(spaced_root["root"] / "scripts" / "restamp-fixed-in.sh"),
        "--project", _EXAMPLE_PROJECT, "--version", "v1.4.0", "BUG-0101",
    ], words


# ---------------------------------------------------------------------------
# (9) discovery's active-RC guard
# ---------------------------------------------------------------------------


def test_discovery_active_rc_guard_names_installed_unwind_rc(spaced_root, tmp_path) -> None:
    root = spaced_root["root"]
    script = (
        "set -uo pipefail\n"
        f"TEAM_ROOT={shlex.quote(str(root))}\n"
        "source scripts/lib/discovery.sh\n"
        "log() { printf '%s\\n' \"$*\" >&2; }\n"
        "_disc_get_release_field() { printf '%s\\n' 'v9.4.0'; }\n"
        "_disc_active_rc_blocks bugs demo\n"
        'echo "RC=$?"\n'
    )
    env = build_scrubbed_env(home_dir=spaced_root["home"], extra_env={
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(spaced_root["temp_dir"]),
    })

    result = run_bash(tmp_path, script, base_env=env)

    assert "RC=1" in result.stdout, result.stderr
    _assert_refused(types.SimpleNamespace(returncode=1, stderr=result.stderr), "discovery.active-rc-in-flight")
    command = _remedy_after(result.stderr, "must be cleared, run: ")
    assert shlex.split(command) == [
        str(root / "scripts" / "unwind-rc.sh"), "--project", "demo", "--key", "v9.4.0",
    ], command


# ---------------------------------------------------------------------------
# (10) task-status.sh, no target
# ---------------------------------------------------------------------------


def test_task_status_missing_target_names_installed_task_status(spaced_root) -> None:
    result = _run("task-status.sh", ["read"], spaced_root)

    _assert_refused(result, "task-status.missing-target")
    words = shlex.split(_remedy_after(result.stderr, "remedy:"))
    assert words == [
        _installed(spaced_root, "task-status.sh"), "read",
        "--project", _EXAMPLE_PROJECT, "--key", "CODER-20260101-001-example-task",
    ], words


# ---------------------------------------------------------------------------
# (11) The Lane A rewrite-missing refusal
# ---------------------------------------------------------------------------


def test_lane_a_rewrite_missing_names_installed_attach_script(tmp_path) -> None:
    from pgai_agent_kanban.overwatch import resolution_stage

    root = tmp_path / "kanban root"
    text = resolution_stage._format_lane_a_rewrite_missing(
        types.SimpleNamespace(regression_bug_id="BUG-9101"), None, str(root / "projects" / "demo" / "bugs"),
        "v9.4.0", str(root), "demo",
    )

    assert "team/scripts/" not in text
    command = _remedy_after(text, "attach the rewrite first: ").split(", adding", 1)[0]
    assert shlex.split(command) == [
        str(root / "scripts" / "overwatch-attach-fix-rewrite.sh"),
        "--project", "demo", "--kanban-root", str(root), "--bug-id", "BUG-9101",
    ], command


# ---------------------------------------------------------------------------
# (13) task-status.sh, the status template remedies
# ---------------------------------------------------------------------------


def _installed_status_template(spaced_root: dict[str, pathlib.Path]) -> str:
    return str(spaced_root["root"].resolve() / "templates" / "task" / "task-status" / "status.md")


def test_task_status_missing_status_file_names_the_installed_template(spaced_root) -> None:
    result = _run("task-status.sh", ["--project", "nope", "--key", "X-1", "--state", "DONE"], spaced_root)

    _assert_refused(result, "task-status.missing-status-file")
    assert "team/templates/" not in result.stderr, result.stderr
    template = _installed_status_template(spaced_root)
    assert _remedy_after(result.stderr, " from ").endswith(f"{template} first"), result.stderr
    assert pathlib.Path(template).is_file(), template


def test_task_status_missing_section_names_the_installed_template(spaced_root) -> None:
    task = spaced_root["root"] / "projects" / "demo" / "tasks" / "X-1"
    task.mkdir(parents=True)
    (task / "status.md").write_text("# Status\n\n## State\nWORKING\n", encoding="utf-8")

    result = _run(
        "task-status.sh", ["--project", "demo", "--key", "X-1", "--state", "DONE", "--summary", "done"], spaced_root,
    )

    _assert_refused(result, "task-status.missing-section")
    assert "team/templates/" not in result.stderr, result.stderr
    template = _installed_status_template(spaced_root)
    assert f"standard template ({template})" in result.stderr, result.stderr
    assert pathlib.Path(template).is_file(), template


# ---------------------------------------------------------------------------
# (14) defer.sh, the campaign file's comment header
# ---------------------------------------------------------------------------

#: A script path a comment names, relative to the kanban root.
_NAMED_SCRIPT_RE = re.compile(r"(?<![\w/.-])((?:team/)?scripts/[\w/.-]+\.sh)")


def test_campaign_file_header_names_the_installed_defer_scripts(spaced_root) -> None:
    root = spaced_root["root"]
    (root / "projects.cfg").write_text("[project:demo]\npriority=1\n", encoding="utf-8")
    (root / "projects" / "demo").mkdir()

    result = _run("defer.sh", ["--project", "demo", "--until", "v9.4.0"], spaced_root)

    assert result.returncode == 0, result.stderr
    lines = (root / "projects" / "demo" / "DEFER-LOW-BUGS").read_text(encoding="utf-8").splitlines()
    header = "\n".join(line for line in lines if line.startswith("#"))
    assert header, lines
    assert not _REPO_PATH_RE.search(header), header
    named = _NAMED_SCRIPT_RE.findall(header)
    assert named == ["scripts/defer.sh", "scripts/undefer.sh", "scripts/lib/defer_posture.sh"], header
    missing = [path for path in named if not (root / path).is_file()]
    assert not missing, f"the header names {missing}, which an installed root does not carry"
    assert lines[-1] == "until=v9.4.0", lines
