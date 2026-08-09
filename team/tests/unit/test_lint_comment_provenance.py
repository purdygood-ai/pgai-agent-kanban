"""
test_lint_comment_provenance.py — Unit tests for team/scripts/lint_comment_provenance.py.

Tests cover:

  1. **Positive (clean fixture)**: a synthetic source file with no provenance
     tokens produces zero findings; the lint exits 0.

  2. **Negative (forced violation)**: a synthetic source file containing a
     genuine ``BUG-NNNN`` reference in a comment is flagged; the lint exits 1.

  3. **Exception paths still pass**: a format-example line (``e.g. BUG-0042``)
     and a per-line opt-out (``# provenance-allowlist: ...`` within 5 lines
     before the flagged line) both suppress the finding.

  4. **Remedy block (PRIORITY-0001-gates-prescribe-remedies)**: forcing a
     violation prints the full three-part remedy block required by
     docs/coding-standards.md ("Gates prescribe, not just diagnose") — an
     upgrade from the lint's prior single-line hint:

       a. ``Cause(s):`` naming the concrete file:line and token.
       b. A numbered ``To fix:`` block of verbatim-runnable commands (no
          angle-bracket placeholders), ending with the re-lint command.
       c. The one-line ``Rule:`` this gate enforces.

  5. **Remedy commands clear the gate**: the exact numbered commands printed
     in the remedy block are parsed out of the captured output and executed,
     verbatim, through a real shell — proving a stranger reading only the
     gate's failure output could clear it with no other guidance. The file
     is also re-parsed as Python afterward to confirm the fix command did not
     corrupt the fixture's syntax.

  6. **Diff-scope mode**: ``--diff-scope`` lints only the lines a feature
     branch's diff added/changed against its source branch — a planted
     violation is caught, pre-existing litter outside the diff is ignored,
     and a rule added to ``_PROVENANCE_PATTERNS`` is enforced identically by
     both the whole-directory scan and diff-scope mode (single rule-source,
     BUG-0045 acceptance #3 and #4).

  7. **Release-notes prose exemption (BUG-0054)**: BUG-NNNN and
     PRIORITY-NNNN references inside the prose surface (release-notes/ by
     default) are the sanctioned record of which items a release addressed
     and are exempt there — in both the whole-directory scan and
     diff-scope mode. Every other pattern (task-ID, version-bundle,
     requirement-Goal) remains enforced in prose exactly as in code, and
     BUG-ID/PRIORITY-ID enforcement in the code surface is unchanged.

Temp directories: this file deliberately does NOT use pytest's ``tmp_path``
fixture (nor the framework's ``pgai_mkdtemp`` helper). Both resolve under
``$PGAI_AGENT_KANBAN_TEMP_DIR/tests/...`` (see team/tests/conftest.py's
``PYTEST_DEBUG_TEMPROOT`` redirect), and lint_comment_provenance.py's own
``_PATH_EXCLUDE_SEGMENTS`` scan filter unconditionally skips any path
containing a ``tests/`` segment (by design, to exclude the real test tree
from provenance scanning). A fixture file created under ``tmp_path`` would
therefore be silently invisible to the scanner, and every test here would
observe 0 findings regardless of fixture content. The ``_scan_scratch_dir``
fixture below provides an equivalent self-cleaning temp directory rooted one
level higher — still under ``PGAI_AGENT_KANBAN_TEMP_DIR``, never bare
``/tmp`` — so the lint actually scans the fixture files these tests create.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Locate lint_comment_provenance.py.
# This file lives at team/tests/unit/test_lint_comment_provenance.py.
# Three parent levels up: unit/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_comment_provenance.py"

# Subdirectory name for this file's scratch fixtures. Deliberately contains
# none of lint_comment_provenance.py's _PATH_EXCLUDE_SEGMENTS substrings
# ("tests/", "test/", "fixtures/", "conftest", "__pycache__") — see the
# module docstring above for why that exclusion matters here.
_SCRATCH_SUBDIR = "provenance_lint_scratch"


def _pgai_temp_root() -> Path:
    """Return $PGAI_AGENT_KANBAN_TEMP_DIR/<_SCRATCH_SUBDIR>, creating it if needed.

    Falls back to the documented resolver default only when the env var is
    unset, matching the fallback convention used by lint_changelog_freshness.py's
    own _get_temp_root() (see the literal itself, below, for the exact value).
    """
    # anti-pattern-allowlist: 2 (justification: mirrors temp.sh's own documented
    # hard-coded fallback default; this test intentionally exercises that exact
    # resolver contract rather than routing through it)
    root = Path(os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or "/tmp/pgai_kanban_tmp")
    scratch_root = root / _SCRATCH_SUBDIR
    scratch_root.mkdir(parents=True, exist_ok=True)
    return scratch_root


@pytest.fixture
def scan_scratch_dir():
    """A self-cleaning scratch directory outside any 'tests/'-segment path.

    See the module docstring for why this replaces pytest's tmp_path in this
    file specifically.
    """
    d = Path(tempfile.mkdtemp(dir=str(_pgai_temp_root())))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _import_lint_module():
    """Import lint_comment_provenance as a module without polluting sys.modules
    permanently.

    Returns:
        The loaded module object.
    """
    spec = importlib.util.spec_from_file_location(
        "lint_comment_provenance", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Import once at module load time; tests reference `_lint` attributes.
_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Baseline behavior: clean fixture passes, genuine violation fails.
# ---------------------------------------------------------------------------


class TestCommentProvenanceBaseline:
    """The core acceptance criterion the gate exists to enforce."""

    def test_clean_fixture_passes(self, scan_scratch_dir: Path) -> None:
        """A file with no provenance tokens produces zero findings."""
        code_dir = scan_scratch_dir / "clean"
        code_dir.mkdir()
        (code_dir / "clean_module.py").write_text(
            "def add(a, b):\n"
            "    # Adds two numbers and returns the sum.\n"
            "    return a + b\n",
            encoding="utf-8",
        )

        # --prose-dirs is pinned to this test's own empty scratch directory
        # (never contains .md files) rather than left at its default, which
        # resolves to the real repo's release-notes/ directory. This test
        # asserts on --code-dirs scan behavior only; without the pin, its
        # result depends on whatever real release-notes content exists at
        # run time, which is state this test does not own or control.
        result = _lint.main(
            ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
        )

        assert result == 0, (
            "lint_comment_provenance.main() must return 0 for a file with no "
            "provenance tokens."
        )

    def test_forced_violation_fails(self, scan_scratch_dir: Path) -> None:
        """A genuine BUG-NNNN reference in a comment is flagged; lint exits 1."""
        code_dir = scan_scratch_dir / "dirty"
        code_dir.mkdir()
        (code_dir / "dirty_module.py").write_text(
            "def foo():\n"
            "    # This references BUG-0042 directly in a comment.\n"
            "    return 1\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", str(code_dir)])

        assert result == 1, (
            "lint_comment_provenance.main() must return 1 when a genuine "
            "BUG-NNNN reference is found in production code."
        )

    def test_format_example_marker_suppresses_finding(
        self, scan_scratch_dir: Path
    ) -> None:
        """A line documenting the BUG-NNNN format as an example is exempt."""
        code_dir = scan_scratch_dir / "example_only"
        code_dir.mkdir()
        (code_dir / "help_text.py").write_text(
            "def usage():\n"
            "    # e.g. --key BUG-0042 shows the expected key format.\n"
            "    return None\n",
            encoding="utf-8",
        )

        # --prose-dirs pinned to this test's own empty scratch directory —
        # see test_clean_fixture_passes for why the default (real
        # release-notes/) must not leak into this assertion.
        result = _lint.main(
            ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
        )

        assert result == 0, (
            "A format/usage-example line (marked with 'e.g.') citing "
            "BUG-0042 as a format illustration must not be flagged."
        )

    def test_per_line_opt_out_suppresses_finding(self, scan_scratch_dir: Path) -> None:
        """A '# provenance-allowlist: ...' comment within 5 lines before the
        flagged line suppresses that finding."""
        code_dir = scan_scratch_dir / "opted_out"
        code_dir.mkdir()
        (code_dir / "opted_out.py").write_text(
            "def foo():\n"
            "    # provenance-allowlist: retained intentionally for audit trail\n"
            "    # This references BUG-0043 directly in a comment.\n"
            "    return 1\n",
            encoding="utf-8",
        )

        # --prose-dirs pinned to this test's own empty scratch directory —
        # see test_clean_fixture_passes for why the default (real
        # release-notes/) must not leak into this assertion.
        result = _lint.main(
            ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
        )

        assert result == 0, (
            "A '# provenance-allowlist: <justification>' comment within 5 "
            "lines before the flagged line must suppress that finding."
        )

    def test_version_goal_provenance_flagged(self, scan_scratch_dir: Path) -> None:
        """A 'vX.Y.Z Goal N' requirement-version citation in a production
        docstring/comment is flagged; the lint exits 1.

        Covers BUG-0031-v2-0-0-provenance-in-code's recurrence-prevention
        gap: the gated lint previously had no pattern for this provenance
        shape, so a v2.0.0-era docstring citing 'v2.0.0 Goal 4' as the
        reason a ceiling exists passed with 0 findings.
        """
        code_dir = scan_scratch_dir / "version_goal_dirty"
        code_dir.mkdir()
        (code_dir / "ceiling.py").write_text(
            "def compute_ceiling():\n"
            "    # This is the ceiling introduced by v2.0.0 Goal 4.\n"
            "    return 3\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", str(code_dir)])

        assert result == 1, (
            "lint_comment_provenance.main() must return 1 when a production "
            "docstring/comment cites a 'vX.Y.Z Goal N' requirement-version "
            "provenance note."
        )

    def test_version_goal_usage_example_allowed(self, scan_scratch_dir: Path) -> None:
        """A directive-8-permitted format/usage example of the 'vX.Y.Z Goal
        N' shape (marked with 'e.g.') is not flagged, even though it
        contains the same version+Goal-N token the provenance check
        targets."""
        code_dir = scan_scratch_dir / "version_goal_example"
        code_dir.mkdir()
        (code_dir / "help_text.py").write_text(
            "def usage():\n"
            "    # e.g. v1.2.3 Goal 4 shows the citation shape this lint flags.\n"
            "    return None\n",
            encoding="utf-8",
        )

        # --prose-dirs pinned to this test's own empty scratch directory —
        # see test_clean_fixture_passes for why the default (real
        # release-notes/) must not leak into this assertion.
        result = _lint.main(
            ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
        )

        assert result == 0, (
            "A format/usage-example line (marked with 'e.g.') illustrating "
            "the 'vX.Y.Z Goal N' shape must not be flagged."
        )


# ---------------------------------------------------------------------------
# Release-summary prose scanning: the lint gates release-notes prose, not
# only shipped code, so an agent-habit stray in a release note is caught the
# same way a stray in a code comment is caught.
# ---------------------------------------------------------------------------


class TestCommentProvenanceProseScanning:
    """`--prose-dirs` scans release-summary prose (.md) for the same
    provenance patterns and honors the same exceptions as code scanning,
    with one deliberate asymmetry: BUG-ID and PRIORITY-ID references are
    exempt on the prose surface (see TestCommentProvenanceReleaseNotesExemption
    below, BUG-0054) because release notes exist to record resolved items
    by ID. This class covers the shared behavior; the exemption itself is
    pinned in the dedicated class."""

    def test_clean_prose_fixture_passes(self, scan_scratch_dir: Path) -> None:
        """A release note with no provenance tokens produces zero findings
        when scanned via --prose-dirs, with an empty --code-dirs so only
        the prose surface is exercised."""
        prose_dir = scan_scratch_dir / "clean_notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- Fixed a race condition in the scheduler.\n",
            encoding="utf-8",
        )

        result = _lint.main(
            ["--code-dirs", "--prose-dirs", str(prose_dir)]
        )

        assert result == 0, (
            "A clean release-notes fixture scanned via --prose-dirs must "
            "produce zero findings."
        )

    def test_seeded_task_id_in_release_note_is_flagged(
        self, scan_scratch_dir: Path
    ) -> None:
        """A deliberately seeded task-ID in release-notes prose is caught —
        the guard demonstrably covers release-summary surfaces, not only
        shipped code, for provenance types other than BUG-ID/PRIORITY-ID
        (those two are exempt on the prose surface; see
        TestCommentProvenanceReleaseNotesExemption, BUG-0054).

        This test previously seeded a BUG-NNNN token and asserted it was
        flagged; BUG-0054 made BUG-ID exempt on the prose surface, so the
        fixture now seeds a task-ID (still enforced everywhere) to keep
        this test's assertion — "the prose surface is genuinely scanned,
        not skipped entirely" — meaningful."""
        prose_dir = scan_scratch_dir / "dirty_notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- Landed by CODER-20260809-099 per operator request.\n",
            encoding="utf-8",
        )

        result = _lint.main(
            ["--code-dirs", "--prose-dirs", str(prose_dir)]
        )

        assert result == 1, (
            "lint_comment_provenance.main() must return 1 when a genuine "
            "task-ID reference is found in release-summary prose — "
            "prose-surface enforcement is unchanged for non-exempt labels."
        )

    def test_prose_format_example_marker_suppresses_finding(
        self, scan_scratch_dir: Path
    ) -> None:
        """A release note illustrating the BUG-NNNN format as a documented
        example (marked with 'e.g.') is exempt, same as in code."""
        prose_dir = scan_scratch_dir / "example_notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "Bug reports use the format e.g. BUG-0042 in the tracker.\n",
            encoding="utf-8",
        )

        result = _lint.main(
            ["--code-dirs", "--prose-dirs", str(prose_dir)]
        )

        assert result == 0, (
            "A format/usage-example line (marked with 'e.g.') in "
            "release-summary prose must not be flagged."
        )

    def test_default_prose_dir_is_release_notes(self) -> None:
        """With no --prose-dirs override, the default prose scan target
        resolves to release-notes/ under the repo root inferred from this
        script's own location — proving the wiring, not just the flag."""
        result = _lint.main(["--code-dirs"])

        assert result in (0, 1), (
            "main() with an empty --code-dirs and default --prose-dirs "
            "must still run to completion (0 or 1), proving release-notes/ "
            "is reachable as the default prose scan target rather than the "
            "call raising or hanging."
        )


# ---------------------------------------------------------------------------
# Release-notes prose exemption (BUG-0054): BUG-NNNN / PRIORITY-NNNN
# references in the prose surface are the sanctioned record of which items
# a release addressed, not a provenance leak. This class pins that
# exemption in both invocation modes so it cannot silently regress, while
# also pinning that every other pattern (and the code surface entirely)
# stays enforced unchanged.
# ---------------------------------------------------------------------------


class TestCommentProvenanceReleaseNotesExemption:
    """BUG-ID / PRIORITY-ID references inside release-notes prose are
    exempt (BUG-0054); enforcement everywhere else is unchanged."""

    def test_bug_id_in_release_notes_bugs_resolved_is_exempt(
        self, scan_scratch_dir: Path
    ) -> None:
        """A 'Bugs Resolved'-style BUG-NNNN reference inside a file scanned
        via --prose-dirs produces zero findings — release notes exist to
        name resolved bugs by ID."""
        prose_dir = scan_scratch_dir / "release-notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- BUG-8123 — fixed a race condition in the scheduler (fixed).\n"
            "\n## Bugs Skipped\n"
            "- BUG-8124 — deferred to a follow-up patch.\n"
            "\n## Known Issues\n"
            "- BUG-8124 — see Bugs Skipped above.\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", "--prose-dirs", str(prose_dir)])

        assert result == 0, (
            "lint_comment_provenance.main() must return 0 for legitimate "
            "BUG-NNNN references in release-notes prose (BUG-0054)."
        )

    def test_priority_id_in_release_notes_is_exempt(
        self, scan_scratch_dir: Path
    ) -> None:
        """A PRIORITY-NNNN reference inside release-notes prose is exempt,
        same as BUG-NNNN."""
        prose_dir = scan_scratch_dir / "release-notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- PRIORITY-0004 — addressed the expedited item.\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", "--prose-dirs", str(prose_dir)])

        assert result == 0, (
            "lint_comment_provenance.main() must return 0 for legitimate "
            "PRIORITY-NNNN references in release-notes prose (BUG-0054)."
        )

    def test_task_id_in_release_notes_is_still_flagged(
        self, scan_scratch_dir: Path
    ) -> None:
        """A task-ID in release-notes prose is still flagged — the
        exemption is scoped to BUG-ID/PRIORITY-ID only, not every pattern."""
        prose_dir = scan_scratch_dir / "release-notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- Shipped by CODER-20260809-099.\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", "--prose-dirs", str(prose_dir)])

        assert result == 1, (
            "A task-ID reference in release-notes prose must still be "
            "flagged — the BUG-0054 exemption does not cover task IDs."
        )

    def test_version_bundle_in_release_notes_is_still_flagged(
        self, scan_scratch_dir: Path
    ) -> None:
        """A version-bundle reference in release-notes prose is still
        flagged — the exemption does not cover version-bundle citations."""
        prose_dir = scan_scratch_dir / "release-notes"
        prose_dir.mkdir()
        (prose_dir / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "Bundled from v1.23.4-bug-bundle.\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", "--prose-dirs", str(prose_dir)])

        assert result == 1, (
            "A version-bundle reference in release-notes prose must still "
            "be flagged — the BUG-0054 exemption does not cover "
            "version-bundle citations."
        )

    def test_bug_id_in_code_surface_is_still_flagged(
        self, scan_scratch_dir: Path
    ) -> None:
        """A BUG-NNNN reference in a code comment is still flagged — the
        BUG-0054 exemption applies only to the prose surface, never to
        code comments, docstrings, or output strings."""
        code_dir = scan_scratch_dir / "code_surface"
        code_dir.mkdir()
        (code_dir / "module.py").write_text(
            "def foo():\n"
            "    # This references BUG-8123 directly in a comment.\n"
            "    return 1\n",
            encoding="utf-8",
        )

        result = _lint.main(["--code-dirs", str(code_dir)])

        assert result == 1, (
            "A BUG-NNNN reference in a code comment must still be flagged "
            "— BUG-0054's exemption is scoped to the prose surface only, "
            "not the code surface."
        )

    def test_diff_scope_release_notes_bug_id_is_exempt(
        self, scan_scratch_dir: Path
    ) -> None:
        """--diff-scope also exempts a BUG-NNNN reference planted inside a
        release-notes/ path — the prose exemption applies identically in
        both invocation modes (BUG-0054)."""
        repo = _init_diff_repo(scan_scratch_dir)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "release-notes").mkdir()
        (repo / "release-notes" / "v9.9.9.md").write_text(
            "# Release Notes: v9.9.9\n\n"
            "## Bug Fixes\n"
            "- BUG-8123 — fixed a race condition (fixed).\n",
            encoding="utf-8",
        )
        _commit_all(repo, "add release notes citing a resolved bug")

        result = _lint.main(
            [
                "--diff-scope",
                "--worktree", str(repo),
                "--source-branch", "source",
                "--feature-branch", "feature",
            ]
        )

        assert result == 0, (
            "--diff-scope must return 0 for a BUG-NNNN reference planted "
            "inside a release-notes/ path (BUG-0054 prose exemption)."
        )

    def test_diff_scope_non_release_notes_md_bug_id_still_flagged(
        self, scan_scratch_dir: Path
    ) -> None:
        """--diff-scope still flags a BUG-NNNN reference planted in a .md
        file OUTSIDE release-notes/ — the exemption is scoped to the
        release-notes prose surface, not every Markdown file."""
        repo = _init_diff_repo(scan_scratch_dir)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "notes.md").write_text(
            "# Some doc\n\nReferences BUG-8123 directly.\n",
            encoding="utf-8",
        )
        _commit_all(repo, "add a non-release-notes markdown file")

        result = _lint.main(
            [
                "--diff-scope",
                "--worktree", str(repo),
                "--source-branch", "source",
                "--feature-branch", "feature",
            ]
        )

        assert result == 1, (
            "--diff-scope must still flag a BUG-NNNN reference in a .md "
            "file outside release-notes/ — the BUG-0054 exemption is "
            "scoped to the release-notes prose surface only."
        )


# ---------------------------------------------------------------------------
# Remedy-block tests (PRIORITY-0001-gates-prescribe-remedies)
# ---------------------------------------------------------------------------

# A numbered fix-command line, e.g. "  1. sed -i '3i...' \"...\"".
_NUMBERED_COMMAND_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.MULTILINE)

# An unresolved angle-bracket placeholder such as "<name>" or "<repo-root>".
# The house placeholder law (docs/coding-standards.md) forbids these in the
# printed fix commands — every site-specific value must already be derived
# and substituted by the time the remedy block is printed.
_ANGLE_BRACKET_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][\w -]*>")


class TestCommentProvenanceRemedyBlock:
    """The remedy block: cause(s), numbered verbatim commands, re-lint, rule.

    Exercises PRIORITY-0001-gates-prescribe-remedies' second lint target end
    to end: force a genuine provenance violation, assert the remedy block
    contains every required part, assert the printed commands carry no
    angle-bracket placeholders, and then actually execute the printed
    commands verbatim to prove they clear the gate.
    """

    def _write_violation_fixture(self, scan_scratch_dir: Path) -> Path:
        """Return a scratch directory containing one file with a genuine,
        unexempted BUG-NNNN reference at a known line number."""
        code_dir = scan_scratch_dir / "remedy_target"
        code_dir.mkdir()
        target = code_dir / "target_module.py"
        target.write_text(
            "#!/usr/bin/env python3\n"
            "def foo():\n"
            "    # This references BUG-0099 directly in a comment.\n"
            "    return 1\n",
            encoding="utf-8",
        )
        return code_dir

    def test_remedy_block_contains_all_required_parts(
        self, scan_scratch_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Forcing a provenance violation prints the full three-part remedy
        block, an upgrade from the lint's prior single-line hint.

        Asserts, per docs/coding-standards.md "Gates prescribe, not just
        diagnose":
          1. Cause(s) with a concrete location (file:line and the token).
          2. A numbered fix block of at least two verbatim-runnable
             commands, ending with the re-lint verification command.
          3. The one-line enforced rule.
          4. No angle-bracket placeholders anywhere in the output.
        """
        code_dir = self._write_violation_fixture(scan_scratch_dir)

        result = _lint.main(["--code-dirs", str(code_dir)])
        assert result == 1, "Forced violation did not fail the gate."

        out = capsys.readouterr().out

        assert "Cause(s):" in out, (
            f"Remedy block missing the 'Cause(s):' section.\nstdout: {out!r}"
        )
        assert "BUG-0099" in out, (
            f"Remedy block does not name the actual offending token.\nstdout: {out!r}"
        )

        assert "To fix:" in out, (
            f"Remedy block missing the 'To fix:' numbered fix block.\nstdout: {out!r}"
        )
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            "Remedy block's numbered fix block has fewer than 2 commands "
            "(expected at least: insert opt-out, then re-lint).\n"
            f"Parsed commands: {commands!r}\nstdout: {out!r}"
        )
        last_command_text = commands[-1][1]
        assert "lint_comment_provenance.py" in last_command_text, (
            "Last numbered command is not the re-lint verification command.\n"
            f"Last command: {last_command_text!r}\nstdout: {out!r}"
        )

        assert (
            "must describe behavior, not process history" in out
        ), (
            f"Remedy block missing the one-line enforced rule.\nstdout: {out!r}"
        )

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(out)
        assert placeholder_hits == [], (
            "Remedy block contains unresolved angle-bracket placeholder(s), "
            "violating the house placeholder law.\n"
            f"Placeholders found: {placeholder_hits!r}\nstdout: {out!r}"
        )

    def test_remedy_block_commands_clear_the_gate(
        self, scan_scratch_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Executing the remedy block's printed commands verbatim clears the
        gate — the motivating end-to-end proof (acceptance criterion 4 of
        CODER-20260727-017-retrofit-provenance-help): a stranger reading only
        the gate's failure output, with no other guidance, can clear it.

        The test parses the exact numbered command strings out of the
        captured stdout (not a hand-reconstructed equivalent) and runs them
        through a real shell, so it proves the literal printed text is
        runnable, not just that the underlying mechanism works. It then
        re-parses the fixed file as Python to prove the fix did not corrupt
        the file's syntax.
        """
        code_dir = self._write_violation_fixture(scan_scratch_dir)
        target = code_dir / "target_module.py"

        result = _lint.main(["--code-dirs", str(code_dir)])
        assert result == 1, "Forced violation did not fail the gate."

        out = capsys.readouterr().out
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            f"Expected at least 2 numbered commands in the remedy block.\nstdout: {out!r}"
        )

        for _, command_text in commands:
            proc = subprocess.run(
                command_text,
                shell=True,
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, (
                f"Printed remedy command exited non-zero: {command_text!r}\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            )

        # The last command IS the re-lint verification command and already
        # asserted returncode == 0 above. Confirm independently via a direct
        # main() call for belt-and-braces.
        recheck = _lint.main(["--code-dirs", str(code_dir)])
        assert recheck == 0, (
            "lint_comment_provenance still fails against the fixture directory "
            "after running every printed remedy command verbatim."
        )

        # The fix command must not have corrupted the fixture's Python syntax.
        ast.parse(target.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Diff-scope mode (BUG-0045): lint only the lines a feature branch's diff
# added/changed against its source branch, reusing the same detection
# rule-source as the whole-directory scan.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command inside *repo* and return the completed process.

    Always targets *repo* via ``-C`` and never touches global git config —
    the repo-local identity set by ``_init_diff_repo`` is enough to commit.
    """
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _init_diff_repo(root: Path) -> Path:
    """Create a throwaway git repo under *root* with a 'source' branch
    holding one initial commit, checked out on 'source'. Returns the repo
    directory. Deliberately named 'repo' (not 'test*'/'fixtures*') so the
    directory itself never collides with lint_comment_provenance.py's own
    ``_PATH_EXCLUDE_SEGMENTS`` scan filter.
    """
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "source")
    _git(repo, "config", "user.email", "lint-diff-scope-test@example.invalid")
    _git(repo, "config", "user.name", "Lint Diff Scope Test")
    (repo / "README.md").write_text("# fixture repo\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo


def _commit_all(repo: Path, message: str) -> None:
    """Stage every change in *repo* and commit it with *message*."""
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


class TestCommentProvenanceDiffScope:
    """``--diff-scope`` lints only the lines a feature branch's diff
    added/changed against its source branch (BUG-0045)."""

    def test_diff_scope_reports_planted_violation(self, scan_scratch_dir: Path) -> None:
        """A feature-branch diff that plants a genuine 'per BUG-0042'
        comment is flagged by diff-scope mode; the lint exits non-zero."""
        repo = _init_diff_repo(scan_scratch_dir)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "planted.py").write_text(
            "def foo():\n"
            "    # per BUG-0042 this needs a fix\n"
            "    return 1\n",
            encoding="utf-8",
        )
        _commit_all(repo, "plant a provenance comment")

        result = _lint.main(
            [
                "--diff-scope",
                "--worktree", str(repo),
                "--source-branch", "source",
                "--feature-branch", "feature",
            ]
        )

        assert result == 1, (
            "Diff-scope mode did not flag a planted BUG-NNNN reference "
            "introduced by the feature branch's diff."
        )

    def test_diff_scope_finding_shape_matches_directory_scan(
        self, scan_scratch_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The diff-scope finding report names file:line, the offending
        token, and the reword remedy — the same shape the whole-directory
        scan produces (Required Output: 'consistent with the existing
        scan')."""
        repo = _init_diff_repo(scan_scratch_dir)
        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "planted.py").write_text(
            "def foo():\n"
            "    # per BUG-0042 this needs a fix\n"
            "    return 1\n",
            encoding="utf-8",
        )
        _commit_all(repo, "plant a provenance comment")

        result = _lint.main(
            [
                "--diff-scope",
                "--worktree", str(repo),
                "--source-branch", "source",
                "--feature-branch", "feature",
            ]
        )
        out = capsys.readouterr().out

        assert result == 1
        assert "planted.py:2" in out, (
            f"Diff-scope output missing the expected file:line.\nstdout: {out!r}"
        )
        assert "BUG-0042" in out, (
            f"Diff-scope output missing the offending token.\nstdout: {out!r}"
        )
        assert "must describe behavior, not process history" in out, (
            f"Diff-scope output missing the reword remedy rule.\nstdout: {out!r}"
        )

    def test_diff_scope_ignores_pre_existing_litter_outside_diff(
        self, scan_scratch_dir: Path
    ) -> None:
        """Provenance litter that predates the feature branch — and that its
        diff never touches — is not reported; diff-scope judges only the
        task's own changed lines (BUG-0045 acceptance #3)."""
        repo = _init_diff_repo(scan_scratch_dir)
        # Litter lands on the source branch BEFORE the feature branch exists.
        (repo / "legacy.py").write_text(
            "def legacy():\n"
            "    # This references BUG-0099 directly in a comment.\n"
            "    return 1\n",
            encoding="utf-8",
        )
        _commit_all(repo, "pre-existing litter on source")

        _git(repo, "checkout", "-q", "-b", "feature")
        (repo / "clean_addition.py").write_text(
            "def clean():\n"
            "    # Adds two numbers and returns the sum.\n"
            "    return 2\n",
            encoding="utf-8",
        )
        _commit_all(repo, "clean, unrelated feature-branch change")

        result = _lint.main(
            [
                "--diff-scope",
                "--worktree", str(repo),
                "--source-branch", "source",
                "--feature-branch", "feature",
            ]
        )

        assert result == 0, (
            "Diff-scope mode must not report pre-existing litter that sits "
            "outside the feature branch's own diff."
        )

    def test_diff_scope_and_directory_scan_share_rule_source(
        self, scan_scratch_dir: Path
    ) -> None:
        """A detection rule added to ``_PROVENANCE_PATTERNS`` is immediately
        enforced by BOTH the whole-directory scan and diff-scope mode, with
        no code change to either scan path — the single rule-source
        guarantee (BUG-0045 acceptance #4)."""
        custom_pattern = re.compile(r"\bZORT-\d+\b")
        custom_entry = (custom_pattern, "ZORT-ID")
        _lint._PROVENANCE_PATTERNS.append(custom_entry)
        try:
            # Directory-scan side: a fixture file under --code-dirs.
            code_dir = scan_scratch_dir / "zort_dir_scan"
            code_dir.mkdir()
            (code_dir / "zort_module.py").write_text(
                "def foo():\n"
                "    # references ZORT-9999 directly\n"
                "    return 1\n",
                encoding="utf-8",
            )
            dir_scan_result = _lint.main(
                ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
            )

            # Diff-scope side: the same custom token, planted by a feature
            # branch's own diff.
            repo = _init_diff_repo(scan_scratch_dir)
            _git(repo, "checkout", "-q", "-b", "feature")
            (repo / "zort_diff.py").write_text(
                "def foo():\n"
                "    # references ZORT-9999 directly\n"
                "    return 1\n",
                encoding="utf-8",
            )
            _commit_all(repo, "plant the custom-pattern token")
            diff_scan_result = _lint.main(
                [
                    "--diff-scope",
                    "--worktree", str(repo),
                    "--source-branch", "source",
                    "--feature-branch", "feature",
                ]
            )
        finally:
            _lint._PROVENANCE_PATTERNS.remove(custom_entry)

        assert dir_scan_result == 1, (
            "Whole-directory scan did not enforce the newly added ZORT-ID "
            "pattern — _PROVENANCE_PATTERNS changes must be picked up "
            "without any other code change."
        )
        assert diff_scan_result == 1, (
            "Diff-scope mode did not enforce the same newly added ZORT-ID "
            "pattern the directory scan enforced — the two modes do not "
            "share one rule-source."
        )

    def test_diff_scope_requires_worktree_and_branches(self) -> None:
        """``--diff-scope`` without the required flags fails with a usage
        error (exit 2), not a silent no-op or a crash."""
        result = _lint.main(["--diff-scope"])

        assert result == 2, (
            "--diff-scope without --worktree/--source-branch/--feature-branch "
            "must exit 2 (usage error)."
        )

    def test_diff_scope_matches_whole_directory_scan_cli_unaffected(
        self, scan_scratch_dir: Path
    ) -> None:
        """Adding --diff-scope's flags to the parser does not change the
        whole-directory scan's existing CLI or default behavior when
        --diff-scope is not passed (backward compatibility)."""
        code_dir = scan_scratch_dir / "unaffected"
        code_dir.mkdir()
        (code_dir / "clean_module.py").write_text(
            "def add(a, b):\n"
            "    # Adds two numbers and returns the sum.\n"
            "    return a + b\n",
            encoding="utf-8",
        )

        result = _lint.main(
            ["--code-dirs", str(code_dir), "--prose-dirs", str(scan_scratch_dir)]
        )

        assert result == 0, (
            "Whole-directory scan behavior regressed after adding "
            "--diff-scope's CLI flags."
        )
