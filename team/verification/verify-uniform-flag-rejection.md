# Verification: Uniform Unknown-Flag Rejection

## Task
CODER-20260630-004-verify-uniform-flag-rejection

## Date
2026-06-30

## Verifier
CODER agent (Claude claude-sonnet-4-6)

## Source Branch
an earlier release candidate

---

## Acceptance Criteria Results

### AC-1: Grep-Completeness Gate

Command run:
```
for f in $(grep -rl operator_args.sh team/scripts --include=*.sh); do \
    grep -q operator_args_validate_known "$f" || echo "MISSING: $f"; \
done
```

Result: **PASS** — no output (gate prints nothing)

All 28 operator scripts contain `operator_args_validate_known`. The only file
containing the call that is excluded from the gate check is the lib itself
(`team/scripts/lib/operator_args.sh`). `wake-now.sh` was included (not excepted)
because it correctly declares all dispatch flags (`--agent`, `--all`, `--bg`,
`--help`) in `OPERATOR_VALID_FLAGS` before calling `operator_args_validate_known`.

Scripts sourcing operator_args.sh (28 operator scripts + 1 lib = 29 total):
- add-project.sh, close.sh, create-project.sh, delete.sh, export-kanban-config.sh,
  export-project.sh, halt-after.sh, halt-global.sh, halt.sh, import-kanban-config.sh,
  import-project.sh, init-project-git-repo.sh, intake.sh, list-rejected.sh,
  recover-rejected.sh, regenerate-changelog.sh, remove-project.sh,
  reset.sh, set-version-ceiling.sh, show.sh, show-test-report.sh, switch-provider.sh,
  unhalt-global.sh, unhalt.sh, unwind-rc.sh, wake-now.sh, wontdo.sh
  (+ lib/operator_args.sh)

Named exceptions documented: none (wake-now.sh is fully covered, not excepted)

---

### AC-2: close.sh / wontdo.sh Symmetry

Both terminal-state setter siblings now reject unknown flags identically.

Test: `bash team/scripts/close.sh --bogus-flag 2>&1`
Output line 1: `close.sh: unknown argument: --bogus-flag`
Exit code: 1

Test: `bash team/scripts/wontdo.sh --bogus-flag 2>&1`
Output line 1: `wontdo.sh: unknown argument: --bogus-flag`
Exit code: 1

Result: **PASS** — Both scripts reject `--bogus-flag` with the uniform
`<script>: unknown argument: --bogus-flag` message and exit 1. Sibling pair
is now symmetric.

---

### AC-3: Destructive Command Spot-Check

Spot-check subject: `reset.sh` (previously unguarded destructive command).

Test (unknown flag rejected):
```
bash team/scripts/reset.sh --bogus-flag 2>&1
```
Output line 1: `reset.sh: unknown argument: --bogus-flag`
Exit code: 1

Test (legitimate flags accepted — validation passes, proceeds to business logic):
```
bash team/scripts/reset.sh --project testproject --key testkey 2>&1
```
Output: Python module not available in test environment (expected); flag
validation passes (no "unknown argument" output).
Exit code: 1 (from Python, not from flag rejection)

Test (legitimate optional flag accepted):
```
bash team/scripts/reset.sh --project testproject --key testkey --keep-artifacts 2>&1
```
Output: Python module error (expected); `--keep-artifacts` accepted by flag
validation with no rejection.
Exit code: 1 (from Python, not from flag rejection)

Result: **PASS** — `reset.sh` rejects unknown flag `--bogus-flag` (exit 1),
accepts legitimate flags `--project`, `--key`, `--keep-artifacts`, and proceeds
past flag validation to business logic (no over-rejection).

---

### AC-4: Help Rendering (All 28 Use OPERATOR_VALID_FLAGS)

Command run:
```
grep -rl "operator_args_render_help[^_]" team/scripts --include=*.sh | grep -v "operator_args.sh"
```
Result: **no output** (no scripts use the deprecated `operator_args_render_help`)

Command run:
```
grep -rl "operator_args_render_help_for_flags" team/scripts --include=*.sh | grep -v "operator_args.sh" | wc -l
```
Result: **28** (all 28 operator scripts use `operator_args_render_help_for_flags`)

Result: **PASS** — All 28 operator commands render `--help` from
`OPERATOR_VALID_FLAGS` via `operator_args_render_help_for_flags`. None have
switched to or remained on the deprecated `operator_args_render_help`.

---

### AC-5: bash -n Syntax Check (All Changed Scripts)

Command run on all 29 files (28 operator scripts + lib):
```
for f in $(grep -rl operator_args.sh team/scripts --include=*.sh); do
    result=$(bash -n "$f" 2>&1)
    [[ -n "$result" ]] && echo "SYNTAX ERROR: $f" && echo "$result"
done
```
Result: **PASS** — no output; all 29 files pass `bash -n` with no syntax errors.

---

## Summary

All 5 acceptance criteria pass:

| # | Criterion | Result |
|---|-----------|--------|
| AC-1 | Grep-completeness gate prints nothing | PASS |
| AC-2 | close.sh / wontdo.sh symmetry | PASS |
| AC-3 | Destructive command rejects unknown flag, accepts legit flags | PASS |
| AC-4 | All 28 use operator_args_render_help_for_flags | PASS |
| AC-5 | bash -n clean on all scripts | PASS |

The unknown-flag rejection rollout is now **complete and uniform**
across all 28 operator commands. The uniform-flag vocabulary contract (uniform
--help via OPERATOR_VALID_FLAGS + uniform rejection via operator_args_validate_known)
is fully closed.
