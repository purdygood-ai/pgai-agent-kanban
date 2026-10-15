"""
test_skip_bug_citation_scan.py
================================
The checked-in true-positive / false-positive corpus for the Python surface
of team/scripts/lint_skip_bug_gate.sh, proving that its skip-citation
detection is structural (identifies actual skip statements via the standard
library `ast` module) rather than line-oriented (a grep that sees text, not
code).

Every fixture below is materialized under pytest's ``tmp_path`` at test
time -- this suite never reads, writes, or asserts against the repository's
own team/tests/ or bugs/ directories. The synthetic bug identifiers used as
test data are clearly-fake, sequential placeholder IDs that exist only
inside these fixtures; they do not name, and are never confused with, any
real filed bug.

Corpus (six forms, matching the acceptance criteria this suite proves):

  Four real citations -- each must be detected as citing its BUG-NNNN:
    1. a plain ``pytest.skip("...")`` call citing a bug ID.
    2. an inline conditional skip written on one line: ``if x: pytest.skip(...)``.
    3. a skip call whose arguments span multiple lines, with the citation on
       a continuation line the call's opening line never touches -- the
       exact shape the line-oriented grep could not see (its character
       class cannot cross a newline).
    4. a ``@pytest.mark.skipif(...)`` decorator citation.

  Two non-citations -- neither must be detected as a citation, even though
  each one previously tripped the line-oriented grep:
    5. a fixture docstring that merely mentions a bug-shaped identifier
       while describing test history -- never passed to a skip call.
    6. a piece of sample test data (an ordinary string assignment) whose
       text happens to contain "reason=BUG-NNNN" -- again, never executed
       as a skip.

Two groups of tests:

  - Unit-level (``test_scan_source_*``): call ``scan_source()`` directly
    against each fixture's source text and assert on the exact findings.
  - End-to-end (``test_gate_script_*``): invoke the real
    ``lint_skip_bug_gate.sh`` as a subprocess against the whole corpus at
    once, proving the gate at the level TESTER and the gated runners
    actually exercise it -- the negative-proof pairing
    docs/test-authoring-guidelines.md requires of every grep-zero gate
    ("Pair each gate with a test that seeds a synthetic violation and
    confirms the gate reports it").
  - End-to-end report-fixture exclusion (``test_gate_script_*report*``,
    ``..._same_named_directory_...``): the same skip text planted in a real
    test file and in ``fixtures/tester_reports/`` is reported only from the
    real test file, and the exclusion is anchored to that one path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Load skip_bug_citation_scan.py as a module, under a name distinct from any
# other test module's own import of a same-named file elsewhere in the tree.
# ---------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SCAN_MODULE_PATH = _SCRIPTS_DIR / "lib" / "skip_bug_citation_scan.py"
_GATE_SCRIPT = _SCRIPTS_DIR / "lint_skip_bug_gate.sh"


def _import_scan_module():
    spec = importlib.util.spec_from_file_location(
        "skip_bug_citation_scan_under_test", _SCAN_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_SCAN_MODULE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_scan = _import_scan_module()


# ---------------------------------------------------------------------------
# The six corpus fixtures, as source text. Kept as module-level constants
# so both the unit-level and end-to-end tests below draw from one definition.
# ---------------------------------------------------------------------------

PLAIN_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_plain_skip_citation():\n"
    '    pytest.skip("BUG-1234 pending fix")\n'
)

INLINE_CONDITIONAL_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_inline_conditional_skip_citation():\n"
    "    x = True\n"
    '    if x: pytest.skip("BUG-1235 pending fix")\n'
)

# The citation sits on a continuation line that itself contains none of
# the line-oriented gate's anchor keywords (no "pytest.skip", no
# "reason=", no "#SKIP:") -- the exact shape a per-line grep cannot see.
MULTILINE_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_multiline_skip_citation_on_continuation_line():\n"
    "    pytest.skip(\n"
    '        "See BUG-1236 for context"\n'
    "    )\n"
)

SKIPIF_DECORATOR_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "@pytest.mark.skipif(\n"
    "    True,\n"
    '    reason="BUG-1237 pending fix",\n'
    ")\n"
    "def test_skipif_decorator_citation():\n"
    "    pass\n"
)

# NOT a citation: the bug-shaped identifier is inside a docstring, never
# passed to a skip call. Uses a non-numeric suffix so it also exercises the
# placeholder-ID pattern's old false-positive mode (skip.*BUG-[A-Za-z]).
DOCSTRING_MENTION_SOURCE = (
    "def fixture_helper_with_historical_note():\n"
    '    """Historical note: this suite once had a skip guarding\n'
    '    BUG-ZQWX; retained for reference."""\n'
    "    return None\n"
)

# NOT a citation: an ordinary string assignment (sample test data), never
# executed as a skip, whose text happens to contain "reason=BUG-NNNN".
SAMPLE_DATA_MENTION_SOURCE = (
    'SAMPLE_CONFIG_LINE = "reason=BUG-1239 unrelated sample payload, '
    'never executed as a skip"\n'
)


# ---------------------------------------------------------------------------
# Unit-level: scan_source() against each fixture in isolation.
# ---------------------------------------------------------------------------


def test_scan_source_catches_plain_skip_citation():
    findings = _scan.scan_source(Path("plain_skip.py"), PLAIN_SKIP_SOURCE, "citation")
    assert len(findings) == 1
    assert "BUG-1234" in findings[0].snippet


def test_scan_source_catches_inline_conditional_skip_citation():
    findings = _scan.scan_source(
        Path("inline_conditional_skip.py"), INLINE_CONDITIONAL_SKIP_SOURCE, "citation"
    )
    assert len(findings) == 1
    assert "BUG-1235" in findings[0].snippet


def test_scan_source_catches_multiline_skip_continuation_citation():
    """Catches a citation on
    a continuation line the line-oriented grep's character class could not
    cross into."""
    findings = _scan.scan_source(
        Path("multiline_skip.py"), MULTILINE_SKIP_SOURCE, "citation"
    )
    assert len(findings) == 1
    assert "BUG-1236" in findings[0].snippet


def test_scan_source_catches_skipif_decorator_citation():
    findings = _scan.scan_source(
        Path("skipif_decorator.py"), SKIPIF_DECORATOR_SOURCE, "citation"
    )
    assert len(findings) == 1
    assert "BUG-1237" in findings[0].snippet


def test_scan_source_does_not_flag_docstring_mention_as_citation():
    findings = _scan.scan_source(
        Path("docstring_mention.py"), DOCSTRING_MENTION_SOURCE, "citation"
    )
    assert findings == []


def test_scan_source_does_not_flag_docstring_mention_as_placeholder():
    """The docstring's non-numeric ID (BUG-ZQWX) tripped the OLD
    line-oriented placeholder pattern (skip.*BUG-[A-Za-z]) because "skip"
    and "BUG-Z..." shared a line; the structural scan never examines this
    text at all because it is not a skip statement's argument list."""
    findings = _scan.scan_source(
        Path("docstring_mention.py"), DOCSTRING_MENTION_SOURCE, "placeholder"
    )
    assert findings == []


def test_scan_source_does_not_flag_sample_data_mention_as_citation():
    findings = _scan.scan_source(
        Path("sample_data_mention.py"), SAMPLE_DATA_MENTION_SOURCE, "citation"
    )
    assert findings == []


# ---------------------------------------------------------------------------
# End-to-end: the real lint_skip_bug_gate.sh against the whole corpus.
# ---------------------------------------------------------------------------


def _write_corpus(tests_dir: Path) -> None:
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "plain_skip.py").write_text(PLAIN_SKIP_SOURCE, encoding="utf-8")
    (tests_dir / "inline_conditional_skip.py").write_text(
        INLINE_CONDITIONAL_SKIP_SOURCE, encoding="utf-8"
    )
    (tests_dir / "multiline_skip.py").write_text(MULTILINE_SKIP_SOURCE, encoding="utf-8")
    (tests_dir / "skipif_decorator.py").write_text(
        SKIPIF_DECORATOR_SOURCE, encoding="utf-8"
    )
    (tests_dir / "docstring_mention.py").write_text(
        DOCSTRING_MENTION_SOURCE, encoding="utf-8"
    )
    (tests_dir / "sample_data_mention.py").write_text(
        SAMPLE_DATA_MENTION_SOURCE, encoding="utf-8"
    )


def _write_matching_bugs_dir(bugs_dir: Path) -> None:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    for bug_id, slug in (
        ("BUG-1234", "plain-skip-fixture"),
        ("BUG-1235", "inline-conditional-skip-fixture"),
        ("BUG-1236", "multiline-skip-fixture"),
        ("BUG-1237", "skipif-decorator-fixture"),
    ):
        (bugs_dir / f"{bug_id}-{slug}.md").write_text("synthetic fixture bug\n", encoding="utf-8")


def test_gate_script_reports_exactly_the_four_real_citations(tmp_path: Path):
    tests_dir = tmp_path / "tests"
    bugs_dir = tmp_path / "bugs"
    _write_corpus(tests_dir)
    _write_matching_bugs_dir(bugs_dir)

    result = run_bash(
        tmp_path,
        f"bash {_GATE_SCRIPT} --tests-dir {tests_dir} --bugs-dir {bugs_dir} --verbose",
    )

    assert result.returncode == 0, result.stderr
    assert "cited BUG-NNNN IDs: BUG-1234 BUG-1235 BUG-1236 BUG-1237" in result.stdout
    assert "BUG-1239" not in result.stdout
    assert "BUG-ZQWX" not in result.stdout
    assert "placeholder check: 0 findings" in result.stdout
    assert "OK — all skip annotations are compliant." in result.stdout


def test_gate_script_fails_when_a_real_citation_is_missing_its_bug_file(tmp_path: Path):
    """Negative proof: with an empty bugs/ dir, the four real citations are
    still detected, and the gate fails because none of them resolve --
    confirming the detection mechanism actually feeds the existing
    missing-bug-file check rather than silently degrading to a no-op."""
    tests_dir = tmp_path / "tests"
    bugs_dir = tmp_path / "bugs"
    _write_corpus(tests_dir)
    bugs_dir.mkdir(parents=True, exist_ok=True)

    result = run_bash(
        tmp_path,
        f"bash {_GATE_SCRIPT} --tests-dir {tests_dir} --bugs-dir {bugs_dir} --verbose",
    )

    assert result.returncode == 1
    for bug_id in ("BUG-1234", "BUG-1235", "BUG-1236", "BUG-1237"):
        assert bug_id in result.stderr
    # The two non-citations must never appear as findings requiring a bug file.
    assert "BUG-1239" not in result.stderr
    assert "BUG-ZQWX" not in result.stderr


# ---------------------------------------------------------------------------
# End-to-end: the TESTER report fixture corpus is report data, not test code.
#
# The tree walk leaves out <tests-dir>/fixtures/tester_reports/ on both
# surfaces (the *.md/*.json grep and the structural *.py scan) and for both
# gates, while every real test file -- and a directory of the same name
# anywhere else -- is still scanned.
# ---------------------------------------------------------------------------

# A placeholder skip, as a real test would carry it and as a verbatim report
# quotes it. The same text is planted in a real test file and in the report
# fixture corpus so the two outcomes differ only by where the text lives.
PLACEHOLDER_SKIP_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_placeholder_skip():\n"
    '    pytest.skip("BUG-SKIP-report-corpus-fixture pending fix")\n'
)

# A skip citing a bug ID no bugs/ file carries (another project's ID, as the
# report corpus quotes them).
UNFILED_CITATION_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_unfiled_citation():\n"
    '    pytest.skip("BUG-1299 pending fix")\n'
)


def _plant_in_report_fixtures(tests_dir: Path, source: str) -> list[Path]:
    """Write *source* into the report fixture corpus on both surfaces: a
    report (*.md, the grep surface) and a legacy reader (*.py, the
    structural surface). Returns the planted paths."""
    corpus = tests_dir / "fixtures" / "tester_reports" / "corpus"
    readers = tests_dir / "fixtures" / "tester_reports" / "legacy_readers"
    corpus.mkdir(parents=True, exist_ok=True)
    readers.mkdir(parents=True, exist_ok=True)
    report = corpus / "quoted_report.md"
    reader = readers / "quoted_reader.py"
    report.write_text(source, encoding="utf-8")
    reader.write_text(source, encoding="utf-8")
    return [report, reader]


def test_gate_script_reports_placeholder_in_real_test_but_not_in_report_fixtures(tmp_path: Path):
    tests_dir = tmp_path / "tests"
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True)
    real_test = tests_dir / "unit" / "test_placeholder_skip.py"
    real_test.parent.mkdir(parents=True)
    real_test.write_text(PLACEHOLDER_SKIP_SOURCE, encoding="utf-8")
    planted = _plant_in_report_fixtures(tests_dir, PLACEHOLDER_SKIP_SOURCE)

    result = run_bash(
        tmp_path,
        f"bash {_GATE_SCRIPT} --tests-dir {tests_dir} --bugs-dir {bugs_dir}",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 placeholder skip citation(s) found" in result.stderr, result.stderr
    assert f"{real_test}:5:" in result.stderr, result.stderr
    for path in planted:
        assert str(path) not in result.stderr, result.stderr


def test_gate_script_is_ok_when_only_report_fixtures_quote_skip_shapes(tmp_path: Path):
    tests_dir = tmp_path / "tests"
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True)
    (tests_dir / "unit").mkdir(parents=True)
    (tests_dir / "unit" / "test_clean.py").write_text(
        "def test_clean():\n    assert True\n", encoding="utf-8"
    )
    _plant_in_report_fixtures(tests_dir, PLACEHOLDER_SKIP_SOURCE + UNFILED_CITATION_SOURCE)

    result = run_bash(
        tmp_path,
        f"bash {_GATE_SCRIPT} --tests-dir {tests_dir} --bugs-dir {bugs_dir}",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK — all skip annotations are compliant." in result.stdout
    assert "BUG-1299" not in result.stderr


def test_gate_script_still_scans_a_same_named_directory_outside_fixtures(tmp_path: Path):
    """The exclusion is anchored to <tests-dir>/fixtures/tester_reports/: a
    directory named tester_reports elsewhere holds real tests and is still
    read, on the citation gate as on the placeholder gate."""
    tests_dir = tmp_path / "tests"
    bugs_dir = tmp_path / "bugs"
    bugs_dir.mkdir(parents=True)
    elsewhere = tests_dir / "unit" / "tester_reports" / "test_unfiled_citation.py"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text(UNFILED_CITATION_SOURCE, encoding="utf-8")

    result = run_bash(
        tmp_path,
        f"bash {_GATE_SCRIPT} --tests-dir {tests_dir} --bugs-dir {bugs_dir}",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "BUG-1299: no matching file found" in result.stderr, result.stderr
    assert f"{elsewhere}:5:" in result.stderr, result.stderr
