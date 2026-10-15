#!/usr/bin/env python3
"""
fake_container_engine.py — a scenario-driven stand-in for docker / podman.

Tests of the container lifecycle scripts must never start a real container.
This file is both:

* **the fake engine** — run as ``docker`` or ``podman`` (through the wrapper
  :class:`FakeEngine` writes onto a test-controlled PATH), it appends every
  invocation to a JSON-lines log and answers from a per-test *scenario*
  file, which it also updates, so a sequence of calls behaves like one
  engine (``run`` creates a container that the next ``container inspect``
  reports running; ``stop`` then ``rm`` make it absent); and
* **the helpers a test imports** to set it up and read it back:
  :class:`FakeEngine`, the scenario builders :func:`container`,
  :func:`scheduler_process`, :func:`agent_process` and
  :func:`wake_driver_process`, and :func:`install_fake_crontab`.

Typical use::

    engine = FakeEngine(tmp_path / "engine", scenario={
        "images": ["pgai-kanban:latest"],
        "containers": {"pgai-kanban": container(
            processes=[scheduler_process(40, kanban_root),
                       agent_process(77, task_id, exits_after_polls=2)])},
    })
    env = build_scrubbed_env(home_dir=..., extra_path_dirs=[engine.bin_dir])
    ...run the script under test...
    assert ["stop", "pgai-kanban"] in engine.calls()

Scenario format (JSON; every key optional)::

    {
      "images": ["pgai-kanban:latest"],          # what image inspect / run find
      "containers": {
        "<name>": {
          "status": "running" | "exited" | "created",
          "image": "pgai-kanban:latest",
          "started_at": "2026-10-11T07:00:00Z",
          "processes": [{"pid": 1, "args": "bash -l",
                         "session": "pseudocron",     # tmux session it runs in
                         "exits_after_polls": 2}],    # gone after 2 'ps' polls
          "tmux_sessions": ["pseudocron"]
        }
      },
      "scheduler": {"log": "<kanban root>/logs/pseudocron.log",
                    "fires": true},              # what a scheduler start writes
      "failures": {"<command>": {"exit": 125, "stderr": "...", "stdout": ""}}
    }

``failures`` forces a command to fail before it does anything. Its keys are
the command as the engine names it, with a leading ``container`` dropped:
``inspect``, ``image inspect``, ``ps``, ``run``, ``stop``, ``rm``,
``start``, ``kill``, ``exec`` (every exec) or ``exec <program>`` (one
program inside the container, e.g. ``exec ps``).

Commands answered: ``[container] inspect`` (also ``--format`` with
``{{.Path}}`` and ``{{json .}}`` templates), ``image inspect``,
``ps`` / ``container ls`` (``-a``, ``-q``, ``--filter name=``,
``--format``), ``images`` / ``image ls``, ``run``, ``start``, ``stop``,
``kill``, ``rm``, ``exec``, ``version``, ``info``, ``build``, ``pull``
(the last two only record and succeed: nothing is built or fetched). Inside
``exec`` the fake answers ``ps`` (always ``pid args`` rows with no header,
the ``ps -eo pid=,args=`` shape), ``tmux new|new-session -s S CMD``,
``tmux has-session -t S``, ``tmux kill-session -t S``, ``pkill [-f]
PATTERN``, ``kill [-SIG] PID...`` and ``bash|sh -c 'CMD'`` (re-read as
CMD); any other program succeeds with no output. A tmux session whose
command names ``pseudocron.py`` starts a scheduler process and, when the
scenario's ``scheduler.log`` is set, appends pseudocron's startup banner
and -- when ``scheduler.fires`` is true -- one ``fired (minute=NN)`` line.
Any other engine command fails with exit 125 naming itself, so a script
reaching for something the fake does not model is told so.

Log format: one JSON object per invocation,
``{"engine": "docker", "argv": [...], "command": "inspect"}``.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import shlex
import stat
import sys
import tempfile
from typing import Iterable, Optional

ENV_SCENARIO = "PGAI_FAKE_ENGINE_SCENARIO"
ENV_LOG = "PGAI_FAKE_ENGINE_LOG"
ENV_NAME = "PGAI_FAKE_ENGINE_NAME"

UNSUPPORTED_EXIT = 125

# `run` options that take a value (as a separate argument). Anything else
# starting with '-' is a flag, or carries its value after '='.
_RUN_VALUE_OPTIONS = frozenset({
    "-v", "--volume", "-e", "--env", "--name", "-w", "--workdir", "-u", "--user",
    "--entrypoint", "-p", "--publish", "--mount", "--network", "--net", "--restart",
    "-l", "--label", "-h", "--hostname", "--env-file", "--tmpfs", "--cap-add",
    "--cap-drop", "--security-opt", "--pid", "--ipc", "--userns", "--add-host",
    "--device", "--log-driver", "--log-opt", "-m", "--memory", "--cpus", "--shm-size",
    "--stop-timeout", "--stop-signal", "--platform", "--pull", "--cidfile",
    "--group-add", "--ulimit", "--health-cmd", "--label-file",
})
_EXEC_VALUE_OPTIONS = frozenset({"-e", "--env", "-u", "--user", "-w", "--workdir", "--env-file"})


# ---------------------------------------------------------------------------
# Test-side helpers
# ---------------------------------------------------------------------------


def container(
    *,
    status: str = "running",
    image: str = "pgai-kanban:latest",
    started_at: str = "2026-10-11T07:00:00Z",
    processes: Iterable[dict] = (),
    tmux_sessions: Iterable[str] = (),
) -> dict:
    """A scenario container entry. A running container always has pid 1
    (``bash -l``, what the image's entrypoint leaves running); a scheduler
    process in *processes* puts its tmux session in ``tmux_sessions``."""
    procs = [dict(p) for p in processes]
    if status == "running" and not any(p.get("pid") == 1 for p in procs):
        procs.insert(0, {"pid": 1, "args": "bash -l"})
    sessions = list(tmux_sessions)
    for proc in procs:
        session = proc.get("session")
        if session and session not in sessions:
            sessions.append(session)
    return {
        "status": status,
        "image": image,
        "started_at": started_at,
        "processes": procs if status == "running" else [],
        "tmux_sessions": sessions if status == "running" else [],
    }


def scheduler_process(pid: int, kanban_root: os.PathLike | str) -> dict:
    """A pseudocron scheduler process running in the ``pseudocron`` tmux session."""
    return {
        "pid": pid,
        "args": f"python3 {pathlib.Path(kanban_root) / 'scripts' / 'pseudocron.py'}",
        "session": "pseudocron",
    }


def agent_process(
    pid: int,
    task_id: str,
    *,
    provider: str = "claude",
    exits_after_polls: Optional[int] = None,
) -> dict:
    """An agent CLI process working *task_id*; with *exits_after_polls* it
    is listed by that many ``exec ... ps`` polls and gone after them."""
    proc = {
        "pid": pid,
        "args": f"{provider} -p Use the subagent to process the kanban task at /k/tasks/{task_id}",
    }
    if exits_after_polls is not None:
        proc["exits_after_polls"] = int(exits_after_polls)
    return proc


def wake_driver_process(
    pid: int,
    kanban_root: os.PathLike | str,
    *,
    script: str = "wake-batch.sh",
    args: str = "--agent=coder --sleep=0",
    exits_after_polls: Optional[int] = None,
) -> dict:
    """A wake script (*script* under ``<kanban_root>/scripts``) running as
    its ``#!/usr/bin/env bash`` line starts it; with *exits_after_polls* it
    is listed by that many ``exec ... ps`` polls and gone after them."""
    proc = {
        "pid": pid,
        "args": f"bash {pathlib.Path(kanban_root) / 'scripts' / script} {args}".rstrip(),
    }
    if exits_after_polls is not None:
        proc["exits_after_polls"] = int(exits_after_polls)
    return proc


def _write_executable(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class FakeEngine:
    """A fake engine installed as ``<root>/bin/<name>``.

    ``root`` holds ``scenario.json`` (the engine's state, rewritten by the
    fake as commands change it) and ``calls.jsonl`` (one line per
    invocation). Put :attr:`bin_dir` first on the subprocess PATH.
    """

    def __init__(self, root: os.PathLike | str, *, name: str = "docker", scenario: Optional[dict] = None):
        self.root = pathlib.Path(root)
        self.name = name
        self.bin_dir = self.root / "bin"
        self.scenario_path = self.root / "scenario.json"
        self.log_path = self.root / "calls.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.set_scenario(scenario or {})
        _write_executable(
            self.bin_dir / name,
            "#!/usr/bin/env bash\n"
            f"# Fake {name} for tests: answers from {self.scenario_path.name}, logs to {self.log_path.name}.\n"
            f"export {ENV_SCENARIO}={shlex.quote(str(self.scenario_path))}\n"
            f"export {ENV_LOG}={shlex.quote(str(self.log_path))}\n"
            f"export {ENV_NAME}={shlex.quote(name)}\n"
            f"exec {shlex.quote(sys.executable)} {shlex.quote(str(pathlib.Path(__file__).resolve()))} \"$@\"\n",
        )

    @property
    def executable(self) -> pathlib.Path:
        return self.bin_dir / self.name

    def set_scenario(self, scenario: dict) -> None:
        self.scenario_path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")

    def scenario(self) -> dict:
        return json.loads(self.scenario_path.read_text(encoding="utf-8"))

    def records(self) -> list[dict]:
        """Every logged invocation, oldest first."""
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def calls(self) -> list[list[str]]:
        """The argv (without the engine name) of every invocation, oldest first."""
        return [record["argv"] for record in self.records()]

    def commands(self) -> list[str]:
        """The command key of every invocation (``run``, ``exec ps`` ...)."""
        return [record["command"] for record in self.records()]


def install_fake_crontab(bin_dir: os.PathLike | str, text: Optional[str]) -> pathlib.Path:
    """Install a ``crontab`` stub in *bin_dir* whose ``-l`` prints *text*.

    *text* ``None`` behaves like a user with no crontab (``no crontab for
    <user>`` on stderr, exit 1). Every other use (installing or removing a
    crontab) changes nothing, is refused with exit 1, and is recorded in
    ``<bin_dir>/crontab-calls.log`` beside every ``-l``.
    """
    bin_dir = pathlib.Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = bin_dir / "crontab.txt"
    calls = bin_dir / "crontab-calls.log"
    if text is None:
        body.unlink(missing_ok=True)
    else:
        body.write_text(text, encoding="utf-8")
    path = bin_dir / "crontab"
    _write_executable(
        path,
        "#!/usr/bin/env bash\n"
        "# Fake crontab for tests: lists a fixed crontab, changes nothing.\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        "if [[ \"$*\" == \"-l\" ]]; then\n"
        f"    if [[ -f {shlex.quote(str(body))} ]]; then cat {shlex.quote(str(body))}; exit 0; fi\n"
        "    echo \"no crontab for ${USER:-user}\" >&2; exit 1\n"
        "fi\n"
        "echo \"fake crontab: only -l is supported; refused: $*\" >&2\n"
        "exit 1\n",
    )
    return path


# ---------------------------------------------------------------------------
# Engine side
# ---------------------------------------------------------------------------


class _Exit(Exception):
    def __init__(self, code: int, stdout: str = "", stderr: str = ""):
        super().__init__(code)
        self.code, self.stdout, self.stderr = code, stdout, stderr


def _fake_id(name: str) -> str:
    """A stable 64-hex container ID for *name* (the same in every call)."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _command_key(argv: list[str]) -> tuple[str, list[str]]:
    """The command key for *argv* and the arguments after it."""
    if not argv:
        return "", []
    words = list(argv)
    if words[0] == "container" and len(words) > 1:
        words = words[1:]
        if words[0] in ("ls", "list"):
            words[0] = "ps"
    if words[0] == "image" and len(words) > 1:
        if words[1] in ("ls", "list"):
            return "images", words[2:]
        return f"image {words[1]}", words[2:]
    if words[0] == "exec":
        return "exec", words[1:]
    return words[0], words[1:]


def _flag_value(args: list[str], *names: str) -> Optional[str]:
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith(name + "="):
                return arg[len(name) + 1:]
    return None


def _positionals(args: list[str], value_options: Iterable[str] = ("-f", "--format", "--filter", "-t", "--time", "--type")) -> list[str]:
    values = set(value_options)
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg in values:
            skip = True
        elif not arg.startswith("-"):
            out.append(arg)
    return out


def _render_template(template: str, obj: dict) -> str:
    def field(path: str):
        value = obj
        for part in [p for p in path.split(".") if p]:
            value = value.get(part) if isinstance(value, dict) else None
        return value

    def repl(match: re.Match) -> str:
        expr = match.group(1).strip()
        if expr.startswith("json"):
            return json.dumps(field(expr[4:].strip()))
        value = field(expr)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return ",".join(str(v) for v in value)
        return "" if value is None else str(value)

    return re.sub(r"\{\{\s*(.*?)\s*\}\}", repl, template)


class _Engine:
    def __init__(self, scenario: dict):
        self.s = scenario
        self.s.setdefault("containers", {})
        self.s.setdefault("images", [])
        self.out: list[str] = []
        self.err: list[str] = []

    # -- shared -------------------------------------------------------------

    def _container(self, name: str) -> dict:
        found = self.s["containers"].get(name)
        if found is None:
            raise _Exit(1, stderr=f"Error response from daemon: No such container: {name}")
        return found

    def _inspect_obj(self, name: str, c: dict) -> dict:
        running = c.get("status") == "running"
        return {
            "Id": _fake_id(name)[:12],
            "Name": f"/{name}",
            "Image": "sha256:" + "0" * 12,
            "State": {
                "Status": c.get("status", "exited"),
                "Running": running,
                "StartedAt": c.get("started_at") or "0001-01-01T00:00:00Z",
            },
            "Config": {"Image": c.get("image", "")},
        }

    def _next_pid(self, c: dict) -> int:
        return max([99] + [int(p.get("pid", 0)) for p in c.get("processes", [])]) + 1

    # -- commands -----------------------------------------------------------

    def inspect(self, args: list[str]) -> int:
        fmt = _flag_value(args, "-f", "--format")
        names = _positionals(args)
        objs, missing = [], []
        for name in names:
            c = self.s["containers"].get(name)
            if c is not None:
                objs.append(self._inspect_obj(name, c))
            elif name in self.s["images"]:
                objs.append({"Id": "sha256:" + "1" * 12, "RepoTags": [name]})
            else:
                missing.append(name)
        if fmt:
            self.out.extend(_render_template(fmt, o) for o in objs)
        else:
            self.out.append(json.dumps(objs, indent=4))
        if missing:
            self.err.append(f"Error: No such object: {missing[0]}")
            return 1
        return 0

    def image_inspect(self, args: list[str]) -> int:
        names = _positionals(args)
        found = [n for n in names if n in self.s["images"]]
        self.out.append(json.dumps([{"Id": "sha256:" + "1" * 12, "RepoTags": [n]} for n in found], indent=4))
        missing = [n for n in names if n not in self.s["images"]]
        if missing:
            self.err.append(f"Error: No such image: {missing[0]}")
            return 1
        return 0

    def images(self, args: list[str]) -> int:
        self.out.extend(self.s["images"])
        return 0

    def ps(self, args: list[str]) -> int:
        show_all = any(a in ("-a", "--all") for a in args)
        quiet = any(a in ("-q", "--quiet") for a in args)
        name_filter = None
        flt = _flag_value(args, "--filter", "-f")
        if flt and flt.startswith("name="):
            name_filter = re.compile(flt[len("name="):])
        fmt = _flag_value(args, "--format")
        rows = []
        for name, c in self.s["containers"].items():
            if not show_all and c.get("status") != "running":
                continue
            if name_filter and not name_filter.search(name):
                continue
            rows.append({
                "ID": _fake_id(name)[:12],
                "Names": name,
                "Image": c.get("image", ""),
                "State": c.get("status", ""),
                "Status": "Up" if c.get("status") == "running" else "Exited (0)",
            })
        for row in rows:
            if quiet:
                self.out.append(row["ID"])
            elif fmt:
                self.out.append(_render_template(fmt, row))
            else:
                self.out.append(f"{row['ID']}  {row['Image']}  {row['Status']}  {row['Names']}")
        return 0

    def run(self, args: list[str]) -> int:
        detached = False
        name = None
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                i += 1
                break
            if not arg.startswith("-"):
                break
            if arg in _RUN_VALUE_OPTIONS:
                if arg == "--name" and i + 1 < len(args):
                    name = args[i + 1]
                i += 2
                continue
            if arg.startswith("--name="):
                name = arg[len("--name="):]
            elif arg in ("-d", "--detach") or (re.fullmatch(r"-[a-zA-Z]+", arg) and "d" in arg):
                detached = True
            i += 1
        if i >= len(args):
            raise _Exit(UNSUPPORTED_EXIT, stderr="fake container engine: run needs an image")
        image, command = args[i], args[i + 1:]
        if image not in self.s["images"]:
            raise _Exit(UNSUPPORTED_EXIT, stderr=f"Unable to find image '{image}' locally (the fake engine never pulls)")
        if name and name in self.s["containers"]:
            raise _Exit(UNSUPPORTED_EXIT, stderr=f'Conflict. The container name "/{name}" is already in use')
        if not detached:
            return 0
        name = name or f"fake_{len(self.s['containers']) + 1}"
        entry = container(image=image, started_at=_now_iso())
        entry["processes"][0]["args"] = " ".join(command) or "bash -l"
        entry["run_argv"] = ["run"] + args
        self.s["containers"][name] = entry
        self.out.append(_fake_id(name))
        return 0

    def start(self, args: list[str]) -> int:
        for name in _positionals(args):
            c = self._container(name)
            if c.get("status") != "running":
                c.update(status="running", started_at=_now_iso(),
                         processes=[{"pid": 1, "args": "bash -l"}], tmux_sessions=[])
            self.out.append(name)
        return 0

    def stop(self, args: list[str]) -> int:
        for name in _positionals(args):
            c = self._container(name)
            c.update(status="exited", processes=[], tmux_sessions=[])
            self.out.append(name)
        return 0

    kill = stop

    def rm(self, args: list[str]) -> int:
        force = any(a in ("-f", "--force") or (re.fullmatch(r"-[a-zA-Z]+", a) and "f" in a) for a in args)
        for name in _positionals(args):
            c = self._container(name)
            if c.get("status") == "running" and not force:
                raise _Exit(1, stderr=f"Error response from daemon: cannot remove container \"/{name}\": container is running: stop the container before removing or force remove")
            del self.s["containers"][name]
            self.out.append(name)
        return 0

    def version(self, args: list[str]) -> int:
        self.out.append("Fake container engine 0.0.0")
        return 0

    info = version

    def build(self, args: list[str]) -> int:
        return 0

    pull = build

    # -- exec ---------------------------------------------------------------

    def exec(self, args: list[str]) -> int:
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in _EXEC_VALUE_OPTIONS else 1
        if i >= len(args):
            raise _Exit(UNSUPPORTED_EXIT, stderr="fake container engine: exec needs a container and a command")
        name, command = args[i], args[i + 1:]
        c = self._container(name)
        if c.get("status") != "running":
            raise _Exit(1, stderr=f"Error response from daemon: container {name} is not running")
        return self._in_container(c, command)

    def _in_container(self, c: dict, command: list[str]) -> int:
        if not command:
            raise _Exit(UNSUPPORTED_EXIT, stderr="fake container engine: exec needs a command")
        program = command[0].rsplit("/", 1)[-1]
        rest = command[1:]
        if program in ("bash", "sh") and "-c" in rest:
            script = rest[rest.index("-c") + 1] if rest.index("-c") + 1 < len(rest) else ""
            return self._in_container(c, shlex.split(script))
        if program == "ps":
            return self._ps_in(c)
        if program == "tmux":
            return self._tmux(c, rest)
        if program == "pkill":
            pattern = next((a for a in rest if not a.startswith("-")), "")
            before = len(c["processes"])
            c["processes"] = [p for p in c["processes"] if p.get("pid") == 1 or not re.search(pattern, p.get("args", ""))]
            self._sync_sessions(c)
            return 0 if len(c["processes"]) < before else 1
        if program == "kill":
            pids = {int(a) for a in rest if a.isdigit()}
            c["processes"] = [p for p in c["processes"] if int(p.get("pid", 0)) not in pids]
            self._sync_sessions(c)
            return 0
        return 0

    def _ps_in(self, c: dict) -> int:
        for proc in c["processes"]:
            self.out.append(f"{int(proc['pid']):>7} {proc['args']}")
        remaining = []
        for proc in c["processes"]:
            polls = proc.get("exits_after_polls")
            if polls is not None:
                polls -= 1
                if polls <= 0:
                    continue
                proc["exits_after_polls"] = polls
            remaining.append(proc)
        c["processes"] = remaining
        self._sync_sessions(c)
        return 0

    def _sync_sessions(self, c: dict) -> None:
        live = {p.get("session") for p in c["processes"]}
        c["tmux_sessions"] = [s for s in c.get("tmux_sessions", []) if s in live]

    def _tmux(self, c: dict, rest: list[str]) -> int:
        verb = rest[0] if rest else ""
        args = rest[1:]
        sessions = c.setdefault("tmux_sessions", [])
        target = _flag_value(args, "-s") if verb in ("new", "new-session") else _flag_value(args, "-t")
        if verb in ("new", "new-session"):
            target = target or "0"
            if target in sessions:
                raise _Exit(1, stderr=f"duplicate session: {target}")
            sessions.append(target)
            cmd = " ".join(a for a in args if a not in ("-d", "-s", target))
            if "pseudocron.py" in cmd:
                script = next((t for t in shlex.split(cmd) if t.endswith("pseudocron.py")), "pseudocron.py")
                c["processes"].append({"pid": self._next_pid(c), "args": f"python3 {script}", "session": target})
                self._scheduler_started()
            elif cmd:
                c["processes"].append({"pid": self._next_pid(c), "args": cmd, "session": target})
            return 0
        if verb == "has-session":
            return 0 if target in sessions else 1
        if verb == "kill-session":
            if target not in sessions:
                raise _Exit(1, stderr=f"can't find session: {target}")
            c["processes"] = [p for p in c["processes"] if p.get("session") != target]
            sessions.remove(target)
            return 0
        if verb in ("ls", "list-sessions"):
            self.out.extend(f"{s}: 1 windows" for s in sessions)
            return 0 if sessions else 1
        raise _Exit(UNSUPPORTED_EXIT, stderr=f"fake container engine: tmux {verb} is not modelled")

    def _scheduler_started(self) -> None:
        sched = self.s.get("scheduler") or {}
        log = sched.get("log")
        if not log:
            return
        path = pathlib.Path(log)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now(datetime.timezone.utc)
        lines = [
            f"pseudocron starting: 5 jobs loaded from {path.parent.parent / 'pseudocron.cfg'}",
            f"pseudocron starting: 3 vars loaded from {path.parent.parent / 'pseudocron.env'}",
        ]
        if sched.get("fires"):
            lines.append(f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} fired (minute={now.minute:02d}): scripts/wake-batch.sh --agent=pm --sleep=0")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    # -- dispatch -----------------------------------------------------------

    def dispatch(self, key: str, args: list[str]) -> int:
        failures = self.s.get("failures") or {}
        candidates = [key]
        if key == "exec":
            program = self._exec_program(args)
            if program:
                candidates.insert(0, f"exec {program}")
        for candidate in candidates:
            forced = failures.get(candidate)
            if forced:
                raise _Exit(int(forced.get("exit", 1)), forced.get("stdout", ""), forced.get("stderr", ""))
        handler = {
            "inspect": self.inspect, "image inspect": self.image_inspect, "images": self.images,
            "ps": self.ps, "run": self.run, "start": self.start, "stop": self.stop,
            "kill": self.kill, "rm": self.rm, "exec": self.exec, "version": self.version,
            "info": self.info, "build": self.build, "pull": self.pull,
        }.get(key)
        if handler is None:
            raise _Exit(UNSUPPORTED_EXIT, stderr=f"fake container engine: '{key}' is not modelled; extend tests/fixtures/fake_container_engine.py")
        return handler(args)

    @staticmethod
    def _exec_program(args: list[str]) -> str:
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in _EXEC_VALUE_OPTIONS else 1
        command = args[i + 1:]
        if not command:
            return ""
        program = command[0].rsplit("/", 1)[-1]
        if program in ("bash", "sh") and "-c" in command:
            idx = command.index("-c") + 1
            inner = shlex.split(command[idx]) if idx < len(command) else []
            return inner[0].rsplit("/", 1)[-1] if inner else program
        return program


@contextlib.contextmanager
def _locked(path: pathlib.Path):
    with open(f"{path}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _save(path: pathlib.Path, scenario: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".scenario-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(scenario, fh, indent=2)
    os.replace(tmp, path)


def engine_main(argv: list[str]) -> int:
    """Answer one engine invocation; returns its exit status."""
    scenario_env, log_env = os.environ.get(ENV_SCENARIO), os.environ.get(ENV_LOG)
    if not scenario_env or not log_env:
        print(f"fake container engine: {ENV_SCENARIO} and {ENV_LOG} must name the scenario and call-log files", file=sys.stderr)
        return UNSUPPORTED_EXIT
    scenario_path, log_path = pathlib.Path(scenario_env), pathlib.Path(log_env)
    key, args = _command_key(argv)
    engine_name = os.environ.get(ENV_NAME) or pathlib.Path(sys.argv[0]).name
    with _locked(scenario_path):
        with open(log_path, "a", encoding="utf-8") as log:
            command = key
            if key == "exec":
                program = _Engine._exec_program(args)
                command = f"exec {program}" if program else key
            log.write(json.dumps({"engine": engine_name, "argv": argv, "command": command}) + "\n")
        scenario = json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.exists() else {}
        engine = _Engine(scenario)
        try:
            code = engine.dispatch(key, args)
            stdout, stderr = engine.out, engine.err
        except _Exit as exc:
            code = exc.code
            stdout = engine.out + ([exc.stdout] if exc.stdout else [])
            stderr = engine.err + ([exc.stderr] if exc.stderr else [])
        _save(scenario_path, engine.s)
    if stdout:
        sys.stdout.write("\n".join(stdout) + "\n")
    if stderr:
        sys.stderr.write("\n".join(stderr) + "\n")
    return code


if __name__ == "__main__":
    sys.exit(engine_main(sys.argv[1:]))
