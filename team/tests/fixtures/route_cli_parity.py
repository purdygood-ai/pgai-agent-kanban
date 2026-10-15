"""
route_cli_parity.py
===================
Test helper: prove that an operator verb's REST route and its CLI script
reach the same Python function -- the function the verb's ``impl`` column
in ``team/config/operator_verbs.manifest`` names as
``module:<dotted.module.function>``.

WHY THIS EXISTS
---------------
A verb ported to a module carries two promises: the route calls the
function in-process and starts no process on the way, and the CLI script
reaches the SAME function through ``team/scripts/lib/pp_run_ops.sh``.
``team/scripts/lint_api_route_impl.py`` checks both by reading source. This
helper checks them by running the code, from one manifest row, so every
ported verb's test asserts the same two things the same way instead of
each test file hand-rolling its own spy and its own subprocess guard.

WHAT IS ASSERTED
----------------
``assert_route_reaches_function(client, row, json=..., params=...)``
    Sends the row's REST method and route through *client* (an in-process
    ``TestClient``) with every process-starting entry point replaced by one
    that records the attempt and raises ``SubprocessForbidden``, and with
    the row's function wrapped by a spy. Fails when the route started, or
    tried to start, a process -- even one whose exception the route
    swallowed -- or when it never called the function.

``assert_cli_reaches_function(row, cli_args=..., env=..., scratch_dir=...)``
    Always: the row's script exists and reaches the function through
    ``pp_run_ops`` by the same source rule the lint applies (a
    ``pp_run_ops <module> <function>`` call, or a ``pp_pythonpath`` import
    of the function). With *cli_args*: also runs the script for real and
    fails unless a Python process it started called the function. The run
    puts a generated ``sitecustomize`` ahead of everything on PYTHONPATH,
    which wraps the function at interpreter start and records each call. The
    run's PYTHONPATH carries ONLY that directory and its working directory
    is *scratch_dir*. So the package is importable only where
    ``pp_run_ops`` / ``pp_pythonpath`` composed the path. A bare
    ``python3 -m pgai_agent_kanban...`` that bypasses them cannot import it,
    and the call is never recorded.

``assert_route_cli_parity(row, client, ...)``
    Both of the above; returns a ``ParityResult`` holding the HTTP response
    and the CLI run for the caller's own effect assertions.

HOW TO USE
----------
    from tests.fixtures.golden_board import PROJECT_NAME, build_synthetic_board
    from tests.fixtures.route_cli_parity import assert_route_cli_parity, build_api_client
    from tests.fixtures.scrubbed_subprocess_env import build_scrubbed_env

    rest_board = tmp_path / "rest-board"
    build_synthetic_board(rest_board)
    cli_board = tmp_path / "cli-board"
    build_synthetic_board(cli_board)
    env = build_scrubbed_env(home_dir=tmp_path / "home", extra_env={
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(cli_board),
        "KANBAN_ROOT": str(cli_board),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "scratch"),
    })
    result = assert_route_cli_parity(
        "halt", build_api_client(rest_board),
        json={"project": PROJECT_NAME},
        cli_args=["--project", PROJECT_NAME], env=env, scratch_dir=tmp_path / "scratch",
    )
    assert result.response.status_code == 200

*row* is a verb name (looked up in the real manifest) or any object with
the manifest reader's ``Verb`` attributes: ``name``, ``impl``,
``rest_method``, ``rest_route``, ``script``.

WHAT THIS HELPER DOES NOT DO
----------------------------
- It does not compare what the two surfaces produced; the caller asserts
  effects (``golden_comparator.py`` compares JSON, ``action_parity_cases.py``
  compares on-disk effects).
- It does not build a board or an environment. The caller passes a client
  whose ``kanban_root`` is a throwaway board and a CLI environment pointed
  at another; nothing here falls back to the operator's live install.
- The spy rebinds every module attribute that holds the function. A copy
  held elsewhere -- a dispatch dict built at import time, a default
  argument, a closure -- is not rebound, and calls through it are not
  seen. The CLI spy wraps the function in the module the interpreter
  imports, so a script that runs the defining module itself as
  ``python3 -m <that module>`` executes a second copy the spy never sees;
  run the package entry point (``python3 -m pgai_agent_kanban.ops``) as
  the existing scripts do.
- Forbidding covers the standard library's process entry points
  (``subprocess.Popen`` and everything built on it, ``os.system``,
  ``os.popen``, the ``os`` spawn/exec/fork families,
  ``asyncio.create_subprocess_*``). A C extension that forks on its own
  is out of reach.
- It imports no pytest; failures are plain ``AssertionError`` with the
  verb, the route or script, and the function named.
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import contextlib
import dataclasses
import functools
import importlib
import importlib.util
import os
import pathlib
import subprocess
import sys
from types import ModuleType
from typing import Any, Callable, Iterator, Optional, Sequence, Union

# team/ is three levels up from this file (fixtures/ -> tests/ -> team/),
# the same self-location golden_board.py and action_parity_cases.py use.
_TEAM_DIR = pathlib.Path(__file__).resolve().parent.parent.parent
_LINT_SCRIPT = _TEAM_DIR / "scripts" / "lint_api_route_impl.py"

if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

from pgai_agent_kanban.lib import operator_verb_manifest as ovm  # noqa: E402

__all__ = [
    "CliRun",
    "ParityResult",
    "ParityTarget",
    "SubprocessForbidden",
    "assert_cli_reaches_function",
    "assert_route_cli_parity",
    "assert_route_reaches_function",
    "build_api_client",
    "forbid_subprocess",
    "parity_target",
    "run_cli_with_spy",
    "spy_on",
]

# Environment names the generated sitecustomize reads in the CLI run.
SPY_TARGET_ENV = "PGAI_ROUTE_CLI_PARITY_TARGET"
SPY_RECORD_ENV = "PGAI_ROUTE_CLI_PARITY_RECORD"


class SubprocessForbidden(RuntimeError):
    """Raised by a process entry point called while ``forbid_subprocess`` is active."""


# ---------------------------------------------------------------------------
# The row under test
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ParityTarget:
    """What one ``module:`` manifest row promises: a route, a script, one function."""

    verb: str
    module: str
    function: str
    method: str
    route: str
    script: Optional[str]

    @property
    def dotted(self) -> str:
        return f"{self.module}.{self.function}"


def parity_target(row: Union[str, Any]) -> ParityTarget:
    """Read the route, script and function a manifest row declares.

    Args:
        row: A verb name, looked up in the real manifest, or an object with
             the manifest ``Verb`` record's ``name``, ``impl``,
             ``rest_method``, ``rest_route`` and ``script`` attributes.

    Raises:
        AssertionError: the row's ``impl`` is not ``module:<function>``
            (a ``shell`` row has no shared function to check yet).
    """
    verb = ovm.get_verb(row) if isinstance(row, str) else row
    try:
        impl = ovm.parse_impl(verb.impl)
    except ValueError as exc:
        raise AssertionError(f"verb '{verb.name}': {exc}") from exc
    if impl.kind != "module":
        raise AssertionError(
            f"verb '{verb.name}' has impl {verb.impl!r}; route/CLI parity applies to a "
            "module:<dotted.module.function> row -- flip the row in "
            "team/config/operator_verbs.manifest in the change that ports the verb"
        )
    return ParityTarget(
        verb=verb.name,
        module=impl.module,
        function=impl.function,
        method=verb.rest_method,
        route=verb.rest_route,
        script=verb.script,
    )


def _import_function(target: ParityTarget) -> tuple[ModuleType, Callable]:
    try:
        module = importlib.import_module(target.module)
    except ImportError as exc:
        raise AssertionError(
            f"verb '{target.verb}': module {target.module} named by impl module:{target.dotted} "
            f"does not import ({exc})"
        ) from exc
    function = getattr(module, target.function, None)
    if not callable(function):
        raise AssertionError(
            f"verb '{target.verb}': {target.module} has no function {target.function} "
            f"(impl module:{target.dotted})"
        )
    return module, function


# ---------------------------------------------------------------------------
# In-process guards
# ---------------------------------------------------------------------------

# (module, attribute) pairs that start a process. subprocess.run, call,
# check_call, check_output and getoutput all construct subprocess.Popen, so
# replacing Popen covers them.
_PROCESS_ENTRY_POINTS: tuple[tuple[ModuleType, str], ...] = tuple(
    (module, name)
    for module, names in (
        (subprocess, ("Popen",)),
        (
            os,
            (
                "system", "popen", "posix_spawn", "posix_spawnp", "fork", "forkpty",
                "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
                "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
            ),
        ),
        (asyncio, ("create_subprocess_exec", "create_subprocess_shell")),
        (asyncio.subprocess, ("create_subprocess_exec", "create_subprocess_shell")),
    )
    for name in names
    if hasattr(module, name)
)


def _describe_argv(args: tuple, kwargs: dict) -> str:
    argv = args[0] if args else kwargs.get("args", kwargs.get("cmd", ""))
    if isinstance(argv, (list, tuple)):
        return " ".join(str(a) for a in argv)
    return str(argv)


@contextlib.contextmanager
def forbid_subprocess() -> Iterator[list[str]]:
    """Make every standard process entry point record the attempt and raise.

    Yields the list the attempts are appended to, one
    ``"<module>.<name>: <argv>"`` string each. The list survives a route
    that catches ``SubprocessForbidden`` and carries on, so a swallowed
    attempt is still visible. Every entry point is restored on exit.
    """
    attempts: list[str] = []
    originals: list[tuple[ModuleType, str, Any]] = []

    def _forbidden(label: str) -> Callable:
        def _refuse(*args: Any, **kwargs: Any) -> Any:
            attempts.append(f"{label}: {_describe_argv(args, kwargs)}")
            raise SubprocessForbidden(f"{label} called while process execution is forbidden")

        return _refuse

    class _ForbiddenPopen(subprocess.Popen):
        # A subclass keeps isinstance(x, subprocess.Popen) checks working;
        # Popen.__del__ tolerates the half-built instance __init__ leaves.
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            attempts.append(f"subprocess.Popen: {_describe_argv(args, kwargs)}")
            raise SubprocessForbidden("subprocess.Popen called while process execution is forbidden")

    try:
        for module, name in _PROCESS_ENTRY_POINTS:
            originals.append((module, name, getattr(module, name)))
            replacement = _ForbiddenPopen if (module, name) == (subprocess, "Popen") else _forbidden(
                f"{module.__name__}.{name}"
            )
            setattr(module, name, replacement)
        yield attempts
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)


def _rebind_everywhere(original: Callable, replacement: Callable) -> list[tuple[dict, str]]:
    """Point every loaded module attribute holding *original* at *replacement*."""
    rebound: list[tuple[dict, str]] = []
    for module in list(sys.modules.values()):
        try:
            namespace = vars(module)
        except TypeError:
            continue
        for name, value in list(namespace.items()):
            if value is original:
                namespace[name] = replacement
                rebound.append((namespace, name))
    return rebound


@contextlib.contextmanager
def spy_on(dotted: str) -> Iterator[list[tuple[tuple, dict]]]:
    """Wrap the function *dotted* names so each call is recorded, then delegated.

    Every loaded module attribute bound to the function -- its defining
    module, a package re-export, a router's ``from ... import`` -- is
    rebound to the spy for the duration, so a call by any of those names is
    seen. Import the code under test (build the client) before entering.

    Yields the list of ``(args, kwargs)`` tuples, one per call.
    """
    module_name, _, function_name = dotted.rpartition(".")
    target = ParityTarget("", module_name, function_name, "", "", None)
    _module, original = _import_function(target)
    calls: list[tuple[tuple, dict]] = []

    @functools.wraps(original)
    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    rebound = _rebind_everywhere(original, _spy)
    try:
        yield calls
    finally:
        for namespace, name in rebound:
            if namespace.get(name) is _spy:
                namespace[name] = original


# ---------------------------------------------------------------------------
# REST half
# ---------------------------------------------------------------------------


def build_api_client(kanban_root: Union[str, pathlib.Path]) -> Any:
    """Return an in-process ``TestClient`` for the production API on *kanban_root*.

    *kanban_root* is the server's root: route data and ``scripts/`` both
    resolve under it, so pass a throwaway board (``golden_board`` symlinks
    the real ``scripts/`` tree into one). The client names a loopback host,
    the only kind the API answers to.
    """
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=pathlib.Path(kanban_root))
    return TestClient(create_app(cfg=cfg), base_url="http://127.0.0.1", raise_server_exceptions=True)


def _excerpt(text: str, limit: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."


def assert_route_reaches_function(
    client: Any,
    row: Union[str, Any],
    *,
    json: Any = None,
    params: Optional[dict] = None,
) -> Any:
    """Assert the row's route calls its function in-process and starts no process.

    Args:
        client: An in-process client with ``request(method, url, json=,
                params=)`` -- ``build_api_client`` or any ``TestClient``.
        row:    A verb name or manifest ``Verb``-shaped record (see
                ``parity_target``).
        json:   Request body for the route.
        params: Query parameters for the route.

    Returns:
        The response, for the caller's own assertions on status and body.

    Raises:
        AssertionError: the route started or tried to start a process, or
            never called the function.
    """
    target = parity_target(row)
    where = f"verb '{target.verb}': {target.method} {target.route}"
    response = None
    raised: Optional[BaseException] = None
    with spy_on(target.dotted) as calls, forbid_subprocess() as attempts:
        try:
            response = client.request(target.method, target.route, json=json, params=params)
        except Exception as exc:  # noqa: BLE001 -- judged below against the attempts list
            raised = exc
    if attempts:
        listed = "\n".join(f"  - {a}" for a in attempts)
        raise AssertionError(
            f"{where} started a process; a module:{target.dotted} route calls the function "
            f"in-process and starts none. Attempts:\n{listed}"
        )
    if raised is not None:
        raise raised
    if not calls:
        raise AssertionError(
            f"{where} never called {target.dotted} (HTTP {response.status_code}: "
            f"{_excerpt(response.text)})"
        )
    return response


# ---------------------------------------------------------------------------
# CLI half
# ---------------------------------------------------------------------------

# The sitecustomize the CLI run puts first on PYTHONPATH. It wraps the target
# function in every loaded module that holds it and appends one line per call
# to the record file. An interpreter that cannot import the target (a python3
# started without pp_run_ops' path) runs unmodified.
_SITECUSTOMIZE = '''\
"""Route/CLI parity spy: records calls to the function named in the environment."""
import functools
import importlib
import os
import sys


def _install():
    dotted = os.environ.get("{target_env}", "")
    record = os.environ.get("{record_env}", "")
    module_name, _, function_name = dotted.rpartition(".")
    if not (module_name and function_name and record):
        return
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return
    original = getattr(module, function_name, None)
    if not callable(original):
        return

    @functools.wraps(original)
    def _spy(*args, **kwargs):
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(dotted + " pid=" + str(os.getpid()) + "\\n")
        return original(*args, **kwargs)

    for loaded in list(sys.modules.values()):
        try:
            namespace = vars(loaded)
        except TypeError:
            continue
        for name, value in list(namespace.items()):
            if value is original:
                namespace[name] = _spy


_install()
'''.format(target_env=SPY_TARGET_ENV, record_env=SPY_RECORD_ENV)


@dataclasses.dataclass(frozen=True)
class CliRun:
    """One spied run of a CLI script."""

    completed: subprocess.CompletedProcess
    calls: list[str]


def _load_lint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lint_api_route_impl_for_parity", _LINT_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {_LINT_SCRIPT}; the static pp_run_ops check needs it")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _script_path(target: ParityTarget, team_dir: pathlib.Path) -> pathlib.Path:
    if not target.script:
        raise AssertionError(f"verb '{target.verb}' declares no script")
    return team_dir / target.script


def run_cli_with_spy(
    row: Union[str, Any],
    cli_args: Sequence[str],
    *,
    env: dict,
    scratch_dir: Union[str, pathlib.Path],
    team_dir: Union[str, pathlib.Path, None] = None,
    timeout: int = 120,
) -> CliRun:
    """Run the row's script with the call-recording sitecustomize in place.

    Args:
        row:         A verb name or manifest ``Verb``-shaped record.
        cli_args:    Arguments after the script path.
        env:         The script's environment (a throwaway board, built with
                     ``scrubbed_subprocess_env``). Its PYTHONPATH is replaced:
                     the run sees only the spy directory there.
        scratch_dir: A directory this run may write in (the spy module and
                     its record file); also the run's working directory.
        team_dir:    The tree whose script runs (default: this tree's team/).
        timeout:     Seconds before the run is abandoned.

    Returns:
        The completed process and the recorded calls, one string per call.
    """
    target = parity_target(row)
    script = _script_path(target, pathlib.Path(team_dir) if team_dir else _TEAM_DIR)
    scratch = pathlib.Path(scratch_dir)
    spy_dir = scratch / "route-cli-parity-spy"
    spy_dir.mkdir(parents=True, exist_ok=True)
    (spy_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    record = scratch / f"route-cli-parity-calls-{target.verb}.log"
    record.unlink(missing_ok=True)

    run_env = dict(env)
    run_env["PYTHONPATH"] = str(spy_dir)
    run_env[SPY_TARGET_ENV] = target.dotted
    run_env[SPY_RECORD_ENV] = str(record)
    completed = subprocess.run(
        ["bash", str(script), *cli_args],
        cwd=str(scratch),
        env=run_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    calls = record.read_text(encoding="utf-8").splitlines() if record.is_file() else []
    return CliRun(completed, calls)


def assert_cli_reaches_function(
    row: Union[str, Any],
    *,
    cli_args: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    scratch_dir: Union[str, pathlib.Path, None] = None,
    team_dir: Union[str, pathlib.Path, None] = None,
) -> Optional[CliRun]:
    """Assert the row's script reaches its function through pp_run_ops.

    The source check always runs. With *cli_args*, the script also runs (see
    ``run_cli_with_spy``; *env* and *scratch_dir* are then required) and
    must call the function at least once. A non-zero exit is not by itself
    a failure -- a refusal path may legitimately run after the call -- so
    the caller asserts the exit code it expects.

    Returns:
        The spied run when *cli_args* was given, otherwise None.

    Raises:
        AssertionError: naming every way the script failed to reach the
            function.
    """
    target = parity_target(row)
    script = _script_path(target, pathlib.Path(team_dir) if team_dir else _TEAM_DIR)
    problems: list[str] = []
    if not script.is_file():
        raise AssertionError(f"verb '{target.verb}': script {script} does not exist")
    text = script.read_text(encoding="utf-8", errors="replace")
    if not _load_lint().script_reaches(text, target.module, target.function):
        problems.append(
            f"{script} has no 'pp_run_ops {target.module} {target.function}' call (or a module "
            f"above it) and no pp_pythonpath import of {target.function}"
        )
    run: Optional[CliRun] = None
    if cli_args is not None:
        if env is None or scratch_dir is None:
            raise AssertionError("running the script needs env= and scratch_dir= (a throwaway board and scratch dir)")
        run = run_cli_with_spy(row, cli_args, env=env, scratch_dir=scratch_dir, team_dir=team_dir)
        if not run.calls:
            problems.append(
                f"bash {script} {' '.join(cli_args)} exited {run.completed.returncode} and never reached "
                f"{target.dotted} through pp_run_ops; stdout: {_excerpt(run.completed.stdout)!r} "
                f"stderr: {_excerpt(run.completed.stderr)!r}"
            )
    if problems:
        listed = "\n".join(f"  - {p}" for p in problems)
        raise AssertionError(f"verb '{target.verb}': CLI does not reach {target.dotted}:\n{listed}")
    return run


# ---------------------------------------------------------------------------
# Both halves
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ParityResult:
    """What ``assert_route_cli_parity`` observed, for the caller's effect checks."""

    target: ParityTarget
    response: Any
    cli: Optional[CliRun]


def assert_route_cli_parity(
    row: Union[str, Any],
    client: Any,
    *,
    json: Any = None,
    params: Optional[dict] = None,
    cli_args: Optional[Sequence[str]] = None,
    env: Optional[dict] = None,
    scratch_dir: Union[str, pathlib.Path, None] = None,
    team_dir: Union[str, pathlib.Path, None] = None,
) -> ParityResult:
    """Assert both halves for one ``module:`` row; see the module docstring."""
    target = parity_target(row)
    response = assert_route_reaches_function(client, row, json=json, params=params)
    cli = assert_cli_reaches_function(
        row, cli_args=cli_args, env=env, scratch_dir=scratch_dir, team_dir=team_dir
    )
    return ParityResult(target, response, cli)
