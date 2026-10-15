#!/usr/bin/env bash
# team/scripts/lib/cold_lane_pytest_args.sh
# The pytest arguments the cold-build lane (run-cold-build-tests.sh) runs
# its suite with: the one definition the lane and the tests of its output
# shape both read, so neither can drift from the other.
#
# Source this file to get COLD_LANE_PYTEST_ARGS, a bash array:
#   source "$(dirname "${BASH_SOURCE[0]}")/cold_lane_pytest_args.sh"
#   python3 -m pytest "${COLD_LANE_PYTEST_ARGS[@]}"
#
#   tests/cold/    every module under team/tests/cold/, relative to team/
#                  (the directory the lane runs pytest from). A new module
#                  dropped into tests/cold/ joins the lane with no edit here.
#   --capture=no   test stdout reaches the lane output whatever the test's
#                  outcome. The deep-verify classifier
#                  (pgai_agent_kanban.deep_verify.classify) reads two lines
#                  that skipped and passing tests print: the
#                  "[cold-build] SKIP — <reason>" narration of a pair skipped
#                  for an unavailable engine or registry, and the
#                  "[cold-build] [<engine>] [<flavor>] Image built
#                  successfully: <tag>" line of a pair that built. pytest's
#                  default capture shows a test's stdout only when it fails,
#                  so without this an engine-less run carries neither line
#                  and cannot be told apart from a built one.
#
# The lane appends --verbose when it is asked to; nothing else is added.
# Sourcing this file defines the array and does nothing else.

# shellcheck disable=SC2034  # read by the sourcing script
COLD_LANE_PYTEST_ARGS=(
  "tests/cold/"
  "--capture=no"
)
