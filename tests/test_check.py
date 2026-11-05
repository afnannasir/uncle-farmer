"""check.py's step choice: CI runs ruff and pyright in one job and pytest in another (--only), so
each must run exactly what it names, with the same commands and coverage floors as a full run."""

from __future__ import annotations

import pytest

import check


def ran(monkeypatch: pytest.MonkeyPatch, argv: list[str], coverage_file: bool = True) -> list[str]:
    """The steps check.main(argv) starts, by their tool name, without running any of them."""
    started: list[str] = []

    def call(cmd: list[str], cwd: str) -> int:
        started.append(cmd[2] if cmd[1] == "-m" else "java_tests")
        return 0

    def exists(path: str) -> bool:
        return coverage_file
    monkeypatch.setattr(check.subprocess, "call", call)
    monkeypatch.setattr(check.os.path, "exists", exists)
    monkeypatch.setattr(check, "have_jdk", lambda: True)
    assert check.main(argv) == 0
    return started


def test_without_only_every_step_runs(monkeypatch: pytest.MonkeyPatch):
    assert ran(monkeypatch, []) == ["ruff", "pyright", "check_manifest", "pytest", "java_tests", "coverage"]


def test_only_static_runs_ruff_and_pyright_and_no_coverage_floor(monkeypatch: pytest.MonkeyPatch):
    # a .coverage left from an earlier run must not be judged by a job that ran no tests
    assert ran(monkeypatch, ["--only", "ruff,pyright"]) == ["ruff", "pyright"]


def test_only_pytest_keeps_the_engine_coverage_floor(monkeypatch: pytest.MonkeyPatch):
    assert ran(monkeypatch, ["--only", "pytest"]) == ["pytest", "coverage"]


def test_skip_agent_still_leaves_the_java_tests_out(monkeypatch: pytest.MonkeyPatch):
    assert ran(monkeypatch, ["--skip-agent"]) == ["ruff", "pyright", "check_manifest", "pytest", "coverage"]


def test_the_static_job_checks_the_manifest_with_the_locked_setuptools(monkeypatch: pytest.MonkeyPatch):
    cmds: list[list[str]] = []

    def call(cmd: list[str], cwd: str) -> int:
        cmds.append(cmd)
        return 0
    monkeypatch.setattr(check.subprocess, "call", call)
    assert check.main(["--only", "ruff,pyright,manifest"]) == 0
    assert cmds[2][1:] == ["-m", "check_manifest", "--no-build-isolation"]


def test_only_core_judges_the_coverage_the_tests_left(monkeypatch: pytest.MonkeyPatch):
    # CI's tests job runs `uv run pytest --cov` itself, then check.py --only core
    assert ran(monkeypatch, ["--only", "core"]) == ["coverage"]


def test_only_core_fails_without_coverage_data(monkeypatch: pytest.MonkeyPatch):
    def missing(path: str) -> bool:
        return False

    def call(cmd: list[str], cwd: str) -> int:
        return 0
    monkeypatch.setattr(check.os.path, "exists", missing)
    monkeypatch.setattr(check.subprocess, "call", call)
    assert check.main(["--only", "core"]) == 1


def test_the_floors_are_the_raised_ones():
    # the whole app's floor is coverage's own (pyproject.toml), the engine's is check.py's
    import tomllib
    with open(check.os.path.join(check.ROOT, "pyproject.toml"), "rb") as f:
        report = tomllib.load(f)["tool"]["coverage"]["report"]
    assert report["fail_under"] == 78 and check.CORE_FLOOR == 92
    assert "--cov" in check.PYTEST_COV and "--cov-report=term-missing" in check.PYTEST_COV


@pytest.mark.parametrize("argv", [["--only"], ["--only", "lint"], ["--only", "ruff,"]])
def test_only_refuses_unknown_or_missing_steps(argv: list[str]):
    with pytest.raises(SystemExit):
        check.only(argv)
