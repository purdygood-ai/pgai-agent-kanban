"""
test_token_capture_modules.py
=============================
The wake's token-usage capture in Python: ``parse_claude_json`` in
``pgai_agent_kanban.lib.provider_usage`` and the tokens.json write and
dominant-model read in ``pgai_agent_kanban.lib.token_capture``.

Covered behaviour:

  * the claude usage parse writes the agent text and returns the usage
    record; empties the result file when the response is unreadable;
    keeps the raw text and refuses when no line is JSON; falls back to
    the last JSON line; prefers the requested model as canonical_model_id
    only when the response used it, otherwise the model with the most
    input+output tokens;
  * the tokens.json write creates a new file, merges into an existing one
    (per-model and flat schemas), writes an empty RC version as ``none``,
    reads a non-digit elapsed time as 0, writes the failure record, and
    carries effort forward;
  * the dominant-model read names the single model, ranks several by
    output tokens alone with ties to the alphabetically first, and
    returns nothing for a missing, invalid or non-object file or an
    absent model_usage map;
  * each command-line verb's stdout and exit status;
  * token_capture.sh, sourced, reaches the parse and the write through
    pp_run_ops (the write on the no-jq fallback path).

Each function is exercised in-process; the command line and the bash
wiring run as subprocesses with an environment built from an explicit set
and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from pgai_agent_kanban.lib import provider_usage, token_capture
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_TOKEN_CAPTURE_SH = _TEAM_DIR / "scripts" / "lib" / "token_capture.sh"
_MODULE = "pgai_agent_kanban.lib.token_capture"
_NOW = "2026-01-02T03:04:05Z"

_CLAUDE_RESPONSE = {
    "type": "result",
    "result": "the agent's answer",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 4,
    },
    "total_cost_usd": 0.5,
    "modelUsage": {
        "claude-opus-4-8": {"inputTokens": 5, "outputTokens": 6, "costUSD": 0.4},
        "claude-haiku-4-5-20251001": {
            "inputTokens": 100,
            "outputTokens": 1,
            "cacheReadInputTokens": 7,
            "costUSD": 0.1,
        },
    },
}

_PER_MODEL_USAGE = {
    "input_tokens": 1,
    "output_tokens": 2,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "canonical_model_id": "m1",
    "total_cost_usd": 0.25,
    "model_usage": {
        "m1": {
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_usd": 0.25,
        }
    },
}

_FLAT_USAGE = {
    "input_tokens": 7,
    "output_tokens": 3,
    "cache_creation_input_tokens": 1,
    "cache_read_input_tokens": 2,
    "canonical_model_id": "",
    "total_cost_usd": None,
    "model_usage": None,
}


def _write_json(path: pathlib.Path, value: object) -> pathlib.Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _env(tmp_path: pathlib.Path, **extra: str) -> dict:
    return build_scrubbed_env(home_dir=tmp_path / "home", extra_env=extra)


def _run_cli(tmp_path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path, PYTHONPATH=str(_TEAM_DIR)),
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


def _write(tmp_path: pathlib.Path, usage: object, **kwargs) -> dict:
    """Write *usage* into tmp_path/tokens.json and return the file, parsed."""
    tokens = tmp_path / "tokens.json"
    args = {
        "model": "mod",
        "provider": "claude",
        "agent": "coder",
        "elapsed_seconds": 30,
        "rc_version": "v1.2.3",
        "effort": "",
        "now": _NOW,
    }
    args.update(kwargs)
    token_capture.write_tokens_record(
        usage,
        tokens,
        args["model"],
        args["provider"],
        args["agent"],
        args["elapsed_seconds"],
        args["rc_version"],
        args["effort"],
        now=args["now"],
    )
    return json.loads(tokens.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# (a) the claude JSON usage parse
# ---------------------------------------------------------------------------


def test_claude_parse_writes_agent_text_and_returns_the_usage_record(tmp_path: pathlib.Path) -> None:
    response = _write_json(tmp_path / "raw.json", _CLAUDE_RESPONSE)
    result = tmp_path / "result.txt"

    record = provider_usage.parse_claude_json(response, result, "claude-opus-4-8")

    assert result.read_text(encoding="utf-8") == "the agent's answer"
    assert list(record) == [
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "canonical_model_id",
        "total_cost_usd",
        "model_usage",
    ]
    assert record["input_tokens"] == 10
    assert record["output_tokens"] == 20
    assert record["cache_creation_input_tokens"] == 3
    assert record["cache_read_input_tokens"] == 4
    assert record["canonical_model_id"] == "claude-opus-4-8"
    assert record["total_cost_usd"] == 0.5
    assert record["model_usage"]["claude-haiku-4-5-20251001"] == {
        "input_tokens": 100,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 7,
        "cost_usd": 0.1,
    }


def test_claude_parse_requested_model_absent_from_the_response_picks_the_busiest(
    tmp_path: pathlib.Path,
) -> None:
    response = _write_json(tmp_path / "raw.json", _CLAUDE_RESPONSE)

    record = provider_usage.parse_claude_json(response, tmp_path / "result.txt", "claude-sonnet-4-6")

    # haiku: 100 + 1 input+output tokens; opus: 5 + 6.
    assert record["canonical_model_id"] == "claude-haiku-4-5-20251001"


def test_claude_parse_without_model_usage_leaves_cost_and_breakdown_null(tmp_path: pathlib.Path) -> None:
    response = _write_json(tmp_path / "raw.json", {"result": "x", "usage": {"input_tokens": 9}})

    record = provider_usage.parse_claude_json(response, tmp_path / "result.txt", "claude-opus-4-8")

    assert record["canonical_model_id"] == ""
    assert record["total_cost_usd"] is None
    assert record["model_usage"] is None
    assert record["input_tokens"] == 9 and record["output_tokens"] == 0


def test_claude_parse_joins_text_content_blocks_from_the_last_json_line(tmp_path: pathlib.Path) -> None:
    blocks = {
        "result": [{"type": "text", "text": "a"}, {"type": "tool_use", "text": "x"}, {"type": "text", "text": "b"}],
        "usage": {},
    }
    response = tmp_path / "raw.json"
    response.write_text("not json\n" + json.dumps(blocks) + "\n", encoding="utf-8")
    result = tmp_path / "result.txt"

    provider_usage.parse_claude_json(response, result)

    assert result.read_text(encoding="utf-8") == "a\nb"


def test_claude_parse_unreadable_response_empties_the_result_file(tmp_path: pathlib.Path) -> None:
    unreadable = tmp_path / "a-directory"
    unreadable.mkdir()
    result = tmp_path / "result.txt"
    result.write_text("stale answer", encoding="utf-8")

    with pytest.raises(OSError):
        provider_usage.parse_claude_json(unreadable, result)

    assert result.read_text(encoding="utf-8") == ""


def test_claude_parse_malformed_json_keeps_the_raw_text_as_the_result(tmp_path: pathlib.Path) -> None:
    response = tmp_path / "raw.json"
    response.write_text("  not json\nstill not json  \n", encoding="utf-8")
    result = tmp_path / "result.txt"

    with pytest.raises(provider_usage.UnparseableOutputError):
        provider_usage.parse_claude_json(response, result)

    assert result.read_text(encoding="utf-8") == "not json\nstill not json"


def test_parse_claude_json_cli_success_and_failures(tmp_path: pathlib.Path) -> None:
    good = _write_json(tmp_path / "raw.json", _CLAUDE_RESPONSE)
    bad = tmp_path / "bad.json"
    bad.write_text("not json\n", encoding="utf-8")
    not_an_object = _write_json(tmp_path / "list.json", [1, 2])

    ok = _run_cli(tmp_path, "parse-claude-json", "--", str(good), str(tmp_path / "r1.txt"), "claude-opus-4-8")
    malformed = _run_cli(tmp_path, "parse-claude-json", str(bad), str(tmp_path / "r2.txt"))
    unreadable = _run_cli(tmp_path, "parse-claude-json", str(tmp_path), str(tmp_path / "r3.txt"))
    wrong_shape = _run_cli(tmp_path, "parse-claude-json", str(not_an_object), str(tmp_path / "r4.txt"))

    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.startswith('{"input_tokens":10,"output_tokens":20,')
    assert json.loads(ok.stdout)["canonical_model_id"] == "claude-opus-4-8"
    assert malformed.returncode == 1
    assert json.loads(malformed.stdout) == {"captured": False, "reason": "cannot parse JSON from claude output"}
    assert malformed.stderr.strip() == "token_capture: cannot parse JSON from claude output"
    assert unreadable.returncode == 1
    assert json.loads(unreadable.stdout)["captured"] is False
    assert unreadable.stderr.startswith("token_capture: cannot read json_file:")
    assert wrong_shape.returncode == 1 and wrong_shape.stdout == ""
    assert "Traceback" not in wrong_shape.stderr and len(wrong_shape.stderr.splitlines()) == 1


# ---------------------------------------------------------------------------
# (b) the tokens.json record write
# ---------------------------------------------------------------------------


def test_write_creates_a_new_per_model_record(tmp_path: pathlib.Path) -> None:
    written = _write(tmp_path, _PER_MODEL_USAGE)

    assert written == {
        "provider": "claude",
        "agent": "coder",
        "rc_version": "v1.2.3",
        "invocations": 1,
        "elapsed_seconds": 30,
        "timestamp": _NOW,
        "total_cost_usd": 0.25,
        "model_usage": _PER_MODEL_USAGE["model_usage"],
    }
    assert (tmp_path / "tokens.json").read_text(encoding="utf-8").endswith("}\n")


def test_write_merges_into_an_existing_record(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, _PER_MODEL_USAGE, effort="high")
    second = {
        "total_cost_usd": 0.5,
        "model_usage": {
            "m1": {"input_tokens": 3, "output_tokens": 4, "cost_usd": 0.5},
            "m2": {"output_tokens": 9},
        },
    }

    written = _write(tmp_path, second, elapsed_seconds=5, rc_version="none")

    assert written["invocations"] == 2
    assert written["elapsed_seconds"] == 35
    assert written["total_cost_usd"] == 0.75
    assert written["rc_version"] == "v1.2.3", "'none' keeps the existing RC version"
    assert written["effort"] == "high", "an omitted effort keeps the existing value"
    assert written["model_usage"]["m1"]["input_tokens"] == 4
    assert written["model_usage"]["m1"]["output_tokens"] == 6
    assert written["model_usage"]["m2"] == {
        "input_tokens": 0,
        "output_tokens": 9,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost_usd": 0,
    }


def test_write_flat_schema_sums_token_counts(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, _FLAT_USAGE)

    written = _write(tmp_path, _FLAT_USAGE, model="other", elapsed_seconds=2)

    assert list(written)[:4] == ["model", "provider", "agent", "rc_version"]
    assert written["model"] == "other"
    assert written["input_tokens"] == 14 and written["cache_read_input_tokens"] == 4
    assert written["invocations"] == 2 and written["elapsed_seconds"] == 32
    assert "effort" not in written


def test_write_empty_rc_version_is_written_as_none(tmp_path: pathlib.Path) -> None:
    assert _write(tmp_path, _PER_MODEL_USAGE, rc_version="")["rc_version"] == "none"
    (tmp_path / "tokens.json").unlink()
    assert _write(tmp_path, {"captured": False}, rc_version="")["rc_version"] == "none"


@pytest.mark.parametrize("elapsed", ["abc", "-3", "1.5", ""])
def test_write_non_digit_elapsed_time_reads_as_zero(tmp_path: pathlib.Path, elapsed: str) -> None:
    assert _write(tmp_path, _FLAT_USAGE, elapsed_seconds=elapsed)["elapsed_seconds"] == 0


def test_write_failure_record_replaces_the_file(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, _PER_MODEL_USAGE)

    written = _write(tmp_path, {"captured": False, "reason": "boom"}, effort="low", elapsed_seconds="7")

    assert written == {
        "captured": False,
        "reason": "boom",
        "provider": "claude",
        "agent": "coder",
        "rc_version": "v1.2.3",
        "invocations": 1,
        "elapsed_seconds": 7,
        "timestamp": _NOW,
        "total_cost_usd": 0,
        "model_usage": {},
        "effort": "low",
    }


def test_load_usage_record_reads_empty_and_invalid_text_as_no_usage() -> None:
    assert token_capture.load_usage_record("") == {}
    assert token_capture.load_usage_record(" {} ") == {}
    assert token_capture.load_usage_record("not json") == {}
    assert token_capture.load_usage_record('{"input_tokens":1}') == {"input_tokens": 1}


def test_write_tokens_cli(tmp_path: pathlib.Path) -> None:
    tokens = tmp_path / "tokens.json"

    ok = _run_cli(
        tmp_path, "write-tokens", "--", json.dumps(_FLAT_USAGE), str(tokens), "mod", "claude", "coder", "-5", "", ""
    )
    refused = _run_cli(tmp_path, "write-tokens", "[1]", str(tokens), "mod", "claude", "coder", "1")
    unwritable = _run_cli(tmp_path, "write-tokens", "{}", str(tmp_path), "mod", "claude", "coder", "1")

    assert ok.returncode == 0 and ok.stdout == "", ok.stderr
    written = json.loads(tokens.read_text(encoding="utf-8"))
    assert written["elapsed_seconds"] == 0 and written["rc_version"] == "none"
    assert refused.returncode == 1 and len(refused.stderr.splitlines()) == 1
    assert unwritable.returncode == 1
    assert unwritable.stderr.startswith("token_capture: cannot write tokens.json:")


# ---------------------------------------------------------------------------
# (c) the dominant-model read
# ---------------------------------------------------------------------------


def test_dominant_model_with_one_model(tmp_path: pathlib.Path) -> None:
    tokens = _write_json(tmp_path / "tokens.json", {"model_usage": {"only": {"output_tokens": 1}}})

    assert token_capture.dominant_output_token_model(tokens) == "only"


def test_dominant_model_ranks_several_by_output_tokens_with_alphabetical_ties(tmp_path: pathlib.Path) -> None:
    tokens = _write_json(
        tmp_path / "tokens.json",
        {
            "model_usage": {
                "busy-input": {"input_tokens": 10_000, "output_tokens": 2},
                "zeta": {"output_tokens": 50},
                "alpha": {"output_tokens": 50},
                "not-an-entry": "text",
                "bad-count": {"output_tokens": "many"},
            }
        },
    )

    assert token_capture.dominant_output_token_model(tokens) == "alpha"


@pytest.mark.parametrize(
    "content",
    [
        None,  # no file at all
        "not json",
        "[1, 2]",
        '{"model": "m", "input_tokens": 5}',
        '{"model_usage": null}',
        '{"model_usage": {}}',
        '{"model_usage": {"m": {"output_tokens": 0}}}',
    ],
)
def test_dominant_model_undeterminable_returns_none(tmp_path: pathlib.Path, content) -> None:
    tokens = tmp_path / "tokens.json"
    if content is not None:
        tokens.write_text(content, encoding="utf-8")

    assert token_capture.dominant_output_token_model(tokens) is None


def test_dominant_model_cli_exit_status(tmp_path: pathlib.Path) -> None:
    tokens = _write_json(tmp_path / "tokens.json", {"model_usage": {"m": {"output_tokens": 3}}})

    found = _run_cli(tmp_path, "dominant-model", str(tokens))
    missing = _run_cli(tmp_path, "dominant-model", str(tmp_path / "absent.json"))

    assert (found.returncode, found.stdout) == (0, "m\n")
    assert (missing.returncode, missing.stdout, missing.stderr) == (1, "", "")


# ---------------------------------------------------------------------------
# token_capture.sh reaches the module through pp_run_ops
# ---------------------------------------------------------------------------


def test_token_capture_sh_parses_and_writes_through_the_module(tmp_path: pathlib.Path) -> None:
    response = _write_json(tmp_path / "raw.json", _CLAUDE_RESPONSE)
    artifacts = tmp_path / "artifacts"
    script = tmp_path / "capture.sh"
    script.write_text(
        "set -euo pipefail\n" f'source "{_TOKEN_CAPTURE_SH}"\n'
        # Hide jq so the merge takes the module path whatever the host has.
        'command() { if [[ "$1" == "-v" && "$2" == "jq" ]]; then return 1; fi; builtin command "$@"; }\n'
        f'usage="$(token_capture_parse_result "{response}" "{tmp_path / "result.txt"}" claude-opus-4-8)"\n'
        'echo "$usage"\n'
        f'token_capture_merge_into_task_artifacts "$usage" "{artifacts}" mod claude coder 12 "" high\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=_env(tmp_path, PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "temp")),
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["canonical_model_id"] == "claude-opus-4-8"
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "the agent's answer"
    written = json.loads((artifacts / "tokens.json").read_text(encoding="utf-8"))
    assert written["rc_version"] == "none"
    assert written["elapsed_seconds"] == 12
    assert written["effort"] == "high"
    assert set(written["model_usage"]) == {"claude-opus-4-8", "claude-haiku-4-5-20251001"}
