"""Run Uncle Farmer from source under a profiler that understands threads and wx.

    python profile_bot.py --tool yappi --minutes 5
    python profile_bot.py --tool tracemalloc --minutes 30
    python profile_bot.py --tool all --minutes 5        # both

Reports go to logs\\profiles\\<time>\\. `--minutes` stops the profiler and writes the report
while the bot keeps running; without it the report is written when the app closes.

Only process-wide or thread-aware tools belong here: the bot runs a farming thread, a private
message thread (which long-polls the agent) and the wx main loop, so pyinstrument's single-thread
setprofile hook is out - it would miss both worker threads and interfere with wx. What the script
uses instead:

  yappi        C-level, per-thread wall clock; the report is split by thread
  tracemalloc  process-wide allocation tracing, split by line and file

For a no-restart snapshot on the live process, py-spy still works on all threads:

    py-spy record -p <pid> -d 60 -f speedscope -o %TEMP%\\unclefarmer.json

Micro-benchmarks (perf_counter_ns) live in benchmark.py.
"""

import argparse
import atexit
import collections
import os
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from unclefarmer.__main__ import main as app_main      # noqa: E402

# Frames that mean "this thread is not working, it is waiting". Their time is still part of the
# thread's wall clock, so it is reported separately instead of at the top of the busy list.
SKIP_THREADS = {"profiler-timer"}      # the --minutes timer just waits for the whole profile

SOCKET_WAITS = {"recv", "recv_into", "recvfrom", "sendall", "send", "accept", "connect"}
THREAD_WAITS = {"wait", "acquire", "join"}


def is_wait(fn):
    """True for the blocking calls a thread parks in: sockets, locks, wx's loop, sleep."""
    path = (fn.module or "").lower().replace("/", "\\")
    base = path.rsplit("\\", 1)[-1]
    name = fn.name.rsplit(".", 1)[-1]
    if base == "threading.py":
        return name in THREAD_WAITS
    if base == "queue.py":
        return name in ("get", "put")
    if base == "socket.py":
        return name in SOCKET_WAITS
    if base in ("select.py", "selectors.py"):
        return name in ("select", "poll", "epoll")
    if base == "core.py" and "\\wx\\" in path:
        return name == "MainLoop"
    if name == "sleep":
        return base == "time.py" or not path
    return False


def outer_waits(stats):
    """Waits without counting a nested one twice (Event.wait wraps Condition.wait; a queue wraps
    a condition). Socket reads and the wx loop are separate, so they stay alongside."""
    waits = [fn for fn in stats if is_wait(fn)]
    if any(fn.name.endswith("Event.wait") for fn in waits):
        waits = [fn for fn in waits if not fn.module.lower().endswith("threading.py")]
        waits += [fn for fn in stats if fn.name.endswith("Event.wait")]
    return waits


class Left:
    """A function stat with what the other threads made of it taken out (see Yappi._stats_for)."""

    def __init__(self, fn, ncall, ttot):
        self.name, self.module, self.lineno = fn.name, fn.module, fn.lineno
        self.full_name = getattr(fn, "full_name", "%s:%s %s" % (fn.module, fn.lineno, fn.name))
        self.ncall, self.ttot = ncall, ttot


class Yappi:
    """Wall clock per thread: the only honest answer to 'where did the run's time go'."""

    TOP_PER_THREAD = 25
    MIN_SECONDS = 0.5        # threads below this are listed in the table only

    def __init__(self, out, minutes):
        import yappi

        self.yappi = yappi
        self.out = out
        self.done = False
        self.lock = threading.Lock()
        self.started = time.time()
        self.names = {threading.get_ident(): threading.current_thread().name}
        self._orig_start = threading.Thread.start
        names, orig = self.names, self._orig_start

        def start(thread, *a, **kw):
            # Thread.start, not Thread.run: the app's own threads subclass threading.Thread and
            # override run(), so a patch on run() never sees them (they printed as "thread 2556").
            done = orig(thread, *a, **kw)
            if thread.ident is not None:
                names[thread.ident] = thread.name
            return done

        threading.Thread.start = start
        yappi.set_clock_type("wall")
        yappi.start(profile_threads=True, builtins=False)

    def _thread_name(self, st):
        return self.names.get(st.tid) or ("thread %s" % st.tid)

    def _stats_for(self, st):
        """This thread's own function stats. get_func_stats() with no argument merges a function
        that several threads call into ONE stat tagged with one context - that is how the wx thread
        once appeared to have made the farm thread's 66,463 AgentLink.send calls. The ctx_id filter
        does work, except for ctx_id 0, which yappi reads as "no filter", so the main thread gets
        whatever no other context claimed."""
        if st.id:
            return list(self.yappi.get_func_stats(ctx_id=st.id))
        claimed = collections.defaultdict(lambda: [0, 0.0])
        for other in self.yappi.get_thread_stats():
            if not other.id:
                continue
            for fn in self.yappi.get_func_stats(ctx_id=other.id):
                key = (fn.name, fn.module, fn.lineno)
                claimed[key][0] += fn.ncall
                claimed[key][1] += fn.ttot
        left = []
        for fn in self.yappi.get_func_stats():
            calls, total = claimed[(fn.name, fn.module, fn.lineno)]
            if fn.ncall > calls or fn.ttot - total > 1e-9:
                left.append(Left(fn, fn.ncall - calls, fn.ttot - total))
        return left

    def dump(self):
        with self.lock:
            if self.done:
                return
            self.done = True
            threading.Thread.start = self._orig_start
            self.yappi.stop()
            elapsed = time.time() - self.started
            threads = []
            for st in self.yappi.get_thread_stats():
                name = self._thread_name(st)
                if name in SKIP_THREADS:
                    continue
                threads.append((st.ttot or 0.0, name, st.tid, self._stats_for(st)))
            threads.sort(reverse=True)
            path = os.path.join(self.out, "yappi-wall.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("yappi wall clock, written %s, %.1f s after start\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), elapsed))
                f.write("Threads run at the same time, so their totals add up to more than %.1f s."
                        " Read each thread on its own.\n" % elapsed)
                f.write("\n%-9s %-34s %10s %10s %8s\n"
                        % ("tid", "thread", "thread_s", "wait_s", "busy_s"))
                for total, name, tid, stats in threads:
                    waits = sum(fn.ttot for fn in outer_waits(stats))
                    f.write("%-9s %-34s %10.2f %10.2f %8.2f\n"
                            % (tid, name[:34], total, waits, max(0.0, total - waits)))
                for total, name, tid, stats in threads:
                    if total < self.MIN_SECONDS:
                        continue
                    waits = sorted(outer_waits(stats), key=lambda fn: fn.ttot, reverse=True)
                    work = sorted((fn for fn in stats if not is_wait(fn)),
                                  key=lambda fn: fn.ttot, reverse=True)
                    f.write("\n=== %s (tid %s): %.2f s of thread wall ===\n" % (name, tid, total))
                    if waits:
                        f.write("  waiting: " + ", ".join(
                            "%s %.1f s%s" % (fn.name, fn.ttot,
                                             "" if fn.ttot else " (a C call: no time recorded)")
                            for fn in waits[:4]) + "\n")
                    f.write("%10s %8s  %s\n" % ("total_s", "calls", "function"))
                    for fn in work[:self.TOP_PER_THREAD]:
                        f.write("%10.2f %8d  %s (%s:%s)\n"
                                % (fn.ttot, fn.ncall, fn.full_name, fn.module, fn.lineno))
            print("yappi: %s (%d threads)" % (path, len(threads)))


class Tracemalloc:
    """Every thread's allocations: top lines, per file, and what grew since the start."""

    def __init__(self, out, minutes):
        import tracemalloc

        self.tracemalloc = tracemalloc
        self.out = out
        self.done = False
        self.lock = threading.Lock()
        self.started = not tracemalloc.is_tracing()
        if self.started:
            tracemalloc.start(25)
        self.base = tracemalloc.take_snapshot()

    def dump(self):
        with self.lock:
            if self.done:
                return
            self.done = True
            end = self.tracemalloc.take_snapshot()
            current, peak = self.tracemalloc.get_traced_memory()
            lines = end.statistics("lineno")
            files = end.statistics("filename")
            grew = end.compare_to(self.base, "lineno")
            path = os.path.join(self.out, "tracemalloc.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("tracemalloc at %s: %.1f MiB current, %.1f MiB peak\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), current / 2**20, peak / 2**20))
                f.write("The start snapshot is taken before the app loads maps, so 'growth' "
                        "includes first-use caches.\n")
                f.write("\ntop 60 lines by current size (KiB, not cumulative)\n")
                for stat in lines[:60]:
                    f.write("%9.1f  %s\n" % (stat.size / 1024, stat.traceback))
                f.write("\ntop files by current size (MiB)\n")
                for stat in files[:25]:
                    f.write("%9.2f  %s\n" % (stat.size / 2**20, stat.traceback))
                f.write("\ngrowth since start, top 60 lines (KiB)\n")
                for stat in grew[:60]:
                    f.write("%+9.1f  %s\n" % (stat.size_diff / 1024, stat.traceback))
            if self.started:
                self.tracemalloc.stop()      # leaving it on slows every later call by ~10x
            print("tracemalloc: %s" % path)


def main():
    parser = argparse.ArgumentParser(description="Profile Uncle Farmer from source.")
    parser.add_argument("--tool", choices=["yappi", "tracemalloc", "all"], default="yappi")
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="stop the profiler after this many minutes and write the report (0: on exit)")
    parser.add_argument("--out", default="", help="report folder (default logs/profiles/<time>)")
    args = parser.parse_args()

    out = args.out or os.path.join(ROOT, "logs", "profiles", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    tools = []
    try:
        if args.tool in ("yappi", "all"):
            tools.append(Yappi(out, args.minutes))
        if args.tool in ("tracemalloc", "all"):
            tools.append(Tracemalloc(out, args.minutes))
    except ImportError as e:
        print("cannot profile with %s: %s" % (args.tool, e))
        print("install it with: uv sync")
        return 2

    finished = threading.Event()

    def dump_all(*_):
        if finished.is_set():
            return
        finished.set()
        for tool in tools:
            try:
                tool.dump()
            except Exception:
                traceback.print_exc()

    atexit.register(dump_all)
    if args.minutes:
        timer = threading.Timer(args.minutes * 60.0, dump_all)
        timer.name = "profiler-timer"
        timer.daemon = True
        timer.start()
    print("profiling with %s; reports go to %s" % (args.tool, out))
    try:
        app_main()
    finally:
        dump_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
