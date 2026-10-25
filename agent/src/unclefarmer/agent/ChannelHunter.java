package unclefarmer.agent;

import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Finds the game's netty Channel and splices a passive tap into its pipeline.
 *
 * The Channel is not reachable through any public API, so every loaded application class is checked
 * for a static field that leads, within a couple of hops, to a live Channel. The connection only
 * exists after login, hence the polling.
 */
final class ChannelHunter implements Runnable {

    private static final int NODE_BUDGET = 40000;

    /** Full scans of every loaded class: often while nothing is connected, rarely once it is. */
    private static final long FULL_SCAN_IDLE_MS = 2000L;
    private static final long FULL_SCAN_LIVE_MS = 30000L;

    /**
     * Checks of just the fields that led to a Channel before. Every login opens a new connection,
     * and the client sends the login and the character choice within a second or two of opening it;
     * a tap that arrives later misses which character is being played, and anything said in between.
     */
    private static final long QUICK_IDLE_MS = 50L;
    private static final long QUICK_LIVE_MS = 1000L;

    private final Instrumentation inst;
    private final Set<Object> tapped =
            Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
    /** Static fields a Channel has been reached from; the game reuses them for each new connection. */
    private final Set<Field> roots = new LinkedHashSet<Field>();
    private Field currentRoot;
    private Class<?> channelClass;
    private int nodes;

    ChannelHunter(Instrumentation inst) {
        this.inst = inst;
    }

    @Override
    public void run() {
        boolean announced = false;
        long lastFull = 0;
        while (true) {
            boolean live = false;
            try {
                if (channelClass == null) channelClass = findLoaded("io.netty.channel.Channel");
                if (channelClass != null) {
                    if (!announced) {
                        Log.info("netty is loaded, scanning for the Channel");
                        announced = true;
                    }
                    if (!roots.isEmpty()) quickScan();
                    live = pruneAndCheckLive();
                    long now = System.currentTimeMillis();
                    if (now - lastFull >= (live ? FULL_SCAN_LIVE_MS : FULL_SCAN_IDLE_MS)) {
                        scan();
                        lastFull = now;
                        live = pruneAndCheckLive();
                    }
                }
            } catch (Throwable t) {
                Log.error("hunt", t);
            }
            try {
                Thread.sleep(live ? QUICK_LIVE_MS : QUICK_IDLE_MS);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    /**
     * Forgets closed channels and reports whether the game connection is up. The voice channel is
     * UDP and does not count: it can outlive a logout, and it is not where logins happen.
     */
    private boolean pruneAndCheckLive() {
        boolean live = false;
        for (Iterator<Object> it = tapped.iterator(); it.hasNext(); ) {
            Object channel = it.next();
            if (!isActive(channel)) {
                it.remove();
            } else if (channel.getClass().getName().indexOf("Datagram") < 0) {
                live = true;
            }
        }
        return live;
    }

    private void quickScan() {
        nodes = 0;
        Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
        for (Field f : roots) {
            try {
                currentRoot = f;
                walk(f.get(null), 0, seen);
            } catch (Throwable ignored) { }
        }
    }

    private Class<?> findLoaded(String name) {
        for (Class<?> c : inst.getAllLoadedClasses()) {
            if (name.equals(c.getName())) return c;
        }
        return null;
    }

    private void scan() {
        nodes = 0;
        Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
        for (Class<?> c : inst.getAllLoadedClasses()) {
            if (c.getClassLoader() == null || !interesting(c.getName())) continue;
            Field[] fields;
            try {
                fields = c.getDeclaredFields();
            } catch (Throwable t) {
                continue;
            }
            for (Field f : fields) {
                if (!Modifier.isStatic(f.getModifiers()) || f.getType().isPrimitive()) continue;
                try {
                    f.setAccessible(true);
                    currentRoot = f;
                    walk(f.get(null), 0, seen);
                } catch (Throwable ignored) {
                    // Unreadable field; the next one may still lead somewhere.
                }
            }
        }
    }

    private static boolean interesting(String name) {
        return !name.startsWith("java.") && !name.startsWith("sun.") && !name.startsWith("jdk.")
                && !name.startsWith("javax.") && !name.startsWith("com.sun.")
                && !name.startsWith("javafx.") && !name.startsWith(Agent.PKG + ".")
                && !name.startsWith("io.netty.");
    }

    /** Depth-limited search for a live Channel reachable from a static field. */
    private void walk(Object o, int depth, Set<Object> seen) {
        if (o == null || depth > 2 || ++nodes > NODE_BUDGET) return;
        if (!seen.add(o)) return;

        if (channelClass.isInstance(o)) {
            if (!tapped.contains(o) && isActive(o) && attach(o)) {
                tapped.add(o);
                if (currentRoot != null) roots.add(currentRoot);
            }
            return;
        }

        Class<?> c = o.getClass();
        if (c.isArray() || isSimple(c)) return;

        if (o instanceof Collection) {
            for (Object e : (Collection<?>) o) walk(e, depth + 1, seen);
            return;
        }
        if (o instanceof Map) {
            for (Object e : ((Map<?, ?>) o).values()) walk(e, depth + 1, seen);
            return;
        }
        if (!interesting(c.getName())) return;

        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            Field[] fields;
            try {
                fields = k.getDeclaredFields();
            } catch (Throwable t) {
                return;
            }
            for (Field f : fields) {
                if (Modifier.isStatic(f.getModifiers()) || f.getType().isPrimitive()) continue;
                try {
                    f.setAccessible(true);
                    walk(f.get(o), depth + 1, seen);
                } catch (Throwable ignored) { }
            }
        }
    }

    private static boolean isSimple(Class<?> c) {
        return c.isPrimitive() || c.isEnum() || c == String.class || c == Class.class
                || Number.class.isAssignableFrom(c) || c == Boolean.class || c == Character.class;
    }

    private boolean isActive(Object channel) {
        try {
            Method m = channel.getClass().getMethod("isActive");
            m.setAccessible(true);
            return Boolean.TRUE.equals(m.invoke(channel));
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * Splices the tap in just before the last handler: the one position where messages are fully
     * decoded but not yet consumed. Earlier positions carry only ciphertext, since the first
     * handler in the chain is what decrypts.
     */
    private boolean attach(Object channel) {
        try {
            ClassLoader cl = channel.getClass().getClassLoader();
            Object pipeline = channel.getClass().getMethod("pipeline").invoke(channel);
            Class<?> pipelineClass = pipeline.getClass();

            Map<?, ?> handlers = (Map<?, ?>) pipelineClass.getMethod("toMap").invoke(pipeline);
            Log.info("pipeline for " + channel + ":");
            List<String> names = new ArrayList<String>();
            for (Map.Entry<?, ?> e : handlers.entrySet()) {
                Object handler = e.getValue();
                Log.info("    " + e.getKey() + "  ->  "
                        + (handler == null ? "null" : handler.getClass().getName()));
                names.add(String.valueOf(e.getKey()));
            }

            Class<?> inbound = Class.forName("io.netty.channel.ChannelInboundHandler", false, cl);
            Class<?> outbound = Class.forName("io.netty.channel.ChannelOutboundHandler", false, cl);
            Class<?> handlerClass = Class.forName("io.netty.channel.ChannelHandler", false, cl);

            if (names.isEmpty()) {
                Object tap = Proxy.newProxyInstance(cl, new Class<?>[]{inbound, outbound},
                        new PipeTap("head"));
                pipelineClass.getMethod("addFirst", String.class, handlerClass)
                        .invoke(pipeline, Agent.PKG, tap);
                Log.info("tap installed at the head (empty pipeline)");
            } else {
                String last = names.get(names.size() - 1);
                Object tap = Proxy.newProxyInstance(cl, new Class<?>[]{inbound, outbound},
                        new PipeTap("before " + last));
                pipelineClass.getMethod("addBefore", String.class, String.class, handlerClass)
                        .invoke(pipeline, last, Agent.PKG, tap);
                Log.info("tap installed before " + last);
            }
            return true;
        } catch (Throwable t) {
            Log.error("attach", t);
            return false;
        }
    }
}
