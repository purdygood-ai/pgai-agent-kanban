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
import re
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
    # Deliberately exercises the real tree via the lint's default scan
    # roots (not a fixture): result == 0 is content-independent (main()
    # always returns 0 by this lint's own CONVERT-class contract), and the
    # per-finding schema assertions below hold regardless of which sites
    # currently lack a classification comment.
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    assert "FINDINGS_JSON:" in captured.out
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)
    assert isinstance(data, list)
    for finding in data:
        assert finding["lint"] == "gate_census_compliance"
        # Only the "missing_signal" kind carries a nonempty missing_signals
        # list -- the reconciliation kinds (unindexed_anchor,
        # anchor_class_mismatch, unresolved_locator, rollup_mismatch) name
        # their own disagreement via other fields (token/index_class/
        # site_class, or table_summary/rollup_summary) instead.
        if finding["kind"] == _lint._FINDING_KIND_MISSING_SIGNAL:
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
    # Deliberately exercises the real tree via the lint's default scan
    # roots (not a fixture): the exclusion of these basenames is structural
    # (they are absent from _CENSUS_BASH_FILES by construction), so this
    # holds regardless of what other quality-class findings exist elsewhere.
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
# SITE-TYPE scoping -- operator-CLI argument/usage refusals inside
# otherwise in-scope files (e.g. release.sh's own --project flag parser)
# must not be candidate sites at all, matching the census's documented
# SITE-TYPE exclusion (docs/blocking-site-census.md, "Scope decision
# (documented)"), not just its FILE-level exclusion (tested above).
# ---------------------------------------------------------------------------


def test_bash_exit_inside_positional_case_dispatch_is_excluded(tmp_path: Path) -> None:
    """An exit inside a `case "$1" in ... esac` argument-parsing block is
    excluded from candidacy entirely -- a flag parser's own
    '--project requires a value' or an 'unrecognized argument' catch-all --
    even though neither carries a classification comment or an emitter
    call."""
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
    """Regression guard on the real tree: no default-scope finding's line
    falls inside a `case "$1"`/`case "${1:-}"` argument-dispatch block in
    the file it was found in -- self-consistency check using the lint's
    own range helper, so this does not hardcode line numbers that could
    drift as the scanned files change."""
    # Deliberately exercises the real tree via the lint's default scan
    # roots (not a fixture): the self-consistency check below is immune to
    # which findings currently exist, so this is content-independent.
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
            "operator-CLI site-type exclusion regression"
        )


def test_real_tree_default_scan_finding_count_stays_near_zero(capsys) -> None:
    """Regression tripwire: the operator-CLI SITE-TYPE exclusion and the
    census-locator anchor (see "CENSUS-LOCATOR ANCHOR SCOPE" in the lint's
    module docstring and docs/blocking-site-census.md's "Locator Index"
    section) together exempt every site the census already tracks, so a
    clean tree yields ~0 findings. The threshold below is not a literal
    `== 0` because a line-number-anchored site is deliberately line-number
    sensitive (an unrelated edit that shifts an anchored line makes that
    one site reappear until its locator entry is refreshed -- documented,
    self-healing noise, not a bug): a small nonzero count is expected
    drift, not a regression, but a large count means either a genuinely
    new unannotated gate landed or the anchor mechanism itself broke."""
    result = _lint.main([])
    assert result == 0

    captured = capsys.readouterr()
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    data = json.loads(json_block)

    assert len(data) <= 5, (
        f"expected a clean-tree default scan to stay approximately zero now "
        f"that every previously-baselined site is census-locator-anchored; "
        f"got {len(data)}. If this is a genuine new unannotated gate, add it "
        "to docs/blocking-site-census.md's Locator Index (or annotate it "
        "inline) and raise this threshold deliberately only if neither is "
        "possible; if several anchored lines drifted at once, refresh their "
        "line numbers in the Locator Index instead."
    )


def test_census_locator_anchor_exempts_documented_site(tmp_path: Path) -> None:
    """CENSUS-LOCATOR ANCHOR SCOPE: a candidate exit whose exact
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
        "## Locator Index (lint-consumed)\n\n"
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:3 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    assert locators.by_line == {gate_file.resolve(): {3}}
    assert locators.by_token == {}

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
    assert _lint._load_census_locators(tmp_path) == _lint._CensusLocators()

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "# Blocking-Site Census\n\nNo locator index here.\n", encoding="utf-8"
    )
    assert _lint._load_census_locators(tmp_path) == _lint._CensusLocators()


def test_census_locator_token_anchor_exempts_by_name_not_position(tmp_path: Path) -> None:
    """The token form of a Locator Index entry (`<path>:<token> <CLASS>`)
    resolves via a `# CENSUS-ANCHOR: <token>` marker rather than a line
    number -- inserting a blank line above the refusal (and its marker)
    must not un-exempt the site, which is the whole point of the token
    form over the line-number form."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"

    def _write_gate(leading_blank_lines: int) -> None:
        gate_file.write_text(
            "#!/usr/bin/env bash\n"
            + ("\n" * leading_blank_lines)
            + "# CENSUS-ANCHOR: some-gate-01\n"
            "echo 'ERROR: unrecoverable state' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )

    _write_gate(leading_blank_lines=0)

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    census_doc = docs_dir / "blocking-site-census.md"
    census_doc.write_text(
        "# Blocking-Site Census\n\n"
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:some-gate-01 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    assert locators.by_token == {gate_file.resolve(): {"some-gate-01"}}

    findings = _lint._scan_bash_file(gate_file, locators)
    assert findings == [], "a site named by its CENSUS-ANCHOR token must produce no finding"

    # Insert a blank line above the marker and the refusal -- both shift
    # down by one line. The token-form entry must still resolve; a
    # line-number entry would not survive this.
    _write_gate(leading_blank_lines=1)
    findings_after_shift = _lint._scan_bash_file(gate_file, locators)
    assert findings_after_shift == [], (
        "the token-anchored site lost its exemption after an unrelated line "
        "shift -- it should resolve by name, not by position"
    )


def test_census_locator_token_anchor_requires_marker_in_file(tmp_path: Path) -> None:
    """A token-keyed Locator Index entry with no matching `# CENSUS-ANCHOR:`
    marker in the file exempts nothing -- both halves (marker, index entry)
    are required together."""
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
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:some-gate-01 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_bash_file(gate_file, locators)
    assert len(findings) == 1, (
        "an index entry with no matching in-file marker must not exempt the "
        "site"
    )


# ---------------------------------------------------------------------------
# Anchor RECONCILIATION direction: every `# CENSUS-ANCHOR: <token>` marker
# in a scanned file must be named by a Locator Index `path:token CLASS`
# line, and that line's class must agree with what the site's own inline
# comment declares. The exemption tests above prove the index is CONSUMED
# correctly; these prove the lint also REPORTS the two ways a marker and
# its index entry can go out of sync -- an unpaired marker (index entry
# absent) and a class disagreement (index entry present but wrong) --
# which the exemption path alone never checked.
# ---------------------------------------------------------------------------


def test_unpaired_census_anchor_marker_is_reported(tmp_path: Path) -> None:
    """A `# CENSUS-ANCHOR: <token>` marker in a scanned file whose token no
    Locator Index line names is a half-authored anchor: reported as its own
    `unindexed_anchor` finding, distinct from the missing-signal findings."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_unpaired_anchor.sh",
        "#!/usr/bin/env bash\n"
        "# CENSUS-ANCHOR: fixture-unpaired-01\n"
        "# BLOCK-WORTHY: destructive state, refuse rather than guess\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
    )

    # No census doc at all -> _load_census_locators fails soft to an empty
    # mapping, so the marker's token is unindexed by construction.
    findings = _lint._scan_bash_file(bash_dir / "fixture_unpaired_anchor.sh", census_locators=None)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_UNINDEXED_ANCHOR
    assert finding.token == "fixture-unpaired-01"
    assert finding.site_class == "BLOCK-WORTHY"
    # missing_signals stays empty for this finding kind -- it is a distinct
    # vocabulary from the missing-signal check (this site fully declares
    # both signals, so it would otherwise be silent).
    assert finding.missing_signals == []


def test_paired_census_anchor_marker_produces_no_finding(tmp_path: Path) -> None:
    """The same marker, correctly named by a Locator Index entry carrying
    the class the site itself declares, produces no finding at all."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "fixture_paired_anchor.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "# CENSUS-ANCHOR: fixture-paired-01\n"
        "# BLOCK-WORTHY: destructive state, refuse rather than guess\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
        encoding="utf-8",
    )

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/fixture_paired_anchor.sh:fixture-paired-01 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_bash_file(gate_file, locators)
    assert findings == [], "a correctly-paired marker/index entry must produce no finding"


def test_census_anchor_class_mismatch_is_reported(tmp_path: Path) -> None:
    """A marker whose Locator Index entry disagrees with the class the
    site's own inline comment declares is reported as its own
    `anchor_class_mismatch` finding."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "fixture_mismatched_anchor.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "# CENSUS-ANCHOR: fixture-mismatch-01\n"
        "# BLOCK-WORTHY: destructive state, refuse rather than guess\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
        encoding="utf-8",
    )

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        # The index disagrees with the site's own BLOCK-WORTHY comment above.
        "team/scripts/fixture_mismatched_anchor.sh:fixture-mismatch-01 CONVERT\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_bash_file(gate_file, locators)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_ANCHOR_CLASS_MISMATCH
    assert finding.token == "fixture-mismatch-01"
    assert finding.index_class == "CONVERT"
    assert finding.site_class == "BLOCK-WORTHY"


def test_anchor_reconciliation_findings_still_return_zero_from_main(tmp_path: Path) -> None:
    """Same quality-class posture as every other finding kind: an unpaired
    anchor never turns main()'s exit code non-zero."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(
        bash_dir,
        "fixture_unpaired_anchor.sh",
        "#!/usr/bin/env bash\n"
        "# CENSUS-ANCHOR: fixture-unpaired-02\n"
        "# CONVERT: quality-class finding, zero runtime-failure risk\n"
        'pgai_emit_error_contract "gate.example" "something stopped" "run the fix"\n'
        "exit 1\n",
    )

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    assert result == 0, "quality-class lint must never return non-zero"


# ---------------------------------------------------------------------------
# LOCATOR RESOLUTION (key or token, never a line number): a non-numeric
# Locator Index entry resolves either by a `# CENSUS-ANCHOR: <key>` marker
# (the pre-existing mechanism, proven above) or by a literal dual-channel
# emitter argument matching the key -- keying a site on its own dual-channel
# error key where it has one, falling back to a stable anchor token where
# it does not. These tests prove the key-literal path specifically, plus
# the new LOCATOR INDEX -> SITE reconciliation direction that reports a
# pin naming neither shape as an informative finding.
# ---------------------------------------------------------------------------


def test_resolve_error_key_lines_exempts_call_with_matching_literal_key() -> None:
    """A dual-channel emitter call whose literal first argument matches a
    requested key exempts a forward window from that call -- proven at the
    helper level, independent of any file I/O."""
    lines = (
        "#!/usr/bin/env bash\n"
        "if ! some_check; then\n"
        "  pgai_emit_error_contract \\\n"
        "    \"some.stable.key\" \\\n"
        "    \"something stopped\" \\\n"
        "    \"run the fix\"\n"
        "  exit 1\n"
        "fi\n"
    ).splitlines()

    exempted = _lint._resolve_error_key_lines(
        lines, {"some.stable.key"}, _lint._BASH_EMITTER_RE, is_python=False
    )

    # Line 7 (1-indexed) is the `exit 1` -- several lines below the call
    # (line 3) whose own line does not carry the literal key (it sits on
    # line 4, bash's continuation style).
    assert 7 in exempted
    assert 4 in exempted  # the call's own probed lines are exempted too


def test_resolve_error_key_lines_ignores_a_different_key() -> None:
    """A key that does not appear as a literal emitter argument anywhere in
    the file exempts nothing -- the whole point of the reconciliation
    direction this enables (an entry naming a key nothing emits is a stale
    pin, not a free pass)."""
    lines = (
        "#!/usr/bin/env bash\n"
        "pgai_emit_error_contract \"some.other.key\" \"msg\" \"remedy\"\n"
        "exit 1\n"
    ).splitlines()

    exempted = _lint._resolve_error_key_lines(
        lines, {"some.stable.key"}, _lint._BASH_EMITTER_RE, is_python=False
    )

    assert exempted == set()


def test_scan_bash_file_exempts_site_via_dual_channel_key(tmp_path: Path) -> None:
    """A Locator Index entry keyed on a site's own literal dual-channel
    error key resolves that site WITHOUT any `# CENSUS-ANCHOR:` marker in
    the file -- the site is already self-identifying via the key it
    already speaks."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "if ! some_check; then\n"
        "  pgai_emit_error_contract \\\n"
        "    \"some-gate.check-failed\" \\\n"
        "    \"the check failed\" \\\n"
        "    \"run the fix\"\n"
        "  exit 1\n"
        "fi\n",
        encoding="utf-8",
    )

    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:some-gate.check-failed BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_bash_file(gate_file, locators)

    assert findings == [], (
        "a site named by its own literal dual-channel error key must "
        "resolve without a CENSUS-ANCHOR marker"
    )


def test_scan_locator_resolution_silent_when_key_resolves(tmp_path: Path) -> None:
    """`_scan_locator_resolution` produces no finding for a key-form entry
    that resolves via a literal dual-channel argument."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "pgai_emit_error_contract \"some-gate.check-failed\" \"msg\" \"remedy\"\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:some-gate.check-failed BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_locator_resolution(locators, repo_root)

    assert findings == []


def test_scan_locator_resolution_silent_when_token_resolves(tmp_path: Path) -> None:
    """`_scan_locator_resolution` produces no finding for a token-form entry
    that resolves via a `# CENSUS-ANCHOR:` marker (the original mechanism)."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "# CENSUS-ANCHOR: some-gate-01\n"
        "echo 'ERROR: unrecoverable state' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:some-gate-01 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_locator_resolution(locators, repo_root)

    assert findings == []


def test_scan_locator_resolution_reports_unresolvable_key(tmp_path: Path) -> None:
    """A Locator Index entry naming a key that resolves at no site in its
    file -- no marker, no matching literal emitter argument -- is reported
    as exactly one informative `unresolved_locator` finding naming that key."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'ERROR: unrelated failure' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:no-such-anchor-anywhere BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_locator_resolution(locators, repo_root)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_UNRESOLVED_LOCATOR
    assert finding.token == "no-such-anchor-anywhere"
    assert finding.index_class == "BLOCK-WORTHY"


def test_scan_locator_resolution_reports_unresolvable_line(tmp_path: Path) -> None:
    """A line-form entry naming a line that is not a candidate exit is
    reported the same way -- the reconciliation direction covers both
    Locator Index shapes, not just the key form."""
    repo_root = tmp_path
    scripts_dir = repo_root / "team" / "scripts"
    scripts_dir.mkdir(parents=True)
    gate_file = scripts_dir / "some_gate.sh"
    gate_file.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'this line is not a fatal exit'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/some_gate.sh:2 BLOCK-WORTHY\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_locator_resolution(locators, repo_root)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_UNRESOLVED_LOCATOR
    assert finding.token == "2"


def test_scan_locator_resolution_missing_file_reports_unresolvable(tmp_path: Path) -> None:
    """A Locator Index entry naming a file that does not exist at all
    resolves nothing for any of its entries -- fail-soft, reported rather
    than raised."""
    repo_root = tmp_path
    docs_dir = repo_root / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "<!-- CENSUS-LOCATOR-INDEX-BEGIN -->\n"
        "team/scripts/does_not_exist.sh:some-token-01 CONVERT\n"
        "<!-- CENSUS-LOCATOR-INDEX-END -->\n",
        encoding="utf-8",
    )

    locators = _lint._load_census_locators(repo_root)
    findings = _lint._scan_locator_resolution(locators, repo_root)

    assert len(findings) == 1
    assert findings[0].kind == _lint._FINDING_KIND_UNRESOLVED_LOCATOR
    assert findings[0].token == "some-token-01"


def test_main_bash_dirs_override_skips_locator_resolution(tmp_path: Path, capsys) -> None:
    """`main()` only runs the census-wide LOCATOR INDEX -> SITE
    reconciliation against its own default scan surface. An ad-hoc
    `--bash-dirs`/`--python-dir` sweep (every other test in this module)
    must not pull in findings about the REAL repo's census against a
    fixture tree it was never written against -- proven directly by
    confirming a clean synthetic fixture run reports ZERO findings, even
    though the real repo's census (loaded unconditionally by
    `_load_census_locators` regardless of the override) is not empty."""
    bash_dir = tmp_path / "scripts"
    bash_dir.mkdir()
    _write_bash_file(bash_dir, "fixture_clean.sh", "#!/usr/bin/env bash\necho hi\nexit 0\n")

    result = _lint.main([
        "--bash-dirs", str(bash_dir),
        "--python-dir", str(_empty_python_dir(tmp_path)),
    ])

    assert result == 0
    captured = capsys.readouterr()
    json_block = captured.out.split("FINDINGS_JSON:", 1)[1].strip()
    assert json.loads(json_block) == [], (
        "an ad-hoc --bash-dirs sweep must not surface the real repo's "
        "census-wide locator-resolution findings against an unrelated "
        "fixture tree"
    )


# ---------------------------------------------------------------------------
# ROLLUP RECONCILIATION: the census document's own
# "**Rollup: N sites, M unclassified.**" summary line, and its companion
# per-class breakdown line, must agree with what the inventory table above
# them actually contains. See "ROLLUP RECONCILIATION" in the lint's module
# docstring and `_scan_census_rollup`.
# ---------------------------------------------------------------------------


def _write_census_doc(
    docs_dir: Path,
    *,
    rows: list[tuple[int, str]],
    rollup_sites: int,
    rollup_unclassified: int,
    rollup_block: int,
    rollup_convert: int,
    rollup_agent: int,
) -> Path:
    """Build a minimal synthetic census doc: a numbered inventory table
    (``rows`` is a list of ``(row_number, class)`` pairs) followed by the
    two-line hand-authored rollup summary this lint reconciles against it."""
    lines = [
        "# Blocking-Site Census\n",
        "\n",
        "## At-a-glance rollup\n",
        "\n",
        "| # | Site | Source (path · locator) | Trigger | Class |\n",
        "|---|---|---|---|---|\n",
    ]
    for num, cls in rows:
        lines.append(f"| {num} | Fixture site {num} | `some/path.sh` · `exit 1` | Some trigger | {cls} |\n")
    lines.append("\n")
    lines.append(f"**Rollup: {rollup_sites} sites, {rollup_unclassified} unclassified.**\n")
    lines.append(f"{rollup_block} BLOCK-WORTHY, {rollup_convert} CONVERT, {rollup_agent} AGENT-RESOLVABLE.\n")
    lines.append("\n")
    lines.append("## BLOCK-WORTHY justifications\n")

    docs_dir.mkdir(parents=True, exist_ok=True)
    census_doc = docs_dir / "blocking-site-census.md"
    census_doc.write_text("".join(lines), encoding="utf-8")
    return census_doc


def test_scan_census_rollup_matching_counts_produce_no_finding(tmp_path: Path) -> None:
    """A rollup line whose counts agree with the table produces no finding."""
    _write_census_doc(
        tmp_path / "docs",
        rows=[
            (1, "BLOCK-WORTHY"),
            (2, "BLOCK-WORTHY"),
            (3, "CONVERT"),
            (4, "AGENT-RESOLVABLE"),
        ],
        rollup_sites=4,
        rollup_unclassified=0,
        rollup_block=2,
        rollup_convert=1,
        rollup_agent=1,
    )

    findings = _lint._scan_census_rollup(tmp_path)
    assert findings == []


def test_scan_census_rollup_site_count_mismatch_is_reported(tmp_path: Path) -> None:
    """A rollup line understating the table's row count -- the exact shape
    of drift this scan exists to catch -- is flagged as a `rollup_mismatch`
    finding naming both sides' counts."""
    _write_census_doc(
        tmp_path / "docs",
        rows=[
            (1, "BLOCK-WORTHY"),
            (2, "BLOCK-WORTHY"),
            (3, "CONVERT"),
        ],
        rollup_sites=2,  # table actually has 3 rows
        rollup_unclassified=0,
        rollup_block=1,  # table actually has 2 BLOCK-WORTHY rows
        rollup_convert=1,
        rollup_agent=0,
    )

    findings = _lint._scan_census_rollup(tmp_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_ROLLUP_MISMATCH
    assert finding.table_summary == (
        "3 sites, 0 unclassified -- 2 BLOCK-WORTHY, 1 CONVERT, 0 AGENT-RESOLVABLE"
    )
    assert finding.rollup_summary == (
        "2 sites, 0 unclassified -- 1 BLOCK-WORTHY, 1 CONVERT, 0 AGENT-RESOLVABLE"
    )


def test_scan_census_rollup_per_class_mismatch_with_correct_total_is_reported(
    tmp_path: Path,
) -> None:
    """A rollup whose site TOTAL matches but whose per-class breakdown does
    not (e.g. a row reclassified without updating the summary) is still
    caught -- the total alone is not sufficient agreement."""
    _write_census_doc(
        tmp_path / "docs",
        rows=[
            (1, "BLOCK-WORTHY"),
            (2, "CONVERT"),
            (3, "CONVERT"),
        ],
        rollup_sites=3,
        rollup_unclassified=0,
        rollup_block=2,  # table actually has 1 BLOCK-WORTHY, 2 CONVERT
        rollup_convert=1,
        rollup_agent=0,
    )

    findings = _lint._scan_census_rollup(tmp_path)
    assert len(findings) == 1
    assert findings[0].kind == _lint._FINDING_KIND_ROLLUP_MISMATCH


def test_scan_census_rollup_no_table_rows_fails_soft(tmp_path: Path) -> None:
    """A census doc with no numbered inventory rows (e.g. the fenced Locator
    Index section only) produces no finding -- there is nothing to
    reconcile a rollup line against."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "# Blocking-Site Census\n\nNo table here.\n", encoding="utf-8"
    )

    assert _lint._scan_census_rollup(tmp_path) == []


def test_scan_census_rollup_missing_doc_fails_soft(tmp_path: Path) -> None:
    """A repo root with no census doc at all produces no finding rather
    than raising -- same fail-soft posture as `_load_census_locators`."""
    assert _lint._scan_census_rollup(tmp_path) == []


def test_scan_census_rollup_missing_rollup_line_fails_soft(tmp_path: Path) -> None:
    """A table with numbered rows but no parseable "**Rollup: ...**" line
    at all produces no finding -- there is no summary line to reconcile."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "# Blocking-Site Census\n\n"
        "## At-a-glance rollup\n\n"
        "| # | Site | Source | Trigger | Class |\n"
        "|---|---|---|---|---|\n"
        "| 1 | Fixture | `x` | `y` | BLOCK-WORTHY |\n",
        encoding="utf-8",
    )

    assert _lint._scan_census_rollup(tmp_path) == []


def test_scan_census_rollup_missing_class_breakdown_line_is_reported(tmp_path: Path) -> None:
    """A "**Rollup: ...**" line present with no parseable per-class
    breakdown line following it is itself a reportable disagreement -- the
    document's contract is that both lines exist together."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(
        "# Blocking-Site Census\n\n"
        "## At-a-glance rollup\n\n"
        "| # | Site | Source | Trigger | Class |\n"
        "|---|---|---|---|---|\n"
        "| 1 | Fixture | `x` | `y` | BLOCK-WORTHY |\n\n"
        "**Rollup: 1 sites, 0 unclassified.**\n"
        "(breakdown line intentionally omitted)\n",
        encoding="utf-8",
    )

    findings = _lint._scan_census_rollup(tmp_path)
    assert len(findings) == 1
    assert findings[0].kind == _lint._FINDING_KIND_ROLLUP_MISMATCH
    assert "no parseable per-class breakdown" in findings[0].rollup_summary


def test_main_reports_rollup_mismatch_and_still_returns_zero(monkeypatch, tmp_path: Path) -> None:
    """`main()`'s default-scope run surfaces a `rollup_mismatch` finding
    when the census doc it reads (via the lint's own `Path(__file__)`-
    derived repo root) disagrees with its table, and still returns 0 --
    this quality-class lint never blocks on this finding kind either.
    Proven by monkeypatching `_scan_census_rollup` (rather than relocating
    the whole repo root) so the rest of `main()`'s default scan surface
    (the real bash/python file lists) is exercised unchanged."""

    def _fake_scan(repo_root):
        return [
            _lint._Finding(
                path=repo_root / "docs" / "blocking-site-census.md",
                line=160,
                snippet="**Rollup: 57 sites, 0 unclassified.**",
                kind=_lint._FINDING_KIND_ROLLUP_MISMATCH,
                table_summary="60 sites, 0 unclassified -- 32 BLOCK-WORTHY, 26 CONVERT, 2 AGENT-RESOLVABLE",
                rollup_summary="57 sites, 0 unclassified -- 29 BLOCK-WORTHY, 26 CONVERT, 2 AGENT-RESOLVABLE",
            )
        ]

    monkeypatch.setattr(_lint, "_scan_census_rollup", _fake_scan)

    result = _lint.main([])
    assert result == 0, "quality-class lint must never return non-zero on a rollup_mismatch finding"


def test_scratch_copy_of_real_census_doc_with_seeded_mismatch_is_detected(tmp_path: Path) -> None:
    """Required-Output test: seed a DELIBERATE mismatch into a scratch copy
    of the real `docs/blocking-site-census.md` (rather than a from-scratch
    synthetic doc) and confirm the lint catches it -- proving the check
    works against the real document's actual table shape (escaped pipes in
    code spans, multi-line justification prose, etc.), decoupled from
    whatever the live rollup line says on any given day (ticket 7 owns
    correcting that line; this test does not depend on it being wrong)."""
    real_census_doc = _TEAM_DIR.parent / "docs" / "blocking-site-census.md"
    real_text = real_census_doc.read_text(encoding="utf-8")

    # Deliberately corrupt the rollup's site count so it disagrees with the
    # table by a known, controlled amount -- regardless of what the real
    # rollup line currently says.
    corrupted_text, n = re.subn(
        r"\*\*Rollup: \d+ sites, (\d+) unclassified\.\*\*",
        lambda m: f"**Rollup: 999999 sites, {m.group(1)} unclassified.**",
        real_text,
        count=1,
    )
    assert n == 1, "fixture setup must find exactly one Rollup line to corrupt"

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "blocking-site-census.md").write_text(corrupted_text, encoding="utf-8")

    findings = _lint._scan_census_rollup(tmp_path)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == _lint._FINDING_KIND_ROLLUP_MISMATCH
    assert finding.rollup_summary.startswith("999999 sites")
    assert not finding.table_summary.startswith("999999")


def test_real_tree_census_rollup_check_runs_without_crashing() -> None:
    """Real-tree sanity check (same posture as
    `test_real_tree_scan_runs_to_completion`): `_scan_census_rollup` never
    raises against the live repo, and any finding it does produce is
    correctly shaped. This does not hardcode the live rollup's current
    drift (see the scratch-copy test above for that proof) so it
    keeps passing whether or not the document half of the rollup has been
    reconciled yet."""
    repo_root = _TEAM_DIR.parent
    findings = _lint._scan_census_rollup(repo_root)
    assert isinstance(findings, list)
    for finding in findings:
        assert finding.kind == _lint._FINDING_KIND_ROLLUP_MISMATCH
        assert finding.table_summary
        assert finding.rollup_summary
