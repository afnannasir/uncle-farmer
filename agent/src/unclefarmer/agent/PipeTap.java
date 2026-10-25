package unclefarmer.agent;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.util.Arrays;

/**
 * A netty ChannelHandler built with java.lang.reflect.Proxy, so the agent needs no compile-time
 * dependency on netty (and keeps working when the game's netty version changes). Every message is
 * read into the World model and then forwarded unchanged, so the tap is invisible to the game.
 * Nothing is written to disk: no packet log, no chat log.
 */
final class PipeTap implements InvocationHandler {

    /** Where in the pipeline this tap sits, e.g. "before ch3". Prefixed to every logged line. */
    private final String label;

    PipeTap(String label) {
        this.label = label;
    }

    /** Inbound events travel head-to-tail and are re-fired as ctx.fireXxx(...). */
    private static final String[] INBOUND = {
            "channelRegistered", "channelUnregistered", "channelActive", "channelInactive",
            "channelRead", "channelReadComplete", "channelWritabilityChanged",
            "userEventTriggered", "exceptionCaught"
    };

    /** Outbound events travel tail-to-head and are re-issued as ctx.xxx(...). */
    private static final String[] OUTBOUND = {
            "bind", "connect", "disconnect", "close", "deregister", "read", "write", "flush"
    };

    @Override
    public Object invoke(Object proxy, Method m, Object[] args) throws Throwable {
        String name = m.getName();

        if (name.equals("hashCode") && empty(args)) return System.identityHashCode(proxy);
        if (name.equals("toString") && empty(args)) return Agent.PKG + ".PipeTap[" + label + "]";
        if (name.equals("equals") && args != null && args.length == 1) {
            return Boolean.valueOf(proxy == args[0]);
        }

        boolean ours = false;
        try {
            ours = record(name, args);
        } catch (Throwable t) {
            try {
                Log.error("tap " + name, t);
            } catch (Throwable ignored) { }
        }
        // A message the agent asked for itself (the sheet of `ask sheet`) ends here: read, and
        // not passed on, so the game opens no window for it.
        if (ours) return null;

        // handlerAdded/handlerRemoved terminate here; everything else is passed along.
        if (name.equals("handlerAdded") || name.equals("handlerRemoved")) return null;

        try {
            return forward(name, m, args);
        } catch (Throwable t) {
            // This handler runs inside the game's netty pipeline. An agent bug here must not
            // escape into the client: log it and let the pipeline carry on without this event.
            try {
                Log.error("tap forward " + name, t);
            } catch (Throwable ignored) { }
            return null;
        }
    }

    /** True for an inbound message that is the agent's alone (World.inbound). */
    private boolean record(String name, Object[] args) {
        if (args == null || args.length < 2 || !(args[1] instanceof byte[])) return false;
        if (name.equals("channelRead")) return World.inbound((byte[]) args[1]);
        if (name.equals("write")) World.outbound((byte[]) args[1]);
        return false;
    }

    private Object forward(String name, Method m, Object[] args) throws Throwable {
        if (args == null || args.length == 0) return null;

        Object ctx = args[0];
        Object[] rest = Arrays.copyOfRange(args, 1, args.length);

        String target;
        if (contains(INBOUND, name)) {
            target = "fire" + Character.toUpperCase(name.charAt(0)) + name.substring(1);
        } else if (contains(OUTBOUND, name)) {
            target = name;
        } else {
            return null;
        }

        Method fire = find(ctx.getClass(), target, rest.length);
        if (fire == null) {
            Log.info("no forward for " + name + " -> " + target + "/" + rest.length);
            return null;
        }
        fire.setAccessible(true);
        Object result = fire.invoke(ctx, rest);
        return m.getReturnType() == void.class ? null : result;
    }

    /** Matched by name and arity: the exact parameter types differ across netty versions. */
    private static Method find(Class<?> c, String name, int arity) {
        for (Method m : c.getMethods()) {
            if (m.getName().equals(name) && m.getParameterTypes().length == arity) return m;
        }
        return null;
    }

    private static boolean contains(String[] names, String name) {
        for (String n : names) {
            if (n.equals(name)) return true;
        }
        return false;
    }

    private static boolean empty(Object[] args) {
        return args == null || args.length == 0;
    }
}
