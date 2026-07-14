"""
test_filename_contract.py
=========================
Unit tests for the filename_contract module and an earlier defect acceptance criteria.

an earlier defect root cause: the eligibility sieve in _disc_list_all_eligible_requirements
encoded a semver-shape requirement (vX.Y.Z-slug.md) for ALL requirements files,
independent of version_semantics.  Label-semantics files with honest names like
v20260712-pvg-fieldtest.md (no dots) were silently rejected before the semantics
branch could accept them.

This file tests:

  1. filename_contract module: patterns, helpers, structural single-source guarantee.
  2. _disc_list_all_eligible_requirements behavior for an earlier defect acceptance criteria:
     a. The exact an earlier defect fixture filename (v20260712-pvg-fieldtest.md, NO dots) is
        selected under label semantics.
     b. A dotless file under semver semantics emits a named skip line containing
        "semver-shape-required" and is NOT selected.
     c. A dotted vX.Y.Z-slug.md file under semver semantics is still selected
        (regression check).
     d. Structural: the filename pattern literal appears in exactly one sourced
        location (filename_contract.py), not duplicated in write.py or discovery.sh.

Naming convention: describe the behavior under test, never the bug or ticket ID.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_DISCOVERY_SH = _SCRIPTS_LIB / "discovery.sh"
_WRITE_PY = _TEAM_DIR / "pgai_agent_kanban" / "ops" / "write.py"
_FILENAME_CONTRACT_PY = (
    _TEAM_DIR / "pgai_agent_kanban" / "lib" / "filename_contract.py"
)


# ---------------------------------------------------------------------------
# Helpers to run _disc_list_all_eligible_requirements in a subprocess
# ---------------------------------------------------------------------------

def _run_eligibility(
    req_dir: pathlib.Path,
    version_semantics: str,
    last_released: str = "v0.0.0",
) -> subprocess.CompletedProcess:
    """Run _disc_list_all_eligible_requirements against req_dir.

    Injects the team/ directory as TEAM_ROOT so the Python heredoc can import
    filename_contract via sys.path.
    """
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        _disc_list_all_eligible_requirements "{req_dir}" "{last_released}" "" "" "" "{version_semantics}"
    """)

    import os
    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(_TEAM_DIR.parent)
    env["TEAM_ROOT"] = str(_TEAM_DIR)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(_TEAM_DIR.parent)
    env.pop("PGAI_DEV_TREE_PATH", None)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _make_requirements_file(
    req_dir: pathlib.Path,
    filename: str,
    target_version: str,
    status: str = "open",
) -> pathlib.Path:
    """Write a minimal requirements file to req_dir."""
    req_dir.mkdir(parents=True, exist_ok=True)
    req_file = req_dir / filename
    req_file.write_text(
        f"# {filename}: test\n\n"
        f"## Status\n{status}\n\n"
        f"## Target Version\n{target_version}\n\n"
        f"## Workflow Type\ntesting-only\n\n"
        f"## PM Task\nnone\n\n"
        f"## Summary\nTest.\n",
        encoding="utf-8",
    )
    return req_file


# ---------------------------------------------------------------------------
# 1. filename_contract module: pattern correctness
# ---------------------------------------------------------------------------


class TestFilenameContractPatterns:
    """The filename_contract module exports correct patterns."""

    def test_filename_contract_module_exists(self) -> None:
        """The filename_contract module exists at the expected path."""
        assert _FILENAME_CONTRACT_PY.exists(), (
            f"Expected filename_contract.py at {_FILENAME_CONTRACT_PY}. "
            "This file must exist — it is the single-source filename contract."
        )

    def test_intake_requirements_re_matches_dotted_semver_filename(self) -> None:
        """INTAKE_REQUIREMENTS_RE matches a dotted semver filename."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import INTAKE_REQUIREMENTS_RE

        assert INTAKE_REQUIREMENTS_RE.match("v1.20.10-bugfix-bundle.md"), (
            "INTAKE_REQUIREMENTS_RE must match dotted semver filenames like v1.20.10-slug.md"
        )

    def test_intake_requirements_re_matches_dotless_label_filename(self) -> None:
        """INTAKE_REQUIREMENTS_RE matches the an earlier defect fixture filename (no dots)."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import INTAKE_REQUIREMENTS_RE

        # The load-bearing an earlier defect fixture filename per acceptance criterion 1.
        assert INTAKE_REQUIREMENTS_RE.match("v20260712-pvg-fieldtest.md"), (
            "INTAKE_REQUIREMENTS_RE must match label-shaped filenames like "
            "v20260712-pvg-fieldtest.md — this is the intake contract."
        )

    def test_semver_requirements_re_matches_dotted_filename(self) -> None:
        """SEMVER_REQUIREMENTS_RE matches a properly-shaped dotted semver filename."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import SEMVER_REQUIREMENTS_RE

        assert SEMVER_REQUIREMENTS_RE.match("v1.20.10-bugfix-bundle.md"), (
            "SEMVER_REQUIREMENTS_RE must match vX.Y.Z-slug.md filenames"
        )

    def test_semver_requirements_re_rejects_dotless_filename(self) -> None:
        """SEMVER_REQUIREMENTS_RE rejects a dotless label-shaped filename."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import SEMVER_REQUIREMENTS_RE

        assert not SEMVER_REQUIREMENTS_RE.match("v20260712-pvg-fieldtest.md"), (
            "SEMVER_REQUIREMENTS_RE must NOT match dotless filenames — "
            "v20260712-pvg-fieldtest.md has no dots and must be rejected by this pattern."
        )

    def test_filename_semantics_eligible_label_accepts_dotless(self) -> None:
        """filename_semantics_eligible returns eligible=True for dotless under label."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import filename_semantics_eligible

        eligible, reason = filename_semantics_eligible("v20260712-pvg-fieldtest.md", "label")
        assert eligible, (
            "filename_semantics_eligible must return eligible=True for a dotless "
            "filename under label semantics."
        )
        assert reason is None

    def test_filename_semantics_eligible_semver_rejects_dotless(self) -> None:
        """filename_semantics_eligible returns eligible=False for dotless under semver."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import (
            filename_semantics_eligible,
            SKIP_REASON_SEMVER_SHAPE_REQUIRED,
        )

        eligible, reason = filename_semantics_eligible("v20260712-pvg-fieldtest.md", "semver")
        assert not eligible, (
            "filename_semantics_eligible must return eligible=False for a dotless "
            "filename under semver semantics."
        )
        assert reason == SKIP_REASON_SEMVER_SHAPE_REQUIRED, (
            f"Expected reason={SKIP_REASON_SEMVER_SHAPE_REQUIRED!r}; got {reason!r}"
        )

    def test_filename_semantics_eligible_semver_accepts_dotted(self) -> None:
        """filename_semantics_eligible returns eligible=True for dotted under semver."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import filename_semantics_eligible

        eligible, reason = filename_semantics_eligible("v1.20.10-bugfix-bundle.md", "semver")
        assert eligible, (
            "filename_semantics_eligible must return eligible=True for a dotted "
            "semver filename under semver semantics."
        )
        assert reason is None

    def test_skip_log_line_format(self) -> None:
        """skip_log_line produces the canonical discovery skip format."""
        import sys
        sys.path.insert(0, str(_TEAM_DIR))
        from pgai_agent_kanban.lib.filename_contract import skip_log_line

        line = skip_log_line("v20260712-pvg-fieldtest.md", "semver-shape-required")
        assert line == "discovery: skipping v20260712-pvg-fieldtest.md: semver-shape-required", (
            f"skip_log_line produced unexpected format: {line!r}"
        )


# ---------------------------------------------------------------------------
# 2. Structural single-source guarantee (an earlier defect acceptance criterion 3)
# ---------------------------------------------------------------------------


class TestSingleSourceStructural:
    """The filename pattern literal appears in exactly one sourced location.

    an earlier defect acceptance criterion 3: intake and discovery both import from
    filename_contract; neither duplicates a local pattern definition.
    """

    # The raw pattern string as it appears in filename_contract.py.
    # We check that it appears in filename_contract.py and NOT as a
    # standalone re.compile() call in write.py or discovery.sh.
    _INTAKE_PATTERN_LITERAL = r"^v[0-9].*\.md$"
    _SEMVER_PATTERN_LITERAL = r"^v[0-9]+\.[0-9]+\.[0-9]+-.+\.md$"

    def test_intake_pattern_lives_in_filename_contract(self) -> None:
        """The intake requirements pattern literal is present in filename_contract.py."""
        text = _FILENAME_CONTRACT_PY.read_text(encoding="utf-8")
        assert self._INTAKE_PATTERN_LITERAL in text, (
            f"Expected intake pattern {self._INTAKE_PATTERN_LITERAL!r} to be defined in "
            f"filename_contract.py.  This is the single-source location."
        )

    def test_semver_pattern_lives_in_filename_contract(self) -> None:
        """The semver requirements pattern literal is present in filename_contract.py."""
        text = _FILENAME_CONTRACT_PY.read_text(encoding="utf-8")
        assert self._SEMVER_PATTERN_LITERAL in text, (
            f"Expected semver pattern {self._SEMVER_PATTERN_LITERAL!r} to be defined in "
            f"filename_contract.py.  This is the single-source location."
        )

    def test_write_py_imports_intake_re_from_filename_contract(self) -> None:
        """write.py imports INTAKE_REQUIREMENTS_RE from filename_contract, not locally."""
        text = _WRITE_PY.read_text(encoding="utf-8")
        # Must import from filename_contract
        assert "from pgai_agent_kanban.lib.filename_contract import" in text, (
            "write.py must import from pgai_agent_kanban.lib.filename_contract"
        )
        assert "INTAKE_REQUIREMENTS_RE" in text, (
            "write.py must reference INTAKE_REQUIREMENTS_RE from filename_contract"
        )

    def test_write_py_does_not_duplicate_intake_pattern_inline(self) -> None:
        """write.py does not contain an inline re.compile() of the intake pattern."""
        text = _WRITE_PY.read_text(encoding="utf-8")
        # The old inline pattern was r'^v[0-9].*\.md$' inside re.compile().
        # It must not appear as a standalone literal (only allowed in the import line
        # or comments, not as an argument to re.compile).
        # We search for the literal inside re.compile() calls specifically.
        inline_re_pattern = re.compile(
            r"""re\.compile\s*\(\s*r?['"](\^v\[0-9\].*\\\.md\$)['"]"""
        )
        matches = inline_re_pattern.findall(text)
        assert not matches, (
            f"write.py must not define the intake pattern inline via re.compile(); "
            f"it must import INTAKE_REQUIREMENTS_RE from filename_contract. "
            f"Found inline pattern(s): {matches!r}"
        )

    def test_discovery_sh_imports_filename_contract_in_eligibility_function(self) -> None:
        """discovery.sh's eligibility function imports from filename_contract."""
        text = _DISCOVERY_SH.read_text(encoding="utf-8")
        # The import must appear inside the eligibility Python heredoc.
        assert "pgai_agent_kanban.lib.filename_contract" in text, (
            "discovery.sh must import from pgai_agent_kanban.lib.filename_contract "
            "inside _disc_list_all_eligible_requirements."
        )

    def test_discovery_sh_does_not_contain_old_bundle_re_with_semver_arm(self) -> None:
        """discovery.sh no longer contains the old BUNDLE_RE with semver-only arm.

        The old pattern was:
          r'^(v[0-9]+\\.[0-9]+\\.[0-9]+-.+|PRIORITY-...|BUG-...)\\..md$'
        This combined semver and non-semver arms in a single RE that silently
        rejected label-shaped filenames.  After the fix it must not exist.
        """
        text = _DISCOVERY_SH.read_text(encoding="utf-8")
        # The old BUNDLE_RE assigned the combined pattern to a variable named BUNDLE_RE.
        old_assignment = re.compile(r'\bBUNDLE_RE\s*=\s*re\.compile\s*\(')
        assert not old_assignment.search(text), (
            "discovery.sh still contains a BUNDLE_RE = re.compile(...) assignment. "
            "The fix replaces this with imports from filename_contract."
        )


# ---------------------------------------------------------------------------
# 3. Behavioral: _disc_list_all_eligible_requirements (an earlier defect acceptance)
# ---------------------------------------------------------------------------


class TestEligibilityFilenameGateSemantics:
    """Behavioral tests for the semantics-aware filename gate in discovery.

    These run _disc_list_all_eligible_requirements in a subprocess against
    minimal fixture directories.
    """

    def test_dotless_label_filename_is_selected_under_label_semantics(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """The an earlier defect fixture filename is selected under label semantics.

        v20260712-pvg-fieldtest.md (NO dots) with a label Target Version must
        appear in the eligible list when version_semantics=label.

        This is an earlier defect acceptance criterion 1.  The fixture filename is
        load-bearing — a dotted substitute does not satisfy this criterion.
        """
        req_dir = tmp_path / "requirements"
        _make_requirements_file(
            req_dir,
            "v20260712-pvg-fieldtest.md",   # an earlier defect fixture filename — NO dots
            "v20260712-pvg-fieldtest",       # label Target Version
        )

        result = _run_eligibility(req_dir, "label")
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
        assert "v20260712-pvg-fieldtest.md" in output_names, (
            f"Expected v20260712-pvg-fieldtest.md in eligible list under label semantics; "
            f"got {output_names!r}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_dotless_file_under_semver_semantics_emits_named_skip_line(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A dotless file under semver semantics emits a named skip line.

        v20260712-slug.md is an intake-valid filename but fails the semver
        shape requirement.  On a semver project it must produce a named
        skip line containing 'semver-shape-required' and must NOT appear
        in the eligible output.

        This is an earlier defect acceptance criterion 2: no silent rejection.
        """
        req_dir = tmp_path / "requirements"
        _make_requirements_file(
            req_dir,
            "v20260712-dotless-semver-project.md",
            "v20260712-dotless-semver-project",
        )

        result = _run_eligibility(req_dir, "semver")
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Skip line must be present in stderr.
        assert "semver-shape-required" in result.stderr, (
            f"Expected 'semver-shape-required' in stderr for dotless file on semver project.\n"
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "v20260712-dotless-semver-project.md" in result.stderr, (
            f"Expected the filename to appear in the skip log line.\n"
            f"stderr: {result.stderr}"
        )

        # The file must NOT appear in eligible output.
        assert "v20260712-dotless-semver-project.md" not in result.stdout, (
            f"Dotless file must not appear in eligible list on semver project.\n"
            f"stdout: {result.stdout}"
        )

    def test_dotted_semver_filename_is_still_selected_under_semver_semantics(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """A dotted vX.Y.Z-slug.md file is still selected on a semver project.

        This is the regression check: the semver path must be byte-identical
        for properly-shaped dotted filenames — the fix must not break them.

        an earlier defect acceptance criterion 2 (semver regression).
        """
        req_dir = tmp_path / "requirements"
        _make_requirements_file(
            req_dir,
            "v0.0.1-bugfix-bundle-test.md",
            "v0.0.1",
        )

        result = _run_eligibility(req_dir, "semver", last_released="v0.0.0")
        assert result.returncode == 0, (
            f"_disc_list_all_eligible_requirements exited non-zero.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
        assert "v0.0.1-bugfix-bundle-test.md" in output_names, (
            f"Dotted semver file must still be selected on semver project.\n"
            f"got {output_names!r}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_dotless_and_dotted_files_coexist_in_label_project(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Both dotless and dotted filenames are eligible under label semantics.

        A label-semantics project accepts any intake-valid requirements filename.
        Mixing dotless and dotted files in the same directory: both must appear.
        """
        req_dir = tmp_path / "requirements"
        _make_requirements_file(
            req_dir,
            "v20260712-pvg-fieldtest.md",
            "v20260712-pvg-fieldtest",
        )
        _make_requirements_file(
            req_dir,
            "v1.0.0-another-bundle.md",
            "v20260101-another-bundle",
        )

        result = _run_eligibility(req_dir, "label")
        assert result.returncode == 0, (
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        output_names = [
            pathlib.Path(line.strip()).name
            for line in result.stdout.strip().splitlines()
            if line.strip()
        ]
        assert "v20260712-pvg-fieldtest.md" in output_names, (
            f"Dotless label filename must be selected under label semantics; "
            f"got {output_names!r}"
        )
        assert "v1.0.0-another-bundle.md" in output_names, (
            f"Dotted filename must also be selected under label semantics; "
            f"got {output_names!r}"
        )
