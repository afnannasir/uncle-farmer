"""Windows/NTFS update recovery. Standard library only; usable from an isolated runtime.

Journal paths never authorize writes: all destinations are derived from the installation,
transaction ID and validated single-component entry names. Backups survive interrupted recovery.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from collections.abc import Callable, Generator
from typing import Any, cast

HERE = ".recovery"
JOURNAL = "transaction.json"
PAYLOAD = "UncleFarmerApp.exe"
PROTECTED = {"unclefarmer.exe", HERE, "profiles", "plans", "scripts", "logs", "messages", "results",
             "survey", "update", "settings.json", "schedules.json"}
KEEP = {"agent": "run", "data": "game_texts.json"}
TERMINAL = {"COMMITTED", "ROLLED_BACK"}
EARLY = {"DOWNLOADING", "EXTRACTING", "STAGING", "STAGED"}
STATES = EARLY | TERMINAL | {"BACKING_UP", "APPLYING", "VERIFYING", "COMMITTING", "ROLLING_BACK", "RECOVERY_REQUIRED"}


class RecoveryError(Exception):
    pass


def checkpoint(operation: str) -> None:
    """Operation boundary for process-termination integration tests (no production side effects)."""


def safe(path: str, root: str) -> None:
    root, path = os.path.abspath(root), os.path.abspath(path)
    if os.path.commonpath([root, path]) != root:
        raise RecoveryError("recovery path outside installation: " + path)
    current = path
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            info = None
        if info is not None:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise RecoveryError("reparse point in recovery path: " + current)
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise RecoveryError("hard-linked recovery file: " + current)
        if os.path.dirname(current) == current:
            break
        current = os.path.dirname(current)


def ntfs(app: str) -> None:
    if os.name != "nt":
        raise RecoveryError("durable updates require Windows and NTFS")
    kernel: Any = ctypes.windll.kernel32
    volume = ctypes.create_unicode_buffer(32768)
    filesystem = ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumePathNameW(app, volume, len(volume)) or not kernel.GetVolumeInformationW(
            volume.value, None, 0, None, None, None, filesystem, len(filesystem)):
        raise ctypes.WinError()
    if filesystem.value.upper() != "NTFS":
        raise RecoveryError("durable updates require NTFS, found " + filesystem.value)


def flush(path: str) -> None:
    """Flush file contents. NTFS directory flush is best effort; see recovery guide."""
    if os.path.isdir(path):
        return  # Windows does not promise an unprivileged directory fsync API.
    with open(path, "rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())  # Windows CRT _commit -> FlushFileBuffers.


def atomic(path: str, value: dict[str, Any], *, envelope: bool = True) -> None:
    safe(path, os.path.dirname(path))
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    data = (json.dumps({"sha256": hashlib.sha256(body).hexdigest(), "payload": value},
                       sort_keys=True, separators=(",", ":")).encode() if envelope else body)
    descriptor, temporary = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        flush(path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def fingerprint(path: str) -> dict[str, str] | None:
    safe(path, path)
    if not os.path.lexists(path):
        return None
    if not os.path.isdir(path):
        with open(path, "rb") as stream:
            return {"": hashlib.file_digest(stream, "sha256").hexdigest()}
    result: dict[str, str] = {"/": "directory"}
    for parent, dirs, files in os.walk(path):
        for name in dirs + files:
            safe(os.path.join(parent, name), path)
        for name in dirs:
            result[os.path.relpath(os.path.join(parent, name), path).replace(os.sep, "/") + "/"] = "directory"
        for name in files:
            file = os.path.join(parent, name)
            with open(file, "rb") as stream:
                result[os.path.relpath(file, path).replace(os.sep, "/")] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def durable_tree(path: str) -> None:
    safe(path, path)
    if os.path.isdir(path):
        for parent, _, files in os.walk(path):
            for name in files:
                file = os.path.join(parent, name)
                safe(file, path)
                flush(file)
    else:
        flush(path)


def durable_tree_for_entries(app: str, record: dict[str, Any]) -> None:
    for entry in record["entries"]:
        path = os.path.join(app, entry["name"])
        if os.path.exists(path):
            durable_tree(path)


def _name(value: object) -> bool:
    return (isinstance(value, str) and value not in ("", ".", "..") and not any(c in value for c in '/\\:')
            and not value.endswith((".", " ")) and value.casefold() not in PROTECTED)


def validate(data: object, app: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RecoveryError("invalid recovery journal object")
    record = cast(dict[str, Any], data)
    if (type(record.get("format")) is not int or record.get("format") != 1 or not isinstance(record.get("id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", record["id"]) is None or record.get("state") not in STATES
            or os.path.normcase(record.get("app", "")) != os.path.normcase(os.path.abspath(app))
            or not isinstance(record.get("entries"), list) or type(record.get("owner")) is not int
            or type(record.get("owner_stamp")) is not int
            or not isinstance(record.get("previous"), str) or not isinstance(record.get("target"), str)):
        raise RecoveryError("invalid recovery journal header")
    if not record["entries"] and record["state"] not in EARLY | {"ROLLED_BACK"}:
        raise RecoveryError("empty application replacement plan")
    if not 0 < record["owner"] <= 0xFFFFFFFF or record["owner_stamp"] <= 0:
        raise RecoveryError("invalid transaction process identity")
    if record.get("protected") != protected_names():
        raise RecoveryError("invalid journal preservation rules")
    expected = os.path.join(app, HERE, record["id"])
    if (not isinstance(record.get("backup"), str) or not isinstance(record.get("staging"), str)
            or os.path.normcase(record["backup"]) != os.path.normcase(os.path.join(expected, "old"))
            or os.path.normcase(record["staging"]) != os.path.normcase(os.path.join(expected, "new"))):
        raise RecoveryError("invalid recovery journal paths")
    record.update(app=os.path.abspath(app), backup=os.path.join(expected, "old"), staging=os.path.join(expected, "new"))
    names: set[str] = set()
    for raw in cast(list[Any], record["entries"]):
        if not isinstance(raw, dict):
            raise RecoveryError("invalid recovery journal entry")
        entry = cast(dict[str, Any], raw)
        if "old" not in entry or "new" not in entry or entry["old"] is None and entry["new"] is None:
            raise RecoveryError("incomplete recovery entry manifest")
        name = entry.get("name")
        if not isinstance(name, str) or not _name(name) or name.casefold() in names:
            raise RecoveryError("invalid recovery journal entry")
        names.add(name.casefold())
        for key in ("old", "new"):
            manifest = entry.get(key)
            if manifest is not None and (not isinstance(manifest, dict) or not manifest or any(
                    not isinstance(k, str) or not isinstance(v, str) or
                    v != "directory" and re.fullmatch(r"[0-9a-f]{64}", v) is None
                    for k, v in cast(dict[object, object], manifest).items())):
                raise RecoveryError("invalid recovery manifest")
        _verify_preserved_entry(entry)
    safe(expected, app)
    return record


def protected_names() -> list[str]:
    return sorted(PROTECTED | {folder + "/" + name for folder, name in KEEP.items()})


def _verify_preserved_entry(entry: dict[str, Any]) -> None:
    child = KEEP.get(str(entry["name"]).casefold())
    if child is None:
        return
    old = cast(dict[str, str], entry["old"] or {})
    new = {k.casefold(): v for k, v in cast(dict[str, str], entry["new"] or {}).items()}
    for path, digest in old.items():
        key = path.casefold()
        if (key == child or key.startswith(child + "/")) and new.get(key) != digest:
            raise RecoveryError("the update plan changes protected data: " + entry["name"] + "/" + path)


def load(app: str) -> dict[str, Any] | None:
    root = os.path.join(app, HERE)
    safe(root, app)
    found = False
    paths = [os.path.join(root, JOURNAL + suffix) for suffix in ("", ".prev")]
    plans = [os.path.join(root, n, "plan.json") for n in os.listdir(root)
             if re.fullmatch(r"[0-9a-f]{32}", n)] if os.path.isdir(root) else []
    # An immutable plan in the backup tree can recover two corrupt journal copies.
    # More than one candidate is ambiguous: never guess which transaction owns the installation.
    if len(plans) == 1:
        paths += plans
    for path in paths:
        safe(path, app)
        if not os.path.exists(path):
            continue
        found = True
        try:
            with open(path, encoding="utf-8") as stream:
                envelope = json.load(stream)
            body = json.dumps(envelope["payload"], sort_keys=True, separators=(",", ":")).encode()
            if envelope["sha256"] != hashlib.sha256(body).hexdigest():
                raise ValueError("journal checksum mismatch")
            return validate(envelope["payload"], app)
        except (OSError, ValueError, KeyError, TypeError, RecoveryError):
            continue
    if found or plans:
        raise RecoveryError("no valid recovery journal; preserve .recovery and stop")
    return None


def save(app: str, record: dict[str, Any], state: str, operation: str) -> None:
    record.update(state=state, last=operation, rollback_required=state not in TERMINAL, committed=state == "COMMITTED")
    validate(record, app)
    root = os.path.join(app, HERE)
    safe(root, app)
    os.makedirs(root, exist_ok=True)
    # Both copies describe this transaction. A torn latest update falls back to a
    # previous operation, then recovery reconciles it with actual file hashes.
    old = load(app)
    if old is not None and old["id"] == record["id"]:
        atomic(os.path.join(root, JOURNAL + ".prev"), old)
    atomic(os.path.join(root, JOURNAL), record)
    if old is None or old["id"] != record["id"] or state in TERMINAL:
        atomic(os.path.join(root, JOURNAL + ".prev"), record)
    checkpoint(operation)


@contextmanager
def locked(app: str) -> Generator[None]:
    """Exclusive Windows handle; a killed process releases it without cleanup code."""
    ntfs(app)
    root = os.path.join(app, HERE)
    safe(root, app)
    os.makedirs(root, exist_ok=True)
    lock = os.path.join(root, "lock")
    safe(lock, app)
    kernel: Any = ctypes.windll.kernel32
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(lock, 0xC0000000, 0, None, 4, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise RecoveryError("another updater or recovery process holds " + lock)
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def begin(app: str, previous: str, target: str, phase: str = "STAGING") -> dict[str, Any]:
    app = os.path.abspath(app)
    existing = load(app)
    if existing and existing["state"] in TERMINAL:
        cleanup(app, existing)  # Do not orphan an older backup/plan when starting another transaction.
    if existing and existing["state"] not in TERMINAL:
        if existing["state"] not in EARLY or existing["owner"] != os.getpid() and owner_alive(
                existing["owner"], existing["owner_stamp"]):
            raise RecoveryError("an unfinished update must be recovered first")
        if existing["target"] != "?" and target != "?" and existing["target"] != target:
            raise RecoveryError("transaction target does not match the offered release")
        if target != "?":
            existing["target"] = target
        existing["owner"] = os.getpid()
        existing["owner_stamp"] = current_stamp()
        save(app, existing, phase, "phase:" + phase)
        return existing
    transaction = uuid.uuid4().hex
    work = os.path.join(app, HERE, transaction)
    record: dict[str, Any] = dict(format=1, id=transaction, app=app, previous=previous, target=target, state=phase,
                                  staging=os.path.join(work, "new"), backup=os.path.join(work, "old"),
                                  entries=[], backed_up=[], replaced=[], waiting=[], protected=protected_names(),
                                  owner=os.getpid(), owner_stamp=current_stamp(), last="", rollback_required=True, committed=False)
    save(app, record, phase, "journal-created")
    return record


def plan(app: str, record: dict[str, Any], names: list[str]) -> None:
    record["entries"] = [{"name": n, "old": fingerprint(os.path.join(app, n)),
                           "new": fingerprint(os.path.join(record["staging"], n))} for n in names]
    record["waiting"] = names.copy()
    durable_tree(record["staging"])
    for n in names:
        old = os.path.join(app, n)
        if os.path.exists(old):
            durable_tree(old)
    atomic(os.path.join(os.path.dirname(record["backup"]), "plan.json"), record)
    save(app, record, "STAGED", "plan-durable")


def complete(app: str, record: dict[str, Any]) -> bool:
    try:
        with open(os.path.join(app, "build.json"), encoding="utf-8") as stream:
            build = json.load(stream)
        version_matches = isinstance(build, dict) and cast(dict[str, Any], build).get("version") == record["target"]
    except (OSError, ValueError):
        return False
    return version_matches and bool(record["entries"]) and all(
        fingerprint(os.path.join(app, e["name"])) == e["new"] for e in record["entries"])


def cleanup(app: str, record: dict[str, Any]) -> None:
    if record["state"] not in TERMINAL:
        raise RecoveryError("refusing cleanup of unfinished transaction")
    work = os.path.dirname(record["backup"])
    safe(work, app)
    if os.path.isdir(work):
        fingerprint(work)  # Reject nested reparse points before recursive cleanup.
        checkpoint("cleanup-started")
        _remove_private_tree(work, app)
        checkpoint("cleanup-finished")


def _remove_private_tree(path: str, app: str) -> None:
    safe(path, app)
    if os.path.isdir(path):
        for name in os.listdir(path):
            _remove_private_tree(os.path.join(path, name), app)
        os.rmdir(path)
    else:
        os.remove(path)
    checkpoint("cleanup-removed:" + os.path.relpath(path, app))


def rollback(app: str, record: dict[str, Any], rename: Callable[[str, str], None]) -> None:
    # Validate all necessary recovery sources before modifying any destination.
    for entry in record["entries"]:
        name, old = entry["name"], entry["old"]
        actual = fingerprint(os.path.join(app, name))
        if actual == old:
            continue
        if old is not None and recovery_source(record, str(name), old) is None:
            raise RecoveryError("cannot safely restore " + name + "; preserve backups")
    save(app, record, "ROLLING_BACK", "rollback-started")
    work = os.path.dirname(record["backup"])
    for entry in reversed(record["entries"]):
        name, old = entry["name"], entry["old"]
        destination = os.path.join(app, name)
        safe(destination, app)
        if fingerprint(destination) == old:
            continue
        if os.path.exists(destination):
            actual = fingerprint(destination)
            if actual != entry["new"]:
                # A genuinely torn replacement (or an external edit) must not
                # prevent restoring a verified old version, nor may it be deleted.
                retained = os.path.join(app, HERE, "retained-" + record["id"] + "-" + uuid.uuid4().hex)
                safe(retained, app)
                os.mkdir(retained)
                discard = os.path.join(retained, name)
                log_error(app, "Unexpected installed data retained at: " + discard)
            else:
                discard = os.path.join(work, "discard-" + uuid.uuid4().hex)
            rename(destination, discard)
            checkpoint("rollback-removed-new:" + name)
        if old is not None:
            # Copy, never consume the only verified backup. Interrupted partial
            # restoration lives at a fresh private path, not at the destination.
            restored = os.path.join(work, "restore-" + uuid.uuid4().hex)
            backup = recovery_source(record, str(name), old)
            if backup is None:
                raise RecoveryError("verified recovery source disappeared: " + name)
            safe(backup, app)
            if os.path.isdir(backup):
                shutil.copytree(backup, restored)
            else:
                shutil.copy2(backup, restored)
            durable_tree(restored)
            if fingerprint(restored) != old:
                raise RecoveryError("incomplete restored copy: " + name)
            checkpoint("rollback-prepared:" + name)
            rename(restored, destination)
            checkpoint("rollback-restored:" + name)
        save(app, record, "ROLLING_BACK", "restored:" + name)
    if any(fingerprint(os.path.join(app, e["name"])) != e["old"] for e in record["entries"]):
        raise RecoveryError("restored installation failed verification")
    save(app, record, "ROLLED_BACK", "rollback-verified")


def recovery_source(record: dict[str, Any], name: str, expected: dict[str, str]) -> str | None:
    work = os.path.dirname(str(record["backup"]))
    for directory in (str(record["backup"]), os.path.join(work, "retired")):
        candidate = os.path.join(directory, name)
        if fingerprint(candidate) == expected:
            return candidate
    return None


def move(source: str, target: str) -> None:
    """A same-volume NTFS move with a write-through request, never copy-and-delete."""
    if os.stat(os.path.dirname(source)).st_dev != os.stat(os.path.dirname(target)).st_dev:
        raise RecoveryError("cross-volume recovery move refused")
    kernel: Any = ctypes.windll.kernel32
    kernel.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    if not kernel.MoveFileExW(str(source), str(target), 8):  # MOVEFILE_WRITE_THROUGH; destination must not exist
        raise ctypes.WinError()


def rename(source: str, target: str, *, deadline: float | None = None) -> None:
    deadline = deadline if deadline is not None else time.monotonic() + 180
    while True:
        try:
            # Existing destinations are never deleted to circumvent Windows locks.
            move(source, target)
            if os.path.isfile(target):
                flush(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def recover(app: str) -> str:
    app = os.path.abspath(app)
    with locked(app):
        record = load(app)
        if record is None:
            return "no transaction"
        if record["state"] in TERMINAL:
            finish_recovery(app, record, report=False)
            return record["state"]
        # A live owner may still be downloading or applying. Never clean its files.
        deadline = time.monotonic() + 5
        while owner_alive(record["owner"], record["owner_stamp"]):
            if time.monotonic() >= deadline:
                raise RecoveryError("the transaction owner is still running")
            time.sleep(.05)
        if not record["entries"]:
            # The installed tree has not been touched. Only discard known early artifacts.
            staging = os.path.join(app, "update")
            safe(staging, app)
            if os.path.isdir(staging):
                for name in os.listdir(staging):
                    if name == "new" or name.endswith(".part"):
                        path = os.path.join(staging, name)
                        safe(path, app)
                        fingerprint(path)  # reject nested reparse points before recursive removal
                        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            save(app, record, "ROLLED_BACK", "incomplete-staging-discarded")
        elif record["state"] not in {"ROLLING_BACK", "RECOVERY_REQUIRED"} and complete(app, record):
            save(app, record, "COMMITTED", "recovered-complete-installation")
        else:
            deadline = time.monotonic() + 180
            rollback(app, record, lambda a, b: rename(a, b, deadline=deadline))
        finish_recovery(app, record, report=True)
        return record["state"]


def log_error(app: str, message: str) -> None:
    path = os.path.join(app, HERE, "recovery.log")
    try:
        safe(path, app)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, RecoveryError) as error:
        print("Recovery log unavailable: %s; %s" % (error, message), file=sys.stderr)


def finish_recovery(app: str, record: dict[str, Any], *, report: bool) -> None:
    if report:
        try:
            staging = os.path.join(app, "update")
            safe(staging, app)
            os.makedirs(staging, exist_ok=True)
            atomic(os.path.join(staging, "result.json"), {
                "version": record["target"], "ok": record["state"] == "COMMITTED",
                "error": "" if record["state"] == "COMMITTED" else
                    "interrupted update recovered; previous version restored: " + record["previous"]}, envelope=False)
        except Exception as error:
            log_error(app, "Could not report recovered transaction: " + str(error))
    try:
        cleanup(app, record)
    except (OSError, RecoveryError) as error:
        # A verified terminal installation remains usable if an old backup is locked.
        log_error(app, "Backup cleanup deferred: " + str(error))


def process_stamp(handle: int) -> int:
    kernel: Any = ctypes.windll.kernel32
    fields = [ctypes.c_ulonglong() for _ in range(4)]
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(f) for f in fields)):
        raise ctypes.WinError()
    return fields[0].value


def current_stamp() -> int:
    kernel: Any = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    return process_stamp(kernel.GetCurrentProcess())


def owner_alive(pid: int, stamp: int) -> bool:
    kernel: Any = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x101000, False, pid)
    if handle:
        try:
            return process_stamp(handle) == stamp and kernel.WaitForSingleObject(ctypes.c_void_p(handle), 0) != 0
        finally:
            kernel.CloseHandle(handle)
    if kernel.GetLastError() == 87:
        return False
    raise RecoveryError("cannot establish that the transaction owner exited")
