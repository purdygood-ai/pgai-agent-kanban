"""
test_lint_status_writer_bypass.py
===================================
Tests for team/scripts/lint_status_writer_bypass.py — the structural gate
that flags a task's ## State, ## Needs Human, ## Summary, or ## Blockers
field being mutated directly (a bespoke re.sub/re.subn/sed -i) in an
agent-facing script, instead of through
team/pgai_agent_kanban/task_status.py.

Test coverage:

  1. Real-tree green — the shipped default scan surface (team/scripts/*.sh
     and team/pgai_agent_kanban/**/*.py, minus the named exemptions)
     produces zero findings.
  2. A synthetic direct mutation of ## State (the exact shape a bespoke
     re.subn edit would take) is detected.
  3. A read-only reference to a managed heading (re.search, not re.sub) is
     NOT flagged -- reading state to make a decision is not the bypass this
     lint exists to catch.
  4. A brand-new status.md created from a heredoc template (no mutating
     signal present) is NOT flagged -- creating a file is not mutating one.
  5. A comment-only mention of a managed heading is NOT flagged.
  6. An unrelated sed -i elsewhere in the same file, far from any managed
     heading, does NOT co-trigger a finding (window-bounded, not file-wide).
  7. main() return codes: 0 clean, 1 violation, 2 missing scan path / empty
     default scope.
  8. --help exits 0.
  9. Remedy block names the offending file:line and the enforced rule.
  10. The two named exemptions (task-status.sh, unwind-rc.sh) are excluded
      from the default scan even when they exist on disk.
  11. The default scan covers the Python package: a package module holding
      a hand-rolled Blocked Reason / Blockers splice is flagged, and
      main() with no --paths exits 1 on it.
  12. The package's own task_status.py, the sanctioned writer, is left out
      of the default scan; every other package module, nested ones
      included, is in it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_status_writer_bypass.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_status_writer_bypass", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


def _write_sh(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Case 1 — Real tree is clean
# ---------------------------------------------------------------------------


def test_real_tree_default_scope_has_zero_findings() -> None:
    """The shipped default surface (team/scripts/*.sh and
    team/pgai_agent_kanban/**/*.py, minus the named exemptions) produces
    zero findings.

    If this test fails, a direct status.md field mutation was introduced
    into an agent-facing script or a package module — exactly the
    regression class this lint exists to catch.
    """
    scan_targets = _lint._default_scan_targets(_TEAM_DIR)
    assert scan_targets, "expected at least one team/scripts/*.sh file to scan"
    assert any(
        p.suffix == ".py" for p in scan_targets
    ), "expected the default scan to include team/pgai_agent_kanban/ modules"
    violations = _lint.scan_paths(scan_targets)
    assert violations == [], "Unexpected status-writer-bypass finding(s) on the real tree:\n" + "\n".join(
        f"  {v.path}:{v.line} ({v.kind}) {v.snippet}" for v in violations
    )


# ---------------------------------------------------------------------------
# Case 2 — Planted violation is detected
# ---------------------------------------------------------------------------


def test_direct_state_mutation_is_flagged(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "planted.sh",
        r"""
        #!/usr/bin/env bash
        python3 - "$status_file" <<'PY'
        import pathlib, re, sys
        p = pathlib.Path(sys.argv[1])
        text = p.read_text()
        text, n = re.subn(r'(## State\n(?:[ \t]*\n)*)([^\n]+)', r'\1DONE', text)
        p.write_text(text)
        PY
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a direct ## State mutation."
    assert violations[0].path == fixture
    assert "task_status.py" in violations[0].kind


def test_sed_in_place_mutation_of_blockers_is_flagged(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "planted_sed.sh",
        r"""
        #!/usr/bin/env bash
        sed -i "/^## Blockers$/{n;s/.*/Waiting on review/}" "$status_file"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations, "Expected a finding for a sed -i targeting ## Blockers."


# ---------------------------------------------------------------------------
# Case 3 — Read-only reference is NOT flagged
# ---------------------------------------------------------------------------


def test_read_only_state_reference_is_not_flagged(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "reader.sh",
        r"""
        #!/usr/bin/env bash
        python3 - "$status_file" <<'PY'
        import pathlib, re, sys
        text = pathlib.Path(sys.argv[1]).read_text()
        m = re.search(r'## State\n(?:[ \t]*\n)*([^\n]+)', text)
        print(m.group(1) if m else "")
        PY
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], f"Read-only reference incorrectly flagged: {violations}"


# ---------------------------------------------------------------------------
# Case 4 — Brand-new file creation (heredoc template) is NOT flagged
# ---------------------------------------------------------------------------


def test_heredoc_new_file_creation_is_not_flagged(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "creates_new_status.sh",
        r"""
        #!/usr/bin/env bash
        cat > "${TASK_DIR}/status.md" <<EOF
        # Status

        ## State
        BACKLOG

        ## Summary
        none

        ## Blockers
        none
        EOF
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], f"New-file heredoc creation incorrectly flagged: {violations}"


# ---------------------------------------------------------------------------
# Case 5 — Comment-only mention is exempt
# ---------------------------------------------------------------------------


def test_comment_only_mention_is_not_flagged(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "documents_headings.sh",
        r"""
        #!/usr/bin/env bash
        # This script does not touch ## State, ## Summary, or ## Blockers.
        # It also never runs sed -i or re.subn( on anything.
        echo "no real mutation here"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], f"Comment-only mention incorrectly flagged: {violations}"


# ---------------------------------------------------------------------------
# Case 6 — Unrelated sed -i far from a managed heading is not co-triggered
# ---------------------------------------------------------------------------


def test_unrelated_distant_sed_is_not_flagged(tmp_path: Path) -> None:
    filler = "\n".join(f"echo 'line {i}'" for i in range(30))
    fixture = _write_sh(
        tmp_path / "distant.sh",
        f"""
        #!/usr/bin/env bash
        # ## Summary appears here, far from any mutation below.
        {filler}
        sed -i "s/foo/bar/" "$some_other_file"
        """,
    )

    violations = _lint.scan_paths([fixture])

    assert violations == [], f"Distant unrelated sed incorrectly co-triggered: {violations}"


# ---------------------------------------------------------------------------
# Case 7 — main() return codes
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_paths(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "clean.sh",
        r"""
        #!/usr/bin/env bash
        echo "nothing to see here"
        """,
    )
    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 0, f"Expected main() to return 0 on a clean file, got {rc}"


def test_main_returns_one_on_violation(tmp_path: Path) -> None:
    fixture = _write_sh(
        tmp_path / "violator.sh",
        r"""
        #!/usr/bin/env bash
        text, n = re.subn(r'(## Summary\n)([^\n]+)', r'\1new', text)
        """,
    )
    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 1, f"Expected main() to return 1 on a violation, got {rc}"


def test_main_returns_two_on_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.sh"
    rc = _lint.main(["--paths", str(missing)])
    assert rc == 2, f"Expected main() to return 2 for a missing scan path, got {rc}"


def test_main_returns_two_on_empty_default_scope(tmp_path: Path) -> None:
    team_dir = tmp_path / "team"
    (team_dir / "scripts").mkdir(parents=True, exist_ok=True)
    rc = _lint.main(["--team-dir", str(team_dir)])
    assert rc == 2, (
        "Expected main() to return 2 when no team/scripts/*.sh or "
        f"team/pgai_agent_kanban/**/*.py files exist, got {rc}"
    )


# ---------------------------------------------------------------------------
# Case 8 — --help exits 0
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
    assert "lint_status_writer_bypass" in result.stdout


# ---------------------------------------------------------------------------
# Case 9 — Remedy block names the offending location and the rule
# ---------------------------------------------------------------------------


def test_remedy_block_names_file_line_and_rule(tmp_path: Path, capsys) -> None:
    fixture = _write_sh(
        tmp_path / "violator.sh",
        r"""
        #!/usr/bin/env bash
        python3 - "$status_file" <<'PY'
        import re
        text, n = re.subn(r'(## State\n)([^\n]+)', r'\1DONE', text)
        PY
        """,
    )

    rc = _lint.main(["--paths", str(fixture)])
    assert rc == 1

    out = capsys.readouterr().out
    assert "Cause(s):" in out
    assert str(fixture) in out
    assert "task_status.py" in out
    assert "Rule:" in out


# ---------------------------------------------------------------------------
# Case 10 — Named exemptions excluded from the default scan
# ---------------------------------------------------------------------------


def test_default_scan_targets_excludes_named_exemptions(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "task-status.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "unwind-rc.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts_dir / "other.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    targets = _lint._default_scan_targets(tmp_path / "team")
    names = {p.name for p in targets}

    assert "task-status.sh" not in names
    assert "unwind-rc.sh" not in names
    assert "other.sh" in names


# ---------------------------------------------------------------------------
# Case 11 — The default scan covers the Python package
# ---------------------------------------------------------------------------

# A package module that decides where to splice a section by searching for
# ## Blockers itself and then rewrites status.md with re.sub, instead of
# going through task_status.
_PACKAGE_SPLICE = r"""
import re


def mark(text, reason):
    if re.search(r'^## Blocked Reason\s*$', text, flags=re.M):
        return text
    elif re.search(r'^## Blockers\s*$', text, flags=re.M):
        text = re.sub(
            r'(^## Blockers[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
            lambda m: m.group(1) + m.group(2) + "\n\n## Blocked Reason\n" + reason + m.group(3),
            text,
            flags=re.M,
            count=1,
        )
    return text
"""


def _team_with_package_module(tmp_path: Path, relpath: str, content: str) -> Path:
    team_dir = tmp_path / "team"
    _write_sh(team_dir / "scripts" / "clean.sh", "#!/usr/bin/env bash\necho clean\n")
    _write_sh(team_dir / "pgai_agent_kanban" / relpath, content)
    return team_dir


def test_package_module_splice_is_flagged_by_default_scan(tmp_path: Path, capsys) -> None:
    team_dir = _team_with_package_module(tmp_path, "overwatch/wont_do_splice.py", _PACKAGE_SPLICE)
    module = team_dir / "pgai_agent_kanban" / "overwatch" / "wont_do_splice.py"

    violations = _lint.scan_paths(_lint._default_scan_targets(team_dir))

    assert violations, "expected the package module's splice to be flagged"
    assert {v.path for v in violations} == {module}
    assert r"elif re.search(r'^## Blockers\s*$', text, flags=re.M):" in [v.snippet for v in violations]
    assert _lint.main(["--team-dir", str(team_dir)]) == 1
    assert f"{module}:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Case 12 — The package's task_status.py is the sanctioned writer
# ---------------------------------------------------------------------------


def test_default_scan_targets_package_modules_except_task_status(tmp_path: Path) -> None:
    team_dir = _team_with_package_module(tmp_path, "task_status.py", _PACKAGE_SPLICE)
    _write_sh(team_dir / "pgai_agent_kanban" / "ops" / "deep" / "nested.py", "X = 1\n")
    package_dir = team_dir / "pgai_agent_kanban"

    targets = _lint._default_scan_targets(team_dir)
    package_targets = {p.relative_to(package_dir).as_posix() for p in targets if p.suffix == ".py"}

    assert package_targets == {"ops/deep/nested.py"}
    assert _lint.main(["--team-dir", str(team_dir)]) == 0
