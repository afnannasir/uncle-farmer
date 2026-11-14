# Updater hardening report

This records the original 17-issue hardening task and its verification results.
The later persistent journal and independent bootstrap implementation is described
in [Windows/NTFS crash recovery](UPDATER_CRASH_RECOVERY.md), which supersedes this
report's original lack-of-crash-recovery limitation.

## Scope and shutdown decision

The updater was inspected against the flat Nuitka output used by `build_exe.py`.
The existing `dist/Uncle Farmer` layout also passed the shared runtime verifier.
No real installation was updated, and no new executable was compiled for this task.

Requirements 3 and 12 conflicted over whether the copier could start before the
original application closed. The user chose a **paused copier with a readiness
check**: start and verify the helper first, cancel it if closing is vetoed, and
authorize copying only after the window is destroyed and application locks are
released. The helper independently checks that the old process has exited.

## Issues, causes, fixes and regression coverage

All 17 issues have implementations and regression coverage. The guarantees are
subject to the recovery limitations below.

| # | Status and original cause | Implementation | Representative regression coverage |
|---|---|---|---|
| 1 | Fixed: direct copies could leave mixed versions. | Prepare and validate a complete tree beside the application; move old entries to backup, journal swaps, undo in reverse on failure; executable and metadata last. | `test_flat_update_failure_restores_every_old_byte` (fourth copy and fourth swap), `test_backup_exists_until_both_commit_files_are_installed`, `test_failed_restore_keeps_old_runtime_backup` |
| 2 | Fixed with safe-path limitation: argument parsing, reporting and restarting could escape error handling. | Enclose helper work in exception handling; independently report and recover; derive the writable application from the helper's staged location. | `test_invalid_helper_arguments_report_failure_and_attempt_recovery`, result-write, directory-creation and restart-failure tests |
| 3 | Fixed using the user-approved paused flow: a vetoed close could leave an active copier. | Verify the paused helper before closing; cancel on veto; authorize only after destruction and lock release. | `test_paused_copier_never_activates_on_close_veto`, lifecycle tests for live bots, dead helpers and unsuccessful destruction |
| 4 | Fixed: staging validation could occur after writes. | Validate staging before download, unpack and claims; recheck containment and links before subsequent writes. Reject unsafe ZIP entries. | `test_staging_junction_rejected_before_any_write` for all three operations; existing ZIP safety tests |
| 5 | Fixed: unexpected worker and HTTP exceptions could strand the progress dialog and partial files. | Catch/log worker exceptions, deliver completion, release claims, close progress and reset state; validate lengths and clean partial downloads. | Invalid-length, interrupted-download, worker-exception and progress-reset tests |
| 6 | Fixed: a source interpreter or arbitrary destination could be treated as an application. | Reject source execution; require a staged frozen release and valid existing application; reject a destination different from the staged helper's application. | `test_source_execution_never_copies_python_environment`, `test_helper_rejects_other_destination_without_writing_there` |
| 7 | Fixed: wait timeouts were not proof of process exit. | Check Windows wait results and independently check liveness immediately before applying. | Process-wait timeout/status tests; `test_helper_checks_old_process_again_before_apply` |
| 8 | Fixed: broadly matched ZIP names could disagree with the release tag. | Require the exact versioned filename and explicit uploaded state; validate extracted build version. | `test_stable_asset_must_match_tag_and_be_uploaded`, `test_staged_build_metadata_must_match_offer` |
| 9 | Fixed with legacy inventory limitation: `_internal` did not describe the flat Nuitka runtime. | Replace incoming runtime folders completely; remove known obsolete shipped entries transactionally; record relative shipped-file inventories in new build metadata; preserve protected and locally learned files. | Flat-success, full-byte rollback and `test_data_preserves_unshipped_learning_but_removes_known_obsolete_data` |
| 10 | Fixed: per-file retries had no shared budget or space preflight. | Check preparation space plus preserved data and a margin; propagate one monotonic deadline through preparation and swaps; rollback after timeout. | Low-space, locked-copy, multiple-locked-files and late-replacement deadline tests |
| 11 | Fixed: valid non-object JSON could crash startup. | Validate the result object and required field types; return the unreadable-result failure for malformed data. | `test_result_json_shapes_never_crash_startup` and existing missing/invalid result tests |
| 12 | Fixed: `Popen` success did not prove helper readiness. | Keep the process handle; require a matching PID and random-token busy signal within five seconds; cancel on failure without closing the original app. | `test_copier_startup_handshake` (ready, early exit, missing/wrong signal and launch error), built-copy startup-failure test |
| 13 | Fixed: copy-over behavior assumed matching entry types. | Move the old entry aside before installing the new entry, allowing both file/folder transitions and reverse restoration. | `test_file_folder_type_change`, `test_type_change_is_restored_after_a_later_failure` |
| 14 | Fixed: a game could open during download. | Recheck games before the paused helper starts and reuse the existing close/consent flow. | `test_game_started_during_download_blocks_application` and existing game-close tests |
| 15 | Fixed: extraction and helper startup had an unclaimed gap. | Hold the parent claim through queued GUI completion and helper readiness; preserve the helper's claim on handoff; revalidate cleanup after waits. | Worker claim tests, `test_claim_survives_successful_unpack_until_completion_callback`, `test_cleanup_rechecks_staging_after_waiting` |
| 16 | Fixed: a NULL Windows process handle was mistaken for an exited process. | Treat access denied and unknown API failures as potentially alive; log errors; only a nonexistent/exited process releases ownership. | `test_process_open_failure_is_not_proof_of_exit` (access denied, invalid PID and unexpected error), existing busy/cleanup tests |
| 17 | Fixed: preservation and replacement comparisons differed in casing behavior. | Case-fold rule comparisons and inventory keys while retaining installed path spelling. | `test_case_insensitive_protected_nested_directories`, `test_mixed_case_never_entries_are_not_installed`, flat runtime replacement tests |

Additional review reproduced and fixed two integrity gaps: preserving unshipped
learned files under `data/`, and rejecting a directory masquerading as a required
DLL. Runtime file/type checks are shared with the build verifier.

## Changed files and responsibilities

- `unclefarmer/updater.py`: release selection, staging validation, claims and
  cleanup, download/extraction, runtime validation, file inventory, transactional
  application/rollback, process checks, helper handshake, authorization,
  recovery and result validation.
- `unclefarmer/gui/mainparts/updates.py`: worker completion and progress cleanup,
  continuous claim ownership, final game check and paused-helper shutdown flow.
- `unclefarmer/gui/mainparts/base.py` and `unclefarmer/gui/main.py`: pending-helper
  state and accurate close/destruction checks.
- `unclefarmer/__main__.py`: authorize the pending helper after instance/data
  locks are released.
- `build_exe.py`: share the required flat-runtime verification contract.
- `tests/test_updater_hardening.py`: new fault-injection and safety regressions.
- `tests/test_updater.py`, `tests/test_built_copy.py`, `tests/test_ci_build.py`,
  `tests/test_update_channels.py`, `tests/test_lifecycle_logs.py`: existing
  coverage adapted and extended for real release structure and shutdown rules.

Existing tests now supply uploaded asset states, complete flat-runtime fixtures,
and staged frozen-helper identities instead of permissive simplified folders.
Whole-flow tests explicitly authorize the helper and simulate the old process
exiting. Metadata expectations include the shipped-file inventory. Token tests
also verify that authentication is restricted to HTTPS GitHub API requests.
These are contract changes required by the safety checks, not removed coverage.

## Verification

- Initial updater/build/CI baseline: **140 passed**, or 192 tests when
  update-channel tests were included.
- Red runs reproduced the staging, transaction, worker, process, data retention
  and runtime-type failures before their corresponding fixes.
- Final focused group (updater, new regressions, built-copy, channels, CI,
  lifecycle and scheduling): **338 passed**.
- Final parallel full suite: **4188 passed, 10 skipped, 1 failed**. The failure
  was `test_schedule.py::test_the_history_keeps_its_newest_entries_only`: Windows
  denied an atomic rename of its own temporary `schedules.json`. No scheduler,
  JSON I/O, file-lock or scheduling test source was changed.
- Serial full-suite rerun: **4187 passed, 10 skipped, 2 failed**. Both failures
  were Windows access-denied errors in the unchanged scheduling tests:
  `test_a_schedule_in_the_skipped_hour_runs_when_the_clock_comes_out_of_it[02:00]`
  and `test_the_history_keeps_its_newest_entries_only`. Therefore parallel test
  execution alone does not explain the problem. The full suite is **not clean**;
  the cause of these intermittent Windows rename denials remains unresolved.
- Isolated rerun of those two scheduling tests: **1 passed, 1 failed**. The
  history test also received Windows access denied when run without updater
  tests, so this failure does not require the updater test flow to reproduce.
- Ruff: **passed** for `unclefarmer`, `tests` and `build_exe.py`.
- Strict Pyright with the project interpreter: **0 errors, 0 warnings**.
  An initial sandboxed invocation could not resolve installed dependencies;
  the final run used the explicit virtual-environment Python with dependency
  access restored.
- Logs are retained under `logs/updater-*.txt` as local verification evidence.
- `git diff --check` passed.

The first full run had 4183 passes, 10 skips and two failures: a new test's type
cast evaluated a monkeypatched `Popen` as a generic, and an unrelated scheduling
test encountered Windows access denied on its temporary JSON rename. The cast
was corrected; the scheduling test passed in the final focused group without
changing scheduler code. The complete suite was rerun after the final changes.

## Recovery limits

This is a transaction with rollback on handled failures, not one indivisible
directory replacement. A power loss or forced helper termination between entry
swaps is outside the tested recovery guarantee; there is no persistent crash
recovery journal.

If Windows or storage permanently refuses both installation and restoration,
software cannot guarantee rollback. Old entries are retained in the named
sibling backup, failure is reported, and automatic launch of a mixed installation
is withheld. Manual recovery may be necessary. Result writing and restarting are
independent best-effort operations, not guarantees against an unwritable disk or
an executable Windows refuses to start.

Malformed helper calls report and recover only when a valid application can be
inferred from the helper's trusted `update/new` location. An arbitrary invocation
outside that location is logged; it cannot select a writable result/restart path.
If the original process is alive, the helper does not launch a duplicate.

Older releases lack full shipped-file inventories. Recognizable obsolete runtime
entries can be removed, but unknown legacy files are retained rather than risk
deleting user data. The new inventory enables precise cleanup on later updates.
Staging links are checked before filesystem operations; this does not claim
handle-based protection against a separate malicious process racing every path
check. Release verification checks layout, versions, sizes and the available
download digest; it is not a new package-signing system.

All executable launches, API failures, file locks, disk errors and recovery paths
in the regression suite use temporary fixtures or test doubles. The existing
Nuitka folder was inspected read-only. A real compiled, in-place update remains
an integration check for a controlled installation, not something run against
the user's working application during this task.
