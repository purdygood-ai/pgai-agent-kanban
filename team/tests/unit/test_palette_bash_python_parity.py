"""
test_palette_bash_python_parity.py
===================================
Unit tests for PRIORITY-0004 goal 3 — the parity fixture.

The canonical default project palette lives in exactly one place,
team/config/default_palette.txt.  Two independent readers consume it:

  - the bash array PGAI_DEFAULT_PALETTE, built at source time in
    team/scripts/lib/projects_cfg.sh
  - the Python tuple returned by
    pgai_agent_kanban.lib.palette.load_default_palette()

Both readers are supposed to see the same file and therefore produce the
same ordered sequence of hex colors.  This file proves that structurally,
rather than by inspection, so a future edit that reintroduces a
hand-transcribed copy on one side (or only partially updates one side)
fails the suite instead of silently drifting.

Two behaviors are covered:

1. Agreement: loading the palette via the bash path (subprocess sourcing
   the real projects_cfg.sh) and via the real Python helper produces
   element-for-element identical sequences.
2. Divergence detection: when a copy of the bash library is deliberately
   pointed at a temporary, perturbed copy of the definition file (one
   entry's hex value mutated), the same parity assertion used in (1)
   fails, and the failure message names the diverging index and both
   the perturbed and canonical values at that index.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_PROJECTS_CFG_SH = _TEAM_DIR / "scripts" / "lib" / "projects_cfg.sh"
_PALETTE_DEFINITION_FILE = _TEAM_DIR / "config" / "default_palette.txt"

# Mirrors the parse rule documented in team/config/default_palette.txt and
# implemented identically by both readers: a line is a data line only when
# its first whitespace-delimited token matches this pattern.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _import_load_default_palette():
    """Import and return pgai_agent_kanban.lib.palette.load_default_palette.

    Defensive sys.path insert matches the pattern used elsewhere in this
    test directory (e.g. test_filename_contract.py) -- run-unit-tests.sh
    cd's into team/ before invoking pytest, which normally makes the
    package importable via rootdir insertion, but inserting explicitly
    keeps this test correct when pytest is invoked from another cwd.
    """
    sys.path.insert(0, str(_TEAM_DIR))
    from pgai_agent_kanban.lib.palette import load_default_palette

    return load_default_palette


def _load_bash_palette(script_path: pathlib.Path) -> list[str]:
    """Source *script_path* in a bash subprocess and return PGAI_DEFAULT_PALETTE.

    Exercises the real bash load path exactly as a caller would: source the
    library, then read the resulting array. One element per line on stdout.
    """
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(script_path))} && "
            'printf \'%s\\n\' "${PGAI_DEFAULT_PALETTE[@]}"',
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Sourcing {script_path} failed (exit {result.returncode}); "
        f"stderr: {result.stderr!r}"
    )
    return [line for line in result.stdout.splitlines() if line]


def _assert_palette_parity(bash_palette: list[str], python_palette) -> None:
    """Assert two palette sequences are element-for-element identical.

    This is the actual parity assertion both tests below exercise: the
    agreement test expects it to pass silently; the divergence test expects
    it to raise, naming the diverging index and both values at that index.
    """
    bash_list = list(bash_palette)
    python_list = list(python_palette)

    assert len(bash_list) == len(python_list), (
        f"Palette length mismatch: bash read {len(bash_list)} entries "
        f"{bash_list!r}, python read {len(python_list)} entries "
        f"{python_list!r}."
    )

    for index, (bash_hex, python_hex) in enumerate(zip(bash_list, python_list)):
        assert bash_hex == python_hex, (
            f"Palette entries diverge at index {index}: "
            f"bash={bash_hex!r} python={python_hex!r}. The bash "
            f"(team/scripts/lib/projects_cfg.sh PGAI_DEFAULT_PALETTE) and "
            f"Python (pgai_agent_kanban.lib.palette.load_default_palette) "
            f"palette reads must be element-for-element identical."
        )


def _build_perturbed_definition_text(diverging_index: int) -> tuple[str, str, str]:
    """Return (perturbed_file_text, original_hex, replacement_hex).

    Derives the perturbed content from the REAL definition file rather than
    hand-transcribing the canonical palette a second time in this test --
    only the hex token on the data line at *diverging_index* is replaced.
    """
    real_lines = _PALETTE_DEFINITION_FILE.read_text(encoding="utf-8").splitlines()
    data_line_positions = [
        i
        for i, line in enumerate(real_lines)
        if line.split() and _HEX_RE.match(line.split()[0])
    ]
    assert len(data_line_positions) > diverging_index, (
        f"Definition file at {_PALETTE_DEFINITION_FILE} has "
        f"{len(data_line_positions)} data lines; expected more than "
        f"{diverging_index} to perturb index {diverging_index}."
    )

    target_line_index = data_line_positions[diverging_index]
    original_line = real_lines[target_line_index]
    parts = original_line.split(None, 2)
    original_hex = parts[0]

    # Pick a replacement guaranteed to differ from the original.
    replacement_hex = "#000000" if original_hex.lower() != "#000000" else "#ffffff"

    perturbed_line = replacement_hex + original_line[len(original_hex):]
    perturbed_lines = list(real_lines)
    perturbed_lines[target_line_index] = perturbed_line

    return "\n".join(perturbed_lines) + "\n", original_hex, replacement_hex


# ---------------------------------------------------------------------------
# Test 1 -- agreement: bash and Python reads of the real definition file match
# ---------------------------------------------------------------------------


def test_bash_and_python_palette_reads_are_element_for_element_identical() -> None:
    """The bash and Python palette loads agree, entry for entry, in order.

    Loads PGAI_DEFAULT_PALETTE via a bash subprocess sourcing the real
    team/scripts/lib/projects_cfg.sh, and the real Python helper's
    load_default_palette(), then asserts element-for-element equality.
    Both readers consume team/config/default_palette.txt, so agreement
    here confirms neither side holds a stale or hand-transcribed copy.
    """
    load_default_palette = _import_load_default_palette()

    bash_palette = _load_bash_palette(_PROJECTS_CFG_SH)
    python_palette = load_default_palette()

    assert bash_palette, "Bash palette load produced zero entries."
    assert python_palette, "Python palette load produced zero entries."

    _assert_palette_parity(bash_palette, python_palette)


# ---------------------------------------------------------------------------
# Test 2 -- divergence detection: a perturbed temp copy fails, naming the index
# ---------------------------------------------------------------------------


def test_perturbed_temp_copy_fails_parity_naming_diverging_index(
    tmp_path: pathlib.Path,
) -> None:
    """A deliberately perturbed temp copy fails parity, naming the index.

    Lays out a temp copy of the (unmodified) bash library alongside a
    perturbed copy of the definition file, preserving the real relative
    path (scripts/lib/projects_cfg.sh -> ../../config/default_palette.txt)
    so the library's own BASH_SOURCE-relative resolution finds the
    perturbed file -- this recreates the exact drift scenario PRIORITY-0004
    exists to prevent: one reader (here, bash) sees an edited copy that the
    other reader (Python, reading the real canonical file) does not.

    The same _assert_palette_parity() helper used by the agreement test
    above must fail, and its failure message must name the diverging index
    plus both the perturbed and canonical hex values at that index.
    """
    load_default_palette = _import_load_default_palette()

    diverging_index = 3
    perturbed_text, original_hex, replacement_hex = _build_perturbed_definition_text(
        diverging_index
    )
    assert replacement_hex != original_hex  # sanity: the perturbation changes something

    perturbed_root = tmp_path / "perturbed-tree"
    lib_dir = perturbed_root / "scripts" / "lib"
    lib_dir.mkdir(parents=True)
    shutil.copy(_PROJECTS_CFG_SH, lib_dir / "projects_cfg.sh")

    config_dir = perturbed_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "default_palette.txt").write_text(perturbed_text, encoding="utf-8")

    perturbed_bash_palette = _load_bash_palette(lib_dir / "projects_cfg.sh")
    canonical_python_palette = load_default_palette()

    with pytest.raises(AssertionError) as excinfo:
        _assert_palette_parity(perturbed_bash_palette, canonical_python_palette)

    message = str(excinfo.value)
    assert f"index {diverging_index}" in message, (
        f"Failure message must name the diverging index ({diverging_index}); "
        f"got: {message!r}"
    )
    assert replacement_hex in message, (
        f"Failure message must include the perturbed value ({replacement_hex}); "
        f"got: {message!r}"
    )
    assert original_hex in message, (
        f"Failure message must include the canonical value ({original_hex}); "
        f"got: {message!r}"
    )
