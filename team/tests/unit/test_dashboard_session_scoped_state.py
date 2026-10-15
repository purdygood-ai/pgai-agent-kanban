"""
test_dashboard_session_scoped_state.py
======================================
One dashboard session's create and kill leave every other tmux session --
and every other dashboard -- as it was.

  (A) kill.sh --session A removes A's armed-pane folder and the loop-registry
      entries of A's own panes, and leaves dashboard B's armed folder, loop
      entries and the shared snapshots in place while B is still running.
      Once no dashboard session remains, the shared dashboard folder goes
      and tmux's own prefix+p (previous-window) is restored.
  (B) create.sh's session options are set on its session only: no
      set-option -g for the status options, and terminal-overrides -- a
      server option -- is appended only when missing, so two creates add
      each entry once. Its prefix+p binding is conditional on the session's
      @pgai_dashboard marker, so other sessions keep previous-window.

tmux is a recording bash stub on PATH with just enough state (the
terminal-overrides list, the live sessions) to answer the queries the two
scripts make; no tmux server is involved. Temp state lives in a fixture
temp root under tmp_path.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_CREATE_SH = _TEAM_DIR / "scripts" / "dashboard" / "create.sh"
_KILL_SH = _TEAM_DIR / "scripts" / "dashboard" / "kill.sh"

_TMUX_STUB = textwrap.dedent("""\
    #!/bin/bash
    # Recording tmux stub. State lives beside it:
    #   calls.log         every invocation, one line each
    #   overrides.txt     the server's terminal-overrides entries
    #   sessions.txt      "<name>\\t<@pgai_dashboard>" per live session
    #   panes-<session>   that session's pane ids
    here="$(dirname "$0")"
    printf '%s\\n' "$*" >> "$here/calls.log"
    case "$1" in
      has-session)
        grep -q "^${3#=}	" "$here/sessions.txt" 2>/dev/null; exit $? ;;
      list-panes)
        session=""; prev=""
        for arg; do [[ "$prev" == "-t" ]] && session="$arg"; prev="$arg"; done
        cat "$here/panes-${session}" 2>/dev/null; exit 0 ;;
      kill-session)
        sed -i "/^${3}	/d" "$here/sessions.txt"; exit 0 ;;
      list-sessions)
        cat "$here/sessions.txt" 2>/dev/null; exit 0 ;;
      show-options)
        [[ "$*" == *"-sv terminal-overrides"* ]] && cat "$here/overrides.txt" 2>/dev/null; exit 0 ;;
      set-option)
        if [[ "$2" == "-sa" && "$3" == "terminal-overrides" ]]; then
          printf '%s\\n' "${4#,}" >> "$here/overrides.txt"
        fi
        exit 0 ;;
    esac
    exit 0
""")


def _install_stub(tmp_path: pathlib.Path) -> pathlib.Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "tmux"
    stub.write_text(_TMUX_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return bin_dir


def _env(tmp_path: pathlib.Path, root: pathlib.Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PGAI_TASKS_DIR", "PGAI_PROJECT_ROOT", "TMUX", "TMUX_PANE", "PGAI_DASHBOARD_SESSION_NAME")}
    env.update(
        PATH=f"{tmp_path / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}",
        KANBAN_ROOT=str(root),
        PGAI_AGENT_KANBAN_ROOT_PATH=str(root),
        PGAI_AGENT_KANBAN_TEMP_DIR=str(tmp_path / "temp"),
    )
    return env


def _calls(bin_dir: pathlib.Path) -> list[str]:
    log = bin_dir / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# ---------------------------------------------------------------------------
# (A) kill.sh
# ---------------------------------------------------------------------------


def _two_dashboards(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    root = tmp_path / "root"
    root.mkdir()
    bin_dir = _install_stub(tmp_path)
    (bin_dir / "sessions.txt").write_text("dash-a\t1\ndash-b\t1\n", encoding="utf-8")
    (bin_dir / "panes-dash-a").write_text("%1\n%2\n", encoding="utf-8")
    (bin_dir / "panes-dash-b").write_text("%3\n", encoding="utf-8")
    dash = tmp_path / "temp" / "dashboard"
    for session, labels in (("dash-a", ["main.0", "main.1"]), ("dash-b", ["main.0"])):
        armed = dash / "armed" / session
        armed.mkdir(parents=True)
        for label in labels:
            (armed / label).touch()
    (dash / "loops").mkdir()
    for pane in ("%1", "%2", "%3"):
        (dash / "loops" / pane).write_text("4242\n", encoding="utf-8")
    (dash / "snapshots").mkdir()
    (dash / "snapshots" / "board.snap").write_text("x\n", encoding="utf-8")
    return root, bin_dir, dash


def _kill(tmp_path: pathlib.Path, root: pathlib.Path, session: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_KILL_SH), "--kanban-root", str(root), "--session", session],
        capture_output=True, text=True, env=_env(tmp_path, root), timeout=60,
    )


def test_killing_one_dashboard_leaves_the_other_dashboards_state(tmp_path: pathlib.Path) -> None:
    root, bin_dir, dash = _two_dashboards(tmp_path)

    result = _kill(tmp_path, root, "dash-a")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (dash / "armed" / "dash-a").exists(), "the killed session's armed folder is removed"
    assert not (dash / "loops" / "%1").exists() and not (dash / "loops" / "%2").exists()
    assert (dash / "armed" / "dash-b" / "main.0").exists(), "B's armed folder must survive A's kill"
    assert (dash / "loops" / "%3").exists(), "B's loop entry must survive A's kill"
    assert (dash / "snapshots" / "board.snap").exists(), "shared state stays while a dashboard remains"
    assert not any(call.startswith("bind-key") for call in _calls(bin_dir)), _calls(bin_dir)


def test_killing_the_last_dashboard_removes_the_shared_folder_and_restores_prefix_p(tmp_path: pathlib.Path) -> None:
    root, bin_dir, dash = _two_dashboards(tmp_path)
    assert _kill(tmp_path, root, "dash-a").returncode == 0

    result = _kill(tmp_path, root, "dash-b")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not dash.exists(), "with no dashboard left the shared dashboard folder goes"
    assert "bind-key -T prefix p previous-window" in _calls(bin_dir), _calls(bin_dir)


# ---------------------------------------------------------------------------
# (B) create.sh session options
# ---------------------------------------------------------------------------


def _extract(names: list[str]) -> str:
    alternation = "|".join(re.escape(name) for name in names)
    return textwrap.dedent(f"""\
        eval "$(awk '
            /^({alternation})\\(\\)/ {{ found=1; depth=0 }}
            found {{
                print
                n = split($0, a, "")
                for (i = 1; i <= n; i++) {{
                    if (a[i] == "{{") depth++
                    if (a[i] == "}}") {{ depth--; if (depth == 0) found=0 }}
                }}
            }}
        ' '{_CREATE_SH}')"
    """)


def test_session_options_are_session_scoped_and_overrides_are_added_once(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    bin_dir = _install_stub(tmp_path)
    script = (
        "set -euo pipefail\n"
        "DASHBOARD_TMUX_ACTIVE_WINDOW_STYLE='#[fg=white,bold]'\n"
        + _extract(["dashboard_apply_session_options", "dashboard_apply_window_status_style"])
        + "dashboard_apply_session_options dash-a 5\n"
        + "dashboard_apply_window_status_style dash-a\n"
        + "dashboard_apply_session_options dash-a 5\n"
    )
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env=_env(tmp_path, root), timeout=60)
    assert result.returncode == 0, result.stderr

    calls = [call for call in _calls(bin_dir) if call.startswith("set-option")]
    assert calls, "the option functions must set options"
    global_sets = [call for call in calls if re.search(r"(^|\s)-g[a-z]*(\s|$)", call)]
    assert global_sets == [], f"no option may be set server-wide with -g: {global_sets}"
    for option in ("status 3", "status-style", "status-interval", "status-format[0]", "status-format[1]",
                   "status-format[2]", "default-terminal", "@pgai_dashboard 1"):
        assert any(call.startswith(f"set-option -t dash-a {option}") for call in calls), (option, calls)
    overrides = [call for call in calls if "terminal-overrides" in call]
    assert overrides == [
        "set-option -sa terminal-overrides ,xterm-256color:Tc",
        "set-option -sa terminal-overrides ,xterm-256color:RGB",
    ], f"each override is appended once over two runs: {overrides}"


def test_create_sh_sets_no_global_option_and_binds_prefix_p_conditionally() -> None:
    lines = [line for line in _CREATE_SH.read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    text = "\n".join(lines)
    assert not re.search(r"tmux set-option[^\n]*\s-g[a-z]*\s", text), "create.sh must not set a global option"
    binding = re.search(r"tmux bind-key -T prefix p (.*)", text)
    assert binding, "create.sh binds prefix+p"
    assert binding.group(1).startswith("if-shell -F '#{@pgai_dashboard}'"), binding.group(0)
    assert "previous-window" in text[binding.start(): binding.start() + 400], (
        "outside a dashboard session prefix+p must stay previous-window"
    )
