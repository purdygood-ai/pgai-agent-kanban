# Dashboard Layout Tests

Index of the tests that assert on how the dashboard is laid out: pane
counts, pane widths and positions, the operator board's column set, and how
a board column truncates its items and cuts its rows. Each heading is a test
file under `team/`; each row names a test function defined in that file.

Run them with the targeted unit runner, from the repository root:

```bash
bash team/scripts/run-unit-tests.sh --files \
    tests/unit/test_dashboard_board_window_geometry.py \
    tests/unit/test_dashboard_operator_shell_init.py \
    tests/unit/test_dashboard_window_census.py \
    tests/unit/test_dashboard_create_sh_pane_counts_match_census.py \
    tests/unit/test_dashboard_column_render_module.py
```

`team/pyproject.toml` registers a `dashboard_layout` marker, but no test
carries it, so `pytest -m dashboard_layout` selects nothing. Use the file
list above.

The tmux tests start a private tmux server (`tmux -L <unique socket>`) with
a fixed-size detached session; the operator's own dashboard session is never
touched.

---

## Board windows: window 1 (`operator`) and window 2 (`visibility`)

### tests/unit/test_dashboard_board_window_geometry.py

Builds both board windows with the shared builder `create.sh` uses and reads
each pane's position and size back with `list-panes`.

| Test | Layout asserted |
|---|---|
| `test_operator_columns_and_legend_have_visibility_widths` | On the eight-column board, window 1's columns have exactly window 2's widths and positions, and both legends span the window |
| `test_operator_shell_row_sits_between_board_and_legend` | Without a workbench, window 1's shell is full width and 15% of the window height (at least 3 rows), directly under the board; the 1-row legend is the window's bottom row |
| `test_visibility_geometry_is_unchanged` | Window 2's panes match their pinned geometry at 160x48 and 237x60 |
| `test_shell_row_never_drops_below_its_minimum_rows` | A shell percentage that yields fewer than 3 rows still gets 3 |
| `test_operator_without_workbench_keeps_its_prior_geometry` | Window 1 built with no workbench arguments, or a workbench width of 0, matches its pinned no-workbench geometry |
| `test_workbench_column_spans_board_and_shell_beside_the_board` | At 220 columns with a 40% workbench, the workbench is the far-right pane and as tall as board and shell together; board and shell end at its divider; the legend stays full width |
| `test_narrow_window_builds_no_workbench_and_says_so_once` | At 120 columns, below a 160-column threshold, no workbench is built, board and shell take the full width, and one workbench notice names both widths |
| `test_columns_beside_workbench_keep_their_pct0_proportions` | Beside the workbench, each board column keeps its no-workbench share of the board region and is at least 15 columns wide |
| `test_merged_board_geometry` | The merged board (three input columns and AGENTS) matches its pinned geometry, its columns and dividers fill the board region, and no column is under 15 |
| `test_merged_board_with_workbench_has_seven_panes_in_place` | The merged board with a workbench is seven panes: four columns side by side, the shell at board width under them, the workbench at far right, the legend full width |
| `test_split_column_set_keeps_the_eight_column_geometry` | `split` without a workbench builds the pinned eight-column geometry |
| `test_split_column_set_with_workbench_keeps_its_geometry` | `split`, or no column set given, with a workbench builds the pinned eleven-pane geometry |
| `test_columns_keep_the_floor_beside_the_workbench_or_no_workbench_is_built` | At each tested width, either every column beside the workbench is at least 15 wide and the columns fill the board region, or no workbench is built and one notice names the width |
| `test_reflow_hook_parks_the_workbench_when_narrow_and_rejoins_it_when_wide` | Resizing to 120 columns moves the workbench to its own window and gives the board the no-workbench geometry; resizing back to 220 restores the built geometry |
| `test_split_board_keeps_the_floor_or_shares_the_width_evenly` | The split board without a workbench keeps every column at least 15 wide at 127 and 129 columns; at 120 and 126 the eight columns share the width evenly, one notice names the width and the 127 columns needed, and `@pgai_board_notice` holds it |
| `test_reflow_hook_reapplies_the_builder_widths_after_each_resize` | A board window built at 220 columns and resized to 183, 120 and 200 (operator with a workbench under both column sets, operator without one, visibility) has a fresh build's widths at each size, +/-1, and the degraded-layout notice exactly where that build records one |
| `test_legend_bar_shows_the_board_notice_until_a_reflow_clears_it` | The split operator board without a workbench, resized from 220 to 120 columns, has its legend bar show the recorded `@pgai_board_notice` line cut to the pane width; resized back to 220, the notice is cleared and the bar shows the legend again |

### tests/unit/test_dashboard_operator_shell_init.py

Runs `create.sh` itself in a sandbox and inspects the session it builds.

| Test | Layout asserted |
|---|---|
| `test_default_board_is_three_input_columns_and_one_agents_column` | With no `operator_board_agents` line, window 1's board is BUGS, PRIORITIES, REQUIREMENTS and AGENTS, followed by the shell, workbench and legend at the merged geometry; window 2 is untouched |
| `test_split_board_is_the_eleven_pane_window_running_window_2s_commands` | `operator_board_agents = split` builds eleven panes: window 2's eight columns and legend, with the shell and workbench at panes 8 and 9, at the eight-column geometry |
| `test_visibility_window_ignores_operator_board_agents` | Under either value, window 2 has nine panes (eight columns and the legend) at its pinned geometry |
| `test_cmdline_and_workbench_shells_are_initialised_and_cmdline_is_active` | At 220 columns with the default config, window 1 holds four board columns, the shell, a workbench 40% of the width, and the legend |
| `test_zero_workbench_pct_builds_no_workbench` | `operator_workbench_pct = 0` builds window 1 without a workbench |
| `test_both_board_windows_carry_the_reflow_hook_without_a_workbench` | With `operator_workbench_pct = 0`, window 1 and window 2 each carry their own layout hook running `board-reflow.sh` on that window, and the session's index-0 and index-1 hook entries remain |
| `test_narrow_window_builds_no_workbench_and_says_so` | A 120-column session under the default 160-column threshold builds no workbench, and `create.sh` prints one notice naming both widths |
| `test_narrow_client_gets_no_workbench_and_the_pct0_board` | A client attaching from a 120-column terminal sees window 1 without the workbench, at the no-workbench board geometry; the workbench returns when the terminal widens |
| `test_client_at_least_the_threshold_wide_keeps_the_workbench` | A 180-column client keeps the workbench, every pane at the size the builder gives at that width |
| `test_board_columns_carry_the_resolved_rows_per_column` | Every board column in windows 1 and 2 is started with the resolved row count: `DASHBOARD_ROWS_PER_COLUMN`, else `kanban.cfg` `rows_per_column`, else 21; a value that is not a positive whole number falls back to 21 |

---

## Pane counts for every window

### tests/unit/test_dashboard_window_census.py

Checks the window census, `team/config/dashboard_windows.census`, which
declares every window's panes.

| Test | Layout asserted |
|---|---|
| `test_every_window_pane_count_matches_declared_panes` | Each census window's declared pane count equals the number of pane records it lists |
| `test_checked_in_census_includes_both_operator_variants` | Window 1 is declared once per `operator_board_agents` value: merged (4 board columns) and split (8), each with the shell, the workbench and the legend |
| `test_merged_operator_window_is_three_inputs_and_one_agents_column` | The merged variant is window 2's three input columns, one AGENTS column, the shell, the workbench and the legend, and no other pane |
| `test_operator_window_is_the_visibility_board_plus_a_shell_row` | The split variant is window 2's eight columns and legend in the same order, plus one shell pane and one workbench pane |
| `test_checked_in_census_includes_both_drill_variants` | The drill window is declared in a release and a document variant, at one window index, the last in creation order |

### tests/unit/test_dashboard_create_sh_pane_counts_match_census.py

| Test | Layout asserted |
|---|---|
| `test_create_sh_builds_the_pane_count_the_census_declares_for_every_window` | For every window `create.sh` builds, its `split-window` calls produce exactly the pane count the census declares, and every census window is built |

---

## Column content: width and rows

### tests/unit/test_dashboard_column_render_module.py

| Test | Layout asserted |
|---|---|
| `test_truncate_keeps_short_text_and_cuts_long_text_to_width` | Text that fits is unchanged; longer text is cut to the column width, ending in `…` |
| `test_truncate_ignores_escapes_for_width_and_resets_before_the_ellipsis` | Colour escapes do not count toward the width, and the colour is reset before the `…` |
| `test_input_column_cuts_to_max_rows_with_a_more_row` | An input column with more items than its row limit shows the newest items and a final `... and N more` row |
| `test_queue_column_cuts_to_max_rows_with_a_more_row` | A queue column with more tasks than its row limit ends in a `... and N more` row |
| `test_compute_layout_shrinks_blocks_to_fit_the_column` | As the number of projects grows, a multi-project column gives each project fewer rows, down to a floor, so the column stays within its row limit |
| `test_multi_column_marker_survives_truncation_of_a_narrow_pane` | In a 20-column multi-project pane, the item text is truncated but its `[carried]` marker is kept, and no line is wider than the pane |
