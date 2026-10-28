"""
test_temp_root_parity.py
========================
The bash temp root wrapper (``pgai_temp_dir`` / ``pgai_configured_temp_root``
in ``scripts/lib/temp.sh``) gives the same root as the Python resolver
(``pgai_agent_kanban.lib.temp_root.resolve_temp_root``) for every row of the
shared case table ``tests/fixtures/temp_root_cases.json``.

temp.sh matches the resolver rather than calling it, because pgai_temp_dir
runs on hot paths where an interpreter start per call is too dear. This
module is what makes "matches" a checked claim instead of a hope: each row
is run through BOTH resolvers in the same sandbox, and the bash answer is
compared with the answer the Python resolver actually returned there (and
with the table's own expectation, so a row both get wrong still fails).

HOW A CASE RUNS
---------------
Each row gets its own sandbox (pytest ``tmp_path``) laid out by
``set_up_case``. Both resolvers are copied into ``<sandbox>/install`` as
install.sh lays them out -- temp.sh and ini_parser.sh under
``scripts/lib/``, the Python resolver under ``pgai_agent_kanban/`` -- so the
self-locate step of each reads ``<sandbox>/install/kanban.cfg`` and nothing
next to this checkout. Each runs in its own subprocess whose environment is
built from an explicit set (``build_scrubbed_env``) plus the row's own
PGAI_AGENT_KANBAN_TEMP_DIR / PGAI_AGENT_KANBAN_ROOT_PATH.

A row that names an explicit kanban root is driven, on the bash side, by
exporting that root as PGAI_AGENT_KANBAN_ROOT_PATH -- the table guarantees
such rows set no root path and no install kanban.cfg of their own, so the
two forms must agree.

Covers:
  - every row: pgai_temp_dir == resolve_temp_root(), and
    pgai_configured_temp_root == resolve_temp_root(honour_override=False);
    pgai_temp_dir creates the root it returns (that pgai_configured_temp_root
    creates nothing is test_temp_root_resolution.py's);
  - every row, the explicit-config form: pgai_temp_dir <board>/kanban.cfg
    and pgai_configured_temp_root <board>/kanban.cfg equal
    resolve_temp_root(<board>) with and without the override;
  - every row with a board kanban.cfg: load_config <board>/kanban.cfg
    exports PGAI_AGENT_KANBAN_TEMP_DIR and TMPDIR as the resolver's root for
    that cfg, and the directory exists.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.fixtures.temp_root_cases import TEMP_ROOT_CASES, CaseSetup, TempRootCase, set_up_case
from tests.unit.shell_harness import BashResult, run_bash
from tests.unit.test_temp_root_resolver import _RESOLVER_FILES

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PACKAGE_DIR = _TEAM_DIR / "pgai_agent_kanban"
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_CONFIG_LOADER = _LIB_DIR / "config_loader.sh"

# The Python resolver and everything it imports, relative to the package
# root, is _RESOLVER_FILES: one set shared with test_temp_root_resolver.py,
# whose import-closure test names any first-party module the set misses.
# The bash wrapper and the one sibling it sources.
_WRAPPER_FILES = ("temp.sh", "ini_parser.sh")

_KANBAN_VARS = ("PGAI_AGENT_KANBAN_TEMP_DIR", "PGAI_AGENT_KANBAN_ROOT_PATH")

# Runs inside the Python subprocess: import the copied resolver from argv[1]
# and print, as JSON, its answers for the row's own kanban root (argv[2], JSON
# null = none) and for the explicit board root (argv[3]), override honoured
# and ignored.
_PY_DRIVER = """
import json, sys
install, kanban_root, board = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
sys.path.insert(0, install)
from pgai_agent_kanban.lib import temp_root
print(json.dumps({
    "module": temp_root.__file__,
    "honoured": temp_root.resolve_temp_root(kanban_root),
    "ignored": temp_root.resolve_temp_root(kanban_root, honour_override=False),
    "board_honoured": temp_root.resolve_temp_root(board),
    "board_ignored": temp_root.resolve_temp_root(board, honour_override=False),
}))
"""


def _diagnostic(result: "BashResult | subprocess.CompletedProcess") -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _install(install: pathlib.Path) -> pathlib.Path:
    """Copy both resolvers into *install* in the installed layout; return the copied temp.sh."""
    for relative in _RESOLVER_FILES:
        target = install / "pgai_agent_kanban" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_PACKAGE_DIR / relative, target)
    lib = install / "scripts" / "lib"
    lib.mkdir(parents=True)
    for name in _WRAPPER_FILES:
        shutil.copy(_LIB_DIR / name, lib / name)
    return lib / "temp.sh"


def _case_env(setup: CaseSetup, sandbox: pathlib.Path, *, kanban_root_as_root_path: bool) -> dict:
    """The row's environment on a scrubbed base, optionally with its explicit
    kanban root exported as PGAI_AGENT_KANBAN_ROOT_PATH (the bash form)."""
    extra = dict(setup.env)
    if kanban_root_as_root_path and setup.kanban_root is not None:
        extra["PGAI_AGENT_KANBAN_ROOT_PATH"] = setup.kanban_root
    home = sandbox / "home"
    home.mkdir(exist_ok=True)
    env = build_scrubbed_env(home_dir=home, extra_env=extra)
    for name in _KANBAN_VARS:
        assert name in extra or name not in env, f"{name} leaked into the case environment"
    return env


def _python_answers(case: TempRootCase, setup: CaseSetup, sandbox: pathlib.Path) -> dict:
    result = subprocess.run(
        [
            sys.executable, "-I", "-c", _PY_DRIVER,
            str(setup.install), json.dumps(setup.kanban_root), str(setup.board),
        ],
        env=_case_env(setup, sandbox, kanban_root_as_root_path=False),
        cwd=sandbox,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Python resolver for case {case.id!r} exited {result.returncode}\n{_diagnostic(result)}"
    answer = json.loads(result.stdout)
    assert pathlib.Path(answer["module"]).is_relative_to(setup.install), (
        f"case {case.id!r} must run the resolver copied into {setup.install}, not {answer['module']}"
    )
    return answer


def _bash_answers(case: TempRootCase, setup: CaseSetup, temp_sh: pathlib.Path, sandbox: pathlib.Path) -> dict:
    """Source the copied temp.sh and print each answer as one 'name=value' line."""
    board_cfg = setup.board / "kanban.cfg"
    script = "\n".join([
        f"source '{temp_sh}'",
        "printf 'honoured=%s\\n' \"$(pgai_temp_dir)\"",
        "printf 'ignored=%s\\n' \"$(pgai_configured_temp_root)\"",
        f"printf 'board_honoured=%s\\n' \"$(pgai_temp_dir '{board_cfg}')\"",
        f"printf 'board_ignored=%s\\n' \"$(pgai_configured_temp_root '{board_cfg}')\"",
    ])
    result = run_bash(
        sandbox, script, base_env=_case_env(setup, sandbox, kanban_root_as_root_path=True), cwd=sandbox,
    )
    assert result.returncode == 0, f"temp.sh for case {case.id!r} exited {result.returncode}\n{_diagnostic(result)}"
    assert result.stderr == "", f"temp.sh must be silent on stderr for case {case.id!r}\n{_diagnostic(result)}"
    answers = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert set(answers) == {"honoured", "ignored", "board_honoured", "board_ignored"}, _diagnostic(result)
    return answers


def _run_both(case: TempRootCase, sandbox: pathlib.Path) -> "tuple[CaseSetup, dict, dict]":
    setup = set_up_case(case, sandbox)
    temp_sh = _install(setup.install)
    python = _python_answers(case, setup, sandbox)
    bash = _bash_answers(case, setup, temp_sh, sandbox)
    return setup, python, bash


def _detail(case: TempRootCase) -> str:
    return f"case {case.id!r} ({case.covers}){': ' + case.note if case.note else ''}"


# ---------------------------------------------------------------------------
# Every row: the bash wrapper gives the Python resolver's answer.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda case: case.id)
def test_pgai_temp_dir_gives_the_python_resolver_root_for_every_case(
    case: TempRootCase, tmp_path: pathlib.Path
) -> None:
    setup, python, bash = _run_both(case, tmp_path)
    assert python["honoured"] == setup.expected, (
        f"{_detail(case)}\nthe Python resolver returned {python['honoured']!r}, the table expects {setup.expected!r}"
    )
    assert bash["honoured"] == python["honoured"], (
        f"{_detail(case)}\npgai_temp_dir returned {bash['honoured']!r}; "
        f"resolve_temp_root() returned {python['honoured']!r} for the same row"
    )
    assert pathlib.Path(bash["honoured"]).is_dir(), (
        f"{_detail(case)}\npgai_temp_dir must create the root it returns ({bash['honoured']!r})"
    )


@pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda case: case.id)
def test_pgai_configured_temp_root_gives_the_python_resolver_root_ignoring_the_override(
    case: TempRootCase, tmp_path: pathlib.Path
) -> None:
    setup, python, bash = _run_both(case, tmp_path)
    assert python["ignored"] == setup.expected_ignoring_override, (
        f"{_detail(case)}\nresolve_temp_root(honour_override=False) returned {python['ignored']!r}, "
        f"the table expects {setup.expected_ignoring_override!r}"
    )
    assert bash["ignored"] == python["ignored"], (
        f"{_detail(case)}\npgai_configured_temp_root returned {bash['ignored']!r}; "
        f"resolve_temp_root(honour_override=False) returned {python['ignored']!r} for the same row"
    )


@pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda case: case.id)
def test_explicit_config_form_gives_the_python_resolver_root_for_that_kanban_root(
    case: TempRootCase, tmp_path: pathlib.Path
) -> None:
    """pgai_temp_dir <cfg> is load_config's path; resolve_temp_root(<root>) is
    its Python twin. Both read <board>/kanban.cfg alone, whatever the row's
    root path and install kanban.cfg say."""
    _setup, python, bash = _run_both(case, tmp_path)
    assert bash["board_honoured"] == python["board_honoured"], (
        f"{_detail(case)}\npgai_temp_dir <board>/kanban.cfg returned {bash['board_honoured']!r}; "
        f"resolve_temp_root(<board>) returned {python['board_honoured']!r}"
    )
    assert bash["board_ignored"] == python["board_ignored"], (
        f"{_detail(case)}\npgai_configured_temp_root <board>/kanban.cfg returned {bash['board_ignored']!r}; "
        f"resolve_temp_root(<board>, honour_override=False) returned {python['board_ignored']!r}"
    )


# ---------------------------------------------------------------------------
# load_config takes PGAI_AGENT_KANBAN_TEMP_DIR (and TMPDIR) from the wrapper.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", [c for c in TEMP_ROOT_CASES if c.board_cfg is not None], ids=lambda case: case.id
)
def test_load_config_exports_the_resolver_root_for_the_cfg_it_was_given(
    case: TempRootCase, tmp_path: pathlib.Path
) -> None:
    setup = set_up_case(case, tmp_path)
    _install(setup.install)
    python = _python_answers(case, setup, tmp_path)
    board_cfg = setup.board / "kanban.cfg"
    script = "\n".join([
        f"source '{_CONFIG_LOADER}'",
        f"load_config '{board_cfg}' || exit 7",
        "printf 'temp_dir=%s\\n' \"$PGAI_AGENT_KANBAN_TEMP_DIR\"",
        "printf 'tmpdir=%s\\n' \"$TMPDIR\"",
    ])
    result = run_bash(
        tmp_path, script, base_env=_case_env(setup, tmp_path, kanban_root_as_root_path=False), cwd=tmp_path,
    )
    assert result.returncode == 0, f"load_config for case {case.id!r} exited {result.returncode}\n{_diagnostic(result)}"
    exported = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert exported.get("temp_dir") == python["board_honoured"], (
        f"{_detail(case)}\nload_config exported PGAI_AGENT_KANBAN_TEMP_DIR={exported.get('temp_dir')!r}; "
        f"resolve_temp_root(<board>) returned {python['board_honoured']!r}\n{_diagnostic(result)}"
    )
    assert exported.get("tmpdir") == exported.get("temp_dir"), (
        f"{_detail(case)}\nTMPDIR must be exported from the resolved temp root\n{_diagnostic(result)}"
    )
    assert pathlib.Path(exported["temp_dir"]).is_dir(), (
        f"{_detail(case)}\nload_config must leave {exported['temp_dir']!r} created"
    )
