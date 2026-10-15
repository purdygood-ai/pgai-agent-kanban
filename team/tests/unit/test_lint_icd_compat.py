"""
test_lint_icd_compat.py — Unit tests for team/scripts/lint_icd_compat.py.

Tests cover:

  1. **Positive (clean tree)**: check_compat() with the real docs/api/icd.json and
     docs/api/baselines/ as shipped exits 0 and returns True.

  2. **Behavioral negatives** — each uses a scratch current-ICD written to tmp_path;
     the real baseline (icd-v1.0.0.json) is the reference:
     (a) Removed baseline path: check_compat returns False and the error names the
         specific removed path and states the major-bump policy.
     (b) Removed baseline response field: check_compat returns False and the error
         names the specific removed field and states the major-bump policy.
     (c) New required request field on a baseline endpoint: check_compat returns
         False and the error names the specific new required field and states the
         major-bump policy.

  3. **Behavioral positives** — additive-only changes must pass without touching baselines:
     (a) New path added to current ICD: check_compat returns True.
     (b) New optional field added to a request body in current ICD: check_compat returns
         True.

  4. **Retirement path**: with a scratch SUPPORTED manifest that does NOT list 1.0.0,
     the same break that failed the negatives now passes.  Proves that support is exactly
     what the manifest says — retiring a version makes its protection end.

  5. **Empty-manifest lint error**: a SUPPORTED file with only whitespace makes
     check_compat return False with a loud error naming the empty-manifest condition.

  6. **Missing baseline file**: a version listed in SUPPORTED whose baseline file is
     absent makes check_compat return False.

  7. **Published-baseline immutability (rule e)** (``TestPublishedBaselineImmutability``):
     in a scratch git repository whose main branch carries committed baselines, a
     baseline below the current ICD version whose working-tree bytes differ from the
     main-branch copy is reported naming the file; the current-version baseline, a
     baseline the main branch does not carry, a non-git directory and a missing ref
     are all out of scope (no violation, one-line note); ordering is semver, not
     string order; and check_compat() wires the rule in (returns False).

  8. **Support-table mirror** (``TestSupportTableMirror``): against markdown and
     SUPPORTED fixtures, a version SUPPORTED lists without a table row is reported
     as missing (naming the version and both files), a non-retired row SUPPORTED
     lacks is reported as extra, and a matching table reports no drift.  Retired
     rows, manifest comments/blank lines, a leading ``v``, heading level and case
     are tolerated; a retired row still listed in SUPPORTED, a document without
     the table, and an absent document are each reported.

  9. **Support-table mirror via the CLI** (``TestSupportTableMirrorCli``): main()
     exits 1 on drift with a causes/fix/rule block and 0 when mirrored, while
     check_compat() itself still returns True on the drifting fixture.

All scratch files are written under tmp_path (redirected to the framework temp root
by conftest.py when PGAI_AGENT_KANBAN_TEMP_DIR is set).  No bare /tmp paths.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Locate lint_icd_compat.py, the real ICD artifact, and the baselines dir.
# This file lives at team/tests/unit/test_lint_icd_compat.py.
# Three parent levels up: unit/ -> tests/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent          # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                     # project_root/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LINT_SCRIPT = _SCRIPTS_DIR / "lint_icd_compat.py"
_ICD_ARTIFACT = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"
_BASELINES_DIR = _DEV_TREE_ROOT / "docs" / "api" / "baselines"


def _import_lint_module():
    """Import lint_icd_compat as a module without polluting sys.modules permanently.

    Returns:
        The loaded module object.

    Raises:
        ImportError: if the lint script cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location("lint_icd_compat", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec from {_LINT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Import once at module load time; tests reference `_lint` attributes.
_lint = _import_lint_module()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_icd() -> dict:
    """Return the parsed contents of docs/api/icd.json.

    Returns:
        The ICD document as a Python dict.
    """
    return json.loads(_ICD_ARTIFACT.read_text(encoding="utf-8"))


def _write_icd(path: pathlib.Path, doc: dict) -> None:
    """Write a dict as deterministic JSON to path (sorted keys, 2-space indent, newline).

    Args:
        path: Destination path.
        doc:  Python dict to serialise.
    """
    path.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _scratch_baselines(
    tmp_path: pathlib.Path,
    versions: list[str],
    *,
    baseline_doc: dict | None = None,
) -> pathlib.Path:
    """Build a scratch baselines directory with a SUPPORTED manifest.

    Each version in *versions* gets a corresponding icd-v<version>.json file.
    When *baseline_doc* is None the real docs/api/icd.json content is used.

    Args:
        tmp_path:     A temporary directory (from pytest's tmp_path fixture).
        versions:     List of version strings to list in SUPPORTED and create files for.
        baseline_doc: Override document for all baseline files (default: real icd.json).

    Returns:
        Path to the scratch baselines directory.
    """
    baselines_dir = tmp_path / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    doc = baseline_doc if baseline_doc is not None else _load_icd()
    (baselines_dir / "SUPPORTED").write_text(
        "\n".join(versions) + "\n", encoding="utf-8"
    )
    for version in versions:
        _write_icd(baselines_dir / f"icd-v{version}.json", doc)
    return baselines_dir


# ---------------------------------------------------------------------------
# Positive test: clean tree
# ---------------------------------------------------------------------------


class TestIcdCompatPositive:
    """Clean tree: check_compat returns True for the committed artifacts."""

    def test_clean_tree_passes(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_compat() returns True for the committed docs/api/icd.json.

        At the current contract version (ICD 1.18.5), icd.json was regenerated
        and icd-v1.18.5.json was frozen as a byte-identical copy. SUPPORTED lists
        1.0.0 through 1.18.5. The compat gate must pass because icd.json (1.18.5)
        is a superset of every older baseline (no paths or response fields
        removed and no request field became required; the latest change is
        description-only).

        Args:
            capsys: pytest capture fixture for inspecting stdout/stderr.
        """
        # Deliberately exercises the real docs/api/icd.json and its committed
        # baselines/ (not a fixture): the compat check's whole purpose is to
        # validate this specific pair of artifacts, so there is no isolated
        # fixture substitute that would test the same thing.
        result = _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is True, (
            "check_compat() returned False for the committed artifacts.\n"
            f"Captured stderr: {capsys.readouterr().err}\n"
            "icd.json (1.18.5) must be a compatible superset of every older baseline — "
            "no path, response field or optional request field was removed."
        )

    def test_clean_tree_prints_ok_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_compat() prints a positive confirmation on a clean tree.

        Args:
            capsys: pytest capture fixture.
        """
        _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=_BASELINES_DIR,
        )
        captured = capsys.readouterr()
        assert "ok" in captured.out.lower() or "compatible" in captured.out.lower(), (
            "check_compat() did not print a positive confirmation on a clean tree.\n"
            f"stdout: {captured.out!r}\n"
            "Expected output containing 'ok' or 'compatible'."
        )

    def test_baseline_byte_equality(self) -> None:
        """docs/api/baselines/icd-v1.18.5.json is byte-identical to docs/api/icd.json.

        At the current contract version (ICD 1.18.5), the freeze is
        icd-v1.18.5.json.  The frozen baseline must be byte-identical to the
        current artifact.  icd-v1.0.0.json through icd-v1.18.4.json intentionally
        differ (they are older, published baselines, not the current version).

        This is a structural check, not a compat check; it verifies the freeze was
        done correctly for this RC's version.

        Note: When the ICD version is bumped in a future RC, repoint this test at
        the new current version — the prior version's baseline stays frozen.
        """
        baseline = _BASELINES_DIR / "icd-v1.18.5.json"
        assert baseline.exists(), (
            f"Baseline file does not exist: {baseline}\n"
            "Run the baseline freeze step to create docs/api/baselines/icd-v1.18.5.json "
            "as a byte-identical copy of docs/api/icd.json after regeneration."
        )
        current_bytes = _ICD_ARTIFACT.read_bytes()
        baseline_bytes = baseline.read_bytes()
        assert current_bytes == baseline_bytes, (
            "docs/api/baselines/icd-v1.18.5.json is NOT byte-identical to docs/api/icd.json.\n"
            f"Current size: {len(current_bytes)} bytes\n"
            f"Baseline size: {len(baseline_bytes)} bytes\n"
            "The frozen baseline must be an exact copy of the current artifact at this RC."
        )

    def test_supported_manifest_contains_all_versions(self) -> None:
        """docs/api/baselines/SUPPORTED contains '1.0.0' through '1.18.5'.

        The ICD patch bump (1.18.4 → 1.18.5) removes nothing: 1.0.0 through 1.18.4
        are kept in SUPPORTED so the compat gate continues to prove backward
        compatibility. The 1.18.5 entry reflects the new freeze.

        Args: none — reads the committed manifest file.
        """
        supported = _BASELINES_DIR / "SUPPORTED"
        assert supported.exists(), (
            f"SUPPORTED manifest does not exist at {supported}"
        )
        content = supported.read_text(encoding="utf-8")
        lines = [v.strip() for v in content.splitlines() if v.strip()]
        for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0", "1.8.0", "1.9.0", "1.10.0", "1.11.0", "1.12.0", "1.13.0", "1.13.1", "1.14.0", "1.15.0", "1.16.0", "1.16.1", "1.17.0", "1.17.1", "1.18.0", "1.18.1", "1.18.2", "1.18.3", "1.18.4"):
            assert version in lines, (
                f"SUPPORTED manifest is missing '{version}'.\n"
                f"Got: {lines!r}\n"
                f"ICD {version} must remain in SUPPORTED — kept as a prior version."
            )
        assert "1.18.5" in lines, (
            f"SUPPORTED manifest is missing '1.18.5'.\n"
            f"Got: {lines!r}\n"
            "ICD 1.18.5 must be added to SUPPORTED after the patch bump."
        )


# ---------------------------------------------------------------------------
# Behavioral negative tests
# ---------------------------------------------------------------------------


class TestIcdCompatBehavioralNegative:
    """Behavioral negatives: each break fires with a message naming the violation."""

    def test_removed_path_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Removing a baseline path from the current ICD triggers a compat failure.

        The scratch current-ICD has /approvals deleted.  The real baseline
        (icd-v1.0.0.json) still has /approvals.  The compat gate must detect
        the removal and exit non-zero.

        Args:
            tmp_path: pytest-provided temp directory (routed to framework temp root).
            capsys:   pytest capture fixture for inspecting stderr.
        """
        scratch = _load_icd()
        assert "/approvals" in scratch["paths"], (
            "/approvals not found in icd.json — adjust this test to use a path "
            "that exists in the current ICD."
        )
        del scratch["paths"]["/approvals"]
        scratch_path = tmp_path / "scratch_removed_path.json"
        _write_icd(scratch_path, scratch)

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is False, (
            "check_compat() returned True for a current ICD with /approvals removed.\n"
            "The path-removal detection is not firing."
        )

    def test_removed_path_error_names_break(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Removed-path error names the specific path and states the major-bump policy.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture for inspecting stderr.
        """
        scratch = _load_icd()
        del scratch["paths"]["/approvals"]
        scratch_path = tmp_path / "scratch_removed_path_msg.json"
        _write_icd(scratch_path, scratch)

        _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        err = capsys.readouterr().err

        assert "/approvals" in err, (
            f"Error output does not name the removed path '/approvals'.\n"
            f"stderr: {err!r}"
        )
        assert "breaking changes require a major ICD version" in err, (
            f"Error output does not state the major-bump policy.\n"
            f"stderr: {err!r}\n"
            "Expected: 'breaking changes require a major ICD version and an "
            "operator-approved RC'"
        )

    def test_removed_response_field_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Removing a response field from the current ICD triggers a compat failure.

        The test creates a scratch BASELINE that declares explicit properties on
        the /health endpoint's response schema, then uses the real icd.json as the
        current ICD (which does not declare those properties).  This simulates a
        scenario where a response field present in the baseline has been removed
        from the current ICD.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        # Build a baseline that declares response fields on /health.
        baseline_with_fields = _load_icd()
        baseline_with_fields["paths"]["/health"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"] = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "uptime_seconds": {"type": "integer"},
            },
        }

        scratch_baselines = _scratch_baselines(
            tmp_path,
            versions=["2.0.0"],
            baseline_doc=baseline_with_fields,
        )

        # Current ICD is the real icd.json — it does NOT have those properties.
        result = _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=scratch_baselines,
        )
        assert result is False, (
            "check_compat() returned True when a baseline response field ('status') "
            "is absent from the current ICD.\n"
            "Response-field removal detection is not firing."
        )

    def test_removed_response_field_error_names_break(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Removed-response-field error names the specific field and states the policy.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        baseline_with_fields = _load_icd()
        baseline_with_fields["paths"]["/health"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"] = {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
            },
        }
        scratch_baselines = _scratch_baselines(
            tmp_path,
            versions=["2.0.0"],
            baseline_doc=baseline_with_fields,
        )

        _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=scratch_baselines,
        )
        err = capsys.readouterr().err

        assert "status" in err, (
            f"Error output does not name the removed field 'status'.\n"
            f"stderr: {err!r}"
        )
        assert "breaking changes require a major ICD version" in err, (
            f"Error output does not state the major-bump policy.\n"
            f"stderr: {err!r}"
        )

    def test_new_required_request_field_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Adding a new required request field to a baseline endpoint fails compat.

        The scratch current-ICD adds 'new_required_field' as a required property to
        the AddProjectBody schema used by POST /operations/add-project.  The baseline
        (icd-v1.0.0.json) does not have this field.  The compat gate must detect the
        new required field.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        scratch = _load_icd()
        add_body = scratch["components"]["schemas"]["AddProjectBody"]
        add_body["properties"]["new_required_field"] = {
            "type": "string",
            "title": "New Required Field",
        }
        # Ensure the field is in the required list.
        required = list(add_body.get("required") or [])
        required.append("new_required_field")
        add_body["required"] = required

        scratch_path = tmp_path / "scratch_new_required.json"
        _write_icd(scratch_path, scratch)

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is False, (
            "check_compat() returned True for a current ICD with a new required field "
            "'new_required_field' on POST /operations/add-project.\n"
            "New-required-field detection is not firing."
        )

    def test_new_required_request_field_error_names_break(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """New-required-field error names the field and states the major-bump policy.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        scratch = _load_icd()
        add_body = scratch["components"]["schemas"]["AddProjectBody"]
        add_body["properties"]["new_required_field"] = {
            "type": "string",
            "title": "New Required Field",
        }
        required = list(add_body.get("required") or [])
        required.append("new_required_field")
        add_body["required"] = required

        scratch_path = tmp_path / "scratch_new_required_msg.json"
        _write_icd(scratch_path, scratch)

        _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        err = capsys.readouterr().err

        assert "new_required_field" in err, (
            f"Error output does not name the new required field.\n"
            f"stderr: {err!r}"
        )
        assert "breaking changes require a major ICD version" in err, (
            f"Error output does not state the major-bump policy.\n"
            f"stderr: {err!r}"
        )


# ---------------------------------------------------------------------------
# Behavioral positive tests
# ---------------------------------------------------------------------------


class TestIcdCompatBehavioralPositive:
    """Additive-only changes are legal and must pass without touching baselines."""

    def test_added_path_passes(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Adding a new path to the current ICD does not fail compat.

        The baseline (icd-v1.0.0.json) does not have /new-endpoint; the scratch
        current ICD does.  Adding paths is an additive change and must pass.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        scratch = _load_icd()
        scratch["paths"]["/new-endpoint"] = {
            "get": {
                "operationId": "new_endpoint_get",
                "summary": "Brand new endpoint added additively.",
                "responses": {
                    "200": {
                        "description": "Success.",
                        "content": {"application/json": {"schema": {}}},
                    }
                },
            }
        }
        scratch_path = tmp_path / "scratch_added_path.json"
        _write_icd(scratch_path, scratch)

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is True, (
            "check_compat() returned False for a current ICD with an added path.\n"
            "Adding a new endpoint is an additive change and must pass compat.\n"
            f"stderr: {capsys.readouterr().err!r}"
        )

    def test_added_optional_field_passes(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Adding a new optional field to a request body does not fail compat.

        The baseline (icd-v1.0.0.json) does not have 'extra_hint' on AddProjectBody;
        the scratch current ICD does (as an optional nullable field).  Adding optional
        fields is an additive change and must pass.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        scratch = _load_icd()
        add_body = scratch["components"]["schemas"]["AddProjectBody"]
        add_body["properties"]["extra_hint"] = {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "title": "Extra Hint",
        }
        # NOT added to 'required' — this is an optional field.

        scratch_path = tmp_path / "scratch_added_optional.json"
        _write_icd(scratch_path, scratch)

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is True, (
            "check_compat() returned False for a current ICD with an added optional field.\n"
            "Adding an optional field is an additive change and must pass compat.\n"
            f"stderr: {capsys.readouterr().err!r}"
        )


# ---------------------------------------------------------------------------
# Retirement path
# ---------------------------------------------------------------------------


class TestIcdCompatRetirementPath:
    """Retiring a version from SUPPORTED removes its protection."""

    def test_break_passes_when_version_retired(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A breaking change against 1.0.0 passes when 1.0.0 is removed from SUPPORTED.

        The scratch SUPPORTED manifest lists '2.0.0' only; the icd-v2.0.0.json
        baseline is byte-identical to the scratch current ICD (which has /approvals
        removed).  Because 1.0.0 is not listed, the /approvals removal is not a
        compat violation against any supported baseline, so the gate must pass.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        # Scratch current ICD with /approvals removed.
        scratch = _load_icd()
        del scratch["paths"]["/approvals"]
        scratch_path = tmp_path / "scratch_retired_break.json"
        _write_icd(scratch_path, scratch)

        # Scratch baselines: 2.0.0 only, baseline = the scratch (so they match).
        scratch_baselines = _scratch_baselines(
            tmp_path,
            versions=["2.0.0"],
            baseline_doc=scratch,
        )

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=scratch_baselines,
        )
        assert result is True, (
            "check_compat() returned False for a break against a retired baseline.\n"
            "When 1.0.0 is removed from SUPPORTED, removing /approvals must pass — "
            "the protection has been explicitly retired.\n"
            f"stderr: {capsys.readouterr().err!r}"
        )

    def test_break_still_fails_when_version_still_supported(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Same break fails when 1.0.0 is still in SUPPORTED.

        Controls the retirement test by confirming the identical break fails with the
        real baselines directory (which still lists 1.0.0).

        Args:
            tmp_path: pytest-provided temp directory.
        """
        scratch = _load_icd()
        del scratch["paths"]["/approvals"]
        scratch_path = tmp_path / "scratch_still_supported.json"
        _write_icd(scratch_path, scratch)

        result = _lint.check_compat(
            icd_path=scratch_path,
            baselines_dir=_BASELINES_DIR,
        )
        assert result is False, (
            "check_compat() returned True for a break against a still-supported baseline.\n"
            "Removing /approvals must fail when 1.0.0 is still in SUPPORTED."
        )


# ---------------------------------------------------------------------------
# Empty-manifest lint error
# ---------------------------------------------------------------------------


class TestIcdCompatEmptyManifest:
    """An empty SUPPORTED manifest is a lint error (fail-loud)."""

    def test_empty_manifest_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_compat() returns False and emits an error for an empty SUPPORTED file.

        An empty manifest is not valid — support status must be explicit, never
        inferred.  A compat gate that silently passes on an empty manifest could
        let breaking changes ship undetected.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        empty_baselines = tmp_path / "empty_baselines"
        empty_baselines.mkdir()
        (empty_baselines / "SUPPORTED").write_text("", encoding="utf-8")

        result = _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=empty_baselines,
        )
        assert result is False, (
            "check_compat() returned True for an empty SUPPORTED manifest.\n"
            "An empty manifest must fail loudly — support status must be explicit."
        )

    def test_empty_manifest_error_names_condition(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty-manifest error output names the empty-manifest condition.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        empty_baselines = tmp_path / "empty_baselines_msg"
        empty_baselines.mkdir()
        (empty_baselines / "SUPPORTED").write_text("\n   \n", encoding="utf-8")

        _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=empty_baselines,
        )
        err = capsys.readouterr().err

        assert "empty" in err.lower(), (
            f"Error output does not name the empty-manifest condition.\n"
            f"stderr: {err!r}\n"
            "Expected 'empty' in the error message."
        )

    def test_whitespace_only_manifest_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A SUPPORTED file containing only whitespace is treated as empty.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        baselines_dir = tmp_path / "whitespace_baselines"
        baselines_dir.mkdir()
        (baselines_dir / "SUPPORTED").write_text("  \n\n  \n", encoding="utf-8")

        result = _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=baselines_dir,
        )
        assert result is False, (
            "check_compat() returned True for a whitespace-only SUPPORTED manifest.\n"
            "Whitespace-only content must be treated as empty (fail-loud)."
        )


# ---------------------------------------------------------------------------
# Missing baseline file
# ---------------------------------------------------------------------------


class TestIcdCompatMissingBaseline:
    """A version listed in SUPPORTED whose baseline file is absent is a lint error."""

    def test_missing_baseline_file_fails(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_compat() returns False when a listed baseline file does not exist.

        The scratch SUPPORTED lists '9.9.9', but no icd-v9.9.9.json file is created.
        A missing baseline file is a lint error.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        missing_baselines = tmp_path / "missing_baselines"
        missing_baselines.mkdir()
        (missing_baselines / "SUPPORTED").write_text("9.9.9\n", encoding="utf-8")
        # No icd-v9.9.9.json created.

        result = _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=missing_baselines,
        )
        assert result is False, (
            "check_compat() returned True when the baseline file for '9.9.9' is missing.\n"
            "A missing baseline file must be a lint error."
        )

    def test_missing_baseline_file_error_names_version(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Missing-baseline error output names the missing version.

        Args:
            tmp_path: pytest-provided temp directory.
            capsys:   pytest capture fixture.
        """
        missing_baselines = tmp_path / "missing_baselines_msg"
        missing_baselines.mkdir()
        (missing_baselines / "SUPPORTED").write_text("9.9.9\n", encoding="utf-8")

        _lint.check_compat(
            icd_path=_ICD_ARTIFACT,
            baselines_dir=missing_baselines,
        )
        err = capsys.readouterr().err

        assert "9.9.9" in err, (
            f"Error output does not name the missing version '9.9.9'.\n"
            f"stderr: {err!r}"
        )


# ---------------------------------------------------------------------------
# Published-baseline immutability (rule e)
# ---------------------------------------------------------------------------


class TestPublishedBaselineImmutability:
    """check_published_baselines() reports a rewritten published baseline and nothing else.

    Each test builds a scratch git repository under ``tmp_path`` whose
    ``scratch_main`` branch carries committed baseline files — the stand-in for
    the project's prefixed main branch (the last released tree) — then works on
    a second branch so the working tree can diverge from it.  The real
    ``docs/api/baselines`` and the real ``<branch_prefix>main`` are never read.
    """

    _MAIN = "scratch_main"

    @staticmethod
    def _git(repo: pathlib.Path, *args: str) -> None:
        """Run a git command in *repo* with a fixed identity, failing loudly."""
        subprocess.run(
            ["git", "-c", "user.name=lint-test", "-c", "user.email=lint-test@example.invalid",
             *args],
            cwd=str(repo), check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )

    def _scratch_repo(
        self, tmp_path: pathlib.Path, versions: list[str]
    ) -> tuple[pathlib.Path, pathlib.Path]:
        """Create a git repo whose main branch commits one baseline per version.

        Args:
            tmp_path: pytest-provided temp directory.
            versions: Baseline versions to commit (each file carries the real
                      ICD document stamped with that version).

        Returns:
            ``(repo_root, baselines_dir)``; HEAD is on a work branch off
            ``scratch_main`` so edits do not move the reference branch.
        """
        repo = tmp_path / "repo"
        baselines_dir = repo / "docs" / "api" / "baselines"
        baselines_dir.mkdir(parents=True)
        (baselines_dir / "SUPPORTED").write_text("\n".join(versions) + "\n", encoding="utf-8")
        for version in versions:
            doc = _load_icd()
            doc.setdefault("info", {})["version"] = version
            _write_icd(baselines_dir / f"icd-v{version}.json", doc)
        self._git(repo, "init", "-q")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "published baselines")
        self._git(repo, "branch", "-M", self._MAIN)
        self._git(repo, "checkout", "-q", "-b", "work")
        return repo, baselines_dir

    @staticmethod
    def _mutate(path: pathlib.Path) -> None:
        """Change *path*'s bytes while keeping it valid JSON."""
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.setdefault("info", {})["description"] = "rewritten after publication"
        _write_icd(path, doc)

    def test_mutated_older_baseline_is_reported_naming_the_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A baseline below the current version that differs from main is one violation."""
        repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        self._mutate(baselines_dir / "icd-v1.0.0.json")

        violations = _lint.check_published_baselines(
            "1.1.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        )

        assert len(violations) == 1, f"expected exactly one violation, got {violations!r}"
        assert "icd-v1.0.0.json" in violations[0], violations[0]
        assert self._MAIN in violations[0], violations[0]
        assert "checkout" in violations[0], (
            f"violation does not prescribe the restoring command: {violations[0]}"
        )

    def test_clean_tree_reports_nothing(self, tmp_path: pathlib.Path) -> None:
        """Baselines identical to the main-branch copies produce no violation."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        assert _lint.check_published_baselines(
            "1.1.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        ) == []

    def test_current_version_baseline_is_out_of_scope(self, tmp_path: pathlib.Path) -> None:
        """Only versions strictly below the current ICD version are this rule's business."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        self._mutate(baselines_dir / "icd-v1.1.0.json")
        assert _lint.check_published_baselines(
            "1.1.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        ) == []

    def test_baseline_absent_on_main_is_skipped(self, tmp_path: pathlib.Path) -> None:
        """A baseline the main branch does not carry (introduced by this release) is not compared."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0"])
        doc = _load_icd()
        doc["info"]["version"] = "1.1.0"
        _write_icd(baselines_dir / "icd-v1.1.0.json", doc)  # uncommitted, not on main
        assert _lint.check_published_baselines(
            "1.2.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        ) == []

    def test_ordering_is_semver_not_string_order(self, tmp_path: pathlib.Path) -> None:
        """1.9.0 is below 1.10.0 under semver (string order would place it above and skip it)."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.9.0", "1.10.0"])
        self._mutate(baselines_dir / "icd-v1.9.0.json")
        violations = _lint.check_published_baselines(
            "1.10.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        )
        assert len(violations) == 1 and "icd-v1.9.0.json" in violations[0], violations

    def test_non_git_directory_skips_with_a_note(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A baselines directory outside any git work tree is skipped, never a violation."""
        baselines_dir = _scratch_baselines(tmp_path, ["1.0.0"])
        assert _lint.check_published_baselines(
            "1.1.0", baselines_dir=baselines_dir, main_ref=self._MAIN
        ) == []
        assert "skipped" in capsys.readouterr().out

    def test_missing_ref_skips_with_a_note(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A main ref that does not exist is skipped with a note naming it."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        self._mutate(baselines_dir / "icd-v1.0.0.json")
        assert _lint.check_published_baselines(
            "1.1.0", baselines_dir=baselines_dir, main_ref="no_such_ref"
        ) == []
        assert "no_such_ref" in capsys.readouterr().out

    def test_check_compat_reports_rule_e_and_returns_false(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check_compat() wires the published-baseline rule: a mutation fails the gate."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        self._mutate(baselines_dir / "icd-v1.0.0.json")
        icd = tmp_path / "icd.json"
        doc = _load_icd()
        doc["info"]["version"] = "1.1.0"
        _write_icd(icd, doc)

        ok = _lint.check_compat(icd_path=icd, baselines_dir=baselines_dir, main_ref=self._MAIN)

        assert ok is False
        err = capsys.readouterr().err
        assert "icd-v1.0.0.json" in err and "published" in err, err

    def test_check_compat_passes_when_baselines_match_main(self, tmp_path: pathlib.Path) -> None:
        """check_compat() with untouched baselines and a superset ICD returns True."""
        _repo, baselines_dir = self._scratch_repo(tmp_path, ["1.0.0", "1.1.0"])
        icd = tmp_path / "icd.json"
        doc = _load_icd()
        doc["info"]["version"] = "1.1.0"
        _write_icd(icd, doc)
        assert _lint.check_compat(
            icd_path=icd, baselines_dir=baselines_dir, main_ref=self._MAIN
        ) is True


# ---------------------------------------------------------------------------
# Support-table mirror check
# ---------------------------------------------------------------------------


def _write_support_doc(
    path: pathlib.Path,
    rows: list[tuple[str, str]],
    *,
    heading: str = "### Support table",
) -> pathlib.Path:
    """Write a markdown fixture whose support table carries *rows*.

    Args:
        path:    Destination file.
        rows:    ``(version, status)`` pairs, one table row each.
        heading: The heading line introducing the table.

    Returns:
        *path*, for chaining.
    """
    body = [
        "# Fixture guide",
        "",
        "## 9. ICD",
        "",
        "| Name | Meaning |",
        "|---|---|",
        "| 0.0.1 | an unrelated table before the support table |",
        "",
        heading,
        "",
        "The table mirrors SUPPORTED.",
        "",
        "| Version | Status | Since |",
        "|---|---|---|",
    ]
    body += [f"| {version} | {status} | v2.0.0 |" for version, status in rows]
    body += ["", "Column meanings follow.", "", "### Retiring a version", ""]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _write_manifest(path: pathlib.Path, lines: list[str]) -> pathlib.Path:
    """Write a SUPPORTED fixture with *lines*, one per line.

    Args:
        path:  Destination file.
        lines: Raw manifest lines (versions, comments, or blanks).

    Returns:
        *path*, for chaining.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestSupportTableMirror:
    """find_support_table_drift() reports the support table drifting from SUPPORTED.

    Every fixture — the markdown document and the manifest — is written under
    ``tmp_path``; the real docs/operator-api.md and docs/api/baselines are
    never read.
    """

    def test_version_missing_from_table_is_reported_naming_both_files(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A version SUPPORTED lists without a table row is one 'missing' finding."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0", "1.1.0", "1.2.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "supported"), ("1.1.0", "supported")]
        )

        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)

        assert [(kind, version) for kind, version, _ in findings] == [("missing", "1.2.0")]
        cause = findings[0][2]
        assert "1.2.0" in cause and str(doc) in cause and str(manifest) in cause, cause

    def test_version_extra_in_table_is_reported_as_extra(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A supported table row whose version SUPPORTED lacks is one 'extra' finding."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "supported"), ("1.5.0", "supported")]
        )

        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)

        assert [(kind, version) for kind, version, _ in findings] == [("extra", "1.5.0")]
        cause = findings[0][2]
        assert "extra" in cause and "1.5.0" in cause, cause
        assert str(doc) in cause and str(manifest) in cause, cause

    def test_matching_table_reports_no_drift(self, tmp_path: pathlib.Path) -> None:
        """A table listing exactly the SUPPORTED versions yields no finding."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0", "1.9.0", "1.10.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md",
            [("1.0.0", "supported"), ("1.9.0", "supported"), ("1.10.0", "supported")],
        )
        assert _lint.find_support_table_drift(doc_path=doc, supported_file=manifest) == []
        assert _lint.check_support_table(doc_path=doc, supported_file=manifest) == []

    def test_missing_and_extra_are_reported_together(self, tmp_path: pathlib.Path) -> None:
        """Every drifting version is named in one pass, missing ones first."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0", "1.1.0", "1.2.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "supported"), ("2.0.0", "supported")]
        )
        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)
        assert [(kind, version) for kind, version, _ in findings] == [
            ("missing", "1.1.0"),
            ("missing", "1.2.0"),
            ("extra", "2.0.0"),
        ]

    def test_retired_row_absent_from_manifest_is_not_drift(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A retired row stays in the table after its line leaves SUPPORTED; that is no drift."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.1.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "retired"), ("1.1.0", "supported")]
        )
        assert _lint.find_support_table_drift(doc_path=doc, supported_file=manifest) == []

    def test_retired_row_still_in_manifest_is_reported(self, tmp_path: pathlib.Path) -> None:
        """A row marked retired whose version SUPPORTED still lists is one 'retired' finding."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0", "1.1.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "retired"), ("1.1.0", "supported")]
        )
        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)
        assert [(kind, version) for kind, version, _ in findings] == [("retired", "1.0.0")]

    def test_manifest_comments_blanks_and_v_prefix_are_tolerated(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Comment lines, blank lines, padding and a leading v never read as drift."""
        manifest = _write_manifest(
            tmp_path / "SUPPORTED",
            ["# supported contract versions", "", "  1.0.0  ", "v1.1.0", "   ", "# end"],
        )
        doc = _write_support_doc(
            tmp_path / "guide.md", [("`1.0.0`", "supported"), ("1.1.0", "Supported")]
        )
        assert _lint.read_manifest_versions(manifest) == ["1.0.0", "1.1.0"]
        assert _lint.find_support_table_drift(doc_path=doc, supported_file=manifest) == []

    def test_heading_level_and_case_are_tolerated(self, tmp_path: pathlib.Path) -> None:
        """The table is found under any heading level spelling 'Support table' in any case."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "supported")], heading="## SUPPORT TABLE  "
        )
        assert _lint.find_support_table_drift(doc_path=doc, supported_file=manifest) == []

    def test_document_without_support_table_is_reported(self, tmp_path: pathlib.Path) -> None:
        """No 'Support table' heading is a finding naming the document, not silence."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0"])
        doc = _write_support_doc(
            tmp_path / "guide.md", [("1.0.0", "supported")], heading="### Version list"
        )
        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)
        assert [kind for kind, _, _ in findings] == ["no-table"]
        assert str(doc) in findings[0][2], findings

    def test_missing_document_is_reported(self, tmp_path: pathlib.Path) -> None:
        """An absent support-table document is a finding naming its path."""
        manifest = _write_manifest(tmp_path / "SUPPORTED", ["1.0.0"])
        doc = tmp_path / "absent.md"
        findings = _lint.find_support_table_drift(doc_path=doc, supported_file=manifest)
        assert [kind for kind, _, _ in findings] == ["no-table"]
        assert str(doc) in findings[0][2], findings

    def test_absent_manifest_is_left_to_check_compat(self, tmp_path: pathlib.Path) -> None:
        """With no SUPPORTED file the mirror check stays silent; check_compat reports it."""
        doc = _write_support_doc(tmp_path / "guide.md", [("1.0.0", "supported")])
        assert _lint.find_support_table_drift(
            doc_path=doc, supported_file=tmp_path / "SUPPORTED"
        ) == []


class TestSupportTableMirrorCli:
    """main() runs the mirror check beside check_compat() and exits 1 on drift.

    The ICD and its baselines are minimal fixtures under ``tmp_path`` (no paths,
    so the compatibility rules pass), and ``--main-ref`` names a ref that does
    not exist so the published-baseline rule skips; only the mirror check can
    change the exit status.
    """

    @staticmethod
    def _fixture_tree(
        tmp_path: pathlib.Path, rows: list[tuple[str, str]]
    ) -> list[str]:
        """Build ICD, baselines and support-doc fixtures; return main() argv."""
        doc = {"openapi": "3.1.0", "info": {"title": "fixture", "version": "1.1.0"}, "paths": {}}
        icd = tmp_path / "icd.json"
        _write_icd(icd, doc)
        baselines_dir = tmp_path / "baselines"
        _write_manifest(baselines_dir / "SUPPORTED", ["1.0.0", "1.1.0"])
        for version in ("1.0.0", "1.1.0"):
            _write_icd(baselines_dir / f"icd-v{version}.json", doc)
        support_doc = _write_support_doc(tmp_path / "guide.md", rows)
        return [
            "--icd", str(icd),
            "--baselines-dir", str(baselines_dir),
            "--main-ref", "no_such_ref",
            "--support-doc", str(support_doc),
        ]

    def test_drift_exits_1_with_remedy_block(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing row makes main() return 1 and print causes, fix and rule."""
        argv = self._fixture_tree(tmp_path, [("1.0.0", "supported")])

        assert _lint.main(argv) == 1

        err = capsys.readouterr().err
        assert "missing: version 1.1.0" in err, err
        assert "| 1.1.0 | supported |" in err, err
        assert "To fix:" in err and "Rule:" in err, err

    def test_drift_leaves_check_compat_passing(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Drift changes only the script's exit status; check_compat() still returns True."""
        argv = self._fixture_tree(tmp_path, [("1.0.0", "supported")])
        icd, baselines_dir = pathlib.Path(argv[1]), pathlib.Path(argv[3])

        assert _lint.check_compat(
            icd_path=icd, baselines_dir=baselines_dir, main_ref="no_such_ref"
        ) is True
        assert "support table" not in capsys.readouterr().err

    def test_mirrored_table_exits_0(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A table mirroring SUPPORTED lets main() return 0 with an ok line."""
        argv = self._fixture_tree(
            tmp_path, [("1.0.0", "supported"), ("1.1.0", "supported")]
        )

        assert _lint.main(argv) == 0

        out = capsys.readouterr().out
        assert "mirrors" in out, out
