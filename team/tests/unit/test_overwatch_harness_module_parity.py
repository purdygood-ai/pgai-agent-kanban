"""
test_overwatch_harness_module_parity.py
=======================================
Behaviour preservation for the status.md readers and writers that moved
from inline Python heredocs to pgai_agent_kanban.task_status:

  team/scripts/lib/overwatch_protocol.sh
      transient_retry_field / zero_progress_field   (read --field)
      transient_retry_write / zero_progress_record  (render --append-missing)
  team/scripts/lib/overwatch-checks/check-transient-api-error.sh
      _ctae_do_requeue   (## Transient Retry record, State, Labels, Blockers,
                          Needs Human, Summary, blank-line collapse)
      _ctae_do_escalate  (Needs Human, Blocked Reason, blank-line collapse)
  team/scripts/wake/harness.sh
      _harness_write_state_done / _harness_write_summary

Each scenario runs twice on byte-identical fixtures at the same path: once
with the module-backed functions (the real files), once with the
heredoc-backed reference implementation of the same functions
(tests/fixtures/overwatch_harness_heredoc_reference.sh, sourced after the
real files so its definitions replace the converted ones). The two runs must
agree on exit code, stdout, stderr and the bytes of the status file.

harness.sh runs its wake dispatch when loaded, so its two writers are lifted
out of the shipped file by name (the technique test_ops_section_writers.py
uses for provider_invoke_agent); overwatch_protocol.sh and
check-transient-api-error.sh are sourced whole, with the OVERWATCH
collaborators overwatch_backup_file / overwatch_log_action stubbed, and the
## Block Record writer the module-backed escalate also calls
(block_record_write and its evidence helpers) stubbed to a no-op: the
reference predates that record, and its write is covered by
test_block_sites_write_block_record.py.

Fixture variants: the status.md shapes the writers and hand-edits produce --
canonical, blank lines before the value, a bold value, trailing text after
the value, trailing spaces on the heading, an empty body, the record body
"none", a record that is the last section, no trailing newline, runs of
blank lines, CRLF line endings, an absent section, a field with an empty
value -- for the zero-progress cap counting (count read, incremented,
written back, read again), the retry count, and the requeue and escalate
status rewrites.

Inputs the heredocs misread, and the shared reader accepts by design, are
pinned separately below ("deliberate differences"). Each test there asserts
the module-backed behaviour and that the reference behaved differently:
  * a lowercase record heading reads and rewrites in place;
  * a ``### subheading`` inside a record stays inside its body;
  * a status.md carrying a heading twice has only the first rewritten;
  * the harness appends an absent ## State after a newline it adds when
    the file has none;
  * a record write to a missing status.md fails with the module's
    wording on stderr (the exit code and untouched path are unchanged).

Every fixture lives under pytest's tmp_path; the subprocess environment is
built explicitly and its working directory is the fixture root, so module
resolution through pp_run_ops is shown not to depend on the caller's cwd.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

# This file lives at team/tests/unit/, so three parents up is team/.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LIB_DIR = _TEAM_DIR / "scripts" / "lib"
_OVERWATCH_PROTOCOL = _LIB_DIR / "overwatch_protocol.sh"
_TRANSIENT_CHECK = _LIB_DIR / "overwatch-checks" / "check-transient-api-error.sh"
_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"
_REFERENCE_LIB = _TEAM_DIR / "tests" / "fixtures" / "overwatch_harness_heredoc_reference.sh"

_IMPLS = ("module", "reference")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _lift(path: pathlib.Path, func: str) -> str:
    """Bash that defines *func* from the shipped *path* without running the rest of it."""
    return (
        f"eval \"$(awk '/^{func}\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' '{path}')\"\n"
        f"declare -F {func} >/dev/null || {{ echo '{func} not found in {path}' >&2; exit 97; }}\n"
    )


def _script(impl: str, calls: str) -> str:
    """Bash that loads the *impl* functions, then runs *calls*.

    errexit is off so a failing call reports its own exit code; each call
    line prints ``RC=<n>`` itself where the code matters.
    """
    reference = f'source "{_REFERENCE_LIB}"\n' if impl == "reference" else ""
    return (
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            source "{_LIB_DIR / 'pp_run_ops.sh'}"
            source "{_LIB_DIR / 'atomic_write.sh'}"
            source "{_OVERWATCH_PROTOCOL}"
            source "{_TRANSIENT_CHECK}"
            overwatch_backup_file() {{ echo none; }}
            overwatch_log_action() {{ printf 'LOG: %s|%s|%s\\n' "$2" "$3" "$5"; }}
            block_record_write() {{ :; }}
            block_record_owner_role() {{ :; }}
            block_record_evidence() {{ :; }}
        """)
        + _lift(_HARNESS, "_harness_write_state_done")
        + _lift(_HARNESS, "_harness_write_summary")
        + reference
        + calls
        + "\n"
    )


def _run(impl: str, root: pathlib.Path, text: "str | None", calls: str) -> dict:
    """Write *text* to ``root/status.md`` (unless None), run *calls* with
    *impl*, and return the exit code, both streams and the file bytes."""
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    status = root / "status.md"
    if text is not None:
        status.write_bytes(text.encode("utf-8"))
    temp_dir = root.parent / f"{root.name}-kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        "KANBAN_ROOT": str(temp_dir),
        "TRANSIENT_RETRY_CAP": "3",
        "ZERO_PROGRESS_CAP": "3",
        "S": str(status),
    }
    proc = subprocess.run(
        ["bash", "-c", _script(impl, calls)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    leftovers = sorted(p.name for p in root.iterdir() if p.name != "status.md")
    return {
        "rc": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "file": status.read_bytes() if status.exists() else None,
        "leftovers": leftovers,
    }


def _both(tmp_path: pathlib.Path, text: "str | None", calls: str) -> tuple[dict, dict]:
    module = _run("module", tmp_path / "root", text, calls)
    reference = _run("reference", tmp_path / "root", text, calls)
    return module, reference


def _assert_parity(tmp_path: pathlib.Path, text: "str | None", calls: str) -> dict:
    module, reference = _both(tmp_path, text, calls)
    assert module == reference, (
        f"module and heredoc reference disagree\n--- module ---\n{module}\n"
        f"--- reference ---\n{reference}"
    )
    return module


# ---------------------------------------------------------------------------
# Fixture shapes
# ---------------------------------------------------------------------------

_STATUS_SHAPES = {
    "canonical": (
        "# Status\n\n## State\nBLOCKED\n\n## Summary\nold summary\n\n## Needs Human\nyes\n\n"
        "## Blockers\nsomething broke\n\n## Labels\nfoo\n\n## Blocked Reason\nprovider error\n"
    ),
    "blank-lines-before-values": (
        "## State\n\n\nBLOCKED\n\n## Summary\n\n\nold\n\n## Needs Human\n\nyes\n\n"
        "## Blockers\n\nx\n"
    ),
    "bold-and-trailing-text": (
        "## State\n**BLOCKED**\n\n## Summary\nold (see log)\n\n## Needs Human\nyes - waiting\n\n"
        "## Labels\n**bold**\n"
    ),
    "trailing-spaces-on-headings": (
        "## State   \nBLOCKED\n\n## Summary \t\nold\n\n## Needs Human  \nyes\n\n## Labels  \nfoo\n"
    ),
    "empty-bodies": "## State\n\n## Summary\n\n## Needs Human\n\n## Blockers\n\n## Labels\n\n## Blocked Reason\n",
    "labels-already-transient": "## State\nBLOCKED\n\n## Labels\nTRANSIENT other\n\n## Summary\nold\n",
    "no-labels-no-blocked-reason": "## State\nBLOCKED\n\n## Summary\nold\n\n## Needs Human\nyes\n",
    "no-trailing-newline": "## State\nBLOCKED\n\n## Needs Human\nyes\n\n## Summary\nold",
    "blank-line-runs": "## State\nBLOCKED\n\n\n\n## Summary\nold\n\n\n\n\n## Needs Human\nyes\n\n\n",
    "crlf": "## State\r\nBLOCKED\r\n\r\n## Summary\r\nold\r\n\r\n## Needs Human\r\nyes\r\n\r\n## Labels\r\nfoo\r\n",
    "records-present": (
        "## State\nBLOCKED\n\n## Transient Retry\nreason_code: transient_api_error\nretry_count: 2\n"
        "retry_limit: 3\nprovider: claude\nlast_error: overloaded\n\n## Zero Progress\n"
        "reason_code: stale_active_orphan\ncount: 1\nlimit: 3\ndetail: no state change (cycle 1/3)\n\n"
        "## Summary\nold\n"
    ),
    "records-none": "## State\nWORKING\n\n## Transient Retry\nnone\n\n## Zero Progress\nNone\n\n## Summary\nx\n",
    "records-empty": "## State\nWORKING\n\n## Transient Retry\n\n## Zero Progress\n\n## Summary\ncount: 9\n",
    "records-last-no-newline": "## State\nWORKING\n\n## Zero Progress\nreason_code: x\ncount: 2",
    "only-heading": "## Summary\n",
    "empty-file": "",
}
_SHAPE_IDS = list(_STATUS_SHAPES)


# ---------------------------------------------------------------------------
# overwatch_protocol.sh: the durable-record readers
# ---------------------------------------------------------------------------


_READS = """\
for f in reason_code retry_count retry_limit provider last_error missing; do
  printf '%s=' "TR:$f"; transient_retry_field "$S" "$f" "dflt"; echo "RC=$?"
done
for f in reason_code count limit detail missing; do
  printf '%s=' "ZP:$f"; zero_progress_field "$S" "$f" "0"; echo "RC=$?"
done
printf 'NODEFAULT='; transient_retry_field "$S" retry_count; echo "RC=$?"
"""


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_record_readers_match_the_heredoc_reference(tmp_path: pathlib.Path, shape: str) -> None:
    _assert_parity(tmp_path, _STATUS_SHAPES[shape], _READS)


@pytest.mark.parametrize(
    "record",
    [
        "## Transient Retry\nreason_code: a\nretry_count: \nprovider: p\n",
        "## Transient Retry\nretry_count:\t7  \n",
        "## Transient Retry\n\n\nretry_count: 4\n\n\n## Next\nx\n",
        "## Transient Retry   \nretry_count: 5\n",
        "## Transient Retry\nNONE\n",
        "## Transient Retry\nretry_count: 1\nretry_count: 2\n",
        "## Transient Retry\nxretry_count: 8\nretry_count: 3\n",
        "## State\nx\n## Transient Retry\nretry_count: 6\n## Summary\nretry_count: 9\n",
    ],
    ids=["empty-value-runs-on", "tab-and-trailing-space", "blank-line-runs", "heading-trailing-spaces",
         "uppercase-none", "repeated-field", "prefixed-key", "no-blank-separators"],
)
def test_record_field_variants_match_the_heredoc_reference(tmp_path: pathlib.Path, record: str) -> None:
    _assert_parity(tmp_path, record, _READS)


def test_record_reader_on_a_missing_file_prints_the_default(tmp_path: pathlib.Path) -> None:
    out = _assert_parity(tmp_path, None, _READS)
    assert "TR:retry_count=dflt\nRC=0" in out["stdout"]
    assert "ZP:count=0\nRC=0" in out["stdout"]


# ---------------------------------------------------------------------------
# overwatch_protocol.sh: the durable-record writers
# ---------------------------------------------------------------------------


_WRITES = {
    "transient-retry": 'transient_retry_write "$S" transient_api_error 2 3 claude "API Error: 529 overloaded"; echo "RC=$?"',
    "transient-retry-trailing-space": 'transient_retry_write "$S" transient_api_error 1 3 codex "cut at 80 "; echo "RC=$?"',
    "transient-retry-empty-error": 'transient_retry_write "$S" transient_api_error 1 3 claude ""; echo "RC=$?"',
    "zero-progress": 'zero_progress_record "$S" stale_active_orphan 1 3 "no state change (cycle 1/3)"; echo "RC=$?"',
    "zero-progress-reset": 'zero_progress_reset "$S"; echo "RC=$?"',
}


@pytest.mark.parametrize("write", list(_WRITES))
@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_record_writers_match_the_heredoc_reference(tmp_path: pathlib.Path, shape: str, write: str) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES[shape], _WRITES[write])
    assert out["leftovers"] == [], out


def test_zero_progress_cap_counting_matches_the_heredoc_reference(tmp_path: pathlib.Path) -> None:
    # The read-increment-write cycle zero_progress_advance_or_escalate runs,
    # four times past a cap of 3, then a reset and one more cycle.
    cycle = textwrap.dedent("""\
        cycle() {
          local n; n="$(zero_progress_field "$S" count 0)"
          [[ "$n" =~ ^[0-9]+$ ]] || n=0; n=$(( n + 1 ))
          zero_progress_record "$S" stale_active_orphan "$n" "$(zero_progress_cap)" "orphan (cycle $n/$(zero_progress_cap))"
          if (( n < $(zero_progress_cap) )); then echo "cycle $n BACKLOG"; else echo "cycle $n BLOCKED"; fi
        }
        cycle; cycle; cycle; cycle
        zero_progress_reset "$S"; echo "after reset: $(zero_progress_field "$S" count x)"
        cycle
    """)
    out = _assert_parity(tmp_path, _STATUS_SHAPES["canonical"], cycle)
    assert out["stdout"].splitlines() == [
        "cycle 1 BACKLOG", "cycle 2 BACKLOG", "cycle 3 BLOCKED", "cycle 4 BLOCKED",
        "after reset: 0", "cycle 1 BACKLOG",
    ]


def test_transient_retry_counting_matches_the_heredoc_reference(tmp_path: pathlib.Path) -> None:
    cycle = textwrap.dedent("""\
        for i in 1 2 3 4; do
          n="$(transient_retry_field "$S" retry_count 0)"; n=$(( n + 1 ))
          transient_retry_write "$S" transient_api_error "$n" "$(transient_retry_limit)" claude "sig $i"
          echo "count=$(transient_retry_field "$S" retry_count 0) last=$(transient_retry_field "$S" last_error)"
        done
    """)
    out = _assert_parity(tmp_path, _STATUS_SHAPES["no-labels-no-blocked-reason"], cycle)
    assert out["stdout"].splitlines()[-1] == "count=4 last=sig 4"


def test_record_write_keeps_the_file_mode(tmp_path: pathlib.Path) -> None:
    calls = 'chmod 640 "$S"; zero_progress_record "$S" r 1 3 d; stat -c %a "$S"'
    out = _assert_parity(tmp_path, _STATUS_SHAPES["canonical"], calls)
    assert out["stdout"].strip() == "640"


# ---------------------------------------------------------------------------
# check-transient-api-error.sh: requeue and escalate
# ---------------------------------------------------------------------------


_REQUEUE = (
    '_CTAE_TASK_ID=CODER-20260101-001-parity-task _CTAE_STATUS_FILE="$S" _CTAE_NEW_COUNT=2 '
    '_ctae_do_requeue; echo "RC=$?"'
)
_ESCALATE = (
    '_CTAE_TASK_ID=CODER-20260101-001-parity-task _CTAE_STATUS_FILE="$S" _CTAE_CURRENT_COUNT=3 '
    '_CTAE_CAP=3 _ctae_do_escalate; echo "RC=$?"'
)


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_requeue_matches_the_heredoc_reference(tmp_path: pathlib.Path, shape: str) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES[shape], _REQUEUE)
    assert out["leftovers"] == [], out


@pytest.mark.parametrize("shape", _SHAPE_IDS)
def test_escalate_matches_the_heredoc_reference(tmp_path: pathlib.Path, shape: str) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES[shape], _ESCALATE)
    assert out["leftovers"] == [], out


def test_requeue_output_on_the_canonical_status(tmp_path: pathlib.Path) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES["canonical"], _REQUEUE)
    assert out["file"].decode() == (
        "# Status\n\n## State\nBACKLOG\n\n## Summary\nAuto-requeued by OVERWATCH "
        "check-transient-api-error: transient API error detected in provider batch log. "
        "Requeue #2.\n\n## Needs Human\nno\n\n## Blockers\nnone\n\n## Labels\nfoo TRANSIENT\n\n"
        "## Blocked Reason\nprovider error\n\n## Transient Retry\nreason_code: transient_api_error\n"
        "retry_count: 2\nretry_limit: 3\nprovider: overwatch\n"
        "last_error: transient signature matched in provider batch log\n"
    )
    assert "LOG: CODER-20260101-001-parity-task|transient-auto-requeued|" in out["stdout"]


def test_escalate_output_on_a_status_without_blocked_reason(tmp_path: pathlib.Path) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES["no-labels-no-blocked-reason"], _ESCALATE)
    assert out["file"].decode() == (
        "## State\nBLOCKED\n\n## Summary\nold\n\n## Needs Human\nyes\n\n## Blocked Reason\n"
        "Transient API error retry ceiling reached (3/3, see ## Transient Retry record); "
        "human investigation required.\n"
    )


def test_requeue_and_escalate_on_a_missing_file_fail_alike(tmp_path: pathlib.Path) -> None:
    # The stubbed backup succeeds, so the status write is what fails.
    for calls in (_REQUEUE, _ESCALATE):
        module, reference = _both(tmp_path, None, calls)
        assert module["file"] is None and reference["file"] is None
        assert module["stdout"] == reference["stdout"]
        assert module["stdout"].strip().endswith("RC=1")


# ---------------------------------------------------------------------------
# harness.sh: simulated-completion writers
# ---------------------------------------------------------------------------


_HARNESS_CALLS = (
    '_harness_write_state_done "$S"; echo "RC=$?"\n'
    '_harness_write_summary "$S"; echo "RC=$?"'
)


# An empty file has no final newline and no ## State: the append there is the
# pinned difference test_harness_state_append_adds_the_missing_final_newline.
@pytest.mark.parametrize("shape", [s for s in _SHAPE_IDS if s != "empty-file"])
def test_harness_writers_match_the_heredoc_reference(tmp_path: pathlib.Path, shape: str) -> None:
    out = _assert_parity(tmp_path, _STATUS_SHAPES[shape], _HARNESS_CALLS)
    assert out["leftovers"] == [], out


def test_harness_appends_an_absent_state(tmp_path: pathlib.Path) -> None:
    out = _assert_parity(tmp_path, "## Needs Human\nno\n", _HARNESS_CALLS)
    assert out["file"] == b"## Needs Human\nno\n\n## State\nDONE\n"


def test_harness_on_a_state_last_without_newline(tmp_path: pathlib.Path) -> None:
    # ## State present, so the no-newline append difference does not arise.
    out = _assert_parity(tmp_path, "## Summary\nold\n\n## State\nWORKING", _HARNESS_CALLS)
    assert out["file"] == (
        b"## Summary\nHarness stub: task completed by integration test harness.\n\n## State\nDONE\n"
    )


# ---------------------------------------------------------------------------
# Deliberate differences
# ---------------------------------------------------------------------------


def test_lowercase_record_heading_now_reads_and_rewrites_in_place(tmp_path: pathlib.Path) -> None:
    text = "## State\nWORKING\n\n## zero progress\nreason_code: x\ncount: 2\nlimit: 3\ndetail: d\n"
    calls = 'zero_progress_field "$S" count 0\nzero_progress_record "$S" y 3 3 e'
    module, reference = _both(tmp_path, text, calls)
    assert module["stdout"] == "2\n"
    assert module["file"] == b"## State\nWORKING\n\n## zero progress\nreason_code: y\ncount: 3\nlimit: 3\ndetail: e\n"
    assert reference["stdout"] == "0\n"
    assert reference["file"].count(b"count: ") == 2  # appended a second record


def test_subheading_inside_a_record_stays_in_its_body(tmp_path: pathlib.Path) -> None:
    text = "## Zero Progress\nreason_code: x\n### note\ncount: 2\n"
    module, reference = _both(tmp_path, text, 'zero_progress_field "$S" count 0')
    assert module["stdout"] == "2\n"
    assert reference["stdout"] == "0\n"


def test_duplicate_heading_has_only_the_first_rewritten(tmp_path: pathlib.Path) -> None:
    text = "## State\nBLOCKED\n\n## Summary\nold\n\n## State\nBLOCKED\n"
    module, reference = _both(tmp_path, text, _REQUEUE)
    assert module["rc"] == reference["rc"] == 0
    assert module["file"].decode().startswith("## State\nBACKLOG\n\n## Summary\nAuto-requeued")
    assert "## State\nBLOCKED\n" in module["file"].decode()
    assert "## State\nBLOCKED\n" not in reference["file"].decode()


def test_harness_state_append_adds_the_missing_final_newline(tmp_path: pathlib.Path) -> None:
    module, reference = _both(tmp_path, "## Needs Human\nno", _HARNESS_CALLS)
    assert module["file"] == b"## Needs Human\nno\n\n## State\nDONE\n"
    assert reference["file"] == b"## Needs Human\nno\n## State\nDONE\n"


def test_record_write_to_a_missing_file_names_the_failure_in_the_module_wording(tmp_path: pathlib.Path) -> None:
    calls = 'transient_retry_write "$S" r 1 3 p e; echo "RC=$?"'
    module, reference = _both(tmp_path, None, calls)
    assert module["stdout"] == reference["stdout"] == "RC=1\n"
    assert module["file"] is None and reference["file"] is None
    assert f"transient_retry_write: cannot write {tmp_path / 'root' / 'status.md'}" in module["stderr"]
    assert "transient_retry_write: cannot read" in reference["stderr"]
