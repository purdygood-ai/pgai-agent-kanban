"""
test_rest_remedies_name_installed_scripts.py — REST refusal remedies name the
installed operator script, shell-quoted, under the server's kanban root.

An installed kanban keeps its operator scripts at ``<kanban root>/scripts/``
(there is no ``team/`` directory), so a remedy runs as printed only when it
names that path and quotes it. Each case drives one in-process refusal through
the production app on a sandbox root whose path holds a space, takes the
command out of the remedy prose, and checks that ``shlex.split`` of it yields
the installed script as its first word -- the path a shell would run, which
the synthetic board makes real (its ``scripts/`` links the tree's own).

  1. ``POST /operations/release-locks {"release": "no-such-lock"}``: 404,
     ``release_locks.not_found``, naming ``<root>/scripts/release-locks.sh``.
  2. ``POST /operations/defer`` with a project and no ``until``: 422,
     ``defer.until_required``, naming ``<root>/scripts/defer.sh`` with the
     rest of a runnable command line.

Every root is a throwaway directory under ``tmp_path``; the five
root-resolving environment names point into it or are cleared for the
duration of each test (docs/test-authoring-guidelines.md, "Live-install
safety").
"""

from __future__ import annotations

import pathlib
import re
import shlex
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board  # noqa: E402
from tests.fixtures.route_cli_parity import build_api_client  # noqa: E402


@pytest.fixture
def board(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A synthetic board at a path holding a space, with the process environment pointed at it."""
    root = tmp_path / "kanban root" / "board"
    build_synthetic_board(root)
    (root / "tmp").mkdir(exist_ok=True)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root / "tmp"))
    return root


def _command_words(remedy: str, before: str, after: str) -> list[str]:
    """The shell words of the command between *before* and *after* in *remedy*."""
    match = re.search(re.escape(before) + r"\s*(.+?)\s*" + after, remedy, re.DOTALL)
    assert match is not None, f"no command between {before!r} and {after!r} in remedy: {remedy!r}"
    return shlex.split(match.group(1))


def _assert_names_installed_script(words: list[str], board: pathlib.Path, script: str) -> None:
    installed = str(board.resolve() / "scripts" / script)
    assert words, "the remedy's command part is empty"
    assert words[0] == installed, f"first word {words[0]!r} is not the installed script {installed!r}"
    assert pathlib.Path(words[0]).is_file(), f"the named script does not exist on the board: {words[0]}"


def test_release_locks_not_found_remedy_names_the_installed_script(board):
    response = build_api_client(board).post("/operations/release-locks", json={"release": "no-such-lock"})

    assert response.status_code == 404, response.text
    body = response.json()
    assert body["error"] == "release_locks.not_found", body
    words = _command_words(body["remedy"], "Run", r"with no flags")
    _assert_names_installed_script(words, board, "release-locks.sh")
    assert words == [str(board.resolve() / "scripts" / "release-locks.sh")], words


def test_defer_until_required_remedy_names_the_installed_script(board):
    response = build_api_client(board).post("/operations/defer", json={"project": PROJECT_NAME})

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "defer.until_required", body
    words = _command_words(body["remedy"], "e.g.", r"\.?\s*$")
    _assert_names_installed_script(words, board, "defer.sh")
    assert words[1:] == ["--project", PROJECT_NAME, "--until", "v1.2.3"], words
