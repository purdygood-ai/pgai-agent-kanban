"""
test_defer_icon_truth.py
==========================
The closing "icon-truth" fixture for the deferral release: asserts
AGREEMENT, not appearance. The status bar's rendered "BUGS DEFERRED n
(<=level)" segment (team/scripts/dashboard/status-bottom.sh, resolved
through team/scripts/dashboard/lib/defer_scope.sh) must report the exact
same count and level the real discovery pipeline
(team/scripts/lib/discovery_defer.sh) actually held back in the same
project state -- never a value that merely LOOKS plausible against
project.cfg.

Checked in the two states where an operator would otherwise have to
trust the surface at its word:
  - the cfg-tier state (project.cfg [release] alone, no campaign file)
  - the campaign-file-tier state (DEFER-LOW-BUGS in force, forcing
    deferred_bugs=true / deferred_bug_level=low regardless of cfg)
plus the malformed-file state, where the surface must never guess a
posture it could not actually resolve -- it renders a warning glyph and
the offending file's path instead of a (possibly wrong) count.

This file keeps its own local copies of the discovery-driving helper
shapes established in test_discovery_bug_deferral_gate.py -- each
integration test file owns its own helper shapes rather than importing
another module's private functions.

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the ticket that
    motivated them (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap
from typing import Optional

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SCRIPTS_LIB = _SCRIPTS_DIR / "lib"
_STATUS_BOTTOM_SH = _SCRIPTS_DIR / "dashboard" / "status-bottom.sh"

_DEFER_SEGMENT_RE = re.compile(r"BUGS DEFERRED (\d+) \(<=(\w+)\)(?: until (\S+))?")


# ---------------------------------------------------------------------------
# Shared helpers (local to this module)
# ---------------------------------------------------------------------------


def _operator_env(kanban_root: pathlib.Path, *, no_color: bool, term: str = "xterm") -> dict:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["TERM"] = term
    if no_color:
        env["NO_COLOR"] = "1"
    else:
        env.pop("NO_COLOR", None)
    return env


def _run_status_bottom(
    kanban_root: pathlib.Path, project_name: str, *, no_color: bool
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_STATUS_BOTTOM_SH), str(kanban_root), project_name],
        capture_output=True,
        text=True,
        env=_operator_env(kanban_root, no_color=no_color),
        cwd=str(kanban_root),
        timeout=30,
    )


def _parse_defer_segment(stdout: str) -> Optional[tuple]:
    """Returns (count, level, until_or_None) or None if the segment is
    absent from the status bar's output.
    """
    m = _DEFER_SEGMENT_RE.search(stdout)
    if not m:
        return None
    return int(m.group(1)), m.group(2), m.group(3)


def _run_discovery(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    Echoes DISCOVERY_LAST_STATUS on stdout after the run.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)
    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(kanban_root)
    env["TEAM_ROOT"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_NAME"] = project_name
    env.pop("PGAI_DEV_TREE_PATH", None)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _set_defer_posture(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    deferred_bugs: Optional[bool] = None,
    deferred_bug_level: Optional[str] = None,
) -> None:
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    lines = ["", "[release]"]
    if deferred_bugs is not None:
        lines.append(f"deferred_bugs = {'true' if deferred_bugs else 'false'}")
    if deferred_bug_level is not None:
        lines.append(f"deferred_bug_level = {deferred_bug_level}")
    with cfg_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str,
    slug: str,
    severity: str,
) -> pathlib.Path:
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"**Severity:** {severity}\n\n"
        "## Status\nopen\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _add_requirements_file(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    """A pending requirements file withholds the hygiene sweep so the
    deferred bug files stay 'open' -- the exact state the status bar's
    count must agree with.
    """
    req_dir = kanban_root / "projects" / project_name / "requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / "v0.0.5-unrelated.md"
    req_file.write_text(
        "# v0.0.5: unrelated\n\n"
        "## Status\nopen\n\n"
        "## Target Version\nv0.0.5\n\n"
        "## Workflow Type\nrelease\n\n"
        "## PM Task\nnone\n\n"
        "## Summary\nTest requirements file.\n",
        encoding="utf-8",
    )
    return req_file


def _bundle_files(kanban_root: pathlib.Path, project_name: str) -> list[pathlib.Path]:
    return list(
        (kanban_root / "projects" / project_name / "requirements").glob("*-bugfix-bundle-*.md")
    )


def _bug_status(bug_file: pathlib.Path) -> str:
    text = bug_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Status":
            return lines[i + 1].strip()
    return ""


# ---------------------------------------------------------------------------
# cfg-tier state: project.cfg [release] alone, no campaign file.
# ---------------------------------------------------------------------------


class TestIconTruthInCfgTierState:
    def test_status_bar_count_and_level_match_what_discovery_held_back(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _set_defer_posture(root, "project_a", deferred_bugs=True, deferred_bug_level="low")
        held_back = [
            _add_bug_file(root, "project_a", "BUG-0001", "low-a", "low"),
            _add_bug_file(root, "project_a", "BUG-0002", "low-b", "low"),
            _add_bug_file(root, "project_a", "BUG-0003", "low-c", "low"),
        ]
        _add_requirements_file(root, "project_a")

        disc_result = _run_discovery(root, "project_a")
        assert disc_result.returncode == 0, disc_result.stderr
        assert not _bundle_files(root, "project_a"), disc_result.stderr
        for bug_file in held_back:
            assert _bug_status(bug_file) == "open", bug_file

        status_result = _run_status_bottom(root, "project_a", no_color=True)
        assert status_result.returncode == 0, status_result.stderr
        segment = _parse_defer_segment(status_result.stdout)
        assert segment is not None, (
            f"Expected a BUGS DEFERRED segment in the status bar.\nstdout: {status_result.stdout!r}"
        )
        count, level, until = segment
        assert count == len(held_back), (
            f"Status bar reported {count} deferred, but discovery actually held "
            f"back {len(held_back)} bug(s).\nstdout: {status_result.stdout!r}"
        )
        assert level == "low"
        assert until is None, "cfg-tier state carries no until target"


# ---------------------------------------------------------------------------
# campaign-file-tier state: DEFER-LOW-BUGS in force.
# ---------------------------------------------------------------------------


class TestIconTruthInCampaignFileTierState:
    def test_status_bar_count_level_and_until_match_what_discovery_held_back(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        campaign_file = root / "projects" / "project_a" / "DEFER-LOW-BUGS"
        campaign_file.write_text("until=v3.0.0\n", encoding="utf-8")

        held_back = [
            _add_bug_file(root, "project_a", "BUG-0001", "low-a", "low"),
            _add_bug_file(root, "project_a", "BUG-0002", "low-b", "low"),
        ]
        _add_requirements_file(root, "project_a")

        disc_result = _run_discovery(root, "project_a")
        assert disc_result.returncode == 0, disc_result.stderr
        assert not _bundle_files(root, "project_a"), disc_result.stderr
        for bug_file in held_back:
            assert _bug_status(bug_file) == "open", bug_file

        status_result = _run_status_bottom(root, "project_a", no_color=True)
        assert status_result.returncode == 0, status_result.stderr
        segment = _parse_defer_segment(status_result.stdout)
        assert segment is not None, (
            f"Expected a BUGS DEFERRED segment in the status bar.\nstdout: {status_result.stdout!r}"
        )
        count, level, until = segment
        assert count == len(held_back), (
            f"Status bar reported {count} deferred, but discovery actually held "
            f"back {len(held_back)} bug(s).\nstdout: {status_result.stdout!r}"
        )
        assert level == "low"
        assert until == "v3.0.0", "campaign-file-tier state must name the until target"


# ---------------------------------------------------------------------------
# malformed-file state: the surface never guesses a posture it could not
# actually resolve.
# ---------------------------------------------------------------------------


class TestIconTruthInMalformedFileState:
    def test_status_bar_shows_warning_glyph_and_path_instead_of_a_guessed_posture(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        campaign_file = root / "projects" / "project_a" / "DEFER-LOW-BUGS"
        campaign_file.write_text("until=not-a-version\n", encoding="utf-8")

        # rich mode: the warning glyph and the exact offending path.
        rich_result = _run_status_bottom(root, "project_a", no_color=False)
        assert rich_result.returncode == 0, rich_result.stderr
        assert "⚠" in rich_result.stdout, (
            f"Expected the warning glyph in rich mode.\nstdout: {rich_result.stdout!r}"
        )
        assert str(campaign_file) in rich_result.stdout, (
            f"Expected the offending file's exact path in rich mode.\nstdout: {rich_result.stdout!r}"
        )
        assert _parse_defer_segment(rich_result.stdout) is None, (
            "A malformed campaign file must never render a (possibly wrong) "
            "BUGS DEFERRED count."
        )

        # NO_COLOR mode: the same path, in plain text, no glyph.
        plain_result = _run_status_bottom(root, "project_a", no_color=True)
        assert plain_result.returncode == 0, plain_result.stderr
        assert f"DEFER CONFIG UNREADABLE: {campaign_file}" in plain_result.stdout, (
            f"Expected the plain-text warning naming the offending path.\n"
            f"stdout: {plain_result.stdout!r}"
        )
        assert "⚠" not in plain_result.stdout, "NO_COLOR mode must not carry the glyph"

        # Agreement: discovery itself never quietly bundles anything while
        # the posture is unresolvable -- the surface's refusal to guess
        # matches discovery's own inability to decide.
        _add_bug_file(root, "project_a", "BUG-0001", "low-a", "low")
        disc_result = _run_discovery(root, "project_a")
        assert not _bundle_files(root, "project_a"), (
            "Discovery must not bundle anything while the campaign file is "
            f"malformed -- the status bar's warning and discovery's own "
            f"behaviour must agree.\nstderr:\n{disc_result.stderr}"
        )
