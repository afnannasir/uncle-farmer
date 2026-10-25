package unclefarmer.agent;

import java.nio.charset.Charset;

/**
 * Byte-level rules of the Mist World protocol, shared by everything that reads messages.
 *
 * Every rule here was checked against captured traffic rather than assumed. See PROTOCOL.md.
 */
final class Protocol {

    static final Charset UTF8 = Charset.forName("UTF-8");

    /**
     * Top four bytes of System.currentTimeMillis() for this era. Movement messages embed an 8-byte
     * timestamp, and the fields before it are not a fixed size, so this is how the position is
     * located: find the timestamp, and the position is the four bytes after it.
     *
     * Derived from the clock rather than written down: a constant only matches for about 50 days
     * and then every position is silently dropped (the old 0x000001A0 stopped matching 2026-10-03).
     * Read from the clock at each message, not once at the start: a game left running across one
     * of those 50-day turns would otherwise lose every position from then on. A timestamp from
     * a few minutes either side of the turn still matches (PREFIX_SLACK_MS).
     */
    static final long PREFIX_SLACK_MS = 10L * 60L * 1000L;

    private static byte[] epochPrefix(long now) {
        return new byte[] {(byte) (now >>> 56), (byte) (now >>> 48), (byte) (now >>> 40), (byte) (now >>> 32)};
    }

    private Protocol() { }

    static int readInt(byte[] m, int off) {
        if (m == null || off < 0 || off + 4 > m.length) return 0;
        return ((m[off] & 0xFF) << 24) | ((m[off + 1] & 0xFF) << 16)
                | ((m[off + 2] & 0xFF) << 8) | (m[off + 3] & 0xFF);
    }

    static int readShort(byte[] m, int off) {
        if (m == null || off < 0 || off + 2 > m.length) return 0;
        return ((m[off] & 0xFF) << 8) | (m[off + 1] & 0xFF);
    }

    static boolean wordChar(byte b) {
        int u = b & 0xFF;
        return (u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z') || (u >= '0' && u <= '9')
                || u == '_' || u == '-';
    }

    /** Real names are one run of word characters; anything else is a misread binary field. */
    static boolean plausibleName(String s) {
        if (s == null || s.length() < 3 || s.length() > 24) return false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (!Character.isLetterOrDigit(c) && c != '_' && c != '-') return false;
        }
        return true;
    }

    /**
     * Name from an op=32 entity record: "name,title,equipment-set," for a player, just "name" for
     * a monster.
     *
     * The name starts at a fixed offset -- 41 for players, 37 for monsters -- which follows from
     * the position sitting at 33. A minority of records carry one extra 4-byte field, so that is
     * tried second.
     *
     * Scanning backwards from the end instead looks tempting and is wrong: the binary field in
     * front of the name often ends in a printable byte, which the scan cannot tell from the first
     * letter. Measured over the capture, scanning produced 484 distinct names including "N Guard"
     * (190x), "HGuard", "PGuard" and "XHN Guard"; reading at the offset produced 374, with all of
     * those collapsing into "Guard" (209x).
     */
    static String entityName(byte[] m) {
        if (m == null || m.length < 38) return null;
        int base = readInt(m, 1) > 0 ? 41 : 37;

        String name = field(m, base);
        if (name != null) return name;
        return field(m, base + 4);
    }

    /** One comma-delimited text field, or null if what is there does not look like a name. */
    private static String field(byte[] m, int off) {
        if (off < 0 || off >= m.length) return null;
        int end = off;
        while (end < m.length && m[end] != ',' && (m[end] & 0xFF) >= 0x20) end++;
        if (end == off) return null;

        String s = new String(m, off, end - off, UTF8).trim();
        return !s.isEmpty() && Character.isLetter(s.charAt(0)) ? s : null;
    }

    /**
     * Byte offset of the current position in an op=36 or op=37 message: just past the embedded
     * timestamp. Some entities carry a 4-byte sequence counter before it and some do not, so the
     * position sits at offset 13 in one message and 17 in the next -- within the same opcode.
     * Reading at a fixed offset lands on the speed field and yields distances in the hundreds of
     * tiles.
     *
     * @return the offset, or -1 if no timestamp is present.
     */
    static int positionOffset(byte[] m) {
        return positionOffset(m, System.currentTimeMillis());
    }

    static int positionOffset(byte[] m, long now) {
        if (m == null) return -1;
        byte[][] prefixes = {epochPrefix(now), epochPrefix(now - PREFIX_SLACK_MS), epochPrefix(now + PREFIX_SLACK_MS)};
        int limit = Math.min(m.length - 4, 14);
        for (int i = 1; i < limit; i++) {
            for (byte[] p : prefixes) {
                if (m[i] == p[0] && m[i + 1] == p[1] && m[i + 2] == p[2] && m[i + 3] == p[3]) return i + 8;
            }
        }
        return -1;
    }

    /** op=6: "06 <2> <2> <1> <1> name", or a 7-byte form with no name, meaning the map was left. */
    static String mapName(byte[] m) {
        if (m == null || m.length <= 7) return null;
        String name = new String(m, 7, m.length - 7, UTF8).trim();
        return name.isEmpty() ? null : name;
    }

    /**
     * Position carried by an op=32 entity record, as raw 100-per-tile units, or null.
     *
     * This is where the entity was when the record was sent, which is what makes it useful: an
     * entity that is standing still never sends a movement message, so this is the only place its
     * position ever appears. Verified at offset 33 by matching against movement messages for the
     * same entity (the only offset that ever agrees).
     */
    static int[] entityPosition(byte[] m) {
        if (m == null || m.length < 37) return null;
        return new int[]{readShort(m, 33), readShort(m, 35)};
    }

    /** Inbound coordinates are 16-bit at 100 per tile; our own outbound ones 32-bit at 1000. */
    static double tiles100(int raw) {
        return raw / 100.0;
    }

    static double tiles1000(int raw) {
        return raw / 1000.0;
    }

    static String jsonEscape(String s) {
        if (s == null) return "";
        StringBuilder sb = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
