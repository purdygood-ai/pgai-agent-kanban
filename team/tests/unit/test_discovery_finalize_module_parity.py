"""
test_discovery_finalize_module_parity.py
========================================
Behaviour preservation for the intake/bundle/cache writers in
team/scripts/lib/discovery_state.sh and the status.md ## Model read and
## Summary append in team/scripts/lib/wake_driver_finalize.sh, whose
parsing lives in the shared pgai_agent_kanban modules:

  _disc_set_status_field        md_fields render-status
  _disc_read_bundle_field       md_fields read
  _disc_update_bundle_pm_queued md_fields render-status + render-value
  _disc_mark_cache_bundled      ops render_ensure_marker
  narrate_model_mismatch        task_status read --raw (## Model)
  _wd_append_summary_finding    task_status render --append (## Summary)

Each scenario runs twice on byte-identical fixtures at the same path: once
with the module-backed functions (the real libs, sourced), once with the
heredoc-backed reference implementation of the same functions
(tests/fixtures/discovery_finalize_heredoc_reference.sh, sourced after the
real libs so its definitions replace the converted ones). The two runs must
agree on exit code, stdout, stderr and the bytes of every fixture file.

Fixture variants: the intake and bundle shapes the writers produce (a bug
file's ``## Status`` / value, the bundle writer's ``## Status`` / blank line
/ value layout, ``## PM Task`` holding ``none`` or a task id), hand-edit
shapes (trailing spaces on the heading, trailing text after the value, no
trailing newline, CRLF, an empty body, an absent heading with and without a
title line), backlog caches (missing file and directory, a pending or done
line, other entries, no ``## Queue`` section, a section after the queue) and
status.md shapes for the finalize pair (empty, populated, multi-line, last
section, blank-line runs, no ``## Summary`` / ``## Model``).

Inputs the heredocs misread and the shared modules handle by design are
pinned separately below ("deliberate differences"). Each test there asserts
the module-backed behaviour and that the reference behaved differently:
  * an inline ``## Status: open`` heading is rewritten in place (the
    heredoc inserted a second ``## Status``);
  * an empty bundle field reads empty (the heredoc read the next heading);
  * an empty ``## PM Task`` is filled in place (the heredoc overwrote the
    next heading line), and a ``none`` value with trailing spaces is
    replaced whole (the heredoc left the spaces on a line of their own);
  * a bold bundle value reads as the text inside;
  * a cache line marked ``[W]``/``[A]``/``[B]`` is flipped in place, every
    duplicate line is flipped, and a key that prefixes another key never
    flips the other key's line (the shared set_marker entry match);
  * a lowercase or inline ``## Model`` / ``## summary`` heading is read and
    appended to.

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
_DISCOVERY_STATE = _LIB_DIR / "discovery_state.sh"
_FINALIZE = _LIB_DIR / "wake_driver_finalize.sh"
_REFERENCE_LIB = _TEAM_DIR / "tests" / "fixtures" / "discovery_finalize_heredoc_reference.sh"

_ITEM = "BUG-0001-parity-item"
_OTHER = "BUG-0002-parity-other"
_PM_ID = "PM-20260101-001-decompose-parity-bundle"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _script(impl: str, calls: str) -> str:
    """Bash that loads the *impl* functions, then runs *calls*.

    errexit is off so a failing call reports its own exit code; each call
    line prints ``RC=<n>`` itself where the code matters. The stubs follow
    the reference so both implementations see the same collaborators.
    """
    reference = f'source "{_REFERENCE_LIB}"\n' if impl == "reference" else ""
    return (
        textwrap.dedent(f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            log() {{ printf 'LOG: %s\\n' "$*"; }}
            source "{_DISCOVERY_STATE}"
            source "{_FINALIZE}"
        """)
        + reference
        + "resolve_dominant_output_token_model() { echo model-b; }\n"
        + calls
        + "\n"
    )


def _run(impl: str, root: pathlib.Path, files: dict[str, str], calls: str) -> dict:
    """Lay *files* out under a fresh *root*, run *calls* with *impl*, and
    return the exit code, both streams and the bytes of every file."""
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    temp_dir = root.parent / f"{root.name}-kanban-temp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "LANG": "C.UTF-8",
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(temp_dir),
        "KANBAN_ROOT": str(temp_dir),
    }
    proc = subprocess.run(
        ["bash", "-c", _script(impl, calls)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(root),
    )
    snapshot = {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }
    return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "files": snapshot}


def _both(tmp_path: pathlib.Path, files: dict[str, str], calls: str) -> tuple[dict, dict]:
    """Run *calls* with the module-backed and the reference functions."""
    root = tmp_path / "fixture"
    return _run("module", root, files, calls), _run("reference", root, files, calls)


def _assert_same(module: dict, reference: dict) -> None:
    for field in ("rc", "stdout", "stderr", "files"):
        assert module[field] == reference[field], (
            f"{field} differs between the module-backed and the reference functions\n"
            f"module:    {module[field]!r}\nreference: {reference[field]!r}\n"
            f"module stderr: {module['stderr']}\nreference stderr: {reference['stderr']}"
        )


def _assert_differs(module: dict, reference: dict, field: str = "files") -> None:
    assert module[field] != reference[field], (
        f"expected the module-backed {field} to differ from the reference here\n{module[field]!r}"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bundle(status: str = "open", pm_task: str = "none") -> str:
    """A requirements bundle in the exact layout _disc_write_bundle writes."""
    return (
        "# Requirements: v9.9.9 — BUG bundle\n\n"
        f"## Status\n\n{status}\n\n"
        f"## PM Task\n\n{pm_task}\n\n"
        "## Target Version\n\nv9.9.9\n\n"
        "## Workflow Type\n\nrelease\n\n"
        "## Bundled Items\n\n"
        f"- {_ITEM}.md (`/x/bugs/{_ITEM}.md`)\n\n"
        "## Notes\n\nBundled by discovery pipeline.\n"
    )


# Intake (bug/priority) and bundle files carrying a ## Status the writer
# rewrites, plus files without one.
_INTAKE_VARIANTS = {
    "bug-canonical": f"# {_ITEM}\n\n## Status\nopen\n\n## Category\nwake\n\n## Symptom\ns\n",
    "bundle-layout": _bundle(),
    "bundle-running": _bundle("running", _PM_ID),
    "status-last": f"# {_ITEM}\n\n## Category\nwake\n\n## Status\nopen\n",
    "status-last-no-newline": f"# {_ITEM}\n\n## Category\nwake\n\n## Status\nopen",
    "empty-status": f"# {_ITEM}\n\n## Status\n\n## Category\nwake\n",
    "lowercase-heading": f"# {_ITEM}\n\n## status\nopen\n\n## Category\nwake\n",
    "heading-trailing-spaces": f"# {_ITEM}\n\n## Status  \nopen\n\n## Category\nwake\n",
    "trailing-text": f"# {_ITEM}\n\n## Status\nopen (reopened by operator)\n\n## Category\nwake\n",
    "blank-line-runs": f"# {_ITEM}\n\n## Status\n\n\nopen\n\n\n\n## Category\nwake\n",
    "crlf": f"# {_ITEM}\r\n\r\n## Status\r\nopen\r\n\r\n## Category\r\nwake\r\n",
    "absent-with-title": f"# {_ITEM}\n\n## Category\nwake\n",
    "absent-no-title": "## Category\nwake\n",
    "absent-empty-file": "",
}

# Bundle fields the readers are asked for, on shapes whose field is populated.
_READ_VARIANTS = {
    "bundle-open": _bundle(),
    "bundle-running": _bundle("running", _PM_ID),
    "bug-canonical": _INTAKE_VARIANTS["bug-canonical"],
    "status-last-no-newline": _INTAKE_VARIANTS["status-last-no-newline"],
    "lowercase-heading": _INTAKE_VARIANTS["lowercase-heading"],
    "heading-trailing-spaces": _INTAKE_VARIANTS["heading-trailing-spaces"],
    "trailing-text": _INTAKE_VARIANTS["trailing-text"],
    "blank-line-runs": _INTAKE_VARIANTS["blank-line-runs"],
    "crlf": _INTAKE_VARIANTS["crlf"],
    "absent-heading": _INTAKE_VARIANTS["absent-with-title"],
}

# Bundles whose ## PM Task the queued-PM writer rewrites or adds.
_PM_VARIANTS = {
    "bundle-none": _bundle(),
    "bundle-already-set": _bundle("running", "PM-20250101-001-older"),
    "no-blank-line-before-value": "# R\n\n## Status\nopen\n\n## PM Task\nnone\n\n## Notes\nn\n",
    "pm-task-last": "# R\n\n## Status\n\nopen\n\n## PM Task\n\nnone\n",
    "pm-task-last-no-newline": "# R\n\n## Status\n\nopen\n\n## PM Task\n\nnone",
    "indented-value": "# R\n\n## Status\n\nopen\n\n## PM Task\n\n  none\n\n## Notes\nn\n",
    "id-with-trailing-spaces": "# R\n\n## Status\n\nopen\n\n## PM Task\n\nPM-20250101-001-older  \n\n## Notes\nn\n",
    "multi-line-body": "# R\n\n## Status\nopen\n\n## PM Task\n\nnone\nsee notes\n\n## Notes\nn\n",
    "absent-before-other-heading": "# R\n\n## Status\n\nopen\n\n## Target Version\n\nv9.9.9\n",
    "absent-only-status": "# R\n\n## Status\n\nopen\n",
    "absent-only-status-no-newline": "# R\n\n## Status\n\nopen",
    "crlf": _bundle().replace("\n", "\r\n"),
}


def _cache(*lines: str, tail: str = "") -> str:
    return "# Backlog Cache\n\n## Queue\n\n" + "".join(f"{line}\n" for line in lines) + tail


_CACHE_VARIANTS = {
    "pending-line": _cache(f"- [x] {_OTHER}", f"- [ ] {_ITEM}"),
    "done-line": _cache(f"- [x] {_ITEM}"),
    "pending-with-description": _cache(f"- [ ] {_ITEM} — the bug", f"- [ ] {_OTHER} — next"),
    "indented-line": _cache(f"  - [ ] {_ITEM}"),
    "absent-among-others": _cache(f"- [x] {_OTHER}"),
    "absent-empty-queue": _cache(),
    "absent-queue-then-section": _cache(f"- [x] {_OTHER}", tail="\n## Notes\nn\n"),
    "absent-queue-trailing-blank-lines": _cache(f"- [x] {_OTHER}", tail="\n\n"),
    "absent-no-queue-section": "# Backlog Cache\n\nsomething\n",
    "absent-no-queue-no-newline": "# Backlog Cache",
}


def _status(model: str = "## Model\nmodel-a\n\n", summary: str = "## Summary\nold summary\n\n") -> str:
    return (
        "# Status\n\n## Task\nCODER-20260101-001-parity\n\n## Role\nCODER\n\n"
        f"{model}## State\nWORKING\n\n{summary}## Needs Human\nno\n"
    )


_MODEL_VARIANTS = {
    "stamped-differs": _status(),
    "stamped-matches": _status("## Model\nmodel-b\n\n"),
    "empty-model": _status("## Model\n\n"),
    "no-model-heading": _status(""),
    "blank-lines-before-value": _status("## Model\n\n\nmodel-a\n\n"),
    "heading-trailing-spaces": _status("## Model  \nmodel-a\n\n"),
    "model-last": "## Role\nCODER\n\n## Model\nmodel-a\n",
    "model-last-no-newline": "## Role\nCODER\n\n## Model\nmodel-a",
    "crlf": _status().replace("\n", "\r\n"),
    "two-line-body": _status("## Model\nmodel-a\nsecond\n\n"),
}

_SUMMARY_VARIANTS = {
    "populated": _status(),
    "empty-then-next": _status(summary="## Summary\n\n"),
    "next-heading-directly": _status(summary="## Summary\n"),
    "multi-paragraph": _status(summary="## Summary\nfirst\n\nsecond paragraph\n\n"),
    "blank-line-runs": _status(summary="## Summary\n\n\nold\n\n\n\n"),
    "heading-trailing-spaces": _status(summary="## Summary  \nold\n\n"),
    "summary-last": "## State\nDONE\n\n## Summary\nold\n",
    "summary-last-empty": "## State\nDONE\n\n## Summary\n",
    "summary-last-no-newline": "## State\nDONE\n\n## Summary\nold",
    "no-summary": "## State\nDONE\n\n## Needs Human\nno\n",
    "crlf": _status().replace("\n", "\r\n"),
}


# ---------------------------------------------------------------------------
# discovery_state.sh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["open", "running", "done"])
@pytest.mark.parametrize("variant", sorted(_INTAKE_VARIANTS))
def test_status_field_write_matches_reference(tmp_path: pathlib.Path, variant: str, value: str) -> None:
    module, reference = _both(
        tmp_path,
        {"item.md": _INTAKE_VARIANTS[variant]},
        f'_disc_set_status_field item.md {value}; echo "RC=$?"',
    )
    _assert_same(module, reference)


def test_status_field_write_skips_a_non_path_line_like_reference(tmp_path: pathlib.Path) -> None:
    module, reference = _both(
        tmp_path,
        {"item.md": _INTAKE_VARIANTS["bug-canonical"]},
        '_disc_set_status_field "Bundled 3 items into a requirements file" running; echo "RC=$?"',
    )
    _assert_same(module, reference)
    assert "skipping non-path line" in module["stderr"]


@pytest.mark.parametrize("field", ["Status", "PM Task", "Target Version", "Category"])
@pytest.mark.parametrize("variant", sorted(_READ_VARIANTS))
def test_bundle_field_read_matches_reference(tmp_path: pathlib.Path, variant: str, field: str) -> None:
    text = _READ_VARIANTS[variant]
    module, reference = _both(
        tmp_path,
        {"bundle.md": text},
        f'value="$(_disc_read_bundle_field bundle.md "{field}")"; echo "RC=$? [$value]"',
    )
    _assert_same(module, reference)


def test_bundle_field_read_of_a_missing_file_returns_1_like_reference(tmp_path: pathlib.Path) -> None:
    module, reference = _both(
        tmp_path, {}, 'value="$(_disc_read_bundle_field nowhere.md Status)"; echo "RC=$? [$value]"'
    )
    _assert_same(module, reference)
    assert "RC=1 []" in module["stdout"]


@pytest.mark.parametrize("variant", sorted(_PM_VARIANTS))
def test_bundle_pm_queued_write_matches_reference(tmp_path: pathlib.Path, variant: str) -> None:
    module, reference = _both(
        tmp_path,
        {"bundle.md": _PM_VARIANTS[variant]},
        f'_disc_update_bundle_pm_queued bundle.md {_PM_ID}; echo "RC=$?"',
    )
    _assert_same(module, reference)


def test_bundle_pm_queued_write_then_read_round_trips(tmp_path: pathlib.Path) -> None:
    calls = (
        f"_disc_update_bundle_pm_queued bundle.md {_PM_ID}\n"
        'echo "STATUS=[$(_disc_read_bundle_field bundle.md Status)]"\n'
        'echo "PM=[$(_disc_read_bundle_field bundle.md "PM Task")]"'
    )
    module, reference = _both(tmp_path, {"bundle.md": _bundle()}, calls)
    _assert_same(module, reference)
    assert module["stdout"] == f"STATUS=[running]\nPM=[{_PM_ID}]\n"


@pytest.mark.parametrize("variant", sorted(_CACHE_VARIANTS))
def test_cache_flip_matches_reference(tmp_path: pathlib.Path, variant: str) -> None:
    module, reference = _both(
        tmp_path,
        {"queues/bug_backlog.md": _CACHE_VARIANTS[variant]},
        f'_disc_mark_cache_bundled queues/bug_backlog.md {_ITEM}; echo "RC=$?"',
    )
    _assert_same(module, reference)


def test_cache_flip_creates_a_missing_cache_and_directory_like_reference(tmp_path: pathlib.Path) -> None:
    module, reference = _both(
        tmp_path, {}, f'_disc_mark_cache_bundled new/dir/bug_backlog.md {_ITEM}; echo "RC=$?"'
    )
    _assert_same(module, reference)
    assert module["files"]["new/dir/bug_backlog.md"] == (
        f"# Backlog Cache\n\n## Queue\n\n- [x] {_ITEM}\n".encode()
    )


def test_cache_flip_is_idempotent_like_reference(tmp_path: pathlib.Path) -> None:
    calls = "\n".join([f"_disc_mark_cache_bundled q.md {_ITEM}"] * 3)
    module, reference = _both(tmp_path, {"q.md": _CACHE_VARIANTS["absent-among-others"]}, calls)
    _assert_same(module, reference)


# ---------------------------------------------------------------------------
# wake_driver_finalize.sh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", sorted(_MODEL_VARIANTS))
def test_model_read_matches_reference(tmp_path: pathlib.Path, variant: str) -> None:
    module, reference = _both(
        tmp_path,
        {"status.md": _MODEL_VARIANTS[variant]},
        'narrate_model_mismatch status.md tokens.json demo; echo "RC=$?"',
    )
    _assert_same(module, reference)


def test_model_read_of_a_missing_status_file_is_silent_like_reference(tmp_path: pathlib.Path) -> None:
    module, reference = _both(tmp_path, {}, 'narrate_model_mismatch none.md tokens.json demo; echo "RC=$?"')
    _assert_same(module, reference)
    assert module["stdout"] == "RC=0\n"


@pytest.mark.parametrize("variant", sorted(_SUMMARY_VARIANTS))
def test_summary_append_matches_reference(tmp_path: pathlib.Path, variant: str) -> None:
    module, reference = _both(
        tmp_path,
        {"status.md": _SUMMARY_VARIANTS[variant]},
        '_wd_append_summary_finding status.md "POLLUTION SWEEP WARNING: agent tester modified'
        ' tracked file(s) during this task: a.sh b.py"; echo "RC=$?"',
    )
    _assert_same(module, reference)


def test_summary_append_twice_matches_reference(tmp_path: pathlib.Path) -> None:
    calls = '_wd_append_summary_finding status.md first\n_wd_append_summary_finding status.md second'
    module, reference = _both(tmp_path, {"status.md": _SUMMARY_VARIANTS["empty-then-next"]}, calls)
    _assert_same(module, reference)


# ---------------------------------------------------------------------------
# Deliberate differences
# ---------------------------------------------------------------------------


def test_inline_status_heading_is_rewritten_in_place(tmp_path: pathlib.Path) -> None:
    text = f"# {_ITEM}\n\n## Status: open\n\n## Category\nwake\n"
    module, reference = _both(tmp_path, {"item.md": text}, "_disc_set_status_field item.md running")
    assert module["files"]["item.md"] == f"# {_ITEM}\n\n## Status\nrunning\n\n## Category\nwake\n".encode()
    _assert_differs(module, reference)


def test_empty_bundle_field_reads_empty_not_the_next_heading(tmp_path: pathlib.Path) -> None:
    text = "# R\n\n## PM Task\n\n## Target Version\n\nv9.9.9\n"
    module, reference = _both(
        tmp_path, {"b.md": text}, 'echo "[$(_disc_read_bundle_field b.md "PM Task")] RC=$?"'
    )
    assert module["stdout"] == "[] RC=0\n"
    assert reference["stdout"] == "[## Target Version] RC=0\n"


def test_bold_bundle_value_reads_as_the_text_inside(tmp_path: pathlib.Path) -> None:
    module, reference = _both(
        tmp_path, {"b.md": "# R\n\n## Status\n\n**done**\n"}, 'echo "[$(_disc_read_bundle_field b.md Status)]"'
    )
    assert module["stdout"] == "[done]\n"
    _assert_differs(module, reference, "stdout")


def test_pm_task_none_with_trailing_spaces_is_replaced_whole(tmp_path: pathlib.Path) -> None:
    text = "# R\n\n## Status\n\nopen\n\n## PM Task\n\nnone  \n\n## Notes\nn\n"
    module, reference = _both(tmp_path, {"b.md": text}, f"_disc_update_bundle_pm_queued b.md {_PM_ID}")
    assert module["files"]["b.md"] == (
        f"# R\n\n## Status\nrunning\n\n## PM Task\n\n{_PM_ID}\n\n## Notes\nn\n".encode()
    )
    # The heredoc matched only "none" and left the trailing spaces on a line of their own.
    assert f"{_PM_ID}\n  \n".encode() in reference["files"]["b.md"]


def test_empty_pm_task_is_filled_without_touching_the_next_heading(tmp_path: pathlib.Path) -> None:
    text = "# R\n\n## Status\n\nopen\n\n## PM Task\n\n## Target Version\n\nv9.9.9\n"
    module, reference = _both(tmp_path, {"b.md": text}, f"_disc_update_bundle_pm_queued b.md {_PM_ID}")
    assert module["files"]["b.md"] == (
        f"# R\n\n## Status\nrunning\n\n## PM Task\n{_PM_ID}\n\n## Target Version\n\nv9.9.9\n".encode()
    )
    assert b"## Target Version" not in reference["files"]["b.md"]


@pytest.mark.parametrize("marker", ["W", "A", "B"])
def test_cache_line_with_another_marker_is_flipped_in_place(tmp_path: pathlib.Path, marker: str) -> None:
    text = _cache(f"- [{marker}] {_ITEM} — desc")
    module, reference = _both(tmp_path, {"q.md": text}, f"_disc_mark_cache_bundled q.md {_ITEM}")
    assert module["files"]["q.md"] == _cache(f"- [x] {_ITEM} — desc").encode()
    _assert_differs(module, reference)


def test_cache_flip_marks_every_duplicate_line(tmp_path: pathlib.Path) -> None:
    text = _cache(f"- [ ] {_ITEM} — first", f"- [ ] {_ITEM} — second")
    module, reference = _both(tmp_path, {"q.md": text}, f"_disc_mark_cache_bundled q.md {_ITEM}")
    assert module["files"]["q.md"] == _cache(f"- [x] {_ITEM} — first", f"- [x] {_ITEM} — second").encode()
    _assert_differs(module, reference)


def test_cache_flip_never_marks_a_key_that_merely_starts_with_the_item(tmp_path: pathlib.Path) -> None:
    text = _cache(f"- [ ] {_ITEM}-followup")
    module, reference = _both(tmp_path, {"q.md": text}, f"_disc_mark_cache_bundled q.md {_ITEM}")
    assert module["files"]["q.md"] == _cache(f"- [ ] {_ITEM}-followup", f"- [x] {_ITEM}").encode()
    assert reference["files"]["q.md"] == _cache(f"- [x] {_ITEM}-followup").encode()


@pytest.mark.parametrize("heading", ["## model\nmodel-a", "## Model: model-a"], ids=["lowercase", "inline"])
def test_model_read_accepts_lowercase_and_inline_headings(tmp_path: pathlib.Path, heading: str) -> None:
    text = f"## Role\nCODER\n\n{heading}\n\n## State\nWORKING\n"
    module, reference = _both(tmp_path, {"status.md": text}, "narrate_model_mismatch status.md t.json demo")
    assert "stamped 'model-a'" in module["stdout"]
    assert reference["stdout"] == ""


def test_summary_append_accepts_a_lowercase_heading(tmp_path: pathlib.Path) -> None:
    text = "## State\nDONE\n\n## summary\nold\n\n## Needs Human\nno\n"
    module, reference = _both(tmp_path, {"status.md": text}, "_wd_append_summary_finding status.md F")
    assert module["files"]["status.md"] == b"## State\nDONE\n\n## summary\nold\n\nF\n\n## Needs Human\nno\n"
    assert reference["files"]["status.md"] == text.encode()
