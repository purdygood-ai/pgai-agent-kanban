"""
test_cm_release_promote_helper_path.py
=======================================
Regression coverage for the Step 13c bundle-promotion helper lookup in
`team/scripts/lib/cm/release_finalize.sh`: the expression that resolves
`promote_bundled_items.py` from that stage file's own location must keep
landing on a real file whether the tree is laid out as the dev tree nests it
(`team/scripts/lib/cm/` and `team/scripts/cm/`) or as an install flattens it
(`scripts/lib/cm/` and `scripts/cm/`).

WHY EXTRACTION, NOT REPLICATION
--------------------------------
Rather than hand-retype the resolution expression into the test (risking
silent drift from the real script if it is ever edited), this file extracts
the two assignment lines verbatim from the live `release_finalize.sh` text at
test time and runs the extracted expression under bash with `BASH_SOURCE[0]`
pointing at a stage file placed at each layout under test. The test therefore
exercises the production resolution logic, not a copy of it — a future
edit that reintroduces a caller-relative or sibling-only lookup (the shape
that broke when this stage was split out of `cm/release.sh` without moving
`promote_bundled_items.py` along with it) fails this test rather than
shipping unnoticed.
"""

from __future__ import annotations

import pathlib

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_RELEASE_FINALIZE_SH = _TEAM_DIR / "scripts" / "lib" / "cm" / "release_finalize.sh"

_SCRIPTS_ROOT_MARKER = "_cm_scripts_root="
_PROMOTE_SCRIPT_MARKER = "_cm_promote_script="


def _extract_resolution_snippet(source_path: pathlib.Path) -> str:
    """Extract the two-line promote-script path resolution, verbatim.

    Finds the line assigning `_cm_scripts_root` and the very next line
    assigning `_cm_promote_script`, and returns both joined with a newline.
    Raises AssertionError if either line is missing, so a rename or
    reshape of the resolution logic fails this test loudly instead of
    silently testing nothing.
    """
    lines = source_path.read_text(encoding="utf-8").splitlines()
    root_idx = next(
        (i for i, l in enumerate(lines) if l.strip().startswith(_SCRIPTS_ROOT_MARKER)),
        None,
    )
    assert root_idx is not None, (
        f"Could not find a '{_SCRIPTS_ROOT_MARKER}' assignment in {source_path}. "
        "The promote-script path resolution may have been renamed or reshaped."
    )
    promote_idx = root_idx + 1
    assert promote_idx < len(lines) and lines[promote_idx].strip().startswith(
        _PROMOTE_SCRIPT_MARKER
    ), (
        f"Expected a '{_PROMOTE_SCRIPT_MARKER}' assignment immediately after "
        f"the '{_SCRIPTS_ROOT_MARKER}' line in {source_path}, but did not find one. "
        "The promote-script path resolution may have been renamed or reshaped."
    )
    return "\n".join([lines[root_idx].strip(), lines[promote_idx].strip()])


def _resolve_promote_script(tmp_path: pathlib.Path, stage_file: pathlib.Path) -> object:
    """Run the extracted resolution snippet with BASH_SOURCE[0] == stage_file.

    stage_file need not contain real release_finalize.sh content -- it only
    needs to exist at the path under test, since BASH_SOURCE[0] for a
    directly-executed script is that script's own path. Prints the resolved
    path so the test can assert on both the printed value and whether it
    names a file that actually exists.
    """
    snippet = _extract_resolution_snippet(_RELEASE_FINALIZE_SH)
    stage_file.parent.mkdir(parents=True, exist_ok=True)
    stage_file.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        f"{snippet}\n"
        'echo "$_cm_promote_script"\n'
        'if [[ -f "$_cm_promote_script" ]]; then echo RESOLVED; else echo MISSING; fi\n',
        encoding="utf-8",
    )
    return run_bash(tmp_path, f"bash {str(stage_file)!r}")


def _build_layout(root: pathlib.Path, *, scripts_prefix: str) -> tuple[pathlib.Path, pathlib.Path]:
    """Build a tree with promote_bundled_items.py beside the *cm* stages only.

    scripts_prefix is "team/scripts" for the dev-tree layout or "scripts" for
    the flattened installed layout. Returns (stage_file, real_helper_path).
    The helper is placed ONLY at <scripts_prefix>/cm/promote_bundled_items.py
    -- never beside the stage file in lib/cm/ -- so a resolution that still
    assumes co-location with the stage file (a sibling-only lookup) resolves to a
    path that does not exist and this test catches it.
    """
    stage_file = root / scripts_prefix / "lib" / "cm" / "release_finalize.sh"
    real_helper = root / scripts_prefix / "cm" / "promote_bundled_items.py"
    real_helper.parent.mkdir(parents=True, exist_ok=True)
    real_helper.write_text("# stand-in for promote_bundled_items.py\n", encoding="utf-8")
    return stage_file, real_helper


def test_resolution_finds_helper_in_dev_tree_layout(tmp_path: pathlib.Path) -> None:
    """Dev tree: team/scripts/lib/cm/ stage resolves to team/scripts/cm/ helper."""
    root = tmp_path / "dev_tree"
    stage_file, real_helper = _build_layout(root, scripts_prefix="team/scripts")

    result = _resolve_promote_script(tmp_path, stage_file)

    assert result.returncode == 0, result.stderr
    assert "RESOLVED" in result.stdout, result.stdout
    assert str(real_helper) in result.stdout


def test_resolution_finds_helper_in_installed_layout(tmp_path: pathlib.Path) -> None:
    """Simulated install: flattened scripts/lib/cm/ resolves to scripts/cm/ helper."""
    root = tmp_path / "installed_root"
    stage_file, real_helper = _build_layout(root, scripts_prefix="scripts")

    result = _resolve_promote_script(tmp_path, stage_file)

    assert result.returncode == 0, result.stderr
    assert "RESOLVED" in result.stdout, result.stdout
    assert str(real_helper) in result.stdout


def test_resolution_reports_missing_when_helper_absent(tmp_path: pathlib.Path) -> None:
    """Regression guard: if the helper genuinely is not where resolution
    expects it (either layout), the check must report MISSING rather than
    silently resolving to a path that does not exist. This is what proves
    the test fails against a sibling-only lookup: the
    only file created here is at scripts/cm/, never at scripts/lib/cm/, so a
    resolution that still assumes co-location with the stage file --
    dirname(BASH_SOURCE[0])/promote_bundled_items.py -- resolves to
    scripts/lib/cm/promote_bundled_items.py and reports MISSING."""
    root = tmp_path / "dev_tree_no_helper"
    stage_file = root / "team" / "scripts" / "lib" / "cm" / "release_finalize.sh"
    stage_file.parent.mkdir(parents=True, exist_ok=True)
    # No promote_bundled_items.py anywhere in this tree.

    result = _resolve_promote_script(tmp_path, stage_file)

    assert result.returncode == 0, result.stderr
    assert "MISSING" in result.stdout, result.stdout
