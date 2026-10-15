"""
test_lint_docstring_coverage.py
===============================
team/scripts/lint_docstring_coverage.py reports, under
team/pgai_agent_kanban/, team/pm-agent/ and team/scripts/ (tests and
fixtures excluded), every module without a module docstring, every public
function, class or public method without a docstring, and -- in package
code only -- every ``add_argument`` call without ``help=``. It is quality
class: it exits 0 whatever it finds, in every case below.

Synthetic modules are seeded under tmp_path with the repository layout the
lint expects (``<root>/team/...``) and fed through ``--paths`` or scanned
whole with ``--root``. The last case runs a fixture copy of
run-unit-tests.sh to show the runner carries the lint's findings into its
aggregate without changing its own exit status.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT = _SCRIPTS_DIR / "lint_docstring_coverage.py"
_PREFIX = "docstring_coverage: "

_PKG = "team/pgai_agent_kanban/example"


def _seed(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT), *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(cwd),
    )


def _findings(result: subprocess.CompletedProcess) -> list[str]:
    return [line[len(_PREFIX):] for line in result.stdout.splitlines() if line.startswith(_PREFIX)]


def _scan(root: pathlib.Path, *paths: pathlib.Path) -> list[str]:
    result = _run("--root", str(root), "--paths", *(str(p) for p in paths), cwd=root)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return _findings(result)


# ---------------------------------------------------------------------------
# The acceptance fixture: an undocumented function and a comment-only package
# ---------------------------------------------------------------------------


def test_undocumented_function_and_comment_only_init_are_both_reported(tmp_path: pathlib.Path) -> None:
    module = _seed(
        tmp_path,
        f"{_PKG}/tool.py",
        '"""A documented module."""\n\n\ndef build_widget(size):\n    return size * 2\n',
    )
    init = _seed(tmp_path, f"{_PKG}/__init__.py", "# example -- a package described only in a comment.\n")
    findings = _scan(tmp_path, module, init)
    assert findings == [
        f"{_PKG}/tool.py:4 build_widget -- missing-docstring",
        f"{_PKG}/__init__.py:1 <module> -- missing-module-docstring",
    ], findings


def test_documented_module_reports_nothing(tmp_path: pathlib.Path) -> None:
    module = _seed(
        tmp_path,
        f"{_PKG}/tool.py",
        '"""A documented module."""\n\n\n'
        'def build_widget(size):\n    """Return a widget twice *size*."""\n    return size * 2\n\n\n'
        'class Widget:\n    """A widget."""\n\n'
        '    def area(self):\n        """Return the area."""\n        return 1\n',
    )
    assert _scan(tmp_path, module) == []


def test_empty_module_is_reported(tmp_path: pathlib.Path) -> None:
    init = _seed(tmp_path, f"{_PKG}/__init__.py", "")
    assert _scan(tmp_path, init) == [f"{_PKG}/__init__.py:1 <module> -- missing-module-docstring"]


# ---------------------------------------------------------------------------
# Which names are public
# ---------------------------------------------------------------------------

_NAMES_SOURCE = '''"""Module."""
import typing


class Shape:
    def area(self):
        return 1

    async def refresh(self):
        return None

    @property
    def size(self):
        return 2

    @size.setter
    def size(self, value):
        pass

    @typing.overload
    def scale(self, factor: int) -> int: ...

    def _private_helper(self):
        return 3

    def __repr__(self):
        return "Shape"


class _Hidden:
    def visible_method(self):
        return 4


def _private_function():
    return 5


async def fetch():
    return 6


Alias = Shape
'''


def test_public_names_are_reported_and_private_ones_are_not(tmp_path: pathlib.Path) -> None:
    module = _seed(tmp_path, f"{_PKG}/shapes.py", _NAMES_SOURCE)
    findings = _scan(tmp_path, module)
    names = [line.split(" ")[1] for line in findings]
    assert names == ["Shape", "Shape.area", "Shape.refresh", "Shape.size", "fetch"], findings


def test_finding_names_the_def_line(tmp_path: pathlib.Path) -> None:
    module = _seed(
        tmp_path,
        f"{_PKG}/decorated.py",
        '"""Module."""\nimport functools\n\n\n@functools.lru_cache()\ndef cached():\n    return 1\n',
    )
    assert _scan(tmp_path, module) == [f"{_PKG}/decorated.py:6 cached -- missing-docstring"]


# ---------------------------------------------------------------------------
# add_argument help= (package code only)
# ---------------------------------------------------------------------------

_PARSER_SOURCE = '''"""Module."""
import argparse


def make_parser(extra):
    """Return the parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--bare")
    parser.add_argument("--with-help", help="documented")
    parser.add_argument("--hidden", help=argparse.SUPPRESS)
    parser.add_argument("--spread", **extra)
    parser.add_argument("target")
    return parser
'''


def test_package_add_argument_without_help_is_reported(tmp_path: pathlib.Path) -> None:
    module = _seed(tmp_path, f"{_PKG}/cli.py", _PARSER_SOURCE)
    assert _scan(tmp_path, module) == [
        f"{_PKG}/cli.py:8 --bare -- add-argument-without-help",
        f"{_PKG}/cli.py:12 target -- add-argument-without-help",
    ]


@pytest.mark.parametrize("tree", ["team/scripts", "team/pm-agent"])
def test_add_argument_help_is_not_required_outside_the_package(tmp_path: pathlib.Path, tree: str) -> None:
    module = _seed(tmp_path, f"{tree}/cli.py", _PARSER_SOURCE)
    assert _scan(tmp_path, module) == []


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

_UNDOCUMENTED = "def bare():\n    return 1\n"


@pytest.mark.parametrize(
    "rel",
    [
        "team/pgai_agent_kanban/api/tests/helper.py",
        "team/tests/unit/helper.py",
        "team/scripts/fixtures/sample.py",
        "team/scripts/test_something.py",
        "team/pm-agent/conftest.py",
        "team/other_tree/module.py",
        "team/scripts/notes.txt",
    ],
)
def test_out_of_scope_files_are_not_reported(tmp_path: pathlib.Path, rel: str) -> None:
    path = _seed(tmp_path, rel, _UNDOCUMENTED)
    assert _scan(tmp_path, path) == []


@pytest.mark.parametrize(
    "rel",
    [
        "team/pgai_agent_kanban/lib/module.py",
        "team/pm-agent/module.py",
        "team/scripts/module.py",
        "team/scripts/lib/module.py",
        "team/scripts/dashboard/module.py",
    ],
)
def test_each_scanned_tree_is_reported(tmp_path: pathlib.Path, rel: str) -> None:
    path = _seed(tmp_path, rel, _UNDOCUMENTED)
    assert _scan(tmp_path, path) == [
        f"{rel}:1 <module> -- missing-module-docstring",
        f"{rel}:1 bare -- missing-docstring",
    ]


def test_whole_tree_scan_reports_only_in_scope_code(tmp_path: pathlib.Path) -> None:
    _seed(tmp_path, "team/scripts/tool.py", _UNDOCUMENTED)
    _seed(tmp_path, "team/tests/unit/test_tool.py", _UNDOCUMENTED)
    _seed(tmp_path, "team/other_tree/module.py", _UNDOCUMENTED)
    result = _run("--root", str(tmp_path), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert _findings(result) == [
        "team/scripts/tool.py:1 <module> -- missing-module-docstring",
        "team/scripts/tool.py:1 bare -- missing-docstring",
    ]
    assert "lint_docstring_coverage: totals: 1 file(s) scanned; 2 finding(s)" in result.stdout


def test_unparseable_file_yields_no_finding_and_exits_zero(tmp_path: pathlib.Path) -> None:
    path = _seed(tmp_path, "team/scripts/broken.py", "def broken(:\n")
    assert _scan(tmp_path, path) == []


def test_missing_path_is_narrated_and_still_exits_zero(tmp_path: pathlib.Path) -> None:
    result = _run("--root", str(tmp_path), "--paths", str(tmp_path / "team/scripts/absent.py"), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "not a file" in result.stderr


# ---------------------------------------------------------------------------
# Findings file
# ---------------------------------------------------------------------------


def test_findings_file_carries_each_finding(tmp_path: pathlib.Path) -> None:
    module = _seed(tmp_path, f"{_PKG}/cli.py", _PARSER_SOURCE.replace('"""Module."""\n', ""))
    out = tmp_path / "out" / "findings.json"
    result = _run("--root", str(tmp_path), "--paths", str(module), "--findings-file", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    records = json.loads(out.read_text(encoding="utf-8"))
    assert [(r["kind"], r["line"], r["name"]) for r in records] == [
        ("missing-module-docstring", 1, "<module>"),
        ("add-argument-without-help", 7, "--bare"),
        ("add-argument-without-help", 11, "target"),
    ]
    for record in records:
        assert record["lint"] == "docstring_coverage"
        assert record["severity"] == "quality"
        assert record["file"] == f"{_PKG}/cli.py"
        assert record["summary"].startswith(f"{_PKG}/cli.py:{record['line']} {record['name']}")
        assert record["remedy"] == "python3 team/scripts/lint_docstring_coverage.py"


def test_clean_run_writes_an_empty_findings_file(tmp_path: pathlib.Path) -> None:
    module = _seed(tmp_path, f"{_PKG}/clean.py", '"""Module."""\n')
    out = tmp_path / "findings.json"
    result = _run("--root", str(tmp_path), "--paths", str(module), "--findings-file", str(out), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_checkout_scan_exits_zero_and_prints_totals() -> None:
    """The real checkout is scanned; quality class means exit 0 whatever the
    count, so only the run itself is asserted."""
    result = _run(cwd=_TEAM_DIR.parent)
    assert result.returncode == 0, result.stderr
    assert re.search(r"lint_docstring_coverage: totals: \d+ file\(s\) scanned", result.stdout), result.stdout


# ---------------------------------------------------------------------------
# Runner wiring: findings reach the aggregate, the exit status is unchanged
# ---------------------------------------------------------------------------

_SCRIPT_REF_RE = re.compile(r"((?:dashboard/)?[\w-]+\.(?:py|sh))\b")


def _runner_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """A throwaway repo holding the real unit runner, its real lib/, the
    real docstring-coverage lint, a stand-in (exit 0) for every other script
    the runner names, one passing test, and one undocumented script."""
    team = tmp_path / "fixture_repo" / "team"
    scripts = team / "scripts"
    scripts.mkdir(parents=True)
    runner_text = (_SCRIPTS_DIR / "run-unit-tests.sh").read_text(encoding="utf-8")
    shutil.copy2(_SCRIPTS_DIR / "run-unit-tests.sh", scripts / "run-unit-tests.sh")
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib")
    for name in sorted(set(_SCRIPT_REF_RE.findall(runner_text))):
        real = _SCRIPTS_DIR / name
        if name in ("run-unit-tests.sh", _LINT.name) or not real.is_file():
            continue
        stub = scripts / name
        stub.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".py"):
            stub.write_text('#!/usr/bin/env python3\n"""Stand-in."""\nimport sys\nsys.exit(0)\n', encoding="utf-8")
        else:
            stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    shutil.copy2(_LINT, scripts / _LINT.name)
    _seed(team, "scripts/seeded_tool.py", '"""Module."""\n\n\ndef undocumented():\n    return 1\n')
    for rel in ("tests/unit", "pgai_agent_kanban/api/tests"):
        (team / rel).mkdir(parents=True, exist_ok=True)
    (team / "tests/unit/test_fixture_marker.py").write_text(
        "def test_fixture_marker():\n    assert True\n", encoding="utf-8"
    )
    return team


def test_unit_runner_merges_docstring_findings_and_keeps_exit_zero(tmp_path: pathlib.Path) -> None:
    team = _runner_fixture(tmp_path)
    kanban_root = tmp_path / "kanban_root"
    kanban_root.mkdir()
    (kanban_root / "kanban.cfg").write_text("[testing]\nworkers = 1\n", encoding="utf-8")
    temp_dir = tmp_path / "scratch"
    temp_dir.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
    }
    result = subprocess.run(
        ["bash", str(team / "scripts" / "run-unit-tests.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(team),
    )
    detail = f"exit {result.returncode}\nstdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
    assert result.returncode == 0, detail
    assert "team/scripts/seeded_tool.py:4 undocumented -- missing-docstring" in result.stdout, detail

    aggregate = json.loads((temp_dir / "lint" / "findings-unit.json").read_text(encoding="utf-8"))
    records = aggregate["findings"] if isinstance(aggregate, dict) else aggregate
    coverage = [r for r in records if r.get("lint") == "docstring_coverage"]
    seeded = [r for r in coverage if r["file"] == "team/scripts/seeded_tool.py"]
    assert [(r["line"], r["name"], r["kind"]) for r in seeded] == [
        (4, "undocumented", "missing-docstring")
    ], json.dumps(aggregate, indent=2)[:4000]
