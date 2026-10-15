"""
test_dashboard_queue_pane.py — ``pgai_agent_kanban.dashboard.queue_pane``, the
one text renderer of the queue-state pane (show-queues.sh and the compact
body of GET /show-queues), with its two small helpers:
``lib.active_provider.read_active_provider`` and
``dashboard.halt_state.halt_flag_and_text``.

What is proven here:

  1. Colours: on unless ``TERM=dumb`` / ``NO_COLOR=1``; each colour from its
     ``PGAI_DASHBOARD_COLOR_*`` variable, else kanban.cfg ``[dashboard]``,
     else the default. Pane width from ``PGAI_AGENT_PANE_WIDTH``.
  2. Compact rows: exact text for an empty role, a role with state codes,
     coloured codes, and the currently-working section (slug, started time,
     each elapsed format).
  3. Details listing: exact text, agent order, blank line between agents,
     each warning's wording, header colour.
  4. Planning: the named project is fetched first and always; iterate-all
     lists registered projects whose directory exists; compact mode always
     fetches its data project, even an empty or missing one, while details
     mode fetches a fallback data project only when it exists.
  5. The whole pane: provider line and its warning, compact counters from
     the data project, a compact pane with no data project or a missing one
     refused with no text rendered, details header colour from the data
     project's halt state.
  6. The provider reader's values and fall-backs, and the halt pair.

Boards are throwaway directories under ``tmp_path``; the renderer is fed
plain dicts shaped like ``ops.read.get_queues`` results where a board is not
needed.
"""

from __future__ import annotations

import pathlib
import time

import pytest

from pgai_agent_kanban.dashboard import queue_pane
from pgai_agent_kanban.dashboard.halt_state import halt_flag_and_text
from pgai_agent_kanban.dashboard.queue_details import empty_queue_details
from pgai_agent_kanban.dashboard.task_counts import QUEUE_ROLES, QUEUE_STATS
from pgai_agent_kanban.lib.active_provider import read_active_provider
from pgai_agent_kanban.lib.palette import ansi_code
from pgai_agent_kanban.ops import read as ops_read
from pgai_agent_kanban.ops.context import OpsContext
from pgai_agent_kanban.ops.errors import NotFound, Refused

_PLAIN = {"TERM": "dumb"}
_COLOR = {"TERM": "xterm-256color"}


def _counts(**per_role: dict) -> dict:
    zero = {stat: 0 for stat in QUEUE_STATS}
    return {role: {**zero, **per_role.get(role, {})} for role in QUEUE_ROLES}


def _queues(counts: dict, working=None, halt=None, details=None) -> dict:
    return {
        "counts": counts,
        "working": working,
        "halt": halt or {"flag": "no", "text": ""},
        "details": details or empty_queue_details(),
    }


def _palette(environ: dict, kanban_root: pathlib.Path) -> queue_pane.PanePalette:
    return queue_pane.resolve_pane_palette(kanban_root, environ)


# ---------------------------------------------------------------------------
# 1. Colours and width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("environ", [{"TERM": "dumb"}, {"TERM": "xterm", "NO_COLOR": "1"}])
def test_colour_off_leaves_every_escape_empty(tmp_path: pathlib.Path, environ: dict) -> None:
    palette = _palette(environ, tmp_path)
    assert palette.use_color is False
    assert {getattr(palette, name) for name in ("reset", "label", "done", "halt", "dim", "bold")} == {""}


def test_colour_names_come_from_environment_then_kanban_cfg_then_default(tmp_path: pathlib.Path) -> None:
    (tmp_path / "kanban.cfg").write_text(
        "[dashboard]\ncolor_done = blue\ncolor_label = magenta\ncolor_working =\n", encoding="utf-8"
    )
    palette = _palette({**_COLOR, "PGAI_DASHBOARD_COLOR_LABEL": "white", "PGAI_DASHBOARD_COLOR_HALT": "blue"},
                       tmp_path)
    assert palette.done == ansi_code("blue", True)        # kanban.cfg
    assert palette.label == ansi_code("white", True)      # environment wins over kanban.cfg
    assert palette.working == ansi_code("yellow", True)   # empty kanban.cfg value -> default
    assert palette.blocked == ansi_code("red", True)      # default
    assert palette.halt == ansi_code("blue", True)        # environment only
    assert palette.dim == ansi_code("dim", True)


@pytest.mark.parametrize(("value", "expected"), [(None, 36), ("50", 50), ("", 36), ("wide", 36)])
def test_pane_width(value, expected: int) -> None:
    environ = {} if value is None else {"PGAI_AGENT_PANE_WIDTH": value}
    assert queue_pane.pane_width(environ) == expected


# ---------------------------------------------------------------------------
# 2. Compact rows
# ---------------------------------------------------------------------------


def test_compact_rows_plain(tmp_path: pathlib.Path) -> None:
    counts = _counts(PM={"TOTAL": 2, "DONE": 1},
                     CODER={"TOTAL": 7, "DONE": 4, "WORKING": 1, "BLOCKED": 1, "WAITING": 1})
    text = queue_pane.render_compact_rows(_queues(counts), _palette(_PLAIN, tmp_path), 36, now=0)
    assert text.splitlines() == [
        "pm:     1/2" + " " * 22 + "50%",
        "coder:  4/7  1W 1B 1Wa" + " " * 11 + "57%",
        "writer: 0/0",
        "tester: 0/0",
        "cm:     0/0",
    ]


def test_compact_row_keeps_one_space_before_the_percentage_when_too_wide(tmp_path: pathlib.Path) -> None:
    counts = _counts(CODER={"TOTAL": 100, "DONE": 100, "WORKING": 10, "BLOCKED": 20, "WAITING": 30})
    text = queue_pane.render_compact_rows(_queues(counts), _palette(_PLAIN, tmp_path), 20, now=0)
    assert text.splitlines()[1] == "coder:  100/100  10W 20B 30Wa 100%"


def test_compact_rows_colour_the_label_count_and_each_state_code(tmp_path: pathlib.Path) -> None:
    palette = _palette(_COLOR, tmp_path)
    counts = _counts(CODER={"TOTAL": 2, "DONE": 1, "WORKING": 1})
    rows = queue_pane.render_compact_rows(_queues(counts), palette, 36, now=0).splitlines()
    p = palette
    assert rows[0] == f"{p.label}pm:     {p.reset}{p.dim}0/0{p.reset}"
    assert rows[1] == f"{p.label}coder:  {p.reset}{p.done}1/2{p.reset}  {p.working}1W{p.reset}" + " " * 18 + "50%"


@pytest.mark.parametrize(
    ("age", "elapsed"),
    [(42, "42s"), (437, "7m 17s"), (7380, "2h 3m")],
)
def test_working_section_shows_slug_start_time_and_elapsed(tmp_path: pathlib.Path, age: int, elapsed: str) -> None:
    mtime = 1767268800
    working = {"task_id": "CODER-20260101-001-fix-the-thing", "mtime": mtime, "role": "CODER"}
    text = queue_pane.render_compact_rows(_queues(_counts(), working), _palette(_PLAIN, tmp_path), 36,
                                          now=mtime + age)
    assert text.splitlines()[5:] == [
        "",
        "— currently working —",
        "fix-the-thing",
        f"  started: {time.strftime('%H:%M:%S', time.localtime(mtime))}",
        f"  elapsed: {elapsed}",
    ]


def test_working_id_without_slug_shape_is_shown_whole_and_truncated_to_width(tmp_path: pathlib.Path) -> None:
    working = {"task_id": "CLAUDE-CODER-20260101-001-legacy-name", "mtime": 0, "role": "CODER"}
    text = queue_pane.render_compact_rows(_queues(_counts(), working), _palette(_PLAIN, tmp_path), 12, now=0)
    assert text.splitlines()[5:] == ["", "— currently working —", "CLAUDE-CODER"]


def test_no_data_renders_all_zero_rows_and_no_working_section(tmp_path: pathlib.Path) -> None:
    text = queue_pane.render_compact_rows(None, _palette(_PLAIN, tmp_path), 36, now=0)
    assert text == "pm:     0/0\ncoder:  0/0\nwriter: 0/0\ntester: 0/0\ncm:     0/0\n"


# ---------------------------------------------------------------------------
# 3. Details listing
# ---------------------------------------------------------------------------


def test_details_listing_plain_text(tmp_path: pathlib.Path) -> None:
    details = empty_queue_details()
    details["coder"]["WORKING"] = [{"task_id": "CODER-2", "warning": "marker_mismatch"},
                                   {"task_id": "CODER-1", "warning": None}]
    details["coder"]["BACKLOG"] = [{"task_id": "CODER-3", "warning": "unknown_state:FOO"}]
    details["pm"]["BLOCKED"] = [{"task_id": "PM-1", "warning": "no_queue_entry"}]
    details["unknown"]["WAITING"] = [{"task_id": "X-1", "warning": "folder_missing"}]

    text = queue_pane.render_details_listing(details, "", _palette(_PLAIN, tmp_path))

    assert text == (
        "=== pm ===\n[BLOCKED]\n  PM-1  ⚠ no queue entry\n"
        "\n"
        "=== coder ===\n[WORKING]\n  CODER-1\n  CODER-2  ⚠ marker/status mismatch\n"
        "[BACKLOG]\n  CODER-3  ⚠ [UNKNOWN STATE: FOO]\n"
        "\n"
        "=== unknown ===\n[WAITING]\n  X-1  ⚠ task folder missing\n"
    )


def test_details_listing_colours(tmp_path: pathlib.Path) -> None:
    p = _palette(_COLOR, tmp_path)
    details = empty_queue_details()
    details["cm"]["WAITING"] = [{"task_id": "CM-1", "warning": "no_queue_entry"}]

    labelled = queue_pane.render_details_listing(details, "", p)
    halted = queue_pane.render_details_listing(details, p.halt, p)

    assert labelled == (
        f"{p.label}{p.bold}=== cm ==={p.reset}\n{p.waiting}[WAITING]{p.reset}\n"
        f"  {p.dim}CM-1{p.reset}  {p.yellow}⚠ no queue entry{p.reset}\n"
    )
    assert halted.startswith(f"{p.halt}{p.bold}=== cm ===")


def test_empty_details_render_nothing(tmp_path: pathlib.Path) -> None:
    assert queue_pane.render_details_listing(empty_queue_details(), "", _palette(_PLAIN, tmp_path)) == ""


# ---------------------------------------------------------------------------
# 4. Planning
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: pathlib.Path) -> OpsContext:
    """Three registered projects (priority order b, a, c); c has no directory."""
    (tmp_path / "projects.cfg").write_text(
        "[project:a-proj]\npriority = 2\n[project:b-proj]\npriority = 1\n[project:c-proj]\npriority = 3\n",
        encoding="utf-8",
    )
    for name in ("a-proj", "b-proj", "lone"):
        (tmp_path / "projects" / name / "tasks").mkdir(parents=True)
    return OpsContext(kanban_root=tmp_path)


def test_plan_compact_named_project(registry: OpsContext) -> None:
    plan = queue_pane.plan_queue_pane(registry, "a-proj", "a-proj", False)
    assert (plan.listed, plan.fetch) == ((), ("a-proj",))


def test_plan_named_project_is_fetched_even_without_a_directory(registry: OpsContext) -> None:
    plan = queue_pane.plan_queue_pane(registry, "c-proj", "c-proj", True)
    assert (plan.listed, plan.fetch) == (("c-proj",), ("c-proj",))


def test_plan_details_iterates_registered_projects_with_a_directory(registry: OpsContext) -> None:
    plan = queue_pane.plan_queue_pane(registry, "", "", True)
    assert plan.listed == ("b-proj", "a-proj")
    assert plan.fetch == ("b-proj", "a-proj")


def test_plan_compact_always_fetches_the_data_project_whatever_its_name(registry: OpsContext) -> None:
    assert queue_pane.plan_queue_pane(registry, "", "lone", False).fetch == ("lone",)
    assert queue_pane.plan_queue_pane(registry, "", "ghost", False).fetch == ("ghost",)
    assert queue_pane.plan_queue_pane(registry, "", "", False).fetch == ("",)


def test_plan_details_fetches_a_fallback_data_project_only_when_it_exists(registry: OpsContext) -> None:
    assert queue_pane.plan_queue_pane(registry, "", "lone", True).fetch == ("b-proj", "a-proj", "lone")
    assert queue_pane.plan_queue_pane(registry, "", "ghost", True).fetch == ("b-proj", "a-proj")


# ---------------------------------------------------------------------------
# 5. The whole pane
# ---------------------------------------------------------------------------


def _render(ctx: OpsContext, project: str, data_project: str, details: bool, environ: dict):
    plan = queue_pane.plan_queue_pane(ctx, project, data_project, details)
    queues = {name: ops_read.get_queues(ctx, name) for name in plan.fetch}
    return queue_pane.render_queue_pane(ctx, plan, queues, environ=environ, now=0)


def test_pane_starts_with_the_provider_line_and_reports_its_warning(registry: OpsContext) -> None:
    pane = _render(registry, "lone", "lone", False, _PLAIN)
    assert pane.stdout.splitlines()[0] == "Active provider: claude"
    assert "kanban.cfg not found" in pane.stderr

    (registry.kanban_root / "kanban.cfg").write_text("[providers]\nactive = Codex\n", encoding="utf-8")
    pane = _render(registry, "", "", True, _PLAIN)
    assert pane.stdout.splitlines()[0] == "Active provider: codex"
    assert pane.stderr == ""


def test_compact_counters_come_from_the_data_project(registry: OpsContext) -> None:
    task = registry.kanban_root / "projects" / "lone" / "tasks" / "CODER-20260101-001-x"
    task.mkdir()
    (task / "status.md").write_text("## State\nBLOCKED\n", encoding="utf-8")
    rows = _render(registry, "", "lone", False, _PLAIN).stdout.splitlines()
    assert rows[2] == "coder:  0/1  1B" + " " * 19 + "0%"


def test_compact_pane_with_no_data_project_is_refused_not_rendered_as_zeros(registry: OpsContext) -> None:
    with pytest.raises(Refused) as excinfo:
        _render(registry, "", "", False, _PLAIN)
    assert str(excinfo.value).startswith("error:   show_queues.no_project\n")


def test_compact_pane_with_a_missing_fallback_data_project_is_refused(registry: OpsContext) -> None:
    with pytest.raises(NotFound) as excinfo:
        _render(registry, "", "ghost", False, _PLAIN)
    assert str(excinfo.value).startswith("error:   show_queues.project_directory_missing\n")


def test_compact_render_requires_the_data_project_result(registry: OpsContext) -> None:
    plan = queue_pane.plan_queue_pane(registry, "", "lone", False)
    with pytest.raises(KeyError):
        queue_pane.render_queue_pane(registry, plan, {}, environ=_PLAIN, now=0)


@pytest.mark.parametrize(
    ("sentinel", "body", "expected_color"),
    [("HALT", "", "halt"), ("HALT-AFTER", "rc", "yellow"), (None, "", "label")],
)
def test_details_header_colour_follows_the_data_project_halt_state(
    registry: OpsContext, sentinel, body: str, expected_color: str
) -> None:
    project = registry.kanban_root / "projects" / "a-proj"
    task = project / "tasks" / "CODER-20260101-001-x"
    task.mkdir()
    (task / "status.md").write_text("## State\nBACKLOG\n", encoding="utf-8")
    if sentinel:
        (project / sentinel).write_text(body, encoding="utf-8")
    palette = _palette(_COLOR, registry.kanban_root)
    text = _render(registry, "a-proj", "a-proj", True, _COLOR).stdout
    # The README-less task is listed under the "unknown" agent.
    assert text.splitlines()[1].startswith(f"{getattr(palette, expected_color)}{palette.bold}=== unknown ===")


def test_missing_fallback_data_project_takes_the_global_halt_for_the_header(registry: OpsContext) -> None:
    (registry.kanban_root / "HALT").write_text("", encoding="utf-8")
    task = registry.kanban_root / "projects" / "b-proj" / "tasks" / "CODER-20260101-001-x"
    task.mkdir()
    (task / "status.md").write_text("## State\nBACKLOG\n", encoding="utf-8")
    palette = _palette(_COLOR, registry.kanban_root)
    text = _render(registry, "", "ghost", True, _COLOR).stdout
    assert text.splitlines()[1].startswith(f"{palette.halt}{palette.bold}=== unknown ===")


# ---------------------------------------------------------------------------
# 6. Provider reader and halt pair
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cfg", "provider", "warning_fragment"),
    [
        (None, "claude", "kanban.cfg not found"),
        ("[providers]\nactive = gemini\n", "gemini", None),
        ("[providers]\nactive =  CO DEX \n", "codex", None),
        ("[providers]\n", "claude", "unset or empty"),
        ("[providers]\nactive = openai\n", "claude", "unrecognized provider 'openai'"),
    ],
)
def test_read_active_provider(tmp_path: pathlib.Path, cfg, provider: str, warning_fragment) -> None:
    if cfg is not None:
        (tmp_path / "kanban.cfg").write_text(cfg, encoding="utf-8")
    got, warning = read_active_provider(tmp_path)
    assert got == provider
    if warning_fragment is None:
        assert warning == ""
    else:
        assert warning_fragment in warning


def test_halt_pair_for_a_project_directory_that_does_not_exist(tmp_path: pathlib.Path) -> None:
    assert halt_flag_and_text(tmp_path / "projects" / "ghost", tmp_path) == ("no", "")
    (tmp_path / "HALT").write_text("", encoding="utf-8")
    assert halt_flag_and_text(tmp_path / "projects" / "ghost", tmp_path) == ("yes", "HALT")
