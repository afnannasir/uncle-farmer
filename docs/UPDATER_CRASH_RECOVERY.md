# Windows/NTFS updater crash recovery

This extends the updater hardening work with persistent transaction recovery.
It targets Windows on NTFS. Process-termination tests are not physical power-loss
tests, and no storage stack can be promised to survive hardware that ignores
flush requests or loses/corrupts every recovery copy.

## Startup and packaging

`bootstrap.exe` is a small native Windows updater/bootstrapper built from
`unclefarmer/native/bootstrap.c`. The Nuitka application is `UncleFarmer.exe`,
beside its normal flat runtime DLLs and data, and is what players start. The
bootstrap imports Windows system DLLs only (kernel32, shell32, msvcrt) and never
loads the application's Python runtime. Both executables are required by the
build/release verifier.

The bootstrap has three modes:

- `bootstrap.exe --update <copier> <pid> <version> <token>`: how the app starts an
  update. The bootstrap checks that `<copier>` is `UncleFarmer.exe` inside this
  installation's `update/new` folder, runs it as
  `--apply-update <app> <pid> <version> <token> --bootstrap` inside a
  kill-on-close job (the app's cancel ends the bootstrap and with it the paused
  copier) and waits for it. The copier waits for the old app `<pid>` to exit and
  swaps the files transactionally, as below. Exit code 0 means the app may start
  again (the update committed, or failed and rolled back cleanly: the restarted
  app shows the result); `updater.RESTART_WITHHELD` means it must not (the old
  app still runs, or a rollback was incomplete). On 0 the bootstrap runs recovery
  if a journal or the armed marker exists, starts `UncleFarmer.exe` and exits.
- `bootstrap.exe --relaunch <pid>`: waits up to two minutes for that process to
  end, then recovers if needed, starts `UncleFarmer.exe` and exits. A copier that
  ran without the bootstrap (see migration) restarts the app this way, and so
  does an app that finds an unfinished update at startup.
- `bootstrap.exe [args]`: recovers if needed, then starts `UncleFarmer.exe [args]`.

The application's own startup check: when the journal is unfinished (or no
journal copy can be read), `UncleFarmer.exe` starts `bootstrap.exe --relaunch`
with its own PID and exits before it takes any lock or loads the GUI. After a
recovery the bootstrap passes `--recovered`; an app started that way that still
finds an unfinished update shows an error naming `bootstrap.exe` instead of
handing it back again, so the two can never loop. If an interruption leaves
`UncleFarmer.exe` itself unable to load, starting `bootstrap.exe` by hand runs
the same recovery.

Before the first download or application mutation, the updater prepares and
verifies an independent runtime under `.recovery/runtime`. It copies a complete
release, flushes its files, records its fingerprint, and publishes it on the same
volume. A cache preparation has one deadline, a free-space check and an exclusive
Windows handle. Abandoned private cache preparations can be discarded safely
under that lock. The published runtime is not updated in place.

The normal UI prepares this rescue copy from the currently running installation,
which is known to boot, before trying the staged copier. A failed staged loader
therefore cannot leave the only rescue copy dependent on that failed loader.
The direct copier entry can initialize a missing rescue cache from its own
already-running staged release before it modifies any installed entries.
One full rescue runtime remains on disk after successful updates.

The bootstrap checks the persistent armed marker and journal copies before it
starts the application. It invokes the isolated runtime with `--recover-update`
and the installation path, waits for a successful recovery exit, then starts
`UncleFarmer.exe`. The recovery helper's working directory is the rescue directory,
not the installation being repaired. A failed helper prevents normal launch.
The recovery entry dispatch runs before updater, GUI, settings or normal paths
imports; its implementation uses the Python standard library in the independent
runtime. A normal `UncleFarmer.exe` start hands an unfinished transaction to the bootstrap.

`bootstrap.exe` and `.recovery` are protected from ordinary replacement and
cleanup. The replaceable application executable is installed immediately before
`build.json`. Existing NEVER and KEEP_IN protections remain in effect.
New installations need a build containing both executables. An older updater
cannot retroactively gain crash protection during the initial migration to this
packaging. Bootstrap protocol changes need a deliberate compatible migration
(a new `BOOTSTRAP_MARKER`); ordinary updates do not replace a current bootstrap or
the rescue runtime in place.

The one migration that does happen: a copy from before `bootstrap.exe` (1.1.0
and older) has no bootstrap. Its old updater starts the new release's
`UncleFarmer.exe` copier directly; the transaction replaces `UncleFarmer.exe` like
any other application entry, and once it is `COMMITTED` the copier copies the
release's `bootstrap.exe` into place with one same-volume rename, only when the
installed file is missing or lacks `updater.BOOTSTRAP_MARKER`, which `bootstrap.c`
embeds. A failure there is logged and costs only the restart path: the copier
then starts `UncleFarmer.exe` directly. A new app whose folder has no current
bootstrap also runs the copier directly.

Dev builds of 2026-11-14 used an earlier layout: the native front door was
`UncleFarmer.exe` and the app `UncleFarmerApp.exe`. Their updaters require
`UncleFarmerApp.exe` in a release, so they refuse this one without changing
anything; players unzip it over the folder once. After that, the next update
removes the leftover `UncleFarmerApp.exe` (`updater.LEGACY_APP`), renews a rescue
runtime whose `UncleFarmer.exe` is the old front door (only while no started
replacement needs it), and still reads journals written under the old protected
list (`update_recovery.LEGACY_PROTECTED`).

`build_exe.py` compiles `bootstrap.exe` before starting the expensive Nuitka
build, using the repository's existing GCC/MinGW or Zig C compiler discovery.
A missing compatible C toolchain is a build error. A source checkout does not
perform application self-updates; it continues to use git.

## Persistent transaction

The journal lives at `.recovery/transaction.json`, outside `update/` cleanup.
Each transaction has a random ID and a private same-volume directory containing
`new/`, `old/`, `retired/`, restoration/discard entries and an immutable `plan.json`.

The journal records its format, ID, installation, previous and target versions,
state, preparation and backup paths, replacement entries, old/new SHA-256
inventories, backed-up/replaced/waiting lists, protected names, process owner and
Windows creation timestamp, last operation, rollback requirement and commit flag.
Inventories include directory markers, so empty directories and entry type changes
are distinguishable. A PID alone is insufficient after reboot or PID reuse.

States include DOWNLOADING, EXTRACTING, STAGING, STAGED, BACKING_UP, APPLYING,
VERIFYING, COMMITTING, COMMITTED, ROLLING_BACK, ROLLED_BACK and RECOVERY_REQUIRED.
Journal changes use a flushed temporary file, `os.replace`, and a flush of the
published file. A checksummed previous copy is retained. Both journal copies are
written to a terminal state before backup cleanup begins. The immutable plan
can reconstruct the transaction if both mutable journals are damaged or missing.
Ambiguous or wholly invalid records stop recovery without destroying evidence.

Paths in a journal do not grant write permission. Recovery derives paths from
the supplied installation and validated transaction ID, checks recorded paths
against those derived paths, rejects protected/duplicate/unsafe entry names and
unexpected reparse points, and compares actual contents with recorded hashes.
The checksum detects corruption; it is not a digital signature or a substitute
for protecting the installation from an attacker with write access.

## Backup, installation and repeatable recovery

For each managed entry, the updater durably records intent, copies the original
to the backup, verifies and flushes that copy, then records backup completion.
Only then does it move the installed original aside and move the prepared entry
into place. Same-volume Windows `MoveFileExW` requests write-through behavior;
cross-volume moves are refused. Sharing/access errors receive bounded retries
under an overall operation deadline. No locked original is deleted as a workaround.

Preparation space includes the staged tree, preserved data, backup copies and
room for a restoration copy. Before commit, runtime requirements, target metadata
and every planned installed entry are verified. Installed files are flushed.
Only then can COMMITTED be persisted and backups removed.

After an interrupted download/extraction/staging operation that touched no
installed entries, recovery discards checked incomplete `.part` and `update/new`
artifacts and retains the installed application. For an interrupted replacement,
it first checks all recovery sources against actual contents. A verified retired
original can substitute for a missing/damaged backup copy. It restores old
entries in reverse order, copying from verified backups into fresh private paths
and verifying/flushing them before publication. It never consumes or overwrites
the only verified backup. A second crash during restoration can be recovered by
repeating these checks.

If all managed target entries and target metadata already match, recovery can
commit the complete new installation. A transaction already rolling back always
continues rollback. Unexpected/torn installed contents are moved into retained
directories outside transaction cleanup before restoring a verified original;
external edits are therefore preserved for inspection, not deleted as temporary
files. Missing all verified old sources for a missing/changed original, unsafe
paths, or unusable recovery records stop normal launch and preserve available
files. Recovery does not guess from the
presence of an executable.

Successful recovery writes the existing `update/result.json` format for the UI.
Recovery failures and deferred cleanup are logged to `.recovery/recovery.log`;
native helper startup failures record their Windows/process code in a uniquely
created `.recovery/bootstrap-error-*.txt` file without launching the payload;
unwritable logging also reports to stderr. A locked obsolete backup after a verified
commit/rollback defers cleanup without preventing the verified application from
starting. A later transaction cannot abandon that older backup by overwriting
its journal before cleanup succeeds.

## Windows durability boundaries

The implementation checks the real target filesystem using Win32 volume APIs.
File data is flushed with `os.fsync` (the Windows CRT commit operation), staged and
backup contents are verified, and entry moves request `MOVEFILE_WRITE_THROUGH`.
An exclusive non-shared Windows file handle prevents concurrent recovery/apply;
Windows releases it when a process is terminated. The original application must
still exit before application entries are changed.

Windows does not provide a generally guaranteed unprivileged Python-style
directory fsync contract. The implementation does not claim one. NTFS metadata
journaling also does not make the application transaction atomic. Persistent
plans and reconciliation address interrupted operations, subject to the OS,
filter drivers, controller and device honoring durability requests. Volume-wide
administrator flushes, TxF and delayed reboot replacements are not required.
See Microsoft's [MoveFileExW contract](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
and [file caching documentation](https://learn.microsoft.com/en-us/windows/win32/fileio/file-caching).

Path checks are repeated, but are not a handle-relative security system against a
separate malicious writer racing every filesystem operation. Unsupported long-path
or access/readonly restrictions fail safely; no broader long-path policy or ACL
changes are made by this feature.

Read-only files are refused, on purpose, before anything is copied. Every copy an
update makes (the rescue runtime, the prepared release, the verified backups,
including the preserved `data/game_texts.json` and `agent/run`) is flushed with
`FlushFileBuffers`, which Windows allows only through a handle that may write, so a
read-only file among the entries an update copies could only be made durable by
changing the player's attribute, which the updater never does. The installation step
checks only the entries it backs up and replaces, so a read-only file it never touches
(the player's own note beside the exe) does not stop it; the one-time rescue runtime
copies everything but the player's folders, so its preparation names such a file too.
Both the app (before it closes)
and the copier (before the journal begins) name such files and stop with nothing
changed; the player makes them writable and updates again. Read-only copies left in
`.recovery` by an older version's failed attempt are made writable and removed
when that finished transaction is cleaned up, so they never block a later update.

Permanent disk failure, loss of the rescue runtime
and all valid journals/plans, or permanently blocked restoration may require manual
recovery from retained files. Never remove `.recovery` to bypass a failed recovery.

## Verification scenarios

`tests/test_update_recovery.py` uses real Windows/NTFS temporary installations.
It launches the real Python interpreter, reaches a controlled boundary, then uses
Windows process termination so Python cleanup handlers cannot run. A separate
fresh process performs recovery and checks complete old/new bytes and protected
user files. Recovery is also repeated after a second interruption.

Covered boundaries include:

1. Download with an unfinished partial file.
2. Partial ZIP extraction.
3. Partial staging.
4. Initial durable journal publication.
5. Backup copy before completion is recorded.
6. Original retirement and intent before replacement.
7. Replacement before the journal completion record.
8. Payload executable replacement.
9. Intent immediately before replacing build metadata.
10. Directory replacement and both file/directory type changes.
11. Backup cleanup, including interruption after a cleanup deletion.
12. Rollback preparation, and termination/restart during recovery itself.
13. All files installed and verified before commit.
14. Immediately after commit.

Additional tests cover one/two/all corrupted journal/plan copies, missing journals,
missing backups, invalid protected-path instructions, junctions, live owners/PID
reuse, same-volume moves, actual Windows sharing violations, locked terminal
backup cleanup, bootstrap ordering and failure refusal with a broken application
Python DLL, and recovery entry dispatch/source rejection. Existing updater tests
cover insufficient space, shared deadlines, malformed releases and preservation.

Native bootstrap tests compile and execute the real C launcher with small native
helper/payload fixtures. Filesystem recovery tests execute the real Python recovery
implementation. This does not substitute for building the complete new Nuitka
release and testing its loader/runtime on a disposable installation.

Verification results:

- Real Windows/NTFS interruption tests execute on the test volume, checked with
  Win32 filesystem APIs. Forty-four recovery/native/path cases are now present.
- Focused updater/recovery/build/channel/lifecycle group: **329 passed**.
  This includes the latest retired-original fallback, torn-file retention and
  known-working rescue-cache corrections.
- Supplemental final native bootstrap/path checks: **5 passed, 39 deselected**.
  These recompiled the final C launcher and checked that an uninspectable journal
  directory cannot be treated as a missing directory and written through.
- Full-suite run: **4227 passed, 10 skipped, 1 failed**. The failure was
  `test_schedule.py::test_the_history_keeps_its_newest_entries_only`, with Windows
  access denied on an atomic temporary `schedules.json` rename in unchanged
  scheduling/JSON I/O code. It also failed before this crash-recovery task. The
  full suite is not clean. The final focused/supplemental checks followed the
  last recovery-source and path safeguards; the complete suite was not rerun
  after those final focused changes.
- Ruff passed for the application, tests, build and release scripts. Final strict
  Pyright: **0 errors, 0 warnings**, recorded in `logs/crash-types-last.txt`.
- PE import inspection of the actual compiled native launcher found only
  `KERNEL32.dll` and `msvcrt.dll`; no application/Python DLL imports.
  Evidence: `logs/crash-native-imports.txt`. (Since `bootstrap.exe` parses its
  arguments with `CommandLineToArgvW` it also imports `SHELL32.dll`, a system DLL.)
- `git diff --check` passed.

Evidence is retained in `logs/crash-release-check.txt`,
`logs/crash-native-last.txt`, `logs/crash-full.txt` and
`logs/crash-types-last.txt` (local verification artifacts). No live installation
update was performed. The main Nuitka application was not rebuilt; native test executables
were compiled and executed. Complete two-executable Nuitka deployment and VM or
physical power-loss testing remain integration/release checks.

## Manual VM power-interruption procedure

1. Use a disposable Windows VM with NTFS and a restorable VM snapshot. Prepare
   two complete builds using this launcher/recovery protocol with distinct build
   versions. Copy test profiles, settings, learned data and agent/run state, and
   record hashes. Keep an external copy of the baseline. Close the test application.
2. In that VM, use a source diagnostic worker to call
   `updater.ensure_recovery_runtime(APP, RELEASE)` then `updater.apply(APP, RELEASE, print)`.
   Replace `update_recovery.checkpoint` only in that disposable diagnostic worker
   with a callback that prints a selected boundary and waits. Use the boundary
   names in the automated tests; no production environment-variable backdoor exists.
3. At each selected boundary, use the hypervisor's hard power-off/reset operation
   without a guest shutdown. Do not do this on the user's working laptop or live
   installation. Restore the baseline VM snapshot before each independent case.
4. Boot the VM and launch its actual `UncleFarmer.exe` (it hands an unfinished update
   to `bootstrap.exe`), or `bootstrap.exe` when the reset left `UncleFarmer.exe`
   unable to load, not a source interpreter. Confirm that native recovery runs before normal startup,
   examine the journal/result/log, compare the entire managed tree with one known
   version, and verify user data. Repeat a hard reset during recovery, then launch again.
5. Include locked-file/antivirus cases, low-space cases and a reset during cleanup.
   Record Windows build, NTFS volume and VM disk/cache settings. Guest process kills,
   VM hard resets and physical storage power removal are different tests; report them
   separately. Actual hardware-cache power-loss testing requires a disposable machine
   and storage setup and has not been performed by these automated tests.

## Changed files

- `unclefarmer/update_recovery.py`: standard-library journal, validation, Win32
  durability/locking, manifests, reconciliation, repeatable rollback and cleanup.
- `unclefarmer/native/bootstrap.c`: `bootstrap.exe`, the independent stable updater/bootstrapper and pre-runtime recovery.
- `unclefarmer/updater.py`: snapshot preparation, early states, durable apply journal,
  verified backups, preserved bootstrap, free-space accounting and cleanup protection.
- `unclefarmer/__main__.py`: minimal rescue dispatch and unfinished-update startup guard.
- `build_exe.py`, `release.py`: two-executable packaging and bootstrap build/protection.
- `tests/test_update_recovery.py`: termination, NTFS, corruption and native-bootstrap tests.
- `tests/test_updater.py`, `tests/test_updater_hardening.py`: updated payload/backup
  contracts and integrated download-to-restart regression coverage.

The GUI/lifecycle integration is described in [the base hardening report](UPDATER_HARDENING.md).
No live installation, game process or working character data is changed by these tests.
