"""
test_ops_section_writers.py
===========================
Section writers in OVERWATCH, the wake per-project stage and the integration
harness provider must rewrite only the section they target. An empty target
body is filled in place -- it never runs into the next section, so the heading
and body that follow survive byte-for-byte. A populated target gets exactly the
same record as before, and the separator after it is re-emitted as found.

Writers covered:
  scripts/lib/overwatch_protocol.sh
      transient_retry_write          (## Transient Retry)
      zero_progress_record           (## Zero Progress)
  scripts/lib/wake_project.sh
      _update_readme_feature_branch  (## Feature Branch in a task README)
  scripts/wake/harness.sh
      provider_invoke_agent's stub writers (## State, ## Summary, through
      _harness_write_state_done / _harness_write_summary)

Every test runs the shipped bash function on a fixture file. overwatch_protocol
.sh is sourced whole; the other two files do work at source time, so the one
function under test is lifted out of the shipped file verbatim and eval'd --
the same technique test_ops_section_readers.py uses for _status_section_re.
The harness writers and the feature-branch writer call
pgai_agent_kanban.task_status through pp_run_ops and commit through
pgai_atomic_write, which the wake gets from its bootstrap; the lifted run
sources pp_run_ops.sh and atomic_write.sh directly instead.
Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_OVERWATCH_PROTOCOL = _LIB / "overwatch_protocol.sh"
_WAKE_PROJECT = _LIB / "wake_project.sh"
_HARNESS = _TEAM_DIR / "scripts" / "wake" / "harness.sh"

_SCOPED_FILES = [_OVERWATCH_PROTOCOL, _WAKE_PROJECT, _HARNESS]

# The pre-boundary splice shape: "\s*\n(.*?)" under re.S lets \s* swallow an
# empty body's blank line so the lazy body starts at the next heading.
_OLD_WRITER_SHAPE = re.compile(r"\\s\*\\n\)\(\.\*\?\)")

_TRANSIENT_RECORD = (
    "reason_code: transient_api_error\n"
    "retry_count: 2\n"
    "retry_limit: 3\n"
    "provider: claude\n"
    "last_error: overloaded"
)
_ZERO_PROGRESS_RECORD = "reason_code: stale_active_orphan\ncount: 1\nlimit: 3\ndetail: no state change"
_BRANCH = "ai_feature/example-task"
_HARNESS_SUMMARY = "Harness stub: task completed by integration test harness."


def _explain(result) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"


def _lift(path: pathlib.Path, func: str) -> str:
    """Bash that defines *func* from the shipped *path* without running the
    rest of the file."""
    return f"""eval "$(awk '/^{func}\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' '{path}')"
declare -F {func} >/dev/null || {{ echo "{func} not found in {path}" >&2; exit 97; }}"""


def _sections(text: str) -> dict[str, str]:
    """Map each '## ' heading to its full chunk: heading line through the
    byte before the next heading (blank separator lines included)."""
    starts = [m.start() for m in re.finditer(r"^## ", text, flags=re.M)]
    chunks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
    return {chunk.split("\n", 1)[0].rstrip(): chunk for chunk in chunks}


def _assert_only_target_changed(before: str, after: str, target: str) -> None:
    old, new = _sections(before), _sections(after)
    assert list(new) == list(old), f"section list changed: {list(old)} -> {list(new)}\n{after!r}"
    for heading, chunk in old.items():
        if heading != target:
            assert new[heading] == chunk, f"{heading!r} altered while writing {target!r}:\n{after!r}"


# --- writers under test -------------------------------------------------------


def _transient_retry_write(tmp_path: pathlib.Path, text: str) -> str:
    status = tmp_path / "status.md"
    status.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_OVERWATCH_PROTOCOL}'\n"
        f"transient_retry_write '{status}' transient_api_error 2 3 claude overloaded",
    )
    assert result.returncode == 0, _explain(result)
    return status.read_text(encoding="utf-8")


def _zero_progress_record(tmp_path: pathlib.Path, text: str) -> str:
    status = tmp_path / "status.md"
    status.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_OVERWATCH_PROTOCOL}'\n"
        f"zero_progress_record '{status}' stale_active_orphan 1 3 'no state change'",
    )
    assert result.returncode == 0, _explain(result)
    return status.read_text(encoding="utf-8")


def _update_readme_feature_branch(tmp_path: pathlib.Path, text: str) -> str:
    readme = tmp_path / "README.md"
    readme.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_LIB / 'pp_run_ops.sh'}'\nsource '{_LIB / 'atomic_write.sh'}'\n"
        f"{_lift(_WAKE_PROJECT, '_update_readme_feature_branch')}\n"
        f"_update_readme_feature_branch '{readme}' '{_BRANCH}'",
    )
    assert result.returncode == 0, _explain(result)
    return readme.read_text(encoding="utf-8")


def _harness_invoke(tmp_path: pathlib.Path, text: str) -> str:
    tasks_root = tmp_path / "tasks"
    status = tasks_root / "example-task" / "status.md"
    status.parent.mkdir(parents=True)
    status.write_text(text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_LIB / 'pp_run_ops.sh'}'\nsource '{_LIB / 'atomic_write.sh'}'\n"
        f"{_lift(_HARNESS, '_harness_write_state_done')}\n{_lift(_HARNESS, '_harness_write_summary')}\n"
        f"{_lift(_HARNESS, 'provider_invoke_agent')}\n"
        f"TASKS_ROOT='{tasks_root}' provider_invoke_agent prompt model default "
        f"example-task '' /dev/null coder",
    )
    assert result.returncode == 0, _explain(result)
    return status.read_text(encoding="utf-8")


# (writer, target heading, record written into the target)
_WRITERS = [
    (_transient_retry_write, "## Transient Retry", _TRANSIENT_RECORD),
    (_zero_progress_record, "## Zero Progress", _ZERO_PROGRESS_RECORD),
    (_update_readme_feature_branch, "## Feature Branch", _BRANCH),
]
_WRITER_IDS = ["transient-retry", "zero-progress", "feature-branch"]


# --- pattern level ------------------------------------------------------------


def test_scoped_files_carry_no_section_writer_of_the_old_shape() -> None:
    # Intentional pattern-scan: every current and future splice in these
    # files must use the section-body boundary; each file is read in full.
    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for path in _SCOPED_FILES
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _OLD_WRITER_SHAPE.search(line)
    ]
    assert hits == [], "section writers must stop the body at the next '##' line:\n" + "\n".join(hits)


# --- the reported shapes ------------------------------------------------------


def test_transient_retry_write_on_empty_section_keeps_zero_progress(tmp_path: pathlib.Path) -> None:
    before = (
        "## State\nWORKING\n\n## Transient Retry\n\n"
        "## Zero Progress\nreason_code: stuck\ncount: 2\n\n## Needs Human\nno\n"
    )
    after = _transient_retry_write(tmp_path, before)
    assert after == (
        f"## State\nWORKING\n\n## Transient Retry\n{_TRANSIENT_RECORD}\n\n"
        "## Zero Progress\nreason_code: stuck\ncount: 2\n\n## Needs Human\nno\n"
    )
    _assert_only_target_changed(before, after, "## Transient Retry")


def test_zero_progress_record_on_empty_section_keeps_needs_human(tmp_path: pathlib.Path) -> None:
    before = "## State\nWORKING\n\n## Zero Progress\n\n## Needs Human\nno\n\n## Blockers\nnone\n"
    after = _zero_progress_record(tmp_path, before)
    assert after == (
        f"## State\nWORKING\n\n## Zero Progress\n{_ZERO_PROGRESS_RECORD}\n\n"
        "## Needs Human\nno\n\n## Blockers\nnone\n"
    )
    _assert_only_target_changed(before, after, "## Zero Progress")


def test_feature_branch_update_on_empty_field_keeps_workflow_type(tmp_path: pathlib.Path) -> None:
    before = "# Task\n\n## Feature Branch\n\n## Workflow Type\nfeature\n\n## Goal\ng\n"
    after = _update_readme_feature_branch(tmp_path, before)
    assert after == f"# Task\n\n## Feature Branch\n{_BRANCH}\n\n## Workflow Type\nfeature\n\n## Goal\ng\n"
    _assert_only_target_changed(before, after, "## Feature Branch")


# --- every writer, every target shape -----------------------------------------


@pytest.mark.parametrize(("write", "target", "record"), _WRITERS, ids=_WRITER_IDS)
@pytest.mark.parametrize(
    "shape",
    [
        "## Task\nx\n\n{h}\n\n## Next\nkeep me\n\n## Last\nz\n",
        "## Task\nx\n\n{h}\n## Next\nkeep me\n",
        "## Task\nx\n\n{h}   \n\n\n## Next\nkeep me\n",
        "{h}\n\n## Next\nkeep me\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "first-section"],
)
def test_empty_non_last_target_is_filled_and_other_sections_survive(
    tmp_path: pathlib.Path, write, target: str, record: str, shape: str
) -> None:
    before = shape.format(h=target)
    after = write(tmp_path, before)
    _assert_only_target_changed(before, after, target)
    body = _sections(after)[target].split("\n", 1)[1]
    assert body.strip() == record, f"{target} body is {body!r}"


@pytest.mark.parametrize(("write", "target", "record"), _WRITERS, ids=_WRITER_IDS)
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("## Task\nx\n\n{h}\n", "## Task\nx\n\n{h}\n{r}\n"),
        ("## Task\nx\n\n{h}", "## Task\nx\n\n{h}\n{r}\n"),
        ("## Task\nx\n\n{h}\nold: 1\nold: 2\n", "## Task\nx\n\n{h}\n{r}\n"),
        ("## Task\nx\n\n{h}\nold: 1\n", "## Task\nx\n\n{h}\n{r}\n"),
    ],
    ids=["empty-last", "empty-last-no-newline", "populated-last", "populated-last-one-line"],
)
def test_last_section_target_is_rewritten_in_place(
    tmp_path: pathlib.Path, write, target: str, record: str, shape: str, expected: str
) -> None:
    # A heading on the file's last line with no newline is filled in place
    # too, never appended a second time or skipped.
    after = write(tmp_path, shape.format(h=target))
    assert after == expected.format(h=target, r=record)


@pytest.mark.parametrize(("write", "target", "record"), _WRITERS, ids=_WRITER_IDS)
def test_populated_non_last_target_gets_the_same_record_and_keeps_its_separator(
    tmp_path: pathlib.Path, write, target: str, record: str
) -> None:
    before = f"## Task\nx\n\n{target}\nold: 1\nold: 2\n\n## Next\nkeep me\n\n## Last\nz\n"
    after = write(tmp_path, before)
    assert after == f"## Task\nx\n\n{target}\n{record}\n\n## Next\nkeep me\n\n## Last\nz\n"
    # A second write changes nothing: the separator does not grow per rewrite.
    assert write(tmp_path, after) == after


@pytest.mark.parametrize(
    ("write", "target", "record"), _WRITERS[:2], ids=_WRITER_IDS[:2]
)
def test_absent_record_section_is_appended_as_before(
    tmp_path: pathlib.Path, write, target: str, record: str
) -> None:
    assert write(tmp_path, "## State\nWORKING\n") == f"## State\nWORKING\n\n{target}\n{record}\n"
    assert write(tmp_path, "## State\nWORKING") == f"## State\nWORKING\n\n{target}\n{record}\n"


def test_feature_branch_update_is_a_no_op_when_the_field_is_absent(tmp_path: pathlib.Path) -> None:
    before = "# Task\n\n## Workflow Type\nrelease\n\n## Goal\ng\n"
    assert _update_readme_feature_branch(tmp_path, before) == before


# --- harness.sh provider_invoke_agent stub writers ----------------------------


def test_harness_stub_fills_empty_state_and_summary_without_dropping_sections(tmp_path: pathlib.Path) -> None:
    before = "## State\n\n## Summary\n\n## Needs Human\nno\n\n## Blockers\nnone\n"
    after = _harness_invoke(tmp_path, before)
    assert after == (
        f"## State\nDONE\n\n## Summary\n{_HARNESS_SUMMARY}\n\n## Needs Human\nno\n\n## Blockers\nnone\n"
    )


def test_harness_stub_rewrites_populated_state_and_summary_as_before(tmp_path: pathlib.Path) -> None:
    before = "## State\nWORKING\n\n## Summary\nold summary\n\n## Needs Human\nno\n"
    after = _harness_invoke(tmp_path, before)
    assert after == f"## State\nDONE\n\n## Summary\n{_HARNESS_SUMMARY}\n\n## Needs Human\nno\n"


def test_harness_stub_appends_state_when_absent(tmp_path: pathlib.Path) -> None:
    after = _harness_invoke(tmp_path, "## Needs Human\nno\n")
    assert after == "## Needs Human\nno\n\n## State\nDONE\n"
