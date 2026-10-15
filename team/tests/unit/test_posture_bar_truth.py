"""
test_posture_bar_truth.py
===========================
Posture-truth fixtures: the bar structurally cannot show one live flag
while a second one is also active and silently invisible.

WHY THIS EXISTS
---------------
Previously, team/scripts/dashboard/lib/halt_scope.sh's
dashboard_halt_scope() emitted ONE state token and its per-project ladder
stopped at the FIRST halted-or-draining scope it found. An operator
reading the taskbar during a global HALT that coincided with a project's
own HALT-AFTER (or during two independently-halted projects) was shown a
true fact that implied a false one: "here is THE active flag" when there
were actually two or three. This file locks in the full render: every active
scope renders simultaneously, each naming its own scope, and a
version-pinned HALT-AFTER's "until vX.Y.Z" phrase is composed in exactly
one place in the tree.

ACCEPTANCE CRITERIA COVERED
---------------------------
  1. A global HALT, a project HALT-AFTER, and a non-zero deferral posture
     all in force at once -- the bar renders all three segments
     simultaneously, each naming its scope.
  2. Two different projects halted -- both are named; neither is
     suppressed by the other.
  3. Rendered scope words match the SOP vocabulary exactly (GLOBAL, or the
     literal project name -- never a generic "PROJECT" placeholder).
  4. A version-pinned HALT-AFTER renders "until vX.Y.Z", and grep shows
     that phrase composed in exactly one place in the tree.
  5. The NO_COLOR form carries the same set of segments as the rich form,
     for every fixture combination.
  6. Segment order is identical across repeated renders of the same
     state.

Exercised primarily against team/scripts/dashboard/status-bottom.sh (the
taskbar referred to as "the bar"); the simultaneous
scenario is also checked against status-right.sh and show-header.sh, since
those surfaces must agree with the bottom bar rather
than showing a contradictory picture.

Design notes:
  - Uses the two_project_root fixture (tests/fixtures/two_project.py) for
    the two-halted-projects scenario -- the standard cross-project
    fixture, not a hand-rolled tree.
  - Test names describe the behaviour under test, not the ticket ID
    (docs/test-authoring-guidelines.md, Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DASHBOARD_DIR = _TEAM_DIR / "scripts" / "dashboard"
_STATUS_BOTTOM_SH = _DASHBOARD_DIR / "status-bottom.sh"
_STATUS_RIGHT_SH = _DASHBOARD_DIR / "status-right.sh"
_SHOW_HEADER_SH = _DASHBOARD_DIR / "show-header.sh"
_PKG_DIR = _TEAM_DIR / "pgai_agent_kanban"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _touch_global_halt(kanban_root: pathlib.Path) -> None:
    (kanban_root / "HALT").write_text("", encoding="utf-8")


def _write_project_halt(kanban_root: pathlib.Path, project: str) -> None:
    proj_root = kanban_root / "projects" / project
    proj_root.mkdir(parents=True, exist_ok=True)
    (proj_root / "HALT").write_text("", encoding="utf-8")


def _write_project_halt_after(kanban_root: pathlib.Path, project: str, token: str) -> None:
    proj_root = kanban_root / "projects" / project
    proj_root.mkdir(parents=True, exist_ok=True)
    (proj_root / "HALT-AFTER").write_text(f"{token}\n", encoding="utf-8")


def _enable_defer_posture(kanban_root: pathlib.Path, project: str, *, level: str = "low") -> None:
    """Enable deferral at the project.cfg tier -- consulted when a
    renderer resolves THAT project's own posture (an explicit-project
    drill call, e.g. status-bottom.sh's per-project bar)."""
    cfg = kanban_root / "projects" / project / "project.cfg"
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write(f"\n[release]\ndeferred_bugs = true\ndeferred_bug_level = {level}\n")


def _enable_defer_posture_global(kanban_root: pathlib.Path, *, level: str = "low") -> None:
    """Enable deferral at the kanban.cfg tier -- every project inherits it,
    so a global bar (status-right.sh, status-bottom.sh without a project)
    renders it once, as the GLOBAL deferral scope."""
    cfg = kanban_root / "kanban.cfg"
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write(f"\n[release]\ndeferred_bugs = true\ndeferred_bug_level = {level}\n")


def _add_open_bug(
    kanban_root: pathlib.Path, project: str, bug_id: str, *, severity: str = "low"
) -> None:
    bugs_dir = kanban_root / "projects" / project / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    (bugs_dir / f"{bug_id}-fixture.md").write_text(
        f"# {bug_id}: fixture\n\n**Severity:** {severity}\n\n## Status\nopen\n",
        encoding="utf-8",
    )


def _env(kanban_root: pathlib.Path, *, term: str) -> dict:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    # The sandbox root ships no pgai_agent_kanban/, so the renderers resolve
    # their Python helpers through the dev-tree probe: name this test's own
    # tree, never an inherited checkout.
    env["PGAI_DEV_TREE_PATH"] = str(_TEAM_DIR.parent)
    env["TERM"] = term
    if term == "dumb":
        env["NO_COLOR"] = "1"
    else:
        env.pop("NO_COLOR", None)
    return env


def _run(script: pathlib.Path, args: list, kanban_root: pathlib.Path, *, term: str) -> str:
    result = subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=_env(kanban_root, term=term),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{script.name} exited {result.returncode}; stderr: {result.stderr!r}"
    )
    return result.stdout


def _run_status_bottom(kanban_root: pathlib.Path, project: str, *, term: str) -> str:
    return _run(_STATUS_BOTTOM_SH, [str(kanban_root), project], kanban_root, term=term)


def _run_status_bottom_global(kanban_root: pathlib.Path, *, term: str) -> str:
    return _run(_STATUS_BOTTOM_SH, [str(kanban_root)], kanban_root, term=term)


# ---------------------------------------------------------------------------
# AC1 — global HALT + project HALT-AFTER + non-zero deferral posture, all
# rendered simultaneously, each naming its scope.
# ---------------------------------------------------------------------------


class TestSimultaneousFlagsAllRenderTogether:
    def test_status_bottom_renders_all_three_flags_together(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _touch_global_halt(root)
        _write_project_halt_after(root, "project_a", "rc:v2.9.15")
        _enable_defer_posture(root, "project_a", level="low")
        _add_open_bug(root, "project_a", "BUG-0001", severity="low")

        stdout = _run_status_bottom(root, "project_a", term="dumb")

        # NO_COLOR bracket form joins scope with a colon: "HALT-AFTER:<scope> ...".
        assert "HALT GLOBAL" in stdout, stdout
        assert "HALT-AFTER:project_a until v2.9.15" in stdout, stdout
        assert "BUGS DEFERRED 1 (<=low)" in stdout, stdout

    def test_status_right_renders_all_three_flags_together(
        self, two_project_root: pathlib.Path
    ) -> None:
        """status-right.sh must agree with the bottom bar -- same three
        flags, none suppressed by the others."""
        root = two_project_root
        _touch_global_halt(root)
        _write_project_halt_after(root, "project_a", "rc:v2.9.15")
        # status-right.sh is a global bar: a kanban.cfg-tier deferral (every
        # project inherits it) renders once, as the GLOBAL scope. See
        # _enable_defer_posture_global's docstring.
        _enable_defer_posture_global(root, level="low")
        _add_open_bug(root, "project_a", "BUG-0001", severity="low")

        stdout = _run(_STATUS_RIGHT_SH, [str(root)], root, term="dumb")

        assert "HALT GLOBAL" in stdout, stdout
        assert "HALT-AFTER:project_a until v2.9.15" in stdout, stdout
        assert "DEFER GLOBAL <=low (1 waiting)" in stdout, stdout

    def test_show_header_agrees_with_bottom_bar_on_simultaneous_flags(
        self, two_project_root: pathlib.Path
    ) -> None:
        """show-header.sh (the drilled per-project header pane) must not
        contradict the bottom bar: a global HALT and this project's own
        HALT-AFTER both render, neither suppressing the other."""
        root = two_project_root
        _touch_global_halt(root)
        _write_project_halt_after(root, "project_a", "rc:v2.9.15")

        stdout = _run(
            _SHOW_HEADER_SH,
            ["--kanban-root", str(root), "--project", "project_a"],
            root,
            term="dumb",
        )

        assert "HALT GLOBAL" in stdout, stdout
        assert "HALT-AFTER project_a" in stdout, stdout
        assert "until v2.9.15" in stdout, stdout


# ---------------------------------------------------------------------------
# AC2 — two different projects halted; both named, neither suppressed.
# ---------------------------------------------------------------------------


class TestTwoHaltedProjectsBothNamed:
    def test_status_bottom_names_both_halted_projects(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _write_project_halt(root, "project_a")
        _write_project_halt(root, "project_b")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "HALT project_a" in stdout, stdout
        assert "HALT project_b" in stdout, stdout

    def test_status_right_names_both_halted_projects(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _write_project_halt(root, "project_a")
        _write_project_halt(root, "project_b")

        stdout = _run(_STATUS_RIGHT_SH, [str(root)], root, term="dumb")

        assert "HALT project_a" in stdout, stdout
        assert "HALT project_b" in stdout, stdout

    def test_one_halted_one_draining_project_both_named(
        self, two_project_root: pathlib.Path
    ) -> None:
        """Mixed states across two projects -- a HALT and a HALT-AFTER on
        two different projects -- both render, distinctly."""
        root = two_project_root
        _write_project_halt(root, "project_a")
        _write_project_halt_after(root, "project_b", "coder")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "HALT project_a" in stdout, stdout
        assert "HALT-AFTER:project_b coder" in stdout, stdout


# ---------------------------------------------------------------------------
# AC3 — rendered scope words match the SOP vocabulary exactly.
# ---------------------------------------------------------------------------


class TestScopeWordsMatchSopVocabularyExactly:
    def test_global_scope_renders_as_literal_global(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _touch_global_halt(root)

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "[HALT GLOBAL]" in stdout, stdout

    def test_project_scope_renders_as_literal_project_name_never_generic_word(
        self, two_project_root: pathlib.Path
    ) -> None:
        """The old first-wins contract collapsed any per-project scope to
        the generic word "PROJECT". The SOP vocabulary requires the real
        project name -- and forbids the literal word "PROJECT" appearing
        as a stand-in scope token."""
        root = two_project_root
        _write_project_halt(root, "project_a")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "[HALT project_a]" in stdout, stdout
        # The generic placeholder must never appear as a scope word.
        assert "HALT PROJECT]" not in stdout, stdout
        assert "HALT PROJECT " not in stdout, stdout


# ---------------------------------------------------------------------------
# AC4 — version-pinned HALT-AFTER renders "until vX.Y.Z"; composed in
# exactly one place in the tree.
# ---------------------------------------------------------------------------


class TestVersionPinnedHaltAfterDescriber:
    def test_status_bottom_renders_until_phrase_for_versioned_token(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _write_project_halt_after(root, "project_a", "rc:v3.1.4")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "until v3.1.4" in stdout, stdout
        # The raw token itself must not leak into the rendered text.
        assert "rc:v3.1.4" not in stdout, stdout

    def test_bare_non_versioned_token_renders_unchanged(
        self, two_project_root: pathlib.Path
    ) -> None:
        """A non-versioned token (e.g. the bare role token "coder") is not
        a release boundary and renders as itself -- no "until " phrase."""
        root = two_project_root
        _write_project_halt_after(root, "project_a", "coder")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert "HALT-AFTER:project_a coder" in stdout, stdout
        assert "until" not in stdout, stdout

    def test_until_phrase_is_composed_in_exactly_one_place_in_the_tree(self) -> None:
        """The literal f-string composition of the "until <version>" phrase
        must exist in exactly one place: describe_token() in
        pgai_agent_kanban/halt_after/token.py. Every dashboard renderer
        (status-bottom.sh, status-right.sh, show-header.sh) calls the one
        shared wrapper (dashboard_halt_describe_event in
        lib/halt_scope.sh) instead of locally formatting the phrase --
        this test greps production Python sources for the composition
        itself (not mere mentions of the word "until" in comments/docs) to
        lock that invariant in.
        """
        # Matches only an f-string COMPOSITION of the phrase (f"until {...}"
        # or f'until {...}') -- not a plain quoted example value such as a
        # docstring's ">>> describe_token(...) \n 'until v2.9.15'" return
        # value, which names the phrase without composing it.
        composition_re = re.compile(r'''f["']until\s*\{''')
        hits = []
        for py_file in sorted(_PKG_DIR.rglob("*.py")):
            rel = py_file.relative_to(_TEAM_DIR).as_posix()
            if "/tests/" in rel or rel.startswith("tests/"):
                continue
            for lineno, line in enumerate(
                py_file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if composition_re.search(line):
                    hits.append(f"{rel}:{lineno}: {stripped}")

        assert hits == [
            f"pgai_agent_kanban/halt_after/token.py:131: return f\"until {{token[3:]}}\""
        ], (
            "expected the \"until \" phrase to be composed in exactly one "
            f"place (halt_after/token.py's describe_token); found: {hits}"
        )

    def test_no_bash_renderer_locally_composes_the_until_phrase(self) -> None:
        """No dashboard .sh file may hand-format its own "until <var>"
        string -- every renderer must delegate to
        dashboard_halt_describe_event instead. Guards against a future
        regression re-introducing a second phrasing site in bash.
        """
        composition_re = re.compile(r'["\']until\s+\$')
        hits = []
        for sh_file in sorted(_DASHBOARD_DIR.rglob("*.sh")):
            for lineno, line in enumerate(
                sh_file.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if composition_re.search(line):
                    hits.append(f"{sh_file.relative_to(_TEAM_DIR).as_posix()}:{lineno}: {stripped}")

        assert hits == [], f"found local 'until $...' composition outside the shared describer: {hits}"


# ---------------------------------------------------------------------------
# AC5 — NO_COLOR form carries the same set of segments as the rich form.
# ---------------------------------------------------------------------------


def _flag_substrings_present(stdout: str) -> set:
    """Return the set of flag markers present, independent of color markup.

    Strips tmux #[fg=...]/#[default] markup and ANSI escapes so a rich-mode
    and a NO_COLOR-mode render can be compared on the same normalized text.
    """
    stripped = re.sub(r"#\[[^\]]*\]", "", stdout)
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", stripped)
    # NO_COLOR mode joins the scope word with a colon ("HALT-AFTER:<scope>");
    # rich mode uses a plain space ("HALT-AFTER <scope>"). Normalize the
    # colon form to the space form so both modes compare on identical text.
    stripped = stripped.replace("HALT-AFTER:", "HALT-AFTER ")
    markers = set()
    for pattern in (
        r"HALT GLOBAL",
        r"HALT project_a",
        r"HALT project_b",
        r"HALT-AFTER project_a[^\]|]*",
        r"HALT-AFTER GLOBAL[^\]|]*",
        r"BUGS DEFERRED \d+ \(<=\w+\)",
        r"until v[\d.]+",
    ):
        for m in re.finditer(pattern, stripped):
            markers.add(m.group(0).strip())
    return markers


class TestNoColorFormMatchesRichFormSegmentSet:
    @pytest.mark.parametrize(
        "setup",
        [
            "global_halt_only",
            "global_halt_and_project_draining",
            "two_projects_halted",
            "all_three_simultaneous",
        ],
    )
    def test_segment_set_identical_across_color_modes(
        self, two_project_root: pathlib.Path, setup: str
    ) -> None:
        root = two_project_root

        if setup == "global_halt_only":
            _touch_global_halt(root)
        elif setup == "global_halt_and_project_draining":
            _touch_global_halt(root)
            _write_project_halt_after(root, "project_a", "rc:v1.2.3")
        elif setup == "two_projects_halted":
            _write_project_halt(root, "project_a")
            _write_project_halt(root, "project_b")
        elif setup == "all_three_simultaneous":
            _touch_global_halt(root)
            _write_project_halt_after(root, "project_a", "rc:v2.9.15")
            _enable_defer_posture(root, "project_a", level="low")
            _add_open_bug(root, "project_a", "BUG-0001", severity="low")

        project = "project_a" if setup != "two_projects_halted" else None
        if project:
            dumb_out = _run_status_bottom(root, project, term="dumb")
            rich_out = _run_status_bottom(root, project, term="xterm")
        else:
            dumb_out = _run_status_bottom_global(root, term="dumb")
            rich_out = _run_status_bottom_global(root, term="xterm")

        dumb_markers = _flag_substrings_present(dumb_out)
        rich_markers = _flag_substrings_present(rich_out)

        assert dumb_markers, f"expected at least one flag marker; dumb output: {dumb_out!r}"
        assert dumb_markers == rich_markers, (
            f"NO_COLOR and rich renders disagree on active segments for {setup!r}.\n"
            f"NO_COLOR only: {dumb_markers - rich_markers}\n"
            f"rich only: {rich_markers - dumb_markers}\n"
            f"dumb stdout: {dumb_out!r}\nrich stdout: {rich_out!r}"
        )


# ---------------------------------------------------------------------------
# AC6 — segment order is identical across repeated renders of the same
# state.
# ---------------------------------------------------------------------------


class TestSegmentOrderDeterministic:
    def test_repeated_renders_of_the_same_state_preserve_segment_order(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _touch_global_halt(root)
        _write_project_halt_after(root, "project_a", "rc:v2.9.15")
        _enable_defer_posture(root, "project_a", level="low")
        _add_open_bug(root, "project_a", "BUG-0001", severity="low")

        first = _run_status_bottom(root, "project_a", term="dumb")
        second = _run_status_bottom(root, "project_a", term="dumb")

        # Compare every segment except the trailing timestamp (which
        # legitimately ticks between the two subprocess invocations).
        first_segments = first.split(" | ")[:-1]
        second_segments = second.split(" | ")[:-1]

        assert first_segments == second_segments, (
            f"segment order changed between repeated renders of the same "
            f"state.\nfirst:  {first_segments}\nsecond: {second_segments}"
        )

    def test_global_scope_always_precedes_project_scopes(
        self, two_project_root: pathlib.Path
    ) -> None:
        """GLOBAL is always emitted before any per-project scope -- part of
        the documented deterministic emission order."""
        root = two_project_root
        _touch_global_halt(root)
        _write_project_halt(root, "project_a")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert stdout.index("HALT GLOBAL") < stdout.index("HALT project_a"), stdout

    def test_project_scopes_emitted_in_alphabetical_order(
        self, two_project_root: pathlib.Path
    ) -> None:
        root = two_project_root
        _write_project_halt(root, "project_b")
        _write_project_halt(root, "project_a")

        stdout = _run_status_bottom_global(root, term="dumb")

        assert stdout.index("HALT project_a") < stdout.index("HALT project_b"), stdout
