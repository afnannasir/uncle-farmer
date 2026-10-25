package unclefarmer.agent;

import java.lang.management.ManagementFactory;

/**
 * Whether the game itself is still sound, for the `health` command.
 *
 * The game is a 32-bit program: it has 2 GB of address space (4 GB once mw.exe is large-address
 * aware), and 1 GB of that is the Java heap. When the rest fills up, starting a thread fails with
 * "OutOfMemoryError: unable to create new native thread". The game's sound and world timer
 * (Timer-1) died of exactly that, the character froze where it stood, and the bot read the frozen
 * game as a stuck walk for five minutes before giving up. Every such error is counted here: any
 * game thread that dies of it (through the default uncaught-exception handler), and any the agent
 * logs itself (Log.error).
 */
final class Health {

    private static volatile int ooms;
    private static volatile long lastAt;
    private static volatile String lastWhere = "";

    private Health() { }

    /** Before the game's main class runs, so its threads die through our handler. */
    static void install() {
        final Thread.UncaughtExceptionHandler before = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler(new Thread.UncaughtExceptionHandler() {
            @Override
            public void uncaughtException(Thread t, Throwable e) {
                note("thread " + t.getName(), e);
                if (before != null) {
                    before.uncaughtException(t, e);
                    return;
                }
                // What ThreadGroup does with no default handler, so the game's error.log reads the same.
                if (!(e instanceof ThreadDeath)) {
                    System.err.print("Exception in thread \"" + t.getName() + "\" ");
                    e.printStackTrace(System.err);
                }
            }
        });
    }

    /** Counts e if it (or what caused it) is an OutOfMemoryError. */
    static void note(String where, Throwable e) {
        for (Throwable c = e; c != null; c = c.getCause()) {
            if (c instanceof OutOfMemoryError) {
                ooms++;
                lastAt = System.currentTimeMillis();
                lastWhere = where + ": " + c.getMessage();
                return;
            }
            if (c.getCause() == c) return;
        }
    }

    /** {"ok":true,"oom":n,"oom_at":ms,"oom_where":"...","threads":n,"heap_mb":n,"heap_max_mb":n} */
    static String snapshot() {
        Runtime rt = Runtime.getRuntime();
        int threads;
        try {
            threads = ManagementFactory.getThreadMXBean().getThreadCount();
        } catch (Throwable t) {
            threads = Thread.activeCount();
        }
        long mb = 1024L * 1024L;
        return "{\"ok\":true,\"oom\":" + ooms + ",\"oom_at\":" + lastAt
                + ",\"oom_where\":\"" + Protocol.jsonEscape(lastWhere) + "\""
                + ",\"threads\":" + threads
                + ",\"heap_mb\":" + ((rt.totalMemory() - rt.freeMemory()) / mb)
                + ",\"heap_max_mb\":" + (rt.maxMemory() / mb) + "}";
    }
}
