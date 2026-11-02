"""Start Uncle Farmer.

    python -m unclefarmer            the app
    python -m unclefarmer --strings  refresh lang/unclefarmer.pot, merge the .po files, compile them
    UncleFarmer.exe --apply-update <app folder> <pid> <version>
                                     (the updater's own step: see updater.py)
    UncleFarmer.exe --self-test <report.json>
                                     check the copy without a window (selftest.py; CI runs it)
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import wx

    from .applog import Lifecycle


def main() -> None:
    if "--apply-update" in sys.argv:
        from . import updater
        updater.run_apply(sys.argv[sys.argv.index("--apply-update") + 1:])
        return
    if "--self-test" in sys.argv:
        from . import selftest
        i = sys.argv.index("--self-test")
        sys.exit(selftest.run(sys.argv[i + 1] if i + 1 < len(sys.argv) else "self-test.json"))
    from . import i18n
    if "--strings" in sys.argv:
        path, n = i18n.write_template()
        print("%d strings written to %s" % (n, path))
        return
    from . import applog
    boot = applog.bootup
    boot.begin(_about())
    try:
        app = _start(boot)
    except BaseException as e:
        boot.crashed(e)
        raise
    if app is None:
        return
    # wx.App.MainLoop is wxPython's own Python wrapper (it restores stdio after the loop) and its stub
    # leaves it unannotated; nothing here reads what it returns
    app.MainLoop()  # pyright: ignore[reportUnknownMemberType]
    _finish(applog.teardown)


def _about() -> str:
    from . import __version__
    build = "packaged build" if getattr(sys, "frozen", False) else "from source"
    return "Uncle Farmer %s, %s, Python %s" % (__version__, build, sys.version.split()[0])


def _start(boot: Lifecycle) -> wx.App | None:
    """Every stage up to the main loop, each in bootup.log; the window's own first checks and the
    END line come from MainFrame.first_run, once the loop runs. None: another copy is open."""
    from . import instance
    with boot.step("one copy only"):
        first = instance.acquire()
    if not first:
        boot.note("another Uncle Farmer is open: it is brought to the front and this copy ends")
        instance.focus_existing()
        boot.end("not started (another copy is open)")
        return None
    from . import applog, filelock, settings
    with boot.step("hold the data files"):
        filelock.acquire_startup()
    with boot.step("settings"):
        config = settings.load_app()
    with boot.step("log settings"):
        applog.configure(config)
    from . import i18n
    with boot.step("language"):
        i18n.load(config.get("language", "en"))
    with boot.step("game language"):
        from . import gamelang
        gamelang.warm()        # the game's own language back to English, read before the first look
    with boot.step("wx"):
        import wx
        app = wx.App(False)
        i18n.attach_wx(config.get("language", "en"))
    with boot.step("main window"):
        from .gui.main import MainFrame
        frame = MainFrame()
        frame.Show()
    boot.note("main loop starting")
    return app


def _finish(down: Lifecycle) -> None:
    """After the main loop: what the window's on_close didn't, then the END of teardown.log."""
    if not down.open:
        down.begin("the main loop ended without a close request from the window")
    else:
        down.note("main loop ended")
    from . import filelock, instance
    with down.step("release the data files", must=False):
        filelock.release_all()
    with down.step("release the one-copy lock", must=False):
        instance.release()
    down.end("closed")


if __name__ == "__main__":
    main()
