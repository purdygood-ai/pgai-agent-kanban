# Python Import Audit — pgai-agent-kanban

**Generated:** 2026-07-22  
**Target version:** v1.26.11  
**Task:** CODER-20260722-016-python-import-audit  

---

## Methodology

**Scan technique:** Python AST (`ast.parse` + `ast.walk`) applied to every `.py`
file found under the three scan roots:

- `team/scripts/`
- `team/pgai_agent_kanban/`
- `team/tests/`

The scanner also covers `team/pm-agent/` and `team/halt_after/` (intra-project
packages that the three roots import from); they are included in the source path
column but categorized accordingly.

For each file, all top-level `import X` and `from X import Y` statements are
collected (relative imports with `level > 0` are excluded — those are intra-package
and never third-party). The top-level component (first dot-segment) of each module
name is extracted.

**Stdlib exclusion source of truth:** `sys.stdlib_module_names` as exposed by
CPython 3.12.12 on this host. Modules whose top-level name appears in that set are
excluded from this table.

**Intra-project exclusion:** Modules whose import name corresponds to a package or
module that lives inside the `team/` tree are excluded from the third-party table
and listed separately below. This includes: `pgai_agent_kanban`, `pm_agent`,
`pm_materialize`, `pm_status`, `pseudocron`, `aggregate_tokens`, `metrics_aggregator`,
`metrics_csv_writer`, `workflow_loader`, `lib`, `tests`.

**Classification** — each entry is classified by the source tree it appears in:

- **runtime** — `team/pgai_agent_kanban/` non-test files; `team/pm-agent/` non-test files; `team/scripts/` non-test files
- **test** — `team/tests/`, `team/pgai_agent_kanban/api/tests/`, or any file matching `test_*.py`

---

## Third-Party Import Audit Table

| Import name | PyPI distribution | Source file(s) | Category | requirements.txt | requirements-test.txt | Status |
|---|---|---|---|---|---|---|
| `fastapi` | `fastapi` | `pgai_agent_kanban/api/app.py`, `pgai_agent_kanban/api/dependencies.py`, `pgai_agent_kanban/api/reads.py`, `pgai_agent_kanban/api/routers/board.py`, `pgai_agent_kanban/api/routers/logs.py`, `pgai_agent_kanban/api/routers/operations.py`, `pgai_agent_kanban/api/routers/projects.py`, `pgai_agent_kanban/api/routers/traces.py`, `pgai_agent_kanban/api/tests/` (12 files), `tests/unit/test_api_server_lifecycle_bash.py`, `tests/unit/test_review_cmds.py` | runtime + test | MISSING | MISSING | **GAP** |
| `pydantic` | `pydantic` | `pgai_agent_kanban/api/routers/operations.py`, `scripts/lint_api_parity.py` (conditional), `tests/unit/test_lint_api_parity.py` | runtime + test | MISSING | MISSING | **GAP** |
| `pytest` | `pytest` | `tests/conftest.py`, `tests/fixtures/` (5 files), `tests/integration/` (15 files), `tests/unit/` (75+ files), `pgai_agent_kanban/api/tests/` (14 files) | test only | not applicable | declared (`pytest>=7`) | declared |
| `uvicorn` | `uvicorn` | `pgai_agent_kanban/api/main.py` | runtime only | MISSING | MISSING | **GAP** |
| `yaml` | `PyYAML` | `pm-agent/lib/workflow_loader.py` | runtime only | declared (`PyYAML>=6.0`) | declared (via `-r requirements.txt`) | declared |

---

## Gaps Summary

Three third-party packages are imported by the shipping tree but absent from both
requirements files:

| Package | Import name | Missing from | Notes |
|---|---|---|---|
| `fastapi` | `fastapi` | `requirements.txt`, `requirements-test.txt` | Used by the operator REST API (runtime) and all API test suites. Required in **runtime** requirements. |
| `pydantic` | `pydantic` | `requirements.txt`, `requirements-test.txt` | Used in `api/routers/operations.py` (BaseModel) and `lint_api_parity.py` (conditional import). FastAPI depends on pydantic internally, but it should also be declared explicitly as a direct dependency. Required in **runtime** requirements. |
| `uvicorn` | `uvicorn` | `requirements.txt`, `requirements-test.txt` | The ASGI server that runs the API; imported directly in `api/main.py`. Required in **runtime** requirements. |

**Known suspects named in the priority item:**

- `pytest` — present in `requirements-test.txt` as `pytest>=7`. **Not a gap.**
- `coverage` / `pytest-cov` — not directly imported by any Python file in the scan
  tree (used via CLI flags in shell scripts). Declared in `requirements-test.txt` as
  `pytest-cov>=5`. **Not a gap in the import sense; declared correctly.**
- `pyyaml` — declared in `requirements.txt` as `PyYAML>=6.0`. **Not a gap.**

---

## Indirect / Transitive Dependencies (noted for completeness)

`fastapi.testclient.TestClient` (used in 15 test files) requires `httpx` at runtime.
`httpx` is not directly imported anywhere in the tree (no `import httpx` / `from httpx`
statements found), but it is a required installation-time dependency for
`TestClient` to function. `httpx` is an optional extra of fastapi (`fastapi[standard]`)
and an optional extra of starlette. Once `fastapi` is added to the requirements files,
it should be pinned as `fastapi[standard]` (or `httpx` added explicitly) so that
`TestClient` works out-of-the-box in container builds.

This is not a gap in the import-census sense (no direct `import httpx` found), but
it is a gap in the installation requirements sense. Downstream tickets should address it.

---

## Intra-Project Modules (excluded from third-party table)

The following module names appear as imports but resolve to packages/modules within
the `team/` source tree. They are NOT third-party dependencies:

| Import name | Resolves to |
|---|---|
| `pgai_agent_kanban` | `team/pgai_agent_kanban/` package |
| `pm_agent` | `team/pm-agent/` (sys.path manipulation in test bootstrap) |
| `pm_materialize` | `team/pm-agent/pm_materialize.py` |
| `pm_status` | `team/pm-agent/pm_status.py` |
| `pseudocron` | `team/scripts/pseudocron.py` |
| `aggregate_tokens` | `team/pm-agent/aggregate_tokens.py` |
| `metrics_aggregator` | `team/scripts/lib/metrics_aggregator.py` |
| `metrics_csv_writer` | `team/scripts/lib/metrics_csv_writer.py` |
| `workflow_loader` | `team/pm-agent/lib/workflow_loader.py` |
| `lib` | `team/pm-agent/lib/` package |
| `tests` | `team/tests/` package (cross-imported in integration fixtures) |

---

## Stdlib Modules Observed (excluded from audit)

The following standard-library modules were encountered and excluded per
`sys.stdlib_module_names` (Python 3.12):

`argparse`, `ast`, `collections`, `configparser`, `contextlib`, `csv`,
`dataclasses`, `datetime`, `difflib`, `fcntl`, `glob`, `hashlib`, `importlib`,
`io`, `ipaddress`, `json`, `logging`, `os`, `pathlib`, `re`, `shutil`,
`signal`, `socket`, `stat`, `subprocess`, `sys`, `tempfile`, `textwrap`,
`time`, `types`, `typing`, `unittest`, `urllib`, `warnings`

---

## Current Requirements File State

### `requirements.txt` (runtime)

```
PyYAML>=6.0
```

### `requirements-test.txt` (test/verification)

```
-r requirements.txt
pytest>=7
pytest-cov>=5
```

---

## Recommended Additions (for downstream tickets)

These additions are **not made by this ticket** (audit only per task constraints):

**`requirements.txt`** should add:
```
fastapi[standard]>=0.100     # operator REST API; [standard] pulls httpx for TestClient
pydantic>=2.0                # direct dep: BaseModel in api/routers/operations.py
uvicorn[standard]>=0.20      # ASGI server; api/main.py imports uvicorn directly
```

**`requirements-test.txt`** needs no additional entries beyond `-r requirements.txt`
— all test-only imports (`pytest`, `pytest-cov`) are already declared, and the
runtime packages above cover what the API tests require.
