"""
test_temp_root_resolution.py
=============================
Direct contract coverage for how the framework resolves its temp root
(scripts/lib/temp.sh): the shared three-tier resolver
``_pgai_temp_dir_from_config``, the read-only ``pgai_configured_temp_root``
built on it, and the invariant both share with ``pgai_temp_dir`` -- the
resolved value never names the filesystem root.

WHY DIRECT TESTS
----------------
``pgai_configured_temp_root`` is the provenance source the git remote-
boundary guard widens its fixture-local exemption from: the guard trusts
its answer to say whether a scratch root is one the framework itself
produced. Until now the only exercise it got was incidental, through guard
tests written for another purpose, which pin one direction of one property
by accident. Each property the function's own header states as contract is
asserted here on its own, so the next edit to any tier cannot narrow the
helper quietly.

Every scenario sources the real temp.sh under bash (tests/unit/
shell_harness.run_bash) and reads the value the function prints; the
self-locate and last-resort tiers copy temp.sh (and the ini_parser.sh it
self-locates) into a scratch install layout so ``_PGAI_TEMP_SH_DIR`` really
does resolve two directories up to a root this test controls.

Covers:
  - the override split: with PGAI_AGENT_KANBAN_TEMP_DIR set to one value
    and kanban.cfg naming another, pgai_configured_temp_root returns the
    configured root and pgai_temp_dir returns the override, in the SAME
    environment -- the two answers differing is the property the guard
    depends on;
  - tier 2a: kanban.cfg found through PGAI_AGENT_KANBAN_ROOT_PATH;
  - tier 2b: the self-locate fallback, with that variable unset and with
    it set but pointing at a directory holding no readable kanban.cfg;
  - tier 3: the last-resort literal when no kanban.cfg is readable on
    either path, and when the configured pair is incomplete;
  - pgai_configured_temp_root never creates the directory it names, while
    pgai_temp_dir does;
  - the filesystem-root decision: a configured pair that canonicalises to
    '/' ('/' + '/', or a parent-reference spelling), and an override that
    does, are rejected like the empty string and the bare '/' and replaced
    by the last-resort literal; the header states that rule in the words
    this module checks for.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_TEMP_SH = _LIB_DIR / "temp.sh"
_INI_PARSER = _LIB_DIR / "ini_parser.sh"

# anti-pattern-allowlist: 2 (justification: this module asserts temp.sh's own
# documented tier-3 fallback and the sentinel it substitutes for a value the
# filesystem-root invariant rejects; the literal IS the value under test, so
# routing through the resolver would compare the resolver to itself)
_LAST_RESORT = "/tmp/pgai_kanban_tmp"


def _write_cfg(root: pathlib.Path, tmp_root: str, tmp_subdir: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "kanban.cfg").write_text(
        f"[paths]\ntmp_root = {tmp_root}\ntmp_subdir = {tmp_subdir}\n", encoding="utf-8"
    )


def _scratch_install(tmp_path: pathlib.Path, name: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Copy temp.sh and ini_parser.sh into <tmp_path>/<name>/scripts/lib so
    the copy's self-locate tier resolves its install root to
    <tmp_path>/<name>. Returns (install root, path of the copied temp.sh)."""
    install = tmp_path / name
    lib = install / "scripts" / "lib"
    lib.mkdir(parents=True)
    shutil.copy(_TEMP_SH, lib / "temp.sh")
    shutil.copy(_INI_PARSER, lib / "ini_parser.sh")
    return install, lib / "temp.sh"


def _resolve(
    tmp_path: pathlib.Path,
    *,
    temp_sh: pathlib.Path = _TEMP_SH,
    root_path: str | None,
    override: str | None,
    call: str = "pgai_configured_temp_root",
) -> str:
    """Source *temp_sh* with PGAI_AGENT_KANBAN_ROOT_PATH / PGAI_AGENT_KANBAN_TEMP_DIR
    set to the given values (None = unset) and print what *call* echoes.

    Both variables are unset inside the script first, so the ambient values
    the pytest process inherited (conftest redirects the root; a wake
    exports the temp dir) never leak into what "unset" means here.
    """
    lines = ["unset PGAI_AGENT_KANBAN_ROOT_PATH PGAI_AGENT_KANBAN_TEMP_DIR"]
    if root_path is not None:
        lines.append(f'export PGAI_AGENT_KANBAN_ROOT_PATH="{root_path}"')
    if override is not None:
        lines.append(f'export PGAI_AGENT_KANBAN_TEMP_DIR="{override}"')
    lines.append(f'source "{temp_sh}"')
    lines.append(f'printf "%s" "$({call})"')
    result = run_bash(tmp_path, "\n".join(lines))
    assert result.returncode == 0, (
        f"{call} exited {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stderr == "", f"{call} must be silent on stderr; got {result.stderr!r}"
    return result.stdout


# ---------------------------------------------------------------------------
# The override split: the property the boundary guard depends on.
# ---------------------------------------------------------------------------


def test_configured_root_ignores_the_override_while_temp_dir_honours_it(
    tmp_path: pathlib.Path,
) -> None:
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, str(tmp_path / "configured"), "scratch")
    override = tmp_path / "override" / "projects" / "name"
    override.mkdir(parents=True)

    configured = _resolve(tmp_path, root_path=str(cfg_root), override=str(override))
    effective = _resolve(
        tmp_path, root_path=str(cfg_root), override=str(override), call="pgai_temp_dir"
    )

    assert configured == str(tmp_path / "configured" / "scratch"), (
        "pgai_configured_temp_root must return the kanban.cfg-derived root and "
        f"ignore PGAI_AGENT_KANBAN_TEMP_DIR entirely; got {configured!r}"
    )
    assert effective == str(override), (
        "pgai_temp_dir must return the PGAI_AGENT_KANBAN_TEMP_DIR override in the "
        f"same environment; got {effective!r}"
    )
    assert configured != effective, (
        "the two answers must differ under an override -- that difference is how "
        "the guard tells a framework-produced root from a configured look-alike"
    )


# ---------------------------------------------------------------------------
# Tier 2a: kanban.cfg reached through PGAI_AGENT_KANBAN_ROOT_PATH.
# ---------------------------------------------------------------------------


def test_tier_2a_reads_kanban_cfg_at_the_root_path(tmp_path: pathlib.Path) -> None:
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, str(tmp_path / "root-a"), "sub-a")
    assert _resolve(tmp_path, root_path=str(cfg_root), override=None) == str(
        tmp_path / "root-a" / "sub-a"
    )


# ---------------------------------------------------------------------------
# Tier 2b: self-locate from the temp.sh copy's own install root.
# ---------------------------------------------------------------------------


def test_tier_2b_self_locates_kanban_cfg_when_root_path_is_unset(
    tmp_path: pathlib.Path,
) -> None:
    install, temp_sh = _scratch_install(tmp_path, "install")
    _write_cfg(install, str(tmp_path / "self-located"), "sub-b")
    assert _resolve(tmp_path, temp_sh=temp_sh, root_path=None, override=None) == str(
        tmp_path / "self-located" / "sub-b"
    ), "with PGAI_AGENT_KANBAN_ROOT_PATH unset the resolver must fall back to the kanban.cfg two directories above temp.sh"


def test_tier_2b_self_locates_when_root_path_names_a_directory_without_kanban_cfg(
    tmp_path: pathlib.Path,
) -> None:
    install, temp_sh = _scratch_install(tmp_path, "install")
    _write_cfg(install, str(tmp_path / "self-located"), "sub-c")
    empty_root = tmp_path / "no-cfg-here"
    empty_root.mkdir()
    assert _resolve(tmp_path, temp_sh=temp_sh, root_path=str(empty_root), override=None) == str(
        tmp_path / "self-located" / "sub-c"
    ), "an unreadable kanban.cfg at the root path must fall through to the self-locate tier, not to the last resort"


# ---------------------------------------------------------------------------
# Tier 3: the last-resort literal.
# ---------------------------------------------------------------------------


def test_tier_3_last_resort_when_no_kanban_cfg_is_readable_anywhere(
    tmp_path: pathlib.Path,
) -> None:
    _install, temp_sh = _scratch_install(tmp_path, "bare-install")  # no kanban.cfg written
    empty_root = tmp_path / "no-cfg-here"
    empty_root.mkdir()
    assert _resolve(tmp_path, temp_sh=temp_sh, root_path=str(empty_root), override=None) == _LAST_RESORT
    assert _resolve(tmp_path, temp_sh=temp_sh, root_path=None, override=None) == _LAST_RESORT


def test_tier_3_last_resort_when_the_configured_pair_is_incomplete(
    tmp_path: pathlib.Path,
) -> None:
    _install, temp_sh = _scratch_install(tmp_path, "half-install")
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, str(tmp_path / "root-only"), "")
    assert _resolve(tmp_path, temp_sh=temp_sh, root_path=str(cfg_root), override=None) == _LAST_RESORT, (
        "tmp_root without tmp_subdir is not a configured root; the resolver must not invent one"
    )


# ---------------------------------------------------------------------------
# Read-only: pgai_configured_temp_root never creates what it names.
# ---------------------------------------------------------------------------


def test_configured_root_does_not_create_the_directory_it_names(
    tmp_path: pathlib.Path,
) -> None:
    cfg_root = tmp_path / "cfg"
    named = tmp_path / "never-created" / "scratch"
    _write_cfg(cfg_root, str(tmp_path / "never-created"), "scratch")

    assert _resolve(tmp_path, root_path=str(cfg_root), override=None) == str(named)
    assert not named.exists() and not named.parent.exists(), (
        "pgai_configured_temp_root is read-only: asking for the configured root "
        f"must not bring {named} into existence"
    )
    # Contrast: pgai_temp_dir is the creating variant.
    assert _resolve(tmp_path, root_path=str(cfg_root), override=None, call="pgai_temp_dir") == str(named)
    assert named.is_dir(), "pgai_temp_dir creates the root it returns"


# ---------------------------------------------------------------------------
# The filesystem-root decision: canonicalising to '/' is rejected.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tmp_root, tmp_subdir",
    [
        pytest.param("/", "/", id="slash-plus-slash"),
        pytest.param("/tmp", "..", id="parent-reference"),
        pytest.param("//", ".", id="double-slash-dot"),
    ],
)
def test_configured_pair_naming_the_filesystem_root_is_replaced_by_the_last_resort(
    tmp_path: pathlib.Path, tmp_root: str, tmp_subdir: str
) -> None:
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, tmp_root, tmp_subdir)
    got = _resolve(tmp_path, root_path=str(cfg_root), override=None)
    assert got == _LAST_RESORT, (
        f"kanban.cfg tmp_root={tmp_root!r} tmp_subdir={tmp_subdir!r} names the "
        f"filesystem root once canonicalised; the resolver must reject it like the "
        f"empty string and the bare '/', not return {got!r}"
    )


@pytest.mark.parametrize("override", ["/", "///", "/.", "/tmp/.."])
def test_override_naming_the_filesystem_root_is_replaced_by_the_last_resort(
    tmp_path: pathlib.Path, override: str
) -> None:
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, str(tmp_path / "configured"), "scratch")
    got = _resolve(tmp_path, root_path=str(cfg_root), override=override, call="pgai_temp_dir")
    assert got == _LAST_RESORT, (
        f"PGAI_AGENT_KANBAN_TEMP_DIR={override!r} names the filesystem root; "
        f"pgai_temp_dir must never hand it back, got {got!r}"
    )


def test_a_real_root_passes_the_invariant_unchanged(tmp_path: pathlib.Path) -> None:
    """The check compares the canonical form only; the configured spelling is
    what comes back, so a symlinked or relative-looking-but-real root is not
    rewritten."""
    cfg_root = tmp_path / "cfg"
    _write_cfg(cfg_root, str(tmp_path / "real"), "root/")
    assert _resolve(tmp_path, root_path=str(cfg_root), override=None) == str(tmp_path / "real") + "/root/"


def test_header_states_the_filesystem_root_decision(tmp_path: pathlib.Path) -> None:
    """The recorded decision: values canonicalising to '/' are rejected like
    the empty string and the bare '/'. The header must say so in those
    terms, so a reader of temp.sh and a reader of this module see the same
    rule."""
    header = _TEMP_SH.read_text(encoding="utf-8")
    assert "canonicalises to '/'" in header, (
        "temp.sh's header must state that a resolved value canonicalising to '/' "
        "is rejected -- the decision this module's filesystem-root tests pin"
    )
    assert "_pgai_temp_root_names_filesystem_root" in header, (
        "temp.sh must name the single helper that implements the invariant"
    )
