"""
test_temp_root_resolver.py
==========================
Contract coverage for ``pgai_agent_kanban.lib.temp_root.resolve_temp_root``,
the one Python temp root resolver, driven by the shared case table
``tests/fixtures/temp_root_cases.json`` -- the same rows the bash resolver
in ``scripts/lib/temp.sh`` answers to.

HOW A CASE RUNS
---------------
Each row gets its own sandbox (pytest ``tmp_path``). The resolver and the
package modules it imports are copied into ``<sandbox>/install`` in the
installed layout, so the copy's self-locate step reads
``<sandbox>/install/kanban.cfg`` and never a kanban.cfg next to this
checkout. The copy runs in a fresh ``python3 -I`` subprocess whose
environment is built from an explicit set (``build_scrubbed_env``) plus
the row's own PGAI_AGENT_KANBAN_TEMP_DIR / PGAI_AGENT_KANBAN_ROOT_PATH, so
nothing the pytest process inherited decides what "unset" means. Both
answers are read: the override honoured, and ignored.

Covers:
  - every row of the table, both answers;
  - the copied file set: it holds every first-party module importing the
    resolver loads, so a new import fails one test naming the module
    instead of every row with ModuleNotFoundError;
  - the table itself: unique ids, the fields each row needs, rows a
    resolver without an explicit-root argument can drive unchanged, its
    fallback equal to the resolver's constant, and one row at least for
    each rule this module's docstring names;
  - Python-only behaviour the table cannot express: an explicit kanban
    root wins over PGAI_AGENT_KANBAN_ROOT_PATH; the shipped
    kanban.cfg_example resolves to the same root temp.sh gives; the
    resolver creates nothing;
  - api/config.py kanban_temp_root delegates: it returns the resolver's
    answer for the explicit-root rows, and its _FALLBACK_TEMP_ROOT is the
    resolver's constant.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from pgai_agent_kanban.api import config
from pgai_agent_kanban.lib import ini, temp_root
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.temp_root_cases import TABLE_FALLBACK, TEMP_ROOT_CASES, TempRootCase, set_up_case

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"

# The resolver and everything it imports, relative to the package root.
# test_temp_root_parity.py copies this same set; the import-closure test
# below fails naming any first-party module importing the resolver loads
# that is missing here.
_RESOLVER_FILES = (
    "__init__.py",
    "env.py",
    "lib/__init__.py",
    "lib/error_contract.py",
    "lib/ini.py",
    "lib/temp_root.py",
)

_KANBAN_VARS = ("PGAI_AGENT_KANBAN_TEMP_DIR", "PGAI_AGENT_KANBAN_ROOT_PATH")

# Runs inside the subprocess: import the copied resolver from argv[1], call
# it with the explicit kanban root in argv[2] (JSON null = none), print both
# answers as JSON.
_DRIVER = """
import json, sys
install, kanban_root = sys.argv[1], json.loads(sys.argv[2])
sys.path.insert(0, install)
from pgai_agent_kanban.lib import temp_root
print(json.dumps({
    "module": temp_root.__file__,
    "honoured": temp_root.resolve_temp_root(kanban_root),
    "ignored": temp_root.resolve_temp_root(kanban_root, honour_override=False),
}))
"""


def _install_resolver(install: pathlib.Path) -> None:
    """Copy the resolver into *install* as install.sh lays the package out."""
    for relative in _RESOLVER_FILES:
        target = install / "pgai_agent_kanban" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_PACKAGE_DIR / relative, target)


def _run_case(case: TempRootCase, sandbox: pathlib.Path):
    setup = set_up_case(case, sandbox)
    _install_resolver(setup.install)
    home = sandbox / "home"
    home.mkdir()
    env = build_scrubbed_env(home_dir=home, extra_env=setup.env)
    for name in _KANBAN_VARS:
        assert name in setup.env or name not in env, f"{name} leaked into the case environment"
    result = subprocess.run(
        [sys.executable, "-I", "-c", _DRIVER, str(setup.install), json.dumps(setup.kanban_root)],
        env=env,
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"resolver subprocess for case {case.id!r} exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    answer = json.loads(result.stdout)
    assert pathlib.Path(answer["module"]).is_relative_to(setup.install), (
        f"case {case.id!r} must run the resolver copied into {setup.install}, "
        f"not {answer['module']} -- otherwise its self-locate step reads another tree"
    )
    return setup, answer


# Runs inside the subprocess: import the resolver from the package root in
# argv[1] and print, as JSON, the file of every pgai_agent_kanban module the
# import loaded.
_CLOSURE_DRIVER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import pgai_agent_kanban.lib.temp_root
print(json.dumps(sorted(
    module.__file__ for name, module in sys.modules.items()
    if (name == "pgai_agent_kanban" or name.startswith("pgai_agent_kanban."))
    and getattr(module, "__file__", None)
)))
"""


# ---------------------------------------------------------------------------
# The copied file set.
# ---------------------------------------------------------------------------


def test_copied_files_hold_the_resolver_first_party_import_closure(tmp_path: pathlib.Path) -> None:
    """Importing the resolver from this checkout loads only modules the
    sandbox copy carries; a module it misses is named here, once."""
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", "-c", _CLOSURE_DRIVER, str(_TEAM_DIR)],
        env=build_scrubbed_env(home_dir=home),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"importing pgai_agent_kanban.lib.temp_root from {_TEAM_DIR} exited {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    loaded = {
        pathlib.Path(path).resolve().relative_to(_PACKAGE_DIR.resolve()).as_posix()
        for path in json.loads(result.stdout)
    }
    missing = sorted(loaded - set(_RESOLVER_FILES))
    assert not missing, (
        f"importing pgai_agent_kanban.lib.temp_root loads {missing}, which the sandbox copy lacks; "
        f"add {', '.join(repr(path) for path in missing)} to _RESOLVER_FILES in {pathlib.Path(__file__).name}"
    )


# ---------------------------------------------------------------------------
# Every row of the shared table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda case: case.id)
def test_resolver_answers_every_case_in_the_shared_table(case: TempRootCase, tmp_path: pathlib.Path) -> None:
    setup, answer = _run_case(case, tmp_path)
    detail = f"case {case.id!r} ({case.covers}){': ' + case.note if case.note else ''}"
    assert (
        answer["honoured"] == setup.expected
    ), f"{detail}\nresolve_temp_root() returned {answer['honoured']!r}, the table expects {setup.expected!r}"
    assert answer["ignored"] == setup.expected_ignoring_override, (
        f"{detail}\nresolve_temp_root(honour_override=False) returned {answer['ignored']!r}, "
        f"the table expects {setup.expected_ignoring_override!r}"
    )


# ---------------------------------------------------------------------------
# The table itself.
# ---------------------------------------------------------------------------


def test_case_ids_are_unique() -> None:
    ids = [case.id for case in TEMP_ROOT_CASES]
    assert len(ids) == len(set(ids)), f"duplicate case ids: {sorted({i for i in ids if ids.count(i) > 1})}"


def test_table_fallback_is_the_resolver_constant() -> None:
    assert TABLE_FALLBACK == temp_root.FALLBACK_TEMP_ROOT, (
        f"the table expects the last resort {TABLE_FALLBACK!r} but the resolver's constant is "
        f"{temp_root.FALLBACK_TEMP_ROOT!r}; the two must name the same root"
    )


@pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda case: case.id)
def test_every_case_is_drivable_by_a_resolver_without_an_explicit_root(case: TempRootCase) -> None:
    """temp.sh has no explicit-root argument: a driver exports kanban_root as
    PGAI_AGENT_KANBAN_ROOT_PATH. That reaches the same answer only when the
    row sets no root path of its own and gives the self-locate step nothing
    to find."""
    if case.kanban_root is not None:
        assert (
            case.root_path is None and case.install_cfg is None
        ), f"case {case.id!r} sets kanban_root, so it must leave root_path and install_cfg null"
    if case.override is not None:
        assert (
            case.expected_ignoring_override is not None
        ), f"case {case.id!r} sets an override, so it must state expected_ignoring_override"


@pytest.mark.parametrize(
    ("label", "matches"),
    [
        ("the override", lambda c: c.override and c.covers == "override"),
        ("[paths] through an explicit kanban root", lambda c: c.kanban_root and c.covers.startswith("config")),
        ("[paths] through PGAI_AGENT_KANBAN_ROOT_PATH", lambda c: c.root_path and c.covers.startswith("config")),
        ("[paths] through the self-located install root", lambda c: c.install_cfg and c.covers.startswith("config")),
        (
            "a lone tmp_root taking tmp_subdir's default",
            lambda c: c.covers == "per-key default"
            and c.expected.endswith("/pgai_kanban_tmp")
            and c.expected != "{fallback}",
        ),
        (
            "a lone tmp_subdir taking tmp_root's default",
            lambda c: c.covers == "per-key default" and c.expected.startswith("/tmp/") and c.expected != "{fallback}",
        ),
        ("no readable kanban.cfg", lambda c: c.covers == "no readable kanban.cfg"),
        ("an override naming '/'", lambda c: c.override and c.covers == "filesystem-root invariant"),
        (
            "a configured pair naming '/'",
            lambda c: c.board_cfg
            and not c.override
            and c.expected == "{fallback}"
            and c.covers == "filesystem-root invariant",
        ),
    ],
)
def test_table_covers_each_rule(label: str, matches) -> None:
    assert any(matches(case) for case in TEMP_ROOT_CASES), f"the case table has no row for {label}"


# ---------------------------------------------------------------------------
# Behaviour the table cannot express (no bash twin).
# ---------------------------------------------------------------------------


@pytest.fixture
def no_kanban_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in _KANBAN_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _write_pair(root: pathlib.Path, tmp_root: str, tmp_subdir: str) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(f"[paths]\ntmp_root = {tmp_root}\ntmp_subdir = {tmp_subdir}\n", encoding="utf-8")
    return root


def test_explicit_kanban_root_wins_over_the_root_path_variable(
    tmp_path: pathlib.Path, no_kanban_env: pytest.MonkeyPatch
) -> None:
    other = _write_pair(tmp_path / "other", str(tmp_path / "other-tmp"), "k")
    no_kanban_env.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(other))
    named = _write_pair(tmp_path / "named", str(tmp_path / "named-tmp"), "k")
    bare = tmp_path / "bare"
    bare.mkdir()

    assert temp_root.resolve_temp_root(named) == f"{tmp_path / 'named-tmp'}/k"
    assert temp_root.resolve_temp_root(bare) == temp_root.FALLBACK_TEMP_ROOT, (
        "an explicit kanban root without kanban.cfg reads as the last resort; it must not "
        "fall back to PGAI_AGENT_KANBAN_ROOT_PATH's kanban.cfg"
    )


def test_shipped_kanban_cfg_example_resolves_as_the_shipped_defaults_case(
    tmp_path: pathlib.Path, no_kanban_env: pytest.MonkeyPatch
) -> None:
    """The table's shipped-defaults row is the shipped kanban.cfg_example: its
    [paths] pair is the row's, and the real file resolves to the row's root."""
    (case,) = [c for c in TEMP_ROOT_CASES if c.id == "shipped-paths-defaults"]
    example = _TEAM_DIR.parent / "kanban.cfg_example"
    shipped = {key: ini.read_line_value(example, "paths", key) for key in ("tmp_root", "tmp_subdir")}
    assert shipped == case.board_cfg, (
        f"{example} ships [paths] {shipped}; the shipped-paths-defaults case says {case.board_cfg} -- "
        "update the case to the shipped values"
    )
    root = tmp_path / "board"
    root.mkdir()
    shutil.copy(example, root / "kanban.cfg")
    assert temp_root.resolve_temp_root(root) == case.expected


def test_resolver_creates_nothing(tmp_path: pathlib.Path, no_kanban_env: pytest.MonkeyPatch) -> None:
    named = tmp_path / "never-created"
    root = _write_pair(tmp_path / "board", str(named), "scratch")
    assert temp_root.resolve_temp_root(root) == f"{named}/scratch"
    no_kanban_env.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "override-never-created"))
    assert temp_root.resolve_temp_root(root) == str(tmp_path / "override-never-created")
    assert (
        not named.exists() and not (tmp_path / "override-never-created").exists()
    ), "resolve_temp_root is read-only: naming a root must not create it"


# ---------------------------------------------------------------------------
# api/config.py delegates.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", [c for c in TEMP_ROOT_CASES if c.kanban_root is not None], ids=lambda case: case.id)
def test_kanban_temp_root_returns_the_resolver_answer(
    case: TempRootCase, tmp_path: pathlib.Path, no_kanban_env: pytest.MonkeyPatch
) -> None:
    setup = set_up_case(case, tmp_path)
    for name, value in setup.env.items():
        no_kanban_env.setenv(name, value)
    assert config.kanban_temp_root(setup.kanban_root) == pathlib.Path(
        setup.expected
    ), f"case {case.id!r}: kanban_temp_root must give the resolver's answer {setup.expected!r}"


def test_config_fallback_name_is_the_resolver_constant() -> None:
    assert config._FALLBACK_TEMP_ROOT is temp_root.FALLBACK_TEMP_ROOT
