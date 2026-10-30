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

1. Fork the repository and make a branch for your change from `dev` (see "CI/CD" below).
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
5. Open a pull request into `dev` that says what it changes and how you tested it. CI tests it;
   it can be merged once every required check is green.

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

## CI/CD

Two long-lived branches:

- **`dev`** - where work comes together. Branch from it (`git switch -c my-change dev`) and open a
  pull request back into it.
- **`master`** - the stable, production code. It only changes through a pull request from `dev`
  (or a release branch), and only what passed every check gets there.

One workflow, `.github/workflows/ci.yml`, does everything. Its jobs:

| Job | Runs on | What it does |
|---|---|---|
| Python checks (Windows, Python 3.11 / 3.14) | every pull request into `dev` or `master`, every push to them | `uv sync --locked`, pip-audit of `uv.lock`, then `check.py --skip-agent --report-dir reports`: ruff, pyright, pytest with the coverage floors (70% overall, 90% for `unclefarmer/engine`). The JUnit and coverage XML are kept as the `test-reports-python-<version>` artifact, also when tests fail. |
| Java agent (build, Java 8 check, tests) | the same | `agent/build_agent.py` builds the jar (`--release 8`, warnings are errors), `ci_build.py verify-agent` checks its manifest and that every class is Java 8 (the game's Java), `agent/java_tests.py` runs the agent's tests and the packet parser fuzz. |
| Windows build (development / production) | pushes to `dev` and `master` only, after **both** jobs above passed | builds the app from the same commit the tests checked (`ci_build.py build`, see below) and uploads it. |

Pull requests are tested, never built. Other branches run nothing (push one as a pull request to
test it). A newer push to a pull request or to `dev` cancels the run still going; a run on `master`
is never cancelled. Every job runs on `windows-latest`: the app, its paths and its tests are
Windows-only. The tests need no game and no developer files: the agent jar, `game_texts.json`, the
player's folders and every temporary file are made in the run's own folders.

**The build** (`ci_build.py build --type development|production`) is the project's own build, not
a second one: `build_exe.py --full` (the agent jar, Nuitka, UPX, the data, the folder check) and
`release.py`'s `make_zip`. Then it checks:

1. the folder and the zip hold everything in `build_exe.REQUIRED` (the exe, Python and wx DLLs, the
   agent jar, the NVDA bridge, the routing DLL, the data, translations and guides); the zip is read
   back member by member;
2. the built exe checks itself without a window: `UncleFarmer.exe --self-test report.json`
   (`unclefarmer/selftest.py`: the libraries, the routing DLL, the data, the translations and the
   agent jar), within 2 minutes;
3. it writes `build-info.json` and `<zip>.sha256`, the SHA-256 of the finished zip.

UPX is installed by the workflow (version and SHA-256 pinned in `ci.yml`) and found through
`UPX_DIR`. On your PC `build_exe.py` takes `UPX_DIR`, else the `upx` on PATH (`winget install
UPX.UPX`); without either it stops with that message instead of building something different.

**Artifacts** (Actions -> the run -> Artifacts at the bottom; they are zips of the files below):

| Branch | Artifact | Kept |
|---|---|---|
| `dev` | `UncleFarmer-dev-<short sha>` | 14 days |
| `master` | `UncleFarmer-master-<version>-<short sha>` | 90 days |

Each holds the app zip of the same name, its `.zip.sha256` and `build-info.json` (application,
version, build type, branch, full and short commit, UTC time, run id and number, Python, Java and
UPX versions, runner, package size and SHA-256). The version is the project's own
(`unclefarmer/__init__.py`); CI never changes it. To check a download:

```
certutil -hashfile UncleFarmer-dev-1a2b3c4.zip SHA256
type UncleFarmer-dev-1a2b3c4.zip.sha256
```

The two hashes must match. A `dev` artifact is a development build: never hand it out as a release.

**The same checks on your PC:**

```
uv sync --locked
uv run python check.py                     # what the Python job runs (plus the Java tests if you have a JDK)
uv run python agent/build_agent.py dist\agent-check.jar
uv run python ci_build.py verify-agent dist\agent-check.jar
uv run python agent/java_tests.py
uv run python ci_build.py build --type development     # the whole build job, about half an hour
```

**Secrets:** none. No job reads a secret or gets more than read access to the repository, pull
requests from forks run with the same read-only token, and nothing is signed or published.

**Branch protection.** The rules are in `.github/rulesets/`. Once the GitHub CLI is logged in with
admin rights on the repository, apply them (OWNER/REPO: the repository's owner and name):

```
gh api -X POST repos/OWNER/REPO/rulesets --input .github/rulesets/master.json
gh api -X POST repos/OWNER/REPO/rulesets --input .github/rulesets/dev.json
```

Or by hand: Settings -> Rules -> Rulesets -> New branch ruleset, one for `master` and one for `dev`:

- Target branches: `master` (or `dev`); enforcement **Active**; **no bypass list** (admins included).
- **Restrict deletions** and **Block force pushes**.
- **Require a pull request before merging** (`master`: also dismiss stale approvals and require
  conversation resolution). Required approvals: 0 while one person maintains the project; set it to
  1 once there are other collaborators who can review.
- **Require status checks to pass**: `Python checks (Windows, Python 3.11)`, `Python checks
  (Windows, Python 3.14)`, `Java agent (build, Java 8 check, tests)` (they appear in the list after
  the workflow's first run). `master`: also **Require branches to be up to date before merging**.

The build job is not a required check on purpose: it never runs for a pull request.

## Releasing

A release is made from `master`, by hand: CI builds every push to `master` (the production
artifact above) but never publishes a release, signs anything or changes the version. When `dev`
holds what the release should have, merge it into `master` with a pull request, then on `master`:
write the version's changes at the top of `CHANGELOG.md`, commit and push, then
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
