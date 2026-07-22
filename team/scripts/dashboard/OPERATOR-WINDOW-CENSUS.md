# OPERATOR-WINDOW-CENSUS.md
# Window-Index Call-Site Census for Dashboard Scripts

**Purpose:** Enumerate every dashboard call site that targets a tmux window by
numeric index or that assumes a specific window ordering, so the subsequent
operator-window insertion has a complete map of what to update.

**Task:** CODER-20260722-009-window-index-census
**Date:** 2026-07-22
**Related:** PRIORITY-0009 — operator dashboard window insertion at index 2

---

## Current Window Creation Order

Windows are created in this sequence in `team/scripts/dashboard/create.sh`.
tmux assigns indices starting from 0 (no `base-index` override found).
The comment headers in create.sh use 1-based "W" labels (W1, W2, …).

| Comment label | Window name     | tmux index (current) | create.sh line |
|---------------|-----------------|----------------------|----------------|
| W1 / Window 0 | `main`          | 0                    | 515            |
| W2            | `visibility`    | 1                    | 802            |
| W3            | `attention`     | 2                    | 955            |
| W4            | `git`           | 3                    | 981            |
| W5            | `metadata`      | 4                    | 993            |
| W6            | `metrics`       | 5                    | 1017           |
| W7            | `logs`          | 6                    | 1025           |
| W8            | `debug-logs`    | 7                    | 1041           |
| W9            | `training-logs` | 8                    | 1062           |
| W10           | `terminal`      | 9                    | 1078           |
| W14           | `human-review`  | 10                   | 1096           |
| W11+          | `drill-N`       | 11+                  | 1178 / 1201    |

Note: The "W14" label in create.sh comments is a reserved label (not tmux
index 14). The tmux index for `human-review` is 10 because it is the 11th
window created. The "W11+" drill windows occupy tmux indices 11 onward.

---

## Target Order After Inserting `operator` at Index 2

When an `operator` window is inserted after `main` and before `visibility`,
the indices of all subsequent windows shift by +1.

| Comment label | Window name     | tmux index (after insertion) |
|---------------|-----------------|------------------------------|
| W1            | `main`          | 0  (unchanged)               |
| NEW           | `operator`      | 1  (inserted)                |
| W2            | `visibility`    | 2  (was 1)                   |
| W3            | `attention`     | 3  (was 2)                   |
| W4            | `git`           | 4  (was 3)                   |
| W5            | `metadata`      | 5  (was 4)                   |
| W6            | `metrics`       | 6  (was 5)                   |
| W7            | `logs`          | 7  (was 6)                   |
| W8            | `debug-logs`    | 8  (was 7)                   |
| W9            | `training-logs` | 9  (was 8)                   |
| W10           | `terminal`      | 10 (was 9)                   |
| W14           | `human-review`  | 11 (was 10)                  |
| W11+          | `drill-N`       | 12+ (were 11+)               |

---

## Grep Commands Used

All searches were run against the worktree at:
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260722-009-window-index-census`

```bash
# Pattern 1: select-window calls
grep -rn "select-window" team/scripts/

# Pattern 2: -t with numeric window index (session:N without pane dot)
grep -rn "\-t [^'\"]*:[0-9]" team/scripts/

# Pattern 3: window_index format variable
grep -rn "window_index" team/scripts/

# Pattern 4: new-window calls
grep -rn "new-window" team/scripts/

# Pattern 5: hard-coded numeric targets (session:N)
grep -rn "\-t.*:[0-9][0-9]*[^.]" team/scripts/dashboard/

# Pattern 6: move-window / swap-window
grep -rn "move-window\|swap-window" team/scripts/

# Pattern 7: list-windows
grep -rn "list-windows" team/scripts/

# Pattern 8: W-number comment labels
grep -rn "W[0-9][0-9]*:" team/scripts/dashboard/create.sh

# Pattern 9: direct numeric window targets (no pane, no name)
grep -rn '"\${SESSION_NAME}:[0-9]"' team/scripts/dashboard/
grep -rn "\-t.*\${SESSION_NAME}:[0-9]" team/scripts/dashboard/

# Pattern 10: Python tests referencing window indices
grep -rn "window_index\|select.window\|new.window\|list.windows" team/tests/
```

---

## Census Results

### A. `select-window` call sites

#### A1. `team/scripts/dashboard/create.sh:1257`

```bash
tmux select-window -t "${SESSION_NAME}:main"
```

**Classification:** NAME-BASED (safe)
**Needs update when `operator` inserted at index 2?** No.
Targets window by name `main`. tmux resolves by name regardless of index.

#### A2. `team/scripts/dashboard/create.sh:1258`

```bash
tmux select-pane -t "${SESSION_NAME}:main.0"
```

**Classification:** NAME-BASED (safe)
**Needs update?** No.
This is a pane selection within the named window `main`. Not a window-index reference.

#### A3. `team/scripts/dashboard/project-toggle.sh:135`

```bash
tmux select-window -t "${SESSION_NAME}:${TARGET_WINDOW}"
```

**Classification:** NAME-BASED (safe)
**Needs update?** No.
`TARGET_WINDOW` is always set to either `"main"` or the result of `tmux list-windows`
name parsing (e.g., `"drill-1"`, `"drill-2"`). Never a bare numeric index.

---

### B. `window_index` format variable

#### B1. `team/scripts/dashboard/create.sh:570`

```bash
"#{W:#{?#{m:drill-*,#{window_name}},,#{?window_active,#[fg=white bold],#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} }}"
```

This is a **tmux status-format[0] string** (the main status bar line). It uses
`#{window_index}` as a **tmux format token** — this is tmux's own variable
rendering the tab's index in the status bar display. It is not a hardcoded
literal. The filter `#{?#{m:drill-*,#{window_name}},,OUTPUT}` excludes drill-N
windows from this line.

**Classification:** NAME-BASED in terms of routing; `#{window_index}` is a
display-only tmux format variable (not a hardcoded literal).
**Needs update?** No. After inserting `operator`, tmux automatically renumbers
and `#{window_index}` will display the new indices correctly in the status bar.
The operator window will appear with its tmux-assigned index automatically.

#### B2. `team/scripts/dashboard/create.sh:581`

```bash
"#{W:#{?#{m:drill-*,#{window_name}},#{?window_active,#[fg=white bold],#[window-status-style]}#{window_index}:#{window_name}#{?window_active,*,} ,}}"
```

This is **status-format[1]** — the drill-window-only line. Same as B1: uses
`#{window_index}` as a tmux format token for display only.

**Classification:** NAME-BASED (display format token, not a hardcoded literal).
**Needs update?** No. Automatic via tmux.

#### B3. `team/scripts/dashboard/project-toggle.sh:84`

```bash
tmux list-windows -t "$SESSION_NAME" -F "#{window_index}	#{window_name}" 2>/dev/null \
  | awk -F'\t' '$2 ~ /^drill-[0-9]+/ { print $2 }' \
  | sort -t- -k2 -n
```

`#{window_index}` is used in the format string passed to `tmux list-windows`
to retrieve the index alongside the name, but the result is immediately piped
through `awk` which filters on name (`$2 ~ /^drill-[0-9]+/`) and **prints only
the name** (`print $2`). The index is not retained or used in any window selection.

**Classification:** NAME-BASED (index is a sort key only, discarded).
**Needs update?** No. The filter is name-based; adding a new named window does
not affect drill-N discovery.

---

### C. `new-window` call sites (window creation order)

All `new-window` calls use `-n <name>` and never specify a numeric window index
in the `-t` target beyond the session name. None are index-based.

| Line | Command snippet | Classification | Needs update? |
|------|-----------------|----------------|---------------|
| 802  | `tmux new-window -t "${SESSION_NAME}" -n "visibility"` | NAME-BASED | No |
| 955  | `tmux new-window -t "${SESSION_NAME}" -n "attention"` | NAME-BASED | No |
| 981  | `tmux new-window -t "${SESSION_NAME}" -n "git"` | NAME-BASED | No |
| 993  | `tmux new-window -t "${SESSION_NAME}" -n "metadata"` | NAME-BASED | No |
| 1017 | `tmux new-window -t "${SESSION_NAME}" -n "metrics"` | NAME-BASED | No |
| 1025 | `tmux new-window -t "${SESSION_NAME}" -n "logs"` | NAME-BASED | No |
| 1041 | `tmux new-window -t "${SESSION_NAME}" -n "debug-logs"` | NAME-BASED | No |
| 1062 | `tmux new-window -t "${SESSION_NAME}" -n "training-logs"` | NAME-BASED | No |
| 1078 | `tmux new-window -t "${SESSION_NAME}" -n "terminal"` | NAME-BASED | No |
| 1096 | `tmux new-window -t "${SESSION_NAME}" -n "human-review"` | NAME-BASED | No |
| 1178 | `tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"` | NAME-BASED | No |
| 1201 | `tmux new-window -t "${SESSION_NAME}" -n "${_DRILL_WIN}"` | NAME-BASED | No |

**No `new-window` call specifies a numeric window index.** All new windows are
appended to the session (tmux default behavior when only session name is given
in `-t`). The insertion of an `operator` window must use the same pattern.

---

### D. `send-keys` and `resize-pane` — pane index references (not window indices)

These commands use the format `session:window_name.pane_index` (note the `.`
separator). The numeric part is a **pane index within a named window**, not a
window index.

**Classification:** All NAME-BASED at the window level (pane numbers are
intra-window, not cross-window references).
**Needs update?** No. Pane indices within named windows are unaffected by
inserting a new window at a different position.

Representative examples (all follow the same `session:name.pane` pattern):

| File | Line | Snippet |
|------|------|---------|
| create.sh | 732 | `send-keys -t "${SESSION_NAME}:main.0"` |
| create.sh | 733 | `send-keys -t "${SESSION_NAME}:main.1"` |
| create.sh | 722 | `resize-pane -t "${SESSION_NAME}:main.0"` |
| create.sh | 937 | `send-keys -t "${SESSION_NAME}:visibility.0"` |
| create.sh | 983 | `send-keys -t "${SESSION_NAME}:git.0"` |
| create.sh | 1019 | `send-keys -t "${SESSION_NAME}:metrics.0"` |
| create.sh | 1045 | `send-keys -t "${SESSION_NAME}:debug-logs.0"` |
| create.sh | 1066 | `send-keys -t "${SESSION_NAME}:training-logs.0"` |
| create.sh | 1081 | `send-keys -t "${SESSION_NAME}:terminal.0"` |
| create.sh | 1097 | `send-keys -t "${SESSION_NAME}:human-review"` |
| create.sh | 1183 | `send-keys -t "${SESSION_NAME}:${_DRILL_WIN}.0"` |

---

### E. `display-message` — dimension queries

| File | Line | Snippet | Classification |
|------|------|---------|----------------|
| create.sh | 654 | `display-message -t "${SESSION_NAME}:main" -p '#{window_width}'` | NAME-BASED |
| create.sh | 655 | `display-message -t "${SESSION_NAME}:main" -p '#{window_height}'` | NAME-BASED |
| create.sh | 853 | `display-message -t "${SESSION_NAME}:visibility" -p '#{window_width}'` | NAME-BASED |
| create.sh | 854 | `display-message -t "${SESSION_NAME}:visibility" -p '#{window_height}'` | NAME-BASED |
| project-toggle.sh | 98 | `display-message -t "$SESSION_NAME" -p '#{window_name}'` | NAME-BASED |

All use window names. None are index-based.

---

### F. `verify-window0-geometry.sh` — window-0 reference

`team/scripts/dashboard/verify-window0-geometry.sh:78`:

```bash
WINDOW_TARGET="${SESSION_NAME}:main"
```

The script accesses the main window by name (`main`) not by numeric index,
even though the script is named `verify-window0-geometry.sh`.

**Classification:** NAME-BASED (safe).
**Needs update?** No. After operator window insertion, `main` remains `main`.

---

### G. Comment-only W-number labels (index-based in documentation only)

These appear in create.sh comment blocks and `attention.sh` header comments.
They are informational only — no tmux command execution depends on them.

| File | Line | Snippet | Notes |
|------|------|---------|-------|
| create.sh | 21 | `# W2: Visibility` | 1-based label; W1=main |
| create.sh | 27 | `# W3: Attention` | 1-based label |
| create.sh | 28 | `# W4: Git` | 1-based label |
| create.sh | 34 | `# W5: Metadata` | 1-based label |
| create.sh | 37 | `# W6: Metrics` | 1-based label |
| create.sh | 43 | `# W7: Logs full-screen` | 1-based label |
| create.sh | 44 | `# W8: debug-logs` | 1-based label |
| create.sh | 48 | `# W9: training-logs` | 1-based label |
| create.sh | 52 | `# W10: Terminal` | 1-based label |
| create.sh | 53 | `# W14: human-review` | reserved label (not tmux index 14) |
| create.sh | 58 | `# W11+: drill-N` | 1-based label range |
| create.sh | 397 | `# bottom-right of Window 1 middle row` | Window 1 = main |
| create.sh | 509 | `# Drill-N windows (W11+)` | 1-based label |
| create.sh | 953 | `# Window 3: Attention` | 1-based label |
| create.sh | 959 | `# Window 4: Git` | 1-based label |
| create.sh | 987 | `# Window 5: Metadata` | 1-based label |
| create.sh | 997 | `# Window 6: Metrics` | 1-based label |
| create.sh | 1023 | `# Window 7: Logs full-screen` | 1-based label |
| create.sh | 1029 | `# Window 8: debug-logs` | 1-based label |
| create.sh | 1049 | `# Window 9: training-logs` | 1-based label |
| create.sh | 1070 | `# Window 10: Terminal` | 1-based label |
| create.sh | 1085 | `# Window 14: human-review` | reserved label |
| create.sh | 1093 | `# (W11+) that follow` | 1-based label range |
| create.sh | 1100 | `# Windows W11+` | 1-based label range |
| attention.sh | 3 | `# Window 3 attention panel` | 1-based label (matches W3=attention) |

**Classification:** INDEX-BASED in documentation only (comment text, not executable).
**Needs update?** Yes — comment labels should be updated when operator window is
inserted, to maintain comment accuracy. These are cosmetic updates with no runtime
impact.

After insertion of `operator` at position 2 (new W2), the label assignments shift:

| Old label | Window name     | New label |
|-----------|-----------------|-----------|
| W1        | `main`          | W1 (unchanged) |
| —         | `operator` (new)| W2        |
| W2        | `visibility`    | W3        |
| W3        | `attention`     | W4        |
| W4        | `git`           | W5        |
| W5        | `metadata`      | W6        |
| W6        | `metrics`       | W7        |
| W7        | `logs`          | W8        |
| W8        | `debug-logs`    | W9        |
| W9        | `training-logs` | W10       |
| W10       | `terminal`      | W11       |
| W14       | `human-review`  | W12 (or reassigned) |
| W11+      | `drill-N`       | W13+      |

---

### H. `list-windows` call sites

| File | Line | Snippet | Classification |
|------|------|---------|----------------|
| project-toggle.sh | 84 | `tmux list-windows -t "$SESSION_NAME" -F "#{window_index}\t#{window_name}"` | NAME-BASED (result filtered by name) |

This is the only `list-windows` call. The result is piped through awk that
filters on `window_name` pattern `^drill-[0-9]+` and prints the name only.
The window_index is used as a sort key but the downstream `select-window` at
line 135 uses the name. Safe.

---

### I. Python test files — window index references

**Search result:** ZERO HITS.

```bash
grep -rn "window_index\|select.window\|new.window\|list.windows" team/tests/
```

No Python test files reference tmux window indices, select-window operations,
new-window counts, or list-windows parsing. The `DASHBOARD-LAYOUT-TESTS.md`
inventory mentions tests for `new-session + new-window call count` and
`split-window` operations, but `test_dashboard_layout.py` does not exist in the
current tree — it is listed in the test inventory document but has not been
committed yet.

The existing `team/tests/unit/test_dashboard_smoke_container.py` does not
reference window indices.

---

### J. `attention-routing` — numeric window reference scan

```bash
grep -rn "attention.*[0-9]\|[0-9].*attention" team/scripts/dashboard/
```

**Result:** ZERO runtime hits. The only match is `attention.sh:3`:

```bash
# Window 3 attention panel — shows BLOCKED tasks, quarantine alerts, ...
```

This is a comment, covered in section G above.
No attention routing script selects or targets the attention window by numeric
index. Routing to the attention window (if any) would use the name `attention`.

---

## Summary

### Index-based references (need update when operator window is inserted)

**NONE found in executable code.**

All tmux command targets in `team/scripts/dashboard/` and `team/scripts/lib/`
use window names (e.g., `${SESSION_NAME}:main`, `${SESSION_NAME}:attention`) or
pane-within-named-window format (`${SESSION_NAME}:main.0`). No script targets
a window by bare numeric index (`${SESSION_NAME}:0`, `${SESSION_NAME}:2`, etc.).

### Name-based references (safe — no update needed)

All `select-window`, `new-window`, `send-keys`, `resize-pane`, `split-window`,
`display-message`, and `list-windows` calls use window names. They are immune to
window ordering changes.

### Comment/documentation labels (update for accuracy, no runtime impact)

W-number labels in `create.sh` header and inline comments, and the
`"Window 3 attention panel"` header in `attention.sh`, use 1-based labels that
will become stale after the insertion. Updating these is a cosmetic task — no
script behavior changes.

### Zero-hit confirmation

```bash
# Confirm no direct numeric window targets exist:
grep -rn '"\${SESSION_NAME}:[0-9]"' team/scripts/dashboard/
# Result: ZERO HITS

grep -rn "\-t.*\${SESSION_NAME}:[0-9][^.]" team/scripts/dashboard/
# Result: ZERO HITS (only one hit was in a comment in show-status-window.sh)

grep -rn "move-window\|swap-window" team/scripts/
# Result: ZERO HITS
```

### Inserting `operator` at index 2 — implementation guidance

1. Add `tmux new-window` for `operator` **after** the `main` window construction
   block and **before** the `visibility` block. Since tmux appends new windows
   sequentially, the correct insertion requires explicit positioning:
   `tmux new-window -t "${SESSION_NAME}:1" -n "operator"`
   (inserting at position 1 pushes all later windows +1).
2. No existing `select-window`, `send-keys`, or `resize-pane` calls need to be
   changed — they all use names.
3. Update W-number comment labels in `create.sh` (lines listed in section G)
   and `attention.sh:3` for documentation accuracy.
4. Add `operator` to the window-order comment at line 69 of `create.sh`.
