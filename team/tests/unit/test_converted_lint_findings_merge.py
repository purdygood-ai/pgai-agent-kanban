"""
test_converted_lint_findings_merge.py
======================================
Proves that each freshness and repository lint converted to quality class
reports per instance through the gated runners' merge path, rather than as
one coarse pass/fail entry or not at all.

The runner call sites are not re-typed here. For each lint, the test cuts
the lint's own call-site block (``_R?_*_FINDINGS=...`` through ``unset``)
out of ``team/scripts/run-unit-tests.sh`` and ``run-integration-tests.sh``
and runs it, with the real ``team/scripts/lib/gated_runner_lint.sh``
sourced, from a seeded fixture tree. The aggregate findings file the
library then writes is read back:

  - every converted lint contributes one finding per seeded instance;
  - none of them contributes a coarse entry (``exit_code``/``detail``),
    which is what ``gated_runner_run_quality_lint`` records for a lint that
    exited non-zero.

The fixture tree is a throwaway git repository under ``tmp_path`` holding
copies of the lint scripts (each self-locates its defaults from its own
file, so the copies scan the fixture, never this dev tree) and a symlink to
the real ``pgai_agent_kanban`` package. Seeds:

  orphan_tests                 team/stray/test_orphan.py (one orphan)
  upgrade_no_temp_copy_guard   team/scripts/upgrade.sh with one retired line
  icd_freshness                docs/api/icd.json that no regeneration produces
  icd_compat                   a SUPPORTED baseline with two paths the ICD lacks
  rc_empty_commits             one ``--allow-empty`` commit on rc/v1.0.0

``lint_changelog_freshness.py`` has no runner call site, so its findings
file -- from a direct run against a stale scratch CHANGELOG.md -- is merged
through ``gated_runner_merge_findings_file`` directly.

Every subprocess runs with an explicit environment whose kanban roots and
temp root point into a sandbox under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter

import pytest

from tests.fixtures.binary_guard import require_binary
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_GATED_RUNNER_LINT = _SCRIPTS_DIR / "lib" / "gated_runner_lint.sh"

# The converted lints each runner calls, and the findings each fixture seed
# must produce per lint (the "lint" key of the merged finding).
_UNIT_LINTS = {
    "lint_orphan_tests.py": ("orphan_tests", 1),
    "lint_upgrade_no_temp_copy_guard.py": ("upgrade_no_temp_copy_guard", 1),
    "lint_icd_freshness.py": ("icd_freshness", 1),
    "lint_icd_compat.py": ("icd_compat", 2),
}
_INTEGRATION_LINTS = {
    "lint_icd_freshness.py": ("icd_freshness", 1),
    "lint_icd_compat.py": ("icd_compat", 2),
    "lint_rc_empty_commits.py": ("rc_empty_commits", 1),
}

_BLOCK_START_RE = re.compile(r'^(_R[UI]_\w+_FINDINGS)="\$\{PGAI_AGENT_KANBAN_TEMP_DIR\}/lint/')


@pytest.fixture(autouse=True)
def _require_git() -> None:
    """Every test here builds a git repository for the RC empty-commit seed."""
    require_binary("git", label="converted-lint-findings-merge")


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
        if f"scripts/{lint_script}" in block:
            assert f'--findings-file "${var}"' in block, block
            assert f'gated_runner_merge_findings_file "${var}"' in block, block
            return block
    raise AssertionError(f"{runner} has no --findings-file call-site block for {lint_script}")


def _git(repo: pathlib.Path, *args: str) -> None:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"git {' '.join(args)}: {result.stderr}"


def _write_json(path: pathlib.Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _seeded_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the seeded fixture tree described in the module docstring; return it."""
    tree = tmp_path / "tree"
    scripts = tree / "team" / "scripts"
    scripts.mkdir(parents=True)
    for name in sorted(set(_UNIT_LINTS) | set(_INTEGRATION_LINTS)):
        shutil.copy2(_SCRIPTS_DIR / name, scripts / name)
    (tree / "team" / "pgai_agent_kanban").symlink_to(_TEAM_DIR / "pgai_agent_kanban")

    (tree / "team" / "stray").mkdir()
    (tree / "team" / "stray" / "test_orphan.py").write_text("", encoding="utf-8")
    (scripts / "upgrade.sh").write_text(
        "#!/usr/bin/env bash\n# _PGAI_UPGRADE_REEXEC is retired\n_PGAI_UPGRADE_REEXEC=1\n",
        encoding="utf-8",
    )

    api = tree / "docs" / "api"
    api.mkdir(parents=True)
    shutil.copy2(_REPO_ROOT / "docs" / "api" / "ICD_VERSION", api / "ICD_VERSION")
    info = {"title": "fixture", "version": "1.1.0"}
    _write_json(api / "icd.json", {"openapi": "3.1.0", "info": info, "paths": {}})
    baselines = api / "baselines"
    _write_json(
        baselines / "icd-v1.0.0.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "fixture", "version": "1.0.0"},
            "paths": {"/gone-a": {"get": {}}, "/gone-b": {"get": {}}},
        },
    )
    (baselines / "SUPPORTED").write_text("1.0.0\n", encoding="utf-8")
    (tree / "docs" / "operator-api.md").write_text(
        "# Operator API\n\n## Support table\n\n| Version | Status | Since |\n"
        "|---|---|---|\n| 1.0.0 | supported | v1.0.0 |\n",
        encoding="utf-8",
    )

    _git(tree, "init", "-q", "--initial-branch=main")
    _git(tree, "config", "user.email", "test@pgai.local")
    _git(tree, "config", "user.name", "PGAI Test")
    _git(tree, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "initial")
    _git(tree, "checkout", "-q", "-b", "rc/v1.0.0")
    _git(tree, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "stray empty commit")
    return tree


def _sandbox_env(tmp_path: pathlib.Path) -> dict:
    """An explicit environment whose kanban roots and temp root are a sandbox."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "tmp").mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
        # The interpreter running this suite carries the API dependencies the
        # ICD regeneration imports; the lints' bare python3 must resolve to it.
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(sandbox),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(sandbox),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox / "tmp"),
        },
    )


def _run_merge_harness(tmp_path: pathlib.Path, workdir: pathlib.Path, body: str, out: pathlib.Path) -> list[dict]:
    """Run *body* in bash with the real gated_runner_lint.sh sourced, from
    *workdir*, then write the aggregate findings file to *out* and return
    its findings."""
    script = (
        "set -euo pipefail\n"
        f'source "{_GATED_RUNNER_LINT}"\n'
        f"{body}\n"
        f'gated_runner_write_findings_file "{out}" fixture-runner\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        cwd=str(workdir),
        env=_sandbox_env(tmp_path),
        timeout=300,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return json.loads(out.read_text(encoding="utf-8"))["findings"]


def _assert_per_instance(findings: list[dict], expected: dict[str, tuple[str, int]]) -> None:
    by_lint = Counter(f["lint"] for f in findings)
    for _script, (lint, count) in expected.items():
        mine = [f for f in findings if f["lint"] == lint]
        assert by_lint[lint] == count, (lint, mine)
        for finding in mine:
            assert (
                "exit_code" not in finding and "detail" not in finding
            ), f"{lint} reached the aggregate as a coarse pass/fail entry: {finding}"
            assert finding["severity"] == "quality", finding
            assert finding.get("cause") and finding.get("remedy"), finding


@pytest.mark.parametrize(
    ("runner", "lints"),
    [("run-unit-tests.sh", _UNIT_LINTS), ("run-integration-tests.sh", _INTEGRATION_LINTS)],
)
def test_runner_call_sites_merge_each_converted_lint_per_instance(
    tmp_path: pathlib.Path, runner: str, lints: dict[str, tuple[str, int]]
) -> None:
    tree = _seeded_tree(tmp_path)
    body = "\n".join(_call_site_block(runner, script) for script in lints)
    out = tmp_path / "aggregate.json"

    findings = _run_merge_harness(tmp_path, tree / "team", body, out)

    _assert_per_instance(findings, lints)
    causes = [f["cause"] for f in findings if f["lint"] == "icd_compat"]
    assert any("'/gone-a'" in c for c in causes) and any("'/gone-b'" in c for c in causes), causes


def test_changelog_freshness_findings_merge_per_instance(tmp_path: pathlib.Path) -> None:
    stale = tmp_path / "CHANGELOG.md"
    stale.write_text("# Changelog\n\nhand edited\n", encoding="utf-8")
    bugs = tmp_path / "bugs"
    bugs.mkdir()
    lint_out = tmp_path / "changelog_freshness.json"

    direct = subprocess.run(
        [
            sys.executable,
            str(_SCRIPTS_DIR / "lint_changelog_freshness.py"),
            "--changelog",
            str(stale),
            "--bugs-dir",
            str(bugs),
            "--findings-file",
            str(lint_out),
        ],
        capture_output=True,
        text=True,
        cwd=str(_TEAM_DIR),
        env=_sandbox_env(tmp_path),
        timeout=300,
    )
    assert direct.returncode == 0, f"stdout={direct.stdout}\nstderr={direct.stderr}"
    per_lint = json.loads(lint_out.read_text(encoding="utf-8"))
    assert len(per_lint) > 1, "a hand-edited CHANGELOG.md should yield one finding per cause"

    findings = _run_merge_harness(
        tmp_path,
        _TEAM_DIR,
        f'gated_runner_merge_findings_file "{lint_out}"',
        tmp_path / "aggregate.json",
    )

    assert findings == per_lint
    assert {f["lint"] for f in findings} == {"changelog_freshness"}
    assert all(os.fspath(stale) in f["cause"] for f in findings), findings
