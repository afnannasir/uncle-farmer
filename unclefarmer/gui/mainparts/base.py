"""What the main window's parts share: a few helpers, and the window's surface declared once for
the type checker.

The main window (gui/main.py) is put together from one mixin per job, in this package, and every
part calls the others through self. Pyright reads one file at a time, so without FrameParts each
mixin would look like a class using attributes and methods it has never seen. FrameParts is
type-only: it has no __init__, holds no state and changes nothing at run time; the window's
widgets, its state (made in MainFrame.__init__ and the tab builders) and every part's methods are
declared here as Any.
"""

import ctypes
from typing import Any

import wx

from ...clock import CLOCK, Clock
from ...i18n import _

LOG_LIMIT = 3000
SNAP_FRESH = 5.0          # seconds a game reading may be reused (by the poll and by editors)


def foreground_pid():
    """The process id of the window in front, or None."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


def _of(now, top):
    if now is None or now < 0:
        return _("Not read yet")
    if top and top > 0:
        return _("{now} of {top} ({pct}%)", now=int(now), top=int(top), pct=int(round(100.0 * now / top)))
    return str(int(now))


def _duration(seconds):
    m = int(seconds // 60)
    return _("{h} h {m} min", h=m // 60, m=m % 60) if m >= 60 else _("{m} min", m=m)


class FrameParts(wx.Frame):
    clock: Clock = CLOCK
    # ---------------------------------------------------------------- state and widgets
    chat_windows: Any
    chats: Any
    destroyed: Any
    fields: Any
    filling: Any
    game_list: Any
    games: Any
    items: Any
    last_line: Any
    log_box: Any
    log_filter: Any
    log_lines: Any
    messengers: Any
    nb: Any
    pause_btn: Any
    plan_list: Any
    runs: Any
    farm_starts: Any
    s: Any
    scanned: Any
    scanning: Any
    shutdown_wanted: Any
    silenced: Any
    snap: Any
    snap_busy: Any
    start_btn: Any
    stop_btn: Any
    summary: Any
    timer: Any
    updating: Any
    what: Any
    # ---------------------------------------------------------------- every part's methods
    _append: Any
    _checked_updates: Any
    _done: Any
    _got_snapshot: Any
    _note: Any
    _snapshot_for: Any
    _start_follow: Any
    _update_done: Any
    _update_progress: Any
    _when_stopped: Any
    after_update: Any
    auto_scan: Any
    auto_scan_async: Any
    bot_page: Any
    build_menu: Any
    cached: Any
    call: Any
    character: Any
    chat_line: Any
    check_updates: Any
    clear_log: Any
    copy_log: Any
    copy_plan: Any
    delete_selected: Any
    edit_bag: Any
    edit_messages: Any
    edit_other_profile: Any
    edit_profile: Any
    edit_selected: Any
    errand_for: Any
    fill_runs: Any
    first_run: Any
    follow_for: Any
    forget_answered: Any
    game_changed: Any
    game_in_front: Any
    game_row: Any
    go_tab: Any
    install_update: Any
    is_paused: Any
    item_label: Any
    item_text: Any
    keep_chats: Any
    keep_messengers: Any
    keep_silent: Any
    label_games: Any
    last_seen: Any
    listed_item: Any
    live_tally: Any
    locate: Any
    log: Any
    log_page: Any
    new_plan: Any
    new_script: Any
    offer_update: Any
    on_close: Any
    on_done: Any
    on_log: Any
    on_plan_key: Any
    open_chat: Any
    open_client: Any
    open_results_folder: Any
    open_setup: Any
    open_tool: Any
    open_user_guide: Any
    pick_last_run: Any
    plans_page: Any
    poll: Any
    progress: Any
    refresh_games: Any
    render: Any
    request_snapshot: Any
    run_errand: Any
    run_for: Any
    run_text: Any
    say: Any
    scan_now: Any
    script_help: Any
    selected_character: Any
    selected_index: Any
    selected_item: Any
    selected_pid: Any
    set_game_speech: Any
    show_log: Any
    show_results: Any
    show_status: Any
    show_summary: Any
    shut_down: Any
    snapshot: Any
    speak_results: Any
    start: Any
    start_bot: Any
    stop: Any
    stop_all: Any
    stop_bot: Any
    sync_pause: Any
    tagged: Any
    toggle: Any
    toggle_pause: Any
    unfollow: Any
    update_buttons: Any
    walk: Any
