# Hybrid Shop Setup

Operator guide for configuring pgai-agent-kanban in a shop where human developers and AI agents share the same repository, using the `branch_prefix` feature to keep AI-generated branches visually distinct.

## What this is

In a hybrid shop, humans and AI agents both push branches and cut releases into one repository. Without a marker, AI-generated work is indistinguishable from human work in `git log`, in pull-request lists, and in branch protection rules.

The `branch_prefix` setting solves that. When set to a short identifier like `ai_` or `team-`, every release-candidate branch and every release tag the kanban produces is prefixed with that string. Humans continue to use their own branch naming; AI work is grep-able at a glance.

Pure-AI shops (the default) need no configuration — leaving `branch_prefix` empty produces unprefixed branch and tag names.

## Prerequisites

Before you begin you should have:

- A working pgai-agent-kanban install.
- A project already registered under `projects/<name>/` with its own `project.cfg`.
- No release candidate currently in flight for the project you are configuring. Check by running `git branch` in the project's dev tree and confirming no `rc/v*` (or already-prefixed `<prefix>rc/v*`) branch exists.
- A clean git working tree in the project's dev tree.

If a release candidate is open, finish or cancel it before changing the prefix. See [Switching prefix mid-release](#switching-prefix-mid-release) below.

## Installing on an existing repository

If you already have a git repository with human work on `main`, the kanban attaches to it without disturbing existing branches.

1. Install pgai-agent-kanban as a sub-dependency of your workflow (or alongside it), and point its `dev_tree_path` in `project.cfg` at your existing repository's checkout. The kanban will operate inside that working tree.
2. Before the kanban runs for the first time, `cd` into the dev tree, check out `main`, and pull the latest. The first AI cycle uses `main` as its starting point.
3. On first use, the kanban creates `ai_main` (the prefixed version of its long-running integration branch) by branching from `main`. RC branches (`ai_rc/vX.Y.Z`) branch from `ai_main`, ship into it via a single squash, and are deleted — the single-lane flow uses `ai_main` as the sole long-running AI branch. Your human `main` is left untouched; the AI never commits directly to it.
4. On the remote, set branch protection rules that match the split: protect `main` against direct pushes from the kanban's service account, and permit the same account to push branches matching `ai_*`. Human developers continue to interact with `main` through their normal workflow.
5. No existing branches need to be renamed. Human feature branches, release branches, and tags stay exactly as they were.

The kanban's footprint on the repository is entirely contained within branches and tags matching the configured `branch_prefix`. Removing the kanban later is as simple as deleting those refs.

## Step-by-step configuration

### 1. Choose a prefix

Pick a short, recognizable string. Common choices:

| Prefix    | Reads as                  |
|-----------|---------------------------|
| `ai_`     | AI-generated              |
| `team-`   | A specific team's bot     |
| `MyOrg_`  | An org-wide AI identity   |

Allowed characters are letters (`a-z`, `A-Z`), digits (`0-9`), underscore (`_`), and hyphen (`-`). Anything else — spaces, slashes, dots, punctuation — is rejected.

| Valid       | Invalid       |
|-------------|---------------|
| `ai_`       | `my prefix`   |
| `team-`     | `feat/`       |
| `MyOrg_`    | `v1.0`        |

End the prefix with a separator character (`_` or `-`) so the resulting branch names read cleanly. `ai_rc/v0.31.0` is easier to scan than `airc/v0.31.0`.

### 2. Edit `project.cfg`

Open the project config:

```
projects/<name>/project.cfg
```

Under the `[project]` section, add or uncomment the `branch_prefix` key and set your chosen value:

```ini
[project]
; ...other keys...
branch_prefix = ai_
```

Save the file. No restart is required — the next AI workflow invocation will read the new value.

### 3. Confirm by opening a release candidate

The simplest correctness check is to open a fresh RC and look at the resulting branch name. If your prefix is `ai_`, the branch should appear as `ai_rc/v<next-version>` rather than `rc/v<next-version>`.

If the prefix is malformed, the kanban refuses to proceed and reports the invalid value. Fix the config and try again.

## Watching the first RC

Once the kanban opens its first prefixed release candidate, the operator's job is mostly observation. A short checklist confirms the prefix is taking effect end-to-end.

1. In the dev tree, run `git branch` and confirm `ai_rc/v0.31.0` (or whatever your next version is) appears. The unprefixed `rc/v0.31.0` form should not exist.
2. Open the kanban dashboard. The RC should show as active for this project, with its prefixed version string in the release column.
3. As tasks progress, feature branches will appear as `ai_feature/<task-id>`. Confirm they merge back into `ai_rc/v0.31.0`, not into `rc/v0.31.0` or any unprefixed branch.
4. When all tasks reach DONE and the TESTER role passes the RC, the CM role merges the RC into `ai_main` and creates a tag named `ai_v0.31.0`. This is the AI's release artifact.
5. You do not need to intervene during the RC itself. The operator's gate is at the `ai_main` → `main` boundary, not inside the RC.

If anything in this list appears without the prefix — an unprefixed RC branch, an unprefixed feature branch, an unprefixed tag — stop and re-check `project.cfg`. The prefix should be visible at every step.

## Expected behavior per mode

### Pure-AI mode (default, empty prefix)

`branch_prefix` omitted or set to `branch_prefix =` produces unprefixed branch and tag names:

| Artifact          | Form          |
|-------------------|---------------|
| RC branch         | `rc/vX.Y.Z`   |
| Release tag       | `vX.Y.Z`      |
| Dashboard display | `vX.Y.Z`      |

### Hybrid mode (prefix set)

With `branch_prefix = ai_` (or whatever value you chose), every AI-generated branch and tag for that project carries the prefix:

| Artifact          | Form              |
|-------------------|-------------------|
| RC branch         | `ai_rc/vX.Y.Z`    |
| Release tag       | `ai_vX.Y.Z`       |
| Dashboard display | `ai_vX.Y.Z`       |

Branches pushed to the remote carry the prefix too — the AI does not push under a different name than it works under locally. This matters for branch protection rules: a rule scoped to `ai_*` will cover every AI branch the kanban produces.

Human-created branches are not affected. The prefix only governs what the kanban itself creates.

Internally, semantic version parsing strips the prefix before extracting `X.Y.Z`, so `ai_v0.31.0` is still recognized as version `0.31.0` for ordering and comparison.

## Verification

After setting `branch_prefix` and before turning AI agents loose on production work, verify the configuration end-to-end.

### Check the local branch

After the next RC opens, list local branches and confirm the prefixed name is present:

```bash
git branch | grep rc/
```

You should see your prefixed RC branch (for example `ai_rc/v0.31.0`) and nothing matching the unprefixed `rc/v*` pattern.

### Check the remote push target

After the RC has been pushed, list remote branches:

```bash
git branch -r | grep rc/
```

The remote should carry the same prefixed name (for example `origin/ai_rc/v0.31.0`). If you see an unprefixed branch on the remote, the prefix is only being applied locally — stop and investigate before proceeding.

### Check the release tag

After a release ships, list tags:

```bash
git tag --list "*v*"
```

Confirm the new tag is prefixed (for example `ai_v0.31.0`).

### Check the dashboard

Open the unified dashboard. The project's release column should display the prefixed version string. If it shows the bare `vX.Y.Z` form, the dashboard scripts are not filtering on the prefix — check that you edited the right project's `project.cfg`.

### Run a full cycle under each mode

Before relying on the prefix in production, run one complete RC cycle under each configuration you intend to use:

1. With `branch_prefix` empty, open an RC, ship it, and confirm the unprefixed branch and tag appear as expected.
2. With `branch_prefix = ai_` (or your chosen value), open an RC, ship it, and confirm the prefixed branch and tag appear, both locally and on the remote.

If both cycles complete cleanly, the configuration is sound.

## First PR/MR: ai_main → main

Once the kanban tags its first release on `ai_main`, the operator decides when and how that work lands on the human `main`. This is the manual gate the hybrid setup is designed around.

1. **Review the tag.** Run `git show ai_v0.31.0` to inspect the tagged commit. Confirm the squash message reads accurately, then open `release-notes/v0.31.0.md` and read what the AI claims it shipped.
2. **Check the diff.** Run `git diff main ai_main`. This is the full set of changes the AI produced that have not yet reached human `main`. Skim it the way you would skim a contractor's pull request.
3. **Run your own tests.** At minimum, check out `ai_main` locally and run the project's full test suite. Do not rely solely on the AI's own TESTER pass — your CI configuration and your test environment may differ from the kanban's.
4. **Create the PR/MR.** In your git host (GitHub, GitLab, Bitbucket, etc.), open a pull or merge request from `ai_main` into `main`. Add whatever reviewers and approval gates your team's policy requires.
5. **Choose a merge strategy.** Squash-and-merge lands the entire AI release as a single commit on `main` — cleaner history, but you lose the per-task granularity. Merge-commit preserves every intermediate commit the AI made — fuller history, but the log on `main` interleaves AI commits with human ones. Either is valid; pick one and document the choice so future operators stay consistent.
6. **After merge.** `main` now carries the AI's release. If your team tags releases on `main`, add a parallel tag there (for example `v0.31.0` alongside the AI's `ai_v0.31.0`) per your normal release convention.

### Ongoing sync

After the first PR/MR lands, the two branches need to stay roughly in step or the next AI RC will conflict against stale state.

- The kanban handles the common case automatically. On its next cycle after a merged PR, it back-merges `main` into `ai_main` so the AI's integration branch picks up whatever shipped to human `main`. You do not need to do this manually for the normal PR-merge path.
- The exception is direct human pushes to `main` — hotfixes, emergency reverts, or any commit that bypasses the AI's release flow. The kanban will not see these until they appear on `main` and it next syncs. To force-sync ahead of the next AI cycle, run `git fetch origin && git checkout ai_main && git merge origin/main` in the dev tree.
- Watch the dashboard. If `ai_main` has fallen more than a handful of commits behind `main`, sync it manually before triggering the next RC. Letting it drift makes the next RC's merge-up step harder than it needs to be.

## Switching prefix mid-release

**Switching `branch_prefix` while a release candidate is open is unsupported.** The kanban does not rename in-flight branches, retag past releases, or migrate dashboard state across a prefix change.

The safe path is to wait. Let the current RC ship or cancel it cleanly, then change the prefix before the next `open-rc`.

If you change `branch_prefix` while an RC is open, you can leave the framework in a state where:

- The open RC branch still carries the old prefix (or no prefix).
- New release tooling looks for the new prefix and fails to find the in-flight branch.
- Dashboard scripts disagree about which version is current.

Recovery from that state is manual and project-specific. Avoid it by treating the prefix as a setting you choose once and only revisit between releases.

Switching the prefix on a fresh project — one with no RC open and no prior releases — is fully supported. Pick a value, set it, and proceed.

## Prefix conventions and bumping

The prefix is a setting you pick once and rarely change. A few rules govern when and how to change it cleanly.

- **Do not change the prefix while an RC is open.** This is covered in detail under [Switching prefix mid-release](#switching-prefix-mid-release). Treat that section as authoritative on mid-flight changes.
- **Legitimate reasons to change a prefix** include team rebranding (the bot used to be `oldteam_`, now it is `newteam_`), switching the CI service account that owns the AI workflow, or splitting two AI workflows that previously shared a single prefix into distinct identities.
- **How to bump cleanly.** Wait for no RC to be open. Edit `branch_prefix` in `project.cfg` to its new value. Open the next RC. That RC and every artifact created from it forward will carry the new prefix. Old branches and old tags keep their old prefix permanently — there is no retroactive rename, and you should not attempt one by hand.
- **Pick the naming convention on day one.** End the prefix with `_` or `-` so the resulting branch names stay readable (`ai_rc/v0.31.0` reads cleanly; `airc/v0.31.0` does not). Changing the trailing separator later counts as a prefix change and follows the same wait-for-clean-RC rule.

In practice most projects pick a prefix at install time and never revisit it. The framework supports change, but the operational cost of a change is high enough that you should design to avoid it.

## FAQ and troubleshooting

### I set a prefix and now my old branches look unprefixed

That is expected. Existing branches and tags are not retroactively renamed. The prefix only applies to artifacts created after the setting is in place.

### My prefix has a slash in it and the kanban rejects it

Slashes are reserved for the path component of branch names (for example the `/` in `rc/v0.31.0`). A prefix like `feat/` would produce nested-looking branch names and is rejected to keep things unambiguous. Use a hyphen or underscore separator instead: `feat-` or `feat_`.

### My prefix is a literal version string like `v1.0` and it is rejected

Dots are also not allowed, for the same reason: they conflict with the semantic-version segments later in the branch and tag names. Pick a non-versioned identifier.

### I set the prefix after the RC was already open and now things are confused

This is the unsupported case. See [Switching prefix mid-release](#switching-prefix-mid-release). If the RC has not yet been shipped, the cleanest recovery is to cancel it, revert `branch_prefix` to its pre-change value, reopen the RC under the original prefix, and ship or cancel it cleanly. Then change the prefix and proceed with the next cycle.

### Can different projects in the same installation have different prefixes?

Yes. `branch_prefix` is a per-project setting. Each project's `project.cfg` is independent, so a single installation can host a pure-AI project alongside a hybrid project with prefix `ai_` alongside another hybrid project with prefix `team-`.

### Does the prefix apply to human-created branches?

No. The setting only governs branches and tags that the kanban itself creates. Human developers continue to use whatever naming convention they like.

### What if I want to remove the prefix and go back to pure-AI mode?

Wait until no RC is open, then set `branch_prefix =` (or delete the line). Subsequent branches and tags will be created without a prefix. Past prefixed artifacts are left in place; nothing is renamed.

## See also

- `project.cfg_example` — annotated template at the repository root, including the full `branch_prefix` reference.
- `docs/projects-cfg.md` — registry-level configuration for which projects exist and how the dashboard renders them.
