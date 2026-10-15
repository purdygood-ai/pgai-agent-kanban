"""
test_doc_truth_lint_wiring.py
===============================
Regression coverage for the doc-truth lint being an orphan verb: the
failure mode in which `team/scripts/lint_doc_command_truth.py` exists, works,
and finds real defects, but nothing in the shipped tree ever invokes it — a
grep for its name across every `.sh`/`.py`/`.txt`/`.cfg`/`.json` file in the
tree returns hits only inside the script itself, its own unit tests, and
`docs/blocking-site-census.md`. Its findings reach nobody unless a human
types the command by hand.

This suite proves two things a description of the wiring cannot:

  1. **The call site exists.** `team/scripts/run-unit-tests.sh` names the
     lint through `gated_runner_run_quality_lint "doc_command_truth"` — a
     grep-based regression guard against the exact silent-orphan failure
     mode described above (see this module's own module-docstring
     description of the orphaned state, in which that grep has zero hits
     there).
  2. **The wiring shape behaves correctly.** Invoked exactly the way
     run-unit-tests.sh now invokes it — through the shared
     `gated_runner_run_quality_lint` wrapper — a seeded false citation
     produces exactly one recorded finding named "doc_command_truth" and
     the wrapper's own exit code stays 0 (CONVERT class: warn, file,
     continue; never abort the calling runner), matching
     `team/scripts/lib/gated_runner_lint.sh`'s CONVERT contract.

The findings-file JSON schema itself (schema_version, generated_at, runner,
findings[]) is already covered by test_gated_runner_lint.py; this suite only
proves THIS lint's own entry lands in it with the expected "lint" key.
"""

from __future__ import annotations

import json
import pathlib

from tests.unit.shell_harness import run_bash

_RUN_UNIT_TESTS = "scripts/run-unit-tests.sh"
_LINT_SCRIPT = "scripts/lint_doc_command_truth.py"
_GATED_RUNNER_LIB = "scripts/lib/gated_runner_lint.sh"


def test_run_unit_tests_calls_the_doc_truth_lint_through_the_quality_wrapper() -> None:
    """run-unit-tests.sh names the lint through the shared quality-class
    wrapper, on the same warn-file-continue footing as every other lint the
    gated runners carry -- wiring that was previously entirely absent."""
    team_dir = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
    text = (team_dir / "scripts" / "run-unit-tests.sh").read_text(encoding="utf-8")
    assert 'gated_runner_run_quality_lint "doc_command_truth"' in text, (
        "run-unit-tests.sh must invoke lint_doc_command_truth.py through "
        "gated_runner_run_quality_lint."
    )
    assert "lint_doc_command_truth.py" in text


def _build_fixture_tree_with_false_citation(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal synthetic repo tree with one document citing a script that
    does not exist -- the seeded false citation this suite feeds through
    the exact wrapper call shape run-unit-tests.sh uses."""
    root = tmp_path / "fixture_repo"
    (root / "team" / "scripts").mkdir(parents=True)
    (root / "docs").mkdir()
    doc = root / "docs" / "fake.md"
    doc.write_text(
        "# Fake doc\n\n```bash\nteam/scripts/does_not_exist.sh\n```\n",
        encoding="utf-8",
    )
    return root, doc


def test_seeded_false_citation_produces_one_named_finding_and_wrapper_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """Invoked exactly as run-unit-tests.sh now does (through
    gated_runner_run_quality_lint), a seeded false citation in a scratch
    document produces exactly one finding whose "lint" key names the
    doc-truth check, and the wrapper's own exit code is unaffected —
    proving the acceptance criterion 'a seeded false citation ... produces
    a finding ... and does not change the runner's exit code.'"""
    root, doc = _build_fixture_tree_with_false_citation(tmp_path)
    findings_file = tmp_path / "findings-unit.json"

    script = f"""
set -uo pipefail
source {_GATED_RUNNER_LIB}
gated_runner_run_quality_lint "doc_command_truth" -- \\
  python3 {_LINT_SCRIPT} --repo-root "{root}" --doc "{doc}"
echo "WRAPPER_RETURNED=$?"
gated_runner_write_findings_file "{findings_file}" "run-unit-tests.sh"
"""
    result = run_bash(tmp_path, script)

    assert "WRAPPER_RETURNED=0" in result.stdout, (
        "gated_runner_run_quality_lint must return 0 even though the "
        f"wrapped lint found a false citation.\nstdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.returncode == 0

    doc_written = json.loads(findings_file.read_text(encoding="utf-8"))
    findings = [f for f in doc_written["findings"] if f.get("lint") == "doc_command_truth"]
    assert len(findings) == 1, (
        f"expected exactly one doc_command_truth finding, got: {doc_written['findings']}"
    )
    assert findings[0]["severity"] == "quality"
    assert "does_not_exist.sh" in findings[0]["detail"]
