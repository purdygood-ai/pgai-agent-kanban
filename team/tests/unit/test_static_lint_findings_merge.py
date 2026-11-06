"""
test_static_lint_findings_merge.py
==================================
Proves that each static-analysis lint converted to quality class reports per
instance through the gated runners' merge path, rather than as one coarse
pass/fail entry or not at all.

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

The fixture tree is a throwaway directory under ``tmp_path`` holding copies
of the lint scripts (each self-locates its defaults from its own file, so
the copies scan the fixture, never this dev tree), copies of the real
requirements files and the ``pm-agent`` directory, and a symlink to the real
``pgai_agent_kanban`` package.
Seeds:

  help_presence               team/scripts/no_help.sh (no --help handler)
  python_import_completeness  team/tests/helpers/stray_import.py (httpx)
  dashboard_network_git_gate  team/scripts/dashboard/git-status.sh (git fetch)
  env_bootstrap               team/scripts/dashboard/rootpeek.sh (reads the
                              kanban root with no prelude)
  engine_size_dupe            team/scripts/lib/discovery.sh over the line
                              ceiling, and one function defined in two
                              co-sourced lib stages (two findings). Its
                              Python-def check scans the symlinked real
                              package, so those findings are not counted
                              against the seeds; each is still checked to
                              arrive per instance.

``lint_lib_function_dedupe.py`` has no runner call site, so its findings
file -- from a direct run over the same seeded lib/ stages -- is merged
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

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_GATED_RUNNER_LINT = _SCRIPTS_DIR / "lib" / "gated_runner_lint.sh"

# The converted lints each runner calls, and the findings each fixture seed
# must produce per lint (the "lint" key of the merged finding).
_UNIT_LINTS = {
    "lint_help_presence.py": ("help_presence", 1),
    "lint_python_import_completeness.py": ("python_import_completeness", 1),
    "lint_dashboard_network_git_gate.py": ("dashboard_network_git_gate", 1),
    "lint_env_bootstrap.py": ("env_bootstrap", 1),
    "lint_engine_size_dupe.py": ("engine_size_dupe", 2),
}
_INTEGRATION_LINTS = {
    "lint_help_presence.py": ("help_presence", 1),
    "lint_dashboard_network_git_gate.py": ("dashboard_network_git_gate", 1),
    "lint_env_bootstrap.py": ("env_bootstrap", 1),
    "lint_engine_size_dupe.py": ("engine_size_dupe", 2),
}

_BLOCK_START_RE = re.compile(r'^(_R[UI]_\w+_FINDINGS)="\$\{PGAI_AGENT_KANBAN_TEMP_DIR\}/lint/')


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


def _write(path: pathlib.Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _seeded_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build the seeded fixture tree described in the module docstring; return it."""
    tree = tmp_path / "tree"
    scripts = tree / "team" / "scripts"
    scripts.mkdir(parents=True)
    # lint_engine_size_dupe.py imports lint_python_duplicates.py from
    # beside itself, so the dependency is copied with it.
    for name in sorted(set(_UNIT_LINTS) | {"lint_lib_function_dedupe.py", "lint_python_duplicates.py"}):
        shutil.copy2(_SCRIPTS_DIR / name, scripts / name)
    (tree / "team" / "pgai_agent_kanban").symlink_to(_TEAM_DIR / "pgai_agent_kanban")
    # The package imports pm-agent modules by bare name; the import lint
    # counts them local only when their files sit under team/ (its walk does
    # not descend into a symlinked directory), so pm-agent is copied.
    shutil.copytree(
        _TEAM_DIR / "pm-agent",
        tree / "team" / "pm-agent",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("requirements.txt", "requirements-test.txt"):
        shutil.copy2(_REPO_ROOT / name, tree / name)
    _write(scripts / "help_presence_exempt.txt", "# fixture exempt list: nothing exempt\n")

    _write(scripts / "no_help.sh", "#!/usr/bin/env bash\necho working\n", executable=True)
    _write(tree / "team" / "tests" / "helpers" / "stray_import.py", "import httpx\n")
    _write(
        scripts / "dashboard" / "git-status.sh",
        '#!/usr/bin/env bash\ngit -C "$dev_tree" fetch origin --tags\n',
    )
    # Answers --help so the recursive help-presence scan does not count it:
    # this seed is the env_bootstrap lint's alone.
    _write(
        scripts / "dashboard" / "rootpeek.sh",
        '#!/usr/bin/env bash\ncase "${1:-}" in --help|-h) echo "Usage: rootpeek.sh"; exit 0 ;; esac\n'
        'echo "$PGAI_AGENT_KANBAN_ROOT_PATH"\n',
        executable=True,
    )
    _write(scripts / "lib" / "discovery.sh", "".join(f"# line {n}\n" for n in range(900)))
    _write(
        scripts / "lib" / "fixture_driver.sh",
        '#!/usr/bin/env bash\nSCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'source "${SCRIPT_DIR}/fixture_stage_a.sh"\nsource "${SCRIPT_DIR}/fixture_stage_b.sh"\n',
    )
    _write(scripts / "lib" / "fixture_stage_a.sh", "shadowed_fixture_helper() { :; }\n")
    _write(scripts / "lib" / "fixture_stage_b.sh", "shadowed_fixture_helper() { :; }\n")
    return tree


def _sandbox_env(tmp_path: pathlib.Path) -> dict:
    """An explicit environment whose kanban roots and temp root are a sandbox."""
    sandbox = tmp_path / "sandbox"
    (sandbox / "tmp").mkdir(parents=True, exist_ok=True)
    return build_scrubbed_env(
        home_dir=tmp_path / "home",
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
    assert (
        "quality-class finding(s) (exit" not in result.stderr
    ), f"a converted lint exited non-zero inside the runner block:\n{result.stderr}"
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


def test_runner_call_sites_merge_each_converted_lint_per_instance(tmp_path: pathlib.Path) -> None:
    for runner, lints in (
        ("run-unit-tests.sh", _UNIT_LINTS),
        ("run-integration-tests.sh", _INTEGRATION_LINTS),
    ):
        tree = _seeded_tree(tmp_path / runner)
        body = "\n".join(_call_site_block(runner, script) for script in lints)
        out = tmp_path / runner / "aggregate.json"

        merged_all = _run_merge_harness(tmp_path / runner, tree / "team", body, out)
        # Copied Python defs come from the real package the tree links to,
        # not from a seed: check their shape, keep them out of the counts.
        python_defs = [f for f in merged_all if f.get("kind") == "duplicate-python-def"]
        _assert_per_instance(python_defs, {"lint_engine_size_dupe.py": ("engine_size_dupe", len(python_defs))})
        findings = [f for f in merged_all if f not in python_defs]

        _assert_per_instance(findings, lints)
        merged = {f["lint"]: f for f in findings if f["lint"] != "engine_size_dupe"}
        assert merged["help_presence"]["file"].endswith("scripts/no_help.sh"), merged
        assert merged["dashboard_network_git_gate"]["verb"] == "fetch", merged
        assert merged["env_bootstrap"]["file"].endswith("dashboard/rootpeek.sh"), merged
        if "python_import_completeness" in merged:
            assert merged["python_import_completeness"]["module"] == "httpx", merged
        engine_kinds = sorted(f["kind"] for f in findings if f["lint"] == "engine_size_dupe")
        assert engine_kinds == ["duplicate-function", "engine-size"], findings


def test_lib_function_dedupe_findings_merge_per_instance(tmp_path: pathlib.Path) -> None:
    tree = _seeded_tree(tmp_path)
    lint_out = tmp_path / "lib_function_dedupe.json"

    direct = subprocess.run(
        [
            sys.executable,
            str(tree / "team" / "scripts" / "lint_lib_function_dedupe.py"),
            "--findings-file",
            str(lint_out),
        ],
        capture_output=True,
        text=True,
        cwd=str(tree / "team"),
        env=_sandbox_env(tmp_path),
        timeout=300,
    )
    assert direct.returncode == 0, f"stdout={direct.stdout}\nstderr={direct.stderr}"
    per_lint = json.loads(lint_out.read_text(encoding="utf-8"))

    findings = _run_merge_harness(
        tmp_path,
        tree / "team",
        f'gated_runner_merge_findings_file "{lint_out}"',
        tmp_path / "aggregate.json",
    )

    assert findings == per_lint
    assert [f["function"] for f in findings] == ["shadowed_fixture_helper"], findings
    assert all(os.fspath("fixture_stage_a.sh") in f["cause"] for f in findings), findings
