"""
test_codex_provider_modules.py
==============================
The Codex wake provider's Python half: ``pgai_agent_kanban.lib.codex_provider``
and the codex functions of ``pgai_agent_kanban.lib.provider_usage``.

Covered behaviour:

  * capture-stdin copies stdin byte for byte into a new temp file and
    prints its path;
  * the preflight scan reports a codex error event, passes a stream whose
    first event is not an error, and reads an empty, blank-only, missing or
    unreadable stream as "no output" (a failure only when the CLI exited
    non-zero) -- on provider_codex.sh's in-memory fallback path too, where
    a CLI that exits non-zero printing nothing fails the preflight;
  * the config model read returns double- and single-quoted values, and
    ``unknown`` for a missing key or a missing file;
  * the JSONL usage parse writes the agent text and returns the usage
    record, empties the result file when the stream is unreadable, and
    refuses a malformed usage block;
  * canonical_model_id stamping;
  * provider_codex.sh, sourced, reaches the same functions through
    pp_run_ops.

Each function is exercised in-process; the command line and the bash
wiring run as subprocesses with an environment built from an explicit set
and a throwaway HOME under tmp_path.
"""

from __future__ import annotations

import io
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

from pgai_agent_kanban.lib import codex_provider, provider_usage
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_PROVIDER = _TEAM_DIR / "scripts" / "lib" / "provider_codex.sh"
_MODULE = "pgai_agent_kanban.lib.codex_provider"

_AGENT_MESSAGE = '{"type":"item.completed","item":{"type":"agent_message","text":"%s"}}'
_TURN_COMPLETED = '{"type":"turn.completed","usage":' '{"input_tokens":120,"cached_input_tokens":40,"output_tokens":7}}'


def _write_lines(path: pathlib.Path, *lines: str) -> pathlib.Path:
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def _run_cli(tmp_path: pathlib.Path, *args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PYTHONPATH": str(_TEAM_DIR), "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path)},
    )
    return subprocess.run(
        [sys.executable, "-m", _MODULE, *args],
        input=stdin,
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# (a) capture-stdin
# ---------------------------------------------------------------------------


def test_copy_stream_to_temp_file_keeps_every_byte(tmp_path: pathlib.Path, monkeypatch) -> None:
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path))
    data = b'{"type":"error","message":"x"}\n\xff raw bytes\n'

    path = pathlib.Path(codex_provider.copy_stream_to_temp_file(io.BytesIO(data)))

    assert path.parent == tmp_path
    assert path.name.startswith("pf_jsonl_")
    assert path.read_bytes() == data


def test_copy_stream_lands_under_the_resolved_temp_root_with_tmpdir_unset(tmp_path: pathlib.Path, monkeypatch) -> None:
    """With TMPDIR unset the capture still lands under resolve_temp_root(),
    which is created when missing -- never in the system temp directory."""
    root = tmp_path / "kanban-temp" / "not-yet-made"
    monkeypatch.delenv("TMPDIR", raising=False)
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(root))

    path = pathlib.Path(codex_provider.copy_stream_to_temp_file(io.BytesIO(b"x")))

    assert path.parent == root
    assert root.is_dir()


def test_capture_stdin_prints_the_path_of_the_new_file(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path, "capture-stdin", stdin=b"line one\nline two\n")

    assert result.returncode == 0, result.stderr
    path = pathlib.Path(result.stdout.decode().strip())
    assert path.parent == tmp_path
    assert path.read_bytes() == b"line one\nline two\n"


# ---------------------------------------------------------------------------
# (b) preflight error scan
# ---------------------------------------------------------------------------


def test_preflight_scan_reports_an_error_event(tmp_path: pathlib.Path) -> None:
    jsonl = _write_lines(tmp_path / "out.jsonl", "", "not json", '{"type":"error","message":"model not found"}')

    message = codex_provider.preflight_error_message("gpt-nope", 1, jsonl)

    assert message == "model 'gpt-nope' rejected by codex: model not found"


def test_preflight_scan_error_event_without_a_message(tmp_path: pathlib.Path) -> None:
    jsonl = _write_lines(tmp_path / "out.jsonl", '{"type":"error"}')

    assert codex_provider.preflight_error_message("m", 0, jsonl) == "model 'm' rejected by codex: unknown error"


def test_preflight_scan_passes_when_the_first_event_is_not_an_error(tmp_path: pathlib.Path) -> None:
    # A later error event does not count: the first JSON event decides.
    jsonl = _write_lines(tmp_path / "out.jsonl", '{"type":"thread.started"}', '{"type":"error","message":"late"}')

    assert codex_provider.preflight_error_message("gpt-ok", 0, jsonl) is None
    # A non-zero exit with output is still a pass for the scan.
    assert codex_provider.preflight_error_message("gpt-ok", 3, jsonl) is None


@pytest.mark.parametrize("source", ["empty", "missing", "directory", "no path"])
def test_preflight_scan_reads_empty_or_unreadable_output_as_no_output(tmp_path: pathlib.Path, source: str) -> None:
    jsonl = {
        "empty": tmp_path / "empty.jsonl",
        "missing": tmp_path / "missing.jsonl",
        "directory": tmp_path,
        "no path": "",
    }[source]
    if source == "empty":
        jsonl.write_text("", encoding="utf-8")

    assert codex_provider.preflight_error_message("m", 0, jsonl) is None
    assert codex_provider.preflight_error_message("m", 7, jsonl) == (
        "codex exited 7 with no output; check CLI installation"
    )


def test_preflight_scan_reads_a_newline_only_stream_as_no_output(tmp_path: pathlib.Path) -> None:
    jsonl = tmp_path / "newline.jsonl"
    jsonl.write_text("\n", encoding="utf-8")

    assert codex_provider.preflight_error_message("m1", 0, jsonl) is None
    assert codex_provider.preflight_error_message("m1", 3, jsonl) == (
        "codex exited 3 with no output; check CLI installation"
    )


_SILENT_FAILING_CODEX = "#!/usr/bin/env bash\nexit 3\n"


def test_fallback_preflight_fails_a_cli_that_exits_non_zero_printing_nothing(tmp_path: pathlib.Path) -> None:
    """With the framework temp root unusable (a path under a regular file),
    mktemp fails and the preflight takes its in-memory fallback; a codex
    that exits 3 with no output still FAILs, naming the exit status."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text(_SILENT_FAILING_CODEX, encoding="utf-8")
    stub.chmod(0o755)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    log = tmp_path / "wake.log"
    script = (
        f'export SCRIPT_DIR="{_TEAM_DIR / "scripts" / "wake"}"\n'
        f'source "{_PROVIDER}"\n'
        "rc=0\n"
        f'provider_model_preflight m1 "{log}" >/dev/null || rc=$?\n'
        'echo "rc=$rc"\n'
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_path_dirs=[bin_dir],
        extra_env={
            "PGAI_AGENT_KANBAN_TEMP_DIR": str(blocker / "temp"),
            "TMPDIR": str(tmp_path),
            "PROVIDER_CLI": "codex",
            "AGENT": "tester",
            "MODEL_PREFLIGHT_TTL_SECONDS": "0",
        },
    )

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
        check=False,
    )

    assert "rc=1" in result.stdout, result.stdout + result.stderr
    log_text = log.read_text(encoding="utf-8")
    assert "codex exited 3 with no output; check CLI installation" in log_text, log_text
    assert "PASS" not in log_text, log_text


def test_preflight_error_cli_exit_status_says_whether_a_message_was_printed(tmp_path: pathlib.Path) -> None:
    rejected = _write_lines(tmp_path / "rejected.jsonl", '{"type":"error","message":"nope"}')
    accepted = _write_lines(tmp_path / "accepted.jsonl", '{"type":"turn.completed","usage":{}}')

    failed = _run_cli(tmp_path, "preflight-error", "gpt-x", "0", str(rejected))
    passed = _run_cli(tmp_path, "preflight-error", "gpt-x", "0", str(accepted))
    no_file = _run_cli(tmp_path, "preflight-error", "gpt-x", "2", "")

    assert (failed.returncode, failed.stdout.decode()) == (0, "model 'gpt-x' rejected by codex: nope\n")
    assert (passed.returncode, passed.stdout) == (1, b"")
    assert (no_file.returncode, no_file.stdout.decode()) == (
        0,
        "codex exited 2 with no output; check CLI installation\n",
    )


# ---------------------------------------------------------------------------
# (c) config model read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        ('model = "gpt-5.3-codex"\n', "gpt-5.3-codex"),
        ("model_reasoning_effort = 'high'\n  model = 'gpt-single'  # pinned\n", "gpt-single"),
        ('approval_policy = "never"\nmodel_reasoning_effort = "high"\n', "unknown"),
    ],
    ids=["double-quoted", "single-quoted", "missing-key"],
)
def test_config_model_read(tmp_path: pathlib.Path, config_text: str, expected: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(config_text, encoding="utf-8")

    assert codex_provider.read_config_model(config) == expected


def test_config_model_read_missing_file_is_unknown(tmp_path: pathlib.Path) -> None:
    assert codex_provider.read_config_model(tmp_path / "no-such-config.toml") == "unknown"


def test_config_model_cli_prints_unknown_for_a_missing_file(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path, "config-model", str(tmp_path / "absent.toml"))

    assert (result.returncode, result.stdout) == (0, b"unknown\n")


# ---------------------------------------------------------------------------
# (d) JSONL usage parse
# ---------------------------------------------------------------------------


def test_usage_parse_writes_agent_text_and_returns_usage(tmp_path: pathlib.Path) -> None:
    jsonl = _write_lines(
        tmp_path / "raw.jsonl",
        '{"type":"thread.started","thread_id":"t"}',
        _AGENT_MESSAGE % "first",
        "{truncated",
        '{"type":"item.completed","item":{"type":"command_execution","text":"ignored"}}',
        _AGENT_MESSAGE % "second",
        _TURN_COMPLETED,
    )
    result_text = tmp_path / "result.txt"
    warnings: list[str] = []

    record = provider_usage.parse_codex_jsonl(jsonl, result_text, warn=warnings.append)

    assert record == {
        "input_tokens": 120,
        "output_tokens": 7,
        "cache_read_input_tokens": 40,
        "cache_creation_input_tokens": 0,
    }
    assert result_text.read_text(encoding="utf-8") == "first\nsecond"
    assert warnings == []
    assert provider_usage.compact_json(record) == (
        '{"input_tokens":120,"output_tokens":7,"cache_read_input_tokens":40,"cache_creation_input_tokens":0}'
    )


def test_usage_parse_without_turn_completed_warns_and_returns_zeros(tmp_path: pathlib.Path) -> None:
    jsonl = _write_lines(tmp_path / "raw.jsonl", _AGENT_MESSAGE % "only text")
    warnings: list[str] = []

    record = provider_usage.parse_codex_jsonl(jsonl, tmp_path / "result.txt", warn=warnings.append)

    assert set(record.values()) == {0}
    assert warnings == ["provider_codex: WARNING: no turn.completed event found in JSONL — usage will be 0"]


def test_usage_parse_unreadable_stream_empties_the_result_file(tmp_path: pathlib.Path) -> None:
    result_text = tmp_path / "result.txt"
    result_text.write_text("stale answer", encoding="utf-8")

    with pytest.raises(OSError):
        provider_usage.parse_codex_jsonl(tmp_path / "missing.jsonl", result_text)

    assert result_text.read_text(encoding="utf-8") == ""


def test_usage_parse_malformed_usage_is_refused_before_writing(tmp_path: pathlib.Path) -> None:
    jsonl = _write_lines(tmp_path / "raw.jsonl", '{"type":"turn.completed","usage":{"input_tokens":"lots"}}')
    result_text = tmp_path / "result.txt"

    with pytest.raises(ValueError):
        provider_usage.parse_codex_jsonl(jsonl, result_text)

    assert not result_text.exists()


def test_parse_jsonl_cli_success_and_failures(tmp_path: pathlib.Path) -> None:
    good = _write_lines(tmp_path / "good.jsonl", _AGENT_MESSAGE % "hi", _TURN_COMPLETED)
    bad = _write_lines(tmp_path / "bad.jsonl", '{"type":"turn.completed","usage":{"output_tokens":null}}')

    ok = _run_cli(tmp_path, "parse-jsonl", str(good), str(tmp_path / "good.txt"))
    unreadable = _run_cli(tmp_path, "parse-jsonl", str(tmp_path / "gone.jsonl"), str(tmp_path / "gone.txt"))
    malformed = _run_cli(tmp_path, "parse-jsonl", str(bad), str(tmp_path / "bad.txt"))

    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["cache_read_input_tokens"] == 40
    assert (tmp_path / "good.txt").read_text(encoding="utf-8") == "hi"

    assert unreadable.returncode == 1
    assert json.loads(unreadable.stdout)["captured"] is False
    assert b"cannot read jsonl_file" in unreadable.stderr

    assert (malformed.returncode, malformed.stdout) == (1, b"")
    assert b"Traceback" not in malformed.stderr and b"cannot parse usage" in malformed.stderr


# ---------------------------------------------------------------------------
# (e) canonical_model_id stamping
# ---------------------------------------------------------------------------


def test_stamp_adds_canonical_model_id_last_and_leaves_the_input_alone() -> None:
    record = {"input_tokens": 1, "output_tokens": 2}

    stamped = provider_usage.stamp_canonical_model_id(record, "gpt-5.3-codex")

    assert list(stamped) == ["input_tokens", "output_tokens", "canonical_model_id"]
    assert stamped["canonical_model_id"] == "gpt-5.3-codex"
    assert "canonical_model_id" not in record


def test_stamp_model_cli_prints_compact_json(tmp_path: pathlib.Path) -> None:
    result = _run_cli(tmp_path, "stamp-model", '{"input_tokens": 1, "canonical_model_id": "old"}', "gpt-new")
    refused = _run_cli(tmp_path, "stamp-model", "[1]", "gpt-new")

    assert (result.returncode, result.stdout) == (0, b'{"input_tokens":1,"canonical_model_id":"gpt-new"}\n')
    assert (refused.returncode, refused.stdout) == (1, b"")


# ---------------------------------------------------------------------------
# provider_codex.sh reaches the module through pp_run_ops
# ---------------------------------------------------------------------------


def test_provider_parse_output_stamps_the_configured_model(tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "gpt-from-config"\n', encoding="utf-8")
    jsonl = _write_lines(tmp_path / "raw.jsonl", _AGENT_MESSAGE % "done", _TURN_COMPLETED)
    script = tmp_path / "parse.sh"
    script.write_text(
        "set -euo pipefail\n"
        f'export SCRIPT_DIR="{_TEAM_DIR / "scripts" / "wake"}"\n'
        f'source "{_PROVIDER}"\n'
        'echo "config=$(codex_read_model_from_config)"\n'
        f'provider_parse_output "{jsonl}" "{tmp_path / "result.txt"}" ""\n'
        f'provider_parse_output "{jsonl}" "{tmp_path / "pinned.txt"}" "gpt-pinned"\n',
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=home,
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "temp")},
    )

    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config_line, unpinned, pinned = result.stdout.splitlines()
    assert config_line == "config=gpt-from-config"
    assert json.loads(unpinned)["canonical_model_id"] == "gpt-from-config"
    assert "canonical_model_id" not in json.loads(pinned)
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "done"
