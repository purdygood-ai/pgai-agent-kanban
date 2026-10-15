"""
test_markdown_fence.py
======================
The CommonMark fence rule in pgai_agent_kanban/lib/markdown_fence.py, shared
by team/scripts/lint_doc_command_truth.py and
team/scripts/harness/lib/doc_step_extract.py: any run of three or more
backticks or tildes opens a block whatever its info string, and only a line
made of the same character, at least as long, closes it.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.lib.markdown_fence import (
    SHELL_FENCE_LANGS,
    find_fence_close,
    is_fence_closer,
    match_fence_opener,
)


@pytest.mark.parametrize(
    "line, expected",
    [
        ("```", ("```", "")),
        ("```bash", ("```", "bash")),
        ("```text", ("```", "text")),
        ("~~~python", ("~~~", "python")),
        ("````markdown", ("````", "markdown")),
        ("```Bash title=x", ("```", "bash")),
        ("``` sh", ("```", "sh")),
    ],
)
def test_any_info_string_opens_a_fence(line: str, expected: tuple[str, str]) -> None:
    assert match_fence_opener(line) == expected


@pytest.mark.parametrize("line", ["``", "~~", "echo ```", "```x```", "plain prose"])
def test_non_fence_lines_do_not_open_a_fence(line: str) -> None:
    assert match_fence_opener(line) is None


@pytest.mark.parametrize(
    "line, token, closes",
    [
        ("```", "```", True),
        ("````", "```", True),
        ("```  ", "```", True),
        ("```", "````", False),
        ("~~~", "```", False),
        ("```bash", "```", False),
        ("~~~~", "~~~", True),
    ],
)
def test_closer_is_same_character_at_least_as_long(line: str, token: str, closes: bool) -> None:
    assert is_fence_closer(line, token) is closes


def test_find_fence_close_skips_shorter_and_other_character_runs() -> None:
    lines = ["````text", "```", "~~~~", "````", "after"]
    assert find_fence_close(lines, 1, "````") == 3


def test_find_fence_close_returns_length_when_unterminated() -> None:
    lines = ["```text", "body", "more body"]
    assert find_fence_close(lines, 1, "```") == len(lines)


def test_find_fence_close_matches_indented_closer() -> None:
    lines = ["  ```bash", "  echo x", "  ```"]
    assert find_fence_close(lines, 1, "```") == 2


def test_shell_languages_are_bash_and_sh_only() -> None:
    assert SHELL_FENCE_LANGS == frozenset({"bash", "sh"})
