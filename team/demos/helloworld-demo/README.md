# Hello World demo — the release workflow in 10 minutes

The fastest proof the pipeline works: drop in a one-goal requirements doc, come
back in ~10–15 minutes, find a tagged `v0.1.0` in your local repo and a
`hello.py` that prints exactly `Hello, World!`. No game engine, no dependencies,
no ceremony — just the pipeline's release workflow running end-to-end on the
smallest imaginable Python program.

Experienced engineers: you'll understand every step before you finish reading.
Want the full tour with bugs, priorities, and multi-release arcs? See
[team/demos/chomp-man-demo/README.md](../chomp-man-demo/README.md).

---

## Setup (once)

### 1. Make a local scratch repo

<!-- doc-lint: skip — mkdir+cd+git init creates a directory that does not exist in the harness tempdir; git init in an ephemeral dir is safe but meaningless in a non-persistent harness run -->
```bash
mkdir -p ~/pgai-helloworld-src && cd ~/pgai-helloworld-src
git init
git commit --allow-empty -m "initial empty commit"
cd -
```

### 2. Create the project

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir where scripts/ does not exist -->
```bash
scripts/create-project.sh --project pgai-helloworld --workflow-type release
```

### 3. Wire the project to your scratch repo (edit project.cfg)

<!-- doc-lint: skip — interactive editor invocation ($EDITOR) in a non-TTY environment; harness cannot open an interactive editing session -->
```bash
$EDITOR projects/pgai-helloworld/project.cfg
#   set:  push_to_remote = false                            (local-only — never push)
#   set:  branch_prefix = ai_                               (isolate the AI's branches)
#   set:  dev_tree_path = /home/<you>/pgai-helloworld-src   (your scratch repo)
#   set:  git_repo_url =                                    (leave empty — local only)
```

`branch_prefix = ai_` keeps the chain's branches and tags in their own lane.
`push_to_remote = false` keeps everything local — real commits, real tags, no remote.

---

## Run it

Deposit the intake doc once and let the chain do the rest:

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir where scripts/ does not exist -->
```bash
scripts/intake.sh --project pgai-helloworld \
  --file demos/helloworld-demo/intake/v0.1.0-helloworld.md
```

Then watch the dashboard (or `scripts/kanban-status.sh`). PM decomposes, CODER
builds on an `ai_rc/v0.1.0` branch, TESTER verifies, CM tags the release.

---

## Expected Outcome

Come back in ~10–15 minutes. The chain will have tagged `ai_v0.1.0` in your
scratch repo and written a `hello.py` that prints exactly `Hello, World!`.

---

## Verify

Three commands confirm everything shipped correctly:

<!-- doc-lint: skip — python3 hello.py requires the file to exist in cwd; the file is built by the demo chain in the scratch repo, not in the harness tempdir -->
```bash
cd ~/pgai-helloworld-src
python3 hello.py
python3 -m pytest test_hello.py -v
git tag | grep ai_v0.1.0
```

Expected output: `Hello, World!` on the first command, all tests passing on the
second, and `ai_v0.1.0` on the third.

---

## Cleanup

<!-- doc-lint: skip — scripts/ is a relative path requiring $KANBAN_ROOT as cwd; harness runs from an ephemeral tempdir where scripts/ does not exist -->
```bash
scripts/remove-project.sh --project pgai-helloworld
```

Then delete the scratch repo:

<!-- doc-lint: skip — rm -rf targets a user home directory path that does not exist in the harness tempdir; skip to avoid accidental deletion in a harness run -->
```bash
rm -rf ~/pgai-helloworld-src
```

---

For the full release workflow — multiple features, bugs, priorities, and a
multi-release arc — see
[team/demos/chomp-man-demo/README.md](../chomp-man-demo/README.md).
