"""Watch a whole farming night from outside, then say how it went.

    python nightwatch.py watch --hours 8           # start it, then farm; it runs on by itself
    python nightwatch.py status                    # is it running, how far along
    python nightwatch.py report                    # the newest night (or: report logs\\nights\\<dir>)

`watch` touches nothing in the bot or the games. It starts detached (closing this window or the
terminal doesn't stop it), at low priority, and every --every seconds writes how much CPU, memory,
handles and threads the bot and each game use. Alongside it keeps py-spy attached to the bot in
15-minute pieces (non-blocking: py-spy reads the bot's memory, it never pauses it), sampling every
thread, waiting ones too. It ends after --hours, or when the bot is gone for good.

`report` reads that, the day logs, errors.log and logs\\threads.log (which OS thread is which
character's run), and writes report.md and summary.json beside the samples:
  - per character: kills and contribution (what a night is judged by), per hour too, the bot's
    own hourly time split, deaths, stucks, potions, repairs, and the targets that cost a walk or a
    fight and gave nothing (not selectable, taken, no damage, gone, too long);
  - the gaps between kills: how they spread, and the longest ones with what happened in them;
  - walking, from logs\\field-<day>.jsonl: how often the monster picked was already in reach, how
    long the walks to the others took, search walks, and the place to stand that would have had
    the most of them in reach;
  - CPU and memory: the bot and every game, by the hour, and how fast memory grows;
  - py-spy: per thread, how much of the time it worked and how much it waited, which of our code
    it was in, and how long it waited for the agent.

Output goes to logs\\nights\\<start time>\\ (git-ignored): it carries character names.
"""

import argparse
import collections
import ctypes
import datetime as dt
import glob
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from ctypes import wintypes
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(ROOT, "logs")
NIGHTS = os.path.join(LOGS, "nights")
BOT_MARKS = ("unclefarmer.pyw", "-m unclefarmer", "unclefarmer\\__main__", "uncle farmer.exe", "unclefarmer.exe")
GAME_EXE = "mw.exe"
PIECE = 900             # seconds of py-spy per profile file: py-spy writes its file only when a
                        # piece ends, so a PC shut down mid-piece (the bot's after-stop action, a
                        # power cut) loses at most this much of the profile

# --------------------------------------------------------------------------- Windows process facts

_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
QUERY = 0x1000          # PROCESS_QUERY_LIMITED_INFORMATION
BELOW_NORMAL = 0x4000


class _MemCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _Entry(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]


def processes():
    """{pid: (exe name lower-case, thread count)} of every process, from one Toolhelp snapshot."""
    k = _k32
    assert k is not None
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    out = {}
    e = _Entry()
    e.dwSize = ctypes.sizeof(_Entry)
    ok = k.Process32First(snap, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.szExeFile.decode("mbcs", "replace").lower(), e.cntThreads)
        ok = k.Process32Next(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return out


def usage(pid):
    """{"cpu_s", "private_mb", "working_mb", "peak_working_mb", "handles"} of a process, or None."""
    k = _k32
    assert k is not None
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(QUERY, False, pid)
    if not h:
        return None
    try:
        c, e, kt, ut = (wintypes.FILETIME() for _i in range(4))
        if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return None

        def secs(f):
            return ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
        m = _MemCounters()
        m.cb = ctypes.sizeof(m)
        k.K32GetProcessMemoryInfo(h, ctypes.byref(m), m.cb)
        n = wintypes.DWORD()
        k.GetProcessHandleCount(h, ctypes.byref(n))
        return {"cpu_s": round(secs(kt) + secs(ut), 2), "private_mb": round(m.PrivateUsage / 2**20, 1),
                "working_mb": round(m.WorkingSetSize / 2**20, 1),
                "peak_working_mb": round(m.PeakWorkingSetSize / 2**20, 1), "handles": n.value}
    finally:
        k.CloseHandle(h)


def system_memory():
    s = _MemStatus()
    s.dwLength = ctypes.sizeof(s)
    assert _k32 is not None
    _k32.GlobalMemoryStatusEx(ctypes.byref(s))
    return {"load_pct": s.dwMemoryLoad, "free_mb": round(s.ullAvailPhys / 2**20),
            "commit_free_mb": round(s.ullAvailPageFile / 2**20)}


def command_lines(names):
    """{pid: command line} of the processes with these exe names (one PowerShell call)."""
    flt = " or ".join("Name='%s'" % n for n in names)
    ps = ("Get-CimInstance Win32_Process -Filter \"%s\" | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
          % flt)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        pid, _sep, cmd = line.partition("\t")
        if pid.strip().isdigit():
            found[int(pid)] = cmd.strip()
    return found


def find_bot():
    """The pid of the running Uncle Farmer, or None."""
    for pid, cmd in sorted(command_lines(("python.exe", "pythonw.exe", "Uncle Farmer.exe", "UncleFarmer.exe")).items()):
        low = cmd.lower()
        if any(m in low for m in BOT_MARKS) and "nightwatch" not in low and "pytest" not in low:
            return pid
    return None


def py_spy():
    return shutil.which("py-spy") or os.path.join(os.path.dirname(sys.executable), "Scripts", "py-spy.exe")


# --------------------------------------------------------------------------- watch

def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sample(now, bot, procs):
    """One resource sample: the PC, the bot and every game."""
    row = {"t": round(now, 1), "system": system_memory(), "bot": None, "games": {}}
    if bot is not None:
        u = usage(bot)
        if u:
            u["threads"] = procs.get(bot, ("", 0))[1]
            row["bot"] = u
    for pid, (exe, threads) in procs.items():
        if exe == GAME_EXE:
            u = usage(pid)
            if u:
                u["threads"] = threads
                row["games"][str(pid)] = u
    return row


def _finish(spy, wait):
    """Let a py-spy piece end and write its file; one that won't is killed (and its piece lost)."""
    if spy is None or spy.poll() is not None:
        return
    try:
        spy.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        spy.terminate()


class Watch:
    """The watcher itself (runs detached; see start())."""

    def __init__(self, out, hours, every, rate):
        self.out, self.every, self.rate = out, every, rate
        self.info_path = os.path.join(out, "watch.json")
        self.info = {"started": _now(), "hours": hours, "every": every, "rate": rate, "watcher_pid": os.getpid(),
                     "bots": [], "ended": None, "why": None, "profiles": []}
        self.end = time.time() + hours * 3600
        self.bot, self.spy, self.gone_since, self.last_find = None, None, None, 0.0

    def save(self):
        _write_json(self.info_path, self.info)

    def find(self, now, procs):
        """Keep self.bot on the running bot: after it closes, look again once a minute. False when
        it has been gone for 30 minutes (the night is over)."""
        if self.bot is not None and self.bot in procs:
            return True
        _finish(self.spy, 30)          # py-spy ends by itself with the process it watched
        self.spy = None
        if self.bot is not None:
            self.gone_since = self.gone_since or now
            self.bot = None
        if now - self.last_find >= 60:
            self.last_find = now
            self.bot = find_bot()
            if self.bot:
                self.gone_since = None
                self.info["bots"].append({"pid": self.bot, "at": _now()})
                self.save()
        return not (self.bot is None and self.gone_since and now - self.gone_since > 1800)

    def profile(self, now):
        """A new py-spy piece when none is running: PIECE seconds, or what is left of the watch."""
        if self.bot is None or (self.spy is not None and self.spy.poll() is None):
            return
        piece = min(PIECE, max(60, int(self.end - now)))
        name = "profile-%s.txt" % time.strftime("%Y%m%d-%H%M%S")
        with open(os.path.join(self.out, "py-spy.log"), "a") as err:
            self.spy = subprocess.Popen(
                [py_spy(), "record", "--pid", str(self.bot), "--rate", str(self.rate), "--duration", str(piece),
                 "--threads", "--idle", "--nonblocking", "--format", "raw", "--output", os.path.join(self.out, name)],
                stdout=subprocess.DEVNULL, stderr=err,
                creationflags=BELOW_NORMAL | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.info["profiles"].append({"file": name, "pid": self.bot, "from": _now(), "seconds": piece})
        self.save()

    def run(self):
        if _k32 is not None:
            _k32.SetPriorityClass(_k32.GetCurrentProcess(), BELOW_NORMAL)
        os.makedirs(self.out, exist_ok=True)
        self.save()
        self.info["why"] = "the hours were up"
        try:
            with open(os.path.join(self.out, "samples.jsonl"), "a", encoding="utf-8") as samples:
                while time.time() < self.end:
                    now = time.time()
                    procs = processes()
                    if not self.find(now, procs):
                        self.info["why"] = "the bot was gone for 30 minutes"
                        break
                    self.profile(now)
                    samples.write(json.dumps(_sample(now, self.bot, procs)) + "\n")
                    samples.flush()
                    self.info["last_sample"] = _now()
                    self.save()
                    time.sleep(max(1.0, self.every - (time.time() - now)))
        finally:
            _finish(self.spy, 120)     # its piece ends with the watch: let it write the file
            self.info["ended"] = _now()
            self.save()


def watch(out, hours, every, rate):
    Watch(out, hours, every, rate).run()


def start(hours, every, rate):
    """Start the watcher detached, so it outlives this console."""
    if not os.path.exists(py_spy()):
        sys.exit("py-spy isn't installed: uv sync installs it")
    running = newest()
    if running and _alive(running):
        sys.exit("a watch is already running: %s" % running)
    out = os.path.join(NIGHTS, time.strftime("%Y-%m-%d_%H%M"))
    os.makedirs(out, exist_ok=True)
    exe = sys.executable.replace("python.exe", "pythonw.exe")
    flags = 0x00000008 | 0x00000200 | BELOW_NORMAL     # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([exe, os.path.abspath(__file__), "_watch", out, str(hours), str(every), str(rate)],
                     creationflags=flags, close_fds=True, cwd=ROOT)
    print("watching for %g hours: %s" % (hours, out))


def newest():
    dirs = sorted(d for d in glob.glob(os.path.join(NIGHTS, "*")) if os.path.isfile(os.path.join(d, "watch.json")))
    return dirs[-1] if dirs else None


def _alive(night):
    try:
        with open(os.path.join(night, "watch.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return False
    return not info.get("ended") and info.get("watcher_pid") in processes()


def status():
    night = newest()
    if not night:
        print("no night watched yet")
        return
    with open(os.path.join(night, "watch.json"), encoding="utf-8") as f:
        info = json.load(f)
    print(night)
    print("running" if _alive(night) else "ended %s (%s)" % (info.get("ended"), info.get("why")))
    print("started %s, last sample %s, bot pids %s, profiles %d" % (
        info["started"], info.get("last_sample"), [b["pid"] for b in info["bots"]], len(info["profiles"])))


# --------------------------------------------------------------------------- reading the logs

LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)  ([^:]{1,40}): (.*)$")
EVENTS = [      # (kind, pattern) - the first that matches names the line
    ("kill", re.compile(r" down \((\d+) so far\)$|^a monster down \(")),
    ("death", re.compile(r"^died on (.+) \((\d+) so far\)")),
    ("fight", re.compile(r"^(?:attacked by .+, )?fighting (.+)$")),
    ("no_select", re.compile(r"wouldn't select (.+) - next one")),
    ("taken", re.compile(r"^(.+) was taken by someone else")),
    ("theirs", re.compile(r"^(.+) is about to be killed by someone else")),
    ("no_damage", re.compile(r"^(.+) takes no damage, leaving it")),
    ("gone", re.compile(r"^(.+) is gone, and none of our hits landed")),
    ("too_long", re.compile(r"^(.+): fight took too long")),
    ("stuck", re.compile(r"^stuck ")),
    ("unstuck_map", re.compile(r"^leaving .+ to get unstuck")),
    ("hp_potion", re.compile(r"^HP \d+/\d+ \(\d+%\) - ")),
    ("mp_potion", re.compile(r"^MP about \d+% - ")),
    ("pet_potion", re.compile(r"^pet (?:HP|MP) ")),
    ("repair", re.compile(r"^repairing all the gear")),
    ("timing", re.compile(r"^last (\d+) min: (\d+) kills \((\d+) an hour\), ([\d.]+) s a kill - (.*)$")),
    ("contribution", re.compile(r"^last (\d+) min: contribution \+(\d+) \((\d+) an hour\)")),
    ("game_memory", re.compile(r"^game memory: (\d+) of (\d+) MB in use")),
    ("start", re.compile(r"^starting ")),
    ("stop", re.compile(r"^(?:STOPPED|stopped)(?::|$)")),
]
WASTE = ("no_select", "taken", "theirs", "no_damage", "gone", "too_long")
WASTE_WORDS = {"no_select": "not selectable", "taken": "taken by someone else",
               "theirs": "nearly killed by someone else", "no_damage": "took no damage",
               "gone": "gone before a hit landed", "too_long": "fight took too long"}


def _log_files(day):
    base = os.path.join(LOGS, day.strftime("%Y-%m-%d") + ".log")
    olds = sorted(glob.glob(base + ".*"), key=lambda p: -int(p.rsplit(".", 1)[1]) if p.rsplit(".", 1)[1].isdigit() else 0)
    return [p for p in olds if p.rsplit(".", 1)[1].isdigit()] + ([base] if os.path.exists(base) else [])


def read_log(t0, t1):
    """[(datetime, character, kind or None, match or None, text)] of the run log lines between
    t0 and t1, every day file they span (and its rotated parts), in order."""
    rows = []
    day = t0.date()
    while day <= t1.date():
        for path in _log_files(day):
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = LINE.match(line.rstrip("\n"))
                    if not m:
                        continue
                    at = dt.datetime.combine(day, dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                    if not t0 <= at <= t1:
                        continue
                    who, text = m.group(4).strip(), m.group(5)
                    kind, hit = None, None
                    for k, pat in EVENTS:
                        hit = pat.search(text)
                        if hit:
                            kind = k
                            break
                    rows.append((at, who, kind, hit, text))
        day += dt.timedelta(days=1)
    rows.sort(key=lambda r: r[0])
    return rows


def read_errors(t0, t1):
    out = []
    path = os.path.join(LOGS, "errors.log")
    if not os.path.exists(path):
        return out
    head = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)  (.*)$")
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = head.match(line)
            if m:
                at = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                if t0 <= at <= t1:
                    out.append((at, m.group(2)[:160]))
    return out


def read_results(t0, t1):
    """{character: [result rows]} of the runs that ended between t0 and t1 (+10 min for the walk
    to safety), from results/<character>.jsonl."""
    out = collections.defaultdict(list)
    for path in glob.glob(os.path.join(ROOT, "results", "*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    start = dt.datetime.strptime(r["start"], "%Y-%m-%d %H:%M:%S")
                except (ValueError, KeyError, TypeError):
                    continue
                if r.get("kind") == "tool":
                    continue            # a Gem Maker or chores record: no farming
                end = start + dt.timedelta(minutes=r.get("minutes") or 0)
                if t0 <= end <= t1 + dt.timedelta(minutes=10):
                    out[r.get("character") or os.path.basename(path)[:-6]].append(r)
    return out


def read_field(t0, t1):
    """{character: [field-log records]} between t0 and t1 (logs/field-<day>.jsonl, written by the
    bot from 2026-10-24 on): each fight from the pick on, and each search walk."""
    out = collections.defaultdict(list)
    day = t0.date()
    while day <= t1.date():
        base = os.path.join(LOGS, "field-%s.jsonl" % day.strftime("%Y-%m-%d"))
        for path in sorted(glob.glob(base + ".*"), reverse=True) + [base]:
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        at = dt.datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S")
                    except (ValueError, KeyError, TypeError):
                        continue
                    if t0 <= at <= t1:
                        out[r.get("who") or "?"].append(r)
        day += dt.timedelta(days=1)
    return out


def stand_point(fights):
    """The place we stood that would have had the most of the monsters we picked within reach,
    on the map with the most fights: (map, (x, y), share within reach from there, share in reach
    where we actually stood when we picked, fights). Only places we stood count - they are
    walkable. The monsters are those we saw from where we were, so it is a hint, not a promise."""
    by_map = collections.defaultdict(list)
    for r in fights:
        by_map[r.get("map")].append(r)
    if not by_map:
        return None
    mid, rows = max(by_map.items(), key=lambda kv: len(kv[1]))
    reach = statistics.median(r["reach"] for r in rows)
    mobs = [tuple(r["mob"]) for r in rows]
    spots = sorted({(round(r["me"][0]), round(r["me"][1])) for r in rows})
    if len(spots) > 1500:
        spots = spots[::len(spots) // 1500 + 1]
    rr = reach * reach
    best, best_n = None, -1
    for sx, sy in spots:
        n = sum(1 for mx, my in mobs if (mx - sx) ** 2 + (my - sy) ** 2 <= rr)
        if n > best_n:
            best, best_n = (sx, sy), n
    actual = sum(1 for r in rows if r["dist"] <= r["reach"])
    return mid, best, best_n / len(rows), actual / len(rows), len(rows), reach


def walking(records):
    """What the field log says about one character's walking: picks in reach, walk times, fights
    that never reached their monster, search walks, and a better place to stand."""
    fights = [r for r in records if r.get("kind") == "fight"]
    searches = [r for r in records if r.get("kind") == "search"]
    if not fights and not searches:
        return None
    w: dict[str, Any] = {"fights": len(fights), "kills": sum(r.get("kills", 0) for r in fights)}
    walked = sorted(r["reach_s"] for r in fights if "reach_s" in r)
    w["in_reach_at_pick"] = sum(1 for r in fights if r["dist"] <= r["reach"])
    w["passed_by"] = sum(1 for r in fights if r["dist"] > r["reach"] and r.get("in_reach", 0) > 0
                         and not r.get("attacking"))
    w["walk_s"] = sum(walked)
    w["walk_median"] = _pct(walked, 0.5) if walked else 0
    w["walk_p90"] = _pct(walked, 0.9) if walked else 0
    beyond = sorted(r["dist"] - r["reach"] for r in fights if r["dist"] > r["reach"])
    w["beyond_median"] = _pct(beyond, 0.5) if beyond else 0
    never = [r for r in fights if "reach_s" not in r]
    w["never"] = len(never)
    w["never_s"] = sum(r.get("end_s", 0) for r in never)
    w["fight_s"] = sum(r.get("end_s", 0) - r.get("reach_s", 0) for r in fights if "reach_s" in r)
    w["sight_median"] = _pct(sorted(r.get("sight", 0) for r in fights), 0.5) if fights else 0
    w["hurt"] = sum(1 for r in fights if r.get("hurt"))
    w["searches"] = len(searches)
    w["search_s"] = sum(r.get("s", 0) for r in searches)
    w["search_results"] = collections.Counter(r.get("result") for r in searches)
    w["stand"] = stand_point(fights)
    return w


def thread_names(t1):
    """{OS thread id: label} from logs/threads.log, the newest record of each id up to t1."""
    out = {}
    path = os.path.join(LOGS, "threads.log")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("  ", 2)
                if len(parts) == 3 and parts[1].strip().isdigit():
                    try:
                        at = dt.datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if at <= t1:
                        out[int(parts[1])] = parts[2].strip()
    return out


# --------------------------------------------------------------------------- the night, per character

def _pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(q * len(xs))) - 1)]


def characters(rows):
    """Per character: totals, per hour, kill gaps, wasted targets."""
    out = {}
    by = collections.defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    for who, rs in by.items():
        c = collections.Counter(r[2] for r in rs if r[2])
        if not c["kill"] and not c["fight"]:
            continue                       # a tool run or a one-off, not farming
        first, last = rs[0][0], rs[-1][0]
        hours = collections.OrderedDict()
        for r in rs:
            h = r[0].replace(minute=0, second=0)
            hc = hours.setdefault(h, collections.Counter())
            if r[2]:
                hc[r[2]] += 1
        timing = [(r[0], r[3].groups()) for r in rs if r[2] == "timing"]
        contrib = [(r[0], int(r[3].group(2)), int(r[3].group(1))) for r in rs if r[2] == "contribution"]
        kills = [r[0] for r in rs if r[2] == "kill"]
        gaps = []
        for a, b in zip(kills, kills[1:]):
            gaps.append(((b - a).total_seconds(), a, b))
        # each target from "fighting X" until what ended it: a kill, a waste, or the next target
        # a target that gave nothing cost the time since the last target ended (a kill or another
        # waste): searching for it, walking to it, fighting it
        wasted = collections.Counter()
        wasted_s = collections.Counter()
        since = None
        for r in rs:
            if r[2] == "kill" or r[2] == "start":
                since = r[0]
            elif r[2] in WASTE:
                wasted[r[2]] += 1
                if since is not None:
                    wasted_s[r[2]] += min(120.0, (r[0] - since).total_seconds())
                since = r[0]
        long_gaps = []
        for s, a, b in sorted(gaps, reverse=True)[:8]:
            between = [r for r in rs if a < r[0] < b][:14]
            long_gaps.append({"seconds": s, "from": a.strftime("%H:%M:%S"), "to": b.strftime("%H:%M:%S"),
                              "lines": ["%s %s" % (r[0].strftime("%H:%M:%S"), r[4][:110]) for r in between]})
        g = [x[0] for x in gaps]
        out[who] = {
            "from": first, "to": last, "hours": (last - first).total_seconds() / 3600, "counts": c,
            "per_hour": hours, "timing": timing, "contribution": contrib, "gaps": g,
            "gap_stats": {"median": _pct(g, 0.5), "p90": _pct(g, 0.9), "p99": _pct(g, 0.99),
                          "over_60s": sum(1 for x in g if x > 60), "over_60s_total": sum(x for x in g if x > 60)},
            "long_gaps": long_gaps, "wasted": wasted, "wasted_s": wasted_s,
            "game_memory": [(r[0], int(r[3].group(1)), int(r[3].group(2))) for r in rs if r[2] == "game_memory"],
            "stops": [(r[0], r[4]) for r in rs if r[2] == "stop"],
        }
    return out


# --------------------------------------------------------------------------- resources

def resources(samples):
    """Per hour: the bot's CPU (% of one core), private memory; per game the same; the PC's free
    memory. Growth in MB an hour from the first to the last sample of each process."""
    hours = collections.OrderedDict()
    procs = collections.defaultdict(list)          # name -> [(t, usage)]
    sysmin = None
    for s in samples:
        h = dt.datetime.fromtimestamp(s["t"]).replace(minute=0, second=0, microsecond=0)
        hours.setdefault(h, [])
        hours[h].append(s)
        if s.get("bot"):
            procs["bot"].append((s["t"], s["bot"]))
        for pid, u in s.get("games", {}).items():
            procs["game " + pid].append((s["t"], u))
        free = s["system"]["free_mb"]
        sysmin = free if sysmin is None else min(sysmin, free)
    out = {"procs": {}, "free_min_mb": sysmin, "hours": []}
    for name, rows in procs.items():
        if len(rows) < 2:
            continue
        (t0, a), (t1, b) = rows[0], rows[-1]
        span = max(1.0, t1 - t0)
        busiest = 0.0
        for (ta, ua), (tb, ub) in zip(rows, rows[1:]):
            if tb > ta and ub["cpu_s"] >= ua["cpu_s"]:
                busiest = max(busiest, 100 * (ub["cpu_s"] - ua["cpu_s"]) / (tb - ta))
        out["procs"][name] = {
            "minutes": round(span / 60), "cpu_avg_pct": round(100 * (b["cpu_s"] - a["cpu_s"]) / span, 2),
            "cpu_max_pct": round(busiest, 1), "private_start_mb": a["private_mb"], "private_end_mb": b["private_mb"],
            "private_max_mb": max(u["private_mb"] for _t, u in rows),
            "growth_mb_per_h": round((b["private_mb"] - a["private_mb"]) * 3600 / span, 1),
            "handles_start": a["handles"], "handles_end": b["handles"],
            "threads_start": a.get("threads"), "threads_end": b.get("threads")}
    for h, ss in hours.items():
        row = {"hour": h, "free_min_mb": min(s["system"]["free_mb"] for s in ss)}
        bots = [s["bot"] for s in ss if s.get("bot")]
        if len(bots) >= 2:
            span = ss[-1]["t"] - ss[0]["t"]
            row["bot_cpu_pct"] = round(100 * (bots[-1]["cpu_s"] - bots[0]["cpu_s"]) / max(1.0, span), 2)
            row["bot_private_mb"] = bots[-1]["private_mb"]
        row["games_private_mb"] = {pid: u["private_mb"] for pid, u in ss[-1].get("games", {}).items()}
        out["hours"].append(row)
    return out


# --------------------------------------------------------------------------- py-spy

FRAME = re.compile(r"^(.*) \((.*?)(?::(\d+))?\)$")
# py-spy's raw format doesn't say which samples were idle, and without --native the C call that
# blocks (time.sleep, a socket read) isn't a frame: the innermost Python frame is the one that
# called it. These functions only wait there, and these files only wait in their leaves.
WAITING = {"sleep", "wait", "_wait", "acquire", "recv", "recv_into", "readline", "readinto", "select", "accept",
           "MainLoop", "get", "_wait_for_tstate_lock", "join", "poll", "WaitForSingleObject", "_recv", "Yield",
           "ShowModal"}
WAITING_FILES = ("socket.py", "selectors.py", "queue.py", "threading.py", "ssl.py")
AGENT_FILES = ("unclefarmer\\link.py", "unclefarmer/link.py")


def _ours(path):
    p = path.replace("/", "\\").lower()
    i = p.find("unclefarmer\\")
    return p[i:] if i >= 0 and "site-packages" not in p else None


def profiles(night, info, names):
    """Per thread over every profile file: samples, how many waited, where our code was (by function,
    inclusive), how much waited inside link.py (on the agent), and the busiest leaves."""
    threads = {}
    for prof in info.get("profiles", []):
        path = os.path.join(night, prof["file"])
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stack, _sp, count = line.rstrip("\n").rpartition(" ")
                if not count.isdigit():
                    continue
                n = int(count)
                frames = stack.split(";")
                head = re.match(r"thread \((\d+)\)", frames[0])
                tid = int(head.group(1)) if head else 0
                frames = frames[1:] if head else frames
                label = names.get(tid) or "thread %d" % tid
                t = threads.setdefault(label, {"samples": 0, "waiting": 0, "agent_wait": 0, "agent": 0,
                                               "funcs": collections.Counter(), "busy_leaf": collections.Counter(),
                                               "wait_where": collections.Counter()})
                t["samples"] += n
                parsed = []
                for fr in frames:
                    m = FRAME.match(fr)
                    if m:
                        parsed.append((m.group(1), m.group(2)))
                if not parsed:
                    continue
                leaf_fn = parsed[-1][0]
                leaf_file = re.split(r"[\\/]", parsed[-1][1])[-1].lower()
                # socket.recv is C, so a thread blocked on the agent's answer shows link._ask as
                # its innermost Python frame: that is waiting too (the bot used 3% of a core while
                # the chat threads read as "working 100%")
                waiting = (leaf_fn in WAITING or leaf_file in WAITING_FILES
                           or (leaf_fn == "_ask" and leaf_file == "link.py"))
                in_agent = any(f.replace("/", "\\").lower().endswith(AGENT_FILES[0]) for _fn, f in parsed)
                if in_agent:
                    t["agent"] += n
                if waiting:
                    t["waiting"] += n
                    if in_agent:
                        t["agent_wait"] += n
                    ours = [(fn, _ours(f)) for fn, f in parsed if _ours(f)]
                    if ours:
                        t["wait_where"]["%s (%s)" % ours[-1]] += n
                else:
                    ours = [(fn, _ours(f)) for fn, f in parsed if _ours(f)]
                    t["busy_leaf"]["%s (%s)" % (leaf_fn, parsed[-1][1].replace("/", "\\").split("\\")[-1])] += n
                for key in {"%s (%s)" % (fn, o) for fn, o in [(fn, _ours(f)) for fn, f in parsed] if o}:
                    t["funcs"][key] += n
    return threads


# --------------------------------------------------------------------------- the report

def _h(td_s):
    s = int(td_s)
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def _contribution(c):
    """(contribution, minutes it covers): the runs' results when they cover more of the night than
    the bot's hourly contribution lines (a run's first hour has no line yet)."""
    got = sum(x[1] for x in c["contribution"])
    mins = sum(x[2] for x in c["contribution"])
    runs = [r for r in c.get("runs") or [] if isinstance(r.get("contribution"), int)]
    if runs and sum(r.get("minutes") or 0 for r in runs) > mins:
        got, mins = sum(r["contribution"] for r in runs), sum(r.get("minutes") or 0 for r in runs)
    return got, mins


def _md_overview(chars, summary):
    md = ["## Characters", "",
          "| Character | Hours | Kills | Kills/h | Contribution | Contribution/h | Deaths | Stucks | Wasted targets "
          "| Time lost to them |", "|---|---|---|---|---|---|---|---|---|---|"]
    for who, c in sorted(chars.items()):
        k = c["counts"]["kill"]
        got, mins = _contribution(c)
        md.append("| %s | %.1f | %d | %d | %s | %s | %d | %d | %d | %s |" % (
            who, c["hours"], k, k / c["hours"] if c["hours"] else 0, got if mins else "?",
            int(got * 60 / mins) if mins else "?", c["counts"]["death"], c["counts"]["stuck"],
            sum(c["wasted"].values()), _h(sum(c["wasted_s"].values()))))
        summary["characters"][who] = {"hours": round(c["hours"], 2), "kills": k, "contribution": got,
                                      "contribution_minutes": mins, "deaths": c["counts"]["death"],
                                      "stucks": c["counts"]["stuck"], "wasted": dict(c["wasted"]),
                                      "wasted_seconds": dict(c["wasted_s"]), "gap_stats": c["gap_stats"],
                                      "walking": c.get("walking")}
    return md + [""]


def _md_runs(runs):
    if not runs:
        return []
    md = ["| Run | Started | Minutes | Kills | Contribution | Contribution/h | Deaths |", "|---|---|---|---|---|---|---|"]
    for r in runs:
        mins = r.get("minutes") or 0
        md.append("| %s | %s | %.0f | %s | %s | %s | %s |" % (
            r.get("run", ""), r.get("start", "")[11:16], mins, r.get("kills", "?"), r.get("contribution", "?"),
            int(r["contribution"] * 60 / mins) if mins and isinstance(r.get("contribution"), int) else "?",
            r.get("deaths", "?")))
    return md + [""]


def _md_gaps(c):
    gs = c["gap_stats"]
    md = ["Time between kills: median %.0f s, 90%% under %.0f s, 99%% under %.0f s; %d gaps over a minute, %s in "
          "all." % (gs["median"], gs["p90"], gs["p99"], gs["over_60s"], _h(gs["over_60s_total"])), ""]
    if c["wasted"]:
        md += ["Targets that gave nothing (and the time from the target before to giving up on it): " + "; ".join(
            "%s %d (%s)" % (WASTE_WORDS[w], n, _h(c["wasted_s"][w])) for w, n in c["wasted"].most_common()), ""]
    if c["long_gaps"]:
        md += ["Longest gaps between kills:", ""]
        for g in c["long_gaps"][:5]:
            md.append("- %s, %s to %s:" % (_h(g["seconds"]), g["from"], g["to"]))
            md += ["  - %s" % ln for ln in g["lines"]]
        md.append("")
    return md


def _md_character(who, c):
    n = c["counts"]
    md = ["## %s" % who, "",
          "From %s to %s. Potions: %d HP, %d MP, %d for the pet. Repairs: %d. Stucks: %d (left the map to get "
          "unstuck %d times)." % (c["from"].strftime("%H:%M"), c["to"].strftime("%H:%M"), n["hp_potion"],
                                  n["mp_potion"], n["pet_potion"], n["repair"], n["stuck"], n["unstuck_map"]), "",
          "| Hour | Kills | Deaths | Stucks | Wasted targets | HP potions | MP potions |", "|---|---|---|---|---|---|---|"]
    for h, hc in c["per_hour"].items():
        md.append("| %s | %d | %d | %d | %d | %d | %d |" % (
            h.strftime("%H:00"), hc["kill"], hc["death"], hc["stuck"], sum(hc[w] for w in WASTE),
            hc["hp_potion"], hc["mp_potion"]))
    md.append("")
    if c["timing"]:
        md += ["The bot's own hourly lines (each covers the hour before it):", ""]
        md += ["- %s: %s kills, %s s a kill - %s" % (at.strftime("%H:%M"), g[1], g[3], g[4]) for at, g in c["timing"]]
        md.append("")
    md += _md_runs(c.get("runs") or [])
    if c["contribution"]:
        md += ["Contribution by the hour: " + ", ".join(
            "%s +%d" % (at.strftime("%H:%M"), n) for at, n, _m in c["contribution"]), ""]
    md += _md_gaps(c)
    md += _md_walking(c.get("walking"))
    if c["stops"]:
        md += ["Stops: " + "; ".join("%s %s" % (at.strftime("%H:%M"), t) for at, t in c["stops"]), ""]
    return md


def _md_walking(w):
    if not w:
        return []
    n = w["fights"] or 1
    md = ["### Walking (field log)", "",
          "%d fights picked by the farm loop, %d kills. The monster picked was already in reach %d times "
          "(%d%%); %d times something else was in reach and a farther one was picked. Monsters in sight at a "
          "pick: median %d. Already hurt by someone when picked: %d." % (
              w["fights"], w["kills"], w["in_reach_at_pick"], 100 * w["in_reach_at_pick"] / n, w["passed_by"],
              w["sight_median"], w["hurt"]), "",
          "Walking up to monsters: %s in all, median %.1f s, 90%% under %.1f s; the monster was a median %.1f "
          "tiles past our reach. In reach and fighting: %s. Never reached (taken, gone, stuck on the way): %d "
          "fights, %s." % (_h(w["walk_s"]), w["walk_median"], w["walk_p90"], w["beyond_median"], _h(w["fight_s"]),
                           w["never"], _h(w["never_s"])), "",
          "Search walks with nothing in sight: %d, %s in all (%s)." % (
              w["searches"], _h(w["search_s"]), ", ".join("%s %d" % kv for kv in w["search_results"].most_common())
              or "none"), ""]
    st = w["stand"]
    if st:
        mid, spot, share, actual, rows, reach = st
        md += ["Where to stand: on map %s (%d fights), standing at %d,%d would have had %d%% of the monsters we "
               "picked within reach (%.1f tiles) at the moment we picked them, against %d%% from where we "
               "actually stood. Only places we stood were tried, and the monsters are the ones we saw from "
               "there." % (mid, rows, spot[0], spot[1], 100 * share, reach, 100 * actual), ""]
    return md


def _md_resources(res):
    md = ["## CPU and memory", ""]
    if not res["procs"]:
        return md + ["No resource samples.", ""]
    md += ["| Process | Minutes | CPU avg | CPU max | Memory start | end | max | Growth/h | Handles | Threads |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for name, p in sorted(res["procs"].items()):
        md.append("| %s | %d | %.2f%% | %.1f%% | %.0f MB | %.0f MB | %.0f MB | %+.1f MB | %s to %s | %s to %s |" % (
            name, p["minutes"], p["cpu_avg_pct"], p["cpu_max_pct"], p["private_start_mb"], p["private_end_mb"],
            p["private_max_mb"], p["growth_mb_per_h"], p["handles_start"], p["handles_end"],
            p["threads_start"], p["threads_end"]))
    md += ["", "CPU is a share of one core. The PC's free memory never fell below %s MB." % res["free_min_mb"], "",
           "| Hour | Bot CPU | Bot memory | Games' memory | PC free |", "|---|---|---|---|---|"]
    for r in res["hours"]:
        md.append("| %s | %s%% | %s MB | %s | %s MB |" % (
            r["hour"].strftime("%H:00"), r.get("bot_cpu_pct", "?"), r.get("bot_private_mb", "?"),
            ", ".join("%.0f MB" % v for v in r["games_private_mb"].values()) or "none", r["free_min_mb"]))
    return md + [""]


def _md_threads(prof, summary):
    md = ["## Where the bot's threads spent their time (py-spy)", ""]
    if not prof:
        return md + ["No profile.", ""]
    for label, t in sorted(prof.items(), key=lambda kv: -kv[1]["samples"]):
        if t["samples"] < 50:
            continue
        all_ = t["samples"]
        busy = all_ - t["waiting"]
        md += ["### %s" % label, "",
               "%d samples: working %.1f%%, waiting %.1f%%. Inside agent calls %.1f%% (waiting for the agent's answer "
               "%.1f%%)." % (all_, 100 * busy / all_, 100 * t["waiting"] / all_, 100 * t["agent"] / all_,
                             100 * t["agent_wait"] / all_), "",
               "Our code, share of this thread's time (inclusive): " + "; ".join(
                   "%s %.1f%%" % (k, 100 * n / all_) for k, n in t["funcs"].most_common(14)), ""]
        if t["wait_where"]:
            md += ["Where it waited: " + "; ".join(
                "%s %.1f%%" % (k, 100 * n / all_) for k, n in t["wait_where"].most_common(8)), ""]
        if busy:
            md += ["Busiest lines of work: " + "; ".join(
                "%s %.1f%%" % (k, 100 * n / all_) for k, n in t["busy_leaf"].most_common(8)), ""]
        summary["threads"][label] = {"samples": all_, "waiting": t["waiting"], "agent": t["agent"],
                                     "agent_wait": t["agent_wait"], "top": t["funcs"].most_common(25)}
    return md


def report(night):
    with open(os.path.join(night, "watch.json"), encoding="utf-8") as f:
        info = json.load(f)
    t0 = dt.datetime.strptime(info["started"], "%Y-%m-%d %H:%M:%S")
    # a watch the PC's shutdown cut off never wrote its end: its last sample is where it stopped
    end = info.get("ended") or info.get("last_sample")
    t1 = dt.datetime.strptime(end, "%Y-%m-%d %H:%M:%S") if end else dt.datetime.now()
    rows = read_log(t0, t1)
    chars = characters(rows)
    for who, runs in read_results(t0, t1).items():
        if who in chars:
            chars[who]["runs"] = runs
    for who, recs in read_field(t0, t1).items():
        if who in chars:
            chars[who]["walking"] = walking(recs)
    samples = []
    sp = os.path.join(night, "samples.jsonl")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            samples = [json.loads(x) for x in f if x.strip()]
    res = resources(samples)
    prof = profiles(night, info, thread_names(t1))
    errors = read_errors(t0, t1)
    summary = {"night": os.path.basename(night), "characters": {}, "resources": res["procs"], "threads": {}}
    md = ["# Night %s to %s" % (t0.strftime("%Y-%m-%d %H:%M"), t1.strftime("%H:%M")), "",
          "Judged by contribution and kills. Watched %.1f hours; %d log lines; %d resource samples; %d profile "
          "files." % ((t1 - t0).total_seconds() / 3600, len(rows), len(samples), len(info.get("profiles", []))), ""]
    md += _md_overview(chars, summary)
    for who, c in sorted(chars.items()):
        md += _md_character(who, c)
    md += _md_resources(res)
    md += _md_threads(prof, summary)
    md += ["## Errors", ""]
    md += ["- %s %s" % (at.strftime("%H:%M:%S"), text) for at, text in errors[:40]] or ["None."]
    md.append("")
    with open(os.path.join(night, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    with open(os.path.join(night, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
    return os.path.join(night, "report.md")


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = a.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="start watching (detached)")
    w.add_argument("--hours", type=float, default=8.0)
    w.add_argument("--every", type=float, default=30.0, help="seconds between resource samples")
    w.add_argument("--rate", type=int, default=20, help="py-spy samples a second")
    sub.add_parser("status")
    r = sub.add_parser("report")
    r.add_argument("night", nargs="?")
    i = sub.add_parser("_watch")
    i.add_argument("out")
    i.add_argument("hours", type=float)
    i.add_argument("every", type=float)
    i.add_argument("rate", type=int)
    args = a.parse_args(argv)
    if args.cmd == "watch":
        start(args.hours, args.every, args.rate)
    elif args.cmd == "_watch":
        watch(args.out, args.hours, args.every, args.rate)
    elif args.cmd == "status":
        status()
    else:
        night = args.night or newest()
        if not night:
            sys.exit("no night watched yet")
        print(report(night))


if __name__ == "__main__":
    main()
