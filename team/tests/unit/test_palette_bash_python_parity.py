"""
test_palette_bash_python_parity.py
===================================
Unit tests for the default-palette bash/Python parity fixture.

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

Two behaviors are covered for the ordered PROJECT-color list:

1. Agreement: loading the palette via the bash path (subprocess sourcing
   the real projects_cfg.sh) and via the real Python helper produces
   element-for-element identical sequences.
2. Divergence detection: when a copy of the bash library is deliberately
   pointed at a temporary, perturbed copy of the definition file (one
   entry's hex value mutated), the same parity assertion used in (1)
   fails, and the failure message names the diverging index and both
   the perturbed and canonical values at that index.

The SAME file also declares a second, keyed section -- the semantic
dashboard color keys ("%<key> <color-name>" lines; see that file's own
"Semantic dashboard color keys" header for the documented parse rule).
Two more readers consume that section:

  - team/scripts/lib/dashboard_palette.sh's dashboard_semantic_color() /
    _dashboard_palette_load()
  - pgai_agent_kanban.lib.palette.load_semantic_palette() /
    resolve_semantic_color()

The three tests below extend this same parity fixture to that section
rather than duplicating it in a second file, so both sections share one
fixture: key-for-key agreement, a missing-key refusal that both
readers produce (naming the key and the file), and a perturbed-copy
divergence check mirroring test 2 above.
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
_DASHBOARD_PALETTE_SH = _TEAM_DIR / "scripts" / "lib" / "dashboard_palette.sh"
_PALETTE_DEFINITION_FILE = _TEAM_DIR / "config" / "default_palette.txt"

# Mirrors the parse rule documented in team/config/default_palette.txt and
# implemented identically by both readers: a line is a data line only when
# its first whitespace-delimited token matches this pattern.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Mirrors the semantic-key parse rule documented in the same file's
# "Semantic dashboard color keys" section, and implemented identically by
# both readers (team/scripts/lib/dashboard_palette.sh's `[[ "$line" == %* ]]`
# check; pgai_agent_kanban.lib.palette._SEMANTIC_KEY_LINE_RE).
_SEMANTIC_KEY_LINE_RE = re.compile(r"^%(\S+)\s+(\S+)")


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
    perturbed file -- this recreates the exact drift scenario this parity
    check exists to prevent: one reader (here, bash) sees an edited copy that the
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


# ---------------------------------------------------------------------------
# Semantic dashboard color keys -- the "%<key> <color-name>" section of the
# SAME definition file. Extends this fixture rather than duplicating it in
# a second test file, so both sections share one fixture.
# ---------------------------------------------------------------------------


def _load_bash_semantic_palette(script_path: pathlib.Path) -> dict[str, str]:
    """Source *script_path* (dashboard_palette.sh) and dump its parsed map.

    Exercises the real bash load path: source the library, force a load via
    the internal loader, then print the resulting associative array as
    tab-separated "key<TAB>color-name" lines.
    """
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(script_path))} && "
            "_dashboard_palette_load && "
            'for k in "${!_DASHBOARD_PALETTE_MAP[@]}"; do '
            'printf "%s\\t%s\\n" "$k" "${_DASHBOARD_PALETTE_MAP[$k]}"; done',
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"Sourcing {script_path} failed (exit {result.returncode}); "
        f"stderr: {result.stderr!r}"
    )
    entries: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line:
            continue
        key, _, color_name = line.partition("\t")
        entries[key] = color_name
    return entries


def _assert_semantic_palette_parity(
    bash_map: dict[str, str], python_map: dict[str, str]
) -> None:
    """Assert two semantic key->color-name maps are identical.

    Mirrors _assert_palette_parity's role for the ordered list, adapted to
    a keyed mapping: same key set, same value for every shared key.
    """
    bash_keys = set(bash_map)
    python_keys = set(python_map)
    assert bash_keys == python_keys, (
        f"Semantic palette key sets diverge: bash-only={bash_keys - python_keys!r}, "
        f"python-only={python_keys - bash_keys!r}."
    )

    for key in sorted(bash_keys):
        assert bash_map[key] == python_map[key], (
            f"Semantic palette entries diverge at key {key!r}: "
            f"bash={bash_map[key]!r} python={python_map[key]!r}. The bash "
            f"(team/scripts/lib/dashboard_palette.sh) and Python "
            f"(pgai_agent_kanban.lib.palette.load_semantic_palette) semantic "
            f"palette reads must be key-for-key identical."
        )


def test_bash_and_python_semantic_palette_reads_are_key_for_key_identical() -> None:
    """The bash and Python semantic-key reads of the real file agree.

    Loads the semantic key -> color-name map via a bash subprocess sourcing
    the real team/scripts/lib/dashboard_palette.sh, and via the real Python
    helper's load_semantic_palette(), then asserts key-for-key equality.
    """
    from pgai_agent_kanban.lib.palette import load_semantic_palette

    sys.path.insert(0, str(_TEAM_DIR))

    bash_map = _load_bash_semantic_palette(_DASHBOARD_PALETTE_SH)
    python_map = load_semantic_palette()

    assert bash_map, "Bash semantic palette load produced zero entries."
    assert python_map, "Python semantic palette load produced zero entries."

    _assert_semantic_palette_parity(bash_map, python_map)


def test_semantic_palette_missing_key_refuses_dual_channel_in_both_readers() -> None:
    """Both readers refuse an undeclared semantic key, naming key and file.

    Exercises the palette's fail-loud contract: a lookup of an
    undeclared key must never silently return a default/empty color -- both
    the bash and Python readers must refuse, and both refusals must name
    the missing key and the palette file that was read.
    """
    from pgai_agent_kanban.lib.palette import PaletteKeyError, resolve_semantic_color

    missing_key = "definitely_not_a_declared_semantic_key"

    # Python side: raises PaletteKeyError carrying the dual-channel house
    # shape (error/message/remedy), naming the key and the file.
    with pytest.raises(PaletteKeyError) as excinfo:
        resolve_semantic_color(missing_key, use_color=True)
    python_message = str(excinfo.value)
    assert missing_key in python_message
    assert str(_PALETTE_DEFINITION_FILE) in python_message
    assert "error:" in python_message and "message:" in python_message and "remedy:" in python_message

    # Bash side: dashboard_semantic_color returns non-zero and prints the
    # same three-field house shape to stderr, naming the key and the file.
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {shlex.quote(str(_DASHBOARD_PALETTE_SH))} && "
            f"USE_COLOR=true dashboard_semantic_color {shlex.quote(missing_key)}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0, (
        "dashboard_semantic_color must return non-zero for an undeclared key; "
        f"got exit {result.returncode}, stdout={result.stdout!r}"
    )
    assert missing_key in result.stderr, (
        f"Bash refusal must name the missing key ({missing_key!r}); "
        f"got stderr={result.stderr!r}"
    )
    assert str(_PALETTE_DEFINITION_FILE) in result.stderr, (
        f"Bash refusal must name the palette file ({_PALETTE_DEFINITION_FILE}); "
        f"got stderr={result.stderr!r}"
    )
    assert "error:" in result.stderr and "message:" in result.stderr and "remedy:" in result.stderr


def test_perturbed_temp_copy_of_semantic_palette_fails_parity_naming_diverging_key(
    tmp_path: pathlib.Path,
) -> None:
    """A deliberately perturbed temp copy of the semantic section fails parity.

    Mirrors test_perturbed_temp_copy_fails_parity_naming_diverging_index
    above for the semantic-key section: lays out a temp copy of the
    (unmodified) bash libraries alongside a perturbed copy of the
    definition file (one semantic key's color-name value mutated),
    preserving the real relative layout (scripts/lib/dashboard_palette.sh
    -> ../../config/default_palette.txt; scripts/lib/script_common.sh and
    error_contract.sh as siblings) so the library's own BASH_SOURCE-
    relative resolution finds the perturbed file.
    """
    from pgai_agent_kanban.lib.palette import load_semantic_palette

    real_text = _PALETTE_DEFINITION_FILE.read_text(encoding="utf-8")
    real_lines = real_text.splitlines()
    semantic_line_positions = [
        i for i, line in enumerate(real_lines) if line.startswith("%")
    ]
    assert semantic_line_positions, "Definition file has no semantic-key lines to perturb."

    target_line_index = semantic_line_positions[0]
    original_line = real_lines[target_line_index]
    match = _SEMANTIC_KEY_LINE_RE.match(original_line)
    assert match is not None
    diverging_key = match.group(1)
    original_color = match.group(2)
    replacement_color = "black" if original_color != "black" else "white"

    # The key and color-name fields are whitespace-padded for readability
    # (see the file's own column-aligned layout) -- replace only the color
    # token itself, found by re-matching and slicing on its exact span
    # rather than a literal substring replace (which would need to guess
    # the padding width).
    color_start, color_end = match.span(2)
    perturbed_line = (
        original_line[:color_start] + replacement_color + original_line[color_end:]
    )
    perturbed_lines = list(real_lines)
    perturbed_lines[target_line_index] = perturbed_line
    perturbed_text = "\n".join(perturbed_lines) + "\n"

    perturbed_root = tmp_path / "perturbed-semantic-tree"
    lib_dir = perturbed_root / "scripts" / "lib"
    lib_dir.mkdir(parents=True)
    shutil.copy(_DASHBOARD_PALETTE_SH, lib_dir / "dashboard_palette.sh")
    shutil.copy(_TEAM_DIR / "scripts" / "lib" / "script_common.sh", lib_dir / "script_common.sh")
    shutil.copy(_TEAM_DIR / "scripts" / "lib" / "error_contract.sh", lib_dir / "error_contract.sh")

    config_dir = perturbed_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "default_palette.txt").write_text(perturbed_text, encoding="utf-8")

    perturbed_bash_map = _load_bash_semantic_palette(lib_dir / "dashboard_palette.sh")
    canonical_python_map = load_semantic_palette()

    with pytest.raises(AssertionError) as excinfo:
        _assert_semantic_palette_parity(perturbed_bash_map, canonical_python_map)

    message = str(excinfo.value)
    assert diverging_key in message, (
        f"Failure message must name the diverging key ({diverging_key!r}); "
        f"got: {message!r}"
    )
    assert replacement_color in message, (
        f"Failure message must include the perturbed value ({replacement_color!r}); "
        f"got: {message!r}"
    )
    assert original_color in message, (
        f"Failure message must include the canonical value ({original_color!r}); "
        f"got: {message!r}"
    )
