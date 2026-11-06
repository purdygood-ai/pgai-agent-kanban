"""
version_arg.py -- the one check a release-version argument passes.

A module that takes a release version from its caller (a CLI flag, an
import argument) and uses it to name a file, a record or an inbox source
checks it here first, so every such module accepts the same shape and
refuses with the same message:

  RELEASE_VERSION_RE      ``^v\\d+\\.\\d+\\.\\d+$``, the only shape accepted.
  RELEASE_VERSION_HELP    the ``--version`` help text: ``the RC, e.g. v1.2.3``.
  is_release_version(v)   True when *v*, stripped, has that shape.
  checked_release_version(v, error_class, error_key, remedy, consequence="")
                          *v* stripped, or raise ``error_class(error_key,
                          message, remedy)`` -- the caller's own
                          dual-channel refusal class and key.

Ordering versions is not this module's job; that is
:mod:`pgai_agent_kanban.lib.semver`.

Usage (import)::

    from pgai_agent_kanban.lib.version_arg import checked_release_version
    version = checked_release_version(
        args.version, MyModuleError, "my-module.bad-version",
        "python3 -m pgai_agent_kanban.cm.my_module --help",
        consequence="it cannot name a record",
    )
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = [
    "RELEASE_VERSION_HELP",
    "RELEASE_VERSION_RE",
    "checked_release_version",
    "is_release_version",
]

#: A release version, the only shape accepted.
RELEASE_VERSION_RE = re.compile(r"^v\d+\.\d+\.\d+$")

#: The help text of a ``--version`` option that takes a release version.
RELEASE_VERSION_HELP = "the RC, e.g. v1.2.3"


def is_release_version(value: object) -> bool:
    """True when *value*, stripped, is a release version ``vX.Y.Z``."""
    return bool(RELEASE_VERSION_RE.match(str(value or "").strip()))


def checked_release_version(
    value: object,
    error_class: Callable[[str, str, str], Exception],
    error_key: str,
    remedy: str,
    consequence: str = "",
) -> str:
    """*value* stripped, when it is a release version ``vX.Y.Z``.

    Otherwise raises ``error_class(error_key, message, remedy)``: the
    caller's own dual-channel refusal (a
    :class:`~pgai_agent_kanban.ops.errors.ContractError` subclass, or any
    class taking the same three fields). The message names the value and
    the expected shape; *consequence*, when given, is appended as what
    the bad value cannot do (``"so it cannot name a record"``).
    """
    text = str(value or "").strip()
    if RELEASE_VERSION_RE.match(text):
        return text
    tail = f", so {consequence}" if consequence else ""
    raise error_class(
        error_key,
        f"'{text}' is not a release version of the form vX.Y.Z (for example v1.2.3, as release-state.md's "
        f"Active RC field names it){tail}.",
        remedy,
    )
