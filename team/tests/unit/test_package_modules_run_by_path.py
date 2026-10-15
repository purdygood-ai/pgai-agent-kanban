"""
test_package_modules_run_by_path.py
===================================
Package modules that carry their own command-line interface, or that carry
the self-location guard, keep working when started by file path, with no
PYTHONPATH set.

Started as ``python3 team/pgai_agent_kanban/<sub>/<module>.py``, a module has
no parent package and the interpreter puts only its own directory on
sys.path, so a ``from pgai_agent_kanban... import`` at module top fails unless
the module first puts its own package root on sys.path. These tests pin
three properties of that self-location:

  1. By-path ``--help`` runs the module's own command-line handling with no
     traceback, from a working directory unrelated to the tree, and exits 0
     -- a CLI that takes positional arguments only answers ``--help`` with
     its full usage, not with its wrong-argument-list exit code
     (``lint_doc_command_truth.py`` runs this probe for every ``.py`` path a
     scanned document cites).
  2. Loaded from its file outside the package, the module binds the
     ``pgai_agent_kanban`` package of the tree it lives in -- never another
     tree's copy.
  3. Imported as a normal package member, the module leaves sys.path
     exactly as it found it.

The module set is discovered, not listed: every module under
``pgai_agent_kanban/`` (outside any ``tests/`` directory) that has an
``if __name__ == "__main__":`` block and a module-level package import, plus
every module that carries the ``if not __package__:`` self-location guard,
minus the commented package-only allow-list below. A module that gains a
command-line interface is therefore covered the moment it exists, and has
to be either guarded or consciously allow-listed.

Every child process runs with an environment built from an explicit set
(no PYTHONPATH) and a sandboxed kanban root and temp dir under tmp_path.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PACKAGE_NAME = "pgai_agent_kanban"
_PACKAGE_DIR = _TEAM_DIR / _PACKAGE_NAME

# A guarded module to copy the self-location guard from, named in failure
# messages so the remedy is concrete.
_GUARD_EXAMPLE = _PACKAGE_DIR / "reports" / "agent_timing.py"

# Package-only modules: started with ``python3 -m``, never by file path, so
# the by-path properties do not apply to them. Each entry's comment states
# why, as behaviour. A module listed here must not advertise the by-path form
# anywhere in its docstring or usage text
# (test_package_only_modules_do_not_advertise_by_path_use checks this).
_PACKAGE_ONLY_MODULES = frozenset({
    # Its command-line verbs are served by the package entry point,
    # ``python3 -m pgai_agent_kanban.ops``. The package imports this module
    # eagerly to build its public facade, so started as a script it would
    # run as a second copy beside the one the package loaded.
    "pgai_agent_kanban.ops.write",
})

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _is_package_import(node: ast.AST, package: str) -> bool:
    """True for ``import <package>[.x]`` or ``from <package>[.x] import ...``."""
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return node.level == 0 and (module == package or module.startswith(package + "."))
    if isinstance(node, ast.Import):
        return any(a.name == package or a.name.startswith(package + ".") for a in node.names)
    return False


def _is_main_block(node: ast.AST) -> bool:
    """True for ``if __name__ == "__main__":`` (either operand order)."""
    if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
        return False
    test = node.test
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left, test.comparators[0]]
    has_name = any(isinstance(o, ast.Name) and o.id == "__name__" for o in operands)
    has_main = any(isinstance(o, ast.Constant) and o.value == "__main__" for o in operands)
    return has_name and has_main


def _is_self_location_guard(node: ast.AST) -> bool:
    """True for a module-level ``if not __package__:`` block."""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "__package__"
    )


def _module_level_statements(body: list[ast.stmt]):
    """Yield every statement that runs when the module loads: the module
    body and the bodies of its module-level if/try/with blocks, but never a
    function or class body, which runs only when called."""
    for node in body:
        yield node
        if isinstance(node, (ast.If, ast.Try, ast.With)):
            for field in ("body", "orelse", "finalbody"):
                yield from _module_level_statements(getattr(node, field, None) or [])
            for handler in getattr(node, "handlers", None) or []:
                yield from _module_level_statements(handler.body)


def _discover_by_path_modules(package_dir: pathlib.Path) -> dict[str, pathlib.Path]:
    """Return ``{dotted module name: path}`` for every module under
    *package_dir*, outside any ``tests/`` directory, that has a ``__main__``
    block and a module-level import of its own package, or that carries the
    self-location guard."""
    # Intentional pattern-scan: every current and future package module with
    # a command-line interface must run by path or be allow-listed, so the
    # scan covers the whole package by construction.
    package = package_dir.name
    found: dict[str, pathlib.Path] = {}
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if "tests" in relative.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        statements = list(_module_level_statements(tree.body))
        has_cli = any(_is_main_block(n) for n in tree.body) and any(
            _is_package_import(n, package) for n in statements
        )
        has_guard = any(_is_self_location_guard(n) for n in tree.body)
        if has_cli or has_guard:
            dotted = ".".join((package, *relative.with_suffix("").parts))
            found[dotted] = path
    return found


_DISCOVERED = _discover_by_path_modules(_PACKAGE_DIR)
_BY_PATH_MODULES = {
    name: path for name, path in _DISCOVERED.items() if name not in _PACKAGE_ONLY_MODULES
}
_MODULE_IDS = sorted(_BY_PATH_MODULES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sandboxed_env(tmp_path: pathlib.Path, *, pythonpath: str | None = None) -> dict:
    """Return a child environment with no inherited PYTHONPATH and a
    throwaway kanban root and temp dir under *tmp_path*."""
    kanban_root = tmp_path / "kanban"
    temp_dir = tmp_path / "tmp"
    kanban_root.mkdir(exist_ok=True)
    temp_dir.mkdir(exist_ok=True)
    extra = {
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    if pythonpath is not None:
        extra["PYTHONPATH"] = pythonpath
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env=extra)


def _run(argv: list[str], tmp_path: pathlib.Path, env: dict) -> subprocess.CompletedProcess:
    workdir = tmp_path / "elsewhere"
    workdir.mkdir(exist_ok=True)
    return subprocess.run(
        argv, cwd=str(workdir), env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


# ---------------------------------------------------------------------------
# The three by-path properties, for every discovered module
# ---------------------------------------------------------------------------


def test_discovery_finds_the_package_command_line_modules() -> None:
    assert _MODULE_IDS, (
        f"discovery found no module under {_PACKAGE_DIR} with a __main__ block "
        f"and a package import; the by-path tests below would cover nothing"
    )


@pytest.mark.parametrize("module_name", _MODULE_IDS)
def test_help_by_file_path_runs_without_pythonpath(
    module_name: str, tmp_path: pathlib.Path
) -> None:
    module_path = _BY_PATH_MODULES[module_name]
    env = _sandboxed_env(tmp_path)
    assert "PYTHONPATH" not in env

    result = _run([sys.executable, str(module_path), "--help"], tmp_path, env)

    assert "Traceback" not in result.stderr and result.returncode == 0, (
        f"'python3 {module_path} --help' exited {result.returncode} "
        f"(expected 0) with PYTHONPATH unset. If the output shows "
        f"No module named '{_PACKAGE_NAME}', give the module the "
        f"'if not __package__:' self-location guard (copy it from "
        f"{_GUARD_EXAMPLE}), or, if it is deliberately started only with "
        f"'python3 -m {module_name}', add it to _PACKAGE_ONLY_MODULES in "
        f"{__file__} with a comment saying why.\n{_diagnostic(result)}"
    )


@pytest.mark.parametrize("module_name", _MODULE_IDS)
def test_file_load_binds_the_package_of_its_own_tree(
    module_name: str, tmp_path: pathlib.Path
) -> None:
    module_path = _BY_PATH_MODULES[module_name]
    probe = textwrap.dedent(
        f"""\
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "loaded_by_file", {str(module_path)!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        print(sys.modules["pgai_agent_kanban"].__file__)
        """
    )

    result = _run([sys.executable, "-c", probe], tmp_path, _sandboxed_env(tmp_path))

    assert result.returncode == 0, _diagnostic(result)
    bound = pathlib.Path(result.stdout.strip()).resolve()
    assert bound == (_PACKAGE_DIR / "__init__.py").resolve(), (
        f"loading {module_path} by file bound pgai_agent_kanban from {bound}, "
        f"not from the tree the module lives in ({_PACKAGE_DIR})"
    )


@pytest.mark.parametrize("module_name", _MODULE_IDS)
def test_package_member_import_leaves_sys_path_unchanged(
    module_name: str, tmp_path: pathlib.Path
) -> None:
    # Import the package's parent modules first so the measured window
    # covers only the target module's own top-level code.
    parent = module_name.rsplit(".", 1)[0]
    probe = textwrap.dedent(
        f"""\
        import importlib, json, sys
        importlib.import_module({parent!r})
        before = list(sys.path)
        importlib.import_module({module_name!r})
        print(json.dumps([before, list(sys.path)]))
        """
    )
    env = _sandboxed_env(tmp_path, pythonpath=str(_TEAM_DIR))

    result = _run([sys.executable, "-c", probe], tmp_path, env)

    assert result.returncode == 0, _diagnostic(result)
    before, after = json.loads(result.stdout)
    assert after == before, (
        f"importing {module_name} as a package member changed sys.path "
        f"from {before} to {after}"
    )


# ---------------------------------------------------------------------------
# The allow-list and the exit-code table stay honest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", sorted(_PACKAGE_ONLY_MODULES))
def test_package_only_entry_names_a_discovered_module(module_name: str) -> None:
    assert module_name in _DISCOVERED, (
        f"{module_name} is on the package-only allow-list but discovery no "
        f"longer finds it as a command-line module; remove its entry from "
        f"_PACKAGE_ONLY_MODULES in {__file__}"
    )


@pytest.mark.parametrize("module_name", sorted(_PACKAGE_ONLY_MODULES))
def test_package_only_modules_do_not_advertise_by_path_use(module_name: str) -> None:
    # Static guard: a package-only module has no by-path run to exercise, so
    # the invariant -- its own text never tells a reader to start it by file
    # path -- is checked on the source. The behavioural half is the by-path
    # --help test above, which covers every module not on the allow-list.
    path = _DISCOVERED[module_name]
    stem = re.escape(path.stem)
    by_path_forms = [
        re.compile(rf"\bpython[0-9.]*\s+\S*\b{stem}\.py\b"),
        re.compile(rf"\busage:?\s+\S*\b{stem}\.py\b", re.IGNORECASE),
        re.compile(r"\busage\b.*sys\.argv\[0\]", re.IGNORECASE),
    ]
    hits = [
        f"{path}:{number}: {line.strip()}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(form.search(line) for form in by_path_forms)
    ]
    assert not hits, (
        f"{module_name} is package-only but its text advertises the by-path "
        f"form; rewrite each line to 'python3 -m {module_name}', or guard the "
        f"module and drop its allow-list entry:\n" + "\n".join(hits)
    )


# ---------------------------------------------------------------------------
# Negative proof: discovery picks up a new command-line module, and the
# by-path probe fails for one that is neither guarded nor allow-listed
# ---------------------------------------------------------------------------


def _synthetic_package(root: pathlib.Path) -> pathlib.Path:
    """Build a throwaway ``pgai_agent_kanban`` tree under *root* holding one
    module of each discovery shape, and return its package directory."""
    package = root / _PACKAGE_NAME
    _write(package / "__init__.py", "")
    _write(package / "helper.py", "VALUE = 1\n")
    _write(package / "sub" / "__init__.py", "")
    guard = """\
        import pathlib, sys
        if not __package__:
            _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
            if _OWN_PACKAGE_ROOT not in sys.path:
                sys.path.insert(0, _OWN_PACKAGE_ROOT)
        """
    cli = f"""\
        from {_PACKAGE_NAME}.helper import VALUE
        if __name__ == "__main__":
            print(VALUE)
        """
    _write(package / "sub" / "cli_unguarded.py", cli)
    _write(package / "sub" / "cli_guarded.py", guard + cli)
    _write(package / "sub" / "guard_only.py", guard + f"from {_PACKAGE_NAME}.helper import VALUE\n")
    _write(package / "sub" / "import_no_main.py", f"from {_PACKAGE_NAME}.helper import VALUE\n")
    _write(
        package / "sub" / "lazy_import_cli.py",
        f"""\
        def main():
            from {_PACKAGE_NAME}.helper import VALUE
            print(VALUE)
        if __name__ == "__main__":
            main()
        """,
    )
    _write(
        package / "sub" / "guarded_try_import_cli.py",
        f"""\
        try:
            from {_PACKAGE_NAME}.helper import VALUE
        except ImportError:
            VALUE = 0
        if __name__ == "__main__":
            print(VALUE)
        """,
    )
    _write(package / "tests" / "test_cli.py", cli)
    return package


def test_discovery_covers_exactly_the_command_line_and_guarded_shapes(
    tmp_path: pathlib.Path,
) -> None:
    package = _synthetic_package(tmp_path)

    found = _discover_by_path_modules(package)

    assert set(found) == {
        f"{_PACKAGE_NAME}.sub.cli_unguarded",
        f"{_PACKAGE_NAME}.sub.cli_guarded",
        f"{_PACKAGE_NAME}.sub.guard_only",
        f"{_PACKAGE_NAME}.sub.guarded_try_import_cli",
    }, f"discovery returned {sorted(found)}"


def test_by_path_probe_fails_for_an_unguarded_command_line_module(
    tmp_path: pathlib.Path,
) -> None:
    package = _synthetic_package(tmp_path / "tree")
    env = _sandboxed_env(tmp_path)

    unguarded = _run(
        [sys.executable, str(package / "sub" / "cli_unguarded.py"), "--help"], tmp_path, env
    )
    guarded = _run(
        [sys.executable, str(package / "sub" / "cli_guarded.py"), "--help"], tmp_path, env
    )

    assert unguarded.returncode != 0, _diagnostic(unguarded)
    assert f"No module named '{_PACKAGE_NAME}'" in unguarded.stderr, _diagnostic(unguarded)
    assert guarded.returncode == 0, _diagnostic(guarded)
    assert "Traceback" not in guarded.stderr, _diagnostic(guarded)
