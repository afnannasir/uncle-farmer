package unclefarmer.agent;

import java.util.ArrayList;
import java.util.List;

/**
 * The shop and Shard Market menus whose layout is known, and choosing in them by index.
 *
 * Every menu below was read from the game's own dialogs (logs/dialogs.log, the cmd array each entry
 * sends with OUT op=19 when chosen) and was the same in every city it was seen in:
 *
 *   shard_market  the Shard Broker's list (Giant Rock, Mario Basin, Peach Blossom, Tulip, Holy Blood;
 *                 2026-10-11 and 2026-10-14). Row 0: a header without a command, then "View
 *                 description" (cmd [4]) beside it. Rows 1..17: one shard each, options [name,
 *                 "Unit price N arnebia; ..."], cmd [1, item id, price], in SHARD_MARKET_IDS order.
 *                 The prices change; the order and the ids never did.
 *   shard_trade   what choosing a shard opens: row 0 "buy" cmd [2, item id, price, 0], row 1 "sell"
 *                 cmd [3, item id, price, 0]. Then the game asks how many (its input box, TextInput).
 *   potion_shop   a potion seller's list (Apothecary Phillips, Pharmacist Kania; 2026-10-04 to
 *                 2026-11-02): rows 0..11, options [name, "unit price N arnebia", what it does],
 *                 cmd [1, item id, 0], in POTION_SHOP_IDS order. Then the input box asks how many.
 *
 * Other shops (the grocer's) were never recorded, so they have no layout here: the bot chooses in
 * them by the entry's text, as before.
 *
 * Choosing by index never trusts the index alone. The open menu must be the layout asked for, as
 * a whole (classify); the row the verified layout gives the item must hold that item's id, and in
 * shard_trade the command must be the buy or sell asked for. Anything else is refused with the
 * reason, nothing is pressed, and the bot falls back to choosing by text. The cursor is then put
 * on the entry the way the game's own row keys do it (game.i.e()/f(): row d, column e, option f =
 * 0, under the world's lock) and Enter is pressed as the Enter key is.
 *
 * Every other menu (agent 55, `choose`): the bot finds the entry it wants in its `dialog` reading,
 * by text, and sends that entry's row and column with the reading's signature. Under the world's
 * lock the open menu must still have that signature (the same content, row for row) and an entry
 * there; then the cursor is put on it and Enter pressed as above, with no walk down the list a key
 * at a time. Anything else is refused and the bot walks to it with the keys, the nearer way round.
 */
final class Menus {

    static final String SHARD_MARKET = "shard_market";
    static final String SHARD_TRADE = "shard_trade";
    static final String POTION_SHOP = "potion_shop";
    static final String UNKNOWN = "unknown";

    /** The Shard Market's item ids, row 1 first (row 0 is the header). */
    static final int[] SHARD_MARKET_IDS = {
        20985, 21015,                                     // Weapon / Equipment Enhance Crystal Shard
        20385, 20405, 20425, 20445, 20465, 20485,         // Level 1 Ruby, Sapphire, Tiger Eye Stone, Opal, Spinel, Aquamarine
        20505, 20525, 20545, 20565, 20585, 20605,         // Garnet, Blond Jade, Grape Stone, Olivine, Topaz, Obsidian
        20625, 20645, 20665,                              // Sunlight Stone, Moonstone, Turquoise
    };
    /** A potion seller's item ids, row 0 first. */
    static final int[] POTION_SHOP_IDS = {
        23385, 23395, 23386, 23396, 23387, 23397,         // Small, Medium, Large Red / Blue Potion
        23435, 23445, 23436, 23446, 23437, 23447,         // the pet's
    };
    static final int BUY = 2;                             // shard_trade commands
    static final int SELL = 3;

    private Menus() { }

    /** One entry as the game holds it (game.z): its flags, text, options and command. */
    static final class Entry {
        final int flags;
        final String text;
        final String[] options;
        final int[] cmd;

        Entry(int flags, String[] options, int[] cmd) {
            this(flags, null, options, cmd);
        }

        Entry(int flags, String text, String[] options, int[] cmd) {
            this.flags = flags;
            this.text = text;
            this.options = options == null ? new String[0] : options;
            this.cmd = cmd;
        }

        String name() {
            return options.length > 0 ? options[0] : null;
        }

        int hash() {
            int h = flags;
            h = 31 * h + (text == null ? 0 : text.hashCode());
            h = 31 * h + java.util.Arrays.hashCode(options);
            return 31 * h + java.util.Arrays.hashCode(cmd);
        }
    }

    /** The open dialog's rows of entries (null for an empty cell). */
    static final class Menu {
        final List<List<Entry>> rows = new ArrayList<List<Entry>>();

        Entry at(int row, int col) {
            if (row < 0 || row >= rows.size()) return null;
            List<Entry> r = rows.get(row);
            return col < 0 || col >= r.size() ? null : r.get(col);
        }
    }

    /** Refused: the reason, as the bot logs it. */
    static final class Refused extends Exception {
        private static final long serialVersionUID = 1L;

        Refused(String why) {
            super(why);
        }
    }

    // ---------------------------------------------------------------- what the open menu is
    /** The layout the menu has, judged by the commands of every entry (no text, so a language
     * pack doesn't change it), or UNKNOWN. */
    static String classify(Menu m) {
        if (m == null || m.rows.isEmpty()) return UNKNOWN;
        if (isTrade(m)) return SHARD_TRADE;
        if (isMarket(m)) return SHARD_MARKET;
        if (isShop(m)) return POTION_SHOP;
        return UNKNOWN;
    }

    private static boolean isTrade(Menu m) {
        if (m.rows.size() != 2) return false;
        Entry buy = only(m, 0);
        Entry sell = only(m, 1);
        return buy != null && sell != null && cmd(buy, 4) && cmd(sell, 4)
                && buy.cmd[0] == BUY && sell.cmd[0] == SELL && buy.cmd[1] == sell.cmd[1] && buy.cmd[1] > 0;
    }

    private static boolean isMarket(Menu m) {
        Entry head = m.at(0, 0);
        if (head == null || head.cmd != null || m.rows.size() < 2) return false;
        for (int r = 1; r < m.rows.size(); r++) {
            Entry e = only(m, r);
            if (e == null || !cmd(e, 3) || e.cmd[0] != 1 || e.cmd[1] <= 0 || e.cmd[2] <= 0) return false;
        }
        return true;
    }

    private static boolean isShop(Menu m) {
        for (int r = 0; r < m.rows.size(); r++) {
            Entry e = only(m, r);
            if (e == null || !cmd(e, 3) || e.cmd[0] != 1 || e.cmd[1] <= 0 || e.cmd[2] != 0 || e.options.length < 2) {
                return false;
            }
        }
        return true;
    }

    /** The row's single entry, or null when it has none or more than one. */
    private static Entry only(Menu m, int row) {
        List<Entry> r = m.rows.get(row);
        return r.size() == 1 ? r.get(0) : null;
    }

    private static boolean cmd(Entry e, int length) {
        return e.cmd != null && e.cmd.length == length;
    }

    /** The item ids in the menu's item rows, in order (market: rows 1..; shop: rows 0..). */
    static int[] ids(Menu m, String layout) {
        int first = layout.equals(SHARD_MARKET) ? 1 : 0;
        int[] out = new int[Math.max(0, m.rows.size() - first)];
        for (int r = first; r < m.rows.size(); r++) {
            Entry e = only(m, r);
            out[r - first] = e != null && e.cmd != null && e.cmd.length > 1 ? e.cmd[1] : 0;
        }
        return out;
    }

    /** The menu's item rows are exactly the verified ones, in the verified order. */
    static boolean verified(Menu m, String layout) {
        if (layout.equals(SHARD_TRADE)) return true;           // its two rows are the layout itself
        int[] want = layout.equals(SHARD_MARKET) ? SHARD_MARKET_IDS
                : layout.equals(POTION_SHOP) ? POTION_SHOP_IDS : null;
        return want != null && java.util.Arrays.equals(ids(m, layout), want);
    }

    // ---------------------------------------------------------------- choosing by index
    /** {row, col} of the item in the open menu by the verified layout, after every check; Refused
     * with the reason otherwise. action: "buy" or "sell" in shard_trade, null elsewhere. */
    static int[] place(Menu m, String layout, int id, String action) throws Refused {
        String is = classify(m);
        if (!is.equals(layout)) throw new Refused("the open menu is " + is + ", not " + layout);
        if (!verified(m, layout)) throw new Refused(layout + " changed: its items are not the verified ones");
        int row;
        int want = 1;
        if (layout.equals(SHARD_TRADE)) {
            if ("buy".equals(action)) want = BUY;
            else if ("sell".equals(action)) want = SELL;
            else throw new Refused("shard_trade needs buy or sell");
            row = want == BUY ? 0 : 1;
        } else {
            if (action != null) throw new Refused(layout + " takes no action");
            int[] table = layout.equals(SHARD_MARKET) ? SHARD_MARKET_IDS : POTION_SHOP_IDS;
            int i = indexOf(table, id);
            if (i < 0) throw new Refused("item " + id + " is not in the verified " + layout);
            row = layout.equals(SHARD_MARKET) ? i + 1 : i;
        }
        Entry e = m.at(row, 0);
        if (e == null || e.cmd == null || e.cmd.length < 2) throw new Refused("row " + row + " has no item");
        if (e.cmd[1] != id) throw new Refused("row " + row + " holds item " + e.cmd[1] + ", not " + id);
        if (e.cmd[0] != want) throw new Refused("row " + row + " sends command " + e.cmd[0] + ", not " + want);
        return new int[] {row, 0};
    }

    // ---------------------------------------------------------------- choosing any menu's entry
    /**
     * A number for the menu's whole content - every row, every entry's flags, text, options and
     * command, in order; not the cursor. `dialog` reports it with the open menu, and `choose` takes
     * it back: the bot found the entry it wants in that reading, so a menu whose content changed
     * since (another one opened, a row came or went) never gets the row the bot found in the old
     * one.
     */
    static int signature(Menu m) {
        int h = m.rows.size();
        for (List<Entry> r : m.rows) {
            h = 31 * h + r.size();
            for (Entry e : r) h = 31 * h + (e == null ? 0 : e.hash());
        }
        return h;
    }

    /** `choose <row> <col> <sig>`: the entry at row, col of the menu read with that signature;
     * Refused with the reason otherwise. */
    static Entry chosen(Menu m, int row, int col, int sig) throws Refused {
        if (m == null || m.rows.isEmpty()) throw new Refused("the menu has no rows");
        if (signature(m) != sig) throw new Refused("the menu changed since it was read");
        Entry e = m.at(row, col);
        if (e == null) throw new Refused("no entry at row " + row + " col " + col);
        return e;
    }

    private static final java.util.regex.Pattern UNIT_PRICE =
            java.util.regex.Pattern.compile("(?i)unit price\\s+(\\d{1,9})\\s+arnebia");

    /** A shop row sends no price (cmd [1, id, 0]): the one its text shows, or 0. */
    static int shownPrice(Entry e) {
        for (String o : e.options) {
            if (o == null) continue;
            java.util.regex.Matcher m = UNIT_PRICE.matcher(o);
            if (m.find()) return Integer.parseInt(m.group(1));
        }
        return 0;
    }

    private static int indexOf(int[] a, int v) {
        for (int i = 0; i < a.length; i++) {
            if (a[i] == v) return i;
        }
        return -1;
    }

    // ---------------------------------------------------------------- the `menu` reply
    /** {"ok":true,"layout":..,"verified":..,"items":[{"row","col","id","name","price","action"?}]} */
    static String describe(Menu m) {
        String layout = classify(m);
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"ok\":true,\"layout\":\"").append(layout).append('"');
        sb.append(",\"verified\":").append(!layout.equals(UNKNOWN) && verified(m, layout));
        sb.append(",\"items\":[");
        if (!layout.equals(UNKNOWN)) {
            int first = layout.equals(SHARD_MARKET) ? 1 : 0;
            boolean comma = false;
            for (int r = first; r < m.rows.size(); r++) {
                Entry e = m.at(r, 0);
                if (e == null || e.cmd == null || e.cmd.length < 3) continue;
                if (comma) sb.append(',');
                comma = true;
                sb.append("{\"row\":").append(r).append(",\"col\":0,\"id\":").append(e.cmd[1]);
                sb.append(",\"name\":");
                String n = e.name();
                if (n == null) sb.append("null");
                else sb.append('"').append(Protocol.jsonEscape(n)).append('"');
                sb.append(",\"price\":").append(e.cmd[2] > 0 ? e.cmd[2] : shownPrice(e));
                if (layout.equals(SHARD_TRADE)) sb.append(",\"action\":\"").append(e.cmd[0] == BUY ? "buy" : "sell").append('"');
                sb.append('}');
            }
        }
        return sb.append("]}").toString();
    }
}
