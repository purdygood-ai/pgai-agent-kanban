"""
test_model_mismatch_narration.py
=================================
Unit tests for the model-substitution self-detection added to the shared
wake finalize path (scripts/lib/wake_driver_finalize.sh).

An earlier defect let a subagent wrapper's frontmatter model pin silently
override the model kanban.cfg resolved for a role: status.md's ## Model
section recorded the configured/requested model while a different model
produced almost all of the task's output tokens, and nothing detected the
disagreement. Two functions on the finalize path now detect it:

  resolve_dominant_output_token_model <tokens_json_path>
      Prints the model name whose model_usage entry in tokens.json (the
      artifact token_capture.sh writes) recorded the greatest output_tokens
      total. Prints nothing and returns 1 when that cannot be determined.

  narrate_model_mismatch <task_status> <tokens_json_path> [<task_id>]
      Compares that observed model against the value stamp_model_field
      wrote into <task_status>'s ## Model section, and logs exactly one
      warning naming both on disagreement. Narration only: never mutates a
      file, never changes task state, always returns 0.

Acceptance criteria exercised (task README):
  - Agreeing case: no warning, exits as before.
  - Disagreeing case: one warning naming both values, still exits 0.
  - Absent/unparseable usage: no warning, no failure.

Tests invoke wake_driver_finalize.sh via subprocess (bash -c "source ... &&
..."), matching the pattern in test_token_capture_effort_field.py, so the
fixtures exercise the real production shell code rather than a
reimplementation of it. The faithful log() stub from
team/tests/fixtures/log_stub.py stands in for the production log() function
(wake_bootstrap.sh), which wake_driver_finalize.sh documents as a dependency
sourced by its caller before these functions run.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.fixtures.log_stub import LogStubCapture
from tests.unit.shell_harness import run_bash

# ---------------------------------------------------------------------------
# Paths (relative to team/, pytest's cwd when run via run-unit-tests.sh)
# ---------------------------------------------------------------------------
_FINALIZE_SH = pathlib.Path("scripts/lib/wake_driver_finalize.sh")


def _write_status(path: pathlib.Path, model_value: str | None) -> None:
    """Write a minimal status.md, with or without a ## Model section."""
    if model_value is None:
        path.write_text(
            "## Task\nT-1\n\n## State\nWORKING\n",
            encoding="utf-8",
        )
    else:
        path.write_text(
            f"## Task\nT-1\n\n## Model\n{model_value}\n\n## State\nWORKING\n",
            encoding="utf-8",
        )


def _write_tokens(path: pathlib.Path, content) -> None:
    if content is None:
        return
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content), encoding="utf-8")


# ---------------------------------------------------------------------------
# resolve_dominant_output_token_model — direct unit coverage
# ---------------------------------------------------------------------------


def test_dominant_model_ranks_by_output_tokens_not_total_volume(
    tmp_path: pathlib.Path,
) -> None:
    """The model with fewer total tokens (input+output) but more OUTPUT
    tokens must still win — this is what distinguishes the ranking from
    token_capture.sh's own canonical_model_id (input+output combined, with
    a same-name tiebreak toward the requested model) and is the whole point
    of the fix: the substitution the original defect uncovered is exactly a
    case where the requested model has low output and high cache-read
    input."""
    tokens = tmp_path / "tokens.json"
    _write_tokens(
        tokens,
        {
            "model_usage": {
                # Huge input volume (e.g. cache reads) but tiny output —
                # this is the dispatcher-turn / pinned model shape the
                # original defect described.
                "claude-fable-5-1": {
                    "input_tokens": 500000,
                    "output_tokens": 3000,
                },
                # Small input, large output — the model that did the work.
                "claude-sonnet-5": {
                    "input_tokens": 200,
                    "output_tokens": 90000,
                },
            }
        },
    )
    result = run_bash(
        tmp_path,
        f"source {_FINALIZE_SH!s} && resolve_dominant_output_token_model {str(tokens)!r}",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "claude-sonnet-5"


def test_dominant_model_ties_broken_deterministically(tmp_path: pathlib.Path) -> None:
    """Equal output_tokens across two models resolves to the alphabetically
    first key — deterministic, not iteration-order-dependent."""
    tokens = tmp_path / "tokens.json"
    _write_tokens(
        tokens,
        {
            "model_usage": {
                "claude-zeta-1": {"output_tokens": 500},
                "claude-alpha-1": {"output_tokens": 500},
            }
        },
    )
    result = run_bash(
        tmp_path,
        f"source {_FINALIZE_SH!s} && resolve_dominant_output_token_model {str(tokens)!r}",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "claude-alpha-1"


@pytest.mark.parametrize(
    "label,content",
    [
        ("missing_file", None),
        ("empty_file", ""),
        ("malformed_json", "{not valid json"),
        ("model_usage_absent", {"model": "claude-fable-5-1", "output_tokens": 10}),
        ("model_usage_null", {"model_usage": None}),
        ("model_usage_empty_dict", {"model_usage": {}}),
        (
            "captured_false_failure_record",
            {"captured": False, "reason": "no usage emitted", "model_usage": {}},
        ),
        (
            "all_zero_output_tokens",
            {
                "model_usage": {
                    "claude-a": {"output_tokens": 0},
                    "claude-b": {"output_tokens": 0},
                }
            },
        ),
    ],
)
def test_dominant_model_undeterminable_cases_return_1_and_print_nothing(
    tmp_path: pathlib.Path, label: str, content
) -> None:
    tokens = tmp_path / "tokens.json"
    _write_tokens(tokens, content)
    result = run_bash(
        tmp_path,
        f"source {_FINALIZE_SH!s} && resolve_dominant_output_token_model {str(tokens)!r}",
    )
    assert result.returncode == 1, f"{label}: expected rc=1, got {result.returncode}"
    assert result.stdout.strip() == "", f"{label}: expected empty stdout, got {result.stdout!r}"


# ---------------------------------------------------------------------------
# narrate_model_mismatch — the finalize-path comparison
# ---------------------------------------------------------------------------


def _run_narrate(
    tmp_path: pathlib.Path,
    status_path: pathlib.Path,
    tokens_path: pathlib.Path,
    task_id: str = "T-1",
) -> LogStubCapture:
    cap = LogStubCapture(tmp_path)
    with cap:
        cap.run_script(
            f"source {_FINALIZE_SH!s}\n"
            f"narrate_model_mismatch {str(status_path)!r} {str(tokens_path)!r} {task_id!r}\n"
            f"echo \"EXIT:$?\"\n"
        )
    return cap


def test_agreeing_case_emits_no_warning(tmp_path: pathlib.Path) -> None:
    """Stamped model equals the dominant output-token model: no warning,
    exit 0 — acceptance criterion 1."""
    status = tmp_path / "status.md"
    tokens = tmp_path / "tokens.json"
    _write_status(status, "claude-sonnet-5")
    _write_tokens(
        tokens,
        {"model_usage": {"claude-sonnet-5": {"output_tokens": 9000}}},
    )
    cap = _run_narrate(tmp_path, status, tokens)
    assert "EXIT:0" in cap.stdout
    assert "model mismatch" not in cap.log_content
    assert "model mismatch" not in cap.stdout


def test_disagreeing_case_warns_naming_both_models_and_exits_zero(
    tmp_path: pathlib.Path,
) -> None:
    """Stamped model differs from the dominant output-token model: exactly
    one warning naming both values, and the caller still sees exit 0 —
    acceptance criterion 2. This is the direct regression check for the
    original substitution scenario: stamp says fable, tokens.json shows
    sonnet did the work."""
    status = tmp_path / "status.md"
    tokens = tmp_path / "tokens.json"
    _write_status(status, "claude-fable-5-1")
    _write_tokens(
        tokens,
        {
            "model_usage": {
                "claude-fable-5-1": {"output_tokens": 3000},
                "claude-sonnet-5": {"output_tokens": 90000},
            }
        },
    )
    cap = _run_narrate(tmp_path, status, tokens)
    assert "EXIT:0" in cap.stdout, "narration must never change the caller's exit status"
    warning_lines = [
        line for line in cap.log_content.splitlines() if "model mismatch" in line
    ]
    assert len(warning_lines) == 1, f"expected exactly one warning; got: {cap.log_content!r}"
    assert "claude-fable-5-1" in warning_lines[0]
    assert "claude-sonnet-5" in warning_lines[0]


@pytest.mark.parametrize(
    "label,tokens_content",
    [
        ("missing_tokens_json", None),
        ("empty_tokens_json", ""),
        ("malformed_tokens_json", "{not valid json"),
        ("legacy_schema_no_model_usage", {"model": "claude-fable-5-1", "output_tokens": 5}),
        ("model_usage_null", {"model_usage": None}),
        ("model_usage_empty", {"model_usage": {}}),
        (
            "captured_false_failure_record",
            {"captured": False, "reason": "agent exited without usage output", "model_usage": {}},
        ),
    ],
)
def test_absent_or_unparseable_usage_is_silent_no_op(
    tmp_path: pathlib.Path, label: str, tokens_content
) -> None:
    """Acceptance criterion 3: missing, empty, or unparseable token usage —
    including the legacy schema and the captured:false failure record — is
    a silent no-op: no warning, no error, exit 0."""
    status = tmp_path / "status.md"
    tokens = tmp_path / "tokens.json"
    _write_status(status, "claude-fable-5-1")
    _write_tokens(tokens, tokens_content)
    cap = _run_narrate(tmp_path, status, tokens)
    assert "EXIT:0" in cap.stdout, f"{label}: must exit 0 even when usage is undeterminable"
    assert "model mismatch" not in cap.log_content, f"{label}: unexpected warning"
    assert "WARNING" not in cap.log_content, f"{label}: unexpected warning of any kind"


def test_missing_status_file_is_silent_no_op(tmp_path: pathlib.Path) -> None:
    """A task_status path that does not exist is a silent no-op, not a
    crash — narration must degrade at least as gracefully as the rest of
    the finalize path around it."""
    status = tmp_path / "does-not-exist-status.md"
    tokens = tmp_path / "tokens.json"
    _write_tokens(tokens, {"model_usage": {"claude-sonnet-5": {"output_tokens": 9000}}})
    cap = _run_narrate(tmp_path, status, tokens)
    assert "EXIT:0" in cap.stdout
    assert "model mismatch" not in cap.log_content


def test_status_file_without_model_section_is_silent_no_op(
    tmp_path: pathlib.Path,
) -> None:
    """A status.md carrying no ## Model section (or an empty one) has
    nothing to compare against — silent no-op rather than a warning
    naming an empty stamped value."""
    status = tmp_path / "status.md"
    tokens = tmp_path / "tokens.json"
    _write_status(status, None)  # no ## Model section at all
    _write_tokens(tokens, {"model_usage": {"claude-sonnet-5": {"output_tokens": 9000}}})
    cap = _run_narrate(tmp_path, status, tokens)
    assert "EXIT:0" in cap.stdout
    assert "model mismatch" not in cap.log_content


def test_never_mutates_status_file(tmp_path: pathlib.Path) -> None:
    """Narration must never write to the status file — constraint: 'Do not
    change what the model stamp writes.'"""
    status = tmp_path / "status.md"
    tokens = tmp_path / "tokens.json"
    _write_status(status, "claude-fable-5-1")
    _write_tokens(
        tokens,
        {
            "model_usage": {
                "claude-fable-5-1": {"output_tokens": 100},
                "claude-sonnet-5": {"output_tokens": 90000},
            }
        },
    )
    before = status.read_text(encoding="utf-8")
    _run_narrate(tmp_path, status, tokens)
    after = status.read_text(encoding="utf-8")
    assert before == after, "narrate_model_mismatch must never mutate task_status"
