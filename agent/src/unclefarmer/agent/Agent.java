package unclefarmer.agent;

import java.net.URI;
import java.nio.file.Path;
import java.nio.file.Paths;

/** What every part of the agent shares: its package name, the tap thread and its output folder. */
public final class Agent {

    private Agent() { }

    /** This build's package. Anything that names "us" (the netty tap, threads) uses this. */
    static final String PKG = Agent.class.getName().substring(0, Agent.class.getName().lastIndexOf('.'));

    static void start(final Runnable hunter) {
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    hunter.run();
                } catch (Throwable t) {
                    Log.error("hunter", t);
                }
            }
        }, "unclefarmer-hunter");
        t.setDaemon(true);
        t.setPriority(Thread.MIN_PRIORITY);
        t.start();
    }

    /** Explicit argument wins; otherwise a folder beside the jar. */
    static Path outputDir(String args, String folder) {
        if (args != null && !args.trim().isEmpty()) return Paths.get(args.trim());
        try {
            URI jar = Agent.class.getProtectionDomain().getCodeSource().getLocation().toURI();
            return Paths.get(jar).getParent().resolve(folder);
        } catch (Throwable t) {
            return Paths.get(System.getProperty("user.dir"), folder);
        }
    }
}
