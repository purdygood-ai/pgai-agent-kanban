"""
test_health_version_fields.py — Tests for the /health endpoint's version fields.

Covers the BUG-0035 fix: /health now reports both ``installed`` (read from the
VERSION file on every request) and ``running`` (baked once at app startup and
never re-read).

Test suite:
  1. **Schema superset**: /health returns all pre-fix fields (``service``,
     ``kanban_root``, ``version``) plus the new ``installed`` and ``running``
     fields — no fields were removed.

  2. **Running version is baked at startup**: After the app is created, mutating
     the VERSION file causes ``installed`` to report the new value on the next
     request, but ``running`` continues to report the pre-mutation (startup-time)
     value.

  3. **Initial values agree**: At startup (before any mutation), ``version``,
     ``installed``, and ``running`` all report the same value.

Design notes
------------
- All tests use FastAPI's TestClient (ASGI transport — no real network socket,
  no port collisions, deterministic per-test lifecycle).
- The kanban root is a tmp_path sandbox with a synthetic VERSION file; no live
  kanban state is touched.
- The ``running`` version is baked in ``create_app()`` — there is no lifespan
  event or global state involved.  The TestClient approach is therefore correct:
  creating the app captures the running version; subsequent VERSION file mutations
  are visible through ``installed`` but not through ``running``.

Test-name policy (SOP.md Anti-pattern 6):
  Names describe observable behavior, never bug IDs or scaffolding labels.
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture: a TestClient with a synthetic VERSION file
# ---------------------------------------------------------------------------


def _make_client(kanban_root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a TestClient with kanban_root pointing at a synthetic sandbox.

    Imports are deferred so the module does not trigger app creation before
    the environment is patched (mirrors the pattern in test_fidelity.py and
    test_cors.py).

    Args:
        kanban_root:  Path to a synthetic kanban root that contains a VERSION file.
        monkeypatch:  pytest monkeypatch fixture (used to isolate env vars).

    Returns:
        A configured TestClient.  Callers must use it as a context manager to
        ensure proper ASGI lifespan handling.
    """
    from pgai_agent_kanban.api.app import create_app
    from pgai_agent_kanban.api.config import ApiConfig

    monkeypatch.setenv("PGAI_AGENT_KANBAN_ROOT_PATH", str(kanban_root))

    cfg = ApiConfig(
        host="127.0.0.1",
        port=0,  # ephemeral; TestClient does not open a real socket
        kanban_root=kanban_root,
    )
    app = create_app(cfg=cfg)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Test 1 — Schema superset
# ---------------------------------------------------------------------------


class TestHealthSchemaSuperset:
    """GET /health returns all pre-fix fields plus the new version fields."""

    def test_all_pre_fix_fields_present(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /health returns service, kanban_root, and version (pre-fix fields).

        These fields must not be removed; downstream consumers may depend on them.
        Adding ``installed`` and ``running`` must not displace any existing field.

        Args:
            tmp_path:    pytest-provided temp directory (sandbox root).
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        (kanban_root / "VERSION").write_text("v1.2.3\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health")

        assert resp.status_code == 200, (
            f"GET /health returned HTTP {resp.status_code}; expected 200."
        )
        body = resp.json()

        # Pre-fix fields that must remain.
        assert "service" in body, (
            f"Pre-fix field 'service' missing from /health response.\n"
            f"Got: {sorted(body.keys())}"
        )
        assert "kanban_root" in body, (
            f"Pre-fix field 'kanban_root' missing from /health response.\n"
            f"Got: {sorted(body.keys())}"
        )
        assert "version" in body, (
            f"Pre-fix field 'version' missing from /health response.\n"
            f"Got: {sorted(body.keys())}"
        )

    def test_new_version_fields_present(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /health returns the new installed and running fields.

        Both fields must be present in every /health response after the fix.

        Args:
            tmp_path:    pytest-provided temp directory.
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        (kanban_root / "VERSION").write_text("v1.2.3\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()

        assert "installed" in body, (
            f"New field 'installed' missing from /health response.\n"
            f"Got: {sorted(body.keys())}"
        )
        assert "running" in body, (
            f"New field 'running' missing from /health response.\n"
            f"Got: {sorted(body.keys())}"
        )


# ---------------------------------------------------------------------------
# Test 2 — Running version is baked at startup; installed follows disk
# ---------------------------------------------------------------------------


class TestRunningVersionBakedAtStartup:
    """running is captured once at app creation; installed reflects the current VERSION file."""

    def test_installed_follows_version_file_running_does_not(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mutating VERSION after boot changes installed but not running.

        Steps:
          1. Write VERSION = "v1.0.0" and create the app (baking running = "v1.0.0").
          2. Confirm the first /health call returns installed = running = "v1.0.0".
          3. Overwrite VERSION with "v2.0.0" (simulating an upgrade deploy).
          4. Call /health again.
          5. Assert installed == "v2.0.0" (per-request disk read picked up the change).
          6. Assert running  == "v1.0.0" (startup-baked value is unchanged).

        Args:
            tmp_path:    pytest-provided temp directory.
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        version_file = kanban_root / "VERSION"

        # Step 1 — write initial version and create the app.
        version_file.write_text("v1.0.0\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            # Step 2 — confirm initial state.
            pre_resp = client.get("/health")
            assert pre_resp.status_code == 200
            pre_body = pre_resp.json()
            assert pre_body["installed"] == "v1.0.0", (
                f"Before mutation: expected installed='v1.0.0', got {pre_body['installed']!r}"
            )
            assert pre_body["running"] == "v1.0.0", (
                f"Before mutation: expected running='v1.0.0', got {pre_body['running']!r}"
            )

            # Step 3 — mutate VERSION on disk (simulating an in-place upgrade).
            version_file.write_text("v2.0.0\n", encoding="utf-8")

            # Step 4 — call /health again with the same running app.
            post_resp = client.get("/health")
            assert post_resp.status_code == 200
            post_body = post_resp.json()

        # Step 5 — installed should reflect the new on-disk value.
        assert post_body["installed"] == "v2.0.0", (
            f"After mutation: expected installed='v2.0.0', got {post_body['installed']!r}.\n"
            "installed must be read from VERSION on each request."
        )

        # Step 6 — running must not change; it was baked at startup.
        assert post_body["running"] == "v1.0.0", (
            f"After mutation: expected running='v1.0.0', got {post_body['running']!r}.\n"
            "running must be the startup-time version and must never be re-read from disk."
        )

    def test_running_unchanged_across_multiple_requests(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """running stays constant across many /health calls even as VERSION changes.

        Verifies that the baked-at-startup guarantee holds across multiple
        consecutive mutations, not just a single before/after pair.

        Args:
            tmp_path:    pytest-provided temp directory.
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        version_file = kanban_root / "VERSION"

        startup_version = "v0.5.0"
        version_file.write_text(f"{startup_version}\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            # Mutate VERSION multiple times and confirm running is always startup_version.
            for new_ver in ("v0.6.0", "v0.7.0", "v1.0.0"):
                version_file.write_text(f"{new_ver}\n", encoding="utf-8")
                resp = client.get("/health")
                assert resp.status_code == 200
                body = resp.json()
                assert body["running"] == startup_version, (
                    f"After updating VERSION to {new_ver!r}: "
                    f"expected running={startup_version!r}, got {body['running']!r}.\n"
                    "running must never change after startup."
                )
                assert body["installed"] == new_ver, (
                    f"After updating VERSION to {new_ver!r}: "
                    f"expected installed={new_ver!r}, got {body['installed']!r}.\n"
                    "installed must reflect the current on-disk VERSION."
                )


# ---------------------------------------------------------------------------
# Test 3 — Initial values agree
# ---------------------------------------------------------------------------


class TestInitialValuesAgree:
    """At startup (before any mutation), version, installed, and running agree."""

    def test_version_installed_running_equal_at_startup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Before any VERSION mutation, version == installed == running.

        This confirms the new fields are consistent with the existing ``version``
        field at the moment of startup.

        Args:
            tmp_path:    pytest-provided temp directory.
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        (kanban_root / "VERSION").write_text("v3.14.0\n", encoding="utf-8")

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health")

        assert resp.status_code == 200
        body = resp.json()

        assert body["version"] == "v3.14.0", (
            f"Expected version='v3.14.0', got {body['version']!r}"
        )
        assert body["installed"] == "v3.14.0", (
            f"Expected installed='v3.14.0', got {body['installed']!r}"
        )
        assert body["running"] == "v3.14.0", (
            f"Expected running='v3.14.0', got {body['running']!r}"
        )
        assert body["version"] == body["installed"], (
            f"version and installed must be equal at startup; "
            f"version={body['version']!r}, installed={body['installed']!r}"
        )
        assert body["version"] == body["running"], (
            f"version and running must be equal at startup; "
            f"version={body['version']!r}, running={body['running']!r}"
        )

    def test_health_returns_200_with_missing_version_file(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /health returns 200 even when the VERSION file is absent.

        When the VERSION file does not exist, both installed and running
        fall back to the sentinel value "unknown".  The endpoint must not
        raise an exception.

        Args:
            tmp_path:    pytest-provided temp directory.
            monkeypatch: pytest monkeypatch fixture.
        """
        kanban_root = tmp_path / "kanban"
        kanban_root.mkdir()
        # Intentionally do NOT create a VERSION file.

        with _make_client(kanban_root, monkeypatch) as client:
            resp = client.get("/health")

        assert resp.status_code == 200, (
            f"GET /health must return 200 even without a VERSION file; "
            f"got {resp.status_code}."
        )
        body = resp.json()
        assert body["installed"] == "unknown", (
            f"Expected installed='unknown' when VERSION is absent; "
            f"got {body['installed']!r}"
        )
        assert body["running"] == "unknown", (
            f"Expected running='unknown' when VERSION is absent at startup; "
            f"got {body['running']!r}"
        )
