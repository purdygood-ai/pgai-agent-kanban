"""
pgai_agent_kanban.lib.metrics.common — pieces every metrics and cost
surface shares: the text-result shape, the colour decision, the JSON
roll-up reader and the project registry walk.

TextResult is what a text surface returns instead of printing: the exit
code, stdout and stderr a CLI run would have produced. The CLI entry point
(``python3 -m pgai_agent_kanban.lib.metrics``) writes the two streams and
exits with the code; a REST route puts the same three values in its
envelope. One function, two renderers of its result.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
from typing import Any, Mapping, Optional

from .. import ini
from ..palette import ansi_code

__all__ = [
    "TextResult",
    "TextOutput",
    "Colors",
    "color_enabled",
    "colors_for",
    "load_json_object",
    "registered_projects",
]


@dataclasses.dataclass(frozen=True)
class TextResult:
    """A rendered text surface: exit code, stdout and stderr."""

    exit_code: int
    stdout: str
    stderr: str = ""


class TextOutput:
    """The stdout and stderr lines one text run produces, in order."""

    def __init__(self) -> None:
        self.out: list[str] = []
        self.err: list[str] = []

    def result(self, exit_code: int) -> TextResult:
        """The run's TextResult: every line newline-terminated."""
        return TextResult(
            exit_code,
            "".join(f"{line}\n" for line in self.out),
            "".join(f"{line}\n" for line in self.err),
        )


@dataclasses.dataclass(frozen=True)
class Colors:
    """The ANSI escapes the metrics renderers use; every field is "" when colour is off."""

    reset: str = ""
    bold: str = ""
    dim: str = ""
    cyan: str = ""
    green: str = ""
    yellow: str = ""


def color_enabled(no_color_flag: bool, environ: Optional[Mapping[str, str]] = None) -> bool:
    """Decide colour the way the dashboard scripts do.

    Colour is off when ``--no-color`` was given, when ``NO_COLOR`` is set
    non-empty, or when ``TERM`` is ``dumb``. An unset ``TERM`` counts as
    ``dumb``: bash assigns ``TERM=dumb`` when its environment carries none,
    and the scripts these renderers came from read it after that
    assignment.
    """
    env = os.environ if environ is None else environ
    if no_color_flag or env.get("NO_COLOR", ""):
        return False
    return env.get("TERM", "dumb") != "dumb"


def colors_for(use_color: bool) -> Colors:
    """The escapes for *use_color*, from the shared palette table."""
    return Colors(
        reset=ansi_code("reset", use_color),
        bold=ansi_code("bold", use_color),
        dim=ansi_code("dim", use_color),
        cyan=ansi_code("cyan", use_color),
        green=ansi_code("green", use_color),
        yellow=ansi_code("yellow", use_color),
    )


def load_json_object(path: pathlib.Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Read *path* as a JSON object.

    Returns ``(data, None)`` on success and ``(None, reason)`` when the
    file is missing, unreadable, not JSON, or not a JSON object -- the
    caller decides whether the reason is worth a warning.
    """
    if not path.is_file():
        return None, "not a file"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def registered_projects(kanban_root: pathlib.Path) -> list[str]:
    """Project names from ``projects.cfg`` in priority order (registration order breaks ties).

    An absent or unparseable ``projects.cfg`` yields no names, as the
    dashboard's registry walk does.
    """
    try:
        return [entry.name for entry in ini.projects_registry(kanban_root)]
    except (ini.IniError, UnicodeDecodeError, OSError):
        return []
