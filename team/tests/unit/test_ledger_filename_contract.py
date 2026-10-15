"""
test_ledger_filename_contract.py
=================================
Unit and behavioral tests for the ledger_filename_contract module and its
wiring into discovery's scan-and-select layer (_disc_find_unhandled_items).

Background: two malformation classes were observed in production ledger
filings (bugs/, priority/) on reduced-context lanes — date-first names
(the TASK-ID convention bleeding into the ledger namespace) and
under-padded numbers (a short numeric field that is visually plausible but
arithmetically poisonous under lexical comparison). Neither class was
gated at filing time, and the loose "4 or more digits" scan pattern let a
date-first name's 8-digit date poison the numeric high-water mark used to
allocate the next free ledger number.

This file tests:

  1. ledger_filename_contract module: pattern correctness, defect
     classification, numeric high-water arithmetic, prescriptive remedy text.
  2. _disc_find_unhandled_items behavior via the real filing/scan-and-select
     path (subprocess, not a regex-only unit test):
     a. A date-first priority filename is refused with the date-first
        remedy text; the well-formed sibling file is still selected.
     b. An under-padded priority filename is refused with the padding
        remedy naming both the padded guess and the next free number.
     c. A valid PRIORITY-NNNN-<slug>.md and the historical
        PRIORITY-NNNN-YYYYMMDD-<slug>.md are both accepted, byte-identical
        to today's behavior.
     d. A ledger containing 0009 and 0036 yields next free number 0037
        (numeric, not lexical); a planted malformed file does not
        participate in numbering.

Naming convention: describe the behavior under test, never the bug or
ticket ID.
"""

from __future__ import annotations

import pathlib
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_DISCOVERY_SH = _SCRIPTS_LIB / "discovery.sh"
# _disc_find_unhandled_items lives in this lib stage file and runs its scan
# in the discovery items module; the structural import check below reads
# both.
_DISCOVERY_ITEMS_SH = _SCRIPTS_LIB / "discovery_items.sh"
_DISCOVERY_ITEMS_PY = _TEAM_DIR / "pgai_agent_kanban" / "discovery" / "items.py"
_LEDGER_CONTRACT_PY = (
    _TEAM_DIR / "pgai_agent_kanban" / "lib" / "ledger_filename_contract.py"
)


def _import_ledger_module():
    import sys

    sys.path.insert(0, str(_TEAM_DIR))
    from pgai_agent_kanban.lib import ledger_filename_contract

    return ledger_filename_contract


# ---------------------------------------------------------------------------
# Helper to run _disc_find_unhandled_items in a subprocess (the real
# filing/scan-and-select production path, not a regex-only unit test).
# ---------------------------------------------------------------------------


def _run_find_unhandled(
    target_dir: pathlib.Path,
    id_prefix: str,
    cache_file: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke _disc_find_unhandled_items against target_dir and return the result."""
    cache_path = cache_file if cache_file is not None else (target_dir / "backlog.md")
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        _disc_find_unhandled_items "{target_dir}" "{cache_path}" "{id_prefix}"
    """)

    import os

    env = dict(os.environ)
    env.setdefault("PGAI_AGENT_KANBAN_TEMP_DIR", str(target_dir.parent / "tmp"))

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _write_priority_file(dir_path: pathlib.Path, filename: str) -> pathlib.Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    f = dir_path / filename
    f.write_text(
        "# Synthetic priority fixture\n\n## Status\nopen\n\n## Summary\nFixture.\n",
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# 1. ledger_filename_contract module: pattern and arithmetic correctness
# ---------------------------------------------------------------------------


class TestLedgerFilenameContractModule:
    """The ledger_filename_contract module exports correct patterns and helpers."""

    def test_module_exists(self) -> None:
        assert _LEDGER_CONTRACT_PY.exists(), (
            f"Expected ledger_filename_contract.py at {_LEDGER_CONTRACT_PY}. "
            "This file must exist — it is the single-source ledger filename contract."
        )

    def test_ledger_filename_re_accepts_plain_form(self) -> None:
        mod = _import_ledger_module()
        assert mod.ledger_filename_re("PRIORITY").match("PRIORITY-0036-some-slug.md")
        assert mod.ledger_filename_re("BUG").match("BUG-0009-fix-thing.md")

    def test_ledger_filename_re_accepts_historical_dated_form(self) -> None:
        mod = _import_ledger_module()
        assert mod.ledger_filename_re("PRIORITY").match(
            "PRIORITY-0036-20260803-some-slug.md"
        )

    def test_ledger_filename_re_rejects_date_first_form(self) -> None:
        mod = _import_ledger_module()
        assert not mod.ledger_filename_re("PRIORITY").match(
            "PRIORITY-20260803-001-some-slug.md"
        )

    def test_ledger_filename_re_rejects_under_padded_number(self) -> None:
        mod = _import_ledger_module()
        assert not mod.ledger_filename_re("PRIORITY").match("PRIORITY-036-some-slug.md")

    def test_classify_defect_identifies_date_first(self) -> None:
        mod = _import_ledger_module()
        defect = mod.classify_defect("PRIORITY-20260803-001-slug.md", "PRIORITY")
        assert defect == mod.DEFECT_DATE_FIRST

    def test_classify_defect_identifies_under_padded(self) -> None:
        mod = _import_ledger_module()
        defect = mod.classify_defect("PRIORITY-036-slug.md", "PRIORITY")
        assert defect == mod.DEFECT_UNDER_PADDED

    def test_classify_defect_returns_none_for_valid_name(self) -> None:
        mod = _import_ledger_module()
        assert mod.classify_defect("PRIORITY-0036-slug.md", "PRIORITY") is None

    def test_next_free_number_parses_numerically(
        self, tmp_path: pathlib.Path
    ) -> None:
        """0009 and 0036 in the same directory yield next free number 37.

        A lexical high-water scan would still coincidentally get this case
        right (both numbers are the same zero-padded width), but a planted
        malformed file that carries a much larger apparent numeric value
        must not be allowed to poison the result — see the companion test
        below for that fixture.
        """
        mod = _import_ledger_module()
        d = tmp_path / "priority"
        d.mkdir()
        (d / "PRIORITY-0009-a.md").write_text("x", encoding="utf-8")
        (d / "PRIORITY-0036-b.md").write_text("x", encoding="utf-8")
        assert mod.next_free_number(d, "PRIORITY") == 37

    def test_next_free_number_ignores_malformed_planted_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A planted malformed file with a large apparent number does not poison scanning."""
        mod = _import_ledger_module()
        d = tmp_path / "priority"
        d.mkdir()
        (d / "PRIORITY-0009-a.md").write_text("x", encoding="utf-8")
        (d / "PRIORITY-0036-b.md").write_text("x", encoding="utf-8")
        (d / "PRIORITY-20260803-001-date-first-bad.md").write_text("x", encoding="utf-8")
        assert mod.next_free_number(d, "PRIORITY") == 37

    def test_build_remedy_date_first_names_next_free_number(
        self, tmp_path: pathlib.Path
    ) -> None:
        mod = _import_ledger_module()
        d = tmp_path / "priority"
        d.mkdir()
        (d / "PRIORITY-0036-existing.md").write_text("x", encoding="utf-8")
        remedy = mod.build_remedy("PRIORITY-20260803-001-bad.md", "PRIORITY", d)
        assert "PRIORITY-0037" in remedy
        assert "number-first" in remedy or "date" in remedy.lower()

    def test_build_remedy_under_padded_names_padded_guess_and_next_free(
        self, tmp_path: pathlib.Path
    ) -> None:
        mod = _import_ledger_module()
        d = tmp_path / "priority"
        d.mkdir()
        (d / "PRIORITY-0036-existing.md").write_text("x", encoding="utf-8")
        remedy = mod.build_remedy("PRIORITY-036-bad.md", "PRIORITY", d)
        assert "PRIORITY-0036" in remedy, (
            f"Expected the padded guess in remedy text: {remedy!r}"
        )
        assert "PRIORITY-0037" in remedy, (
            f"Expected the next free number in remedy text: {remedy!r}"
        )

    def test_build_remedy_never_suggests_auto_rename(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Remedy text prescribes; it never instructs an automated rename."""
        mod = _import_ledger_module()
        d = tmp_path / "priority"
        d.mkdir()
        remedy = mod.build_remedy("PRIORITY-036-bad.md", "PRIORITY", d)
        assert "auto-rename" not in remedy.lower()
        assert "automatically renam" not in remedy.lower()


class TestSingleSourceStructural:
    """discovery's scan-and-select layer imports the shared contract."""

    def test_discovery_sh_imports_ledger_filename_contract(self) -> None:
        shell_text = _DISCOVERY_ITEMS_SH.read_text(encoding="utf-8")
        assert "pgai_agent_kanban.discovery.items scan" in shell_text, (
            "discovery_items.sh's _disc_find_unhandled_items must run its scan "
            "through pgai_agent_kanban.discovery.items."
        )
        module_text = _DISCOVERY_ITEMS_PY.read_text(encoding="utf-8")
        assert "pgai_agent_kanban.lib.ledger_filename_contract" in module_text, (
            "pgai_agent_kanban.discovery.items must import from "
            "pgai_agent_kanban.lib.ledger_filename_contract."
        )


# ---------------------------------------------------------------------------
# 2. Behavioral: _disc_find_unhandled_items via the real filing/scan path
# ---------------------------------------------------------------------------


class TestFilingTimeGateRealPath:
    """Exercises the real production scan-and-select entry point, not a
    regex-only unit test, per the acceptance fixtures."""

    def test_date_first_priority_name_is_refused_with_date_first_remedy(
        self, tmp_path: pathlib.Path
    ) -> None:
        priority_dir = tmp_path / "priority"
        _write_priority_file(
            priority_dir, "PRIORITY-20260803-001-date-first-bad.md"
        )
        # A well-formed sibling must remain selected — ledger/selection untouched.
        _write_priority_file(priority_dir, "PRIORITY-0001-well-formed.md")

        result = _run_find_unhandled(priority_dir, "PRIORITY-")
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert "date-first-bad.md" in result.stderr, (
            f"Expected the date-first file to be rejected on stderr.\n"
            f"stderr: {result.stderr}"
        )
        assert "number-first" in result.stderr, (
            f"Expected the date-first remedy text on stderr.\nstderr: {result.stderr}"
        )
        assert "date-first-bad.md" not in result.stdout, (
            f"Malformed file must not appear in the selected/validated output.\n"
            f"stdout: {result.stdout}"
        )
        assert "well-formed" in result.stdout, (
            f"Well-formed sibling file must still be selected.\nstdout: {result.stdout}"
        )

    def test_under_padded_priority_name_is_refused_with_padding_remedy(
        self, tmp_path: pathlib.Path
    ) -> None:
        priority_dir = tmp_path / "priority"
        _write_priority_file(priority_dir, "PRIORITY-0036-existing.md")
        _write_priority_file(priority_dir, "PRIORITY-036-under-padded.md")

        result = _run_find_unhandled(priority_dir, "PRIORITY-")
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        assert "under-padded.md" in result.stderr
        assert "PRIORITY-0036" in result.stderr, (
            f"Expected the padded guess in the remedy.\n"
            f"stderr: {result.stderr}"
        )
        assert "PRIORITY-0037" in result.stderr, (
            f"Expected the next free number in the remedy.\n"
            f"stderr: {result.stderr}"
        )
        assert "under-padded.md" not in result.stdout

    def test_valid_plain_and_historical_dated_names_are_both_accepted(
        self, tmp_path: pathlib.Path
    ) -> None:
        priority_dir = tmp_path / "priority"
        _write_priority_file(priority_dir, "PRIORITY-0036-plain-form.md")
        _write_priority_file(
            priority_dir, "PRIORITY-0037-20260803-historical-dated-form.md"
        )

        result = _run_find_unhandled(priority_dir, "PRIORITY-")
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = {
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        }
        assert "PRIORITY-0036-plain-form.md" in output_names, (
            f"got {output_names!r}\nstderr: {result.stderr}"
        )
        assert "PRIORITY-0037-20260803-historical-dated-form.md" in output_names, (
            f"got {output_names!r}\nstderr: {result.stderr}"
        )
        # No rejections for either well-formed file.
        assert "plain-form.md" not in result.stderr
        assert "historical-dated-form.md" not in result.stderr

    def test_high_water_fixture_yields_correct_next_number_via_module(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The directory scan feeding the real filing path computes 0009,
        0036 -> next free 0037, with a planted malformed file excluded.

        This exercises the exact same next_free_number() call the
        production remedy-building code path uses, against a directory
        shaped like the acceptance fixture.
        """
        priority_dir = tmp_path / "priority"
        priority_dir.mkdir()
        (priority_dir / "PRIORITY-0009-a.md").write_text("x", encoding="utf-8")
        (priority_dir / "PRIORITY-0036-b.md").write_text("x", encoding="utf-8")
        (priority_dir / "PRIORITY-99-planted-malformed.md").write_text(
            "x", encoding="utf-8"
        )

        mod = _import_ledger_module()
        assert mod.next_free_number(priority_dir, "PRIORITY") == 37
