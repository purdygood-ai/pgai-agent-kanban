# Testing Conventions

## Why this file exists

The v1.0.0 moat is autonomous reliability — the system must run unattended without an operator catching its mistakes. Tests are the only thing standing between an autonomous chain and a regression that ships. Six recurring failure modes shipped because tests passed against a *convenient* condition rather than the *real* breaking condition. This document names those six failure modes, fixes the convention for each, and points at the reusable helper that makes the correct-fidelity test the easy one to write. The conventions close the class. For test authoring anti-patterns that live alongside these (pattern-scan, environment-coupling, order-dependence, production-coupling, side-effect leakage) see the **Test Authoring Guidelines** section of `team/SOP.md` — do not duplicate it here.

## The six failure modes

### 1. Strict-mode bash not exercised

Tests called the Python entry point directly. Nothing sourced the wake shell under `set -euo pipefail`, so an errexit chain-killer passed CI.

Example shape of the gap:

```python
# Test invoked the module directly — never ran the shell wrapper.
subprocess.run(["python3", "-m", "team.pgai_agent_kanban.wake"])
```

**Convention.** When the production entry point is a bash script under strict mode, the test must invoke that script — not a Python shortcut around it. Run it under `bash` with `set -euo pipefail` honored. If you must inject a helper into the script, embed it as a bash fragment; do not bypass the shell.

**Helper.** None for the bash invocation itself — invoke the real script. For a faithful `log()` injection inside that script, use `make_log_stub_fragment` from `team/tests/fixtures/log_stub.py`.

### 2. Installed tree not simulated

Tests ran in the dev tree where the Python shim package `team/pgai_agent_kanban/` is always importable. The installed layout produced by `install.sh` does not contain that shim, so an import-resolution defect passed every dev-tree test.

Example shape of the gap:

```python
# Passes only because cwd is the dev tree and team/ is on sys.path.
from team.pgai_agent_kanban import wake
wake.run()
```

**Convention.** Code that runs after `install.sh` must be tested against a tree that mirrors the installed layout — no `team/` shim, real `workflows/`, empty `roles/scripts/logs/locks/projects/` skeletons. Do not rely on dev-tree side effects for imports or path discovery.

**Helper.** `build_installed_root(parent)` and the `installed_root` pytest fixture in `team/tests/fixtures/installed_root.py`.

### 3. Multi-project not fixtured

Tests built single-project fixtures only. The wake outer loop, the dashboard multi-project pane, and the discovery pipeline's per-project isolation went untested or were tested with hand-rolled inconsistent trees, so a cross-project isolation defect slipped through.

Example shape of the gap:

```python
# Single project — never exercises cross-project isolation.
root = make_project_root(tmp_path, name="only_project")
```

**Convention.** Any code that iterates projects, or that promises isolation between projects, must be tested against a root with at least two registered projects. Use the standard `project_a` / `project_b` names so failures are recognizable across the suite.

**Helper.** `build_two_project_root(parent)` and the `two_project_root` pytest fixture in `team/tests/fixtures/two_project.py`.

### 4. Mock too convenient

A test stubbed `log()` as stderr-only. The real `log()` tees to both stdout and a log file, so a command-substitution capture-contamination defect passed — the stub silently removed the very output channel that carried the bug.

Example shape of the gap:

```bash
# Stub omits the stdout path that the real log() writes through.
log() { echo "[mock]: $*" >&2; }
```

**Convention.** A stub of a shared primitive — `log()`, a path resolver, a config loader — must reproduce every production behavior the code under test could depend on, especially the ones that look incidental (which channel it writes to, whether it tees, whether it appends a newline). If you cannot articulate why a behavior was *safe* to omit from the stub, do not omit it. When in doubt, prefer the real primitive over a stub.

**Helper.** `make_log_stub_fragment(log_file)`, the `log_stub_fragment` fixture, and the `LogStubCapture` context manager in `team/tests/fixtures/log_stub.py`. All three faithfully reproduce the tee-to-stdout-plus-file behavior of the production `log()`.

### 5. Env-var resolution not faithfully tested

Tests set the legacy environment variable, so the canonical-var-only path and the both-unset fresh-customer path were never exercised. A root-resolution defect shipped because the developer's convenient environment hid it.

Example shape of the gap:

```python
# Sets the legacy var the dev shell happens to export — never tests the
# fresh-install case where neither var is set.
monkeypatch.setenv("LEGACY_KANBAN_ROOT", str(tmp_path))
```

**Convention.** When code resolves a value from multiple environment variables (canonical, legacy, defaulted-to-None), every reachable combination must have a test. Always include the *fresh-customer* case where no kanban env vars are set at all. Never rely on the dev shell's ambient environment to satisfy a code path — clear it explicitly with `monkeypatch.delenv(..., raising=False)`.

**Helper.** None specific — use `monkeypatch.setenv` / `monkeypatch.delenv` and the `_block_live_kanban_writes` autouse fixture in `team/tests/conftest.py` as your sandbox. When building a fresh-install root to point env vars at, use `build_installed_root` (mode 2) so the layout matches what a real fresh customer would have.

### 6. Live-install pollution from ad-hoc invocations

An agent running a state-mutating script outside the test runner can create stray fixture projects in the operator's live `projects.cfg`. Pytest is fully sandboxed (`conftest.py` redirects every root env var, `run-e2e.sh` works in a tmpdir), so this pollution never comes from the harness — it comes from running a state-mutating script — `create-project.sh`, `add-project.sh`, or a test file invoked directly outside the runner — against the live install. The harness sandbox only protects code run *through* the harness.

Example shape of the gap:

```bash
# Agent runs the real script with the LIVE root in its environment.
# Result: stray projects in the operator's live projects.cfg.
team/scripts/create-project.sh foo
```

**Convention.** Before running any script that writes under `$KANBAN_ROOT` — `create-project.sh`, `add-project.sh`, `remove-project.sh`, or a test file invoked directly rather than via `scripts/run-unit-tests.sh` or `scripts/run-integration-tests.sh` — point `PGAI_AGENT_KANBAN_ROOT_PATH` at a throwaway temp root. The harness sandbox does not extend to ad-hoc invocations; you must extend it yourself for every manual run.

```bash
PGAI_AGENT_KANBAN_ROOT_PATH="$(mktemp -d)" team/scripts/create-project.sh foo
```

**Helper.** None — this is operator discipline, not a fixture. The role-file clauses in `roles/CODER.md` and `roles/TESTER.md` encode it; the optional `is_self_build` defensive guard in `create-project.sh` is belt-and-suspenders, not a substitute. Always route through the test runners; reach for the temp-root pattern above when you cannot.

## Interface testing: test the contract, not each caller's plumbing

When production logic lives in one shared place — a library function, a module, a service — and several entry points call it (a CLI command, a scheduled job, a future web or API layer), the logic has one *interface contract* and many *adapters* over it. The contract is what every adapter depends on; the adapters are interchangeable plumbing on top of it.

The discipline: **test the shared logic at its interface, so the test is the contract every caller must satisfy — not a test of one caller's plumbing.** A test written against the shared function validates every adapter that calls it, present and future. A test written only against one adapter's surface (one command's stdout, one endpoint's response shape) validates that adapter alone and must be re-authored for every other caller of the same logic.

This applies to any project that has shared logic behind multiple callers. A project with no such structure — a single script with no shared core — has nothing to apply here; the rule is about *shared* logic, not all logic.

### Two layers, both required

1. **Interface layer (the contract — primary).** Call the shared unit directly and assert on its *return value and its raised errors*, not on text printed by a caller. This is the layer every adapter reuses.

   ```python
   # Contract: the shared function itself. Caller-agnostic.
   def test_archive_record_marks_record_inactive(tmp_store):
       result = archive_record(tmp_store, record_id="r-001")
       assert result.status == "inactive"          # typed return, not stdout scrape

   def test_archive_record_unknown_id_raises_not_found(tmp_store):
       with pytest.raises(NotFound):                # typed error, not an exit code
           archive_record(tmp_store, record_id="does-not-exist")
   ```

2. **Adapter layer (the caller — fidelity).** The existing fidelity rules still hold: exercise the real entry point under its real conditions (failure mode 1 — strict-mode bash; the installed-tree and multi-project modes as applicable) and assert exit code plus on-disk effect. But the adapter test asserts only what the *adapter* adds — argument parsing, exit-code or status mapping, help-text honesty — and trusts the interface test for the underlying behavior. Assert the behavioral contract once, at the shared unit; do not re-assert it through every caller.

### Rules for interface-layer tests

- **Assert on typed results and typed errors, never on a caller's exit code or printed strings.** The shared logic raises a typed error; each adapter maps that error to its own surface (a CLI maps it to an exit code, an API maps it to a status code). A test asserting an exit code is testing the adapter; a test asserting the typed error is testing the contract that all adapters share.
- **The shared unit owns the invariants; assert them at the shared unit.** A refusal guard, a terminal-state check, an atomic write, an input-validation rule — if the shared logic enforces it, every adapter inherits it and none may bypass it. A new caller (a button, an endpoint) must not be able to reach a state the existing caller refuses, so the test that proves the guard must sit at the shared unit, not at one caller.
- **Build the test context against the same layout the callers run in** (e.g. the installed layout, not a dev-only tree — failure mode 2). A future adapter will run against the real layout, so the contract test should too.
- **Cover multi-subject isolation where the shared logic iterates or isolates** (failure mode 3) — isolation guarantees are part of the contract every adapter exposes.
- **Name by behavior, not by caller or provenance** (see SOP.md "Test Authoring Guidelines", Anti-pattern 6): `test_archive_record_refuses_active_record`, not `test_archive_cli_exit_2` and not `test_bug1234_fix`.

### Why this matters before the next caller exists

Every interface-layer test written now is a test the next adapter — a web layer, an API, a scheduled job — does not have to re-author, because it is already the contract. Conversely, every behavioral assertion buried in a single caller's surface test is a contract the next adapter must re-discover and re-test. Writing at the interface captures the contract once, at the boundary all callers share.

## When to reach for which helper

| Helper | File | Closes failure modes |
| --- | --- | --- |
| `build_installed_root(parent)` / `installed_root` fixture | `team/tests/fixtures/installed_root.py` | 2 (installed tree); supports 5 (fresh-install env case) |
| `build_two_project_root(parent)` / `two_project_root` fixture | `team/tests/fixtures/two_project.py` | 3 (multi-project) |
| `make_log_stub_fragment(log_file)` / `log_stub_fragment` fixture / `LogStubCapture` | `team/tests/fixtures/log_stub.py` | 4 (faithful primitive stub); supports 1 (inject inside real strict-mode bash) |
| `monkeypatch` + `_block_live_kanban_writes` autouse fixture in `team/tests/conftest.py` | `team/tests/conftest.py` | 5 (env-var resolution); enforces in-harness sandbox for all six modes |
| Temp-root discipline: `PGAI_AGENT_KANBAN_ROOT_PATH="$(mktemp -d)"` | operator practice (see role files) | 6 (live-install pollution) |

Modes 1 and 6 have no dedicated Python fixture: mode 1 is solved by invoking the real bash script under its real strict mode (optionally with the `log_stub.py` fragment injected), and mode 6 is solved by always routing state-mutating scripts through a throwaway temp root or the test runners.

## Verification

When TESTER verifies an RC, it cross-references whether the tests under review actually exercise the breaking condition rather than a mocked-away proxy. The fidelity checklist lives in `roles/TESTER.md` — see that file for the verification questions TESTER applies. If a test in this repository violates a convention above without a documented exception, TESTER should flag it as a gap. Authors of new tests: reach for the helper before reaching for a custom mock.
