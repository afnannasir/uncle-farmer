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
    assert [r["check"] for r in report["checks"]] == ["imports", "native routing", "data", "translations", "help texts",
                                                       "game agent"]
    assert all(r["ok"] for r in report["checks"] if r["required"])


def test_a_failing_required_self_test_check_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        raise RuntimeError("no data")
    monkeypatch.setattr(selftest, "checks", lambda: [("data", broken, True), ("game agent", broken, False)])
    path = tmp_path / "report.json"
    assert selftest.run(str(path)) == 1
    rows = json.loads(path.read_text(encoding="utf-8"))["checks"]
    assert rows[0]["ok"] is False and "no data" in rows[0]["detail"]


def test_the_build_keeps_docstrings_the_help_windows_show() -> None:
    # -OO strips docstrings: Commands for trusted players and Script commands came up empty
    assert build_exe.PYTHON_FLAG == "--python-flag=-O"


def test_the_self_test_fails_when_the_help_texts_are_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import messages
    monkeypatch.setattr(messages, "__doc__", None)
    path = tmp_path / "report.json"
    assert selftest.run(str(path)) == 1
    row = next(r for r in json.loads(path.read_text(encoding="utf-8"))["checks"] if r["check"] == "help texts")
    assert row["ok"] is False and "messages" in row["detail"]


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


# ---------------------------------------------------------------- the Dev channel's builds
def test_the_release_version_marks_a_dev_build_with_its_run() -> None:
    assert ci_build.release_version("production", "0.9.0", 57) == "0.9.0"
    assert ci_build.release_version("development", "0.9.0", 57) == "0.9.0-dev.57"
    with pytest.raises(SystemExit):
        ci_build.release_version("nightly", "0.9.0", 57)


@pytest.mark.parametrize("value, want", [("57", 57), ("", 0), ("x", 0), ("-3", 0)])
def test_the_run_number_comes_from_github(monkeypatch: pytest.MonkeyPatch, value: str, want: int) -> None:
    monkeypatch.setenv("GITHUB_RUN_NUMBER", value)
    assert ci_build.run_number() == want


def test_build_info_says_the_release_version_and_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ci_build, "tool_version", stand_in(ci_build.tool_version, lambda cmd: "tool 1.0"))
    monkeypatch.setenv("UPX_DIR", "")
    z = zipped(fake_folder(tmp_path), tmp_path / "UncleFarmer-dev-0123456.zip")
    info = ci_build.build_info("development", "0.9.0", SHA, "dev", str(z), "ab" * 32, "0.9.0-dev.57")
    assert info["version"] == "0.9.0" and info["release_version"] == "0.9.0-dev.57" and info["channel"] == "dev"
    assert ci_build.build_info("production", "0.9.0", SHA, "master", str(z), "ab" * 32)["channel"] == "master"


@pytest.mark.parametrize("build_type, want_version, want_channel", [
    ("development", "-dev.41", "dev"), ("production", "", "master")])
def test_every_build_carries_its_build_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_type: str,
                                            want_version: str, want_channel: str) -> None:
    import release
    from unclefarmer import __version__
    out = tmp_path / "dist" / "Uncle Farmer"
    monkeypatch.setattr(build_exe, "OUT", str(out))
    monkeypatch.setattr(release, "OUT", str(out))
    monkeypatch.setattr(build_exe, "main", lambda full=False: fake_folder(tmp_path / "dist"))
    monkeypatch.setattr(ci_build, "commit", returning((SHA, "dev")))
    monkeypatch.setattr(ci_build, "git", returning(SHA))
    no_report: dict[str, Any] = {}
    monkeypatch.setattr(ci_build, "smoke_test", returning(no_report))
    monkeypatch.setattr(ci_build, "tool_version", returning("tool 1.0"))
    monkeypatch.setenv("GITHUB_RUN_NUMBER", "41")
    monkeypatch.setenv("UPX_DIR", "")
    github_output = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    ci_build.build(build_type, str(tmp_path / "ci"))
    (zip_path,) = (tmp_path / "ci").glob("*.zip")
    with zipfile.ZipFile(zip_path) as z:
        built = json.loads(z.read("Uncle Farmer/build.json"))
    assert built == {"version": __version__ + want_version, "channel": want_channel, "commit": SHA}
    assert "release_version=%s%s\n" % (__version__, want_version) in github_output.read_text(encoding="utf-8")


def published(tag: str, prerelease: bool = True, **over: Any) -> dict[str, Any]:
    rel: dict[str, Any] = {"tag_name": tag, "prerelease": prerelease, "draft": False, "assets": []}
    rel.update(over)
    return rel


class Gh:
    """A pretend gh CLI: the releases there are, and every command it was given."""

    def __init__(self, releases: list[dict[str, Any]], digest: str | None = None) -> None:
        self.releases, self.digest = releases, digest
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[0] == "api" and "releases?per_page" in args[1]:
            return json.dumps(self.releases)
        if args[0] == "api":
            tag = args[1].rsplit("/", 1)[1]
            return json.dumps(next(r for r in self.releases if r["tag_name"] == tag))
        if args[:2] == ["release", "delete"]:
            self.releases = [r for r in self.releases if r["tag_name"] != args[2]]
        elif args[:2] == ["release", "create"]:
            files = [a for a in args[3:] if os.path.isfile(a)]
            assets = [{"name": os.path.basename(f), "digest": self.digest or "sha256:" + hashlib.sha256(
                Path(f).read_bytes()).hexdigest()} for f in files]
            self.releases.insert(0, published(args[2], prerelease="--prerelease" in args, assets=assets))
        return ""

    def deleted(self) -> list[str]:
        return [c[2] for c in self.calls if c[:2] == ["release", "delete"]]


def built_zip(tmp_path: Path) -> Path:
    (tmp_path / "ci").mkdir()
    return zipped(fake_folder(tmp_path / "f"), tmp_path / "ci" / "UncleFarmer-dev-0123456.zip")


def test_a_dev_build_is_published_as_a_pre_release_never_latest(tmp_path: Path) -> None:
    gh = Gh([published("v0.9.0", prerelease=False)])
    z = built_zip(tmp_path)
    tag = ci_build.publish_dev(str(z), "0.9.0-dev.57", SHA, repo="o/uncle-farmer", run=gh)
    assert tag == "v0.9.0-dev.57"
    create = next(c for c in gh.calls if c[:2] == ["release", "create"])
    assert create[2] == "v0.9.0-dev.57"
    assert {"--prerelease", "--latest=false"} <= set(create)
    assert create[create.index("--target") + 1] == SHA and create[create.index("--repo") + 1] == "o/uncle-farmer"
    uploads = [os.path.basename(a) for a in create if os.path.isfile(a)]
    assert uploads == ["UncleFarmer-0.9.0-dev.57.zip", "UncleFarmer-0.9.0-dev.57.zip.sha256"]
    sha_file = tmp_path / "ci" / "publish" / "UncleFarmer-0.9.0-dev.57.zip.sha256"
    assert sha_file.read_text(encoding="ascii").split()[0] == hashlib.sha256(z.read_bytes()).hexdigest()
    assert gh.deleted() == []                                  # the stable release is never touched


def test_only_the_newest_dev_pre_releases_are_kept(tmp_path: Path) -> None:
    old = [published("v0.9.0-dev.%d" % n) for n in range(40, 52)]          # 12 older dev builds
    others = [published("v0.9.0", prerelease=False), published("v0.8.0", prerelease=False),
              published("v0.9.0-dev.1", prerelease=False),               # dev-shaped, but not a pre-release
              published("v1.0.0-beta.1"), published("nightly")]          # pre-releases, but not dev builds
    gh = Gh(old + others)
    ci_build.publish_dev(str(built_zip(tmp_path)), "0.9.0-dev.57", SHA, keep=10, repo="o/r", run=gh)
    assert sorted(gh.deleted()) == ["v0.9.0-dev.40", "v0.9.0-dev.41", "v0.9.0-dev.42"]
    left = {r["tag_name"] for r in gh.releases}
    assert {r["tag_name"] for r in others} <= left and "v0.9.0-dev.57" in left


def test_prune_orders_by_version_not_by_text() -> None:
    rels = [published("v0.9.0-dev.%d" % n) for n in (9, 10, 100)] + [published("v0.10.0-dev.2")]
    assert ci_build.to_prune(rels, 2) == ["v0.9.0-dev.10", "v0.9.0-dev.9"]
    assert ci_build.to_prune(rels, 0) == ci_build.to_prune(rels, 1)      # the newest always stays


def test_a_re_run_replaces_its_own_pre_release(tmp_path: Path) -> None:
    gh = Gh([published("v0.9.0-dev.57")])
    ci_build.publish_dev(str(built_zip(tmp_path)), "0.9.0-dev.57", SHA, repo="o/r", run=gh)
    assert gh.deleted() == ["v0.9.0-dev.57"]
    assert [r["tag_name"] for r in gh.releases] == ["v0.9.0-dev.57"]


def test_a_stable_release_with_that_tag_is_never_replaced(tmp_path: Path) -> None:
    gh = Gh([published("v0.9.0-dev.57", prerelease=False)])
    with pytest.raises(SystemExit):
        ci_build.publish_dev(str(built_zip(tmp_path)), "0.9.0-dev.57", SHA, repo="o/r", run=gh)
    assert gh.deleted() == [] and not any(c[:2] == ["release", "create"] for c in gh.calls)


def test_a_published_zip_with_another_checksum_fails_the_job(tmp_path: Path) -> None:
    gh = Gh([], digest="sha256:" + "00" * 32)
    with pytest.raises(SystemExit):
        ci_build.publish_dev(str(built_zip(tmp_path)), "0.9.0-dev.57", SHA, repo="o/r", run=gh)


@pytest.mark.parametrize("version, sha, repo", [
    ("0.9.0", SHA, "o/r"),                  # a stable version is never published here
    ("0.9.0-dev.x", SHA, "o/r"),
    ("0.9.0-dev.57", "abc", "o/r"),
    ("0.9.0-dev.57", SHA, ""),
])
def test_publishing_needs_a_dev_version_a_commit_and_a_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  version: str, sha: str, repo: str) -> None:
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    gh = Gh([])
    with pytest.raises(SystemExit):
        ci_build.publish_dev(str(built_zip(tmp_path)), version, sha, repo=repo, run=gh)
    assert gh.calls == []


def test_the_workflow_publishes_dev_builds_only_from_dev_with_write_access_there_only() -> None:
    text = (Path(ci_build.ROOT) / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    head, jobs = text.split("\njobs:\n", 1)
    others, publish = jobs.split("\n  publish-dev:\n", 1)
    assert "permissions:\n  contents: read\n" in head                 # the default stays read-only
    assert "contents: write" not in others                            # no other job may write
    assert "contents: write" in publish and "needs: [build]" in publish
    assert "if: github.event_name != 'pull_request' && github.ref == 'refs/heads/dev'" in publish
    assert "ci_build.py publish-dev" in publish and "secrets." not in text
    # the master build is as it was: built and uploaded, never published by CI
    assert "github.ref == 'refs/heads/dev' || github.ref == 'refs/heads/master'" in others
    assert "release_version: ${{ steps.build.outputs.release_version }}" in others
