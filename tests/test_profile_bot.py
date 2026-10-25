"""profile_bot: the thread-aware profiler harness writes its report files (no GUI is started)."""

import os
import re
import threading

import pytest

import profile_bot


def test_yappi_splits_the_report_by_thread(tmp_path):
    pytest.importorskip("yappi")
    tool = profile_bot.Yappi(str(tmp_path), 0)
    tool.MIN_SECONDS = 0.0                        # keep the per-thread sections whatever the timing

    def work(n=400000):
        return sum(i * i for i in range(n))

    worker = threading.Thread(target=work, name="unclefarmer-run-7")
    worker.start()
    work()
    worker.join()
    tool.dump()
    tool.dump()                                   # the timer and the exit path both call it
    path = os.path.join(str(tmp_path), "yappi-wall.txt")
    text = open(path, encoding="utf-8").read()
    assert "Read each thread on its own" in text
    assert "unclefarmer-run-7" in text            # names are captured when the thread starts
    assert "MainThread" in text
    assert "thread_s" in text and "function" in text


class Fn:
    def __init__(self, name, module, total=0.0):
        self.name, self.module, self.ttot, self.ncall = name, module, total, 1


def test_wait_frames_are_recognised_across_modules():
    assert profile_bot.is_wait(Fn("Event.wait", r"C:\Python314\Lib\threading.py"))
    assert profile_bot.is_wait(Fn("socket.recv", r"C:\Python314\Lib\socket.py"))
    assert profile_bot.is_wait(Fn("Queue.get", r"C:\Python314\Lib\queue.py"))
    assert profile_bot.is_wait(Fn("MainFrame.MainLoop", r"C:\Python314\Lib\site-packages\wx\core.py"))
    assert not profile_bot.is_wait(Fn("AgentLink.send", r"F:\work\unclefarmer\unclefarmer\link.py"))
    assert not profile_bot.is_wait(Fn("Bot.fight", r"F:\work\unclefarmer\unclefarmer\engine\botparts\fighting.py"))


def test_the_main_thread_is_not_given_other_threads_work(tmp_path):
    pytest.importorskip("yappi")
    tool = profile_bot.Yappi(str(tmp_path), 0)
    tool.MIN_SECONDS = 0.0

    def main_only(n=300000):
        return sum(i * i for i in range(n))

    def worker_only(n=300000):
        return sum(i * i for i in range(n))

    worker = threading.Thread(target=worker_only, name="worker")
    worker.start()
    main_only()
    worker.join()
    tool.dump()
    text = open(os.path.join(str(tmp_path), "yappi-wall.txt"), encoding="utf-8").read()
    main_part, worker_part = text.split("=== worker")[0], text.split("=== worker")[1].split("=== ")[0]
    assert "worker_only" not in main_part          # ctx_id=0 must not mean "all contexts"
    assert "main_only" not in worker_part


def _calls(text, section, name):
    """The call counts a section of the report gives a function (the line is 'total_s calls
    path:line name (module:lineno)')."""
    part = text.split("=== %s" % section)[1].split("=== ")[0]
    return [int(n) for n in
            re.findall(r"\s+[\d.]+\s+(\d+)\s+[^\n]*?%s \(" % re.escape(name), part)]


def test_a_shared_function_is_split_by_the_calls_each_thread_made(tmp_path):
    # get_func_stats() with no argument merges a function that several threads call into one stat
    # tagged with one context: the wx thread was once credited with the farm thread's 66,463
    # AgentLink.send calls, and the main thread with another thread's Tally.result.
    pytest.importorskip("yappi")
    tool = profile_bot.Yappi(str(tmp_path), 0)
    tool.MIN_SECONDS = 0.0

    def shared(n=60000):
        return sum(i * i for i in range(n))

    def worker():
        for _ in range(4):
            shared()

    w = threading.Thread(target=worker, name="sharer")
    w.start()
    for _ in range(2):
        shared()
    w.join()
    tool.dump()
    text = open(os.path.join(str(tmp_path), "yappi-wall.txt"), encoding="utf-8").read()
    assert _calls(text, "sharer", "shared") == [4]          # its own four calls, not all six
    assert _calls(text, "MainThread", "shared") == [2]      # the left-over, not the sum


def test_names_are_captured_for_threads_that_override_run(tmp_path):
    # The app's own threads subclass threading.Thread and override run(): a patch on Thread.run
    # never sees them, which is why every one of them printed as "thread 2556".
    pytest.importorskip("yappi")

    class Farm(threading.Thread):
        def run(self) -> None:
            _ = sum(i * i for i in range(200000))

    tool = profile_bot.Yappi(str(tmp_path), 0)
    tool.MIN_SECONDS = 0.0
    farm = Farm(name="unclefarmer-run-42")
    farm.start()
    farm.join()
    tool.dump()
    text = open(os.path.join(str(tmp_path), "yappi-wall.txt"), encoding="utf-8").read()
    assert "=== unclefarmer-run-42 (tid" in text


def test_the_harness_timer_thread_is_left_out(tmp_path):
    # threading.Timer also overrides run(), so the timer that stops the profile used to appear in
    # the report as an unnamed thread whose wall clock was the whole run.
    pytest.importorskip("yappi")
    tool = profile_bot.Yappi(str(tmp_path), 0)
    tool.MIN_SECONDS = 0.0
    timer = threading.Timer(3600, lambda: None)
    timer.name = "profiler-timer"              # main() names it this way before starting it
    timer.daemon = True
    timer.start()
    try:
        tool.dump()
        text = open(os.path.join(str(tmp_path), "yappi-wall.txt"), encoding="utf-8").read()
    finally:
        timer.cancel()
    assert "profiler-timer" not in text


def test_nested_event_and_condition_waits_are_counted_once():
    event = Fn("Event.wait", r"C:\Python314\Lib\threading.py", 100.0)
    cond = Fn("Condition.wait", r"C:\Python314\Lib\threading.py", 99.5)
    recv = Fn("socket.recv", r"C:\Python314\Lib\socket.py", 20.0)
    waits = profile_bot.outer_waits([event, cond, recv])
    assert sum(fn.ttot for fn in waits) == 120.0


def test_tracemalloc_reports_size_and_growth(tmp_path):
    tool = profile_bot.Tracemalloc(str(tmp_path), 0)
    held = [bytearray(4096) for _ in range(20)]   # noqa: F841 - kept alive for the snapshot
    tool.dump()
    text = open(os.path.join(str(tmp_path), "tracemalloc.txt"), encoding="utf-8").read()
    assert "top 60 lines" in text and "growth since start" in text
    assert "MiB current" in text


def test_pyinstrument_is_gone():
    assert not hasattr(profile_bot, "Pyinstrument")
