package unclefarmer.agent;

import java.nio.charset.Charset;

/**
 * The game's end-of-dungeon (and battle) report, IN op=66, read as the client's own reader
 * (game.N.V) reads it:
 *     42 <kind:1> <head:2> <4 shorts, the 4th = monsters killed in all>
 *        rows of 49 bytes until an id of 0:
 *          <id:4> <class*2+1 (+1 for a woman):2> <level:1> <3 shorts> <kills:2> <damage:4>
 *          <healing:4> <controls:2> <buffs:2> <debuffs removed:2> <buffs removed:2>
 *          <resurrections:2> <highest hit:4> <highest area hit:4> <highest heal:4>
 *          <highest area heal:4>
 *        kind 7 (one side: dungeons) ends there; the others (two sides: battles) carry 4 shorts
 *        and a second list of rows
 *        <names, GBK, comma-separated>: the place, its part ("Demon  Cave floor 13", or empty),
 *        then each row's player name in row order
 * Recorded 2026-11-17 for a five-player Demon Cave floor: total 141, rows 24/34/31/34/18 kills,
 * names "Demon  Cave,Demon  Cave floor 13,<five names>". Only the newest report is kept.
 */
final class Report {

    private static final Charset GBK = Charset.forName("GBK");
    static final int ROW = 49;

    private static volatile String last = null;
    private static volatile long lastAt = 0L;

    private Report() { }

    /** IN op=66. */
    static void inbound(byte[] m) {
        String json = decode(m, System.currentTimeMillis());
        if (json != null) {
            last = json;
            lastAt = System.currentTimeMillis();
        }
    }

    /** The newest report if it arrived after `after` (ms), else {}. */
    static String since(long after) {
        String r = last;
        return r != null && lastAt > after ? r : "{}";
    }

    static void forget() {
        last = null;
        lastAt = 0L;
    }

    /** The report as JSON, or null when the message isn't one this reader understands. */
    static String decode(byte[] m, long now) {
        if (m == null || m.length < 12 + 4) return null;
        int kind = m[1] & 0xFF;
        int total = Protocol.readShort(m, 10);
        StringBuilder rows = new StringBuilder(512);
        int[] at = {12};
        int n = rows(m, at, rows, 0);
        if (n < 0) return null;
        int second = 0;
        if (kind != 7) {
            if (at[0] + 8 > m.length) return null;
            at[0] += 8;
            second = rows(m, at, rows, n);
            if (second < 0) return null;
        }
        String[] names = new String(m, at[0], m.length - at[0], GBK).split(",", -1);
        StringBuilder sb = new StringBuilder(rows.length() + 256);
        sb.append("{\"at\":").append(now)
          .append(",\"kind\":").append(kind)
          .append(",\"total_kills\":").append(total)
          .append(",\"place\":\"").append(Protocol.jsonEscape(names.length > 0 ? names[0].trim() : "")).append('"')
          .append(",\"part\":\"").append(Protocol.jsonEscape(names.length > 1 ? names[1].trim() : "")).append('"')
          .append(",\"players\":[");
        // Names follow the rows in order; with two sides the list isn't one name a row, so the
        // names are left off there (and kept whole in "names").
        String[] rowList = rows.toString().split("\u0001", -1);
        int count = n + second;
        boolean named = kind == 7 && names.length == 2 + count;
        for (int i = 0; i < count; i++) {
            if (i > 0) sb.append(',');
            String row = rowList[i];
            if (named) {
                sb.append(row, 0, row.length() - 1)
                  .append(",\"name\":\"").append(Protocol.jsonEscape(names[2 + i].trim())).append("\"}");
            } else {
                sb.append(row);
            }
        }
        sb.append("],\"names\":[");
        for (int i = 2; i < names.length; i++) {
            if (i > 2) sb.append(',');
            sb.append('"').append(Protocol.jsonEscape(names[i].trim())).append('"');
        }
        return sb.append("]}").toString();
    }

    /** Rows from at[0] until an id of 0, appended as JSON objects separated by \u0001. Returns
     *  how many, or -1 when the message ends inside them. */
    private static int rows(byte[] m, int[] at, StringBuilder out, int before) {
        int n = 0;
        while (true) {
            int p = at[0];
            if (p + 4 > m.length) return -1;
            int id = Protocol.readInt(m, p);
            if (id == 0) {
                at[0] = p + 4;
                return n;
            }
            if (p + ROW > m.length) return -1;
            int cs = Protocol.readShort(m, p + 4);
            if (before + n > 0) out.append('\u0001');
            out.append("{\"id\":").append(id)
               .append(",\"class\":").append((cs - 1) / 2)
               .append(",\"female\":").append(cs % 2 == 0)
               .append(",\"level\":").append(m[p + 6] & 0xFF)
               .append(",\"kills\":").append(Protocol.readShort(m, p + 13))
               .append(",\"damage\":").append(Protocol.readInt(m, p + 15))
               .append(",\"healing\":").append(Protocol.readInt(m, p + 19))
               .append(",\"controls\":").append(Protocol.readShort(m, p + 23))
               .append(",\"buffs\":").append(Protocol.readShort(m, p + 25))
               .append(",\"resurrections\":").append(Protocol.readShort(m, p + 31))
               .append(",\"highest_hit\":").append(Protocol.readInt(m, p + 33))
               .append(",\"highest_area_hit\":").append(Protocol.readInt(m, p + 37))
               .append(",\"highest_heal\":").append(Protocol.readInt(m, p + 41))
               .append(",\"highest_area_heal\":").append(Protocol.readInt(m, p + 45))
               .append('}');
            at[0] = p + ROW;
            n++;
        }
    }
}
