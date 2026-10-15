"""
test_api_validation.py
======================
Behavioural tests for pgai_agent_kanban.api.validation: has_traversal, the
traversal check, and require_component, the single-name check the operator
API applies to every project, key and selector field before a route acts on
it. Also ComponentRefused.block, the refusal as the shared emitter's
three-line block that the ops layer raises, and control_character_fault,
the one-line check for a value written into a config file.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.api.validation import (
    ComponentRefused,
    control_character_fault,
    has_traversal,
    require_component,
)
from pgai_agent_kanban.lib.error_contract import parse_error_contract


@pytest.mark.parametrize(
    "value",
    [
        "..",
        "../etc",
        "pgai-agent-kanban/../../secrets",
        "a..b",
    ],
)
def test_parent_directory_segments_are_rejected(value: str) -> None:
    assert has_traversal(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "/home/user/pgai_agent_kanban",
        "\\windows\\system32",
        "C:\\kanban",
    ],
)
def test_absolute_paths_are_rejected(value: str) -> None:
    assert has_traversal(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "%2e%2e",
        "%2E%2E%2Fetc",
        "proj%2fother",
        "proj%5Cother",
        "proj%00",
        "proj\x00",
    ],
)
def test_encoded_separators_dots_and_nul_are_rejected(value: str) -> None:
    assert has_traversal(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "pgai-agent-kanban",
        "my-app",
        "coder",
        "CODER-20260101-001-example",
        "v2.28.0",
        "",
    ],
)
def test_single_path_components_are_accepted(value: str) -> None:
    assert has_traversal(value) is False


# ---------------------------------------------------------------------------
# require_component -- the single-name check every project, key and selector
# request field passes before a route acts on it.
# ---------------------------------------------------------------------------

# One refused value per class require_component names, with the reason
# fragment the refusal must carry so the caller can see what was wrong.
_REFUSED_COMPONENTS = [
    pytest.param("", "is empty", id="empty"),
    pytest.param("/", "path separator", id="slash"),
    pytest.param("/etc/passwd", "path separator", id="absolute-path"),
    pytest.param("proj/other", "path separator", id="embedded-slash"),
    pytest.param("\\", "path separator", id="backslash"),
    pytest.param("..", "parent directory", id="dotdot"),
    pytest.param("a..b", "parent directory", id="embedded-dotdot"),
    pytest.param(".", "current or a parent directory", id="lone-dot"),
    pytest.param("proj\x00", "NUL", id="nul"),
    pytest.param("rc\nrc:v9.9.9", "control character (U+000A)", id="newline"),
    pytest.param("rc\r", "control character (U+000D)", id="carriage-return"),
    pytest.param("a\tb", "control character (U+0009)", id="tab"),
    pytest.param("a\x7fb", "control character (U+007F)", id="del"),
    pytest.param("a\u2028b", "control character (U+2028)", id="line-separator"),
    pytest.param("*", "glob character '*'", id="star"),
    pytest.param("CODER-*", "glob character '*'", id="trailing-star"),
    pytest.param("CODER-?", "glob character '?'", id="question-mark"),
    pytest.param("[a]", "glob character '['", id="open-bracket"),
    pytest.param("a]", "glob character ']'", id="close-bracket"),
    pytest.param("-x", "starts with '-'", id="leading-dash"),
    pytest.param("--operator-approved", "starts with '-'", id="flag-shaped"),
    pytest.param("%2e%2e", "URL-encoded", id="encoded-dots"),
    pytest.param("proj%2Fother", "URL-encoded", id="encoded-slash"),
]


@pytest.mark.parametrize("value, reason", _REFUSED_COMPONENTS)
def test_require_component_refuses_each_class(value: str, reason: str) -> None:
    with pytest.raises(ComponentRefused) as caught:
        require_component(value, "key")
    assert caught.value.field == "key"
    assert reason in caught.value.reason, (
        f"refusal of {value!r} should say {reason!r}; said {caught.value.reason!r}"
    )


@pytest.mark.parametrize(
    "value",
    [
        "pgai-agent-kanban",
        "my-app",
        "my_app.v2",
        "proj",
        "CODER-20260101-001-example",
        "TESTER-20260101-002-verify-1-2-3",
        "CODER-2026",
        "BUG-0001-example",
        "PRIORITY-0007",
        "v2.37.7",
        "2.37.7",
        "rc",
        "rc:v2.37.7",
        "coder",
        "repo-wake-my-app",
    ],
)
def test_require_component_accepts_plain_names(value: str) -> None:
    assert require_component(value, "project") == value


@pytest.mark.parametrize("value", [None, 7, ["proj"]])
def test_require_component_refuses_non_strings(value: object) -> None:
    with pytest.raises(ComponentRefused) as caught:
        require_component(value, "project")
    assert "not a string" in caught.value.reason


def test_component_refusal_contract_names_field_and_verb() -> None:
    with pytest.raises(ComponentRefused) as caught:
        require_component("..", "project")
    triple = caught.value.contract("remove-project", "curl -sS http://127.0.0.1:8300/x")
    assert set(triple) == {"error", "message", "remedy"}
    assert triple["error"] == "remove_project.project_invalid"
    assert "'project'" in triple["message"]
    assert "remove-project" in triple["message"]
    assert triple["remedy"] == "curl -sS http://127.0.0.1:8300/x"


def test_component_refusal_contract_rejects_a_blank_remedy() -> None:
    with pytest.raises(ComponentRefused) as caught:
        require_component("*", "key")
    with pytest.raises(ValueError):
        caught.value.contract("close", "   ")


def test_require_component_refuses_everything_has_traversal_refuses() -> None:
    for value in ("..", "../etc", "/etc/passwd", "C:\\kanban", "%2e%2e", "proj%5Cother", "proj%00"):
        assert has_traversal(value) is True
        with pytest.raises(ComponentRefused):
            require_component(value, "project")


def test_component_refusal_block_is_the_emitters_three_line_block() -> None:
    with pytest.raises(ComponentRefused) as caught:
        require_component("../x", "key")
    block = caught.value.block("resolve_item", "ls /srv/kanban/projects/proj/tasks")
    triple = parse_error_contract(block)
    assert triple is not None
    assert triple["error"] == "resolve_item.key_invalid"
    assert "'key'" in triple["message"]
    assert triple["remedy"] == "ls /srv/kanban/projects/proj/tasks"
    assert caught.value.contract("resolve_item", "ls /srv/kanban/projects/proj/tasks") == triple


@pytest.mark.parametrize(
    "value, code",
    [("a\nb", "U+000A"), ("a\rb", "U+000D"), ("a\tb", "U+0009"), ("\x00", "U+0000"),
     ("a\x7fb", "U+007F"), ("a\x85b", "U+0085"), ("a\u2028b", "U+2028"), ("a\u2029b", "U+2029")],
)
def test_control_character_fault_names_the_first_control_character(value: str, code: str) -> None:
    fault = control_character_fault(value)
    assert fault is not None and code in fault
    assert value not in fault


@pytest.mark.parametrize(
    "value",
    ["/srv/develop/my-app", "git@example.com:org/app.git", "https://example.com/a b", "origin", ""],
)
def test_control_character_fault_passes_a_one_line_value_with_separators(value: str) -> None:
    assert control_character_fault(value) is None
