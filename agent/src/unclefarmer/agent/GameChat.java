package unclefarmer.agent;

import java.io.UnsupportedEncodingException;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;

/**
 * The game's chat channels for the Chat window: what arrives, and sending a line. Kept in memory
 * only and never written anywhere (no log line carries a message); when the game closes it is gone.
 * Private messages are {@link PrivateChat}.
 *
 * Channel numbers, read off real traffic (a labelled test line in each channel): sent as 0 global,
 * 1 map, 2 nearby, 3 gang, 4 team, 5 international, 6 the private channel (a channel players join,
 * not private messages, which are op=56 and PrivateChat); they arrive as three times that (0, 3,
 * 6, 9, 12, 15, 18). Other numbers that arrive are the game's broadcasts (loot, events, team
 * recruiting, the monster count).
 *
 *   IN  op=55  37 <ts:8> <msgid:4> <channel:1> [<extra:2> when channel & 32] [<room:4> when
 *              (channel & 31) >= 15] <sender:4> "name\ntext" -- the game's own reader (game.N.P,
 *              javap) takes it apart in that order, so the name starts at a known byte (nameStart)
 *   IN  op=8   08 05 <room:4> "room name"  -- the international room we are in
 *   OUT        the game's own sender, N.a(channel, target, text): target 0, or the room for
 *              international and the private channel (the chat model's fields, Memory.chatRooms).
 *              It refuses a second line within 2 s (3 s on global) with a spoken warning, as when
 *              the player types.
 */
final class GameChat {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int KEEP = 400;
    private static final int INTERNATIONAL_OUT = 5;
    private static final int PRIVATE_CHANNEL_OUT = 6;

    private static final List<Object[]> LINES = new ArrayList<Object[]>();   // {seq, at, channel, name, text, voice}
    private static long seq;
    private static volatile int room;          // the international room we are in, 0 until known

    private GameChat() { }

    /** IN op=55. */
    static void inbound(byte[] m) {
        if (m.length < 15) return;
        int channel = m[13] & 0xFF;
        if (channel >= Voice.FLAG) {
            voice(m, channel - Voice.FLAG);
            return;
        }
        String[] parts = split(m, nameStart(m));
        if (parts == null || parts[1].trim().isEmpty()) return;
        synchronized (LINES) {
            LINES.add(new Object[] {++seq, System.currentTimeMillis(), channel, parts[0] == null ? "" : parts[0],
                    parts[1].replace("\r", "").replace("\n", " ").trim(), null});
            while (LINES.size() > KEEP) LINES.remove(0);
            LINES.notifyAll();
        }
    }

    /**
     * A voice message (Voice): "name\n", the 32-character name the game keeps the sound under, then
     * the sound. The name starts where a text line's does (nameStart); the newline taken is the
     * first one after it with such a sound name right after it.
     */
    static void voice(byte[] m, int channel) {
        int start = nameStart(m);
        for (int i = start; i < m.length; i++) {
            if (m[i] != 0x0A) continue;
            String id = Voice.nameAfter(m, i);
            if (id == null) continue;
            String name = new String(m, start, i - start, UTF8);
            synchronized (LINES) {
                LINES.add(new Object[] {++seq, System.currentTimeMillis(), channel, plausibleName(name) ? name : "", "", id});
                while (LINES.size() > KEEP) LINES.remove(0);
                LINES.notifyAll();
            }
            return;
        }
    }

    /** IN op=8 05 <room:4> "name": the international room joined. */
    static void roomNamed(byte[] m) {
        if (m.length < 7 || m[1] != INTERNATIONAL_OUT) return;
        int r = Protocol.readInt(m, 2);
        if (r > 0 && r < 0x10000) room = r;
    }

    /** `chat <after> [ms]`: lines numbered above `after`, waiting up to ms for one. */
    static String since(long after, long ms) {
        long end = System.currentTimeMillis() + Math.max(0, Math.min(ms, 30000));
        StringBuilder sb = new StringBuilder("{\"ok\":true,\"last\":");
        synchronized (LINES) {
            while (seq <= after && ms > 0) {
                long left = end - System.currentTimeMillis();
                if (left <= 0) break;
                try {
                    LINES.wait(left);
                } catch (InterruptedException e) {
                    break;
                }
            }
            sb.append(seq).append(",\"room\":").append(room).append(",\"chat\":[");
            boolean first = true;
            long now = System.currentTimeMillis();
            for (Object[] e : LINES) {
                if ((Long) e[0] <= after) continue;
                if (!first) sb.append(',');
                first = false;
                sb.append("{\"n\":").append(e[0]).append(",\"age_ms\":").append(now - (Long) e[1])
                  .append(",\"channel\":").append(e[2]).append(",\"name\":\"")
                  .append(Protocol.jsonEscape((String) e[3])).append("\",\"text\":\"")
                  .append(Protocol.jsonEscape((String) e[4])).append('"');
                if (e[5] != null) sb.append(",\"voice\":\"").append(e[5]).append('"');
                sb.append('}');
            }
        }
        return sb.append("]}").toString();
    }

    /** `say <channel> <text>`: a line on channel 0-6 (the numbers it is sent with). */
    static String say(int channel, String text) {
        if (channel < 0 || channel > PRIVATE_CHANNEL_OUT || text == null || text.trim().isEmpty()) {
            return "error: say <0-6> <text>";
        }
        int target = 0;
        if (channel == INTERNATIONAL_OUT || channel == PRIVATE_CHANNEL_OUT) {
            int[] rooms;
            try {
                rooms = Memory.chatRooms();
            } catch (Memory.Unavailable e) {
                rooms = new int[] {room, 0};
            }
            target = channel == INTERNATIONAL_OUT ? (rooms[0] > 0 ? rooms[0] : room) : rooms[1];
            if (target <= 0) {
                return channel == INTERNATIONAL_OUT ? "error: no international room joined yet"
                        : "error: no private channel joined yet";
            }
        }
        if (PrivateChat.sender() == null) return "error: the game's chat sender wasn't found";
        ChatQueue.add(channel, target, text);     // in turn with every other line, as the game allows
        return "sent";
    }

    /**
     * Where the sender's name starts, worked out as the game's reader does (game.N.P): after the
     * channel byte (13) come 2 more bytes when it has flag 32, then (with that flag taken off) the
     * room's id when (channel & 31) >= 15, then the sender's id; the name is next. The channel byte
     * is read signed, as the game's readByte() does.
     *
     * The name used to be found by walking back from the newline over letters and digits, which
     * ran on into the sender's id whenever its last byte was one ("2magoestelar" from an id ending
     * in 0x32) - a quarter of all players - and put a digit-led name's own digits twice.
     */
    static int nameStart(byte[] m) {
        int b = m[13];
        int at = 14;
        if ((b & 32) == 32) {
            at += 2;
            b -= 32;
        }
        if ((b & 31) >= 15) at += 4;
        return at + 4;
    }

    /**
     * The trailing "name\ntext" from the name's own start: everything up to the first newline is
     * the name, as the game takes it; a name that isn't one (a broadcast, whose text names the
     * actor itself) is left empty.
     */
    private static String[] split(byte[] m, int start) {
        if (m.length <= start) return null;
        for (int i = start; i < m.length; i++) {
            if (m[i] != 0x0A) continue;
            String name = new String(m, start, i - start, UTF8);
            return new String[] {plausibleName(name) ? name : null, decode(m, i + 1, m.length - i - 1)};
        }
        return new String[] {null, decode(m, start, m.length - start)};
    }

    private static boolean plausibleName(String s) {
        if (s.length() < 3 || s.length() > 24) return false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (!Character.isLetterOrDigit(c) && c != '_' && c != '-') return false;
        }
        return true;
    }

    private static String decode(byte[] m, int off, int len) {
        if (len <= 0 || off < 0 || off + len > m.length) return "";
        String s = new String(m, off, len, UTF8);
        if (s.indexOf('�') < 0) return s;
        try {
            return new String(m, off, len, "GBK");
        } catch (UnsupportedEncodingException e) {
            return s;
        }
    }
}
