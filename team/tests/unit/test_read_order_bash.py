"""
test_read_order_bash.py
========================
Behavioral unit tests for team/scripts/lib/read_order.sh.

Tests source the shell script and invoke read_order_resolve_role /
read_order_role_byte_total via the bash harness. All manifest fixtures are
written under tmp_path (pytest-managed); no bare /tmp paths are used.

Behavioral requirements under test:
  read_order_resolve_role:
    - Emits, in manifest order, the resolved absolute paths of files a
      manifest names, honoring the default (self-located) root against the
      real tree.
    - Emits nothing (and narrates to stderr) for a manifest line whose file
      does not exist -- never a hard failure.
    - Blank lines and '#' comment lines in the manifest are ignored.
    - A role with no manifest file prints nothing on stdout and returns 0
      (not fatal -- narrated to stderr).

  read_order_role_byte_total:
    - Returns the summed byte size of exactly the files the resolver
      emitted for that role.
    - Returns 0 for a role with no manifest.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

_LIB = "scripts/lib/read_order.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources read_order.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


def _make_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """Build a fixture root directory with its own read-order/ manifest dir."""
    root = tmp_path / "fixture_root"
    (root / "read-order").mkdir(parents=True)
    return root


# ---------------------------------------------------------------------------
# read_order_resolve_role — against the real tree (default self-located root)
# ---------------------------------------------------------------------------


def test_resolve_role_coder_emits_real_manifest_paths_in_order(
    tmp_path: pathlib.Path,
) -> None:
    """Resolving role 'coder' against the real tree emits coder.txt's files, in order."""
    repo_team_root = pathlib.Path(__file__).resolve().parents[2]
    manifest = repo_team_root / "read-order" / "coder.txt"
    expected_lines = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    expected_paths = [str(repo_team_root / rel) for rel in expected_lines]

    result = run_bash(tmp_path, _source("read_order_resolve_role coder"))
    assert result.returncode == 0
    actual_paths = [line for line in result.stdout.splitlines() if line]
    assert actual_paths == expected_paths
    # Every emitted path must actually exist -- the resolver never emits a
    # path for a manifest line whose file is absent.
    for p in actual_paths:
        assert pathlib.Path(p).is_file()


def test_resolve_role_missing_role_emits_nothing_and_returns_zero(
    tmp_path: pathlib.Path,
) -> None:
    """A role with no manifest file prints nothing on stdout and exits 0."""
    result = run_bash(tmp_path, _source("read_order_resolve_role does-not-exist"))
    assert result.returncode == 0
    assert result.stdout == ""
    assert "no manifest for role" in result.stderr


# ---------------------------------------------------------------------------
# read_order_resolve_role — against a fabricated fixture root
# ---------------------------------------------------------------------------


def test_resolve_role_skips_missing_file_line_without_failing(
    tmp_path: pathlib.Path,
) -> None:
    """A manifest line naming an absent file is skipped, narrated, never fatal."""
    root = _make_root(tmp_path)
    (root / "PRESENT.md").write_text("present content\n", encoding="utf-8")
    (root / "read-order" / "testrole.txt").write_text(
        "PRESENT.md\nABSENT.md\n", encoding="utf-8"
    )

    result = run_bash(tmp_path, _source(f"read_order_resolve_role testrole '{root}'"))
    assert result.returncode == 0
    actual_paths = [line for line in result.stdout.splitlines() if line]
    assert actual_paths == [str(root / "PRESENT.md")]
    assert "ABSENT.md" in result.stderr
    assert "not found" in result.stderr


def test_resolve_role_ignores_blank_lines_and_comments(tmp_path: pathlib.Path) -> None:
    """Blank lines and '#' comment lines in the manifest are ignored."""
    root = _make_root(tmp_path)
    (root / "A.md").write_text("a\n", encoding="utf-8")
    (root / "B.md").write_text("b\n", encoding="utf-8")
    (root / "read-order" / "testrole.txt").write_text(
        "# a leading comment\n\nA.md\n\n# another comment\nB.md\n",
        encoding="utf-8",
    )

    result = run_bash(tmp_path, _source(f"read_order_resolve_role testrole '{root}'"))
    assert result.returncode == 0
    actual_paths = [line for line in result.stdout.splitlines() if line]
    assert actual_paths == [str(root / "A.md"), str(root / "B.md")]


def test_resolve_role_missing_manifest_file_prints_nothing(
    tmp_path: pathlib.Path,
) -> None:
    """A role with no matching manifest file under a fabricated root is not fatal."""
    root = _make_root(tmp_path)
    result = run_bash(tmp_path, _source(f"read_order_resolve_role ghost '{root}'"))
    assert result.returncode == 0
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# read_order_role_byte_total
# ---------------------------------------------------------------------------


def test_byte_total_sums_exactly_the_emitted_files(tmp_path: pathlib.Path) -> None:
    """The byte-total helper sums only the files the resolver actually emitted."""
    root = _make_root(tmp_path)
    (root / "A.md").write_text("12345", encoding="utf-8")  # 5 bytes
    (root / "B.md").write_text("1234567890", encoding="utf-8")  # 10 bytes
    (root / "read-order" / "testrole.txt").write_text(
        "A.md\nMISSING.md\nB.md\n", encoding="utf-8"
    )

    result = run_bash(
        tmp_path, _source(f"read_order_role_byte_total testrole '{root}'")
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "15"


def test_byte_total_returns_zero_for_role_with_no_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """The byte-total helper returns 0 for a role with no manifest."""
    root = _make_root(tmp_path)
    result = run_bash(tmp_path, _source(f"read_order_role_byte_total ghost '{root}'"))
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_byte_total_matches_manual_sum_of_real_coder_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """The byte-total helper for 'coder' matches a manual sum of the resolved files."""
    resolve_result = run_bash(tmp_path, _source("read_order_resolve_role coder"))
    paths = [line for line in resolve_result.stdout.splitlines() if line]
    expected_total = sum(pathlib.Path(p).stat().st_size for p in paths)

    total_result = run_bash(tmp_path, _source("read_order_role_byte_total coder"))
    assert total_result.returncode == 0
    assert int(total_result.stdout.strip()) == expected_total
