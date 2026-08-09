# Blocking-Site Census — v2.3.6

## Purpose

This census inventories every place in the shipped pgai-agent-kanban code that
can **halt, refuse, or block the loop**, and classifies each site against
**The Loop** doctrine (`team/SOP.md`, "The Loop"). It is the standing reference
that future gates are measured against: every new refusal added to the tree
must declare which class below it belongs to.

It is the input contract for two downstream pieces of the v2.3.6 release: the
CONVERT conversions (which turn every `CONVERT`-class site into
warn-file-continue) and the census lint (which checks that every gate declares
its class and carries the dual-channel error contract).

This is a tracking/audit document, not code. It records what each site's
blocking behavior *should* be under the doctrine; it does not assert that every
`CONVERT` row has already been converted at every call site.

## Doctrine anchor

From `team/SOP.md` ("The Loop"): the kanban is a looped system — human input in,
verified output out, and the loop keeps turning on new human input or on a bug
the verifier files. **Bugs heal**; the next iteration is the default repair
path. **Quality findings never stop the loop** — they carry zero risk of
runtime failure and are noted and carried forward. **Blocking is the rare
exception**, reserved for work that moves the system *backwards*: destructive
failures, unusable builds, or verification unable to run at all.

## Classification vocabulary

Every row below carries exactly one of three classes.

- **BLOCK-WORTHY** — The refusal is justified: the condition it catches moves
  the system backwards. It fits one of the doctrine's backward-motion
  categories — destructive/state-corrupting failure, breakage-class (no
  trustworthy pass signal / unusable build), verification unable to run, or
  approval-/invariant-by-design (an operator stop signal or a release-integrity
  invariant whose removal would let the loop corrupt itself). Every
  BLOCK-WORTHY row carries a one-sentence doctrine-tied justification.
- **CONVERT** — A quality-class finding that currently exits fatally but
  carries zero runtime-failure risk. Under the doctrine it should
  **warn + file a bug + continue**. `roles/CM.md` ("Quality-class lint findings
  are non-blocking") is the authoritative enumeration of this class: provenance,
  anti-pattern, orphan-test, help-presence, doc/CHANGELOG/ICD-freshness,
  API-parity, dual-channel-pair, and every other static lint.
- **AGENT-RESOLVABLE** — Fatal to the current done-claim, but self-serviceable
  by the agent in the same session (re-merge, re-commit, reword the offending
  line, or add the allowlist marker with justification, then re-complete). A
  refusal here is an agent chore, never a Needs-Human block.

## Discovery method (reproducible)

Discovery combined a governance read with a code grep, scoped to **loop-bearing
machinery** — the autonomous wake → discovery → PM → CODER/WRITER → TESTER → CM
pipeline and the gate/lint fleet that runs inside it. Commands run from the dev
tree root:

```bash
# HALT writers and ship-policy triggers in the release script
grep -nE "cm_halt|write_halt|append_halt_event" team/scripts/cm/release.sh

# HALT / refuse guards in the discovery pipeline and wake dispatch
grep -nE "HALT|refus|exit [1-9]" team/scripts/lib/discovery.sh team/scripts/wake-batch.sh

# The CODER/WRITER completion gate
grep -n "coder_writer_completion_gate" team/scripts/lib/wake_common.sh

# The standalone lint / gate fleet and their fatal exits
grep -nE "sys\.exit\([1-9]|return 1|exit [1-9]" team/scripts/lint_*.py team/scripts/lint_*.sh \
  team/scripts/check_breakage_class.py team/scripts/audit_coding_standards.sh

# Breakage-class hard gate vs quality-class warn-file wrapper
grep -n "check_breakage_class\|gated_runner_lint" team/scripts/run-*-tests.sh

# Ship-policy matrix HALT rows (governance)
grep -n "HALT" team/roles/CM.md
```

The gate/lint fleet backbone reuses the enumeration already proven in
`docs/audits/v1.27.2-gate-remedy-census.md` (35 gate/lint entries), re-scanned
against the current tree and **re-classified against The Loop doctrine** instead
of that census's remedy-obligation axis.

**Scope decision (documented).** "Shipped code" is read through the loop lens the
doctrine defines. Pure operator-CLI argument/usage refusals in interactive
utilities (`show.sh`, `delete.sh`, `close.sh`, and the like) are *not* included:
they can only reject a human's malformed command at a terminal — they can never
halt the autonomous loop, because the loop never invokes them. The census covers
every grep-discoverable site that the *autonomous pipeline* can reach: HALT
triggers, ship-policy matrix HALT rows, release-script gates, hook guards,
wake/discovery refusals, the completion gate, breakage-class pre-flight, and the
full quality-class lint fleet the runners orchestrate.

## At-a-glance rollup

| # | Site | Source (path · locator) | Trigger | Class |
|---|---|---|---|---|
| 1 | Wake provider dispatch | `team/scripts/wake-batch.sh` · provider `exit 1` | Active provider's `wake/<provider>.sh` missing or not executable | BLOCK-WORTHY |
| 2 | Env-bootstrap source abort | `team/scripts/wake-batch.sh`, `run-*-tests.sh`, `ship-rc.sh` · `source lib/env_bootstrap.sh \|\| exit 1` | Bootstrap prelude cannot be located or sourced | BLOCK-WORTHY |
| 3 | Global HALT guard | `team/scripts/lib/discovery.sh` · `${TEAM_ROOT}/HALT` check | Operator/CM global HALT flag present | BLOCK-WORTHY |
| 4 | Per-project HALT guard | `team/scripts/lib/discovery.sh` · `pp_project_halted` | Per-project `projects/<name>/HALT` flag present | BLOCK-WORTHY |
| 5 | Active-RC single-flight guard | `team/scripts/lib/discovery.sh` · `_disc_active_rc_blocks` | An RC is in flight; refuses to queue PM / bundle | BLOCK-WORTHY |
| 6 | Completion gate — merge check | `team/scripts/lib/wake_common.sh` · `coder_writer_completion_gate` #1 | Feature branch not merged into source branch at done-claim | AGENT-RESOLVABLE |
| 7 | Completion gate — cleanliness | `team/scripts/lib/wake_common.sh` · `coder_writer_completion_gate` #2 | Uncommitted changes in the task worktree at done-claim | AGENT-RESOLVABLE |
| 8 | Matrix HALT — TESTER BLOCKED | `team/roles/CM.md` matrix · `release.sh` `trigger-1.tester-blocked` | Verification could not complete | BLOCK-WORTHY |
| 9 | Matrix HALT — systemic_risk high | `team/roles/CM.md` matrix · `release.sh` `trigger-2.systemic-risk-high` | Report-level systemic risk = high | BLOCK-WORTHY |
| 10 | Matrix HALT — serious + large fix | `team/roles/CM.md` matrix · `release.sh` `trigger-3.serious-concerns-large-fix` | SHIP-WITH-SERIOUS-CONCERNS with any large-fix finding | BLOCK-WORTHY |
| 11 | Matrix HALT — missing ordered report | `team/roles/CM.md` matrix (no report, `Test Required: true`) | Verification ordered but its report is missing | BLOCK-WORTHY |
| 12 | Matrix HALT — unrecognized inputs | `team/roles/CM.md` matrix · `release.sh` `tester-state-unrecognized` / `ship-policy.halt` | State/recommendation string the matrix does not recognize | BLOCK-WORTHY |
| 13 | Pre-squash hook gate | `team/scripts/cm/release.sh` · `trigger-4.pre-squash-hook-not-executable` / `-failed` | Project pre-squash hook non-executable or exits non-zero | BLOCK-WORTHY |
| 14 | Squash conflict HALT | `team/scripts/cm/release.sh` · `trigger-5.squash-conflict` | Squash into main produced a merge conflict | BLOCK-WORTHY |
| 15 | Tag-points-at-wrong-commit | `team/scripts/cm/release.sh` · `trigger-5b.tag-points-at-wrong-commit` | Existing tag does not point at the squash commit | BLOCK-WORTHY |
| 16 | Push-fails-after-retries | `team/roles/CM.md` trigger 6 · `cm/finalize-release.sh` push `exit 1` | Origin unreachable/rejecting after retries | BLOCK-WORTHY |
| 17 | Tag-exists-on-origin | `team/scripts/cm/release.sh` · `trigger-7.tag-exists-on-origin` | Release tag already exists on origin | BLOCK-WORTHY |
| 18 | Last-3-RCs NON-FUNCTIONAL | `team/scripts/cm/release.sh` · `trigger-8.last-3-rcs-non-functional` | Three consecutive RCs shipped NON-FUNCTIONAL | BLOCK-WORTHY |
| 19 | Release-notes status-stamp guard | `team/scripts/cm/release.sh` · `trigger-9.*` (4 sub-sites) | Placeholder survived / stamp failed / no `## Status` heading | BLOCK-WORTHY |
| 20 | Squash-pollution guard (strict) | `team/scripts/cm/release.sh` · `trigger-10.pollution-guard-strict` | Unexpected paths polluted the squash | BLOCK-WORTHY |
| 21 | Fidelity-gate divergence | `team/scripts/cm/release.sh` · `trigger-11.fidelity-gate-divergence` | RC tree diverges from main after squash | BLOCK-WORTHY |
| 22 | Pre-tag hook gate | `team/scripts/cm/release.sh` · `pre-tag-hook-not-executable` + pre-tag hook failure | Project pre-tag hook non-executable or exits non-zero | BLOCK-WORTHY |
| 23 | Pre-tag VERSION gate | `team/scripts/cm/release.sh` · on-disk `VERSION` vs tag | On-disk `VERSION` ≠ the tag about to be cut | BLOCK-WORTHY |
| 24 | Declared-required hook absent | `team/scripts/ship-rc.sh` / `cm/release.sh` · `cm_release_<phase>_hook_required` | A hook declared `_hook_required=true` is missing | BLOCK-WORTHY |
| 25 | Breakage-class pre-flight gate | `team/scripts/check_breakage_class.py` (via `run-unit-tests.sh`, `run-integration-tests.sh`) | Syntax error, compile failure, or unresolved first-party import | BLOCK-WORTHY |
| 26 | pytest real-defect suite failure | `team/scripts/run-{unit,integration,gated}-tests.sh` · non-zero pytest | A real assertion/defect failure in the suite | BLOCK-WORTHY |
| 27 | `require_dev_tree` pre-flight abort | `team/scripts/lib/dev_tree.sh` (via all three runners) | `dev_tree_path` not configured or does not exist | BLOCK-WORTHY |
| 28 | pytest-not-installed abort | `team/scripts/run-{unit,integration,gated}-tests.sh` · `exit 2` | `pytest` not importable | BLOCK-WORTHY |
| 29 | Per-project dev-tree verify gate | `team/scripts/verify-perproject-devtree-gate.sh` | Behavioral assertion of per-project dev-tree resolution fails | BLOCK-WORTHY |
| 30 | CHANGELOG freshness | `team/scripts/lint_changelog_freshness.py` · `return 1` | Checked-in `CHANGELOG.md` stale vs regeneration | CONVERT |
| 31 | Comment provenance (standalone) | `team/scripts/lint_comment_provenance.py` · `return 1` | Banned provenance token in scanned files | CONVERT |
| 32 | ICD freshness | `team/scripts/lint_icd_freshness.py` · `exit 1` | Checked-in `docs/api/icd.json` stale vs regeneration | CONVERT |
| 33 | ICD compatibility | `team/scripts/lint_icd_compat.py` | ICD breaks a SUPPORTED baseline / manifest unparseable | CONVERT |
| 34 | API parity | `team/scripts/lint_api_parity.py` · `return 1` | API model missing an expected field | CONVERT |
| 35 | Upgrade no-temp-copy guard | `team/scripts/lint_upgrade_no_temp_copy_guard.py` · `return 1` | Forbidden lines in the upgrade pre-retirement block | CONVERT |
| 36 | Help presence (Directive 6) | `team/scripts/lint_help_presence.py` · `return 1` | Script lacks a `--help`/`-h` handler | CONVERT |
| 37 | Python import completeness | `team/scripts/lint_python_import_completeness.py` · `return 1` | Third-party import not covered by `requirements*.txt` | CONVERT |
| 38 | Dashboard network-git gate | `team/scripts/lint_dashboard_network_git_gate.py` · `return 1` | Network-capable git verb under `dashboard/` | CONVERT |
| 39 | Env-bootstrap ordering | `team/scripts/lint_env_bootstrap.py` · `return 1` | Kanban-root env access outside the canonical resolver | CONVERT |
| 40 | Lib-function dedupe | `team/scripts/lint_lib_function_dedupe.py` · `return 1` | Duplicate function definition under `lib/` | CONVERT |
| 41 | Orphan tests | `team/scripts/lint_orphan_tests.py` · `return 1` | `test_*.py` unreachable from any collected root | CONVERT |
| 42 | Test anti-patterns | `team/scripts/lint_test_anti_patterns.py` · `return 1` | Banned test pattern (e.g. bare `/tmp`) | CONVERT |
| 43 | Dual-channel pair | `team/scripts/lint_dual_channel_pair.py` | Refusal narration missing a dual-channel pair | CONVERT |
| 44 | Skip-cites-real-bug gate | `team/scripts/lint_skip_bug_gate.sh` · `exit 1`/`exit 2` | Placeholder skip citation, or cited `BUG-NNNN` with no file | CONVERT |
| 45 | Coding-standards audit | `team/scripts/audit_coding_standards.sh` · `exit 2` | MANUAL-REVIEW-REQUIRED / FINDINGS in the audit | CONVERT |
| 46 | Coder stale-literal check | `team/scripts/coder-stale-literal-check.sh` · `exit 1` | Stale literal / residue in changed code | CONVERT |
| 47 | doc-lint — cmd suite | `team/scripts/harness/doc-lint.sh` | Fenced documented command exits non-zero | CONVERT |
| 48 | doc-lint — links suite | `team/scripts/harness/doc-lint.sh` | Relative Markdown link does not resolve | CONVERT |
| 49 | doc-lint — fossil-greps suite | `team/scripts/harness/doc-lint.sh` | Known internal-residue pattern matches | CONVERT |
| 50 | doc-lint — feature-greps suite | `team/scripts/harness/doc-lint.sh` | Required feature token missing from audited docs | CONVERT |
| 51 | Bare-`/tmp` cleanliness assertion | `team/scripts/run-unit-tests.sh` (via `lib/temp.sh`) | Suite left residue in bare `/tmp` | CONVERT |
| 52 | TCP-listener cleanliness assertion | `team/scripts/run-unit-tests.sh` (via `lib/temp.sh`) | Framework-rooted TCP listener survived the suite | CONVERT |

**Rollup: 52 sites, 0 unclassified.**
27 BLOCK-WORTHY, 23 CONVERT, 2 AGENT-RESOLVABLE.

Rows 13, 19, and 22 each fold more than one `cm_halt` call site of the same
family into a single row (pre-squash executable/failed; the four `trigger-9`
status-stamp sub-sites; pre-tag executable/failed); they are counted once each
because a single conversion or justification decision governs the whole family.

## BLOCK-WORTHY justifications

Every BLOCK-WORTHY row carries a one-sentence justification tied to The Loop
doctrine's backward-motion categories (destructive · breakage · verification
unable to run · approval/invariant by design).

1. **Wake provider dispatch** — Verification unable to run: if the loop's own
   entry point cannot dispatch to a provider, no agent runs at all, so failing
   loudly beats silently doing nothing.
2. **Env-bootstrap source abort** — Verification unable to run: the execution
   environment (paths, root resolution) is unresolved, and every downstream
   step would compound the corruption.
3. **Global HALT guard** — Approval-by-design: the global HALT file is the
   operator's and CM's explicit stop signal, and honoring it is the whole point
   of the flag.
4. **Per-project HALT guard** — Approval-by-design: the per-project HALT is the
   operator's scoped stop signal for one project's lane.
5. **Active-RC single-flight guard** — Invariant-by-design: allowing PM to
   decompose a second release while an RC is in flight would inject unbounded
   mid-release scope creep, moving the release backwards.
8. **Matrix HALT — TESTER BLOCKED** — Verification unable to run: CM cannot ship
   on a verification that never completed.
9. **Matrix HALT — systemic_risk high** — Defect too severe for the next
    iteration to absorb: high systemic risk signals the loop is unhealthy, not
    merely that known bugs exist.
10. **Matrix HALT — serious + large fix** — Defect too severe: shipping through
    a large-effort serious finding risks a build the next patch cannot repair.
11. **Matrix HALT — missing ordered report** — Verification unable to run:
    verification was ordered and its evidence is absent, so the inputs cannot be
    trusted.
12. **Matrix HALT — unrecognized inputs** — Fail-loud on untrusted input: an
    unrecognized state/recommendation string means CM's inputs cannot be
    trusted, and shipping on garbage input is the one silent-default failure the
    matrix must refuse.
13. **Pre-squash hook gate** — Destructive-path: the finalization mechanic is
    broken, so the squash into main cannot proceed safely.
14. **Squash conflict HALT** — Destructive-path: the git state is damaged and
    must not be auto-resolved.
15. **Tag-points-at-wrong-commit** — Destructive-path: a tag mislabeling the
    release would permanently misrepresent what shipped.
16. **Push-fails-after-retries** — Verification/distribution unable to complete:
    the release is locally complete but cannot be distributed, and the operator
    must resolve the network/auth fault.
17. **Tag-exists-on-origin** — Destructive-path/race: re-shipping over an
    existing remote tag would overwrite a published release.
18. **Last-3-RCs NON-FUNCTIONAL** — Loop unhealthy: three degraded ships in a
    row prove the chain is not self-correcting and must pause for review.
19. **Release-notes status-stamp guard** — Fail-loud on untrusted record: the
    published release record cannot be guaranteed to state the actual ship
    decision.
20. **Squash-pollution guard (strict)** — Destructive-path: unexpected paths in
    the squash mean the tree that ships is not the tree that was verified.
21. **Fidelity-gate divergence** — Destructive-path: the RC and main trees are
    not byte-identical, so an out-of-band change would ship unverified.
22. **Pre-tag hook gate** — Destructive-path: a broken pre-tag finalization step
    means the tagged state cannot be trusted.
23. **Pre-tag VERSION gate** — Destructive-path: an on-disk `VERSION` that
    disagrees with the tag would ship a mislabeled build.
24. **Declared-required hook absent** — Breakage-by-contract: the project
    explicitly declared the hook mandatory, so its absence is a mis-release the
    operator opted into catching.
25. **Breakage-class pre-flight gate** — Breakage-class: a syntax/compile/
    first-party-import failure means the suite would produce no trustworthy pass
    signal.
26. **pytest real-defect suite failure** — Breakage/defect: a real assertion
    failure is exactly the backward-motion signal the loop must not ship past.
27. **`require_dev_tree` pre-flight abort** — Verification unable to run: without
    a configured, existing dev tree there is nothing to test.
28. **pytest-not-installed abort** — Verification unable to run: the test runner
    itself is absent, so no pass signal can be produced.
29. **Per-project dev-tree verify gate** — Breakage-class: a failing behavioral
    assertion means the per-project dev-tree resolution the pipeline depends on
    is broken.

## CONVERT notes

The `CONVERT` rows (30–52) are quality-class static findings: each names a
`file:line`/artifact and a fix, and none carries runtime-failure risk to the
running loop. Under the doctrine they should **warn, file a bug, and continue**.

The gated runners already realize this at the orchestration layer:
`team/scripts/lib/gated_runner_lint.sh` runs each quality-class lint and returns
0 regardless of the lint's own exit code, writing a machine-readable findings
file that TESTER surfaces in its "Lint Findings (non-blocking)" report section
(see `roles/CM.md`, "Quality-class lint findings are non-blocking"). The
standalone lint scripts retain their own `exit 1`/`return 1` for direct/CI
invocation — **that** direct-invocation fatal exit is the `CONVERT`-class site
this census tracks, so that any remaining hard-gate call path is converted to
warn-file-continue and any new quality lint inherits the same wrapper.

Rows 51–52 (temp/listener cleanliness) are test-side hygiene assertions: they
detect a test that leaked scratch state, which is zero-risk to the shipped
product, so they belong in the file-and-continue class rather than as a hard
suite abort.

## AGENT-RESOLVABLE notes

The two completion-gate checks (rows 6–7, `coder_writer_completion_gate` in
`team/scripts/lib/wake_common.sh`) are fatal to a CODER/WRITER done-claim but
are self-serviceable in the same session: re-merge the feature branch (row 6)
or commit or discard the dirty worktree (row 7), then re-complete. Per the
doctrine (goal 3 of the v2.3.6 requirements), a refusal here is an agent
chore, not a Needs-Human block — the role files instruct the agent to
resolve it in-session and re-complete. A third check, a diff-scope
comment-provenance lint, previously sat here (through v2.3.10) and is
retired (the requeue-versus-park redesign that moved litter enforcement to verification time): the same litter class is caught exclusively at
TESTER-time by the tree-wide provenance lint already running in the gated
runners' quality-class fleet (row 31), so detection did not regress — only
the redundant, expensive-to-hit stopping point did.

## Locator Index (lint-consumed, BUG-0053)

`lint_gate_census_compliance.py`'s default scan previously flagged every
literal fatal exit (`exit N` / `sys.exit(N)`) in an in-scope file as a
candidate needing its own inline `# BLOCK-WORTHY|CONVERT|AGENT-RESOLVABLE`
comment and dual-channel emitter call — 72 findings on an otherwise clean
`v2.3.9` tree (the clean-tree findings-flood calibration), because none of the census's 53 documented rows had
ever been retrofitted with those inline signals. Retrofitting real emitter
calls at all 72 sites across 15 release-critical scripts (`cm/release.sh`,
`ship-rc.sh`, `cm/finalize-release.sh`, and the rest) would touch live release
machinery at dozens of points for a quality-class lint — BUG-0053 explicitly
sanctions the lower-risk alternative (its Fix option 2): anchor the scan to
this document's own enumerated locators instead.

The index below is that anchor. Each line is
`<path-relative-to-repo-root>:<line> <CLASS>` for a current, real refusal site
in the tree — the exact `(file, line)` the lint's own default scan reports
when run against the current tree, paired with the class the site's row
family above (rollup rows 1–52) already assigns to that kind of gate:
`BLOCK-WORTHY` for hooks/git-operation/tag/version/HALT/RC-state gates
(`cm/release.sh`, `ship-rc.sh`, `cm/finalize-release.sh`, `lib/dev_tree.sh`,
`lib/wake_common.sh`, `verify-perproject-devtree-gate.sh`,
`run-{unit,integration,gated}-tests.sh`, `wake-batch.sh` — rows 1–2, 26–29),
`CONVERT` for the quality-class lint fleet's own precondition/finding exits
(`audit_coding_standards.sh`, `coder-stale-literal-check.sh`,
`harness/doc-lint.sh`, `lint_help_presence.py`, `lint_skip_bug_gate.sh` —
rows 36, 44–50).

`lint_gate_census_compliance.py` parses this fenced block at scan time (see
`_load_census_locators` in the lint script). A default-scope exit that lands
on exactly one of these `(file, line)` pairs is treated as already tracked
and audited at the document level — analogous to the existing operator-CLI
SITE-TYPE exclusion (see "SITE-TYPE SCOPE" in the lint's module docstring) —
and is exempt from the inline code-comment/emitter-call check. A fatal exit
anywhere else in an in-scope file (a genuinely new, undocumented gate) is
still scanned exactly as before, so a new non-compliant gate still surfaces
as an actionable finding.

**Trade-off (documented, not hidden).** This mechanism is line-number
sensitive: an edit that shifts one of these lines without updating the
locator here makes that site reappear as an ordinary finding, even though
its own refusal did not change. That is a deliberately cheap failure mode —
self-healing noise the next `run-unit-tests.sh` surfaces and an operator or
CODER corrects by re-pointing the stale line at its refusal's new location —
not a silent, permanent exemption. Regenerate a stale entry by re-running
`python3 team/scripts/lint_gate_census_compliance.py`, finding the site's new
line number, and updating the entry below; do not delete an entry to silence
a real gate.

<!-- CENSUS-LOCATOR-INDEX-BEGIN -->
team/scripts/audit_coding_standards.sh:157 CONVERT
team/scripts/audit_coding_standards.sh:166 CONVERT
team/scripts/audit_coding_standards.sh:753 CONVERT
team/scripts/cm/finalize-release.sh:172 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:181 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:187 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:241 BLOCK-WORTHY
team/scripts/cm/finalize-release.sh:250 BLOCK-WORTHY
team/scripts/cm/release.sh:280 BLOCK-WORTHY
team/scripts/cm/release.sh:297 BLOCK-WORTHY
team/scripts/cm/release.sh:303 BLOCK-WORTHY
team/scripts/cm/release.sh:404 BLOCK-WORTHY
team/scripts/cm/release.sh:478 BLOCK-WORTHY
team/scripts/cm/release.sh:524 BLOCK-WORTHY
team/scripts/cm/release.sh:530 BLOCK-WORTHY
team/scripts/cm/release.sh:553 BLOCK-WORTHY
team/scripts/cm/release.sh:572 BLOCK-WORTHY
team/scripts/cm/release.sh:584 BLOCK-WORTHY
team/scripts/cm/release.sh:596 BLOCK-WORTHY
team/scripts/cm/release.sh:704 BLOCK-WORTHY
team/scripts/cm/release.sh:1019 BLOCK-WORTHY
team/scripts/cm/release.sh:1026 BLOCK-WORTHY
team/scripts/cm/release.sh:1406 BLOCK-WORTHY
team/scripts/cm/release.sh:1600 BLOCK-WORTHY
team/scripts/cm/release.sh:2053 BLOCK-WORTHY
team/scripts/cm/release.sh:2097 BLOCK-WORTHY
team/scripts/cm/release.sh:2168 BLOCK-WORTHY
team/scripts/cm/release.sh:2185 BLOCK-WORTHY
team/scripts/cm/release.sh:2246 BLOCK-WORTHY
team/scripts/cm/release.sh:2268 BLOCK-WORTHY
team/scripts/cm/release.sh:2384 BLOCK-WORTHY
team/scripts/cm/release.sh:2394 BLOCK-WORTHY
team/scripts/coder-stale-literal-check.sh:56 CONVERT
team/scripts/coder-stale-literal-check.sh:153 CONVERT
team/scripts/harness/doc-lint.sh:119 CONVERT
team/scripts/harness/doc-lint.sh:726 CONVERT
team/scripts/lib/dev_tree.sh:110 BLOCK-WORTHY
team/scripts/lib/dev_tree.sh:127 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:160 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:201 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:378 BLOCK-WORTHY
team/scripts/lib/wake_common.sh:2472 BLOCK-WORTHY
team/scripts/lint_help_presence.py:178 CONVERT
team/scripts/lint_skip_bug_gate.sh:99 CONVERT
team/scripts/lint_skip_bug_gate.sh:231 CONVERT
team/scripts/run-gated-tests.sh:34 BLOCK-WORTHY
team/scripts/run-gated-tests.sh:158 BLOCK-WORTHY
team/scripts/run-gated-tests.sh:202 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:43 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:236 BLOCK-WORTHY
team/scripts/run-integration-tests.sh:261 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:27 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:188 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:213 BLOCK-WORTHY
team/scripts/run-unit-tests.sh:504 BLOCK-WORTHY
team/scripts/ship-rc.sh:62 BLOCK-WORTHY
team/scripts/ship-rc.sh:239 BLOCK-WORTHY
team/scripts/ship-rc.sh:252 BLOCK-WORTHY
team/scripts/ship-rc.sh:311 BLOCK-WORTHY
team/scripts/ship-rc.sh:362 BLOCK-WORTHY
team/scripts/ship-rc.sh:386 BLOCK-WORTHY
team/scripts/ship-rc.sh:513 BLOCK-WORTHY
team/scripts/ship-rc.sh:520 BLOCK-WORTHY
team/scripts/ship-rc.sh:526 BLOCK-WORTHY
team/scripts/ship-rc.sh:533 BLOCK-WORTHY
team/scripts/ship-rc.sh:537 BLOCK-WORTHY
team/scripts/ship-rc.sh:589 BLOCK-WORTHY
team/scripts/ship-rc.sh:621 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:37 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:226 BLOCK-WORTHY
team/scripts/verify-perproject-devtree-gate.sh:333 BLOCK-WORTHY
team/scripts/wake-batch.sh:78 BLOCK-WORTHY
<!-- CENSUS-LOCATOR-INDEX-END -->

## Coverage statement

Every family named in the ticket is covered: HALT triggers (rows 3–5, 8–24),
ship-policy matrix HALT rows (rows 8–12), fatal lint exits (rows 30–52), matrix
HALT rows (rows 8–12), hook guards (rows 13, 22, 24), and wake-time refusals
(rows 1–2). The completion gate (rows 6–7) and breakage-class pre-flight
(row 25) are covered beyond the named minimum. Every row carries exactly one
class; every BLOCK-WORTHY row carries a doctrine-tied justification; zero rows
are unclassified.

**Note on the `SUBSTRATE_BROKEN` signal.** `team/SOP.md` reserves a
`SUBSTRATE_BROKEN` pause signal for substrate-level failures. It is recognized
only by the wake substrate itself and has **no grep-discoverable code site** in
the shipped scripts of this tree, so it is intentionally not tabled above; it is
recorded here so a future reviewer does not read its absence as an omission.
