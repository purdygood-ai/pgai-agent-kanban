"""
test_lint_runnable_remedy.py
==============================
Tests for team/scripts/lint_runnable_remedy.py -- the quality-class lint
that flags dual-channel error-contract call sites whose REMEDY field
still carries an angle-bracket placeholder ('<name>', '<sha>', ...) a
stranger cannot paste into a shell and run as printed.

Tests cover the core acceptance criteria:

  1. **A planted '<name>' in a remedy is caught, naming file/line/
     substring** -- both a bash call and a Python call (plain string and
     f-string literal chunks) are surfaced as findings; reverting the
     plant makes the same fixture clean.

  2. **Only the remedy argument is scanned** -- a planted '<name>' in the
     message field of the same call produces no finding.

  3. **The real repository tree, after the sweep, produces zero
     findings** -- a live regression guard proving every call site this
     ticket converted (and every other call site already in the tree)
     hands the reader a remedy with no template slot left in it.

  4. **defer.sh and halt-after.sh each emit a refusal whose remedy is a
     literal runnable command with no angle brackets** -- proven by
     invoking each script's refusal path via subprocess and asserting
     the stderr remedy line contains no '<...>' substring.

Additional coverage: a remedy built entirely from a resolved bash
variable or Python f-string expression (no literal placeholder text)
produces no finding; dynamic/unparseable call shapes are skipped rather
than guessed at; the findings-file JSON carries the expected keys; the
lint's own exit code is 1 on a finding and 0 on a clean scan (its own
process exit code -- the CONVERT-class non-fatal posture is applied by
the run-unit-tests.sh wrapper, not by this script itself).

All tests use pytest's ``tmp_path`` for synthetic fixtures -- no bare
/tmp paths, no live kanban state mutations. The subprocess tests
(acceptance criterion 4) point PGAI_AGENT_KANBAN_ROOT_PATH at a
throwaway tmp_path root and unset PGAI_PROJECT_NAME, matching this
repository's Live-Install Safety convention (roles/CODER.md) so no
script invoked here can ever touch the operator's real kanban root.

Naming: function names describe behavior under test, never bug IDs or
task IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_runnable_remedy.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_runnable_remedy.py"
_DEFER_SH = _SCRIPTS_DIR / "defer.sh"
_HALT_AFTER_SH = _SCRIPTS_DIR / "halt-after.sh"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_runnable_remedy", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec: the lint module uses @dataclass,
    # and dataclasses.dataclass() resolves its own module via sys.modules
    # while processing fields -- an unregistered module (the default state
    # for a bare importlib.util.module_from_spec load) makes that lookup
    # return None and crash instead of importing.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_bash_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _write_python_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _empty_python_dir(tmp_path: Path) -> Path:
    empty = tmp_path / "empty_python_root"
    empty.mkdir()
    return empty


def _empty_bash_dir(tmp_path: Path) -> Path:
    empty = tmp_path / "empty_bash_root"
    empty.mkdir()
    return empty


# ---------------------------------------------------------------------------
# Acceptance criterion: a planted placeholder is caught, naming
# file/line/substring; reverting makes it green.
# ---------------------------------------------------------------------------


def test_bash_remedy_with_planted_placeholder_is_reported(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_planted.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "something stopped" \\\n'
        '  "Re-run with --project <name>."\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_planted.sh")
    assert len(findings) == 1
    assert findings[0].placeholders == ["<name>"]
    assert findings[0].function == "pgai_emit_error_contract"

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 1, "a planted placeholder must make the lint's own exit code non-zero"


def test_bash_remedy_placeholder_reverted_is_clean(tmp_path: Path) -> None:
    """The same fixture, with the placeholder replaced by a literal value,
    produces zero findings and a zero exit code -- 'reverting makes it
    green' from the acceptance criteria."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_reverted.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "something stopped" \\\n'
        '  "Re-run with --project pgai-agent-kanban."\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_reverted.sh")
    assert findings == []

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 0


def test_python_remedy_with_planted_placeholder_in_plain_string_is_reported(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_planted.py",
        "from pgai_agent_kanban.lib.error_contract import format_error_contract\n\n"
        "def fail():\n"
        '    format_error_contract("gate.example", "stopped", "add <color-name> to the file")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_planted.py")
    assert len(findings) == 1
    assert findings[0].placeholders == ["<color-name>"]


def test_python_remedy_with_planted_placeholder_in_fstring_literal_chunk_is_reported(tmp_path: Path) -> None:
    """An f-string's literal text (not its interpolated {expr} parts) is
    scanned -- a hand-typed '<sha>' inside an f-string is just as
    unrunnable as one in a plain string literal."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_fstring_planted.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail(path):\n"
        '    emit_error_contract("gate.example", "stopped", f"edit {path}:12 -- see <sha> above")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_fstring_planted.py")
    assert len(findings) == 1
    assert findings[0].placeholders == ["<sha>"]


def test_python_remedy_built_entirely_from_fstring_expression_is_not_a_finding(tmp_path: Path) -> None:
    """A remedy whose only content is an already-resolved f-string
    expression (no literal '<...>' text typed by the author) is not
    flagged, even though the expression could theoretically resolve to
    a surprising value at runtime -- this lint is a static, literal-text
    check, matching lint_dual_channel_pair.py's own posture."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_resolved_fstring.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail(project):\n"
        '    emit_error_contract("gate.example", "stopped", f"re-run with --project {project}")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_resolved_fstring.py")
    assert findings == []


def test_bash_remedy_built_entirely_from_variable_is_not_a_finding(tmp_path: Path) -> None:
    """A bash remedy token built from an unexpanded shell variable never
    renders as literal '<...>' text -- only hand-typed template text is
    ever flagged."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_variable_remedy.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "something stopped" \\\n'
        '  "Re-run with --project ${_project} --until ${_until}."\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_variable_remedy.sh")
    assert findings == []


# ---------------------------------------------------------------------------
# Acceptance criterion: only the remedy argument is scanned -- a planted
# '<name>' in the message field produces no finding.
# ---------------------------------------------------------------------------


def test_placeholder_in_message_field_produces_no_finding(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_message_placeholder.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "no project named <name> was found" \\\n'
        '  "Re-run with --project pgai-agent-kanban."\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_message_placeholder.sh")
    assert findings == [], "a placeholder in the message field must not be a remedy finding"


def test_placeholder_in_python_message_field_produces_no_finding(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_message_placeholder.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail():\n"
        '    emit_error_contract("gate.example", "no project named <name>", "run team/scripts/defer.sh --project pgai-agent-kanban")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_message_placeholder.py")
    assert findings == []


# ---------------------------------------------------------------------------
# Dynamic / unparseable call shapes are skipped, not guessed at
# ---------------------------------------------------------------------------


def test_python_kwargs_spread_call_is_skipped(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_kwargs_spread.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail(fields):\n"
        "    emit_error_contract(**fields)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_kwargs_spread.py")
    assert findings == []


def test_python_syntax_error_file_is_skipped_not_crashed(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(python_dir, "fixture_broken.py", "def broken(:\n    pass\n")

    findings = _lint._scan_python_file(python_dir / "fixture_broken.py")
    assert findings == []


def test_comment_mentioning_function_name_is_not_a_call_site(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_comment_only.sh",
        "#!/usr/bin/env bash\n"
        "# Call pgai_emit_error_contract with a remedy like '<name>'.\n"
        "echo done\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_comment_only.sh")
    assert findings == []


# ---------------------------------------------------------------------------
# Findings-file JSON shape
# ---------------------------------------------------------------------------


def test_findings_file_contains_machine_readable_json(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_planted.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "something stopped" \\\n'
        '  "Re-run with --project <name>."\n',
    )
    findings_file = tmp_path / "findings" / "runnable_remedy.json"

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
        "--findings-file", str(findings_file),
    ])

    assert result == 1
    assert findings_file.is_file()

    data = json.loads(findings_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    finding = data[0]
    assert finding["lint"] == "runnable_remedy"
    assert finding["severity"] == "quality"
    assert finding["placeholders"] == ["<name>"]
    assert finding["function"] == "pgai_emit_error_contract"
    assert "fixture_planted.sh" in finding["file"]
    assert finding["line"] == 2
    assert "<name>" in finding["cause"]
    assert finding["remedy"], "remedy field must be a non-empty prescriptive fix string"


def test_findings_file_is_empty_array_on_clean_tree(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_ok.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "message text" "run team/scripts/defer.sh --project pgai-agent-kanban --until v2.20.0"\n',
    )
    findings_file = tmp_path / "findings.json"

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
        "--findings-file", str(findings_file),
    ])

    assert result == 0
    data = json.loads(findings_file.read_text(encoding="utf-8"))
    assert data == []


# ---------------------------------------------------------------------------
# Real-tree sanity check -- acceptance criterion: running the lint against
# the tree after the sweep exits 0 with no findings.
# ---------------------------------------------------------------------------


def test_real_tree_produces_zero_findings_after_the_sweep(capsys) -> None:
    """Every dual-channel remedy in this codebase is a literal runnable
    command with no angle-bracket placeholder. A regression here means a
    later edit reintroduced a template slot into a remedy field."""
    result = _lint.main([])
    assert result == 0, "the lint must exit 0 once every remedy in the tree is runnable"

    captured = capsys.readouterr()
    assert "0 offenders" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    assert json.loads(json_block) == []


# ---------------------------------------------------------------------------
# Acceptance criterion: defer.sh and halt-after.sh each emit at least one
# refusal whose remedy is a literal runnable command with no angle
# brackets, demonstrated by invoking the refusal path in a test.
# ---------------------------------------------------------------------------


def _isolated_env(kanban_root: Path) -> dict[str, str]:
    """An environment repointed at a throwaway kanban root, with
    PGAI_PROJECT_NAME unset -- Live-Install Safety (roles/CODER.md):
    a script invoked in a test must never be able to resolve back to
    the operator's real kanban root or a real project."""
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    return env


def test_defer_sh_project_required_refusal_remedy_has_no_angle_brackets(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(_DEFER_SH), "--until", "v2.20.0"],
        capture_output=True,
        text=True,
        env=_isolated_env(tmp_path),
        timeout=15,
    )

    assert result.returncode != 0
    assert "remedy:" in result.stderr
    remedy_line = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    assert "<" not in remedy_line and ">" not in remedy_line, (
        f"defer.sh's --project-required remedy must be a literal runnable "
        f"command with no template slot, got: {remedy_line!r}"
    )
    assert "--project" in remedy_line


def test_halt_after_sh_project_required_refusal_remedy_has_no_angle_brackets(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(_HALT_AFTER_SH)],
        capture_output=True,
        text=True,
        env=_isolated_env(tmp_path),
        timeout=15,
    )

    assert result.returncode != 0
    assert "remedy:" in result.stderr
    remedy_line = next(line for line in result.stderr.splitlines() if line.startswith("remedy:"))
    assert "<" not in remedy_line and ">" not in remedy_line, (
        f"halt-after.sh's --project-required remedy must be a literal "
        f"runnable command with no template slot, got: {remedy_line!r}"
    )
    assert "--project" in remedy_line
