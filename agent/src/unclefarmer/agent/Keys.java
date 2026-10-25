package unclefarmer.agent;

import java.awt.Frame;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Presses keys in the game the way the keyboard does, from inside the JVM.
 *
 * The game doesn't read keys through AWT. It installs a native low-level keyboard hook (game.Q, a
 * JNA LowLevelKeyboardProc) whose callback hands every key to one static method:
 *
 *   game.Win.a(int flags, int scanCode, int vkCode)
 *
 * flags are the KBDLLHOOKSTRUCT flags: 0x80 = key up, 0x01 = extended key, and 0x10 = INJECTED. A
 * key with the injected bit is swallowed and ignored, which is why SendInput, NVDA Remote and
 * on-screen keyboards never worked and mwBot needs the Interception driver. Calling the method
 * ourselves with clean flags is indistinguishable from a physical key.
 *
 * The method also ignores every key while the static boolean Win.v is false. windowActivated sets it
 * and windowDeactivated clears it, so it means "the game window has focus".
 *
 * That method only does bookkeeping: it notes the key as held in Win.e (the game reads Shift, Alt,
 * Left Ctrl and the walking keys from there) and puts {3, flags, scan, vk, pressed twice} on the
 * game's input queue Win.f, which the game's own input thread works through. So the bot's keys go
 * the same way without the hook: the bookkeeping here, then onto that queue (see direct()). The
 * game never has to think it has focus, so a key the player types in another window can't slip
 * in alongside one of ours. Keys the method treats specially (F5-F12, the Windows key, Alt+Tab)
 * still go through it, with Win.v set for the instant of the call, as does everything if the
 * game's fields ever stop looking the way direct() expects.
 *
 * Held keys are re-sent the way Windows auto-repeats a held key, and are all released when the
 * controller that pressed them disconnects, so a crashed bot can't leave the character walking.
 */
final class Keys implements Runnable {

    static final String WIN_CLASS = "game.Win";
    static final String FOCUS_FIELD = "v";

    private static final int FLAG_EXTENDED = 0x01;
    private static final int FLAG_UP = 0x80;

    /** Windows' default typematic timing: first repeat after ~500 ms, then ~30 per second. */
    private static final long REPEAT_DELAY_MS = 500;
    private static final long REPEAT_EVERY_MS = 33;
    /** With no key held the repeat thread only looks this often: a held key's first repeat is
     *  REPEAT_DELAY_MS away anyway, and an idle game shouldn't be woken 30 times a second. */
    private static final long IDLE_EVERY_MS = 200;

    private static final Map<String, int[]> KEYS = new HashMap<String, int[]>();

    static {
        // name -> {virtual key, scan code, extended}
        String letters = "qwertyuiopasdfghjklzxcvbnm";
        int[] letterScans = {0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19,
                             0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26,
                             0x2C, 0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32};
        for (int i = 0; i < letters.length(); i++) {
            char c = letters.charAt(i);
            KEYS.put(String.valueOf(c), new int[] {Character.toUpperCase(c), letterScans[i], 0});
        }
        for (int d = 1; d <= 9; d++) KEYS.put(String.valueOf(d), new int[] {'0' + d, 0x01 + d, 0});
        KEYS.put("0", new int[] {'0', 0x0B, 0});
        KEYS.put("left", new int[] {37, 0x4B, 1});
        KEYS.put("up", new int[] {38, 0x48, 1});
        KEYS.put("right", new int[] {39, 0x4D, 1});
        KEYS.put("down", new int[] {40, 0x50, 1});
        KEYS.put("space", new int[] {32, 0x39, 0});
        KEYS.put("enter", new int[] {13, 0x1C, 0});
        KEYS.put("esc", new int[] {27, 0x01, 0});
        KEYS.put("tab", new int[] {9, 0x0F, 0});
        KEYS.put("backspace", new int[] {8, 0x0E, 0});
        // W_ATTACK_PAGETURN: flips the attack-key page (Win.y). VK 20 is in the game's accepted set.
        KEYS.put("capslock", new int[] {20, 0x3A, 0});
        // W_SELF_INFO (input.ini spells it BACKWOTE): opens our own character sheet.
        KEYS.put("`", new int[] {192, 0x29, 0});
        int[] numScans = {0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49};
        for (int d = 0; d <= 9; d++) KEYS.put("num" + d, new int[] {96 + d, numScans[d], 0});
        KEYS.put("lctrl", new int[] {162, 0x1D, 0});
        KEYS.put("rctrl", new int[] {163, 0x1D, 1});
        KEYS.put("alt", new int[] {164, 0x38, 0});
        KEYS.put("shift", new int[] {160, 0x2A, 0});
        KEYS.put("-", new int[] {189, 0x0C, 0});
        KEYS.put("=", new int[] {187, 0x0D, 0});
        KEYS.put("'", new int[] {222, 0x28, 0});
        KEYS.put("[", new int[] {219, 0x1A, 0});
        KEYS.put("]", new int[] {221, 0x1B, 0});
        KEYS.put("\\", new int[] {220, 0x2B, 0});
        KEYS.put("delete", new int[] {46, 0x53, 1});
        KEYS.put(".", new int[] {190, 0x34, 0});
        KEYS.put(",", new int[] {188, 0x33, 0});
        KEYS.put(";", new int[] {186, 0x27, 0});
        KEYS.put("/", new int[] {191, 0x35, 0});
        for (int f = 1; f <= 12; f++) KEYS.put("f" + f, new int[] {111 + f, f <= 10 ? 0x3A + f : 0x56 + f - 10, 0});
    }

    /** Held keys -> when they went down. */
    private static final Map<String, Long> HELD = new ConcurrentHashMap<String, Long>();

    /** Set on the thread that is sending one of our keys (send), so GuardedKeys can tell a
     *  modifier we press from one the player holds: the game's hook runs on its own thread. */
    private static final ThreadLocal<Boolean> OURS = new ThreadLocal<Boolean>();
    /** Runs that asked for the guard (guardmods on), across connections. */
    private static int guarding = 0;

    /**
     * Win.e while a bot run drives this game. The game's keyboard hook notes Shift, Ctrl and Alt as
     * held in Win.e for every key on the computer, before it checks that its window has focus, and
     * the game's input thread reads them from there for the next key. So the player holding Ctrl in
     * another window (NVDA) turned the bot's "2" into Ctrl+2 - put the panel item under the cursor
     * on key 2 - and the beast replaced the blue potions (nights of 2026-10-09 and 2026-10-11).
     * Taking Ctrl back out before each of our keys (forgetPlayersCtrl) lost a race: the held Ctrl
     * repeats every 33 ms and the queue is read a moment after we send. Here the player's
     * modifiers are never written while a run has the guard on; ours still are.
     *
     * The hook itself still sees the player's: it lets Tab out to Windows only while Alt is held
     * (Alt+Tab), and with Alt hidden it swallowed Tab, so Alt+Tab did nothing while a run drove the
     * game. The player's modifiers are kept to one side and answered only on the hook's thread (the
     * thread that writes them); the input thread, which reads them for each queued key, and our
     * own sends still see only ours.
     */
    static final class GuardedKeys extends java.util.HashMap<Object, Object> {
        private static final long serialVersionUID = 1L;
        volatile boolean on;
        /** The player's Shift, Ctrl and Alt held while the guard is on: vk -> since. */
        final transient Map<Object, Object> players = new ConcurrentHashMap<Object, Object>();
        /** The game's keyboard hook thread: the one that notes the player's keys. */
        transient volatile Thread hook;

        GuardedKeys(Map<Object, Object> was) {
            super(was);
        }

        private boolean playersOnHook(Object key) {
            return Thread.currentThread() == hook && players.containsKey(key);
        }

        @Override
        public Object put(Object key, Object value) {
            if (on && isModifier(key) && OURS.get() == null) {
                hook = Thread.currentThread();
                players.put(key, value);
                return null;
            }
            return super.put(key, value);
        }

        @Override
        public boolean containsKey(Object key) {
            return super.containsKey(key) || playersOnHook(key);
        }

        @Override
        public Object get(Object key) {
            Object v = super.get(key);
            return v != null || !playersOnHook(key) ? v : players.get(key);
        }

        /** The player letting go of a modifier forgets only theirs: one we hold stays held. */
        @Override
        public Object remove(Object key) {
            if (on && isModifier(key) && OURS.get() == null) return players.remove(key);
            return super.remove(key);
        }
    }

    static boolean isModifier(Object key) {
        int vk = key instanceof Integer ? ((Integer) key).intValue() : -1;
        return vk >= 160 && vk <= 165;      // Shift, Ctrl, Alt, left and right
    }

    /**
     * "guardmods on" for the length of a run (the connection's closing turns it off), so the
     * player's own Shift, Ctrl and Alt work in this game whenever no bot is running in it.
     */
    @SuppressWarnings("unchecked")
    static synchronized String guardModifiers(boolean on) {
        if (!resolve() || pressed == null) return "not ready: " + status;
        guarding = Math.max(0, guarding + (on ? 1 : -1));
        try {
            Object m = pressed.get(null);
            GuardedKeys g;
            if (m instanceof GuardedKeys) {
                g = (GuardedKeys) m;
            } else if (guarding == 0) {
                return "off";
            } else if (!(m instanceof java.util.HashMap)) {
                return "not ready: Win.e is not a HashMap";
            } else {
                g = new GuardedKeys((Map<Object, Object>) m);
                pressed.set(null, g);
            }
            g.on = guarding > 0;
            g.players.clear();
            if (g.on) {
                // whatever the player holds right now is forgotten too; what we hold stays
                java.util.Set<Integer> ours = new java.util.HashSet<Integer>();
                for (String name : HELD.keySet()) ours.add(Integer.valueOf(KEYS.get(name)[0]));
                for (int vk = 160; vk <= 165; vk++) {
                    if (!ours.contains(Integer.valueOf(vk))) g.remove(Integer.valueOf(vk));
                }
            }
            Log.info("keys: the player's Shift, Ctrl and Alt " + (g.on ? "are ignored while the bot runs" : "count again"));
            return g.on ? "on" : "off";
        } catch (Throwable t) {
            Log.error("guardmods", t);
            return "failed: " + t;
        }
    }

    private static Method handler;
    private static Field focus;
    /** Win.e: the keys the game believes are held (vk -> since). Null if the game moved it. */
    private static Field pressed;

    static final String PRESSED_FIELD = "e";
    private static final int VK_LCTRL = 162;

    // The rest of what Win's key method uses (javap, this build): the input queue, the screen state
    // that picks the message kind, the last key and when it went down (pressed twice), the open
    // text box flag, and the keys the game listens to at all. Any one missing: no direct keys.
    static final String QUEUE_FIELD = "f";
    static final String SCREEN_FIELD = "g";
    static final String SCREENS_FIELD = "h";
    static final String LAST_KEY_FIELD = "t";
    static final String LAST_AT_FIELD = "u";
    static final String TYPING_FIELD = "w";
    static final String ACCEPTED_FIELD = "s";
    private static Field queue, screen, screens, lastKey, lastAt, typing, accepted;
    private static volatile String status = "not resolved";

    private Keys() { }

    static void start() {
        Thread t = new Thread(new Keys(), "mwagent-keys");
        t.setDaemon(true);
        t.start();
    }

    /** Resolves the handler if it can, so asking is enough to find out whether keys will work. */
    static synchronized String status() {
        resolve();
        return status;
    }

    // ---------------------------------------------------------------- public actions

    static String tap(String name, long holdMs) {
        if (name.length() > 1 && name.indexOf('+') > 0) return combo(name, holdMs);
        String r = down(name);
        if (!r.startsWith("down")) return r;
        // Held down already (another connection walks on it): it is pressed as it is, and letting
        // it go here would stop that connection's walk with its key still counted as held.
        if (r.endsWith(ALREADY)) return "tapped " + name + ALREADY;
        sleep(Math.max(20, Math.min(holdMs, 2000)));
        return up(name).replace("up", "tapped");
    }

    /** "lctrl+alt+up": hold each modifier in order, tap the last key, release in reverse - only
     *  the modifiers pressed here: one another connection already held stays down. */
    private static String combo(String spec, long holdMs) {
        String[] parts = spec.split("[+]");
        for (String p : parts) {
            if (!KEYS.containsKey(p)) return "unknown key: " + p;
        }
        boolean[] ours = new boolean[parts.length];
        try {
            for (int i = 0; i < parts.length - 1; i++) {
                String r = down(parts[i]);
                if (!r.startsWith("down")) return r;
                ours[i] = !r.endsWith(ALREADY);
                sleep(20);
            }
            String r = tap(parts[parts.length - 1], holdMs);
            return r.startsWith("tapped") ? "tapped " + spec : r;
        } finally {
            for (int i = parts.length - 2; i >= 0; i--) {
                if (ours[i]) up(parts[i]);
            }
        }
    }

    static final String ALREADY = " (already held)";

    static String down(String name) {
        int[] k = KEYS.get(name);
        if (k == null) return "unknown key: " + name;
        // Claimed before it is sent, in one step: two connections pressing the same key at once
        // (the bot and the message helper) both passed a containsKey check and both counted it as
        // theirs, so the first to close let go of the key the other was still walking on.
        if (HELD.putIfAbsent(name, Long.valueOf(System.currentTimeMillis())) != null) {
            return "down " + name + ALREADY;
        }
        String r = send(k, true);
        if (r != null) {
            HELD.remove(name);
            return r;
        }
        return "down " + name;
    }

    static String up(String name) {
        int[] k = KEYS.get(name);
        if (k == null) return "unknown key: " + name;
        HELD.remove(name);
        String r = send(k, false);
        return r != null ? r : "up " + name;
    }

    /** Lets go of the keys one connection holds, and only those: another controller in the same
     *  game (the private-message helper, a second run) must keep its character walking. */
    static String release(java.util.Set<String> names) {
        int n = 0;
        for (String name : new java.util.ArrayList<String>(names)) {
            if (HELD.containsKey(name)) {
                up(name);
                n++;
            }
        }
        names.clear();
        if (n > 0) Log.info("ACTION released " + n + " key(s) this connection held");
        return "released " + n;
    }

    // ---------------------------------------------------------------- the call itself

    /** Null on success, otherwise why it failed. */
    private static synchronized String send(int[] k, boolean down) {
        if (!resolve()) return "not ready: " + status;
        OURS.set(Boolean.TRUE);
        try {
            return sendOurs(k, down);
        } finally {
            OURS.remove();
        }
    }

    private static String sendOurs(int[] k, boolean down) {
        int flags = (k[2] != 0 ? FLAG_EXTENDED : 0) | (down ? 0 : FLAG_UP);
        if (k[0] != VK_LCTRL && !HELD.containsKey("lctrl")) forgetPlayersCtrl();
        if (queue != null && direct(k[0], k[1], flags, down)) return null;
        try {
            boolean hadFocus = focus.getBoolean(null);
            if (!hadFocus) focus.setBoolean(null, true);
            try {
                handler.invoke(null, Integer.valueOf(flags), Integer.valueOf(k[1]), Integer.valueOf(k[0]));
            } finally {
                if (!hadFocus) focus.setBoolean(null, false);
            }
            return null;
        } catch (Throwable t) {
            Log.error("key", t);
            return "failed: " + t;
        }
    }

    /**
     * Win's key method for the keys the bot sends, done here: the same bookkeeping in Win.e, and
     * the same message onto the game's input queue (the game's input thread then reads Shift, Alt
     * and Left Ctrl from Win.e as it would for a key from the hook). False: leave this key to the
     * game's own method (F5-F12, the Windows key, F2 and Alt+Tab each do something of their own
     * there), or the fields didn't look right - then every later key goes that way too.
     */
    @SuppressWarnings("unchecked")
    private static boolean direct(int vk, int scan, int flags, boolean down) {
        if ((vk >= 116 && vk <= 123) || vk == 91 || vk == 113) return false;
        try {
            Map<Object, Object> held = (Map<Object, Object>) pressed.get(null);
            if (vk == 9 && (held.containsKey(Integer.valueOf(164)) || held.containsKey(Integer.valueOf(165)))) return false;
            java.util.Set<Object> listens = (java.util.Set<Object>) accepted.get(null);
            Integer key = Integer.valueOf(vk);
            if (listens != null && !listens.contains(key)) return true;   // the game ignores this key
            if (typing.getInt(null) > 0) return true;     // a text box is open: hook keys do nothing then
            long now = System.currentTimeMillis();
            if (vk == 160 || vk == 161 || vk == 162 || vk == 164 || vk == 165) {
                // Shift, Left Ctrl, Alt: only noted as held; the next key reads them
                if (down) {
                    if (!held.containsKey(key)) held.put(key, Long.valueOf(now));
                } else {
                    held.remove(key);
                }
                return true;
            }
            boolean twice = false;
            if (down) {
                if (held.containsKey(key)) return true;   // still down: the game sends nothing for a repeat
                held.put(key, Long.valueOf(now));
                twice = vk == lastKey.getInt(null) && now - lastAt.getLong(null) <= 300;
                lastKey.setInt(null, vk);
                lastAt.setLong(null, now);
            } else if (held.containsKey(key)) {
                twice = vk == lastKey.getInt(null) && now - lastAt.getLong(null) <= 200;
                held.remove(key);
            }
            int[] st = (int[]) screen.get(null);
            int[] message = {3, flags, scan, vk, twice ? 1 : 0};
            java.util.concurrent.BlockingQueue<Object> q = (java.util.concurrent.BlockingQueue<Object>) queue.get(null);
            if (st[1] != 999 && st[2] != 999 && st[3] != 999) {
                q.put(message);
            } else if (st[1] == 999 && screenKind(st[0]) == 11) {
                message[0] = 4;
                q.put(message);
            }
            return true;
        } catch (Throwable t) {
            Log.error("direct keys - using the game's key method from now on", t);
            queue = null;
            return false;
        }
    }

    /** Field a of entry i in Win.h (game.an): which kind of screen is up; -1 if there is none
     *  (the game's own method would throw there and drop the key, as this does). */
    private static int screenKind(int i) {
        try {
            Object o = ((java.util.List<?>) screens.get(null)).get(i);
            Field a = o.getClass().getDeclaredField("a");
            a.setAccessible(true);
            return a.getInt(o);
        } catch (Throwable t) {
            return -1;
        }
    }

    /** The static field, accessible, if it has this type; else null. */
    private static Field staticField(Class<?> win, String name, Class<?> type) {
        try {
            Field f = win.getDeclaredField(name);
            if (!Modifier.isStatic(f.getModifiers()) || !type.isAssignableFrom(f.getType())) return null;
            f.setAccessible(true);
            return f;
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * The game follows Left Ctrl before it checks focus (Left Ctrl silences its speech), so a
     * screen reader user's Ctrl, pressed in any window, sits in Win.e while it is down. A key we
     * send then counts as Left Ctrl + that key: Left Ctrl + a hotkey puts the panel item under the
     * cursor on that hotkey (live: a just-summoned pet took over the blue potions' key). Our own
     * keys never mean Ctrl unless we hold lctrl ourselves, so forget the player's. Not put back:
     * if they let go meanwhile, a restored entry would make every later key a Ctrl combination,
     * and their next Ctrl press records it again.
     */
    @SuppressWarnings("unchecked")
    private static void forgetPlayersCtrl() {
        if (pressed == null) return;
        try {
            Map<Object, Object> m = (Map<Object, Object>) pressed.get(null);
            if (m != null && m.remove(Integer.valueOf(VK_LCTRL)) != null) Log.info("ACTION ignored the player's Left Ctrl for our key");
        } catch (Throwable t) {
            Log.error("ctrl", t);
            pressed = null;
        }
    }

    /**
     * Finds Win's key handler by shape: the static method taking (int,int,int) and returning int.
     * The name is obfuscated and may change on a patch; the shape is what the JNA hook needs, so it
     * stays. Refuses if there isn't exactly one.
     */
    private static boolean resolve() {
        if (handler != null) return true;
        Class<?> win = null;
        for (Frame f : Frame.getFrames()) {
            if (f.getClass().getName().equals(WIN_CLASS)) {
                win = f.getClass();
                break;
            }
        }
        if (win == null) {
            status = "no " + WIN_CLASS + " window yet";
            return false;
        }
        Method found = null;
        int count = 0;
        for (Method m : win.getDeclaredMethods()) {
            Class<?>[] p = m.getParameterTypes();
            if (Modifier.isStatic(m.getModifiers()) && m.getReturnType() == int.class && p.length == 3
                    && p[0] == int.class && p[1] == int.class && p[2] == int.class) {
                found = m;
                count++;
            }
        }
        if (count != 1) {
            status = count + " candidate key handlers on " + WIN_CLASS + " (expected 1) - game patched?";
            return false;
        }
        try {
            Field v = win.getDeclaredField(FOCUS_FIELD);
            if (!Modifier.isStatic(v.getModifiers()) || v.getType() != boolean.class) {
                status = WIN_CLASS + "." + FOCUS_FIELD + " is not the static focus flag - game patched?";
                return false;
            }
            v.setAccessible(true);
            found.setAccessible(true);
            focus = v;
            handler = found;
            try {
                Field e = win.getDeclaredField(PRESSED_FIELD);
                if (Modifier.isStatic(e.getModifiers()) && Map.class.isAssignableFrom(e.getType())) {
                    e.setAccessible(true);
                    pressed = e;
                } else {
                    Log.info("keys: " + WIN_CLASS + "." + PRESSED_FIELD + " is not the held-keys map; the player's Ctrl can't be ignored");
                }
            } catch (NoSuchFieldException ignored) {
                Log.info("keys: no " + WIN_CLASS + "." + PRESSED_FIELD + "; the player's Ctrl can't be ignored");
            }
            Field fq = staticField(win, QUEUE_FIELD, java.util.concurrent.BlockingQueue.class);
            Field fg = staticField(win, SCREEN_FIELD, int[].class);
            Field fh = staticField(win, SCREENS_FIELD, java.util.List.class);
            Field ft = staticField(win, LAST_KEY_FIELD, int.class);
            Field fu = staticField(win, LAST_AT_FIELD, long.class);
            Field fw = staticField(win, TYPING_FIELD, int.class);
            Field fs = staticField(win, ACCEPTED_FIELD, java.util.Set.class);
            boolean all = pressed != null && fq != null && fg != null && fh != null && ft != null
                    && fu != null && fw != null && fs != null;
            if (all) {
                screen = fg;
                screens = fh;
                lastKey = ft;
                lastAt = fu;
                typing = fw;
                accepted = fs;
                queue = fq;
            }
            status = "ready (" + WIN_CLASS + "." + found.getName()
                    + (all ? ", straight onto the input queue" : ", through the key method") + ")";
            Log.info("keys " + status);
            return true;
        } catch (Throwable t) {
            status = "resolve failed: " + t;
            return false;
        }
    }

    // ---------------------------------------------------------------- auto-repeat

    @Override
    public void run() {
        while (true) {
            sleep(HELD.isEmpty() ? IDLE_EVERY_MS : REPEAT_EVERY_MS);
            try {
                long now = System.currentTimeMillis();
                for (Map.Entry<String, Long> e : HELD.entrySet()) {
                    if (now - e.getValue().longValue() >= REPEAT_DELAY_MS) repeat(e.getKey());
                }
            } catch (Throwable t) {
                Log.error("key repeat", t);
            }
        }
    }

    /** A held key's repeat, only while it is still held: up() takes the key out of HELD before it
     *  sends the release, so a repeat read from HELD just before that and sent just after would
     *  put the key down again - and the game would walk on with nobody holding it. */
    private static synchronized void repeat(String name) {
        if (HELD.containsKey(name)) send(KEYS.get(name), true);
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }
}
