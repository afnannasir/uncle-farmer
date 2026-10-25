package unclefarmer.agent;

import java.lang.reflect.Method;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

/**
 * Three things the bot used to do with keys, done the way the game's own code does them:
 * `use <uid>`, `select <id>` and `slot <n> [sustain] [lock]`.
 *
 * use: the use key on a bag row (game.i.w) and a hotkey (game.i.g) both end in game.N.b(0, uid, 0),
 * which writes OUT op=18: 0 (the bag panel), the item's uid and 0 as ints (javap 2026-10-04). The
 * client checks nothing else; the server uses the item and sends the bag's new counts. Here the same
 * ten bytes go through the game's connection (Ask.send), for an item the bag holds, so the bot no
 * longer walks the bag panel with the arrow keys - which also moved us in a fight.
 *
 * select: the target keys (game.i.o) set the client's selected target and speak it; no packet goes
 * out (Memory.select). Done straight, the bot's pick is the game's target at once, with no presses
 * that land on the next monster that way instead.
 *
 * slot: an attack key (W_ATTACK1..10 in config/input.ini, Alt for W_SUSTAIN_ATTACK, the lock key for
 * W_LOCK_ATTACK) is read by game.Win, which only calls world.a(slot, sustain, lock, 0L): slot is the
 * key's number, plus 10 while the second attack page is up (Win.y); that method schedules the
 * game's own press task (game.j) on the game's timer, and everything after it - which skill the slot
 * holds, whom it goes at, the client's range and target checks, the cast request (OUT op=15),
 * walking up for a follow attack - is the game's (javap 2026-10-24). Here that same call is made
 * without the key: no keyboard, no focus, no modifier the player is holding, no page key.
 *
 * A key also comes back up, and game.Win answers that too: once no attack key is down it calls
 * world.a(0L), which schedules the press task with slot -1, and that ends the attack (game.i.G:
 * cancels the cast task and clears the "attacking" bit of our own character). A press with no
 * release leaves the game sure the key is still held: after the next kill every walk fails. So each
 * slot press is let go HOLD_MS later, as the bot's own key taps are (javap 2026-10-25).
 */
final class Act {

    static final int OP_USE = 18;
    static final int BAG_PANEL = 0;
    /** The same item again this soon is refused: one press of the key can't do it faster. */
    static final long SAME_ITEM_GAP_MS = 300L;

    private static volatile int lastUid;
    private static volatile long lastAt;

    private Act() { }

    static String use(int uid) {
        try {
            if (!Memory.inBag(uid)) return "error: no item " + uid + " in the bag";
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        }
        long now = System.currentTimeMillis();
        if (uid == lastUid && now - lastAt < SAME_ITEM_GAP_MS) return "error: too soon";
        String failed = Ask.send(packet(uid), "use " + uid);
        if (failed != null) return failed;
        lastUid = uid;
        lastAt = now;
        return "used";
    }

    /** The attack keys' numbers (W_ATTACK1..10); the second page is the same keys, 11-20. */
    static final int KEYS_PER_PAGE = 10;
    /** How long a slot press is held before it is let go: the bot's key taps in a fight hold 30 ms. */
    static final long HOLD_MS = 30L;
    private static volatile Method press;
    private static volatile Method letGo;
    private static ScheduledFuture<?> release;
    private static final ScheduledExecutorService RELEASES = Executors.newSingleThreadScheduledExecutor(
            new ThreadFactory() {
                @Override
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "unclefarmer-slot-release");
                    t.setDaemon(true);
                    return t;
                }
            });

    /** `slot <n> [sustain] [lock]`: what attack key n does (with Alt, with the lock key) on the page
     *  that is up. "pressed <slot>", or why not. */
    static String slot(int n, boolean sustain, boolean lock) {
        if (n < 1 || n > KEYS_PER_PAGE) return "error: slot 1-" + KEYS_PER_PAGE;
        int page = Memory.attackPage();
        if (page < 0) return "error: the attack page can't be read";
        int slot = page == 0 ? n : n + KEYS_PER_PAGE;
        try {
            return press(Memory.world(), slot, sustain, lock, HOLD_MS);
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("slot " + n, t);
            return "error: " + Protocol.jsonEscape(String.valueOf(t));
        }
    }

    /** Key down on `world` now, key up holdMs later. A press that comes before the last one was let
     *  go takes its release over, as a key pressed again while still down would. */
    static String press(final Object world, int slot, boolean sustain, boolean lock, long holdMs) throws Exception {
        Method down = pressMethod(world.getClass());
        if (down == null) return "error: the game's attack-key method wasn't found";
        final Method up = releaseMethod(world.getClass());
        if (up == null) return "error: the game's attack-key release wasn't found";
        down.invoke(world, slot, sustain, lock, 0L);
        synchronized (Act.class) {
            if (release != null) release.cancel(false);
            release = RELEASES.schedule(new Runnable() {
                @Override
                public void run() {
                    try {
                        up.invoke(world, 0L);
                    } catch (Throwable t) {
                        Log.error("slot release", t);
                    }
                }
            }, holdMs, TimeUnit.MILLISECONDS);
        }
        return "pressed " + slot;
    }

    /** world.a(long), the one method of that shape (game.Win calls it once no attack key is down). */
    static Method releaseMethod(Class<?> world) {
        Method m = letGo;
        if (m != null && m.getDeclaringClass() == world) return m;
        Method found = null;
        for (Method c : world.getDeclaredMethods()) {
            Class<?>[] p = c.getParameterTypes();
            if (c.getReturnType() == void.class && p.length == 1 && p[0] == long.class) {
                if (found != null) return null;
                found = c;
            }
        }
        if (found != null) {
            found.setAccessible(true);
            letGo = found;
        }
        return found;
    }

    /** world.a(int, boolean, boolean, long), the one method of that shape (game.Win's attack keys
     *  call it): found by its parameters, not its name, so a renamed build can't make it another. */
    static Method pressMethod(Class<?> world) {
        Method m = press;
        if (m != null && m.getDeclaringClass() == world) return m;
        Method found = null;
        for (Method c : world.getDeclaredMethods()) {
            Class<?>[] p = c.getParameterTypes();
            if (c.getReturnType() == void.class && p.length == 4 && p[0] == int.class
                    && p[1] == boolean.class && p[2] == boolean.class && p[3] == long.class) {
                if (found != null) return null;       // two of that shape: not sure which, so neither
                found = c;
            }
        }
        if (found != null) {
            found.setAccessible(true);
            press = found;
        }
        return found;
    }

    static String select(int id) {
        try {
            return Memory.select(id);
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        }
    }

    /** op 18, the bag panel byte, the uid, then a 0 int: what N.b(0, uid, 0) writes. */
    static byte[] packet(int uid) {
        return new byte[] {(byte) OP_USE, (byte) BAG_PANEL,
                (byte) (uid >>> 24), (byte) (uid >>> 16), (byte) (uid >>> 8), (byte) uid, 0, 0, 0, 0};
    }
}
