"""lib/config.py — Read PGAI kanban configuration from cfg files and env vars.

Precedence when get_config() is called with no explicit kanban_root
(lowest to highest):
    1. Defaults baked in here
    2. User-wide ~/.config/pgai-kanban.cfg
    3. Per-install $KANBAN_ROOT/config.cfg
    4. Environment variables (always win)

When get_config() is called WITH an explicit kanban_root, that argument
gets a precedence layer of its own, above defaults, user-wide config, and
environment variables, for the None-defaulted PGAI_*_DIR keys (the ones
derived from the root rather than independently configured): each such
key is re-derived from the explicit kanban_root, so a caller that names a
directory gets that directory back regardless of how the ambient
environment — PGAI_PROJECT_ROOT and a user-wide ~/.config/pgai-kanban.cfg
entry included — happens to be configured. The one layer this does NOT
outrank is a per-install $KANBAN_ROOT/config.cfg: that file is read from
Path(kanban_root) / "config.cfg", i.e. from inside the very root the
caller named, so it is root-local data the caller placed there rather
than ambient environment, and it still applies on top of the explicit
root the same way it does in the no-override case. Keys that are not
root-derived (PGAI_CLEANUP_RETENTION_DAYS) are unaffected by an explicit
kanban_root and keep following the precedence above.
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
        Merged configuration. When kanban_root is None, env vars take the
        highest precedence, as documented in the module docstring. When
        kanban_root is given explicitly, it outranks PGAI_PROJECT_ROOT
        scoping, a user-wide config.cfg entry, and the matching per-key
        environment variable for the None-defaulted PGAI_*_DIR keys —
        but not a per-install config.cfg living inside that same root,
        which still applies — see the module docstring.
    """
    explicit_root = kanban_root is not None
    if kanban_root is None:
        # Resolve canonical var first, new-path as default.
        kanban_root = (
            os.environ.get("PGAI_AGENT_KANBAN_ROOT_PATH")
            or str(Path.home() / "pgai_agent_kanban")
        )
    kanban_root = str(kanban_root)

    suffix_map = {
        "PGAI_REQUIREMENTS_DIR": "requirements",
        "PGAI_PRIORITY_DIR": "tasks/queues/priority",
        "PGAI_ARCHIVE_DIR": "tasks/archive",
        "PGAI_BRIEFS_DIR": "briefs",
        "PGAI_TASKS_DIR": "tasks",
        "PGAI_LOGS_DIR": "logs",
    }

    # Build relative-to-root defaults
    defaults = dict(_DEFAULTS)
    for key, val in defaults.items():
        if val is None:
            defaults[key] = str(Path(kanban_root) / suffix_map[key])

    # In the multi-project layout, runtime state (tasks, requirements, etc.)
    # lives under projects/<name>/, not at kanban root. The wake script exports
    # PGAI_PROJECT_ROOT pointing at the project directory; when present, prefer
    # project-scoped paths for runtime state over the kanban-root-scoped defaults.
    #
    # This scoping is itself an ambient-environment source (PGAI_PROJECT_ROOT
    # is a wake-session env var, not caller input), so it is skipped entirely
    # when the caller passed an explicit kanban_root — an explicit override
    # outranks ambient environment for the keys derived from it, and stays
    # anchored to the root the caller named. The kanban-root paths remain as
    # fallback for installs without a projects/ layout and for tests that do
    # not set PGAI_PROJECT_ROOT.
    project_root_env = os.environ.get("PGAI_PROJECT_ROOT", "").strip()
    if project_root_env and not explicit_root:
        for key, suffix in suffix_map.items():
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
    # User-wide config.cfg is ambient, home-directory-scoped configuration —
    # it is not data placed under the root the caller named. Skip it for the
    # None-defaulted PGAI_*_DIR keys when the caller passed an explicit
    # kanban_root, exactly as the environment loop below does, so a
    # cfg-file entry sitting in the user's home directory cannot silently
    # redirect a root-derived key any more than an env var can.
    for key, val in user_cfg.items():
        if key not in _DEFAULTS:
            continue
        if explicit_root and key in suffix_map:
            continue
        config[key] = val
    # Per-install config.cfg is read from Path(kanban_root) / "config.cfg" —
    # i.e. from inside the very root the caller named — so it is root-local
    # data rather than ambient environment. It always applies, explicit
    # kanban_root or not.
    config.update({k: v for k, v in install_cfg.items() if k in _DEFAULTS})
    # Env vars win — except for the None-defaulted PGAI_*_DIR keys when the
    # caller passed an explicit kanban_root. Those keys already skip
    # PGAI_PROJECT_ROOT scoping above; skipping their per-key environment
    # override here is what gives an explicit kanban_root its own
    # precedence layer above environment variables, so a caller that names
    # a directory always gets that directory back rather than a config that
    # claims one root while a derived directory silently points elsewhere.
    # Keys that are not root-derived (PGAI_CLEANUP_RETENTION_DAYS) are
    # unaffected and keep following env-vars-always-win either way.
    for key in _DEFAULTS:
        if explicit_root and key in suffix_map:
            continue
        env_val = os.environ.get(key)
        if env_val is not None:
            config[key] = env_val

    config["KANBAN_ROOT"] = kanban_root
    return config
