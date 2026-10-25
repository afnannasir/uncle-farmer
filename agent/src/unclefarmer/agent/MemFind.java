package unclefarmer.agent;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Deque;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Finds where the game keeps a value you can see on screen: "memfind 50000" lists every field
 * reachable from the world object whose value is 50000, as a path like world.F.c or
 * world.i[1234].r. Run it again after the value changes and keep the paths that follow it.
 *
 * A text needle ("memfind 'Valley's Vista'") matches String fields containing it, ignoring case.
 *
 * Read-only. It only walks game objects (classes in package "game") and the collections and arrays
 * they hold, plus the static fields of every game class it meets, so it never wanders into AWT,
 * threads or netty. Breadth-first with a depth and node budget, so it stays quick.
 */
final class MemFind {

    private static final int MAX_DEPTH = 6;
    private static final int MAX_NODES = 3000000;

    /**
     * world.N is the map-data cache keyed by map id (live: N[1041] is central plains). It is most of
     * the heap and full of small ints, so it drowns any numeric search and used to exhaust the budget
     * before the rest of the world was reached. Never a place a character stat lives.
     */
    private static final String SKIP_PATH = "world.N";
    private static final int MAX_HITS = 60;
    private static final int MAX_ELEMENTS = 5000;

    private MemFind() { }

    private static final class Node {
        final Object value;
        final String path;
        final int depth;

        Node(Object value, String path, int depth) {
            this.value = value;
            this.path = path;
            this.depth = depth;
        }
    }

    static String find(String needleText) {
        String text = needleText.trim();
        if (text.length() >= 2 && (text.charAt(0) == '\'' || text.charAt(0) == '"')
                && text.charAt(text.length() - 1) == text.charAt(0)) {
            text = text.substring(1, text.length() - 1);
        }
        if (text.isEmpty()) return "usage: memfind <number> | memfind '<text>'";

        Long number = null;
        if (!needleText.trim().startsWith("'") && !needleText.trim().startsWith("\"")) {
            try {
                number = Long.valueOf(Long.parseLong(text));
            } catch (NumberFormatException ignored) {
                // a text search
            }
        }
        String lower = text.toLowerCase();

        Object world;
        try {
            world = Memory.world();
        } catch (Memory.Unavailable u) {
            return "unavailable: " + u.getMessage();
        }

        List<String> hits = new ArrayList<String>();
        Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
        Set<Class<?>> staticsDone = Collections.newSetFromMap(new IdentityHashMap<Class<?>, Boolean>());
        Deque<Node> queue = new ArrayDeque<Node>();
        queue.add(new Node(world, "world", 0));
        seen.add(world);
        int nodes = 0;

        while (!queue.isEmpty() && hits.size() < MAX_HITS && nodes < MAX_NODES) {
            Node n = queue.poll();
            nodes++;
            Object o = n.value;
            Class<?> c = o.getClass();

            try {
                if (c.isArray()) {
                    int len = Math.min(Array.getLength(o), MAX_ELEMENTS);
                    Class<?> component = c.getComponentType();
                    for (int i = 0; i < len; i++) {
                        Object v = Array.get(o, i);
                        visit(v, component.isPrimitive() ? component : null,
                                n.path + "[" + i + "]", n.depth, number, lower, hits, seen, queue);
                    }
                } else if (o instanceof Map) {
                    int i = 0;
                    for (Object e : new ArrayList<Object>(((Map<?, ?>) o).entrySet())) {
                        if (i++ >= MAX_ELEMENTS) break;
                        Map.Entry<?, ?> entry = (Map.Entry<?, ?>) e;
                        visit(entry.getValue(), null, n.path + "[" + entry.getKey() + "]", n.depth,
                                number, lower, hits, seen, queue);
                    }
                } else if (o instanceof Collection) {
                    int i = 0;
                    for (Object v : new ArrayList<Object>((Collection<?>) o)) {
                        if (i >= MAX_ELEMENTS) break;
                        visit(v, null, n.path + "[" + i + "]", n.depth, number, lower, hits, seen,
                                queue);
                        i++;
                    }
                } else {
                    for (Class<?> k = c; k != null && isGame(k); k = k.getSuperclass()) {
                        if (staticsDone.add(k)) {
                            fields(k, null, k.getName(), 0, number, lower, hits, seen, queue);
                        }
                        fields(k, o, n.path, n.depth, number, lower, hits, seen, queue);
                    }
                }
            } catch (Throwable t) {
                // The game changed something under us; skip this node.
            }
        }

        StringBuilder sb = new StringBuilder();
        sb.append(hits.size()).append(" hit(s) for ").append(number != null ? number : "'" + text + "'")
                .append(" (searched ").append(nodes).append(" objects")
                .append(queue.isEmpty() ? ", complete)" : ", STOPPED EARLY: limit reached)");
        for (String h : hits) sb.append(" | ").append(h);
        return sb.toString();
    }

    /** Static fields when target is null, instance fields otherwise. */
    private static void fields(Class<?> k, Object target, String path, int depth, Long number,
                               String lower, List<String> hits, Set<Object> seen, Deque<Node> queue)
            throws IllegalAccessException {
        for (Field f : k.getDeclaredFields()) {
            if (Modifier.isStatic(f.getModifiers()) != (target == null) || f.isSynthetic()) continue;
            try {
                f.setAccessible(true);
            } catch (Throwable t) {
                continue;
            }
            Class<?> type = f.getType();
            Object v = f.get(target);
            visit(v, type.isPrimitive() ? type : null, path + "." + f.getName(), depth, number, lower,
                    hits, seen, queue);
        }
    }

    private static void visit(Object v, Class<?> primitive, String path, int depth, Long number,
                              String lower, List<String> hits, Set<Object> seen, Deque<Node> queue) {
        if (v == null || hits.size() >= MAX_HITS) return;

        if (v instanceof Number && !(v instanceof Float) && !(v instanceof Double)) {
            if (number != null && ((Number) v).longValue() == number.longValue()) {
                hits.add(path + " = " + v + " (" + (primitive != null ? primitive.getName()
                        : v.getClass().getSimpleName()) + ")");
            }
            return;
        }
        if (v instanceof Float || v instanceof Double) {
            if (number != null && ((Number) v).doubleValue() == number.doubleValue()) {
                hits.add(path + " = " + v);
            }
            return;
        }
        if (v instanceof String) {
            if (number == null && ((String) v).toLowerCase().contains(lower)) {
                String s = (String) v;
                hits.add(path + " = \"" + (s.length() > 120 ? s.substring(0, 120) + "..." : s) + "\"");
            }
            return;
        }
        if (v instanceof Boolean || v instanceof Character) return;
        if (depth + 1 > MAX_DEPTH || path.equals(SKIP_PATH)) return;

        Class<?> c = v.getClass();
        boolean container = c.isArray() || v instanceof Map || v instanceof Collection;
        if (!container && !isGame(c)) return;
        if (seen.add(v)) queue.add(new Node(v, path, depth + 1));
    }

    private static boolean isGame(Class<?> c) {
        String name = c.getName();
        return name.startsWith("game.") && !name.startsWith("game.$Proxy");
    }
}
