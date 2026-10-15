"""
test_carried_item_bundle_hold.py
================================
A shipped bundle that is the only record of a still-``running`` item stays
in requirements/, and an item stranded before that rule existed is offered
again:

  - the archive plan (``pgai_agent_kanban.archive.plan``) holds an older
    shipped bundle that lists a ``running`` item, with a reason naming the
    item, and moves it once every listed item is terminal;
  - ``purge_shipped_bundles`` (team/scripts/lib/purge-helpers.sh) with
    apply leaves such a bundle in place, and still purges one whose items
    are all terminal;
  - once the bundle has already left requirements/, the discovery item scan
    (``_disc_find_unhandled_items``) re-offers the ``running`` item and logs
    a WARNING naming it, while a ``running`` item a live bundle still lists
    stays out;
  - ``bundle_items.running_items`` finds a listed item by name beside the
    bundle even when the bundle names it under another install's path.

Every fixture is a throwaway kanban root under pytest's tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest

from pgai_agent_kanban.archive import plan
from pgai_agent_kanban.archive.plan import HOLD_CARRIED_ITEM
from pgai_agent_kanban.lib import bundle_items
from tests.fixtures.archive_sandbox import ArchiveSandbox
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PURGE = _TEAM_DIR / "scripts" / "lib" / "purge-helpers.sh"
_ITEMS_LIB = _TEAM_DIR / "scripts" / "lib" / "discovery_items.sh"
_OLD = time.time() - 100 * 86400


def _fixture(tmp_path: pathlib.Path, bug_status: str = "running"):
    """A bug file at *bug_status*, an older done bundle listing it,
    and a newer requirements file; v1.0.0 released, no Active RC."""
    sandbox = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    bug = sandbox.add_bug(1, bug_status, slug="x")
    bundle = sandbox.add_requirement("v0.9.0", "done", slug="bugfix-bundle-20260101", bundled=[bug])
    sandbox.add_requirement("v1.0.0", "done", slug="feature")
    return sandbox, bug, bundle


def _env(root: pathlib.Path) -> dict:
    (root / "tmp").mkdir(exist_ok=True)
    home = root / "home"
    home.mkdir(exist_ok=True)
    return build_scrubbed_env(
        home_dir=home,
        extra_path_dirs=[pathlib.Path(sys.executable).parent],
        extra_env={
            "KANBAN_ROOT": str(root),
            "PGAI_AGENT_KANBAN_ROOT_PATH": str(root),
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(root / "tmp"),
        },
    )


# ---------------------------------------------------------------------------
# The archive plan
# ---------------------------------------------------------------------------


def test_archive_plan_holds_an_older_bundle_that_lists_a_running_item(tmp_path) -> None:
    sandbox, bug, bundle = _fixture(tmp_path)

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    held = {h.item_id: h for h in result.held if h.category == "requirements"}
    assert bundle.name not in result.ids_in("requirements")
    assert held[bundle.name].code == HOLD_CARRIED_ITEM
    assert bug.name in held[bundle.name].detail


@pytest.mark.parametrize("bug_status", ["done", "wont-do"])
def test_archive_plan_moves_a_bundle_whose_listed_items_are_all_terminal(tmp_path, bug_status: str) -> None:
    sandbox, _bug, bundle = _fixture(tmp_path, bug_status)

    result = plan(sandbox.project, 1, kanban_root=sandbox.root)

    assert result.ids_in("requirements") == (bundle.name,)


# ---------------------------------------------------------------------------
# The shipped-bundle purge
# ---------------------------------------------------------------------------


def _purge(tmp_path: pathlib.Path, sandbox: ArchiveSandbox, bundle: pathlib.Path) -> str:
    notes = sandbox.project_root / "release-notes"
    notes.mkdir(exist_ok=True)
    (notes / "v0.9.0.md").write_text("# v0.9.0\n", encoding="utf-8")
    os.utime(bundle, (_OLD, _OLD))
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_PURGE}'\npurge_shipped_bundles '{sandbox.project_root}' 30 true\n",
        base_env=_env(sandbox.root),
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    return result.stdout


def test_purge_with_apply_keeps_a_shipped_bundle_that_lists_a_running_item(tmp_path) -> None:
    sandbox, bug, bundle = _fixture(tmp_path)

    out = _purge(tmp_path, sandbox, bundle)

    assert bundle.is_file(), out
    assert f"[SKIP] {bundle} (lists running item(s) {bug.name} — kept until they close)" in out.splitlines()
    assert out.splitlines()[-1].split()[0] == "0"


def test_purge_with_apply_still_removes_a_shipped_bundle_whose_items_are_terminal(tmp_path) -> None:
    sandbox, _bug, bundle = _fixture(tmp_path, "done")

    out = _purge(tmp_path, sandbox, bundle)

    assert not bundle.exists(), out
    assert f"[PURGED] {bundle} (shipped per release-notes/v0.9.0.md)" in out.splitlines()


# ---------------------------------------------------------------------------
# The discovery item scan re-offers an orphaned running item
# ---------------------------------------------------------------------------


def _scan(tmp_path: pathlib.Path, sandbox: ArchiveSandbox):
    bugs = sandbox.project_root / "bugs"
    return run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_ITEMS_LIB}'\n"
        f"_disc_find_unhandled_items '{bugs}' '{bugs.parent / 'tasks' / 'queues' / 'bug_backlog.md'}' BUG-\n",
        base_env=_env(sandbox.root),
        timeout=60,
    )


def test_item_scan_re_offers_a_running_item_whose_bundle_already_left_requirements(tmp_path) -> None:
    sandbox, bug, bundle = _fixture(tmp_path)
    moved = sandbox.root / "archive" / bundle.name
    moved.parent.mkdir(parents=True)
    bundle.rename(moved)

    result = _scan(tmp_path, sandbox)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(bug.resolve())]
    warnings = [line for line in result.stderr.splitlines() if line.startswith("WARNING:") and bug.name in line]
    assert len(warnings) == 1, result.stderr
    assert "re-offering" in warnings[0]


@pytest.mark.parametrize("bundle_status", ["running", "open"])
def test_item_scan_leaves_out_a_running_item_a_live_bundle_still_lists(tmp_path, bundle_status: str) -> None:
    sandbox = ArchiveSandbox(tmp_path, active_rc="none", last_released="v1.0.0")
    bug = sandbox.add_bug(1, "running", slug="x")
    sandbox.add_requirement("v1.0.1", bundle_status, slug="bugfix-bundle-20260101", bundled=[bug])

    result = _scan(tmp_path, sandbox)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "WARNING" not in result.stderr


# ---------------------------------------------------------------------------
# running_items
# ---------------------------------------------------------------------------


def test_running_items_resolves_a_listed_item_by_name_beside_the_bundle(tmp_path) -> None:
    sandbox = ArchiveSandbox(tmp_path)
    running = sandbox.add_bug(1, "running", slug="x")
    done = sandbox.add_bug(2, "done", slug="y")
    gone = sandbox.add_bug(3, "running", slug="gone")
    gone.unlink()
    elsewhere = pathlib.PurePath("/elsewhere/projects/p/bugs")
    bundle = sandbox.project_root / "requirements" / "v0.9.0-bugfix-bundle-20260101.md"
    lines = "".join(f"- {item.name} (`{elsewhere / item.name}`)\n" for item in (running, done, gone))
    bundle.write_text(f"# bundle\n\n## Status\ndone\n\n## Bundled Items\n\n{lines}", encoding="utf-8")

    assert bundle_items.running_items(bundle) == [running.name]
