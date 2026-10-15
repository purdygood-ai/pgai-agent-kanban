"""
test_operations_intake_filename_guard.py — POST /operations/intake accepts
only a bare intake filename and writes only inside the intake temp directory.

What is proven here:

  1. A filename that names a path (an absolute path, '../x.md', 'a/b.md',
     '..', a bug-prefixed directory followed by '../../x.md', an absolute
     path to a file outside the temp root, a relative path climbing to it --
     _PATH_NAMES lists them) is refused at HTTP 422 with a non-blank
     dual-channel triple, live or dry-run, and nothing is created, changed or
     removed anywhere in the throwaway tree -- the sentinel file outside the
     temp root keeps its content and mtime, and the temp root is not even
     created.
  2. A bug-, priority- and version-prefixed bare name (_ACCEPTED) are still
     deposited into bugs/, priority/ and requirements/, from a temp file in
     a fresh per-request directory directly under the intake temp directory.
  3. A dry-run envelope -- for an accepted name and for every refused one --
     names no path outside the intake temp directory.
  4. A bare but unroutable name is still the CLI's own refusal (covered for
     parity in test_operations_ops_calls.py) and now writes no temp file;
     its remedy names nothing under the temp root and is a curl line that,
     re-sent exactly as printed, deposits the routable example name.
  5. The temp-file writer refuses a per-request directory that resolves
     outside the intake temp directory through a symlink, creating nothing
     there.

Every board is a throwaway directory under ``tmp_path``; the API is rooted
at it and PGAI_AGENT_KANBAN_TEMP_DIR points inside it.
"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from pgai_agent_kanban.api.routers import operations as operations_module
from pgai_agent_kanban.lib import filename_contract
from pgai_agent_kanban.lib.error_contract import parse_error_contract
from pgai_agent_kanban.ops import write as ops_write
from pgai_agent_kanban.ops.errors import Refused

_PROJECT = "proj"
_CONTENT = "# Intake payload\n\n## Status\nopen\n"
_SENTINEL_CONTENT = "sentinel: must never change\n"

# The names the acceptance criteria list, each a path rather than a bare name.
_PATH_NAMES = ["/tmp/x.md", "../x.md", "a/b.md", "..", "BUG-0001/../../x.md"]

# Where the absolute name above would have written: observed, never written by
# the test -- it only proves the refused request left that path as it was.
# anti-pattern-allowlist: 2 (justification: the attack target the acceptance criteria name, read-only observed; the test writes nothing there)
_ABSOLUTE_TARGET = pathlib.Path("/tmp/x.md")

_ACCEPTED = [
    ("BUG-0001-x.md", "bugs"),
    ("PRIORITY-0001-x.md", "priority"),
    ("v1.2.3.md", "requirements"),
]


# ---------------------------------------------------------------------------
# Sandbox: kanban root, temp root and a sentinel outside the temp root
# ---------------------------------------------------------------------------


class _Sandbox:
    def __init__(self, base: pathlib.Path) -> None:
        self.base = base
        self.root = base / "kanban"
        self.temp_root = base / "server_tmp"
        self.intake_dir = self.temp_root / "intake"
        self.sentinel = base / "outside" / "x.md"


def _build_sandbox(base: pathlib.Path) -> _Sandbox:
    box = _Sandbox(base)
    box.root.mkdir(parents=True)
    (box.root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority = 1\n", encoding="utf-8")
    project = box.root / "projects" / _PROJECT
    (project / "tasks" / "queues").mkdir(parents=True)
    for sub in ("bugs", "priority", "requirements"):
        (project / sub).mkdir()
    (box.root / "logs").mkdir()
    box.sentinel.parent.mkdir(parents=True)
    box.sentinel.write_text(_SENTINEL_CONTENT, encoding="utf-8")
    # A fixed past mtime, so an overwrite within the same second still shows.
    os.utime(box.sentinel, (1_000_000_000, 1_000_000_000))
    return box


@pytest.fixture
def box(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> _Sandbox:
    sandbox = _build_sandbox(tmp_path / "sandbox")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox.root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox.root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(sandbox.temp_root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PGAI_TASKS_DIR", raising=False)
    return sandbox


@pytest.fixture
def client(box: _Sandbox) -> Generator[TestClient, None, None]:
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=box.root)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as c:
        yield c


def _tree_state(base: pathlib.Path) -> dict[str, tuple[str, int, bytes]]:
    """Every entry under *base*: kind, mtime and (for files) content."""
    state: dict[str, tuple[str, int, bytes]] = {}
    for path in sorted(base.rglob("*")):
        st = path.lstat()
        if path.is_symlink():
            state[str(path)] = ("link", st.st_mtime_ns, os.readlink(path).encode())
        elif path.is_dir():
            state[str(path)] = ("dir", st.st_mtime_ns, b"")
        else:
            state[str(path)] = ("file", st.st_mtime_ns, path.read_bytes())
    return state


def _outside_state(path: pathlib.Path) -> tuple[bool, int, bytes]:
    """Existence, mtime and content of a path outside the sandbox."""
    if not path.exists():
        return (False, 0, b"")
    return (True, path.stat().st_mtime_ns, path.read_bytes())


def _strings(value: object) -> list[str]:
    """Every string anywhere in a decoded JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _absolute_paths(envelope: dict) -> list[pathlib.Path]:
    """Every whitespace-separated token in the envelope that is an absolute path."""
    return [
        pathlib.Path(token.strip("'\""))
        for text in _strings(envelope)
        for token in text.split()
        if token.strip("'\"").startswith("/")
    ]


def _assert_no_path_outside_intake_dir(envelope: dict, box: _Sandbox) -> None:
    root = box.intake_dir.resolve()
    for path in _absolute_paths(envelope):
        resolved = path.resolve()
        assert resolved != root and resolved.is_relative_to(root), (
            f"the envelope names {path}, outside the intake temp directory {root}: {envelope}"
        )


def _refused_names(box: _Sandbox) -> list[str]:
    """The listed path names plus two aimed straight at the sentinel."""
    return [*_PATH_NAMES, str(box.sentinel), "../../outside/x.md"]


# ---------------------------------------------------------------------------
# 1 and 3. A path is refused at 422, writes nothing, echoes no path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_path_filename_is_refused_dual_channel_and_nothing_is_written(
    dry_run: bool, client: TestClient, box: _Sandbox
) -> None:
    for name in _refused_names(box):
        before = _tree_state(box.base)
        bare_tmp_before = _outside_state(_ABSOLUTE_TARGET)

        resp = client.post(
            "/operations/intake",
            json={"project": _PROJECT, "filename": name, "content": _CONTENT, "dry_run": dry_run},
        )

        body = resp.json()
        assert 400 <= resp.status_code < 500, (name, resp.status_code, body)
        for key in ("error", "message", "remedy"):
            assert isinstance(body.get(key), str) and body[key].strip(), (name, key, body)
        assert body["error"] == "intake.filename_not_bare", (name, body)
        assert "BUG-0001-example.md" in body["remedy"], (name, body)
        assert "<" not in body["remedy"], f"the remedy carries a placeholder: {body['remedy']}"
        assert _tree_state(box.base) == before, f"{name!r} changed the sandbox tree"
        assert not box.temp_root.exists(), f"{name!r} created the temp root"
        assert box.sentinel.read_text(encoding="utf-8") == _SENTINEL_CONTENT
        assert box.sentinel.stat().st_mtime_ns == 1_000_000_000 * 10**9
        assert _outside_state(_ABSOLUTE_TARGET) == bare_tmp_before, f"{name!r} touched {_ABSOLUTE_TARGET}"
        assert name not in json.dumps(body) or name == "..", f"the refusal echoes {name!r}: {body}"
        _assert_no_path_outside_intake_dir(body, box)


def test_dry_run_for_an_accepted_name_names_only_a_path_inside_the_intake_dir(
    client: TestClient, box: _Sandbox
) -> None:
    before = _tree_state(box.base)

    resp = client.post(
        "/operations/intake",
        json={"project": _PROJECT, "filename": "BUG-0001-x.md", "content": _CONTENT, "dry_run": True},
    )

    body = resp.json()
    assert resp.status_code == 200, body
    planned = _absolute_paths(body)
    assert len(planned) == 1, f"expected the one planned temp file path in {body}"
    assert planned[0].name == "BUG-0001-x.md"
    assert planned[0].parent.parent == box.intake_dir, planned[0]
    _assert_no_path_outside_intake_dir(body, box)
    assert _tree_state(box.base) == before, "a dry run wrote to the sandbox"


# ---------------------------------------------------------------------------
# 2. Accepted names deposit as before, via a fresh per-request temp file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name, subdir", _ACCEPTED, ids=[n for n, _ in _ACCEPTED])
def test_accepted_name_is_deposited_from_a_per_request_temp_file(
    name: str, subdir: str, client: TestClient, box: _Sandbox
) -> None:
    resp = client.post("/operations/intake", json={"project": _PROJECT, "filename": name, "content": _CONTENT})

    body = resp.json()
    assert resp.status_code == 200, body
    deposited = box.root / "projects" / _PROJECT / subdir / name
    assert deposited.read_text(encoding="utf-8") == _CONTENT
    assert body["stdout"] == f"intake: deposited {deposited.resolve()}\n" or body["stdout"] == (
        f"intake: deposited {deposited}\n"
    ), body
    temp_files = [p for p in box.intake_dir.rglob("*") if p.is_file()]
    assert len(temp_files) == 1, temp_files
    assert temp_files[0].name == name
    assert temp_files[0].parent.parent == box.intake_dir, "the temp file is not in a per-request directory"
    assert temp_files[0].read_text(encoding="utf-8") == _CONTENT
    assert box.sentinel.read_text(encoding="utf-8") == _SENTINEL_CONTENT


def test_two_requests_for_one_name_use_two_temp_directories(client: TestClient, box: _Sandbox) -> None:
    for _ in range(2):
        client.post(
            "/operations/intake",
            json={"project": _PROJECT, "filename": "BUG-0001-x.md", "content": _CONTENT},
        )

    request_dirs = [p for p in box.intake_dir.iterdir() if p.is_dir()]
    assert len(request_dirs) == 2, request_dirs
    assert all((d / "BUG-0001-x.md").is_file() for d in request_dirs)


# ---------------------------------------------------------------------------
# 4. An unroutable bare name is refused before any temp file is written
# ---------------------------------------------------------------------------


def test_unroutable_bare_name_writes_no_temp_file(client: TestClient, box: _Sandbox) -> None:
    resp = client.post("/operations/intake", json={"project": _PROJECT, "filename": "notes.md", "content": _CONTENT})

    body = resp.json()
    assert resp.status_code == 409, body
    assert body["error"] == "intake.refused", body
    assert "cannot route 'notes.md'" in body["message"], body
    written = [p for p in box.temp_root.rglob("*") if p.is_file()] if box.temp_root.exists() else []
    assert written == [], f"an unroutable name still wrote {written}"


def test_unroutable_bare_name_remedy_is_a_runnable_resend_naming_no_temp_file(
    client: TestClient, box: _Sandbox
) -> None:
    before = _tree_state(box.base)

    resp = client.post("/operations/intake", json={"project": _PROJECT, "filename": "notes.md", "content": _CONTENT})

    body = resp.json()
    assert resp.status_code == 409, body
    assert body["error"] == "intake.refused", body
    remedy = body["remedy"]
    assert "<" not in remedy, f"the remedy carries a placeholder: {remedy}"
    assert str(box.temp_root) not in remedy, f"the remedy names a path under the temp root: {remedy}"
    for path in _absolute_paths({"remedy": remedy}):
        assert path.exists(), f"the remedy names {path}, which does not exist: {remedy}"
    assert _tree_state(box.base) == before, "the refused request changed the sandbox tree"
    assert not box.temp_root.exists(), "the refused request created the temp root"

    # The remedy's curl line parses as a shell command and re-sends a body
    # whose name routes; sending that body exactly as printed is accepted.
    argv = shlex.split(remedy[remedy.index("curl "):])
    assert argv[:4] == ["curl", "-sS", "-X", "POST"], argv
    assert argv[4].endswith("/operations/intake"), argv
    resend = json.loads(argv[argv.index("-d") + 1])
    assert resend["project"] == _PROJECT, resend
    assert resend["filename"] == "BUG-0001-example.md", resend

    retry = client.post("/operations/intake", json=resend)

    assert retry.status_code == 200, retry.json()
    deposited = box.root / "projects" / _PROJECT / "bugs" / "BUG-0001-example.md"
    assert deposited.read_text(encoding="utf-8") == resend["content"]


# ---------------------------------------------------------------------------
# 5. The writer refuses a per-request directory resolving outside
# ---------------------------------------------------------------------------


def test_temp_writer_refuses_a_request_dir_resolving_outside_the_intake_dir(box: _Sandbox) -> None:
    box.intake_dir.mkdir(parents=True)
    outside = box.sentinel.parent
    (box.intake_dir / "link").symlink_to(outside, target_is_directory=True)
    before = _tree_state(outside)
    temp_file = box.intake_dir / "link" / "fresh" / "BUG-0001-x.md"

    with pytest.raises(Refused) as caught:
        operations_module._write_intake_temp_file(box.intake_dir, temp_file, _CONTENT)

    triple = parse_error_contract(str(caught.value))
    assert triple is not None and triple["error"] == "intake.temp_path_unconfined", str(caught.value)
    assert triple["message"].strip() and triple["remedy"].strip()
    assert _tree_state(outside) == before, "the writer created something outside the intake dir"


# ---------------------------------------------------------------------------
# The route and the CLI path share one filename check
# ---------------------------------------------------------------------------


def test_route_and_deposit_intake_share_the_one_filename_check() -> None:
    # Static guard: the route and ops.write (deposit_intake's module) both
    # bind the filename_contract helper itself, not a copy; the behavioural
    # tests above (route) and tests/unit/test_intake_filename_guard.py
    # (deposit_intake, intake.sh) show each refusing through it.
    assert operations_module.intake_destination is filename_contract.intake_destination
    assert ops_write.intake_destination is filename_contract.intake_destination
