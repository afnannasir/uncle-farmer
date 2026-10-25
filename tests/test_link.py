"""The agent link protocol: finding a live agent, the auth handshake, one line per request.

Most tests replace socket.create_connection with a scripted fake. The socket-thread tests below
exercise the same bytes over a real loopback connection, including failures the game cannot easily
produce on demand: a refused secret, a connection closed after auth and a garbled reply.
"""

import os
import socket
import threading

import pytest

from unclefarmer import link


class Sock:
    """A fake connection: sendall records what we wrote, recv hands back scripted bytes."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = False
        self.options = []

    def sendall(self, data):
        self.sent.append(data)

    def setsockopt(self, level, opt, value):
        self.options.append((level, opt, value))

    def recv(self, n):
        return self.replies.pop(0) if self.replies else b""

    def close(self):
        self.closed = True


class FakeAgent:
    """A tiny socket server speaking the agent's auth/one-line protocol."""

    def __init__(self, secret="sec", replies=()):
        self.secret = secret
        self.replies = list(replies)
        self.seen = []
        self.conn = None
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((link.HOST, 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            self.conn, _ = self.server.accept()
        except OSError:
            return
        try:
            with self.conn.makefile("rb") as incoming:
                first = incoming.readline()
                if first != ("auth " + self.secret + "\n").encode("utf-8"):
                    self.conn.sendall(b"refused\n")
                    return
                self.conn.sendall(b"ok\n")
                for line in incoming:
                    self.seen.append(line)
                    if not self.replies:
                        return
                    self.conn.sendall(self.replies.pop(0))
        except OSError:
            pass
        finally:
            try:
                self.conn.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.close()
        self.thread.join(2)


def port_file(run, pid, port, secret, mtime):
    p = run / ("agent.%d.port" % pid)
    p.write_text("%d %d %s" % (port, pid, secret), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def patch(monkeypatch, tmp_path, sock, live=(1,)):
    monkeypatch.setattr(link.paths, "AGENT_RUN", str(tmp_path))
    monkeypatch.setattr(link, "_alive", lambda pid: pid in live)
    monkeypatch.setattr(link.socket, "create_connection", lambda addr, timeout=None: sock)
    return sock


# ---------------------------------------------------------------- finding agents
def test_agents_reads_the_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(link.paths, "AGENT_RUN", str(tmp_path))
    monkeypatch.setattr(link, "_alive", lambda pid: pid in (1, 2))
    port_file(tmp_path, 1, 17311, "aa", 1000)
    port_file(tmp_path, 2, 17312, "bb", 2000)
    (tmp_path / "agent.x.port").write_text("nonsense", encoding="utf-8")
    port_file(tmp_path, 9, 17319, "dead", 3000)          # not alive: left out
    assert link.agents() == [(17312, 2, "bb"), (17311, 1, "aa")]


def test_agents_without_a_run_folder(tmp_path):
    assert link.agents(str(tmp_path / "missing")) == []


# ---------------------------------------------------------------- the handshake
def test_auth_then_one_line_per_request(monkeypatch, tmp_path):
    sock = Sock([b"ok\n", b'{"ok":true,"n":5}\n'])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    assert (a.port, a.pid) == (17311, 1)
    assert a.json("mem") == {"ok": True, "n": 5}
    assert sock.sent == [b"auth sec\n", b"mem\n"]        # the secret stays on the port file, not self


def test_a_leftover_line_is_reused_before_asking_again(monkeypatch, tmp_path):
    sock = Sock([b"ok\nextra\n"])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    assert a.send("x") == "extra"
    assert sock.replies == []


def test_a_bad_secret_is_refused(monkeypatch, tmp_path):
    sock = Sock([b"no\n"])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    with pytest.raises(link.AgentError, match="refused"):
        link.AgentLink()
    assert sock.closed is True


def test_a_connection_says_do_not_delay(monkeypatch, tmp_path):
    sock = Sock([b"ok\n"])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    link.AgentLink()
    assert sock.options == [(link.socket.IPPROTO_TCP, link.socket.TCP_NODELAY, 1)]


def test_a_closed_connection_is_an_error(monkeypatch, tmp_path):
    sock = Sock([b"ok\n", b""])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    with pytest.raises(link.AgentError, match="closed"):
        a.send("x")


def test_no_game_yet(monkeypatch, tmp_path):
    monkeypatch.setattr(link.paths, "AGENT_RUN", str(tmp_path))
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    monkeypatch.setattr(link.socket, "create_connection", lambda *a, **k: pytest.fail("connected"))
    with pytest.raises(link.AgentError, match="no_game"):
        link.AgentLink()


def test_connection_refused_is_left_to_the_caller(monkeypatch, tmp_path):
    def refuse(addr, timeout=None):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(link.paths, "AGENT_RUN", str(tmp_path))
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    monkeypatch.setattr(link.socket, "create_connection", refuse)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    with pytest.raises(ConnectionRefusedError):
        link.AgentLink()


def test_the_pid_picks_its_own_game(monkeypatch, tmp_path):
    sock = Sock([b"ok\n"])
    seen = []
    monkeypatch.setattr(link.paths, "AGENT_RUN", str(tmp_path))
    monkeypatch.setattr(link, "_alive", lambda pid: pid in (1, 2))
    monkeypatch.setattr(link.socket, "create_connection",
                        lambda addr, timeout=None: seen.append(addr) or sock)
    port_file(tmp_path, 1, 17311, "aa", 1000)
    port_file(tmp_path, 2, 17312, "bb", 2000)
    a = link.AgentLink(pid=2)
    assert a.pid == 2 and seen == [("127.0.0.1", 17312)]


def test_close_swallows_an_error_and_the_context_manager_closes(monkeypatch, tmp_path):
    sock = Sock([b"ok\n"])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    with link.AgentLink() as a:
        assert a.pid == 1
    assert sock.closed is True

    class BadClose(Sock):
        def close(self):
            raise OSError("already gone")

    bad = BadClose([b"ok\n"])
    patch(monkeypatch, tmp_path, bad)
    link.AgentLink().close()                             # no exception


def test_json_rejects_a_bad_reply(monkeypatch, tmp_path):
    sock = Sock([b"ok\n", b"not json\n"])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    with pytest.raises(link.AgentError, match="bad reply"):   # a link problem, not a bot bug
        link.AgentLink().json("mem")


# ---------------------------------------------------------------- games
class StubAgent:
    def __init__(self, pid=None, timeout=10):
        self.pid = pid

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def json(self, line):
        return {"ok": True, "self": {"name": "Hero"}, "map": {"name": "Town"}}


def test_games_names_every_live_game(monkeypatch):
    monkeypatch.setattr(link, "agents", lambda run_dir=None: [(17311, 1, "s"), (17312, 2, "t")])
    monkeypatch.setattr(link, "AgentLink", StubAgent)
    assert link.games() == [(1, "Hero", "Town"), (2, "Hero", "Town")]


def test_games_reports_a_game_that_will_not_answer(monkeypatch):
    class Bad(StubAgent):
        def json(self, line):
            raise link.AgentError("closed")

    monkeypatch.setattr(link, "agents", lambda run_dir=None: [(17311, 1, "s")])
    monkeypatch.setattr(link, "AgentLink", Bad)
    assert link.games() == [(1, None, None)]


def test_games_leaves_an_unlogged_in_game_blank(monkeypatch):
    class NotOk(StubAgent):
        def json(self, line):
            return {"ok": False}

    monkeypatch.setattr(link, "agents", lambda run_dir=None: [(17311, 1, "s")])
    monkeypatch.setattr(link, "AgentLink", NotOk)
    assert link.games() == [(1, None, None)]


# ---------------------------------------------------------------- real loopback socket
def _server_file(tmp_path, server, secret):
    port_file(tmp_path, 1, server.port, secret, 1000)


def test_real_socket_auth_and_one_line_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    with FakeAgent(replies=[b'{"ok":true,"n":5}\n']) as server:
        _server_file(tmp_path, server, "sec")
        with link.AgentLink(run_dir=str(tmp_path)) as a:
            assert a.json("mem") == {"ok": True, "n": 5}
        assert server.seen == [b"mem\n"]


def test_real_socket_refuses_a_wrong_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    with FakeAgent(secret="right") as server:
        _server_file(tmp_path, server, "wrong")
        with pytest.raises(link.AgentError, match="refused"):
            link.AgentLink(run_dir=str(tmp_path))
        server.thread.join(2)             # the server answered and hung up


def test_real_socket_closed_after_auth_is_a_link_error(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    with FakeAgent(replies=[]) as server:
        _server_file(tmp_path, server, "sec")
        a = link.AgentLink(run_dir=str(tmp_path))
        try:
            with pytest.raises(link.AgentError, match="closed"):
                a.send("mem")
        finally:
            a.close()


def test_real_socket_garbled_reply_is_a_bad_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(link, "_alive", lambda pid: True)
    with FakeAgent(replies=[b"this is not json\n"]) as server:
        _server_file(tmp_path, server, "sec")
        a = link.AgentLink(run_dir=str(tmp_path))
        try:
            with pytest.raises(link.AgentError, match="bad reply"):
                a.json("mem")
        finally:
            a.close()


def test_a_request_that_times_out_ends_the_link_instead_of_reading_the_next_answer_late(monkeypatch, tmp_path):
    class SlowSock(Sock):
        def recv(self, n):
            if len(self.sent) == 2:
                raise TimeoutError("timed out")       # the game took longer than the timeout
            return super().recv(n)

    sock = SlowSock([b"ok\n", b'{"late": "the bag"}\n'])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    with pytest.raises(link.AgentError, match="timed out"):
        a.send("mem bag")
    assert sock.closed
    with pytest.raises(link.AgentError, match="timed out"):
        a.send("mem self")                 # never the late answer to "mem bag" as our HP
    assert sock.sent == [b"auth sec\n", b"mem bag\n"]


def test_a_connection_closed_mid_reply_stays_closed(monkeypatch, tmp_path):
    sock = Sock([b"ok\n", b'{"half'])           # then the socket ends
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    with pytest.raises(link.AgentError, match="closed"):
        a.send("mem")
    sock.replies = [b'"rest"}\n']
    with pytest.raises(link.AgentError):
        a.send("ping")
