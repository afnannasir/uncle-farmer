# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""The rest of unclefarmer/messages.py: the text files, the reply book, the messenger loop,
stranger replies, and every trusted-player command handler.

tests/test_messages.py already covers parse_command's common cases, details(), cmd_help and
cmd_buff. This file adds only what it misses and never touches the game, the network or wx.
"""

import collections
import queue
import threading
import time
import types

import pytest

from unclefarmer import messages
from unclefarmer.clock import CLOCK


# ---------------------------------------------------------------- helpers
class FakeLink:
    """The agent side the Messenger talks to, scripting replies per request."""

    def __init__(self):
        self.sent = []
        self.closed = False
        self.version = "mwagent 16"
        self.mem = {}
        self.bag = []
        self.dialog = {"ok": True, "open": False}
        self.last = 0
        self.pms = []
        self.on_pm = None
        self.pm_result = "sent"
        self.close_result = "closing"
        self.speech_off_result = "off"
        self.speech_on_result = "on"

    def send(self, line):
        self.sent.append(line)
        if line == "version":
            return self.version
        if line == "speech off":
            return self.speech_off_result
        if line == "speech on":
            return self.speech_on_result
        if line.startswith("pm "):
            return self.pm_result
        if line == "closegame":
            return self.close_result
        return "ok"

    def json(self, line):
        if line == "mem":
            return self.mem
        if line == "mem bag":
            return self.bag
        if line == "dialog":
            return self.dialog
        if line.startswith("pms"):
            if self.on_pm is not None:
                return self.on_pm(line)
            return {"last": self.last, "pms": self.pms}
        return {}

    def close(self):
        self.closed = True


class FakeHost:
    """The window's side of the Messenger, recording every call."""

    def __init__(self, run=None, character="Farmer", start_result="Started.",
                 errand_result="busy", follow_result="Following.", unfollow_result="Unfollowed."):
        self.run = run
        self.character_ = character
        self.logs = []
        self.started = []
        self.stopped = []
        self.errands = []
        self.follow_asks = []
        self.unfollow_asks = []
        self.calls = []
        self.start_result = start_result
        self.errand_result = errand_result
        self.follow_result = follow_result
        self.unfollow_result = unfollow_result

    def run_for(self, pid):
        return self.run

    def call(self, fn, *a):
        self.calls.append((fn, a))
        return fn(*a)

    def log(self, line, speak=False, who=None, private=False):
        self.logs.append((line, speak, who, private))

    def character(self, pid):
        return self.character_

    def start_bot(self, pid, plan):
        self.started.append((pid, plan))
        return self.start_result

    def stop_bot(self, pid):
        self.stopped.append(pid)

    def errand_for(self, pid, text, title, tell):
        self.errands.append((pid, text, title))
        return self.errand_result

    def follow_for(self, pid, name, tell):
        self.follow_asks.append((pid, name))
        return self.follow_result

    def unfollow(self, pid):
        self.unfollow_asks.append(pid)
        return self.unfollow_result

    def locate(self, name):
        return None

    def last_seen(self, name):
        return None


def make(host=None, link=None):
    m = messages.Messenger(1, host or FakeHost())
    m.link = link or FakeLink()
    return m


def default_cfg(**over):
    return {**messages.DEFAULTS, **over}


@pytest.fixture(autouse=True)
def _no_repo_writes(tmp_path, monkeypatch):
    """Never let a test touch the real messages folder in the repo."""
    monkeypatch.setattr(messages, "MESSAGES_DIR", str(tmp_path / "messages"))
    monkeypatch.setattr(messages, "REPLIES_FILE", str(tmp_path / "messages" / "replies.txt"))
    monkeypatch.setattr(messages, "GOODBYES_FILE", str(tmp_path / "messages" / "goodbyes.txt"))


# ---------------------------------------------------------------- settings and texts
def test_config_overlays_the_saved_settings(monkeypatch):
    monkeypatch.setattr(messages.settings, "load_app", lambda: {"pm_max_replies": 9})
    c = messages.config()
    assert c["pm_max_replies"] == 9
    assert c["pm_replies"] == "running"


def test_lines_creates_the_file_from_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(messages, "MESSAGES_DIR", str(tmp_path / "messages"))
    path = tmp_path / "messages" / "r.txt"
    got = messages._lines(str(path), "one\n# comment\n\n two \n")
    assert got == ["one", "two"]
    assert path.read_text(encoding="utf-8") == "one\n# comment\n\n two \n"
    assert (tmp_path / "messages").is_dir()


def test_replies_and_goodbyes_use_the_files(tmp_path, monkeypatch):
    monkeypatch.setattr(messages, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(messages, "REPLIES_FILE", str(tmp_path / "replies.txt"))
    monkeypatch.setattr(messages, "GOODBYES_FILE", str(tmp_path / "goodbyes.txt"))
    assert messages.replies() == [l for l in messages.DEFAULT_REPLIES.splitlines() if l.strip()]
    assert messages.goodbyes() == [l for l in messages.DEFAULT_GOODBYES.splitlines() if l.strip()]


def test_lines_creates_the_folder_of_the_file_it_is_given(tmp_path, monkeypatch):
    # The file's own folder is made; the old code made MESSAGES_DIR instead, whatever path it got.
    monkeypatch.setattr(messages, "MESSAGES_DIR", str(tmp_path / "module_dir"))
    other = tmp_path / "elsewhere" / "deeper" / "r.txt"
    messages._lines(str(other), "hi\n")
    assert other.read_text(encoding="utf-8") == "hi\n"
    assert other.parent.is_dir()
    assert not (tmp_path / "module_dir").exists()


def test_save_lines_strips_blanks(tmp_path, monkeypatch):
    monkeypatch.setattr(messages, "MESSAGES_DIR", str(tmp_path / "m"))
    path = tmp_path / "m" / "out.txt"
    messages.save_lines(str(path), ["  a  ", "", "b"])
    assert path.read_text(encoding="utf-8") == "a\nb\n"
    assert (tmp_path / "m").is_dir()


def test_parse_command_empty_and_bad_team_phrasings():
    assert messages.parse_command("") is None
    assert messages.parse_command("   ") is None
    assert messages.parse_command("leave now") is None
    assert messages.parse_command("quit now") is None
    assert messages.parse_command("leave team") == ("leave", "")
    assert messages.parse_command("/where") == ("where", "")


# ---------------------------------------------------------------- the reply book
def test_reply_book_hands_out_shuffled_lines(monkeypatch):
    monkeypatch.setattr(messages, "replies", lambda: ["a", "b"])
    monkeypatch.setattr(messages.random, "shuffle", lambda x: None)
    b = messages.ReplyBook()
    cfg = {"pm_quiet_minutes": 1, "pm_max_replies": 4}
    assert b.answer("Bob", cfg, now=100) == "b"
    assert b.answer("Bob", cfg, now=101) == "a"
    assert b.answer("Bob", cfg, now=102) == "b"        # the lines run out, so reloaded


def test_reply_book_with_no_replies_at_all(monkeypatch):
    monkeypatch.setattr(messages, "replies", lambda: [])
    b = messages.ReplyBook()
    assert b.answer("Bob", {"pm_quiet_minutes": 1, "pm_max_replies": 4}, now=100) is None


def test_reply_book_goodbye_then_quiet(monkeypatch):
    monkeypatch.setattr(messages, "replies", lambda: ["a"])
    monkeypatch.setattr(messages, "goodbyes", lambda: ["bye"])
    b = messages.ReplyBook()
    cfg = {"pm_quiet_minutes": 1, "pm_max_replies": 1}
    assert b.answer("Bob", cfg, now=100) == "a"
    assert b.answer("Bob", cfg, now=101) == "bye"
    assert b.answer("Bob", cfg, now=102) is None       # inside the silence
    assert b.answer("Bob", cfg, now=1000) == "a"       # the silence has run out
    assert b.people["bob"]["count"] == 1


def test_reply_book_without_goodbyes(monkeypatch):
    monkeypatch.setattr(messages, "replies", lambda: ["a"])
    monkeypatch.setattr(messages, "goodbyes", lambda: [])
    b = messages.ReplyBook()
    assert b.answer("Bob", {"pm_quiet_minutes": 1, "pm_max_replies": 0}, now=1) is None


def test_reply_book_forget():
    b = messages.ReplyBook()
    b.answer("Bob", {"pm_quiet_minutes": 1, "pm_max_replies": 1}, now=1)
    assert b.people
    b.forget()
    assert b.people == {}


# ---------------------------------------------------------------- the loop
def test_messenger_init_and_stop():
    host = FakeHost()
    m = messages.Messenger(3, host)
    assert m.pid == 3 and m.host is host
    assert m.name == "unclefarmer-pm-3"
    assert m.outbox == collections.deque() and m.pending == set()
    assert isinstance(m.book, messages.ReplyBook)
    m.stop()
    assert m.halt.is_set()


def test_run_arms_loops_and_closes(monkeypatch):
    host = FakeHost()
    m = messages.Messenger(1, host)
    link = FakeLink()
    monkeypatch.setattr(messages, "AgentLink", lambda pid=None: link)
    monkeypatch.setattr(messages, "config", lambda: default_cfg())
    seen = []

    def on_pm(line):
        seen.append(line)
        if len(seen) >= 2:
            m.halt.set()
        return {"last": 1, "pms": [{"name": "Bob", "text": "hi", "id": 5}]}

    link.on_pm = on_pm
    m.run()
    assert link.closed is True
    assert m.ready is False
    assert "arm" in link.sent
    assert seen[0] == "pms 0"
    assert any(line.startswith("pms 1 ") for line in seen)


def test_run_gives_up_on_an_old_agent(monkeypatch):
    m = messages.Messenger(1, FakeHost())
    link = FakeLink()
    link.version = "mwagent 15"
    monkeypatch.setattr(messages, "AgentLink", lambda pid=None: link)
    m.run()
    assert m.unsupported is True
    assert "arm" not in link.sent
    assert link.closed is True


def test_run_survives_the_game_closing(monkeypatch):
    m = messages.Messenger(1, FakeHost())
    link = FakeLink()
    monkeypatch.setattr(messages, "AgentLink", lambda pid=None: link)

    def boom(line):
        raise messages.AgentError("gone")

    link.on_pm = boom
    m.run()
    assert link.closed is True
    assert m.ready is False


def test_note_players_remembers_only_trusted_players(monkeypatch):
    m = make()
    m.link.mem = {"ok": True, "map": {"id": 7}, "entities": [
        {"kind": "player", "name": "Ann", "x": 1, "y": 2},
        {"kind": "monster", "name": "wolf", "x": 3, "y": 4},
        {"kind": "player", "name": "Stranger", "x": 5, "y": 6},
    ]}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.note_players()
    assert set(m.sightings) == {"ann"}
    assert m.sightings["ann"][:3] == (7, 1, 2)


def test_note_players_silent_when_not_logged_in(monkeypatch):
    m = make()
    m.link.mem = {"ok": False}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.note_players()
    assert m.sightings == {}


def test_say_hands_the_line_to_the_window():
    host = FakeHost()
    m = make(host)
    m.say("hello", True, private=True)
    assert host.logs == [("hello", True, "Farmer", True)]


# ---------------------------------------------------------------- arriving messages
def test_received_trusted_command_is_dispatched(monkeypatch):
    host = FakeHost()
    m = make(host)
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.received({"name": "Ann", "text": "where", "id": 9})
    assert host.logs[0][0] == "message from Ann: where"
    assert any(item[0] == 9 for item in m.outbox)


def test_received_trusted_chat_away_note_once(monkeypatch):
    host = FakeHost(run=types.SimpleNamespace())
    m = make(host)
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"], pm_away_note=True))
    m.received({"name": "Ann", "text": "hi there", "id": 9})
    assert any("away" in item[2].lower() for item in m.outbox)
    before = len(m.outbox)
    m.received({"name": "Ann", "text": "still here", "id": 9})
    assert len(m.outbox) == before                # noted, not repeated


def test_received_trusted_chat_without_a_run_stays_silent(monkeypatch):
    m = make()
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.received({"name": "Ann", "text": "hi there", "id": 9})
    assert not m.outbox


def test_received_commands_off_is_treated_as_chat(monkeypatch):
    host = FakeHost(run=types.SimpleNamespace())
    m = make(host)
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"], pm_commands=False))
    m.received({"name": "Ann", "text": "where", "id": 9})
    assert not any("Not logged" in item[2] for item in m.outbox)


def test_received_without_a_sender_id_is_ignored(monkeypatch):
    m = make()
    monkeypatch.setattr(messages, "config", lambda: default_cfg())
    m.received({"name": "Bob", "text": "hi"})
    assert not m.outbox


@pytest.mark.parametrize("text, extra", [("hey", {"pm_replies": "never"}),
                                         ("hey", {"pm_replies": "running"})])
def test_received_stranger_left_alone(monkeypatch, text, extra):
    m = make()
    monkeypatch.setattr(messages, "config", lambda: default_cfg(**extra))
    m.received({"name": "Bob", "text": text, "id": 3})
    assert not m.outbox


def test_received_stranger_is_answered_once_per_burst(monkeypatch):
    m = make()
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_replies="always", pm_delay=[0, 0]))
    monkeypatch.setattr(messages, "replies", lambda: ["hi"])
    monkeypatch.setattr(messages.random, "shuffle", lambda x: None)
    monkeypatch.setattr(messages.random, "uniform", lambda a, b: 0.0)
    m.received({"name": "Bob", "text": "one", "id": 3})
    assert len(m.outbox) == 1 and "bob" in m.pending
    m.received({"name": "Bob", "text": "two", "id": 3})
    assert len(m.outbox) == 1


def test_received_stranger_delay_uses_the_high_end(monkeypatch):
    m = make()
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_replies="always", pm_delay=[1, 5]))
    monkeypatch.setattr(messages, "replies", lambda: ["hi"])
    monkeypatch.setattr(messages.random, "shuffle", lambda x: None)
    seen = []
    monkeypatch.setattr(messages.random, "uniform", lambda a, b: seen.append((a, b)) or b)
    m.received({"name": "Bob", "text": "one", "id": 3})
    assert seen == [(1, 5)]
    assert m.outbox[0][3] > time.time()


# ---------------------------------------------------------------- waiting and sending
def test_wait_for_empty_and_past(monkeypatch):
    m = make()
    monkeypatch.setattr(CLOCK, "time", lambda: 1000.0)
    assert m.wait_for() == messages.WAIT
    m.last_sent = 1000.0
    m.outbox.append((1, "bob", "x", 0.0))
    monkeypatch.setattr(CLOCK, "time", lambda: 1005.0)
    assert m.wait_for() == 0.0


def test_send_first_jumps_the_queue():
    m = make()
    m.send(1, "a", "x")
    m.send(1, "b", "y", first=True)
    assert [item[1] for item in m.outbox] == ["b", "a"]


def test_send_queues_a_long_line_whole():
    # the agent (v35) cuts a line over the game's 200 characters into parts, so nothing is lost here
    m = make()
    m.send(1, "a", "z" * 500)
    assert len(m.outbox[0][2]) == 500


def test_flush_sends_a_due_line_and_clears_pending(monkeypatch):
    host = FakeHost()
    m = make(host)
    monkeypatch.setattr(CLOCK, "time", lambda: 100.0)
    m.pending.add("bob")
    m.outbox.append((5, "Bob", "hi", 0.0))
    m.flush()
    assert "pm 5 hi" in m.link.sent
    assert "bob" not in m.pending
    assert m.last_sent == 100.0
    assert host.logs[-1][0] == "message to Bob: hi"


def test_flush_reports_a_refused_line(monkeypatch):
    host = FakeHost()
    m = make(host)
    m.link.pm_result = "busy"
    monkeypatch.setattr(CLOCK, "time", lambda: 100.0)
    m.outbox.append((5, "Bob", "hi", 0.0))
    m.flush()
    assert "couldn't message Bob: busy" == host.logs[-1][0]


def test_flush_waits_for_its_turn(monkeypatch):
    m = make()
    monkeypatch.setattr(CLOCK, "time", lambda: 100.0)
    m.last_sent = 99.0                          # the game's 2 s rule
    m.outbox.append((5, "Bob", "hi", 0.0))
    m.flush()
    assert m.outbox
    m.last_sent = 0.0
    m.outbox.append((5, "Bob", "later", 200.0))  # not due yet
    m.flush()
    assert [item[2] for item in m.outbox] == ["later"]


def test_flush_now_uses_the_messenger_clock(clock):
    m = make()
    m.clock = clock
    m.outbox.append((5, "Bob", "later", clock.now + 1))
    m.flush_now()
    assert clock.now == pytest.approx(1001.2)
    assert not m.outbox


def test_flush_now_drains_the_queue(monkeypatch):
    m = make()
    monkeypatch.setattr(CLOCK, "time", lambda: 100.0)
    monkeypatch.setattr(CLOCK, "sleep", lambda seconds: None)
    m.send(1, "Bob", "hi")
    m.flush_now()
    assert not m.outbox


# ---------------------------------------------------------------- dispatching commands
def test_command_logs_asks_and_queues_the_answer(monkeypatch):
    host = FakeHost()
    m = make(host)
    m.link.mem = {"ok": False}
    assert m.command(4, "Ann", "status", "") is None
    assert host.logs[0][0] == "Ann asks: status"
    assert m.outbox[0][:2] == (4, "Ann")


def test_command_turns_an_error_into_an_answer(allow_ignored_failures):
    m = make()
    assert m.command(4, "Ann", "nope", "") is None
    assert "that didn't work" in m.outbox[0][2]


def test_snap_reports_ok_and_not():
    m = make()
    m.link.mem = {"ok": True, "x": 1}
    assert m.snap() == {"ok": True, "x": 1}
    m.link.mem = {"ok": False}
    assert m.snap() is None


# ---------------------------------------------------------------- status and place
def test_cmd_status_idle_and_not_logged_in():
    m = make()
    assert m.cmd_status(1, "Ann", "") == "Not logged in."
    m.link.mem = {"ok": True, "map": {"name": "Farm"},
                  "self": {"hp": 50, "maxhp": 100, "vitals": {"mp": 10}, "sheet": {"maxmp": 20}}}
    assert m.cmd_status(1, "Ann", "") == "Idle on Farm, HP 50%, MP 50%."
    m.link.mem = {"ok": True, "map": {"name": "Farm"}, "self": {"hp": 50, "maxhp": 100}}
    assert m.cmd_status(1, "Ann", "") == "Idle on Farm, HP 50%."


def test_cmd_status_while_running(monkeypatch):
    run = types.SimpleNamespace(title="wolves", started=time.time() - 130,
                                tally=types.SimpleNamespace(result=lambda: {"kills": 3}),
                                bot=types.SimpleNamespace(paused=threading.Event()))
    host = FakeHost(run=run)
    m = make(host)
    m.link.mem = {"ok": True, "map": {"name": "Farm"}, "self": {"hp": 50, "maxhp": 100}}
    assert m.cmd_status(1, "Ann", "") == "running wolves for 2 min on Farm, HP 50%, 3 kills"
    run.bot.paused.set()
    assert m.cmd_status(1, "Ann", "") == "paused wolves for 2 min on Farm, HP 50%, 3 kills"


def test_cmd_where():
    m = make()
    assert m.cmd_where(1, "Ann", "") == "Not logged in."
    m.link.mem = {"ok": True, "map": {"name": "Town"}, "self": {"x": 10.9, "y": 2.1}}
    assert m.cmd_where(1, "Ann", "") == "Town at 10, 2"


# ---------------------------------------------------------------- results
def test_cmd_results_this_run_last_run_and_none(monkeypatch):
    monkeypatch.setattr(messages.stats, "short", lambda r: "S")
    monkeypatch.setattr(messages.stats, "runs", lambda who, since=None, kind=None: [])
    run = types.SimpleNamespace(tally=types.SimpleNamespace(result=lambda: {"kills": 1}))
    assert make(FakeHost(run=run)).cmd_results(1, "Ann", "") == "This run: S"
    assert make().cmd_results(1, "Ann", "") == "No results yet."
    monkeypatch.setattr(messages.stats, "runs", lambda who, since=None: [{"kills": 0}])
    assert make().cmd_results(1, "Ann", "") == "Last run: S"


def test_cmd_today_empty_and_totals(monkeypatch):
    monkeypatch.setattr(messages.stats, "runs", lambda who, since=None, kind=None: [])
    monkeypatch.setattr(messages.stats, "short", lambda r: "S")
    assert make().cmd_today(1, "Ann", "") == "Nothing today yet."
    run = types.SimpleNamespace(tally=types.SimpleNamespace(
        results=lambda: [{"kills": 1, "kind": "farming"}, {"kind": "tool"}]))     # a job on the way: not farming
    monkeypatch.setattr(messages.stats, "add_up",
                        lambda rs: {"runs": 2, "minutes": 90.5})
    assert make(FakeHost(run=run)).cmd_today(1, "Ann", "") == "Today, 2 runs, 90 min: S"


# ---------------------------------------------------------------- bag and drop time
def test_cmd_bag_lists_potions_and_load():
    m = make()
    m.link.bag = [{"name": "Small Potion", "count": 3},
                  {"name": "Sword", "count": 1},
                  {"name": "Big potion", "count": 2}]
    m.link.mem = {"ok": True, "self": {"sheet": {"load": 10, "maxload": 50}}}
    assert m.cmd_bag(1, "Ann", "") == "load 10 of 50, Small Potion 3, Big potion 2"


def test_cmd_bag_without_a_readable_bag():
    m = make()
    m.link.bag = {"not": "a list"}
    assert m.cmd_bag(1, "Ann", "") == "no potions"


def test_cmd_drop_left_and_unread():
    m = make()
    assert m.cmd_drop(1, "Ann", "") == "Drop time not read yet."
    m.link.mem = {"ok": True, "self": {"sheet": {"drop_seconds": 3661, "age": 61}}}
    assert m.cmd_drop(1, "Ann", "") == "Drop time left: 1 h 0 min"
    m.link.mem = {"ok": True, "self": {"sheet": {"drop_seconds": 10, "age": 100}}}
    assert m.cmd_drop(1, "Ann", "") == "Drop time left: 0 h 0 min"


# ---------------------------------------------------------------- run control
def test_cmd_pause_and_resume_without_a_run():
    m = make()
    assert m.cmd_pause(1, "Ann", "") == "The bot isn't running."
    assert m.cmd_resume(1, "Ann", "") == "The bot isn't running."


def test_cmd_pause_and_resume_hold_the_bot():
    bot = types.SimpleNamespace(paused=threading.Event())
    run = types.SimpleNamespace(bot=bot)
    m = make(FakeHost(run=run))
    assert m.cmd_pause(1, "Ann", "") == "Paused. Send resume to go on."
    assert bot.paused.is_set()
    assert m.cmd_resume(1, "Ann", "") == "Going on."
    assert not bot.paused.is_set()


def test_cmd_stop():
    assert make().cmd_stop(1, "Ann", "") == "The bot isn't running."
    host = FakeHost(run=types.SimpleNamespace())
    assert make(host).cmd_stop(1, "Ann", "") == "Stopping."
    assert host.stopped == [1]


def test_cmd_start():
    host = FakeHost(run=types.SimpleNamespace())
    assert make(host).cmd_start(1, "Ann", "") == "Already running."
    host2 = FakeHost()
    m = make(host2)
    assert m.cmd_start(1, "Ann", "wolves") == "Started."
    assert host2.started == [(1, "wolves")]


def test_cmd_start_blank_plan_is_none():
    host = FakeHost()
    make(host).cmd_start(1, "Ann", "   ")
    assert host.started == [(1, None)]


def test_cmd_mute_and_unmute():
    m = make()
    assert m.cmd_mute(1, "Ann", "") == "The game is quiet."
    assert m.cmd_unmute(1, "Ann", "") == "The game talks again."
    m.link.speech_off_result = "no"
    m.link.speech_on_result = "no"
    assert m.cmd_mute(1, "Ann", "") == "Couldn't."
    assert m.cmd_unmute(1, "Ann", "") == "Couldn't."


def test_cmd_quit_closes_and_reports_failure(monkeypatch):
    host = FakeHost(run=types.SimpleNamespace())
    m = make(host)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_quit(1, "Ann", "") is None
    assert host.stopped == [1]
    assert "closegame" in m.link.sent
    assert any("Closing the game." == item[2] for item in m.outbox)
    m.link.close_result = "nope"
    assert m.cmd_quit(1, "Ann", "") == "Couldn't close it: nope"


# ---------------------------------------------------------------- chores
def test_cmd_home_queues_for_a_running_bot():
    bot = types.SimpleNamespace(requests=queue.Queue())
    m = make(FakeHost(run=types.SimpleNamespace(bot=bot)))
    assert m.cmd_home(1, "Ann", "") == "Going home."
    assert bot.requests.qsize() == 1


def test_cmd_home_takes_a_farming_run_back_to_where_it_started():
    from unclefarmer.engine.bot import Stopped
    bot = types.SimpleNamespace(requests=queue.Queue(), start_place=(1503, 40, 60), end_place=None)
    m = make(FakeHost(run=types.SimpleNamespace(bot=bot, walk=False)))
    assert m.cmd_home(1, "Ann", "") == "Going back to where farming started."
    job = bot.requests.get_nowait()
    with pytest.raises(Stopped) as stop:
        job(bot)
    assert bot.end_place == ("place", 1503, [40, 60])      # the run's own ending travels there
    assert stop.value.asked is True                        # asked for: no closing the game after it


def test_cmd_home_with_nothing_running_goes_where_the_last_farm_started(monkeypatch):
    host = FakeHost(errand_result="")              # the errand starts (no "busy")
    host.farm_start_for = lambda pid: (1503, 40, 60)
    m = make(host)
    assert m.cmd_home(1, "Ann", "") == "Going back to where farming started."
    assert host.errands and host.errands[-1][1] == "go 1503 at 40,60"


def test_cmd_home_raises_stopped_when_a_run_is_going(monkeypatch):
    from unclefarmer.engine.bot import Stopped
    m = make()
    bot = types.SimpleNamespace(go_home=lambda tries=1: True)
    monkeypatch.setattr(m, "idle_bot", lambda: bot)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    with pytest.raises(Stopped):
        m.cmd_home(1, "Ann", "")            # idle_bot_ref is unset, so the run must end here


def test_cmd_home_when_idle(monkeypatch):
    m = make()
    bot = types.SimpleNamespace(go_home=lambda tries=1: True)
    m.idle_bot_ref = bot
    monkeypatch.setattr(m, "idle_bot", lambda: bot)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_home(1, "Ann", "") is None
    assert any(item[2] == "Home." for item in m.outbox)


def test_cmd_home_failure_and_not_logged_in(monkeypatch):
    m = make()
    bot = types.SimpleNamespace(go_home=lambda tries=1: False)
    m.idle_bot_ref = bot
    monkeypatch.setattr(m, "idle_bot", lambda: bot)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_home(1, "Ann", "") is None
    assert any("hearthstone didn't work" in item[2] for item in m.outbox)
    monkeypatch.setattr(m, "idle_bot", lambda: None)
    assert m.cmd_home(1, "Ann", "") == "Not logged in."


def test_cmd_come_with_an_unreadable_location(monkeypatch):
    m = make()
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    answer = m.cmd_come(1, "Ann", "gibberish")
    assert answer is not None and answer.startswith("I can't tell the map")


def test_cmd_come_from_a_running_bot(monkeypatch):
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    bot = types.SimpleNamespace(requests=queue.Queue())
    m = make(FakeHost(run=types.SimpleNamespace(bot=bot)))
    assert m.cmd_come(1, "Ann", "") == "Coming."
    assert bot.requests.qsize() == 1


def test_cmd_come_while_following(monkeypatch):
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    bot = types.SimpleNamespace(requests=queue.Queue())
    run = types.SimpleNamespace(bot=bot, follows="ann")
    m = make(FakeHost(run=run))
    assert m.cmd_come(1, "Ann", "") == "Coming."
    assert bot.requests.qsize() == 1


def test_cmd_come_when_idle_opens_an_errand(monkeypatch):
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (5, (1, 2)))
    host = FakeHost(errand_result="busy")
    m = make(host)
    assert m.cmd_come(1, "Ann", "Ann's spot") == "busy"
    assert host.errands[0][1] == 'come "Ann" 5 at 1,2'
    host.errand_result = None
    assert make(host).cmd_come(1, "Ann", "Ann's spot") == "Coming."


def test_cmd_come_idle_tells_through_the_errand(monkeypatch):
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    host = FakeHost()
    m = make(host)

    def errand(pid, text, title, tell):
        tell("message")
        return "busy"

    monkeypatch.setattr(host, "errand_for", errand)
    assert m.cmd_come(1, "Ann", "") == "busy"
    assert any(item[2] == "message" for item in m.outbox)


def test_cmd_come_closer_when_the_run_is_following(monkeypatch):
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    monkeypatch.setattr(follow, "come_to", lambda bot, name, mid, cell: "Here!")
    m = make()
    m.host.run = types.SimpleNamespace(bot=None, follows="ann")
    monkeypatch.setattr(m, "idle_bot", lambda: object())
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_come(1, "Ann", "") is None
    assert any(item[2] == "Here!" for item in m.outbox)


def test_cmd_come_job_ends_the_run_beside_them(monkeypatch):
    from unclefarmer.engine.bot import Stopped
    import unclefarmer.engine.follow as follow
    monkeypatch.setattr(follow, "parse_location", lambda text: (None, None))
    monkeypatch.setattr(follow, "come_to", lambda bot, name, mid, cell: "Here!")
    m = make()
    m.host.run = types.SimpleNamespace(bot=None, follows="someoneelse")
    monkeypatch.setattr(m, "idle_bot", lambda: object())
    monkeypatch.setattr(m, "flush_now", lambda: None)
    with pytest.raises(Stopped) as caught:
        m.cmd_come(1, "Ann", "")
    assert caught.value.stay is True


def test_cmd_follow_tells_through_the_host(monkeypatch):
    host = FakeHost()
    m = make(host)

    def follow_for(pid, name, tell):
        tell("Following you.")
        return "ok"

    monkeypatch.setattr(host, "follow_for", follow_for)
    assert m.cmd_follow(1, "Ann", "") == "ok"
    assert any(item[2] == "Following you." for item in m.outbox)


def test_cmd_follow_and_unfollow():
    host = FakeHost()
    m = make(host)
    assert m.cmd_follow(1, "Ann", "") == "Following."
    assert host.follow_asks == [(1, "Ann")]
    assert m.cmd_unfollow(1, "Ann", "") == "Unfollowed."
    assert host.unfollow_asks == [1]


def test_chore_puts_the_job_on_a_running_bot():
    bot = types.SimpleNamespace(requests=queue.Queue())
    m = make(FakeHost(run=types.SimpleNamespace(bot=bot)))
    assert m.chore(lambda b: None, "Working.", 1, "Ann") == "Working."
    assert bot.requests.qsize() == 1


def test_chore_runs_the_job_when_idle(monkeypatch):
    m = make()
    bot = object()
    monkeypatch.setattr(m, "idle_bot", lambda: bot)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    seen = []
    assert m.chore(lambda b: seen.append(b), "Doing it.", 1, "Ann") is None
    assert seen == [bot]
    assert any(item[2] == "Doing it." for item in m.outbox)


def test_chore_without_a_bot(monkeypatch):
    m = make()
    monkeypatch.setattr(m, "idle_bot", lambda: None)
    assert m.chore(lambda b: None, None, 1, "Ann") == "Not logged in."


# ---------------------------------------------------------------- team commands
def test_cmd_add_when_idle(monkeypatch):
    import unclefarmer.engine.team as team
    monkeypatch.setattr(team, "accept_application", lambda bot, name: (True, "Accepted."))
    m = make()
    bot = object()
    monkeypatch.setattr(m, "idle_bot", lambda: bot)
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_add(1, "Ann", "") is None
    assert any(item[2] == "Accepted." for item in m.outbox)


def test_cmd_leader_and_leave_when_idle(monkeypatch):
    import unclefarmer.engine.team as team
    monkeypatch.setattr(team, "make_leader", lambda bot, name: (True, "You lead."))
    monkeypatch.setattr(team, "leave", lambda bot: (True, "Left."))
    m = make()
    monkeypatch.setattr(m, "idle_bot", lambda: object())
    monkeypatch.setattr(m, "flush_now", lambda: None)
    assert m.cmd_leader(1, "Ann", "") is None
    assert m.cmd_leave(1, "Ann", "") is None
    texts = [item[2] for item in m.outbox]
    assert "You lead." in texts and "Left." in texts


def test_cmd_add_queues_for_a_running_bot():
    bot = types.SimpleNamespace(requests=queue.Queue())
    m = make(FakeHost(run=types.SimpleNamespace(bot=bot)))
    assert m.cmd_add(1, "Ann", "") == "Looking for your application."
    assert bot.requests.qsize() == 1


# ---------------------------------------------------------------- the idle bot
def test_idle_bot_builds_one_from_the_link(monkeypatch):
    m = make()
    m.link.mem = {"ok": True, "self": {"name": "Farmer"}}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))

    class FakeBot:
        pass

    monkeypatch.setattr(messages, "Bot", lambda *a, **k: FakeBot())
    monkeypatch.setattr(messages.settings, "load_profile", lambda name: {"plan": "wolves"})
    bot = m.idle_bot()
    assert isinstance(bot, FakeBot)
    assert m.idle_bot_ref is bot
    assert bot.trusted.names == {"ann"} and not bot.trusted.ids


def test_idle_bot_without_a_snapshot():
    m = make()
    m.link.mem = {"ok": False}
    assert m.idle_bot() is None


# ---------------------------------------------------------------- invites while idle
def test_idle_invites_accepts_a_trusted_invite(monkeypatch):
    host = FakeHost()
    m = make(host)
    m.link.dialog = {"ok": True, "open": True,
                     "top": {"rows": [[{"text": "Ann invites you to join the \"Wolves\" team"}]]}}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    import unclefarmer.engine.team as team
    monkeypatch.setattr(team, "invite_in", lambda texts: ("Ann", "Wolves"))
    monkeypatch.setattr(team, "accept_invite", lambda bot: True)
    monkeypatch.setattr(m, "idle_bot", lambda: object())
    m.idle_invites()
    assert any("joined" in item[0] for item in host.logs)


def test_idle_invites_leaves_strangers_and_closed_dialogs(monkeypatch):
    host = FakeHost()
    m = make(host)
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.idle_invites()                                   # nothing open
    m.link.dialog = {"ok": True, "open": True, "top": {"rows": [[{"text": "x"}]]}}
    import unclefarmer.engine.team as team
    monkeypatch.setattr(team, "invite_in", lambda texts: ("Stranger", "Wolves"))
    m.idle_invites()
    assert not any("joined" in item[0] for item in host.logs)


def test_idle_invites_disabled(monkeypatch):
    host = FakeHost()
    m = make(host)
    m.link.dialog = {"ok": True, "open": True, "top": {"rows": [[{"text": "y"}]]}}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_accept_invites=False))
    import unclefarmer.engine.team as team
    monkeypatch.setattr(team, "invite_in", lambda texts: ("Ann", "Wolves"))
    m.idle_invites()
    assert not host.logs


def test_idle_invites_when_a_bot_runs(monkeypatch):
    host = FakeHost(run=types.SimpleNamespace())
    m = make(host)
    m.link.dialog = {"ok": True, "open": True, "top": {"rows": [[{"text": "y"}]]}}
    monkeypatch.setattr(messages, "config", lambda: default_cfg(pm_trusted=["Ann"]))
    m.idle_invites()
    assert not host.logs


def test_an_unexpected_messenger_error_leaves_a_traceback(monkeypatch, allow_ignored_failures):
    crashes = []

    class BadLink:
        def send(self, *a):
            raise RuntimeError("bug")

        def close(self):
            pass

    monkeypatch.setattr(messages, "AgentLink", lambda pid: BadLink())
    monkeypatch.setattr(messages.applog, "crash", lambda *a: crashes.append(a))
    m = messages.Messenger(7, FakeHost())
    m.run()                      # must not take the thread (or the test) down
    assert crashes and "7" in crashes[0][0]
    assert m.ready is False
