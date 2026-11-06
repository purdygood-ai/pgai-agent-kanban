"""
dataclass_records.py — build a dataclass record from a plain dict.

The ledgers and evidence records read JSON written by an older or newer
release, so a key the record does not declare is ignored rather than
refused. One implementation serves every ``from_dict``.

Public API:
    from_known_fields(cls, data) -> instance of cls
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any
from collections.abc import Mapping

__all__ = ["from_known_fields"]


def from_known_fields[T](cls: type[T], data: Mapping[str, Any]) -> T:
    """Build dataclass *cls* from *data*, passing only the keys *cls* declares as fields."""
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in known})
