"""
config.py — API service configuration.

Reads the [api] section from the kanban.cfg INI file.  The kanban root is
resolved from the PGAI_AGENT_KANBAN_ROOT_PATH environment variable via the
canonical resolver in :mod:`pgai_agent_kanban.env`, which the api-server
launcher sources from shell-env before invoking uvicorn.

Configuration keys (all in [api] section of kanban.cfg):

    host   — address the uvicorn server binds to.
              Default: 127.0.0.1
              Must be a loopback address; the startup guard in main.py
              refuses to start if this resolves to anything else.

    port   — TCP port the uvicorn server listens on.
              Default: 8300

    shell_out_timeout_seconds — seconds a route's operator-script run
              (adapter.shell_out) may take before its process group is
              stopped and the route answers the shell_out.timeout refusal
              (HTTP 504). A non-positive or non-integer value reads as the
              default. Default: 300

Port precedence (highest first):

    1. ``PGAI_API_PORT`` environment variable, when set to an integer.
       A blank value reads as unset; a non-integer value is ignored with a
       one-line warning on stderr.
    2. ``[api] port`` in kanban.cfg (a non-integer value reads as absent).
    3. The built-in default, 8300.

This module is the only implementation of that precedence.  The server
(main.py) binds the port it returns, and team/scripts/api-server.sh asks
this same function which port to probe for status and stop, so the two
can never disagree.

Temp root (``kanban_temp_root``): the one resolver every route uses for the
files it writes or reads under the framework temp root -- the same answer
``pgai_temp_dir`` in team/scripts/lib/temp.sh gives: PGAI_AGENT_KANBAN_TEMP_DIR
when set (team/scripts/api-server.sh exports the value it resolved), else
kanban.cfg ``[paths] tmp_root`` / ``tmp_subdir`` under the kanban root, else
the documented last resort.

Usage:
    from pgai_agent_kanban.api.config import load_api_config, ApiConfig
    cfg = load_api_config()
    # cfg.host -> "127.0.0.1"
    # cfg.port -> 8300

Command line (what team/scripts/api-server.sh runs to learn the port):
    python3 -m pgai_agent_kanban.api.config port
        Print load_api_config().port. A kanban root or kanban.cfg the loader
        cannot resolve exits 1 with the loader's error on stderr.
"""

import os
import pathlib
import sys
from dataclasses import dataclass, field

# Run by file path (``python3 .../config.py``) or loaded from a file outside
# its package, this module has no parent package and the interpreter puts
# only its own directory on sys.path, so the package imports below would not
# resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.env import resolve_kanban_root  # noqa: E402
from pgai_agent_kanban.lib import ini  # noqa: E402

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8300
_CFG_FILENAME = "kanban.cfg"
_SECTION = "api"
_PORT_ENV_VAR = "PGAI_API_PORT"
_DEFAULT_SHELL_OUT_TIMEOUT_SECONDS = 300
_TEMP_DIR_ENV_VAR = "PGAI_AGENT_KANBAN_TEMP_DIR"
# anti-pattern-allowlist: 2 (justification: the literal is the resolver's documented last-resort temp root, the same one temp.sh's pgai_temp_dir falls back to)
_FALLBACK_TEMP_ROOT = "/tmp/pgai_kanban_tmp"


@dataclass
class ApiConfig:
    """Resolved API configuration values."""

    host: str = field(default=_DEFAULT_HOST)
    port: int = field(default=_DEFAULT_PORT)
    kanban_root: pathlib.Path = field(default_factory=lambda: pathlib.Path("."))
    shell_out_timeout_seconds: int = field(default=_DEFAULT_SHELL_OUT_TIMEOUT_SECONDS)


def kanban_temp_root(kanban_root: "pathlib.Path | str") -> pathlib.Path:
    """Return the framework temp root for *kanban_root*, as ``pgai_temp_dir`` resolves it.

    PGAI_AGENT_KANBAN_TEMP_DIR when set and non-blank; else ``<kanban_root>/
    kanban.cfg [paths] tmp_root`` joined with ``tmp_subdir`` when both are
    set; else the documented last-resort root. A value naming the
    filesystem root reads as the last resort. The directory is not created.
    """
    value = os.environ.get(_TEMP_DIR_ENV_VAR, "").strip()
    if not value:
        cfg = pathlib.Path(kanban_root) / _CFG_FILENAME
        tmp_root = tmp_subdir = ""
        if cfg.is_file():
            tmp_root = (ini.read_line_value(cfg, "paths", "tmp_root") or "").strip()
            tmp_subdir = (ini.read_line_value(cfg, "paths", "tmp_subdir") or "").strip()
        value = f"{tmp_root}/{tmp_subdir}" if tmp_root and tmp_subdir else _FALLBACK_TEMP_ROOT
    if os.path.realpath(value) == os.sep:
        value = _FALLBACK_TEMP_ROOT
    return pathlib.Path(value)


def _positive_int(raw: str, default: int) -> int:
    """*raw* as a positive integer, or *default* when it is not one."""
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _find_kanban_cfg(kanban_root: pathlib.Path) -> pathlib.Path | None:
    """Return the path to kanban.cfg, or None when not found.

    Resolution order (highest precedence first):
      1. ``<kanban_root>/kanban.cfg`` — normal live-install path.
      2. ``./kanban.cfg`` — working-directory fallback for development runs.

    Args:
        kanban_root: Resolved kanban root path from the canonical resolver.
    """
    candidate = kanban_root / _CFG_FILENAME
    if candidate.is_file():
        return candidate

    local_candidate = pathlib.Path(".") / _CFG_FILENAME
    if local_candidate.is_file():
        return local_candidate

    return None


def _port_env_override() -> int | None:
    """Return the port named by ``PGAI_API_PORT``, or None when it does not apply.

    None means "no override": the variable is unset, blank, or not an
    integer.  A non-integer value is reported on stderr (naming the value
    and the fallback) so an operator who mistyped it can see why the
    kanban.cfg port was used instead.
    """
    raw = os.environ.get(_PORT_ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(
            f"{_PORT_ENV_VAR}={raw!r} is not an integer port; ignoring it and "
            f"using kanban.cfg [{_SECTION}] port (default {_DEFAULT_PORT}). "
            f"Set it to a number, e.g. {_PORT_ENV_VAR}=8301, or unset it.",
            file=sys.stderr,
        )
        return None


def load_api_config() -> ApiConfig:
    """Read [api] host and port from kanban.cfg and return an ApiConfig.

    Resolves the kanban root through the canonical resolver
    (:func:`pgai_agent_kanban.env.resolve_kanban_root`), then reads the
    ``[api]`` section from ``<kanban_root>/kanban.cfg``.

    When kanban.cfg is absent or the [api] section is missing, all keys fall
    back to their defaults.  This makes the service startable with no
    configuration file present (useful for integration tests).

    ``PGAI_API_PORT``, when set to an integer, overrides the port from
    kanban.cfg (see the module docstring for the full precedence).

    Raises:
        RuntimeError: When ``PGAI_AGENT_KANBAN_ROOT_PATH`` is unset or empty.

    Returns:
        ApiConfig with host, port, and resolved kanban_root fields populated.
    """
    # The canonical resolver reads PGAI_AGENT_KANBAN_ROOT_PATH, absolutizes,
    # and raises RuntimeError with the fail-loud grammar when unset.
    kanban_root = resolve_kanban_root()

    port_override = _port_env_override()

    cfg_path = _find_kanban_cfg(kanban_root)
    if cfg_path is None:
        return ApiConfig(
            host=_DEFAULT_HOST,
            port=_DEFAULT_PORT if port_override is None else port_override,
            kanban_root=kanban_root,
        )

    parser = ini.load(cfg_path)

    host = _DEFAULT_HOST
    port = _DEFAULT_PORT
    shell_out_timeout = _DEFAULT_SHELL_OUT_TIMEOUT_SECONDS

    if parser.has_section(_SECTION):
        host = parser.get(_SECTION, "host", fallback=_DEFAULT_HOST).strip()
        port_raw = parser.get(_SECTION, "port", fallback=str(_DEFAULT_PORT)).strip()
        try:
            port = int(port_raw)
        except ValueError:
            # Non-integer port in config; fall back to default.
            port = _DEFAULT_PORT
        shell_out_timeout = _positive_int(
            parser.get(_SECTION, "shell_out_timeout_seconds", fallback=""),
            _DEFAULT_SHELL_OUT_TIMEOUT_SECONDS,
        )

    if port_override is not None:
        port = port_override

    return ApiConfig(host=host, port=port, kanban_root=kanban_root, shell_out_timeout_seconds=shell_out_timeout)


def main(argv: "list[str] | None" = None) -> int:
    """Command line: ``port`` prints the resolved API port; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args != ["port"]:
        print("usage: python3 -m pgai_agent_kanban.api.config port", file=sys.stderr)
        return 2
    print(load_api_config().port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
