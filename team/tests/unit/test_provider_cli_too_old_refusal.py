"""
test_provider_cli_too_old_refusal.py
=====================================
Behavioral fixtures for the wake's handling of a provider CLI that is too
old for the configured model: the Claude CLI exits in seconds with

    [claude-code:unrecognized_model] {"model":"...","query_source":"sdk"}
    API Error: 400 Claude Code 2.1.276 does not support this model;
    version 2.1.280 or newer is required.

That is an infrastructure condition -- no agent ran -- and the wake now
treats it as one instead of as a stuck task:

  (1) provider_claude.sh's ``claude_cli_too_old_for_model`` names the
      signature once and extracts the installed and required versions;
      transient, safeguard, and other-400 outputs never match.
  (2) The real ``provider_dispatch``, run against a fake ``claude`` binary
      that prints the signature and exits 1, narrates a dual-channel
      refusal (stable error key, message naming both versions, runnable
      remedy) and leaves the per-task memo ``provider_dispatch_refused``
      answers from; the real finalize routing then returns the task to
      BACKLOG with its durable ## Zero Progress record untouched and
      ## Needs Human unchanged.
  (3) ``_wd_run_provider_and_capture_tokens`` never reports
      "token capture status: success" for such a dispatch.
  (4) ``provider_model_preflight`` refuses ahead of dispatch while
      ``claude --version`` is below the version an earlier refusal memoed
      for the same model, and clears the memo once the CLI satisfies it.

Every test runs a stub CLI on PATH; none makes a real provider call. Tests
source the REAL production files and call their real functions (not a
reimplementation), following the fake-``claude``-on-PATH harness in
test_wake_claude_effort_wiring.py and the function-extraction harness in
test_zero_progress_cycle_cap.py.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import re
import stat
import subprocess
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_LIB_DIR = _SCRIPTS_DIR / "lib"
_WAKE_SCRIPT_DIR = _SCRIPTS_DIR / "wake"
_INI_PARSER = _LIB_DIR / "ini_parser.sh"
_CONFIG_LOADER = _LIB_DIR / "config_loader.sh"
_PROVIDER_CLAUDE = _LIB_DIR / "provider_claude.sh"
_PROVIDER_CODEX = _LIB_DIR / "provider_codex.sh"
_DRIVER_INVOKE = _LIB_DIR / "wake_driver_invoke.sh"
_DRIVER_FINALIZE = _LIB_DIR / "wake_driver_finalize.sh"
_WAKE_TASK_STATE = _LIB_DIR / "wake_task_state.sh"
_WAKE_TASK_TRANSITION = _LIB_DIR / "wake_task_transition.sh"
_OVERWATCH_PROTOCOL = _LIB_DIR / "overwatch_protocol.sh"
_TEMP_LIB = _LIB_DIR / "temp.sh"

_ERROR_KEY = "provider-claude-cli-too-old-for-model"
_TOO_OLD_STDERR = (
    '[claude-code:unrecognized_model] {"model":"claude-opus-5-5","query_source":"sdk"}\n'
    "API Error: 400 Claude Code 2.1.276 does not support this model; "
    "version 2.1.280 or newer is required.\n"
)

# The fake claude: `--version` reports FAKE_CLAUDE_VERSION; a dispatch
# behaves per FAKE_CLAUDE_MODE. "too-old" reproduces the real CLI's shape --
# the signature on stderr, a JSON error result with zero usage on stdout
# (which is what let the token-capture step call the run a success), exit 1.
_FAKE_CLAUDE_SCRIPT = """\
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  echo "${FAKE_CLAUDE_VERSION:-2.1.276} (Claude Code)"
  exit 0
fi
case "${FAKE_CLAUDE_MODE:-ok}" in
  too-old)
    printf '%s' "$FAKE_CLAUDE_TOO_OLD_STDERR" >&2
    echo '{"type":"result","subtype":"error_during_execution","is_error":true,"result":"API Error: 400 Claude Code 2.1.276 does not support this model; version 2.1.280 or newer is required.","usage":{"input_tokens":0,"output_tokens":0}}'
    exit 1
    ;;
  overloaded)
    echo 'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}' >&2
    exit 1
    ;;
  *)
    echo '{"type":"result","result":"ok","usage":{"input_tokens":1,"output_tokens":1}}'
    exit 0
    ;;
esac
"""


def _install_fake_claude(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "claude"
    fake.write_text(_FAKE_CLAUDE_SCRIPT, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _env_with_fake_claude(
    bin_dir: pathlib.Path, *, mode: str = "ok", version: str = "2.1.276"
) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["FAKE_CLAUDE_MODE"] = mode
    env["FAKE_CLAUDE_VERSION"] = version
    env["FAKE_CLAUDE_TOO_OLD_STDERR"] = _TOO_OLD_STDERR
    # A refusal is recorded to the per-project ledger only when a project is
    # in scope; these fixtures are project-less on purpose.
    env.pop("PGAI_PROJECT_NAME", None)
    return env


def _run(harness: str, env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", harness], capture_output=True, text=True, env=env, timeout=timeout
    )


def _provider_header(tmp_path: pathlib.Path) -> str:
    """Bash prelude that sources the real provider_claude.sh the way
    wake/claude.sh does (SCRIPT_DIR, config loaded for config_get)."""
    cfg = tmp_path / "kanban.cfg"
    cfg.write_text("[providers]\nactive = claude\n", encoding="utf-8")
    temp_root = tmp_path / "kanban_tmp"
    temp_root.mkdir(exist_ok=True)
    return f"""
set -uo pipefail
export SCRIPT_DIR={str(_WAKE_SCRIPT_DIR)!r}
export AGENT=coder
export PGAI_AGENT_KANBAN_TEMP_DIR={str(temp_root)!r}
source {str(_INI_PARSER)!r}
source {str(_CONFIG_LOADER)!r}
load_config {str(cfg)!r}
source {str(_PROVIDER_CLAUDE)!r}
"""


def _extract_function_snippet(func_name: str, source_file: pathlib.Path) -> str:
    """awk brace-matching extraction of one top-level bash function body
    (the convention test_zero_progress_cycle_cap.py established) so the
    finalize-stage function is exercised without wd_finalize_task's full
    substrate."""
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^{func_name}\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{
                        depth--
                        if (depth == 0) {{ found=0 }}
                    }}
                }}
            }}
        ' '{source_file}')"
    """)


def _write_status_file(
    tmp_path: pathlib.Path, task_id: str, *, state: str, zero_progress_count: int
) -> pathlib.Path:
    """A real-shaped status.md carrying an existing durable ## Zero Progress
    record, so "not incremented" is observable as an unchanged count rather
    than as an absent section."""
    task_dir = tmp_path / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    status_file = task_dir / "status.md"
    status_file.write_text(
        textwrap.dedent(f"""\
            # Status

            ## Task
            {task_id}

            ## State
            {state}

            ## Summary
            In progress.

            ## Artifacts
            none

            ## Blockers
            none

            ## Needs Human
            no

            ## Zero Progress
            reason_code: clean_exit_no_progress
            count: {zero_progress_count}
            limit: 3
            detail: prior cycle

            ## Next Recommended Step
            TBD

            ## Instruction Conflicts
            none
        """),
        encoding="utf-8",
    )
    return status_file


def _field(status_text: str, heading: str) -> str:
    m = re.search(rf"^##\s+{re.escape(heading)}\s*\n(.*?)(\n+##|\Z)", status_text, flags=re.S | re.M)
    assert m is not None, f"## {heading} section not found in:\n{status_text}"
    return m.group(1).strip()


def _zero_progress_count(status_text: str) -> str:
    body = _field(status_text, "Zero Progress")
    m = re.search(r"^count:\s*(\d+)\s*$", body, flags=re.M)
    assert m is not None, f"count line missing from ## Zero Progress:\n{body}"
    return m.group(1)


# ---------------------------------------------------------------------------
# (1) Signature recognition: claude_cli_too_old_for_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_TOO_OLD_STDERR, "2.1.276 2.1.280"),
        (
            "API Error: 400 Claude Code 2.1.276 does not support this model; version 2.1.280 or newer is required.",
            "2.1.276 2.1.280",
        ),
        ('[claude-code:unrecognized_model] {"model":"claude-opus-5-5","query_source":"sdk"}', "unknown unknown"),
        (
            '{"type":"result","is_error":true,"result":"API Error: 400 Claude Code 2.1.300 does not support this model; version 2.1.310 or newer is required."}',
            "2.1.300 2.1.310",
        ),
    ],
)
def test_cli_too_old_signature_is_recognized_and_versions_extracted(
    tmp_path: pathlib.Path, text: str, expected: str
) -> None:
    """Either line of the CLI's pair -- on stderr or embedded in a JSON
    result -- is recognized, and the installed/required versions are
    extracted ("unknown" when the matched line does not name them)."""
    bin_dir = _install_fake_claude(tmp_path)
    harness = _provider_header(tmp_path) + f"""
claude_cli_too_old_for_model {text!r}; rc=$?
echo; echo "RC=$rc"
"""
    result = _run(harness, _env_with_fake_claude(bin_dir))
    assert "RC=0" in result.stdout, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    assert result.stdout.splitlines()[0] == expected, result.stdout


@pytest.mark.parametrize(
    "text",
    [
        'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
        "API Error: 400 invalid_request_error: max_tokens must be a positive integer",
        "Error: request blocked by safety system before dispatch.",
        "HTTP 429 Too Many Requests -- rate limit exceeded, retry later",
        "",
    ],
)
def test_other_provider_failures_never_match_the_cli_too_old_signature(
    tmp_path: pathlib.Path, text: str
) -> None:
    """A transient error, a different 400, a safeguard refusal, a rate limit,
    or empty output is not this condition -- each keeps its own handling."""
    bin_dir = _install_fake_claude(tmp_path)
    harness = _provider_header(tmp_path) + f"""
out="$(claude_cli_too_old_for_model {text!r})"; rc=$?
echo "RC=$rc OUT=[$out]"
"""
    result = _run(harness, _env_with_fake_claude(bin_dir))
    assert "RC=1 OUT=[]" in result.stdout, f"stdout={result.stdout!r}\nstderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# (2) Dispatch path: real provider_dispatch + real finalize routing
# ---------------------------------------------------------------------------


def _dispatch_and_finalize_harness(
    tmp_path: pathlib.Path, task_id: str, status_file: pathlib.Path, backlog_file: pathlib.Path
) -> tuple[str, pathlib.Path]:
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    raw_output = tmp_path / "raw_output.json"
    harness = _provider_header(tmp_path) + f"""
source {str(_OVERWATCH_PROTOCOL)!r}
source {str(_WAKE_TASK_STATE)!r}
source {str(_WAKE_TASK_TRANSITION)!r}
{_extract_function_snippet("requeue_after_dispatch_refusal", _DRIVER_FINALIZE)}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE" >&2; }}
LOG_FILE={str(log_file)!r}
BACKLOG={str(backlog_file)!r}
task_id={task_id!r}
task_status={str(status_file)!r}

# The wake moved the task to WORKING before this point (wd_invoke_task);
# the fixture's status.md already says so. Dispatch through the real
# provider function against the fake CLI on PATH.
provider_dispatch "test prompt" "claude-opus-5-5" "PGAI_CODER_MODEL env" "" "" \\
  "$task_id" "$LOG_FILE" "coder" {str(raw_output)!r}
echo "AGENT_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"

# The real finalize routing (wd_finalize_task): a task still WORKING whose
# dispatch the CLI refused is requeued without the zero-progress path.
final_state="WORKING"
if [[ "$final_state" == "WORKING" ]] && provider_dispatch_refused "$task_id"; then
  echo "DISPATCH_REFUSED=true"
  final_state="$(requeue_after_dispatch_refusal "$task_id" "$task_status" "$LOG_FILE")"
else
  echo "DISPATCH_REFUSED=false"
fi
echo "FINAL_STATE=${{final_state}}"
echo "ZP_COUNT=$(zero_progress_field "$task_status" count 0)"
"""
    return harness, log_file


def test_refused_dispatch_returns_task_to_backlog_without_spending_zero_progress_budget(
    tmp_path: pathlib.Path,
) -> None:
    """The acceptance shape end to end: a stub CLI that prints the
    too-old-for-model 400 and exits 1 leaves the task BACKLOG (marker [ ]),
    its ## Zero Progress count exactly where it was, ## Needs Human no, and
    the log carrying a dual-channel refusal that names the installed CLI
    version, the required version, and a runnable remedy."""
    task_id = "CODER-20260101-040-fixture-refused"
    status_file = _write_status_file(tmp_path, task_id, state="WORKING", zero_progress_count=1)
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(f"# Coder Backlog\n\n- [A] {task_id}\n", encoding="utf-8")
    bin_dir = _install_fake_claude(tmp_path)
    harness, log_file = _dispatch_and_finalize_harness(tmp_path, task_id, status_file, backlog_file)

    result = _run(harness, _env_with_fake_claude(bin_dir, mode="too-old"))

    assert result.returncode == 0, f"harness failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "AGENT_EXIT=1" in result.stdout, result.stdout
    assert "DISPATCH_REFUSED=true" in result.stdout, result.stdout
    assert "FINAL_STATE=BACKLOG" in result.stdout, result.stdout
    assert "ZP_COUNT=1" in result.stdout, f"zero-progress count must be untouched:\n{result.stdout}"

    status_text = status_file.read_text(encoding="utf-8")
    assert _field(status_text, "State") == "BACKLOG", status_text
    assert _zero_progress_count(status_text) == "1", status_text
    assert _field(status_text, "Needs Human") == "no", status_text
    assert _field(status_text, "Blockers") == "none", status_text
    assert "zero-progress count unchanged" in _field(status_text, "Summary"), status_text
    queue_text = backlog_file.read_text(encoding="utf-8")
    assert re.search(rf"^- \[ \] {re.escape(task_id)}\s*$", queue_text, flags=re.M), queue_text

    log_text = log_file.read_text(encoding="utf-8")
    assert f"error:   {_ERROR_KEY}" in log_text, log_text
    message = re.search(r"^message: (.*)$", log_text, flags=re.M)
    assert message is not None, log_text
    assert "2.1.276" in message.group(1) and "2.1.280" in message.group(1), message.group(1)
    assert "claude-opus-5-5" in message.group(1), message.group(1)
    remedy = re.search(r"^remedy:  (.*)$", log_text, flags=re.M)
    assert remedy is not None, log_text
    assert remedy.group(1).startswith("claude update"), remedy.group(1)
    assert "<" not in remedy.group(1), f"remedy must be runnable as printed: {remedy.group(1)}"
    # The verbatim provider output is still in the log for the operator.
    assert "does not support this model" in log_text, log_text

    # The pre-dispatch memo names the model and the version the CLI needs.
    memo = tmp_path / "kanban_tmp" / "provider_claude" / "required_cli_version"
    assert memo.read_text(encoding="utf-8") == "claude-opus-5-5\t2.1.280\n", memo.read_text(encoding="utf-8")


def test_transient_failure_at_dispatch_is_not_reported_as_a_refused_dispatch(
    tmp_path: pathlib.Path,
) -> None:
    """A 529 overloaded exit is a real provider failure: no memo, no
    refusal key in the log, and the finalize routing leaves it to the
    ordinary transient/zero-progress classification."""
    task_id = "CODER-20260101-041-fixture-overloaded"
    status_file = _write_status_file(tmp_path, task_id, state="WORKING", zero_progress_count=1)
    backlog_file = tmp_path / "coder_backlog.md"
    backlog_file.write_text(f"# Coder Backlog\n\n- [A] {task_id}\n", encoding="utf-8")
    bin_dir = _install_fake_claude(tmp_path)
    harness, log_file = _dispatch_and_finalize_harness(tmp_path, task_id, status_file, backlog_file)

    result = _run(harness, _env_with_fake_claude(bin_dir, mode="overloaded"))

    assert result.returncode == 0, f"harness failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "AGENT_EXIT=1" in result.stdout, result.stdout
    assert "DISPATCH_REFUSED=false" in result.stdout, result.stdout
    assert "FINAL_STATE=WORKING" in result.stdout, result.stdout
    assert _ERROR_KEY not in log_file.read_text(encoding="utf-8")
    assert _field(status_file.read_text(encoding="utf-8"), "State") == "WORKING"


def test_stale_refusal_memo_is_cleared_by_the_next_dispatch(tmp_path: pathlib.Path) -> None:
    """A memo left by an earlier refused dispatch never answers for a later
    dispatch of the same task: provider_dispatch clears it first, so a
    dispatch that succeeds is not reported as refused."""
    task_id = "CODER-20260101-042-fixture-stale-memo"
    bin_dir = _install_fake_claude(tmp_path)
    memo_dir = tmp_path / "kanban_tmp" / "provider_claude"
    memo_dir.mkdir(parents=True)
    (memo_dir / f"dispatch_refused.{task_id}").write_text("", encoding="utf-8")
    log_file = tmp_path / "wake.log"
    harness = _provider_header(tmp_path) + f"""
provider_dispatch_refused {task_id!r} && echo "BEFORE=refused" || echo "BEFORE=clear"
provider_dispatch "test prompt" "" "provider default" "" "" {task_id!r} {str(log_file)!r} "coder" {str(tmp_path / "raw.json")!r}
echo "AGENT_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
provider_dispatch_refused {task_id!r} && echo "AFTER=refused" || echo "AFTER=clear"
"""
    result = _run(harness, _env_with_fake_claude(bin_dir, mode="ok"))
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "BEFORE=refused" in result.stdout, result.stdout
    assert "AGENT_EXIT=0" in result.stdout, result.stdout
    assert "AFTER=clear" in result.stdout, result.stdout


def test_codex_provider_never_reports_a_refused_dispatch(tmp_path: pathlib.Path) -> None:
    """The Codex side of the fifth interface function classifies no
    signature today, so it always answers "not refused"."""
    harness = f"""
set -uo pipefail
export SCRIPT_DIR={str(_WAKE_SCRIPT_DIR)!r}
export PGAI_AGENT_KANBAN_TEMP_DIR={str(tmp_path)!r}
source {str(_PROVIDER_CODEX)!r}
provider_dispatch_refused "ANY-TASK"; echo "RC=$?"
"""
    result = _run(harness, dict(os.environ))
    assert "RC=1" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"


# ---------------------------------------------------------------------------
# (3) Token capture never calls a refused dispatch a success
# ---------------------------------------------------------------------------


def test_token_capture_status_is_not_success_for_a_refused_dispatch(tmp_path: pathlib.Path) -> None:
    """Runs the real _wd_run_provider_and_capture_tokens against the
    too-old stub: tokens.json is still written (the attempt stays accounted
    for) but the status line says NOT STARTED, never success."""
    bin_dir = _install_fake_claude(tmp_path)
    log_file = tmp_path / "wake.log"
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    harness = _provider_header(tmp_path) + f"""
source {str(_DRIVER_INVOKE)!r}
log() {{ echo "[$(date -Iseconds)] wake(${{AGENT}}): $*" | tee -a "$LOG_FILE" >&2; }}
get_release_state_field() {{ return 1; }}
prompt="test prompt"
selected_model="claude-opus-5-5"
model_source="PGAI_CODER_MODEL env"
selected_effort=""
effort_source=""
task_id="CODER-20260101-043-fixture-tokens"
task_artifact_dir={str(artifact_dir)!r}
SUBAGENT="coder"
LOG_FILE={str(log_file)!r}
_wd_run_provider_and_capture_tokens
echo "AGENT_EXIT=${{PROVIDER_AGENT_EXIT_CODE}}"
"""
    result = _run(harness, _env_with_fake_claude(bin_dir, mode="too-old"))
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "AGENT_EXIT=1" in result.stdout, result.stdout
    combined = result.stderr + log_file.read_text(encoding="utf-8")
    assert "token capture status: NOT STARTED" in combined, combined
    assert "token capture status: success" not in combined, combined
    assert (artifact_dir / "tokens.json").is_file(), "tokens.json must still be written"


# ---------------------------------------------------------------------------
# (4) Pre-dispatch preflight driven by the memo and `claude --version`
# ---------------------------------------------------------------------------


def _preflight_harness(tmp_path: pathlib.Path, selected_model: str) -> tuple[str, pathlib.Path]:
    log_file = tmp_path / "wake.log"
    log_file.write_text("", encoding="utf-8")
    harness = _provider_header(tmp_path) + f"""
provider_model_preflight {selected_model!r} {str(log_file)!r}; rc=$?
echo "RC=$rc"
"""
    return harness, log_file


def _write_required_memo(tmp_path: pathlib.Path, model: str, required: str) -> pathlib.Path:
    memo_dir = tmp_path / "kanban_tmp" / "provider_claude"
    memo_dir.mkdir(parents=True, exist_ok=True)
    memo = memo_dir / "required_cli_version"
    memo.write_text(f"{model}\t{required}\n", encoding="utf-8")
    return memo


def test_preflight_refuses_before_dispatch_while_installed_cli_is_below_memoed_requirement(
    tmp_path: pathlib.Path,
) -> None:
    """With a memo from an earlier refusal naming this model and a
    `claude --version` still below it, the preflight returns 1 (the wake
    dispatches nothing) and narrates the dual-channel refusal; the memo
    stays for the next wake."""
    bin_dir = _install_fake_claude(tmp_path)
    memo = _write_required_memo(tmp_path, "claude-opus-5-5", "2.1.280")
    harness, log_file = _preflight_harness(tmp_path, "claude-opus-5-5")
    result = _run(harness, _env_with_fake_claude(bin_dir, version="2.1.276"))
    assert "RC=1" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    log_text = log_file.read_text(encoding="utf-8")
    assert "model-preflight: FAIL" in log_text, log_text
    assert f"error:   {_ERROR_KEY}" in log_text, log_text
    assert "2.1.276" in log_text and "2.1.280" in log_text, log_text
    assert re.search(r"^remedy:  claude update", log_text, flags=re.M), log_text
    assert "no-op pass" not in log_text, log_text
    assert memo.is_file(), "memo must survive a refused preflight"


def test_preflight_passes_and_clears_memo_once_installed_cli_satisfies_requirement(
    tmp_path: pathlib.Path,
) -> None:
    """After `claude update`, the same preflight passes, says so, and
    removes the memo so no later wake re-checks a satisfied requirement."""
    bin_dir = _install_fake_claude(tmp_path)
    memo = _write_required_memo(tmp_path, "claude-opus-5-5", "2.1.280")
    harness, log_file = _preflight_harness(tmp_path, "claude-opus-5-5")
    result = _run(harness, _env_with_fake_claude(bin_dir, version="2.1.284"))
    assert "RC=0" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    log_text = log_file.read_text(encoding="utf-8")
    assert "now satisfies the 2.1.280" in log_text, log_text
    assert _ERROR_KEY not in log_text, log_text
    assert not memo.exists(), "memo must be cleared once satisfied"


def test_preflight_ignores_a_memo_for_a_different_model(tmp_path: pathlib.Path) -> None:
    """A memo for another model neither refuses nor clears: the operator
    may have switched to a model the installed CLI supports."""
    bin_dir = _install_fake_claude(tmp_path)
    memo = _write_required_memo(tmp_path, "claude-opus-5-5", "2.1.280")
    harness, log_file = _preflight_harness(tmp_path, "claude-sonnet-4-6")
    result = _run(harness, _env_with_fake_claude(bin_dir, version="2.1.276"))
    assert "RC=0" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert _ERROR_KEY not in log_file.read_text(encoding="utf-8")
    assert memo.is_file()


def test_preflight_names_the_installed_cli_version_when_nothing_is_memoed(
    tmp_path: pathlib.Path,
) -> None:
    """The default preflight line is no longer a bare no-op pass: it names
    the installed CLI version and says model support is verified on first
    dispatch."""
    bin_dir = _install_fake_claude(tmp_path)
    harness, log_file = _preflight_harness(tmp_path, "claude-opus-5-5")
    result = _run(harness, _env_with_fake_claude(bin_dir, version="2.1.276"))
    assert "RC=0" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr}"
    log_text = log_file.read_text(encoding="utf-8")
    assert "installed CLI 2.1.276" in log_text, log_text
    assert "verified on first dispatch" in log_text, log_text
    assert "no-op pass" not in log_text, log_text
