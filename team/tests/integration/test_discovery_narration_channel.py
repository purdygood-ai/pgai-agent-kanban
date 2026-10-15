"""
test_discovery_narration_channel.py
====================================
Integration coverage for the discovery pipeline's dual-channel contract:
stdout of a data-producing step carries only data (bundled item paths, one
per line); every narration line goes to the log channel (stderr / the wake
log file), never to stdout.

Background: team/scripts/lib/wake_bootstrap.sh's log() writes each
narration line through `tee -a "$LOG_FILE"`. Before this file's fix, tee's
own stdout copy was NOT redirected to stderr, so any discovery function
whose result a caller captures via `$(...)` -- discovery_step_bugs's
piggyback deferral filter is the sharpest example -- got a narration line
spliced into its returned data, indistinguishable from a real path until
something downstream (`_disc_set_status_field`'s read_text, or the
`## Bundled Items` renderer in `_disc_write_bundle`) tried to treat it as
one.

Two independent layers are exercised here:
  1. The primary fix -- log()'s stdout tap is gone, so narration can never
     reach a data channel in the first place. Exercised by
     TestPiggybackUnderTheRealWakeLogChannel using the actual production
     log() extracted verbatim from wake_bootstrap.sh (so this test can
     never silently drift from what the real wake path does).
  2. The defense-in-depth post-filter shape guard -- even if narration DID
     leak into a data stream (reproduced here by deliberately using a
     log() whose tee stdout copy is not redirected to stderr, as an
     adversarial stand-in), discovery_step_bugs's
     post-filter validation (_disc_bundle_item_shape_ok in
     discovery_steps.sh) refuses to bundle at all rather than rendering a
     bundle a bad line slipped past: it narrates the refusal and writes no
     requirements file. Exercised by
     TestContaminatedPostFilterListRefusesTheWholeBundle.

Design notes:
  - All tests use two_project_root (tmp_path-backed). No bare /tmp paths.
  - Test names describe the behaviour under test, not the bug ID that
    motivated them (roles/TESTER.md Anti-pattern 6).
  - Reuses the _run_discovery / _add_bug_file / _bundle_files / _bug_status
    helper shapes established in test_discovery_bug_deferral_gate.py and
    test_discovery_bug_deferral_piggyback.py (each integration test file
    keeps its own local copies rather than importing another test module's
    private functions -- see test_discovery_race_guard.py for the same
    convention).
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import textwrap
from typing import Optional

from pgai_agent_kanban.cm.bundle_family import parse_bundled_item_basenames

# ---------------------------------------------------------------------------
# Path constants (resolve from this file's own location)
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_SCRIPTS_LIB = _TEAM_DIR / "scripts" / "lib"
_WAKE_BOOTSTRAP_SH = _SCRIPTS_LIB / "wake_bootstrap.sh"


def _extract_bash_function(source_file: pathlib.Path, func_name: str) -> str:
    """Extract a `name() { ... }` function body verbatim from *source_file*.

    Embedding the real production log() (rather than a hand-typed copy) in
    the test harness means this test can never silently drift from what
    wake_bootstrap.sh actually does -- a stale hand copy would keep
    "passing" even after a regression reintroduced the stdout tap.
    """
    text = source_file.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(func_name)}\(\)\s*\{{.*?^\}}\s*$", re.M | re.S)
    m = pattern.search(text)
    assert m, f"Could not find function {func_name}() in {source_file}"
    return m.group(0)


# ---------------------------------------------------------------------------
# Shared helpers (local to this module)
# ---------------------------------------------------------------------------


def _run_discovery(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    log_prelude: str = "",
    log_file: Optional[pathlib.Path] = None,
) -> subprocess.CompletedProcess:
    """Run discovery_run_pipeline against a temporary kanban tree.

    *log_prelude* is bash source injected BEFORE `source discovery.sh`, so a
    caller can pre-declare log() exactly the way wake_bootstrap.sh does
    (discovery.sh's own guard only installs its stderr-only shim when no
    log() is already declared -- see discovery.sh's "Logging shim" header).
    When *log_prelude* is empty, discovery.sh's own minimal stderr-only
    shim is what runs, matching pm-agent.sh's (non-wake) call path.

    Echoes DISCOVERY_LAST_STATUS on stdout after the run.
    """
    log_file_line = f'LOG_FILE="{log_file}"' if log_file is not None else 'LOG_FILE="/dev/null"'
    script = textwrap.dedent(f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        AGENT="test"
        {log_file_line}
        {log_prelude}
        source "{_SCRIPTS_LIB}/ini_parser.sh"
        source "{_SCRIPTS_LIB}/temp.sh"
        source "{_SCRIPTS_LIB}/semver.sh"
        source "{_SCRIPTS_LIB}/project_paths.sh"
        source "{_SCRIPTS_LIB}/discovery.sh"
        discovery_run_pipeline "{project_name}"
        echo "DISCOVERY_STATUS=${{DISCOVERY_LAST_STATUS}}"
    """)

    base_env = dict(os.environ)
    base_env["KANBAN_ROOT"] = str(kanban_root)
    base_env["TEAM_ROOT"] = str(kanban_root)
    base_env["PGAI_AGENT_KANBAN_ROOT_PATH"] = str(kanban_root)
    base_env["PGAI_PROJECT_NAME"] = project_name
    base_env.pop("PGAI_DEV_TREE_PATH", None)

    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=base_env,
        cwd=str(kanban_root),
        timeout=60,
    )


def _add_bug_file(
    kanban_root: pathlib.Path,
    project_name: str,
    bug_id: str = "BUG-0001",
    slug: str = "test-fix",
    status: str = "open",
    severity: Optional[str] = "low",
) -> pathlib.Path:
    """Create a bug file. severity=None omits the **Severity:** field entirely."""
    bugs_dir = kanban_root / "projects" / project_name / "bugs"
    bugs_dir.mkdir(parents=True, exist_ok=True)
    bug_file = bugs_dir / f"{bug_id}-{slug}.md"
    severity_line = f"**Severity:** {severity}\n\n" if severity is not None else ""
    bug_file.write_text(
        f"# {bug_id}: {slug}\n\n"
        f"{severity_line}"
        f"## Status\n{status}\n\n"
        "## Summary\nTest bug file.\n",
        encoding="utf-8",
    )
    return bug_file


def _requirements_dir(kanban_root: pathlib.Path, project_name: str) -> pathlib.Path:
    return kanban_root / "projects" / project_name / "requirements"


def _set_defer_posture(
    kanban_root: pathlib.Path,
    project_name: str,
    *,
    deferred_bugs: Optional[bool] = None,
    deferred_bug_level: Optional[str] = None,
    defer_piggyback: Optional[bool] = None,
) -> None:
    cfg_path = kanban_root / "projects" / project_name / "project.cfg"
    lines = ["", "[release]"]
    if deferred_bugs is not None:
        lines.append(f"deferred_bugs = {'true' if deferred_bugs else 'false'}")
    if deferred_bug_level is not None:
        lines.append(f"deferred_bug_level = {deferred_bug_level}")
    if defer_piggyback is not None:
        lines.append(f"defer_piggyback = {'true' if defer_piggyback else 'false'}")
    with cfg_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _bundle_files(kanban_root: pathlib.Path, project_name: str) -> list[pathlib.Path]:
    return list(_requirements_dir(kanban_root, project_name).glob("*-bugfix-bundle-*.md"))


def _bug_status(bug_file: pathlib.Path) -> str:
    text = bug_file.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines()):
        if line.strip() == "## Status":
            return text.splitlines()[i + 1].strip()
    return ""


def _make_five_bug_scenario(root: pathlib.Path, project_name: str) -> tuple[pathlib.Path, list[pathlib.Path]]:
    """One medium trigger bug + three low (deferred) riders, piggyback=true."""
    _set_defer_posture(
        root, project_name, deferred_bugs=True, deferred_bug_level="low", defer_piggyback=True
    )
    trigger = _add_bug_file(root, project_name, bug_id="BUG-0001", slug="trigger", severity="medium")
    riders = [
        _add_bug_file(root, project_name, bug_id="BUG-0002", slug="rider-a", severity="low"),
        _add_bug_file(root, project_name, bug_id="BUG-0003", slug="rider-b", severity="low"),
        _add_bug_file(root, project_name, bug_id="BUG-0004", slug="rider-c", severity="low"),
    ]
    return trigger, riders


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPiggybackUnderTheRealWakeLogChannel:
    """Exercises the primary fix: narration through the production log()
    (extracted verbatim from wake_bootstrap.sh) can never reach a data
    channel, so the piggyback path produces a clean bundle with no
    traceback, regardless of how many deferred riders it carries.
    """

    def test_three_deferred_riders_bundle_cleanly_with_no_traceback(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        root = two_project_root
        trigger, riders = _make_five_bug_scenario(root, "project_a")

        log_file = tmp_path / "wake.log"
        real_log_fn = _extract_bash_function(_WAKE_BOOTSTRAP_SH, "log")
        result = _run_discovery(
            root, "project_a", log_prelude=real_log_fn, log_file=log_file
        )

        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "Traceback" not in result.stdout, result.stdout
        assert "Traceback" not in result.stderr, result.stderr

        bundles = _bundle_files(root, "project_a")
        assert bundles, f"Expected a bundle for the trigger bug.\nstderr:\n{result.stderr}"
        bundle_text = bundles[0].read_text(encoding="utf-8")

        item_ids = parse_bundled_item_basenames(str(bundles[0]))
        # Exactly four bundled items: the trigger plus all three riders --
        # no extra (contaminated) line snuck in.
        assert len(item_ids) == 4, item_ids
        assert any("BUG-0001" in i for i in item_ids), item_ids
        for rider in riders:
            rider_id = rider.name.split("-")[0] + "-" + rider.name.split("-")[1]
            assert any(rider_id in i for i in item_ids), item_ids

        # Narration lands on the log channel (the log file tee() also
        # writes to), never inside the bundle document itself, and the
        # consumer-side loud-skip guards never had anything to reject.
        trigger_id = trigger.name.replace(".md", "")
        log_content = log_file.read_text(encoding="utf-8")
        assert f"piggybacking 3 deferred bugs (trigger: {trigger_id} medium)" in log_content
        assert "piggybacking" not in bundle_text.lower(), bundle_text
        assert "WARNING skipping non-path line" not in log_content, log_content

        assert _bug_status(trigger) == "running"
        for rider in riders:
            assert _bug_status(rider) == "running", rider


class TestContaminatedPostFilterListRefusesTheWholeBundle:
    """Exercises the post-filter shape guard
    (_disc_bundle_item_shape_ok in discovery_steps.sh): when a narration
    line leaks into the post-filter item list (reproduced here with a
    deliberately un-redirected tee, standing in for a log() that writes
    narration to stdout), discovery_step_bugs refuses to bundle at all -- narrating the
    refusal and writing no file -- rather than silently dropping the bad
    line and shipping a bundle that may be missing real items or, absent
    this guard, could ship contaminated with non-path content.
    """

    def test_contaminated_stdout_produces_no_bundle_and_narrates_the_refusal(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        root = two_project_root
        trigger, riders = _make_five_bug_scenario(root, "project_a")

        # Deliberately reproduce a leaking log() shape (tee's stdout copy
        # NOT redirected to stderr) as an adversarial stand-in -- the exact
        # condition the real log() in wake_bootstrap.sh never produces.
        # Used here only to prove the belt-and-suspenders layer still holds
        # if narration ever leaks into a data stream again.
        contaminating_log_fn = textwrap.dedent("""\
            log() {
              echo "[$(date -Iseconds)] wake(${AGENT}): $*" | tee -a "$LOG_FILE"
            }
        """)

        log_file = tmp_path / "wake.log"
        result = _run_discovery(
            root, "project_a", log_prelude=contaminating_log_fn, log_file=log_file
        )

        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        assert "Traceback" not in result.stdout, result.stdout
        assert "Traceback" not in result.stderr, result.stderr

        # tee -a "$LOG_FILE" always appends regardless of which fd its own
        # stdout copy happens to land in, so the log file is where the
        # refusal narration is guaranteed to land.
        log_content = log_file.read_text(encoding="utf-8")
        trigger_id = trigger.name.replace(".md", "")
        assert (
            "refusing to bundle -- post-filter item list contains a line "
            "that is not a bundleable item path"
        ) in log_content, log_content
        assert f"piggybacking 3 deferred bugs (trigger: {trigger_id} medium)" in log_content, (
            log_content
        )

        # No bundle is produced: the post-filter shape guard refuses the
        # whole step before _disc_write_bundle is ever called.
        bundles = _bundle_files(root, "project_a")
        assert not bundles, f"Expected no bundle to be written.\nlog:\n{log_content}"

        # Neither the trigger nor its riders were marked running -- the
        # step refused before the per-item marking loop that follows
        # _disc_write_bundle ever ran.
        assert _bug_status(trigger) == "open"
        for rider in riders:
            assert _bug_status(rider) == "open", rider


class TestFullTranscriptHasNoPythonTracebacks:
    def test_bug_and_priority_steps_produce_no_traceback_in_full_transcript(
        self, two_project_root: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        """Runs the bug step (with a piggyback carry) under the real wake
        log channel and greps the combined stdout+stderr transcript for
        the literal string "Traceback" -- the unhandled-exception marker
        Python always prints. Covers discovery_step_bugs; the sibling
        discovery_step_priority path shares the same
        _disc_write_bundle/_disc_set_status_field consumers exercised here,
        so a regression in either would surface the same way.
        """
        root = two_project_root
        _make_five_bug_scenario(root, "project_a")

        log_file = tmp_path / "wake.log"
        real_log_fn = _extract_bash_function(_WAKE_BOOTSTRAP_SH, "log")
        result = _run_discovery(
            root, "project_a", log_prelude=real_log_fn, log_file=log_file
        )

        transcript = result.stdout + "\n" + result.stderr + "\n" + log_file.read_text(encoding="utf-8")
        assert "Traceback" not in transcript, transcript
        assert result.returncode == 0, transcript


class TestDiscoveryDriverWritesNoNarrationToStdout:
    """Static/behavioral audit companion to the runtime tests above: greps
    team/scripts/lib/discovery.sh's own echo/printf sites (the discovery
    driver) and confirms every one of them either writes to a
    file/variable a caller is meant to capture as data, or is explicitly
    redirected to stderr. Documents, and locks in, the "narration never
    reaches stdout" invariant for the discovery driver.
    """

    def test_every_echo_or_printf_in_discovery_sh_is_narration_redirected_to_stderr(self) -> None:
        discovery_sh = _SCRIPTS_LIB / "discovery.sh"
        text = discovery_sh.read_text(encoding="utf-8")
        offending = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\becho\b|\bprintf\b", stripped) and ">&2" not in stripped:
                offending.append(f"{lineno}: {line}")
        assert not offending, (
            "team/scripts/lib/discovery.sh has an echo/printf site that does not "
            "redirect to stderr (narration must never reach stdout):\n"
            + "\n".join(offending)
        )


class TestBugDiscoveryStepWritesNoNarrationToStdout:
    """Static audit companion scoped to the bug discovery step itself
    (discovery_step_bugs in discovery_steps.sh). Unlike discovery.sh --
    a pure driver where every echo/printf can simply require ">&2" --
    discovery_step_bugs is a data-producing function that legitimately
    pipes intermediate output (line counts via `$(echo ... | wc -l)`,
    shape probes via `echo ... | grep -q`) without ever letting that
    output reach the function's own real stdout. So the invariant checked
    here is broader than a bare ">&2" requirement: every echo/printf site
    either feeds a pipe on the same line (its output is consumed by the
    next command in that pipeline, never escaping as this function's own
    stdout) or is redirected straight to stderr. Locks in the "no
    data-producing function in the bug discovery step emits non-path
    content to stdout" invariant so a future edit cannot reintroduce a
    bare narration echo without failing the suite.
    """

    def test_every_echo_or_printf_in_discovery_step_bugs_is_piped_or_stderr(self) -> None:
        discovery_steps_sh = _SCRIPTS_LIB / "discovery_steps.sh"
        func_body = _extract_bash_function(discovery_steps_sh, "discovery_step_bugs")
        offending = []
        for lineno, line in enumerate(func_body.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if (
                re.search(r"\becho\b|\bprintf\b", stripped)
                and ">&2" not in stripped
                and "|" not in stripped
            ):
                offending.append(f"{lineno}: {line}")
        assert not offending, (
            "discovery_step_bugs() in team/scripts/lib/discovery_steps.sh has an "
            "echo/printf site that neither feeds a same-line pipe nor redirects "
            "to stderr -- its output would reach the function's own stdout, "
            "which a caller may capture as bundle-item data (narration must "
            "never reach that channel):\n" + "\n".join(offending)
        )
