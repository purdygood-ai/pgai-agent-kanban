#!/usr/bin/env bash
# team/scripts/lib/safe_overwrite.sh
# Reusable prompt-and-backup primitives for install.sh and any other
# operator-state-touching scripts in the pgai-agent-kanban framework.
#
# Source this file to get the safe_overwrite_* and related functions:
#   source "$(dirname "${BASH_SOURCE[0]}")/safe_overwrite.sh"
#
# Design principles
# -----------------
# 1. NEVER silently destructive — every modification to operator state requires
#    explicit confirmation, except for idempotent identical-content writes which
#    are silent no-ops (cmp -s guard).
# 2. Backup BEFORE modification — the original is preserved before anything
#    is written, so a mid-flight failure leaves the operator's original intact.
# 3. Conservative defaults — overwrite prompts default to N (safe); install
#    prompts default to Y (convenient for fresh installs).
# 4. Recovery instructions on decline — when an operator says "no," they are
#    shown the manual command to perform the action themselves later.
# 5. No top-level side effects when sourced — this file defines functions only.
#
# Backup naming convention
# ------------------------
# All backup paths use $HOME/.<basename>.before-install-YYYYMMDD-HHMMSS.bak
# where <basename> is derived from the target path's basename.
# This keeps backups in a predictable, easily-findable location:
#   ls $HOME/.crontab*          # find crontab backups
#   ls $HOME/.CODER.md*         # find role file backups
#
# Crontab command seam
# --------------------
# All invocations of the crontab binary go through the single _run_crontab
# function defined below.  Tests and callers can redirect crontab writes by
# exporting PGAI_CRONTAB_CMD before sourcing this file:
#   PGAI_CRONTAB_CMD=/path/to/fake-crontab source safe_overwrite.sh
# When PGAI_CRONTAB_CMD is unset or empty, the real 'crontab' binary is used.
#
# Functions
# ---------
#   safe_overwrite_file <source> <target>
#     Install a file with cmp-skip-identical, prompt on overwrite (default N),
#     backup-then-copy on confirmation, and recovery instructions on decline.
#     On first install (target does not exist): prompts with default Y.
#     Returns 0 on success (installed, updated, or skipped).
#     Returns 1 on backup failure.
#     Returns 2 when operator declines and recovery instructions are printed.
#     Stdout carries status messages; stderr carries errors.
#
#   backup_current_crontab <backup_path>
#     Write the current user crontab to <backup_path>.
#     Returns 0 on success.
#     Returns 1 if crontab -l fails or if the write to backup_path fails.
#     Returns 2 if no crontab is currently set (nothing to back up).
#     Stdout carries the status message; stderr carries errors.
#
#   safe_overwrite_crontab <new_template> [force_overwrite]
#     Handles all three crontab states with prompts and backup:
#       (a) No existing crontab   — install prompt, default Y.
#       (b) PGAI entries present  — upgrade prompt, default N (conservative);
#             if force_overwrite="true", skips prompt and applies unconditionally.
#       (c) Non-PGAI entries only — replace prompt, default N (conservative);
#             if force_overwrite="true", skips prompt and applies unconditionally.
#     Backs up the existing crontab before any modification.
#     On decline, prints recovery instructions.
#
#     No-TTY / non-interactive confirm gate:
#       When there is no controlling TTY AND the caller has not set
#       PGAI_CRONTAB_CONFIRM=1, safe_overwrite_crontab declines immediately
#       (returns 2) without invoking the crontab binary.  This prevents
#       non-interactive runs (CI, cron, automated agents) from silently
#       overwriting the operator's live crontab via the "default Y" path.
#       Callers that genuinely need non-interactive installs must export
#       PGAI_CRONTAB_CONFIRM=1 before calling this function.
#
#     Returns 0 on success (installed, updated, or declined-cleanly).
#     Returns 1 on backup failure (aborts; does NOT proceed with install).
#     Returns 2 when operator declines (recovery instructions printed).
#     Stdout carries status and prompt output; stderr carries errors.
#
#   warn_if_empty_crontab
#     Post-install sanity check.  If crontab is currently empty (or absent),
#     prints a loud WARNING with recovery instructions and returns 1.
#     Returns 0 if crontab is non-empty.
#     Intended to be called at the end of install.sh after crontab handling.

# ---------------------------------------------------------------------------
# _safe_overwrite_backup_path <target>
# Internal helper: derive the $HOME/.<basename>.before-install-YYYYMMDD-HHMMSS.bak
# path for a given target file.
# Usage: local bak; bak="$(_safe_overwrite_backup_path "$target")"
# ---------------------------------------------------------------------------
_safe_overwrite_backup_path() {
    local target="$1"
    local basename
    basename="$(basename "$target")"
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    printf '%s/.%s.before-install-%s.bak' "$HOME" "$basename" "$ts"
}

# ---------------------------------------------------------------------------
# _run_crontab [args...]
# Single chokepoint for all crontab binary invocations in this file.
#
# Honors PGAI_CRONTAB_CMD: when set and non-empty, that command is used
# instead of the real 'crontab' binary.  Tests redirect crontab writes by
# exporting PGAI_CRONTAB_CMD=/path/to/fake before sourcing this library.
# When PGAI_CRONTAB_CMD is unset or empty, the real 'crontab' binary is used.
#
# Usage: _run_crontab [args...]          # e.g. _run_crontab "$template_file"
#        _run_crontab -l                 # read current crontab
# ---------------------------------------------------------------------------
_run_crontab() {
    "${PGAI_CRONTAB_CMD:-crontab}" "$@"
}

# ---------------------------------------------------------------------------
# _prompt_tty <prompt_text> <varname>
# Display a prompt and read a response, bypassing any stdout/stdin redirection
# of the calling script.
#
# Why: when install.sh is invoked as 'bash install.sh 2>&1 | tee log', stdout
# (where 'printf prompt' writes) is captured by tee, and stdin is the pipe
# (which has no terminal). Operators see no prompt and 'read' gets EOF — the
# prompt's default behavior (often Y for install) silently triggers.
#
# Strategy:
#   - If /dev/tty is readable AND writable: write the prompt there and read
#     from there. This bypasses any redirection of stdout/stdin.
#   - Otherwise (no controlling terminal — CI, cron, automated runs): print
#     the prompt to stderr (which is normally unredirected), then read from
#     stdin with a short timeout. If the read times out (true non-interactive
#     run), the variable is set to the empty string and the caller's
#     case "${response:-DEFAULT}" handling picks up the safe default.
#
# Usage:
#   local response
#   _prompt_tty 'Install foo? [Y/n] ' response
#   case "${response:-Y}" in [yY]*) ... ;; esac
# ---------------------------------------------------------------------------
_prompt_tty() {
    local prompt_text="$1"
    local __varname="$2"
    local __value=""

    # Guard: attempt to open /dev/tty for read+write.  The simple -r/-w tests
    # can return true even when /dev/tty reports "No such device or address" on
    # open (e.g. subshell started by a non-interactive SSH session, background
    # cron job, or pipe-wrapped invocation with no controlling terminal).
    # Using exec 3<>/dev/tty gives a definitive yes/no that respects set -e
    # callers, and we can close fd 3 immediately after the test.
    local _tty_ok=false
    if exec 3<>/dev/tty 2>/dev/null; then
        exec 3>&-
        _tty_ok=true
    fi

    if [[ "$_tty_ok" == "true" ]]; then
        printf '%s' "$prompt_text" > /dev/tty
        IFS= read -r __value < /dev/tty || __value=""
    else
        # No controlling tty — degrade gracefully: prompt via stderr (which
        # the operator usually still sees even under '... | tee LOG'), then
        # read from stdin ONLY if data is already available (readiness gate).
        #
        # `read -r -t 0 <&0` is a non-consuming data-ready test — it returns 0
        # immediately if bytes are waiting, non-zero otherwise, and never
        # consumes any input. Only when data is already present do we issue the
        # real read; otherwise we return empty immediately and let the caller
        # apply its default. A piped affirmative answer (e.g. `printf 'y\n' | ...`)
        # still arrives in time because the pipe is filled before the read test runs.
        printf '%s' "$prompt_text" >&2
        # shellcheck disable=SC2034
        if IFS= read -r -t 0 <&0 2>/dev/null; then
            # Stdin has data ready — consume it with a short timeout as safety net.
            IFS= read -r -t 2 __value || __value=""
        else
            # Stdin is open but idle — return empty immediately (sub-second).
            __value=""
        fi
    fi

    # Assign back to the caller's variable by name.
    printf -v "$__varname" '%s' "$__value"
}

# ---------------------------------------------------------------------------
# safe_overwrite_file <source> <target>
#
# Installs <source> to <target> with full operator-safety guards:
#
#   1. If target does not exist:
#        - Prompt "Install <target>? [Y/n]" (default Y for first-time install).
#        - On yes: copy source to target, print confirmation.
#        - On no:  print recovery instruction, return 2.
#
#   2. If target exists and is identical to source (cmp -s):
#        - Silent no-op.  No prompt.  No output.  Return 0.
#
#   3. If target exists and differs from source:
#        - Show sizes and modification date of existing target.
#        - Prompt "Overwrite <target>? [y/N]" (default N — conservative).
#        - On yes:
#            a. Compute backup path ($HOME/.<basename>.before-install-<ts>.bak).
#            b. Copy target → backup.  On copy failure: print error, return 1.
#            c. Copy source → target.  Print "Updated: <target> (backup: <bak>)".
#        - On no:  print recovery instruction ("cp <source> <target>"), return 2.
#
# Arguments:
#   $1  source  — path to the new/replacement file
#   $2  target  — path to the file being managed (may or may not exist)
#
# Return codes:
#   0  success (installed, updated, or skipped — identical or declined cleanly)
#   1  backup write failure (does NOT proceed with install when this happens)
#   2  operator declined (recovery instructions printed to stdout)
# ---------------------------------------------------------------------------
safe_overwrite_file() {
    local source="$1"
    local target="$2"

    if [[ -z "$source" || -z "$target" ]]; then
        printf 'safe_overwrite.sh: safe_overwrite_file requires two arguments: <source> <target>\n' >&2
        return 1
    fi

    if [[ ! -r "$source" ]]; then
        printf 'safe_overwrite.sh: source file not readable: %s\n' "$source" >&2
        return 1
    fi

    # ---- Case 1: target does not exist (first-time install) ----------------
    if [[ ! -e "$target" ]]; then
        local response
        _prompt_tty "$(printf 'Install %s? [Y/n] ' "$target")" response
        case "${response:-Y}" in
            [yY]*)
                # Ensure parent directory exists.
                local target_dir
                target_dir="$(dirname "$target")"
                if [[ ! -d "$target_dir" ]]; then
                    printf 'safe_overwrite.sh: target directory does not exist: %s\n' "$target_dir" >&2
                    return 1
                fi
                cp "$source" "$target" || { printf 'safe_overwrite.sh: copy failed: %s -> %s\n' "$source" "$target" >&2; return 1; }
                printf 'Installed: %s\n' "$target"
                return 0
                ;;
            *)
                printf 'Skipped: %s\n' "$target"
                printf 'To install manually:\n'
                printf '  cp %s %s\n' "$source" "$target"
                return 2
                ;;
        esac
    fi

    # ---- Case 2: target exists and is identical to source ------------------
    if cmp -s "$source" "$target"; then
        # Silent no-op: identical content, nothing to do.
        return 0
    fi

    # ---- Case 3: target exists and differs from source ---------------------
    local existing_info
    existing_info="$(stat -c '%s bytes, modified %y' "$target" 2>/dev/null || printf 'unknown size/mtime')"
    local new_size
    new_size="$(stat -c '%s' "$source" 2>/dev/null || printf 'unknown') bytes"

    printf '\n'
    printf 'File differs: %s\n' "$target"
    printf '  Existing: %s\n' "$existing_info"
    printf '  New:      %s (from %s)\n' "$new_size" "$source"

    local bak
    bak="$(_safe_overwrite_backup_path "$target")"
    printf '  Backup would be saved to: %s\n' "$bak"
    printf '\n'

    local response
    _prompt_tty 'Overwrite? [y/N] ' response
    case "$response" in
        [yY]*)
            # Backup first.
            cp "$target" "$bak" || {
                printf 'safe_overwrite.sh: backup failed: %s -> %s\n' "$target" "$bak" >&2
                return 1
            }
            # Now overwrite.
            cp "$source" "$target" || {
                printf 'safe_overwrite.sh: copy failed after backup: %s -> %s\n' "$source" "$target" >&2
                printf '  Original is preserved at: %s\n' "$bak"
                return 1
            }
            printf 'Updated: %s (backup: %s)\n' "$target" "$bak"
            return 0
            ;;
        *)
            printf 'Skipped: %s\n' "$target"
            printf 'To update manually:\n'
            printf '  cp %s %s\n' "$source" "$target"
            return 2
            ;;
    esac
}

# ---------------------------------------------------------------------------
# backup_current_crontab <backup_path>
#
# Write the current user crontab to <backup_path>.
#
# Arguments:
#   $1  backup_path  — destination file for the crontab backup
#
# Return codes:
#   0  success (crontab written to backup_path)
#   1  failure (crontab -l error, or write to backup_path failed)
#   2  no crontab currently set (nothing to back up; backup_path not written)
#
# Notes:
#   - The caller is responsible for deriving the backup_path (e.g., using
#     _safe_overwrite_backup_path or a hardcoded $HOME/.crontab.before-install-<ts>.bak).
#   - A return code of 2 is NOT an error — it means the system has no crontab
#     yet and there is nothing to preserve.
# ---------------------------------------------------------------------------
backup_current_crontab() {
    local backup_path="$1"

    if [[ -z "$backup_path" ]]; then
        printf 'safe_overwrite.sh: backup_current_crontab requires a <backup_path> argument\n' >&2
        return 1
    fi

    local current_crontab
    current_crontab="$(_run_crontab -l 2>/dev/null)"
    local crontab_status=$?

    # crontab -l exits non-zero when there is no crontab at all (and prints
    # "no crontab for <user>" to stderr).  Distinguish that from a real error.
    if [[ $crontab_status -ne 0 ]]; then
        # No crontab for this user.
        return 2
    fi

    if [[ -z "$current_crontab" ]]; then
        # crontab -l succeeded but returned empty content (unusual but possible).
        return 2
    fi

    # Write the crontab content to the backup file.
    printf '%s\n' "$current_crontab" > "$backup_path" 2>/dev/null || {
        printf 'safe_overwrite.sh: failed to write crontab backup to: %s\n' "$backup_path" >&2
        return 1
    }

    printf 'Crontab backed up to: %s\n' "$backup_path"
    return 0
}

# ---------------------------------------------------------------------------
# safe_overwrite_crontab <new_template> [force_overwrite]
#
# Handle all three crontab states with prompts, backup, and recovery instructions:
#
#   (a) No existing crontab (or empty):
#         - "No existing crontab found."
#         - "Install PGAI Kanban crontab now? [Y/n]"  (default Y)
#         - On yes: crontab "$new_template"
#         - On no:  print "  crontab $new_template", return 2.
#
#   (b) Existing crontab with PGAI entries (wake-batch.sh lines):
#         - Show matching PGAI lines (up to 10).
#         - Derive backup path; announce it.
#         - If force_overwrite=true: skip prompt, proceed with backup+replace.
#         - Otherwise: "Replace with new template? [y/N]"  (default N — conservative)
#         - On yes / force: backup, then crontab "$new_template".
#         - On no:  print multi-step manual command, return 2.
#
#   (c) Existing crontab with no PGAI entries:
#         - Show first 5 lines of existing crontab.
#         - Warn that replacing will discard non-PGAI entries.
#         - Derive backup path; announce it.
#         - If force_overwrite=true: skip prompt, proceed with backup+replace.
#         - Otherwise: "Replace? (will REPLACE non-PGAI entries) [y/N]"  (default N)
#         - On yes / force: backup, then crontab "$new_template".
#         - On no:  print manual merge instructions, return 2.
#
# Arguments:
#   $1  new_template    — path to the new crontab template file to install
#   $2  force_overwrite — optional; set to "true" to skip interactive prompts in
#                         cases (b) and (c) and apply the new template unconditionally
#                         (after backup).  Intended for --tier CLI invocations where
#                         the operator has explicitly selected a tier and expects it
#                         to be applied regardless of what the existing crontab contains.
#
# Return codes:
#   0  success (crontab installed, updated, or operator chose to skip cleanly)
#   1  backup failure (aborts — does NOT install the new crontab)
#   2  operator declined (recovery instructions printed)
# ---------------------------------------------------------------------------
safe_overwrite_crontab() {
    # Confirm-gate mechanism:
    #   When no TTY is available AND PGAI_CRONTAB_CONFIRM is not set to "1",
    #   this function declines immediately (returns 2) rather than silently
    #   defaulting to Y on the no-TTY timeout path.  Callers that need
    #   non-interactive installs must export PGAI_CRONTAB_CONFIRM=1 first.
    #   Interactive (TTY-attached) runs are not affected by this gate.
    local new_template="$1"
    local force_overwrite="${2:-false}"

    if [[ -z "$new_template" ]]; then
        printf 'safe_overwrite.sh: safe_overwrite_crontab requires a <new_template> argument\n' >&2
        return 1
    fi

    if [[ ! -r "$new_template" ]]; then
        printf 'safe_overwrite.sh: crontab template not readable: %s\n' "$new_template" >&2
        return 1
    fi

    # No-TTY explicit-confirm gate.
    # Check whether we have a controlling terminal using the same exec 3<>/dev/tty
    # test that _prompt_tty uses.  If there is no TTY AND the caller did not
    # export PGAI_CRONTAB_CONFIRM=1, decline immediately to avoid silent overwrite.
    local _soc_tty_ok=false
    if exec 3<>/dev/tty 2>/dev/null; then
        exec 3>&-
        _soc_tty_ok=true
    fi
    if [[ "$_soc_tty_ok" == "false" && "${PGAI_CRONTAB_CONFIRM:-}" != "1" ]]; then
        printf 'safe_overwrite.sh: no TTY detected and PGAI_CRONTAB_CONFIRM is not set.\n' >&2
        printf 'Non-interactive crontab installs require the caller to export PGAI_CRONTAB_CONFIRM=1.\n' >&2
        printf 'Declining to modify crontab to prevent silent overwrite.\n' >&2
        return 2
    fi

    local current_crontab
    current_crontab="$(_run_crontab -l 2>/dev/null)"

    # ---- Case (a): No existing crontab ------------------------------------
    if [[ -z "$current_crontab" ]]; then
        printf 'No existing crontab found.\n'
        local response
        _prompt_tty 'Install PGAI Kanban crontab now? [Y/n] ' response
        case "${response:-Y}" in
            [yY]*)
                _run_crontab "$new_template" || {
                    printf 'safe_overwrite.sh: crontab install failed\n' >&2
                    return 1
                }
                printf 'Crontab installed.\n'
                return 0
                ;;
            *)
                printf 'Crontab not installed.\n'
                printf 'To install manually:\n'
                printf '  crontab %s\n' "$new_template"
                return 2
                ;;
        esac
    fi

    # Derive backup path for cases (b) and (c).
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    local backup_file="${HOME}/.crontab.before-install-${ts}.bak"

    # ---- Case (b): Existing crontab has PGAI entries ----------------------
    # Detection: wake-batch.sh (provider-agnostic dispatcher, current layout).
    if printf '%s\n' "$current_crontab" | grep -qE 'wake-batch\.sh'; then
        printf 'Existing crontab has PGAI Kanban entries:\n'
        printf '%s\n' "$current_crontab" | grep -E 'wake-batch\.sh' | head -10
        printf '...\n'
        printf '\n'
        printf 'Updating will back up current crontab to: %s\n' "$backup_file"

        local do_replace
        if [[ "$force_overwrite" == "true" ]]; then
            printf 'Tier override requested — applying new template (force_overwrite=true).\n'
            do_replace="yes"
        else
            local response
            _prompt_tty 'Replace with new template? [y/N] ' response
            do_replace="$response"
        fi

        case "${do_replace:-no}" in
            [yY]*|yes)
                # Backup first.
                backup_current_crontab "$backup_file" || {
                    printf 'safe_overwrite.sh: aborting — backup failed before crontab modification\n' >&2
                    return 1
                }
                _run_crontab "$new_template" || {
                    printf 'safe_overwrite.sh: crontab install failed; original preserved at: %s\n' "$backup_file" >&2
                    return 1
                }
                printf 'Crontab updated. Backup: %s\n' "$backup_file"
                return 0
                ;;
            *)
                printf 'Crontab unchanged.\n'
                printf 'To update manually:\n'
                printf '  cp %s /tmp/new-crontab \\\n' "$new_template"
                printf '  crontab -l > %s \\\n' "$backup_file"
                printf '  crontab /tmp/new-crontab\n'
                return 2
                ;;
        esac
    fi

    # ---- Case (c): Existing crontab with no PGAI entries ------------------
    printf 'Existing crontab found (no PGAI entries detected):\n'
    printf '%s\n' "$current_crontab" | head -5
    printf '...\n'
    printf '\n'
    printf 'REPLACING this crontab will discard existing entries.\n'
    printf 'Existing entries will be backed up to: %s\n' "$backup_file"

    local do_replace
    if [[ "$force_overwrite" == "true" ]]; then
        printf 'Tier override requested — applying new template (force_overwrite=true).\n'
        do_replace="yes"
    else
        local response
        _prompt_tty 'Replace? (will REPLACE non-PGAI entries) [y/N] ' response
        do_replace="$response"
    fi

    case "${do_replace:-no}" in
        [yY]*|yes)
            # Backup first.
            backup_current_crontab "$backup_file" || {
                printf 'safe_overwrite.sh: aborting — backup failed before crontab modification\n' >&2
                return 1
            }
            _run_crontab "$new_template" || {
                printf 'safe_overwrite.sh: crontab install failed; original preserved at: %s\n' "$backup_file" >&2
                return 1
            }
            printf 'Crontab installed. Previous crontab backed up: %s\n' "$backup_file"
            return 0
            ;;
        *)
            printf 'Crontab unchanged. You will need to manually merge entries.\n'
            printf 'Template: %s\n' "$new_template"
            printf 'To merge manually:\n'
            printf '  crontab -l > %s\n' "$backup_file"
            printf '  # Edit %s to add PGAI entries from: %s\n' "$backup_file" "$new_template"
            printf '  crontab %s\n' "$backup_file"
            return 2
            ;;
    esac
}

# ---------------------------------------------------------------------------
# warn_if_empty_crontab
#
# Post-install sanity check.  If crontab is currently empty (or absent),
# print a loud WARNING with recovery instructions.
#
# Intended to be called at the END of install.sh after all crontab handling
# has completed.  An empty crontab after a successful install is a red flag
# indicating that wake scripts will not fire.
#
# Arguments: none
#
# Return codes:
#   0  crontab is non-empty — no warning printed
#   1  crontab is empty or absent — warning printed with recovery instructions
# ---------------------------------------------------------------------------
warn_if_empty_crontab() {
    local current_crontab
    current_crontab="$(_run_crontab -l 2>/dev/null)"

    if [[ -z "$current_crontab" ]]; then
        printf '\n'
        printf '!!! WARNING: crontab is empty after install !!!\n'
        printf 'Wake scripts will NOT fire. The autonomous chain is halted.\n'
        printf '\n'
        printf 'This may indicate the crontab install step was declined or failed.\n'
        printf 'To recover, install the crontab manually:\n'
        printf '\n'
        printf '  crontab "${PGAI_AGENT_KANBAN_ROOT_PATH}/templates/install/crontab.example"\n'
        printf '\n'
        printf 'Or restore from a backup (look in %s for .crontab.before-install-*.bak files):\n' "$HOME"
        printf '\n'
        printf '  ls %s/.crontab.before-install-*.bak\n' "$HOME"
        printf '  crontab %s/.crontab.before-install-<timestamp>.bak\n' "$HOME"
        printf '\n'
        return 1
    fi

    return 0
}
