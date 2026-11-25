package unclefarmer.agent;

import java.awt.EventQueue;
import java.awt.Frame;
import java.awt.event.KeyEvent;
import java.awt.event.KeyListener;
import java.lang.reflect.Field;

/**
 * Types into the game's input box (quantities to buy, sell, take out of storage...).
 *
 * The box is not driven by the low-level keyboard hook that {@link Keys} feeds: game.Win is itself
 * the KeyListener of its frame, and (javap, 2026-10-04):
 *   Win.a(game.y, String, int)  opens the box: Win.G = the dialog asking, Win.z = a new
 *                               StringBuilder for the text, Win.A/B = the caret
 *   Win.keyTyped(KeyEvent)      inserts getKeyChar() into Win.z at the caret
 *   Win.keyPressed(KeyEvent)    Enter (keyCode 10) checks the text and submits it
 * So we hand those two methods the same KeyEvents a keyboard would produce, on the event thread.
 *
 * Open-ness is the static mode Win.w, not Win.G: both handlers return at once while w == 0, and a
 * submit sets w = 0 but leaves G pointing at the old dialog (seen live 2026-10-04: a potion was
 * bought, yet G stayed set). w is 3 for a quantity, 1/2/4 for the other edit boxes.
 */
final class TextInput {

    static final String WIN_TEXT = "z";
    static final String WIN_MODE = "w";

    private TextInput() { }

    private static Frame win() throws Memory.Unavailable {
        for (Frame f : Frame.getFrames()) {
            if (f.getClass().getName().equals(Memory.WIN_CLASS)) return f;
        }
        throw new Memory.Unavailable("nowin: no " + Memory.WIN_CLASS + " frame");
    }

    private static Object field(Object o, String name) {
        for (Class<?> k = o.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Field f = k.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(o);
            } catch (NoSuchFieldException e) {
                // superclass
            } catch (IllegalAccessException e) {
                return null;
            }
        }
        return null;
    }

    private static int mode(Frame w) {
        Object m = field(w, WIN_MODE);
        return m instanceof Integer ? (Integer) m : 0;
    }

    /** Whether the game's input box is currently open. */
    static boolean open() throws Memory.Unavailable {
        return mode(win()) != 0;
    }

    /** {"open":bool,"mode":n,"text":"..."}: whether the input box is up and what has been typed so far. */
    static String state() {
        try {
            Frame w = win();
            int mode = mode(w);
            Object text = field(w, WIN_TEXT);
            return "{\"ok\":true,\"open\":" + (mode != 0) + ",\"mode\":" + mode + ",\"text\":\""
                    + Protocol.jsonEscape(text == null || mode == 0 ? "" : text.toString()) + "\"}";
        } catch (Memory.Unavailable u) {
            return "{\"ok\":false,\"error\":\"" + Protocol.jsonEscape(u.getMessage()) + "\"}";
        }
    }

    /** Types text into the open input box. */
    static String type(final String text) {
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                for (int i = 0; i < text.length(); i++) {
                    char c = text.charAt(i);
                    l.keyTyped(new KeyEvent(w, KeyEvent.KEY_TYPED, System.currentTimeMillis(), 0,
                            KeyEvent.VK_UNDEFINED, c));
                }
            }
        }, "typed " + text.length());
    }

    /** Presses Enter in the input box, which submits it. */
    static String enter() {
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                long now = System.currentTimeMillis();
                l.keyPressed(new KeyEvent(w, KeyEvent.KEY_PRESSED, now, 0, KeyEvent.VK_ENTER, '\n'));
                l.keyReleased(new KeyEvent(w, KeyEvent.KEY_RELEASED, now, 0, KeyEvent.VK_ENTER, '\n'));
            }
        }, "entered");
    }

    /**
     * The text of a request line back as typed: a backslash and n is a line feed, two backslashes are one
     * (a request is one line, so the app writes a line feed that way). Any other backslash stays as it is.
     */
    static String unescape(String s) {
        StringBuilder out = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\\' && i + 1 < s.length()) {
                char n = s.charAt(i + 1);
                if (n == 'n') {
                    out.append('\n');
                    i++;
                    continue;
                }
                if (n == '\\') {
                    out.append('\\');
                    i++;
                    continue;
                }
            }
            out.append(c);
        }
        return out.toString();
    }

    /**
     * Pastes text, line feeds included, into the open input box as the game's own Ctrl+V does (Win.keyPressed,
     * keyCode 86: the selection is replaced, the text goes in at the caret with no filtering, and the caret
     * moves after it), but from the request instead of the clipboard, which stays the player's. Typing can't
     * do it: keyTyped drops every character of 31 or less, and Enter submits (seen live 2026-11-25: a
     * pasted line feed is stored as a plain line feed in the announcement, broadcast and system message boxes).
     */
    static String paste(final String escaped) {
        final String text = unescape(escaped);
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                Object z = field(w, WIN_TEXT);
                Object caret = field(w, "A");
                Object other = field(w, "B");
                if (!(z instanceof StringBuilder) || !(caret instanceof Integer) || !(other instanceof Integer)) {
                    throw new IllegalStateException("the input box's fields moved");
                }
                StringBuilder box = (StringBuilder) z;
                int at = (Integer) caret;
                int sel = (Integer) other;
                if (at < sel) {
                    box.delete(at, Math.min(sel, box.length()));
                } else if (at > sel) {
                    box.delete(sel, Math.min(at, box.length()));
                    at = sel;
                }
                at = Math.max(0, Math.min(at, box.length()));
                box.insert(at, text);
                at += text.length();
                setInt(w, "A", at);
                setInt(w, "B", at);
            }
        }, "pasted " + text.length());
    }

    private static void setInt(Object o, String name, int value) {
        for (Class<?> k = o.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Field f = k.getDeclaredField(name);
                f.setAccessible(true);
                f.setInt(o, value);
                return;
            } catch (NoSuchFieldException e) {
                // superclass
            } catch (IllegalAccessException e) {
                throw new IllegalStateException("the input box's field " + name + " can't be set");
            }
        }
        throw new IllegalStateException("the input box has no field " + name);
    }

    /** Replaces a quantity and submits it in one event-queue turn. */
    static String number(final String number) {
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                clear(w, l);
                type(w, l, number);
                enter(w, l);
            }
        }, "entered " + number.length());
    }

    /** Escape: closes whatever box is open (sets Win.w = 0; the game says its title), the same
     *  as the player pressing Escape. The low-level hook can't do it: it drops keys while w != 0. */
    static String escape() {
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                long now = System.currentTimeMillis();
                l.keyPressed(new KeyEvent(w, KeyEvent.KEY_PRESSED, now, 0, KeyEvent.VK_ESCAPE, (char) 27));
                l.keyReleased(new KeyEvent(w, KeyEvent.KEY_RELEASED, now, 0, KeyEvent.VK_ESCAPE, (char) 27));
            }
        }, "escaped");
    }

    /** Clears what has been typed (Backspace through the game's own handler). */
    static String clear() {
        return run(new Job() {
            public void on(Frame w, KeyListener l) {
                clear(w, l);
            }
        }, "cleared");
    }

    private static void type(Frame w, KeyListener l, String text) {
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            l.keyTyped(new KeyEvent(w, KeyEvent.KEY_TYPED, System.currentTimeMillis(), 0,
                    KeyEvent.VK_UNDEFINED, c));
        }
    }

    private static void enter(Frame w, KeyListener l) {
        long now = System.currentTimeMillis();
        l.keyPressed(new KeyEvent(w, KeyEvent.KEY_PRESSED, now, 0, KeyEvent.VK_ENTER, '\n'));
        l.keyReleased(new KeyEvent(w, KeyEvent.KEY_RELEASED, now, 0, KeyEvent.VK_ENTER, '\n'));
    }

    private static void clear(Frame w, KeyListener l) {
        Object text = field(w, WIN_TEXT);
        int n = text == null ? 0 : text.toString().length();
        long now = System.currentTimeMillis();
        for (int i = 0; i < n + 1; i++) {
            l.keyPressed(new KeyEvent(w, KeyEvent.KEY_PRESSED, now, 0, KeyEvent.VK_BACK_SPACE, '\b'));
            l.keyTyped(new KeyEvent(w, KeyEvent.KEY_TYPED, now, 0, KeyEvent.VK_UNDEFINED, '\b'));
            l.keyReleased(new KeyEvent(w, KeyEvent.KEY_RELEASED, now, 0, KeyEvent.VK_BACK_SPACE, '\b'));
        }
    }

    private interface Job {
        void on(Frame w, KeyListener l);
    }

    private static String run(final Job job, String done) {
        try {
            final Frame w = win();
            if (mode(w) == 0) return "refused: no input box is open";
            if (!(w instanceof KeyListener)) return "refused: the game window is not its own key listener";
            final Throwable[] failed = new Throwable[1];
            EventQueue.invokeAndWait(new Runnable() {
                public void run() {
                    try {
                        job.on(w, (KeyListener) w);
                    } catch (Throwable t) {
                        failed[0] = t;
                    }
                }
            });
            if (failed[0] != null) {
                Log.error("text input", failed[0]);
                return "failed: " + failed[0];
            }
            return done;
        } catch (Memory.Unavailable u) {
            return "refused: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("text input", t);
            return "failed: " + t;
        }
    }
}
