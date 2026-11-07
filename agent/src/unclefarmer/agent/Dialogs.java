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
 *
 * `pick` (agent 53) and `choose` (agent 55) are the things here that act. `pick` it chooses an entry of a verified shop or Shard
 * Market menu by its index (Menus); `choose` any menu's entry at the row and column the bot read,
 * when the menu still has the signature that reading gave it (Menus.signature). The game does the same itself when the server picks an entry
 * for it (game.N.e(byte[]): y.d = n, y.e = 0, then world.a(Win), the method Enter calls): the
 * checks, the cursor and the choice all happen under the world's lock, the one every dialog change
 * of the game's takes, so the menu can't change between the check and the choice.
 */
final class Dialogs {

    private static final long MENU_STEP_WAIT = 5L;
    private static final long MENU_STEP_TIMEOUT = 3000L;

    static final String W_DIALOGS = "U";
    static final String W_SELECTED = "av";
    static final String D_KIND = "b";
    static final String D_ROW = "d";
    static final String D_COL = "e";
    static final String D_OPTION = "f";
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

    /** `menu`: the open menu as Menus sees it (layout, verified, items by index). */
    static String menu() {
        try {
            Object world = Memory.world();
            loader = world.getClass().getClassLoader();
            List<Object> open = copy(list(world, W_DIALOGS));
            if (open.isEmpty()) return "{\"ok\":true,\"layout\":\"none\",\"verified\":false,\"items\":[]}";
            return Menus.describe(model(open.get(open.size() - 1)));
        } catch (Memory.Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            Log.error("menu", t);
            return error("failed: " + t);
        }
    }

    /** `pick <layout> <id> [buy|sell]`: choose the item by its verified index, or refuse with the
     * reason and touch nothing. */
    static String pick(String layout, int id, String action) {
        try {
            Object world = Memory.world();
            loader = world.getClass().getClassLoader();
            java.awt.Frame win = Memory.window();
            if (win == null) return "error: no game window";
            Method choose = chooseMethod(world.getClass(), win.getClass());
            if (choose == null) return "error: the game's choose method wasn't found";
            synchronized (world) {
                List<Object> open = copy(list(world, W_DIALOGS));
                if (open.isEmpty()) return "refused: no menu is open";
                Object y = open.get(open.size() - 1);
                int[] at;
                try {
                    at = Menus.place(model(y), layout, id, action);
                } catch (Menus.Refused r) {
                    return "refused: " + r.getMessage();
                }
                setInt(y, D_ROW, at[0]);
                setInt(y, D_COL, at[1]);
                setInt(y, D_OPTION, 0);
                choose.invoke(world, win);
                Log.info("ACTION menu " + layout + " row " + at[0] + " item " + id + (action == null ? "" : " " + action));
                return "picked " + layout + " " + at[0] + " " + at[1] + " " + id;
            }
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("pick", t);
            return "error: " + t;
        }
    }

    /** Complete one verified shard sale without controller round trips between its menu stages. */
    static String shardSell(int id, int count) {
        if (count <= 0) return "error: shardsell count must be positive";
        int broker;
        try {
            broker = selectedId();
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        }
        String chosen = pick(Menus.SHARD_MARKET, id, null);
        if (!chosen.startsWith("picked ")) return chosen;
        long until = System.currentTimeMillis() + MENU_STEP_TIMEOUT;
        while (true) {
            String sold = pick(Menus.SHARD_TRADE, id, "sell");
            if (sold.startsWith("picked ")) break;
            if (!waiting(sold)) return sold;
            if (!pause(until)) return "refused: the Shard Market did not open its sell menu";
        }
        while (true) {
            try {
                if (TextInput.open()) break;
            } catch (Memory.Unavailable u) {
                return "error: " + u.getMessage();
            }
            if (!pause(until)) return "refused: the Shard Market did not open its quantity box";
        }
        String entered = TextInput.number(Integer.toString(count));
        if (!entered.startsWith("entered")) return entered;
        while (true) {
            try {
                if (tradeFinished()) {
                    reopenMarket(broker, id);
                    return "sold " + id + " " + count;
                }
            } catch (Memory.Unavailable u) {
                return "error: " + u.getMessage();
            }
            if (!pause(until)) return "refused: the Shard Market did not finish the sale";
        }
    }

    /** Send one verified sale command from the currently verified Shard Market. */
    static String shardSellDirect(int id, int count) {
        if (count <= 0 || count > 2000) return "error: sharddirect count must be 1-2000";
        try {
            Object world = Memory.world();
            synchronized (world) {
                List<Object> open = copy(list(world, W_DIALOGS));
                if (open.isEmpty()) return "refused: no menu is open";
                Menus.Menu market = model(open.get(open.size() - 1));
                int[] at = Menus.place(market, Menus.SHARD_MARKET, id, null);
                Menus.Entry item = market.at(at[0], at[1]);
                int broker = selectedId(world);
                if (broker >= 0) return "refused: no Shard Broker is selected";
                String failed = Ask.send(shardPacket(broker, id, item.cmd[2], count), "direct shard sell");
                return failed == null ? "sent " + id + " " + count : failed;
            }
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Menus.Refused r) {
            return "refused: " + r.getMessage();
        } catch (Throwable t) {
            Log.error("direct shard sell", t);
            return "error: " + t;
        }
    }

    /** Validate a whole market plan first, then queue its already-observed sale commands in order. */
    static String shardSellDirectAll(int[] ids, int[] counts) {
        if (ids.length == 0 || ids.length != counts.length) return "error: sharddirectall needs item/count pairs";
        try {
            Object world = Memory.world();
            synchronized (world) {
                List<Object> open = copy(list(world, W_DIALOGS));
                if (open.isEmpty()) return "refused: no menu is open";
                Menus.Menu market = model(open.get(open.size() - 1));
                int broker = selectedId(world);
                if (broker >= 0) return "refused: no Shard Broker is selected";
                byte[][] packets = new byte[ids.length][];
                for (int i = 0; i < ids.length; i++) {
                    if (counts[i] <= 0 || counts[i] > 2000) return "error: sharddirect count must be 1-2000";
                    int[] at = Menus.place(market, Menus.SHARD_MARKET, ids[i], null);
                    Menus.Entry item = market.at(at[0], at[1]);
                    packets[i] = shardPacket(broker, ids[i], item.cmd[2], counts[i]);
                }
                for (byte[] packet : packets) {
                    String failed = Ask.send(packet, "direct shard sell");
                    if (failed != null) return failed;
                }
                return "sent all " + ids.length;
            }
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Menus.Refused r) {
            return "refused: " + r.getMessage();
        } catch (Throwable t) {
            Log.error("direct shard sell", t);
            return "error: " + t;
        }
    }

    /** OUT op=34: broker id, command kind 31, then the verified sale command. */
    static byte[] shardPacket(int broker, int item, int price, int count) {
        int[] command = {Menus.SELL, item, price, count};
        byte[] packet = new byte[6 + command.length * 4];
        packet[0] = 34;
        writeInt(packet, 1, broker);
        packet[5] = 31;
        for (int i = 0; i < command.length; i++) writeInt(packet, 6 + i * 4, command[i]);
        return packet;
    }

    private static void writeInt(byte[] packet, int offset, int value) {
        packet[offset] = (byte) (value >>> 24);
        packet[offset + 1] = (byte) (value >>> 16);
        packet[offset + 2] = (byte) (value >>> 8);
        packet[offset + 3] = (byte) value;
    }

    private static boolean waiting(String reply) {
        return reply.startsWith("refused: no menu is open") || reply.startsWith("refused: the open menu is ");
    }

    /** The client leaves the old dialog object around while the quantity's server reply is due;
     * wait for that trade menu to be replaced or closed before another command can use a menu. */
    private static boolean tradeFinished() throws Memory.Unavailable {
        Object world = Memory.world();
        synchronized (world) {
            List<Object> open = copy(list(world, W_DIALOGS));
            return open.isEmpty() || !Menus.classify(model(open.get(open.size() - 1))).equals(Menus.SHARD_TRADE);
        }
    }

    /** The server closes a completed trade. If the NPC that opened it is still selected, invoke
     * the game's own Enter action immediately and wait for that same verified market to return. */
    private static boolean reopenMarket(int broker, int item) {
        if (broker >= 0) return false;      // NPC ids are negative
        try {
            Object world = Memory.world();
            java.awt.Frame win = Memory.window();
            Method enter = win == null ? null : chooseMethod(world.getClass(), win.getClass());
            if (enter == null) return false;
            synchronized (world) {
                List<Object> open = copy(list(world, W_DIALOGS));
                if (!open.isEmpty()) return marketHas(open.get(open.size() - 1), item);
                if (selectedId(world) != broker) return false;
                enter.invoke(world, win);
            }
            long until = System.currentTimeMillis() + MENU_STEP_TIMEOUT;
            while (true) {
                synchronized (world) {
                    List<Object> open = copy(list(world, W_DIALOGS));
                    if (!open.isEmpty()) return marketHas(open.get(open.size() - 1), item);
                }
                if (!pause(until)) return false;
            }
        } catch (Throwable t) {
            Log.error("reopen shard market", t);
            return false;
        }
    }

    private static boolean marketHas(Object dialog, int item) throws Memory.Unavailable {
        try {
            Menus.place(model(dialog), Menus.SHARD_MARKET, item, null);
            return true;
        } catch (Menus.Refused r) {
            return false;
        }
    }

    private static int selectedId() throws Memory.Unavailable {
        return selectedId(Memory.world());
    }

    private static int selectedId(Object world) throws Memory.Unavailable {
        Object selected = ref(world, W_SELECTED);
        return selected == null ? 0 : intField(selected, E_ID);
    }

    private static boolean pause(long until) {
        if (System.currentTimeMillis() >= until) return false;
        try {
            Thread.sleep(MENU_STEP_WAIT);
            return true;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    /** `choose <row> <col> <sig>`: choose the entry at row, col of the open menu, which must still
     * be the one read with that signature (`dialog` "sig"), or refuse with the reason and touch
     * nothing. As Enter on that entry, with no walk to it a key at a time. */
    static String choose(int row, int col, int sig) {
        try {
            Object world = Memory.world();
            loader = world.getClass().getClassLoader();
            java.awt.Frame win = Memory.window();
            if (win == null) return "error: no game window";
            Method enter = chooseMethod(world.getClass(), win.getClass());
            if (enter == null) return "error: the game's choose method wasn't found";
            synchronized (world) {
                List<Object> open = copy(list(world, W_DIALOGS));
                if (open.isEmpty()) return "refused: no menu is open";
                Object y = open.get(open.size() - 1);
                try {
                    Menus.chosen(model(y, false), row, col, sig);
                } catch (Menus.Refused r) {
                    return "refused: " + r.getMessage();
                }
                setInt(y, D_ROW, row);
                setInt(y, D_COL, col);
                setInt(y, D_OPTION, 0);
                enter.invoke(world, win);
                Log.info("ACTION menu row " + row + " col " + col);
                return "chosen " + row + " " + col;
            }
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("choose", t);
            return "error: " + t;
        }
    }

    private static volatile Method choose;

    /** world.a(Win): public, boolean, the window its only parameter - the one method of that shape
     * (Enter's). Found by its shape, so a renamed build can't make it another. */
    static Method chooseMethod(Class<?> world, Class<?> win) {
        Method m = choose;
        if (m != null && m.getDeclaringClass() == world) return m;
        Method found = null;
        for (Method c : world.getDeclaredMethods()) {
            Class<?>[] p = c.getParameterTypes();
            if (c.getReturnType() == boolean.class && p.length == 1 && p[0] == win
                    && Modifier.isPublic(c.getModifiers())) {
                if (found != null) return null;       // two of that shape: not sure which, so neither
                found = c;
            }
        }
        if (found != null) {
            found.setAccessible(true);
            choose = found;
        }
        return found;
    }

    /** The dialog y as Menus reads it: every entry's flags, options (in English) and command. */
    static Menus.Menu model(Object y) throws Memory.Unavailable {
        return model(y, true);
    }

    /** english false: the game's own strings, text too, untranslated - what Menus.signature takes,
     * so no translation lookups for it on every reading. */
    static Menus.Menu model(Object y, boolean english) throws Memory.Unavailable {
        Menus.Menu m = new Menus.Menu();
        for (Object row : copy(list(y, D_ROWS))) {
            List<Menus.Entry> cells = new ArrayList<Menus.Entry>();
            List<Object> zs = row instanceof ArrayList ? copy((ArrayList<?>) row) : new ArrayList<Object>();
            for (Object z : zs) {
                if (z == null) {
                    cells.add(null);
                    continue;
                }
                Object opts = ref(z, Z_OPTIONS);
                String[] o = null;
                if (opts instanceof String[]) {
                    o = ((String[]) opts).clone();
                    if (english) {
                        for (int i = 0; i < o.length; i++) o[i] = english(o[i]);
                    }
                }
                Object cmd = ref(z, Z_COMMAND);
                Object text = english ? null : ref(z, Z_TEXT);
                cells.add(new Menus.Entry(intField(z, Z_FLAGS), text instanceof String ? (String) text : null, o,
                        cmd instanceof int[] ? ((int[]) cmd).clone() : null));
            }
            m.rows.add(cells);
        }
        return m;
    }

    private static void setInt(Object o, String name, int value) throws Memory.Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null || f.getType() != int.class) throw moved(o, name);
        try {
            f.setInt(o, value);
        } catch (IllegalAccessException e) {
            throw moved(o, name);
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
        sb.append(",\"sig\":").append(Menus.signature(model(y, false)));
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
