"""
fixture_hands_free_demo_green_checks.py
========================================
Deliberate good-example fixture: a small, always-passing pytest module
standing in for a release's own green test suite in the hands-free demo
(team/tests/integration/test_cm_release_synthetic.py, class
TestHandsFreeDemoEndToEnd). The demo runner
(team/tests/fixtures/hands_free_demo_runner.sh) invokes pytest against this
file directly to prove the "green tests" half of the fixture tree
independently of the "quality-class finding in notes prose" half.

This file is not named test_*.py (see the fixtures/ directory-exclusion
note in scripts/lint_orphan_tests.py) so it is never picked up by either
gated runner's own pytest argv; it is invoked only by the demo runner and by
tests that explicitly point pytest at this path.
"""


def test_hands_free_demo_fixture_tree_is_green() -> None:
    """A trivial assertion representing an otherwise-passing fixture suite."""
    assert 1 + 1 == 2


def test_hands_free_demo_fixture_tree_has_no_defects() -> None:
    """A second trivial assertion so the fixture suite has more than one test."""
    assert sorted([3, 1, 2]) == [1, 2, 3]
