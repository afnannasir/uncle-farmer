package unclefarmer.agent;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.Collection;
import java.util.IdentityHashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Lets go of the connections the game leaves behind.
 *
 * Every login and every reconnect builds a new server connection (game.w; game.o's exceptionCaught
 * makes one after each drop), and every login a new voice socket (game.x), each on a NioEventLoopGroup
 * of its own. Nothing in the game ever shuts one of those groups down. A dropped connection keeps its
 * event loop thread and selectors for the rest of the game, and the old voice socket stays bound with
 * its thread running after game.x.a has moved on to the new one. A game that reconnects a dozen times
 * fills its 32-bit address space, and the next thread anything starts (the sound engine, a timer)
 * fails with "unable to create new native thread", which stops the game.
 *
 * ChannelHunter reports every channel it taps and every one it finds closed. Once a closed channel's
 * group has had no open channel on it for GRACE_MS, it is shut down gracefully (its tasks finish
 * first). A voice socket tapped before the game's current one is closed first: the game sends
 * voice only through the current one. Only the hunter's thread calls this class.
 */
final class DeadLinks {

    /** How long a closed connection's group is left before it is shut down. */
    static final long GRACE_MS = 5000L;

    /** Group -> when a channel of it was found closed. */
    private final Map<Object, Long> pending = new IdentityHashMap<Object, Long>();
    /** Channel -> its group, read while it was open (a closed channel still answers, but read early). */
    private final Map<Object, Object> groups = new IdentityHashMap<Object, Object>();
    /** Channel -> the order it was tapped in: the voice socket tapped last is the newest. */
    private final Map<Object, Long> order = new IdentityHashMap<Object, Long>();
    private long sequence;
    int voiceClosed;
    int groupsShut;

    void tapped(Object channel) {
        order.put(channel, ++sequence);
        Object group = groupOf(channel);
        if (group != null) groups.put(channel, group);
    }

    /** The channel was found closed: its group is shut down once nothing open still runs on it. */
    void closed(Object channel, long now) {
        order.remove(channel);
        Object group = groups.remove(channel);
        if (group == null) group = groupOf(channel);
        if (group != null && !pending.containsKey(group)) pending.put(group, now);
    }

    /**
     * Closes every open voice socket that was tapped before the game's current one. voice: the
     * channel game.x.a() answers, or null when it can't be read (nothing is closed then).
     */
    void closeOldVoice(Collection<Object> open, Object voice) {
        Long newest = voice == null ? null : order.get(voice);
        if (newest == null) return;
        for (Object c : open) {
            Long at = order.get(c);
            if (c == voice || at == null || at >= newest || !datagram(c) || !active(c)) continue;
            if (call(c, "close") != null || !active(c)) {
                voiceClosed++;
                Log.info("closed an old voice socket the game left open");
            }
        }
    }

    /** Shuts down every group that has had no open channel for GRACE_MS. open: the channels still tapped. */
    void reap(Collection<Object> open, long now) {
        for (Iterator<Map.Entry<Object, Long>> it = pending.entrySet().iterator(); it.hasNext(); ) {
            Map.Entry<Object, Long> e = it.next();
            Object group = e.getKey();
            if (inUse(group, open)) {
                it.remove();                // a live connection runs on it: it comes back when that one closes
                continue;
            }
            if (now - e.getValue() < GRACE_MS) continue;
            it.remove();
            if (Boolean.TRUE.equals(call(group, "isShuttingDown"))) continue;
            if (call(group, "shutdownGracefully") != null) {
                groupsShut++;
                Log.info("shut down the network threads of a closed connection (the game leaves them running)");
            }
        }
    }

    int pendingForTests() { return pending.size(); }

    private boolean inUse(Object group, Collection<Object> open) {
        List<Object> copy = new ArrayList<Object>(open);
        for (Object c : copy) {
            if (!active(c)) continue;
            Object g = groups.get(c);
            if (g == null) g = groupOf(c);
            if (g == group) return true;
        }
        return false;
    }

    static boolean datagram(Object channel) {
        return channel.getClass().getName().indexOf("Datagram") >= 0;
    }

    static boolean active(Object channel) {
        return Boolean.TRUE.equals(call(channel, "isActive"));
    }

    /** channel.eventLoop().parent(): the NioEventLoopGroup the game built for this connection. */
    static Object groupOf(Object channel) {
        Object loop = call(channel, "eventLoop");
        return loop == null ? null : call(loop, "parent");
    }

    /** A public no-argument method by name, or null when it is missing or fails. */
    static Object call(Object target, String name) {
        try {
            Method m = target.getClass().getMethod(name);
            m.setAccessible(true);
            return m.invoke(target);
        } catch (Throwable t) {
            return null;
        }
    }
}
