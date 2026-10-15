"""
test_deep_verify_bug_reaches_discovery_bundle.py
====================================================
Integration test proving a deep-verify-filed bug reaches the next RC
through the SAME ordinary path any other TESTER finding uses: the
discovery pipeline's bug bundler (team/scripts/lib/discovery.sh,
discovery_step_bugs) picks it up on its next scan and folds it into a
requirements bundle, exactly like team/tests/integration/
test_discovery_pipeline.py's TestBugIntakeToBundleFlow already proves for a
hand-authored bug file.

This test composes two real subprocess invocations against the same
temporary kanban tree, in order:
  1. team/scripts/deep-verify.sh --project <name>, with the cold lane
     stubbed to fail -- files a real BUG-NNNN report via the same code
     path team/tests/integration/test_deep_verify_run.py exercises.
  2. The discovery pipeline (discovery_run_pipeline), against the SAME
     project -- bundles the just-filed bug into requirements/.

No seam is mocked between the two steps: the bug file deep-verify.sh wrote
is read from disk by discovery exactly as an operator's cron-driven pipeline
would read it.

No test invokes a real container build -- the cold lane is stubbed via the
PGAI_DEEP_VERIFY_COLD_LANE_CMD seam. tmp_path is redirected under
$PGAI_AGENT_KANBAN_TEMP_DIR by team/tests/conftest.py's pytest_configure,
so every fixture path this file creates already satisfies the framework's
temp-file hygiene rule.

Test names describe behaviour under test, never bug/task IDs (team/roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

from tests.fixtures.two_project import PROJECT_A
from tests.fixtures.scrubbed_subprocess_env import scrub_agent_scoped_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_DEEP_VERIFY_SCRIPT = _TEAM_DIR / "scripts" / "deep-verify.sh"

_STUB_DOCKERFILE_FAILURE = """\
#!/usr/bin/env bash
echo "E       Failed: docker build of docker/claude-code/Dockerfile failed (exit 1) at step: RUN npm ci; base-image reachability was confirmed" 1>&2
echo "Cold-build lane FAILED (exit code: 1)" 1>&2
exit 1
"""


def _write_stub(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "stub_fail.sh"
    stub.write_text(_STUB_DOCKERFILE_FAILURE, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _file_deep_verify_bug(kanban_root: pathlib.Path, project: str, stub: pathlib.Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_DEEP_VERIFY_COLD_LANE_CMD"] = str(stub)
    # The stubbed lane stands in for the container tier, which runs only
    # with operator authorization and never inside an agent wake.
    scrub_agent_scoped_env(env)
    env["PGAI_CONTAINER_TIER"] = "1"
    return subprocess.run(
        ["bash", str(_DEEP_VERIFY_SCRIPT), "--project", project, "--image", "all"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _run_discovery(kanban_root: pathlib.Path, project_name: str) -> subprocess.CompletedProcess:
    """Thin subprocess wrapper mirroring test_discovery_pipeline.py's own
    _run_discovery() -- sources the real discovery library against the
    caller's temp tree and calls discovery_run_pipeline for one project."""
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)

    env = dict(os.environ)
    env["KANBAN_ROOT"] = str(kanban_root)
    env["TEAM_ROOT"] = str(kanban_root)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["PGAI_PROJECT_NAME"] = project_name
    env["PGAI_DISCOVERY_REJECT_THRESHOLD"] = "3"
    env.pop("PGAI_DEV_TREE_PATH", None)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(kanban_root),
        timeout=60,
    )


def test_deep_verify_filed_bug_is_bundled_by_the_next_discovery_scan(
    two_project_root: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    kanban_root = two_project_root
    stub = _write_stub(tmp_path)

    filing = _file_deep_verify_bug(kanban_root, PROJECT_A, stub)
    assert filing.returncode == 1, filing.stderr

    bugs_dir = kanban_root / "projects" / PROJECT_A / "bugs"
    filed_bugs = list(bugs_dir.glob("BUG-*.md"))
    assert len(filed_bugs) == 1, f"expected exactly one filed bug; found {filed_bugs}"
    filed_bug_name = filed_bugs[0].name

    discovery = _run_discovery(kanban_root, PROJECT_A)
    assert discovery.returncode == 0, (
        f"discovery_run_pipeline exited non-zero.\nstdout: {discovery.stdout}\n"
        f"stderr: {discovery.stderr}"
    )
    assert "DISCOVERY_STATUS=produced_work" in discovery.stdout

    req_dir = kanban_root / "projects" / PROJECT_A / "requirements"
    bundle_files = list(req_dir.glob("*-bugfix-bundle-*.md"))
    assert bundle_files, f"expected a bugfix-bundle-*.md in {req_dir}"
    bundle_text = bundle_files[0].read_text(encoding="utf-8")
    assert filed_bug_name in bundle_text, (
        f"expected the deep-verify-filed bug {filed_bug_name!r} to be named in the "
        f"bundle.\nBundle content:\n{bundle_text}"
    )

    # The source bug file's own ## Status flips to 'running' once bundled --
    # the same transition test_discovery_pipeline.py's
    # test_bug_bundle_marks_source_file_as_running asserts for a
    # hand-authored bug file.
    updated_bug_text = filed_bugs[0].read_text(encoding="utf-8")
    assert "running" in updated_bug_text.lower()
