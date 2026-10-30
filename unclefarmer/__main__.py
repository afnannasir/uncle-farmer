"""Start Uncle Farmer.

    python -m unclefarmer            the app
    python -m unclefarmer --strings  refresh lang/unclefarmer.pot, merge the .po files, compile them
    UncleFarmer.exe --apply-update <app folder> <pid> <version>
                                     (the updater's own step: see updater.py)
    UncleFarmer.exe --self-test <report.json>
                                     check the copy without a window (selftest.py; CI runs it)
"""

import sys


def main():
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
    from . import instance
    if not instance.acquire():
        instance.focus_existing()
        return
    from . import applog, filelock, settings
    filelock.acquire_startup()
    import wx
    config = settings.load_app()
    applog.configure(config)
    i18n.load(config.get("language", "en"))
    from . import gamelang
    gamelang.warm()            # the game's own language back to English, read before the first look
    app = wx.App(False)
    i18n.attach_wx(config.get("language", "en"))
    from .gui.main import MainFrame
    frame = MainFrame()
    frame.Show()
    # wx.App.MainLoop is wxPython's own Python wrapper (it restores stdio after the loop) and its stub
    # leaves it unannotated; nothing here reads what it returns
    app.MainLoop()  # pyright: ignore[reportUnknownMemberType]


if __name__ == "__main__":
    main()
