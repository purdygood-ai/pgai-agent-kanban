"""
test_tiered_cfg_value_resolver.py — the project.cfg > kanban.cfg > default
resolver and its bash twin answer identically.

Every row of ``CASES`` builds one kanban root in ``tmp_path`` and asserts the
same expected ``(value, tier, source)`` from both resolvers:

  * Python: ``pgai_agent_kanban.lib.project_cfg.resolve_tiered_cfg_value``
  * bash:   ``pp_resolve_tiered_cfg_value`` in ``team/scripts/lib/project_paths.sh``,
            sourced under ``set -euo pipefail``, in its ``--with-tier`` form
            and its plain value form.

The rows cover the precedence itself, then the edge cases every hand-written
tier walk handles today: a blank value, a missing project.cfg, kanban.cfg or
section, the legacy PROJECT.cfg fallback (and opting out of it), root-only
resolution, and the ``read_ini`` line rules (inline comments, CRLF, exact key
match, first line wins).
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import subprocess
import sys
from collections.abc import Mapping

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import project_cfg  # noqa: E402
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env  # noqa: E402

_PROJECT_PATHS = _TEAM_DIR / "scripts" / "lib" / "project_paths.sh"
_PROJECT_DIR = "projects/demo"
_PROJECT_CFG = f"{_PROJECT_DIR}/project.cfg"
_LEGACY_CFG = f"{_PROJECT_DIR}/PROJECT.cfg"
_KANBAN_CFG = "kanban.cfg"


@dataclasses.dataclass(frozen=True)
class Case:
    """One fixture row: the files to write, the lookup, and the expected answer.

    ``files`` maps a path relative to the kanban root to its exact text.
    ``project`` False resolves root-only (empty project root).
    ``default`` None omits the default argument entirely.
    ``source`` is relative to the kanban root; ``""`` for the default tier.
    """

    id: str
    files: Mapping[str, str]
    value: str
    tier: str
    source: str
    section: str = "release"
    key: str = "knob"
    default: str | None = "fallback"
    project: bool = True
    legacy: bool = True


CASES = [
    # --- precedence --------------------------------------------------------
    Case(
        "project_cfg_outranks_kanban_cfg",
        {_PROJECT_CFG: "[release]\nknob = from-project\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-project",
        "project.cfg",
        _PROJECT_CFG,
    ),
    Case(
        "kanban_cfg_answers_when_only_it_sets_the_key",
        {_PROJECT_CFG: "[release]\nother = x\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "default_answers_when_neither_sets_the_key",
        {_PROJECT_CFG: "[release]\nother = x\n", _KANBAN_CFG: "[release]\nother = y\n"},
        "fallback",
        "default",
        "",
    ),
    Case(
        "omitted_default_reads_as_empty",
        {_KANBAN_CFG: "[release]\nother = y\n"},
        "",
        "default",
        "",
        default=None,
    ),
    # --- blank values ------------------------------------------------------
    Case(
        "blank_project_value_falls_through_to_kanban_cfg",
        {_PROJECT_CFG: "[release]\nknob =\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "whitespace_only_project_value_falls_through",
        {_PROJECT_CFG: "[release]\nknob =   \t\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "blank_in_both_files_falls_through_to_default",
        {_PROJECT_CFG: "[release]\nknob =\n", _KANBAN_CFG: "[release]\nknob = \n"},
        "fallback",
        "default",
        "",
    ),
    # --- missing files and sections ----------------------------------------
    Case(
        "missing_project_cfg_reads_kanban_cfg",
        {_KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "missing_kanban_cfg_still_reads_project_cfg",
        {_PROJECT_CFG: "[release]\nknob = from-project\n"},
        "from-project",
        "project.cfg",
        _PROJECT_CFG,
    ),
    Case(
        "missing_kanban_cfg_and_unset_project_key_give_default",
        {_PROJECT_CFG: "[release]\nother = x\n"},
        "fallback",
        "default",
        "",
    ),
    Case(
        "no_config_files_at_all_give_default",
        {},
        "fallback",
        "default",
        "",
    ),
    Case(
        "key_under_another_section_in_project_cfg_is_not_read",
        {_PROJECT_CFG: "[locks]\nknob = wrong-section\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "missing_section_in_both_files_gives_default",
        {_PROJECT_CFG: "[project]\nknob = a\n", _KANBAN_CFG: "[wake]\nknob = b\n"},
        "fallback",
        "default",
        "",
    ),
    # --- legacy PROJECT.cfg ------------------------------------------------
    Case(
        "legacy_project_cfg_bare_line_is_read_when_project_cfg_is_absent",
        {_LEGACY_CFG: 'knob="from-legacy"\n', _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-legacy",
        "project.cfg",
        _LEGACY_CFG,
    ),
    Case(
        "legacy_project_cfg_section_line_is_read_when_project_cfg_is_absent",
        {_LEGACY_CFG: "[release]\nknob = legacy-section\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "legacy-section",
        "project.cfg",
        _LEGACY_CFG,
    ),
    Case(
        "legacy_project_cfg_is_ignored_when_project_cfg_exists",
        {
            _PROJECT_CFG: "[release]\nother = x\n",
            _LEGACY_CFG: "knob=from-legacy\n",
            _KANBAN_CFG: "[release]\nknob = from-kanban\n",
        },
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "legacy_opt_out_ignores_a_lone_project_cfg_legacy_file",
        {_LEGACY_CFG: "knob=from-legacy\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
        legacy=False,
    ),
    Case(
        "legacy_opt_out_still_reads_project_cfg",
        {_PROJECT_CFG: "[release]\nknob = from-project\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-project",
        "project.cfg",
        _PROJECT_CFG,
        legacy=False,
    ),
    # --- root-only -----------------------------------------------------------
    Case(
        "root_only_skips_the_project_tier",
        {_PROJECT_CFG: "[release]\nknob = from-project\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
        project=False,
    ),
    Case(
        "root_only_with_nothing_set_gives_default",
        {_PROJECT_CFG: "[release]\nknob = from-project\n"},
        "fallback",
        "default",
        "",
        project=False,
    ),
    # --- read_ini line rules -----------------------------------------------
    Case(
        "inline_comment_is_stripped",
        {_PROJECT_CFG: "[release]\nknob = 7   # seven\n", _KANBAN_CFG: "[release]\nknob = 3 ; three\n"},
        "7",
        "project.cfg",
        _PROJECT_CFG,
    ),
    Case(
        "crlf_line_endings_read_as_lf",
        {_KANBAN_CFG: "[release]\r\nknob = crlf-value\r\n"},
        "crlf-value",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "spaced_section_header_matches",
        {_KANBAN_CFG: "[ release ]\nknob = spaced\n"},
        "spaced",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "key_match_is_case_sensitive",
        {_PROJECT_CFG: "[release]\nKNOB = upper\n", _KANBAN_CFG: "[release]\nKnob = mixed\n"},
        "fallback",
        "default",
        "",
    ),
    Case(
        "first_matching_line_wins",
        {_KANBAN_CFG: "[release]\nknob = first\nknob = second\n"},
        "first",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "value_keeps_equals_signs",
        {_KANBAN_CFG: "[release]\nknob = a=b=c\n"},
        "a=b=c",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    Case(
        "other_section_and_key_resolve_through_the_same_chain",
        {
            _PROJECT_CFG: "[locks]\nstale_lock_reap_after_wake_periods = 10\n",
            _KANBAN_CFG: "[locks]\nstale_lock_reap_after_wake_periods = 3\n",
        },
        "10",
        "project.cfg",
        _PROJECT_CFG,
        section="locks",
        key="stale_lock_reap_after_wake_periods",
        default="3",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_root(tmp_path: pathlib.Path, files: Mapping[str, str]) -> pathlib.Path:
    root = tmp_path / "root"
    (root / _PROJECT_DIR).mkdir(parents=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


def _python_resolve(root: pathlib.Path, case: Case) -> project_cfg.TieredValue:
    project_root = root / _PROJECT_DIR if case.project else None
    kwargs = {} if case.default is None else {"default": case.default}
    return project_cfg.resolve_tiered_cfg_value(
        root, project_root, case.section, case.key, legacy=case.legacy, **kwargs
    )


def _bash_resolve(tmp_path: pathlib.Path, root: pathlib.Path, case: Case) -> tuple[list[str], str]:
    """Run the bash twin under strict mode; return (--with-tier lines, plain value)."""
    args = [] if case.legacy else ["--no-legacy"]
    args += [str(root), str(root / _PROJECT_DIR) if case.project else "", case.section, case.key]
    if case.default is not None:
        args.append(case.default)
    script = (
        "set -euo pipefail\n"
        'source "$1"; shift\n'
        'pp_resolve_tiered_cfg_value --with-tier "$@"\n'
        "printf 'PLAIN:'\n"
        'pp_resolve_tiered_cfg_value "$@"\n'
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
        },
    )
    proc = subprocess.run(
        ["bash", "-c", script, "resolve", str(_PROJECT_PATHS), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        cwd=str(tmp_path),
    )
    assert (
        proc.returncode == 0
    ), f"bash twin failed (rc={proc.returncode}):\nstdout={proc.stdout!r}\nstderr={proc.stderr}"
    tiered, sep, plain = proc.stdout.partition("PLAIN:")
    assert sep, f"bash twin output lacks the plain-value marker:\n{proc.stdout!r}\nstderr={proc.stderr}"
    return tiered.split("\n")[:-1], plain


def _expected_source(root: pathlib.Path, case: Case) -> str:
    return str(root / case.source) if case.source else ""


# ---------------------------------------------------------------------------
# The shared fixture table, driven through both languages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_python_and_bash_return_the_same_value_tier_and_source(tmp_path, case):
    root = _build_root(tmp_path, case.files)
    expected = project_cfg.TieredValue(case.value, case.tier, _expected_source(root, case))

    assert _python_resolve(root, case) == expected

    tiered_lines, plain = _bash_resolve(tmp_path, root, case)
    assert tiered_lines == [expected.value, expected.tier, expected.source]
    assert plain == expected.value


def test_fixture_table_ids_are_unique():
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


_UNREADABLE_ROWS = [
    # (file made unreadable, files, expected value, tier, source)
    (
        _PROJECT_CFG,
        {_PROJECT_CFG: "[release]\nknob = from-project\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "from-kanban",
        "kanban.cfg",
        _KANBAN_CFG,
    ),
    (
        _KANBAN_CFG,
        {_PROJECT_CFG: "[release]\nother = x\n", _KANBAN_CFG: "[release]\nknob = from-kanban\n"},
        "fallback",
        "default",
        "",
    ),
]


@pytest.mark.parametrize("unreadable, files, value, tier, source", _UNREADABLE_ROWS, ids=["project_cfg", "kanban_cfg"])
def test_unreadable_file_passes_to_the_next_tier_in_both_languages(tmp_path, unreadable, files, value, tier, source):
    case = Case(f"unreadable_{unreadable}", files, value, tier, source)
    root = _build_root(tmp_path, case.files)
    path = root / unreadable
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("file permissions do not restrict reads for this user (running as root)")
        expected = project_cfg.TieredValue(value, tier, _expected_source(root, case))
        assert _python_resolve(root, case) == expected
        tiered_lines, plain = _bash_resolve(tmp_path, root, case)
        assert tiered_lines == [expected.value, expected.tier, expected.source]
        assert plain == expected.value
    finally:
        path.chmod(0o644)
