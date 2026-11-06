"""
test_lint_icd_freshness.py — Unit tests for team/scripts/lint_icd_freshness.py.

Tests cover:

  1. **Positive (fresh tree)**: call check_freshness() with the real
     docs/api/icd.json and docs/api/ICD_VERSION; assert it returns True.
     This proves the gate exits 0 when the artifact is up-to-date.

  2. **Behavioral negative (stale artifact)**: copy docs/api/icd.json to a
     scratch file under tmp_path, byte-modify one character, and pass the
     modified path to check_freshness().  Assert it returns False and that the
     error output names the artifact as stale plus the regeneration command.
     This exercises the real comparison code — no mocked exit codes.

  3. **Missing artifact**: pass a nonexistent path as icd_path; assert
     check_freshness() returns False and the error names the missing artifact.

  4. **CLI findings** (``TestIcdFreshnessCliFindings``): main() against a
     stale scratch artifact prints the staleness, writes one quality finding
     to --findings-file, and exits 0 (quality class); against the committed
     artifact it writes an empty findings array.

All temp paths use pytest's tmp_path (redirected to the framework temp root
by conftest.py when PGAI_AGENT_KANBAN_TEMP_DIR is set).  No bare /tmp paths.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Locate lint_icd_freshness.py and the real ICD artifact.
# This file lives at team/tests/unit/test_lint_icd_freshness.py.
# Three parent levels up: unit/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent  # project_root/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_icd_freshness.py"
_ICD_ARTIFACT = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_ICD_VERSION_FILE = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"


def _import_lint_module():
    """Import lint_icd_freshness as a module without polluting sys.modules permanently.

    Returns:
        The loaded module object.

    Raises:
        ImportError: if the lint script cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location("lint_icd_freshness", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Import once at module load time; tests reference `_lint` attributes.
_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Positive test
# ---------------------------------------------------------------------------


class TestIcdFreshnessPositive:
    """Fresh tree: check_freshness returns True when the artifact is up-to-date."""

    def test_fresh_artifact_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_freshness() returns True for the committed docs/api/icd.json.

        The real checked-in artifact was generated from the current codebase in
        this RC.  Re-running the generator should produce byte-identical output,
        so the freshness gate must pass.

        Args:
            capsys: pytest capture fixture used to inspect stdout/stderr.
        """
        # Deliberately exercises the real docs/api/icd.json and
        # docs/api/ICD_VERSION (not a fixture): the freshness check's whole
        # purpose is to validate this specific committed pair, so there is
        # no isolated fixture substitute that would test the same thing.
        result = _lint.check_freshness(
            icd_path=_ICD_ARTIFACT,
            version_file=_ICD_VERSION_FILE,
        )
        assert result is True, (
            "check_freshness() returned False for the committed icd.json artifact.\n"
            f"Captured stderr: {capsys.readouterr().err}\n"
            "The artifact must be byte-identical to a fresh regeneration.  Run "
            "'bash team/scripts/generate-icd.sh' and commit the result."
        )

    def test_fresh_artifact_prints_ok_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_freshness() prints an 'ok' confirmation when the artifact is fresh.

        Verifies that the gate emits a positive confirmation message to stdout
        rather than silently succeeding — this message appears in runner output
        and lets operators see the gate ran.

        Args:
            capsys: pytest capture fixture.
        """
        _lint.check_freshness(
            icd_path=_ICD_ARTIFACT,
            version_file=_ICD_VERSION_FILE,
        )
        captured = capsys.readouterr()
        assert "ok" in captured.out.lower() or "fresh" in captured.out.lower(), (
            f"check_freshness() did not print a positive confirmation to stdout.\n"
            f"stdout: {captured.out!r}\n"
            "Expected output containing 'ok' or 'fresh'."
        )


# ---------------------------------------------------------------------------
# Behavioral negative tests
# ---------------------------------------------------------------------------


class TestIcdFreshnessBehavioralNegative:
    """Stale artifact: check_freshness returns False and names the staleness.

    Each test in this class actually modifies a scratch artifact and exercises
    the real comparison code — exit codes and return values are not mocked.
    """

    def test_byte_modified_artifact_fails(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """check_freshness() returns False when the artifact has been byte-modified.

        The test copies docs/api/icd.json to a scratch file under tmp_path,
        replaces the last byte with a different character, and passes the
        modified file as icd_path.  The gate regenerates the ICD to a temp
        path and byte-compares; the modification causes the comparison to fail.

        Args:
            tmp_path:  pytest-provided temp directory (routed to framework temp root).
            capsys:    pytest capture fixture for inspecting stderr.
        """
        # Create a byte-modified scratch artifact.
        original_bytes = _ICD_ARTIFACT.read_bytes()
        # Flip the last byte to guarantee a byte mismatch.  The artifact ends
        # with a newline (0x0a); replace with 0x0b to get a different byte.
        assert len(original_bytes) > 0, "Artifact is empty — cannot byte-modify"
        mutated = original_bytes[:-1] + bytes([original_bytes[-1] ^ 0x01])

        scratch = tmp_path / "stale_icd.json"
        scratch.write_bytes(mutated)

        result = _lint.check_freshness(
            icd_path=scratch,
            version_file=_ICD_VERSION_FILE,
        )
        assert result is False, (
            "check_freshness() returned True for a byte-modified artifact.\n"
            "The staleness detection code is not firing — the byte-compare must "
            "return False when the artifact differs from a fresh regeneration."
        )

    def test_stale_artifact_error_names_staleness(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Stale artifact error output names the artifact as stale.

        Verifies that the error message emitted on a byte mismatch includes:
          - the word 'stale' (naming the condition)
          - the regeneration command ('generate-icd.sh')

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting stderr.
        """
        original_bytes = _ICD_ARTIFACT.read_bytes()
        mutated = original_bytes[:-1] + bytes([original_bytes[-1] ^ 0x01])
        scratch = tmp_path / "stale_icd_msg.json"
        scratch.write_bytes(mutated)

        _lint.check_freshness(
            icd_path=scratch,
            version_file=_ICD_VERSION_FILE,
        )
        captured = capsys.readouterr()
        err = captured.err

        assert "stale" in err.lower(), (
            f"Error output does not name the artifact as stale.\n"
            f"stderr: {err!r}\n"
            "Expected the word 'stale' in the error message."
        )
        assert "generate-icd.sh" in err, (
            f"Error output does not state the regeneration command.\n"
            f"stderr: {err!r}\n"
            "Expected 'generate-icd.sh' in the error message so operators know "
            "how to fix the staleness."
        )

    def test_missing_artifact_fails(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """check_freshness() returns False when the artifact file does not exist.

        An absent artifact is treated as stale — it has not been generated and
        committed.  The error output must identify the missing file.

        Args:
            tmp_path:  pytest-provided temp directory.
            capsys:    pytest capture fixture for inspecting stderr.
        """
        absent = tmp_path / "nonexistent_icd.json"
        # Confirm the path does not exist (tmp_path is a fresh directory).
        assert not absent.exists()

        result = _lint.check_freshness(
            icd_path=absent,
            version_file=_ICD_VERSION_FILE,
        )
        assert result is False, (
            "check_freshness() returned True for a nonexistent artifact path.\n"
            "A missing artifact must be treated as stale."
        )
        captured = capsys.readouterr()
        assert captured.err, (
            "check_freshness() emitted no error output for a missing artifact.\n"
            "The error message must name the missing file so operators know what to fix."
        )


# ---------------------------------------------------------------------------
# CLI findings: quality class -- report, record, exit 0
# ---------------------------------------------------------------------------


class TestIcdFreshnessCliFindings:
    """main() records each problem as a finding and exits 0 whatever it finds."""

    def test_stale_artifact_records_one_finding_and_exits_0(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A byte-modified artifact is printed stale, written as one finding, exit 0."""
        original_bytes = _ICD_ARTIFACT.read_bytes()
        scratch = tmp_path / "stale_icd_cli.json"
        scratch.write_bytes(original_bytes[:-1] + bytes([original_bytes[-1] ^ 0x01]))
        findings_file = tmp_path / "findings" / "icd_freshness.json"

        exit_code = _lint.main(
            [
                "--icd",
                str(scratch),
                "--version-file",
                str(_ICD_VERSION_FILE),
                "--findings-file",
                str(findings_file),
            ]
        )

        assert exit_code == 0, "a stale ICD is a quality finding: main() must return 0"
        err = capsys.readouterr().err
        assert "stale" in err.lower(), err
        assert "error:   lint.icd-freshness" in err, err
        findings = json.loads(findings_file.read_text(encoding="utf-8"))
        assert len(findings) == 1, findings
        assert findings[0]["lint"] == "icd_freshness"
        assert findings[0]["severity"] == "quality"
        assert findings[0]["file"] == str(scratch.resolve())
        assert "generate-icd.sh" in findings[0]["remedy"], findings[0]

    def test_fresh_artifact_writes_empty_findings_file(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The committed artifact writes an empty findings array and exits 0."""
        findings_file = tmp_path / "findings.json"

        exit_code = _lint.main(
            [
                "--icd",
                str(_ICD_ARTIFACT),
                "--version-file",
                str(_ICD_VERSION_FILE),
                "--findings-file",
                str(findings_file),
            ]
        )

        assert exit_code == 0
        assert json.loads(findings_file.read_text(encoding="utf-8")) == []
        capsys.readouterr()


def test_regeneration_crash_is_a_could_not_check_finding(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generator that raises leaves freshness unchecked: one finding, exit 0."""
    import pgai_agent_kanban.api.generate_icd as generator

    def _raise(**_kwargs: object) -> None:
        raise RuntimeError("fixture: API dependency unavailable")

    monkeypatch.setattr(generator, "generate_icd", _raise)
    findings_file = tmp_path / "findings.json"

    exit_code = _lint.main(
        [
            "--icd",
            str(_ICD_ARTIFACT),
            "--version-file",
            str(_ICD_VERSION_FILE),
            "--findings-file",
            str(findings_file),
        ]
    )

    assert exit_code == 0
    err = capsys.readouterr().err
    assert "fixture: API dependency unavailable" in err, err
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    assert len(findings) == 1, findings
    assert "could not be checked" in findings[0]["cause"], findings[0]
