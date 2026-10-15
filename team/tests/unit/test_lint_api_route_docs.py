"""
test_lint_api_route_docs.py — Unit tests for team/scripts/lint_api_route_docs.py.

The lint checks that every REST route the API declares has a hand-written
row (or heading) in docs/operator-api.md's Endpoint table section and a curl
example whose URL matches it, path parameters as wildcards, and that the
page's starts-a-process list matches the operator verb census.

Most tests build a fixture tree under pytest's ``tmp_path``: an app factory,
a reads router and an operations router under ``team/pgai_agent_kanban/api/``,
a ``team/config/operator_verbs.manifest``, a ``docs/operator-api.md``, and
copies of the two library modules the lint imports (the manifest reader and
the error-contract emitter). The lint is pointed at it with ``--root``, so
those tests read nothing from this dev tree's own routes, census or docs.
The last group runs the lint against the real tree and the real runner
wiring.

Coverage:

  1. A fixture tree whose routes all have a row or heading and a curl line,
     and whose starts-a-process list matches the census, is clean; the lint
     exits 0 and writes ``[]``.
  2. A fixture route added to the app is reported twice by ``METHOD path``
     -- no table row, no curl example -- and adding a row and a curl line
     whose URL fills the path parameters clears both.
  3. A row inside the generated census reference does not document a route.
  4. A starts-a-process list naming an in-process route, or omitting a
     shell route or the dashboard pane route, is reported.
  5. Every run exits 0 and writes one findings-file entry per finding.
  6. Against the real tree the lint reports nothing; the runner's own
     wiring block records a seeded finding and returns 0.

Naming: function names describe behaviour under test, never bug IDs or task
IDs (roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

_THIS_FILE = Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent
_REPO_ROOT = _TEAM_DIR.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_api_route_docs.py"
_ROUTE_SCAN_SCRIPT = _TEAM_DIR / "scripts" / "lint_api_route_impl.py"
_GATED_RUNNER_LIB = _TEAM_DIR / "scripts" / "lib" / "gated_runner_lint.sh"
_UNIT_RUNNER = _TEAM_DIR / "scripts" / "run-unit-tests.sh"
_LIB_COPIES = ("operator_verb_manifest.py", "error_contract.py")

_HALT = "module:pgai_agent_kanban.ops.write.halt"

APP = textwrap.dedent(
    '''\
    from fastapi import FastAPI


    def create_app():
        app = FastAPI()

        @app.get("/health")
        def health():
            return {}

        return app
    '''
)

READS = textwrap.dedent(
    '''\
    from fastapi import APIRouter

    router = APIRouter()


    @router.get("/status")
    def get_status():
        return {}


    @router.get("/dashboard/{pane}")
    def get_dashboard_pane(pane):
        return {}


    @router.get("/projects/{project}/tasks/{key}/readme")
    def get_task_readme(project, key):
        return {}
    {extra}'''
)

OPERATIONS = textwrap.dedent(
    '''\
    from fastapi import APIRouter

    router = APIRouter(prefix="/operations")


    @router.post("/halt")
    def post_halt(body):
        return {}


    @router.post("/wake-now")
    def post_wake_now(body):
        return {}
    '''
)

PROCESS_LIST = (
    "- `GET /dashboard/{pane}` runs the pane script and waits for it.\n"
    "- `POST /operations/wake-now` runs `wake-now.sh`,\n"
    "  detached when `bg` is set.\n"
)

DOC = """\
# Operator REST API

## 5. Endpoint table

Every endpoint runs in process, with these exceptions:

{process_list}
`/health` reads its data directly.

### Reads (GET)

| Endpoint | CLI twin | Parameters |
|---|---|---|
| `GET /health` | (none) | none |
| `GET /status` | `kanban-status.sh` | `project` |
| `GET /dashboard/queue` | `dashboard/column-render.sh` | `column_type` |
{extra_rows}
#### `GET /projects/{{project}}/tasks/{{key}}/readme` — a task's README

Returns one task's README.

### Mutations (POST)

| Endpoint | CLI twin | Required body fields | Optional body fields |
|---|---|---|---|
| `POST /operations/halt` | `halt.sh` | `project` | — |
| `POST /operations/wake-now` | `wake-now.sh` | `agent` | `bg` |

<!-- OPERATOR-VERBS-GENERATED:BEGIN section=api-reference -->
| Verb | Method | Route |
|---|---|---|
{generated_rows}
<!-- OPERATOR-VERBS-GENERATED:END section=api-reference -->

## 7. curl examples

```bash
curl -sS http://127.0.0.1:8300/health
curl -sS "http://127.0.0.1:8300/status?project=my-app"
curl -sS "http://127.0.0.1:8300/dashboard/queue?column_type=coder"
curl -sS "http://127.0.0.1:8300/projects/my-app/tasks/CODER-1/readme"
curl -sS -X POST http://127.0.0.1:8300/operations/halt \\
  -H 'Content-Type: application/json' -d '{{"project": "my-app"}}'
curl -sS -X POST \\
  http://127.0.0.1:8300/operations/wake-now \\
  -H 'Content-Type: application/json' -d '{{"agent": "coder"}}'
{extra_curl}```
"""


def _row(name: str, method: str, route: str, impl: str) -> str:
    return "\t".join(
        ["V", name, "active", f"scripts/{name}.sh", "project|help", "yes", "idempotent", method, route,
         "NONE", "action", "DualChannelShellEnvelope", impl]
    )


_ROWS = [
    _row("halt", "POST", "/operations/halt", _HALT),
    _row("wake-now", "POST", "/operations/wake-now", "shell"),
]


def _make_tree(
    tmp_path: Path,
    *,
    extra_route: str = "",
    process_list: str = PROCESS_LIST,
    extra_rows: str = "",
    generated_rows: str = "",
    extra_curl: str = "",
    rows: "list[str] | None" = None,
) -> Path:
    """Build a fixture repository the lint can be pointed at with --root."""
    root = tmp_path / "repo"
    team = root / "team"
    (team / "config").mkdir(parents=True)
    (team / "config" / "operator_verbs.manifest").write_text(
        "# fixture manifest\n" + "\n".join(_ROWS if rows is None else rows) + "\n", encoding="utf-8"
    )
    pkg = team / "pgai_agent_kanban"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "api" / "routers").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "lib" / "__init__.py").write_text("", encoding="utf-8")
    for name in _LIB_COPIES:
        shutil.copy2(_TEAM_DIR / "pgai_agent_kanban" / "lib" / name, pkg / "lib" / name)
    (pkg / "api" / "app.py").write_text(APP, encoding="utf-8")
    (pkg / "api" / "reads.py").write_text(READS.replace("{extra}", extra_route), encoding="utf-8")
    (pkg / "api" / "routers" / "operations.py").write_text(OPERATIONS, encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "operator-api.md").write_text(
        DOC.format(
            process_list=process_list,
            extra_rows=extra_rows,
            generated_rows=generated_rows,
            extra_curl=extra_curl,
        ),
        encoding="utf-8",
    )
    return root


def _run(root: Path, tmp_path: Path, *extra: str) -> "tuple[int, str, list[dict]]":
    """Run the lint against *root*; return (exit code, stdout, findings)."""
    findings_file = tmp_path / "findings.json"
    proc = subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), "--root", str(root), "--findings-file", str(findings_file), *extra],
        capture_output=True,
        text=True,
        timeout=60,
    )
    findings = json.loads(findings_file.read_text(encoding="utf-8"))
    return proc.returncode, proc.stdout + proc.stderr, findings


def _kinds(findings: "list[dict]") -> "list[tuple[str, str]]":
    return sorted((f["kind"], f["route"]) for f in findings)


_WIDGET_ROUTE = textwrap.dedent(
    '''\


    @router.get("/projects/{project}/widgets/{widget_id}")
    def get_widget(project, widget_id):
        return {}
    '''
)


# ---------------------------------------------------------------------------
# 1. A fully documented fixture is clean
# ---------------------------------------------------------------------------


def test_fully_documented_fixture_is_clean_and_exits_zero(tmp_path):
    code, out, findings = _run(_make_tree(tmp_path), tmp_path)
    assert code == 0, out
    assert findings == [], out
    assert "6 route(s)" in out and "0 finding(s)" in out, out


# ---------------------------------------------------------------------------
# 2. A new route without a row or a curl line
# ---------------------------------------------------------------------------


def test_new_route_is_reported_for_its_missing_row_and_curl_example(tmp_path):
    code, out, findings = _run(_make_tree(tmp_path, extra_route=_WIDGET_ROUTE), tmp_path)
    assert code == 0, out
    assert _kinds(findings) == [
        ("route_curl_missing", "GET /projects/{project}/widgets/{widget_id}"),
        ("route_table_row_missing", "GET /projects/{project}/widgets/{widget_id}"),
    ], out
    by_kind = {f["kind"]: f for f in findings}
    # Each remedy opens the page at the section to edit: the Endpoint table
    # heading (line 3) and the curl examples heading. No placeholder.
    assert by_kind["route_table_row_missing"]["remedy"] == "${EDITOR:-vi} +3 docs/operator-api.md"
    assert by_kind["route_curl_missing"]["remedy"].startswith("${EDITOR:-vi} +")
    assert all(f["severity"] == "quality" and f["lint"] == "api_route_docs" for f in findings)
    assert "[route_table_row_missing] GET /projects/{project}/widgets/{widget_id}" in out, out


def test_row_with_param_names_and_curl_with_values_document_the_route(tmp_path):
    root = _make_tree(
        tmp_path,
        extra_route=_WIDGET_ROUTE,
        extra_rows="| `GET /projects/{project}/widgets/{id}` | `widget.sh` | path parameters |\n",
        extra_curl='curl -sS "http://127.0.0.1:8300/projects/my-app/widgets/w-1?verbose=true"\n',
    )
    code, out, findings = _run(root, tmp_path)
    assert code == 0, out
    assert findings == [], out


def test_curl_line_for_a_different_path_does_not_document_the_route(tmp_path):
    root = _make_tree(
        tmp_path,
        extra_route=_WIDGET_ROUTE,
        extra_rows="| `GET /projects/{project}/widgets/{widget_id}` | `widget.sh` | none |\n",
        extra_curl='curl -sS "http://127.0.0.1:8300/projects/my-app/widgets"\n',
    )
    _code, out, findings = _run(root, tmp_path)
    assert _kinds(findings) == [("route_curl_missing", "GET /projects/{project}/widgets/{widget_id}")], out


# ---------------------------------------------------------------------------
# 3. The generated census reference does not count
# ---------------------------------------------------------------------------


def test_generated_reference_row_does_not_document_a_route(tmp_path):
    root = _make_tree(
        tmp_path,
        extra_route=_WIDGET_ROUTE,
        generated_rows="| `GET /projects/{project}/widgets/{widget_id}` | `GET` | widgets |",
        extra_curl='curl -sS "http://127.0.0.1:8300/projects/my-app/widgets/w-1"\n',
    )
    _code, out, findings = _run(root, tmp_path)
    assert _kinds(findings) == [("route_table_row_missing", "GET /projects/{project}/widgets/{widget_id}")], out


# ---------------------------------------------------------------------------
# 4. The starts-a-process list against the census
# ---------------------------------------------------------------------------


def test_process_list_naming_an_in_process_route_and_omitting_a_shell_route_is_reported(tmp_path):
    process_list = (
        "- `GET /dashboard/{pane}` runs the pane script and waits for it.\n"
        "- `POST /operations/halt` writes the HALT file.\n"
    )
    code, out, findings = _run(_make_tree(tmp_path, process_list=process_list), tmp_path)
    assert code == 0, out
    assert _kinds(findings) == [
        ("process_route_not_shell", "POST /operations/halt"),
        ("process_route_unlisted", "POST /operations/wake-now"),
    ], out
    not_shell = next(f for f in findings if f["kind"] == "process_route_not_shell")
    assert not_shell["line"] == 8 and not_shell["remedy"] == "${EDITOR:-vi} +8 docs/operator-api.md", not_shell


def test_process_list_omitting_the_dashboard_pane_route_is_reported(tmp_path):
    process_list = "- `POST /operations/wake-now` runs `wake-now.sh`.\n"
    _code, out, findings = _run(_make_tree(tmp_path, process_list=process_list), tmp_path)
    assert _kinds(findings) == [("process_route_unlisted", "GET /dashboard/{pane}")], out


def test_process_list_follows_the_census_when_a_verb_moves_in_process(tmp_path):
    rows = [
        _row("halt", "POST", "/operations/halt", _HALT),
        _row("wake-now", "POST", "/operations/wake-now", "module:pgai_agent_kanban.ops.write.wake_now"),
    ]
    _code, out, findings = _run(_make_tree(tmp_path, rows=rows), tmp_path)
    assert _kinds(findings) == [("process_route_not_shell", "POST /operations/wake-now")], out


# ---------------------------------------------------------------------------
# 5. Exit code and findings file
# ---------------------------------------------------------------------------


def test_missing_doc_is_one_finding_and_exit_zero(tmp_path):
    root = _make_tree(tmp_path)
    (root / "docs" / "operator-api.md").unlink()
    code, out, findings = _run(root, tmp_path)
    assert code == 0, out
    assert [f["kind"] for f in findings] == ["doc-unreadable"], out


# ---------------------------------------------------------------------------
# 6. The real tree and the runner wiring
# ---------------------------------------------------------------------------


def test_real_tree_documents_every_route(tmp_path):
    code, out, findings = _run(_REPO_ROOT, tmp_path)
    assert code == 0, out
    assert findings == [], out
    routes = int(re.search(r"totals: (\d+) route\(s\)", out).group(1))
    assert routes >= 49, f"implausibly few routes found on the real tree: {out}"


_BLOCK_START = 'echo "Running API route documentation lint..."'
_BLOCK_END_RE = re.compile(r"^unset _RU_API_ROUTE_DOCS_FINDINGS\s*$", re.M)


def _runner_block(runner: Path) -> str:
    text = runner.read_text(encoding="utf-8")
    start = text.find(_BLOCK_START)
    assert start != -1, f"{runner.name} does not run the API route documentation lint"
    end = _BLOCK_END_RE.search(text, start)
    assert end is not None, f"{runner.name}: API route documentation lint block has no closing unset"
    return text[start : end.end()]


def test_runner_wiring_records_a_seeded_finding_and_returns_zero(tmp_path):
    """Run run-unit-tests.sh's own wiring block against a seeded fixture tree.

    The fixture's ``team/scripts`` carries copies of the lint and the route
    scanner it imports (the lint self-locates its root from its own path);
    the block runs from the fixture's ``team`` directory, as the runner runs
    from its own.
    """
    root = _make_tree(tmp_path, extra_route=_WIDGET_ROUTE)
    scripts = root / "team" / "scripts"
    scripts.mkdir()
    shutil.copy2(_LINT_SCRIPT, scripts / _LINT_SCRIPT.name)
    shutil.copy2(_ROUTE_SCAN_SCRIPT, scripts / _ROUTE_SCAN_SCRIPT.name)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    out_file = tmp_path / "findings-out.json"
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -euo pipefail\n"
        f'source "{_GATED_RUNNER_LIB}"\n'
        + _runner_block(_UNIT_RUNNER)
        + "\n"
        + f'gated_runner_write_findings_file "{out_file}" "{_UNIT_RUNNER.name}"\n'
        + 'echo "block-exit=$?"\n',
        encoding="utf-8",
    )
    env = build_scrubbed_env(
        home_dir=tmp_path / "home",
        extra_env={"PGAI_AGENT_KANBAN_TEMP_DIR": str(scratch)},
    )
    proc = subprocess.run(
        ["bash", str(driver)],
        cwd=root / "team",
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "block-exit=0" in proc.stdout
    data = json.loads(out_file.read_text(encoding="utf-8"))
    ours = [f for f in data["findings"] if f.get("lint") == "api_route_docs"]
    assert sorted((f["kind"], f["route"]) for f in ours) == [
        ("route_curl_missing", "GET /projects/{project}/widgets/{widget_id}"),
        ("route_table_row_missing", "GET /projects/{project}/widgets/{widget_id}"),
    ]
