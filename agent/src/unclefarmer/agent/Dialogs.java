package unclefarmer.agent;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;

/**
 * Reads the client's open dialogs (NPC menus, confirmations) and the target the keys selected.
 * Read-only like {@link Memory}: the bot moves through a dialog with the game's own keys.
 *
 * Found with javap on 2026-10-04:
 *
 *   world .U  ArrayList<game.y>   open dialogs, the last one on top; empty = no dialog.
 *              While it is non-empty, game.Win's key handler sends W/S to rows (game.i.e/f),
 *              A/D to columns (c/d), Enter to "choose" (game.i.a(Win)), Esc to close (j).
 *   game.y    .b int kind, .c int, .d int selected ROW, .e int selected COLUMN,
 *             .g ArrayList<ArrayList<game.z>> rows of entries. y.c() = g[d][e].
 *   game.z    .a int flags, .e String text (shown), .f .g .h .i Strings, .j String[] options,
 *             .d int[] the command sent with OUT op=19 when chosen, .k short.
 *             Text may hold {0,n}/{1,n} templates; z.a() fills them (pure string code).
 *   world .av game.B   the selected target (game.i.o, the direction target keys, sets it).
 *             Enter with no dialog open talks to it: if it is within 5 tiles and in line of
 *             sight the client sends OUT op=30 <id> 0, and the server answers with a dialog.
 *   world .G  int      special local targets 1..10 (team view); o() resets it to 0.
 *
 * game.Trans.b(String) is the client's own Chinese -> English lookup (a static map read).
 */
final class Dialogs {

    static final String W_DIALOGS = "U";
    static final String W_SELECTED = "av";
    static final String D_KIND = "b";
    static final String D_ROW = "d";
    static final String D_COL = "e";
    static final String D_ROWS = "g";
    static final String Z_FLAGS = "a";
    static final String Z_TEXT = "e";
    static final String[] Z_EXTRA = {"f", "g", "h", "i"};
    static final String Z_OPTIONS = "j";
    static final String Z_COMMAND = "d";
    static final String E_ID = "e";
    static final String E_NAME = "h";
    static final String E_X = "i";
    static final String E_Y = "j";

    private Dialogs() { }

    /** {"open":n,"top":{...}|null,"selected":{...}|null} */
    static String read() {
        try {
            Object world = Memory.world();
            loader = world.getClass().getClassLoader();
            StringBuilder sb = new StringBuilder(512);
            List<Object> open = copy(list(world, W_DIALOGS));
            sb.append("{\"ok\":true,\"open\":").append(open.size());
            sb.append(",\"top\":");
            if (open.isEmpty()) sb.append("null");
            else dialog(open.get(open.size() - 1), sb);
            sb.append(",\"selected\":");
            selected(world, sb);
            return sb.append('}').toString();
        } catch (Memory.Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            Log.error("dialogs", t);
            return error("failed: " + t);
        }
    }

    /** The target the keys selected: {"id":..,"name":..,"x":..,"y":..} or null. */
    private static void selected(Object world, StringBuilder sb) throws Memory.Unavailable {
        Field f = field(world.getClass(), W_SELECTED);
        if (f == null || f.getType().isPrimitive()) throw moved(world, W_SELECTED);
        Object e = getOrNull(f, world);
        if (e == null) {
            sb.append("null");
            return;
        }
        sb.append("{\"id\":").append(intField(e, E_ID));
        sb.append(",\"name\":");
        str(sb, (String) ref(e, E_NAME));
        sb.append(",\"x\":").append(tiles(intField(e, E_X)));
        sb.append(",\"y\":").append(tiles(intField(e, E_Y)));
        sb.append('}');
    }

    private static void dialog(Object y, StringBuilder sb) throws Memory.Unavailable {
        sb.append("{\"kind\":").append(intField(y, D_KIND));
        sb.append(",\"row\":").append(intField(y, D_ROW));
        sb.append(",\"col\":").append(intField(y, D_COL));
        sb.append(",\"rows\":[");
        List<Object> rows = copy(list(y, D_ROWS));
        for (int r = 0; r < rows.size(); r++) {
            if (r > 0) sb.append(',');
            sb.append('[');
            Object row = rows.get(r);
            List<Object> cells = row instanceof ArrayList ? copy((ArrayList<?>) row) : new ArrayList<Object>();
            for (int c = 0; c < cells.size(); c++) {
                if (c > 0) sb.append(',');
                entry(cells.get(c), sb);
            }
            sb.append(']');
        }
        sb.append("]}");
    }

    private static void entry(Object z, StringBuilder sb) throws Memory.Unavailable {
        if (z == null) {
            sb.append("null");
            return;
        }
        String raw = (String) ref(z, Z_TEXT);
        sb.append("{\"flags\":").append(intField(z, Z_FLAGS));
        sb.append(",\"raw\":");
        str(sb, raw);
        sb.append(",\"text\":");
        str(sb, english(filled(z, raw)));
        for (String extra : Z_EXTRA) {
            String s = (String) ref(z, extra);
            if (s != null && !s.isEmpty()) {
                sb.append(",\"").append(extra).append("\":");
                str(sb, english(s));
            }
        }
        Object opts = ref(z, Z_OPTIONS);
        if (opts instanceof String[]) {
            sb.append(",\"options\":[");
            String[] o = (String[]) opts;
            for (int i = 0; i < o.length; i++) {
                if (i > 0) sb.append(',');
                str(sb, english(o[i]));
            }
            sb.append(']');
        }
        Object cmd = ref(z, Z_COMMAND);
        if (cmd instanceof int[]) {
            sb.append(",\"cmd\":[");
            int[] d = (int[]) cmd;
            for (int i = 0; i < d.length; i++) {
                if (i > 0) sb.append(',');
                sb.append(d[i]);
            }
            sb.append(']');
        }
        sb.append('}');
    }

    // ---------------------------------------------------------------- the client's text helpers

    private static volatile ClassLoader loader;
    private static volatile Method fill;
    private static volatile Method trans;
    private static volatile boolean transLooked;

    /** z.a(): the entry's text with its {0,n} templates filled in; the raw text if that fails. */
    private static String filled(Object z, String raw) {
        try {
            Method m = fill;
            if (m == null) {
                m = z.getClass().getDeclaredMethod("a", String.class);
                if (m.getReturnType() != String.class) return raw;
                m.setAccessible(true);
                fill = m;
            }
            Object s = m.invoke(z, raw);
            return s instanceof String ? (String) s : raw;
        } catch (Throwable t) {
            return raw;
        }
    }

    /** game.Trans.b(String): the client's own translation of a Chinese string, or the string. */
    private static String english(String s) {
        if (s == null || s.isEmpty()) return s;
        try {
            Method m = trans;
            if (m == null && !transLooked) {
                transLooked = true;
                Class<?> c = Class.forName("game.Trans", false, loader);
                for (Method k : c.getDeclaredMethods()) {
                    if (k.getName().equals("b") && Modifier.isStatic(k.getModifiers())
                            && k.getReturnType() == String.class
                            && k.getParameterTypes().length == 1 && k.getParameterTypes()[0] == String.class) {
                        k.setAccessible(true);
                        trans = m = k;
                    }
                }
            }
            if (m == null) return s;
            Object t = m.invoke(null, s);
            return t instanceof String && !((String) t).isEmpty() ? (String) t : s;
        } catch (Throwable t) {
            return s;
        }
    }

    // ---------------------------------------------------------------- reflection (as in Memory)

    private static Field field(Class<?> c, String name) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Field f = k.getDeclaredField(name);
                f.setAccessible(true);
                return f;
            } catch (NoSuchFieldException ignored) {
                // superclass
            }
        }
        return null;
    }

    private static Object getOrNull(Field f, Object o) {
        try {
            return f.get(o);
        } catch (IllegalAccessException e) {
            return null;
        }
    }

    private static Object ref(Object o, String name) throws Memory.Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null || f.getType().isPrimitive()) throw moved(o, name);
        return getOrNull(f, o);
    }

    private static ArrayList<?> list(Object o, String name) throws Memory.Unavailable {
        Object v = ref(o, name);
        if (v == null) return new ArrayList<Object>();
        if (!(v instanceof ArrayList)) throw moved(o, name);
        return (ArrayList<?>) v;
    }

    private static int intField(Object o, String name) throws Memory.Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null || f.getType() != int.class) throw moved(o, name);
        try {
            return f.getInt(o);
        } catch (IllegalAccessException e) {
            throw moved(o, name);
        }
    }

    private static List<Object> copy(ArrayList<?> list) throws Memory.Unavailable {
        for (int attempt = 0; attempt < 5; attempt++) {
            try {
                return new ArrayList<Object>(list);
            } catch (RuntimeException e) {
                Thread.yield();
            }
        }
        throw new Memory.Unavailable("busy: the game kept changing a dialog");
    }

    private static Memory.Unavailable moved(Object o, String name) {
        return new Memory.Unavailable("fields moved: " + o.getClass().getName() + "." + name
                + " is missing or has the wrong type (game patched?)");
    }

    private static String tiles(int raw) {
        return String.format(java.util.Locale.ROOT, "%.2f", raw / 1000.0);
    }

    private static void str(StringBuilder sb, String s) {
        if (s == null) sb.append("null");
        else sb.append('"').append(Protocol.jsonEscape(s)).append('"');
    }

    private static String error(String why) {
        StringBuilder sb = new StringBuilder("{\"ok\":false,\"error\":");
        str(sb, why);
        return sb.append('}').toString();
    }
}
