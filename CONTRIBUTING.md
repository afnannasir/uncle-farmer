# Contributing to Uncle Farmer

Thank you for helping! Every bug report, fix, translation and map tip makes the bot better for
everyone.

## Reporting a bug

Open an issue and include:

- what you did, what you expected, and what happened instead;
- the map you were on, and the plan or script you ran;
- the day's log from the `logs` folder, if you can (look through it first: it may contain the names
  of players who sent your character private messages, and you can remove those).

## Changing the code

1. Fork the repository and make a branch for your change.
2. Set up as in the README's "Running from source", and test in the game.
3. Keep to the house style:
   - **Screen readers first.** Native wx controls only, a label created right before every field
     (use `gui/common.py`'s `Form`), short labels, units in brackets, and no "0 = off" codes: an
     optional value gets a check box.
   - **Every string the player sees goes through `_()`**, so it can be translated.
   - **Read the game, don't guess.** Prefer what the game itself says (its texts, its messages)
     over fixed numbers in settings.
   - **Nothing leaves the computer.** No network calls except to the agent on `127.0.0.1` and the
     update check in `unclefarmer/updater.py` (GitHub's latest release, nothing sent about the
     player; it can be turned off), and
     no chat or packet logging in the agent.
   - **No personal details in code or comments**: no character names, account details, or other
     players' names. Use made-up examples.
4. Run the checks:

   ```
   uv sync
   uv run python check.py
   ```

   `uv sync` installs the exact versions in `uv.lock` into `.venv`; the tools can also run on their
   own (`uv run pytest`, `uv run ruff check .`, `uv run pyright`). check.py runs ruff, pyright and
   pytest with coverage, and says "all checks passed" or which failed. Coverage must stay at 70%
   overall and at 90% for `unclefarmer/engine` (the GUI has no tests, so it must not be able to
   hide an engine regression). `uv run python check.py --quick` leaves out the slow real-time tests. pytest runs in parallel (`pytest-xdist`) and in random order
   (`pytest-randomly`); use `-n0` and `--randomly-seed=<number>` when reproducing a failure.

   The same checks run before every commit once you install the hook:

   ```
   git config core.hooksPath .githooks
   ```

   Skip it for one commit with `git commit --no-verify`.

   ruff checks style and likely bugs; pyright type-checks the code in standard mode and must report no
   error. Every part of the Bot declares what it needs in `engine/botparts/parts.py`, so a mixin's
   calls on `self` are checked without the part that implements them. Snapshots and profiles are
   `dict[str, Any]` (what the agent sends), not typed field by field. Test files carry
   `reportAttributeAccessIssue=false` at the top: the stand-ins replace real methods with fakes, and
   running the tests is what checks them. Add the hints a changed function needs rather than
   loosening `pyrightconfig.json`.
   The tests need no game: they fight, walk and follow against a pretend game, replay a recorded
   fight (`tests/data`), and check the map data, translations and CPU use. `uv run pytest -m "not
   slow"` skips the tests that measure CPU use or search real map routes. Bot fights and waits use
   an injected clock, so they are deterministic and fast. Test data must stay anonymous: made-up players and ids.

   **Long runs.** `tests/simgame.py` is a pretend game behind the agent's own protocol: the real
   bot connects to it as to the game (port file, `auth`, one line in, one line out) and runs a whole
   `Run` - scan, setup, script, fights, results, the end - against a real map with monsters that
   spawn, fight back, die and drop loot. The game's clock is the run's: waiting moves it on at once,
   so an hour of farming takes about 20 seconds. `tests/test_simgame.py` runs ten game minutes on
   every normal test run. The soak tests run hours of it and are skipped unless asked for:

   ```
   uv run pytest --soak -m soak -n 0 -s tests/test_soak.py
   set UNCLEFARMER_SOAK_HOURS=8 && uv run pytest --soak -m soak -n 0 -s tests/test_soak.py
   ```

   They measure as they go: the memory the app holds (start, middle, end - it must level off),
   threads and open handles (back to where they were after every start and stop), the results
   against what the game really did (no drift), `errors.log` (empty), stuck walks (none), and keys
   (every run lets go of its own before its connection closes). Faults are injected - a dropped
   connection, a stalled or garbled answer, the character killed, the bag filled - and every run
   must stop or recover safely. A soak only finds what the simulator can do, so grow it from real
   bugs: when a long run goes wrong in the real game, add what happened as an event (`Events` in
   `simgame.py`), give it a test in `test_soak.py`, and it stays covered. Run them on a quiet PC: a
   few minutes each, a few hundred MB of memory.
5. Open a pull request that says what it changes and how you tested it.

## Dependencies

Every package is listed in `pyproject.toml`: what the app ships under `dependencies`, the checking
tools in the `dev` group, Nuitka in the `build` group. `uv.lock` pins the exact version of each,
and CI's `uv sync --locked` fails when the two disagree, so commit them together:

```
uv add <package>                 # a package the app needs
uv add --group dev <package>     # a tool for checking it
uv lock --upgrade                # move every pin to the newest allowed version
uv lock --upgrade-package numpy  # just one
```

To check the pinned versions for known vulnerabilities, the way CI does:

```
uv export --locked --all-groups --no-emit-project --format requirements-txt --output-file locked.txt
uv run pip-audit --requirement locked.txt --require-hashes --disable-pip
```

## Releasing

Write the version's changes at the top of `CHANGELOG.md`, commit and push, then
`uv run python release.py 0.5.1 --notes-file notes.md` (the new section as the notes). It runs check.py,
sets the version, builds the folder, zips it as `dist/UncleFarmer-0.5.1.zip` and makes the GitHub
release v0.5.1 with it, using the GitHub CLI (`gh`, logged in). Every copy's updater finds it there:
the notes are what it shows under "What's new". Commit the version change afterwards.

`--draft` makes a draft no copy sees until you publish it; `--local` only builds and zips, with the
notes inside as "What's new.txt" and beside it as `UncleFarmer-0.5.1.txt`.

## Translating

Copy `lang/unclefarmer.pot` to `lang/<language code>.po`. In its header set `Language` to the code
and `X-Language-Name` to the language's own name (that is what Settings shows), then fill in the
`msgstr` lines. An empty `msgstr` stays in English, and the `{placeholders}` must stay as they are.
When new strings are added, `uv run python -m unclefarmer --strings` refreshes the template, merges your
file (translations of strings that are gone are kept as `#~` lines) and compiles
`lang/<code>/LC_MESSAGES/unclefarmer.mo`, which the app loads with gettext; wx loads the same
catalog, plus its own `wxstd` one for standard buttons and menus.

## Licence

By contributing, you agree that your work is shared under the project's licence, the GNU GPL v3.0.
