"""
test_discovery_priority_cap_and_write_result.py
================================================
discovery_step_priority (team/scripts/lib/discovery_steps.sh) honours
``[versioning] max_priorities_per_run = 0`` as "priority ingest disabled",
and both bundling steps report a refused bundle write as nothing bundled.

Behaviour under test:
  1. Cap 0 with one open PRIORITY file: the step returns non-zero, logs
     that priority ingest is disabled, never scans the priority directory,
     writes nothing under requirements/ and leaves the item ``open``.
  2. Cap 0 inside one pipeline iteration (_disc_run_pipeline_once): Step 2
     reports nothing to do, so the pipeline reaches Step 3 (requirements).
  3. Cap 1 with two open PRIORITY files bundles exactly the lower-numbered
     one, marks it running, and logs "bundle written" -- unchanged.
  4. A refused _disc_write_bundle (priority and bug steps alike) is logged
     as refused, marks no item running, and the step returns non-zero --
     never "bundle written".
  5. The real _disc_write_bundle failing -- the workflow plugin will not
     load, or requirements/ is not writable -- leaves requirements/ empty
     (no partial or scratch bundle), the item ``open`` and its backlog line
     ``[ ]``, and the step returns non-zero.

The project helpers (pp_*), the scan, the repo-lock entry and the item
markers are stubbed in bash so each test drives the real step function
against a fixture tree under tmp_path; nothing touches a kanban root.
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from tests.unit.shell_harness import run_bash

_DISCOVERY_LIB = "scripts/lib/discovery.sh"


def _diagnostic(result) -> str:
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"


def _fixture(tmp_path: pathlib.Path, priorities: tuple[str, ...]) -> pathlib.Path:
    root = tmp_path / "project_a"
    for sub in ("bugs", "priority", "requirements", "tasks/queues"):
        (root / sub).mkdir(parents=True)
    for name in priorities:
        (root / "priority" / name).write_text(f"# {name}\n\n## Status\nopen\n", encoding="utf-8")
    return root


def _stubs(root: pathlib.Path, cap: str) -> str:
    """Project lookups and side-effecting helpers replaced by recorders.

    The scan lists every *.md under priority/ (and records that it ran);
    the item markers print MARK lines instead of rewriting files, so an
    item's own ## Status is only ever changed by a real write path.
    """
    return textwrap.dedent(f"""\
        log() {{ printf '%s\\n' "$*" >&2; }}
        pp_require_project_context() {{ printf '%s\\n' "$1"; }}
        pp_project_root() {{ printf '%s\\n' '{root}'; }}
        pp_bugs_dir() {{ printf '%s\\n' '{root}/bugs'; }}
        pp_priority_dir() {{ printf '%s\\n' '{root}/priority'; }}
        pp_queue_path() {{ printf '%s\\n' '{root}/tasks/queues/'"$2"'_backlog.md'; }}
        pp_requirements_dir() {{ printf '%s\\n' '{root}/requirements'; }}
        pp_tasks_dir() {{ printf '%s\\n' '{root}/tasks'; }}
        pp_release_state() {{ printf '%s\\n' '{root}/release-state.md'; }}
        pp_max_priorities_per_run() {{ printf '%s\\n' '{cap}'; }}
        _disc_find_unhandled_items() {{
            echo "SCAN_RAN" >&2
            local _f
            for _f in "$1"/*.md; do [[ -f "$_f" ]] && printf '%s\\n' "$_f"; done
        }}
        _disc_active_rc_blocks() {{ return 0; }}
        _disc_enter_write_phase() {{ return 0; }}
        _disc_pending_bundle_of_type() {{ return 1; }}
        discovery_compute_next_patch() {{ echo "v1.2.4"; }}
        _disc_check_ceiling_and_log() {{ return 0; }}
        _disc_order_bundle_items() {{ printf '%s\\n' "$1"; }}
        wf_load_plugin() {{ WF_LOAD_ERROR=""; return 0; }}
        wf_bundle_source_branch() {{ echo "rc/$1"; }}
        _disc_mark_cache_bundled() {{ echo "MARK_CACHE $2" >&2; }}
        _disc_set_status_field() {{ echo "MARK_STATUS $(basename "$1") $2" >&2; }}
        """)


def _run_step(tmp_path: pathlib.Path, root: pathlib.Path, cap: str, body: str):
    script = (
        "set -uo pipefail\n"
        f"TEAM_ROOT='{tmp_path}'\n"
        f"source {_DISCOVERY_LIB}\n"
        + _stubs(root, cap)
        + textwrap.dedent(body)
    )
    return run_bash(tmp_path, script)


def test_cap_zero_disables_priority_ingest_and_touches_nothing(tmp_path):
    root = _fixture(tmp_path, ("PRIORITY-0001-a.md",))
    item = root / "priority" / "PRIORITY-0001-a.md"
    before = item.read_text(encoding="utf-8")

    result = _run_step(tmp_path, root, "0", """\
        discovery_step_priority project_a
        echo "RC=$?"
        """)

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert "priority ingest is disabled" in result.stderr, _diagnostic(result)
    assert "max_priorities_per_run = 0" in result.stderr, _diagnostic(result)
    assert "SCAN_RAN" not in result.stderr, "a disabled cap must return before scanning"
    assert "bundle written" not in result.stderr, _diagnostic(result)
    assert "MARK_" not in result.stderr, _diagnostic(result)
    assert list((root / "requirements").iterdir()) == [], _diagnostic(result)
    assert item.read_text(encoding="utf-8") == before, "the PRIORITY item must stay open and untouched"


def test_cap_zero_lets_the_pipeline_reach_the_requirements_step(tmp_path):
    root = _fixture(tmp_path, ("PRIORITY-0001-a.md",))
    (root / "project.cfg").write_text("[versioning]\nmax_priorities_per_run = 0\n", encoding="utf-8")

    result = _run_step(tmp_path, root, "0", f"""\
        _pp_project_cfg_file() {{ printf '%s\\n' '{root}/project.cfg'; }}
        pp_project_halted() {{ return 1; }}
        _disc_pm_ticket_in_flight() {{ return 1; }}
        _disc_chain_idle() {{ return 0; }}
        pp_max_minor() {{ echo ""; }}
        pp_max_major() {{ echo ""; }}
        pp_max_patch() {{ echo ""; }}
        discovery_step_bugs() {{ return 1; }}
        discovery_step_requirements() {{ echo "REQUIREMENTS_STEP_REACHED" >&2; return 1; }}
        DISCOVERY_LAST_STATUS=""
        _disc_run_pipeline_once project_a
        echo "STATUS=$DISCOVERY_LAST_STATUS"
        """)

    assert "REQUIREMENTS_STEP_REACHED" in result.stderr, _diagnostic(result)
    assert "STATUS=produced_work" not in result.stdout, _diagnostic(result)
    assert "priority ingest is disabled" in result.stderr, _diagnostic(result)
    assert list((root / "requirements").iterdir()) == [], _diagnostic(result)


def test_cap_one_bundles_only_the_lowest_numbered_priority(tmp_path):
    root = _fixture(tmp_path, ("PRIORITY-0002-b.md", "PRIORITY-0001-a.md"))

    result = _run_step(tmp_path, root, "1", """\
        discovery_step_priority project_a
        echo "RC=$?"
        """)

    assert "RC=0" in result.stdout, _diagnostic(result)
    bundles = list((root / "requirements").iterdir())
    assert len(bundles) == 1 and bundles[0].name.startswith("v1.2.4-priority-bundle-"), _diagnostic(result)
    text = bundles[0].read_text(encoding="utf-8")
    assert "PRIORITY-0001-a.md" in text and "PRIORITY-0002-b.md" not in text, text
    assert "MARK_STATUS PRIORITY-0001-a.md running" in result.stderr, _diagnostic(result)
    assert "MARK_CACHE PRIORITY-0002-b" not in result.stderr, _diagnostic(result)
    assert "bundle written, v1.2.4 ready for Step 3" in result.stderr, _diagnostic(result)


def test_refused_priority_bundle_write_is_not_reported_as_written(tmp_path):
    root = _fixture(tmp_path, ("PRIORITY-0001-a.md",))

    result = _run_step(tmp_path, root, "1", """\
        _disc_write_bundle() { log "_disc_write_bundle: refusing (stub)"; return 1; }
        discovery_step_priority project_a
        echo "RC=$?"
        """)

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert "discovery_step_priority: bundle write refused" in result.stderr, _diagnostic(result)
    assert "bundle written" not in result.stderr, _diagnostic(result)
    assert "MARK_" not in result.stderr, "no item may be marked running when nothing was written"


def test_refused_bug_bundle_write_is_not_reported_as_written(tmp_path):
    root = _fixture(tmp_path, ())
    (root / "bugs" / "BUG-0001-a.md").write_text("# BUG-0001\n\n## Status\nopen\n", encoding="utf-8")

    result = _run_step(tmp_path, root, "1", """\
        _disc_check_bug_id_collisions() { :; }
        _disc_bug_deferral_filter() { printf '%s\n' "$2"; }
        _disc_write_bundle() { log "_disc_write_bundle: refusing (stub)"; return 1; }
        discovery_step_bugs project_a
        echo "RC=$?"
        """)

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert "discovery_step_bugs: bundle write refused" in result.stderr, _diagnostic(result)
    assert "bundle written" not in result.stderr, _diagnostic(result)
    assert "MARK_" not in result.stderr, _diagnostic(result)


def _item_fixture(tmp_path: pathlib.Path, kind: str):
    """One open item of *kind* (``BUG`` or ``PRIORITY``) plus its backlog line."""
    root = _fixture(tmp_path, ())
    sub, queue = ("bugs", "bug") if kind == "BUG" else ("priority", "priority")
    item = root / sub / f"{kind}-0001-a.md"
    item.write_text(f"# {kind}-0001\n\n## Status\nopen\n", encoding="utf-8")
    backlog = root / "tasks" / "queues" / f"{queue}_backlog.md"
    backlog.write_text(f"- [ ] {kind}-0001-a\n", encoding="utf-8")
    return root, item, backlog


# The real _disc_write_bundle re-enables errexit after its plugin probe, so
# each step runs as an `||` operand to report its status instead of ending
# the harness script.
_STEP_CALLS = {
    "PRIORITY": """\
        rc=0; discovery_step_priority project_a || rc=$?
        echo "RC=$rc"
        """,
    "BUG": """\
        _disc_check_bug_id_collisions() { :; }
        _disc_bug_deferral_filter() { printf '%s\\n' "$2"; }
        rc=0; discovery_step_bugs project_a || rc=$?
        echo "RC=$rc"
        """,
}


@pytest.mark.parametrize("kind", ["PRIORITY", "BUG"])
def test_workflow_plugin_load_failure_leaves_the_item_open_and_unticked(tmp_path, kind):
    root, item, backlog = _item_fixture(tmp_path, kind)
    item_before, backlog_before = item.read_bytes(), backlog.read_bytes()

    result = _run_step(
        tmp_path, root, "1",
        'wf_load_plugin() { WF_LOAD_ERROR="stubbed load failure"; return 1; }\n'
        + textwrap.dedent(_STEP_CALLS[kind]),
    )

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert "cannot load workflow plugin" in result.stderr, _diagnostic(result)
    assert "bundle write refused" in result.stderr, _diagnostic(result)
    assert "bundle written" not in result.stderr, _diagnostic(result)
    assert "MARK_" not in result.stderr, _diagnostic(result)
    assert list((root / "requirements").iterdir()) == [], _diagnostic(result)
    assert item.read_bytes() == item_before, "the item must stay open"
    assert backlog.read_bytes() == backlog_before, "the backlog line must stay [ ]"


@pytest.mark.parametrize("kind", ["PRIORITY", "BUG"])
def test_unwritable_requirements_dir_leaves_no_partial_bundle(tmp_path, kind):
    root, item, backlog = _item_fixture(tmp_path, kind)
    item_before, backlog_before = item.read_bytes(), backlog.read_bytes()
    requirements = root / "requirements"
    requirements.chmod(0o555)
    try:
        result = _run_step(tmp_path, root, "1", _STEP_CALLS[kind])
        leftovers = list(requirements.iterdir())
    finally:
        requirements.chmod(0o755)

    assert "RC=1" in result.stdout, _diagnostic(result)
    assert "nothing written" in result.stderr, _diagnostic(result)
    assert "bundle written" not in result.stderr, _diagnostic(result)
    assert "MARK_" not in result.stderr, _diagnostic(result)
    assert leftovers == [], f"partial or scratch bundle left behind: {leftovers}"
    assert item.read_bytes() == item_before
    assert backlog.read_bytes() == backlog_before


def test_successful_bundle_write_leaves_no_scratch_file(tmp_path):
    root, _item, _backlog = _item_fixture(tmp_path, "PRIORITY")

    result = _run_step(tmp_path, root, "1", _STEP_CALLS["PRIORITY"])

    assert "RC=0" in result.stdout, _diagnostic(result)
    names = [p.name for p in (root / "requirements").iterdir()]
    assert len(names) == 1 and names[0].startswith("v1.2.4-priority-bundle-"), names
