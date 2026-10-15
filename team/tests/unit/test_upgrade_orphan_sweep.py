"""
test_upgrade_orphan_sweep.py
============================
Behavioral tests for upgrade.sh's post-deposit orphan sweep.

After the deposit and the retirement step, upgrade.sh lists every file under
the directories it deposited into that the deposited tree does not contain.
Without ``--force`` each one is reported by relative path and left in place;
with ``--force`` each one is moved to ``retired/<UTC-ts>/<relative-path>``,
the retirement step's graveyard layout, and the summary counts it.

WHAT IS TESTED
--------------
1. **Report, then move aside.** A sandbox install holds extra files under
   deposited directories (``scripts/``, ``scripts/lib/``,
   ``pgai_agent_kanban/``, a shipped plugin directory ``workflows/release/``
   and ``docs/``) that the fixture dev tree lacks. A run without ``--force``
   names each by relative path, leaves each byte-identical in place and
   reports the count in the summary. A ``--force`` run then moves each to
   ``retired/<ts>/<relative-path>`` with its bytes intact, narrates each
   move, and counts them in the summary.

2. **Out-of-scope files untouched.** In the same sandbox, files under
   ``projects/``, the protected ``kanban.cfg`` and ``secrets``, files
   outside every deposited directory, an operator plugin directory the
   shipped tree does not have (``workflows/acme-deploy/`` and
   ``templates/project/acme-deploy/``), the install-generated
   ``scripts/cron-suggested.txt`` and a bytecode cache are byte-identical
   after both runs and are never reported as orphans.

3. **Clean upgrade.** An install that holds only files the deposited tree
   contains reports zero orphans and creates no ``retired/`` directory.

SANDBOX DESIGN
--------------
Each test builds a throwaway kanban root and dev tree under ``tmp_path``.
The kanban root starts as a copy of everything the dev tree deposits, so
it is already current: a run without ``--force`` deposits nothing, never
reaches the no-TTY skip, and goes on to the sweep. The real upgrade.sh runs
from the installed root (phase 1) and hands off to the dev tree's copy
(phase 2), with ``--no-backup --stamp-version sandbox-test --wake-tier none``.

The subprocess environment is built by ``build_scrubbed_env``: HOME sits
inside the sandbox; PGAI_AGENT_KANBAN_ROOT_PATH, KANBAN_ROOT and
PGAI_AGENT_KANBAN_TEMP_DIR point into it; PGAI_TASKS_DIR, PGAI_PROJECT_ROOT,
CLAUDE_CONFIG_DIR, PGAI_PROJECT_NAME and PGAI_DEV_TREE_PATH are absent. The
child runs in its own session with stdin closed, so no controlling terminal
can turn a skip into a prompt.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

# ---------------------------------------------------------------------------
# Paths to the real scripts (dev-tree layout, relative to this test file)
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_TEAM_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_REAL_UPGRADE_SH = _TEAM_SCRIPTS_DIR / "upgrade.sh"
_REAL_RETIRED_FILES_TXT = _TEAM_DIR / "templates" / "retired-files.txt"

# Helpers upgrade.sh sources from its own lib/ directory in either phase.
_LIB_NAMES = [
    "argparse.sh",
    "env_bootstrap.sh",
    "ini_parser.sh",
    "project_paths.sh",
    "error_contract.sh",
    "temp.sh",
    "retired_files_lint.sh",
    "version_stamp.sh",
    "subagent_deploy.sh",
    "script_common.sh",
    "semver.sh",
]

# team/ items the deposit loop lays down that the fixture dev tree carries.
_TEAM_ROOT_DOCS = ["README.md", "DIRECTIVES.md", "OVERVIEW.md", "SOP-core.md"]
_TEAM_DIRS = [
    "roles",
    "scripts",
    "pm-agent",
    "workflows",
    "pgai_agent_kanban",
    "halt_after",
    "templates",
    "demos",
    "config",
]

# Files under deposited directories that the dev tree does not contain.
_ORPHANS = [
    "docs/stale-guide.md",
    "pgai_agent_kanban/stale_module.py",
    "scripts/lib/stale_lib.sh",
    "scripts/stale-helper.sh",
    "workflows/release/stale-hook.sh",
]

# Files the sweep must never report or move, with or without --force.
_UNTOUCHED = [
    "projects/demo/project.cfg",
    "projects/demo/tasks/T-1/status.md",
    "kanban.cfg",
    "secrets",
    "operator-notes.md",
    "notes/operator.txt",
    "workflows/acme-deploy/workflow.cfg",
    "templates/project/acme-deploy/BUG-TEMPLATE.md",
    "scripts/cron-suggested.txt",
    "scripts/__pycache__/helper.cpython-312.pyc",
]


# ---------------------------------------------------------------------------
# Sandbox builder and runner
# ---------------------------------------------------------------------------


def _write(path: pathlib.Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _build_dev_tree(dev_tree: pathlib.Path) -> None:
    """Build the minimal source tree the deposit reads from."""
    team = dev_tree / "team"
    for name in _TEAM_ROOT_DOCS:
        _write(team / name, f"# {name} stub\n".encode())
    for name in ["roles", "pm-agent", "pgai_agent_kanban", "halt_after", "demos"]:
        _write(team / name / ".gitkeep", b"")
    _write(team / "pgai_agent_kanban" / "shipped_module.py", b"VALUE = 1\n")
    _write(team / "workflows" / "release" / "workflow.cfg", b"[workflow]\ntype = release\n")
    _write(team / "templates" / "project" / "release" / "BUG-TEMPLATE.md", b"# Bug\n")
    _write(team / "templates" / "retired-files.txt", _REAL_RETIRED_FILES_TXT.read_bytes())
    _write(team / "config" / "docs-manifest.txt", b"# docs deposited by upgrade\nguide.md\n")
    _write(team / "scripts" / "shipped-helper.sh", b"#!/usr/bin/env bash\nexit 0\n")
    _write(team / "scripts" / "cron-suggested.template.txt", b"# __KANBAN_ROOT__\n")

    scripts_lib = team / "scripts" / "lib"
    scripts_lib.mkdir(parents=True, exist_ok=True)
    for name in _LIB_NAMES:
        src = _TEAM_SCRIPTS_DIR / "lib" / name
        if src.exists():
            shutil.copy2(src, scripts_lib / name)
    shutil.copy2(_REAL_UPGRADE_SH, team / "scripts" / "upgrade.sh")
    (team / "scripts" / "upgrade.sh").chmod(0o755)

    _write(dev_tree / "docs" / "guide.md", b"# Guide\n")
    _write(dev_tree / "subagents" / "MANIFEST.txt", b"# Empty manifest\n")
    _write(dev_tree / "install.sh", b"#!/usr/bin/env bash\nexit 0\n")
    (dev_tree / "install.sh").chmod(0o755)


def _build_current_install(kanban_root: pathlib.Path, dev_tree: pathlib.Path) -> None:
    """Lay down everything the dev tree deposits, as a prior upgrade would."""
    team = dev_tree / "team"
    kanban_root.mkdir(parents=True, exist_ok=True)
    for name in _TEAM_ROOT_DOCS:
        shutil.copy2(team / name, kanban_root / name)
    for name in _TEAM_DIRS:
        shutil.copytree(team / name, kanban_root / name, symlinks=True)
    _write(kanban_root / "docs" / "guide.md", (dev_tree / "docs" / "guide.md").read_bytes())
    _write(kanban_root / "VERSION", b"v1.2.0-sandbox\n")
    _write(kanban_root / "kanban.cfg", f"[paths]\ndev_tree_path = {dev_tree}\n".encode())


def _build_sandbox(tmp_path: pathlib.Path, *, with_extras: bool) -> dict[str, pathlib.Path]:
    """Build a sandbox root and dev tree; optionally seed orphans and the untouched set."""
    sandbox = tmp_path / "sandbox"
    kanban_root = sandbox / "kanban"
    dev_tree = sandbox / "dev_tree"
    temp_dir = sandbox / "tmp"
    home = sandbox / "home"
    for path in (dev_tree, temp_dir, home):
        path.mkdir(parents=True)

    _build_dev_tree(dev_tree)
    _build_current_install(kanban_root, dev_tree)

    if with_extras:
        for rel in _ORPHANS:
            _write(kanban_root / rel, f"left behind by an older release: {rel}\n".encode())
        for rel in _UNTOUCHED:
            if rel == "kanban.cfg":
                continue  # already written, pointing at the dev tree
            _write(kanban_root / rel, f"must survive the upgrade: {rel}\n".encode() + b"\x00\xff")

    return {
        "kanban_root": kanban_root,
        "dev_tree": dev_tree,
        "temp_dir": temp_dir,
        "home": home,
    }


def _run_upgrade(sandbox: dict[str, pathlib.Path], *, force: bool) -> subprocess.CompletedProcess:
    """Run the installed upgrade.sh against the sandbox root."""
    kanban_root = sandbox["kanban_root"]
    env = build_scrubbed_env(
        home_dir=sandbox["home"],
        extra_env={
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
            "KANBAN_ROOT": str(kanban_root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(sandbox["temp_dir"]),
        },
    )
    args = [
        "bash",
        str(kanban_root / "scripts" / "upgrade.sh"),
        "--no-backup",
        "--wake-tier", "none",
        "--dev-tree", str(sandbox["dev_tree"]),
        "--stamp-version", "sandbox-test",
    ]
    if force:
        args.append("--force")
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(sandbox["home"]),
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _snapshot(kanban_root: pathlib.Path, rels: list[str]) -> dict[str, bytes | None]:
    return {
        rel: (kanban_root / rel).read_bytes() if (kanban_root / rel).is_file() else None
        for rel in rels
    }


def _graveyard(kanban_root: pathlib.Path) -> pathlib.Path | None:
    """Return the single retired/<ts>/ directory, or None."""
    retired = kanban_root / "retired"
    if not retired.is_dir():
        return None
    children = [p for p in retired.iterdir() if p.is_dir()]
    return children[0] if len(children) == 1 else None


def _orphan_lines(output: str) -> list[str]:
    return [ln for ln in output.splitlines() if "[upgrade]" in ln and "Orphan" in ln]


def _summary_line(output: str) -> str:
    lines = [ln for ln in output.splitlines() if ln.strip().startswith("Orphans:")]
    assert len(lines) == 1, f"Expected one 'Orphans:' summary line, found {lines}\n{output}"
    return lines[0]


def _assert_ok(result: subprocess.CompletedProcess) -> str:
    output = result.stdout + result.stderr
    assert result.returncode == 0, (
        f"upgrade.sh exited {result.returncode}.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return output


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _REAL_UPGRADE_SH.exists(),
    reason=f"Real upgrade.sh not found at {_REAL_UPGRADE_SH}; skipping.",
)


def test_orphans_reported_and_left_without_force_then_moved_aside_with_force(
    tmp_path: pathlib.Path,
) -> None:
    """Without --force orphans are named and kept; with --force they move to retired/<ts>/."""
    sandbox = _build_sandbox(tmp_path, with_extras=True)
    kanban_root = sandbox["kanban_root"]
    before = _snapshot(kanban_root, _ORPHANS)

    # Run 1: report only.
    output = _assert_ok(_run_upgrade(sandbox, force=False))
    lines = _orphan_lines(output)
    not_named = [rel for rel in _ORPHANS if not any(f" {rel} " in f"{ln} " for ln in lines)]
    assert not not_named, f"Orphans not reported by relative path: {not_named}\n{output}"
    assert _snapshot(kanban_root, _ORPHANS) == before, (
        f"An orphan changed or moved without --force.\n{output}"
    )
    assert not (kanban_root / "retired").exists(), f"retired/ created without --force.\n{output}"
    summary = _summary_line(output)
    assert f"{len(_ORPHANS)} file(s) left in place" in summary, summary
    assert "--force" in output and "re-run" in output, (
        f"The report-only run does not name the --force re-run.\n{output}"
    )

    # Run 2: move aside.
    output = _assert_ok(_run_upgrade(sandbox, force=True))
    graveyard = _graveyard(kanban_root)
    assert graveyard is not None, f"No single retired/<ts>/ directory after --force.\n{output}"
    ts = graveyard.name
    still_present = [rel for rel in _ORPHANS if (kanban_root / rel).exists()]
    assert not still_present, f"Orphans still at the live root after --force: {still_present}"
    moved_bytes = {rel: (graveyard / rel).read_bytes() for rel in _ORPHANS if (graveyard / rel).is_file()}
    assert moved_bytes == before, (
        f"Graveyard copies under {graveyard} do not match the originals: "
        f"missing {sorted(set(before) - set(moved_bytes))}"
    )
    not_narrated = [
        rel for rel in _ORPHANS
        if not any(f"{rel} -> retired/{ts}/{rel}" in ln for ln in _orphan_lines(output))
    ]
    assert not not_narrated, f"No move-aside line for: {not_narrated}\n{output}"
    summary = _summary_line(output)
    assert f"{len(_ORPHANS)} file(s) moved aside -> retired/{ts}/" in summary, summary


def test_projects_protected_outside_and_operator_plugin_files_untouched_by_both_runs(
    tmp_path: pathlib.Path,
) -> None:
    """projects/, protected files, out-of-scope and operator-owned files survive both runs."""
    sandbox = _build_sandbox(tmp_path, with_extras=True)
    kanban_root = sandbox["kanban_root"]
    before = _snapshot(kanban_root, _UNTOUCHED)
    assert all(v is not None for v in before.values()), before

    for force in (False, True):
        output = _assert_ok(_run_upgrade(sandbox, force=force))
        after = _snapshot(kanban_root, _UNTOUCHED)
        changed = [rel for rel in _UNTOUCHED if after[rel] != before[rel]]
        assert not changed, (
            f"Changed or moved by the {'--force' if force else 'report-only'} run: {changed}\n{output}"
        )
        reported = [
            rel for rel in _UNTOUCHED
            if any(f" {rel} " in f"{ln} " for ln in _orphan_lines(output))
        ]
        assert not reported, f"Reported as orphans but out of scope: {reported}\n{output}"

    graveyard = _graveyard(kanban_root)
    assert graveyard is not None
    in_graveyard = [rel for rel in _UNTOUCHED if (graveyard / rel).exists()]
    assert not in_graveyard, f"Out-of-scope files found in {graveyard}: {in_graveyard}"


def test_clean_upgrade_reports_zero_orphans_and_moves_nothing(tmp_path: pathlib.Path) -> None:
    """An install holding only deposited-tree files has no orphans, with or without --force."""
    sandbox = _build_sandbox(tmp_path, with_extras=False)
    kanban_root = sandbox["kanban_root"]

    for force in (False, True):
        output = _assert_ok(_run_upgrade(sandbox, force=force))
        assert _orphan_lines(output) == [], f"Orphan lines on a clean install:\n{output}"
        assert _summary_line(output).split("Orphans:", 1)[1].strip() == "none", output
        assert not (kanban_root / "retired").exists(), (
            f"retired/ created on a clean install.\n{output}"
        )
