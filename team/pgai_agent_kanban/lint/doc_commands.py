#!/usr/bin/env python3
"""
doc_commands.py — the fenced bash/sh commands of a Markdown document, as
team/scripts/harness/doc-lint.sh's command suite runs them.

Fences are recognised and paired by CommonMark's rule, through the shared
helper pgai_agent_kanban/lib/markdown_fence.py: ANY run of three or more
backticks or tildes opens a fenced block whatever its info string
(``text``, ``ini``, ``python``, ``bash title=x``, none), and the block ends
at its own closer -- a line of the same character, at least as long -- or
at the end of the document. Lines are matched with surrounding whitespace
stripped, so an indented fence or annotation (one inside a list item, say)
counts the same as a flush-left one. Only once a block is paired is its
info string's first word consulted, so a non-shell block never hides the
block after it.

A paired block is a command block when that first word is ``bash`` or
``sh`` (any case), or when it has no info string and an annotation names
it; every other block is passed over. The annotation sits on the line
before the fence (blank lines between are allowed):

  ``<!-- doc-lint: skip ... -->``   block_skip: report every command skipped
  ``<!-- doc-lint: docker -->``     docker_gate: skip every command when no
                                    container engine answers

Within a block, backslash-continued lines join into one command, and blank
and ``#`` comment lines are dropped. A command ending in
``# doc-lint: skip`` is kept with inline_skip set and that annotation removed
from its text.

Usage:
    python3 -m pgai_agent_kanban.lint.doc_commands FILE

prints one line per command, in document order:

    <block>\\t<block_skip>\\t<docker_gate>\\t<inline_skip>\\t<command>

``<block>`` numbers the blocks from 1; the three flags are ``true`` or
``false``. A block with annotations but no command prints nothing. An
unreadable FILE prints ``doc-lint.sh: cannot read FILE: <reason>`` on stderr
and exits 1. ``--help`` prints this text and exits 0.

Public API:
    extract_blocks(text) -> list[dict]   {"block_skip", "docker_gate", "commands": [{"cmd", "inline_skip"}]}
    command_lines(blocks) -> list[str]
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Optional

# Run by file path (``python3 .../doc_commands.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.markdown_fence import SHELL_FENCE_LANGS, find_fence_close, match_fence_opener  # noqa: E402

__all__ = ["command_lines", "extract_blocks"]

_SKIP_ANNOTATION_RE = re.compile(r'<!--\s*doc-lint:\s*skip', re.IGNORECASE)
_DOCKER_ANNOTATION_RE = re.compile(r'<!--\s*doc-lint:\s*docker\s*-->', re.IGNORECASE)
_INLINE_SKIP_RE = re.compile(r'#\s*doc-lint:\s*skip')
_INLINE_SKIP_TAIL_RE = re.compile(r'\s*#\s*doc-lint:\s*skip.*$')


def _join_continuations(raw_lines: list[str]) -> list[str]:
    """Join backslash-continuation lines into logical commands."""
    joined = []
    buf = ""
    for line in raw_lines:
        if line.endswith("\\"):
            buf += line[:-1] + " "
        else:
            buf += line
            joined.append(buf.strip())
            buf = ""
    if buf.strip():
        joined.append(buf.strip())
    return [line for line in joined if line]


def extract_blocks(text: str) -> list[dict]:
    """Return the command blocks of the Markdown *text*; see the module docstring."""
    lines = text.splitlines()
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        block_skip = bool(_SKIP_ANNOTATION_RE.match(line))
        docker_gate = not block_skip and bool(_DOCKER_ANNOTATION_RE.match(line))

        fence_open = None
        if block_skip or docker_gate:
            # The fence must be the next non-blank line.
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                fence_open = match_fence_opener(lines[j].strip())
                if fence_open is not None:
                    i = j
        else:
            fence_open = match_fence_opener(line)
        if fence_open is None:
            i += 1
            continue

        # Every fenced block is paired with its own closer before its info
        # string is consulted, so a block this reader passes over still ends
        # where CommonMark ends it.
        fence_token, lang = fence_open
        body_start = i + 1
        close_idx = find_fence_close(lines, body_start, fence_token)
        i = close_idx + 1  # resume after the closer (past the end when unterminated)

        # A shell block always counts; an unlabelled block counts only when an
        # annotation names it; any other info string never counts.
        annotated = block_skip or docker_gate
        if lang not in SHELL_FENCE_LANGS and not (lang == "" and annotated):
            continue
        raw_lines = lines[body_start:close_idx]

        commands = []
        for raw in _join_continuations(raw_lines):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cmd_text = _INLINE_SKIP_TAIL_RE.sub('', stripped).strip()
            if cmd_text:
                commands.append({"cmd": cmd_text, "inline_skip": bool(_INLINE_SKIP_RE.search(stripped))})

        if commands or block_skip or docker_gate:
            blocks.append({"block_skip": block_skip, "docker_gate": docker_gate, "commands": commands})
    return blocks


def _flag(value: bool) -> str:
    return "true" if value else "false"


def command_lines(blocks: list[dict]) -> list[str]:
    """One tab-separated line per command of *blocks*, as the command line prints them."""
    return [
        f"{number}\t{_flag(block['block_skip'])}\t{_flag(block['docker_gate'])}\t"
        f"{_flag(command['inline_skip'])}\t{command['cmd']}"
        for number, block in enumerate(blocks, 1)
        for command in block["commands"]
    ]


def main(argv: Optional[list] = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if len(args) != 1:
        print("usage: python3 -m pgai_agent_kanban.lint.doc_commands FILE", file=sys.stderr)
        return 2
    try:
        with open(args[0], encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"doc-lint.sh: cannot read {args[0]}: {exc}", file=sys.stderr)
        return 1
    for line in command_lines(extract_blocks(text)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
