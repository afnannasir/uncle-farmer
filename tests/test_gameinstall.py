"""gameinstall.py: finding Mist World and adding/removing Uncle Farmer's agent line.

Covers _ours, agent_line, is_game_folder, _read_lines (missing/CRLF/LF/empty/undecodable), status
(nogame/missing/installed/other), install (backup, dedupe, newline, agent dir), uninstall, and
running_game_folders against a fake Windows process API.

Nothing touches a real game folder or the repo: paths.AGENT_JAR/AGENT_RUN point at tmp_path, the
game folder is a tmp_path, and running_game_folders' ctypes is replaced by a small stand-in.
"""

import os
from types import SimpleNamespace

import pytest

from unclefarmer import gameinstall, paths


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Keep the agent paths off the repo."""
    monkeypatch.setattr(paths, "AGENT_JAR", str(tmp_path / "agent" / "unclefarmer-agent.jar"))
    monkeypatch.setattr(paths, "AGENT_RUN", str(tmp_path / "agent" / "run"))
    return tmp_path


def game_folder(root, vmoptions=None):
    folder = root / "game"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mw.exe").write_bytes(b"MZ")
    if vmoptions is not None:
        (folder / gameinstall.VMOPTIONS).write_bytes(vmoptions)
    return folder


# ------------------------------------------------------------------------------- _ours / agent_line
def test_ours_matches_the_marker_and_legacy_lines():
    assert gameinstall._ours("-javaagent:C:\\x\\unclefarmer-agent.jar=run") is True
    assert gameinstall._ours("-javaagent:C:\\x\\mwfarmer-agent.jar=run") is True
    assert gameinstall._ours("-Xmx2g") is False
    assert gameinstall._ours("") is False


def test_ours_is_case_sensitive():
    # MARKER is looked up verbatim, so a differently-cased name is not recognised.
    assert gameinstall._ours("-javaagent:C:\\x\\UncleFarmer-Agent.Jar=run") is False


def test_agent_line_uses_the_agent_paths():
    assert gameinstall.agent_line() == "-javaagent:%s=%s" % (
        paths.AGENT_JAR, paths.AGENT_RUN)


# ------------------------------------------------------------------------------- is_game_folder
def test_is_game_folder_needs_an_mw_exe_file(tmp_path):
    assert gameinstall.is_game_folder("") is False
    assert gameinstall.is_game_folder(None) is False
    assert gameinstall.is_game_folder(str(tmp_path / "nope")) is False
    folder = game_folder(tmp_path)
    assert gameinstall.is_game_folder(str(folder)) is True


def test_is_game_folder_rejects_an_mw_exe_directory(tmp_path):
    folder = tmp_path / "game"
    (folder / "mw.exe").mkdir(parents=True)
    assert gameinstall.is_game_folder(str(folder)) is False


# ------------------------------------------------------------------------------- _read_lines
def test_read_lines_missing_file(tmp_path):
    folder = game_folder(tmp_path)
    path, lines, nl = gameinstall._read_lines(str(folder))
    assert path == str(folder / gameinstall.VMOPTIONS)
    assert lines == []
    assert nl == "\r\n"


def test_read_lines_keeps_crlf_and_drops_blank_lines(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\r\n\r\n-javaagent:old.jar=run\r\n")
    path, lines, nl = gameinstall._read_lines(str(folder))
    assert nl == "\r\n"
    assert lines == ["-Xmx2g", "-javaagent:old.jar=run"]


def test_read_lines_detects_lf(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\n-javaagent:old.jar=run\n")
    _path, lines, nl = gameinstall._read_lines(str(folder))
    assert nl == "\n"
    assert lines == ["-Xmx2g", "-javaagent:old.jar=run"]


def test_read_lines_empty_file_defaults_to_crlf(tmp_path):
    folder = game_folder(tmp_path, b"")
    _path, lines, nl = gameinstall._read_lines(str(folder))
    assert lines == []
    assert nl == "\r\n"


def test_read_lines_keeps_spacing_and_replaces_bad_bytes(tmp_path):
    folder = game_folder(tmp_path, b"  spaced  \r\n\xff\xfe\r\n")
    _path, lines, nl = gameinstall._read_lines(str(folder))
    assert nl == "\r\n"
    assert lines == ["  spaced  ", "\ufffd\ufffd"]


# ------------------------------------------------------------------------------- status
def test_status_nogame(tmp_path):
    assert gameinstall.status(str(tmp_path / "nope")) == "nogame"
    assert gameinstall.status("") == "nogame"


def test_status_missing_when_no_vmoptions(tmp_path):
    folder = game_folder(tmp_path)
    assert gameinstall.status(str(folder)) == "missing"


def test_status_installed_for_our_exact_line(tmp_path):
    folder = game_folder(tmp_path, ("-Xmx2g\r\n" + gameinstall.agent_line() + "\r\n").encode())
    assert gameinstall.status(str(folder)) == "installed"


def test_status_other_for_a_different_or_legacy_line(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\r\n-javaagent:C:\\Old\\unclefarmer-agent.jar=run\r\n")
    assert gameinstall.status(str(folder)) == "other"
    folder2 = game_folder(tmp_path / "two", b"-javaagent:C:\\x\\mwfarmer-agent.jar=run\r\n")
    assert gameinstall.status(str(folder2)) == "other"


def test_status_ignores_unrelated_lines(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\r\n-XX:+UseG1GC\r\n")
    assert gameinstall.status(str(folder)) == "missing"


# ------------------------------------------------------------------------------- install
def test_install_backs_up_and_appends_our_line(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\n-XX:+UseG1GC\n")
    gameinstall.install(str(folder))
    assert (folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")).read_bytes() == \
        b"-Xmx2g\n-XX:+UseG1GC\n"
    assert (folder / gameinstall.VMOPTIONS).read_bytes() == \
        ("-Xmx2g\n-XX:+UseG1GC\n" + gameinstall.agent_line() + "\n").encode()
    assert os.path.isdir(paths.AGENT_RUN)


def test_install_does_not_overwrite_an_existing_backup(tmp_path):
    folder = game_folder(tmp_path, b"original\n")
    (folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")).write_bytes(b"keep me\n")
    gameinstall.install(str(folder))
    assert (folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")).read_bytes() == b"keep me\n"


def test_install_replaces_our_old_and_legacy_lines(tmp_path):
    folder = game_folder(
        tmp_path,
        ("-Xmx2g\r\n-javaagent:C:\\x\\mwfarmer-agent.jar=run\r\n"
         "-javaagent:C:\\x\\unclefarmer-agent.jar=run\r\n").encode(),
    )
    gameinstall.install(str(folder))
    raw = (folder / gameinstall.VMOPTIONS).read_bytes()
    lines = raw.decode("utf-8").splitlines()
    assert lines == ["-Xmx2g", gameinstall.agent_line()]
    assert raw.endswith(b"\r\n")


def test_install_creates_vmoptions_when_absent(tmp_path):
    folder = game_folder(tmp_path)                    # no vmoptions at all
    gameinstall.install(str(folder))
    assert (folder / gameinstall.VMOPTIONS).read_bytes() == \
        (gameinstall.agent_line() + "\r\n").encode()
    assert not (folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")).exists()


def test_install_is_idempotent(tmp_path):
    folder = game_folder(tmp_path, b"-Xmx2g\n")
    gameinstall.install(str(folder))
    gameinstall.install(str(folder))
    text = (folder / gameinstall.VMOPTIONS).read_text(encoding="utf-8")
    assert text.count(gameinstall.MARKER) == 1


# ------------------------------------------------------------------------------- uninstall
def test_uninstall_removes_our_lines_and_keeps_the_rest(tmp_path):
    folder = game_folder(
        tmp_path, ("-Xmx2g\n-javaagent:C:\\x\\mwfarmer-agent.jar=run\n"
                   + gameinstall.agent_line() + "\n").encode())
    gameinstall.uninstall(str(folder))
    assert (folder / gameinstall.VMOPTIONS).read_bytes() == b"-Xmx2g\n"


def test_uninstall_of_a_missing_file_is_a_no_op(tmp_path):
    folder = game_folder(tmp_path)
    gameinstall.uninstall(str(folder))
    assert not (folder / gameinstall.VMOPTIONS).exists()


def test_uninstall_when_only_our_line_remains_writes_a_newline(tmp_path):
    folder = game_folder(tmp_path, (gameinstall.agent_line() + "\r\n").encode())
    gameinstall.uninstall(str(folder))
    assert (folder / gameinstall.VMOPTIONS).read_bytes() == b"\r\n"


# ------------------------------------------------------------------------------- running_game_folders
class _Array:
    def __init__(self, n):
        self.a = [0] * n

    def __setitem__(self, i, v):
        self.a[i] = v

    def __getitem__(self, i):
        return self.a[i]


class _DWord:
    def __init__(self, value=0):
        self.value = value


class _DWordFactory:
    def __call__(self, value=0):
        return _DWord(value)

    def __mul__(self, n):
        return lambda: _Array(n)


class _Buf:
    def __init__(self):
        self.value = ""


class _Kernel:
    def __init__(self, entries, deny, fail_query):
        self.entries, self.deny, self.fail_query = entries, deny, fail_query
        self.closed = []

    def OpenProcess(self, access, inherit, pid):
        if pid in self.deny or pid not in self.entries:
            return 0
        return pid

    def QueryFullProcessImageNameW(self, h, flags, buf, size):
        if h in self.fail_query or h not in self.entries:
            return 0
        buf.value = self.entries[h]
        return 1

    def CloseHandle(self, h):
        self.closed.append(h)


def fake_windows(monkeypatch, entries, deny=(), fail_query=(), enum_fails=False):
    kernel = _Kernel(entries, set(deny), set(fail_query))

    class _Psapi:
        def EnumProcesses(self, arr, size, got):
            if enum_fails:
                return 0
            for i, pid in enumerate(entries):
                arr[i] = pid
            got.value = len(entries) * 4
            return 1

    psapi = _Psapi()
    ct = SimpleNamespace(
        windll=SimpleNamespace(psapi=psapi, kernel32=kernel),
        byref=lambda x: x,
        sizeof=lambda x: 4,
        create_unicode_buffer=lambda n: _Buf(),
    )
    monkeypatch.setattr(gameinstall, "ctypes", ct)
    monkeypatch.setattr(gameinstall, "wt", SimpleNamespace(DWORD=_DWordFactory()))
    return kernel


def test_running_game_folders_collects_unique_mw_exe_folders(monkeypatch):
    mw = os.path.join("C:", "Games", "Mist World", "mw.exe")
    other = os.path.join("C:", "Games", "Other", "game.exe")
    fake_windows(monkeypatch, {11: mw, 12: os.path.join("C:", "Games", "Mist World", "mw.exe"),
                               13: other})
    assert gameinstall.running_game_folders() == [os.path.join("C:", "Games", "Mist World")]


def test_running_game_folders_skips_unopenable_and_unqueryable_processes(monkeypatch):
    mw = os.path.join("D:", "MW", "mw.exe")
    # only a successfully opened handle is closed; a denied OpenProcess just continues
    kernel = fake_windows(monkeypatch, {5: mw, 6: mw}, deny=(6,), fail_query=(5,))
    assert gameinstall.running_game_folders() == []
    assert kernel.closed == [5]


def test_running_game_folders_returns_empty_when_enumeration_fails(monkeypatch):
    fake_windows(monkeypatch, {1: os.path.join("C:", "MW", "mw.exe")}, enum_fails=True)
    assert gameinstall.running_game_folders() == []


def test_running_game_folders_ignores_non_mw_processes(monkeypatch):
    kernel = fake_windows(monkeypatch, {7: os.path.join("C:", "X", "java.exe")})
    assert gameinstall.running_game_folders() == []
    assert kernel.closed == [7]


# ------------------------------------------------------------------------------- find_installed
def test_an_empty_setting_finds_the_game_the_agent_last_ran_in(sandbox, monkeypatch):
    monkeypatch.setattr(gameinstall, "running_game_folders", lambda: [])
    folder = game_folder(sandbox)
    gameinstall.install(str(folder))
    assert gameinstall.find_installed("") is None              # nothing tells where yet
    run = sandbox / "agent" / "run"
    (run / gameinstall.FOLDER_FILE).write_text(str(folder) + "\n", encoding="utf-8")
    assert gameinstall.agent_folder() == str(folder)
    assert gameinstall.find_installed("") == str(folder)


def test_a_running_game_with_the_agent_is_found_and_one_without_it_is_not(sandbox, monkeypatch):
    bare = game_folder(sandbox / "bare")                        # a copy of the game with no agent
    ours = game_folder(sandbox / "ours")
    gameinstall.install(str(ours))
    monkeypatch.setattr(gameinstall, "running_game_folders", lambda: [str(bare), str(ours)])
    assert gameinstall.find_installed("") == str(ours)
    assert gameinstall.find_installed(str(ours)) == str(ours)   # the remembered one first


def test_the_remembered_folder_wins_while_it_has_the_agent(sandbox, monkeypatch):
    called = []
    monkeypatch.setattr(gameinstall, "running_game_folders", lambda: called.append(1) or [])
    folder = game_folder(sandbox)
    gameinstall.install(str(folder))
    assert gameinstall.find_installed(str(folder)) == str(folder)
    assert called == []                                        # no need to look at the running games


def test_a_games_own_folder_comes_from_its_agent(sandbox):
    folder = game_folder(sandbox)
    run = sandbox / "agent" / "run"
    run.mkdir(parents=True, exist_ok=True)
    assert gameinstall.folder_of(42, "D:/fallback") == "D:/fallback"      # no file: the setting
    (run / "agent.42.folder").write_text(str(folder), encoding="utf-8")
    assert gameinstall.folder_of(42, "D:/fallback") == str(folder)
    (run / "agent.43.folder").write_text(str(sandbox / "gone"), encoding="utf-8")
    assert gameinstall.folder_of(43, "D:/fallback") == "D:/fallback"      # not a game folder (any more)
    assert gameinstall.folder_of(None, "D:/fallback") == "D:/fallback"
