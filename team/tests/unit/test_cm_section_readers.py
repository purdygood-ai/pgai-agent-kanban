"""
test_cm_section_readers.py
==========================
Section readers in the CM release scripts and the blocked-task guard
verifier must stop a section's body at the next ``##`` line, so a section
whose body is empty reads as empty (or as the field's documented default)
-- never as the heading and text of the section that follows it. Populated
values must read exactly as before.

Readers covered:
  scripts/cm/open-doc.sh
      write_active_rc's ``## Last Released`` read (empty -> ``none``),
      through release_state_section (lib/cm/common.sh), and its write
      through the release-state writer (cm/write_rc_state.py)
  scripts/cm/finalize.sh
      read_md_field (``## Artifact Name`` / ``## Output Formats``), which
      also stops at a ``---`` horizontal rule
      the requirements-dir ``## Target Version`` scan
  scripts/cm/promote_bundled_items.py
      _TARGET_VERSION_SECTION_RE / find_requirement_by_version
  scripts/verify-blocked-task-guard.sh
      carries no section reader of its own (the guard's ``## State`` /
      ``## Needs Human`` reads are pgai_agent_kanban.overwatch.block_storm's,
      covered in test_wake_path_section_readers.py); the static scan below
      keeps it that way

Every test runs the shipped code: a bash function is cut out of its script
by name and run, a python heredoc is cut out of its script and run, and the
python module is imported from its file. Fixtures live under pytest's
tmp_path; no live task folder or kanban install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

from pgai_agent_kanban.cm.read_state_field import read_state_section
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_SCRIPTS = _TEAM_DIR / "scripts"
_OPEN_DOC = _SCRIPTS / "cm" / "open-doc.sh"
_FINALIZE = _SCRIPTS / "cm" / "finalize.sh"
_PROMOTE = _SCRIPTS / "cm" / "promote_bundled_items.py"
_GUARD_VERIFIER = _SCRIPTS / "verify-blocked-task-guard.sh"
_WAKE_TASK_STATE = _SCRIPTS / "lib" / "wake_task_state.sh"
_CM_COMMON = _SCRIPTS / "lib" / "cm" / "common.sh"

# The pre-boundary reader shape: "\s*\n(.*?)" lets \s* swallow an empty
# body's blank line so the lazy body starts at the next heading.
_OLD_READER_SHAPE = re.compile(r"\\s\*\\n\(\.\*\?\)")


def _explain(result) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"


def _bash_function(path: pathlib.Path, name: str) -> str:
    """Return the source of the top-level bash function *name* in *path*:
    from its ``name() {`` line through the first column-0 ``}``."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{re.escape(name)}\(\)\s*\{{", line)), None)
    assert start is not None, f"{path.name}: function {name}() not found"
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1]) + "\n"


def _write_fn_file(tmp_path: pathlib.Path, path: pathlib.Path, name: str) -> pathlib.Path:
    fn_file = tmp_path / f"{name}.sh"
    fn_file.write_text(_bash_function(path, name), encoding="utf-8")
    return fn_file


# --- Static guards ------------------------------------------------------------


@pytest.mark.parametrize("path", [_OPEN_DOC, _FINALIZE, _PROMOTE, _GUARD_VERIFIER], ids=lambda p: p.name)
def test_scoped_file_carries_no_section_reader_of_the_old_shape(path: pathlib.Path) -> None:
    # Intentional pattern-scan: every reader in these files must use the
    # section-body boundary; each file is read in full.
    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _OLD_READER_SHAPE.search(line)
    ]
    assert hits == [], "section readers must stop the body at the next '##' line:\n" + "\n".join(hits)


def _canonical_boundary_tail(tmp_path: pathlib.Path) -> str:
    """What _status_section_re prints after its heading argument."""
    result = run_bash(
        tmp_path,
        f"""set -euo pipefail
eval "$(awk '/^_status_section_re\\(\\)/{{f=1}} f{{print}} f&&/^}}/{{exit}}' '{_WAKE_TASK_STATE}')"
_status_section_re 'HEADING'
""",
    )
    assert result.returncode == 0, _explain(result)
    assert result.stdout.startswith("(^HEADING"), _explain(result)
    return result.stdout[len("(^HEADING"):]


def test_target_version_reader_ends_in_the_status_section_boundary(tmp_path: pathlib.Path) -> None:
    tail = _canonical_boundary_tail(tmp_path)
    promote = _import_promote()
    assert promote._TARGET_VERSION_SECTION_RE.pattern.endswith(tail), promote._TARGET_VERSION_SECTION_RE.pattern
    assert promote._TARGET_VERSION_SECTION_RE.flags & re.IGNORECASE


# --- open-doc.sh: write_active_rc preserves ## Last Released ------------------


def _open_doc_rewrite(tmp_path: pathlib.Path, release_state: str) -> str:
    kanban_root = tmp_path / "kanban"
    proj_dir = kanban_root / "projects" / "docproject"
    proj_dir.mkdir(parents=True, exist_ok=True)
    rs_path = proj_dir / "release-state.md"
    rs_path.write_text(release_state, encoding="utf-8")
    fn_file = _write_fn_file(tmp_path, _OPEN_DOC, "write_active_rc")
    result = run_bash(
        tmp_path,
        f"set -euo pipefail\nsource '{_CM_COMMON}'\nsource '{fn_file}'\nwrite_active_rc\n",
        extra_env={"KANBAN_ROOT": str(kanban_root), "PROJECT_NAME": "docproject", "TARGET_VERSION": "v1.5.0"},
    )
    assert result.returncode == 0, _explain(result)
    return rs_path.read_text(encoding="utf-8")


def _field(tmp_path: pathlib.Path, release_state: str, heading: str) -> "str | None":
    """*heading*'s value in *release_state*, read through the one release-state
    reader as a section ending at the next '##' heading (None: absent)."""
    check = tmp_path / "written-release-state.md"
    check.write_text(release_state, encoding="utf-8")
    return read_state_section(str(check), heading)


@pytest.mark.parametrize(
    "release_state",
    [
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n",
        "## Last Released\n## RC Opened At\nnone\n",
        "## Last Released   \n\n\n## RC Opened At\nnone\n",
        "## Active RC\nnone\n\n## Last Released\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "last-section"],
)
def test_open_doc_empty_last_released_is_preserved_as_none(tmp_path: pathlib.Path, release_state: str) -> None:
    written = _open_doc_rewrite(tmp_path, release_state)
    assert _field(tmp_path, written, "Last Released") == "none", written
    assert _field(tmp_path, written, "Active RC") == "v1.5.0", written
    assert written.count("## RC Opened At") == 1, written
    assert written.count("## Last Released") == 1, written


@pytest.mark.parametrize(
    "release_state",
    [
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\nv1.4.2\n\n## RC Opened At\nnone\n",
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n## RC Opened By Task\nnone\n\n## Last Released\nv1.4.2\n",
        "# Release State\n\n## Active RC\nnone\n\n## Last Released\n\nv1.4.2\n\n## RC Opened At\nnone\n",
        "## Active RC\r\nnone\r\n\r\n## Last Released\r\nv1.4.2\r\n\r\n## RC Opened At\r\nnone\r\n",
    ],
    ids=["middle-section", "last-section", "leading-blank", "crlf"],
)
def test_open_doc_populated_last_released_is_preserved_as_before(tmp_path: pathlib.Path, release_state: str) -> None:
    written = _open_doc_rewrite(tmp_path, release_state)
    assert _field(tmp_path, written, "Last Released") == "v1.4.2", written
    assert _field(tmp_path, written, "Active RC") == "v1.5.0", written
    assert _field(tmp_path, written, "RC Opened At") == "none", written
    assert _field(tmp_path, written, "RC Opened By Task") == "none", written


def test_open_doc_missing_last_released_writes_none(tmp_path: pathlib.Path) -> None:
    written = _open_doc_rewrite(tmp_path, "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nv9.9.9\n")
    assert _field(tmp_path, written, "Last Released") == "none", written
    assert _field(tmp_path, written, "RC Opened At") == "none", written


def test_open_doc_keeps_appended_event_blocks(tmp_path: pathlib.Path) -> None:
    """Only the four fields change; an appended event block survives the open."""
    event = "## HALT Event\n- Timestamp: 2026-01-01T00:00:00Z\n- Reason: operator\n"
    before = (
        "# Release State\n\n## Active RC\nnone\n\n## RC Opened At\nnone\n\n"
        "## RC Opened By Task\nnone\n\n## Last Released\nv1.4.2\n\n" + event
    )
    written = _open_doc_rewrite(tmp_path, before)
    assert written == before.replace("## Active RC\nnone\n", "## Active RC\nv1.5.0\n", 1), written


# --- finalize.sh: read_md_field -----------------------------------------------


def _read_md_field(tmp_path: pathlib.Path, doc_text: str, field_pattern: str) -> str:
    doc = tmp_path / "requirements.md"
    doc.write_text(doc_text, encoding="utf-8")
    fn_file = _write_fn_file(tmp_path, _FINALIZE, "read_md_field")
    result = run_bash(tmp_path, f"set -euo pipefail\nsource '{fn_file}'\nread_md_field '{doc}' '{field_pattern}'\n")
    assert result.returncode == 0, _explain(result)
    return result.stdout.rstrip("\n")


@pytest.mark.parametrize(
    "doc_text",
    [
        "# Req\n\n## Artifact Name\n\n## Output Formats\n- markdown\n",
        "## Artifact Name\n## Output Formats\nmarkdown\n",
        "## Artifact Name  \n\n\n## Output Formats\nmarkdown\n",
        "## Output Formats\nmarkdown\n\n## Artifact Name\n",
        "## Artifact Name\n\n---\n\n## Output Formats\nmarkdown\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "last-section", "rule-then-next"],
)
def test_finalize_empty_artifact_name_reads_empty_not_the_next_section(tmp_path: pathlib.Path, doc_text: str) -> None:
    assert _read_md_field(tmp_path, doc_text, r"Artifact\s*Name") == ""


@pytest.mark.parametrize(
    ("doc_text", "pattern", "expected"),
    [
        ("## Artifact Name\nfield-guide\n\n## Output Formats\n- pdf\n", r"Artifact\s*Name", "field-guide"),
        ("## artifact name\n\n  field-guide  \n\n## Output Formats\n- pdf\n", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\nfield-guide\n", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\r\nfield-guide\r\n\r\n## Next\r\nx\r\n", r"Artifact\s*Name", "field-guide"),
        ("## Output Formats\n- markdown\n- pdf\n\n## Notes\nx\n", r"Output\s*Formats?", "- markdown\n- pdf"),
        ("## Artifact Name\nfield-guide\n\n---\n\nTrailing prose\n", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\nfield-guide\n---\n", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\nfield-guide\n----  \nTrailing prose\n", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\nfield-guide\n---", r"Artifact\s*Name", "field-guide"),
        ("## Artifact Name\nfield-guide\n----- note\n\n## Next\nx\n", r"Artifact\s*Name", "field-guide\n----- note"),
        ("## Other\nx\n", r"Artifact\s*Name", ""),
    ],
    ids=[
        "single-value",
        "lower-case-heading-and-padding",
        "last-section",
        "crlf",
        "multi-line-list",
        "stops-at-rule-before-prose",
        "stops-at-rule-at-end",
        "stops-at-long-rule-with-trailing-space",
        "stops-at-rule-without-final-newline",
        "dashes-with-text-are-body",
        "absent-heading",
    ],
)
def test_finalize_populated_md_field_reads_as_before(
    tmp_path: pathlib.Path, doc_text: str, pattern: str, expected: str
) -> None:
    assert _read_md_field(tmp_path, doc_text, pattern).replace("\r", "") == expected


# --- finalize.sh: requirements-dir ## Target Version scan ---------------------


def _finalize_target_version_scan() -> str:
    """The python heredoc finalize.sh runs to find the requirement doc whose
    ## Target Version matches TARGET_VERSION."""
    lines = _FINALIZE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if '_meta_req_file="$(python3 - "$_meta_req_dir"' in line)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "PY")
    return "\n".join(lines[start + 1 : end]) + "\n"


def _scan(tmp_path: pathlib.Path, docs: dict[str, str], version: str) -> str:
    req_dir = tmp_path / "requirements"
    req_dir.mkdir(parents=True)
    for name, text in docs.items():
        (req_dir / name).write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-", str(req_dir), version],
        input=_finalize_target_version_scan(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, _explain(result)
    return pathlib.Path(result.stdout.strip()).name if result.stdout.strip() else ""


def test_finalize_scan_skips_empty_target_version_whose_next_section_holds_the_version(tmp_path: pathlib.Path) -> None:
    docs = {"a-empty.md": "## Target Version\n\n## Previous Version\nv1.5.0\n"}
    assert _scan(tmp_path, docs, "v1.5.0") == ""


def test_finalize_scan_finds_populated_target_version_as_before(tmp_path: pathlib.Path) -> None:
    docs = {
        "a-empty.md": "## Target Version\n\n## Previous Version\nv1.5.0\n",
        "b-match.md": "# Req\n\n## Target Version\nv1.5.0\n\n## Artifact Name\nguide\n",
        "c-other.md": "## Target Version\nv1.6.0\n",
    }
    assert _scan(tmp_path, docs, "1.5.0") == "b-match.md"
    assert _scan(tmp_path / "second", {"z.md": "## target version\n\n  v1.5.0  \n"}, "v1.5.0") == "z.md"


# --- promote_bundled_items.py: _TARGET_VERSION_SECTION_RE ---------------------


def _import_promote():
    spec = importlib.util.spec_from_file_location("promote_bundled_items_under_test", _PROMOTE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.parametrize(
    "text",
    [
        "## Target Version\n\n## Previous Version\nv1.5.0\n",
        "## Target Version\n## Previous Version\nv1.5.0\n",
        "## Target Version  \n\n\n## Previous Version\nv1.5.0\n",
        "## Notes\nx\n\n## Target Version\n",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "last-section"],
)
def test_promote_empty_target_version_reads_empty(text: str) -> None:
    m = _import_promote()._TARGET_VERSION_SECTION_RE.search(text)
    assert m is not None and m.group(2).strip() == "", m.group(2) if m else None


def test_promote_find_requirement_skips_empty_target_version(tmp_path: pathlib.Path) -> None:
    promote = _import_promote()
    (tmp_path / "a-empty.md").write_text("## Target Version\n\n## Previous Version\nv1.5.0\n", encoding="utf-8")
    assert promote.find_requirement_by_version(tmp_path, "v1.5.0") is None


def test_promote_find_requirement_matches_populated_target_version_as_before(tmp_path: pathlib.Path) -> None:
    promote = _import_promote()
    (tmp_path / "a-empty.md").write_text("## Target Version\n\n## Previous Version\nv1.5.0\n", encoding="utf-8")
    (tmp_path / "b-match.md").write_text("## Target Version\nv1.5.0\n\n## Goal\nx\n", encoding="utf-8")
    (tmp_path / "c-lower.md").write_text("## target version\n\n  v1.7.0  \n", encoding="utf-8")
    assert promote.find_requirement_by_version(tmp_path, "1.5.0") == tmp_path / "b-match.md"
    assert promote.find_requirement_by_version(tmp_path, "v1.7.0") == tmp_path / "c-lower.md"
    assert promote.find_requirement_by_version(tmp_path, "v1.8.0") is None
