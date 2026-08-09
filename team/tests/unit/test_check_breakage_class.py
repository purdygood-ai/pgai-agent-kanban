"""
test_check_breakage_class.py
=============================
Tests for team/scripts/check_breakage_class.py — the breakage-class
pre-flight gate for the gated test runners (run-unit-tests.sh,
run-integration-tests.sh).

Breakage-class is the ONE fatal pre-flight check in either runner: a
Python file that cannot compile, a shell script that cannot be tokenized,
or a first-party ``pgai_agent_kanban`` import that no longer resolves.
Every other pre-flight check is quality-class (never fatal — see
test_gated_runner_lint.py).

Tests cover the core acceptance criteria (v2.3.5 requirements, Goal 2 /
Acceptance 2 — "Breakage fixture: a syntax error -> runners exit 1 before
pytest"):

  1. **A Python syntax error is reported and fails the gate** — the
     breakage fixture named in the acceptance criteria.  Also proves the
     finding names the offending file and line.

  2. **A bash syntax error is reported and fails the gate.**

  3. **An unresolved absolute pgai_agent_kanban import is reported.**

  4. **An unresolved relative import inside the pgai_agent_kanban package
     is reported.**

  5. **A clean synthetic tree passes (exit 0).**

  6. **The exclusion set (__pycache__, .venv, fixtures/) is honored** — a
     broken file placed inside an excluded directory is NOT flagged.

  7. **A missing --team-dir is a usage error (exit 2), not a false pass.**

  8. **The remedy block names Cause(s), a numbered fix step, and the
     Rule** (docs/coding-standards.md "Gates prescribe, not just
     diagnose").

  9. **The real team/ tree produces zero breakage-class findings** — the
     proof this gate does not false-positive against the actual codebase
     it ships alongside.

All tests use pytest's ``tmp_path`` for synthetic fixtures — no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load check_breakage_class.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_CHECK_SCRIPT = _SCRIPTS_DIR / "check_breakage_class.py"


def _import_check_module():
    spec = importlib.util.spec_from_file_location("check_breakage_class", _CHECK_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_CHECK_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_check = _import_check_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _minimal_clean_tree(team_dir: Path) -> None:
    """A minimal, entirely valid team/ tree: one clean python file, one
    clean bash file, and a pgai_agent_kanban package whose absolute and
    relative imports all resolve."""
    _write(team_dir / "clean_script.py", "def hello() -> str:\n    return 'hi'\n")
    _write(team_dir / "clean_script.sh", "#!/usr/bin/env bash\necho hello\n")
    pkg = team_dir / "pgai_agent_kanban"
    _write(pkg / "__init__.py", "")
    _write(pkg / "config.py", "VALUE = 1\n")
    _write(
        pkg / "consumer.py",
        "from pgai_agent_kanban.config import VALUE\nfrom . import config\nfrom .config import VALUE as V2\n",
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 1: Python syntax error (the breakage fixture)
# ---------------------------------------------------------------------------


def test_python_syntax_error_fails_the_gate(tmp_path: Path) -> None:
    """A deliberate syntax error is the breakage fixture named in the
    acceptance criteria: 'a syntax error -> runners exit 1 before pytest'."""
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "broken_module.py", "def foo(:\n    pass\n")

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 1, "a Python syntax error must fail the breakage-class gate"


def test_python_syntax_error_names_file_and_line(tmp_path: Path, capsys) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "broken_module.py", "def foo(:\n    pass\n")

    _check.main(["--team-dir", str(team_dir)])
    captured = capsys.readouterr()

    assert "broken_module.py:1" in captured.out
    assert "Python compile error" in captured.out


# ---------------------------------------------------------------------------
# Acceptance criterion 2: bash syntax error
# ---------------------------------------------------------------------------


def test_bash_syntax_error_fails_the_gate(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(
        team_dir / "scripts" / "broken.sh",
        "#!/usr/bin/env bash\nif [[ true\necho unterminated\n",
    )

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 1, "a bash syntax error must fail the breakage-class gate"


def test_bash_syntax_error_names_file(tmp_path: Path, capsys) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(
        team_dir / "scripts" / "broken.sh",
        "#!/usr/bin/env bash\nif [[ true\necho unterminated\n",
    )

    _check.main(["--team-dir", str(team_dir)])
    captured = capsys.readouterr()

    assert "scripts/broken.sh" in captured.out
    assert "Bash syntax error" in captured.out


# ---------------------------------------------------------------------------
# Acceptance criterion 3: unresolved first-party imports
# ---------------------------------------------------------------------------


def test_unresolved_absolute_import_fails_the_gate(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(
        team_dir / "caller.py",
        "from pgai_agent_kanban.nonexistent_module import thing\n",
    )

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 1, "an unresolved absolute pgai_agent_kanban import must fail the gate"


def test_unresolved_absolute_import_names_the_dotted_path(tmp_path: Path, capsys) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(
        team_dir / "caller.py",
        "from pgai_agent_kanban.nonexistent_module import thing\n",
    )

    _check.main(["--team-dir", str(team_dir)])
    captured = capsys.readouterr()

    assert "pgai_agent_kanban.nonexistent_module" in captured.out
    assert "Unresolved first-party import" in captured.out


def test_unresolved_relative_import_inside_package_fails_the_gate(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(
        team_dir / "pgai_agent_kanban" / "broken_consumer.py",
        "from .nonexistent_sibling import thing\n",
    )

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 1, "an unresolved relative import inside the package must fail the gate"


def test_import_from_named_pgai_agent_kanban_package_itself_resolves(tmp_path: Path) -> None:
    """from pgai_agent_kanban import X (module == the package name itself,
    with no trailing dot) must resolve against the package's __init__.py
    and not be misclassified as unresolved."""
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "caller.py", "from pgai_agent_kanban import config\n")

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 0


# ---------------------------------------------------------------------------
# Acceptance criterion 5: clean tree passes
# ---------------------------------------------------------------------------


def test_clean_tree_passes(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 0


# ---------------------------------------------------------------------------
# Acceptance criterion 6: exclusion set is honored
# ---------------------------------------------------------------------------


def test_broken_file_in_excluded_fixtures_dir_is_not_flagged(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "fixtures" / "deliberately_broken.py", "def foo(:\n    pass\n")

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 0, "fixtures/ is a documented exclusion -- broken example files there must not fail the gate"


def test_broken_file_in_pycache_is_not_flagged(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "__pycache__" / "stale.py", "def foo(:\n    pass\n")

    exit_code = _check.main(["--team-dir", str(team_dir)])

    assert exit_code == 0, "__pycache__ is a documented exclusion"


# ---------------------------------------------------------------------------
# Acceptance criterion 7: usage error on a missing --team-dir
# ---------------------------------------------------------------------------


def test_missing_team_dir_is_a_usage_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    exit_code = _check.main(["--team-dir", str(missing)])

    assert exit_code == 2


# ---------------------------------------------------------------------------
# Acceptance criterion 8: the remedy block (docs/coding-standards.md
# "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def test_remedy_block_has_cause_fix_and_rule(tmp_path: Path, capsys) -> None:
    team_dir = tmp_path / "team"
    _minimal_clean_tree(team_dir)
    _write(team_dir / "broken_module.py", "def foo(:\n    pass\n")

    _check.main(["--team-dir", str(team_dir)])
    captured = capsys.readouterr()

    assert "Cause(s):" in captured.out
    assert "To fix:" in captured.out
    assert "Rule:" in captured.out
    # The placeholder law: no unresolved angle-bracket slots in the fix block.
    fix_block = captured.out.split("To fix:", 1)[1].split("Rule:", 1)[0]
    assert "<" not in fix_block and ">" not in fix_block


# ---------------------------------------------------------------------------
# Acceptance criterion 9: the real team/ tree is clean
# ---------------------------------------------------------------------------


def test_real_team_tree_has_zero_breakage_class_findings() -> None:
    """The gate must not false-positive against the actual codebase it
    ships alongside -- this is what proves the gate is safe to wire in as
    the ONLY fatal pre-flight check in both gated runners."""
    exit_code = _check.main(["--team-dir", str(_TEAM_DIR)])

    assert exit_code == 0
