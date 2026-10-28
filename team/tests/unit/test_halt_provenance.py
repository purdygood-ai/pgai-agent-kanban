"""
test_halt_provenance.py
=======================
pgai_agent_kanban.overwatch.halt_provenance tells a chain HALT from an
operator HALT:

  1. Each chain writer -- cm_halt (CM trigger), the HALT-AFTER promotion,
     and OVERWATCH's repair HALT -- writes a body the reader classifies as
     chain, with its writer and reason; the CM HALT also yields its trigger
     number.
  2. halt.sh and halt-global.sh still write an empty file, which reads as
     operator.
  3. A body-less HALT falls back to the sentinel-writes log: promote ->
     chain, halt -> operator, no record -> operator.
  4. Bodies written before the provenance block (the CM header, CM.md's
     hand template, the repair marker) still read as the chain's; any other
     body, or an unknown writer, reads as operator.
  5. The HALT readers (dashboard halt state, why.sh's HALT reason, the
     resolution stage's classifier, the repair-marker state) still read a
     HALT that now has a body.
  6. halt_command, halt_global_command, unhalt_command and
     unhalt_global_command name the script an installed kanban carries,
     <kanban root>/scripts/<script>, never the repository's team/ path; a
     root containing a space is printed shell-quoted, so bash reads the
     command as the intended words.
  7. cm_halt --inbox (the integrity-invariant stops) writes the same
     HALT body and HALT Event as cm_halt without it, then one operator
     inbox entry carrying the HALT's error key, reason and remedy; a
     failed inbox write is narrated and changes neither the exit status
     nor the HALT body. A HALT without --inbox writes no entry.

Every kanban root is a throwaway directory under tmp_path.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys
import textwrap

import pytest

from pgai_agent_kanban.dashboard.halt_state import compute_halt_state, halt_flag_and_text
from pgai_agent_kanban.halt_after.promote import promote
from pgai_agent_kanban.lib.sentinel_provenance import log_path, record_sentinel_write
from pgai_agent_kanban.ops import verdict
from pgai_agent_kanban.overwatch import halt_provenance as hp
from pgai_agent_kanban.overwatch.resolution_stage import classify_halt_file
from tests.unit.shell_harness import run_bash

_TEAM = pathlib.Path(__file__).resolve().parents[2]
_PROJECT = "demo"
_CM_LIBS = (
    "scripts/lib/error_contract.sh",
    "scripts/lib/pp_run_ops.sh",
    "scripts/lib/project_paths.sh",
    "scripts/lib/cm/release_preflight.sh",
)
_POSTURE_LIB = "scripts/lib/cm/release_ship_posture.sh"


def _root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "kanban"
    proj = root / "projects" / _PROJECT
    proj.mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    (root / "kanban.cfg").write_text("[paths]\n", encoding="utf-8")
    (proj / "project.cfg").write_text(f"[project]\nproject_name = {_PROJECT}\n", encoding="utf-8")
    (proj / "release-state.md").write_text("# Release State\n\n## Active RC\nnone\n", encoding="utf-8")
    return root


def _env(root: pathlib.Path) -> dict:
    return {
        "KANBAN_ROOT": str(root),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        "PGAI_TASKS_DIR": "",
        "PGAI_PROJECT_ROOT": "",
        "PGAI_PROJECT_NAME": "",
    }


def _halt(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "HALT"


def _assert_installed_resume(body: str, root: pathlib.Path, script: str, *args: str) -> None:
    """The body's last line is ``# Resume: <root>/scripts/<script> <args>``.

    The command names the installed script under the sandbox kanban root,
    never a repository path with a ``team/`` segment.
    """
    resume = body.rstrip("\n").splitlines()[-1]
    command = " ".join([shlex.quote(str(root / "scripts" / script)), *args])
    assert resume == f"# Resume: {command}", body
    assert "team/" not in resume[len(f"# Resume: {root}"):], resume


def _cm_halt(tmp_path: pathlib.Path, root: pathlib.Path, key: str, *, posture: bool = True, inbox: bool = False):
    libs = _CM_LIBS + ((_POSTURE_LIB,) if posture else ())
    sources = "\n".join(f"source {lib}" for lib in libs)
    flag = " --inbox" if inbox else ""
    script = textwrap.dedent(f"""\
        set -uo pipefail
        {sources}
        PROJECT_NAME={_PROJECT}
        ACTIVE_RC=v1.2.3
        PROJECT_HALT_FILE="{_halt(root)}"
        RELEASE_STATE="{root / 'projects' / _PROJECT / 'release-state.md'}"
        cm_halt "{key}" "The squash to main has conflicts." "git -C /srv/repo status"{flag}
    """)
    return run_bash(tmp_path, script, extra_env=_env(root), timeout=60)


# ---------------------------------------------------------------------------
# 1. Each chain writer is classified chain with its writer and reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, trigger",
    [
        ("cm-release.trigger-5.squash-conflict", "5"),
        ("cm-release.trigger-5b.tag-points-at-wrong-commit", "5b"),
        ("cm-release.trigger-10.pollution-guard-strict", "10"),
        ("cm-release.release-state-write-failed", None),
    ],
)
def test_cm_trigger_halt_is_chain_with_its_trigger_number(tmp_path, key, trigger):
    root = _root(tmp_path)
    result = _cm_halt(tmp_path, root, key)
    assert result.returncode == 1, result.stderr

    body = _halt(root).read_text(encoding="utf-8")
    assert body.splitlines()[0].startswith("# HALT created by CM at "), body
    assert "# halt-writer: cm-release\n" in body
    assert f"# halt-reason: {key}\n" in body
    assert (f"# halt-trigger: {trigger}\n" in body) == (trigger is not None), body

    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_CHAIN, found
    assert found.is_chain
    assert found.writer == hp.WRITER_CM_RELEASE
    assert found.reason == key
    assert found.trigger == trigger
    assert found.source == "body"


def test_cm_halt_without_the_posture_helper_still_yields_the_trigger(tmp_path):
    root = _root(tmp_path)
    result = _cm_halt(tmp_path, root, "cm-release.trigger-5.squash-conflict", posture=False)
    assert result.returncode == 1, result.stderr
    body = _halt(root).read_text(encoding="utf-8")
    assert "# halt-trigger:" not in body, body

    found = hp.classify_halt(_halt(root))
    assert (found.origin, found.writer, found.trigger) == (hp.ORIGIN_CHAIN, hp.WRITER_CM_RELEASE, "5")


def test_halt_after_promotion_halt_is_chain(tmp_path):
    root = _root(tmp_path)
    project = root / "projects" / _PROJECT
    (project / "HALT-AFTER").write_text("rc:v1.2.3\n", encoding="utf-8")

    promote(project, "rc:v1.2.3")

    body = _halt(root).read_text(encoding="utf-8")
    assert body.startswith("# HALT created by HALT-AFTER promotion at "), body
    _assert_installed_resume(body, root, "unhalt.sh", "--project", _PROJECT)
    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_CHAIN, found
    assert found.writer == hp.WRITER_HALT_AFTER_PROMOTION
    assert "HALT-AFTER rc:v1.2.3 auto-promotion" in found.reason
    assert found.trigger is None
    assert "op=promote" in log_path(root).read_text(encoding="utf-8")


def test_promotion_leaves_an_existing_operator_halt_byte_for_byte(tmp_path):
    root = _root(tmp_path)
    project = root / "projects" / _PROJECT
    (project / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    _halt(root).write_bytes(b"")
    record_sentinel_write(root, _halt(root), "halt")

    promote(project, "rc")

    assert _halt(root).read_bytes() == b""
    assert hp.classify_halt(_halt(root)).origin == hp.ORIGIN_OPERATOR


def test_root_scope_promotion_is_the_operators(tmp_path):
    root = _root(tmp_path)
    (root / "HALT-AFTER").write_text("coder\n", encoding="utf-8")

    promote(root, "coder")

    body = (root / "HALT").read_text(encoding="utf-8")
    _assert_installed_resume(body, root, "unhalt-global.sh")
    found = hp.classify_halt(root / "HALT")
    assert found.origin == hp.ORIGIN_OPERATOR, found
    assert found.fleet_scope
    assert found.writer == hp.WRITER_HALT_AFTER_PROMOTION


def test_overwatch_repair_halt_is_chain_and_its_marker_still_reads(tmp_path):
    root = _root(tmp_path)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source scripts/lib/overwatch_protocol.sh
        overwatch_write_repair_halt "{_halt(root)}" fix_stale_lock
        overwatch_repair_halt_state "{_halt(root)}"
    """)
    result = run_bash(tmp_path, script, extra_env=_env(root), timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "live", result.stdout

    body = _halt(root).read_text(encoding="utf-8")
    assert "# halt-writer: overwatch-repair\n" in body
    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_CHAIN, found
    assert found.writer == hp.WRITER_OVERWATCH_REPAIR
    assert found.reason == "OVERWATCH repair in progress (fix_stale_lock)"
    assert found.source == "body"


# ---------------------------------------------------------------------------
# 2. The operator writers are unchanged and read as operator
# ---------------------------------------------------------------------------


def _operator_script(tmp_path, root, script: str, *args: str):
    env = dict(os.environ)
    env.update({k: v for k, v in _env(root).items() if v})
    for name in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "PGAI_PROJECT_NAME"):
        env.pop(name, None)
    return subprocess.run(
        ["bash", str(_TEAM / "scripts" / script), *args],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=120,
    )


def test_halt_sh_writes_an_empty_operator_halt(tmp_path):
    root = _root(tmp_path)
    result = _operator_script(tmp_path, root, "halt.sh", "--project", _PROJECT)
    assert result.returncode == 0, result.stdout + result.stderr

    assert _halt(root).read_bytes() == b""
    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_OPERATOR, found
    assert (found.writer, found.source) == ("halt", "sentinel-log")


def test_halt_global_sh_writes_an_empty_operator_halt(tmp_path):
    root = _root(tmp_path)
    result = _operator_script(tmp_path, root, "halt-global.sh")
    assert result.returncode == 0, result.stdout + result.stderr

    assert (root / "HALT").read_bytes() == b""
    found = hp.classify_halt(root / "HALT")
    assert found.origin == hp.ORIGIN_OPERATOR, found
    assert found.fleet_scope


# ---------------------------------------------------------------------------
# 3. Body-less HALT: the sentinel-writes log decides, failing safe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op, origin",
    [("promote", hp.ORIGIN_CHAIN), ("halt", hp.ORIGIN_OPERATOR), ("halt_global", hp.ORIGIN_OPERATOR)],
)
def test_empty_halt_reads_its_latest_record(tmp_path, op, origin):
    root = _root(tmp_path)
    _halt(root).write_bytes(b"")
    record_sentinel_write(root, _halt(root), op)

    found = hp.classify_halt(_halt(root))
    assert found.origin == origin, found
    assert found.source == "sentinel-log"
    if origin == hp.ORIGIN_CHAIN:
        assert found.writer == hp.WRITER_HALT_AFTER_PROMOTION


def test_empty_halt_with_no_record_is_operator(tmp_path):
    root = _root(tmp_path)
    _halt(root).write_bytes(b"")
    assert not log_path(root).exists()

    found = hp.classify_halt(_halt(root))
    assert (found.origin, found.source) == (hp.ORIGIN_OPERATOR, "no-record")

    record_sentinel_write(root, root / "projects" / "other" / "HALT", "promote")
    assert hp.classify_halt(_halt(root)).origin == hp.ORIGIN_OPERATOR


def test_whitespace_only_halt_is_body_less(tmp_path):
    root = _root(tmp_path)
    _halt(root).write_bytes(b"")
    record_sentinel_write(root, _halt(root), "promote")
    _halt(root).write_bytes(b"  \n\n")

    # The record's digest names the empty file, not these bytes.
    assert hp.classify_halt(_halt(root)).origin == hp.ORIGIN_OPERATOR


def test_promote_record_superseded_by_unhalt_is_operator(tmp_path):
    root = _root(tmp_path)
    _halt(root).write_bytes(b"")
    record_sentinel_write(root, _halt(root), "promote")
    _halt(root).unlink()
    record_sentinel_write(root, _halt(root), "unhalt")
    _halt(root).write_bytes(b"")  # a bare touch nothing recorded

    found = hp.classify_halt(_halt(root))
    assert (found.origin, found.writer) == (hp.ORIGIN_OPERATOR, "unhalt"), found


# ---------------------------------------------------------------------------
# 4. Older bodies, unknown writers, liberal reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body, writer, reason, trigger, source",
    [
        (
            "# HALT created by CM at 2026-01-01T00:00:00Z\n"
            "# error:   cm-release.trigger-4.pre-squash-hook-failed\n"
            "# message: The pre-squash hook failed.\n"
            "# remedy:  bash hooks/pre-squash.sh\n",
            hp.WRITER_CM_RELEASE, "cm-release.trigger-4.pre-squash-hook-failed", "4", "cm-header",
        ),
        (
            "# HALT created by CM at 2026-01-01T00:00:00Z\n"
            "# Reason: trigger 2 - systemic risk high and not clear to ship\n"
            "# Resolution: Operator review required. See CM task status.md for full reason.\n",
            hp.WRITER_CM_RELEASE, "trigger 2 - systemic risk high and not clear to ship", "2", "cm-header",
        ),
        (
            "# HALT created by OVERWATCH while it repairs this project (fix_fn); removed when the repair ends.\n"
            "# overwatch-repair-halt: pid=1 start=1 fn=fix_fn at=2026-01-01T00:00:00Z\n",
            hp.WRITER_OVERWATCH_REPAIR, "OVERWATCH repair in progress (fix_fn)", None, "repair-marker",
        ),
        (
            "# HALT created by CM at 2026-01-01T00:00:00Z\r\n"
            "#halt-writer :  CM_Release  \r\n"
            "#  HALT-REASON: cm-release.trigger-7.tag-exists-on-origin\r\n",
            hp.WRITER_CM_RELEASE, "cm-release.trigger-7.tag-exists-on-origin", "7", "body",
        ),
    ],
)
def test_older_and_hand_edited_chain_bodies_still_read_as_chain(tmp_path, body, writer, reason, trigger, source):
    root = _root(tmp_path)
    _halt(root).write_text(body, encoding="utf-8", newline="")

    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_CHAIN, found
    assert (found.writer, found.reason, found.trigger, found.source) == (writer, reason, trigger, source)


@pytest.mark.parametrize(
    "body",
    [
        "pausing for the weekend -- ops\n",
        "rc\n",
        "# halt-writer: someone-else\n# halt-reason: testing\n",
        "the overwatch-repair-halt marker word alone does not count\n",
    ],
)
def test_other_bodies_are_operator(tmp_path, body):
    root = _root(tmp_path)
    _halt(root).write_text(body, encoding="utf-8")
    record_sentinel_write(root, _halt(root), "promote")

    found = hp.classify_halt(_halt(root))
    assert found.origin == hp.ORIGIN_OPERATOR, found


def test_absent_and_unreadable_halts(tmp_path):
    root = _root(tmp_path)
    assert hp.classify_halt(_halt(root)).origin == hp.ORIGIN_ABSENT

    _halt(root).write_text("# halt-writer: cm-release\n# halt-reason: x\n", encoding="utf-8")
    _halt(root).chmod(0)
    try:
        if os.access(_halt(root), os.R_OK):
            pytest.skip("running with privileges that read a mode-000 file")
        found = hp.classify_halt(_halt(root))
        assert (found.origin, found.source) == (hp.ORIGIN_OPERATOR, "unreadable")
    finally:
        _halt(root).chmod(0o644)


def test_provenance_lines_shape_and_refusals():
    assert hp.provenance_lines("cm_release", "cm-release.trigger-9.stamp-step-failed", "9") == [
        "# halt-writer: cm-release",
        "# halt-reason: cm-release.trigger-9.stamp-step-failed",
        "# halt-trigger: 9",
    ]
    assert hp.provenance_lines(hp.WRITER_OVERWATCH_REPAIR, "two\nlines", "none") == [
        "# halt-writer: overwatch-repair",
        "# halt-reason: two lines",
    ]
    with pytest.raises(ValueError, match="not a chain HALT writer"):
        hp.provenance_lines("halt", "operator pause")
    with pytest.raises(ValueError, match="reason is blank"):
        hp.provenance_lines(hp.WRITER_CM_RELEASE, "  ")


@pytest.mark.parametrize(
    "text, number",
    [
        ("cm-release.trigger-5.squash-conflict", "5"),
        ("cm-release.trigger-5B.tag-points-at-wrong-commit", "5b"),
        ("Trigger 11: fidelity gate divergence", "11"),
        ("cm-release.release-state-write-failed", None),
        (None, None),
    ],
)
def test_trigger_number(text, number):
    assert hp.trigger_number(text) == number


# ---------------------------------------------------------------------------
# 5. The HALT readers still read a HALT with a body; the CLI
# ---------------------------------------------------------------------------


def test_halt_readers_render_a_bodied_halt(tmp_path):
    root = _root(tmp_path)
    project = root / "projects" / _PROJECT
    (project / "HALT-AFTER").write_text("rc\n", encoding="utf-8")
    promote(project, "rc")

    state, _event = compute_halt_state(project)
    assert state == "halted"
    assert halt_flag_and_text(project, root) == ("yes", "HALT")
    assert verdict._halt_reason(str(_halt(root))).startswith("HALT created by HALT-AFTER promotion at ")
    # The resolution stage classifies through provenance: the promotion's HALT
    # is the chain's, and only CM's reads as ship-policy.
    assert classify_halt_file(str(_halt(root))) == "chain"

    _halt(root).unlink()
    assert _cm_halt(tmp_path, root, "cm-release.trigger-5.squash-conflict").returncode == 1
    assert classify_halt_file(str(_halt(root))) == "ship-policy"
    assert verdict._halt_reason(str(_halt(root))).startswith("HALT created by CM at ")


@pytest.mark.parametrize("by_path", [False, True])
def test_cli_prints_the_classification(tmp_path, by_path):
    root = _root(tmp_path)
    assert _cm_halt(tmp_path, root, "cm-release.trigger-8.last-3-rcs-non-functional").returncode == 1
    if by_path:
        command = [sys.executable, str(_TEAM / "pgai_agent_kanban" / "overwatch" / "halt_provenance.py")]
    else:
        command = [sys.executable, "-m", "pgai_agent_kanban.overwatch.halt_provenance"]
    env = dict(os.environ, PYTHONPATH=str(_TEAM))

    plain = subprocess.run(
        [*command, "classify", str(_halt(root))],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )
    assert plain.returncode == 0, plain.stderr
    assert plain.stdout == "chain\tcm-release\tcm-release.trigger-8.last-3-rcs-non-functional\t8\tbody\n"

    as_json = subprocess.run(
        [*command, "classify", str(_halt(root)), "--json", "--kanban-root", str(root)],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )
    assert as_json.returncode == 0, as_json.stderr
    data = json.loads(as_json.stdout)
    assert (data["origin"], data["is_chain"], data["trigger"]) == ("chain", True, "8")


# ---------------------------------------------------------------------------
# 6. The halt and unhalt commands name the installed scripts, shell-quoted
# ---------------------------------------------------------------------------

#: (builder, its arguments after the kanban root, the script, the words after it)
_COMMANDS = [
    (hp.halt_command, (_PROJECT,), "halt.sh", ["--project", _PROJECT]),
    (hp.halt_global_command, (), "halt-global.sh", []),
    (hp.unhalt_command, (_PROJECT,), "unhalt.sh", ["--project", _PROJECT]),
    (hp.unhalt_global_command, (), "unhalt-global.sh", []),
]


def _bash_words(command: str) -> list:
    """The words bash splits *command* into, one per printf line."""
    run = subprocess.run(
        ["bash", "-c", f"printf '%s\\n' {command}"], capture_output=True, text=True, timeout=30
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@pytest.mark.parametrize("builder, args, script, rest", _COMMANDS)
@pytest.mark.parametrize("root_name", ["kanban", "kanban root"])
def test_halt_and_unhalt_commands_name_the_installed_script(tmp_path, root_name, builder, args, script, rest):
    root = tmp_path / root_name
    command = builder(root, *args)
    installed = f"{root}/scripts/{script}"

    assert "team/" not in command
    assert _bash_words(command) == [installed, *rest]
    assert shlex.split(command) == [installed, *rest]
    if " " in root_name:
        # The path carries a space, so it is printed quoted, as one word.
        assert command.startswith(f"'{installed}'")
    else:
        assert command.startswith(f"{installed}")
    # A str root and a Path root print the same command.
    assert builder(str(root), *args) == command


def test_a_project_name_is_quoted_too(tmp_path):
    root = tmp_path / "kanban"
    assert hp.halt_command(root, "two words") == f"{root}/scripts/halt.sh --project 'two words'"
    assert hp.unhalt_command(root, "two words") == f"{root}/scripts/unhalt.sh --project 'two words'"


# ---------------------------------------------------------------------------
# 7. cm_halt --inbox: the same HALT, then one operator inbox entry
# ---------------------------------------------------------------------------


def _inbox(root: pathlib.Path) -> pathlib.Path:
    return root / "projects" / _PROJECT / "operator-inbox.md"


def _body_after_timestamp(root: pathlib.Path) -> str:
    return "\n".join(_halt(root).read_text(encoding="utf-8").splitlines()[1:])


def test_cm_halt_inbox_writes_the_same_halt_and_one_entry_with_its_triple(tmp_path):
    key = "cm-release.trigger-11.fidelity-gate-divergence"
    plain_root = _root(tmp_path / "plain")
    assert _cm_halt(tmp_path, plain_root, key).returncode == 1
    root = _root(tmp_path / "inbox")
    result = _cm_halt(tmp_path, root, key, inbox=True)
    assert result.returncode == 1, result.stderr

    assert _body_after_timestamp(root) == _body_after_timestamp(plain_root)
    assert hp.classify_halt(_halt(root)).origin == hp.ORIGIN_CHAIN
    state = (root / "projects" / _PROJECT / "release-state.md").read_text(encoding="utf-8")
    assert state.count("## HALT Event") == 1 and f"error:   {key}" in state, state

    text = _inbox(root).read_text(encoding="utf-8")
    assert text.count("### INBOX-") == 1, text
    entry = text.replace("\n  ", "\n")
    assert "- **Source:** rc:v1.2.3" in entry, entry
    assert key in entry and "The squash to main has conflicts." in entry, entry
    assert "Remedy: git -C /srv/repo status" in entry, entry
    assert f"{root}/scripts/unhalt.sh --project {_PROJECT}" in entry, entry
    assert "Operator inbox: INBOX-0001 records this stop" in result.stderr, result.stderr
    assert not _inbox(plain_root).exists(), "a HALT without --inbox writes no inbox entry"


def test_cm_halt_inbox_failure_is_narrated_and_keeps_exit_and_halt(tmp_path):
    key = "cm-release.trigger-10.pollution-guard-strict"
    plain_root = _root(tmp_path / "plain")
    assert _cm_halt(tmp_path, plain_root, key).returncode == 1
    root = _root(tmp_path / "broken")
    _inbox(root).mkdir()
    result = _cm_halt(tmp_path, root, key, inbox=True)

    assert result.returncode == 1, result.stderr
    assert _body_after_timestamp(root) == _body_after_timestamp(plain_root)
    assert f"WARNING: the operator inbox entry for this stop ({key}) could not be written" in result.stderr
    assert "inbox.read_failed" in result.stderr, result.stderr
    assert "HALT: scripts/cm/release.sh stopped." in result.stderr
