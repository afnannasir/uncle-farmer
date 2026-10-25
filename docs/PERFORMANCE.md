# Performance: where a decision's time goes

The bot makes one decision per 50 ms tick, and on a live run agent round trips measure 0.08-0.41 ms
each, so the tick is not waiting on the link. This page is about the Python work that does cost
time: routing, choosing a target, and re-planning travel. Farming on open ground needs none of it;
caves and long trips do. (For scale: a live kill cycle is 11-14 s, of which fights are ~4 s and
travel between them the rest, so link and routing micro-costs do not drive kills per hour.)

`benchmark.py` is the rerunnable harness behind every number below.

    python benchmark.py            # about a minute, all sections
    python benchmark.py --quick    # skip the shortloop builds

Each line is the median / p95 / p99 of N repeats after a warmup. "Cold" means the caches named
in the line were emptied first (first visit); "warm" means the bot's own cache answered. Raw
output of the runs quoted here: `bench_baseline.txt` (before) and `bench_after.txt` (after).

Environment: Windows, CPython 3.14.8, numpy 2.5.4, Intel Xeon E5-1650 v3 @ 3.50 GHz. Map 1041
(Giant Rock) is 400x400 with 153,290 walkable cells; map 1503 is an 11,020-cell walled cave.

## 2026-10-23: the map searches in C

Every map search the bot waits on now runs in C (`unclefarmer/native/routing.c`, built by
`python build_native.py` into `unclefarmer/native/_routing-<hash>.dll`, loaded with ctypes). Each
C function is a copy of a Python method in `atlaslib.py` and gives exactly its answer: the heaps
order entries as Python compares the tuples `heapq` holds, and every cost is the same sum of
doubles in the same order (built with `-ffp-contract=off`), so even ties between equal routes go
the same way. The Python bodies stay as the fallback and as the reference the C is tested against.

Raw output: `perf/bench_head.txt` (the code before this change, run from a checkout of 5426cf7),
`perf/bench_python.txt` (this code, `UNCLEFARMER_NO_NATIVE=1`), and `perf/bench_native.txt` (this
code with the bucket queues; the heap column below is the run committed in ece7fe4). Same PC,
Windows 11, CPython 3.14.7, numpy 2.5.4, Intel family 6 model 186. Medians; p95 is in the files.
The last run was noisier: lines whose code did not change between the two C columns (map load,
warm target choice) came out 20-90 % slower in it, so read differences under ~1.5x as noise.

| What the bot waits for | Before | C, heap | C, buckets | |
|---|---|---|---|---|
| walk planning, 12 walks round walls, turkey cave (5302) | 203.5 ms | 10.6 ms | 7.5 ms | 27x |
| walk planning, 12 walks, Karon Village (1001) | 178.2 ms | 9.7 ms | 7.1 ms | 25x |
| walk planning, 12 walks, central plains (1041) | 708.9 ms | 29.3 ms | 19.5 ms | 36x |
| `route` far (200 cells), cold | 737.1 ms | 31.3 ms | 19.3 ms | 38x |
| `route` near (30 cells), cold | 17.7 ms | 0.83 ms | 0.67 ms | 26x |
| choosing among 30 monsters, cold, 1041 / 1503 | 10.0 / 7.8 ms | 0.82 / 0.35 ms | 1.24 / 0.74 ms | 8-12x |
| choosing among 30 monsters, warm | 1.25 ms | 0.27 ms | 0.53 ms | 2.4-4.6x |
| `travel.plan` across maps, cold | 570.3 ms | 49.8 ms | 26.1 ms | 22x |
| `travel._steps_from`, cold | 184.1 ms | 14.4 ms | 9.7 ms | 19x |
| map change: rows + model, 1041 / 5302 / 1001 | 125.6 / 35.8 / 19.0 ms | 38.5 / 3.0 / 5.7 ms | 48.9 / 3.5 / 10.3 ms | 2-12x |
| geometry index at start | 170.9 ms | 96.1 ms | 115.0 ms | 1.5-1.8x |
| `diag_ok` | 0.268 ms | 0.004 ms | 0.007 ms | 40-67x |
| `steps_arr` full / limit 150 | 35.7 / 11.7 ms | 1.7 / 0.55 ms | 1.8 / 0.53 ms | 20x |
| `dijkstra` full, 1 target (dicts out) | 285.2 ms | 39.3 ms | 35.2 ms | 8x (most of it is the dicts; `dijkstra_arr` is ~14 ms) |
| `shortloop.build` 1503 (offline) | 1944.6 ms | 304.1 ms | 383.5 ms | 5-6x; 42 sampled maps build identical loops |

Unchanged on purpose: `astar_rect`, `dijkstra_rect` and `path_rect` (the public dict-returning
methods: the app routes through `_astar_path`, tests and tools read the dicts), `line`, the caches
(hits were already microseconds). A re-plan whose distances are cached runs the same Python graph
search either way: 0.50-0.91 ms with and without C, interleaved in one process, so the 0.49 against
0.65 ms in the files is noise. When the start's distances do have to be searched it is 26-30 ms
with C against 796-856 ms in Python.

What it means for a run: the bot is idle ~97 % of its wall clock (see the py-spy capture below), so
kills an hour don't move. What moves is the stall at the start of a walk, a trip or a map: a walk
round walls on a big map no longer freezes the bot for most of a second, a trip starts ~0.5 s
sooner, and target selection after every kill is under a millisecond. ctypes releases the GIL
during a search, so the window and the message thread keep running while one runs.

### What changed

1. **C searches** (`routing.c`): `dijkstra`, A* over (cell, heading) for `route`, the layered BFS
   behind `steps_arr`, `two_way` and shortloop's coverage, `_components`, `line_ok`, `diag_ok`
   and `diag_hops`. `MapModel._c()` picks them when the DLL is loaded and the model has its flat
   bytes; otherwise the Python body runs. Off-grid sources and fractional cells always go to
   Python, which handles (or refuses) them the old way.
2. **A route search costs what it looks at, not the map's size.** The C A* keeps its 4n-state
   arrays between searches in a per-thread `_Workspace` (21 bytes a state: 13 MiB for a 400x400
   map, per thread that routes on it) and marks what each search wrote with a stamp instead of
   clearing anything. Before that, a 30-cell route spent 2.5 of its 2.7 ms faulting fresh
   multi-MB buffers into memory.
3. **`dijkstra_arr`** returns the search as arrays; `travel._steps_from` and the shortloop builder
   read those instead of 150,000-entry dicts. shortloop's greedy cover is one boolean matrix
   (a matrix-vector product a round instead of a set intersection per candidate) and its 2-opt
   reads a stop-to-stop table.
4. **Map loading**: the geometry index records byte offsets (one binary pass, the csv module only
   for each map's first row), so a map's rows are a seek and one read; exit stand cells come from
   one numpy pass per exit.
5. **Caches evict the least recently used entry** (`unclefarmer/lru.py`). They used
   `dict.popitem()`, which drops the *newest* entry: a full cache froze on its first 512 routes
   (128 plans, 256 distance sets) and every later question missed. A route nobody can reach on a
   gated map was never cached and cost two whole-component searches every time; it is now cached
   in `_route_loose`, beside the gate-less fallback paths, never mixed with the gated ones.
6. **Agent replies** are parsed from bytes and the newline is looked for only in the new part of
   the buffer. Measured: no difference on a 30-monster reply (19-21 us either way); kept because
   it is simpler and a long reply no longer rescans its buffer.

7. **Bucket queues** (Dial's algorithm, `routing.c` "queues"): every step costs a whole number of
   half-steps (1 + penalty 4, 1.5, 0.5 or 0, + TURN 12), so open entries go in buckets by cost
   instead of one heap. Dijkstra sorts each bucket by cell when its turn comes (a bucket is
   complete by then: every step lands in a later one), which is Python's (cost, cell) order
   exactly. The route search keeps the bucket being settled as a heap of (guess, state) - a step
   can leave f unchanged - and makes each later bucket a heap when its turn comes; f never falls
   (the guess never drops by more than a step costs), and if it ever did, or PEN or TURN stopped
   being whole half-steps, the search says so (UF_RETRY) and runs again on the plain heap. Far
   route 31 -> 16-19 ms, whole-map dijkstra 23 -> 14 ms. Tested against the heap step for step
   (same path, same states settled) and against Python with off-grid penalties.
8. **`dijkstra_targets`** (`uf_dijkstra_to`): only the targets' distances, on the kept workspace,
   for `travel._steps_from` - a search that stops at the last port costs those cells, not two
   whole-map arrays.

### Agent requests per decision

Read from the loops themselves (no pretend game plays a whole decision cycle at the protocol
level): a walk tick (`walk_diag`, every 40 ms) sends `mem self` - or none, when a snapshot under
0.15 s old answers - plus `mem map` only on a walk meant to leave the map; `clear_popups` adds
a `dialog` read every 2 s and `check_game` a `health` read every 10 s. The live capture below
(60,221 sends in 1,469 s of farming, ~41 a second, 0.2-0.4 ms each on a clean loopback) puts the
link at 1-2 % of the tick. Folding `mem map` into the `mem self` read would save at most ~0.3 ms
a tick on map-leaving walks and needs an agent change, so the protocol and its timing are left as
they are.

### Second pass: the rest of the code, the agent, and what was only assumed (2026-10-23)

Everything below is measured; `benchmark.py` runs each of them again.

* **The rest of the bot.** 600 engine tests (farming, fighting, walking, health, potions,
  follow, town, travel, chasing, targets, kills, pets) run with every `open()`, settings load
  and `re` call counted by the app line that made it: no file is read and no setting loaded per
  decision. The only repeated calls are regexes (`combat.cd_group` 40 calls in 600 tests), and
  the `re` module answers those from its own compiled-pattern cache. The fight loop's own work on
  the tests' pretend game is 20-40 us a decision (11,969 decisions in 600 s of fighting), against
  a 50 ms tick. The message thread reads `settings.json` on timers only (every 3 s and every
  LOOK_EVERY), at 0.09 ms a read (0.43 ms for a profile).
* **Startup.** Importing the window and everything it needs is ~0.85 s (numpy 180 ms, settings
  with Pydantic 258 ms, wx 75 ms), then the geometry index 96-115 ms on the first map. Paid once
  per start, none of it repeated; left alone.
* **Route search size** (routing.c's own counts, map 1041): a 30-cell route settles 2,740
  states (6,847 pushed); a 200-cell one 104,648 (129,645).
* **The route cache on patrol traffic** (5 laps of the short loop, as walk_to plans each hop):
  80 % hits when every hop starts exactly where the last one ended, 18-21 % when the start moves
  by a cell, as a character really stops. With the C searches a miss costs 0.02-0.09 ms a hop, so
  even a perfect cache would save under 10 ms over 5 laps: keying routes more loosely is not
  worth the risk of handing out a path from the wrong cell.
* **A cache hit**: plain `dict.get` 39-44 ns, `functools.lru_cache` 90 ns, `lru.get` 175 ns. The
  difference is the Python call (an inline OrderedDict `get` + `move_to_end` is 89 ns, the same
  as a helper function 196 ns). A whole route-cache hit, lock and path copy included, is 4-5 us,
  so this is ~3 % of a hit a few times a second: kept as the one readable helper.
* **numba**, timed this time instead of assumed: the same whole-map dijkstra compiles in 1.74 s
  on its first call (every app start would pay it), then runs in 26.2 ms against the C search's
  10.4 ms, with identical distances.
* **The agent's shared state** (Java, `agent/src`). Each connection's keys, arming and modifier
  guard live in that connection's thread; the guard is a counted, `synchronized` switch across
  connections; the chat, private-message, cast and death buffers are all `synchronized`, and
  `Said` and `Log` are `synchronized` methods. One race was real and is fixed: `Keys.down`
  checked `HELD` and then sent, so two connections pressing the same key at once both counted it
  as theirs, and the first to close let go of the key the other was walking on. The key is now
  claimed with `putIfAbsent` before it is sent (and given back if the send fails). The agent's 86
  tests and the packet fuzz pass; the jar the game loads has to be rebuilt with every game closed
  to carry it.
* **Not done: a profile of a live farming run.** It needs the game running a farm; py-spy can be
  attached without a restart (see "Profiling a live run" below). The last one (2026-10-20, on the
  Python searches) showed the farm thread idle 97.5 % of the time.

### Rejected after measurement

* 4-ary heap: dijkstra 27.6 against 23.2 ms on 1041, far route no faster.
* numba: 1.74 s to compile on every start and 26.2 against 10.4 ms for the C search once compiled
  (above); the release is a Nuitka build, where a 19 KB C DLL with no Python dependency ships and
  starts cleanly.
* cachebox: the earlier finding holds (dict hit 51-59 ns against 73-86 ns).

### Caches, keys and memory

| Cache | Key | Bound | Invalidation / growth |
|---|---|---|---|
| `MapModel._route_cache` | (from, to, avoid_directional) | 512, LRU | per model; `without()` starts empty; bypassed under `no_gates()` |
| `MapModel._route_loose` | same | 512, LRU | gate-less fallbacks and gated "no way" answers only; same lifetime |
| `MapModel._ws_local` | per thread | one workspace per thread per model | freed with the thread or the model (8 models kept) |
| `travel._steps_cache` | (map, cell, sorted targets) | 256, LRU | static map data |
| `travel._plan_cache` | every plan input | 128, LRU | inputs are the key; deep-copied out |
| `rows_for` | map id | 4 | functools LRU |

Worst-case extra memory from the workspace: 13 MiB per routing thread on the biggest map, at most
8 models (atlas keeps 8): ~100 MiB per thread in theory, a few tens in practice.

### The DLL

* Committed, so a source checkout runs at full speed without a compiler. Named by routing.c's
  sha256: the loader only takes the DLL built from the routing.c beside it, so an edited routing.c
  never runs with an old DLL, and a rebuild never overwrites one a running app has loaded.
  `tests/test_native.py` fails when it is missing or stale.
* Imports only `KERNEL32.dll` and `msvcrt.dll` (libgcc linked in). Built with MSYS2's gcc 16;
  `build_native.py` also takes `$CC`, gcc on PATH, or zig.
* `build_exe.py` copies it to `<release>/unclefarmer/native/`. `UNCLEFARMER_NO_NATIVE=1` turns it
  off.

### Tests

`tests/test_native.py` (42 tests) runs each C search and its Python body on turkey cave, Karon
Village, Spider cave and central plains, and on a hand-made map: dijkstra dicts and arrays with
targets, limits and ledges; routes to cells and exits with and without gates; BFS fields, two-way
areas, regions; 300 random line and two-key checks a map; hop cutting; stamp wraparound; four
threads routing on one model; fractional and off-grid inputs; out-of-memory returns; the loader's
stale/missing/turned-off cases; LRU order; shortloop's matrix cover and travel's array distances
against the dict code, the bucket route search against the heap one state for state, costs off the
half-step grid, and the targets-only search. Full suite: 2430 passed, plus the agent's 86 Java
tests, ruff and pyright clean (`python check.py`).

A bug found on the way: `diag_steps` walked one cell at a time towards a fractional target and
never reached it, so `diag_ok(p, (x + 0.5, y))` grew its list of steps until the PC ran out of
memory (34.6 GB, 2026-10-23). The bot always passes whole cells, so it never happened in a run;
`diag_steps` and `diag_ok` now cut cells to whole ones.

### What is still the limit (measured, ranked)

1. **Nothing CPU-side in a farming run**: walking (distance) and fighting (cooldowns) are the kill
   cycle; see the py-spy section below.
2. **Whole-map searches**, 14-26 ms on the 400x400 map: `travel.plan`'s distance fields on a cold
   trip, a far route. Now memory-bound (the frontier walks a 13 MiB workspace); smaller states
   (float costs) would be the next step, at the price of exact Python equality.
3. **Map load** of the biggest map, 38-49 ms: numpy painting and the remaining exit work.
4. **Geometry index**, 96-115 ms once at start.
5. **`data/short_loops.json` is stale**: today's builder (before and after this change, they
   agree) gives different loops for 1503 and 1001 than the shipped file. Regenerating changes how
   the bot patrols those maps, so it is left for a deliberate decision.

## Before and after (2026-10-20)

| Operation | Before | After | Note |
|---|---|---|---|
| `steps` distance field, 1041, full | 235.8 ms | 25.8 ms (`steps_arr`) | 9x; the dict form is 88.2 ms |
| `steps`, 1041, limit 150 | 62.9 ms | 8.1 ms (`steps_arr`) | 7.5x |
| `steps`, 1041, limit 40 | 4.6 ms | 1.5 ms | 3x |
| choose among 30 monsters, 1041, cold | ~64 ms | 9.7 ms | one field build plus line checks |
| choose among 30 monsters, 1503, cold | ~9 ms | 6.9 ms | caves are small; numpy rings gain less |
| `route`, 30 cells, 1041 | 22.8 ms | 0.001 ms | every hop after the first is a cache hit |
| `route`, 100 cells, 1041 | 115.1 ms | 0.002 ms | same |
| `route`, 200 cells, 1041 | 724.6 ms | 0.002 ms | same; the cache is bounded (512 paths) |
| `travel.plan`, cross map, cold | 789.2 ms | 748.3 ms | unchanged algorithm: two distance fields |
| `travel.plan`, same question | 789.2 ms | 0.022 ms | the plan cache answers |
| `travel.plan`, one leg later (new start cell) | ~750 ms | 0.65 ms | goal side stays cached |
| `shortloop.build`, 1503 | 9.23 s | 2.62 s | offline data generator, not the bot's path |
| `shortloop.build`, 1001 | 26.17 s | 4.61 s | offline data generator |
| `shortloop.build`, 1047 | 0.14 s | 0.07 s | offline |

Unchanged (already cheap or untouched): full Dijkstra 255 ms / 3 targets 433 ms / limit 150
135 ms / limit 25 3.5 ms; `astar_rect` 124 ms; `diag_ok` 0.40 ms; `line` 0.09 ms; `two_way`
and `nearest_walkable` in microseconds; `plan` on one map 0.009 ms.

## What changed, and why it is safe

1. **`MapModel.steps_arr(src, limit=None)`** (`atlaslib.py`) - a layered BFS over numpy arrays,
   one vector step per ring, returning a flat `int32` field (-1 = not reached). `steps()` is now
   a thin wrapper that turns it into the old dict, so every existing caller is unchanged. Same
   walkability, same one-way gates, same limits; checked cell for cell against a plain `deque`
   BFS in `tests/test_atlas.py` (`test_steps_arr_matches_the_plain_bfs_on_a_real_cave`) and
   against the old implementation during the rewrite on maps 1041/1047/1503.
2. **`walking.walk_dist`** reads the array instead of the dict, and treats -1 as unreachable.
   It keeps one field per standing cell until the bot has moved two cells, as before. Test
   stubs whose maps only provide `steps()` fall back to the dict path.
3. **`MapModel.route` cache** (`ROUTE_CACHE_MAX = 512`) - patrol hops and travel ask for the
   same cell pairs again and again, and one search was 20-700 ms depending on distance. Paths
   are stored as tuples and copied out, so a caller cannot corrupt the cache. The cache is
   bypassed while `no_gates()` is active (that path is planned with every door two-way), and
   `without()` starts with an empty cache because its walls move. Eviction only pops, never
   raises, when another bot thread emptied the cache first.
4. **`shortloop`** - the greedy cover only needs what is within `STOP_R`, so candidates now
   use `dijkstra(c, limit=STOP_R)` (1-3 ms) instead of a whole-map search; full fields are
   computed only for the stops that get chosen, and `covered()` is a multi-source numpy BFS.
   Output is identical on 1001/1002/1003/1047/1503 (loop, steps, coverage, stop count).
   This is an offline builder: nothing calls it during a run.
5. **`travel` caches** - `_steps_from` (map, start cell, sorted targets) keeps walking
   distances to ports and transport stands (`STEPS_CACHE_MAX = 256`); `plan` caches the legs
   for every input that affects them (`PLAN_CACHE_MAX = 128`) and deep-copies out. The bot
   re-plans after every leg, which used to repeat the start and goal distance fields; now only
   a new start cell costs a small graph search (0.65 ms), and the goal side is free.

Rejected after measurement: a list/bytearray-based Dijkstra (475.9 ms vs 444.3 full; 4.59 vs
3.44 at limit 25) and local-binding micro-tuning of the Dijkstra loop (~2.5%).

## Cache inventory

| Cache | Where | Key | Bound | Invalidation |
|---|---|---|---|---|
| route paths | `MapModel._route_cache` | `(from, to, avoid_directional)` | 512 | per map object; `without()` starts empty; off under `no_gates()` |
| travel legs | `travel._plan_cache` | all plan inputs (maps, cells, transport/vip/items/team flags, avoid set, bag item names, hearth home/key) | 128 | none needed: inputs are baked into the key |
| port distances | `travel._steps_cache` | `(map, start cell, sorted target cells)` | 256 | none needed: map geometry is static |
| landing ports | `travel._landing_cache` | `(map, landing cell)` | small, pre-existing | none |

All four are "map data does not change during a run" caches. The only moving inputs - the bag,
the settings, the avoid set, where we stand - are part of the keys, so a changed input is a
different key, never a stale hit. Paths and plans are copied on the way out.

## What is still the limit

Ranked by what a run actually pays:

1. **Agent round trips** (`link.py`): measured on a live run (agent 31, loopback) at 0.08-0.41 ms
   each, with `mem` at 2.4 KB; three reads per decision cost about 1 ms, against a kill cycle of
   roughly 11-14 s, so this is not where the time goes. Agent 31 adds `mem state` (map, self and
   entities without bag/slots/modes/ui) and TCP_NODELAY on both ends, but neither moves the kill
   rate. Batching further requests is not worth doing.
2. **The first `travel.plan` on a cold start** (~750 ms, 15 ticks): two full distance fields
   (start ports, goal ports) at ~230-470 ms each. It happens once per trip start, and every
   later leg is under 1.5 ms. A cheaper option would be a numpy Dijkstra, which is a much
   larger change than the caching above.
3. **The first `route` for a long hop** (725 ms at 200 cells, 23 ms at 30): paid once per cell
   pair, then free. Patrol hops are short, so this is mostly a cave-load cost.
4. **`steps_arr` full field** 25.8 ms (640 KB temporary): only `walk_dist` uses step fields and
   it asks with `DIST_LIMIT` (150), which is 8 ms. A full field is used by tools/tests.
5. **`shortloop.build`** 2.6-4.6 s on the big caves, offline only; the app reads the prebuilt
   `data/short_loops.json`.

`dijkstra_rect` (1.77 s) is never called by the app.

## Profiling a live run

`benchmark.py` covers the pure Python work. For a real run, the app is multithreaded (a farming
thread, a private-message thread that long-polls the agent, and the wx main loop), so only
thread-aware, process-wide tools are useful:

    python profile_bot.py --tool yappi --minutes 5          # wall clock, split by thread
    python profile_bot.py --tool tracemalloc --minutes 30   # allocations, per line and per file
    python profile_bot.py --tool all --minutes 5            # both
    py-spy record -p <pid> -d 60 -f speedscope -o %TEMP%\unclefarmer.json   # attach, no restart

For a whole night, on every character at once, `nightwatch.py` does it from outside and then
reads it all together:

    python nightwatch.py watch --hours 8     # detached, low priority; then start farming
    python nightwatch.py status
    python nightwatch.py report              # logs\nights\<start>\report.md and summary.json

The watcher samples CPU, memory, handles and threads of the bot and every game every 30 s, and
keeps py-spy attached in 15-minute pieces (`--nonblocking --idle --threads`, raw format). The app
writes `logs\threads.log` (OS thread id, game, character) so the profile's threads carry names.
The report puts per character the kills and contribution (from the log's hourly lines and
`results\`), deaths, stucks, potions, the gaps between kills with the longest ones and what
happened in them, and every target that gave nothing (not selectable, taken, nearly killed by
someone else, no damage, gone, too long) with the time it cost; then CPU and memory per process
and per hour, and per thread how much time was work, waiting, and waiting on the agent.
py-spy's raw format doesn't mark idle samples, so waiting is read from the innermost Python frame
(`sleep`, a socket read, `MainLoop` and the like).

pyinstrument was dropped: its `sys.setprofile` hook only sees the calling thread, misses the
farming and message threads, and interferes with wx. yappi is C-level and per-thread; tracemalloc
sees every thread's allocations; py-spy samples all threads from outside.

Reports land in `logs\profiles\<time>\`. `--minutes` stops the profiler and writes the report
while the bot keeps running. The yappi report starts with one row per thread (thread wall, Python
waits, the rest) and then lists each thread's top functions *excluding* waits, so the messenger's
`pms` long-poll cannot hide the farming work. Thread totals overlap in time: never add them up.
Blocking C calls (socket reads, `time.sleep`) carry no Python frame, so their time sits inside the
function that called them (`AgentLink.send` for reads, `Bot.sleep`'s `Event.wait` is visible);
divide a total by its call count to see that per-call cost. yappi's own overhead inflates very short calls (2.8 ms average for `AgentLink.send` in the bot thread, against 0.2-0.4 ms measured by a plain socket probe).

`get_func_stats(ctx_id=0)` is the main thread, but yappi treats 0 as "no filter" and returns every
thread's stats, and `get_func_stats()` with no argument *merges* a function that several threads
call into one stat tagged with a single context. So the report asks per context â€”
`get_func_stats(ctx_id=<thread context>)`, which does filter â€” and gives the main thread only what
no other context claimed. A function the whole process shares (`AgentLink.send`, `Tally.result`)
used to land entirely in the wx thread's list: the 2026-10-20 yappi dump credited it with 66,463
`AgentLink.send` calls and 13,572 `Tally.result` calls, of which py-spy showed it made none.

Thread names are taken by patching `Thread.start`, not `Thread.run`: the app's farming and
messaging threads, and `threading.Timer`, all subclass `Thread` and override `run()`, so a patch on
`run` never saw them and every one of them was printed as "thread 2556" (and the harness's own
timer was not recognised, so its whole run appeared as a row in the table).

### What the collected runs showed (2026-10-20)

yappi, 25 minutes of farming (1500 s), per thread:

| thread | wall | what it was doing |
|---|---|---|
| farming (`unclefarmer-run-*`) | 1469 s | 130 fights 1173 s (9.0 s each), `walk_to` 135 calls 802 s (5.9 s), `_hit_until_down` 118 calls 608 s (5.2 s), `burst` 521 calls 341 s (0.65 s), `Bot.sleep` 2166 calls 273 s, `select_target` 128 calls 40 s, travel 3 calls 99 s (one `cross_exit` alone 98 s), `AgentLink.send` 60221 calls 169 s (2.8 ms) |
| messenger | 1493 s | 2244 `send` calls taking 1490 s: the deliberate `pms` long-poll (0.66 s each); its real work is ~2.5 s |
| wx main loop | 1500 s | almost all of it parked in `MainLoop`; the only work is `show_results` 22 s and `poll` 12 s |

So ~78 % of the farming thread's wall clock is inside `Bot.fight`, and inside a fight walking
(802 s) costs more than casting (341 s, 20.8 casts/min). Target spacing and approach, not the
agent protocol and not the caches, are what the kill rate is made of.

tracemalloc, 30 minutes (207 MiB live, 217 MiB peak), top live allocations:

| size | where | what |
|---|---|---|
| 47.6 + 41.1 MiB | `csv.py:177`, `csv.py:185` | every geometry row list and dict, kept forever by `atlaslib._rows` |
| ~54 MiB | `atlaslib.py:329-334` | the per-map flat lists (`_walk_list`, `_clear_list`, `_gate_list`, `_portal_list`, `_oneway_list`, `_zero_list`), ~9 MiB each |
| KB | `travel.py:113-126` | the travel caches, small in this run |

### Map data in memory (fixed 2026-10-20)

Both of those are now gone:

| what | before | after |
|---|---|---|
| names + ids + line index kept for 375 maps | 87.4 MiB (`all_rows()` built every row) | 0.12 MiB |
| traced live heap after painting two maps | 119.8 MiB | 21.3 MiB |
| flat routing caches of one 402x402 map | 7.40 MiB (six lists) | 0.92 MiB (six `bytes`) |

`all_rows()` is now a lazy mapping: one streaming pass records each map's name and the line range of
its rows (the file is grouped by map: 375 groups, no map split by another), and `rows_for(mid)`
reads just that range back, cached for `GEOMETRY_CACHE` (4) maps. Startup is no slower (names +
index 1.19 s against 1.50 s for the old full parse), and painting a map costs 0.75 s against 0.69 s.
The flat caches are `bytes`, which index to the same ints. An in-process A/B on map 1041 (six
repeats each): `steps` limit 150 27.8 -> 21.7 ms for the BFS that target selection uses, dijkstra
full 386 -> 366 ms the other way, both inside the run-to-run spread measured earlier.

The travel caches are only kilobytes in a real run, so the old "167 MiB step cache" claim was an
artifact of the cumulative tracemalloc view; there is nothing to fix there.

### Why a kill cycle costs what it costs

The app's own phase reports (2.8 h of logs, 2026-10-20) split every cycle the same way: walking to
monsters 36-43 %, fighting 41-51 %, searching 1-25 %, selecting 1-2 %, town and travel 1-7 %, at
10-14 s a kill (255-350 kills an hour). yappi agrees from the other side: 78 % of the farming
thread's wall clock is inside `Bot.fight`, and 802 s of its 25 minutes is `walk_to`.

That walking is travel, not waste: paths on open ground are straight (150 random 10-40 tile pairs on
map 5601: walked/straight median 1.00, p90 1.00, max 1.13), the walk stops at the class's attack
range, and the target is already the nearest one by walking distance. The remaining levers are
gameplay, not code: a denser spawn area, more damage per global cooldown, or the game walking us
into range while the skills keep firing.

One of those is now a setting. `pull_range` (Fighting page, 0 by default): with 0 the bot walks
until the monster is in reach; with, say, 4-6 it starts firing when a monster is that many tiles
outside reach *and is closing the gap itself* (our own walking does not count, or every target
would trigger it). A charging monster closes the last few tiles for us, which takes a second or so
off the cycle; a stationary or ranged one still gets the full walk, so nothing is wasted on refused
casts. Measure it against the log's own report line:

    grep "a kill -" logs/<date>.log | tail -5      # kills an hour, seconds a kill, and the phases

### py-spy on a live farm, 25 minutes (2026-10-20)

    py-spy record -p <bot pid> --duration 1500 --rate 100 --format raw -t -i -o raw.txt

`--idle` samples every live thread at the same rate, so a thread's samples follow its *wall clock*,
which is what a bot that mostly waits needs. A 25 minute Hunter farm (334 kills an hour, 10.7 s a
kill, from the run's own report) came out like this:

| thread | samples | of the capture | blocked | waiting on the agent | running |
|---|---|---|---|---|---|
| farm/run | 149 999 | 26.6 % | 97.5 % | 15.3 % | 2.5 % |
| wx (GUI) | 149 999 | 26.6 % | 93.9 % | 0.1 % | 6.1 % |
| messenger | 149 999 | 26.6 % | 99.5 % | 99.5 % | 0.5 % |
| profiler timer (harness) | 113 121 | 20.1 % | 99.2 % | 0 % | 0.8 % |

The farm thread, by share of its own wall clock: **fighting 56.5 %, walking 42.1 %, town and travel
1.4 %**, reading 0.05 %, map/model work 0.01 %. The run's own report for the same 25 minutes said
walking to monsters 39 %, fighting 53 %, selecting 2 %, searching 3 %, town 2 % - two independent
methods, three points apart.

Where it waited (closest frame under a blocking leaf): 38.8 % inside `stop_event.wait(0.04)` in
`walk_diag` (the 40 ms walk tick), 29.8 % in `burst`, 20.0 % in `_hit_until_down`'s sleeps, 3.0 %
in `key`, 2.6 % in `select_target`. Nothing on the running side reaches even 0.2 % of the wall:
the busiest Python leaves are `Tally.result` 6.8 % *of the 2.5 % running*, `potion_kind`, the bag
expressions and `rows_for`.

What that means: the bot is idle 97.5 % of the time, so there is no CPU left to win - the cycle is
6.0 s of fighting (global cooldown bound: ~3-4 casts at 1.28 s) and 4.5 s of walking (distance
bound: paths are straight, the walk stops at attack range). The two levers left are gameplay ones:
more damage per cooldown, a denser spot, or starting the damage earlier - `pull_range` is still 0
in this profile, so the closing second of every melee approach is unclaimed.

Two things to be careful with. This capture ran under yappi for about two thirds of its length (the
harness had `--minutes 20`), so the 15.3 % spent waiting on an agent reply is an upper bound: a
clean loopback round trip measured 0.2-0.4 ms while this average is nearer 4 ms, which is either
yappi's own overhead or the game's agent thread competing with the game. And the yappi report of
the same window cannot be trusted per thread: it credits 66 463 `AgentLink.send` calls and 13 572
`Tally.result` calls to the wx thread, which py-spy shows making neither (it sits in `MainLoop`),
and it shows every real thread as "thread 2556" because the app's own threads subclass
`threading.Thread` and override `run`, so a patch on `Thread.run` never sees them.

A second, yappi-free capture of the same bot failed: the app had exited by then ("Failed to open
process"), so the clean numbers for the agent wait are still to be collected.

### Cache containers: keep the plain dicts

`benchmark.py` now measures the bounded dicts the bot uses against `cachebox` (Rust LRU), on the
same key and value shapes:

| operation | dict | cachebox |
|---|---|---|
| hit | 64-81 ns | 98-102 ns |
| set + evict the oldest | ~200 ns | 107-117 ns (auto-evict) |
| hit rate on a skewed 3000-key trace, 512 slots | 8.5 % | 8.5 % |

The dict is faster on the hit path, which is what the bot does thousands of times per run; the
cachebox set path is faster only because it folds the eviction into the insert, and the real
insert paths (`route`, `steps_from`, `plan`) already only check the size on a miss. The LRU
eviction order bought no hit-rate gain on the skewed trace. `plan`-shaped keys (tuples holding
frozensets) cost ~203 ns per dict lookup, dominated by hashing, so no container change can help
there. Verdict: no `cachebox` dependency; the hand-rolled caches stay.

## Checking a change

    python -m pytest -q -m "not slow" -p no:cacheprovider
    python -m ruff check unclefarmer tests benchmark.py profile_bot.py
    python benchmark.py --quick
    python build_native.py                 # after any edit of unclefarmer/native/routing.c
    python -m pytest -q tests/test_native.py    # the C searches against the Python ones
    set UNCLEFARMER_NO_NATIVE=1 && python benchmark.py   # the Python searches, for comparison

The suite (2180 tests) covers the caches and the array field: `test_steps_arr_is_the_same_search_as_steps`,
`test_steps_arr_matches_the_plain_bfs_on_a_real_cave`, `test_route_is_cached_but_callers_get_their_own_list`,
`test_route_cache_cannot_leak_a_path_planned_without_gates`,
`test_without_starts_with_no_cached_routes`, `test_plan_cache_hands_out_a_copy`,
`test_plan_is_computed_once_for_the_same_question`, `test_steps_from_is_cached_per_question`.
