"""
test_lib_version_arg.py
=======================
Behavioural tests for pgai_agent_kanban.lib.version_arg: which values pass
as a release version, and that a refusal is raised through the caller's
own error class and key with a message naming the value and no markdown
heading.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.lib.version_arg import (
    RELEASE_VERSION_HELP,
    checked_release_version,
    is_release_version,
)
from pgai_agent_kanban.ops.errors import ContractError


class _ExampleError(ContractError):
    """A caller's own refusal class."""


_REMEDY = "python3 -m pgai_agent_kanban.cm.example --help"


@pytest.mark.parametrize("value", ["v1.2.3", "v0.0.0", "v10.20.300", "  v1.2.3\n"])
def test_a_release_version_is_returned_stripped(value: str) -> None:
    assert checked_release_version(value, _ExampleError, "example.bad-version", _REMEDY) == value.strip()
    assert is_release_version(value) is True


@pytest.mark.parametrize("value", ["", None, "1.2.3", "v1.2", "V1.2.3", "ai_v1.2.3", "v1.2.3-rc1", "v1.2.3 x"])
def test_anything_else_is_refused_through_the_callers_class_and_key(value) -> None:
    assert is_release_version(value) is False
    with pytest.raises(_ExampleError) as caught:
        checked_release_version(value, _ExampleError, "example.bad-version", _REMEDY)
    exc = caught.value
    assert exc.error == "example.bad-version"
    assert exc.remedy == _REMEDY
    assert f"'{str(value or '').strip()}' is not a release version of the form vX.Y.Z" in exc.message


def test_the_message_names_the_field_without_a_markdown_heading() -> None:
    with pytest.raises(_ExampleError) as caught:
        checked_release_version("nope", _ExampleError, "example.bad-version", _REMEDY)
    message = caught.value.message
    assert "Active RC field" in message
    assert "##" not in message
    assert message.endswith(").")


def test_a_consequence_is_appended_to_the_message() -> None:
    with pytest.raises(_ExampleError) as caught:
        checked_release_version(
            "nope", _ExampleError, "example.bad-version", _REMEDY, consequence="it cannot name a record",
        )
    assert caught.value.message.endswith("names it), so it cannot name a record.")


def test_the_help_text_shows_an_example_version() -> None:
    assert is_release_version(RELEASE_VERSION_HELP.rsplit(" ", 1)[-1])
