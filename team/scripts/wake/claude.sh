#!/usr/bin/env bash
# team/scripts/wake/claude.sh
# Claude wake entry point: processes tasks from one agent's backlog queue in a
# loop. Designed for cron.
#
# Usage:
#   wake/claude.sh --agent=<agent> [--sleep=N] [--max-tasks=N] [--help]
#   wake/claude.sh -a <agent> [-s N] [-h]
#   wake/claude.sh <agent>    (deprecated positional form; emits a warning)
#
# A shim: the argument loop, the active-provider fast exit, HALT routing,
# the per-firing header log and the driver hand-off all live in
# lib/wake_entry.sh, whose header documents the options, stop conditions
# and tunables. This file names only what is Claude's: the provider library
# and the API-key variable an ai_auth_mode=oauth wake warns about.

# Strict mode from the first executable line: argument parsing, root
# resolution and the provider preflight in lib/wake_entry.sh all run under
# it.
set -euo pipefail

WAKE_ENTRY_PROVIDER="claude"
WAKE_ENTRY_PROVIDER_LIB="provider_claude.sh"
WAKE_ENTRY_API_KEY_VAR="ANTHROPIC_API_KEY"

# shellcheck source=../lib/wake_entry.sh
source "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/wake_entry.sh"
