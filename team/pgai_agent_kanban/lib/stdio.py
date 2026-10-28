"""
stdio.py -- the one home for writing text to stdout or stderr and flushing.

A command-line verb that prints progress, a refusal or a question and then
keeps working (runs git, waits on a prompt, exits) flushes after each write,
so the line reaches the reader before whatever comes next -- an interleaved
child process's output or the interpreter's exit -- rather than sitting in a
buffer. Both writers write *text* exactly as given (no newline is added)
and flush the stream at once.

Callers that take an injectable writer (``out=`` / ``err=``) default to
these, so a test passes its own collector and the CLI gets the real streams.

Usage (import)::

    from pgai_agent_kanban.lib.stdio import write_stderr
    write_stderr("cm: VERB must be one of: cancel_rc\\n")
"""

from __future__ import annotations

import sys

__all__ = ["write_stderr", "write_stdout"]


def write_stdout(text: str) -> None:
    """Write *text* to ``sys.stdout`` as given and flush it."""
    sys.stdout.write(text)
    sys.stdout.flush()


def write_stderr(text: str) -> None:
    """Write *text* to ``sys.stderr`` as given and flush it."""
    sys.stderr.write(text)
    sys.stderr.flush()
