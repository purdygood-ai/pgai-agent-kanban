"""
test_lint_gate_census_compliance.py
=====================================
Tests for team/scripts/lint_gate_census_compliance.py -- the quality-class
lint that enforces the new-gate rule: every gate/refusal site must declare
its census classification in a code comment AND carry the dual-channel
error contract.

Tests cover the core acceptance criteria:

  1. **A gate missing its census-classification comment is detected** --
     both a bash ``exit 1`` and a Python ``sys.exit(1)`` site that call the
     dual-channel emitter but carry no classification comment are surfaced
     as findings naming the missing ``census_classification`` signal.

  2. **A gate missing the dual-channel error contract is detected** -- both
     a bash and a Python site that carry a classification comment but never
     call the emitter are surfaced as findings naming the missing
     ``dual_channel_contract`` signal.

  3. **The lint warns and files rather than exiting non-zero** -- proven on
     both a clean tree (no findings) and a tree with several findings; the
     lint always returns 0 either way, and findings are still emitted as
     machine-readable JSON.

Additional coverage: a fully-compliant gate (comment + emitter call, either
order, on nearby lines) produces zero findings; a plain non-gate exit (e.g.
``exit 0``, or a `return 1` that this lint does not treat as a candidate
site) produces no finding; the emitter's own definition files are excluded
from the scan; and the real repository tree runs to completion without
crashing.

All tests use pytest's ``tmp_path`` for synthetic fixtures -- no bare /tmp
paths, no live kanban state mutations.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load lint_gate_census_compliance.py as a module.
# Path: team/tests/unit/ -> team/ (two levels up) -> team/scripts/
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_gate_census_compliance.py"


def _import_lint_module():
    spec = importlib.util.spec_from_file_location("lint_gate_census_compliance", _LINT_SCRIPT)
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
# Acceptance criterion 1: a gate missing its census-classification comment
# is detected
# ---------------------------------------------------------------------------


def test_bash_gate_missing_classification_comment_is_reported(tmp_path: Path) -> None:
    """A bash refusal that calls the emitter but has no classification
    comment nearby is flagged for the missing census_classification signal."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_no_class.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_no_class.sh")
    assert len(findings) == 1
    assert findings[0].missing_signals == ["census_classification"]


def test_python_gate_missing_classification_comment_is_reported(tmp_path: Path) -> None:
    """A Python refusal that calls the emitter but has no classification
    comment nearby is flagged for the missing census_classification signal."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_no_class.py",
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n"
        "import sys\n\n"
        "def fail():\n"
        '    emit_error_contract("gate.example", "something stopped", "run the fix")\n'
        "    sys.exit(1)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_no_class.py")
    assert len(findings) == 1
    assert findings[0].missing_signals == ["census_classification"]


def test_main_detects_missing_classification_and_returns_zero(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_no_class.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    assert result == 0, "quality-class lint must never return non-zero"


# ---------------------------------------------------------------------------
# Acceptance criterion 2: a gate missing the dual-channel error contract is
# detected
# ---------------------------------------------------------------------------


def test_bash_gate_missing_dual_channel_contract_is_reported(tmp_path: Path) -> None:
    """A bash refusal that declares its class but never calls the emitter
    is flagged for the missing dual_channel_contract signal."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_no_emit.sh",
        "#!/usr/bin/env bash\n"
        "# BLOCK-WORTHY: the release-integrity invariant this guard protects\n"
        'echo "refusing: something is wrong" >&2\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_no_emit.sh")
    assert len(findings) == 1
    assert findings[0].missing_signals == ["dual_channel_contract"]


def test_python_gate_missing_dual_channel_contract_is_reported(tmp_path: Path) -> None:
    """A Python refusal that declares its class but never calls the emitter
    is flagged for the missing dual_channel_contract signal."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_no_emit.py",
        "import sys\n\n"
        "def fail():\n"
        "    # CONVERT: quality-class finding, zero runtime-failure risk\n"
        '    print("refusing: something is wrong", file=sys.stderr)\n'
        "    sys.exit(1)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_no_emit.py")
    assert len(findings) == 1
    assert findings[0].missing_signals == ["dual_channel_contract"]


def test_main_detects_missing_dual_channel_and_returns_zero(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_no_emit.py",
        "import sys\n\n"
        "def fail():\n"
        "    # AGENT-RESOLVABLE: self-serviceable in the same session\n"
        "    sys.exit(1)\n",
    )

    result = _lint.main([
        "--bash-dirs", str(_empty_bash_dir(tmp_path)),
        "--python-dir", str(python_dir),
    ])

    assert result == 0, "quality-class lint must never return non-zero"


def test_gate_missing_both_signals_reports_both(tmp_path: Path) -> None:
    """A refusal with neither a classification comment nor an emitter call
    is flagged for both missing signals, in fixed signal order."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_bare.sh",
        "#!/usr/bin/env bash\n"
        'echo "nope" >&2\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_bare.sh")
    assert len(findings) == 1
    assert findings[0].missing_signals == ["census_classification", "dual_channel_contract"]


# ---------------------------------------------------------------------------
# Fully-compliant gates produce zero findings
# ---------------------------------------------------------------------------


def test_bash_gate_with_both_signals_produces_no_finding(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_compliant.sh",
        "#!/usr/bin/env bash\n"
        "# BLOCK-WORTHY: destructive-path, the release state cannot be trusted\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_compliant.sh")
    assert findings == []


def test_python_gate_with_both_signals_produces_no_finding(tmp_path: Path) -> None:
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_compliant.py",
        "import sys\n"
        "from pgai_agent_kanban.lib.error_contract import emit_error_contract\n\n"
        "def fail():\n"
        "    # CONVERT: quality-class finding, zero runtime-failure risk\n"
        '    emit_error_contract("gate.example", "something stopped", "run the fix")\n'
        "    sys.exit(1)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_compliant.py")
    assert findings == []


def test_signal_order_is_stable_regardless_of_line_order(tmp_path: Path) -> None:
    """The classification comment may appear after the emitter call (both
    still count, as long as both are within the backward window)."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_reordered.sh",
        "#!/usr/bin/env bash\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "# AGENT-RESOLVABLE: fatal to the done-claim, self-serviceable in-session\n"
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_reordered.sh")
    assert findings == []


# ---------------------------------------------------------------------------
# Non-gate exits are not candidate sites
# ---------------------------------------------------------------------------


def test_success_exit_zero_is_not_a_candidate_site(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_exit_zero.sh",
        "#!/usr/bin/env bash\necho ok\nexit 0\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_exit_zero.sh")
    assert findings == []


def test_commented_out_exit_is_not_a_candidate_site(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_commented_exit.sh",
        "#!/usr/bin/env bash\n# example: exit 1 if the check fails\necho ok\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_commented_exit.sh")
    assert findings == []


# ---------------------------------------------------------------------------
# File-level exclusions
# ---------------------------------------------------------------------------


def test_emitter_definition_file_is_excluded_from_bash_scan(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "error_contract.sh",
        "#!/usr/bin/env bash\necho done\nexit 1\n",
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 0

    files = _lint._iter_bash_files([bash_dir])
    assert files == []


# ---------------------------------------------------------------------------
# Acceptance criterion 3: warn+file, never a fatal exit
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_clean_tree(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_ok.sh",
        "#!/usr/bin/env bash\necho ok\nexit 0\n",
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])
    assert result == 0


def test_main_returns_zero_even_with_multiple_findings(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(bash_dir, "fixture_bad_1.sh", "#!/usr/bin/env bash\nexit 1\n")
    _write_bash_file(bash_dir, "fixture_bad_2.sh", "#!/usr/bin/env bash\nexit 2\n")
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_bad_3.py",
        "import sys\n\ndef fail():\n    sys.exit(1)\n",
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(python_dir),
    ])

    assert result == 0, (
        "lint_gate_census_compliance.main() must return 0 regardless of "
        "finding count -- this is a quality-class lint that never blocks."
    )


def test_findings_file_contains_machine_readable_json(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_missing_both.sh",
        "#!/usr/bin/env bash\nexit 1\n",
    )
    findings_file = tmp_path / "findings" / "gate_census_compliance.json"

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
    assert finding["lint"] == "gate_census_compliance"
    assert finding["severity"] == "quality"
    assert finding["missing_signals"] == ["census_classification", "dual_channel_contract"]
    assert "fixture_missing_both.sh" in finding["file"]
    assert finding["line"] == 2
    assert finding["remedy"], "remedy field must be a non-empty prescriptive fix string"


def test_findings_file_is_empty_array_on_clean_tree(tmp_path: Path) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(bash_dir, "fixture_ok.sh", "#!/usr/bin/env bash\nexit 0\n")
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
    _write_bash_file(bash_dir, "fixture_missing_both.sh", "#!/usr/bin/env bash\nexit 1\n")

    _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    captured = capsys.readouterr()
    assert "FINDINGS_JSON:" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)
    assert len(data) == 1


# ---------------------------------------------------------------------------
# Remedy block shape (docs/coding-standards.md "Gates prescribe, not just
# diagnose") -- cause with concrete location, a fix example, the rule.
# ---------------------------------------------------------------------------


def test_remedy_block_names_location_and_rule(tmp_path: Path, capsys) -> None:
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(bash_dir, "fixture_missing_both.sh", "#!/usr/bin/env bash\nexit 1\n")

    _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    captured = capsys.readouterr()
    assert "fixture_missing_both.sh:2" in captured.out
    assert "Cause(s):" in captured.out
    assert "To fix" in captured.out
    assert "Rule:" in captured.out


# ---------------------------------------------------------------------------
# Real-tree sanity check
# ---------------------------------------------------------------------------


def test_real_tree_scan_runs_to_completion(capsys) -> None:
    """The lint runs against the real repository tree without crashing and
    always returns 0, regardless of how many existing sites have not yet
    been annotated with a census classification comment (retroactively
    annotating the existing gate fleet is separate follow-on work; this
    lint enforces the rule for gates added going forward)."""
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    assert "FINDINGS_JSON:" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)
    assert isinstance(data, list)
    for finding in data:
        assert finding["lint"] == "gate_census_compliance"
        assert finding["missing_signals"], "a finding must name at least one missing signal"


# ---------------------------------------------------------------------------
# Scoping behavior: the default scan surface is the census's own documented
# loop-bearing file list, not every *.sh in team/scripts/. Pure operator-CLI
# argument/usage utilities the census documents as out of scope (show.sh,
# close.sh, wontdo.sh, and the like) must never appear in a default-scope
# finding, on the real tree or on a synthetic fixture tree shaped the same
# way.
# ---------------------------------------------------------------------------


def test_default_bash_files_excludes_documented_operator_clis() -> None:
    """The census-aligned default file list never resolves to a pure
    operator-CLI argument/usage utility -- these are absent from
    _CENSUS_BASH_FILES by construction, not filtered out at scan time."""
    script_dir = _SCRIPTS_DIR
    resolved = {p.name for p in _lint._default_bash_files(script_dir)}

    operator_cli_basenames = {"wontdo.sh", "show.sh", "close.sh", "delete.sh"}
    assert resolved.isdisjoint(operator_cli_basenames), (
        f"default bash scan resolved {resolved & operator_cli_basenames} -- "
        "these are documented as out of scope in docs/blocking-site-census.md"
    )


def test_default_bash_files_includes_documented_loop_bearing_sources() -> None:
    """The census-aligned default file list does include the loop-bearing
    sources the census actually tracks (a sample spanning several rows)."""
    script_dir = _SCRIPTS_DIR
    resolved = {str(p.relative_to(script_dir)) for p in _lint._default_bash_files(script_dir)}

    expected_sample = {
        "wake-batch.sh",
        "run-unit-tests.sh",
        "cm/release.sh",
        "lib/discovery.sh",
        "lib/wake_common.sh",
    }
    assert expected_sample <= resolved


def test_default_python_files_includes_documented_lint_fleet() -> None:
    """The census-aligned default python file list includes the CONVERT-class
    lint fleet the census rollup table names (rows 31-44), scoped to
    team/scripts/ rather than the wider team/pgai_agent_kanban/ tree."""
    script_dir = _SCRIPTS_DIR
    resolved = {str(p.relative_to(script_dir)) for p in _lint._default_python_files(script_dir)}

    expected_sample = {
        "check_breakage_class.py",
        "lint_changelog_freshness.py",
        "lint_help_presence.py",
        "lint_dual_channel_pair.py",
    }
    assert expected_sample <= resolved


def test_real_tree_default_scan_reports_zero_findings_for_operator_clis(capsys) -> None:
    """On the real (clean) tree, the default-scope run never surfaces a
    finding whose file is one of the documented out-of-scope operator-CLI
    utilities."""
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)

    out_of_scope_basenames = {"wontdo.sh", "show.sh", "close.sh", "delete.sh"}
    offending = [f for f in data if Path(f["file"]).name in out_of_scope_basenames]
    assert offending == [], (
        f"default scan produced findings for out-of-scope operator-CLI file(s): {offending}"
    )


def test_bash_dirs_override_still_scans_a_directory(tmp_path: Path) -> None:
    """Passing --bash-dirs explicitly still switches to the pre-existing
    directory-sweep behavior (used by every fixture-based test above), so a
    file named like an excluded operator CLI is scanned when the caller
    deliberately overrides the default scope."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(bash_dir, "wontdo.sh", "#!/usr/bin/env bash\nexit 1\n")

    findings = _lint._iter_bash_files([bash_dir])
    assert [f.name for f in findings] == ["wontdo.sh"]


# ---------------------------------------------------------------------------
# BUG-0052: SITE-TYPE scoping -- operator-CLI argument/usage refusals inside
# otherwise in-scope files (e.g. release.sh's own --project flag parser)
# must not be candidate sites at all, matching the census's documented
# SITE-TYPE exclusion (docs/blocking-site-census.md, "Scope decision
# (documented)"), not just its FILE-level exclusion (tested above).
# ---------------------------------------------------------------------------


def test_bash_exit_inside_positional_case_dispatch_is_excluded(tmp_path: Path) -> None:
    """An exit inside a `case "$1" in ... esac` argument-parsing block is
    excluded from candidacy entirely -- the exact shape named in BUG-0052
    (release.sh's '--project requires a value', an 'unrecognized argument'
    catch-all) -- even though neither carries a classification comment or
    an emitter call."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_arg_parser.sh",
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    --project)\n"
        '      if [[ -z "${2:-}" ]]; then\n'
        '        echo "ERROR: --project requires a value" >&2\n'
        "        exit 1\n"
        "      fi\n"
        "      shift 2\n"
        "      ;;\n"
        "    *)\n"
        '      echo "ERROR: unrecognized argument: $1" >&2\n'
        "      exit 1\n"
        "      ;;\n"
        "  esac\n"
        "done\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_arg_parser.sh")
    assert findings == []


def test_bash_exit_inside_positional_case_dispatch_without_while_loop_is_excluded(
    tmp_path: Path,
) -> None:
    """A bare one-shot `case "${1:-}" in ... esac` dispatcher (no enclosing
    `while` parsing loop) is also excluded -- the shape used by test-harness
    fake-CLI shims (e.g. run-unit-tests.sh's fake `crontab` seam, written to
    another file via a heredoc)."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_embedded_shim.sh",
        "#!/usr/bin/env bash\n"
        'cat > "$FAKE_CLI" <<\'__SEAM_EOF__\'\n'
        "#!/usr/bin/env bash\n"
        'case "${1:-}" in\n'
        "    -l)\n"
        "        exit 1 ;;\n"
        "    *)\n"
        "        exit 1 ;;\n"
        "esac\n"
        "__SEAM_EOF__\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_embedded_shim.sh")
    assert findings == [], (
        "a case dispatcher written via heredoc to another file is still "
        "recognized as an operator-CLI dispatch block -- its own keywords "
        "are in command position within the heredoc body"
    )


def test_bash_prose_in_help_heredoc_does_not_corrupt_case_scan(tmp_path: Path) -> None:
    """Regression guard: a --help heredoc's prose routinely contains
    ordinary English words that collide with bash block keywords ('while',
    'for') at non-command positions. These must not desynchronize the
    case-range scan and must not hide an unrelated, genuinely
    non-compliant gate located later in the file."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_prose_heredoc.sh",
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    --help|-h)\n"
        "      cat <<EOF\n"
        "Usage: fixture.sh [--help]\n"
        "Prints this message while running and waits for input.\n"
        "This tool exists for directives they cover, not for general use.\n"
        "EOF\n"
        "      exit 0\n"
        "      ;;\n"
        "    *)\n"
        '      echo "ERROR: unrecognized argument: $1" >&2\n'
        "      exit 1\n"
        "      ;;\n"
        "  esac\n"
        "done\n"
        "\n"
        'echo "a real, unrelated, unannotated gate" >&2\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_prose_heredoc.sh")
    assert len(findings) == 1, (
        "only the genuinely unrelated trailing `exit 1` (outside the case "
        "block, not usage-guarded) should survive as a finding"
    )
    assert findings[0].line == 20, "the surviving finding must be the trailing exit, not one inside the case block"
    assert findings[0].missing_signals == ["census_classification", "dual_channel_contract"]


def test_bash_exit_guarded_by_usage_function_call_is_excluded(tmp_path: Path) -> None:
    """A required-flag validation that sits after the parsing loop (not
    lexically inside its case body) but calls a `*_usage` helper right
    before its exit -- the shape used by ship-rc.sh's missing --key/
    --project checks -- is excluded via the usage-guard, not the
    case-dispatch range."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_usage_guard.sh",
        "#!/usr/bin/env bash\n"
        '_fixture_usage() { echo "Usage: fixture.sh --key <value>" >&2; }\n'
        'if [[ -z "$KEY" ]]; then\n'
        '  echo "ERROR: missing required flag --key" >&2\n'
        "  _fixture_usage\n"
        "  exit 1\n"
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_usage_guard.sh")
    assert findings == []


def test_bash_exit_guarded_by_usage_help_text_is_excluded(tmp_path: Path) -> None:
    """A required-flag validation that prints a 'Run with --help for
    usage.' pointer (no dedicated `*_usage` function) rather than calling a
    helper -- the shape used by coder-stale-literal-check.sh's required
    --diff/--project checks -- is also excluded."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_usage_text.sh",
        "#!/usr/bin/env bash\n"
        'if [[ -z "$DIFF_RANGE" ]]; then\n'
        '    echo "ERROR: --diff is required" >&2\n'
        '    echo "Run with --help for usage." >&2\n'
        "    exit 1\n"
        "fi\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_usage_text.sh")
    assert findings == []


def test_bash_usage_function_definition_line_is_not_itself_a_guard(tmp_path: Path) -> None:
    """The `*_usage() { ... }` DEFINITION line is not a guard signal for an
    unrelated exit that happens to fall within its window -- only a call
    to (or a printed reference naming) usage guards an exit."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_usage_definition_only.sh",
        "#!/usr/bin/env bash\n"
        '_fixture_usage() { echo "Usage: fixture.sh" >&2; }\n'
        'echo "unrelated failure" >&2\n'
        "exit 1\n",
    )

    findings = _lint._scan_bash_file(bash_dir / "fixture_usage_definition_only.sh")
    assert len(findings) == 1, (
        "a *_usage() DEFINITION line must not itself count as a usage "
        "guard for an unrelated exit in its window"
    )


def test_python_exit_guarded_by_usage_text_is_excluded(tmp_path: Path) -> None:
    """The Python-side mirror of the bash usage-guard exclusion: an exit
    guarded by a printed 'Usage:' pointer is excluded the same way."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_usage_guard.py",
        "import sys\n\n"
        "def main():\n"
        "    if not path:\n"
        '        print("ERROR: --path is required", file=sys.stderr)\n'
        '        print("Usage: fixture.py --path <value>", file=sys.stderr)\n'
        "        sys.exit(1)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_usage_guard.py")
    assert findings == []


def test_python_usage_function_definition_line_is_not_itself_a_guard(tmp_path: Path) -> None:
    """The `def usage(...):` DEFINITION line itself is not a guard signal
    -- only a call to (or a printed reference naming) usage guards an
    exit. The function body deliberately does not repeat the word "usage"
    so this test isolates the definition-line exclusion specifically."""
    python_dir = tmp_path / "pkg"
    python_dir.mkdir()
    _write_python_file(
        python_dir,
        "fixture_usage_definition_only.py",
        "import sys\n\n"
        "def usage():\n"
        '    print("call this before exiting on bad input", file=sys.stderr)\n\n'
        "def fail():\n"
        '    print("unrelated failure", file=sys.stderr)\n'
        "    sys.exit(1)\n",
    )

    findings = _lint._scan_python_file(python_dir / "fixture_usage_definition_only.py")
    assert len(findings) == 1


def test_real_tree_no_findings_inside_positional_case_dispatch_blocks(capsys) -> None:
    """Regression guard for BUG-0052 on the real tree: no default-scope
    finding's line falls inside a `case "$1"`/`case "${1:-}"` argument-
    dispatch block in the file it was found in -- self-consistency check
    using the lint's own range helper, so this does not hardcode line
    numbers that could drift as the scanned files change."""
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)

    repo_root = _TEAM_DIR.parent
    for finding in data:
        abs_path = repo_root / finding["file"]
        if abs_path.suffix != ".sh" or not abs_path.is_file():
            continue
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        case_ranges = _lint._bash_positional_case_ranges(lines)
        line_index = finding["line"] - 1
        assert not _lint._line_in_ranges(line_index, case_ranges), (
            f"{finding['file']}:{finding['line']} is inside a positional-"
            "parameter case dispatch block but was still reported -- "
            "BUG-0052 site-type exclusion regression"
        )


def test_real_tree_default_scan_finding_count_dropped_from_bug_0052_baseline(capsys) -> None:
    """BUG-0053 regression tripwire (tightened from BUG-0052's coarse `< 90`):
    the pre-fix clean-tree baseline was 105 findings (BUG-0050), the
    operator-CLI SITE-TYPE fix brought that to 72 (BUG-0052), and the
    census-locator anchor (BUG-0053; see "CENSUS-LOCATOR ANCHOR SCOPE" in
    the lint's module docstring and docs/blocking-site-census.md's "Locator
    Index" section) exempts every one of those 72 sites, so a clean tree
    now yields ~0. The threshold below is not a literal `== 0` because the
    anchor is deliberately line-number sensitive (an unrelated edit that
    shifts an anchored line makes that one site reappear until its locator
    entry is refreshed -- documented, self-healing noise, not a bug) --
    a small nonzero count is expected drift, not a regression, but a large
    count means either a genuinely new unannotated gate landed or the
    anchor mechanism itself broke."""
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)

    assert len(data) <= 5, (
        f"expected a clean-tree default scan to be approximately zero (BUG-0053 "
        f"enforcement target) now that all 72 BUG-0052-baseline sites are "
        f"census-locator-anchored; got {len(data)}. If this is a genuine new "
        "unannotated gate, add it to docs/blocking-site-census.md's Locator "
        "Index (or annotate it inline) and raise this threshold deliberately "
        "only if neither is possible; if several anchored lines drifted at "
        "once, refresh their line numbers in the Locator Index instead."
    )


def test_census_locator_anchor_exempts_documented_site(tmp_path: Path) -> None:
    """CENSUS-LOCATOR ANCHOR SCOPE (BUG-0053): a candidate exit whose exact
    (file, line) is listed in a census doc's Locator Index is exempted from
    the classification/emitter check, the same as an operator-CLI SITE-TYPE
    exclusion -- proven on a synthetic fixture so it does not depend on the
    real tree's current line numbers."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'ERROR: unrecoverable state' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    census_doc = docs_dir / "blocking-site-census.md"
    census_doc.write_text(
        "# Blocking-Site Census\n\n"
        "## Locator Index (lint-consumed, BUG-0053)\n\n"
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:3 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    assert locators == {gate_file.resolve(): {3}}

    findings = _lint._scan_bash_file(gate_file, locators)
    assert findings == [], "a site at a census-anchored (file, line) must produce no finding"

    # Without the anchor, the same file is a normal, fully-unannotated finding.
    findings_unanchored = _lint._scan_bash_file(gate_file, census_locators=None)
    assert len(findings_unanchored) == 1
    assert set(findings_unanchored[0].missing_signals) == {
        _lint._SIGNAL_CENSUS,
        _lint._SIGNAL_DUAL_CHANNEL,
    }


def test_census_locator_anchor_does_not_exempt_a_different_line(tmp_path: Path) -> None:
    """A locator entry anchors an EXACT (file, line) pair -- a candidate exit
    at a different line in the same file, even one the census doc does not
    mention, is scanned normally. This is what lets a genuinely new,
    undocumented gate still surface as an actionable finding."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'ERROR: first failure' >&2\n"
        "exit 1\n"
        "echo 'ERROR: second, undocumented failure' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    census_doc = docs_dir / "blocking-site-census.md"
    census_doc.write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:3 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_bash_file(gate_file, locators)
    assert len(findings) == 1
    assert findings[0].line == 5, "only the non-anchored line 5 site should be reported"


def test_census_locator_anchor_missing_doc_fails_soft(tmp_path: Path) -> None:
    """A missing census doc (or a doc without the fenced Locator Index
    block) yields an empty anchor mapping rather than raising -- this
    lint's posture is "never crashes, never exits non-zero"."""
    assert _lint._load_census_locators(tmp_path) == {}

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "# Blocking-Site Census\n\nNo locator index here.\n", encoding="utf-8"
    )
    assert _lint._load_census_locators(tmp_path) == {}
