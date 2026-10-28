"""
test_overwatch_no_model_call.py
===============================
OVERWATCH never asks a model to choose: no file under
team/pgai_agent_kanban/overwatch/ invokes a provider CLI, a wake/<provider>.sh
dispatch (or the wake-batch.sh / wake-now.sh agent wakes) or a model API.

The scan reads every file in the package in full -- every ``*.py`` file
through its syntax tree, every data file (the classifier and matrix YAML)
as text -- and reports each of these constructs:

  * a process launch (``subprocess.*``, ``os.system`` / ``popen`` /
    ``exec*`` / ``spawn*``, ``asyncio.create_subprocess_*``) whose program
    is a provider CLI -- a name with its own wake script under
    team/scripts/wake/, or another model CLI;
  * a wake dispatch script path (``wake/<name>.sh``, ``wake-batch.sh``,
    ``wake-now.sh``) or the provider dispatch entry point
    (``provider_dispatch``) in any code string, so a path built into a
    constant and launched later is caught too;
  * an import of a model SDK or an HTTP client (a model API call needs one);
  * a model API endpoint URL.

Docstrings and comments are prose about the package, not calls, and are not
scanned. A negative proof seeds each construct in a throwaway package and
confirms the scan reports it.
"""

from __future__ import annotations

import ast
import pathlib
import re

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_OVERWATCH_PKG = _TEAM_DIR / "pgai_agent_kanban" / "overwatch"
_WAKE_DIR = _TEAM_DIR / "scripts" / "wake"

#: Model CLIs with no wake script of their own in this tree.
_OTHER_MODEL_CLIS = frozenset({"gemini", "aider", "llm", "ollama"})

#: Modules a model API call goes through: model SDKs and HTTP clients.
_MODEL_API_MODULES = (
    "anthropic", "openai", "google.generativeai", "google.genai", "cohere",
    "mistralai", "litellm", "ollama", "requests", "httpx", "aiohttp",
    "urllib3", "urllib.request", "http.client",
)

_DISPATCH_PATH = re.compile(r"(?:^|[/\s\"'])wake/[A-Za-z0-9_.-]+\.sh\b|\bwake-(?:batch|now)\.sh\b|\bprovider_dispatch\b")
_MODEL_API_URL = re.compile(
    r"https?://[^\s\"']*(?:api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis\.com"
    r"|/v1/messages\b|/v1/chat/completions\b)"
)

_LAUNCHERS = {
    ("subprocess", name) for name in ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")
} | {
    ("os", name) for name in ("system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle",
                              "execlp", "execlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "spawnl",
                              "spawnle", "spawnlp", "spawnlpe", "posix_spawn", "posix_spawnp")
} | {("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell")}


def provider_clis() -> frozenset[str]:
    """Every provider with a wake script in this tree, plus other model CLIs."""
    return frozenset(path.stem for path in _WAKE_DIR.glob("*.sh")) | _OTHER_MODEL_CLIS


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _launcher(call: ast.Call) -> tuple[str, str] | None:
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        key = (func.value.id, func.attr)
        return key if key in _LAUNCHERS else None
    return None


def _program(call: ast.Call) -> str | None:
    """The program a launch call runs, when it is written as a literal."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        first = first.elts[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.strip():
        return pathlib.PurePath(first.value.split()[0]).name
    return None


def scan_python(path: pathlib.Path, providers: frozenset[str]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _launcher(node):
            program = _program(node)
            if program in providers:
                found.append(f"{path}:{node.lineno}: launches provider CLI {program!r}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(_MODEL_API_MODULES):
                    found.append(f"{path}:{node.lineno}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith(_MODEL_API_MODULES) or any(
                f"{node.module}.{alias.name}".startswith(_MODEL_API_MODULES) for alias in node.names
            ):
                found.append(f"{path}:{node.lineno}: imports from {node.module}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if _DISPATCH_PATH.search(node.value):
                found.append(f"{path}:{node.lineno}: names a wake dispatch: {node.value!r}")
            if _MODEL_API_URL.search(node.value):
                found.append(f"{path}:{node.lineno}: names a model API endpoint: {node.value!r}")
    return found


def scan_data(path: pathlib.Path) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if _DISPATCH_PATH.search(code) or _MODEL_API_URL.search(code):
            found.append(f"{path}:{number}: {line.strip()}")
    return found


def scan_package(package: pathlib.Path, providers: frozenset[str]) -> list[str]:
    found: list[str] = []
    for path in sorted(p for p in package.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        if path.suffix == ".py":
            found.extend(scan_python(path, providers))
        elif path.suffix in {".yaml", ".yml", ".json", ".sh", ".txt", ".cfg"}:
            found.extend(scan_data(path))
    return found


def test_provider_list_comes_from_the_wake_scripts() -> None:
    providers = provider_clis()
    assert {"claude", "codex"} <= providers, providers


def test_no_model_call_anywhere_in_the_overwatch_package() -> None:
    # Intentional pattern-scan: no current or future file in the overwatch
    # package may call a model -- OVERWATCH's verbs and their limits are code.
    python_files = sorted(_OVERWATCH_PKG.rglob("*.py"))
    assert len(python_files) > 10, f"the overwatch package was not found at {_OVERWATCH_PKG}"

    found = scan_package(_OVERWATCH_PKG, provider_clis())

    assert not found, "OVERWATCH must never call a model:\n" + "\n".join(found)


def test_the_scan_reports_every_construct_it_forbids(tmp_path: pathlib.Path) -> None:
    package = tmp_path / "overwatch"
    package.mkdir()
    (package / "planted.py").write_text(
        '"""Docstrings may say wake/claude.sh and subprocess.run([\'claude\']) freely."""\n'
        "import os\n"
        "import subprocess\n"
        "import anthropic\n"
        "from urllib import request\n"
        "WAKE = 'scripts/wake/codex.sh'\n"
        "subprocess.run(['claude', '-p', 'choose a verb'])\n"
        "subprocess.Popen(('/usr/local/bin/codex', 'exec'))\n"
        "os.system('gemini --prompt pick')\n"
        "subprocess.run(['bash', WAKE])\n"
        "subprocess.run(['scripts/wake-batch.sh', '--agent=overwatch'])\n"
        "URL = 'https://api.anthropic.com/v1/messages'\n",
        encoding="utf-8",
    )
    (package / "rows.yaml").write_text("# wake-now.sh in a comment is fine\nrun: wake-now.sh --agent pm\n",
                                       encoding="utf-8")
    (package / "clean.py").write_text(
        "import subprocess\nsubprocess.run(['git', 'status'])\n", encoding="utf-8"
    )

    found = scan_package(package, provider_clis())
    text = "\n".join(found)

    expected = [
        "launches provider CLI 'claude'",
        "launches provider CLI 'codex'",
        "launches provider CLI 'gemini'",
        "imports anthropic",
        "imports from urllib",
        "names a wake dispatch: 'scripts/wake/codex.sh'",
        "names a wake dispatch: 'scripts/wake-batch.sh'",
        "names a model API endpoint",
        "rows.yaml:2:",
    ]
    for fragment in expected:
        assert fragment in text, f"the scan missed {fragment!r}:\n{text}"
    assert "clean.py" not in text, text
    assert ":1:" not in text.replace("rows.yaml:2:", ""), f"a docstring or comment was reported:\n{text}"
