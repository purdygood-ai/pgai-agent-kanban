"""
test_doc_commands.py
====================
Block pairing in pgai_agent_kanban.lint.doc_commands.extract_blocks(), the
reader doc-lint.sh's cmd suite runs: every fence is paired with its own
closer by the CommonMark rule (pgai_agent_kanban/lib/markdown_fence.py)
before its info string is consulted, so no block -- tagged, indented, or a
longer outer fence wrapping an inner one -- hides the bash block after it.
An unlabelled block counts only when an annotation names it.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.lint.doc_commands import extract_blocks

_BASH_AFTER = "```bash\necho real-command\n```\n"


def _commands(blocks: list[dict]) -> list[str]:
    return [command["cmd"] for block in blocks for command in block["commands"]]


@pytest.mark.parametrize(
    ("preceding", "expected"),
    [
        ("```text\nexample output\n```\n", ["echo real-command"]),
        ("```ini\n[wake]\nzero_progress_cap = 3\n```\n", ["echo real-command"]),
        ("~~~python\nprint('hi')\n~~~\n", ["echo real-command"]),
        ("1. A list item:\n\n   ```bash\n   echo indented\n   ```\n", ["echo indented", "echo real-command"]),
        (
            "````markdown\nA nested example:\n\n```bash\necho nested\n```\n````\n",
            ["echo real-command"],
        ),
    ],
    ids=["text", "ini", "tilde-python", "indented-three-spaces", "four-backtick-outer"],
)
def test_bash_block_after_any_fence_is_returned(preceding: str, expected: list[str]) -> None:
    blocks = extract_blocks(f"# Guide\n\n## Step 1\n\n{preceding}\n{_BASH_AFTER}")
    assert _commands(blocks) == expected, (
        f"a block before the bash block changed which commands were read: {blocks!r}"
    )
    assert all(not block["block_skip"] and not block["docker_gate"] for block in blocks)


def test_indented_annotation_names_the_indented_fence_below_it() -> None:
    text = (
        "1. **Run the lane.**\n\n"
        "   <!-- doc-lint: skip needs the container tier -->\n"
        "   ```bash\n"
        "   # a comment line\n"
        "   scripts/cold-build-now.sh --image all\n"
        "   ```\n\n"
        + _BASH_AFTER
    )
    blocks = extract_blocks(text)
    assert blocks == [
        {"block_skip": True, "docker_gate": False,
         "commands": [{"cmd": "scripts/cold-build-now.sh --image all", "inline_skip": False}]},
        {"block_skip": False, "docker_gate": False,
         "commands": [{"cmd": "echo real-command", "inline_skip": False}]},
    ]


def test_unlabelled_block_counts_only_when_an_annotation_names_it() -> None:
    text = (
        "```\nplain output\n```\n\n"
        "<!-- doc-lint: skip needs a service -->\n\n```\nservice-cmd\n```\n\n"
        "<!-- doc-lint: docker -->\n```\ndocker ps\n```\n\n"
        + _BASH_AFTER
    )
    blocks = extract_blocks(text)
    assert [(block["block_skip"], block["docker_gate"], _commands([block])) for block in blocks] == [
        (True, False, ["service-cmd"]),
        (False, True, ["docker ps"]),
        (False, False, ["echo real-command"]),
    ]


def test_annotation_before_a_non_shell_block_is_spent_on_that_block() -> None:
    # The annotation names the fence on its next non-blank line; a non-shell
    # block is passed over even when annotated, and the annotation does not
    # carry on to the bash block that follows.
    text = "<!-- doc-lint: skip -->\n```text\nsample\n```\n\n" + _BASH_AFTER
    blocks = extract_blocks(text)
    assert blocks == [
        {"block_skip": False, "docker_gate": False,
         "commands": [{"cmd": "echo real-command", "inline_skip": False}]},
    ]


def test_shell_info_string_is_read_by_its_first_word_in_any_case() -> None:
    text = "```Bash title=setup\necho titled\n```\n\n``` sh\necho spaced\n```\n"
    assert _commands(extract_blocks(text)) == ["echo titled", "echo spaced"]


def test_unterminated_block_runs_to_the_end_of_the_document() -> None:
    text = "```text\nnever closed\n\n```bash\necho swallowed\n"
    assert extract_blocks(text) == []
