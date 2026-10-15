"""
test_closed_state_readers.py
============================
The closed states are one shared list (``lib.terminal_states.CLOSED_STATES``:
done, wont-do), and the retired ``superseded`` -- not a kanban state, but
still present in some existing files -- reads as ``wont-do`` everywhere a
status is read. This module covers the discovery-side readers:

  - discovery.items.scan_unhandled_items skips a superseded intake item as
    closed, exactly like a wont-do one, and still offers an open one;
  - discovery.eligibility.list_eligible_requirements skips a superseded
    requirements file as closed;
  - lib.bundle_items.carried_item_names treats a superseded bundle as
    closed, so a 'running' item only it lists is carried;
  - pm-agent's bug_scanner.update_bug_backlog_cache marks a closed bug
    (wont-do or superseded) [x], so get_unbundled_bugs never offers it.

The board classifier, GET /board and column-render.sh are covered by
test_board_column_render_parity.py; close.sh / close_item by
test_ops_core.py and tests/integration/test_operator_commands.py.
"""

from __future__ import annotations

import io
import pathlib
import sys

import pytest

from pgai_agent_kanban.discovery.eligibility import list_eligible_requirements
from pgai_agent_kanban.discovery.items import scan_unhandled_items
from pgai_agent_kanban.lib.bundle_items import carried_item_names
from pgai_agent_kanban.lib.terminal_states import CLOSED_STATES

try:
    from pm_agent.lib.bug_scanner import get_unbundled_bugs, update_bug_backlog_cache
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "pm-agent"))
    from lib.bug_scanner import get_unbundled_bugs, update_bug_backlog_cache  # type: ignore[no-redef]


def _bug(bugs_dir: pathlib.Path, number: int, status: str) -> pathlib.Path:
    bugs_dir.mkdir(parents=True, exist_ok=True)
    path = bugs_dir / f"BUG-{number:04d}-reader-{status}.md"
    path.write_text(
        f"# BUG-{number:04d}\n\n**Severity:** low\n\n## Status\n{status}\n\n## Symptom\nx\n",
        encoding="utf-8",
    )
    return path


def test_discovery_skips_a_superseded_item_as_closed(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "proj" / "bugs"
    open_bug = _bug(bugs, 1, "open")
    _bug(bugs, 2, "wont-do")
    _bug(bugs, 3, "superseded")
    err = io.StringIO()

    offered = scan_unhandled_items(bugs, "BUG-", requirements_dir=tmp_path / "proj" / "requirements", err=err)

    assert offered == [str(open_bug.resolve())]
    assert "skipping BUG-0003-reader-superseded.md: terminal state 'superseded'" in err.getvalue()


def test_requirements_eligibility_skips_a_superseded_file_as_closed(tmp_path: pathlib.Path) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    for version, status in (("v1.1.0", "open"), ("v1.2.0", "superseded"), ("v1.3.0", "wont-do")):
        (req / f"{version}-reader.md").write_text(
            f"# {version}\n\n## Target Version\n{version}\n\n## Status\n{status}\n", encoding="utf-8",
        )
    err = io.StringIO()

    # max_major=None lifts the major ceiling (default 0) so the v1 files are judged on status alone.
    lines = list_eligible_requirements(req, "v1.0.0", max_major=None, err=err)

    assert lines == [str((req / "v1.1.0-reader.md").resolve())]
    assert "skipping v1.2.0-reader.md: terminal state 'superseded'" in err.getvalue()


@pytest.mark.parametrize("bundle_status", [*CLOSED_STATES, "superseded"])
def test_a_running_item_listed_only_by_a_closed_bundle_is_carried(
    tmp_path: pathlib.Path, bundle_status: str,
) -> None:
    req = tmp_path / "requirements"
    req.mkdir()
    item = tmp_path / "bugs" / "BUG-0001-reader-running.md"
    (req / "v1.0.0-bugfix-bundle-20260101.md").write_text(
        f"# Bundle\n\n## Status\n\n{bundle_status}\n\n## Bundled Items\n\n- {item.name} (`{item}`)\n",
        encoding="utf-8",
    )
    assert carried_item_names(req) == frozenset({item.name})


def test_bug_backlog_cache_marks_closed_bugs_bundled(tmp_path: pathlib.Path) -> None:
    bugs = tmp_path / "bugs"
    _bug(bugs, 1, "open")
    _bug(bugs, 2, "wont-do")
    _bug(bugs, 3, "superseded")
    backlog = tmp_path / "tasks" / "queues" / "bug_backlog.md"

    unbundled = get_unbundled_bugs(str(bugs), str(backlog))

    assert [b["id"] for b in unbundled] == ["BUG-0001-reader-open"]
    text = backlog.read_text(encoding="utf-8")
    assert "- [ ] BUG-0001-reader-open\n" in text
    assert "- [x] BUG-0002-reader-wont-do\n" in text
    assert "- [x] BUG-0003-reader-superseded\n" in text
    update_bug_backlog_cache(str(bugs), str(backlog))
    assert backlog.read_text(encoding="utf-8") == text
