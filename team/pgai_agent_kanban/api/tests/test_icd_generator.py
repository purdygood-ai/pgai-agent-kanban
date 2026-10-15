"""
test_icd_generator.py — Unit tests for the ICD generator and version source.

Test suites:

  1. **Determinism tests** (``TestIcdGeneratorDeterminism``):
     Run the generator twice and assert the output is byte-identical.  This
     proves that the deterministic-JSON contract holds — two runs on the same
     tree produce the same bytes.

  2. **Version source tests** (``TestIcdVersionSource``):
     Assert that the app's served OpenAPI ``info.version`` equals the contents
     of the ICD_VERSION file, and that the generator stamps the correct version
     in the artifact.

  3. **Package-data parity tests** (``TestIcdPackageDataParity``):
     Assert that ``team/pgai_agent_kanban/api/ICD_VERSION`` (the package-data
     copy that ships with the installed package) is byte-identical to
     ``docs/api/ICD_VERSION`` (the operator-edited canonical source).  Mutating
     either copy alone makes this suite fail, which is the parity gate.

  4. **Live-install shape tests** (``TestIcdLiveInstallShape``):
     Assert that the API app reports the real contract version when the package
     is imported from a layout with no ``docs/`` directory and no ``team/``
     prefix — the topology used by every live install.

All temp files are written under the framework temp root
(PGAI_AGENT_KANBAN_TEMP_DIR), never directly to /tmp.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import pathlib
import subprocess
import sys

import pytest

# ---------------------------------------------------------------------------
# Locate the dev tree root so tests can find docs/api/ICD_VERSION and
# docs/api/icd.json, even when pytest is invoked from team/.
# This file lives at team/pgai_agent_kanban/api/tests/test_icd_generator.py.
# Four parent levels: tests/ → api/ → pgai_agent_kanban/ → team/ → project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent.parent      # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent                        # project_root/
_ICD_VERSION_FILE = _DEV_TREE_ROOT / "docs" / "api" / "ICD_VERSION"
_ICD_ARTIFACT = _DEV_TREE_ROOT / "docs" / "api" / "icd.json"

# Package-data copy — lives alongside app.py in the api/ package directory.
_PACKAGE_API_DIR = _THIS_FILE.parent.parent             # team/pgai_agent_kanban/api/
_PACKAGE_DATA_VERSION_FILE = _PACKAGE_API_DIR / "ICD_VERSION"


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


class TestIcdGeneratorDeterminism:
    """Assert that two generator runs produce byte-identical output.

    Determinism is required for the freshness gate to be reliable: if the
    generator were non-deterministic, the freshness gate would report false
    failures on a clean tree.
    """

    def test_two_runs_produce_byte_identical_output(self, tmp_path: pathlib.Path) -> None:
        """Two generator runs with the same tree produce byte-identical JSON.

        The generator is invoked twice with separate output paths.  The output
        bytes of both runs are compared directly.  Byte equality (not JSON
        semantic equality) is the correct bar: deterministic means the same
        bytes, not just equivalent parsed values.

        Args:
            tmp_path: pytest-provided temp directory (redirected to the framework
                      temp root by the harness conftest.py when the env var is set).
        """
        from pgai_agent_kanban.api.generate_icd import generate_icd

        out1 = tmp_path / "icd_run1.json"
        out2 = tmp_path / "icd_run2.json"

        generate_icd(output_path=out1, icd_version_file=_ICD_VERSION_FILE)
        generate_icd(output_path=out2, icd_version_file=_ICD_VERSION_FILE)

        bytes1 = out1.read_bytes()
        bytes2 = out2.read_bytes()

        assert bytes1 == bytes2, (
            "Generator produced different bytes on two runs — output is not deterministic.\n"
            f"Run 1 size: {len(bytes1)} bytes\n"
            f"Run 2 size: {len(bytes2)} bytes\n"
            "Check for non-deterministic sources (e.g. dict ordering, timestamps, "
            "hash-based iterators).  The generator must use sort_keys=True."
        )

    def test_output_has_sorted_keys(self, tmp_path: pathlib.Path) -> None:
        """Generator output uses sorted JSON keys (verified by re-parsing and re-dumping).

        Sorted keys are required for determinism when the underlying dict
        iteration order changes (e.g. across Python versions or schema evolution).

        Args:
            tmp_path: pytest-provided temp directory.
        """
        import json

        from pgai_agent_kanban.api.generate_icd import generate_icd

        out = tmp_path / "icd_sorted.json"
        generate_icd(output_path=out, icd_version_file=_ICD_VERSION_FILE)

        raw = out.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        # Re-dump with the same parameters: if keys are already sorted, the
        # output is identical to the original.
        canonical = json.dumps(parsed, sort_keys=True, indent=2) + "\n"
        assert raw == canonical, (
            "Generator output keys are not in sorted order.\n"
            "The generator must use json.dumps(data, sort_keys=True, indent=2) + '\\n'."
        )

    def test_output_has_trailing_newline(self, tmp_path: pathlib.Path) -> None:
        """Generator output ends with exactly one trailing newline.

        A trailing newline is required for POSIX compliance and to ensure
        that the artifact is a well-formed text file.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        from pgai_agent_kanban.api.generate_icd import generate_icd

        out = tmp_path / "icd_newline.json"
        generate_icd(output_path=out, icd_version_file=_ICD_VERSION_FILE)

        raw_bytes = out.read_bytes()
        assert raw_bytes.endswith(b"\n"), (
            "Generator output does not end with a trailing newline.\n"
            "The generator must append '\\n' to the JSON output."
        )
        # Must not end with double newline.
        assert not raw_bytes.endswith(b"\n\n"), (
            "Generator output ends with more than one trailing newline."
        )


# ---------------------------------------------------------------------------
# Version source tests
# ---------------------------------------------------------------------------


class TestIcdVersionSource:
    """Assert that info.version in the app and artifact equals ICD_VERSION contents."""

    def test_served_openapi_info_version_equals_icd_version_file(self) -> None:
        """The FastAPI app's served OpenAPI info.version equals ICD_VERSION contents.

        This verifies the single-source-of-truth contract: the live
        /openapi.json endpoint and the ICD_VERSION file must agree.
        """
        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        expected_version = _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

        cfg = ApiConfig()
        app = create_app(cfg=cfg)
        schema = app.openapi()

        actual_version = schema.get("info", {}).get("version")
        assert actual_version == expected_version, (
            f"app.openapi() info.version is {actual_version!r} but "
            f"ICD_VERSION file contains {expected_version!r}.\n"
            "The app factory must read info.version from docs/api/ICD_VERSION."
        )

    def test_generator_stamps_icd_version_file_contents(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The generator stamps info.version from ICD_VERSION, not from elsewhere.

        Verifies that the generated artifact's info.version equals the contents
        of the ICD_VERSION file exactly.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        from pgai_agent_kanban.api.generate_icd import generate_icd

        out = tmp_path / "icd_version_check.json"
        generate_icd(output_path=out, icd_version_file=_ICD_VERSION_FILE)

        artifact = json.loads(out.read_text(encoding="utf-8"))
        actual_version = artifact.get("info", {}).get("version")
        expected_version = _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

        assert actual_version == expected_version, (
            f"Generated artifact info.version is {actual_version!r} but "
            f"ICD_VERSION contains {expected_version!r}.\n"
            "The generator must stamp info.version from docs/api/ICD_VERSION."
        )

    def test_generator_version_matches_served_version(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Artifact info.version equals the live app's served OpenAPI info.version.

        Three witnesses — ICD_VERSION file, served /openapi.json, and the
        committed artifact — must all agree.  This test verifies the
        artifact-vs-served agreement (the file-vs-artifact and file-vs-served
        cases are covered by the other tests in this class).

        Args:
            tmp_path: pytest-provided temp directory.
        """
        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig
        from pgai_agent_kanban.api.generate_icd import generate_icd

        out = tmp_path / "icd_v_match.json"
        generate_icd(output_path=out, icd_version_file=_ICD_VERSION_FILE)

        artifact = json.loads(out.read_text(encoding="utf-8"))
        artifact_version = artifact.get("info", {}).get("version")

        cfg = ApiConfig()
        app = create_app(cfg=cfg)
        served_version = app.openapi().get("info", {}).get("version")

        assert artifact_version == served_version, (
            f"Artifact info.version ({artifact_version!r}) differs from "
            f"served /openapi.json info.version ({served_version!r}).\n"
            "Both must read from the same source: docs/api/ICD_VERSION."
        )

    def test_icd_version_file_contains_expected_version(self) -> None:
        """ICD_VERSION file exists and contains exactly '1.18.5' followed by a newline.

        This test encodes the acceptance criterion directly: the file must exist
        with the current contract version for this RC.

        Note: When the ICD version is bumped in a future RC, update this assertion
        to the new version.
        """
        assert _ICD_VERSION_FILE.exists(), (
            f"ICD_VERSION file not found at {_ICD_VERSION_FILE}.\n"
            "The file must be created at docs/api/ICD_VERSION with content '1.18.5\\n'."
        )
        raw = _ICD_VERSION_FILE.read_bytes()
        assert raw == b"1.18.5\n", (
            f"ICD_VERSION file content is {raw!r}; expected b'1.18.5\\n'.\n"
            "The file must contain exactly '1.18.5' followed by a single newline."
        )


# ---------------------------------------------------------------------------
# Artifact content tests
# ---------------------------------------------------------------------------


class TestIcdArtifactContent:
    """Assert that the committed docs/api/icd.json contains expected paths."""

    def test_committed_artifact_contains_required_paths(self) -> None:
        """The committed icd.json artifact contains every required registered path.

        Spot-checks the paths named in the acceptance criteria.  This test
        runs against the committed artifact file, not a freshly generated one,
        so it fails if the artifact was not committed (complementing the freshness gate).
        """
        assert _ICD_ARTIFACT.exists(), (
            f"ICD artifact not found at {_ICD_ARTIFACT}.\n"
            "Run 'bash team/scripts/generate-icd.sh' and commit docs/api/icd.json."
        )
        artifact = json.loads(_ICD_ARTIFACT.read_text(encoding="utf-8"))
        paths = set(artifact.get("paths", {}).keys())

        required_paths = [
            "/board",
            "/approvals",
            "/auto-resolutions",
            "/verdicts",
            "/agent-metrics",
            "/show-queues",
            "/verify-rc-state",
            "/release-locks",
            "/release-state",
            "/deep-verify-status",
            "/operations/create-project",
            "/operations/defer",
            "/operations/undefer",
            "/operations/deep-verify",
            "/operations/wake-now",
            "/operations/cold-build-now",
            "/operations/cancel-rc",
            "/operations/release-locks",
            "/operations/archive",
            "/logs/{kind}",
            "/traces",
        ]
        missing = [p for p in required_paths if p not in paths]
        assert not missing, (
            f"Required paths missing from docs/api/icd.json: {missing}\n"
            "Run 'bash team/scripts/generate-icd.sh' to regenerate."
        )

    def test_committed_artifact_info_version_is_1_18_5(self) -> None:
        """The committed icd.json has info.version == '1.18.5'.

        Verifies the artifact reflects the current ICD contract version
        (description-only patch bump: 1.18.4 → 1.18.5): GET /verify-rc-state
        documents the in-process call to ops.rc_state.verify_rc_state and
        GET /board names both quarantine locations. No route or field
        changed.
        """
        assert _ICD_ARTIFACT.exists(), (
            f"ICD artifact not found at {_ICD_ARTIFACT}.\n"
            "Run 'bash team/scripts/generate-icd.sh' and commit docs/api/icd.json."
        )
        artifact = json.loads(_ICD_ARTIFACT.read_text(encoding="utf-8"))
        version = artifact.get("info", {}).get("version")
        assert version == "1.18.5", (
            f"docs/api/icd.json info.version is {version!r}; expected '1.18.5'.\n"
            "Regenerate with 'bash team/scripts/generate-icd.sh'."
        )


# ---------------------------------------------------------------------------
# Wrapper CWD regression tests
# ---------------------------------------------------------------------------


class TestIcdWrapperCwdRegression:
    """Regression tests for the generate-icd.sh wrapper invoked from the repo root.

    These tests invoke the wrapper via subprocess with cwd set to the repository
    root, covering the failure mode where the wrapper exited 1 with
    ModuleNotFoundError because PYTHONPATH was not set.  The existing module-level
    tests never exercised this path (they import the Python module directly),
    which allowed the wrapper defect to ship undetected.

    All output is diverted to the framework temp root; the committed
    docs/api/icd.json is not modified by these tests.
    """

    def _wrapper_path(self) -> pathlib.Path:
        """Return the absolute path to the generate-icd.sh wrapper.

        Returns:
            Path to team/scripts/generate-icd.sh relative to the dev tree root.
        """
        return _TEAM_DIR / "scripts" / "generate-icd.sh"

    def test_wrapper_exits_0_from_repo_root(self, tmp_path: pathlib.Path) -> None:
        """Invoking generate-icd.sh from the repo root exits 0.

        The wrapper sets PYTHONPATH itself before invoking the generator, so
        it succeeds regardless of caller cwd — the pgai_agent_kanban package
        is only importable when team/ is on the path, and without the
        wrapper's own PYTHONPATH handling it would exit 1 with
        ModuleNotFoundError from a repo-root invocation.

        The output is diverted to a temp path so the committed docs/api/icd.json
        is not modified by this test.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        out = tmp_path / "icd_cwd_test.json"
        wrapper = self._wrapper_path()

        result = subprocess.run(
            ["bash", str(wrapper), "--output", str(out)],
            cwd=str(_DEV_TREE_ROOT),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            "generate-icd.sh exited non-zero when invoked from the repo root.\n"
            f"CWD: {_DEV_TREE_ROOT}\n"
            f"Wrapper: {wrapper}\n"
            f"stdout: {result.stdout!r}\n"
            f"stderr: {result.stderr!r}\n"
            "Ensure PYTHONPATH includes team/ before the exec python3 -m call."
        )

    def test_wrapper_output_matches_committed_artifact(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Wrapper output from the repo root is byte-identical to the committed artifact.

        This verifies that the fix does not alter the generator output — the
        PYTHONPATH change is transparent to the JSON content produced.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        out = tmp_path / "icd_cwd_compare.json"
        wrapper = self._wrapper_path()

        result = subprocess.run(
            ["bash", str(wrapper), "--output", str(out)],
            cwd=str(_DEV_TREE_ROOT),
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"Wrapper exited {result.returncode}; stderr: {result.stderr!r}"
        )
        assert out.exists(), f"Wrapper exited 0 but --output file was not created at {out}"

        generated_bytes = out.read_bytes()
        committed_bytes = _ICD_ARTIFACT.read_bytes()

        assert generated_bytes == committed_bytes, (
            "Wrapper output from repo root differs from the committed docs/api/icd.json.\n"
            f"Generated size: {len(generated_bytes)} bytes\n"
            f"Committed size: {len(committed_bytes)} bytes\n"
            "The wrapper must not alter generator output — run the generator and commit "
            "docs/api/icd.json if the schema changed."
        )

    def test_wrapper_two_runs_byte_identical_from_repo_root(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two consecutive wrapper invocations from the repo root produce byte-identical output.

        Verifies that determinism holds end-to-end through the wrapper, not only
        when calling the Python module directly.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        out1 = tmp_path / "icd_cwd_run1.json"
        out2 = tmp_path / "icd_cwd_run2.json"
        wrapper = self._wrapper_path()

        for out in (out1, out2):
            result = subprocess.run(
                ["bash", str(wrapper), "--output", str(out)],
                cwd=str(_DEV_TREE_ROOT),
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"Wrapper exited {result.returncode} on run to {out}; "
                f"stderr: {result.stderr!r}"
            )

        assert out1.read_bytes() == out2.read_bytes(), (
            "Two wrapper invocations from the repo root produced different output.\n"
            "The generator must be deterministic end-to-end through the wrapper."
        )


# ---------------------------------------------------------------------------
# Package-data parity tests
# ---------------------------------------------------------------------------


class TestIcdPackageDataParity:
    """Assert that the package-data ICD_VERSION is byte-identical to the canonical docs copy.

    The parity gate: if either copy is mutated alone (for example, a version
    bump updates ``docs/api/ICD_VERSION`` but the operator forgets to run
    ``generate-icd.sh``), these tests fail and name both paths so the operator
    knows exactly what to fix.

    The ``generate_icd()`` function is the one-step mechanism that keeps both
    copies in sync.  Running it updates ``docs/api/ICD_VERSION`` (the canonical
    source) and ``team/pgai_agent_kanban/api/ICD_VERSION`` (the package-data
    copy) in a single invocation.
    """

    def test_package_data_version_file_exists(self) -> None:
        """The package-data ICD_VERSION file exists in the api/ package directory.

        Verifies the file was created at the expected path.  A missing file
        means the package was not updated to include the ICD_VERSION resource.
        """
        assert _PACKAGE_DATA_VERSION_FILE.exists(), (
            f"Package-data ICD_VERSION not found at {_PACKAGE_DATA_VERSION_FILE}.\n"
            "The file must be present in the api/ package directory so the installed\n"
            "package can read the contract version via importlib.resources.\n"
            "Run 'bash team/scripts/generate-icd.sh' to create and sync the file."
        )

    def test_package_data_version_is_byte_identical_to_docs_version(self) -> None:
        """Package-data ICD_VERSION is byte-identical to docs/api/ICD_VERSION.

        This is the parity gate.  Mutating either copy alone (e.g. bumping the
        docs copy without running the generator, or editing the package copy
        by hand) causes this test to fail.  The error message names both paths
        so the operator knows exactly which files are out of sync.

        The correct fix is always to run ``bash team/scripts/generate-icd.sh``,
        which updates both copies in a single step.
        """
        assert _ICD_VERSION_FILE.exists(), (
            f"Docs ICD_VERSION not found at {_ICD_VERSION_FILE}.\n"
            "The canonical source file must exist at docs/api/ICD_VERSION."
        )
        assert _PACKAGE_DATA_VERSION_FILE.exists(), (
            f"Package-data ICD_VERSION not found at {_PACKAGE_DATA_VERSION_FILE}.\n"
            f"Canonical source: {_ICD_VERSION_FILE}\n"
            "Run 'bash team/scripts/generate-icd.sh' to sync the package-data copy."
        )

        docs_bytes = _ICD_VERSION_FILE.read_bytes()
        pkg_bytes = _PACKAGE_DATA_VERSION_FILE.read_bytes()

        assert docs_bytes == pkg_bytes, (
            "ICD_VERSION copies are not byte-identical — the parity gate detected drift.\n"
            f"  Canonical (docs): {_ICD_VERSION_FILE}\n"
            f"    content: {docs_bytes!r}\n"
            f"  Package-data:     {_PACKAGE_DATA_VERSION_FILE}\n"
            f"    content: {pkg_bytes!r}\n"
            "Fix: run 'bash team/scripts/generate-icd.sh' to update both copies at once."
        )

    def test_importlib_resources_reads_package_data_version(self) -> None:
        """importlib.resources can read ICD_VERSION from the api package.

        Verifies that the file is discoverable via the package-data API, not
        just as a filesystem path.  This is the read path used by app.py on
        any install topology.
        """
        resource_text = (
            importlib.resources.files("pgai_agent_kanban.api")
            .joinpath("ICD_VERSION")
            .read_text(encoding="utf-8")
            .strip()
        )
        expected = _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

        assert resource_text == expected, (
            f"importlib.resources read {resource_text!r} from the package, but\n"
            f"docs/api/ICD_VERSION contains {expected!r}.\n"
            f"Package-data path: {_PACKAGE_DATA_VERSION_FILE}\n"
            "Run 'bash team/scripts/generate-icd.sh' to sync the package-data copy."
        )
        assert resource_text != "unknown", (
            "importlib.resources returned 'unknown' — the package-data ICD_VERSION\n"
            "is missing or empty.  The 'unknown' fallback must be unreachable on a\n"
            "correctly assembled package.\n"
            f"Expected path: {_PACKAGE_DATA_VERSION_FILE}"
        )


# ---------------------------------------------------------------------------
# Live-install shape tests
# ---------------------------------------------------------------------------


class TestIcdLiveInstallShape:
    """Assert the app reports the real contract version from a live-install-shaped tree.

    Live installs relocate the package: the ``team/`` prefix is dropped, and
    the ``docs/`` directory is never deployed.  An ``app.py`` that navigates
    four parents up from ``__file__`` to find ``docs/api/ICD_VERSION`` fails
    silently and returns ``"unknown"`` on live installs.

    Instead, ``app.py`` reads ICD_VERSION via ``importlib.resources``
    from the package data.  The resource travels with the package regardless
    of install topology.

    These tests verify the package-data lookup end-to-end:

    1. A subprocess test that imports the package from a live-install-shaped
       temp tree (no ``docs/``, no ``team/`` prefix) and asserts the real version
       is served.

    2. A unit test that directly calls ``create_app()`` and verifies the version
       is the real one (not ``"unknown"``), relying on the package-data resource.
    """

    def test_create_app_serves_real_version_not_unknown(self) -> None:
        """create_app() serves the real contract version, not 'unknown'.

        Verifies that the factory reads ICD_VERSION from the package-data
        resource (via importlib.resources) rather than via a dev-tree-relative
        path.  This test passes regardless of where __file__ points.
        """
        from pgai_agent_kanban.api.app import create_app
        from pgai_agent_kanban.api.config import ApiConfig

        expected_version = _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

        cfg = ApiConfig()
        app = create_app(cfg=cfg)
        schema = app.openapi()
        actual_version = schema.get("info", {}).get("version")

        assert actual_version != "unknown", (
            "app.openapi() returned 'unknown' for info.version.\n"
            "The factory must read ICD_VERSION from package data via importlib.resources,\n"
            "not from a dev-tree-relative path.  The 'unknown' fallback is unreachable\n"
            "on a correctly assembled package."
        )
        assert actual_version == expected_version, (
            f"app.openapi() info.version is {actual_version!r}; expected {expected_version!r}.\n"
            f"Package-data path: {_PACKAGE_DATA_VERSION_FILE}"
        )

    def test_live_install_shape_subprocess(self, tmp_path: pathlib.Path) -> None:
        """App serves the real version when run from a live-install-shaped tree.

        Copies the pgai_agent_kanban package out of ``team/`` into a temp
        directory (no ``team/`` prefix, no ``docs/`` sibling), then invokes
        a subprocess that imports the package from that temp location.  The
        subprocess asserts that ``create_app().openapi()['info']['version']``
        is not ``'unknown'`` and equals the expected version.

        This is the closest in-test approximation of a live deployment where
        the dev tree layout is absent.

        Args:
            tmp_path: pytest-provided temp directory.
        """
        import shutil

        # Build a live-install-shaped tree: copy the package to a temp dir
        # without any surrounding team/ prefix or docs/ directory.
        src_package = _PACKAGE_API_DIR.parent   # team/pgai_agent_kanban/
        dst_package = tmp_path / "pgai_agent_kanban"
        shutil.copytree(str(src_package), str(dst_package))

        expected_version = _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

        # Script that imports from the relocated package and checks the version.
        check_script = (
            "import sys, importlib\n"
            "from pgai_agent_kanban.api.app import create_app\n"
            "from pgai_agent_kanban.api.config import ApiConfig\n"
            "app = create_app(cfg=ApiConfig())\n"
            "v = app.openapi().get('info', {}).get('version', 'MISSING')\n"
            f"expected = {expected_version!r}\n"
            "if v == 'unknown':\n"
            "    sys.exit(f'FAIL: version is unknown (live-install-shape fixture)')\n"
            "if v != expected:\n"
            "    sys.exit(f'FAIL: version {{v!r}} != expected {{expected!r}}')\n"
            "print(f'PASS: version={{v!r}}')\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", check_script],
            cwd=str(tmp_path),
            env={**os.environ, "PYTHONPATH": str(tmp_path)},
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            "Live-install-shaped fixture subprocess failed.\n"
            f"  Tree root: {tmp_path}\n"
            f"  Expected version: {expected_version!r}\n"
            f"  stdout: {result.stdout!r}\n"
            f"  stderr: {result.stderr!r}\n"
            "The app factory must read ICD_VERSION from package data, not a dev-tree path."
        )


# ---------------------------------------------------------------------------
# Baseline immutability tests
# ---------------------------------------------------------------------------


class TestIcdBaselineImmutability:
    """Assert the generator never rewrites a published baseline by regeneration.

    A baseline file ``docs/api/baselines/icd-v<X.Y.Z>.json`` whose version is
    listed in ``SUPPORTED`` has been published to clients and is immutable.
    Regenerating after a route is added, with ``ICD_VERSION`` unchanged at a
    published version, must therefore refuse dual-channel and leave the
    baseline byte-untouched; regenerating after ``ICD_VERSION`` is bumped
    above every existing baseline must create the new baseline and leave the
    older ones byte-identical.

    Every test builds a scratch tree under ``tmp_path`` — its own
    ``ICD_VERSION``, ``SUPPORTED``, baselines directory and package-data copy
    — and points the generator's ``canonical_output_path`` at that tree's
    ``icd.json`` so the freeze policy runs there.  The real
    ``docs/api/baselines`` is never read or written.
    """

    def _scratch_tree(
        self,
        tmp_path: pathlib.Path,
        version: str,
        supported: list[str],
        baselines: dict[str, bytes],
    ) -> dict:
        """Build a scratch dev-tree layout and return generate_icd keyword arguments.

        Args:
            tmp_path:  pytest-provided temp directory.
            version:   Contents of the scratch ``ICD_VERSION``.
            supported: Versions to list in the scratch ``SUPPORTED`` manifest.
            baselines: ``{version: bytes}`` baseline files to seed.

        Returns:
            Keyword arguments for ``generate_icd`` targeting the scratch tree only.
        """
        root = tmp_path / "tree"
        baselines_dir = root / "docs" / "api" / "baselines"
        baselines_dir.mkdir(parents=True)
        (root / "pkg").mkdir()
        version_file = root / "docs" / "api" / "ICD_VERSION"
        version_file.write_text(version + "\n", encoding="utf-8")
        (baselines_dir / "SUPPORTED").write_text(
            "\n".join(supported) + "\n", encoding="utf-8"
        )
        for bl_version, data in baselines.items():
            (baselines_dir / f"icd-v{bl_version}.json").write_bytes(data)
        output = root / "docs" / "api" / "icd.json"
        return {
            "output_path": output,
            "icd_version_file": version_file,
            "package_version_file": root / "pkg" / "ICD_VERSION",
            "baselines_dir": baselines_dir,
            "canonical_output_path": output,
        }

    @staticmethod
    def _current_version() -> str:
        """Return the real contract version — the one listed in SUPPORTED today."""
        return _ICD_VERSION_FILE.read_text(encoding="utf-8").strip()

    @staticmethod
    def _next_minor(version: str) -> str:
        """Return the additive bump after *version* via the framework semver helper."""
        from pgai_agent_kanban.api.generate_icd import _import_semver

        major, minor, _patch = _import_semver().parse(version)
        return f"{major}.{minor + 1}.0"

    @staticmethod
    def _published_before_route(version: str) -> bytes:
        """Return a baseline as it was published before one route was added.

        Takes the committed artifact, stamps *version*, and drops one path —
        the contract clients pinned on before the current tree gained a route.
        Any fresh regeneration therefore differs from it.

        Args:
            version: ``info.version`` to stamp on the baseline.

        Returns:
            Deterministic JSON bytes (sorted keys, indent 2, trailing newline).
        """
        doc = json.loads(_ICD_ARTIFACT.read_text(encoding="utf-8"))
        doc.setdefault("info", {})["version"] = version
        doc["paths"].pop(sorted(doc["paths"])[-1])
        return (json.dumps(doc, sort_keys=True, indent=2) + "\n").encode("utf-8")

    def test_regenerate_at_published_version_refuses_and_leaves_baseline_untouched(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Published baseline + new route + regenerate at the same version -> refusal.

        The seeded baseline is the contract as published (one route fewer than
        the tree builds today) and its version is listed in SUPPORTED.  The
        generator must exit 1, print the dual-channel error contract naming
        the baseline file and the ICD_VERSION bump step, and leave the
        baseline byte-identical.  ``icd.json`` itself is still written, so the
        mid-release flow (add a route now, bump later) keeps a fresh artifact.
        """
        from pgai_agent_kanban.api.generate_icd import (
            PUBLISHED_BASELINE_ERROR_KEY,
            generate_icd,
        )
        from pgai_agent_kanban.lib.error_contract import parse_error_contract

        version = self._current_version()
        published = self._published_before_route(version)
        kw = self._scratch_tree(tmp_path, version, [version], {version: published})
        baseline = kw["baselines_dir"] / f"icd-v{version}.json"

        with pytest.raises(SystemExit) as excinfo:
            generate_icd(**kw)

        assert excinfo.value.code == 1, (
            f"generate_icd exited {excinfo.value.code!r} on a published-baseline "
            "conflict; the refusal must exit 1."
        )
        assert baseline.read_bytes() == published, (
            f"Published baseline {baseline} was rewritten by regeneration.\n"
            "A baseline listed in SUPPORTED must be left byte-untouched."
        )
        assert kw["output_path"].exists(), (
            "icd.json was not written before the baseline refusal; the artifact "
            "itself must still regenerate so the release's bump step can follow."
        )

        contract = parse_error_contract(capsys.readouterr().err)
        assert contract is not None, (
            "Refusal did not print the error/message/remedy house shape on stderr."
        )
        assert contract["error"] == PUBLISHED_BASELINE_ERROR_KEY
        assert str(baseline) in contract["message"], (
            f"Refusal message does not name the baseline file {baseline}:\n"
            f"{contract['message']}"
        )
        assert "ICD_VERSION" in contract["message"], (
            "Refusal message does not name the ICD_VERSION bump step."
        )
        assert str(kw["icd_version_file"]) in contract["remedy"], (
            f"Remedy does not edit the ICD_VERSION file {kw['icd_version_file']}:\n"
            f"{contract['remedy']}"
        )
        assert "generate-icd.sh" in contract["remedy"]
        assert "<" not in contract["remedy"], (
            f"Remedy carries an angle-bracket placeholder; it must be runnable as "
            f"printed: {contract['remedy']}"
        )

    def test_bump_creates_new_baseline_and_leaves_older_ones_byte_identical(
        self, tmp_path: pathlib.Path
    ) -> None:
        """ICD_VERSION bumped above every existing baseline -> new baseline frozen, old untouched.

        The older baseline is seeded with bytes that differ from anything the
        generator produces, so any write to it would be detected.
        """
        from pgai_agent_kanban.api.generate_icd import generate_icd

        old_version = self._current_version()
        new_version = self._next_minor(old_version)
        old_bytes = self._published_before_route(old_version)
        kw = self._scratch_tree(
            tmp_path, new_version, [old_version], {old_version: old_bytes}
        )

        generate_icd(**kw)

        new_baseline = kw["baselines_dir"] / f"icd-v{new_version}.json"
        old_baseline = kw["baselines_dir"] / f"icd-v{old_version}.json"
        assert new_baseline.exists(), (
            f"Bumping ICD_VERSION to {new_version} did not create {new_baseline}."
        )
        assert new_baseline.read_bytes() == kw["output_path"].read_bytes(), (
            "New baseline is not byte-identical to the freshly written icd.json."
        )
        assert old_baseline.read_bytes() == old_bytes, (
            f"Older baseline {old_baseline} was modified by a bump regeneration; "
            "every older baseline must stay byte-identical."
        )
        assert json.loads(new_baseline.read_text(encoding="utf-8"))["info"]["version"] == new_version

    def test_refreeze_flag_rewrites_published_baseline(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--refreeze is the explicit override: the published baseline is rewritten, loudly."""
        from pgai_agent_kanban.api.generate_icd import generate_icd

        version = self._current_version()
        published = self._published_before_route(version)
        kw = self._scratch_tree(tmp_path, version, [version], {version: published})
        baseline = kw["baselines_dir"] / f"icd-v{version}.json"

        generate_icd(refreeze=True, **kw)

        assert baseline.read_bytes() == kw["output_path"].read_bytes(), (
            "--refreeze did not rewrite the published baseline to the new artifact."
        )
        assert baseline.read_bytes() != published
        assert "--refreeze" in capsys.readouterr().err, (
            "Re-freezing a published baseline must be narrated on stderr."
        )

    def test_regenerate_at_published_version_with_identical_bytes_is_not_a_refusal(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Idempotent regeneration: a published baseline equal to the artifact passes untouched."""
        from pgai_agent_kanban.api.generate_icd import generate_icd

        old_version = self._current_version()
        new_version = self._next_minor(old_version)
        kw = self._scratch_tree(tmp_path, new_version, [old_version], {})
        generate_icd(**kw)  # the bump: freezes icd-v<new_version>.json
        baseline = kw["baselines_dir"] / f"icd-v{new_version}.json"
        frozen = baseline.read_bytes()

        # Publish it, then regenerate at the same version with nothing changed.
        (kw["baselines_dir"] / "SUPPORTED").write_text(
            f"{old_version}\n{new_version}\n", encoding="utf-8"
        )
        generate_icd(**kw)

        assert baseline.read_bytes() == frozen, (
            "An identical regeneration changed the published baseline's bytes."
        )

    def test_unpublished_existing_baseline_is_refreshed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A baseline frozen by a bump but not yet in SUPPORTED follows the artifact."""
        from pgai_agent_kanban.api.generate_icd import generate_icd

        old_version = self._current_version()
        new_version = self._next_minor(old_version)
        stale = b'{"info": {"version": "' + new_version.encode() + b'"}, "paths": {}}\n'
        kw = self._scratch_tree(
            tmp_path, new_version, [old_version], {new_version: stale}
        )

        generate_icd(**kw)

        baseline = kw["baselines_dir"] / f"icd-v{new_version}.json"
        assert baseline.read_bytes() == kw["output_path"].read_bytes(), (
            f"Unpublished baseline {baseline} (not in SUPPORTED) was not refreshed."
        )

    def test_non_canonical_output_never_touches_baselines(
        self, tmp_path: pathlib.Path
    ) -> None:
        """--output PATH (a scratch regeneration) applies no baseline policy at all.

        The freshness gate regenerates to a temp path; with a published,
        differing baseline in place that must neither refuse nor rewrite.
        """
        from pgai_agent_kanban.api.generate_icd import generate_icd

        version = self._current_version()
        published = self._published_before_route(version)
        kw = self._scratch_tree(tmp_path, version, [version], {version: published})
        kw["output_path"] = tmp_path / "scratch-output.json"  # not the canonical path

        generate_icd(**kw)

        baseline = kw["baselines_dir"] / f"icd-v{version}.json"
        assert baseline.read_bytes() == published
        assert kw["output_path"].exists()

    def test_wrapper_help_documents_refreeze_and_accepts_the_flag(
        self, tmp_path: pathlib.Path
    ) -> None:
        """generate-icd.sh forwards --refreeze and its --help names the flag.

        Runs with a non-canonical --output so no baseline in the dev tree is
        consulted; the point is that the flag reaches the module unchanged.
        """
        wrapper = _TEAM_DIR / "scripts" / "generate-icd.sh"
        header = wrapper.read_text(encoding="utf-8")
        assert "--refreeze" in header, (
            "generate-icd.sh's usage header does not document --refreeze."
        )

        help_run = subprocess.run(
            ["bash", str(wrapper), "--help"],
            cwd=str(_DEV_TREE_ROOT), capture_output=True, text=True,
        )
        assert help_run.returncode == 0
        assert "--refreeze" in help_run.stdout

        out = tmp_path / "icd_refreeze_flag.json"
        run = subprocess.run(
            ["bash", str(wrapper), "--refreeze", "--output", str(out)],
            cwd=str(_DEV_TREE_ROOT), capture_output=True, text=True,
        )
        assert run.returncode == 0, (
            f"generate-icd.sh --refreeze --output exited {run.returncode}; "
            f"stderr: {run.stderr!r}"
        )
        assert out.exists()
