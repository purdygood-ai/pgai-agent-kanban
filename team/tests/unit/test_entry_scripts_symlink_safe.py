"""Entry scripts locate ``lib/`` through the resolved script path.

Every operator-invoked script under ``team/scripts`` (the top level and the
``cm``, ``cleanup``, ``dashboard``, ``harness``, ``migrate``, ``migrations``
and ``wake`` directories) finds its library directory from
``${BASH_SOURCE[0]}``. A bare ``dirname "${BASH_SOURCE[0]}"`` is the
directory a *symlink* sits in, so a symlink placed on an operator's PATH made
every such script fail with ``lib/env_bootstrap.sh: No such file or
directory``. The path is resolved with ``readlink -f`` first. One test pins
the form across the tree; one invokes real scripts through a symlink.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_ENTRY_DIRS = (
    _SCRIPTS,
    _SCRIPTS / "cm",
    _SCRIPTS / "cleanup",
    _SCRIPTS / "dashboard",
    _SCRIPTS / "harness",
    _SCRIPTS / "migrate",
    _SCRIPTS / "migrations",
    _SCRIPTS / "wake",
    _SCRIPTS / "wake" / "git-guard",
)
_BARE = '$(dirname "${BASH_SOURCE[0]}")'
_RESOLVED = '$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")'


def _entry_scripts() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for d in _ENTRY_DIRS:
        if d.is_dir():
            found.extend(sorted(p for p in d.glob("*.sh") if p.is_file()))
    return found


def test_entry_scripts_exist() -> None:
    assert len(_entry_scripts()) > 100


def test_no_entry_script_uses_bare_dirname_of_bash_source() -> None:
    offenders = []
    for path in _entry_scripts():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # Escaped forms inside printed remedies (``\$(dirname \"\${BASH_SOURCE``)
            # are text, not a path lookup.
            if _BARE not in line or "\\$(dirname" in line:
                continue
            # A script naming *itself* in a remedy keeps the path the operator
            # invoked (a symlinked install dir stays the install dir).
            if f'&& pwd)/{path.name}' in line:
                continue
            offenders.append(f"{path.relative_to(_TEAM_DIR)}:{lineno}")
    assert offenders == [], (
        "entry scripts must resolve the script path before dirname "
        f"(use {_RESOLVED}); bare form at: " + ", ".join(offenders)
    )


def test_resolved_form_is_present_in_tree() -> None:
    uses = sum(1 for p in _entry_scripts() if _RESOLVED in p.read_text(encoding="utf-8"))
    assert uses > 100


@pytest.mark.parametrize(
    "rel",
    [
        "why.sh",
        "kanban-status.sh",
        "release-state.sh",
        "cm/release.sh",
        "dashboard/create.sh",
    ],
)
def test_help_works_through_a_symlink(tmp_path: pathlib.Path, rel: str) -> None:
    target = _SCRIPTS / rel
    assert target.is_file(), rel
    link = tmp_path / "bin" / pathlib.Path(rel).stem
    link.parent.mkdir()
    link.symlink_to(target)
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_TEAM_DIR)
    proc = subprocess.run(
        [str(link), "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    assert "No such file or directory" not in combined, combined
    assert proc.returncode == 0, combined
