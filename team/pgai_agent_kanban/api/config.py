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

Usage:
    from pgai_agent_kanban.api.config import load_api_config, ApiConfig
    cfg = load_api_config()
    # cfg.host -> "127.0.0.1"
    # cfg.port -> 8300
"""

import configparser
import pathlib
from dataclasses import dataclass, field

from pgai_agent_kanban.env import resolve_kanban_root

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8300
_CFG_FILENAME = "kanban.cfg"
_SECTION = "api"


@dataclass
class ApiConfig:
    """Resolved API configuration values."""

    host: str = field(default=_DEFAULT_HOST)
    port: int = field(default=_DEFAULT_PORT)
    kanban_root: pathlib.Path = field(default_factory=lambda: pathlib.Path("."))


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


def load_api_config() -> ApiConfig:
    """Read [api] host and port from kanban.cfg and return an ApiConfig.

    Resolves the kanban root through the canonical resolver
    (:func:`pgai_agent_kanban.env.resolve_kanban_root`), then reads the
    ``[api]`` section from ``<kanban_root>/kanban.cfg``.

    When kanban.cfg is absent or the [api] section is missing, all keys fall
    back to their defaults.  This makes the service startable with no
    configuration file present (useful for integration tests).

    Raises:
        RuntimeError: When ``PGAI_AGENT_KANBAN_ROOT_PATH`` is unset or empty.

    Returns:
        ApiConfig with host, port, and resolved kanban_root fields populated.
    """
    # The canonical resolver reads PGAI_AGENT_KANBAN_ROOT_PATH, absolutizes,
    # and raises RuntimeError with the fail-loud grammar when unset.
    kanban_root = resolve_kanban_root()

    cfg_path = _find_kanban_cfg(kanban_root)
    if cfg_path is None:
        return ApiConfig(
            host=_DEFAULT_HOST,
            port=_DEFAULT_PORT,
            kanban_root=kanban_root,
        )

    parser = configparser.ConfigParser()
    parser.read(str(cfg_path), encoding="utf-8")

    host = _DEFAULT_HOST
    port = _DEFAULT_PORT

    if parser.has_section(_SECTION):
        host = parser.get(_SECTION, "host", fallback=_DEFAULT_HOST).strip()
        port_raw = parser.get(_SECTION, "port", fallback=str(_DEFAULT_PORT)).strip()
        try:
            port = int(port_raw)
        except ValueError:
            # Non-integer port in config; fall back to default.
            port = _DEFAULT_PORT

    return ApiConfig(host=host, port=port, kanban_root=kanban_root)
