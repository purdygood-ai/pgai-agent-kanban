"""
test_suite_order_independence_hook.py
======================================
Fast, always-on self-test for the order-coupling detection seam
(tests/conftest.py's pytest_collection_modifyitems hook, driven by the
PGAI_TEST_ORDER environment variable).

The heavier order-coupling EXPERIMENT -- running the whole collected estate
twice and diffing per-test PASS/FAIL/SKIP outcomes -- lives in
team/scripts/verify-test-order-independence.sh, an on-demand check (its own
cost is roughly double a normal suite run, so it is not wired into the fast
per-RC gate; see that script's own header for the full rationale). This
module is the cheap, always-on counterpart: it unit-tests the REORDERING
MECHANISM itself directly (no subprocess, no real pytest re-invocation), so
a regression in the seam that the heavier check depends on is caught on
every ordinary run-unit-tests.sh invocation, not only when the on-demand
script happens to be run.

Coverage:
  - Unset / empty PGAI_TEST_ORDER leaves collection order untouched.
  - "reverse" reverses the collected item list end-to-end.
  - "shuffle:<seed>" deterministically reorders items -- the SAME seed
    produces the SAME order across two independent calls (reproducibility),
    and a shuffle of more than one item actually changes the order (not a
    silent no-op).
  - An unrecognized value leaves collection order untouched rather than
    guessing what the caller meant (matches conftest.py's own comment).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_CONFTEST_PATH = pathlib.Path(__file__).parent.parent / "conftest.py"


def _load_conftest():
    """Import tests/conftest.py as a standalone module (not via pytest's
    own conftest machinery), so this test can call
    pytest_collection_modifyitems directly with synthetic input."""
    spec = importlib.util.spec_from_file_location("_order_hook_conftest", _CONFTEST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeItem:
    """A minimal stand-in for a pytest.Item: carries the real `nodeid`
    attribute name the hook's canonicalisation step sorts on, plus the
    identity/equality behavior needed to verify the hook reorders (or does
    not reorder) a list."""

    def __init__(self, node_id: str) -> None:
        self.nodeid = node_id

    def __repr__(self) -> str:
        return f"_FakeItem({self.nodeid!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _FakeItem) and self.nodeid == other.nodeid


def _fake_items(count: int) -> list[_FakeItem]:
    return [_FakeItem(f"test_{i}") for i in range(count)]


@pytest.fixture
def order_hook():
    return _load_conftest().pytest_collection_modifyitems


def test_unset_order_env_leaves_collection_order_untouched(monkeypatch, order_hook) -> None:
    """No PGAI_TEST_ORDER set: the hook must not touch the item list at
    all -- this is the default path every normal test invocation takes."""
    monkeypatch.delenv("PGAI_TEST_ORDER", raising=False)
    items = _fake_items(5)
    original_order = list(items)

    order_hook(config=None, items=items)

    assert items == original_order, (
        "pytest_collection_modifyitems must be a no-op when PGAI_TEST_ORDER "
        "is unset -- normal runs must never be reordered."
    )


def test_empty_order_env_leaves_collection_order_untouched(monkeypatch, order_hook) -> None:
    """An empty-string PGAI_TEST_ORDER is treated the same as unset."""
    monkeypatch.setenv("PGAI_TEST_ORDER", "")
    items = _fake_items(5)
    original_order = list(items)

    order_hook(config=None, items=items)

    assert items == original_order


def test_reverse_reverses_the_full_collected_list(monkeypatch, order_hook) -> None:
    """PGAI_TEST_ORDER=reverse reverses the entire item list end-to-end --
    the deliberately-different order the order-coupling experiment needs."""
    monkeypatch.setenv("PGAI_TEST_ORDER", "reverse")
    items = _fake_items(6)
    original_order = list(items)

    order_hook(config=None, items=items)

    assert items == list(reversed(original_order))
    # Sanity: a real reversal of more than one distinct item must actually
    # change the order (this would also catch an accidental no-op reverse).
    assert items != original_order


def test_shuffle_with_same_seed_is_reproducible(monkeypatch, order_hook) -> None:
    """PGAI_TEST_ORDER=shuffle:<seed> must produce the SAME order every
    time it is given the SAME seed -- reproducibility is what lets a
    divergence found by the on-demand script be re-run and investigated."""
    items_a = _fake_items(10)
    items_b = _fake_items(10)

    monkeypatch.setenv("PGAI_TEST_ORDER", "shuffle:regression-seed-1")
    order_hook(config=None, items=items_a)

    monkeypatch.setenv("PGAI_TEST_ORDER", "shuffle:regression-seed-1")
    order_hook(config=None, items=items_b)

    assert items_a == items_b, (
        "The same shuffle seed must produce the same collection order on "
        "independent invocations."
    )


def test_shuffle_actually_changes_order_for_a_nontrivial_list(monkeypatch, order_hook) -> None:
    """A shuffle of a list long enough to make an accidental identity
    shuffle astronomically unlikely must change the order -- otherwise the
    "deliberately different order" this seam exists to produce would be a
    silent no-op."""
    monkeypatch.setenv("PGAI_TEST_ORDER", "shuffle:regression-seed-2")
    items = _fake_items(30)
    original_order = list(items)

    order_hook(config=None, items=items)

    assert items != original_order, (
        "shuffle: must change collection order for a list this long -- an "
        "unchanged order would mean the seam is not actually reordering "
        "anything."
    )
    # No items lost or duplicated -- a reorder, not a corruption.
    assert sorted(i.nodeid for i in items) == sorted(i.nodeid for i in original_order)


def test_unrecognized_order_value_leaves_collection_order_untouched(monkeypatch, order_hook) -> None:
    """An unrecognized PGAI_TEST_ORDER value must never be silently guessed
    at -- it leaves collection order untouched, exactly like the unset
    case (matches the conftest.py hook's own documented behavior)."""
    monkeypatch.setenv("PGAI_TEST_ORDER", "not-a-real-order-mode")
    items = _fake_items(5)
    original_order = list(items)

    order_hook(config=None, items=items)

    assert items == original_order
