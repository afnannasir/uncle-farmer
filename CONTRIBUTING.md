# Contributing to Uncle Farmer

Thanks for your interest in helping with Uncle Farmer!

Whether you're reporting a bug, fixing something, improving a translation, or sharing a useful map tip, your contribution helps make the bot better for everyone.

You don't need to contribute code to help. Every useful report, suggestion, and improvement is appreciated.

## Table of contents

- [Reporting a bug](#reporting-a-bug)
- [Contributing code](#contributing-code)
- [Development checks and tests](#development-checks-and-tests)
- [Dependencies](#dependencies)
- [CI/CD](#cicd)
- [Releasing](#releasing)
- [Translating](#translating)
- [Licence](#licence)

## Reporting a bug

Found something that doesn't work as expected? Please open an issue and tell us what happened.

Include as much of the following information as you can:

- What you did.
- What you expected to happen.
- What happened instead.
- Which map you were on.
- Which plan or script you were running.
- The day's log from the `logs` folder, if available.

Before sharing a log, please check it for private information. It may contain the names of players who sent your character private messages. You can remove those names before attaching the log.

If Uncle Farmer won't start or won't close, please include the relevant file:

- `logs/bootup.log` when startup fails.
- `logs/teardown.log` when shutdown fails.

Each startup and shutdown is recorded as a sequence of stages, from `BEGIN` to `END`. Failed stages include their traceback, which helps identify what went wrong.

### Bug report template

You can copy this template when opening an issue:

```text
## What happened?

Describe the problem.

## What did you expect to happen?

Describe what should have happened instead.

## Steps to reproduce

1.
2.
3.

## Game information

Map:
Plan or script:

## Logs

Attach the relevant log file, if available.
Remember to remove private player names before sharing it.
```

You don't have to fill in every section. Even a small report can help, especially if it includes clear steps to reproduce the problem.

## Contributing code

Ready to work on the code? Here's how to get started.

### 1. Fork the repository

Create your own fork of Uncle Farmer on GitHub.

Then create a branch for your changes from `dev`:

```powershell
git switch -c my-change dev
```

Replace `my-change` with a short name describing your work.

See [CI/CD](#cicd) for more information about the project's branches.

### 2. Set up the development environment

Follow the [Running from source](README.md#running-from-source) instructions in the README.

Once everything is set up, test your changes in the game whenever practical.

### 3. Follow the project's coding style

A few rules are especially important when working on Uncle Farmer.

**Screen readers come first.**

- Use native wx controls only.
- Create a label immediately before every field. Use `gui/common.py`'s `Form` for this.
- Keep labels short.
- Put units in brackets.
- Avoid special numeric codes such as `0 = off`. If a setting is optional, give it a checkbox.

**Make every player-facing string translatable.**

Every string the player sees must go through `_()`.

**Read the game instead of guessing.**

Prefer information provided by the game itself, such as its text and messages, rather than relying on fixed numbers in settings.

**Keep player information private.**

Nothing should leave the computer except for the explicitly permitted connections:

- Communication with the agent on `127.0.0.1`.
- The update check in `unclefarmer/updater.py`, which checks GitHub's latest release or release list on the Dev channel.

The update check sends no information about the player and can be turned off.

The agent must not log chat messages or packets.

**Keep personal details out of the code.**

Do not include character names, account details, or other players' names in code or comments. Use made-up examples instead.

## Development checks and tests

Before submitting a pull request, run the project's checks.

### 1. Install the dependencies

From the project folder, run:

```powershell
uv sync
```

This installs the exact package versions listed in `uv.lock` into `.venv`.

### 2. Run the checks

Run:

```powershell
uv run python check.py
```

The script runs:

- Ruff
- Pyright
- check-manifest
- pytest with coverage

It reports either `all checks passed` or which checks failed.

You can also run individual tools:

```powershell
uv run pytest
uv run ruff check .
uv run pyright
```

### 3. Understand the coverage requirements

The project has two coverage requirements:

- **78% overall coverage**, enforced by `fail_under` in `pyproject.toml`.
- **92% coverage for `unclefarmer/engine`**, enforced by `CORE_FLOOR` in `check.py`.

The engine has a higher requirement because the GUI has relatively few tests and should not hide regressions in the core engine.

A plain `uv run pytest` does not measure coverage. Use `--cov` when you want pytest to enforce the coverage requirement.

To skip the slower real-time tests:

```powershell
uv run python check.py --quick
```

### 4. Understand how the tests run

The test suite does not need the actual game.

It uses a pretend game to test fighting, walking, following, and other behavior. It also replays a recorded fight from `tests/data` and checks map data, translations, and CPU use.

The tests use:

- `pytest-xdist` to run tests in parallel.
- `pytest-randomly` to run tests in random order.

If you need to reproduce a failure, you can disable parallel execution and choose a specific random seed:

```powershell
uv run pytest -n0 --randomly-seed=123
```

Replace `123` with the seed you want to use.

### 5. Running specific tests

To skip tests marked as slow:

```powershell
uv run pytest -m "not slow"
```

These tests normally include CPU measurements and searches for real map routes.

Bot fights and waits use an injected clock, making them deterministic and fast.

Test data must remain anonymous. Use made-up player names and IDs.

### 6. Long-running tests

The project also includes a simulated game for testing longer runs.

`tests/simgame.py` acts as a pretend game behind the agent's own protocol. The real bot connects to it just as it would connect to the actual game, using the port file, authentication, and one-line-in, one-line-out communication.

It then runs a complete `Run`:

- Scanning
- Setup
- Script execution
- Fighting
- Results
- Shutdown

The simulator uses a real map with monsters that spawn, fight back, die, and drop loot.

Waiting advances the simulated game clock immediately. This means an hour of farming takes approximately 20 seconds.

`tests/test_simgame.py` runs ten simulated game minutes during every normal test run.

For longer tests, use the soak tests. These are skipped unless you explicitly request them.

Run the default soak test:

```powershell
uv run pytest --soak -m soak -n 0 -s tests/test_soak.py
```

To run an eight-hour simulated test:

```powershell
$env:UNCLEFARMER_SOAK_HOURS = "8"
uv run pytest --soak -m soak -n 0 -s tests/test_soak.py
```

The soak tests monitor:

- Memory usage at the start, middle, and end. Memory use should level off.
- Threads and open handles. They should return to their previous levels after every start and stop.
- Results compared with what actually happened in the simulated game. There should be no drift.
- `errors.log`, which should remain empty.
- Stuck walks, which should not occur.
- Keyboard keys, which every run must release before its connection closes.

The tests also inject faults, such as dropped connections, stalled or garbled responses, a killed character, and a full bag. Every run must stop or recover safely.

A simulator can only find problems it knows how to reproduce. When a long run fails in the real game, add the behavior as an event in `Events` inside `simgame.py`, then add a corresponding test in `test_soak.py`.

This keeps real-world problems covered by future tests.

Run soak tests on a quiet PC. They can take several minutes and use a few hundred megabytes of memory.

### 7. Install the Git hook (optional)

The same checks can run automatically before every commit.

Install the hook with:

```powershell
git config core.hooksPath .githooks
```

To skip the hook for one commit:

```powershell
git commit --no-verify -m "your commit message"
```

### 8. Understand what the tools check

**Ruff** checks code style and looks for likely bugs.

**Pyright** checks types in standard mode. It must report no errors.

Every part of the bot declares what it needs in `engine/botparts/parts.py`. This allows a mixin's calls on `self` to be checked even when another part implements those methods.

Snapshots and profiles use `dict[str, Any]`, matching the data sent by the agent, rather than defining every field separately.

Test files have `reportAttributeAccessIssue=false` at the top. Their stand-ins replace real methods with fakes, so running the tests is what checks those replacements.

When changing a function, add the type hints it needs instead of weakening `pyrightconfig.json`.

## Dependencies

Every package used by the project is listed in `pyproject.toml`.

Packages are grouped by purpose:

- `dependencies`: Packages the application needs.
- `dev`: Tools used for development and checks.
- `build`: Build tools, including Nuitka.

`uv.lock` records the exact version of each package.

CI uses `uv sync --locked` and fails if `pyproject.toml` and `uv.lock` disagree. When changing dependencies, commit both files together.

### Common dependency commands

Add a package required by the application:

```powershell
uv add <package>
```

Add a development tool:

```powershell
uv add --group dev <package>
```

Upgrade every package to the newest allowed version:

```powershell
uv lock --upgrade
```

Upgrade only one package:

```powershell
uv lock --upgrade-package numpy
```

Refresh the pip requirements files after changing the lock:

```powershell
uv run python export_requirements.py
```

### Requirements files

The project also provides two files for people who use pip instead of uv:

- `requirements.txt`: Packages needed to run the application.
- `requirements-dev.txt`: Application packages, development tools, and Nuitka.

These files contain pinned versions and hashes exported from `uv.lock`.

Do not edit them by hand. Always generate them using `export_requirements.py`.

`tests/test_requirements.py` checks that both files match `uv.lock`. `release.py` also exports them again when setting a version.

### Checking for vulnerable dependencies

To check the locked package versions for known vulnerabilities, run:

```powershell
uv export --locked --all-groups --no-emit-project --format requirements-txt --output-file locked.txt
uv run pip-audit --requirement locked.txt --require-hashes --disable-pip
```

This is the same dependency audit used by CI.

## CI/CD

Uncle Farmer uses two long-lived branches.

### Branches

**`dev`**

This is where new work comes together.

Create your feature branch from `dev`, then open a pull request back into `dev`.

For example:

```powershell
git switch -c my-change dev
```

**`master`**

This contains the stable, production code.

It only changes through a pull request from `dev` or a release branch. Only changes that pass every required check should reach `master`.

### Continuous integration

The workflow is defined in:

```text
.github/workflows/ci.yml
```

It runs the project's checks automatically.

The workflow has six jobs.

**1. Static checks (ruff, pyright)**

Runs on every pull request into `dev` or `master`, and every push to those branches.

It runs:

```powershell
uv sync --locked
uv run python check.py --only ruff,pyright,manifest
```

This checks Ruff, Pyright, and check-manifest.

check-manifest builds the source distribution using the locked setuptools and compares the files in the distribution with the files tracked by Git. It uses `MANIFEST.in` and the tracked project tree.

**2. Tests (Windows, Python 3.14)**

Runs on the same pull requests and pushes.

It runs:

```powershell
uv sync --locked
uv run pytest --cov --cov-report=term-missing
uv run python check.py --only core
```

The first test command runs the full suite and measures coverage of `unclefarmer/`, showing which lines were missed. It fails below 78%.

The second command checks the engine's 92% coverage requirement.

JUnit and coverage XML reports are saved as the `test-reports` artifact, including when tests fail.

**3. Dependency audit (pip-audit)**

Checks every package in `uv.lock` using pip-audit. Hashes are required.

**4. Java agent (build, Java 8 check, tests)**

Builds the agent with `agent/build_agent.py` using `--release 8`, with warnings treated as errors.

Then:

- `ci_build.py verify-agent` checks the agent's manifest and verifies that every class is Java 8 compatible.
- `agent/java_tests.py` runs the agent's tests and packet parser fuzzing.

**5. Workflow lint (actionlint)**

Checks every workflow in `.github/workflows` using actionlint 1.7.12.

The release binary's SHA-256 is checked before use. The Ubuntu runner's shellcheck also checks each `run:` script.

When upgrading actionlint, update both `ACTIONLINT_VERSION` and `ACTIONLINT_SHA256` in `ci.yml` using the release's checksums file.

**6. Windows build (development / production)**

Runs only on pushes to `dev` and `master`, after all five check jobs have passed.

It builds the application from the same commit that passed the tests using:

```powershell
uv run python ci_build.py build --type development
```

The build process is described below.

### When CI runs

Pull requests are tested but are not built.

Other branches do not run the workflow. Push a branch as a pull request if you want CI to test it.

A newer push to a pull request or to `dev` cancels the run still in progress. A run on `master` is never cancelled.

Every job except actionlint and the Dev publishing job runs on `windows-latest` with Python 3.14. This is the only supported Python version because the application, its paths, and its tests are Windows-only.

The five check jobs run at the same time, so the workflow takes approximately as long as its slowest job.

The tests do not need the actual game or developer files. The agent jar, `game_texts.json`, player folders, and temporary files are created inside each run's own folders.

### The build

The project's own build command is:

```powershell
uv run python ci_build.py build --type development
```

For a production build, use:

```powershell
uv run python ci_build.py build --type production
```

This is not a separate build process. It uses:

- `build_exe.py --full` to build the agent jar, run Nuitka, use UPX, include the data, and check the folder.
- `release.py`'s `make_zip` to create the application archive.

The build then checks the following:

1. The output folder and zip contain everything listed in `build_exe.REQUIRED`, including the executable, Python and wx DLLs, agent jar, NVDA bridge, routing DLL, data, translations, and guides. The zip is read back and checked member by member.
2. The built executable runs its own test without opening a window:

   ```powershell
   UncleFarmer.exe --self-test report.json
   ```

   This checks the libraries, routing DLL, data, translations, and agent jar through `unclefarmer/selftest.py`. It must finish within two minutes.

3. The build creates `build-info.json` and `<zip>.sha256`, containing the SHA-256 hash of the finished zip.

UPX is installed by the workflow. Its version and SHA-256 are pinned in `ci.yml` and it is located through `UPX_DIR`.

On your PC, `build_exe.py` uses `UPX_DIR` if set. Otherwise, it looks for `upx` on PATH.

You can install UPX with:

```powershell
winget install UPX.UPX
```

Without UPX, the build stops with a message instead of silently building something different.

### Caches

The workflow uses caches to make later builds faster.

**uv cache**

setup-uv keeps the downloaded package cache, keyed by `uv.lock` and `pyproject.toml`.

Only the tests job saves this cache. The `.venv` folder is never cached. Each job creates a fresh environment using `uv sync --locked`.

**Nuitka cache**

The build keeps Nuitka's cache, including its MSVC object cache, UPX-compressed files, and downloads.

The cache is stored per branch and per `uv.lock`.

The first build after a lock change compiles everything and takes about 18 minutes. Later builds only compile what changed.

Cache entries are identified by the content being compiled, so stale entries cause a cache miss rather than an incorrect build.

To start fresh, delete the `nuitka-...` entries under:

```text
GitHub Actions -> Caches
```

### Build artifacts

Artifacts are available under:

```text
GitHub Actions -> Select a workflow run -> Artifacts
```

They are zip files containing the application archive, its checksum, and `build-info.json`.

**Development builds (`dev`)**

- Artifact name: `UncleFarmer-dev-<short sha>`
- Retention: 14 days

**Production builds (`master`)**

- Artifact name: `UncleFarmer-master-<version>-<short sha>`
- Retention: 90 days

Each artifact contains:

- The application zip with the same name.
- Its `.zip.sha256` checksum file.
- `build-info.json`.

The build information records the application, version, build type, branch, full and short commit, UTC time, run ID and number, Python, Java and UPX versions, runner, package size, and SHA-256.

The version comes from `unclefarmer/__init__.py`. CI never changes it.

To check a downloaded archive, run:

```powershell
certutil -hashfile UncleFarmer-dev-1a2b3c4.zip SHA256
type UncleFarmer-dev-1a2b3c4.zip.sha256
```

Replace the example filename with the one you downloaded.

The two hashes must match.

A `dev` artifact is a development build. Do not distribute it as a stable release.

### The Dev release channel

After a `dev` build passes, the `publish-dev` job publishes it for installed copies whose Settings say:

```text
Release channel: Dev
```

The job runs on Ubuntu and takes a few seconds.

It uses `ci_build.py publish-dev` to upload the build's zip as a GitHub pre-release.

A tag looks like:

```text
v0.9.0-dev.57
```

The asset name looks like:

```text
UncleFarmer-0.9.0-dev.57.zip
```

The zip also has a `.sha256` file.

Dev pre-releases are never marked as the latest release.

The run number only increases. The version remains the last released version from `unclefarmer/__init__.py`. For example, `0.9.0-dev.57` is a build made after `0.9.0` and before `0.10.0`. The updater orders releases accordingly.

The updater keeps the two channels separate:

- The Master channel reads GitHub's latest release, which is never a pre-release. It also refuses tags or assets containing `-dev.`.
- The Dev channel reads the release list and accepts only pre-releases whose tag, version, and asset all follow the Dev naming format.

Every build, whether from CI or `release.py`, has a `build.json` beside the executable. It contains the version, channel, and commit. An installed Dev build uses this information to identify its own build number. An update replaces this file as well.

The publishing job reads the uploaded artifact back and checks the published zip against GitHub's checksum.

If the same run is published again, it replaces the pre-release with the same tag.

Only the newest ten Dev pre-releases are kept. Older ones and their tags are deleted.

The job only replaces or deletes a release if it is both a pre-release and has a Dev tag. Stable releases are never touched.

A newer push to `dev` cancels a run that is still in progress, including the publishing job. If this leaves a pre-release without its zip, the updater skips it and it is removed later as an old Dev build.

Master builds work differently: CI builds and uploads them as artifacts but never publishes them as stable releases.

### Running the CI checks on your PC

You can run the same checks locally.

Start by syncing the locked dependencies:

```powershell
uv sync --locked
```

Run the main checks:

```powershell
uv run python check.py
```

This runs the static and test checks together, plus the Java tests if you have a JDK installed.

To build and verify the Java agent:

```powershell
uv run python agent/build_agent.py dist\agent-check.jar
uv run python ci_build.py verify-agent dist\agent-check.jar
uv run python agent/java_tests.py
```

To run the complete development build job:

```powershell
uv run python ci_build.py build --type development
```

The complete build takes about half an hour.

### Secrets and permissions

The CI workflow does not use repository secrets.

Every job except `publish-dev` gets read-only access to the repository.

The `publish-dev` job gets the token belonging to its own workflow run, with:

- `contents: write` for releases and tags.
- `actions: read` for accessing the uploaded artifact.

It only runs for `dev` itself, never for a pull request, and installs nothing.

Pull requests from forks use a read-only token. Nothing is signed.

### Branch protection

The branch protection rules are stored in:

```text
.github/rulesets/
```

To apply them, the GitHub CLI must be logged in with administrator permissions for the repository.

Replace `OWNER/REPO` with the repository owner and name, then run:

```powershell
gh api -X POST repos/OWNER/REPO/rulesets --input .github/rulesets/master.json
gh api -X POST repos/OWNER/REPO/rulesets --input .github/rulesets/dev.json
```

You can also configure the rules manually in:

```text
GitHub -> Settings -> Rules -> Rulesets
```

Create one ruleset for `master` and another for `dev`.

Use these settings for both branches:

- Target the appropriate branch.
- Set enforcement to **Active**.
- Leave the bypass list empty, including for administrators.
- Enable **Restrict deletions**.
- Enable **Block force pushes**.
- Require a pull request before merging.
- Require the following status checks:

  - `Static checks (ruff, pyright)`
  - `Tests (Windows, Python 3.14)`
  - `Dependency audit (pip-audit)`
  - `Java agent (build, Java 8 check, tests)`
  - `Workflow lint (actionlint)`

  These checks appear in the list after the workflow has run for the first time.

For `master`, also:

- Dismiss stale approvals.
- Require conversation resolution.
- Require branches to be up to date before merging.

Required approvals can be set to zero while one person maintains the project. Set this to one once other collaborators are available to review changes.

The build job is intentionally not a required check because it does not run for pull requests.

### Dependabot

Dependabot is configured in:

```text
.github/dependabot.yml
```

It opens pull requests into `dev` every Monday.

It creates:

- One pull request for minor and patch updates to locked Python packages (`pyproject.toml` and `uv.lock`).
- One pull request for updates to workflow actions, which remain pinned to commit SHAs.
- Separate pull requests for major updates.

It allows up to five open pull requests per ecosystem.

These pull requests go through the same checks as other changes. They are never merged automatically.

For a Python dependency update, run this on the Dependabot branch:

```powershell
uv run python export_requirements.py
```

Then commit and push the updated requirements files. The tests fail until these files match `uv.lock`.

## Releasing

Stable releases are made manually from `master`.

CI builds every push to `master`, producing the production artifact described above. However, CI does not publish stable releases, sign anything, or change the version.

The Dev pre-releases are the only releases published automatically by CI.

### Preparing a release

When `dev` contains the changes intended for release:

1. Merge `dev` into `master` through a pull request.
2. On `master`, add the new version's changes to the top of `CHANGELOG.md`.
3. Commit and push the changelog.
4. Run the release command.

For example:

```powershell
uv run python release.py 0.5.1 --notes-file notes.md
```

Replace `0.5.1` with the version being released.

The notes file should contain the new changelog section.

The release script:

- Runs `check.py`.
- Sets the version.
- Builds the application folder.
- Creates `dist/UncleFarmer-0.5.1.zip`.
- Creates the GitHub release `v0.5.1` with the application zip.

It uses the GitHub CLI (`gh`), which must already be installed and logged in.

Every installed copy can find the release through its updater. The release notes are also what the application displays under "What's new".

Commit the version change afterwards.

### Other release options

Create a draft release:

```powershell
uv run python release.py 0.5.1 --notes-file notes.md --draft
```

A draft release is not visible to installed copies until you publish it.

Build locally without creating a GitHub release:

```powershell
uv run python release.py 0.5.1 --notes-file notes.md --local
```

The local build includes the notes as `What's new.txt` inside the zip and as `UncleFarmer-0.5.1.txt` beside it.

## Translating

Translations help make Uncle Farmer accessible to more players.

You can contribute a translation even if you don't work on the code.

### 1. Create a translation file

Start with the translation template:

```text
lang/unclefarmer.pot
```

Copy it and name the new file using the language code:

```text
lang/<language code>.po
```

For example, use the appropriate language code for the language you're translating.

### 2. Update the header

In the new `.po` file, set:

- `Language` to the language code.
- `X-Language-Name` to the language's own name.

The language name is what appears in the application's Settings.

### 3. Translate the strings

Fill in the `msgstr` lines with your translations.

Keep these rules in mind:

- An empty `msgstr` means the application continues to display the English text.
- Keep every `{placeholder}` exactly as it appears in the original string.
- Do not translate or remove placeholders.

### 4. Refresh the translation files

When new strings are added to the application, refresh the template and translation files with:

```powershell
uv run python -m unclefarmer --strings
```

This command:

- Refreshes the translation template.
- Merges the template into your translation file.
- Keeps translations for removed strings as `#~` lines.
- Compiles `lang/<code>/LC_MESSAGES/unclefarmer.mo`.

The application loads the compiled translation using gettext.

wx also loads the same translation catalog, along with its own `wxstd` catalog for standard buttons and menus.

## Licence

By contributing, you agree that your work is shared under the project's licence, the GNU GPL v3.0.

Thanks again for helping make Uncle Farmer better!