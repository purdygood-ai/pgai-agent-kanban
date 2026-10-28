#!/usr/bin/env bash
# git_remote_default.sh — the default git remote name, defined once for bash.
#
# PP_GIT_REMOTE_NAME_DEFAULT is the remote a project's release machinery
# talks to when [project] git_remote_name is absent or blank, or the project
# has no config file. project_paths.sh (pp_git_remote_name) and
# dashboard/lib/version.sh both read it from here, so version.sh resolves the
# default without loading project_paths.sh. The Python definition is
# pgai_agent_kanban.lib.git_remote.DEFAULT_REMOTE_NAME;
# tests/unit/test_git_remote_name_callers.py holds the two equal.
#
# No functions and no side effects beyond the one assignment, so sourcing it
# more than once is harmless.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/git_remote_default.sh"           # from scripts/lib/
#   source "$(dirname "${BASH_SOURCE[0]}")/../../lib/git_remote_default.sh" # from scripts/dashboard/lib/

PP_GIT_REMOTE_NAME_DEFAULT="origin"
