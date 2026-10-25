package unclefarmer.agent;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;

/**
 * Every line we send - chat channels and private messages, from the Chat window, the auto replies
 * or answers to trusted players - goes out one at a time, as fast as the game lets it.
 *
 * The game's sender (N.a(channel, target, text), javap 2026-10-21) keeps one static time of the
 * last line sent, for every channel and private messages together, and drops a line that comes
 * within 3 s of it on the global channel or 2 s on any other - silently, but for a spoken warning.
 * So a reply to a private message and a line on another channel a second later lost the second.
 * Here each line waits for the game's own last-sent time (so lines the player typed in the game
 * count too), is sent, and is sent again if the game's time didn't move (it was dropped after all).
 *
 * The game's chat box takes 200 characters; a longer text is cut into parts at a space, each sent
 * in turn.
 */
final class ChatQueue {

    static final int MOST = 200;
    private static final long GLOBAL_GAP = 3000L;
    private static final long GAP = 2000L;
    private static final long MARGIN = 150L;      // a little more than the game asks, never less
    private static final int TRIES = 3;
    private static final String LAST_SENT = "p";

    private static final LinkedBlockingQueue<Object[]> QUEUE = new LinkedBlockingQueue<Object[]>();  // {channel, target, text[, sound]}
    private static volatile Thread worker;
    private static volatile long ourLast;
    private static volatile Field lastSent;       // the game's static long: when it last sent a line
    private static volatile boolean looked;

    private ChatQueue() { }

    /** Queue a text for channel/target, in parts of at most MOST characters. Returns how many parts. */
    static int add(int channel, int target, String text) {
        List<String> parts = split(text.replace('\r', ' ').replace('\n', ' ').trim());
        for (String p : parts) QUEUE.add(new Object[] {Integer.valueOf(channel), Integer.valueOf(target), p});
        start();
        return parts.size();
    }

    /** Queue a voice message (Voice): for Team or Gang, whose sender keeps the same gap as text,
     *  or a private one (channel PrivateChat.PRIVATE, target the player), which keeps the parts of a
     *  long recording in order and apart. */
    static void addVoice(int channel, int target, String name, byte[] sound) {
        QUEUE.add(new Object[] {Integer.valueOf(channel), Integer.valueOf(target), name, sound});
        start();
    }

    /** Cut at the last space before the limit (or hard, in a word longer than the limit). */
    static List<String> split(String text) {
        List<String> out = new ArrayList<String>();
        String rest = text.trim();
        while (rest.length() > MOST) {
            int cut = rest.lastIndexOf(' ', MOST);
            if (cut < MOST / 2) cut = MOST;
            out.add(rest.substring(0, cut).trim());
            rest = rest.substring(cut).trim();
        }
        if (!rest.isEmpty()) out.add(rest);
        return out;
    }

    private static synchronized void start() {
        if (worker != null && worker.isAlive()) return;
        worker = new Thread(new Runnable() {
            public void run() {
                loop();
            }
        }, "unclefarmer-chat-send");
        worker.setDaemon(true);
        worker.start();
    }

    private static void loop() {
        while (true) {
            Object[] job;
            try {
                job = QUEUE.take();
            } catch (InterruptedException e) {
                return;
            }
            int channel = (Integer) job[0];
            for (int i = 0; i < TRIES; i++) {
                waitTurn(channel);
                long before = gameLast();
                if (job.length > 3 && channel == PrivateChat.PRIVATE) {
                    // the private voice sender keeps no last-sent time: once, nothing to check
                    Voice.sendPrivate((Integer) job[1], (String) job[2], (byte[]) job[3]);
                    ourLast = System.currentTimeMillis();
                    break;
                } else if (job.length > 3) {
                    Voice.sendChannel(channel, (String) job[2], (byte[]) job[3]);
                } else {
                    send(channel, (Integer) job[1], (String) job[2]);
                }
                ourLast = System.currentTimeMillis();
                long after = gameLast();
                if (after < 0 || after != before) break;     // the game took it (or we can't tell)
            }
        }
    }

    private static void waitTurn(int channel) {
        long gap = (channel == 0 ? GLOBAL_GAP : GAP) + MARGIN;
        while (true) {
            long last = Math.max(ourLast, gameLast());
            long left = last + gap - System.currentTimeMillis();
            if (left <= 0) return;
            try {
                Thread.sleep(left);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private static void send(int channel, int target, String text) {
        Method m = PrivateChat.sender();
        if (m == null) return;
        try {
            m.invoke(null, Integer.valueOf(channel), Integer.valueOf(target), text);
        } catch (Throwable t) {
            Log.error("chat send", t);
        }
    }

    /** The game's own time of its last line sent, or -1 when it can't be read. */
    private static long gameLast() {
        Field f = lastSentField();
        if (f == null) return -1;
        try {
            return f.getLong(null);
        } catch (Throwable t) {
            return -1;
        }
    }

    /** The sender class's only static long that holds a time near now. */
    private static Field lastSentField() {
        if (looked) return lastSent;
        Method m = PrivateChat.sender();
        if (m == null) return null;
        looked = true;
        Class<?> c = m.getDeclaringClass();
        try {
            Field f = c.getDeclaredField(LAST_SENT);     // its name in this build (javap)
            if (Modifier.isStatic(f.getModifiers()) && f.getType() == long.class) {
                f.setAccessible(true);
                lastSent = f;
                return f;
            }
        } catch (Throwable ignored) {
            // renamed in another build: look for it by what it holds
        }
        long now = System.currentTimeMillis();
        for (Field f : c.getDeclaredFields()) {
            if (!Modifier.isStatic(f.getModifiers()) || f.getType() != long.class) continue;
            try {
                f.setAccessible(true);
                long v = f.getLong(null);
                if (v == 0 || Math.abs(now - v) < 86400000L) {    // 0 before the first line, else a time today
                    lastSent = f;
                    return f;
                }
            } catch (Throwable ignored) {
                // a field we can't read isn't the one
            }
        }
        return null;
    }
}
