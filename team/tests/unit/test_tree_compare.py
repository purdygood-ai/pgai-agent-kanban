"""
test_tree_compare.py
====================
Interface tests for pgai_agent_kanban.lib.tree_compare, the one deployed-copy
comparator: the skills module imports it, and subagent_deploy.sh's
deploy_tree_current runs its command line (that bash wrapper is covered in
test_upgrade_wrapper_drift.py).

Coverage:
  - A byte-identical copy, and one with extra files of its own, differ in
    nothing; a missing directory, a missing file and an edited file are each
    listed, directories first.
  - A symlink to a file is compared as the file; an --exclude glob skips a
    matching file or directory, and an excluded directory is not walked.
  - The command line exits 0 for a current target, 1 (printing the
    differing paths) for a differing or absent target, 2 for a source that
    is not a directory.

Every tree is built under tmp_path.
"""

from __future__ import annotations

import pathlib

import pytest

from pgai_agent_kanban.lib import tree_compare as tc


def _tree(root: pathlib.Path) -> pathlib.Path:
    (root / "lib" / "__pycache__").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "lib" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "lib" / "__pycache__" / "mod.pyc").write_bytes(b"\x00")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    return root


def test_identical_copy_with_extra_target_files_differs_in_nothing(tmp_path):
    src = _tree(tmp_path / "src")
    dst = _tree(tmp_path / "dst")
    (dst / "lib" / "operator.py").write_text("mine\n", encoding="utf-8")
    assert tc.tree_differing_paths(src, dst) == []


def test_missing_directory_missing_file_and_edited_file_are_listed_directories_first(tmp_path):
    src = _tree(tmp_path / "src")
    dst = _tree(tmp_path / "dst")
    (dst / "empty").rmdir()
    (dst / "README.md").unlink()
    (dst / "lib" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    assert tc.tree_differing_paths(src, dst) == ["empty", "README.md", "lib/mod.py"]


def test_symlinked_file_is_compared_as_the_file_it_names(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "real.txt").write_text("same\n", encoding="utf-8")
    (src / "link.txt").symlink_to(tmp_path / "real.txt")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "link.txt").write_text("same\n", encoding="utf-8")
    assert tc.tree_entries(src) == ([], ["link.txt"])
    assert tc.tree_differing_paths(src, dst) == []


@pytest.mark.parametrize(
    ("exclude", "expected"),
    [
        ((), ["lib/__pycache__", "lib/__pycache__/mod.pyc"]),
        (("__pycache__",), []),
        (("*.pyc",), ["lib/__pycache__"]),
    ],
)
def test_exclude_glob_skips_matching_names_and_does_not_walk_an_excluded_directory(tmp_path, exclude, expected):
    src = _tree(tmp_path / "src")
    dst = _tree(tmp_path / "dst")
    for path in sorted((dst / "lib" / "__pycache__").iterdir()):
        path.unlink()
    (dst / "lib" / "__pycache__").rmdir()
    assert tc.tree_differing_paths(src, dst, exclude=exclude) == expected


def test_command_line_exit_codes_and_differing_paths(tmp_path, capsys):
    src = _tree(tmp_path / "src")
    dst = _tree(tmp_path / "dst")
    assert tc.main([str(src), str(dst)]) == tc.EXIT_CURRENT
    assert capsys.readouterr().out == ""

    (dst / "README.md").write_text("edited\n", encoding="utf-8")
    assert tc.main(["--exclude", "__pycache__", str(src), str(dst)]) == tc.EXIT_DIFFERS
    assert capsys.readouterr().out.splitlines() == ["README.md"]

    assert tc.main([str(src), str(tmp_path / "absent")]) == tc.EXIT_DIFFERS
    assert tc.main([str(tmp_path / "absent"), str(dst)]) == tc.EXIT_USAGE
    assert "SOURCE is not a directory" in capsys.readouterr().err
    assert tc.main(["only-one"]) == tc.EXIT_USAGE
