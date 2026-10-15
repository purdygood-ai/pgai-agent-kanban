"""
test_wake_path_section_readers.py
=================================
Section readers on the wake and discovery path must stop a section's body at
the next ``##`` line, so a section whose body is empty reads as empty --
never as the heading and text of the section that follows it. Populated
values must read exactly as before.

Readers covered (team/scripts/lib/, and the module the wake_driver.sh
blocked-task dispatch guard calls):
  block_storm (pgai_agent_kanban.overwatch)
                           blocked-task dispatch guard: ## State, ## Needs Human
                           (behavior level; the reads are task_status's own)
  discovery_guards.sh      _disc_get_release_field, _disc_task_terminal (## State;
                           behavior level -- the read is
                           pgai_agent_kanban.discovery.items task-state,
                           which reads through task_status)
  wake_task_id_gate.sh     check_prerequisites: ## Prerequisites, prerequisite ## State
  wake_autonomous_scan.sh  get_release_state_field
                           (_disc_get_release_field and get_release_state_field
                           read through release_state_section in
                           lib/cm/common.sh, so they are covered at behavior
                           level only)
  wake_lifecycle.sh        close_intake_on_finalize_report: ## Inputs (path + census)
  wake_readme_meta.sh      get_role_from_readme, get_assigned_agent_from_readme
  wake_driver_finalize.sh  narrate_model_mismatch: ## Model (behavior level
                           only -- the read is task_status read --raw; its
                           empty/populated variants are pinned in
                           test_discovery_finalize_module_parity.py)

Two levels of coverage:
  * Pattern level -- each reader's regex literal is read out of the shipped
    lib, compiled exactly as the lib compiles it, and run against empty,
    populated and edge-shaped fixtures. The same level checks that every
    literal carries the section-body boundary _status_section_re
    (wake_task_state.sh) produces, so the readers cannot drift apart.
  * Behavior level -- the real bash function (sourced, or extracted by name
    with its collaborators stubbed) or the real python heredoc is run against
    a fixture whose FOLLOWING section would change the observable result if
    it were misread, so the assertion discriminates a correct boundary from
    one that runs past it.

Every fixture lives under pytest's tmp_path; no live task folder or kanban
install is read or written.

Naming: function names describe behavior under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines", Anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import textwrap

import pytest

from pgai_agent_kanban.overwatch.block_storm import scan_human_gated_blocks
from tests.unit.shell_harness import run_bash

# Paths are relative to team/, run_bash's working directory.
_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LIB = _TEAM_DIR / "scripts" / "lib"
_WAKE_DRIVER = _LIB / "wake_driver.sh"
_DISCOVERY_GUARDS = _LIB / "discovery_guards.sh"
_TASK_ID_GATE = _LIB / "wake_task_id_gate.sh"
_AUTONOMOUS_SCAN = _LIB / "wake_autonomous_scan.sh"
_LIFECYCLE = _LIB / "wake_lifecycle.sh"
_README_META = _LIB / "wake_readme_meta.sh"
_FINALIZE = _LIB / "wake_driver_finalize.sh"
_WAKE_TASK_STATE = _LIB / "wake_task_state.sh"

_SCOPED_LIBS = [
    _WAKE_DRIVER,
    _DISCOVERY_GUARDS,
    _TASK_ID_GATE,
    _AUTONOMOUS_SCAN,
    _LIFECYCLE,
    _README_META,
    _FINALIZE,
]

_TASK_ID = "CODER-20260101-001-demo"

# (lib, heading as written in the regex literal, heading as a file writes it,
#  value bound to ``field`` when the literal is an rf-string)
_SITES = [
    (_TASK_ID_GATE, "## Prerequisites", "## Prerequisites", None),
    (_TASK_ID_GATE, "## State", "## State", None),
    (_LIFECYCLE, r"##[^\S\n]+Inputs", "## Inputs", None),
    (_README_META, "## Role", "## Role", None),
    (_README_META, "## Assigned Agent", "## Assigned Agent", None),
]
_SITE_IDS = [f"{lib.name}:{written}" for lib, _h, written, _f in _SITES]

# The boundary's body group, as it appears in a raw-string literal.
_BODY_GROUP = r"((?:\n(?!##)[^\n]*)*?)"
_LITERAL_RE = re.compile(r"re\.search\(\s*(rf?)'(\(\^[^']*)'\s*,\s*\w+\s*,\s*flags=([\w.| ]+?)\s*\)")


def _explain(result) -> str:
    return f"rc={result.returncode}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"


def _site_literals(lib: pathlib.Path, head: str) -> list[tuple[str, str, str]]:
    """Every (prefix, literal body, flags) in *lib* whose pattern opens with
    ``(^`` + *head* and carries the section-body boundary."""
    found = []
    for m in _LITERAL_RE.finditer(lib.read_text(encoding="utf-8")):
        prefix, body, flags = m.groups()
        if body.startswith("(^" + head) and _BODY_GROUP in body:
            found.append((prefix, body, flags.replace(" ", "")))
    return found


def _compile_site(lib: pathlib.Path, head: str, field: str | None) -> re.Pattern:
    literals = _site_literals(lib, head)
    assert literals, f"no section-reader regex opening with {head!r} found in {lib}"
    prefix, body, flags = literals[0]
    assert flags == "re.M", f"{lib}: {head!r} reader must compile with re.M only, found {flags}"
    pattern = eval(f"{prefix}'{body}'", {"re": re, "field": field})  # literal taken from the lib
    return re.compile(pattern, re.M)


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


def _heredoc_containing(lib: pathlib.Path, marker: str) -> str:
    """Return the python source of the one ``<<'PY'`` heredoc in *lib* that
    contains *marker*."""
    bodies = [
        m.group(1)
        for m in re.finditer(r"<<'PY'\n(.*?)\n[ \t]*PY[ \t]*$", lib.read_text(encoding="utf-8"), re.S | re.M)
        if marker in m.group(1)
    ]
    assert len(bodies) == 1, f"expected one heredoc containing {marker!r} in {lib}, found {len(bodies)}"
    return bodies[0]


def _run_heredoc(source: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-", *args], input=source, capture_output=True, text=True, timeout=30, cwd=str(_TEAM_DIR)
    )


def _extract_fn(name: str, lib: pathlib.Path) -> str:
    """Bash that defines function *name* from *lib* (brace-depth extraction),
    without running the rest of the file."""
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^{name}\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) {{ found=0; exit }} }}
                }}
            }}
        ' '{lib}')"
    """)


# --- Pattern level ------------------------------------------------------------


def test_scoped_libs_carry_no_section_reader_of_the_old_shape() -> None:
    old_shape = re.compile(r"\\s\*\\n\(\.\*\?\)")
    hits = [
        f"{lib.name}:{n}: {line.strip()}"
        for lib in _SCOPED_LIBS
        for n, line in enumerate(lib.read_text(encoding="utf-8").splitlines(), 1)
        if old_shape.search(line)
    ]
    assert hits == [], "section readers must stop the body at the next '##' line:\n" + "\n".join(hits)


@pytest.mark.parametrize(("lib", "head", "written", "field"), _SITES, ids=_SITE_IDS)
def test_reader_regex_uses_the_status_section_boundary(
    tmp_path: pathlib.Path, lib: pathlib.Path, head: str, written: str, field: str | None
) -> None:
    tail = _canonical_boundary_tail(tmp_path)
    literals = _site_literals(lib, head)
    assert literals, f"no section-reader regex opening with {head!r} in {lib}"
    for prefix, body, _flags in literals:
        evaluated = eval(f"{prefix}'{body}'", {"re": re, "field": field or "X"})
        assert evaluated.endswith(tail), (
            f"{lib.name} {head!r} reader diverges from _status_section_re's boundary\n"
            f"  reader: {evaluated}\n  expect: (^<heading>{tail}"
        )


@pytest.mark.parametrize(("lib", "head", "written", "field"), _SITES, ids=_SITE_IDS)
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
    lib: pathlib.Path, head: str, written: str, field: str | None, shape: str
) -> None:
    rx = _compile_site(lib, head, field)
    m = rx.search(shape.format(h=written))
    assert m is not None, f"{lib.name}: heading {written!r} must still be found"
    assert m.group(2).strip() == "", f"{lib.name}: empty {written!r} read as {m.group(2)!r}"


@pytest.mark.parametrize(("lib", "head", "written", "field"), _SITES, ids=_SITE_IDS)
@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("{h}\nBLOCKED\n\n## Summary\nDONE\n", "BLOCKED"),
        ("## Task\nx\n\n{h}\nvalue one\n", "value one"),
        ("{h}\n\nspaced value\n\n\n## Next\nx\n", "spaced value"),
        ("{h}\r\nwindows\r\n\r\n## Next\r\nx\r\n", "windows"),
        ("{h}\n- line a\n- line b\n\n### Sub\nz\n", "- line a\n- line b"),
    ],
    ids=["single-value", "last-section", "leading-blank-lines", "crlf", "multi-line-stops-at-subheading"],
)
def test_reader_regex_reads_populated_body_as_before(
    lib: pathlib.Path, head: str, written: str, field: str | None, shape: str, expected: str
) -> None:
    rx = _compile_site(lib, head, field)
    text = shape.format(h=written)
    m = rx.search(text)
    assert m is not None and m.group(2).strip().replace("\r", "") == expected, (
        f"{lib.name}: populated {written!r} misread: {m.group(2) if m else None!r}"
    )


@pytest.mark.parametrize(("lib", "head", "written", "field"), _SITES, ids=_SITE_IDS)
def test_reader_regex_misses_absent_heading_and_same_named_subheading(
    lib: pathlib.Path, head: str, written: str, field: str | None
) -> None:
    rx = _compile_site(lib, head, field)
    assert rx.search("## Other\nvalue\n") is None, lib.name
    assert rx.search(f"## Other\nx\n\n#{written}\nvalue\n") is None, f"{lib.name}: '#{written}' is not the section"


# --- wake_task_id_gate.sh: check_prerequisites --------------------------------


def _run_prereqs(tmp_path: pathlib.Path, readme_text: str, prereq_status: dict[str, str]):
    tasks = tmp_path / "tasks"
    for task_id, status_text in prereq_status.items():
        (tasks / task_id).mkdir(parents=True, exist_ok=True)
        (tasks / task_id / "status.md").write_text(status_text, encoding="utf-8")
    tasks.mkdir(exist_ok=True)
    readme = tmp_path / "README.md"
    readme.write_text(readme_text, encoding="utf-8")
    return run_bash(
        tmp_path,
        f"""set -uo pipefail
TASKS_ROOT='{tasks}'
source '{_TASK_ID_GATE}'
check_prerequisites '{readme}'
echo "rc=$?"
""",
    )


def test_prerequisites_empty_section_means_none_not_next_section(tmp_path: pathlib.Path) -> None:
    readme = "# Task\n\n## Prerequisites\n\n## Release Version\nv1.2.3\n\n## Notes\n- CODER-20260101-009-other\n"
    result = _run_prereqs(tmp_path, readme, {})
    assert result.stdout.strip() == "rc=0", "an empty ## Prerequisites means no prerequisites\n" + _explain(result)


def test_prerequisite_with_empty_state_reads_empty_state(tmp_path: pathlib.Path) -> None:
    readme = f"# Task\n\n## Prerequisites\n- {_TASK_ID}\n\n## Notes\nx\n"
    status = "## Task\nx\n\n## State\n\n## Summary\nDONE\n\n## Blockers\nnone\n"
    result = _run_prereqs(tmp_path, readme, {_TASK_ID: status})
    assert result.stdout.splitlines() == [f"{_TASK_ID}:", "rc=1"], (
        "an empty prerequisite ## State is unsatisfied and reads empty, never the next section\n" + _explain(result)
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [("DONE", ["rc=0"]), ("WONT-DO", ["rc=0"]), ("WORKING", [f"{_TASK_ID}:WORKING", "rc=1"])],
)
def test_prerequisite_populated_state_reads_as_before(tmp_path: pathlib.Path, state: str, expected: list[str]) -> None:
    readme = f"# Task\n\n## Prerequisites\n- {_TASK_ID}\n\n## Notes\nx\n"
    result = _run_prereqs(tmp_path, readme, {_TASK_ID: f"## State\n{state}\n\n## Summary\nx\n"})
    assert result.stdout.splitlines() == expected, _explain(result)


def test_prerequisites_populated_list_reads_every_entry(tmp_path: pathlib.Path) -> None:
    other = "CODER-20260101-002-demo"
    readme = f"# Task\n\n## Prerequisites\n- {_TASK_ID}\n- {other}\n\n## Release Version\nv1.2.3\n"
    result = _run_prereqs(
        tmp_path, readme, {_TASK_ID: "## State\nDONE\n", other: "## State\nBACKLOG\n\n## Summary\nx\n"}
    )
    assert result.stdout.splitlines() == [f"{other}:BACKLOG", "rc=1"], _explain(result)


# --- discovery_guards.sh --------------------------------------------------------


def _run_disc_terminal(tmp_path: pathlib.Path, status_text: str):
    tasks = tmp_path / "tasks"
    (tasks / _TASK_ID).mkdir(parents=True)
    (tasks / _TASK_ID / "status.md").write_text(status_text, encoding="utf-8")
    return run_bash(
        tmp_path,
        f"""set -euo pipefail
pp_tasks_dir() {{ echo '{tasks}'; }}
source '{_LIB}/pp_run_ops.sh'
{_extract_fn("_disc_task_terminal", _DISCOVERY_GUARDS)}
_disc_task_terminal demo '{_TASK_ID}'
""",
    )


def test_disc_task_terminal_empty_state_is_unparseable_not_next_section(tmp_path: pathlib.Path) -> None:
    result = _run_disc_terminal(tmp_path, "## State\n\n## Summary\nDONE\n")
    assert result.returncode == 0, _explain(result)
    assert result.stdout == "UNPARSEABLE\n", "an empty ## State must not read the next section\n" + _explain(result)


@pytest.mark.parametrize("state", ["DONE", "WONT-DO", "WORKING", "BLOCKED"])
def test_disc_task_terminal_populated_state_reads_as_before(tmp_path: pathlib.Path, state: str) -> None:
    result = _run_disc_terminal(tmp_path, f"## Task\nx\n\n## State\n{state.lower()}\n\n## Summary\nDONE\n")
    assert result.stdout == f"{state}\n", _explain(result)


_COMMON = _LIB / "cm" / "common.sh"
_RELEASE_FIELD_READERS = [
    ("_disc_get_release_field", _DISCOVERY_GUARDS),
    ("get_release_state_field", _AUTONOMOUS_SCAN),
]


def _run_release_field_reader(tmp_path: pathlib.Path, fn: str, lib: pathlib.Path, field: str, text: str | None):
    """Run *fn* from *lib* against a release-state.md holding *text* (None: no file).

    Prints "[<value>] rc=<status>", the value captured exactly as the
    wake/discovery callers capture it.
    """
    path = tmp_path / "release-state.md"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    call = f"{fn} demo '{field}'" if fn.startswith("_disc") else f"{fn} '{field}'"
    return run_bash(
        tmp_path,
        f"""set -uo pipefail
_CURRENT_PROJECT=demo
pp_release_state() {{ echo '{path}'; }}
source '{_COMMON}'
{_extract_fn(fn, lib)}
value="$({call})"
printf '[%s] rc=%s' "$value" "$?"
""",
    )


@pytest.mark.parametrize(("fn", "lib"), _RELEASE_FIELD_READERS, ids=[f for f, _ in _RELEASE_FIELD_READERS])
@pytest.mark.parametrize(
    ("release_state", "field", "expected"),
    [
        ("## Active RC\n\n## Last Released\nv1.2.3\n", "Active RC", "[] rc=0"),
        ("## Active RC\n## Last Released\nv1.2.3\n", "Active RC", "[] rc=0"),
        ("## Active RC   \n\n\n## Last Released\nv1.2.3\n", "Active RC", "[] rc=0"),
        ("## Last Released\nv1.2.3\n\n## Active RC\n", "Active RC", "[] rc=0"),
        ("## Active RC\nv1.3.0\n\n## Last Released\nv1.2.3\n", "Active RC", "[v1.3.0] rc=0"),
        ("## Last Released\nv1.2.3\n\n## Active RC\nnone\n", "Active RC", "[none] rc=0"),
        ("## Active RC\n\n  v1.3.0  \n\n## Next\nx\n", "Active RC", "[v1.3.0] rc=0"),
        ("## Active RC\r\nv1.3.0\r\n\r\n## Next\r\nx\r\n", "Active RC", "[v1.3.0] rc=0"),
        ("## Active RC\nnone\n\n## Last Released\nv1.2.3\n", "Last Released", "[v1.2.3] rc=0"),
        ("## Active RC\nv1.0.0\n\n## Last Released At\n2026-01-01T00:00:00Z\n", "Last Released", "[] rc=1"),
        ("## Other\nvalue\n", "Active RC", "[] rc=1"),
        ("## Other\nx\n\n### Active RC\nv1.3.0\n", "Active RC", "[] rc=1"),
        (None, "Active RC", "[] rc=1"),
    ],
    ids=[
        "empty", "empty-next-heading-directly", "empty-trailing-space-and-blank-lines", "empty-last-section",
        "populated", "populated-last", "leading-blank-lines", "crlf", "last-released",
        "retired-heading-is-not-the-field", "absent-heading", "same-named-subheading", "missing-file",
    ],
)
def test_release_state_field_readers(
    tmp_path: pathlib.Path, fn: str, lib: pathlib.Path, release_state: str | None, field: str, expected: str
) -> None:
    result = _run_release_field_reader(tmp_path, fn, lib, field, release_state)
    assert result.stdout == expected, _explain(result)


@pytest.mark.parametrize(("fn", "lib"), _RELEASE_FIELD_READERS, ids=[f for f, _ in _RELEASE_FIELD_READERS])
def test_release_state_field_readers_print_nothing_on_failure(tmp_path: pathlib.Path, fn: str, lib: pathlib.Path) -> None:
    """A caller's "|| echo none" fallback must capture exactly "none"."""
    path = tmp_path / "release-state.md"
    path.write_text("## Other\nvalue\n", encoding="utf-8")
    call = f"{fn} demo 'Active RC'" if fn.startswith("_disc") else f"{fn} 'Active RC'"
    result = run_bash(
        tmp_path,
        f"""set -euo pipefail
_CURRENT_PROJECT=demo
pp_release_state() {{ echo '{path}'; }}
source '{_COMMON}'
{_extract_fn(fn, lib)}
printf '[%s]' "$({call} 2>/dev/null || echo none)"
""",
    )
    assert result.returncode == 0, _explain(result)
    assert result.stdout == "[none]", _explain(result)


# --- wake_readme_meta.sh -------------------------------------------------------


@pytest.mark.parametrize(
    ("fn", "readme_text", "expected"),
    [
        ("get_role_from_readme", "## Role\n\n## Assigned Agent\nwriter\n", ""),
        ("get_role_from_readme", "## Role\ncoder\n\n## Assigned Agent\nwriter\n", "CODER"),
        ("get_role_from_readme", "## Role\nCODER | WRITER\n", "CODER"),
        ("get_assigned_agent_from_readme", "## Assigned Agent\n\n## Workflow Type\nrelease\n", ""),
        ("get_assigned_agent_from_readme", "## Assigned Agent\nwriter\n\n## Goal\nx\n", "WRITER"),
        ("get_assigned_agent_from_readme", "## Assigned Agent\nnone\n\n## Goal\nx\n", ""),
    ],
    ids=["role-empty", "role-populated", "role-pipe", "agent-empty", "agent-populated", "agent-none"],
)
def test_readme_meta_readers(tmp_path: pathlib.Path, fn: str, readme_text: str, expected: str) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Task\n\n" + readme_text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"""set -euo pipefail
source '{_README_META}'
printf '[%s]' "$({fn} '{readme}')"
""",
    )
    assert result.returncode == 0, _explain(result)
    assert result.stdout == f"[{expected}]", _explain(result)


# --- wake_driver_finalize.sh: narrate_model_mismatch ---------------------------


@pytest.mark.parametrize(
    ("status_text", "warns"),
    [
        ("## Task\nx\n\n## Model\n\n## State\nWORKING\n", False),
        ("## Task\nx\n\n## Model\nmodel-a\n\n## State\nWORKING\n", True),
        ("## Task\nx\n\n## Model\nmodel-b\n\n## State\nWORKING\n", False),
    ],
    ids=["empty-model-no-warning", "stamped-differs-warns", "stamped-matches-silent"],
)
def test_model_mismatch_reads_stamped_model_section(tmp_path: pathlib.Path, status_text: str, warns: bool) -> None:
    status = tmp_path / "status.md"
    status.write_text(status_text, encoding="utf-8")
    result = run_bash(
        tmp_path,
        f"""set -euo pipefail
log() {{ printf 'LOG: %s\\n' "$*"; }}
resolve_dominant_output_token_model() {{ echo model-b; }}
source scripts/lib/pp_run_ops.sh
{_extract_fn("narrate_model_mismatch", _FINALIZE)}
narrate_model_mismatch '{status}' '{tmp_path / "tokens.json"}' demo
""",
    )
    assert result.returncode == 0, _explain(result)
    assert ("model mismatch" in result.stdout) is warns, _explain(result)
    if warns:
        assert "stamped 'model-a'" in result.stdout, _explain(result)


# --- block_storm: the blocked-task dispatch guard's scan -----------------------


def _task_id_pattern(tmp_path: pathlib.Path) -> str:
    result = run_bash(tmp_path, "set -euo pipefail\nsource scripts/lib/task_ids.sh\nkanban_task_id_pattern\n")
    assert result.returncode == 0, _explain(result)
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("status_text", "count"),
    [
        ("## State\nBLOCKED\n\n## Needs Human\nyes\n\n## Summary\nx\n", 1),
        ("## Task\nx\n\n## State\n\nBLOCKED\n\n## Summary\ns\n\n## Needs Human\nyes\n\n## Next\nn\n", 1),
        ("## State\r\nBLOCKED\r\n\r\n## Needs Human\r\nyes\r\n", 1),
        ("## State\nBLOCKED\n\n## Needs Human\nno\n\n## Blocked Reason\nyes\n", 0),
        ("## State\nBLOCKED\n\n## Needs Human\n\n## Blocked Reason\nyes\n", 0),
        ("## State\nBLOCKED\n\n## Needs Human\n\n## Human Answer\nyes\n", 0),
        ("## State\n\n## Summary\nBLOCKED\n\n## Needs Human\nyes\n", 0),
        ("## State\n## Blocked Reason\nBLOCKED\n\n## Needs Human\nyes\n", 0),
        ("## State\nWORKING\n\n## Needs Human\nyes\n", 0),
        ("## State\nDONE\n\n## Needs Human\nyes\n", 0),
    ],
    ids=[
        "blocked-needs-human",
        "blocked-with-sections-between",
        "crlf",
        "blocked-no-human",
        "blocked-empty-needs-human",
        "empty-needs-human-then-next",
        "empty-state",
        "empty-state-next-heading-directly",
        "working",
        "not-blocked",
    ],
)
def test_blocked_dispatch_guard_counts_by_section_body(tmp_path: pathlib.Path, status_text: str, count: int) -> None:
    queues = tmp_path / "queues"
    queues.mkdir()
    (queues / "coder_backlog.md").write_text(f"- [B] {_TASK_ID}\n", encoding="utf-8")
    (tmp_path / "tasks" / _TASK_ID).mkdir(parents=True)
    (tmp_path / "tasks" / _TASK_ID / "status.md").write_text(status_text, encoding="utf-8")
    blocks = scan_human_gated_blocks(queues, tmp_path / "tasks", _task_id_pattern(tmp_path))
    assert len(blocks) == count, [b.task_id for b in blocks]


# --- wake_lifecycle.sh: ## Inputs readers ---------------------------------------


def test_inputs_path_reader_empty_inputs_reads_no_path(tmp_path: pathlib.Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Task\n\n## Inputs\n\n## Context Paths\n- /abs/requirements/req.md\n", encoding="utf-8")
    proc = _run_heredoc(_heredoc_containing(_LIFECYCLE, "line.endswith('.md')"), str(readme))
    assert (proc.returncode, proc.stdout) == (1, ""), f"an empty ## Inputs names no path\n{proc.stdout}{proc.stderr}"


def test_inputs_path_reader_populated_reads_first_absolute_md(tmp_path: pathlib.Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Task\n\n## Inputs\n- team/rel.md\n- /abs/requirements/req.md\n\n## Context Paths\n- /abs/other.md\n",
        encoding="utf-8",
    )
    proc = _run_heredoc(_heredoc_containing(_LIFECYCLE, "line.endswith('.md')"), str(readme))
    assert (proc.returncode, proc.stdout) == (0, "/abs/requirements/req.md\n"), proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("inputs_body", "expected"),
    [("", "ok:0"), ("- /abs/requirements/req.md\n", "ok:1")],
    ids=["empty-inputs-not-counted", "populated-inputs-counted"],
)
def test_inputs_census_reader(tmp_path: pathlib.Path, inputs_body: str, expected: str) -> None:
    task = tmp_path / "tasks" / _TASK_ID
    task.mkdir(parents=True)
    (task / "README.md").write_text(
        f"# Task\n\n## Inputs\n{inputs_body}\n## Notes\n- /abs/requirements/req.md\n", encoding="utf-8"
    )
    (task / "status.md").write_text("## State\nDONE\n", encoding="utf-8")
    proc = _run_heredoc(
        _heredoc_containing(_LIFECYCLE, "TERMINAL = {"), str(tmp_path / "tasks"), "/abs/requirements/req.md"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected, proc.stdout + proc.stderr
