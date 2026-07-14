"""
test_lint_upgrade_no_temp_copy_guard.py
========================================
Tests for team/scripts/lint_upgrade_no_temp_copy_guard.py.

Tests cover:

  1. **Guard fires on fixture** (negative proof): inject a temp file that
     contains the retired guard block and assert the lint exits 1 with a
     message naming all three retired identifiers.  This is the proof that
     the guard guards — without it, a silent pass-through bug in the scanner
     would make the gate always green.

  2. **Clean tree passes**: the real team/scripts/upgrade.sh produces no hits
     (exit 0).  This confirms the retirement from CODER-20260708-002 is intact
     and the scanner recognises the current file as clean.

  3. **Comment lines are exempt**: a fixture whose retired identifiers appear
     only on comment lines must not trigger the lint (comments describing the
     retirement in past tense are permitted).

  4. **All three identifiers are independently detected**: a fixture containing
     each of the three identifiers in isolation yields a hit for that identifier.

  5. **find_executable_hits raises FileNotFoundError for a missing path**: confirms
     the error path in the scanning function.

All tests use pytest's tmp_path and importlib for isolation — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load lint_upgrade_no_temp_copy_guard.py as a module.
# This file lives at team/tests/unit/test_lint_upgrade_no_temp_copy_guard.py.
# Going up three levels: unit/ → tests/ → team/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent    # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_upgrade_no_temp_copy_guard.py"
_REAL_UPGRADE_SH = _SCRIPTS_DIR / "upgrade.sh"


def _import_lint_module():
    """Import lint_upgrade_no_temp_copy_guard as a module."""
    spec = importlib.util.spec_from_file_location(
        "lint_upgrade_no_temp_copy_guard", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Fixture content that reintroduces the retired guard block.
# This is the canonical positive-detection fixture: all three identifiers
# appear as executable code, not in comments.
# ---------------------------------------------------------------------------
_GUARD_BLOCK_FIXTURE = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # upgrade.sh — synthetic fixture for test_lint_upgrade_no_temp_copy_guard.py
    # The lines below are executable, not comments, so they must trigger the lint.
    _PGAI_UPGRADE_SELF_COPY="$(mktemp -t upgrade_self.XXXXXXXXXX)"
    cp "$0" "$_PGAI_UPGRADE_SELF_COPY"
    chmod +x "$_PGAI_UPGRADE_SELF_COPY"
    export _PGAI_UPGRADE_ORIG_DIR
    _PGAI_UPGRADE_ORIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export _PGAI_UPGRADE_REEXEC=1
    exec bash "$_PGAI_UPGRADE_SELF_COPY" "$@"
    echo "This line is never reached when the guard fires."
    """
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_guard_fires_on_fixture_with_retired_block(tmp_path: Path) -> None:
    """Negative proof: the lint reports hits on a fixture containing the retired guard block.

    Injects a temp file that reintroduces all three retired identifiers as
    executable code.  The lint must:
      - return at least one hit
      - include all three retired identifier names in the reported lines

    This is the proof the guard guards: without it, a silent scanner bug
    would make the gate always green even after a resurrection-by-merge.
    """
    fixture = tmp_path / "upgrade_with_retired_guard.sh"
    fixture.write_text(_GUARD_BLOCK_FIXTURE, encoding="utf-8")

    hits = _lint.find_executable_hits(fixture)

    assert hits, (
        f"Expected the lint to find executable hits in the fixture that contains "
        f"the retired guard block, but find_executable_hits() returned an empty list.\n"
        f"Fixture path: {fixture}\n"
        f"Fixture content:\n{_GUARD_BLOCK_FIXTURE}"
    )

    # All three identifiers must be represented in the reported hits.
    reported_lines = "\n".join(line for _, line in hits)
    for identifier in _lint.RETIRED_IDENTIFIERS:
        assert identifier in reported_lines, (
            f"Expected retired identifier '{identifier}' to appear in the hit lines "
            f"reported by find_executable_hits(), but it was not found.\n"
            f"Reported hits:\n"
            + "\n".join(f"  line {n}: {l}" for n, l in hits)
        )


def test_guard_fires_returns_nonzero_exit_for_fixture(tmp_path: Path) -> None:
    """The lint main() returns exit code 1 when the fixture contains the guard block.

    This verifies the end-to-end integration of find_executable_hits() into
    the CLI: hitting violations causes main() to return 1 (not 0 or 2).
    """
    fixture = tmp_path / "upgrade_fixture_retired.sh"
    fixture.write_text(_GUARD_BLOCK_FIXTURE, encoding="utf-8")

    exit_code = _lint.main(["--upgrade-sh", str(fixture)])

    assert exit_code == 1, (
        f"Expected lint main() to return exit code 1 for a fixture with the "
        f"retired guard block, but got exit code {exit_code}."
    )


def test_clean_upgrade_sh_passes() -> None:
    """The real team/scripts/upgrade.sh produces no hits (exit 0).

    Confirms the retirement applied in CODER-20260708-002 is intact: the
    current upgrade.sh contains no executable lines with the retired identifiers.
    If this test fails, the retired guard block has been reintroduced — either
    by a merge conflict or a direct edit.
    """
    if not _REAL_UPGRADE_SH.exists():
        pytest.skip(f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.")

    hits = _lint.find_executable_hits(_REAL_UPGRADE_SH)

    assert not hits, (
        f"Retired temp-copy guard identifier(s) found as executable code in "
        f"{_REAL_UPGRADE_SH}.  The two-phase architecture retired these identifiers; "
        f"their reappearance indicates a squash-merge conflict or direct edit "
        f"restored the pre-retirement block.\n\n"
        f"Executable hits ({len(hits)}):\n"
        + "\n".join(f"  line {n}: {l}" for n, l in hits)
    )


def test_clean_upgrade_sh_main_exits_zero() -> None:
    """main() returns 0 for the real upgrade.sh with no retired identifiers."""
    if not _REAL_UPGRADE_SH.exists():
        pytest.skip(f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.")

    exit_code = _lint.main(["--upgrade-sh", str(_REAL_UPGRADE_SH)])

    assert exit_code == 0, (
        f"Expected lint main() to return 0 for the clean real upgrade.sh, "
        f"but got exit code {exit_code}."
    )


def test_comment_lines_are_exempt(tmp_path: Path) -> None:
    """Retired identifiers on comment-only lines do not trigger the lint.

    Past-tense explanatory comments describing the retirement are permitted.
    A line whose first non-whitespace character is '#' must never be reported
    as a hit, even if it contains one or more retired identifiers.
    """
    comment_only_fixture = tmp_path / "upgrade_with_comment_refs.sh"
    comment_only_fixture.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            # This script no longer uses _PGAI_UPGRADE_SELF_COPY, _PGAI_UPGRADE_REEXEC,
            # or _PGAI_UPGRADE_ORIG_DIR.  Those variables were part of the retired
            # temp-copy guard, removed in v1.7.0 by the two-phase architecture.
            #
            # _PGAI_UPGRADE_SELF_COPY: held the path to the mktemp self-copy.
            # _PGAI_UPGRADE_REEXEC: set to 1 to signal re-exec'd children.
            # _PGAI_UPGRADE_ORIG_DIR: stored the original script directory.
            echo "Upgrade complete (no guard variables in executable code)"
            """
        ),
        encoding="utf-8",
    )

    hits = _lint.find_executable_hits(comment_only_fixture)

    assert not hits, (
        f"Expected no hits for a fixture where all retired identifiers appear only "
        f"in comment lines, but find_executable_hits() reported {len(hits)} hit(s):\n"
        + "\n".join(f"  line {n}: {l}" for n, l in hits)
    )


def test_each_identifier_detected_independently(tmp_path: Path) -> None:
    """Each of the three retired identifiers is individually detected by the lint.

    Confirms the scanner does not hard-code a single check pattern and that
    removing two of the three identifiers from a fixture would not hide the third.
    """
    for identifier in _lint.RETIRED_IDENTIFIERS:
        fixture = tmp_path / f"upgrade_single_{identifier}.sh"
        fixture.write_text(
            f"#!/usr/bin/env bash\n"
            f"{identifier}='synthetic_value'  # executable assignment\n",
            encoding="utf-8",
        )

        hits = _lint.find_executable_hits(fixture)

        assert hits, (
            f"Expected the lint to detect '{identifier}' as an executable hit, "
            f"but find_executable_hits() returned no hits for the fixture:\n"
            f"  {fixture.read_text(encoding='utf-8')}"
        )

        found_identifiers = [line for _, line in hits if identifier in line]
        assert found_identifiers, (
            f"The lint reported hits for the fixture but none contained '{identifier}':\n"
            + "\n".join(f"  line {n}: {l}" for n, l in hits)
        )


def test_missing_file_raises_file_not_found_error(tmp_path: Path) -> None:
    """find_executable_hits() raises FileNotFoundError when the target path is missing."""
    missing = tmp_path / "nonexistent_upgrade.sh"

    with pytest.raises(FileNotFoundError):
        _lint.find_executable_hits(missing)


def test_main_returns_two_for_missing_file(tmp_path: Path) -> None:
    """main() returns exit code 2 when the --upgrade-sh path does not exist."""
    missing = tmp_path / "does_not_exist.sh"

    exit_code = _lint.main(["--upgrade-sh", str(missing)])

    assert exit_code == 2, (
        f"Expected lint main() to return 2 for a missing path, "
        f"but got {exit_code}."
    )
