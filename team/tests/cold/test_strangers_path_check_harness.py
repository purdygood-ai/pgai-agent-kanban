"""
test_strangers_path_check_harness.py
=====================================
Container-tier test for team/scripts/harness/strangers-path-check.sh — the
stranger's-path run-through fixture: a fresh container, the documented
install, the board up, and the helloworld demo shipping, with every
executed command taken from the shipped documents rather than transcribed.

Coverage
--------
This module proves the ONE property that matters most for a fixture whose
whole value is "extract, don't transcribe": a documented step that has been
edited to break still gets extracted from the (edited) document and still
makes the fixture fail, naming the document and the line the broken command
came from. That is what stands between this fixture being a real check on
the documentation and being a fixture that quietly tests a copy of itself.

  - Seeding a broken flag into docs/docker.md's Step 5 install command, in
    a scratch copy of the document passed via `--doc-root`, makes the
    fixture's `install` stage report FAIL and cite `docs/docker.md:<line>`
    — proving the fixture reads the command from the document it was
    pointed at, not from a copy baked into the fixture itself.
  - The real (unmodified) checkout's own docs/docker.md is never touched —
    only a scratch copy under tmp_path is edited — so this test carries no
    risk of leaving the dev tree's real documentation dirty.

Where it runs
-------------
The harness is operator-only (team/scripts/lib/container_tier.sh): its
container stage builds the shipped image and starts a container, so this
module lives in team/tests/cold/ and runs only through
team/scripts/run-cold-build-tests.sh, never in the fast suites. The
operator's authorization (PGAI_CONTAINER_TIER) is forwarded from the lane's
own environment to the harness subprocess; without it the harness refuses
and this test fails naming the refusal. The lane's image selection
(PGAI_CONTAINER_IMAGES) is passed as --image flags, every declared image
when the lane exported none.

Design notes
------------
  - No seam is mocked. The subprocess is the real harness script, driving
    the real doc_step_extract.py against a real (deliberately broken)
    scratch document and the real `./install.sh` in the real checkout.
  - Container and board coverage (docker build, tmux/pseudocron, the tmux
    dashboard) is left to manual, evidence-quoted runs and is not
    automated here:
    a docker build is exactly the class of expensive, environment-coupled
    step team/scripts/run-cold-build-tests.sh's own header says does not
    belong in the fast suites. This module's `install`-stage break is
    enough to prove the extract-don't-transcribe property end to end
    without paying that cost on every run-integration-tests.sh pass — the
    `container` stage still runs for real underneath this test (it is not
    skipped), but its own verdict (PASS, FAIL, or COULD-NOT-ATTEMPT
    depending on what the test host has) is not what this module asserts
    on, and a slow or engine-absent host never fails this test over it.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

from tests.fixtures.container_images import load_selected_container_images
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_HARNESS = _TEAM_DIR / "scripts" / "harness" / "strangers-path-check.sh"
_REAL_DOCKER_DOC = _REPO_ROOT / "docs" / "docker.md"

_INSTALL_STEP5_ORIGINAL = "./install.sh --no-system-cron --add-claude-agents"
_INSTALL_STEP5_BROKEN = "./install.sh --no-system-cron --this-flag-does-not-exist"


def _harness_image_args() -> list:
    """The harness's --image flags: one per image the cold lane itself was
    run with (PGAI_CONTAINER_IMAGES, which the lane exports and
    build_scrubbed_env does not carry), or every declared image when the
    lane exported none. Without a selection the harness lists the images,
    builds nothing and exits 0, so the seeded break would never be
    exercised."""
    args: list = []
    for name in load_selected_container_images(_REPO_ROOT):
        args += ["--image", name]
    return args


def _run_harness(
    doc_root: pathlib.Path,
    *,
    temp_root: pathlib.Path,
    timeout: int = 240,
) -> subprocess.CompletedProcess:
    """Invoke strangers-path-check.sh --doc-root <doc_root> as a subprocess,
    with the image selection from _harness_image_args()."""
    home_dir = temp_root.parent / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    env = build_scrubbed_env(
        home_dir=home_dir,
        extra_path_dirs=(pathlib.Path(sys.executable).parent,),
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_root),
            "PGAI_CONTAINER_TIER": os.environ.get("PGAI_CONTAINER_TIER", ""),
        },
    )
    return subprocess.run(
        ["bash", str(_HARNESS), *_harness_image_args(), "--doc-root", str(doc_root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _make_broken_doc_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Copy docs/docker.md into a scratch doc-root with Step 5 broken.

    Only the ONE line under test is edited; every other line (including the
    other steps' commands this test does not exercise) is left exactly as
    the real checkout has it, so a change to the extractor's other targets
    would still resolve normally from this scratch copy.
    """
    doc_root = tmp_path / "doc-root"
    scratch_docs = doc_root / "docs"
    scratch_docs.mkdir(parents=True)
    text = _REAL_DOCKER_DOC.read_text(encoding="utf-8")
    assert text.count(_INSTALL_STEP5_ORIGINAL) == 1, (
        f"expected exactly one occurrence of {_INSTALL_STEP5_ORIGINAL!r} in "
        f"{_REAL_DOCKER_DOC}; found {text.count(_INSTALL_STEP5_ORIGINAL)}. "
        f"The real doc's Step 5 command text changed — update this test's "
        f"seeded-break string to match."
    )
    broken_text = text.replace(_INSTALL_STEP5_ORIGINAL, _INSTALL_STEP5_BROKEN)
    (scratch_docs / "docker.md").write_text(broken_text, encoding="utf-8")
    return doc_root


def test_seeded_break_in_install_step_fails_and_cites_doc_and_line(
    tmp_path: pathlib.Path,
) -> None:
    doc_root = _make_broken_doc_root(tmp_path)
    result = _run_harness(doc_root, temp_root=tmp_path / "scratch")

    assert result.returncode == 1, (
        f"expected exit 1 with a broken Step 5 command; got "
        f"{result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    # The install stage's own report line: verdict FAIL, and the detail
    # line naming the exact broken command and the doc:line it came from.
    assert "install      FAIL" in result.stdout, (
        f"expected the install stage to report FAIL.\nstdout:\n{result.stdout}"
    )
    assert _INSTALL_STEP5_BROKEN in result.stdout, (
        f"expected the FAIL detail to quote the seeded broken command "
        f"verbatim (proving it was extracted from the scratch document, "
        f"not a transcribed copy).\nstdout:\n{result.stdout}"
    )
    assert re.search(r"docs/docker\.md:\d+", result.stdout), (
        f"expected the FAIL detail to cite 'docs/docker.md:<line>'.\n"
        f"stdout:\n{result.stdout}"
    )

    # The dual-channel refusal at the run's own final exit also names the
    # failed stage list, including "install".
    assert "error:   strangers_path_check.stage_failed" in result.stderr, (
        f"expected the dual-channel error contract on the final refusal.\n"
        f"stderr:\n{result.stderr}"
    )
    assert "install" in result.stderr


def test_unmodified_doc_root_install_step_is_not_the_broken_command(
    tmp_path: pathlib.Path,
) -> None:
    """The real checkout's docs/docker.md still carries the ORIGINAL,
    unbroken Step 5 command — a cheap, fast (no subprocess) regression lock
    on the seed string above staying in sync with the real document, so a
    future doc edit that removes or rewords Step 5 fails this test loudly
    instead of leaving test_seeded_break_... silently unable to seed at all.
    """
    text = _REAL_DOCKER_DOC.read_text(encoding="utf-8")
    assert _INSTALL_STEP5_ORIGINAL in text
    assert _INSTALL_STEP5_BROKEN not in text
