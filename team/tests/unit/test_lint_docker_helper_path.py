"""
test_lint_docker_helper_path.py
===============================
team/scripts/lint_docker_helper_path.py reports every container-engine call
in non-test team/ code that bypasses the shared helper pgai_docker
(team/scripts/lib/docker_use.sh), naming the file and line; it is quality
class, so it exits 0 whatever it finds, and the shipped tree is clean.

Seeded trees are built under tmp_path with the repository layout the lint
expects (``<root>/team/...``) and scanned with ``--root``.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

_TEAM_DIR = pathlib.Path(__file__).resolve().parents[2]
_LINT = _TEAM_DIR / "scripts" / "lint_docker_helper_path.py"


def _seed(root: pathlib.Path, rel: str, text: str) -> pathlib.Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_LINT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _findings(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.startswith("docker_helper_path: ")]


def test_seeded_bare_docker_build_is_reported_naming_the_file(tmp_path: pathlib.Path) -> None:
    _seed(tmp_path, "team/scripts/build-thing.sh", "#!/usr/bin/env bash\nset -e\ndocker build -t thing .\n")
    result = _run("--root", str(tmp_path))
    assert result.returncode == 0, result.stderr
    findings = _findings(result)
    assert len(findings) == 1, result.stdout
    assert findings[0].startswith("docker_helper_path: team/scripts/build-thing.sh:3: calls 'docker' directly")
    assert "pgai_docker" in findings[0]
    assert "1 finding(s)" in result.stdout


@pytest.mark.parametrize(
    "line",
    [
        'podman run --rm img true',
        '/usr/bin/docker pull alpine:3',
        'if ! docker info >/dev/null 2>&1; then echo none; fi',
        'out="$(docker images --format x)"',
        '"${_MY_ENGINE}" build -f Dockerfile .',
        'exec "$DOCKER_EXE" run --rm -it img',
        'true && podman rmi img',
    ],
)
def test_direct_engine_call_shapes_are_reported(tmp_path: pathlib.Path, line: str) -> None:
    _seed(tmp_path, "team/scripts/x.sh", f"#!/usr/bin/env bash\n{line}\n")
    findings = _findings(_run("--root", str(tmp_path)))
    assert len(findings) == 1, (line, findings)
    assert "team/scripts/x.sh:2:" in findings[0]


@pytest.mark.parametrize(
    "line",
    [
        'pgai_docker docker build -t thing .',
        'pgai_docker "$engine" run --rm img',
        'command -v docker >/dev/null 2>&1',
        'echo "run docker build yourself; then docker run it"',
        "printf 'RUN docker build\\n'",
        '# docker build -t thing .',
        'for candidate in docker podman; do echo "$candidate"; done',
        'DOCKERFILE="${REPO_ROOT}/docker/debian/Dockerfile"',
    ],
)
def test_helper_calls_probes_strings_and_comments_are_not_reported(tmp_path: pathlib.Path, line: str) -> None:
    _seed(tmp_path, "team/scripts/x.sh", f"#!/usr/bin/env bash\n{line}\n")
    assert _findings(_run("--root", str(tmp_path))) == [], line


def test_heredoc_bodies_are_not_scanned(tmp_path: pathlib.Path) -> None:
    _seed(
        tmp_path,
        "team/scripts/help.sh",
        "#!/usr/bin/env bash\ncat <<'EOF'\ndocker build -t thing .\nEOF\ndocker run img\n",
    )
    findings = _findings(_run("--root", str(tmp_path)))
    assert len(findings) == 1 and "team/scripts/help.sh:5:" in findings[0], findings


def test_python_subprocess_call_is_reported_but_a_docstring_mention_is_not(tmp_path: pathlib.Path) -> None:
    _seed(
        tmp_path,
        "team/pgai_agent_kanban/thing.py",
        '"""Runs subprocess.run(["docker", "ps"]) in prose only."""\n'
        "import shutil\nimport subprocess\n\n"
        'shutil.which("docker")\n'
        'subprocess.run(["docker", "build", "."], check=False)\n',
    )
    findings = _findings(_run("--root", str(tmp_path)))
    assert len(findings) == 1, findings
    assert "team/pgai_agent_kanban/thing.py:6: invokes 'docker' directly" in findings[0]


def test_tests_directories_and_the_helper_itself_are_not_scanned(tmp_path: pathlib.Path) -> None:
    _seed(tmp_path, "team/tests/unit/test_x.py", 'import subprocess\nsubprocess.run(["docker", "ps"])\n')
    _seed(tmp_path, "team/scripts/lib/docker_use.sh", '#!/usr/bin/env bash\n"$engine" run img\ndocker ps\n')
    result = _run("--root", str(tmp_path))
    assert _findings(result) == [], result.stdout


def test_findings_file_carries_each_finding_and_is_written_empty_when_clean(tmp_path: pathlib.Path) -> None:
    seeded = tmp_path / "seeded"
    _seed(seeded, "team/scripts/build-thing.sh", "docker build -t thing .\n")
    out = tmp_path / "findings.json"
    assert _run("--root", str(seeded), "--findings-file", str(out)).returncode == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == [
        {
            "lint": "docker_helper_path",
            "severity": "quality",
            "kind": "direct-engine-call",
            "file": "team/scripts/build-thing.sh",
            "line": 1,
            "summary": data[0]["summary"],
            "remedy": "python3 team/scripts/lint_docker_helper_path.py",
        }
    ]
    clean = tmp_path / "clean"
    _seed(clean, "team/scripts/ok.sh", "echo hi\n")
    out_clean = tmp_path / "clean.json"
    assert _run("--root", str(clean), "--findings-file", str(out_clean)).returncode == 0
    assert json.loads(out_clean.read_text(encoding="utf-8")) == []


def test_shipped_tree_reports_zero_findings() -> None:
    result = _run()
    assert result.returncode == 0, result.stderr
    assert _findings(result) == [], result.stdout
    assert "; 0 finding(s)" in result.stdout
