"""
test_lint_halt_scope_writers.py
=================================
Tests for team/scripts/lint_halt_scope_writers.py — the check that reports
a HALT-file creation site targeting the fleet-wide $KANBAN_ROOT/HALT
sentinel from a function/script not itself named "global".

Test coverage:

  1. Real-tree green — the shipped team/ tree produces zero violations
     (proof the repaired fleet-scope writers are themselves clean, and
     that the pre-existing legitimate global writers — halt_global,
     unhalt_global, GLOBAL_HALT_FILE — are correctly recognized as
     self-declared).
  2. A synthetic root-HALT write planted in a project-scoped (non-"global")
     bash function is detected.
  3. The same write, inside a function named with "global", is not flagged.
  4. The same write, suppressed by a "# halt-scope-allowlist:" marker, is
     not flagged.
  5. A reader (an `[[ -f ... ]]` existence check) is never flagged — only
     writers are in scope.
  6. A project-scoped HALT write (no KANBAN_ROOT in the path) is never
     flagged, regardless of scope name; nor is one spelled out literally as
     $KANBAN_ROOT/projects/<name>/HALT, in each of the three write shapes
     (assign-then-touch, direct touch, redirect).
  7. Python equivalents of cases 2-4 (root-scoped .touch() outside a
     "global"-named function; allowed by function name; allowed by
     allowlist marker).
  8. An operator-facing echo/printf line that merely PRINTS an example
     "touch $KANBAN_ROOT/HALT" command is not treated as an actual write
     (regression guard for the unwind-rc.sh instructional-text false
     positive found while building this lint).
  9. main() returns 0 on a clean synthetic tree, 1 when a violation is
     present, and 2 when --team-dir does not exist.
 10. The historical shape of overwatch_halt_first_fix (root-scoped
     halt_flag, touched from a non-"global" function) is detected — proof
     the lint catches a project-scoped repair writing the fleet-wide HALT.
 11. A declaration keyword (local/declare/typeset/export/readonly) placed
     in front of the root-scoped assignment does not hide the write from
     detection — asserted per keyword — and the same negative cases from
     2-4/6 (global-named scope, allowlist marker, project-scoped writer)
     still pass when written in the declaration-prefixed form.
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load lint_halt_scope_writers.py as a module (mirrors sibling lint tests'
# import-by-path pattern, e.g. test_lint_dashboard_network_git_gate.py).
# Path: team/tests/unit/ -> team/ (two levels up)
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_halt_scope_writers.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_halt_scope_writers", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Case 1 — Real tree is clean
# ---------------------------------------------------------------------------


def test_real_tree_has_zero_violations() -> None:
    # Deliberately exercises the real team/ directory (not a fixture): this
    # is a live regression guard over the actual source, not an isolated
    # behavior a fixture could stand in for.
    violations = _lint.scan_tree(_TEAM_DIR)
    assert violations == [], (
        f"Expected zero root-HALT-scope violations in the shipped tree; found "
        f"{len(violations)}: {violations}"
    )


# ---------------------------------------------------------------------------
# Case 2-4 — bash: unnamed scope flagged, "global"-named scope allowed,
# allowlist marker allowed.
# ---------------------------------------------------------------------------


def test_bash_root_halt_write_in_unnamed_scope_is_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "single_project_repair.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            halt_flag="${KANBAN_ROOT}/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert len(violations) == 1
    assert violations[0].scope == "single_project_repair"
    assert violations[0].line == 4


def test_bash_root_halt_write_in_global_named_scope_is_allowed(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "fleet_repair.sh",
        """\
        #!/usr/bin/env bash
        halt_global_repair() {
            halt_flag="${KANBAN_ROOT}/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_root_halt_write_suppressed_by_allowlist_marker(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "special_case.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            halt_flag="${KANBAN_ROOT}/HALT"
            # halt-scope-allowlist: deliberate fleet-wide stop, reviewed 2026-09-08
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_variable_named_global_is_allowed_regardless_of_scope_name(
    tmp_path: Path,
) -> None:
    """Mirrors the real GLOBAL_HALT_FILE / _halt_global naming convention:
    a self-declaring variable name is accepted even if the enclosing
    function/script name itself says nothing about scope."""
    sh = _write(
        tmp_path / "reset_state.sh",
        """\
        #!/usr/bin/env bash
        reset_state() {
            GLOBAL_HALT_FILE="${KANBAN_ROOT}/HALT"
            touch "$GLOBAL_HALT_FILE"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 5 — readers are never flagged
# ---------------------------------------------------------------------------


def test_bash_reader_is_never_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "check_halt.sh",
        """\
        #!/usr/bin/env bash
        single_project_check() {
            halt_flag="${KANBAN_ROOT}/HALT"
            if [[ -f "$halt_flag" ]]; then
                echo "halted"
            fi
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 6 — project-scoped write is never flagged
# ---------------------------------------------------------------------------


def test_bash_project_scoped_write_is_never_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "project_repair.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            halt_flag="$(pp_project_root "$OVERWATCH_PROJECT")/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 6b — a project-scoped HALT write spelled out literally under
# $KANBAN_ROOT/projects/<name>/ (rather than resolved through a helper) must
# not be misread as a fleet-scoped write, in each of the three write shapes.
# This is the exact false positive the '.*' between KANBAN_ROOT and /HALT
# used to produce: an author who applies the lint's own recommended fix
# (spell out the project-scoped path) was told the lint still failed.
# ---------------------------------------------------------------------------


def test_bash_literal_project_scoped_assign_and_touch_is_never_flagged(
    tmp_path: Path,
) -> None:
    sh = _write(
        tmp_path / "project_repair_literal_assign.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            local halt_flag="${KANBAN_ROOT}/projects/${name}/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_literal_project_scoped_touch_is_never_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "project_repair_literal_touch.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            touch "${KANBAN_ROOT}/projects/${name}/HALT"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_literal_project_scoped_redirect_is_never_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "project_repair_literal_redirect.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            : > "${KANBAN_ROOT}/projects/${name}/HALT"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 7 — python equivalents
# ---------------------------------------------------------------------------


def test_python_root_halt_write_in_unnamed_function_is_flagged(tmp_path: Path) -> None:
    py = _write(
        tmp_path / "repair.py",
        """\
        def repair_one_project(ctx):
            kanban_root = ctx.kanban_root
            halt_path = kanban_root / "HALT"
            halt_path.touch()
        """,
    )
    violations = _lint.scan_python_file(py)
    assert len(violations) == 1
    assert violations[0].scope == "repair_one_project"


def test_python_root_halt_write_in_global_named_function_is_allowed(tmp_path: Path) -> None:
    py = _write(
        tmp_path / "ops_write.py",
        """\
        def halt_global(ctx):
            kanban_root = ctx.kanban_root
            halt_path = kanban_root / "HALT"
            halt_path.touch()
        """,
    )
    violations = _lint.scan_python_file(py)
    assert violations == []


def test_python_root_halt_write_suppressed_by_allowlist_marker(tmp_path: Path) -> None:
    py = _write(
        tmp_path / "repair2.py",
        """\
        def repair_one_project(ctx):
            kanban_root = ctx.kanban_root
            halt_path = kanban_root / "HALT"
            # halt-scope-allowlist: deliberate fleet-wide stop, reviewed 2026-09-08
            halt_path.touch()
        """,
    )
    violations = _lint.scan_python_file(py)
    assert violations == []


def test_python_reader_is_never_flagged(tmp_path: Path) -> None:
    py = _write(
        tmp_path / "check.py",
        """\
        def check_one_project(ctx):
            kanban_root = ctx.kanban_root
            halt_path = kanban_root / "HALT"
            return halt_path.exists()
        """,
    )
    violations = _lint.scan_python_file(py)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 8 — instructional echo/printf text is not a mutation
# ---------------------------------------------------------------------------


def test_bash_echo_of_example_touch_command_is_not_flagged(tmp_path: Path) -> None:
    sh = _write(
        tmp_path / "unwind_like.sh",
        """\
        #!/usr/bin/env bash
        single_project_precheck() {
            GLOBAL_HALT_FILE="$KANBAN_ROOT/HALT"
            echo "  To halt the entire kanban:"
            echo "    touch $GLOBAL_HALT_FILE"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


# ---------------------------------------------------------------------------
# Case 9 — main() exit codes
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_synthetic_tree(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    (team_dir / "scripts").mkdir(parents=True)
    (team_dir / "pgai_agent_kanban").mkdir(parents=True)
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 0


def test_main_returns_one_when_violation_present(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    scripts_dir = team_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (team_dir / "pgai_agent_kanban").mkdir(parents=True)
    _write(
        scripts_dir / "single_project_repair.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            halt_flag="${KANBAN_ROOT}/HALT"
            touch "$halt_flag"
        }
        """,
    )
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 1


def test_main_returns_two_when_team_dir_missing(tmp_path: Path) -> None:
    rc = _lint.main(["--team-dir", str(tmp_path / "does-not-exist")])
    assert rc == 2


# ---------------------------------------------------------------------------
# Case 10 — historical root-scoped repair shape is detected (regression proof)
# ---------------------------------------------------------------------------


def test_root_scoped_overwatch_halt_shape_is_detected(tmp_path: Path) -> None:
    """Mirrors the exact shape overwatch_halt_first_fix once had: a
    project-scoped repair function touching the fleet-wide root HALT.
    Proof the lint flags that write from a function not named
    "global"."""
    sh = _write(
        tmp_path / "overwatch_protocol.sh",
        """\
        #!/usr/bin/env bash
        overwatch_halt_first_fix() {
            local fn="$1"
            halt_flag="${KANBAN_ROOT}/HALT"
            if [[ -f "$halt_flag" ]]; then
                halt_pre_existing="true"
            else
                touch "$halt_flag" || {
                    return 1
                }
            fi
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert len(violations) == 1
    assert violations[0].scope == "overwatch_halt_first_fix"


# ---------------------------------------------------------------------------
# Case 11 — a declaration keyword in front of the assignment does not hide
# the write.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keyword", ["local", "declare", "typeset", "export", "readonly"]
)
def test_bash_declaration_prefixed_root_halt_write_in_unnamed_scope_is_flagged(
    tmp_path: Path, keyword: str
) -> None:
    """A declaration keyword in front of the assignment must not hide the
    site from detection. Asserted once per keyword rather than by one
    representative, so no single keyword can regress unnoticed."""
    sh = _write(
        tmp_path / f"single_project_repair_{keyword}.sh",
        f"""\
        #!/usr/bin/env bash
        single_project_repair() {{
            {keyword} halt_flag="${{KANBAN_ROOT}}/HALT"
            touch "$halt_flag"
        }}
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert len(violations) == 1, (
        f"declaration keyword {keyword!r} hid the root-scoped write from detection"
    )
    assert violations[0].scope == "single_project_repair"
    assert violations[0].line == 4


def test_bash_declaration_prefixed_write_in_global_named_scope_is_allowed(
    tmp_path: Path,
) -> None:
    """A genuinely fleet-scoped writer, declared with `declare` instead of a
    bare assignment, must stay unflagged — widening the assignment pattern
    must not turn the global-name escape hatch into a false positive."""
    sh = _write(
        tmp_path / "fleet_repair_declare.sh",
        """\
        #!/usr/bin/env bash
        halt_global_repair() {
            declare halt_flag="${KANBAN_ROOT}/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_declaration_prefixed_write_suppressed_by_allowlist_marker(
    tmp_path: Path,
) -> None:
    """The allowlist marker must still suppress a declaration-prefixed
    write."""
    sh = _write(
        tmp_path / "special_case_typeset.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            typeset halt_flag="${KANBAN_ROOT}/HALT"
            # halt-scope-allowlist: deliberate fleet-wide stop, reviewed 2026-09-08
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []


def test_bash_declaration_prefixed_project_scoped_write_is_never_flagged(
    tmp_path: Path,
) -> None:
    """A project-scoped writer (no KANBAN_ROOT in the path) written with a
    declaration keyword must stay unflagged — the widened pattern must not
    trade the declaration-prefix blind spot for a false-positive storm on
    ordinary project-scoped local variables."""
    sh = _write(
        tmp_path / "project_repair_readonly.sh",
        """\
        #!/usr/bin/env bash
        single_project_repair() {
            readonly halt_flag="$(pp_project_root "$OVERWATCH_PROJECT")/HALT"
            touch "$halt_flag"
        }
        """,
    )
    violations = _lint.scan_bash_file(sh)
    assert violations == []
