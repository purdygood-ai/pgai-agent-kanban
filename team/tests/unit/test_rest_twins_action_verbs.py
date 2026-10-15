"""
test_rest_twins_action_verbs.py
================================
Acceptance-criteria tests for the REST twins fronting four mutating verbs
that had no HTTP surface: defer, undefer (the deferral-posture pair),
deep-verify (the DEEP-VERIFY tier's on-demand runner), and wake-now (fire an
agent's wake outside its cron cadence).

Acceptance criteria exercised:

  1. POST /operations/defer arms the same DEFER-LOW-BUGS campaign file, with
     the same contents, that defer.sh arms for the same arguments.
  2. POST /operations/undefer against an unarmed project returns success and
     states the posture was already clear, matching the CLI's idempotent
     behaviour.
  3. A malformed (and a missing) --until over HTTP returns the same stable
     machine error key defer.sh emits for the same input, byte-identical in
     stderr.
  4. POST /operations/deep-verify returns promptly (well before the
     underlying lane finishes) and its response never asserts a verdict the
     lane has not produced; the backgrounded run still completes and writes
     a real ledger record.
  5. POST /operations/wake-now with bg=true returns promptly without waiting
     for the fired agent's wake cycle to finish, and a test observes that the
     invocation actually happened.
  6. The operator verb manifest's absent-twin markers for these four verbs
     are flipped to real values (no "ABSENT" REST columns remain).

All fixture construction uses tmp_path (pytest-managed, redirected under
PGAI_AGENT_KANBAN_TEMP_DIR by the parent conftest.py's autouse fixture).  No
live kanban root is touched; the API server config's kanban_root points at
the real team/ dev tree by default so scripts resolve to production code,
while PGAI_AGENT_KANBAN_ROOT_PATH points every script/engine at the tmp_path
sandbox for data -- the same split team/tests/unit/test_rest_twins_read_verbs.py
established for this API test suite. POST /operations/defer and undefer call
their pgai_agent_kanban.ops.write function in process, which acts on the
server config's kanban_root, so their tests point that config at the sandbox.

Naming: function names describe behavior under test, never bug IDs or task
IDs (see roles/TESTER.md "Test Authoring Guidelines").
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to this file: team/tests/unit/ -> team/ -> repo root)
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent.parent  # team/
_SCRIPTS_DIR = _TEAM_DIR / "scripts"
_DEFER_SH = _SCRIPTS_DIR / "defer.sh"
_UNDEFER_SH = _SCRIPTS_DIR / "undefer.sh"

# Ensure team/ is importable so pgai_agent_kanban resolves regardless of the
# caller's cwd (mirrors test_rest_twins_read_verbs.py's own sys.path bootstrap).
_team_str = str(_TEAM_DIR)
if _team_str not in sys.path:
    sys.path.insert(0, _team_str)


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------


def _write_projects_cfg(root: pathlib.Path, projects: list[tuple[str, int]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, priority in projects:
        lines.append(f"[project:{name}]")
        lines.append(f"priority={priority}")
        lines.append("")
    (root / "projects.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _env_for(kanban_root: pathlib.Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PGAI_PROJECT_NAME", None)
    env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    env["KANBAN_ROOT"] = str(kanban_root)
    return env


def _make_client(sandbox_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, kanban_root_for_scripts: pathlib.Path | None = None):
    """Create a TestClient whose scripts resolve to the real team/ dev tree
    (unless overridden) while data resolves against sandbox_root -- the split
    test_rest_twins_read_verbs.py's own docstring documents."""
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig
    from fastapi.testclient import TestClient

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(sandbox_root))
    monkeypatch.setenv("KANBAN_ROOT", str(sandbox_root))
    monkeypatch.delenv("PGAI_PROJECT_NAME", raising=False)

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,
        kanban_root=kanban_root_for_scripts if kanban_root_for_scripts is not None else _TEAM_DIR,
    )
    return TestClient(create_app(cfg=cfg), raise_server_exceptions=True)


def _register_project(root: pathlib.Path, name: str) -> pathlib.Path:
    _write_projects_cfg(root, [(name, 1)])
    project_dir = root / "projects" / name
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _campaign_file(root: pathlib.Path, name: str) -> pathlib.Path:
    return root / "projects" / name / "DEFER-LOW-BUGS"


# ---------------------------------------------------------------------------
# Acceptance criterion 1 -- POST /operations/defer arms the same file CLI does
# ---------------------------------------------------------------------------


class TestDeferArmsSameCampaignFileAsCli:
    def test_defer_post_produces_byte_identical_campaign_file_to_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli_root = tmp_path / "cli_root"
        http_root = tmp_path / "http_root"
        _register_project(cli_root, "proj")
        _register_project(http_root, "proj")

        cli = subprocess.run(
            ["bash", str(_DEFER_SH), "--project", "proj", "--until", "v2.20.0"],
            capture_output=True, text=True, env=_env_for(cli_root), timeout=15,
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr

        with _make_client(http_root, monkeypatch, kanban_root_for_scripts=http_root) as client:
            resp = client.post(
                "/operations/defer",
                json={"project": "proj", "until": "v2.20.0"},
            )
        assert resp.status_code == 200, resp.json()

        cli_content = _campaign_file(cli_root, "proj").read_text(encoding="utf-8")
        http_content = _campaign_file(http_root, "proj").read_text(encoding="utf-8")
        assert http_content == cli_content, (
            "POST /operations/defer's campaign file differs from defer.sh's own "
            f"output for the same arguments.\nCLI:\n{cli_content!r}\nHTTP:\n{http_content!r}"
        )
        assert "until=v2.20.0" in http_content

    def test_defer_post_idempotent_rearm_matches_cli_wording(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        _register_project(root, "proj")

        with _make_client(root, monkeypatch, kanban_root_for_scripts=root) as client:
            first = client.post(
                "/operations/defer", json={"project": "proj", "until": "v2.20.0"}
            )
            assert first.status_code == 200
            second = client.post(
                "/operations/defer", json={"project": "proj", "until": "v2.20.0"}
            )
        assert second.status_code == 200
        assert "already armed" in second.json()["stdout"], second.json()


# ---------------------------------------------------------------------------
# Acceptance criterion 2 -- POST /operations/undefer against an unarmed
# project succeeds and reports the posture was already clear
# ---------------------------------------------------------------------------


class TestUndeferAgainstUnarmedProjectMatchesCliIdempotence:
    def test_undefer_post_against_unarmed_project_matches_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Same root for both invocations -- undefer against an unarmed
        # project mutates nothing, so a CLI call followed by an HTTP call
        # against the identical path produces byte-identical wording
        # (including the file path named in the message).
        root = tmp_path / "root"
        _register_project(root, "proj")

        cli = subprocess.run(
            ["bash", str(_UNDEFER_SH), "--project", "proj"],
            capture_output=True, text=True, env=_env_for(root), timeout=15,
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr
        assert "nothing to clear" in cli.stdout

        with _make_client(root, monkeypatch, kanban_root_for_scripts=root) as client:
            resp = client.post("/operations/undefer", json={"project": "proj"})
        assert resp.status_code == 200
        body = resp.json()
        assert "nothing to clear" in body["stdout"], body
        assert body["stdout"] == cli.stdout, (
            "HTTP stdout must match undefer.sh's own idempotent-clear wording "
            "byte-for-byte."
        )
        assert not _campaign_file(root, "proj").exists()

    def test_undefer_post_clears_an_existing_campaign(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        _register_project(root, "proj")

        with _make_client(root, monkeypatch, kanban_root_for_scripts=root) as client:
            armed = client.post(
                "/operations/defer", json={"project": "proj", "until": "v2.20.0"}
            )
            assert armed.status_code == 200
            assert _campaign_file(root, "proj").exists()

            cleared = client.post("/operations/undefer", json={"project": "proj"})
        assert cleared.status_code == 200
        assert "cleared" in cleared.json()["stdout"]
        assert not _campaign_file(root, "proj").exists()


# ---------------------------------------------------------------------------
# Acceptance criterion 3 -- malformed/missing --until reproduces defer.sh's
# own stable machine error key, byte-identical, over HTTP
# ---------------------------------------------------------------------------


class TestDeferUntilRefusalMachineKeyParityAcrossSurfaces:
    def test_malformed_until_key_matches_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli_root = tmp_path / "cli_root"
        http_root = tmp_path / "http_root"
        _register_project(cli_root, "proj")
        _register_project(http_root, "proj")

        cli = subprocess.run(
            ["bash", str(_DEFER_SH), "--project", "proj", "--until", "2.14"],
            capture_output=True, text=True, env=_env_for(cli_root), timeout=15,
        )
        assert cli.returncode != 0
        assert "defer.until_malformed" in cli.stderr, cli.stderr

        with _make_client(http_root, monkeypatch, kanban_root_for_scripts=http_root) as client:
            resp = client.post(
                "/operations/defer", json={"project": "proj", "until": "2.14"}
            )
        body = resp.json()
        assert resp.status_code == 422
        assert "defer.until_malformed" in body["stderr"], body
        assert body["stderr"] == cli.stderr, (
            "HTTP stderr must be byte-identical to defer.sh's own stderr -- "
            "the API never pre-validates or reformats --until."
        )
        assert not _campaign_file(http_root, "proj").exists()

    def test_missing_until_key_matches_cli(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cli_root = tmp_path / "cli_root"
        http_root = tmp_path / "http_root"
        _register_project(cli_root, "proj")
        _register_project(http_root, "proj")

        cli = subprocess.run(
            ["bash", str(_DEFER_SH), "--project", "proj"],
            capture_output=True, text=True, env=_env_for(cli_root), timeout=15,
        )
        assert cli.returncode != 0
        assert "defer.until_required" in cli.stderr, cli.stderr

        with _make_client(http_root, monkeypatch, kanban_root_for_scripts=http_root) as client:
            resp = client.post("/operations/defer", json={"project": "proj"})
        body = resp.json()
        assert resp.status_code == 422
        assert "defer.until_required" in body["stderr"], body
        assert body["stderr"] == cli.stderr


# ---------------------------------------------------------------------------
# Acceptance criterion 4 -- POST /operations/deep-verify returns promptly and
# never asserts a verdict the (still-running) lane has not produced
# ---------------------------------------------------------------------------


_STUB_SLOW_GREEN = """\
#!/usr/bin/env bash
sleep 3
echo "[cold-build] [docker] [claude-code] Image built successfully: tag:test"
echo "Cold-build lane PASSED"
exit 0
"""


class TestDeepVerifyReturnsPromptlyWithoutAssertingAVerdict:
    def test_response_returns_before_the_backgrounded_lane_finishes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        _register_project(root, "proj")

        stub = tmp_path / "slow_cold_lane.sh"
        stub.write_text(_STUB_SLOW_GREEN, encoding="utf-8")
        stub.chmod(0o755)
        monkeypatch.setenv("PGAI_DEEP_VERIFY_COLD_LANE_CMD", str(stub))
        # The stubbed lane stands in for the container tier, which runs only
        # with operator authorization and never inside an agent wake.
        monkeypatch.setenv("PGAI_CONTAINER_TIER", "1")
        monkeypatch.delenv("PGAI_AGENT_WAKE_ACTIVE", raising=False)
        # Deep-verify.sh resolves the tree it records against PGAI_DEV_TREE_PATH
        # (or its own repo-root fallback) -- pin it to this dev tree explicitly
        # so the run does not depend on ambient kanban.cfg state.
        monkeypatch.setenv("PGAI_DEV_TREE_PATH", str(_TEAM_DIR.parent))
        # The REST twin passes no --image, so the operator's kanban.cfg
        # default is what selects the images the backgrounded run builds.
        with (root / "kanban.cfg").open("a", encoding="utf-8") as cfg:
            cfg.write("\n[release]\ncontainer_images = all\n")

        with _make_client(root, monkeypatch) as client:
            started = time.monotonic()
            resp = client.post("/operations/deep-verify", json={"project": "proj"})
            elapsed = time.monotonic() - started

        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert elapsed < 2.0, (
            f"POST /operations/deep-verify took {elapsed:.2f}s to return; the "
            "stubbed lane sleeps 3s, so a prompt response must return well "
            "before that -- suspect the endpoint is blocking on the lane."
        )
        assert body["exit_code"] == 0
        assert "not completed" in body["stdout"] or "no verdict" in body["stdout"], body
        assert "Verdict: built" not in body["stdout"], (
            "The response must never assert a verdict the backgrounded lane "
            f"has not produced yet: {body!r}"
        )

        # The backgrounded run genuinely happens: poll the ledger for the
        # verdict the stubbed lane eventually produces.
        ledger_path = root / "projects" / "proj" / "deep-verify-ledger.md"
        deadline = time.monotonic() + 15
        ledger_text = ""
        while time.monotonic() < deadline:
            if ledger_path.exists():
                ledger_text = ledger_path.read_text(encoding="utf-8")
                if "Verdict: built" in ledger_text:
                    break
            time.sleep(0.2)
        assert "Verdict: built" in ledger_text, (
            f"Backgrounded deep-verify run never wrote a built verdict to "
            f"{ledger_path} within the poll window.\nLedger contents: {ledger_text!r}"
        )

    def test_dry_run_short_circuits_with_no_subprocess_and_no_ledger_write(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        _register_project(root, "proj")

        with _make_client(root, monkeypatch) as client:
            resp = client.post(
                "/operations/deep-verify",
                json={"project": "proj", "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["exit_code"] == 0
        assert "dry-run: would execute deep-verify.sh" in body["stdout"], body
        assert not (root / "projects" / "proj" / "deep-verify-ledger.md").exists()

    def test_missing_project_without_scheduled_is_422(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "root"
        with _make_client(root, monkeypatch) as client:
            resp = client.post("/operations/deep-verify", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Acceptance criterion 5 -- POST /operations/wake-now (bg) returns promptly
# without waiting for the fired agent, and the invocation is observed
# ---------------------------------------------------------------------------


_STUB_WAKE_NOW_SLOW_BG = """\
#!/usr/bin/env bash
# Stub replicating wake-now.sh's fire_one_bg() shape: backgrounds a slow
# child WITHOUT redirecting its stdout/stderr away from its own -- the exact
# fd-inheritance shape that would hang a naive shell_out()-based dispatch.
if [[ "$*" == *"--bg"* || "$*" == *"--all"* ]]; then
    ( sleep 3; echo "child finished" >> "{marker}" ) &
    echo "  [stub-agent] PID $!"
    exit 0
else
    sleep 3
    echo "foreground finished"
    exit 0
fi
"""


class TestWakeNowBackgroundDoesNotBlockOnASlowFire:
    def test_bg_true_returns_promptly_and_the_backgrounded_fire_is_observed(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A dedicated kanban_root-for-scripts (NOT the shared team/ dev tree)
        # whose scripts/wake-now.sh is this stub -- isolates the test from a
        # real wake-batch.sh dispatch while exercising the exact fd-sharing
        # hazard the endpoint's detached dispatch exists to avoid.
        stub_kanban_root = tmp_path / "stub_kanban_root"
        (stub_kanban_root / "scripts").mkdir(parents=True, exist_ok=True)
        marker = tmp_path / "child_ran_marker.txt"
        stub_script = stub_kanban_root / "scripts" / "wake-now.sh"
        stub_script.write_text(
            _STUB_WAKE_NOW_SLOW_BG.format(marker=marker), encoding="utf-8"
        )
        stub_script.chmod(0o755)

        data_root = tmp_path / "data_root"
        data_root.mkdir(parents=True, exist_ok=True)

        with _make_client(
            data_root, monkeypatch, kanban_root_for_scripts=stub_kanban_root
        ) as client:
            started = time.monotonic()
            resp = client.post(
                "/operations/wake-now", json={"agent": "coder", "bg": True}
            )
            elapsed = time.monotonic() - started

        assert resp.status_code == 200, resp.json()
        body = resp.json()
        assert elapsed < 2.0, (
            f"POST /operations/wake-now (bg=true) took {elapsed:.2f}s; the "
            "stub's backgrounded child sleeps 3s with its stdout/stderr left "
            "attached to the parent (the exact shape that hangs a naive "
            "shell_out() dispatch) -- a prompt response must return well "
            "before that."
        )
        assert body["exit_code"] == 0
        assert "started" in body["stdout"], body
        assert not marker.exists(), (
            "Marker file exists already -- the backgrounded child must not "
            "have completed synchronously within the request."
        )

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.2)
        assert marker.exists(), (
            "The backgrounded fire never completed -- the detached dispatch "
            "may have been killed early rather than truly detached."
        )

    def test_dry_run_short_circuits_with_no_subprocess(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_root = tmp_path / "data_root"
        data_root.mkdir(parents=True, exist_ok=True)
        with _make_client(data_root, monkeypatch) as client:
            resp = client.post(
                "/operations/wake-now",
                json={"agent": "coder", "bg": True, "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "dry-run: would execute wake-now.sh" in body["stdout"], body


# ---------------------------------------------------------------------------
# Acceptance criterion 6 -- manifest absent-twin markers flipped
# ---------------------------------------------------------------------------


class TestManifestAbsentTwinMarkersFlippedForFourVerbs:
    """The operator verb manifest carries real REST columns (never 'ABSENT')
    for defer, undefer, deep-verify, and wake-now."""

    @pytest.mark.parametrize(
        "verb_name, expected_method, expected_route, expected_body_model",
        [
            ("defer", "POST", "/operations/defer", "DeferBody"),
            ("undefer", "POST", "/operations/undefer", "UndeferBody"),
            ("deep-verify", "POST", "/operations/deep-verify", "DeepVerifyBody"),
            ("wake-now", "POST", "/operations/wake-now", "WakeNowBody"),
        ],
    )
    def test_verb_rest_columns_are_not_absent(
        self, verb_name: str, expected_method: str, expected_route: str, expected_body_model: str
    ) -> None:
        from pgai_agent_kanban.lib.operator_verb_manifest import load_manifest

        verbs = load_manifest()
        verb = verbs[verb_name]
        assert verb.rest_method == expected_method, verb
        assert verb.rest_route == expected_route, verb
        assert verb.rest_body_model == expected_body_model, verb
