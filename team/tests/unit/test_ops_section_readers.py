"""
test_ops_section_readers.py
===========================
Section readers in OVERWATCH, the release-state helper and the ops package
must stop a section's body at the next ``##`` line, so a section whose body
is empty reads as empty -- never as the heading and text of the section that
follows it. Populated values must read exactly as before.

Readers covered:
  scripts/lib/overwatch_protocol.sh
      transient_retry_field   (## Transient Retry; behavior level only --
      zero_progress_field     (## Zero Progress)   the reads are
                              task_status read --field, and the empty and
                              populated shapes are pinned in
                              test_overwatch_harness_module_parity.py)
  scripts/lib/overwatch-checks/check-transient-api-error.sh
      requeue repair's ## Labels read (task_status read --raw; its shapes
      are pinned in test_overwatch_harness_module_parity.py)
  scripts/lib/project_paths.sh
      pp_last_released_version's release-state.md ## Last Released fallback
      (behavior level only -- the read is release_state_section in
      lib/cm/common.sh, the one release-state reader)
  pgai_agent_kanban/ops/write.py
      _read_md_field (generic ``## <header>`` reader, provided by
      pgai_agent_kanban/lib/md_fields.py, where its regex literal lives)

Two levels of coverage:
  * Pattern level -- each reader's regex literal is read out of the shipped
    file, compiled with the flags the file compiles it with, and run against
    empty, populated and edge-shaped fixtures. The same level checks each
    literal ends in the section-body boundary _status_section_re
    (wake_task_state.sh) prints, so these readers and the wake readers cannot
    drift apart.
  * Behavior level -- the real bash function or python function is run on a
    fixture file whose FOLLOWING section would change the observable result
    if it were misread.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from pgai_agent_kanban.ops.write import _read_md_field
from tests.unit.shell_harness import run_bash

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_OVERWATCH_PROTOCOL = _LIB / "overwatch_protocol.sh"
_TRANSIENT_CHECK = _LIB / "overwatch-checks" / "check-transient-api-error.sh"
_PROJECT_PATHS = _LIB / "project_paths.sh"
_OPS_WRITE = _TEAM_DIR / "pgai_agent_kanban" / "ops" / "write.py"
_MD_FIELDS = _TEAM_DIR / "pgai_agent_kanban" / "lib" / "md_fields.py"
_WAKE_TASK_STATE = _LIB / "wake_task_state.sh"

_SCOPED_FILES = [_OVERWATCH_PROTOCOL, _TRANSIENT_CHECK, _PROJECT_PATHS, _OPS_WRITE, _MD_FIELDS]

# The pre-boundary reader shape: "\s*\n(.*?)" lets \s* swallow an empty
# body's blank line so the lazy body starts at the next heading.
_OLD_READER_SHAPE = re.compile(r"\\s\*\\n\(\.\*\?\)")

# Each reader's regex literal, as a (file, extractor, flags, heading as a file
# writes it). The extractor captures the python expression that builds the
# pattern; it is evaluated with ``re`` and ``header`` bound so the pattern is
# exactly what the shipped code compiles.
_SITES = [
    (
        _MD_FIELDS,
        re.compile(r'pattern = re\.compile\(\s*(r"\(\^##\\s\+" \+ re\.escape\(header\) \+ r"[^"]*"),\s*([\w.| ]+),\s*\)'),
        re.M | re.IGNORECASE,
        "## Owner",
    ),
]
_SITE_IDS = ["ops-md-field"]


def _explain(result) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"


def _site_pattern(path: pathlib.Path, extractor: re.Pattern, flags: int) -> tuple[str, re.Pattern]:
    """Return (evaluated pattern source, compiled pattern) for the one reader
    literal *extractor* finds in *path*; fail if it is missing or its flags
    differ from *flags*."""
    matches = list(extractor.finditer(path.read_text(encoding="utf-8")))
    assert len(matches) == 1, f"expected exactly one reader literal in {path.name}, found {len(matches)}"
    expr, flag_expr = matches[0].groups()
    source = eval(expr, {"re": re, "header": "Owner"})  # literal taken from the shipped file
    shipped_flags = eval(flag_expr, {"re": re})
    assert shipped_flags == flags, f"{path.name}: reader flags changed to {flag_expr}"
    return source, re.compile(source, shipped_flags)


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


def _old_shape_hits(path: pathlib.Path) -> list[str]:
    return [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _OLD_READER_SHAPE.search(line)
    ]


# --- Pattern level ------------------------------------------------------------


def test_scoped_files_carry_no_section_reader_of_the_old_shape() -> None:
    # Intentional pattern-scan: every current and future reader in these
    # files must use the section-body boundary; each file is read in full.
    hits = [hit for path in _SCOPED_FILES for hit in _old_shape_hits(path)]
    assert hits == [], "section readers must stop the body at the next '##' line:\n" + "\n".join(hits)


def test_old_shape_sweep_reports_a_seeded_reader(tmp_path: pathlib.Path) -> None:
    seeded = tmp_path / "seeded.sh"
    seeded.write_text("ok=1\nm = re.search(r'^## Labels\\s*\\n(.*?)(\\n+##|\\Z)', text)\n", encoding="utf-8")
    assert _old_shape_hits(seeded) == [
        "seeded.sh:2: m = re.search(r'^## Labels\\s*\\n(.*?)(\\n+##|\\Z)', text)"
    ]


@pytest.mark.parametrize(("path", "extractor", "flags", "written"), _SITES, ids=_SITE_IDS)
def test_reader_regex_ends_in_the_status_section_boundary(
    tmp_path: pathlib.Path, path: pathlib.Path, extractor: re.Pattern, flags: int, written: str
) -> None:
    # Static guard: these readers share the wake readers' body boundary; the
    # behavior tests below are the companion checks.
    tail = _canonical_boundary_tail(tmp_path)
    source, _rx = _site_pattern(path, extractor, flags)
    assert source.endswith(tail), (
        f"{path.name} {written!r} reader diverges from _status_section_re's boundary\n"
        f"  reader: {source}\n  expect: ...{tail}"
    )


@pytest.mark.parametrize(("path", "extractor", "flags", "written"), _SITES, ids=_SITE_IDS)
@pytest.mark.parametrize(
    "shape",
    [
        "{h}\n\n## Summary\nDONE\n\n## Blockers\nnone\n",
        "{h}\n## Summary\nDONE\n",
        "{h}   \n\n\n## Summary\nDONE\n",
        "## Task\nx\n\n{h}\n",
        "## Task\nx\n\n{h}",
    ],
    ids=["blank-line-then-next", "next-heading-directly", "trailing-space-and-blank-lines", "last-section", "last-line-no-newline"],
)
def test_reader_regex_reads_empty_body_as_empty(
    path: pathlib.Path, extractor: re.Pattern, flags: int, written: str, shape: str
) -> None:
    _source, rx = _site_pattern(path, extractor, flags)
    m = rx.search(shape.format(h=written))
    assert m is not None, f"{path.name}: heading {written!r} must still be found"
    assert m.group(2).strip() == "", f"{path.name}: empty {written!r} read as {m.group(2)!r}"


@pytest.mark.parametrize(("path", "extractor", "flags", "written"), _SITES, ids=_SITE_IDS)
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("{h}\nv1.2.3\n\n## Summary\nDONE\n", "v1.2.3"),
        ("## Task\nx\n\n{h}\nvalue one\n", "value one"),
        ("{h}\n\nspaced value\n\n\n## Next\nx\n", "spaced value"),
        ("{h}\r\nwindows\r\n\r\n## Next\r\nx\r\n", "windows"),
        ("{h}\nkey_a: 1\nkey_b: 2\n\n## Next\nx\n", "key_a: 1\nkey_b: 2"),
    ],
    ids=["single-value", "last-section", "leading-blank-lines", "crlf", "multi-line"],
)
def test_reader_regex_reads_populated_body_as_before(
    path: pathlib.Path, extractor: re.Pattern, flags: int, written: str, shape: str, expected: str
) -> None:
    _source, rx = _site_pattern(path, extractor, flags)
    m = rx.search(shape.format(h=written))
    assert m is not None and m.group(2).strip().replace("\r", "") == expected, (
        f"{path.name}: populated {written!r} misread: {m.group(2) if m else None!r}"
    )


# --- overwatch_protocol.sh: transient_retry_field / zero_progress_field -------


def _field(tmp_path: pathlib.Path, func: str, status_text: str, field: str, default: str) -> str:
    status = tmp_path / "status.md"
    status.write_text(status_text, encoding="utf-8")
    result = run_bash(tmp_path, f"source '{_OVERWATCH_PROTOCOL}' && {func} '{status}' '{field}' '{default}'")
    assert result.returncode == 0, _explain(result)
    return result.stdout.strip()


def test_empty_transient_retry_reads_default_not_the_zero_progress_record(tmp_path: pathlib.Path) -> None:
    text = (
        "## State\nWORKING\n\n## Transient Retry\n\n"
        "## Zero Progress\nreason_code: stale_active_orphan\ncount: 2\nlimit: 3\n"
    )
    assert _field(tmp_path, "transient_retry_field", text, "reason_code", "unset") == "unset"
    assert _field(tmp_path, "transient_retry_field", text, "retry_count", "0") == "0"


def test_empty_zero_progress_reads_default_not_the_transient_retry_record(tmp_path: pathlib.Path) -> None:
    text = (
        "## State\nWORKING\n\n## Zero Progress\n\n"
        "## Transient Retry\nreason_code: transient_api_error\nretry_count: 2\nretry_limit: 3\n"
    )
    assert _field(tmp_path, "zero_progress_field", text, "reason_code", "unset") == "unset"
    assert _field(tmp_path, "zero_progress_field", text, "limit", "9") == "9"


@pytest.mark.parametrize(
    ("func", "heading", "field", "value"),
    [
        ("transient_retry_field", "## Transient Retry", "retry_count", "2"),
        ("zero_progress_field", "## Zero Progress", "count", "1"),
    ],
    ids=["transient-retry", "zero-progress"],
)
def test_populated_retry_records_read_as_before(
    tmp_path: pathlib.Path, func: str, heading: str, field: str, value: str
) -> None:
    populated = f"## State\nWORKING\n\n{heading}\nreason_code: x\n{field}: {value}\n\n## Summary\n{field}: 99\n"
    assert _field(tmp_path, func, populated, field, "d") == value
    assert _field(tmp_path, func, f"{heading}\nnone\n\n## Summary\n{field}: 99\n", field, "d") == "d"
    assert _field(tmp_path, func, "## State\nWORKING\n", field, "d") == "d"


# --- project_paths.sh: pp_last_released_version release-state fallback -------


def _last_released(tmp_path: pathlib.Path, release_state: str) -> str:
    kanban_root = tmp_path / "kanban"
    proj_dir = kanban_root / "projects" / "docproject"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.cfg").write_text("[project]\nproject_name=docproject\n", encoding="utf-8")
    (proj_dir / "release-state.md").write_text(release_state, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"source '{_PROJECT_PATHS}' && pp_last_released_version docproject",
        extra_env={"KANBAN_ROOT": str(kanban_root)},
    )
    assert result.returncode == 0, _explain(result)
    return result.stdout.strip()


def test_empty_last_released_reads_as_none_not_the_next_field(tmp_path: pathlib.Path) -> None:
    as_none = _last_released(tmp_path, "## Last Released\nnone\n\n## Active RC\nv9.9.9\n")
    empty = _last_released(tmp_path, "## Last Released\n\n## Active RC\nv9.9.9\n")
    assert empty == as_none, f"empty ## Last Released read as {empty!r}; 'none' reads as {as_none!r}"
    assert "9.9.9" not in empty


def test_populated_last_released_reads_as_before(tmp_path: pathlib.Path) -> None:
    text = "## Active RC\nnone\n\n## Last Released\nv2.3.4\n\n## RC Opened At\nnone\n"
    assert _last_released(tmp_path, text) == "v2.3.4"


# --- ops/write.py: _read_md_field --------------------------------------------


def test_empty_md_field_reads_empty_not_the_next_heading(tmp_path: pathlib.Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("## Owner\n\n## Role\nCODER\n\n## Participant\nClaude\n", encoding="utf-8")
    assert _read_md_field(readme, "Owner") == ""
    assert (_read_md_field(readme, "Owner") or _read_md_field(readme, "Participant")) == "Claude"


def test_populated_md_field_reads_as_before(tmp_path: pathlib.Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("## Owner\n\n  Claude  \nsecond\n\n## Role\nCODER\n", encoding="utf-8")
    assert _read_md_field(readme, "Owner") == "Claude"
    assert _read_md_field(readme, "role") == "CODER"
    assert _read_md_field(readme, "Missing") == ""
