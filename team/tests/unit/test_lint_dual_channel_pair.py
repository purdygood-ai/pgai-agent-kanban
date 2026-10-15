"""
test_lint_dual_channel_pair.py
===============================
Tests for team/scripts/lint_dual_channel_pair.py -- the quality-class lint
that flags dual-channel error-contract call sites missing the error,
message, or remedy field.

Tests cover the core acceptance criteria:

  1. **A call site missing the remedy field is reported as a finding** --
     both a bash call supplying only two of three arguments and a Python
     call supplying only two of three arguments are surfaced as findings,
     each naming the missing field.

  2. **The lint never returns a fatal (non-zero) exit code** -- proven both
     on a clean tree (no findings) and on a tree with several findings; the
     lint always returns 0 either way.

  3. **Findings are emitted in the machine-readable findings format** --
     ``--findings-file`` writes a JSON array to disk; each entry carries the
     file, line, function, missing_fields, cause, and remedy keys.

Additional coverage: blank string literals (not just missing arguments) are
flagged; a fully-supplied call produces zero findings; dynamic call shapes
(``**kwargs`` spreading, unparseable bash statements) are skipped rather
than guessed at; a comment that merely mentions the function name is not
mistaken for a call site; and the real repository tree (post-conversion)
produces zero findings, proving the scanner matches the shared emitter's
actual call-site shapes.

All tests use pytest's ``tmp_path`` for synthetic fixtures -- no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_dual_channel_pair.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_dual_channel_pair.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_dual_channel_pair", _LINT_SCRIPT)
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
    """A python scan root with no .py files, so bash-only fixtures produce
    zero python findings alongside the bash findings under test."""
    empty = tmp_path / "empty_python_root"
    empty.mkdir()
    return empty


def _empty_bash_dir(tmp_path: Path) -> Path:
    empty = tmp_path / "empty_bash_root"
    empty.mkdir()
    return empty


# ---------------------------------------------------------------------------
# Acceptance criterion 1: missing remedy field is reported as a finding
# ---------------------------------------------------------------------------


def test_bash_call_missing_remedy_is_reported(tmp_path: Path) -> None:
    """A bash call supplying only error and message is flagged, naming remedy."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_two_args.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "something stopped"\n',
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    assert result == 0, "quality-class lint must never return non-zero"

    findings = _lint._scan_bash_file(bash_dir / "fixture_two_args.sh")
    assert len(findings) == 1
    assert findings[0].affected_fields == ["remedy"]


def test_python_call_missing_remedy_is_reported(tmp_path: Path) -> None:
    """A Python call supplying only error and message is flagged, naming remedy."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_two_args.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail():\n"
        '    emit_error_contract("gate.example", "something stopped")\n',
    )

    result = _lint.main([
        "--bash-dirs", str(_empty_bash_dir(tmp_path)),
        "--python-dir", str(python_dir),
    ])

    assert result == 0

    findings = _lint._scan_python_file(python_dir / "fixture_two_args.py")
    assert len(findings) == 1
    assert findings[0].affected_fields == ["remedy"]


def test_python_call_missing_remedy_via_keyword_args_still_reported(tmp_path: Path) -> None:
    """error/message supplied by keyword, remedy omitted entirely -- still flagged."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_kwargs.py",
        "from pgai_agent_kanban.lib.error_contract import format_error_contract\n\n"
        "def fail():\n"
        '    format_error_contract(error="gate.example", message="stopped")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_kwargs.py")
    assert len(findings) == 1
    assert findings[0].affected_fields == ["remedy"]


# ---------------------------------------------------------------------------
# Blank literal fields (supplied but empty) are also findings
# ---------------------------------------------------------------------------


def test_bash_call_with_blank_remedy_literal_is_reported(tmp_path: Path) -> None:
    """A bash call supplying an empty string literal for remedy is flagged."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_blank_remedy.sh",
        '#!/usr/bin/env bash\n'
        'pgai_format_error_contract "gate.example" "something stopped" ""\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_blank_remedy.sh")
    assert len(findings) == 1
    assert findings[0].affected_fields == ["remedy"]


def test_python_call_with_blank_message_literal_is_reported(tmp_path: Path) -> None:
    """A Python call supplying an empty string literal for message is flagged."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_blank_message.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail():\n"
        '    emit_error_contract("gate.example", "   ", "run the remedy command")\n',
    )

    findings = _lint._scan_python_file(python_dir / "fixture_blank_message.py")
    assert len(findings) == 1
    assert findings[0].affected_fields == ["message"]


# ---------------------------------------------------------------------------
# Fully-supplied call sites produce zero findings
# ---------------------------------------------------------------------------


def test_bash_call_with_all_three_fields_produces_no_finding(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_complete.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract \\\n'
        '  "gate.example" \\\n'
        '  "something stopped and why" \\\n'
        '  "run the fix command"\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_complete.sh")
    assert findings == []


def test_python_call_with_all_three_fields_produces_no_finding(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_complete.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail(error_key, message, remedy):\n"
        "    emit_error_contract(error_key, message, remedy)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_complete.py")
    assert findings == []


# ---------------------------------------------------------------------------
# Dynamic / unparseable call shapes are skipped, not guessed at
# ---------------------------------------------------------------------------


def test_python_kwargs_spread_call_is_skipped(tmp_path: Path) -> None:
    """A call spreading **kwargs cannot be statically resolved -- skip it."""
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
    """A file this lint cannot parse is silently skipped -- breakage-class
    lints catch syntax errors elsewhere; this lint must not crash on them."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(python_dir, "fixture_broken.py", "def broken(:\n    pass\n")

    findings = _lint._scan_python_file(python_dir / "fixture_broken.py")
    assert findings == []


def test_comment_mentioning_function_name_is_not_a_call_site(tmp_path: Path) -> None:
    """A bash comment that merely names the function is not treated as a call."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_comment_only.sh",
        "#!/usr/bin/env bash\n"
        "# Call pgai_emit_error_contract with the key, message, and remedy.\n"
        "echo done\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_comment_only.sh")
    assert findings == []


# ---------------------------------------------------------------------------
# Availability-probe operands are not call sites (guarded self-source idiom)
# ---------------------------------------------------------------------------


def test_command_v_probe_guard_is_not_a_call_site(tmp_path: Path) -> None:
    """The idiomatic guarded self-source -- 'if ! command -v <emitter>
    >/dev/null 2>&1; then source ...; fi' -- names the emitter only as the
    operand of an availability probe. It must not be reported as a call
    missing its fields."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_probe_guard.sh",
        "#!/usr/bin/env bash\n"
        "if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then\n"
        '  source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"\n'
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_probe_guard.sh")
    assert findings == []

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 0


def test_type_probe_guard_is_not_a_call_site(tmp_path: Path) -> None:
    """'type <emitter>' is an availability probe, same family as 'command -v'."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_type_guard.sh",
        "#!/usr/bin/env bash\n"
        "if ! type pgai_emit_error_contract >/dev/null 2>&1; then\n"
        '  source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"\n'
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_type_guard.sh")
    assert findings == []


def test_type_dash_t_probe_guard_is_not_a_call_site(tmp_path: Path) -> None:
    """'type -t <emitter>' is an availability probe, same family as 'command -v'."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_type_t_guard.sh",
        "#!/usr/bin/env bash\n"
        'if [[ "$(type -t pgai_emit_error_contract)" != "function" ]]; then\n'
        '  source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"\n'
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_type_t_guard.sh")
    assert findings == []


def test_declare_dash_f_probe_guard_is_not_a_call_site(tmp_path: Path) -> None:
    """'declare -F <emitter>' is an availability probe, same family as 'command -v'."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_declare_f_guard.sh",
        "#!/usr/bin/env bash\n"
        "if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then\n"
        '  source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"\n'
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_declare_f_guard.sh")
    assert findings == []


def test_probe_guard_alongside_real_malformed_call_reports_only_the_call(
    tmp_path: Path,
) -> None:
    """A file carrying both the guarded self-source idiom and a genuinely
    malformed call must still report exactly one finding -- the malformed
    call's line -- and not the probe's line."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_probe_and_bad_call.sh",
        "#!/usr/bin/env bash\n"
        "if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then\n"
        '  source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"\n'
        "fi\n"
        'pgai_emit_error_contract "gate.example" "something stopped"\n',
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_probe_and_bad_call.sh")
    assert len(findings) == 1
    assert findings[0].line == 5
    assert findings[0].affected_fields == ["remedy"]


# ---------------------------------------------------------------------------
# Acceptance criterion 2: the lint never returns a fatal exit code
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_tree(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_ok.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "message text" "remedy text"\n',
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 0


def test_main_returns_zero_even_with_multiple_findings(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_bad_1.sh",
        '#!/usr/bin/env bash\npgai_emit_error_contract "gate.one"\n',
    )
    _write_bash_file(
        bash_dir,
        "fixture_bad_2.sh",
        '#!/usr/bin/env bash\npgai_format_error_contract "gate.two" "" ""\n',
    )
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_bad_3.py",
        "from pgai_agent_kanban.lib.error_contract import format_error_contract\n\n"
        "def fail():\n"
        '    format_error_contract("gate.three", "msg only")\n',
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(python_dir),
    ])

    assert result == 0, (
        "lint_dual_channel_pair.main() must return 0 regardless of finding "
        "count -- this is a quality-class lint that never blocks."
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 3: findings are emitted in machine-readable format
# ---------------------------------------------------------------------------


def test_findings_file_contains_machine_readable_json(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_missing_remedy.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "something stopped and why"\n',
    )
    findings_file = tmp_path / "findings" / "dual_channel_pair.json"

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
        "--findings-file", str(findings_file),
    ])

    assert result == 0
    assert findings_file.is_file(), "findings file must be written when --findings-file is given"

    data = json.loads(findings_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    finding = data[0]
    assert finding["lint"] == "dual_channel_pair"
    assert finding["missing_fields"] == ["remedy"]
    assert finding["function"] == "pgai_emit_error_contract"
    assert "fixture_missing_remedy.sh" in finding["file"]
    assert finding["line"] == 2
    assert "remedy" in finding["cause"]
    assert finding["remedy"], "remedy field must be a non-empty prescriptive fix string"


def test_findings_file_is_empty_array_on_clean_tree(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_ok.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "message text" "remedy text"\n',
    )
    findings_file = tmp_path / "findings.json"

    _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
        "--findings-file", str(findings_file),
    ])

    data = json.loads(findings_file.read_text(encoding="utf-8"))
    assert data == []


def test_findings_json_printed_to_stdout(tmp_path: Path, capsys) -> None:
    """Even without --findings-file, the JSON representation is on stdout."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_missing_remedy.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "something stopped"\n',
    )

    _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    captured = capsys.readouterr()
    assert "FINDINGS_JSON:" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)
    assert len(data) == 1
    assert data[0]["missing_fields"] == ["remedy"]


# ---------------------------------------------------------------------------
# Remedy block shape (docs/coding-standards.md "Gates prescribe, not just
# diagnose") -- cause with concrete location, a fix example, the rule.
# ---------------------------------------------------------------------------


def test_remedy_block_names_location_and_rule(tmp_path: Path, capsys) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_missing_remedy.sh",
        '#!/usr/bin/env bash\n'
        'pgai_emit_error_contract "gate.example" "something stopped"\n',
    )

    _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    captured = capsys.readouterr()
    assert "fixture_missing_remedy.sh:2" in captured.out
    assert "Cause(s):" in captured.out
    assert "To fix" in captured.out
    assert "Rule:" in captured.out


# ---------------------------------------------------------------------------
# Real-tree sanity check
# ---------------------------------------------------------------------------


def test_real_tree_produces_zero_findings(capsys) -> None:
    """Every dual-channel call site already converted in this codebase
    supplies all three fields. A regression here means a call site lost a
    field in a later edit."""
    # Deliberately exercises the real tree via the lint's default scan
    # roots (not a fixture): this is a live regression guard proving every
    # already-converted call site still supplies all three fields.
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    assert "0 offenders" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    assert json.loads(json_block) == []
