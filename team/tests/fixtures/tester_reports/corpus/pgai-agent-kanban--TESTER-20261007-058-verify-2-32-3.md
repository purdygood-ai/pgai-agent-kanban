# Verification Report: rc/v2.32.3

Verified RC `ai_rc/v2.32.3` (commit `37f27a8ae31bb8433625658dc32ba525203521a4`) against `requirements/v2.32.3-bugfix-bundle-20261007.md` and its five bundled bugs (BUG-0603 project summary shows template comments, BUG-0604 seeded templates never refreshed, BUG-0605/0606 release-notes stage over its size and inline-Python ceilings, BUG-0607 v2.32.2 notes bullet errata), as decomposed into CODER-053/054/055 and WRITER-056. Both gated runners exited 0 with empty lint findings files. Every bundled item is addressed and every executable acceptance criterion passes. Behavioral checks were run in throwaway roots: a 507-case byte-identity comparison of the moved release-notes generator; real `install.sh --force` and `upgrade.sh --phase2` runs over outdated, operator-edited and missing templates; reproduction of the original project-summary failure on ai_main and its fix on the RC; and the Step 4e stamp on the RC's WRITER-drafted notes. Five follow-ups were filed via Path C (autonomous follow-up filing — non-blocking). Two are small defects this RC introduced: a comment-skipping over-reach in the project summary reader, and a test left pointing at the removed heredoc. Three predate it: a release-notes template line that halts Step 4e if kept, a wrong template path in OPERATIONS.md, and an unattributed mid-RC redeposit of an older release over the live install.

## Recommendation
SHIP-WITH-CONCERNS

## Systemic Risk
medium

## Findings

Suite verdict (Steps 5a, 6.6, 6.7): `run-unit-tests.sh --coverage` UNIT_EXIT=0 (10526 passed, 41 skipped; serial pass 5 passed). `run-integration-tests.sh --coverage` INTEGRATION_EXIT=0 (959 passed, 1 skipped). Both lint pre-flights passed. PASS evidence is these exit codes, not a pytest line.

Acceptance criteria (requirements doc):
- "Each bundled item is addressed in the implementation": pass (BUG-0603, 0604, 0605, 0606 verified behaviorally; BUG-0607 errata present and well-formed).
- "All Python files compile cleanly": pass (841/841).
- "All bash files pass syntax check": pass (335/335).

Per-ticket criteria: all pass except where Findings 1 and 2 below note a caveat. Details are in the step sections after the findings.

### Finding 1: Project summary comment skipping spans sections and mangles quoted comment openers

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one reader in one report)
**Filed As:** BUG-0608-summary-quoted-comment-opener-mangled (Path C-A, filed as BUG — code defect / regression / test failure)
**Filed By:** TESTER (autonomous Path C) — the project summary now mangles a bug's one-line summary when the bug's text quotes the characters that open an HTML comment, because the new comment skipping pairs that quoted opener with a comment end in a later section.
**Regression:** yes
**Integrity:** none

The BUG-0603 fix applies `<!--.*?(?:-->|\Z)` (DOTALL) to everything after the matched heading, not to the section. A quoted `<!--` in inline code is therefore paired with a `-->` in a later section, or with end-of-file when none exists. This breaks CODER-053's constraint ("Output for bugs whose Symptom carries no HTML comment must be unchanged"). It also contradicts the RC release-notes sentence "Bugs without comments render exactly as before." Evidence: ai_main vs RC `_extract_field` over 539 live bug files x 5 fields gave 2 changed reads, both in BUG-0438 (Symptom now splices in Expected-section text). Minimal repro: Symptom "The reader treats a quoted `<!--` as a comment opener." with Expected "Only a real `-->` closes a comment." renders as `The reader treats a quoted `` closes a comment.` on the RC. The intended fix (template guidance comments skipped) is verified separately and works. Rated low under "Rating a Finding": cosmetic text in an informational report, with no dependent path.

### Finding 2: A release-notes test still checks the shell stage instead of the moved generator

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one test function and one docstring)
**Filed As:** BUG-0609-relnotes-test-checks-removed-heredoc (Path C-A)
**Filed By:** TESTER (autonomous Path C) — a release-notes test still checks the shell stage for the Bugs Skipped section after the generator moved into a Python module, so it passes on a shell comment and no longer checks the generator.
**Regression:** yes
**Integrity:** none

`team/tests/unit/test_release_notes_bugs_skipped_render.py::test_generator_source_contains_bugs_skipped_section` greps `team/scripts/lib/cm/release_notes.sh` for "Bugs Skipped". After the generator moved, the only matches are comment lines 130 and 206, so the test is vacuous. `test_release_notes_auto_ship_line.py` line 14 still names the removed RELNOTES_PY heredoc. CODER-055 listed both under "Possible Stale Assertions". This is a test-fidelity finding (role file, "Test fidelity verification"). Behavior is still covered by the file's render tests. Not a stale-assertion in the Step 7 sense: nothing failed.

### Finding 3: The shipped release-notes template halts Step 4e when a draft keeps its Status comment

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (any future WRITER-drafted notes that keep the template's comment will HALT a release)
**Filed As:** BUG-0610-notes-template-comment-halts-stamp (Path C-A)
**Filed By:** TESTER (autonomous Path C) — release notes drafted from the shipped release-notes template halt the release, because the template puts a guidance comment between the Status heading and its placeholder and the ship-time status stamp stops reading at that comment.
**Regression:** no
**Integrity:** none

Reproduced: `cm_stamp_release_notes_status` on an unmodified copy of `team/templates/project/release/RELEASE-NOTES-TEMPLATE.md` prints "release-status placeholder not found" and exits 1. Step 4e (`release_ship_policy.sh` ~610) turns a non-zero exit into `cm_halt "cm-release.trigger-9.stamp-step-failed"`. This release is not affected: the same command on the RC's `release-notes/v2.32.3.md` (comments stripped) exits 0, stamps the status, replaces Bugs Skipped and leaves no placeholder. WRITER-056 flagged this. It predates the RC. Rated medium per core's calibration: a real but unexercised defect in a shipped artefact.

### Finding 4: OPERATIONS.md points at a non-existent bug template path for the Resolved By field

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one sentence of documentation)
**Filed As:** BUG-0611-operations-resolved-by-wrong-template-path (Path C-A)
**Filed By:** TESTER (autonomous Path C) — the operations guide sends readers to a bug template path that does not exist for a field the bug template does not define.
**Regression:** no
**Integrity:** none

`docs/OPERATIONS.md:1031` says "See `projects/<name>/bugs/BUG-TEMPLATE.md` for the field definition" (`## Resolved By`). That path does not exist (copies live under `bugs/templates/`), and no `BUG-TEMPLATE.md` defines the field. It traces to BUG-0604's documentation criterion ("states where the current bug template lives"): the RC's new subsection in the same guide states the location correctly, and this older sentence contradicts it. WRITER-056 flagged it as out of its scope. Predates the RC.

### Finding 5: Live install tooling rewritten mid-RC with an older release, no record of who did it

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks: attribution logging in install/upgrade, investigation, optional lag check)
**Systemic Risk:** medium (live install and release line have drifted before; fixes that only act once installed do not reach the live system)
**Filed As:** BUG-0612-live-install-redeposited-older-release (Path C-A)
**Filed By:** TESTER (autonomous Path C) — while this release candidate was being built, something with no log record rewrote the live install's scripts and package with a release three versions older than the last one shipped.
**Regression:** no
**Integrity:** none

Found by Step 6.12 Q3 and 6.11 (state drift). At 2026-10-07T22:52:29Z, about 15 minutes after the RC opened, 599 files under the live `$KANBAN_ROOT` (`scripts/`, `pgai_agent_kanban/`, `roles/`, `templates/`) and `VERSION` were rewritten. `VERSION` reads `v2.31.6`, while `## Last Released` is `v2.32.2`. All 29 live files that differ between `ai_v2.31.6` and `ai_v2.32.2` match `ai_v2.31.6`. No activity-log entry, upgrade backup tarball, cron/agent log line or dev-tree reflog event exists at that second. The CODER session holding the repo lock was editing source then (its log shows edits at 22:52:38 and no test run before 22:55). The cause is not determined: an unlogged operator action would also explain it. The bug asks for attribution and a lag check rather than asserting an automated fault. Impact on this RC: none observed (its notes are a WRITER draft). The v2.32.2 generator fix is not live, so the next auto-generated notes would regress to the BUG-0607 class.

## Lint Findings (non-blocking)

None. Both findings files were present and empty: `/tmp/pgai_kanban_tmp/lint/findings-unit.json` (0 findings, generated 2026-10-08T00:18:29Z) and `/tmp/pgai_kanban_tmp/lint/findings-integration.json` (0 findings, generated 2026-10-08T00:30:51Z). BUG-0605/0606's `engine_size_dupe` and `inline_python` entries for `release_notes.sh` are gone, as their acceptance criteria require.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=30d freshness=stale — The only record is a red run from 30 days ago against v2.9.20, outside the 7-day window and not this RC's version, so nothing is known about whether v2.32.3's images build; that run's claude-abliterated failure was filed as BUG-0167 (now done), and the podman/rocky9 `COPY --chmod` failure in the same record is cited here, not re-filed.

## Autonomous criterion

1. Tasks force-promoted WAITING to BACKLOG by hand? NO. This task's promotion is recorded "by wake script"; CM-20261007-060 sits in WAITING awaiting this task, as expected.
2. `release-state.md` edited outside the CM open/release/cancel scripts? NO. Its mtime (2026-10-07T22:37:29Z) matches the RC open by CM-20261007-057, and its contents are the four schema fields with well-formed values.
3. Scripts patched mid-build? YES. The live install's tooling was rewritten at 22:52:29Z with a v2.31.6 tree and no attributable record (Finding 5, BUG-0612). RC-branch commits touching `team/scripts/` are this RC's own CODER deliverables (upgrade.sh, release_notes.sh) and are not counted.
4. Queue files edited by hand? NO evidence. Every queue mtime in the window coincides with a wake dispatch event (for example `tester_backlog.md` 23:58:06 vs this task's dispatch record 23:58:07; `writer_backlog.md` 23:57:45 vs a dev-tree checkout at 23:57:45).
5. Non-CM remote git operation? NO. No `git-boundary-guard.jsonl` exists in any of this cycle's task folders (PM-007, CM-057, CODER-053/054/055, WRITER-056, TESTER-058, CM-060). This session ran no fetch, pull or push and never set `PGAI_AGENT_ROLE`. Its one `git clone` copied the local worktree path into a scratch directory for the generator's git-log fallback check.

Autonomous operation criterion: Manual interventions required: one unattributed change (item 3), filed as BUG-0612. It indicates a possible defect (missing install attribution or a lagging deposit path), which is reflected in the recommendation.

## Step results

- 6.1 release-state sanity: pass. Active RC v2.32.3; Last Released v2.32.2 well-formed; `pp_last_released_version` = v2.32.2; `semver_gt v2.32.3 v2.32.2` true.
- 6.2 structural: pass. New files present (`ops/seeded_templates.py`, `cm/release_notes_generator.py`, two test files, `release-notes/v2.32.3.md`); `release_notes.sh` 399 lines; install.sh deposits the whole `pgai_agent_kanban` package, so the new module ships; both workflow template dirs (release, document) carry all three intake templates.
- 6.3 content review: new modules read fully; modified files read at changed regions; docs and release notes read end to end (see WRITER checkpoint).
- 6.4 executable criteria: pass (see checkpoint).
- 6.5 failure-mode/behavioral: pass for the intended fixes; Finding 1 surfaced here.
- 6.8 synthetic release: the diff touches `team/scripts/lib/cm/release_notes.sh` (release cycle). `tests/integration/test_cm_release_synthetic.py` ran inside the green integration suite (including end-to-end hands-free release cases). Pass.
- 6.9 constraint negatives: `set -euo pipefail` preserved in install.sh, upgrade.sh, release_notes.sh; no hardcoded secrets in added lines; no new provenance (bug-shaped strings in added lines are fixture values and a usage example, the sanctioned exceptions; provenance lint 0); no `sys.path`/PYTHONPATH dev-tree target added (the generator loads the scrub by path from its own tree or `PGAI_CM_INTERNAL_ID_SCRUB_DIR`; install/upgrade clear PYTHONPATH and use the installed `pp_run_ops.sh`); inline-python allowlist entry removed, not raised; no `--size-allow-file` added. Pass.
- 6.10 prereq graph: pass. CM-057 (open) -> CODER-053/054/055 -> WRITER-056 -> TESTER-058 -> CM-060 (release); acyclic; all IDs exist; sequence 059 unused (no collision).
- 6.11 spot-check: Finding 5 (state drift). Observations, not filed, are under Caveats.
- 6.12: see Autonomous criterion.
- 6.13 skip-cites-real-bug: pass. The gated `lint_skip_bug_gate` reported "all skip annotations are compliant" (0 missing files). The RC's added skips are `skipif(not install.sh/upgrade.sh exists)` guards that cite no bug. Broad-grep hits on BUG IDs were fixture data in lint self-tests.
- 6.14 coding standards: pass. No new script under `team/scripts/`; the new ops verb has argparse `--help` naming every option; narration strings carry no IDs; the template-dir logic was extracted and shared (`workflow_template_dir`), not duplicated. `project_summary.py` adds a module-local comment regex alongside the sibling readers' copies, which CODER justified because the shared section reader would change comment-free output.
- Coverage (Step 5b, informational): coverage unavailable. Both runners printed "coverage disabled by config ([testing] coverage)" despite `--coverage`. The `coverage/unit.json` and `integration.json` present date from 2026-10-04 and were not used.

## Pre-flight (Steps 2-3)

- Step 2 (RC integrity): 671 DONE task folders name a feature branch; 0 of those branches exist locally (normal post-merge shape), so Check 1 and Check 2 pass trivially. No anomaly.
- Step 3 (working tree): worktree `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20261007-058-verify-2-32-3` is in detached HEAD at `37f27a8ae31bb8433625658dc32ba525203521a4` = `ai_rc/v2.32.3`. `git status --porcelain` empty. `git stash list` empty. Pass.
- RC commits (ai_main..ai_rc/v2.32.3): 4 merges (CODER-053 36c4192e, CODER-054 dafc212e, CODER-055 429440b6, WRITER-056 37f27a8a) with 6 feature commits.

## Checkpoint: BUG-0605 / BUG-0606 (release-notes generator module) — behavioral

- Independent golden comparison: the ai_main RELNOTES_PY heredoc (lines 221-687 of ai_main `release_notes.sh`, run with the ai_main `internal_id_scrub.py`) vs `python3 -m pgai_agent_kanban.cm.release_notes_generator` from the RC, over every requirements file under every project's `requirements/` and `requirements/done/` plus the no-bundle case, each in 3 variants (FUNCTIONAL/no blocks; KNOWN-ISSUE with Known Issues + Bugs Skipped blocks; NON-FUNCTIONAL with Known Issues block). 507 cases: 0 content differences, 0 exit-code or stdout differences.
- No-bundle git-log fallback path against a local clone (merge-base..ai_rc/v2.32.3): byte-identical.
- CLI: fewer than 8 args exits 2 with usage on stderr; `--help` exits 0.
- `release_notes.sh` invocation passes the same 10 positional args and the same `PGAI_CM_INTERNAL_ID_SCRUB_DIR` env var via `pp_run_ops`; `install.sh` copies the whole `pgai_agent_kanban` package, so the new module ships in a live install.

## Checkpoint: BUG-0604 (seeded template refresh) — behavioral

Sandbox root under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/sandbox-t058-install-*` (HOME, KANBAN_ROOT, PGAI_AGENT_KANBAN_ROOT_PATH, TEMP_DIR all repointed; `env -i`).
- `install.sh --force --no-system-cron` over a root with project `alpha` (release; operator-edited BUG template, missing PRIORITY, current REQUIREMENTS) and `beta` (document workflow; outdated BUG, missing others): exit 0; all six copies byte-match their own workflow's installed template; 2 `.bak` backups written, alpha's backup is byte-identical to the operator-edited copy; one narration line per file plus a summary line; no non-template file under `projects/` changed (md5 snapshot).
- Re-run of install: one no-op line, `projects/` byte-identical, no new backups.
- `upgrade.sh --phase2 ... --force` after re-staling alpha's BUG copy: refreshed with backup, exit 0.
- Self-containment: altering only the installed `templates/project/release/PRIORITY-TEMPLATE.md` and running the installed `scripts/lib/pp_run_ops.sh ... refresh_seeded_templates` with PYTHONPATH="" refreshes from the installed copy (marker present), not the dev tree. `--dry-run` wrote nothing.
- Failure isolation: a read-only `beta/requirements/templates/` yields a stderr line naming project and file, exit 1, while `alpha` is still refreshed in the same run. install/upgrade wrap this as a warning (code read).

## Checkpoint: BUG-0603 (project summary skips Symptom comments) — behavioral

- Bug's own Reproduction in a sandbox, run against ai_main and against the RC (PGAI_DEV_TREE_PATH pinned per tree, because `project-summary.sh`'s helper resolver falls back to `$PGAI_DEV_TREE_PATH` when the sandbox root has no installed package):
  - ai_main: `BUG-0998-new-template: [critical] <!-- The opening sentence of this section becomes this bug's bullet in the` (original failure reproduced); comment-only Symptom shows `<!-- guidance`.
  - RC: `BUG-0998-new-template: [critical] The dashboard froze after a resize.`; comment-only Symptom shows the same value as an empty Symptom (`Exp text`, the pre-existing empty-Symptom fall-through). Comment-free bug unchanged.
- Regression sweep, ai_main vs RC `_extract_field` over all 539 live bug files x 5 fields (2695 reads): 2 differ, both in `BUG-0438-ship-policy-fails-open-on-value-formatting.md`, whose Symptom quotes `` `<!--` `` in inline code. See Finding 1.

## Checkpoint: executable acceptance criteria (Step 6.4)

- Requirements AC "All Python files compile cleanly": `python3 -m py_compile` over all 841 tracked `*.py` (scratch pycache prefix): 0 failures. PASS.
- Requirements AC "All bash files pass syntax check": `bash -n` over 335 tracked `*.sh` + bash-shebang files: 0 failures. PASS.
- `python3 team/scripts/lint_inline_python.py`: exit 0, 0 findings (3042 lines). `release_notes.sh` allowlist entry (was `1 341`) removed, not raised. PASS.
- `python3 team/scripts/lint_engine_size_dupe.py`: exit 0, "55 engine file(s) within the 800-line ceiling". `release_notes.sh` is 399 lines. PASS.
- `python3 team/scripts/lint_hygiene.py --classes provenance`: exit 0, 0 findings (covers release-notes/, docs/, code, tests). PASS.
- `release-notes/v2.32.2.md` and `CHANGELOG.md` untouched by the RC diff. PASS.

## Checkpoint: WRITER deliverables

- `release-notes/v2.32.3.md`: template section structure (Status, Summary, What Shipped, Bugs Resolved, Bugs Skipped, Known Issues) plus a labeled `## Errata`; Status body exactly the release-status placeholder; errata is one complete sentence naming what v2.32.2 fixed, contains no triple backtick and does not end in `:`; each RC fix described by substance; Bugs Skipped and Known Issues `None.`. PASS, with one inaccurate claim noted under Finding 1 ("Bugs without comments render exactly as before.").
- Step 4e compatibility of the draft: `cm_stamp_release_notes_status` on a copy of the RC draft with a Bugs Skipped block exits 0, stamps `## Status`, replaces Bugs Skipped, leaves no release-status placeholder. PASS for this release.
- `docs/OPERATIONS.md` new subsection and `docs/tester-verification-reference.md` paragraph: location of the current template, the refresh, per-copy policy, narration format, failure behavior and re-run/dry-run commands all match behavior observed in the sandbox runs above. PASS.

## Caveats

- Status at start read WORKING (wake-stamped on dispatch) with no partial report, so this was treated as a fresh start. No `report.md.previous-RUN-N` existed; no prior verdict was consulted.
- Role-file "Git workflow" says to commit verification artifacts on a feature branch off the RC. The dispatch prompt said not to create a feature branch, and Step 9 says process artifacts are never committed into a dev tree. Artifacts live only in the task folder; no commit was made.
- Observation (not filed, pre-existing, outside the spec's trace): `team/scripts/project-summary.sh` resolves its Python module through `resolve_dashboard_helper`, which falls back to `$PGAI_DEV_TREE_PATH/team/pgai_agent_kanban/...` when the kanban root has no installed copy. SOP-core ("Git Repositories") forbids dev-tree fallbacks in live-runtime code. It made my first ai_main reproduction silently run the RC module until I pinned the variable per tree.
- Observation (not filed): `release-notes/v2.32.3.md` carries `**Release Date:** 2026-10-07`. The RC is verified on 2026-10-08 UTC and WRITER notes no CM step stamps that field, so the date may be a day early at ship. Cosmetic.
- Observation (not filed): the refresh's closing summary line ("refreshed N file(s) ...") does not mention skips when some files were written and others skipped. The per-file stderr line does name each skip, and the exit code is 1.
- Sandboxes and scratch from this run are under `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/` (`sandbox-t058-install-*`, `sandbox-t058-summary-*`, `tester-058/`). Nothing was written to the live install or any live project file except the five Path C bug files and this task folder.
- No suspicious instructions (prompt-injection style content) were found in the files read.
