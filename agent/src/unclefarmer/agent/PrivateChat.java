package unclefarmer.agent;

import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Private messages for the bot: the ones that arrive, and sending one.
 *
 * Arriving (javap + packet log, 2026-10-04): IN op=56
 *     38 <ts:8> <msgid:4> <peer:4> <sender:4> "name\ntext"
 * Our own messages come back the same way with us as the sender; those are left out here and taken
 * from what we send instead (OUT op=25 `19 0b <to:4> <text>`), whether the player typed it in the
 * game or the bot sent it, so a conversation shows both sides. Kept in memory only.
 *
 * Names: id -> name of the players seen (their op=32 record) and of everyone in a private message,
 * so a message can go to a name; memory only, gone with the game.
 *
 * Sending: what the game itself does when a private chat line is entered -- its static
 * `N.a(int channel, int target, String text)` (game.N in this build), which writes OUT op=25
 * `19 0b <target:4> <text>` and refuses (with a spoken warning) a second message within 2 s, 3 s
 * on the global channel. The class is found by shape: it holds itself in a static field and the
 * live netty Channel in an instance field, and has exactly one static void (int, int, String).
 */
final class PrivateChat {

    static final int PRIVATE = 11;          // outbound channel number of a private message
    private static final int KEEP = 300;

    private static final List<Object[]> MESSAGES = new ArrayList<Object[]>();   // {seq, at, id, name, text, mine, voice}
    private static final Map<Integer, String> NAMES = new ConcurrentHashMap<Integer, String>();
    private static final int NAMES_MOST = 5000;
    private static long seq;

    private static volatile Instrumentation inst;
    private static volatile Method sender;

    private PrivateChat() { }

    static void init(Instrumentation i) {
        inst = i;
    }

    /** IN op=56. */
    static void inbound(byte[] m) {
        if (m.length < 22) return;
        int from = Protocol.readInt(m, 17);
        boolean voice = from < 0;                  // a voice message: the sender's id negated (Voice)
        if (voice) from = -from;
        if (from == ownId()) return;               // the echo of something we sent
        int nl = -1;
        for (int i = 21; i < m.length; i++) {
            if (m[i] == 0x0A) {
                nl = i;
                break;
            }
        }
        String name = nl > 21 ? new String(m, 21, nl - 21, Protocol.UTF8).trim() : "";
        String id = voice ? Voice.nameAfter(m, nl) : null;
        if (voice && id == null) return;
        String text = voice ? "" : new String(m, nl < 0 ? 21 : nl + 1, m.length - (nl < 0 ? 21 : nl + 1), Protocol.UTF8).trim();
        learn(from, name);
        synchronized (MESSAGES) {
            MESSAGES.add(new Object[] {++seq, System.currentTimeMillis(), from, name, text, Boolean.FALSE, id});
            while (MESSAGES.size() > KEEP) MESSAGES.remove(0);
            MESSAGES.notifyAll();          // a waiting `pms <after> <ms>` answers right now
        }
    }

    /** OUT op=25 on the private channel number: a message we sent, typed in the game or by the bot. */
    static void outbound(byte[] m) {
        if (m.length >= 6 + Voice.NAME && (m[1] & 0xFF) == Voice.PRIVATE_OUT) {
            // our voice message: 19 4b <to:4> <name:32> <sound>
            int to = Protocol.readInt(m, 2);
            String id = Voice.nameAfter(m, 5);
            if (to <= 0 || id == null) return;
            String name = NAMES.get(Integer.valueOf(to));
            synchronized (MESSAGES) {
                MESSAGES.add(new Object[] {++seq, System.currentTimeMillis(), to, name == null ? "" : name, "",
                        Boolean.TRUE, id});
                while (MESSAGES.size() > KEEP) MESSAGES.remove(0);
                MESSAGES.notifyAll();
            }
            return;
        }
        if (m.length < 7 || (m[1] & 0xFF) != PRIVATE) return;
        int to = Protocol.readInt(m, 2);
        String text = new String(m, 6, m.length - 6, Protocol.UTF8).trim();
        if (to <= 0 || text.isEmpty()) return;
        String name = NAMES.get(Integer.valueOf(to));
        synchronized (MESSAGES) {
            MESSAGES.add(new Object[] {++seq, System.currentTimeMillis(), to, name == null ? "" : name, text,
                    Boolean.TRUE, null});
            while (MESSAGES.size() > KEEP) MESSAGES.remove(0);
            MESSAGES.notifyAll();
        }
    }

    /** A player's name, from their record or a message ("name,title,set," keeps only the name). */
    static void learn(int id, String raw) {
        if (id <= 0 || raw == null) return;
        String name = raw.split(",", 2)[0].trim();
        if (name.isEmpty() || name.length() > 24 || name.contains(" belongs to ")) return;
        if (NAMES.size() > NAMES_MOST) NAMES.clear();
        NAMES.put(Integer.valueOf(id), name);
    }

    /** `idof <name>`: the id of a player seen or messaged this game, or 0. */
    static String idOf(String name) {
        String want = name.trim();
        for (Map.Entry<Integer, String> e : NAMES.entrySet()) {
            if (e.getValue().equalsIgnoreCase(want)) return String.valueOf(e.getKey());
        }
        return "0";
    }

    /**
     * `pms <after> <ms>`: like since(), but when nothing newer than `after` has come yet, waits up
     * to ms for it. The bot keeps one of these open, so a message is in its hands the moment the
     * packet arrives -- nothing is checked on a timer.
     */
    static String await(long after, long ms) {
        long end = System.currentTimeMillis() + Math.max(0, Math.min(ms, 30000));
        synchronized (MESSAGES) {
            while (seq <= after) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) break;
                try {
                    MESSAGES.wait(left);
                } catch (InterruptedException e) {
                    break;
                }
            }
        }
        return since(after);
    }

    /** `pms <after>`: every message with a number above `after`, oldest first. */
    static String since(long after) {
        StringBuilder sb = new StringBuilder("{\"ok\":true,\"last\":");
        synchronized (MESSAGES) {
            sb.append(seq).append(",\"pms\":[");
            boolean first = true;
            long now = System.currentTimeMillis();
            for (Object[] e : MESSAGES) {
                if ((Long) e[0] <= after) continue;
                if (!first) sb.append(',');
                first = false;
                sb.append("{\"n\":").append(e[0]).append(",\"age_ms\":").append(now - (Long) e[1])
                        .append(",\"id\":").append(e[2]).append(",\"name\":\"")
                        .append(Protocol.jsonEscape((String) e[3])).append("\",\"text\":\"")
                        .append(Protocol.jsonEscape((String) e[4])).append('"');
                if (Boolean.TRUE.equals(e[5])) sb.append(",\"mine\":true");     // id and name: who it went to
                if (e[6] != null) sb.append(",\"voice\":\"").append(e[6]).append('"');
                sb.append('}');
            }
        }
        return sb.append("]}").toString();
    }

    /** `pm <id> <text>`: sends a private message the way the game's chat line does. */
    static String send(int to, String text) {
        if (to <= 0 || text == null || text.trim().isEmpty()) return "error: pm <id> <text>";
        if (method() == null) return "error: the game's chat sender wasn't found";
        ChatQueue.add(PRIVATE, to, text);     // in turn with every other line, as the game allows
        return "sent";
    }

    private static Method method() {
        Method m = sender;
        if (m != null || inst == null) return m;
        for (Class<?> c : inst.getAllLoadedClasses()) {
            if (!c.getName().startsWith("game.")) continue;
            try {
                boolean holdsSelf = false;
                boolean holdsChannel = false;
                for (Field f : c.getDeclaredFields()) {
                    if (Modifier.isStatic(f.getModifiers()) && f.getType() == c) holdsSelf = true;
                    if (!Modifier.isStatic(f.getModifiers())
                            && f.getType().getName().equals("io.netty.channel.Channel")) holdsChannel = true;
                }
                if (!holdsSelf || !holdsChannel) continue;
                Method found = null;
                int count = 0;
                for (Method x : c.getDeclaredMethods()) {
                    Class<?>[] p = x.getParameterTypes();
                    if (Modifier.isStatic(x.getModifiers()) && x.getReturnType() == void.class && p.length == 3
                            && p[0] == int.class && p[1] == int.class && p[2] == String.class) {
                        found = x;
                        count++;
                    }
                }
                if (count == 1) {
                    found.setAccessible(true);
                    sender = found;
                    Log.info("private messages go through " + c.getName() + "." + found.getName());
                    return found;
                }
            } catch (Throwable ignored) {
                // a class that can't be inspected isn't the one
            }
        }
        return null;
    }

    /** The game's chat sender, N.a(channel, target, text), or null. */
    static Method sender() {
        return method();
    }

    /** The class holding the game's chat sender (game.N in this build), or null. */
    static Class<?> senderClass() {
        Method m = method();
        return m == null ? null : m.getDeclaringClass();
    }

    private static int ownId() {
        try {
            return Memory.selfId();
        } catch (Throwable t) {
            return World.selfId();
        }
    }
}
