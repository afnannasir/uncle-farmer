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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import wx

    from .applog import Lifecycle


def main() -> None:
    if "--recover-update" in sys.argv:
        # The native bootstrap invokes this only in the immutable rescue runtime.
        # Do not import updater, wx, settings or normal application modules here.
        import os
        from . import update_recovery as recovery
        recovery_app: str | None = None
        trusted_recovery = False
        try:
            index = sys.argv.index("--recover-update")
            if len(sys.argv) != index + 2:
                raise recovery.RecoveryError("expected one recovery application directory")
            recovery_app = os.path.abspath(sys.argv[index + 1])
            expected = os.path.join(recovery_app, recovery.HERE, "runtime", recovery.PAYLOAD)
            packaged = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))
            if not packaged or os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(expected):
                raise recovery.RecoveryError("recovery must run in the independent rescue runtime")
            trusted_recovery = True
            recovery.recover(recovery_app)
        except Exception as error:
            import traceback
            if trusted_recovery and recovery_app is not None:
                try:
                    recovery.log_error(recovery_app, traceback.format_exc())
                except Exception:
                    pass  # stderr still reports failure if the recovery log is unwritable.
            print("Update recovery stopped; files and backups preserved: %s" % error, file=sys.stderr)
            sys.exit(1)
        return
    if "--apply-update" in sys.argv:
        from . import updater
        updater.run_apply(sys.argv[sys.argv.index("--apply-update") + 1:])
        return
    from . import paths, update_recovery as recovery
    if paths.frozen():
        transaction = recovery.load(paths.APP)
        if transaction and transaction["state"] not in recovery.TERMINAL:
            sys.exit("An update needs recovery. Start UncleFarmer.exe, not UncleFarmerApp.exe.")
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
    from . import __version__, paths
    build = "packaged build" if paths.frozen() else "from source"
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
    with boot.step("first-launch language"):
        _first_launch_language(config)
    with boot.step("main window"):
        from .gui.main import MainFrame
        frame = MainFrame()
        frame.Show()
        instance.mark_window(frame.GetHandle())     # what a second launch looks for (focus_existing)
    boot.note("main loop starting")
    return app


def _first_launch_language(config: dict[str, Any]) -> None:
    """Ask once, before building the main window so an accepted language applies immediately."""
    from . import i18n, settings
    from .i18n import _
    if config.get("language_prompted", True):
        return
    current = config.get("language", "en")
    offered = i18n.match_locale(i18n.system_locale())
    if offered is not None and offered[0] != current and current == settings.APP_DEFAULTS["language"]:
        import wx
        from .gui.common import modal
        code, name = offered
        with modal(wx.MessageDialog(None,
                   _("Your system language is supported. Would you like to use {language} in Uncle Farmer?", language=name),
                   _("Would you like to switch to {language}?", language=name),
                   wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)) as dialog:
            dialog.SetYesNoLabels(_("&Yes, switch language"), _("&No, keep current language"))
            if dialog.ShowModal() == wx.ID_YES:
                config["language"] = code
    config["language_prompted"] = True
    settings.save_app(config)
    if config.get("language", "en") != current:
        i18n.load(config["language"])
        i18n.attach_wx(config["language"])


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
    from . import updater
    with down.step("hand the update to the paused copier", must=False):
        updater.finish_apply()
    down.end("closed")


if __name__ == "__main__":
    main()
