"""
test_quarantine_list_and_recover.py
===================================
End-to-end coverage that a file discovery quarantines can be listed and
restored by the operator tools.

Discovery's ``_disc_maybe_quarantine`` (team/scripts/lib/discovery_items.sh)
moves a repeatedly rejected intake file into the project's ``rejected/``
directory (``pp_rejected_dir``, team/scripts/lib/project_paths.sh) beside a
``<name>.reason`` sidecar recording ``original_type`` / ``original_dir``.
With no project name it falls back to the intake directory's own
``.rejected/``. These tests drive that real function in a sandbox kanban
root (threshold 1, so the first rejection quarantines) and then run the
real operator scripts against the same root:

  - ``list-rejected.sh --project <p>`` names the quarantined file;
  - ``recover-rejected.sh --project <p> --file <f>`` exits 0, moves it back
    to the intake directory it came from, and clears its rejection counter;
  - a file in the legacy ``<intake>/.rejected/`` stays listable and
    recoverable;
  - a file whose origin cannot be told is refused dual-channel, nothing is
    moved, and the printed remedy, run as printed, restores it;
  - a ``--rename`` that is not a plain base name (contains ``/``, or is ``.``
    or ``..``) is refused dual-channel before anything moves, and a plain
    base name still restores under the new name.

Every root is a ``two_project_root`` under ``tmp_path``; every subprocess
gets the full sandbox environment (both roots and the temp dir pointed into
it, ``PGAI_TASKS_DIR`` / ``PGAI_PROJECT_ROOT`` cleared).
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import textwrap

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_SCRIPTS_LIB = _SCRIPTS_DIR / "lib"
_LIST_REJECTED = _SCRIPTS_DIR / "list-rejected.sh"
_RECOVER_REJECTED = _SCRIPTS_DIR / "recover-rejected.sh"

_PROJECT = "project_a"


def _sandbox_env(kanban_root: pathlib.Path) -> dict[str, str]:
    """The inherited environment with every live-install pointer moved into *kanban_root*."""
    env = dict(os.environ)
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME", "PGAI_DEV_TREE_PATH"):
        env.pop(name, None)
    temp_dir = kanban_root / "tmp"
    temp_dir.mkdir(exist_ok=True)
    env.update({
        "KANBAN_ROOT": str(kanban_root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(kanban_root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        "TEAM_ROOT": str(kanban_root),
        "TERM": "dumb",
    })
    return env


def _run(argv: list[str], kanban_root: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, capture_output=True, text=True, env=_sandbox_env(kanban_root),
        cwd=str(kanban_root), timeout=120, stdin=subprocess.DEVNULL,
    )


def _quarantine(kanban_root: pathlib.Path, parent_dir: pathlib.Path, filename: str,
                project_name: str) -> subprocess.CompletedProcess:
    """Run the real ``_disc_maybe_quarantine`` once with threshold 1."""
    state_dir = kanban_root / "projects" / _PROJECT / ".discovery-state"
    script = textwrap.dedent(f"""\
        set -euo pipefail
        log() {{ printf '%s\\n' "$*" >&2; }}
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery_items.sh"
        PGAI_DISCOVERY_REJECT_THRESHOLD=1 _disc_maybe_quarantine \\
            "{state_dir}" "{parent_dir}" "{filename}" "malformed filename" "{project_name}"
    """)
    return _run(["bash", "-c", script], kanban_root)


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _project_dir(kanban_root: pathlib.Path) -> pathlib.Path:
    project = kanban_root / "projects" / _PROJECT
    (project / "bugs").mkdir(exist_ok=True)
    (project / "priority").mkdir(exist_ok=True)
    return project


def test_file_quarantined_to_project_rejected_dir_is_listed_and_recovered(two_project_root) -> None:
    root = two_project_root
    project = _project_dir(root)
    (project / "bugs" / "BUG-12-bad.md").write_text("x\n", encoding="utf-8")

    quarantined = _quarantine(root, project / "bugs", "BUG-12-bad.md", _PROJECT)
    assert quarantined.returncode == 0, _diagnostic(quarantined)
    assert (project / "rejected" / "BUG-12-bad.md").is_file(), _diagnostic(quarantined)
    assert (project / "rejected" / "BUG-12-bad.md.reason").is_file()
    assert not (project / "bugs" / "BUG-12-bad.md").exists()

    listed = _run([str(_LIST_REJECTED), "--project", _PROJECT], root)
    assert listed.returncode == 0, _diagnostic(listed)
    assert f"project={_PROJECT} dir=bugs\n  BUG-12-bad.md  (quarantined " in listed.stdout, _diagnostic(listed)
    assert "To recover: scripts/recover-rejected.sh --project <name>" in listed.stdout

    recovered = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "BUG-12-bad.md"], root)
    assert recovered.returncode == 0, _diagnostic(recovered)
    assert (project / "bugs" / "BUG-12-bad.md").read_text(encoding="utf-8") == "x\n"
    assert not (project / "rejected" / "BUG-12-bad.md").exists()
    counts = (project / ".discovery-state" / "rejected-counts.tsv").read_text(encoding="utf-8")
    assert "BUG-12-bad.md\t" not in counts

    relisted = _run([str(_LIST_REJECTED), "--project", _PROJECT], root)
    assert (relisted.returncode, relisted.stdout) == (0, "(no quarantined files found across all projects)\n")


def test_file_quarantined_to_legacy_intake_rejected_dir_is_listed_and_recovered(two_project_root) -> None:
    root = two_project_root
    project = _project_dir(root)
    (project / "priority" / "PRIORITY-7-bad.md").write_text("p\n", encoding="utf-8")

    # No project name: discovery falls back to priority/.rejected/.
    quarantined = _quarantine(root, project / "priority", "PRIORITY-7-bad.md", "")
    assert quarantined.returncode == 0, _diagnostic(quarantined)
    assert (project / "priority" / ".rejected" / "PRIORITY-7-bad.md").is_file(), _diagnostic(quarantined)

    listed = _run([str(_LIST_REJECTED), "--project", _PROJECT], root)
    assert listed.returncode == 0, _diagnostic(listed)
    assert f"project={_PROJECT} dir=priority\n  PRIORITY-7-bad.md  (quarantined " in listed.stdout

    recovered = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "PRIORITY-7-bad.md"], root)
    assert recovered.returncode == 0, _diagnostic(recovered)
    assert (project / "priority" / "PRIORITY-7-bad.md").read_text(encoding="utf-8") == "p\n"
    assert not (project / "priority" / ".rejected" / "PRIORITY-7-bad.md").exists()


def test_file_of_unknown_origin_is_refused_and_the_remedy_restores_it(two_project_root) -> None:
    root = two_project_root
    project = _project_dir(root)
    (project / "rejected").mkdir()
    (project / "rejected" / "notes.md").write_text("n\n", encoding="utf-8")

    refused = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "notes.md"], root)
    assert refused.returncode == 1, _diagnostic(refused)
    assert "error:   recover_rejected.origin_unknown" in refused.stderr, _diagnostic(refused)
    assert (project / "rejected" / "notes.md").is_file()
    assert not (project / "bugs" / "notes.md").exists()

    remedy = next(line for line in refused.stderr.splitlines() if line.startswith("remedy:"))
    ran = _run(["bash", "-c", remedy.partition(":")[2].strip()], root)
    assert ran.returncode == 0, _diagnostic(ran)
    assert (project / "bugs" / "notes.md").read_text(encoding="utf-8") == "n\n"


def _rejected_bug(kanban_root: pathlib.Path) -> pathlib.Path:
    """A project whose rejected/ holds one BUG-prefixed file with no sidecar, so it restores into bugs/."""
    project = _project_dir(kanban_root)
    (project / "rejected").mkdir()
    (project / "rejected" / "BUG-3-x.md").write_text("r\n", encoding="utf-8")
    return project


@pytest.mark.parametrize("rename", ["../../escaped.md", "sub/escaped.md", "/escaped.md", ".", ".."])
def test_rename_that_is_not_a_plain_base_name_is_refused_and_nothing_moves(two_project_root, rename) -> None:
    root = two_project_root
    project = _rejected_bug(root)

    refused = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "BUG-3-x.md",
                    "--rename", rename], root)
    assert refused.returncode == 1, _diagnostic(refused)
    lines = refused.stderr.splitlines()
    assert "error:   recover_rejected.rename_not_basename" in lines, _diagnostic(refused)
    assert any(line.startswith("message: ") and "Nothing was moved" in line for line in lines), _diagnostic(refused)
    assert any(line.startswith("remedy:  ") for line in lines), _diagnostic(refused)

    assert (project / "rejected" / "BUG-3-x.md").read_text(encoding="utf-8") == "r\n"
    assert sorted(p.name for p in (project / "bugs").iterdir()) == []
    assert list(root.rglob("escaped.md")) == []
    assert "restored" not in refused.stdout, _diagnostic(refused)


def test_refused_rename_remedy_run_as_printed_restores_under_the_base_name(two_project_root) -> None:
    root = two_project_root
    project = _rejected_bug(root)

    refused = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "BUG-3-x.md",
                    "--rename", "../../escaped.md"], root)
    assert refused.returncode == 1, _diagnostic(refused)

    remedy = next(line for line in refused.stderr.splitlines() if line.startswith("remedy:"))
    ran = _run(["bash", "-c", remedy.partition(":")[2].strip()], root)
    assert ran.returncode == 0, _diagnostic(ran)
    assert (project / "bugs" / "escaped.md").read_text(encoding="utf-8") == "r\n"
    assert not (project / "rejected" / "BUG-3-x.md").exists()
    assert [p for p in root.rglob("escaped.md")] == [project / "bugs" / "escaped.md"]


def test_rename_to_a_plain_base_name_restores_under_the_new_name(two_project_root) -> None:
    root = two_project_root
    project = _rejected_bug(root)

    recovered = _run([str(_RECOVER_REJECTED), "--project", _PROJECT, "--file", "BUG-3-x.md",
                      "--rename", "BUG-0003-x.md"], root)
    assert recovered.returncode == 0, _diagnostic(recovered)
    assert (project / "bugs" / "BUG-0003-x.md").read_text(encoding="utf-8") == "r\n"
    assert not (project / "rejected" / "BUG-3-x.md").exists()
    assert not (project / "bugs" / "BUG-3-x.md").exists()
