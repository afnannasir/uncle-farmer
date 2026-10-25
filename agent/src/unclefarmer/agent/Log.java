package unclefarmer.agent;

import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.text.SimpleDateFormat;
import java.util.Date;

/** Append-only UTF-8 logs under the agent's output directory. */
final class Log {

    private static final SimpleDateFormat TS = new SimpleDateFormat("HH:mm:ss.SSS");

    /**
     * A log is rolled at this size. The full file goes into archive/ compressed (a packet log
     * shrinks about 7 times) and is kept - all of it is information about the game - until the
     * archive passes ARCHIVE_LIMIT, when the oldest files go first.
     */
    private static final long MAX_BYTES = 64L * 1024 * 1024;
    private static final long ARCHIVE_LIMIT = 8L * 1024 * 1024 * 1024;
    static final String ARCHIVE = "archive";
    private static final int CHECK_EVERY = 500;

    private static Path dir;
    private static int writes;

    /**
     * Several game clients can run at once, each with this agent, all writing into one folder. The
     * agent log is kept per game ("agent.<pid>.log"), so two games' lines never
     * mix; the pid matches the agent.<pid>.port file and the game the bot names in its own log.
     */
    private static final String PID = pid();
    private static final java.util.Set<String> PER_GAME =
            new java.util.HashSet<String>(java.util.Arrays.asList("agent.log"));

    private static String pid() {
        try {
            String n = ManagementFactory.getRuntimeMXBean().getName();     // "1234@host"
            return n.substring(0, n.indexOf('@'));
        } catch (Throwable t) {
            return "0";
        }
    }

    private Log() { }

    static void init(Path outDir) {
        dir = outDir;
    }

    static Path dir() {
        return dir;
    }

    static void to(String file, String line) {
        to(file, line, System.currentTimeMillis());
    }

    /** @param when when the line happened, which differs from now for lines that were held back. */
    static synchronized void to(String file, String line, long when) {
        if (dir == null) return;
        if (PER_GAME.contains(file)) file = file.replace(".log", "." + PID + ".log");
        try {
            Path path = dir.resolve(file);
            // A log may live in a subdirectory.
            Path parent = path.getParent();
            if (parent != null && !Files.isDirectory(parent)) Files.createDirectories(parent);
            if (++writes % CHECK_EVERY == 0) roll(path);
            String stamped = TS.format(new Date(when)) + "  " + line + System.lineSeparator();
            Files.write(path, stamped.getBytes("UTF-8"),
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (Throwable ignored) {
            // A logging failure must never reach the game.
        }
    }

    private static void roll(Path path) {
        try {
            if (!Files.exists(path) || Files.size(path) <= MAX_BYTES) return;
            Path archive = dir.resolve(ARCHIVE);
            Files.createDirectories(archive);
            String name = path.getFileName().toString();
            String stamp = new SimpleDateFormat("yyyyMMdd-HHmmss").format(new Date());
            String base = name.endsWith(".log") ? name.substring(0, name.length() - 4) : name;
            final Path full = archive.resolve(base + "." + stamp + ".log");
            Files.move(path, full);                 // quick; the new lines start a fresh file
            Thread t = new Thread(new Runnable() {
                public void run() {
                    compress(full);
                }
            }, "mwagent-log-archive");
            t.setDaemon(true);
            t.setPriority(Thread.MIN_PRIORITY);
            t.start();
        } catch (Throwable ignored) { }
    }

    /** full -> full.gz, then the oldest archives go while the archive is over its limit. */
    private static void compress(Path full) {
        Path gz = full.resolveSibling(full.getFileName() + ".gz");
        try {
            java.io.InputStream in = Files.newInputStream(full);
            java.io.OutputStream out = new java.util.zip.GZIPOutputStream(Files.newOutputStream(gz), 1 << 16);
            try {
                byte[] buf = new byte[1 << 16];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            } finally {
                in.close();
                out.close();
            }
            Files.delete(full);
        } catch (Throwable t) {
            try {
                Files.deleteIfExists(gz);          // keep the plain file rather than half a .gz
            } catch (Throwable ignored) { }
            return;
        }
        try {
            java.util.List<Path> all = new java.util.ArrayList<Path>();
            long total = 0;
            java.nio.file.DirectoryStream<Path> ds = Files.newDirectoryStream(full.getParent(), "*.gz");
            try {
                for (Path p : ds) {
                    all.add(p);
                    total += Files.size(p);
                }
            } finally {
                ds.close();
            }
            java.util.Collections.sort(all, new java.util.Comparator<Path>() {
                public int compare(Path a, Path b) {
                    try {
                        return Files.getLastModifiedTime(a).compareTo(Files.getLastModifiedTime(b));
                    } catch (java.io.IOException e) {
                        return 0;
                    }
                }
            });
            for (int i = 0; i < all.size() - 1 && total > ARCHIVE_LIMIT; i++) {
                total -= Files.size(all.get(i));
                Files.delete(all.get(i));
            }
        } catch (Throwable ignored) { }
    }

    static void info(String line) {
        to("agent.log", line);
    }

    static void error(String what, Throwable t) {
        Health.note(what, t);
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));
        to("agent.log", "ERROR " + what + ": " + sw);
    }
}
