#!/usr/bin/env python3
"""
status_priority_cap.py — Status-priority-aware row selection for dashboard column rendering.

Extracts the ``_status_priority_cap`` helper from
``team/scripts/dashboard/column-render.sh`` so it can be imported and tested
independently.  The bash script's Python heredoc imports this module and
delegates to it; runtime behavior is unchanged.

Public API
----------
``status_priority_cap(items, cap)``
    Select up to *cap* items from *items*, pinning attention-state rows
    (``'running'``, then ``'blocked'``) so they are never evicted by a naive
    head-of-sorted-list truncation.

Item schema
-----------
Each element of *items* is a dict with at minimum:

    ``status``       str — one of ``'running'``, ``'blocked'``, or any other
                     string for non-attention rows.
    ``id_sort_key``  numeric — sort key used to restore visual DESC order in
                     the returned list (higher value = rendered first).

Any additional keys are passed through unchanged.

Algorithm
---------
1. Separate *items* into three buckets: running, blocked, remainder.
2. Always include all running items.
3. Include as many blocked items as fit within *cap*.
4. Fill remaining slots from *remainder* (assumed already DESC-sorted).
5. Re-sort the selected set by ``id_sort_key`` DESC to restore visual order.

Under-cap path (``len(items) <= cap``):
    Returns ``items[:]`` — a copy byte-identical to the pre-fix naive slice.
    This preserves the regression guarantee from the original fix.

Over-cap, more pinned rows than cap (``slots_after_pin < 0``):
    Returns all running rows plus as many blocked rows as fit (never evicts
    running to make room for blocked).  The returned list may exceed *cap*
    when ``len(pinned_running) > cap``; this is intentional: WORKING rows are
    never dropped, even when pinned count alone exceeds cap.
"""

from __future__ import annotations

from typing import Any


def status_priority_cap(
    items: list[dict[str, Any]],
    cap: int,
) -> list[dict[str, Any]]:
    """Select up to *cap* items, pinning ``'running'`` then ``'blocked'`` rows.

    Args:
        items: List of entry dicts, each containing at minimum ``'status'``
               (str) and ``'id_sort_key'`` (numeric).  The list is assumed to
               be sorted DESC by ``id_sort_key`` before this call, matching the
               preparation step in column-render.sh.
        cap:   Maximum number of items to return.  The under-cap fast path
               returns exactly ``len(items)`` items (a copy); the over-cap
               path may return up to ``len(pinned_running) + cap`` items when
               running items alone exceed *cap* (see module docstring).

    Returns:
        A new list (never a mutation of *items*) of selected entries, sorted
        DESC by ``id_sort_key`` to preserve the visual column order.

    Examples:
        >>> items = [
        ...     {"status": "open",    "id_sort_key": 10},
        ...     {"status": "running", "id_sort_key": 5},
        ...     {"status": "open",    "id_sort_key": 3},
        ... ]
        >>> result = status_priority_cap(items, cap=2)
        >>> len(result) == 2
        True
        >>> result[0]["status"] == "running"  # running row pinned at top by sort key
        True
    """
    if len(items) <= cap:
        return items[:]  # under-cap: identical to pre-fix naive slice

    pinned_running = [e for e in items if e["status"] == "running"]
    pinned_blocked = [e for e in items if e["status"] == "blocked"]
    remainder = [e for e in items if e["status"] not in ("running", "blocked")]

    slots_after_pin = cap - len(pinned_running) - len(pinned_blocked)
    if slots_after_pin < 0:
        # More pinned rows than cap — include all running, fill blocked up to cap.
        # Running rows are never evicted even when they alone exceed cap.
        selected = pinned_running + pinned_blocked[: max(0, cap - len(pinned_running))]
    else:
        selected = pinned_running + pinned_blocked + remainder[:slots_after_pin]

    # Re-sort by id_sort_key DESC to restore visual sort order within the selected set.
    selected.sort(key=lambda e: e["id_sort_key"], reverse=True)
    return selected
