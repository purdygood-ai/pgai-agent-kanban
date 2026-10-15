"""
test_rest_container_image_selection.py
======================================
The optional ``image`` body field on POST /operations/deep-verify and
POST /operations/cold-build-now: the REST form of the container tier's
``--image`` flag.

Behaviour exercised, on both routes:

  1. ``image`` maps onto the dispatched script's argv as one
     ``--image <entry>`` pair per entry, in order: ``["debian"]`` gives
     ``--image debian``, ``"all"`` gives ``--image all``, a two-name list
     gives two pairs.
  2. A name team/config/container-images.txt does not declare is refused
     before anything is spawned -- on a ``dry_run`` and on a real call --
     with the harness's own machine key ``container_tier.unknown_image``
     and a non-empty message and remedy.
  3. A body without ``image`` (or with an empty list) dispatches exactly
     the argv the route built before the field existed, so the script's
     own fallback applies: kanban.cfg [release] container_images, else
     list the images and build nothing.
  4. No body field grants the container tier's operator authorization:
     with PGAI_CONTAINER_TIER unset in the server's environment a call
     carrying ``image`` (and a stray ``operator_approved``) is SKIPPED by
     the harness, and ``--operator-approved`` never reaches the argv.
  5. End to end through the real scripts with a stubbed cold lane, the
     selection reaches the lane as ``--image`` flags, and docker/podman
     stubs on PATH record zero container-engine calls.

The library the routes validate with (pgai_agent_kanban.lib.container_images)
is covered directly as well: the manifest parse rule and the request
vocabulary container_tier.sh uses. A parity test drives the harness's own
``container_tier_select_images`` (sourced from container_tier.sh, no engine,
no script run) and the library with the same ``--image`` entries -- empty
comma slots, leading and trailing commas, whitespace-only entries, tabs --
and asserts the same decision: refused (naming the same undeclared names),
nothing selected, or the same images selected.

Fixture split (as in test_rest_twins_action_verbs.py): the API server's
kanban_root is the real team/ dev tree, so routes dispatch the production
scripts and read the production manifest, while PGAI_AGENT_KANBAN_ROOT_PATH
points every script at a tmp_path sandbox for data.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import time
from typing import Generator
from unittest.mock import patch

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_REPO_ROOT = _TEAM_DIR.parent

_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)

from pgai_agent_kanban.lib import container_images  # noqa: E402

_ROUTES = {
    "/operations/deep-verify": "deep-verify.sh",
    "/operations/cold-build-now": "cold-build-now.sh",
}
_PROJECT = "proj"
_REFUSAL_KEYS = ("error", "message", "remedy")

# Stub cold lane: records the argv deep-verify.sh hands it and the selection
# it exported, then reports a green build. No engine command anywhere.
_STUB_LANE = """\
#!/usr/bin/env bash
printf 'argv=%s\\n' "$*" >> "{lane_log}"
printf 'selection=%s\\n' "${{PGAI_CONTAINER_IMAGES:-}}" >> "{lane_log}"
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""

# docker/podman stand-ins placed first on PATH: any engine call is recorded.
_STUB_ENGINE = """\
#!/usr/bin/env bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "{engine_log}"
exit 0
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, pathlib.Path]:
    """A sandbox kanban root with one registered project, a stubbed cold lane
    and recording docker/podman stubs, under operator authorization."""
    root = tmp_path / "root"
    (root / "projects" / _PROJECT).mkdir(parents=True)
    (root / "projects.cfg").write_text(f"[project:{_PROJECT}]\npriority=1\n\n", encoding="utf-8")

    lane_log = tmp_path / "lane.log"
    lane = tmp_path / "stub_cold_lane.sh"
    lane.write_text(_STUB_LANE.format(lane_log=lane_log), encoding="utf-8")
    lane.chmod(0o755)

    engine_log = tmp_path / "engine.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for engine in ("docker", "podman"):
        stub = bin_dir / engine
        stub.write_text(_STUB_ENGINE.format(engine_log=engine_log), encoding="utf-8")
        stub.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(root))
    monkeypatch.setenv("KANBAN_ROOT", str(root))
    monkeypatch.setenv("PGAI_AGENT_KANBAN_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)
    monkeypatch.delenv("PGAI_CONTAINER_IMAGES", raising=False)
    monkeypatch.setenv("PGAI_DEEP_VERIFY_COLD_LANE_CMD", str(lane))
    monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(_REPO_ROOT))
    # The stubbed lane stands in for the container tier, which runs only
    # with the operator's authorization in the server's environment and
    # never inside an agent wake.
    monkeypatch.setenv("PGAI_CONTAINER_TIER", "1")
    monkeypatch.delenv("PGAI_AGENT_WAKE_ACTIVE", raising=False)
    return {"root": root, "lane_log": lane_log, "engine_log": engine_log}


@pytest.fixture
def client(sandbox: dict[str, pathlib.Path]) -> Generator:
    """A TestClient whose routes dispatch the real team/ scripts."""
    from fastapi.testclient import TestClient

    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    cfg = ApiConfig(host="127.0.0.1", port=0, kanban_root=_TEAM_DIR)
    with TestClient(create_app(cfg=cfg), raise_server_exceptions=True) as c:
        yield c


def _set_cfg_default(root: pathlib.Path, value: str) -> None:
    with (root / "kanban.cfg").open("a", encoding="utf-8") as cfg:
        cfg.write(f"\n[release]\ncontainer_images = {value}\n")


def _log_path_from(stdout: str) -> pathlib.Path:
    """Return the detached run's log file the 'started' narration names."""
    match = re.search(r"captured to (\S+?)\.?$", stdout.strip())
    assert match, f"the started narration names no log file: {stdout!r}"
    return pathlib.Path(match.group(1))


def _wait_for_text(path: pathlib.Path, needle: str, timeout: float = 20.0) -> str:
    """Poll *path* until it contains *needle*; return its text either way."""
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                return text
        time.sleep(0.2)
    return text


def _assert_no_engine_calls(engine_log: pathlib.Path) -> None:
    assert not engine_log.exists(), (
        "a container engine was invoked; recorded calls:\n"
        + engine_log.read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# 1. image -> --image argv (dry_run reports the planned argv)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize(
    ("image", "expected_tail"),
    [
        (["debian"], "--image debian"),
        ("all", "--image all"),
        (["debian", "rocky9"], "--image debian --image rocky9"),
    ],
    ids=["one-name", "all", "two-names"],
)
def test_image_field_becomes_one_image_flag_per_entry(
    client, sandbox, route: str, image, expected_tail: str
) -> None:
    with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
        resp = client.post(route, json={"project": _PROJECT, "image": image, "dry_run": True})
        mock_popen.assert_not_called()
        mock_run.assert_not_called()

    assert resp.status_code == 200, resp.json()
    expected = f"dry-run: would execute {_ROUTES[route]} --project {_PROJECT} {expected_tail}"
    assert resp.json()["stdout"] == expected, resp.json()
    _assert_no_engine_calls(sandbox["engine_log"])


# ---------------------------------------------------------------------------
# 2. Undeclared names are refused before anything is spawned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real-call"])
def test_undeclared_image_is_refused_before_dispatch(
    client, sandbox, route: str, dry_run: bool
) -> None:
    body = {"project": _PROJECT, "image": ["debian", "no-such-image"], "dry_run": dry_run}
    with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
        resp = client.post(route, json=body)
        mock_popen.assert_not_called()
        mock_run.assert_not_called()

    assert resp.status_code == 422, resp.text
    payload = resp.json()
    for key in _REFUSAL_KEYS:
        assert isinstance(payload.get(key), str) and payload[key].strip(), payload
    assert payload["error"] == "container_tier.unknown_image"
    assert "'no-such-image'" in payload["message"], payload["message"]
    assert "'debian'" not in payload["message"], (
        "only the undeclared name should be named as refused: " + payload["message"]
    )
    for declared in container_images.declared_container_images(
        container_images.manifest_path(_TEAM_DIR)
    ):
        assert declared in payload["message"], payload["message"]
    assert payload["remedy"].startswith("curl "), payload["remedy"]
    assert route in payload["remedy"] and '"image": "all"' in payload["remedy"], payload["remedy"]
    assert not (sandbox["root"] / "projects" / _PROJECT / "deep-verify-ledger.md").exists()
    _assert_no_engine_calls(sandbox["engine_log"])


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real-call"])
@pytest.mark.parametrize(
    "image",
    ["debian,,rocky9", [",debian"]],
    ids=["empty-slot-between-commas", "leading-comma"],
)
def test_entry_with_an_empty_comma_slot_is_refused_before_dispatch(
    client, sandbox, route: str, dry_run: bool, image
) -> None:
    """An empty comma slot is an empty name the harness refuses, so the
    route refuses it too, before anything is spawned."""
    body = {"project": _PROJECT, "image": image, "dry_run": dry_run}
    with patch("subprocess.Popen") as mock_popen, patch("subprocess.run") as mock_run:
        resp = client.post(route, json=body)
        mock_popen.assert_not_called()
        mock_run.assert_not_called()

    assert resp.status_code == 422, resp.text
    payload = resp.json()
    for key in _REFUSAL_KEYS:
        assert isinstance(payload.get(key), str) and payload[key].strip(), payload
    assert payload["error"] == "container_tier.unknown_image"
    assert "''" in payload["message"], payload["message"]
    assert not (sandbox["root"] / "projects" / _PROJECT / "deep-verify-ledger.md").exists()
    _assert_no_engine_calls(sandbox["engine_log"])


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_entry_naming_no_image_is_refused(client, route: str) -> None:
    with patch("subprocess.Popen") as mock_popen:
        resp = client.post(route, json={"project": _PROJECT, "image": ""})
        mock_popen.assert_not_called()
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "container_tier.unknown_image"


# ---------------------------------------------------------------------------
# 3. Omitting image keeps the argv, and so the script's own fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(_ROUTES))
@pytest.mark.parametrize("body_extra", [{}, {"image": []}], ids=["omitted", "empty-list"])
def test_body_without_image_dispatches_the_unchanged_argv(
    client, route: str, body_extra: dict
) -> None:
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 4242
        resp = client.post(route, json={"project": _PROJECT, **body_extra})
        assert mock_popen.call_count == 1
        argv = mock_popen.call_args.args[0]

    assert resp.status_code == 200, resp.json()
    assert argv == [str(_TEAM_DIR / "scripts" / _ROUTES[route]), "--project", _PROJECT], argv


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_omitted_image_with_no_default_lists_images_and_builds_nothing(
    client, sandbox, route: str
) -> None:
    resp = client.post(route, json={"project": _PROJECT})
    assert resp.status_code == 200, resp.json()

    log = _log_path_from(resp.json()["stdout"])
    text = _wait_for_text(log, "nothing was built")
    assert "no container image selected -- nothing was built" in text, text
    assert not sandbox["lane_log"].exists(), "the lane ran although no image was selected"
    assert not (sandbox["root"] / "projects" / _PROJECT / "deep-verify-ledger.md").exists()
    _assert_no_engine_calls(sandbox["engine_log"])


def test_omitted_image_falls_back_to_the_kanban_cfg_default(client, sandbox) -> None:
    _set_cfg_default(sandbox["root"], "rocky9")
    resp = client.post("/operations/deep-verify", json={"project": _PROJECT})
    assert resp.status_code == 200, resp.json()

    lane_text = _wait_for_text(sandbox["lane_log"], "selection=")
    assert "argv=--image rocky9" in lane_text, lane_text
    assert "selection=rocky9" in lane_text, lane_text
    _assert_no_engine_calls(sandbox["engine_log"])


# ---------------------------------------------------------------------------
# 4. No body field grants operator authorization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_image_field_does_not_authorize_the_container_tier(
    client, sandbox, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    monkeypatch.delenv("PGAI_CONTAINER_TIER", raising=False)
    body = {"project": _PROJECT, "image": "all", "operator_approved": True}

    preview = client.post(route, json={**body, "dry_run": True})
    assert preview.status_code == 200, preview.json()
    assert "--operator-approved" not in preview.json()["stdout"], preview.json()

    resp = client.post(route, json=body)
    assert resp.status_code == 200, resp.json()
    assert any("operator_approved" in w for w in resp.json()["warnings"]), resp.json()

    log = _log_path_from(resp.json()["stdout"])
    text = _wait_for_text(log, "SKIPPED")
    assert "SKIPPED key=container_tier.operator_only" in text, text
    assert not sandbox["lane_log"].exists(), "the lane ran without operator authorization"
    assert not (sandbox["root"] / "projects" / _PROJECT / "deep-verify-ledger.md").exists()
    _assert_no_engine_calls(sandbox["engine_log"])


# ---------------------------------------------------------------------------
# 5. End to end: the selection reaches the lane, with zero engine calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "image", "lane_argv", "selection"),
    [
        ("/operations/deep-verify", ["debian"], "--image debian", "debian"),
        ("/operations/cold-build-now", ["rocky9", "debian"], "--image debian --image rocky9", "debian rocky9"),
        ("/operations/cold-build-now", "all", "--image debian --image rocky9", "debian rocky9"),
    ],
    ids=["deep-verify-one", "cold-build-now-two", "cold-build-now-all"],
)
def test_selected_images_reach_the_lane_and_the_ledger_records_the_run(
    client, sandbox, route: str, image, lane_argv: str, selection: str
) -> None:
    # A kanban.cfg default the field must override.
    _set_cfg_default(sandbox["root"], "no-default-should-apply")
    resp = client.post(route, json={"project": _PROJECT, "image": image})
    assert resp.status_code == 200, resp.json()
    assert "started for proj" in resp.json()["stdout"], resp.json()

    lane_text = _wait_for_text(sandbox["lane_log"], "selection=")
    assert f"argv={lane_argv}\n" in lane_text, lane_text
    assert f"selection={selection}\n" in lane_text, lane_text

    ledger = sandbox["root"] / "projects" / _PROJECT / "deep-verify-ledger.md"
    ledger_text = _wait_for_text(ledger, "Verdict: built")
    assert "Verdict: built" in ledger_text, ledger_text
    _assert_no_engine_calls(sandbox["engine_log"])


# ---------------------------------------------------------------------------
# The shared library: manifest parse rule and request vocabulary
# ---------------------------------------------------------------------------


class TestContainerImagesLibrary:
    def test_manifest_parse_skips_comments_blanks_and_repeats(self, tmp_path: pathlib.Path) -> None:
        manifest = container_images.manifest_path(tmp_path)
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            "# header\n\n  debian  \nrocky9\n#rocky8\ndebian\n", encoding="utf-8"
        )
        assert container_images.declared_container_images(manifest) == ["debian", "rocky9"]

    def test_missing_manifest_declares_nothing(self, tmp_path: pathlib.Path) -> None:
        manifest = container_images.manifest_path(tmp_path)
        assert container_images.declared_container_images(manifest) == []
        assert container_images.undeclared_image_names(["debian"], []) == ["debian"]

    def test_the_shipped_manifest_is_the_one_the_harness_reads(self) -> None:
        shipped = container_images.manifest_path(_TEAM_DIR)
        assert shipped == _TEAM_DIR / "config" / "container-images.txt"
        assert container_images.declared_container_images(shipped), shipped

    @pytest.mark.parametrize(
        ("entries", "expected"),
        [
            (["debian"], ["debian"]),
            (["debian,rocky9"], ["debian", "rocky9"]),
            (["debian rocky9", "all"], ["debian", "rocky9", "all"]),
            ([""], [""]),
            ([" , "], [""]),
            ([], []),
        ],
    )
    def test_request_entries_split_like_the_harness(self, entries, expected) -> None:
        assert container_images.split_image_request(entries) == expected

    def test_all_and_declared_names_are_accepted_others_are_named(self) -> None:
        declared = ["debian", "rocky9"]
        assert container_images.undeclared_image_names(["all"], declared) == []
        assert container_images.undeclared_image_names(["rocky9,debian"], declared) == []
        assert container_images.undeclared_image_names(
            ["debian", "nope,rocky9", ""], declared
        ) == ["nope", ""]


# ---------------------------------------------------------------------------
# Parity: the library decides exactly as the harness's own selection step
# ---------------------------------------------------------------------------

_CONTAINER_TIER_LIB = _TEAM_DIR / "scripts" / "lib" / "container_tier.sh"

# Sources the harness library, optionally points it at a fixture manifest,
# and runs the two functions a container-tier script runs on its argv. No
# script is executed and no engine command exists on this path: the
# selection step either refuses (exit 3), lists the images because nothing
# was selected (exit 0, "nothing was built"), or returns, and the selection
# is printed.
_HARNESS_DRIVER = """\
set -u
source "$CT_LIB"
if [[ -n "${CT_MANIFEST:-}" ]]; then CONTAINER_TIER_MANIFEST="$CT_MANIFEST"; fi
container_tier_take_args --project proj "$@"
container_tier_select_images /fake/deep-verify.sh --project proj "$@"
printf 'SELECTED=%s\\n' "${CONTAINER_TIER_IMAGES[*]}"
"""

# One --image entry list per case; "a" and "b" stand for the first and
# second declared image of the manifest under test.
_PARITY_CASES = [
    ["a,,b"],
    [",a"],
    ["a,"],
    [" "],
    [" , "],
    ["a , b"],
    ["a\tb"],
    [""],
    ["a,,"],
    [",,"],
    ["a, ,b"],
    [",a,"],
    ["a  b"],
    ["\ta,b "],
    ["a\nzzz"],
    ["all"],
    ["a,nope"],
    ["a", " "],
    [" ", "\t"],
    ["a,", ",b"],
    ["b", "a"],
]


def _harness_decision(
    entries: list[str], tmp_path: pathlib.Path, manifest: "pathlib.Path | None"
) -> tuple[str, list[str]]:
    """Run the harness's selection step on *entries*; return its decision.

    ``("refused", names)`` -- exit 3 with ``container_tier.unknown_image``,
    *names* the undeclared names its message quotes; ``("none", [])`` -- the
    nothing-selected listing; ``("selected", images)`` -- the selection.
    """
    import subprocess

    argv: list[str] = []
    for entry in entries:
        argv.extend(["--image", entry])
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "CT_LIB": str(_CONTAINER_TIER_LIB),
        "PGAI_AGENT_KANBAN_ROOT_PATH": str(tmp_path / "root"),
        "PGAI_AGENT_KANBAN_TEMP_DIR": str(tmp_path / "tmp"),
    }
    if manifest is not None:
        env["CT_MANIFEST"] = str(manifest)
    proc = subprocess.run(
        ["bash", "-c", _HARNESS_DRIVER, "driver", *argv],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = proc.stdout + proc.stderr
    if proc.returncode == 3:
        assert "container_tier.unknown_image" in output, output
        match = re.search(r"names ((?:'[^']*' ?)+), which", output)
        assert match, f"the refusal names no image: {output!r}"
        return "refused", re.findall(r"'([^']*)'", match.group(1))
    assert proc.returncode == 0, f"exit {proc.returncode}: {output!r}"
    if "no container image selected -- nothing was built" in proc.stdout:
        return "none", []
    match = re.search(r"^SELECTED=(.*)$", proc.stdout, re.MULTILINE)
    assert match, f"the selection step neither refused nor selected: {output!r}"
    return "selected", match.group(1).split()


def _library_decision(entries: list[str], declared: list[str]) -> tuple[str, list[str]]:
    """The same decision, computed by pgai_agent_kanban.lib.container_images."""
    unknown = container_images.undeclared_image_names(entries, declared)
    if unknown:
        return "refused", unknown
    names = container_images.split_image_request(entries)
    if not names:
        return "none", []
    wanted = set(declared) if container_images.SELECT_ALL in names else set(names)
    return "selected", [name for name in declared if name in wanted]


def _case_id(entries: list[str]) -> str:
    return "+".join(repr(entry) for entry in entries)


@pytest.mark.parametrize("entries", _PARITY_CASES, ids=[_case_id(c) for c in _PARITY_CASES])
def test_library_decides_like_the_harness_on_a_fixture_manifest(
    tmp_path: pathlib.Path, entries: list[str]
) -> None:
    manifest = container_images.manifest_path(tmp_path / "fixture")
    manifest.parent.mkdir(parents=True)
    manifest.write_text("a\nb\n", encoding="utf-8")
    declared = container_images.declared_container_images(manifest)

    expected = _harness_decision(entries, tmp_path, manifest)
    assert _library_decision(entries, declared) == expected, entries


@pytest.mark.parametrize("entries", _PARITY_CASES, ids=[_case_id(c) for c in _PARITY_CASES])
def test_library_decides_like_the_harness_on_the_shipped_manifest(
    tmp_path: pathlib.Path, entries: list[str]
) -> None:
    declared = container_images.declared_container_images(
        container_images.manifest_path(_TEAM_DIR)
    )
    assert len(declared) >= 2, declared
    first, second = declared[0], declared[1]
    # Swap the stand-ins "a" and "b" for the two declared names, leaving
    # words that merely contain those letters ("all", "nope") alone.
    stand_in = re.compile(r"(?<![A-Za-z])([ab])(?![A-Za-z])")
    real = [
        stand_in.sub(lambda m: first if m.group(1) == "a" else second, entry)
        for entry in entries
    ]

    expected = _harness_decision(real, tmp_path, None)
    assert _library_decision(real, declared) == expected, real
