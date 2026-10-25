package unclefarmer.agent;

import java.lang.reflect.Array;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Collection;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Map;
import java.util.Set;

/**
 * Renders arbitrary objects as text. The game's packet classes are obfuscated and mostly have no
 * useful toString(), so field values are read reflectively instead.
 */
final class Describe {

    private static final int MAX_DEPTH = 3;
    private static final int MAX_ITEMS = 24;
    private static final int MAX_BYTES = 8192;

    private Describe() { }

    static String of(Object o) {
        StringBuilder sb = new StringBuilder();
        Set<Object> seen = Collections.newSetFromMap(new IdentityHashMap<Object, Boolean>());
        try {
            render(o, sb, 0, seen);
        } catch (Throwable t) {
            sb.append("<describe failed: ").append(t).append('>');
        }
        return sb.toString();
    }

    private static void render(Object o, StringBuilder sb, int depth, Set<Object> seen) {
        if (o == null) { sb.append("null"); return; }

        Class<?> c = o.getClass();
        if (isSimple(c)) { sb.append(simple(o)); return; }
        if (isByteBuf(c)) { sb.append(byteBuf(o)); return; }
        if (!seen.add(o)) { sb.append("<cycle ").append(c.getSimpleName()).append('>'); return; }
        if (depth >= MAX_DEPTH) { sb.append('<').append(c.getName()).append('>'); return; }

        if (c.isArray() && c.getComponentType() == byte.class) {
            sb.append(bytes((byte[]) o));
            return;
        }

        if (c.isArray()) {
            int n = Array.getLength(o);
            sb.append(c.getComponentType().getSimpleName()).append('[').append(n).append("]{");
            for (int i = 0; i < Math.min(n, MAX_ITEMS); i++) {
                if (i > 0) sb.append(", ");
                render(Array.get(o, i), sb, depth + 1, seen);
            }
            if (n > MAX_ITEMS) sb.append(", ...");
            sb.append('}');
            return;
        }

        if (o instanceof Collection) {
            Collection<?> col = (Collection<?>) o;
            sb.append(c.getSimpleName()).append('[').append(col.size()).append("]{");
            int i = 0;
            for (Object e : col) {
                if (i >= MAX_ITEMS) { sb.append(", ..."); break; }
                if (i > 0) sb.append(", ");
                render(e, sb, depth + 1, seen);
                i++;
            }
            sb.append('}');
            return;
        }

        if (o instanceof Map) {
            Map<?, ?> map = (Map<?, ?>) o;
            sb.append(c.getSimpleName()).append('[').append(map.size()).append("]{");
            int i = 0;
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (i >= MAX_ITEMS) { sb.append(", ..."); break; }
                if (i > 0) sb.append(", ");
                render(e.getKey(), sb, depth + 1, seen);
                sb.append('=');
                render(e.getValue(), sb, depth + 1, seen);
                i++;
            }
            sb.append('}');
            return;
        }

        sb.append(c.getName()).append('{');
        boolean first = true;
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            Field[] fields;
            try { fields = k.getDeclaredFields(); } catch (Throwable t) { break; }
            for (Field f : fields) {
                if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
                Object v;
                try {
                    f.setAccessible(true);
                    v = f.get(o);
                } catch (Throwable t) {
                    continue;
                }
                if (!first) sb.append(", ");
                first = false;
                sb.append(f.getName()).append('=');
                render(v, sb, depth + 1, seen);
            }
        }
        sb.append('}');
    }

    private static boolean isSimple(Class<?> c) {
        return c.isPrimitive() || c.isEnum() || c == String.class || c == Class.class
                || Number.class.isAssignableFrom(c) || c == Boolean.class || c == Character.class;
    }

    private static String simple(Object o) {
        if (o instanceof String) {
            String s = (String) o;
            return '"' + s.replace("\\", "\\\\").replace("\n", "\n").replace("\r", "\r")
                    .replace("\t", "\t").replace("\"", "\\\"") + '"';
        }
        return String.valueOf(o);
    }

    /**
     * Decoded game messages arrive as byte[]: a leading opcode, then a payload that is usually
     * delimited ASCII but sometimes packed binary. The hex is the authoritative reading; the ascii
     * one is only a convenience, so it is printed only when the message holds a word (four letters
     * in a row). Movement and position messages, most of the log, are binary and printed as hex only.
     */
    private static String bytes(byte[] b) {
        int take = Math.min(b.length, MAX_BYTES);
        StringBuilder ascii = new StringBuilder();
        StringBuilder hex = new StringBuilder();
        int run = 0;
        boolean words = false;
        for (int i = 0; i < take; i++) {
            int u = b[i] & 0xFF;
            ascii.append(u >= 0x20 && u < 0x7F ? (char) u : '.');
            hex.append(HEX[u >> 4]).append(HEX[u & 15]);
            run = (u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z') ? run + 1 : 0;
            if (run >= 4) words = true;
        }
        String cut = b.length > take ? "..." : "";
        return "byte[" + b.length + "]"
                + (b.length > 0 ? " op=" + (b[0] & 0xFF) : "")
                + (words ? " ascii=\"" + ascii + cut + "\"" : "") + " hex=" + hex + cut;
    }

    private static final char[] HEX = "0123456789abcdef".toCharArray();

    /** True for io.netty.buffer.ByteBuf without linking against netty. */
    private static boolean isByteBuf(Class<?> c) {
        for (Class<?> k = c; k != null; k = k.getSuperclass()) {
            if (k.getName().equals("io.netty.buffer.ByteBuf")) return true;
            for (Class<?> i : k.getInterfaces()) {
                if (i.getName().equals("io.netty.buffer.ByteBuf")) return true;
            }
        }
        return false;
    }

    /**
     * Dumps a ByteBuf's readable region without consuming it: readerIndex/getByte leave the
     * buffer untouched, which matters because the game still has to parse it afterwards.
     */
    private static String byteBuf(Object buf) {
        try {
            Class<?> c = buf.getClass();
            Method readable = c.getMethod("readableBytes");
            Method readerIndex = c.getMethod("readerIndex");
            Method getByte = c.getMethod("getByte", int.class);
            int n = (Integer) readable.invoke(buf);
            int start = (Integer) readerIndex.invoke(buf);
            int take = Math.min(n, MAX_BYTES);
            byte[] b = new byte[take];
            for (int i = 0; i < take; i++) {
                b[i] = (Byte) getByte.invoke(buf, start + i);
            }
            StringBuilder ascii = new StringBuilder();
            for (byte x : b) {
                int u = x & 0xFF;
                ascii.append(u >= 0x20 && u < 0x7F ? (char) u : '.');
            }
            StringBuilder hex = new StringBuilder();
            for (int i = 0; i < Math.min(take, 64); i++) {
                hex.append(String.format("%02x", b[i] & 0xFF));
            }
            return "ByteBuf[" + n + "] ascii=\"" + ascii + (n > take ? "...\"" : "\"")
                    + " hex=" + hex + (take > 64 ? "..." : "");
        } catch (Throwable t) {
            return "<ByteBuf unreadable: " + t + ">";
        }
    }
}
