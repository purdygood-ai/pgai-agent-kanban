"""
test_lib_semver.py
==================
Behavioural tests for pgai_agent_kanban.lib.semver, the single source of
version ordering, and for team/pm-agent/lib/semver.py, which re-exports it.

The ordering requirement throughout: versions compare numerically per
component, so v0.9.7 sorts below v0.17.1 even though "v0.9.7" > "v0.17.1"
as text.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from pgai_agent_kanban.lib import semver

_PM_AGENT_SEMVER = Path(__file__).resolve().parents[2] / "pm-agent" / "lib" / "semver.py"

PUBLIC_NAMES = ("lt", "le", "gt", "ge", "eq", "compare", "from_filename", "parse")


def _load_pm_agent_semver():
    """Load team/pm-agent/lib/semver.py under a unique module name."""
    spec = importlib.util.spec_from_file_location("pm_agent_semver_reexport", _PM_AGENT_SEMVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_v0_9_7_orders_below_v0_17_1() -> None:
    assert semver.lt("v0.9.7", "v0.17.1")
    assert semver.gt("v0.17.1", "v0.9.7")
    assert semver.compare("v0.9.7", "v0.17.1") == -1
    assert "v0.9.7" > "v0.17.1"  # the text ordering the helper exists to avoid


def test_sorting_with_the_sort_key_is_numeric() -> None:
    versions = ["v0.17.1", "v0.9.7", "v0.10.0", "v1.0.0", "v0.9.10"]
    assert sorted(versions, key=semver.version_sort_key) == [
        "v0.9.7", "v0.9.10", "v0.10.0", "v0.17.1", "v1.0.0",
    ]


def test_sorting_with_parse_is_numeric() -> None:
    assert max(["v0.9.7", "v0.17.1", "v0.10.2"], key=semver.parse) == "v0.17.1"


# ---------------------------------------------------------------------------
# parse_version: lenient, None on malformed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v0.17.1", (0, 17, 1)),
        ("V2.0.3", (2, 0, 3)),
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3.4", (1, 2, 3)),
    ],
)
def test_parse_version_reads_three_components(raw: str, expected: tuple) -> None:
    assert semver.parse_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "v1.2", "none", "vX.Y.Z", "v1.x.3"])
def test_parse_version_returns_none_for_malformed(raw: str) -> None:
    assert semver.parse_version(raw) is None


# ---------------------------------------------------------------------------
# version_sort_key: total order, never raises
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v1.16.2", (1, 16, 2)),
        ("1.0.0", (1, 0, 0)),
        ("v1.2", (1, 2, 0)),
        ("v1.x.3", (1, 0, 0)),
        ("unknown", (0, 0, 0)),
        ("", (0, 0, 0)),
        ("ai_1.2.3", (1, 2, 3)),
    ],
)
def test_version_sort_key_values(raw: str, expected: tuple) -> None:
    assert semver.version_sort_key(raw) == expected


def test_version_sort_key_puts_malformed_below_every_release() -> None:
    assert semver.version_sort_key("not-a-version") < semver.version_sort_key("v0.0.1")


# ---------------------------------------------------------------------------
# parse / from_filename
# ---------------------------------------------------------------------------


def test_parse_rejects_malformed_with_value_error() -> None:
    with pytest.raises(ValueError, match="Cannot parse version"):
        semver.parse("v1.2")


def test_from_filename_reads_the_basename_only() -> None:
    assert semver.from_filename("/v9.9.9/release-notes/v0.17.0-notes.md") == "v0.17.0"
    assert semver.from_filename("notes.md") == ""


# ---------------------------------------------------------------------------
# pm-agent re-export
# ---------------------------------------------------------------------------


def test_pm_agent_path_re_exports_the_shared_functions() -> None:
    pm_semver = _load_pm_agent_semver()
    for name in PUBLIC_NAMES:
        assert getattr(pm_semver, name) is getattr(semver, name), name


def test_pm_agent_path_orders_versions_like_the_shared_module() -> None:
    pm_semver = _load_pm_agent_semver()
    assert pm_semver.lt("v0.9.7", "v0.17.1")
    assert pm_semver.le("v0.9.7", "v0.9.7")
    assert pm_semver.ge("v0.17.1", "v0.9.7")
    assert pm_semver.eq("0.9.7", "v0.9.7")
    assert pm_semver.compare("v0.17.1", "v0.9.7") == 1
    assert pm_semver.from_filename("v0.17.1-plan.md") == "v0.17.1"
