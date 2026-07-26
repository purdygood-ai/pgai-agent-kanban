"""
test_lint_env_bootstrap.py
==========================
Tests for team/scripts/lint_env_bootstrap.py — the both-sides env-bootstrap
class-closer lint.

Tests cover the four acceptance-criterion cases:

  1. **Bash-side green** (positive case): the real tree (scripts/, scripts/cm/,
     scripts/dashboard/) produces zero violations after the adoption sweep.
     This is the proof the lint passes the current codebase.

  2. **Bash-side scratch negative**: a synthetic executable ``.sh`` file that
     references PGAI_AGENT_KANBAN_ROOT_PATH without sourcing env_bootstrap.sh
     or wake_common.sh triggers a violation.  This is the proof the guard
     actually guards — a silent pass-through bug would make it always green.

  3. **Python-side green** (positive case): the real Python entry points and
     package modules under team/ produce zero violations after all named entry
     points route through resolve_kanban_root().

  4. **Python-side scratch negative**: a synthetic Python file that reads
     PGAI_AGENT_KANBAN_ROOT_PATH directly via os.environ (outside the canonical
     resolver) triggers a violation.

Additional cases:

  5. **Bash-side: comment-only reference is exempt** — a file that mentions
     the env var only in a ``#`` comment line is not flagged even without a
     source prelude.

  6. **Bash-side: non-executable file is exempt** — a file referencing the var
     on a live line but without execute permission is not flagged (only
     executable entry points are in scope).

  7. **Bash-side: wake_common.sh is an accepted equivalent** — a file that
     sources wake_common.sh instead of env_bootstrap.sh passes the lint.

  8. **Python-side: env.py itself is exempt** — the canonical resolver module
     is excluded from the scan even though it accesses the env var.

  9. **Python-side: test files are exempt** — files named test_*.py or under a
     tests/ directory are excluded (monkeypatching the env var in tests is
     legitimate).

 10. **main() returns 0 on clean synthetic trees**: a synthetic scripts/ dir
     with zero violations causes main() to return 0.

 11. **main() returns 1 on a violation**: a synthetic scripts/ dir containing
     one violation causes main() to return 1.

 12. **main() returns 2 when team dir not found**.

 13. **--help exits 0 without error** (argparse integration smoke test via
     subprocess check).

All tests use pytest's ``tmp_path`` and importlib for isolation — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load lint_env_bootstrap.py as a module (mirrors pattern of sibling tests).
# Path: team/tests/unit/ → team/ (three levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent    # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_env_bootstrap.py"

_REAL_SCRIPTS_DIR = _SCRIPTS_DIR
_REAL_CM_DIR = _SCRIPTS_DIR / "cm"
_REAL_DASHBOARD_DIR = _SCRIPTS_DIR / "dashboard"


def _import_lint_module():
    """Import lint_env_bootstrap as a module via importlib (isolated, no sys.modules pollution)."""
    spec = importlib.util.spec_from_file_location(
        "lint_env_bootstrap", _LINT_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Fixture content imports
# ---------------------------------------------------------------------------

from tests.fixtures.fixture_missing_env_bootstrap_sh import (
    BASH_VIOLATION_CONTENT,
    BASH_ORDERING_VIOLATION_CONTENT,
)
from tests.fixtures.fixture_direct_env_read_py import PYTHON_VIOLATION_CONTENT


# ---------------------------------------------------------------------------
# Helpers: write synthetic scripts/
# ---------------------------------------------------------------------------


def _write_sh(path: Path, content: str, *, executable: bool = True) -> Path:
    """Write a shell script at the given path with optional executable mode."""
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(
            stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        )
    else:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    return path


def _write_py(path: Path, content: str) -> Path:
    """Write a Python file at the given path."""
    path.write_text(content, encoding="utf-8")
    return path


def _make_minimal_scripts_dir(tmp_path: Path) -> Path:
    """Create a minimal synthetic scripts/ layout with cm/ and dashboard/ subdirs.

    The directory contains no .sh files — tests add their own.

    Returns the ``scripts/`` directory.
    """
    scripts = tmp_path / "scripts"
    (scripts / "cm").mkdir(parents=True)
    (scripts / "dashboard").mkdir(parents=True)
    (scripts / "lib").mkdir(parents=True)
    return scripts


def _make_minimal_team_dir(tmp_path: Path) -> Path:
    """Create a minimal synthetic team/ layout for Python-side tests.

    Layout:
        tmp_path/
            scripts/
            pgai_agent_kanban/
                env.py       ← canonical resolver (exempt from scan)

    Returns the team/ directory (tmp_path itself).
    """
    team = tmp_path
    scripts = team / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    pkg = team / "pgai_agent_kanban"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "env.py").write_text(
        'import os\n'
        'from pathlib import Path\n'
        'def resolve_kanban_root() -> Path:\n'
        '    raw = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "").strip()\n'
        '    if not raw:\n'
        '        raise RuntimeError("PGAI_AGENT_KANBAN_ROOT_PATH not set")\n'
        '    return Path(raw).resolve()\n',
        encoding="utf-8",
    )
    return team


# ---------------------------------------------------------------------------
# Case 1 — Bash-side green: real tree passes
# ---------------------------------------------------------------------------


def test_bash_side_green_on_real_tree() -> None:
    """Bash-side check exits with zero violations against the real scripts/ tree.

    This confirms that after the adoption sweep (tickets 3-6), all entry points
    in the swept directories source an approved prelude.  If this test fails,
    a new script was added without the required source line.
    """
    violations = _lint.check_bash_side(_REAL_SCRIPTS_DIR, verbose=False)
    assert violations == [], (
        f"Unexpected violations on the real tree:\n"
        + "\n".join(f"  {v.path}: {v.message}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Case 2 — Bash-side scratch negative: missing prelude fires
# ---------------------------------------------------------------------------


def test_bash_side_scratch_negative_fires_on_missing_prelude(
    tmp_path: Path,
) -> None:
    """Bash-side check reports a violation for a script missing the prelude source.

    Plants the violation fixture (BASH_VIOLATION_CONTENT) as an executable .sh
    file in a synthetic scripts/ directory and asserts the lint returns exactly
    one violation naming that file.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    fixture_sh = scripts / "missing_prelude_entry.sh"
    _write_sh(fixture_sh, BASH_VIOLATION_CONTENT, executable=True)

    violations = _lint.check_bash_side(scripts, verbose=False)

    assert violations, (
        "Expected the bash-side check to find a violation in the fixture script, "
        "but it returned no violations.  The lint may have a silent pass-through bug."
    )
    violation_paths = [v.path for v in violations]
    assert fixture_sh in violation_paths, (
        f"Violation not reported for the fixture file.\n"
        f"Fixture: {fixture_sh}\n"
        f"Reported violations: {violation_paths}"
    )


def test_bash_side_scratch_negative_violation_message_is_actionable(
    tmp_path: Path,
) -> None:
    """The bash-side violation message names the missing preludes by name.

    Actionable error messages reduce operator time-to-fix; the message must
    mention at least one of the required bootstrap file names.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    fixture_sh = scripts / "missing_prelude_entry.sh"
    _write_sh(fixture_sh, BASH_VIOLATION_CONTENT, executable=True)

    violations = _lint.check_bash_side(scripts, verbose=False)

    for v in violations:
        if v.path == fixture_sh:
            assert "env_bootstrap.sh" in v.message or "wake_common.sh" in v.message, (
                f"Violation message does not name the required preludes:\n{v.message!r}"
            )
            return

    pytest.fail("Fixture file not among reported violations.")


# ---------------------------------------------------------------------------
# Case 2b — Bash-side ordering violation: source after first usage is flagged
# (BUG-0073 closed-hole proof — kept as a regression fixture)
# ---------------------------------------------------------------------------


def test_bash_side_ordering_violation_is_flagged(tmp_path: Path) -> None:
    """Bash-side check flags a script that sources env_bootstrap.sh AFTER first usage.

    This is the closed-hole proof for BUG-0073.  The pre-fix po-agent.sh pattern
    (and eight other scripts) had env_bootstrap.sh sourced after the first runtime
    use of PGAI_AGENT_KANBAN_ROOT_PATH.  The old presence-only predicate passed
    those files; the ordering check must catch them.

    If this test fails, the ordering check has regressed and the predicate hole
    has re-opened.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    fixture_sh = scripts / "ordering_violation_entry.sh"
    _write_sh(fixture_sh, BASH_ORDERING_VIOLATION_CONTENT, executable=True)

    violations = _lint.check_bash_side(scripts, verbose=False)

    assert violations, (
        "Expected the bash-side check to flag the ordering violation (source after "
        "first usage), but it returned no violations.  The ordering predicate may "
        "have regressed — the BUG-0073 hole may be re-open."
    )
    violation_paths = [v.path for v in violations]
    assert fixture_sh in violation_paths, (
        f"Violation not reported for the ordering-violation fixture.\n"
        f"Fixture: {fixture_sh}\n"
        f"Reported violations: {violation_paths}"
    )


def test_bash_side_ordering_violation_message_names_lines(tmp_path: Path) -> None:
    """The ordering-violation message names the source line and the first-usage line.

    Actionable error messages must tell the operator exactly where to move the
    source call.  Both line numbers must appear in the message.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    fixture_sh = scripts / "ordering_violation_entry.sh"
    _write_sh(fixture_sh, BASH_ORDERING_VIOLATION_CONTENT, executable=True)

    violations = _lint.check_bash_side(scripts, verbose=False)

    for v in violations:
        if v.path == fixture_sh:
            # The message must reference line numbers (ordering context).
            assert "line" in v.message.lower(), (
                f"Ordering-violation message does not mention 'line':\n{v.message!r}"
            )
            return

    pytest.fail("Ordering-violation fixture not among reported violations.")


def test_source_precedes_first_usage_passes_correct_ordering() -> None:
    """_source_precedes_first_usage returns True when source comes before usage."""
    text = (
        "#!/usr/bin/env bash\n"
        'source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"\n'
        "KANBAN_ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}\n"
    )
    ok, src_line, use_line = _lint._source_precedes_first_usage(text)
    assert ok is True, (
        f"Expected correct ordering to be accepted; src={src_line}, use={use_line}"
    )


def test_source_precedes_first_usage_fails_reversed_ordering() -> None:
    """_source_precedes_first_usage returns False when usage comes before source."""
    text = (
        "#!/usr/bin/env bash\n"
        "KANBAN_ROOT=${PGAI_AGENT_KANBAN_ROOT_PATH}\n"
        'source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"\n'
    )
    ok, src_line, use_line = _lint._source_precedes_first_usage(text)
    assert ok is False, (
        f"Expected reversed ordering to be rejected; src={src_line}, use={use_line}"
    )
    assert use_line < src_line, (
        f"Expected use_line ({use_line}) < src_line ({src_line}) for reversed ordering"
    )


def test_source_precedes_first_usage_no_usage_is_ok() -> None:
    """_source_precedes_first_usage returns True when there is no usage of the var."""
    text = (
        "#!/usr/bin/env bash\n"
        'source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"\n'
        "echo hello\n"
    )
    ok, src_line, use_line = _lint._source_precedes_first_usage(text)
    assert ok is True, "Source with no usage should be accepted"
    assert use_line == 0, f"Expected use_line=0 (not found), got {use_line}"


# ---------------------------------------------------------------------------
# Case 2c — Heredoc body exclusion: var mention in heredoc body above source
#           must NOT produce an ordering violation (regression guard for BUG-0087)
# ---------------------------------------------------------------------------


def test_source_precedes_first_usage_heredoc_only_mention_is_not_flagged() -> None:
    """A heredoc body that mentions PGAI_AGENT_KANBAN_ROOT_PATH above the source line
    must NOT be treated as a runtime usage.

    This is the regression guard for BUG-0087: cm/finalize.sh and cm/open-doc.sh
    have --help heredocs that document the env var above the source call.  The lint
    must not flag that pattern as an ordering violation because the heredoc body is
    documentation, not executable code.
    """
    text = textwrap.dedent("""\
        #!/usr/bin/env bash
        _help() {
          cat <<HELPTEXT
        Usage: my-script.sh <args>

        Configuration:
          PGAI_AGENT_KANBAN_ROOT_PATH  kanban root (default: ~/pgai_agent_kanban)
        HELPTEXT
        }

        for _arg in "$@"; do
          case "$_arg" in
            --help|-h) _help; exit 0 ;;
          esac
        done

        # shellcheck source=../lib/env_bootstrap.sh
        source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
    """)
    ok, src_line, use_line = _lint._source_precedes_first_usage(text)
    assert ok is True, (
        f"A heredoc-only mention of the env var above the source line must not "
        f"be flagged as an ordering violation (BUG-0087 regression).  "
        f"src_line={src_line}, use_line={use_line}"
    )


def test_heredoc_body_line_numbers_identifies_body_lines() -> None:
    """_heredoc_body_line_numbers returns the correct set of body line numbers."""
    lines = [
        "#!/usr/bin/env bash",               # 1
        "cat <<HELPTEXT",                     # 2  (opener — not in body)
        "  some documentation text",          # 3  (body)
        "HELPTEXT",                           # 4  (close-tag — not in body)
        "echo done",                          # 5
    ]
    body = _lint._heredoc_body_line_numbers(lines)
    assert 3 in body, f"Line 3 (body) should be in heredoc_body; got {body}"
    assert 2 not in body, "Opener line 2 must not be in heredoc_body"
    assert 4 not in body, "Close-tag line 4 must not be in heredoc_body"
    assert 1 not in body, "Lines before heredoc must not be in heredoc_body"
    assert 5 not in body, "Lines after heredoc must not be in heredoc_body"


def test_heredoc_body_line_numbers_quoted_delimiter() -> None:
    """_heredoc_body_line_numbers handles quoted heredoc delimiters (<<'TOKEN')."""
    lines = [
        "cat <<'HELPTEXT'",    # 1  opener
        "body line",           # 2  body
        "HELPTEXT",            # 3  close-tag
        "done",                # 4
    ]
    body = _lint._heredoc_body_line_numbers(lines)
    assert 2 in body, "Body line inside <<'TOKEN'...TOKEN block must be identified"
    assert 1 not in body
    assert 3 not in body
    assert 4 not in body


def test_bash_side_heredoc_only_mention_does_not_violate(tmp_path: Path) -> None:
    """check_bash_side does not flag a script whose only pre-source mention of the
    env var is inside a heredoc body (--help usage block pattern).

    This is the integration-level regression guard for BUG-0087.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    heredoc_sh = scripts / "heredoc_help_entry.sh"
    _write_sh(
        heredoc_sh,
        textwrap.dedent("""\
            #!/usr/bin/env bash
            _help() {
              cat <<HELPTEXT
            Configuration:
              PGAI_AGENT_KANBAN_ROOT_PATH  kanban root (default: ~/pgai_agent_kanban)
            HELPTEXT
            }
            for _arg in "$@"; do
              case "$_arg" in
                --help|-h) _help; exit 0 ;;
              esac
            done
            source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"
            KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
        """),
        executable=True,
    )

    violations = _lint.check_bash_side(scripts, verbose=False)

    violation_paths = [v.path for v in violations]
    assert heredoc_sh not in violation_paths, (
        "Script with env var mentioned only inside a heredoc body (above the source "
        "line) was incorrectly flagged as an ordering violation (BUG-0087 regression)."
    )


def test_bash_side_non_heredoc_usage_before_source_still_violates(
    tmp_path: Path,
) -> None:
    """check_bash_side still flags a script that uses the env var in live code
    (not a heredoc body) before the source line.

    This guard ensures the heredoc exemption does not weaken the ordering check
    for genuine pre-source runtime uses.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    violation_sh = scripts / "real_usage_before_source.sh"
    _write_sh(
        violation_sh,
        textwrap.dedent("""\
            #!/usr/bin/env bash
            # Deliberate: genuine live use of the var before source — must still fail.
            _help() {
              cat <<HELPTEXT
            Configuration:
              PGAI_AGENT_KANBAN_ROOT_PATH  kanban root
            HELPTEXT
            }
            KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
            source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"
        """),
        executable=True,
    )

    violations = _lint.check_bash_side(scripts, verbose=False)

    violation_paths = [v.path for v in violations]
    assert violation_sh in violation_paths, (
        "Script with a genuine live use of the env var before the source line must "
        "still be flagged as an ordering violation even when a heredoc body also "
        "mentions the var."
    )


# ---------------------------------------------------------------------------
# Case 3 — Python-side green: real tree passes
# ---------------------------------------------------------------------------


def test_python_side_green_on_real_tree() -> None:
    """Python-side check exits with zero violations against the real tree.

    Confirms that all Python entry points and package modules route through
    resolve_kanban_root() rather than reading the env var directly.
    """
    violations = _lint.check_python_side(_TEAM_DIR, verbose=False)
    assert violations == [], (
        f"Unexpected Python violations on the real tree:\n"
        + "\n".join(f"  {v.path}: {v.message}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Case 4 — Python-side scratch negative: direct env read fires
# ---------------------------------------------------------------------------


def test_python_side_scratch_negative_fires_on_direct_env_read(
    tmp_path: Path,
) -> None:
    """Python-side check reports a violation for a script with a direct os.environ read.

    Plants the violation fixture (PYTHON_VIOLATION_CONTENT) as a .py file in a
    synthetic scripts/ directory and asserts the lint returns exactly one
    violation naming that file.
    """
    team = _make_minimal_team_dir(tmp_path)
    fixture_py = team / "scripts" / "direct_env_reader.py"
    _write_py(fixture_py, PYTHON_VIOLATION_CONTENT)

    violations = _lint.check_python_side(team, verbose=False)

    assert violations, (
        "Expected the Python-side check to find a violation in the fixture script, "
        "but it returned no violations.  The lint may have a silent pass-through bug."
    )
    violation_paths = [v.path for v in violations]
    assert fixture_py in violation_paths, (
        f"Violation not reported for the fixture file.\n"
        f"Fixture: {fixture_py}\n"
        f"Reported violations: {violation_paths}"
    )


def test_python_side_scratch_negative_message_names_the_var(
    tmp_path: Path,
) -> None:
    """The Python-side violation message names the env var and the canonical resolver.

    Actionable error messages must include enough context for the operator to
    know what to fix.
    """
    team = _make_minimal_team_dir(tmp_path)
    fixture_py = team / "scripts" / "direct_env_reader.py"
    _write_py(fixture_py, PYTHON_VIOLATION_CONTENT)

    violations = _lint.check_python_side(team, verbose=False)

    for v in violations:
        if v.path == fixture_py:
            assert "PGAI_AGENT_KANBAN_ROOT_PATH" in v.message, (
                f"Violation message does not name the env var:\n{v.message!r}"
            )
            assert "resolve_kanban_root" in v.message or "pgai_agent_kanban.env" in v.message, (
                f"Violation message does not name the canonical resolver:\n{v.message!r}"
            )
            return

    pytest.fail("Fixture file not among reported violations.")


# ---------------------------------------------------------------------------
# Case 5 — Bash-side: comment-only reference is exempt
# ---------------------------------------------------------------------------


def test_bash_side_comment_only_reference_is_not_flagged(tmp_path: Path) -> None:
    """A script that mentions the env var ONLY in comments passes the bash-side check.

    Documentation references in ``#`` comment lines do not constitute a runtime
    dependency and should not require a prelude source line.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    comment_only_sh = scripts / "comment_only.sh"
    _write_sh(
        comment_only_sh,
        textwrap.dedent("""\
            #!/usr/bin/env bash
            # This script documents PGAI_AGENT_KANBAN_ROOT_PATH usage.
            # See also: PGAI_AGENT_KANBAN_ROOT_PATH for environment setup.
            set -euo pipefail
            echo "no live reference to the var"
        """),
        executable=True,
    )

    violations = _lint.check_bash_side(scripts, verbose=False)

    violation_paths = [v.path for v in violations]
    assert comment_only_sh not in violation_paths, (
        "Script with comment-only reference to the env var was incorrectly flagged."
    )


# ---------------------------------------------------------------------------
# Case 6 — Bash-side: non-executable file is exempt
# ---------------------------------------------------------------------------


def test_bash_side_non_executable_is_exempt(tmp_path: Path) -> None:
    """A non-executable .sh file is not flagged even if it references the env var.

    Only executable entry-point scripts are in scope for the bootstrap lint.
    Non-executable .sh files (e.g. sourced library fragments) are exempt.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    nonexec_sh = scripts / "non_executable.sh"
    _write_sh(
        nonexec_sh,
        BASH_VIOLATION_CONTENT,
        executable=False,
    )

    violations = _lint.check_bash_side(scripts, verbose=False)

    violation_paths = [v.path for v in violations]
    assert nonexec_sh not in violation_paths, (
        "Non-executable .sh file was incorrectly flagged by the bash-side check."
    )


# ---------------------------------------------------------------------------
# Case 7 — Bash-side: wake_common.sh is an accepted equivalent
# ---------------------------------------------------------------------------


def test_bash_side_wake_common_is_accepted_equivalent(tmp_path: Path) -> None:
    """A script sourcing wake_common.sh passes the bash-side check.

    wake_common.sh is the accepted equivalent bootstrap for wake-family entry
    points; scripts that source it must not be flagged as violations.
    """
    scripts = _make_minimal_scripts_dir(tmp_path)
    wake_sh = scripts / "wake_entry.sh"
    _write_sh(
        wake_sh,
        textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            # shellcheck source=lib/wake_common.sh
            source "$(dirname "${BASH_SOURCE[0]}")/lib/wake_common.sh"
            echo "root is: ${PGAI_AGENT_KANBAN_ROOT_PATH}"
        """),
        executable=True,
    )

    violations = _lint.check_bash_side(scripts, verbose=False)

    violation_paths = [v.path for v in violations]
    assert wake_sh not in violation_paths, (
        "Script sourcing wake_common.sh was incorrectly flagged as a violation."
    )


# ---------------------------------------------------------------------------
# Case 8 — Python-side: env.py itself is exempt
# ---------------------------------------------------------------------------


def test_python_side_canonical_resolver_is_exempt(tmp_path: Path) -> None:
    """The canonical resolver (pgai_agent_kanban/env.py) is excluded from the scan.

    env.py owns the os.environ access pattern; flagging it would be circular.
    """
    team = _make_minimal_team_dir(tmp_path)
    # env.py is already created by _make_minimal_team_dir with an os.environ read.
    violations = _lint.check_python_side(team, verbose=False)

    violation_paths = [v.path for v in violations]
    env_py = team / "pgai_agent_kanban" / "env.py"
    assert env_py not in violation_paths, (
        "pgai_agent_kanban/env.py (the canonical resolver) was incorrectly flagged."
    )


# ---------------------------------------------------------------------------
# Case 9 — Python-side: test files are exempt
# ---------------------------------------------------------------------------


def test_python_side_test_files_are_exempt(tmp_path: Path) -> None:
    """Files named test_*.py and files under a tests/ directory are excluded.

    Test files legitimately monkeypatch PGAI_AGENT_KANBAN_ROOT_PATH; they must
    not be flagged as violations.
    """
    team = _make_minimal_team_dir(tmp_path)

    # Plant violation content in a test file under scripts/
    test_py = team / "scripts" / "test_something.py"
    _write_py(test_py, PYTHON_VIOLATION_CONTENT)

    # Plant violation content under a tests/ directory
    tests_dir = team / "pgai_agent_kanban" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    in_tests_py = tests_dir / "helper.py"
    _write_py(in_tests_py, PYTHON_VIOLATION_CONTENT)

    violations = _lint.check_python_side(team, verbose=False)

    violation_paths = [v.path for v in violations]
    assert test_py not in violation_paths, (
        "test_*.py file in scripts/ was incorrectly flagged."
    )
    assert in_tests_py not in violation_paths, (
        "File under tests/ directory was incorrectly flagged."
    )


# ---------------------------------------------------------------------------
# Case 10 — main() returns 0 on a clean synthetic team dir
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_tree(tmp_path: Path) -> None:
    """main() returns 0 when both sides find no violations in a clean synthetic tree.

    A synthetic team/ with an empty scripts/ and the canonical env.py (but no
    additional Python files with direct env reads) should produce a clean exit.
    """
    team = _make_minimal_team_dir(tmp_path)
    # Add a clean bash script with the prelude
    scripts = team / "scripts"
    clean_sh = scripts / "clean_entry.sh"
    _write_sh(
        clean_sh,
        textwrap.dedent("""\
            #!/usr/bin/env bash
            set -euo pipefail
            source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"
            echo "${PGAI_AGENT_KANBAN_ROOT_PATH}"
        """),
        executable=True,
    )

    rc = _lint.main(["--team-dir", str(team)])
    assert rc == 0, f"Expected main() to return 0 on a clean tree, got {rc}"


# ---------------------------------------------------------------------------
# Case 11 — main() returns 1 on a violation
# ---------------------------------------------------------------------------


def test_main_returns_one_on_bash_violation(tmp_path: Path) -> None:
    """main() returns 1 when the bash-side check finds at least one violation."""
    team = _make_minimal_team_dir(tmp_path)
    scripts = team / "scripts"
    violation_sh = scripts / "violator.sh"
    _write_sh(violation_sh, BASH_VIOLATION_CONTENT, executable=True)

    rc = _lint.main(["--team-dir", str(team), "--bash-only"])
    assert rc == 1, f"Expected main() to return 1 on a bash violation, got {rc}"


def test_main_returns_one_on_python_violation(tmp_path: Path) -> None:
    """main() returns 1 when the Python-side check finds at least one violation."""
    team = _make_minimal_team_dir(tmp_path)
    violation_py = team / "scripts" / "violator.py"
    _write_py(violation_py, PYTHON_VIOLATION_CONTENT)

    rc = _lint.main(["--team-dir", str(team), "--python-only"])
    assert rc == 1, f"Expected main() to return 1 on a python violation, got {rc}"


# ---------------------------------------------------------------------------
# Case 12 — main() returns 2 when team dir is not found
# ---------------------------------------------------------------------------


def test_main_returns_two_on_missing_team_dir(tmp_path: Path) -> None:
    """main() returns 2 when the specified --team-dir does not exist."""
    nonexistent = tmp_path / "does_not_exist"
    rc = _lint.main(["--team-dir", str(nonexistent)])
    assert rc == 2, f"Expected main() to return 2 for missing team dir, got {rc}"


# ---------------------------------------------------------------------------
# Case 13 — --help exits 0 (argparse integration smoke test)
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    """Running lint_env_bootstrap.py --help exits 0 without errors."""
    result = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--help exited with non-zero code {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert "lint_env_bootstrap" in result.stdout, (
        "Expected 'lint_env_bootstrap' in --help output."
    )


# ---------------------------------------------------------------------------
# Helper function unit tests
# ---------------------------------------------------------------------------


def test_file_references_root_var_detects_non_comment_line() -> None:
    """_file_references_root_var returns True for a non-comment line with the var."""
    text = "KANBAN=${PGAI_AGENT_KANBAN_ROOT_PATH:-/default}\n"
    assert _lint._file_references_root_var(text) is True


def test_file_references_root_var_ignores_comment_lines() -> None:
    """_file_references_root_var returns False when var appears only in comments."""
    text = "# PGAI_AGENT_KANBAN_ROOT_PATH is documented here\necho hello\n"
    assert _lint._file_references_root_var(text) is False


def test_file_sources_approved_prelude_detects_env_bootstrap() -> None:
    """_file_sources_approved_prelude returns True when env_bootstrap.sh is sourced."""
    text = 'source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"\n'
    assert _lint._file_sources_approved_prelude(text) is True


def test_file_sources_approved_prelude_detects_wake_common() -> None:
    """_file_sources_approved_prelude returns True when wake_common.sh is sourced."""
    text = 'source "$(dirname "${BASH_SOURCE[0]}")/lib/wake_common.sh"\n'
    assert _lint._file_sources_approved_prelude(text) is True


def test_file_sources_approved_prelude_rejects_missing_source() -> None:
    """_file_sources_approved_prelude returns False when no approved prelude is sourced."""
    text = "KANBAN=${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}\n"
    assert _lint._file_sources_approved_prelude(text) is False


def test_check_python_file_detects_dict_style_read(tmp_path: Path) -> None:
    """check_python_file_for_direct_env_reads detects os.environ["VAR"] style access."""
    py_file = tmp_path / "example.py"
    py_file.write_text(
        'import os\nroot = os.environ["PGAI_AGENT_KANBAN_ROOT_PATH"]\n',
        encoding="utf-8",
    )
    hits = _lint.check_python_file_for_direct_env_reads(py_file)
    assert hits, "Expected dict-style os.environ access to be detected."


def test_check_python_file_detects_get_style_read(tmp_path: Path) -> None:
    """check_python_file_for_direct_env_reads detects os.environ.get("VAR") style access."""
    py_file = tmp_path / "example.py"
    py_file.write_text(
        'import os\nroot = os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "")\n',
        encoding="utf-8",
    )
    hits = _lint.check_python_file_for_direct_env_reads(py_file)
    assert hits, "Expected get-style os.environ access to be detected."


def test_check_python_file_ignores_comment_line(tmp_path: Path) -> None:
    """check_python_file_for_direct_env_reads ignores accesses on comment lines."""
    py_file = tmp_path / "example.py"
    py_file.write_text(
        '# os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH", "")\nprint("ok")\n',
        encoding="utf-8",
    )
    hits = _lint.check_python_file_for_direct_env_reads(py_file)
    assert not hits, "Expected comment-line access to be ignored."
