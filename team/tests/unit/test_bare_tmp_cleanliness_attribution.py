"""
test_bare_tmp_cleanliness_attribution.py
========================================
Who gets blamed when a new entry appears in bare /tmp during a gated suite
run: ``pgai_tmp_cleanliness_check`` in scripts/lib/temp.sh.

The check diffs a pre-run /tmp snapshot against the current one. Several
managed projects can share a host, and a sibling project may keep a
fixed-name scratch root in /tmp under the same ``pgai_`` prefix the kanban
uses (``pgai_<name>_tmp``, e.g. ``pgai_vpg_tmp``). The check must not call
such an entry kanban residue: it lists it on separate NOTE lines as of
unproven origin, and that alone never makes the check fail. Kanban-shaped
entries (mktemp output such as ``pgai_tmp.ABC123`` or ``tmp.ABC123``) are
still residue and still fail the check, and the configured kanban temp-root
basename is never listed at all. Each residue entry -- and nothing else --
is also left in PGAI_TMP_RESIDUE_FOUND, the per-instance list the gated
runners record one finding each for.

HOW AN ENTRY "APPEARS" WITHOUT TOUCHING /tmp
--------------------------------------------
Every scenario sources the real temp.sh under bash and then redefines
``pgai_tmp_snapshot`` -- the one function that reads /tmp -- to print a
fixed listing: the pre-run snapshot file holds the baseline names, and the
redefined function returns the baseline plus the entries under test. The
real ``pgai_tmp_cleanliness_check`` then runs its diff, classification and
reporting unchanged. Nothing is created in bare /tmp; the snapshot file and
the configured temp root both live under pytest's ``tmp_path``. The
subprocess environment is built from an explicit set (build_scrubbed_env),
with PGAI_AGENT_KANBAN_TEMP_DIR set so temp.sh never reads a kanban.cfg.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import BashResult, run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_TEMP_SH = _TEAM_DIR / "scripts" / "lib" / "temp.sh"

# Names already in /tmp before the simulated run; never reported.
_BASELINE = ("baseline-entry", "systemd-private-baseline")

_SIBLING_ROOT = "pgai_vpg_tmp"
_KANBAN_DEFAULT_ROOT_NAME = "pgai_kanban_tmp"

_FAIL_HEADER = "kanban suite left residue in bare /tmp"
_UNPROVEN_MARK = "origin not proven kanban-authored"

# "[pgai-cleanliness]   /<dir>/<entry> ..." -> the entry basename.
_ENTRY_LINE = re.compile(r"^\[pgai-cleanliness\]\s+(/\S+)")


def _run_check(
    tmp_path: pathlib.Path,
    new_entries: tuple[str, ...],
    *,
    root_name: str = _KANBAN_DEFAULT_ROOT_NAME,
) -> BashResult:
    """Run the real pgai_tmp_cleanliness_check with *new_entries* appearing
    after the pre-run snapshot. The configured temp root is
    ``<tmp_path>/<root_name>``, so its basename is *root_name*."""
    root = tmp_path / root_name
    snapshot = tmp_path / "pre_run_snapshot"
    snapshot.write_text("".join(f"{n}\n" for n in sorted(_BASELINE)), encoding="utf-8")
    current = " ".join(shlex.quote(n) for n in (*_BASELINE, *new_entries))
    script = "\n".join(
        [
            f"source {shlex.quote(str(_TEMP_SH))}",
            f"pgai_tmp_snapshot() {{ printf '%s\\n' {current} | sort; }}",
            f"pgai_tmp_cleanliness_check {shlex.quote(str(snapshot))}",
            "_rc=$?",
            "for _e in \"${PGAI_TMP_RESIDUE_FOUND[@]}\"; do printf 'FOUND=%s\\n' \"$_e\"; done",
            "exit $_rc",
        ]
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(root)},
    )
    return run_bash(tmp_path, script, base_env=env)


def _found(stdout: str) -> list[str]:
    """The basenames PGAI_TMP_RESIDUE_FOUND held after the check (the
    per-instance list the runners record one finding each for)."""
    return [
        os.path.basename(line.removeprefix("FOUND="))
        for line in stdout.splitlines()
        if line.startswith("FOUND=")
    ]


def _sections(stderr: str) -> dict[str, list[tuple[str, str]]]:
    """Split the check's stderr into its NOTE and FAIL blocks.

    Returns {"note": [...], "fail": [...]}, each a list of
    (entry basename, full line) for the entry lines under that header.
    """
    out: dict[str, list[tuple[str, str]]] = {"note": [], "fail": []}
    section = None
    for line in stderr.splitlines():
        if "NOTE:" in line:
            section = "note"
            continue
        if "FAIL:" in line:
            section = "fail"
            continue
        match = _ENTRY_LINE.match(line)
        if match and section is not None:
            out[section].append((os.path.basename(match.group(1)), line))
    return out


def test_sibling_project_temp_root_is_reported_as_unproven_not_as_kanban_residue(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_check(tmp_path, (_SIBLING_ROOT,))
    blocks = _sections(result.stderr)

    assert result.returncode == 0, (
        f"an entry shaped like a sibling project's temp root ({_SIBLING_ROOT}) must not by "
        f"itself fail the check; exit {result.returncode}\nstderr:\n{result.stderr}"
    )
    assert _FAIL_HEADER not in result.stderr, (
        f"{_SIBLING_ROOT} was attributed to the kanban suite:\n{result.stderr}"
    )
    assert [name for name, _ in blocks["note"]] == [_SIBLING_ROOT], (
        f"{_SIBLING_ROOT} should be listed once under the NOTE block; got {blocks}"
    )
    assert _UNPROVEN_MARK in blocks["note"][0][1], (
        f"the NOTE entry line must say its origin is unproven; got {blocks['note'][0][1]!r}"
    )
    assert _found(result.stdout) == [], result.stdout


@pytest.mark.parametrize("kanban_entry", ["pgai_tmp.ABC123", "tmp.ABC123"])
def test_kanban_shaped_entry_is_still_reported_as_kanban_residue(
    tmp_path: pathlib.Path, kanban_entry: str
) -> None:
    result = _run_check(tmp_path, (kanban_entry,))
    blocks = _sections(result.stderr)

    assert result.returncode != 0, (
        f"kanban-shaped residue {kanban_entry} must still fail the check\n"
        f"stderr:\n{result.stderr}"
    )
    assert _FAIL_HEADER in result.stderr
    assert [name for name, _ in blocks["fail"]] == [kanban_entry], (
        f"{kanban_entry} should be listed as kanban residue; got {blocks}"
    )
    assert blocks["note"] == [], f"no NOTE block expected; got {blocks['note']}"
    assert _found(result.stdout) == [kanban_entry], result.stdout


def test_sibling_and_kanban_entries_together_are_each_attributed_correctly(
    tmp_path: pathlib.Path,
) -> None:
    result = _run_check(tmp_path, (_SIBLING_ROOT, "pgai_tmp.ABC123"))
    blocks = _sections(result.stderr)

    assert result.returncode != 0, f"the kanban entry must still fail the check:\n{result.stderr}"
    assert [name for name, _ in blocks["fail"]] == ["pgai_tmp.ABC123"], (
        f"only the kanban-shaped entry belongs under the FAIL block; got {blocks}"
    )
    assert [name for name, _ in blocks["note"]] == [_SIBLING_ROOT], (
        f"the sibling-shaped entry belongs under the NOTE block; got {blocks}"
    )
    assert _found(result.stdout) == ["pgai_tmp.ABC123"], (
        "only kanban residue is a per-instance finding; an entry of unproven "
        f"origin is a NOTE, never a finding: {result.stdout!r}"
    )


@pytest.mark.parametrize("root_name", [_KANBAN_DEFAULT_ROOT_NAME, "pgai_custom_tmp"])
def test_configured_kanban_temp_root_basename_is_never_reported(
    tmp_path: pathlib.Path, root_name: str
) -> None:
    # pgai_custom_tmp has the sibling temp-root shape, but as the configured
    # root it is the kanban's own home and must not appear even as a NOTE.
    result = _run_check(tmp_path, (root_name,), root_name=root_name)

    assert result.returncode == 0, (
        f"the configured temp-root basename {root_name} must not fail the check\n"
        f"stderr:\n{result.stderr}"
    )
    assert result.stderr == "", (
        f"the configured temp-root basename {root_name} must not be listed at all; "
        f"got:\n{result.stderr}"
    )


def test_kanban_default_root_name_outside_a_custom_root_stays_kanban_residue(
    tmp_path: pathlib.Path,
) -> None:
    # pgai_kanban_tmp is the kanban's by name: when the configured root is
    # elsewhere, its appearance in /tmp is kanban code escaping that root,
    # not a sibling project's root.
    result = _run_check(tmp_path, (_KANBAN_DEFAULT_ROOT_NAME,), root_name="pgai_custom_tmp")
    blocks = _sections(result.stderr)

    assert result.returncode != 0, f"expected a kanban-residue failure:\n{result.stderr}"
    assert [name for name, _ in blocks["fail"]] == [_KANBAN_DEFAULT_ROOT_NAME], blocks
