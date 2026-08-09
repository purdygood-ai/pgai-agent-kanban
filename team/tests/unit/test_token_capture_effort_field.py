"""
test_token_capture_effort_field.py
===================================
Unit tests for the reasoning-effort field on the per-task cost artifact,
written by scripts/lib/token_capture.sh's token_capture_merge_into_task_
artifacts (and its jq/python3 merge backends) to <task_artifact_dir>/
tokens.json.

token_capture_merge_into_task_artifacts takes an optional 8th positional
argument, effort -- the reasoning-effort value in force for the invocation
(e.g. "high"), as resolved by config_loader.sh's resolve_role_effort(). The
contract under test:

  1. When effort is supplied, tokens.json carries a top-level "effort" field
     with that value (new schema, legacy schema, and the captured:false
     failure-record shape alike).
  2. When effort is never supplied across any invocation for a task,
     tokens.json carries no "effort" key at all -- byte-identical to the
     pre-effort schema.
  3. Merging a second invocation that omits effort preserves whatever
     effort value a prior invocation in the same task recorded.
  4. Merging a second invocation that supplies a new effort value
     overwrites the previously recorded value.

Both the jq merge path and the python3 fallback merge path are exercised,
since token_capture.sh dispatches to whichever is available and the two
implementations must agree.

Tests invoke token_capture.sh via subprocess (bash -c "source ... && ..."),
matching the pattern in test_config_loader_effort_resolution.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_TOKEN_CAPTURE = _TEAM_DIR / "scripts" / "lib" / "token_capture.sh"

# A representative new-schema usage record (total_cost_usd + model_usage present).
_NEW_SCHEMA_USAGE = json.dumps(
    {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "canonical_model_id": "claude-sonnet-4-6",
        "total_cost_usd": 0.01,
        "model_usage": {
            "claude-sonnet-4-6": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.01,
            }
        },
    }
)

# A representative legacy-schema usage record (total_cost_usd/model_usage absent).
_LEGACY_SCHEMA_USAGE = json.dumps(
    {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "canonical_model_id": "",
        "total_cost_usd": None,
        "model_usage": None,
    }
)

# The captured:false sentinel token_capture_parse_result emits on parse failure.
_FAILURE_USAGE = json.dumps(
    {"captured": False, "reason": "agent exited without usage output"}
)


def _merge(
    tmp_path: pathlib.Path,
    usage_json: str,
    *,
    force_python_fallback: bool = False,
    model: str = "claude-sonnet-4-6",
    provider: str = "claude",
    agent: str = "coder",
    elapsed_seconds: int = 30,
    rc_version: str = "v2.1.0",
    effort: str | None = None,
) -> dict:
    """Invoke token_capture_merge_into_task_artifacts once and return the
    resulting tokens.json content, parsed.

    When force_python_fallback is True, a `command` shell function shadows
    `command -v jq` so token_capture.sh takes the python3 merge path even on
    a host where jq is installed -- this lets a single test body exercise
    both backends without relying on the test runner's jq availability.
    """
    artifact_dir = tmp_path / "artifacts"
    effort_arg = f" {effort!r}" if effort is not None else ""
    fallback_shim = (
        'command() { if [[ "$1" == "-v" && "$2" == "jq" ]]; then return 1; '
        'fi; builtin command "$@"; }\n'
        if force_python_fallback
        else ""
    )
    script = (
        f"source {str(_TOKEN_CAPTURE)!r}\n"
        f"{fallback_shim}"
        f"token_capture_merge_into_task_artifacts {usage_json!r} {str(artifact_dir)!r} "
        f"{model!r} {provider!r} {agent!r} {elapsed_seconds} {rc_version!r}{effort_arg}\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"token_capture_merge_into_task_artifacts exited non-zero: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    tokens_path = artifact_dir / "tokens.json"
    assert tokens_path.is_file(), f"tokens.json was not written to {tokens_path}"
    return json.loads(tokens_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# (1) Absent effort -> no effort key, for every usage-record shape, on
#     both the jq and python3 merge backends.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage_json", [_NEW_SCHEMA_USAGE, _LEGACY_SCHEMA_USAGE, _FAILURE_USAGE]
)
@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_absent_effort_omits_effort_key_from_tokens_json(
    tmp_path: pathlib.Path, usage_json: str, force_python_fallback: bool
) -> None:
    """When no caller ever supplies an effort value, tokens.json carries no
    'effort' key at all -- this is what keeps the artifact byte-identical
    to pre-effort output."""
    record = _merge(tmp_path, usage_json, force_python_fallback=force_python_fallback)
    assert "effort" not in record, f"expected no effort key; got {record!r}"


# ---------------------------------------------------------------------------
# (2) Supplied effort -> effort key with the supplied value, for every
#     usage-record shape, on both merge backends.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage_json", [_NEW_SCHEMA_USAGE, _LEGACY_SCHEMA_USAGE, _FAILURE_USAGE]
)
@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_supplied_effort_is_recorded_on_tokens_json(
    tmp_path: pathlib.Path, usage_json: str, force_python_fallback: bool
) -> None:
    """When effort is supplied, the per-task tokens.json usage record
    carries an effort field with that value."""
    record = _merge(
        tmp_path,
        usage_json,
        force_python_fallback=force_python_fallback,
        effort="high",
    )
    assert record.get("effort") == "high", f"expected effort='high'; got {record!r}"


# ---------------------------------------------------------------------------
# (3) Merge of a second invocation without effort preserves a prior
#     invocation's effort value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_merge_preserves_effort_across_invocations_that_omit_it(
    tmp_path: pathlib.Path, force_python_fallback: bool
) -> None:
    """A second invocation for the same task that does not pass an effort
    argument preserves whatever effort value the first invocation recorded,
    rather than clearing it."""
    _merge(
        tmp_path,
        _NEW_SCHEMA_USAGE,
        force_python_fallback=force_python_fallback,
        effort="high",
    )
    record = _merge(
        tmp_path, _NEW_SCHEMA_USAGE, force_python_fallback=force_python_fallback
    )
    assert record.get("effort") == "high", (
        f"expected effort='high' preserved from the prior invocation; "
        f"got {record!r}"
    )
    assert record["invocations"] == 2


# ---------------------------------------------------------------------------
# (4) Merge of a second invocation with a new effort value overwrites the
#     previously recorded value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_merge_overwrites_effort_when_a_new_value_is_supplied(
    tmp_path: pathlib.Path, force_python_fallback: bool
) -> None:
    """A second invocation supplying a different effort value overwrites
    the one recorded by the first invocation."""
    _merge(
        tmp_path,
        _NEW_SCHEMA_USAGE,
        force_python_fallback=force_python_fallback,
        effort="high",
    )
    record = _merge(
        tmp_path,
        _NEW_SCHEMA_USAGE,
        force_python_fallback=force_python_fallback,
        effort="low",
    )
    assert record.get("effort") == "low", f"expected effort='low'; got {record!r}"


# ---------------------------------------------------------------------------
# (5) Legacy-schema records keep every pre-existing field untouched when
#     effort is added -- the new field is additive, not a schema rewrite.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force_python_fallback", [False, True])
def test_effort_field_is_additive_on_legacy_schema(
    tmp_path: pathlib.Path, force_python_fallback: bool
) -> None:
    """Adding effort to a legacy-schema tokens.json record does not disturb
    any of the pre-existing legacy fields."""
    record = _merge(
        tmp_path,
        _LEGACY_SCHEMA_USAGE,
        force_python_fallback=force_python_fallback,
        model="claude-sonnet-4-6",
        provider="claude",
        agent="writer",
        effort="medium",
    )
    for field in (
        "model",
        "provider",
        "agent",
        "rc_version",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "invocations",
        "elapsed_seconds",
        "timestamp",
    ):
        assert field in record, f"legacy field {field!r} missing from {record!r}"
    assert record["effort"] == "medium"
