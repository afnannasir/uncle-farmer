"""The CI build's packaging (ci_build.py): names, checksums, the zip and agent checks, build info and
the built exe's self-test. The Nuitka build itself only runs in CI (half an hour); these use a
pretend folder."""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import returning, stand_in

import build_exe
import ci_build
from unclefarmer import selftest

SHA = "0123456789abcdef0123456789abcdef01234567"


def fake_folder(root: Path, leave_out: str = "") -> Path:
    """A built folder holding one file for each of build_exe.REQUIRED (bar leave_out)."""
    out = root / "Uncle Farmer"
    for pattern in build_exe.REQUIRED:
        if pattern == leave_out:
            continue
        name = pattern.replace("*", "x")
        path = out / name if "." in os.path.basename(name) else out / name / "__init__.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return out


def zipped(folder: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as z:
        for f in folder.rglob("*"):
            if f.is_file():
                z.write(f, "Uncle Farmer/" + f.relative_to(folder).as_posix())
    return dest


def test_names_say_the_build_type_branch_version_and_commit() -> None:
    assert ci_build.zip_name("development", "0.6.0", SHA) == "UncleFarmer-dev-0123456.zip"
    assert ci_build.zip_name("production", "0.6.0", SHA) == "UncleFarmer-master-0.6.0-0123456.zip"
    with pytest.raises(SystemExit):
        ci_build.zip_name("nightly", "0.6.0", SHA)


def test_the_checksum_is_the_finished_zips_and_sha256sum_readable(tmp_path: Path) -> None:
    z = zipped(fake_folder(tmp_path), tmp_path / "UncleFarmer-dev-0123456.zip")
    digest = ci_build.write_checksum(str(z))
    assert digest == hashlib.sha256(z.read_bytes()).hexdigest()
    assert (tmp_path / "UncleFarmer-dev-0123456.zip.sha256").read_text(encoding="ascii") == \
        "%s *UncleFarmer-dev-0123456.zip\n" % digest


def test_a_complete_zip_passes_and_a_missing_file_is_named(tmp_path: Path) -> None:
    good = zipped(fake_folder(tmp_path / "a"), tmp_path / "good.zip")
    assert ci_build.verify_zip(str(good), build_exe.REQUIRED) == []
    bad = zipped(fake_folder(tmp_path / "b", leave_out="agent/unclefarmer-agent.jar"), tmp_path / "bad.zip")
    assert ci_build.verify_zip(str(bad), build_exe.REQUIRED) == ["agent/unclefarmer-agent.jar"]


def test_a_broken_or_misshapen_zip_stops_the_build(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(SystemExit):
        ci_build.verify_zip(str(junk), build_exe.REQUIRED)
    loose = tmp_path / "loose.zip"
    with zipfile.ZipFile(loose, "w") as z:
        z.writestr("UncleFarmer.exe", "x")              # not inside "Uncle Farmer/"
    with pytest.raises(SystemExit):
        ci_build.verify_zip(str(loose), build_exe.REQUIRED)


def test_the_built_folder_check_names_what_is_missing(tmp_path: Path) -> None:
    build_exe.verify(str(fake_folder(tmp_path / "ok")))
    with pytest.raises(SystemExit) as e:
        build_exe.verify(str(fake_folder(tmp_path / "no", leave_out="data/knowledge.json")))
    assert "data/knowledge.json" in str(e.value)


def klass(major: int) -> bytes:
    return b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, major) + b"\x00" * 8


def agent_jar(path: Path, major: int = 52, manifest: str = "Premain-Class: unclefarmer.agent.BotAgent\r\n") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n" + manifest)
        z.writestr("unclefarmer/agent/BotAgent.class", klass(52))
        z.writestr("unclefarmer/agent/World.class", klass(major))
    return path


def test_the_agent_jar_must_be_java_8_with_its_entry_class(tmp_path: Path) -> None:
    ci_build.verify_agent(str(agent_jar(tmp_path / "ok.jar")))
    with pytest.raises(SystemExit):
        ci_build.verify_agent(str(agent_jar(tmp_path / "new.jar", major=61)))     # Java 17 classes
    with pytest.raises(SystemExit):
        ci_build.verify_agent(str(agent_jar(tmp_path / "plain.jar", manifest="")))
    with pytest.raises(SystemExit):
        ci_build.verify_agent(str(tmp_path / "absent.jar"))
    assert ci_build.class_major(klass(52)) == 52


def test_build_info_describes_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"GITHUB_RUN_ID": "99", "GITHUB_RUN_NUMBER": "7", "GITHUB_REPOSITORY": "o/uncle-farmer",
                 "RUNNER_OS": "Windows", "UPX_DIR": ""}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(ci_build, "tool_version", stand_in(ci_build.tool_version, lambda cmd: "tool 1.0"))
    z = zipped(fake_folder(tmp_path), tmp_path / "UncleFarmer-master-0.6.0-0123456.zip")
    info = ci_build.build_info("production", "0.6.0", SHA, "master", str(z), "ab" * 32)
    assert info["build_type"] == "production" and info["branch"] == "master" and info["version"] == "0.6.0"
    assert info["commit"] == SHA and info["commit_short"] == "0123456"
    assert info["workflow_run_id"] == "99" and info["workflow_run_number"] == "7"
    assert info["built_at_utc"].endswith("Z") and info["sha256"] == "ab" * 32
    assert info["package"] == z.name and info["package_bytes"] == z.stat().st_size
    assert json.loads(json.dumps(info)) == info


def _smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: dict[str, Any] | None, code: int) -> None:
    z = zipped(fake_folder(tmp_path / "f"), tmp_path / "b.zip")

    def run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        assert cmd[1] == "--self-test" and timeout == ci_build.SMOKE_SECONDS
        if report is not None:
            Path(cmd[2]).write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, code)
    monkeypatch.setattr(ci_build.subprocess, "run", run)
    ci_build.smoke_test(str(z), str(tmp_path / "work"))


def test_the_smoke_test_passes_on_a_good_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _smoke(tmp_path, monkeypatch, {"ok": True, "frozen": True,
                                   "checks": [{"check": "imports", "ok": True, "detail": "wx"}]}, 0)


@pytest.mark.parametrize("report,code", [
    ({"ok": False, "frozen": True, "checks": []}, 1),       # a failed check
    ({"ok": True, "frozen": False, "checks": []}, 0),       # ran from source, not the built exe
    (None, 0),                                              # no report at all
])
def test_the_smoke_test_stops_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        report: dict[str, Any] | None, code: int) -> None:
    with pytest.raises(SystemExit):
        _smoke(tmp_path, monkeypatch, report, code)


def test_the_self_test_from_source_reports_every_check(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    code = selftest.run(str(path))
    report = json.loads(path.read_text(encoding="utf-8"))
    assert code == 0 and report["ok"] is True and report["frozen"] is False
    assert [r["check"] for r in report["checks"]] == ["imports", "native routing", "data", "translations", "game agent"]
    assert all(r["ok"] for r in report["checks"] if r["required"])


def test_a_failing_required_self_test_check_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        raise RuntimeError("no data")
    monkeypatch.setattr(selftest, "checks", lambda: [("data", broken, True), ("game agent", broken, False)])
    path = tmp_path / "report.json"
    assert selftest.run(str(path)) == 1
    rows = json.loads(path.read_text(encoding="utf-8"))["checks"]
    assert rows[0]["ok"] is False and "no data" in rows[0]["detail"]


def test_upx_comes_from_upx_dir_or_path_never_a_local_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "upx.exe").write_bytes(b"")
    monkeypatch.setenv("UPX_DIR", str(tmp_path))
    assert build_exe.upx_dir() == str(tmp_path)
    monkeypatch.setenv("UPX_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit):
        build_exe.upx_dir()
    monkeypatch.delenv("UPX_DIR")
    monkeypatch.setattr(build_exe.shutil, "which", returning(None))
    with pytest.raises(SystemExit) as e:
        build_exe.upx_dir()
    assert "UPX not found" in str(e.value)
