"""
test_provider_signatures.py
===========================
The provider-error signature table and its three consumers.

config/provider-error-signatures.txt classes every provider error signature
as transient, refusal or billing. Three consumers read it:

  * the wake's post-exit check (handle_working_after_exit,
    scripts/lib/wake_lifecycle.sh) -- a transient tail takes the
    ## Transient Retry path, anything else the zero-progress path;
  * OVERWATCH's transient check (_ctae_matches_transient_signature,
    scripts/lib/overwatch-checks/check-transient-api-error.sh);
  * the block-evidence classifier (pgai_agent_kanban.overwatch.block_evidence)
    -- a refusal or billing tail is a provider refusal.

Each class's sample, a safeguard abort, and a tail mixing a 429 with a
billing signature go through all three, and through both readers (the
Python module and scripts/lib/provider_signatures.sh), which must agree.
A missing or malformed table fails loud, naming its path.
"""

from __future__ import annotations

import json
import pathlib
import shlex
import shutil
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.lib import provider_signatures as ps
from pgai_agent_kanban.overwatch import block_evidence as be
from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_READER = "scripts/lib/provider_signatures.sh"
_CHECK_TRANSIENT = "scripts/lib/overwatch-checks/check-transient-api-error.sh"

#: One tail per class, plus the safeguard abort and a billing error that
#: arrives with an HTTP 429 -- each with the class every consumer must give it.
SAMPLES: dict[str, tuple[str, str]] = {
    "transient": ("API Error: 529 overloaded_error", "transient"),
    "refusal": ("Error: authentication_error: invalid x-api-key", "refusal"),
    "billing": ("insufficient_quota: You exceeded your current quota", "billing"),
    "safeguard": ("terminated by an API safeguard error ([reasoning_extraction])", "transient"),
    "safety_filter": ("cut off by the same safety-filter error (`[reasoning_extraction]`)", "transient"),
    "billing_with_429": ("Error code: 429 - {'error': {'type': 'insufficient_quota'}}", "billing"),
}

#: Bare numbers that only look like status codes: a task-ID date, a token
#: count, an elapsed-ms value.
DIGIT_NOISE = "task CODER-20260503-001 used 15030 tokens in 5029ms"


def _tail(line: str) -> str:
    return f"agent starting\n{line}\nagent exiting\n"


def _bash_lines(tmp_path: pathlib.Path, script: str) -> list[str]:
    result = run_bash(tmp_path, "set -euo pipefail\n" + script, timeout=60)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout.splitlines()


# --- the table --------------------------------------------------------------


def test_the_installed_table_defines_every_class() -> None:
    table = ps.load_table()
    assert {row.klass for row in table} == set(ps.CLASS_PRECEDENCE)
    assert ps.DEFAULT_TABLE_PATH == _TEAM_DIR / "config" / "provider-error-signatures.txt"


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_python_reader_classes_each_sample(name: str) -> None:
    line, expected = SAMPLES[name]
    assert ps.classify(_tail(line)) == expected


def test_digit_noise_carries_no_signature() -> None:
    assert ps.classify(_tail(DIGIT_NOISE)) is None


def test_bash_reader_agrees_with_python_reader(tmp_path: pathlib.Path) -> None:
    names = sorted(SAMPLES) + ["digit_noise"]
    texts = [SAMPLES[n][0] for n in sorted(SAMPLES)] + [DIGIT_NOISE]
    calls = "\n".join(
        f"printf '%s=%s\\n' {name} \"$(provider_signature_class {shlex.quote(_tail(text))} || echo none)\""
        for name, text in zip(names, texts)
    )
    lines = _bash_lines(tmp_path, f"source {_READER}\nprovider_signatures_load\n{calls}\n")
    got = dict(line.split("=", 1) for line in lines)
    for name, text in zip(names, texts):
        assert got[name] == (ps.classify(_tail(text)) or "none"), f"{name}: bash says {got[name]}"


# --- the three consumers ----------------------------------------------------


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_overwatch_transient_check_matches_only_transient_tails(tmp_path: pathlib.Path, name: str) -> None:
    line, expected = SAMPLES[name]
    lines = _bash_lines(
        tmp_path,
        textwrap.dedent(f"""\
        source {_CHECK_TRANSIENT}
        if _ctae_matches_transient_signature {shlex.quote(_tail(line))}; then echo match; else echo miss; fi
    """),
    )
    assert lines == ["match" if expected == "transient" else "miss"], name


def _wake_post_exit(tmp_path: pathlib.Path, text: str) -> list[str]:
    """Run the real handle_working_after_exit on a log whose tail is *text*.

    Its collaborators are stubbed to report which path it took: the
    transient-retry record write (with the signature it stored) or the
    zero-progress counter. The signature reader and pp_run_ops are the real
    ones, loaded by overwatch_load_module_runner as in a wake.
    """
    agent_log = tmp_path / "agent.log"
    agent_log.write_text(text, encoding="utf-8")
    return _bash_lines(
        tmp_path,
        textwrap.dedent(f"""\
        source scripts/lib/overwatch_protocol.sh
        source scripts/lib/wake_lifecycle.sh
        log() {{ echo "log: $*" >&2; }}
        transient_retry_limit() {{ echo 3; }}
        transient_retry_field() {{ echo 0; }}
        transient_retry_write() {{ echo "path=transient sig=$6" >&3; }}
        zero_progress_advance_or_escalate() {{ echo "path=zero_progress" >&3; echo BACKLOG; }}
        transition_task_state() {{ :; }}
        set_block() {{ :; }}
        normalize_status_file() {{ :; }}
        block_record_write() {{ :; }}
        block_record_owner_role() {{ :; }}
        block_record_evidence() {{ :; }}
        exec 3>&1
        state="$(handle_working_after_exit CODER-20260101-001-example "{tmp_path}/status.md" "{agent_log}" 1)"
        echo "state=$state"
    """),
    )


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_wake_post_exit_retries_only_transient_tails(tmp_path: pathlib.Path, name: str) -> None:
    line, expected = SAMPLES[name]
    lines = _wake_post_exit(tmp_path, _tail(line))
    if expected == "transient":
        assert lines[0].startswith("path=transient sig="), lines
        assert lines[-1] == "state=BLOCKED", lines
    else:
        assert lines == ["path=zero_progress", "state=BACKLOG"], f"{name} must never retry as transient: {lines}"


def test_wake_post_exit_stores_the_safeguard_line_from_its_signature_on(tmp_path: pathlib.Path) -> None:
    lines = _wake_post_exit(tmp_path, _tail(SAMPLES["safeguard"][0]))
    assert lines[0] == "path=transient sig=API safeguard error ([reasoning_extraction])", lines


def test_wake_post_exit_ignores_digit_noise(tmp_path: pathlib.Path) -> None:
    assert _wake_post_exit(tmp_path, _tail(DIGIT_NOISE)) == ["path=zero_progress", "state=BACKLOG"]


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_block_evidence_classes_each_sample(name: str) -> None:
    line, expected = SAMPLES[name]
    assert be.signature_class(_tail(line)) == expected
    label = be.refusal_signature(_tail(line))
    if expected == "transient":
        assert label is None
    else:
        assert ps.first_match(_tail(line), (expected,)).label == label


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_block_evidence_zero_progress_verdict_follows_the_class(tmp_path: pathlib.Path, name: str) -> None:
    line, expected = SAMPLES[name]
    task = tmp_path / "task"
    (task / "logs").mkdir(parents=True)
    batch = tmp_path / "batch.log"
    batch.write_text(_tail(line), encoding="utf-8")
    end = batch.stat().st_size
    rows = [
        {
            "event": "dispatched",
            "batch_log": str(batch),
            "recorded_at": "2026-01-01T10:00:00Z",
            "offset_start": 0,
            "offset_end": None,
        },
        {
            "event": "returned",
            "batch_log": str(batch),
            "recorded_at": "2026-01-01T10:10:00Z",
            "offset_start": 0,
            "offset_end": end,
        },
    ]
    (task / "logs" / "dispatch.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    verdict = be.classify_zero_progress(task)
    assert verdict.reason_code == ("zero_progress" if expected == "transient" else "provider_refusal"), verdict


# --- a missing or malformed table fails loud ---------------------------------


def test_missing_table_names_its_path(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "config" / "provider-error-signatures.txt"
    with pytest.raises(ps.SignatureTableError) as caught:
        ps.load_table(missing)
    assert "provider_signatures.table_unreadable" in str(caught.value)
    assert str(missing) in str(caught.value)


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("sometimes  flaky  oops", "class 'sometimes'"),
        ("transient  Bad-Label  oops", "label 'Bad-Label'"),
        (r"transient  digits  error \d+", "construct grep -E does not read"),
        ("transient  open_group  (unclosed", "does not compile"),
        ("transient", "is not '<class> <label> <pattern>'"),
    ],
)
def test_malformed_line_is_refused_naming_its_line(tmp_path: pathlib.Path, line: str, fragment: str) -> None:
    table = tmp_path / "table.txt"
    table.write_text(f"# header\ntransient  ok  overloaded\n{line}\n", encoding="utf-8")
    with pytest.raises(ps.SignatureTableError) as caught:
        ps.load_table(table)
    assert "line 3" in str(caught.value) and fragment in str(caught.value), str(caught.value)


def _install_copy(root: pathlib.Path, with_table: bool) -> None:
    """A minimal install: the bash reader, pp_run_ops, the package, the table."""
    (root / "scripts" / "lib").mkdir(parents=True)
    for name in ("provider_signatures.sh", "pp_run_ops.sh"):
        shutil.copy2(_TEAM_DIR / "scripts" / "lib" / name, root / "scripts" / "lib" / name)
    shutil.copytree(
        _TEAM_DIR / "pgai_agent_kanban",
        root / "pgai_agent_kanban",
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    if with_table:
        (root / "config").mkdir()
        shutil.copy2(_TEAM_DIR / "config" / "provider-error-signatures.txt", root / "config")


@pytest.mark.parametrize("with_table", [True, False])
def test_bash_reader_reads_the_table_of_its_own_install(tmp_path: pathlib.Path, with_table: bool) -> None:
    root = tmp_path / "install"
    _install_copy(root, with_table)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = build_scrubbed_env(home_dir=tmp_path / "home")
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{root}/scripts/lib/provider_signatures.sh"; '
            f'provider_signature_class {shlex.quote(SAMPLES["safeguard"][0])}',
        ],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if with_table:
        assert (proc.returncode, proc.stdout) == (0, "transient\n"), proc.stderr
    else:
        assert proc.returncode == 2, proc.stdout
        assert str(root / "config" / "provider-error-signatures.txt") in proc.stderr, proc.stderr
