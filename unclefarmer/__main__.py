"""Start Uncle Farmer.

    python -m unclefarmer            the app
    python -m unclefarmer --strings  refresh lang/unclefarmer.pot, merge the .po files, compile them
    UncleFarmer.exe --apply-update <app folder> <pid> <version>
                                     (the updater's own step: see updater.py)
"""

import sys


def main():
    if "--apply-update" in sys.argv:
        from . import updater
        updater.run_apply(sys.argv[sys.argv.index("--apply-update") + 1:])
        return
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
    app.MainLoop()


if __name__ == "__main__":
    main()
