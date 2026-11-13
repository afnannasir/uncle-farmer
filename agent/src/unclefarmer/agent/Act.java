package unclefarmer.agent;

import java.lang.reflect.Method;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;

/**
 * Things the bot used to do with keys, done the way the game's own code does them:
 * `use <uid>`, `discarddirectall <uid>...`, `select <id>`, `slot <n> [sustain] [lock]` and
 * `cast <skill> <target>`.
 *
 * use: the use key on a bag row (game.i.w) and a hotkey (game.i.g) both end in game.N.b(0, uid, 0),
 * which writes OUT op=18: 0 (the bag panel), the item's uid and 0 as ints (javap 2026-10-04). The
 * client checks nothing else; the server uses the item and sends the bag's new counts. Here the same
 * ten bytes go through the game's connection (Ask.send), for an item the bag holds, so the bot no
 * longer walks the bag panel with the arrow keys - which also moved us in a fight.
 *
 * discard: Shift+Delete, the quantity box and "are you sure" end in game.i.d(0, uid, n), which calls
 * game.N.b(0, uid, -n): the same op=18 packet as use, with minus the count where use has 0, one
 * packet for n of a stack (javap 2026-11-07). Here every uid is checked against one bag snapshot,
 * then each stack goes out as one such packet.
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
 *
 * cast: a profession key (Z, X, C: W_SKILL1-3) goes the same way as slot 22-24, and for a corpse
 * skill (Draw Blood, Skinning, Cut Flesh: ids above 20) the press task ends in world.a(int target,
 * short skill, int pkMode, boolean stand), which faces the target and sends the cast request
 * (OUT op=15) - the method the attack keys' casts end in too (javap 2026-11-11). Here that method is
 * called after the key's own checks (Memory.corpseRefusal): no key has to hold the skill, and no
 * target has to be selected.
 *
 * But it is called the way a held key calls it (live night 2026-11-12; javap game.i.a(ISIZ) 135-143
 * and the key task at ~1808): the method sets the "attacking" bit of our character (ak.L |= 4), which
 * only the key's release clears (world.a(0L) -> game.i.G). One call and no release left that bit up,
 * and every walk failed until the next fight's key came back up: 607 stucks and 12 hearthstones in
 * one night. And a held key calls it again every 60 ms (game.b at a fixed rate), each call a fresh
 * OUT op=15 with no throttle, so a cast the server isn't ready for yet (the pause after a kill) is
 * simply sent again; our one call right after a kill was mostly never answered (9 of about 97). So
 * the cast is held: sent every CAST_EVERY_MS until our own cast of that skill comes back (IN op=41),
 * the corpse is no longer one the key would take, or CAST_HOLD_MS is up - then let go, always. An
 * attack key press ends a held cast (the press brings its own release).
 *
 * It is cast STANDING (as Alt+key does: the key passes "stand" = the sustain flag, game.i.a(int,
 * boolean, boolean) ~1808) and only at a corpse in harvesting range, Memory.HARVEST_REACH tiles. The
 * server takes a corpse skill only from close by: measured live 2026-11-12, every harvest landed
 * 1.1-4.5 tiles from the corpse, and casts from further off were never answered (agent 61: 1 in about
 * 13). A plain key press would walk up to a far corpse (stand=false: a path and a walk as well as the
 * request), but walking to corpses costs farming time, so far ones are simply left.
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

    /** Discard bag units the way the game's discard does (game.i.d -> N.b(0, uid, -count)). A uid
     * repeated k times means k units of that stack: they go out as one packet with -k, in the order
     * each uid first appears. Every unit is checked against one bag snapshot before anything is sent. */
    static String discardAll(int[] uids) {
        if (uids.length == 0) return "error: discarddirectall needs item ids";
        try {
            String refused = Memory.discardable(uids);
            if (refused != null) return refused;
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        }
        java.util.LinkedHashMap<Integer, Integer> stacks = new java.util.LinkedHashMap<Integer, Integer>();
        for (int uid : uids) {
            Integer had = stacks.get(Integer.valueOf(uid));
            stacks.put(Integer.valueOf(uid), Integer.valueOf(had == null ? 1 : had.intValue() + 1));
        }
        for (java.util.Map.Entry<Integer, Integer> e : stacks.entrySet()) {
            int uid = e.getKey().intValue();
            String failed = Ask.send(discardPacket(uid, e.getValue().intValue()), "discard " + uid);
            if (failed != null) return failed;
        }
        return "sent all " + uids.length;
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
        endCast(false);       // a held corpse cast gives way to the fight: this press's release ends it
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

    private static volatile Method castM;
    /** The profession key's task (game.b) runs at a fixed rate of 60 ms while the key is held. */
    static final long CAST_EVERY_MS = 60L;
    /** The longest a corpse cast is held: past the pause after a kill, short of a fight's worth. */
    static final long CAST_HOLD_MS = 1500L;
    /** Cast from where we are, as Alt+key does: never walk to a corpse (farming time). */
    static final boolean CAST_STANDING = true;
    private static ScheduledFuture<?> casting;
    private static Runnable castLetGo;

    /** Why a held cast should stop now, or null to send it again. */
    interface StillOn {
        String why() throws Exception;
    }

    /** `cast <skill> <target>`: one of our corpse skills at a fresh corpse, as its profession key
     *  would cast it, held and let go (see the class note). "cast <skill> <target>", "refused: <why>"
     *  (nothing sent), or an error. */
    static String cast(final int skill, final int target) {
        try {
            final Object world = Memory.world();
            Method m = castMethod(world.getClass());
            if (m == null) return "error: the game's cast method wasn't found";
            Method up = releaseMethod(world.getClass());
            if (up == null) return "error: the game's attack-key release wasn't found";
            endCast(true);
            int pk;
            final String name;
            synchronized (world) {
                String no = Memory.corpseRefusal(world, skill, target);
                if (no != null) return "refused: " + no;
                pk = Memory.pkMode(world);
                name = Memory.professionName(world, skill);
            }
            final int me = Memory.selfId();
            final long since = System.currentTimeMillis();
            hold(world, m, up, target, (short) skill, pk, CAST_STANDING, CAST_EVERY_MS, CAST_HOLD_MS, new StillOn() {
                @Override
                public String why() throws Exception {
                    // Only this skill's report ends it: the last shots of the fight that made the
                    // corpse come back after the kill too, and stopped every hold at its first send.
                    if (World.castSince(me, since, name)) return "cast";
                    synchronized (world) {
                        return Memory.corpseRefusal(world, skill, target);
                    }
                }
            });
            return "cast " + skill + " " + target;
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("cast " + skill + " " + target, t);
            return "error: " + Protocol.jsonEscape(String.valueOf(t));
        }
    }

    /** The held cast: `down` now and every `every` ms, until `on` gives a reason or `holdMs` is up,
     *  then `up` (the key's release) once. The first send happens before this returns. */
    static void hold(final Object world, final Method down, final Method up, final int target, final short skill,
                     final int pk, final boolean standing, long every, long holdMs, final StillOn on) throws Exception {
        synchronized (world) {
            down.invoke(world, target, skill, pk, standing);
        }
        final long end = System.currentTimeMillis() + holdMs;
        final Runnable letGo = new Runnable() {
            private boolean done;
            @Override
            public synchronized void run() {
                if (done) return;
                done = true;
                try {
                    up.invoke(world, 0L);
                } catch (Throwable t) {
                    Log.error("cast release", t);
                }
            }
        };
        synchronized (Act.class) {
            castLetGo = letGo;
            casting = RELEASES.scheduleAtFixedRate(new Runnable() {
                @Override
                public void run() {
                    try {
                        if (System.currentTimeMillis() < end && on.why() == null) {
                            synchronized (world) {
                                down.invoke(world, target, skill, pk, standing);
                            }
                            return;
                        }
                    } catch (Throwable t) {
                        Log.error("cast hold", t);
                    }
                    endCast(true);
                }
            }, every, every, TimeUnit.MILLISECONDS);
        }
    }

    /** Stop a held cast, if one is going; release: let the key go as well (not when an attack key
     *  press takes over, which brings its own release). */
    static void endCast(boolean release) {
        Runnable letGo;
        synchronized (Act.class) {
            if (casting == null) return;
            casting.cancel(false);
            casting = null;
            letGo = castLetGo;
            castLetGo = null;
        }
        if (release && letGo != null) letGo.run();
    }

    /** world.a(int, short, int, boolean), the one method of that shape (the cast task, game.b, calls
     *  it): found by its parameters, not its name. */
    static Method castMethod(Class<?> world) {
        Method m = castM;
        if (m != null && m.getDeclaringClass() == world) return m;
        Method found = null;
        for (Method c : world.getDeclaredMethods()) {
            Class<?>[] p = c.getParameterTypes();
            if (c.getReturnType() == void.class && p.length == 4 && p[0] == int.class
                    && p[1] == short.class && p[2] == int.class && p[3] == boolean.class) {
                if (found != null) return null;       // two of that shape: not sure which, so neither
                found = c;
            }
        }
        if (found != null) {
            found.setAccessible(true);
            castM = found;
        }
        return found;
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

    /** op 18, bag panel 0, uid, then minus the count: what N.b(0, uid, -count) writes. */
    static byte[] discardPacket(int uid, int count) {
        int n = -count;
        return new byte[] {(byte) OP_USE, (byte) BAG_PANEL,
                (byte) (uid >>> 24), (byte) (uid >>> 16), (byte) (uid >>> 8), (byte) uid,
                (byte) (n >>> 24), (byte) (n >>> 16), (byte) (n >>> 8), (byte) n};
    }
}
