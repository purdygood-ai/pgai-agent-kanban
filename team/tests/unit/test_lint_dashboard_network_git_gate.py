"""
test_lint_dashboard_network_git_gate.py
========================================
Tests for team/scripts/lint_dashboard_network_git_gate.py — the
"teeth" lint that fails when any network-capable git invocation
(ls-remote|fetch|pull|push|clone) appears under team/scripts/dashboard/*.

Test coverage:

  1. Real-tree green — the shipped team/scripts/dashboard/ tree produces
     zero violations (proof the current codebase is clean).
  2-6. Scratch negatives — one per forbidden verb (ls-remote, fetch, pull,
     push, clone), each planted in a synthetic dashboard/ directory,
     confirming the gate actually fires for each.
  7. No-exceptions-for-the-git-window — a planted ls-remote in a file named
     git-status.sh (the same filename as the real 4:git window script) is
     still flagged; the gate explicitly forbids exempting that window.
  8. Comment-only mention is exempt — a line whose first non-whitespace
     character is '#' does not trigger a violation even though it names a
     forbidden verb (several real dashboard scripts document their own
     local-only guarantee using these exact words).
  9. Nested lib/ subdirectory is scanned (recursive scope).
 10. main() returns 0 on a clean synthetic tree.
 11. main() returns 1 when a violation is present.
 12. main() returns 2 when --team-dir does not exist.
 13. main() returns 2 when the dashboard/ subdirectory does not exist.
 14. --help exits 0 (argparse integration smoke test).
 15. Violation message is actionable — names the file, line, and verb.
 16. Remedy block (the gates-prescribe-remedies pattern): forcing a
     network-git violation prints the full three-part remedy block — cause(s)
     naming the offending file/line/verb, a numbered fix block of verbatim-
     runnable commands with no angle-bracket placeholders ending with the
     re-lint check, and the one-line enforced rule.
 17. Remedy commands clear the gate: the exact numbered commands printed in
     the remedy block are parsed out of the captured output and executed,
     verbatim, through a real shell — the offending line is commented out and
     the lint re-run against the fixture exits 0.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_dashboard_network_git_gate.py as a module (mirrors sibling tests'
# import-by-path pattern, e.g. test_lint_env_bootstrap.py).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_dashboard_network_git_gate.py"
_REAL_DASHBOARD_DIR = _SCRIPTS_DIR / "dashboard"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location(
        "lint_dashboard_network_git_gate", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_sh(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def _make_dashboard_dir(tmp_path: Path) -> Path:
    """Create a minimal synthetic team/scripts/dashboard/ layout with lib/."""
    dashboard = tmp_path / "team" / "scripts" / "dashboard"
    (dashboard / "lib").mkdir(parents=True, exist_ok=True)
    return dashboard


# ---------------------------------------------------------------------------
# Case 1 — Real tree is clean
# ---------------------------------------------------------------------------


def test_real_dashboard_tree_has_zero_violations() -> None:
    """The shipped team/scripts/dashboard/ tree produces zero violations.

    If this test fails, a network-capable git invocation was reintroduced
    somewhere under team/scripts/dashboard/ — exactly the regression class
    this gate exists to catch.
    """
    # Deliberately exercises the real team/scripts/dashboard/ tree (not a
    # fixture): this is a live regression guard, not an isolated behavior a
    # fixture could stand in for.
    violations = _lint.scan_dashboard_dir(_REAL_DASHBOARD_DIR, verbose=False)
    assert violations == [], (
        "Unexpected violations on the real dashboard tree:\n"
        + "\n".join(f"  {v.path}:{v.line} ({v.verb}) {v.snippet}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Cases 2-6 — Scratch negatives, one per forbidden verb
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,invocation",
    [
        ("ls-remote", 'git -C "$dev_tree" ls-remote --tags origin'),
        ("fetch", 'git -C "$dev_tree" fetch origin --tags --quiet'),
        ("pull", 'git -C "$dev_tree" pull origin main'),
        ("push", 'git -C "$dev_tree" push origin main'),
        ("clone", 'git clone git@example.com:some/repo.git "$dev_tree"'),
    ],
)
def test_planted_verb_is_flagged(tmp_path: Path, verb: str, invocation: str) -> None:
    """Each of the five forbidden verbs is detected when planted in a script."""
    dashboard = _make_dashboard_dir(tmp_path)
    fixture_sh = dashboard / f"planted_{verb.replace('-', '_')}.sh"
    _write_sh(
        fixture_sh,
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        {invocation}
        """,
    )

    violations = _lint.scan_dashboard_dir(dashboard, verbose=False)

    assert violations, (
        f"Expected a violation for verb {verb!r} but none was reported."
    )
    matched = [v for v in violations if v.path == fixture_sh]
    assert matched, f"No violation reported for the fixture file {fixture_sh}"
    assert matched[0].verb == verb, (
        f"Expected matched verb {verb!r}, got {matched[0].verb!r}"
    )


# ---------------------------------------------------------------------------
# Case 7 — No exceptions for the git window
# ---------------------------------------------------------------------------


def test_git_window_filename_is_not_exempt(tmp_path: Path) -> None:
    """A planted ls-remote inside a file named git-status.sh is still flagged.

    The gate's design law is explicit: "the ENTIRE dashboard is local-only —
    including the 4:git window."  This test proves the gate applies the same
    rule regardless of filename — there is no special-cased exemption for
    the two real git-window scripts (git-status.sh / git-recent-tags.sh).
    """
    dashboard = _make_dashboard_dir(tmp_path)
    fixture_sh = dashboard / "git-status.sh"
    _write_sh(
        fixture_sh,
        """\
        #!/usr/bin/env bash
        set -euo pipefail
        git -C "$dev_tree" ls-remote --tags origin "refs/tags/$tag"
        """,
    )

    violations = _lint.scan_dashboard_dir(dashboard, verbose=False)

    violation_paths = [v.path for v in violations]
    assert fixture_sh in violation_paths, (
        "A planted ls-remote in a file literally named git-status.sh was not "
        "flagged — the gate must not special-case the git window."
    )


# ---------------------------------------------------------------------------
# Case 8 — Comment-only mention is exempt
# ---------------------------------------------------------------------------


def test_comment_only_mention_is_not_flagged(tmp_path: Path) -> None:
    """A '#' comment line naming a forbidden verb does not trigger a violation.

    Real dashboard scripts (e.g. git-status.sh, git-recent-tags.sh) document
    their own local-only guarantee in prose using these exact words ("no
    fetch, pull, push, clone, or ls-remote is ever invoked here"). Flagging
    that documentation would be a false positive.
    """
    dashboard = _make_dashboard_dir(tmp_path)
    fixture_sh = dashboard / "documents_its_own_safety.sh"
    _write_sh(
        fixture_sh,
        """\
        #!/usr/bin/env bash
        # No git subcommand invoked here ever reaches the network (no fetch,
        # pull, push, clone, or ls-remote) -- this script is local-only.
        set -euo pipefail
        git -C "$dev_tree" tag --list
        """,
    )

    violations = _lint.scan_dashboard_dir(dashboard, verbose=False)

    violation_paths = [v.path for v in violations]
    assert fixture_sh not in violation_paths, (
        "Comment-only mention of forbidden verbs was incorrectly flagged."
    )


# ---------------------------------------------------------------------------
# Case 9 — Recursive scope: lib/ subdirectory is scanned
# ---------------------------------------------------------------------------


def test_lib_subdirectory_is_scanned(tmp_path: Path) -> None:
    """A violation inside dashboard/lib/*.sh is detected (recursive scope)."""
    dashboard = _make_dashboard_dir(tmp_path)
    fixture_sh = dashboard / "lib" / "shared_helper.sh"
    _write_sh(
        fixture_sh,
        """\
        #!/usr/bin/env bash
        get_kanban_version() {
          git -C "$repo_root" fetch origin --tags --quiet
        }
        """,
    )

    violations = _lint.scan_dashboard_dir(dashboard, verbose=False)

    violation_paths = [v.path for v in violations]
    assert fixture_sh in violation_paths, (
        "Violation inside dashboard/lib/ was not detected — scan must be recursive."
    )


# ---------------------------------------------------------------------------
# Case 10 — main() returns 0 on a clean synthetic tree
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_tree(tmp_path: Path) -> None:
    dashboard = _make_dashboard_dir(tmp_path)
    _write_sh(
        dashboard / "clean_script.sh",
        """\
        #!/usr/bin/env bash
        git -C "$dev_tree" tag --list
        git -C "$dev_tree" log -1
        """,
    )

    rc = _lint.main(["--team-dir", str(tmp_path / "team")])
    assert rc == 0, f"Expected main() to return 0 on a clean tree, got {rc}"


# ---------------------------------------------------------------------------
# Case 11 — main() returns 1 on a violation
# ---------------------------------------------------------------------------


def test_main_returns_one_on_violation(tmp_path: Path) -> None:
    dashboard = _make_dashboard_dir(tmp_path)
    _write_sh(
        dashboard / "violator.sh",
        """\
        #!/usr/bin/env bash
        git -C "$dev_tree" ls-remote --tags origin
        """,
    )

    rc = _lint.main(["--team-dir", str(tmp_path / "team")])
    assert rc == 1, f"Expected main() to return 1 on a violation, got {rc}"


# ---------------------------------------------------------------------------
# Case 12 — main() returns 2 when team dir is missing
# ---------------------------------------------------------------------------


def test_main_returns_two_on_missing_team_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "does_not_exist"
    rc = _lint.main(["--team-dir", str(nonexistent)])
    assert rc == 2, f"Expected main() to return 2 for missing team dir, got {rc}"


# ---------------------------------------------------------------------------
# Case 13 — main() returns 2 when dashboard/ subdirectory is missing
# ---------------------------------------------------------------------------


def test_main_returns_two_when_dashboard_subdir_missing(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    (team_dir / "scripts").mkdir(parents=True, exist_ok=True)
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 2, (
        f"Expected main() to return 2 when scripts/dashboard/ is absent, got {rc}"
    )


# ---------------------------------------------------------------------------
# Case 14 — --help exits 0
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--help exited with non-zero code {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert "lint_dashboard_network_git_gate" in result.stdout


# ---------------------------------------------------------------------------
# Case 15 — Violation message is actionable
# ---------------------------------------------------------------------------


def test_violation_message_names_file_line_and_verb(tmp_path: Path) -> None:
    dashboard = _make_dashboard_dir(tmp_path)
    fixture_sh = dashboard / "violator.sh"
    _write_sh(
        fixture_sh,
        """\
        #!/usr/bin/env bash
        set -euo pipefail
        git -C "$dev_tree" ls-remote --tags origin
        """,
    )

    violations = _lint.scan_dashboard_dir(dashboard, verbose=False)
    assert len(violations) == 1
    v = violations[0]
    assert v.path == fixture_sh
    assert v.verb == "ls-remote"
    assert v.line == 3
    assert "ls-remote" in v.snippet


# ---------------------------------------------------------------------------
# Remedy-block tests (the gates-prescribe-remedies pattern)
# ---------------------------------------------------------------------------

# A numbered fix-command line, e.g. "  1. sed -i '3s/^/# /' \"...\"".
_NUMBERED_COMMAND_RE = re.compile(r"^\s*(\d+)\.\s+(.+)$", re.MULTILINE)

# An unresolved angle-bracket placeholder such as "<path>" or "<team-dir>".
# The house placeholder law (docs/coding-standards.md) forbids these in the
# printed fix commands — every site-specific value must already be derived
# and substituted by the time the remedy block is printed.
_ANGLE_BRACKET_PLACEHOLDER_RE = re.compile(r"<[A-Za-z][\w -]*>")


class TestDashboardNetworkGitGateRemedyBlock:
    """The remedy block: cause(s), numbered verbatim commands, re-lint, rule.

    Exercises the gates-prescribe-remedies pattern's second lint target end
    to end: force a network-git violation, assert the remedy block contains
    every required part, assert the printed commands carry no angle-bracket
    placeholders, and then actually execute the printed commands verbatim to
    prove they clear the gate.
    """

    def _write_violation_fixture(self, tmp_path: Path) -> tuple[Path, Path]:
        """Return (team_dir, script_path) for a synthetic dashboard/ directory
        containing exactly one network-git violation."""
        dashboard = _make_dashboard_dir(tmp_path)
        fixture_sh = _write_sh(
            dashboard / "violator.sh",
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            git -C "$dev_tree" ls-remote --tags origin
            """,
        )
        team_dir = tmp_path / "team"
        return team_dir, fixture_sh

    def test_remedy_block_contains_all_required_parts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Forcing a network-git violation prints the full three-part remedy
        block.

        Asserts, per docs/coding-standards.md "Gates prescribe, not just
        diagnose":
          1. Cause(s) with a concrete location (path:line and verb).
          2. A numbered fix block of at least two verbatim-runnable
             commands, ending with the re-lint verification command.
          3. The one-line enforced rule.
          4. No angle-bracket placeholders anywhere in the output.
        """
        team_dir, fixture_sh = self._write_violation_fixture(tmp_path)

        result = _lint.main(["--team-dir", str(team_dir)])
        assert result == 1, "Forced network-git violation did not fail the gate."

        out = capsys.readouterr().out

        assert "Cause(s):" in out, (
            f"Remedy block missing the 'Cause(s):' section.\nstdout: {out!r}"
        )
        assert str(fixture_sh) in out and "ls-remote" in out, (
            f"Remedy block does not name the offending file and verb.\nstdout: {out!r}"
        )

        assert "To fix:" in out, (
            f"Remedy block missing the 'To fix:' numbered fix block.\nstdout: {out!r}"
        )
        commands = _NUMBERED_COMMAND_RE.findall(out)
        assert len(commands) >= 2, (
            "Remedy block's numbered fix block has fewer than 2 commands "
            "(expected at least: comment out the line, then re-lint).\n"
            f"Parsed commands: {commands!r}\nstdout: {out!r}"
        )
        last_command_text = commands[-1][1]
        assert "lint_dashboard_network_git_gate.py" in last_command_text, (
            "Last numbered command is not the re-lint verification command.\n"
            f"Last command: {last_command_text!r}\nstdout: {out!r}"
        )

        assert "is local-only" in out, (
            f"Remedy block missing the one-line enforced rule.\nstdout: {out!r}"
        )

        placeholder_hits = _ANGLE_BRACKET_PLACEHOLDER_RE.findall(out)
        assert placeholder_hits == [], (
            "Remedy block contains unresolved angle-bracket placeholder(s), "
            "violating the house placeholder law.\n"
            f"Placeholders found: {placeholder_hits!r}\nstdout: {out!r}"
        )

    def test_remedy_block_commands_clear_the_gate(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Executing the remedy block's printed commands verbatim clears the
        gate — the motivating end-to-end proof: a stranger reading only the
        gate's failure output, with no other guidance, can clear it.

        The test parses the exact numbered command strings out of the
        captured stdout (not a hand-reconstructed equivalent) and runs them
        through a real shell, then re-invokes the lint directly to confirm
        it now exits 0.
        """
        team_dir, fixture_sh = self._write_violation_fixture(tmp_path)

        result = _lint.main(["--team-dir", str(team_dir)])
        assert result == 1, "Forced network-git violation did not fail the gate."

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
        recheck = _lint.main(["--team-dir", str(team_dir)])
        assert recheck == 0, (
            "lint_dashboard_network_git_gate still fails against the fixture "
            "tree after running every printed remedy command verbatim."
        )

        # The offending line must now be a comment — proof the fix actually
        # neutralized the network call, not merely a string that satisfies
        # the lint's own textual check by coincidence.
        patched_text = fixture_sh.read_text(encoding="utf-8")
        offending_line = [
            line for line in patched_text.splitlines() if "ls-remote" in line
        ]
        assert offending_line, (
            f"Expected the ls-remote line to still be present (commented) "
            f"in the patched file.\nContents: {patched_text!r}"
        )
        assert offending_line[0].lstrip().startswith("#"), (
            "The offending ls-remote line was not commented out by the "
            f"remedy command.\nLine: {offending_line[0]!r}"
        )
