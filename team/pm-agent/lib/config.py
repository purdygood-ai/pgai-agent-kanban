"""lib/config.py — Read PGAI kanban configuration from cfg files and env vars.

Precedence (lowest to highest):
    1. Defaults baked in here
    2. User-wide ~/.config/pgai-kanban.cfg
    3. Per-install $KANBAN_ROOT/config.cfg
    4. Environment variables (always win)
"""

import os
import re
from pathlib import Path


_DEFAULTS = {
    "PGAI_REQUIREMENTS_DIR": None,  # resolved relative to kanban_root if None
    "PGAI_PRIORITY_DIR": None,
    "PGAI_ARCHIVE_DIR": None,
    "PGAI_BRIEFS_DIR": None,
    "PGAI_TASKS_DIR": None,
    "PGAI_LOGS_DIR": None,
    "PGAI_CLEANUP_RETENTION_DAYS": "30",
}

_LINE_RE = re.compile(
    r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$'
)


def _parse_cfg(path):
    """Parse a bash-style KEY=value file; return dict of parsed entries."""
    result = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return result
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # Strip matching outer quotes
        if len(val) >= 2 and val[0] in ('"', "'") and val[-1] == val[0]:
            val = val[1:-1]
        result[key] = val
    return result


def get_config(kanban_root=None):
    """Return configuration dict with PGAI_* keys resolved from all sources.

    Parameters
    ----------
    kanban_root : str or None
        Override the kanban root path.  Defaults to PGAI_AGENT_KANBAN_ROOT_PATH
        (canonical) / ~/pgai_agent_kanban.

    Returns
    -------
    dict
        Merged configuration with env vars taking highest precedence.
    """
    if kanban_root is None:
        # Resolve canonical var first, new-path as default.
        kanban_root = (
            os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
            or str(Path.home() / "pgai_agent_kanban")
        )
    kanban_root = str(kanban_root)

    # Build relative-to-root defaults
    defaults = dict(_DEFAULTS)
    for key, val in defaults.items():
        if val is None:
            suffix_map = {
                "PGAI_REQUIREMENTS_DIR": "requirements",
                "PGAI_PRIORITY_DIR": "tasks/queues/priority",
                "PGAI_ARCHIVE_DIR": "tasks/archive",
                "PGAI_BRIEFS_DIR": "briefs",
                "PGAI_TASKS_DIR": "tasks",
                "PGAI_LOGS_DIR": "logs",
            }
            defaults[key] = str(Path(kanban_root) / suffix_map[key])

    # In the multi-project layout, runtime state (tasks, requirements, etc.)
    # lives under projects/<name>/, not at kanban root. The wake script exports
    # PGAI_PROJECT_ROOT pointing at the project directory; when present, prefer
    # project-scoped paths for runtime state over the kanban-root-scoped defaults.
    #
    # The kanban-root paths remain as fallback for installs without a projects/
    # layout and for tests that do not set PGAI_PROJECT_ROOT.
    project_root_env = os.environ.get("PGAI_PROJECT_ROOT", "").strip()
    if project_root_env:
        project_scoped_overrides = {
            "PGAI_REQUIREMENTS_DIR": "requirements",
            "PGAI_PRIORITY_DIR": "tasks/queues/priority",
            "PGAI_ARCHIVE_DIR": "tasks/archive",
            "PGAI_BRIEFS_DIR": "briefs",
            "PGAI_TASKS_DIR": "tasks",
            "PGAI_LOGS_DIR": "logs",
        }
        for key, suffix in project_scoped_overrides.items():
            # Only override if the default was None-derived (kanban-root-scoped).
            # Don't override values explicitly set in _DEFAULTS or already
            # specified in user/install configs.
            if defaults.get(key) == str(Path(kanban_root) / suffix):
                defaults[key] = str(Path(project_root_env) / suffix)

    # Layer 2: user-wide config
    user_cfg = _parse_cfg(Path.home() / ".config" / "pgai-kanban.cfg")

    # Layer 3: per-install config
    install_cfg = _parse_cfg(Path(kanban_root) / "config.cfg")

    # Merge: defaults -> user_cfg -> install_cfg -> env
    config = {}
    for key in _DEFAULTS:
        config[key] = defaults[key]
    config.update({k: v for k, v in user_cfg.items() if k in _DEFAULTS})
    config.update({k: v for k, v in install_cfg.items() if k in _DEFAULTS})
    # Env vars win
    for key in _DEFAULTS:
        env_val = os.environ.get(key)
        if env_val is not None:
            config[key] = env_val

    config["KANBAN_ROOT"] = kanban_root
    return config
