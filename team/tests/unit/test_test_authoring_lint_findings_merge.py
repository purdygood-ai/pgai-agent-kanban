"""
test_test_authoring_lint_findings_merge.py
==========================================
Proves that the test anti-pattern lint, the skip-cites-a-real-bug gate and
the dual-channel pair lint reach the gated runners' aggregate findings file
one finding per instance, through the same merge path every converted
quality-class lint uses -- never as one coarse pass/fail entry and never
with a non-zero exit.

The runner call sites are not re-typed here. For each lint the test cuts
the lint's own call-site block (``_R?_*_FINDINGS=...`` through ``unset``)
out of ``team/scripts/run-unit-tests.sh`` and ``run-integration-tests.sh``
and runs it, with the real ``team/scripts/lib/gated_runner_lint.sh``
sourced, from a seeded fixture tree; the aggregate findings file the
library then writes is read back.

The fixture tree is a throwaway directory under ``tmp_path`` holding copies
of the three lints and of ``team/scripts/lib/`` (each lint self-locates its
defaults from its own file, so the copies scan the fixture, never this dev
tree) and a symlink to the real ``pgai_agent_kanban`` package. Seeds:

  test_anti_patterns   team/tests/unit/test_seeded_golden.py (one AP7)
  skip_cites_real_bug  team/tests/unit/test_seeded_skips.py (a placeholder
                       citation and a citation of a bug with no file: two)
  dual_channel_pair    team/scripts/seeded_refusal.sh (an emitter call with
                       no remedy: one)

The lints also scan the symlinked real package, so only findings in the
seeded files are counted against the seeds; every finding, seeded or not,
is still checked to arrive per instance.

The fixture text is assembled from fragments so no line of THIS module
matches the detectors it seeds. Every subprocess runs with an explicit
environment whose kanban roots and temp root point into a sandbox under
``tmp_path``; the sandbox's bugs/ directory is empty.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"

# Lint script -> (the "lint" key of its merged findings, findings seeded).
_LINTS = {
    "lint_test_anti_patterns.py": ("test_anti_patterns", 1),
    "lint_skip_bug_gate.sh": ("skip_cites_real_bug", 2),
    "lint_dual_channel_pair.py": ("dual_channel_pair", 1),
}
_RUNNERS = ["run-unit-tests.sh", "run-integration-tests.sh"]

#: The seeded files, relative to the fixture tree's team/.
_SEEDED_FILES = ("tests/unit/test_seeded_golden.py", "tests/unit/test_seeded_skips.py", "scripts/seeded_refusal.sh")

_BLOCK_START_RE = re.compile(r'^(_R[UI]_\w+_FINDINGS)="\$\{PGAI_AGENT_KANBAN_TEMP_DIR\}/lint/')

_GOLDEN_SOURCE = (
    "import os\n"
    "import pathlib\n"
    "import subprocess\n"
    "\n"
    "_HERE = pathlib.Path(__file__).parent\n"
    "_GOLD" + "EN_DIR = _HERE / " + '"surface"\n'
    "\n"
    "\n"
    "def test_capture_matches_committed_surface():\n"
    "    env = dict(os." + "environ)\n"
    '    subprocess.run(["true"], env=env, check=True)\n'
)
_SKIPS_SOURCE = (
    "import pytest\n"
    "\n"
    "\n"
    "def test_waits_on_placeholder():\n"
    "    pytest." + "skip(" + repr("BUG-" + "SKIP-seeded-placeholder pending") + ")\n"
    "\n"
    "\n"
    "def test_waits_on_unfiled():\n"
    "    pytest." + "skip(" + repr("see BUG-" + "9861 for context") + ")\n"
)
_REFUSAL_SOURCE = (
    "#!/usr/bin/env bash\n"
    "seeded_refusal() {\n"
    '    pgai_emit_error_contract "seeded.refusal" ' + '"a message with no remedy"\n'
    "}\n"
)


def _call_site_block(runner: str, lint_script: str) -> str:
    """The runner's call-site block for *lint_script*: its findings-path
    assignment through its ``unset``. Fails when the runner has no such
    block, or when the block does not both pass --findings-file and merge."""
    lines = (_SCRIPTS_DIR / runner).read_text(encoding="utf-8").splitlines()
    for start, line in enumerate(lines):
        match = _BLOCK_START_RE.match(line)
        if not match:
            continue
        var = match.group(1)
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == f"unset {var}")
        block = "\n".join(lines[start : end + 1])
        if f"/{lint_script}" in block:
            assert f'--findings-file "${var}"' in block, block
            assert f'gated_runner_merge_findings_file "${var}"' in block, block
            return block
    raise AssertionError(f"{runner} has no --findings-file call-site block for {lint_script}")


def _is_seeded(finding: dict) -> bool:
    """Whether *finding* names one of the seeded files rather than the real package."""
    return str(finding.get("file", "")).endswith(_SEEDED_FILES)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seeded_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the seeded fixture tree described in the module docstring; return it."""
    tree = tmp_path / "tree"
    scripts = tree / "team" / "scripts"
    shutil.copytree(_SCRIPTS_DIR / "lib", scripts / "lib", ignore=shutil.ignore_patterns("__pycache__"))
    for name in _LINTS:
        shutil.copy2(_SCRIPTS_DIR / name, scripts / name)
    (tree / "team" / "pgai_agent_kanban").symlink_to(_TEAM_DIR / "pgai_agent_kanban")
    _write(tree / "team" / "tests" / "unit" / "test_seeded_golden.py", _GOLDEN_SOURCE)
    _write(tree / "team" / "tests" / "unit" / "test_seeded_skips.py", _SKIPS_SOURCE)
    _write(scripts / "seeded_refusal.sh", _REFUSAL_SOURCE)
    return tree


def _sandbox_env(tmp_path: pathlib.Path) -> dict:
    """An explicit environment whose kanban roots and temp root are a sandbox."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "tmp").mkdir(parents=True, exist_ok=True)
    (sandbox / "projects" / "pgai-agent-kanban" / "bugs").mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(sandbox),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox / "tmp"),
        },
    )


@pytest.mark.parametrize("runner", _RUNNERS)
def test_runner_call_sites_merge_each_test_authoring_lint_per_instance(
    runner: str, tmp_path: pathlib.Path
) -> None:
    tree = _seeded_tree(tmp_path)
    team = tree / "team"
    out = tmp_path / "aggregate.json"
    body = "\n".join(_call_site_block(runner, name) for name in _LINTS)
    script = (
        "set -euo pipefail\n"
        f'SCRIPT_DIR="{team / "scripts"}"\n'
        f'REPO_ROOT="{tree}"\n'
        'source "${SCRIPT_DIR}/lib/gated_runner_lint.sh"\n'
        f"{body}\n"
        f'gated_runner_write_findings_file "{out}" fixture-runner\n'
    )

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(team),
        env=_sandbox_env(tmp_path),
        timeout=300,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "quality-class finding(s) (exit" not in result.stderr, (
        f"a converted lint exited non-zero inside the runner block:\n{result.stderr}"
    )
    merged_all = json.loads(out.read_text(encoding="utf-8"))["findings"]
    findings = [f for f in merged_all if _is_seeded(f)]
    by_lint = Counter(f["lint"] for f in findings)
    for lint, count in _LINTS.values():
        assert by_lint[lint] == count, (lint, findings)
        for finding in (f for f in merged_all if f["lint"] == lint):
            assert "exit_code" not in finding and "detail" not in finding, (
                f"{lint} reached the aggregate as a coarse pass/fail entry: {finding}"
            )
            assert finding["severity"] == "quality", finding
            assert finding.get("cause") and finding.get("remedy"), finding
    ap = next(f for f in findings if f["lint"] == "test_anti_patterns")
    assert (ap["code"], pathlib.Path(ap["file"]).name) == ("AP7", "test_seeded_golden.py"), ap
    skip_kinds = sorted(f["kind"] for f in findings if f["lint"] == "skip_cites_real_bug")
    assert skip_kinds == ["placeholder-citation", "unfiled-citation"], findings
    pair = next(f for f in findings if f["lint"] == "dual_channel_pair")
    assert pathlib.Path(pair["file"]).name == "seeded_refusal.sh", pair
