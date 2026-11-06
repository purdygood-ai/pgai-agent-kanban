"""
temp_root_cases.py
==================
Loader for ``temp_root_cases.json``, the temp root resolution cases the
Python resolver (``pgai_agent_kanban.lib.temp_root.resolve_temp_root``) and
the bash resolver (``pgai_temp_dir`` / ``pgai_configured_temp_root`` in
``scripts/lib/temp.sh``) must both satisfy.

The JSON file is the table; this module only reads it and lays one case out
on disk, so every driver -- Python or bash -- sets a case up the same way:

    from tests.fixtures.temp_root_cases import TEMP_ROOT_CASES, set_up_case

    @pytest.mark.parametrize("case", TEMP_ROOT_CASES, ids=lambda c: c.id)
    def test_x(case, tmp_path):
        setup = set_up_case(case, tmp_path)
        # copy the resolver under test into setup.install so its
        # self-locate step finds setup.install / "kanban.cfg", then run it
        # with setup.env (plus PATH/HOME) and, where it takes one,
        # setup.kanban_root; compare with setup.expected.

``set_up_case`` writes the kanban.cfg files and returns the environment
values to set; it never runs a resolver and never touches anything outside
the sandbox it is given. This module imports no pytest.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

TABLE_PATH = pathlib.Path(__file__).with_name("temp_root_cases.json")

_TABLE = json.loads(TABLE_PATH.read_text(encoding="utf-8"))

#: The last-resort root the table expects, as written in the table.
TABLE_FALLBACK: str = _TABLE["fallback"]

CfgSpec = None | str | dict


@dataclasses.dataclass(frozen=True)
class TempRootCase:
    """One row of the table, placeholders not yet substituted."""

    id: str
    covers: str
    override: str | None
    root_path: str | None
    kanban_root: str | None
    board_cfg: CfgSpec
    install_cfg: CfgSpec
    expected: str
    expected_ignoring_override: str | None = None
    note: str = ""
    cwd_cfg: CfgSpec = None


@dataclasses.dataclass(frozen=True)
class CaseSetup:
    """A case laid out under one sandbox, every placeholder substituted.

    Attributes:
        board:       ``<sandbox>/board`` (always exists).
        install:     ``<sandbox>/install`` (exists; holds kanban.cfg when the
                     case has one) -- the driver copies its resolver here.
        env:         The case's variables: PGAI_AGENT_KANBAN_TEMP_DIR and
                     PGAI_AGENT_KANBAN_ROOT_PATH, each present only when the
                     case sets it. Every other kanban variable stays unset.
        kanban_root: The explicit kanban root argument, or None.
        expected:    The root with the override honoured.
        expected_ignoring_override: The root with the override ignored
                     (equal to *expected* when the case sets no override).
    """

    board: pathlib.Path
    install: pathlib.Path
    env: dict
    kanban_root: str | None
    expected: str
    expected_ignoring_override: str


def _case(row: dict) -> TempRootCase:
    fields = {f.name for f in dataclasses.fields(TempRootCase)}
    unknown = set(row) - fields
    if unknown:
        raise ValueError(f"{TABLE_PATH}: case {row.get('id')!r} has unknown fields {sorted(unknown)}")
    return TempRootCase(**row)


#: Every case in the table, in table order.
TEMP_ROOT_CASES: tuple[TempRootCase, ...] = tuple(_case(row) for row in _TABLE["cases"])


def _substitute(value: str, sandbox: pathlib.Path) -> str:
    return value.replace("{sandbox}", str(sandbox)).replace("{fallback}", TABLE_FALLBACK)


def _write_cfg(directory: pathlib.Path, spec: CfgSpec, sandbox: pathlib.Path) -> None:
    if spec is None:
        return
    if isinstance(spec, str):
        text = _substitute(spec, sandbox)
    else:
        lines = ["[paths]"] + [f"{key} = {_substitute(value, sandbox)}" for key, value in spec.items()]
        text = "\n".join(lines) + "\n"
    # newline="" keeps a case's CRLF line endings as written.
    with open(directory / "kanban.cfg", "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def set_up_case(case: TempRootCase, sandbox: pathlib.Path) -> CaseSetup:
    """Create ``<sandbox>/board`` and ``<sandbox>/install`` with the case's kanban.cfg files
    (and ``<sandbox>/kanban.cfg`` when the case sets ``cwd_cfg``)."""
    board = sandbox / "board"
    install = sandbox / "install"
    board.mkdir(parents=True)
    install.mkdir(parents=True)
    _write_cfg(board, case.board_cfg, sandbox)
    _write_cfg(install, case.install_cfg, sandbox)
    # The drivers run each resolver with the sandbox as its working
    # directory, so a cwd_cfg is the kanban.cfg a relative path would reach.
    _write_cfg(sandbox, case.cwd_cfg, sandbox)

    env: dict = {}
    if case.override is not None:
        env["PGAI_AGENT_KANBAN_TEMP_DIR"] = _substitute(case.override, sandbox)
    if case.root_path is not None:
        env["PGAI_AGENT_KANBAN_ROOT_PATH"] = _substitute(case.root_path, sandbox)

    expected = _substitute(case.expected, sandbox)
    ignoring = case.expected_ignoring_override
    return CaseSetup(
        board=board,
        install=install,
        env=env,
        kanban_root=None if case.kanban_root is None else _substitute(case.kanban_root, sandbox),
        expected=expected,
        expected_ignoring_override=expected if ignoring is None else _substitute(ignoring, sandbox),
    )
