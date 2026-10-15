# Test Authoring Guidelines

The canonical reference for how tests in this kanban are written and how
their quality is judged. It is agent-facing law for three roles: TESTER
cross-references it when judging the quality of new or modified tests
(`team/roles/TESTER.md`, Steps 5a, 6.13 and 6.14), CODER conforms every
test it authors to it, and WRITER follows it for any test that ships
inside a document.

This document is not on any role's read-order manifest. It is consulted
by name, in one hop, from the role file that sends you here.

## Contents

- The six authoring anti-patterns, each with a BAD/BETTER pair and the
  narrow conditions under which it is acceptable.
- The version-named-test rule, and the lint class that enforces it.
- The two rules every grep-zero sweep gate is held to.
- The skip-cites-a-real-bug rule the gated runners enforce mechanically.
- The five contracts binding a test that reaches outside its own process
  — an external binary, a subprocess, or the shipped entry point.
- The test-fidelity checklist TESTER applies to test *results*.
- The two gated test scripts, what each covers, and who runs them when.
- The runners' `--files`, `--quarantined` and `--reuse-if-green` flags,
  the quarantine marker policy, the `serial` marker and the parallel
  lane, the per-RC green ledger, the exit file each kind of run writes,
  and why `--coverage` is opt-in.
- The pre-merge run: what `--pre-merge` runs, including the always-run
  whole-tree guards and their time budget, when to use it and how it
  differs from a targeted or certification run, how affected tests are
  chosen (the default base, `--base`, and the cap), and where its exit
  file is written.

---

## Why tests must encode intent

Tests in this kanban must encode INTENT explicitly. A passing test should pass because the production code satisfies the specification the test captures — not because the test happened to match incidental implementation details, naming conventions, or shared state.

This document is the canonical reference for what TESTER cross-references when judging test quality, and what CODER, WRITER, and any other agent authoring tests must follow. New tests violating it without a documented exception are gaps; TESTER flags them as such. Each guideline maps back to a real failure the operator absorbed. The common root: a test coupled to a naming convention rather than a specification fails spuriously when new code legitimately introduces a matching symbol — it passes for months, then breaks not because behavior was violated but because the scan scope was never bounded.

## Anti-pattern 1 — Pattern-scan universal invariants

A test enumerates every variable, file, or symbol matching a naming pattern and asserts each conforms to behavior X, coupling the assertion to the convention rather than to an explicit list of subjects.

```python
# BAD — couples to the naming convention, not semantics
for cmd in find_variables_matching(r"VIS_.*_CMD"):
    assert "--all-projects" in cmd.contents

# BETTER — explicit allowlist that documents intent
for cmd in ["VIS_BUGS_CMD", "VIS_CODER_CMD", "VIS_TESTER_CMD"]:
    assert "--all-projects" in load_variable(cmd)
```

**When acceptable:** only when the test genuinely intends to cover every current and future symbol matching the pattern (e.g. "no test file may import from `team/internal/`"). Document it inline with a comment beginning `# Intentional pattern-scan:` naming the invariant.

## Anti-pattern 2 — Environment-coupled fixtures

A test assumes a hardcoded working directory, environment-variable value, or temp path that differs between operator machines, CI runners, or framework configurations. It passes on the author's machine and fails — or worse, pollutes shared state — elsewhere.

```bash
# BAD — hardcoded /tmp path; ignores the framework temp-dir convention
TEST_DIR=/tmp/some_specific_path

# BETTER — respect PGAI_AGENT_KANBAN_TEMP_DIR, use mktemp, trap cleanup
TEST_DIR="$(mktemp -d -p "${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp}" test.XXXXXX)"
trap "rm -rf '$TEST_DIR'" EXIT
```

In Python the equivalent is `pathlib.Path(os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR") or "/tmp")`, or pytest's `tmp_path`.

**When acceptable:** almost never. A test that genuinely needs a fixed system path must check for it and skip cleanly when absent, with a comment beginning `# Path-dependent test:` explaining what would be lost by making it portable.

### Temp paths come from the framework helpers, not from a literal

**Rule: scratch space comes from the framework's temp helpers — pytest's `tmp_path` in Python, `pgai_mktemp_d` (or `mktemp -d -p "${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"`) in bash — never from a path the author typed out.**

Two failures follow from a typed path, and the second hides the first. A fixed name is shared by every process that runs the test, so two concurrent runs plant the same file and one deletes the other's fixture mid-assertion. And the estate redirects all pytest-managed temp space under `PGAI_AGENT_KANBAN_TEMP_DIR` (`team/tests/conftest.py` sets `PYTEST_DEBUG_TEMPROOT`), so a literal escapes that redirect entirely and leaves residue on the host that the next run's cleanliness check attributes to whoever ran last.

```python
# BAD — a name the author typed: concurrent runs collide on it, and it
# escapes the framework temp root, so the file outlives the run
litter_file = pathlib.Path("/tmp/tester_fixture.log")

# BETTER — the framework hands each test its own private directory
litter_file = tmp_path / "tester_fixture.log"
```

An f-string is not a way around the rule. `team/scripts/lint_test_anti_patterns.py` matches string-prefixed literals in `Path()`, `open()`, `os.makedirs()` and variable assignments, so `f"/tmp/..."` reads exactly like `"/tmp/..."`.

**When acceptable:** only where landing under bare `/tmp` IS the subject matter — a suite proving the litter check detects real top-level `/tmp` entries cannot plant its fixture inside the temp root that check excludes. Such a file is exempted by name in the lint's bare-`/tmp` subject-matter whitelist, and the exemption is deliberately narrow: it drops only the `Path()`/`open()` constructor checks, every other bare-`/tmp` construct in the file still being a finding. A file that earns the exemption still owes collision safety — route all of its real-`/tmp` fixtures through one helper that appends a fresh suffix per call:

```python
def _bare_tmp_probe(prefix: str) -> pathlib.Path:
    return pathlib.Path(f"/tmp/{prefix}-{uuid.uuid4().hex[:10]}")
```

For a single line that never touches the filesystem — a sample command string that happens to carry a `/tmp` path as data — opt the line out with `# anti-pattern-allowlist: 2 (justification: ...)` instead of widening the whitelist.

### A fixture's own git remote operation resolves inside the throwaway root

**Rule: a fixture may run `git push`, `git fetch` or `git pull` against a repository it created itself, provided the destination resolves to a filesystem path at or below `PGAI_AGENT_KANBAN_TEMP_DIR`. Name that destination explicitly — a path, or a remote whose configured URL is that path — and pass `-C <repo>` so a relative one resolves against the repository rather than the caller's directory.**

Every role but CM runs with a `git` shim ahead of the real binary on PATH, and that shim refuses the three remote verbs. The refusal reads the destination before it reads the role: a destination resolving inside the framework's own throwaway root is not the publishing hazard the boundary exists to stop, so it passes through for every role, CM or not. A destination anywhere else is refused for every role but CM — which is the environment coupling in this shape. A suite whose fixture pushes into a repository the author put outside that root passes in an operator's own shell and fails inside any agent's dispatch, with a `CalledProcessError` raised from fixture setup that reads as a broken tree.

Four things disqualify the exemption, each of them deliberately conservative — an unresolvable destination is refused rather than assumed harmless:

- **No destination token at all.** A bare `git push` leaning on the current branch's configured upstream resolves nothing the guard can read, so it is never exempt. Name the remote or the path.
- **A URL scheme or scp-like host syntax.** `file:///...` and `user@host:path` name a remote by construction, and neither is exempt even when the path underneath is throwaway space the test built seconds earlier.
- **A remote short name that does not resolve.** The short name is resolved to its configured URL by probing the repository; an unconfigured remote, or no probe-safe git binary to run it with, both fall through to the refusal.
- **Any resolved path outside the throwaway root.** Including one the test made itself. The root is the whole test.

```python
# BAD — a file:// URL. The guard reads a URL scheme as a real remote by
# construction, so this is refused under every non-CM role even though
# the path underneath is throwaway space this test created itself
subprocess.run(["git", "-C", str(local), "push", f"file://{origin}", "HEAD"], check=True)

# BETTER — a path under the framework's own throwaway root (pytest's
# tmp_path lives there), named explicitly, with -C so it resolves
origin = tmp_path / "origin.git"
subprocess.run(["git", "init", "--bare", str(origin)], check=True)
subprocess.run(["git", "-C", str(local), "remote", "add", "origin", str(origin)], check=True)
subprocess.run(["git", "-C", str(local), "push", "origin", "HEAD"], check=True)
```

Give the repository path with `-C` and not with `--git-dir`/`--work-tree`: the destination check tracks `-C` alone, and a relative destination given without it is resolved against the caller's directory, which is the coupling this whole anti-pattern is about.

**What tells you when you got it wrong.** The guard appends one JSON-Lines record per refusal — timestamp, role, task, and the full argv — to the file `PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE` names, which under a dispatch is the running task's `logs/git-boundary-guard.jsonl`. An operation that took the exemption records nothing. So the check is one run and one file: put the guard directory ahead of git on PATH, set a non-CM role and an evidence file, run the suite, and read the records. Your own fixture's argv in that file is the entire diagnosis. Both halves of that environment matter — with the role or the evidence sink unset the guard fails open and refuses nothing, so a run missing either proves only that the suite passes unguarded.

**When acceptable:** the exemption belongs to the destination, never to the caller — there is no "it is only a test" opt-out, and a fixture must not acquire one by pointing at a real remote and asking to be excused. A test whose subject IS the refusal does the opposite on purpose: it names a destination outside the throwaway root and asserts the refusal and its evidence record, which is what keeps the boundary's own refusal path covered.

### Subprocess environments are built from an explicit set, not inherited

**Rule: build a subprocess test's environment from an explicit, named set — `tests.fixtures.scrubbed_subprocess_env.build_scrubbed_env` — rather than from `dict(os.environ)` with names deleted back out of it. Where the test compares a captured surface against a committed golden, that is not a preference but a requirement: an inherited environment there makes the verdict a property of the host.**

An inherited environment makes the deletion list the thing that has to stay correct, and it is never correct for long: the next variable that changes the behaviour under test is one nobody thought to delete, so the suite goes red on one machine and stays green on every other. Building the child's environment up from a fixed set closes that structurally — a name the builder does not construct is absent, whatever the invoking shell carries.

```python
# BAD — the verdict now depends on what the operator happened to export
env = dict(os.environ)
env["MAX_TASK_SECONDS"] = "5400"

# BETTER — PATH/HOME/LANG from an explicit base, plus only what this test is about
env = build_scrubbed_env(
    home_dir=tmp_path / "home",
    extra_env={"MAX_TASK_SECONDS": "5400"},
)
```

`team/TESTING.md` ("When to reach for which helper") says when to reach for the builder and how it relates to the wake-specific `scrubbed_wake_env` layered on top of it.

**What tells you when you got it wrong.** `team/scripts/lint_test_anti_patterns.py` reports this under the category `AP7`, and both gated runners invoke it before pytest as part of the `test_anti_patterns` lint. It is quality-class: it records a finding and the run continues, so it warns you at authoring time rather than failing a release candidate.

The check is deliberately narrow. It fires only on the pairing that actually produces a host-dependent verdict — a file that both compares a captured surface against a committed golden **and** builds a subprocess environment by inheritance. Neither half alone is a finding.

**When acceptable:** inheritance stays acceptable for the ordinary case. A test whose assertions are about return codes, emitted files, or log lines needs a real `PATH` and a plausible `HOME`, and inheriting the ambient environment to get them costs nothing — over a hundred sites in `team/tests/` do exactly that and are correct. What is never acceptable is inheriting one underneath a golden comparison. `docs/inherited-environment-exposure-survey.md` carries the measured extent of the inherited-environment shape across the test tree, the commands that reproduce it, and why a blanket conversion of all of those sites is not the intended follow-up. Opt an individual line out with `# anti-pattern-allowlist: 7 (justification: ...)`.

### An absolute-path assertion is checked against a computed value, not a free-floating substring

**Rule: when a test asserts something about an absolute path, compare it against a value the test itself computed — a prefix built from the same root, or the path relative to that root — never a hardcoded path-segment literal searched as a substring of the whole absolute string.**

The whole absolute path always carries the ambient scratch root as its prefix — `PGAI_AGENT_KANBAN_TEMP_DIR`, or whatever pytest's `tmp_path` resolves under it — and that prefix is not something any individual test controls. A substring search over the whole path is a search over that prefix too, so a literal chosen to describe what the CODE did (`"/projects/" not in resolved_path`, meaning "the routing snippet did not append a projects segment") silently also matches what the CALLER's own scratch root happens to contain. The multi-project layout puts scratch space at `.../projects/<name>/...`, so a verifier running under its own dispatch prompt's directed scratch path turns this into a false failure on a tree with no defect in it.

```python
# BAD — searches the WHOLE absolute path, including the ambient scratch
# root prefix this test does not control
assert "/projects/" not in resolved_path, (
    f"Project-less debug log was incorrectly routed to a project directory: {resolved_path}"
)

# BETTER — asserts against a value computed from a root this test built,
# so only what the code under test actually appended can trip it
resolved_relative_parts = pathlib.Path(resolved_path).relative_to(kanban_root).parts
assert "projects" not in resolved_relative_parts, (
    f"Project-less debug log was incorrectly routed to a project directory: {resolved_path}"
)
```

A prefix comparison built from the same root the test constructed (`resolved_path.startswith(str(kanban_root / "projects" / name))`) is equally acceptable — what matters is that the expectation is a value the test computed, not a literal typed against the assumption that no caller's own scratch root will ever contain it.

**What tells you when you got it wrong.** `team/scripts/lint_test_anti_patterns.py` reports this under the category `AP8`, invoked by both gated runners as part of the `test_anti_patterns` lint. It is quality-class: it records a finding and the run continues. The check is deliberately narrow — it fires only when the hardcoded literal both starts and ends with `/` (a path-segment shape: `/projects/`, `/tmp/`), so the hundreds of ordinary, correct containment assertions elsewhere in the tree (`"1/3" in captured.out`, `"rc/v8.8.8" in result.stderr`) are untouched. Opt an individual line out with `# anti-pattern-allowlist: 8 (justification: ...)`.

## Anti-pattern 3 — Order-dependent state

`test_a` populates shared state that `test_b` reads. They pass in declaration order; reordering, or running `test_b` alone, breaks the suite in non-obvious ways, and the dependency is invisible to anyone reading either test.

```python
# BAD — test_b silently depends on test_a having run first
def test_a(): create_global_thing()
def test_b(): assert global_thing_exists()

# BETTER — each test self-contained via a fixture with teardown
@pytest.fixture
def global_thing():
    thing = create_global_thing()
    yield thing
    thing.cleanup()
```

Module-scoped fixtures are a milder form of the same anti-pattern: state shared across a module that no individual test makes explicit. Prefer function scope unless construction cost is genuinely prohibitive.

**When acceptable:** a multi-cycle integration test simulating a sequence of pipeline iterations may intentionally mutate state between steps, provided the whole sequence lives inside one test function and the docstring documents the step order. Cross-test ordering dependencies are never acceptable.

### Order independence is demonstrated, not assumed — and isolation is earned

**Rule: every suite owes the same per-test verdict in any collection order, and a suite is moved into its own process only after a reversed-order run shows a verdict that differs. Isolation on suspicion is not authoring; it is a permanent cost paid for a coupling nobody has observed.**

The evidence bar exists because both mistakes are expensive in opposite directions. Leaving a genuinely coupled suite in the shared process buys an intermittent failure that reads as a flake and trains everyone to re-run; isolating a suite that was never coupled doubles nothing visible but adds a second pytest session to every future run, forever, for no proven gain. The estate's own comparison — `team/scripts/verify-test-order-independence.sh`, which runs the collected set forward and reversed through `team/tests/conftest.py`'s `PGAI_TEST_ORDER` seam and diffs the per-test outcomes — is how a coupling stops being a suspicion and becomes a named test.

```bash
# BAD — a suite pulled out of the shared glob "because it looks state-heavy":
# a second pytest session on every run, for a coupling never demonstrated
python3 -m pytest team/tests/unit/test_state_heavy.py   # run separately, to be safe

# BETTER — demonstrate it first; the check names the test whose verdict moved
bash team/scripts/verify-test-order-independence.sh --target unit
```

The bar the check sets: its diff must name a test whose verdict differs between the two orders, AND running the responsible module alone must reproduce one of those two verdicts — confirming the module is the leak rather than a downstream victim. Only then does the suite move out of the shared glob into its own pytest invocation.

A related authoring habit belongs here, because it manufactures the same class of flake: never assert on elapsed wall-clock time. `assert elapsed < 10` couples the verdict to whatever else the host was doing; inject the sleep and assert on the number of iterations instead.

## Anti-pattern 4 — Production-coupling tests

A test passes because production code happens to contain a specific string, regex, or construct — not because the assertion captures the behavioral spec. The symptom is diagnostic: the failure message names a missing string rather than a broken behavior, telling the operator only that the implementation changed.

```python
# BAD — static source assertion couples the test to an implementation choice
assert "--sort=-version:refname" in self._text

# BETTER — behavioral assertion; the failure message names the broken behavior
lines = _run_recent_tags(_make_git_repo(tmp_path, tags=["v0.10.0", "v0.9.0"]))
assert lines.index("v0.10.0") < lines.index("v0.9.0"), \
    "Tags must be newest-first in semver order, not lexicographic order"
```

**When acceptable:** as cheap structural guards ("this file does not import `os.system`") paired with a companion behavioral test, or when there is no runtime invocation path to exercise — asserting a config template contains a required key. Document the intent with a comment beginning `# Static guard:` naming the invariant.

## Anti-pattern 5 — Side-effect-leaking tests

A test creates files, opens descriptors, forks subprocesses, or mutates environment variables without cleanup. Subsequent runs read stale state, parallel runs collide, and one test's failure masks another's.

```bash
# BAD — temp files without cleanup; stale stderr can mask a real failure
cmd 2>/tmp/pgai_kanban_tmp/stderr.txt

# BETTER — mktemp + trap guarantees cleanup even on test failure
WORK_DIR="$(mktemp -d -p "${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}" test.XXXXXX)"
trap "rm -rf '$WORK_DIR'" EXIT
cmd 2>"$WORK_DIR/stderr.txt"
```

```python
# BAD — bare open() leaks the fd on exception
fd = open(lock_file, "w"); fcntl.flock(fd, fcntl.LOCK_EX); do_thing(); fd.close()

# BETTER — context manager guarantees close on every path
with open(lock_file, "w") as fd:
    fcntl.flock(fd, fcntl.LOCK_EX)
    do_thing()
```

For Python prefer `tmp_path`, context managers, and `monkeypatch` (which auto-reverts on teardown); for bash, `mktemp -d` plus a `trap ... EXIT`.

**When acceptable:** a test that legitimately needs to leave artifacts for inspection must opt in via a flag or environment variable, default to cleanup, and document the opt-in with a comment beginning `# Cleanup opt-out:`.

## Anti-pattern 6 — Provenance and scaffolding in test names

The test-name application of core's naming principle. A test named after *why it was written* — a bug number, a version, a release gate — or after *internal scaffolding* — a phase label, a code-path tag — records which incident prompted it, not what the system is supposed to do. When the provenance is squashed away the name is a dangling reference, and a cold reader cannot tell what the test guards without reading its body. It is also the test-name form of Anti-pattern 4: the name couples to the implementation's history instead of the behavioral spec.

```python
# BAD — names encode provenance/scaffolding, not behavior
def test_v0_49_0_bug_fix(self): ...
def test_gate3_pathB_works(self): ...

# BETTER — names describe the behavior, readable in isolation
def test_agent_marks_task_and_queue_done_after_task_completes(self): ...
def test_unwind_rc_applies_branch_prefix_to_develop_checkout(self): ...
```

The same rule governs test *file* names: name the file after the unit or behavior under test (`test_unwind_rc.py`), never after the incident that motivated it. A test locking in a former regression is welcome — name it after the behavior that regressed, and let git history hold the "why."

### The version-named-test rule, and how it is enforced

The release-named shape is the one form of this anti-pattern a lint can catch without guessing, so it has its own rule: **a test module or test function must not be named for the release that produced it.**

```python
# BAD — file and function both named for the release, not the behavior
# file: test_v1_4_0_queue_fix.py
def test_v1_4_0_queue_fix(self): ...

# BETTER — both named for the behavior the test locks in
# file: test_queue_marker_parsing.py
def test_queue_parser_keeps_blocked_marker_on_requeue(self): ...
```

`team/scripts/lint_hygiene.py`'s `version-named-test` class enforces it across the test tree, reporting two labels — `version-named-test-file` for the module's own filename and `version-named-test-function` for a `def test_...` line. It targets the *pattern*, not the mere presence of a version-shaped token, and is narrowed on two axes so a legitimate name is never flagged:

- **Position.** Only a version token sitting immediately after the mandatory `test_` prefix is a candidate. A version appearing later in the name — because the version string is the test's own subject, as in `test_compares_v1_4_0_against_v1_3_0` — is left alone.
- **Subject reappearance.** Even a prefix match is dropped when the same version, in its dotted prose form, reappears in the surrounding text (the function's own body for a function-name match, the whole file for a module-name match). A test whose assertions repeat `v1.0.0` is genuinely about that version, not labelled with it.

Findings are reported, never blocking. Where a name genuinely needs to keep its version token and neither narrowing covers it, suppress the one line in the allowlist sidecar with a justification — `docs/coding-standards.md`, "The allowlist sidecar," carries the format and the command that writes it.

**When acceptable:** never — in names, docstrings, OR comments. A docstring describes the behavior the test locks in, not the incident that motivated it; internal bug identifiers and version citations are forbidden in test names, file names, docstrings, and comments alike. The single exception is a skip annotation citing an OPEN follow-up bug (below) — a live, verified cross-reference deleted when the bug closes. Bug-shaped and version-shaped VALUES used as test DATA remain fine.

## Grep-zero gates target behavior patterns, and read their whole subject

A grep-zero gate is a sweep test that asserts a forbidden shape appears nowhere in a named subject — no direct `flock` call outside the lock library, no unfiltered `git describe --tags`, no `sys.path` built from a dev-tree variable. It is the one place the pattern-scan caution of Anti-pattern 1 is inverted: covering every current and future occurrence is the whole point. Two rules keep that power from turning into noise.

**Target the behavior, not the name.** A gate keyed on an identifier passes the moment someone renames the thing, and fails the moment an unrelated symbol happens to share the spelling. Key it on the construct: the call shape, the flag usage, the path form.

**Read the whole subject.** No `head`, no fixed result limit, no early `break`. A sweep that stops at the first N hits reports "zero violations" for a tree whose first N lines were clean, which is a green light nobody earned.

```python
# BAD — targets an identifier by name, and truncates the sweep
out = run("grep -rn pgai_lock_acquire team/scripts | head -20")
assert out == ""

# BETTER — targets the construct, reads every candidate file in full
DIRECT_FLOCK = re.compile(r"^\s*flock\s+-n\s")
violations = [
    f"{path}:{n}"
    for path in sorted(Path("team/scripts").rglob("*.sh"))
    for n, line in enumerate(path.read_text().splitlines(), 1)
    if DIRECT_FLOCK.search(line) and path.name not in ALLOWED_CALL_SITES
]
assert not violations, (
    "advisory locks must be taken through the lock library: " + ", ".join(violations)
)
```

**When an identifier is the only available target:** some invariants are genuinely about a name — one canonical field name for a concept, a retired literal that must not come back, a reserved template placeholder, a lint checking for name collisions. Those are legitimate, and the tree carries several. Each one documents itself at its own site with a rationale paragraph beginning `IDENTIFIER-BY-NECESSITY:` naming why no behavior pattern exists to target instead, so a later reader does not mistake the narrow scope for an oversight.

**Every grep-zero gate owes a negative proof.** A sweep that has never been shown to fire is indistinguishable from a sweep whose regex never matches anything. Pair each gate with a test that seeds a synthetic violation and confirms the gate reports it.

## Skipped tests must cite a REAL, existing follow-up bug

The never-block test-handling rule is: when updating a specific test is non-obvious, mark the test skipped AND file a follow-up bug rather than blocking the release. **The second half is not optional.** A `skip()` / `@pytest.mark.skip` / `# SKIP:` annotation citing a bug must reference a real `BUG-NNNN` whose file exists in the project's `bugs/` directory. A skip citing a placeholder ID, a non-existent bug, or no bug at all is a verification FAILURE, not an acceptable deferral.

```python
# BAD — placeholder ID; no such bug was ever filed
@pytest.mark.skip(reason="compute_layout floor case — BUG-SKIP-compute-layout-floor-n1")

# BETTER — cites a real, filed bug whose file exists in bugs/
@pytest.mark.skip(reason="compute_layout floor case deferred — see BUG-NNNN")
```

This is the recurring false-completion pattern: an agent claims the verifiable-but-unverified half of a two-step instruction. An instruction designed to *prevent* blocking becomes a new place to fake completion when the "file a bug" half is faked with a placeholder ID and a comment claiming a bug was filed. The cure is grep-gate completeness: **do not trust the comment — grep for the ID and confirm the file exists.** The gate is enforced mechanically by `team/scripts/lint_skip_bug_gate.sh`, which both gated runners invoke before pytest, and TESTER runs the same check as a standing verification step (`team/roles/TESTER.md`, Step 6.13).

**When acceptable:** a skip that cites no bug because it is an infrastructure guard — skipping cleanly when a required path or optional dependency is absent — is not a follow-up-bug skip and needs no `BUG-NNNN`. The rule binds skips that defer a test because fixing it is non-obvious.

### Put the citation inside the skip statement, not beside it

**Rule: the citation belongs in the skip annotation's own arguments — the `reason=` of `pytest.skip()`, `@pytest.mark.skip`, or `@pytest.mark.skipif` — never in an adjacent comment or docstring.**

On the Python surface the gate parses each file and identifies skips as syntax nodes, so the only text it examines for a citation is the text belonging to a skip statement. Two consequences bind the author. A bug ID written anywhere else — a comment above the decorator, the test's docstring — is invisible to the gate and buys nothing, however convincing it looks to a reader. And a bug-shaped identifier appearing in a docstring or in sample data is not a citation, is never examined, and is not a finding. Line shape is no longer a variable either: a citation split across a continuation line is read exactly like a single-line one, so wrapping a long reason neither hides a placeholder nor breaks a real cite.

```python
# BAD — the statement cites nothing; the ID sits where the gate never looks
# deferred until BUG-NNNN lands
@pytest.mark.skip(reason="compute_layout floor case")

# BETTER — the citation is an argument of the skip itself, in any line shape
@pytest.mark.skip(
    reason="compute_layout floor case deferred — see BUG-NNNN",
)
```

Shell and Markdown files under the test tree have no syntax tree to read, so the gate keeps its line-oriented grep there. On those surfaces keep the citation on the same line as the skip.

## Tests that reach outside their own process

Everything above is about what a test asserts. These five contracts are about what a test *touches*: an external binary, a subprocess, the shipped entry point. They exist because the estate must produce the same verdict on any host — a tmux-less container, a gitless install, a cold machine — and because a test instrument that fails on the host rather than on the tree is an outage of its own.

### Reach for an external binary only through the shared guard

**Rule: a test that invokes an external binary — `tmux`, `docker`, `git` — resolves it through the shared guard in `team/tests/fixtures/binary_guard.py` (re-exported from both conftests), which narrates and skips when the binary is absent. Never call a binary the host may not have without one.**

An unguarded call to an absent binary surfaces as a `FileNotFoundError` ERROR, which reads as a broken tree when the tree is fine and the host is merely missing a package. The skip is narrated — it prints a reason naming the binary before skipping — because a skip nobody can see in the run log is indistinguishable from a pass.

The teardown half is what authors get wrong. A `finally` block written on the assumption that setup resolved the binary reaches for it again when setup skipped, and raises where a skip was already owed. `guard.teardown()` is a no-op when the guard never resolved, which is what makes an unconditional `finally` safe to write — and it still lets a genuine teardown fault propagate instead of swallowing it.

```python
# BAD — a private probe per module, and a teardown that assumes setup resolved
tmux = shutil.which("tmux")          # None on a tmux-less host, and nobody skipped
try:
    yield tmux
finally:
    subprocess.run([tmux, "-L", socket, "kill-server"])   # TypeError when tmux is None

# BETTER — one shared guard: the skip names the binary, the teardown is safe
guard = require_binary("tmux", label="pane-signal-traps")
try:
    yield guard.path
finally:
    guard.teardown(lambda path: subprocess.run([path, "-L", socket, "kill-server"]))
```

A fixture that *raises* on an absent binary is the same violation wearing a different exception: a helper whose contract is "skip when unavailable" must not hand back a `RuntimeError` instead.

**A binary on PATH is not a working engine.** For a container engine the daemon behind the binary can be stopped or pointed at a socket that answers nothing, and that is a different fact with a different remedy — "install docker" does not fix "start the docker daemon." `require_docker_daemon()` probes the daemon after the binary resolves and skips with a reason that says which of the two failed.

Each guard class carries the negative proof the grep-zero rule above demands: a self-test suite seeds an unguarded call, a raising guard, an unconditional teardown after a skipped setup, and a present-binary / unreachable-daemon case, and pairs each with a companion showing the same scenario skipping cleanly once routed through the guard.

**When acceptable:** the guard is owed by the *call*, not by the mention. A test that never invokes the real binary — a fake stub, an assertion about a command string it only builds — needs no guard.

### State your working directory; never inherit it

**Rule: a test that launches a subprocess passes an explicit `cwd` computed from the test file's own location — not from an environment variable, not from `git rev-parse`, and never the directory the interpreter happened to be started in. Scope it to the call; never `chdir` the interpreter.**

An inherited cwd makes the suite's verdict a property of the invocation instead of the tree: green from the repo root, red from `team/` or from a scratch directory, with a failure that accuses the tree. Self-location also survives the hosts this estate promises to run on — a gitless install has no `git rev-parse`, and a scrubbed environment has no variable to read. A process-wide `chdir` is worse than an inherited cwd, because it leaks into every later test in the same session.

```python
# BAD — inherits whatever directory pytest was launched from
subprocess.run(["bash", str(script), "--suite", suite], capture_output=True)

# BETTER — one helper; the cwd is computed from this file's own location
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent     # team/tests/unit/ -> team/
_REPO_ROOT = _TEAM_DIR.parent

def _run(args, *, cwd=_REPO_ROOT):
    return subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=str(cwd))
```

The cheap confirmation: run the suite from the repo root, from `team/`, and from a directory outside the repository, and compare the three results.

### Surface the subprocess's own words when you fail on it

**Rule: every assertion that depends on a subprocess result carries that subprocess's full stdout and stderr in its failure message.**

A bare exit-code assertion reports that something failed and throws away the process's own explanation of why. The confession was on stderr, the test had it in hand, and the operator now re-runs the command by hand to recover what the run already knew. Route every such message through one renderer so no call site can forget it.

```python
# BAD — the process explained itself and the assertion discarded the words
assert result.returncode == 0

# BETTER — one renderer, used by every subprocess-dependent assertion
def _diagnostic(result):
    return f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"

assert result.returncode == 0, _diagnostic(result)
```

**When acceptable:** never, for an assertion whose subject is a subprocess result. Prove it once by breaking a harness-driven assertion on purpose and confirming the pytest failure message quotes the subprocess's own stderr.

### Prove an operator-facing behaviour through the production entry point

**Rule: every operator-facing behaviour owes at least one test that drives the shipped entry point as a real subprocess — from a working directory outside the repository, under an environment rebuilt rather than inherited. A fixture that extracts or sources the mechanism proves the mechanism, and that is a smaller claim.**

Mechanism and wiring fail independently, and only one of them is covered by a block-extraction fixture. Such a fixture runs the shipped arithmetic verbatim and proves it is right; it is silent about whether anything calls it. The gap is not hypothetical — commenting out the single `source` line connecting the entry point to that block makes the entry point die before it ever derives anything, while the mechanism fixture stays green and useless.

The environment is rebuilt, not inherited, for the same reason the cwd is: `PATH`, `HOME` and the locale are constructed from scratch and the operator's own kanban variables scrubbed, with `HOME` pointed at an empty scratch directory so no operator config can leak in. A behaviour that only holds inside the author's shell has not been proven to hold for an operator.

```python
# BAD — proves the arithmetic, says nothing about whether it is ever reached
block = _extract_block(WAKE_BOOTSTRAP, "BG-WAIT-CEILING-DERIVATION")
out = subprocess.run(["bash", "-c", block + "\necho $BASH_MAX_TIMEOUT_MS"], ...)

# BETTER — the real entry point, a foreign cwd, a rebuilt environment
result = run_wake_entrypoint(
    root=build_synthetic_kanban_root(tmp_path),
    env=scrubbed_wake_env(extra_env={"MAX_TASK_SECONDS": "600"}),
    cwd=make_foreign_cwd(tmp_path),
)
```

An acceptance test drives production code, so it owes the sandbox rule in full: a synthetic kanban root under the framework temp space, and a before/after check proving the live install and the operator's crontab are byte-identical after the run.

**When acceptable:** the mechanism fixture is not replaced by the acceptance test — keep both. When the pair disagrees, the mechanism fixture is what tells you which half broke.

### Own every process your subprocess starts

**Rule: a test that launches a subprocess owns every process that subprocess starts, and owes that ownership hardest at the moment its own timeout fires. Give the invocation its own process group and kill the whole group on timeout, then let the timeout stand. A finished run leaves no descendant of the test alive.**

`subprocess.run(..., timeout=N)` SIGKILLs the one child it spawned and nothing else. A child that is itself a wrapper — a shell script, a PATH shim, anything that runs another command as an ordinary foreground child — has descendants that kill never reaches. They are reparented to init and keep running, or block forever reading a pipe whose writer is already gone. Nothing fails at the time; the test that leaked them reports its own timeout and the suite moves on.

The cost lands on the host instead, and on tests that have nothing to do with the subprocess. One stray per timeout, multiplied across a suite, is a population of processes the run never accounts for, and the first assertion to fall over is usually a time-bounded one somewhere else entirely — which is the hardest failure in the estate to attribute, because the test that reports it is not the test that caused it.

```python
# BAD — the timeout kills one pid; anything that pid spawned survives it,
# reparented to init, and this call returns as though it cleaned up
result = subprocess.run(argv, capture_output=True, text=True, timeout=15)

# BETTER — own the group, kill the group, and still fail on a real hang
proc = subprocess.Popen(
    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, env=env, start_new_session=True,      # its pid is also its pgid
)
try:
    stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
except subprocess.TimeoutExpired:
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # every descendant, any depth
    proc.communicate()                               # drain and reap, no zombie
    raise                                            # a genuine hang still fails
```

Two details carry their weight. Re-raise after the cleanup rather than swallowing the timeout — a helper that tidies up and returns quietly converts a hang into a silent pass. And hand the child a stdin you control (a pipe you write and close, or `DEVNULL`); a descendant that inherits an open pipe it can neither read to completion nor be woken from is exactly the process that sits blocked for an hour.

`run_dashboard_script` in `team/tests/fixtures/dashboard_render_fixture.py` is this shape as a shared helper. Reach for it rather than re-rolling the `Popen` block per file.

A callee may defend itself — the git-boundary-guard shim arms a parent-death signal on its hand-off to the real binary, so that one invocation dies with whatever spawned it — but that is the callee's guarantee about itself, not yours about your subprocess tree. A test never gets to assume the program it invoked was written that way, and the program it invoked is rarely the last process in the chain.

**The observable a reviewer can check.** After the suite finishes, the set of live descendants of the test process must be empty. Assert it from inside the suite by walking `/proc` for every process whose parent chain reaches the test's own pid once the call has returned; check it from outside with `ps -eo pid,ppid,etimes,args`, where a stray shows up twice over — as a helper still alive with an age past the timeout that was supposed to bound it, and as `PPid` 1, the signature of a process whose spawner died without it. The coarse host-level tell is a load average climbing past the core count during a run and dropping when the strays are killed.

**When acceptable:** a call whose child is a single binary that forks nothing leaks nothing, and a bare `subprocess.run` timeout is honest there. You rarely know that, though — a wrapper can be interposed between the test and the binary by the environment the suite runs under rather than by anything the test says — so the group is the default and the bare timeout is the exception you can justify.

## Rule of thumb

If a test passes today because production code happens to contain a string, match a pattern, or sit at a path — rather than because production behavior satisfies a documented spec — the test is one legitimate change away from spurious failure. The cost of a spurious failure is not the five-line fix; it is the operator interruption, the waiver, and the erosion of autonomous-build discipline.

Encode intent. Bound scope. Clean up after yourself. Make the failure message tell the next operator what behavior was supposed to hold.

---

## Test fidelity verification

A recurring failure mode: a test passed because it mocked away the exact condition that broke in production. Whenever you meet test results — passing or failing — ask whether the tests covering the RC's changes exercise the real breaking condition or a sanitized proxy. This informs judgment when categorizing findings; it grants no new blocking authority, and `team/TESTING.md` carries the project-wide conventions and the helpers that close each gap.

1. **Real strict-mode bash exercised** — if the code under test is shell logic depending on `set -euo pipefail`, does at least one test source the real entry point so an `errexit` chain-killer would surface?
2. **Installed tree simulated, not the dev tree** — if the code resolves paths, imports modules, or looks for shim packages, does a test exercise install-then-invoke against a simulated installed root?
3. **Multi-project fixtures for multi-project code paths** — does the fixture carry at least two projects, so cross-project isolation is actually exercised?
4. **Stubs match production behavior** — does a stubbed `log()`, path resolver, or other shared primitive reproduce the behavior that matters (`tee`-to-stdout, side effects on captured output, exit-code semantics)?
5. **Real first-run env conditions exercised** — canonical-var-only, legacy-var-only, and both-unset fresh-customer cases, not just the developer's convenient configuration.
6. **No state-mutating script run against the live install** — the RC's tests and any reproduction steps a CODER followed sandbox state-mutating scripts to a throwaway root. The in-harness pytest sandbox protects code run through the gated runners; it does not protect ad-hoc invocations.

A concrete concern — one where you can point at the specific mocked-away condition — is a `bug` finding filed via Path C. A plausible but non-concrete one goes under Caveats.

## Live-install safety

**The same discipline binds the commands an agent types by hand, and this section is the canonical statement of it.** `team/roles/CODER.md`, `team/roles/TESTER.md` and `team/roles/WRITER.md` carry a short runnable form of the rule and cite this section for the rest; where they differ from it, this section is the one to trust.

Any script that writes under the kanban root — the project create/add/remove scripts, `cleanup.sh`, a reproduction step being replayed, or a test file run directly outside the gated runners — runs against a throwaway root anchored under `$PGAI_AGENT_KANBAN_TEMP_DIR` via `mktemp -p` or `pgai_mktemp_d`. Repointing one variable does not put it there. Five names in the inherited environment resolve to the operator's live install, and a sandbox holds only when the command sets or clears all of them: `PGAI_AGENT_KANBAN_ROOT_PATH`, `KANBAN_ROOT`, `PGAI_TASKS_DIR`, `PGAI_PROJECT_ROOT`, and `PGAI_AGENT_KANBAN_TEMP_DIR`.

```bash
SANDBOX="$(mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR" sandbox-XXXXXX)"
mkdir -p "$SANDBOX/tmp"
env -u PGAI_TASKS_DIR -u PGAI_PROJECT_ROOT \
    KANBAN_ROOT="$SANDBOX" \
    PGAI_AGENT_KANBAN_ROOT_PATH="$SANDBOX" \
    PGAI_AGENT_KANBAN_TEMP_DIR="$SANDBOX/tmp" \
    bash team/scripts/cleanup/cleanup.sh --project <name> --dry-run
```

**Why overriding one root is not a sandbox.** A script that resolves its paths through `scripts/lib/project_paths.sh` reads `KANBAN_ROOT` and never falls back to `PGAI_AGENT_KANBAN_ROOT_PATH` — override the documented name alone and every resolved path still names whatever root the inherited `KANBAN_ROOT` holds. `cleanup.sh` reconciles the two before any destructive step: it derives whichever is unset from the other, and when both are set and disagree it refuses dual-channel (`cleanup.root_mismatch`, quoting both values) rather than guess. Read that refusal as a half-built sandbox and re-run with both roots on the same throwaway path. No other script carries that guard.

**Why two of the five are cleared rather than repointed.** `PGAI_TASKS_DIR` and `PGAI_PROJECT_ROOT` arrive as fully-resolved live-install paths and take precedence over anything either root derives, so no root override reaches them — leave `PGAI_TASKS_DIR` in place and a run whose every other step is sandboxed still prunes task folders from the operator's tasks directory. `PGAI_AGENT_KANBAN_TEMP_DIR` governs temp-space sweeps on its own, so a run that purges temp space needs it pointed inside the sandbox too.

The in-harness pytest sandbox protects code run through the gated runners; it does not protect ad-hoc invocations launched by hand. The forbidden shape is "run the script with whatever environment the wake script inherited" — that environment points at the operator's live install, and an overnight run can leave stray fixture projects in the operator's `projects.cfg`, or delete task folders that nothing can bring back.

### A copied worktree is not an isolated tree

The same class of hazard — an inherited pointer that makes a seemingly-isolated command act on real state — exists one layer down, in git, and it is not covered by any of the five variables above.

A per-task working directory is normally a **linked worktree**, and in a linked worktree `.git` is not a directory. It is a one-line file naming the shared repository's metadata:

```
gitdir: /home/you/develop/<repo>/.git/worktrees/<name>
```

A recursive copy of the working directory copies that pointer, so the copy is not an isolated repository: every git command run inside it resolves through the pointer and mutates the **original** repository's branches, HEAD, and index. Nothing in the output says so. `git status` in the copy reports the real branch because it is reading the real branch, which is exactly why the shape is silent by construction.

**The verbs that make it destructive are the history-rewriting ones** — `git revert`, `git reset`, `git checkout -B`, `git rebase`. Run inside a copied worktree, they move the branch the original worktree has checked out, while every path the agent typed sat under its own scratch directory. That shape has already moved a live feature branch onto a revert commit once. Recovery is a reflog walk, and only if someone notices.

**Three safe ways to get a tree you can experiment in.** Each runs as written from inside the worktree; pick one.

```bash
SCRATCH="$(mktemp -d -p "$PGAI_AGENT_KANBAN_TEMP_DIR" scratch-XXXXXX)"

# 1. Clone it. The clone gets its own .git directory and its own refs;
#    nothing you do inside it can reach the source repository.
git clone "$(git rev-parse --show-toplevel)" "$SCRATCH/clone"

# 2. Ask git for a second worktree, then hand it back. Detached at HEAD,
#    so commits made in it move no branch, and git tracks it as its own.
git worktree add --detach "$SCRATCH/wt" HEAD
#   ...experiment...
git worktree remove --force "$SCRATCH/wt"

# 3. If a copy already exists, sever the pointer BEFORE any git command
#    runs in it. A tree with no .git is inert.
cp -r . "$SCRATCH/copy" && rm -rf "$SCRATCH/copy/.git"
```

Option 1 is the default answer: it is the only one of the three where a mistake cannot reach the source repository at all. Option 2 keeps the shared object store, so it is cheap on a large repository, but it is a real worktree of the real repository — reach for a branch name in it and you are back in the hazard. Option 3 is the repair, not the plan; it holds only while no parent directory of the copy is itself a git repository, which is why the copy belongs under `$PGAI_AGENT_KANBAN_TEMP_DIR` and nowhere else.

The rule in one line: **`cp -r` of a worktree is a copy of the files and a share of the repository.** If you want isolation, clone.

---

## The two gated test scripts

The repository ships two test scripts TESTER agents and humans run to verify the system. Both take an optional `--verbose`; both also accept `--coverage` (opt-in, never part of a verdict) and the `--files`, `--quarantined` and `--reuse-if-green` flags described under "Runner flags, the green ledger, and exit files" below. **`scripts/run-unit-tests.sh`** runs the pytest unit suite at `team/tests/unit/` — pure-Python logic: queue parsing, materializer output, status-file parsing. **`scripts/run-integration-tests.sh`** runs the pytest integration suite at `team/tests/integration/` — wake-script behavior end to end against a temporary kanban tree. Exit codes are the same for both: `0` all passed, `1` one or more failed (or the lint pre-flight failed, or — on a `--files` run only — a named file carries a test anti-pattern or skip-citation finding), `2` pytest not installed (or, on a `--pre-merge` run, the run could not start — see "The pre-merge run" below). Install pytest with `pip install pytest --break-system-packages`; verify with `python3 -m pytest --version`.

**Who runs them when.** TESTER runs both as certification runs once per RC verification cycle (`team/roles/TESTER.md`, Step 5a), cross-referencing the anti-patterns above when judging the quality of new or modified tests. It can run them one at a time, or together through `scripts/run-gated-suites.sh`, which splits one `[testing] workers` budget between them and leaves the same evidence. CODER runs the tests for the files it added or changed, with `--files`, and the pre-merge run ("The pre-merge run" below) before merging, and conforms new tests to those same guidelines; a full suite is never a CODER ticket's acceptance, because TESTER runs the full suites once per RC. Humans run both before merging an RC to main.

**Test order is stated, not randomised.** Both runners invoke pytest with `-p no:randomly`, neutralising the `pytest-randomly` plugin when it is installed — without the flag, the two certifying suites execute in a fresh random order on every invocation, with the seed recorded only in captured output, so a one-in-two failure costs a log archaeology pass to reproduce. With the flag, both runners collect and execute in pytest's own stable collection order every time, and each prints a `Test order: stated (pytest-randomly neutralized via -p no:randomly).` line before pytest starts, confirming the policy was in effect for that run. This is the same neutralisation `team/scripts/verify-test-order-independence.sh` already applies to its own forward/reverse passes (see "Anti-pattern 3 — Order-dependent state" above); `-p no:randomly` is a documented no-op when the plugin is absent, so the policy costs nothing on a host that never installed it.

**Stated order is half of a replay; the environment is the other half.** A run's identity is the argv pytest was handed *and* the environment it executed under. Both runners set a known family of variables before launching pytest — a `PYTEST_ADDOPTS` carrying the `--basetemp` this invocation claimed for itself, the `PGAI_*` path and crontab-seam variables, and the `KANBAN_ROOT` / `PYTHONHASHSEED` / `PGAI_LINT_CHANGELOG_MODE` trio the CHANGELOG-freshness lint needs earlier in the same run — and several of those names are deliberately left unexported, or unset again, by the time pytest actually starts. An operator's own shell commonly exports some of the same names ambiently; `KANBAN_ROOT`, after sourcing `shell-env`, is the usual one. Re-run the bare pytest argv in such a shell and you run the same tests under a materially different environment, which is how a genuinely failing suite comes back green and leaves the reader concluding the runner lied. Stating the order removes the seed from a replay; it does nothing about this.

**Every invocation gets its own basetemp.** pytest removes and recreates whatever `--basetemp` it is handed at session start, so two live pytest sessions sharing one path delete each other's fixture directories mid-run: the run still going fails on whichever `tmp_path`-backed test it touches next, indistinguishable from a flake. No runner therefore pins a fixed per-suite path — neither of the two above, nor their two siblings, `scripts/run-gated-tests.sh` (the docker-gated shipped-image module) and `scripts/run-cold-build-tests.sh` (the cold-build lane). Each invocation claims a directory that belongs to it alone, `${PGAI_AGENT_KANBAN_TEMP_DIR}/pytest/<suite>-<scope>.XXXXXX`, where `<suite>` is the runner's own — `unit`, `integration`, `gated`, or `cold` respectively — `<scope>` is `PGAI_TASK_ID` when the wake layer exported one and `pid<pid>` of the runner otherwise, and the six-character suffix is `mktemp`'s — so two runs under the same scope, such as a nested runner spawned by an integration test, still resolve different directories. A second run of the same suite, a sibling agent's run, and any other runner each get their own; nothing shares. The helper is `gated_runner_pytest_basetemp` in `team/scripts/lib/gated_runner_lint.sh`, which carries the scoping rules in full.

**The directory lives exactly as long as the run.** The runner removes its basetemp on its own exit path, whatever the exit code, so the `pytest/` subtree never accumulates one directory per historical run — the bound is no leftovers, not a rolling keep. One consequence follows: a failing test's retained `tmp_path` is gone once the runner exits. To inspect one after the fact, paste the replay line one of the two main runners prints — it runs pytest without the runner's exit hook, at the same path, and leaves the basetemp in place. The two siblings print no replay line.

**What the printed line guarantees.** Immediately before pytest starts, each runner prints one `Replay this exact run:` line, and that line is the whole command rather than a fragment of it: the `cd` into the directory pytest was launched from, an `env` prefix pinning every name in the replay family, and the pytest invocation itself. A name the runner had exported at that moment is carried as a literal `NAME=value` assignment, shell-quoted so a value holding spaces or metacharacters still pastes and runs. A name it did not have exported is carried as `env -u NAME`, forcing that name absent — so the paste is never silently handed whatever value the pasting shell happens to export for it. Both runners build the line through one shared helper, `gated_runner_replay_command` in `team/scripts/lib/gated_runner_lint.sh`, over the one list of names that file declares, so the unit and integration lines carry the same family and a newly added runner variable joins both at once.

The shape it prints, illustrative — the concrete values differ per host and per run, and the `--basetemp` value is the directory this one invocation claimed, so it is never the same twice:

```bash
Replay this exact run: (cd "/path/to/dev-tree/team" && env -u PYTHONHASHSEED -u PGAI_LINT_CHANGELOG_MODE -u KANBAN_ROOT PYTEST_ADDOPTS=--basetemp=/tmp/pgai_kanban_tmp/pytest/unit-pid41230.Xa7Qb2 PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp PGAI_AGENT_KANBAN_ROOT_PATH=/home/you/pgai_agent_kanban python3 -m pytest -p no:randomly tests/unit/)
```

**What you still have to arrange.** Copy the line whole, opening parenthesis to closing parenthesis, and run it in any shell: the environment it needs travels with it, and there is still no seed to capture. Three things do not travel and are yours to arrange. The checkout must still be at the commit that failed. `pytest` must be installed for whichever `python3` your shell resolves. And the paths the line names — the tree it `cd`s into, the `pytest/` directory its `--basetemp` sits under — must still exist. The basetemp itself need not: the runner removed it at exit, the cleanup leaves the parent in place, and pytest recreates the directory from the path in the line. Nothing outside the replay family is pinned either: `PATH`, `HOME`, an active virtualenv, and any variable a test reads that the runner never sets are inherited from your shell as they always were. A test that depends on one of those has a reproduction gap, and the repair is to add that name to the replay family in `team/scripts/lib/gated_runner_lint.sh` — one edit, covering both runners — never to hand-extend the printed line.

---

## Runner flags, the green ledger, and exit files

Both gated runners take three flags that change what kind of run you are making. A **certification run** runs the whole suite: TESTER reads its outputs, and its result is the suite's verdict at that commit. A `--files` run is targeted and writes none of those outputs. A `--quarantined` run writes them, but marks its ledger record so it never stands in for a run of the suite. Each runner's `--help` is the authoritative statement of these flags; this section says when to reach for each and what each run leaves behind.

| Run | What pytest runs | Certification run? | What it writes |
|---|---|---|---|
| plain | the whole suite, quarantined tests excluded | yes | `lint/findings-<suite>.json`, one green-ledger record |
| `--files PATH ...` | exactly the named test files, quarantined tests excluded | no | `targeted-<suite>.exit` only |
| `--quarantined` | exactly the quarantined tests | no — not a run of the suite | `lint/findings-<suite>.json`, one ledger record whose selection is `quarantine` |
| `--reuse-if-green` | the whole suite, or nothing when the ledger already certifies this commit green | yes | `lint/findings-<suite>.json`; a ledger record only when pytest actually ran |

Every path in this section sits under the framework temp root, `$PGAI_AGENT_KANBAN_TEMP_DIR` (default `/tmp/pgai_kanban_tmp`).

### `--files`: run only the tests you touched

```bash
# Run from the repository root. Paths are relative to team/; a team/-prefixed
# or absolute path is also accepted.
bash team/scripts/run-unit-tests.sh --files tests/unit/test_semver.py
bash team/scripts/run-integration-tests.sh --files tests/integration/test_verify_rc_state_release_check.py
```

This is how CODER proves a change: run the test files the change added or modified, and nothing else. pytest runs exactly the named files. Each path must be an existing file under the runner's own test roots — `tests/unit/` or `pgai_agent_kanban/api/tests/` for the unit runner, `tests/integration/` for the integration runner. A path that only looks as if it sits there (`tests/../../etc/passwd`) is resolved first and refused.

**It is not a certification run, and it says so.** The run prints `targeted run — not a certification run` before pytest starts.

**The breakage pre-flight still runs.** The fatal gates — the breakage-class check (syntax, compile, import) and the container-engine-free lint, plus the Dockerfile static and API-parity gates on the unit runner — run exactly as on a certification run, and a failure there still fails the run. A targeted run cannot hide a broken tree.

**Two per-file test-authoring checks run over the named files, and only those files.** After the pre-flight and before pytest, the runner applies the two quality-class checks that judge one test file at a time:

- **The test anti-pattern lint**, `lint_test_anti_patterns.py --files`, runs every check it makes on a test file over each named file.
- **The skip-cites-a-real-bug gate**, `lint_skip_bug_gate.sh --files`, holds every skip in the named files to the rule in "Skipped tests must cite a REAL, existing follow-up bug" above.

Neither check walks a directory or scans runtime scripts on a targeted run, so its cost grows with the files you name, not with the tree. Each prints its findings in full, naming file and line, then one summary line: `[targeted-lint] <name>: OK`, or `[targeted-lint] <name>: finding(s) in the named files`.

**A finding makes the targeted run exit non-zero.** When every named test passes but either check reported, the run exits 1 with an error key, a plain-language message naming the checks that reported, and a remedy that re-runs exactly those checks over the named files. A pytest failure keeps its own verdict; the lint verdict applies once the named tests are green. Fix the reported lines and re-run. For an anti-pattern finding you mean to keep, use the per-line `# anti-pattern-allowlist: <N> (justification: ...)` marker where the finding's own text offers it.

**Everything else in the quality class is still skipped.** A targeted run does not run the whole-tree guard tests, which assert that the entire test tree carries zero findings of some kind. It does not run the rest of the quality-lint fleet either. Both run once per RC, in TESTER's certification runs. A clean targeted run therefore says the named files pass and carry no per-file finding; it says nothing about the rest of the tree.

**The checks record nothing.** Their findings print and set the exit code, and that is all. A targeted run never writes `lint/findings-<suite>.json`, never reads or writes the green ledger, and never files bugs; its only output file is `targeted-<suite>.exit` (see "Exit files" below).

**No `--coverage`.** `--files` combined with `--coverage` is refused before pytest starts, with an error key, a plain-language message, and a remedy. Drop one of the two flags.

### `--quarantined`, and the quarantine marker policy

A **quarantined test** is one whose verdict depends on test order or wall-clock timing rather than on the code under test. It carries `@pytest.mark.quarantine(reason="...")`, or a module-level `pytestmark = pytest.mark.quarantine(reason="...")` when every test in the module shares the dependence. The marker is registered in `team/pyproject.toml`.

Both runners pass pytest `-m "not quarantine"` on every run by default, `--files` runs included. `--quarantined` inverts that to `-m "quarantine"` and runs exactly the quarantined tests; combined with `--files`, it runs only the quarantined tests inside the named files. When the selection leaves nothing to run — a `--files` run over a file whose every test is quarantined, say — pytest's exit code 5 is narrated and the runner exits 0.

The policy the marker is held to:

- **Quarantine is for a demonstrated dependence, not a failing test.** Show the order dependence with `team/scripts/verify-test-order-independence.sh` (see "Order independence is demonstrated, not assumed" above), or the timing dependence as a verdict that tracks host load. A test that fails because the code is wrong is a bug in the code, never a quarantine candidate.
- **`reason=` names the dependence.** Say what the verdict tracks — "order-dependent: inherits environment left by earlier tests", "timing-dependent: a fixed hang guard a loaded host can outrun" — so a reader knows what fixing it means.
- **Each quarantined test has a filed bug** naming its order or timing dependence. When the dependence is fixed, the marker comes off and the test rejoins the default run.
- **A quarantine must not hide a real failure.** `--quarantined` is how a verifier checks that: run it, and record the result alongside the certification runs.

### The `serial` marker and the parallel lane

A certification run with `kanban.cfg` `[testing] workers` above 1, and pytest-xdist installed, runs the suite in two lanes:

1. **Parallel lane.** Every test *not* marked `serial`, spread across xdist worker processes (`-n <workers> --dist loadfile`). Other tests are running at the same moment in other processes.
2. **Serial lane.** Afterwards, the tests marked `@pytest.mark.serial`, in one process.

The two lanes' results combine into one exit code and one findings file. The marker is registered in `team/pyproject.toml`. Each runner's `--help` carries the lane rules, and `docs/tester-verification-reference.md` ("Running the gated suites") covers how the worker budget is split.

**A test is parallel-unsafe when it holds something another process could touch at the same time.** The usual cases:

- a fixed TCP port
- a fixed tmux server or socket name
- a file or directory outside the test's own `tmp_path`
- pytest-xdist workers the test starts itself, such as a test that drives a gated runner with `workers` above 1 — those workers come on top of the run's budget

The evidence works the same way it does for quarantine: demonstrate it. Run the suite with `workers` above 1 several times. A test that fails beside parallel workers and passes alone is the case the marker exists for.

**Prefer removing the shared resource to marking the test.** The serial lane is single-process, so every marked test adds wall-clock time that the worker budget cannot shorten. When the shared resource is incidental, give the test its own and leave it in the parallel lane. Incidental means a port the test could ask the operating system for, a socket name it could make unique, or a path it could take from `tmp_path`. Mark the test only when the shared resource is the point of the test.

**Each marker carries a one-line reason, in the test's docstring.** `serial` takes no argument, unlike `quarantine(reason=...)`. Write the reason as one docstring line naming what the test holds or does that makes it unsafe beside other workers, ending "hence the serial mark":

```python
@pytest.mark.serial
def test_one_failing_integration_test_gives_a_nonzero_run(tmp_path):
    """Runs both suites at once (workers = 2), hence the serial mark."""
```

If the docstring already says more, the reason is its closing sentence. A reader deciding whether the marker can come off needs to know what it guards.

**Skipping or deselecting is not the remedy.** A test that fails beside parallel workers still owes its verdict on every certification run, and the serial lane is where it gives it. Do not:

- skip it
- deselect it with `-m` or `-k`
- quarantine it
- pull it out into a pytest invocation of its own

Quarantine is for a verdict that depends on test order or wall-clock timing, and it takes the test out of the default run. The serial marker only changes *when* the test runs, never *whether*.

```python
# BAD — the parallel failure is hidden, not placed: the test stops running
@pytest.mark.skip(reason="fails under xdist")
def test_runner_parallel_lane_passes(tmp_path): ...

# BETTER — the test still runs on every certification run, alone, and says why
@pytest.mark.serial
def test_runner_parallel_lane_passes(tmp_path):
    """Starts two pytest-xdist workers of its own, hence the serial mark."""
```

**A `--files` run will not show you the problem.** Targeted runs are always single-process, so a test that only fails beside other workers passes there. When a test you write has to hold a shared resource, add the marker as you write it. Do not wait for a certification run to find it.

### `--reuse-if-green` and the per-RC green ledger

Every run without `--files` appends one record to a **green ledger**, scoped to the release candidate in flight; a `--quarantined` run's record carries the selection `quarantine`, so it is never mistaken for a run of the suite. `--reuse-if-green` reads the ledger: when this exact run is already certified green, the runner skips pytest rather than spend another full suite proving the same thing.

**Where the ledger lives:**

```
$PGAI_AGENT_KANBAN_TEMP_DIR/projects/<project>/green-ledger/<rc>/ledger.jsonl
```

`<project>` is `PGAI_PROJECT_NAME` (default `pgai-agent-kanban`). `<rc>` is the project's `## Active RC` value from its `release-state.md`, read and never written; when no release-state file is readable, or the value is absent, blank or `none`, the directory is `no-active-rc`. The temp-root purge does not sweep the `projects/` subtree, so a ledger lasts for the life of its RC, and a new RC starts a new, empty one.

**What each record holds.** One JSON object per line, appended and never rewritten:

| Field | Meaning |
|---|---|
| `recorded_at` | ISO 8601 UTC timestamp of the append |
| `commit` | `git rev-parse HEAD` of the repository the runner ran in, read before pytest started |
| `suite` | `unit` or `integration` |
| `selection` | the pytest `-m` expression the run used — `not quarantine` by default, `quarantine` for `--quarantined` |
| `exit` | the runner's own exit code; `0` is green |
| `findings_sha` | sha256 of the quality-class lint findings the run recorded before pytest |
| `tree_clean` | `true` when `git status --porcelain` was empty before pytest started |

**When a run is reused.** With `--reuse-if-green`, the pre-flight gates and the quality-class lints still run. Then the runner looks for the most recent record with the same commit (`HEAD`), suite and selection. It reuses that record only when the record's `exit` is `0` and its `tree_clean` is `true`, this run's tree is clean too, and this run's findings hash equals the record's `findings_sha`. A reuse prints a line containing `reused`, exits 0 without starting pytest, and appends no record of its own.

Anything else runs the suite normally after one `[green-ledger]` line saying why: no matching record, a recorded failure, a different commit or findings hash, a dirty tree, or `--coverage`, which needs a fresh coverage report a reused result cannot provide. A missing, unreadable or malformed ledger does the same. The ledger never fails a run; a malformed file is moved aside to `ledger.jsonl.malformed` and a fresh ledger started. On a `--files` run the flag is ignored with a note, because a targeted run never reads or writes the ledger.

### Exit files: targeted runs and certification runs write different things

A targeted run and a certification run never share an output, so a CODER's narrow check can never be read as the suite's verdict.

- **Targeted runs** write their exit code, and nothing else, to `$PGAI_AGENT_KANBAN_TEMP_DIR/targeted-unit.exit` or `$PGAI_AGENT_KANBAN_TEMP_DIR/targeted-integration.exit`. Each holds one line, the exit code, and is overwritten by the next targeted run of that suite. The runner's own exit status remains the result of the command you ran.
- **Certification runs** write the aggregate findings file TESTER reads, `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` or `lint/findings-integration.json`, hand it to the aggregate bug filer, and append their green-ledger record.

A targeted run never claims, writes, or files bugs from a findings file, and never touches the ledger. If a findings file's timestamp moved during a `--files` run, something else wrote it.

### Coverage is opt-in

Both runners keep `--coverage`, which measures line and branch coverage through `pytest-cov` and writes its JSON report under `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/`. It is opt-in for verification, not a default:

- **It never enters a verdict.** Coverage feeds no PASS or FAIL, no recommendation, and no risk rating. Verification runs the gated runners without it.
- **The config decides whether `--coverage` collects.** The flag is honoured only when `kanban.cfg` `[testing] coverage = true` (kanban-wide, default `false`), or when the operator sets `PGAI_TESTING_COVERAGE=1` in their own shell for a one-off run. Agents cannot set that variable: the wake bootstrap unsets it, and the runners ignore it while `PGAI_AGENT_WAKE_ACTIVE=1`. Otherwise the run prints `coverage disabled by config ([testing] coverage)`, runs without coverage, and keeps its exit code. The operator's weekly coverage run is a commented line in the crontab templates; `docs/tester-verification-reference.md` ("Running the gated suites") quotes it.
- **Not collecting it is a valid result.** When `pytest-cov` is not installed the runner prints a skip message and runs the suite without coverage; it does not fail. A report records `coverage unavailable`, or that coverage was not collected, and moves on.
- **It changes what else a run can do.** `--files` refuses it, and `--reuse-if-green` runs the suite in full when it is present.

---

## The pre-merge run

`--pre-merge` runs the cheap whole-tree checks a change should pass before it merges. These are the slips a `--files` run cannot see: a ratchet lint allowance exceeded, a new package module that crashes when started by file path, an untouched test that still asserts behaviour the change altered, or a property of the whole tree broken by a file the change never named. The last kind is caught by four always-run whole-tree guards — `guard: env-bootstrap` (a module reading the kanban root around its resolver), `guard: operator-verb-docs` and `guard: dashboard-window-docs` (a generated document left stale), and `guard: docs-citations` (a cited `docs/` page missing from the docs manifest) — which run whatever the diff. Every CODER and WRITER ticket runs it before merging, and the ticket's acceptance criteria say so: "`bash team/scripts/run-unit-tests.sh --pre-merge` exits 0 before merging."

```bash
# From the repository root of your worktree, on the feature branch,
# with the change committed.
bash team/scripts/run-unit-tests.sh --pre-merge
bash team/scripts/run-unit-tests.sh --pre-merge --base ai_main
bash team/scripts/run-integration-tests.sh --pre-merge
```

It is not a certification run, and it says so: the run prints `pre-merge run — not a certification run` before the checks start, then the base, cap and worker budget it will use. Each runner's `--help` is the authoritative statement of the flag; this section says what the run does and how to read it.

### What it runs

The unit runner's pre-merge run hands over to the pre-merge engine, `python3 -m pgai_agent_kanban.testing.pre_merge`, which runs its checks in this order — the by-path test, the ratchet lints (five of them), the tracked-files lint `tracked-ignored`, the four whole-tree guards and the affected tests, twelve checks in all:

| Check | What it does | It fails when |
|---|---|---|
| `by-path` | Runs `tests/unit/test_package_modules_run_by_path.py` over the whole `pgai_agent_kanban` package: every package module with a `__main__` block is started by file path, with no `PYTHONPATH`. | A module with a `__main__` block lacks the self-location guard, so it cannot import its own package when started by path. |
| `lint: inline-python`, `lint: python-duplicates`, `lint: engine-size`, `lint: api-route-impl` | Runs four of the ratchet lints (`lint_inline_python.py`, `lint_python_duplicates.py`, `lint_engine_size_dupe.py`, `lint_api_route_impl.py`) exactly as the gated runners run them, in fail-on-regression mode. | The lint reports anything: a file over its recorded allowance, an unlisted new file, an allowlist entry left above the measured count, a malformed allowlist line, an engine file over its line ceiling, or a cross-file shell function collision. |
| `lint: echo-pipe-grep-q` | Runs the fifth ratchet lint, `team/scripts/lint_echo_pipe_grep_q.py`, the same way. It reports every `echo` or `printf` whose output is piped into a quiet `grep` (`-q`, `--quiet` or `--silent`) in non-test bash under `team/` and in `install.sh`, against the per-file ceilings in `team/config/echo-pipe-grep-q-allowlist.txt`. Under `set -o pipefail` that pipeline can report "no match" for text that matches: `grep` exits at its first match, and the still-writing `echo` dies of SIGPIPE. | The lint reports anything: a site in a file the allowlist does not list, a file over its allowance, an allowlist entry left above the measured count, or a malformed or missing allowlist. **Fix:** test the text without a pipe — `grep -q PATTERN <<< "$text"` (same pattern and flags) or `[[ $text =~ $re ]]` — at each site the output names; where a listed file's count drops, lower its allowlist entry in the same change; then re-check with `python3 team/scripts/lint_echo_pipe_grep_q.py`. |
| `lint: tracked-ignored` | Runs `team/scripts/lint_tracked_ignored.py` after the ratchet lints and judges it the same way. It is not a ratchet: it keeps no allowlist. It reports every tracked file a `.gitignore` rule matches (`git ls-files -ci --exclude-standard`), and every tracked file whose name marks it as generated output — `*.log`, `*.lock`, `.coverage`, `coverage*.json`, `*.orig`, `nohup.out`, rotated `*.previous-RUN-*` artifacts and the like. | The lint reports anything. **Fix:** untrack each file the output names with the `git rm --cached -- <file>` its remedy prints, commit, then re-check with `python3 team/scripts/lint_tracked_ignored.py`. |
| `guard: env-bootstrap` | Runs `team/scripts/lint_env_bootstrap.py`: every Python module reads the kanban root through `resolve_kanban_root()`, and every bash entry point that reads it sources the bootstrap prelude. | A module reads the kanban root directly, such as an `os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")`, or a bash entry point reads it without the prelude. **Fix:** read the root through `resolve_kanban_root()` from `pgai_agent_kanban.env` (Python) or source `scripts/lib/env_bootstrap.sh` (bash) at each site the output names, then re-check with `python3 team/scripts/lint_env_bootstrap.py`. |
| `guard: operator-verb-docs` | Runs `team/scripts/generate_operator_verb_docs.py --check`: the generated verb and route references in `docs/operator-commands.md` and `docs/operator-api.md` match a fresh render from the verb census and each verb's own `--help`. | A verb's `--help` text or census row changed and the generated references were not regenerated. **Fix:** run `python3 team/scripts/generate_operator_verb_docs.py`, then commit `docs/operator-commands.md` and `docs/operator-api.md`. |
| `guard: dashboard-window-docs` | Runs `team/scripts/dashboard/generate_window_docs.py --check`: the dashboard window surfaces match a fresh render from `team/config/dashboard_windows.census`. | The census or a rendered surface changed without a regeneration. **Fix:** run `python3 team/scripts/dashboard/generate_window_docs.py`, then commit every file it reports on a `generate_window_docs: wrote` line: `team/scripts/dashboard/create.sh`, `docs/dashboard.md`, or the per-pane reference beside it, which lives only in the repository checkout. |
| `guard: docs-citations` | Runs `team/scripts/check_docs_citations.py`: every `docs/` page an installed governance file cites is named in `team/config/docs-manifest.txt`. | A governance file cites a `docs/` page the manifest does not name, so the citation would dangle on every installed root. That includes a `docs/`-prefixed citation of a recorded exclusion: a page the manifest's `EXCLUDED-CITATION-TARGETS` line keeps in the repository checkout only, never delivered. **Fix:** follow the checker's own "To fix" steps. For a cited page not yet delivered, add the manifest line the finding names to `team/config/docs-manifest.txt`. For a recorded exclusion, never add a manifest line: reword the citation — drop the `docs/` prefix and say the page is in the repository checkout only (from a page inside `docs/`, a bare sibling filename carrying that caveat is allowed) — or remove it. Then re-check with `python3 team/scripts/check_docs_citations.py`. |
| `affected tests` | Runs, with pytest, the unit test files that mention a name the change touched or list the directory a changed file sits in (see "How affected tests are chosen" below). | Any selected test fails. |

**The lints keep their quality class.** The lints themselves are unchanged, and on a certification run their findings are still recorded rather than fatal. The pre-merge run judges what they report, and any finding fails its check — an allowlist entry left above the measured count included, because a change that shrinks a file's count tightens its allowlist in the same change. Each finding's own summary line is printed, so the output names the file and the allowance it broke.

**The guards run whatever the diff.** Each one checks a property of the whole tree that any change can break: a help text reworded in one script stales a document generated from it, and a direct kanban-root read added to one module breaks a rule checked across every module at once. A test of such a property names none of the files it guards, so the affected-test finder would never select it, however directly the change broke it. The guards are therefore not chosen from the diff at all; every unit pre-merge run runs all four, after the ratchet lints and the `tracked-ignored` lint and before the affected tests.

**The guards are judged by exit code, and keep their class elsewhere.** Each guard is the script the certification run's quality-class block already runs, run the same way and unchanged; on a certification run it keeps the class it had. In a pre-merge run, exit `0` passes the guard. Exit `1` fails it as `finding(s)`; any other exit fails it as `the guard could not run (exit N)`, and a missing guard script as `the guard could not run`. Beneath a failing row the summary prints up to 20 lines of the guard's own output, then a `fix:` line ending in the command that clears it — the **Fix:** in the table above. The guards only read the tree: `--check` renders in memory and writes nothing. They run as scripts rather than as test files, so no test file runs both as a guard and as an affected test in one run.

**The guards' wall-time budget is 5.0 seconds, and an overrun is narrated, never failed.** The summary block adds one `guards:` line reporting the four guards' total time against that budget; together they measured about 1.9 seconds on the kanban's own tree. The budget is a soft estimate. Going over it changes only that line, which then says the guards ran over the budget and that the budget never fails the run; the run's verdict is decided by the checks alone.

**Not every whole-tree lint is a guard.** `team/scripts/lint_doc_command_truth.py`, which checks that every command a document cites answers `--help`, is not among them: it starts one `--help` process per cited command, so its cost grows with the documents and alone exceeds the whole budget. It still runs in every certification run's quality-class block.

**The integration runner runs the affected-test check only.** `run-integration-tests.sh --pre-merge` selects and runs the integration test files under `team/tests/integration` the same way. The by-path test, the ratchet lints and the whole-tree guards belong to the unit runner, so they are not repeated. The ticket criterion names the unit runner; the integration runner's pre-merge run is there for a change whose behaviour the integration suite exercises.

**Every check runs, even after an earlier one fails,** so one run reports everything that needs fixing. The run ends with one summary block: the base and how it was chosen, the suite, cap and workers, then one row per check with its verdict and duration, the detail of each failure beneath its row, the guards' added-time line, and a closing verdict line. The shape, illustrative — the names, counts and timings differ per change:

```
========================================================================
pre-merge summary
base:    ai_rc/vX.Y.Z (default: the only local RC branch)
suite:   unit; cap 60; workers 3
------------------------------------------------------------------------
by-path                       PASS     19.6s  tests/unit/test_package_modules_run_by_path.py
lint: inline-python           PASS      0.5s
lint: python-duplicates       PASS      4.2s
lint: engine-size             PASS      0.6s
lint: api-route-impl          PASS      0.3s
lint: echo-pipe-grep-q        PASS      0.5s
guard: env-bootstrap          PASS      0.2s
guard: operator-verb-docs     FAIL      1.5s  finding(s)
    <the guard's own output, up to 20 lines, naming what drifted>
    fix: regenerate, then commit docs/operator-commands.md and docs/operator-api.md: python3 team/scripts/generate_operator_verb_docs.py
guard: dashboard-window-docs  PASS      0.1s
guard: docs-citations         PASS      0.2s
affected tests                PASS     41.7s  12 of 12 affected test file(s) run
------------------------------------------------------------------------
guards:  4 always-run guard(s) added 2.0s of the 5.0s budget
pre-merge: FAIL -- 1 of 11 check(s) failed (guard: operator-verb-docs) in 69.9s
========================================================================
```

Over budget, the `guards:` line reads `guards:  4 always-run guard(s) added 5.7s, over the 5.0s budget (a soft estimate; it never fails the run)` instead, and the verdict line is unchanged. A failing ratchet lint prints the same way: `1 finding(s)` on its row and the lint's own summary line beneath it, naming the file and the allowance it broke.

**What does not run.** The fatal pre-flight gates that open a certification run are skipped, and so is the rest of its quality-class lint block: of that block, a pre-merge run runs only the four guards above, because it runs exactly the engine's checks and nothing else. The full quality-lint fleet and the full unit suite stay certification's, and running a guard here changes no check's enforcement class on a certification run. `--verbose` and `--no-scratch-sweep` have no effect. pytest uses the `kanban.cfg` `[testing] workers` budget, and `--workers N` may lower it as on a full run; no more than that many pytest workers are alive at once.

**Refused combinations.** `--pre-merge` with `--files`, `--coverage`, `--quarantined` or `--reuse-if-green` is refused before anything runs: a pre-merge run chooses its own tests, is never a certification run, and measures no coverage. `--base` without `--pre-merge` is refused too. Each refusal carries an error key, a plain-language message, and a remedy that re-runs the command in the accepted form.

### When to use it

**On every CODER and WRITER ticket, before merging.** Commit the change first, then run it from the worktree. A failure is fixed in the same session — fix the change, tighten the allowlist, or update the test that asserts the old behaviour — and the run is repeated until it exits 0. Within a ticket, that exit 0 is the acceptance criterion; a failing pre-merge run is the agent's own chore to clear, not a reason to set the ticket `BLOCKED`.

**It sits between a targeted run and a certification run, and replaces neither.**

| | `--files` run | pre-merge run | certification run |
|---|---|---|---|
| Who runs it | CODER, over the test files it added or changed | CODER and WRITER, once per ticket before merging | TESTER, once per RC |
| What pytest runs | exactly the named files | the by-path test and the affected test files, up to the cap | the whole suite |
| Fatal pre-flight gates | run | skipped | run |
| Lints | the two per-file test-authoring checks, over the named files | the ratchet lints, in fail-on-regression mode, the `tracked-ignored` lint and the four whole-tree guards | the full quality-lint fleet, recorded |
| What it writes | `targeted-<suite>.exit` | `pre-merge.exit` (unit) or `pre-merge-integration.exit` (integration) | `lint/findings-<suite>.json` and a green-ledger record |
| Certification run? | no | no | yes |

A ticket that adds or changes tests runs both: `--files` proves the named files pass and carry no per-file finding, behind the breakage pre-flight; the pre-merge run catches what the change did to the rest of the tree. Neither is a substitute for TESTER's certification run, which still runs the full suites once per RC.

**A failed or skipped pre-merge run never blocks a release by itself.** With respect to releases it is quality class. Nothing in the release path reads its exit file, and TESTER still certifies the full suites; a slip a pre-merge run would have caught still surfaces there, later, as a bug against the RC. The pre-merge run exists to move those cheap slips earlier, into the ticket that caused them.

**Exit codes.** `0` every check passed. `1` at least one check failed. `2` the run could not start: the base names no local commit, no RC or main branch exists to default to, or pytest is not installed. A `2` is an invocation problem, not a verdict on the change: fix the invocation — `--base` with a ref that exists locally, say — and run it again.

### How affected tests are chosen

The affected-test finder, `team/pgai_agent_kanban/testing/affected.py`, picks the test files a change should run by a plain identifier search, plus a search for tests that list a changed file's directory. It never runs pytest; the engine runs what it returns.

**The diff is `git diff <base>...HEAD`, read from local refs only.** Nothing is fetched. The three-dot form means "what this branch changed since it left the base", so a base that has moved on since does not pull its own changes into your selection. Uncommitted changes are not part of `base...HEAD` and take no part in choosing tests; the run narrates a dirty tree and says to commit and re-run. The by-path test and the lints read the files on disk, so they do see uncommitted work — commit before you run, so all three checks judge the same change.

**The default base is the RC branch the worktree was cut from.** Without `--base`, the engine looks at the local `<prefix>rc/vX.Y.Z` branches and picks the one HEAD is fewest commits ahead of; on a tie, the higher version wins. When HEAD is on a `<prefix>feature/...` branch, only RC branches with that same prefix are considered. With no RC branch, the default falls back to `<prefix>main`, then `main`; with neither, the run stops with exit 2. The summary always names the base it used and how it was chosen.

**`--base REF` overrides the default.** Give any ref that resolves to a local commit — `--base ai_main` widens the selection to everything the branch carries since main, for example. `--base` is accepted only together with `--pre-merge`.

**Which names a change contributes.** Four sources, merged into one set:

- **Every changed path.** A Python file contributes its module name, the file stem (`team/pgai_agent_kanban/lib/semver.py` gives `semver`). Any other file contributes its full file name (`team/scripts/run-unit-tests.sh` gives `run-unit-tests.sh`). Deleted files count, so a test still citing a removed script is found.
- **Every changed path's directory.** A changed file also contributes a directory name: its directory's repository-relative path with a trailing slash (`team/scripts/dashboard/attention.sh` gives `team/scripts/dashboard/`). A file at the top of the repository or directly inside a top-level directory (`install.sh`, `docs/OPERATIONS.md`) contributes none: its directory is the root of the tree or of a whole subtree, too broad to say anything about a test. A changed test file contributes no directory (below).
- **Changed functions.** In a changed Python file, every function or class whose body holds a changed line contributes its name, together with the functions and classes enclosing it: a changed method gives both the method and its class. In a changed shell script, every shell function whose body holds a changed line contributes its name.
- **Definitions on changed lines.** Added and removed lines are scanned for definitions — in Python, `def` and `class` lines in definition shape (the name followed by `(`, `:` or `[`) and column-0 assignments; in shell, function definitions and upper-case variable assignments. A renamed or removed function therefore still selects the tests that call it by its old name. Docstring and comment lines contribute no definition name: a comment line matches none of these patterns, and a Python line that begins inside a string literal — a docstring's body, a triple-quoted constant — is skipped, judged against the file at `HEAD` for an added line and at the merge base for a removed one. A reworded docstring line reading `class whose members ...` contributes nothing. When that copy of the file does not parse, the definition shape is the only filter. A function whose body holds such a line is still a changed function and contributes its own name, as above.

**A changed test file contributes its own name and nothing else.** Its directory, its changed functions and the definitions on its changed lines are skipped, so names defined only inside a changed test file — its private helpers, module constants and fixture methods — do not select other test files: the same names in another test file are that file's own definitions. The changed test file itself is still selected on its own name. Shared test code is not a test file: a changed conftest file, helper module or fixture file contributes its definitions like any other code, so the tests that call it are still selected.

**The search.** A test file is selected when one of those names other than a directory name (those go to the directory search below) occurs in its text with no letter, digit, underscore or hyphen immediately before or after it. `semver` matches `lib.semver` and `semver.version_sort_key`, but not `semver_lt` or `pysemver`. A test file the change itself edited is selected on its own name, so an edited test always reruns.

- **Where it searches.** The suite's own test tree: `team/tests/unit` for the unit runner, `team/tests/integration` for the integration runner. Tests under `team/pgai_agent_kanban/api/tests/`, which the unit runner's `--files` accepts, are not searched; run them with `--files` when a change touches the API.
- **What counts as a test file.** A `test_*.py` or `*_test.py` file with no `fixtures`, `artifacts`, `__pycache__` or dot directory between it and the tree's root. Conftest files, helper modules and fixture data are never selected. The unit runner also leaves out the by-path test file, which the by-path check has already run, and any file the unit runner never collects.
- **Generic names are dropped first.** A name that would select most of the tree carries no signal. Four kinds are dropped before the search: Python dunders such as `__init__`, names shorter than three characters, directory names with fewer than two components (`docs/`, `/`), and any name in the fixed set `GENERIC_NAMES` in `team/pgai_agent_kanban/testing/affected.py`. That set holds entry-point and boilerplate names (`main`, `run`, `parse_args`, `README.md` and similar), ubiquitous member and attribute names (`failed`, `passed`, `skipped`, `say`, `git`, `commit`), and common English words production code uses as function names: `exists`, `has`, `holds`, `line`, `lines`, `module`, `function`, `code`, `field`, `record`, `step`, `add`, `describe`, `format`, `heading`, `changed` and `last`. Membership is the whole rule — an exact, case-sensitive match against the set, with no frequency count taken at run time; the common words are in it because each occurs in roughly one test file in six or more. Project vocabulary such as `release` is not in the set and stays searched, and a changed module's own name still selects its tests.

**The directory search.** A test that finds its subjects by listing a directory — a `--help` test that globs `scripts/dashboard/*.sh` and parametrizes over the result — never names them, so the identifier search never selects it. A test file is therefore also selected when it lists a changed directory, searched in the same tree among the same test files. Two things must hold, judged on the directory's last two components (`scripts` and `dashboard` for `team/scripts/dashboard/`), so a path anchored at the repository and one anchored at `team/` both count:

- **It cites the directory itself.** A line holds the two components joined as a path — `scripts/dashboard`, `"scripts" / "dashboard"`, `"scripts", "dashboard"` — or the last one joined with `/` to a variable whose name holds the first (`_TEAM_DIR / "scripts"`, case and `-`/`_` ignored). For a directory of exactly two components, whose first is a top-level directory, a variable whose name holds `root` also counts (`ROOT / "scripts"` or `team_root / "scripts"` for `team/scripts/`). A citation continued by a further path component (`scripts/dashboard/kill.sh`, `"dashboard" / "lib"`) names something inside the directory, not the directory, and does not count; one continued by a glob pattern (`scripts/dashboard/*.sh`) does.
- **It lists that directory.** The citing line itself calls `glob`, `rglob`, `iterdir`, `listdir`, `scandir` or `walk`, or it assigns the directory to a name (`_DASHBOARD_DIR = ...`) that the file lists elsewhere (`_DASHBOARD_DIR.glob("*.sh")`, `os.listdir(_DASHBOARD_DIR)`). The assignment may span several lines: a citing line inside a parenthesized or backslash-continued expression assigns to the name its statement starts with, so `_DIR = (`, then `ROOT / "scripts" / "dashboard"`, then `)`, followed by `_DIR.glob("*.sh")`, lists `team/scripts/dashboard/`.

**A recursive listing of an ancestor lists the directory too.** A test that walks a whole subtree covers every directory below it, so a test file is also selected by a changed directory when it cites one of that directory's ancestors and lists the ancestor recursively: `rglob`, `walk`, or a `glob` whose pattern holds `**`, on the citing line or on a name the citing line assigns. `_SCRIPTS = _TEAM_DIR / "scripts"` followed by `_SCRIPTS.rglob("*.sh")` is selected by a change to `team/scripts/dashboard/attention.sh`, and by a change to any file deeper in `team/scripts/`, such as one in `team/scripts/dashboard/lib/`.

- **The ancestor is cited by the same two-component rule,** so it has at least two components itself: `team/scripts/` counts for `team/scripts/dashboard/`; `team/` never does, so a walk of the whole `team/` tree is never selected by the directory search.
- **A non-recursive listing of an ancestor does not count.** A `glob` without `**`, `iterdir`, `listdir` or `scandir` of an ancestor lists only the ancestor's own entries: `_SCRIPTS.glob("*.sh")` is selected by a change directly under `team/scripts/`, never by one in `team/scripts/dashboard/`.

A test that only builds a path into the directory, or adds it to `sys.path`, is not selected by it. A test that lists a changed directory, directly or through an ancestor, counts once per changed directory it lists, so it ranks as a directory listing, below every name match (see "Ranking" below).

**Ranking.** Every matching file is ranked by how many distinct changed names it cites, most first; then by how many changed directories it lists, most first; then by its total number of occurrences of those names and directories, most first; then by its path, so ties order the same on every run. A file that cites a changed name therefore always ranks above one that only lists a changed directory, and the cap leaves out directory-only files first.

**The cap.** The run takes at most `kanban.cfg` `[testing] pre_merge_affected_cap` affected test files — default `60` when the key is absent or blank; `0` means no cap. The key is kanban-wide: `project.cfg` has no `[testing]` section. When more files are affected than the cap allows, the run narrates the cap (`143 affected test files; cap 60 applied, running the 60 most-referencing`) and runs only the top of the ranking. The cap limits how many files are chosen, not how many run at once; concurrency is still the `workers` budget. A malformed value is refused before anything runs, naming the value and the edit that fixes it, rather than guessed at.

**Debugging a selection.** The finder has a command line of its own, which selects and prints without running anything:

```bash
python3 team/pgai_agent_kanban/testing/affected.py --base ai_main --show-names
```

It prints one selected file per line in the form `--files` takes (`tests/unit/test_x.py`), and with `--show-names` lists on stderr the names it searched for and the generic names it dropped. Run on its own it searches the whole `team/tests` tree and applies no cap unless given `--cap N`, so its list can be longer than a runner's; `--json` prints the whole selection, matched count and cap included, as one object. `--names` searches for names you give instead of a diff's, and a name ending in `/` is a directory name: `--names team/scripts/dashboard/` lists the tests that list that directory.

### Where the exit file is written

Each runner writes its pre-merge run's exit code, and nothing else, to a file of its own under the framework temp root:

- `$PGAI_AGENT_KANBAN_TEMP_DIR/pre-merge.exit` — the unit runner.
- `$PGAI_AGENT_KANBAN_TEMP_DIR/pre-merge-integration.exit` — the integration runner, so the two never overwrite each other.

Each holds one line, the exit code, and is overwritten by the next pre-merge run of that runner. The runner's own exit status remains the result of the command you ran.

A pre-merge run never claims or writes `lint/findings-<suite>.json`, never reads or writes the green ledger, and never writes `targeted-<suite>.exit`. The lints' findings files and pytest's basetemp live in a scratch directory of the run's own under `$PGAI_AGENT_KANBAN_TEMP_DIR`, removed when the run ends. The lints run without `PGAI_PROJECT_NAME` in their environment, so a pre-merge finding is not recorded in the kanban's refusal ledger either. The exit file is read by you, not by the release path.
