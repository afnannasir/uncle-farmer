/* bootstrap.exe: Uncle Farmer's updater/bootstrapper. Imports only Windows system DLLs, never the
 * application's Python runtime, so a half-replaced UncleFarmer.exe cannot stop it.
 *
 *   bootstrap.exe [args]
 *       recover an interrupted update if one is pending, then start UncleFarmer.exe [args] and exit
 *   bootstrap.exe --relaunch <pid>
 *       wait until that process has ended (the update copier, or an app that found an unfinished
 *       update), then the same: recover if needed, start UncleFarmer.exe, exit
 *   bootstrap.exe --update <copier> <pid> <version> <token>
 *       the app's update: run the staged copier (update\new\...\UncleFarmer.exe --apply-update),
 *       which waits for the old app <pid> to close and replaces the files. When it exits 0, recover
 *       if needed, start the updated UncleFarmer.exe and exit. Any other exit code: the copier
 *       withheld the restart (the old app still runs, or a rollback was incomplete).
 *
 * Ordinary updates never replace this file: updater.BOOTSTRAP is protected, and only a missing or
 * older bootstrap is put in place after a committed update. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>
#include <wctype.h>
#include <stdio.h>
#include <stdlib.h>

#define CAP 32768
#define APP L"UncleFarmer.exe"
#define WAIT_MS 120000
/* updater.BOOTSTRAP_MARKER: how an update tells this bootstrap from an older or missing one. */
__attribute__((used)) static const char bootstrap_marker[] = "UNCLEFARMER-BOOTSTRAP-2";
static wchar_t root[CAP], path[CAP], command[CAP * 2];

static int safe_path(const wchar_t *value) {
    wchar_t buffer[CAP];
    if (wcslen(value) >= CAP || wcslen(value) < 3) return 0;
    wcscpy(buffer, value);
    for (wchar_t *p = buffer + 3; ; ++p) {
        if (*p == L'\\' || *p == 0) {
            wchar_t saved = *p;
            *p = 0;
            DWORD attr = GetFileAttributesW(buffer);
            *p = saved;
            if (attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
            if (saved == 0) break;
        }
    }
    return 1;
}

static void failure(const char *what, DWORD code) {
    wchar_t log[CAP];
    char text[512];
    swprintf(log, CAP, L"%ls\\.recovery\\bootstrap-error-%lu-%llu.txt", root,
             (unsigned long)GetCurrentProcessId(), (unsigned long long)GetTickCount64());
    if (!safe_path(log)) return;
    wchar_t folder[CAP];
    swprintf(folder, CAP, L"%ls\\.recovery", root);
    CreateDirectoryW(folder, NULL);
    HANDLE file = CreateFileW(log, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    int length = snprintf(text, sizeof(text), "%s (Windows/process code %lu). Uncle Farmer was not started.\r\n",
                          what, (unsigned long)code);
    DWORD written;
    if (length > 0 && length < (int)sizeof(text)) WriteFile(file, text, (DWORD)length, &written, NULL);
    FlushFileBuffers(file);
    CloseHandle(file);
}

/* Start exe with args. wait: block until it ends and return its exit code; else 0 once started.
 * job: when not NULL the child runs inside it (killed with this bootstrap if it is killed). */
static DWORD run(const wchar_t *exe, const wchar_t *args, const wchar_t *working, int wait, HANDLE job) {
    STARTUPINFOW si = {0};
    PROCESS_INFORMATION pi = {0};
    si.cb = sizeof(si);
    if (!safe_path(exe)) return ERROR_CANT_ACCESS_FILE;
    if (swprintf(command, CAP * 2, L"\"%ls\" %ls", exe, args) < 0) return ERROR_BUFFER_OVERFLOW;
    if (!CreateProcessW(exe, command, NULL, NULL, FALSE, job ? CREATE_SUSPENDED : 0, NULL, working, &si, &pi))
        return GetLastError();
    if (job) {
        AssignProcessToJobObject(job, pi.hProcess);   /* best effort: an older Windows refuses nested jobs */
        ResumeThread(pi.hThread);
    }
    CloseHandle(pi.hThread);
    DWORD result = 0;
    if (wait) {
        result = ERROR_GEN_FAILURE;
        if (WaitForSingleObject(pi.hProcess, INFINITE) == WAIT_OBJECT_0) GetExitCodeProcess(pi.hProcess, &result);
    }
    CloseHandle(pi.hProcess);
    return result;
}

static int exists_or_unknown(const wchar_t *name) {
    swprintf(path, CAP, L"%ls\\.recovery\\%ls", root, name);
    if (GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES) return 1;
    DWORD error = GetLastError();
    return error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND;
}

/* 0: nothing pending, or the rescue runtime recovered it (*ran says which); else its failure code. */
static DWORD recover(int *ran) {
    *ran = 0;
    if (!exists_or_unknown(L"transaction.json") && !exists_or_unknown(L"transaction.json.prev")
            && !exists_or_unknown(L"armed"))
        return 0;
    wchar_t recovery_args[CAP + 64];
    wchar_t recovery_directory[CAP];
    swprintf(path, CAP, L"%ls\\.recovery\\runtime\\" APP, root);
    swprintf(recovery_directory, CAP, L"%ls\\.recovery\\runtime", root);
    swprintf(recovery_args, CAP + 64, L"--recover-update \"%ls\"", root);
    DWORD result = run(path, recovery_args, recovery_directory, 1, NULL);
    if (result) {
        failure("Recovery runtime .recovery\\runtime\\UncleFarmer.exe failed; preserve the journals and backups", result);
        return result;      /* Fail closed. Never launch a mixed installation. */
    }
    *ran = 1;
    return 0;
}

static int start_app(const wchar_t *args) {
    int ran;
    DWORD result = recover(&ran);
    if (result) return (int)result;
    wchar_t extra[CAP];
    /* --recovered: the app must not hand an unfinished update back to this bootstrap again */
    if (swprintf(extra, CAP, L"%ls%ls", ran ? L"--recovered " : L"", args) < 0) return ERROR_BUFFER_OVERFLOW;
    swprintf(path, CAP, L"%ls\\" APP, root);
    result = run(path, extra, root, 0, NULL);
    if (result) failure("Could not start UncleFarmer.exe", result);
    return (int)result;
}

static int plain(const wchar_t *value, const wchar_t *allowed, size_t most);

static int wait_for(const wchar_t *text) {
    if (!plain(text, L"", 10)) return 0;
    unsigned long pid = wcstoul(text, NULL, 10);
    if (pid == 0 || pid == GetCurrentProcessId()) return 0;
    HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, (DWORD)pid);
    if (!process) return GetLastError() == ERROR_INVALID_PARAMETER;   /* already gone */
    DWORD status = WaitForSingleObject(process, WAIT_MS);
    CloseHandle(process);
    return status == WAIT_OBJECT_0;
}

static int plain(const wchar_t *value, const wchar_t *allowed, size_t most) {
    size_t n = wcslen(value);
    if (n == 0 || n > most) return 0;
    for (const wchar_t *p = value; *p; ++p)
        if (!iswdigit(*p) && !wcschr(allowed, *p)) return 0;
    return 1;
}

/* One spelling per file: full path (separators, "." and ".." resolved), then the long name, since
 * the same folder can be named by its short (8.3) and its long name. Checks compare these only. */
static int long_name(const wchar_t *value, wchar_t *out) {
    wchar_t full[CAP];
    DWORD n = GetFullPathNameW(value, CAP, full, NULL);
    if (n == 0 || n >= CAP) return 0;
    n = GetLongPathNameW(full, out, CAP);
    return n > 0 && n < CAP;
}

static int update(wchar_t **argv) {
    static wchar_t copier[CAP];
    const wchar_t *pid = argv[3], *version = argv[4], *token = argv[5];
    wchar_t staging[CAP], working[CAP], args[CAP + 256];
    if (wcslen(argv[2]) >= CAP || !long_name(argv[2], copier)) {
        failure("The update's copier is missing", ERROR_FILE_NOT_FOUND);
        return ERROR_FILE_NOT_FOUND;
    }
    size_t name_length = wcslen(L"\\" APP), copier_length = wcslen(copier);
    swprintf(staging, CAP, L"%ls\\update\\new\\", root);
    /* only the staged release's own app, inside this installation's update\new folder */
    if (copier_length >= CAP || _wcsnicmp(copier, staging, wcslen(staging)) != 0
            || wcsstr(copier, L"\\..\\") || wcschr(copier, L'/')
            || copier_length <= name_length || _wcsicmp(copier + copier_length - name_length, L"\\" APP) != 0
            || !plain(pid, L"", 10) || !plain(version, L".-abcdefghijklmnopqrstuvwxyz", 64)
            || !plain(token, L"abcdef", 32) || wcslen(token) != 32) {
        failure("Invalid update request", ERROR_INVALID_PARAMETER);
        return ERROR_INVALID_PARAMETER;
    }
    wcscpy(working, copier);
    *wcsrchr(working, L'\\') = 0;
    swprintf(args, CAP + 256, L"--apply-update \"%ls\" %ls %ls %ls --bootstrap", root, pid, version, token);
    /* The app's cancel kills this bootstrap; the job then takes the paused copier with it. */
    HANDLE job = CreateJobObjectW(NULL, NULL);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
    }
    DWORD result = run(copier, args, working, 1, job);
    if (job) CloseHandle(job);       /* the copier has ended: nothing left for the job to kill */
    if (result) return (int)result;  /* the copier withheld the restart and logged why */
    return start_app(L"");
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR args, int show) {
    (void)instance; (void)previous; (void)show;
    DWORD n = GetModuleFileNameW(NULL, root, CAP);
    if (!n || n >= CAP || !safe_path(root)) return ERROR_BAD_PATHNAME;
    wchar_t *slash = wcsrchr(root, L'\\');
    if (!slash) return ERROR_BAD_PATHNAME;
    *slash = 0;
    if (long_name(root, path)) wcscpy(root, path);
    if (wcslen(root) + 128 >= CAP) return ERROR_BUFFER_OVERFLOW;
    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv && argc == 6 && wcscmp(argv[1], L"--update") == 0) return update(argv);
    if (argv && argc == 3 && wcscmp(argv[1], L"--relaunch") == 0) {
        if (!wait_for(argv[2])) {
            failure("The process to wait for did not end", WAIT_TIMEOUT);
            return WAIT_TIMEOUT;
        }
        return start_app(L"");
    }
    return start_app(args);
}
