"""
test_dashboard_column_render_priority_cap.py
============================================
Unit tests for the status-priority-aware row selection helper
``status_priority_cap`` from
``pgai_agent_kanban.dashboard.status_priority_cap``.

These tests cover the acceptance fixtures required by an earlier defect:

1. 10-task / cap-8 / WORKING-at-lowest-ID fixture:
   The WORKING (running) row must appear in the rendered set even though it
   sits at the tail of a DESC-sorted list and would be truncated by a naive
   items[:cap] slice.

2. BLOCKED-pinning branch (WORKING absent, BLOCKED at lowest ID):
   The BLOCKED (blocked) row must appear in the rendered set.

3. Under-cap fast path:
   Output must be byte-identical to items[:cap] (the pre-fix naive slice).

4. slots_after_pin < 0 branch (pinned rows exceed cap):
   The function returns more than cap items when running rows alone exceed cap
   — intentional, never evicts WORKING.  Behaviour is documented here.
"""

from __future__ import annotations

from pgai_agent_kanban.dashboard.status_priority_cap import status_priority_cap as _status_priority_cap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(status: str, sort_key: int) -> dict:
    """Build a minimal entry dict matching the column-render.sh item schema."""
    return {"status": status, "id_sort_key": sort_key}


def _statuses(items: list[dict]) -> list[str]:
    """Return the status field of each item, in order."""
    return [e["status"] for e in items]


def _sort_keys(items: list[dict]) -> list[int]:
    """Return the id_sort_key of each item, in order."""
    return [e["id_sort_key"] for e in items]


# ---------------------------------------------------------------------------
# Fixture builder — 10 tasks / cap-8 / WORKING at lowest ID
#
# Simulates the an earlier defect acceptance criterion #1 scenario:
#   - 10 tasks, all "open" except the lowest-ID task which is "running"
#   - List pre-sorted DESC by id_sort_key (highest first, lowest last)
#   - A naive items[:8] slice would drop tasks 001 and 002; 001 is WORKING
#     and must be pinned.
# ---------------------------------------------------------------------------

def _build_ten_task_fixture() -> list[dict]:
    """10 tasks DESC by ID, WORKING (running) at position 009 (lowest ID)."""
    items = []
    for n in range(10, 0, -1):  # 10, 9, 8, … 1 (DESC order)
        status = "running" if n == 1 else "open"
        items.append(_make_item(status, n))
    # Resulting list: sort_keys [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    #                  statuses  [open]*9 + [running]
    return items


# ---------------------------------------------------------------------------
# Test 1 — 10 tasks / cap-8 / WORKING at lowest ID
# ---------------------------------------------------------------------------

def test_working_row_pinned_when_at_tail_of_sorted_list() -> None:
    """WORKING row at the lowest ID is included even when naive slice would drop it.

    an earlier defect acceptance criterion #1:
    - 10 tasks, cap 8, one WORKING at the lowest-ID position (sort_key=1).
    - A naive items[:8] would return sort_keys [10,9,8,7,6,5,4,3] and omit
      the WORKING row (sort_key=1).
    - status_priority_cap must pin the WORKING row and return it in the set.
    """
    items = _build_ten_task_fixture()
    assert len(items) == 10

    result = _status_priority_cap(items, cap=8)

    # The WORKING row must be present in the result.
    assert any(e["status"] == "running" for e in result), (
        "WORKING (running) row was evicted from the rendered set; "
        "status_priority_cap must pin running rows regardless of their sort position."
    )

    # Result count: 1 running + 7 open = 8 (exactly cap)
    assert len(result) == 8, (
        f"Expected 8 items (cap), got {len(result)}: {_sort_keys(result)}"
    )

    # Result must be sorted DESC by id_sort_key.
    keys = _sort_keys(result)
    assert keys == sorted(keys, reverse=True), (
        f"Result is not sorted DESC by id_sort_key: {keys}"
    )


def test_working_row_pinned_with_blocked_also_present() -> None:
    """WORKING and BLOCKED rows are both pinned when present in a truncating set.

    Extends the an earlier defect acceptance criterion #1 fixture to verify that both
    attention-state row types coexist correctly: WORKING at sort_key=1,
    BLOCKED at sort_key=2, remainder at sort_keys 10..3.
    """
    items = []
    for n in range(10, 0, -1):
        if n == 1:
            status = "running"
        elif n == 2:
            status = "blocked"
        else:
            status = "open"
        items.append(_make_item(status, n))

    result = _status_priority_cap(items, cap=8)

    statuses = _statuses(result)
    assert "running" in statuses, "WORKING row must be pinned"
    assert "blocked" in statuses, "BLOCKED row must be pinned"
    assert len(result) == 8, f"Expected 8 items (cap), got {len(result)}"
    keys = _sort_keys(result)
    assert keys == sorted(keys, reverse=True), f"Result not sorted DESC: {keys}"


# ---------------------------------------------------------------------------
# Test 2 — BLOCKED-pinning branch (WORKING absent, BLOCKED at lowest ID)
# ---------------------------------------------------------------------------

def test_blocked_row_pinned_when_working_is_absent() -> None:
    """BLOCKED row at the lowest ID is included when no WORKING row exists.

    an earlier defect acceptance criterion #1 (BLOCKED-pinning branch):
    - 10 tasks, cap 8, one BLOCKED at the lowest-ID position.
    - WORKING is absent.
    - status_priority_cap must pin the BLOCKED row in the rendered set.
    """
    items = []
    for n in range(10, 0, -1):
        status = "blocked" if n == 1 else "open"
        items.append(_make_item(status, n))

    result = _status_priority_cap(items, cap=8)

    assert any(e["status"] == "blocked" for e in result), (
        "BLOCKED row was evicted from the rendered set; "
        "status_priority_cap must pin blocked rows after running rows."
    )
    assert len(result) == 8, f"Expected 8 items (cap), got {len(result)}"
    keys = _sort_keys(result)
    assert keys == sorted(keys, reverse=True), f"Result not sorted DESC: {keys}"


def test_multiple_blocked_rows_filled_to_cap() -> None:
    """Multiple BLOCKED rows are included up to the cap limit.

    Verifies the 'include as many blocked items as fit' guarantee: when there
    are more blocked rows than available slots after running rows, blocked rows
    fill remaining cap slots and the lowest-key blocked rows are dropped.
    """
    # 5 blocked (sort_keys 5..1) + 5 open (sort_keys 10..6), cap=6
    items = [_make_item("open",    k) for k in range(10, 5, -1)]  # 10,9,8,7,6
    items += [_make_item("blocked", k) for k in range(5, 0, -1)]  # 5,4,3,2,1

    result = _status_priority_cap(items, cap=6)

    # All 5 blocked fit within cap=6 (0 running + 5 blocked = 5 pinned, 1 open slot)
    statuses = _statuses(result)
    assert statuses.count("blocked") == 5, (
        f"Expected 5 blocked rows, got {statuses.count('blocked')}: {statuses}"
    )
    assert len(result) == 6, f"Expected 6 items (cap), got {len(result)}"


# ---------------------------------------------------------------------------
# Test 3 — Under-cap fast path (regression check)
# ---------------------------------------------------------------------------

def test_under_cap_output_byte_identical_to_naive_slice() -> None:
    """Under-cap boards return output byte-identical to the pre-fix items[:cap] slice.

    an earlier defect acceptance criterion #2:
    When len(items) <= cap, status_priority_cap must return a copy that is
    structurally identical to items[:cap], preserving the pre-fix behavior
    for boards that do not require truncation.
    """
    items = [
        _make_item("running", 10),
        _make_item("open",    9),
        _make_item("blocked", 8),
        _make_item("open",    7),
    ]
    cap = 8  # well above len(items)=4

    result = _status_priority_cap(items, cap=cap)
    expected = items[:]

    assert result == expected, (
        f"Under-cap result differs from items[:cap].\n"
        f"  expected: {expected}\n"
        f"  got:      {result}"
    )


def test_under_cap_returns_copy_not_same_object() -> None:
    """Under-cap path returns a copy; mutating the result does not affect the original."""
    items = [_make_item("open", k) for k in range(3, 0, -1)]
    result = _status_priority_cap(items, cap=10)

    assert result is not items, "status_priority_cap must return a copy, not the original list"

    # Mutate the copy and verify the original is unchanged.
    result.clear()
    assert len(items) == 3, "Original items list was mutated by status_priority_cap"


def test_under_cap_exact_boundary() -> None:
    """When len(items) == cap, the fast path applies and all items are returned."""
    items = [_make_item("open", k) for k in range(5, 0, -1)]
    result = _status_priority_cap(items, cap=5)

    assert result == items[:5], (
        "Exact-boundary case (len == cap) should return a copy identical to items[:cap]"
    )
    assert len(result) == 5


def test_under_cap_with_mixed_statuses() -> None:
    """Under-cap fast path applies even when items contain running/blocked statuses."""
    items = [
        _make_item("running", 3),
        _make_item("blocked", 2),
        _make_item("open",    1),
    ]
    result = _status_priority_cap(items, cap=10)
    assert result == items[:], (
        "Under-cap with mixed statuses must be byte-identical to items[:]"
    )


# ---------------------------------------------------------------------------
# Test 4 — slots_after_pin < 0 branch (pinned rows exceed cap)
# ---------------------------------------------------------------------------

def test_pinned_running_exceeds_cap_returns_all_running() -> None:
    """When running rows alone exceed cap, all running rows are returned.

    Observed behavior (intentional): status_priority_cap never evicts WORKING
    rows, so the returned list may exceed cap when len(running) > cap.
    This test documents and pins that behavior so regressions are caught.
    """
    # 5 running rows, cap=3 → slots_after_pin = 3 - 5 - 0 = -2
    items = [_make_item("running", k) for k in range(5, 0, -1)]

    result = _status_priority_cap(items, cap=3)

    running_count = sum(1 for e in result if e["status"] == "running")
    assert running_count == 5, (
        f"All 5 running rows must be returned; got {running_count}: {_statuses(result)}"
    )
    # Result exceeds cap — this is intentional: WORKING rows are never evicted.
    assert len(result) >= 3, "Result must have at least cap items when running >= cap"


def test_running_and_blocked_both_exceed_cap_running_wins() -> None:
    """When running+blocked > cap, blocked rows are truncated but running rows are not.

    slots_after_pin < 0: 2 running + 4 blocked, cap=3.
    Expected: all 2 running + 1 blocked (fills cap - len(running) = 1 slot).
    Blocked rows are truncated; running rows are kept whole.
    """
    # Items in DESC order: blocked 6,5,4,3 then running 2,1
    items = [_make_item("blocked", k) for k in range(6, 2, -1)]  # 6,5,4,3
    items += [_make_item("running", k) for k in range(2, 0, -1)]  # 2,1

    result = _status_priority_cap(items, cap=3)

    statuses = _statuses(result)
    running_count = statuses.count("running")
    blocked_count = statuses.count("blocked")

    assert running_count == 2, (
        f"Both running rows must survive; got {running_count}: {statuses}"
    )
    # cap - len(running) = 3 - 2 = 1 slot for blocked
    assert blocked_count == 1, (
        f"Expected 1 blocked row to fill remaining slot; got {blocked_count}: {statuses}"
    )
    assert len(result) == 3, f"Expected 3 items total, got {len(result)}: {statuses}"


def test_slots_after_pin_negative_no_blocked_rows() -> None:
    """When running rows alone exceed cap and no blocked rows exist, only running returned."""
    items = [_make_item("running", k) for k in range(4, 0, -1)]  # 4,3,2,1
    items += [_make_item("open",   k) for k in range(8, 4, -1)]  # 8,7,6,5

    result = _status_priority_cap(items, cap=2)

    statuses = _statuses(result)
    assert all(s == "running" for s in statuses), (
        f"Only running rows should be present when they exceed cap; got: {statuses}"
    )
    assert len(result) == 4, (
        f"All 4 running rows retained (never evicted); got {len(result)}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Edge cases
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty() -> None:
    """Empty input list returns empty list regardless of cap."""
    assert _status_priority_cap([], cap=8) == []


def test_cap_zero_returns_empty_when_no_running() -> None:
    """cap=0 with no running rows returns empty list (no slots available)."""
    items = [_make_item("open", k) for k in range(3, 0, -1)]
    result = _status_priority_cap(items, cap=0)
    # slots_after_pin = 0 - 0 - 0 = 0; remainder[:0] = []; selected = []
    assert result == [], f"Expected empty list for cap=0, got: {result}"


def test_cap_zero_with_running_returns_running() -> None:
    """cap=0 with running rows returns all running rows (never evicted)."""
    items = [_make_item("running", 2), _make_item("open", 1)]
    result = _status_priority_cap(items, cap=0)
    statuses = _statuses(result)
    # slots_after_pin = 0 - 1 - 0 = -1 → all running kept
    assert "running" in statuses, (
        f"Running row must survive even at cap=0; got: {statuses}"
    )


def test_result_sorted_desc_by_id_sort_key() -> None:
    """Result is always sorted DESC by id_sort_key regardless of bucket composition."""
    items = [
        _make_item("open",    10),
        _make_item("open",     9),
        _make_item("open",     8),
        _make_item("running",  3),  # pinned but low sort_key
        _make_item("blocked",  2),  # pinned but low sort_key
        _make_item("open",     1),
    ]
    result = _status_priority_cap(items, cap=4)

    keys = _sort_keys(result)
    assert keys == sorted(keys, reverse=True), (
        f"Result must be sorted DESC by id_sort_key; got: {keys}"
    )
    # 1 running + 1 blocked + 2 open = 4 == cap
    assert len(result) == 4


def test_only_running_items_all_returned_under_cap() -> None:
    """When all items are running and under cap, all are returned unchanged."""
    items = [_make_item("running", k) for k in range(3, 0, -1)]
    result = _status_priority_cap(items, cap=5)
    assert result == items[:], "All running items under cap must be returned unchanged"


def test_only_blocked_items_returned_up_to_cap() -> None:
    """When all items are blocked and over cap, only cap items are returned."""
    items = [_make_item("blocked", k) for k in range(10, 0, -1)]
    result = _status_priority_cap(items, cap=5)
    assert len(result) == 5, f"Expected 5 blocked items (cap), got {len(result)}"
    keys = _sort_keys(result)
    assert keys == sorted(keys, reverse=True), f"Result not sorted DESC: {keys}"
