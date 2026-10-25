package unclefarmer.agent;

import java.awt.EventQueue;
import java.awt.Frame;

/**
 * Names the game window after the character in it ("Mist World - Name"), so several clients
 * can be told apart in Alt+Tab and by a screen reader.
 *
 * The game owns its title bar: it puts input prompts there on purpose (the screen reader reads
 * them) and sets its plain name back after every input box. So:
 *  - only the game's own plain name (the first title the window had) is ever replaced, never a
 *    prompt;
 *  - only while the window is NOT in front. A title change on the active window is read aloud,
 *    and the game resets the title after every chat line; doing it in the background means the
 *    tagged name is already there when the player switches back, with nothing spoken twice.
 */
final class Titles implements Runnable {

    private static final long EVERY_MS = 500;
    private static final String SEPARATOR = " - ";

    private String plain;       // the game's own name for its window
    private String ours;        // the last title we set

    private Titles() { }

    static void start() {
        Thread t = new Thread(new Titles(), "mwagent-titles");
        t.setDaemon(true);
        t.setPriority(Thread.MIN_PRIORITY);
        t.start();
    }

    @Override
    public void run() {
        while (true) {
            try {
                Thread.sleep(EVERY_MS);
                tick();
            } catch (InterruptedException e) {
                return;
            } catch (Throwable t) {
                // Cosmetic; never worth disturbing the game over.
            }
        }
    }

    private void tick() {
        final Frame win = Memory.window();
        if (win == null || !win.isDisplayable()) return;
        String now = win.getTitle();
        if (now == null || now.isEmpty()) return;
        if (plain == null) {
            plain = now;
            Log.info("window title: \"" + plain + "\"");
        }
        if (win.isActive()) return;

        String name = Memory.selfName();
        final String want = name == null || name.isEmpty() ? plain : plain + SEPARATOR + name;
        if (now.equals(want)) return;
        // The plain name, or a name we gave it for a character who has since logged out. Anything
        // else is the game's (a prompt) and stays.
        if (!now.equals(plain) && !now.equals(ours)) return;
        ours = want;
        EventQueue.invokeLater(new Runnable() {
            @Override
            public void run() {
                try {
                    if (!win.isActive()) win.setTitle(want);
                } catch (Throwable ignored) {
                    // The window closed between the check and the call.
                }
            }
        });
    }
}
