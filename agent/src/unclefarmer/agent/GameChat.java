package unclefarmer.agent;

import java.io.UnsupportedEncodingException;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

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
 *
 * A player can be in several international rooms and several private channels at once (switched
 * in the game's own chat line): every international and private-channel line carries its room's
 * id, kept with the line, and the chat model keeps each room's lines apart (Memory.chatJoined) and
 * names the room it has up now (Memory.chatRoomNames). Names are remembered as rooms come up.
 */
final class GameChat {

    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int KEEP = 400;
    private static final int INTERNATIONAL_OUT = 5;
    private static final int PRIVATE_CHANNEL_OUT = 6;

    private static final int INTERNATIONAL_IN = 15;
    private static final int PRIVATE_CHANNEL_IN = 18;
    private static final long ROOMS_EVERY_MS = 1000;

    private static final List<Object[]> LINES = new ArrayList<Object[]>();   // {seq, at, channel, name, text, voice, room}
    private static long seq;
    private static volatile int room;          // the international room we are in, 0 until known
    // "<out channel>:<room id>" -> its name, as the chat model showed it
    private static final Map<String, String> NAMES = new LinkedHashMap<String, String>();
    private static int[][] joined = {{}, {}};
    private static int[] current = {0, 0};
    private static long roomsRead;

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
        String name = parts[0] == null ? "" : parts[0];
        String text = parts[1].replace("\r", "").replace("\n", " ").trim();
        TeamHandoff.chat(channel, name, text);
        int in = channel == INTERNATIONAL_IN || channel == PRIVATE_CHANNEL_IN ? roomOf(m) : 0;
        synchronized (LINES) {
            LINES.add(new Object[] {++seq, System.currentTimeMillis(), channel, name, text, null, in});
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
                LINES.add(new Object[] {++seq, System.currentTimeMillis(), channel, plausibleName(name) ? name : "", "", id, 0});
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

    /**
     * The room id an international or private-channel line came from: the 4 bytes after the
     * channel byte (and its 2 extra bytes when it has flag 32), where the game's reader takes it
     * (nameStart). 0 when the line is too short.
     */
    static int roomOf(byte[] m) {
        int at = 14;
        if ((m[13] & 32) == 32) at += 2;
        int r = Protocol.readInt(m, at);
        return r > 0 ? r : 0;
    }

    /** The joined rooms and their names from the chat model, at most once a second. */
    private static void readRooms() {
        long now = System.currentTimeMillis();
        synchronized (NAMES) {
            if (now - roomsRead < ROOMS_EVERY_MS) return;
            roomsRead = now;
            try {
                int[] up = Memory.chatRooms();
                String[] names = Memory.chatRoomNames();
                joined = Memory.chatJoined();
                current = up;
                for (int i = 0; i < 2; i++) {
                    String n = names[i];
                    if (up[i] > 0 && n != null && !n.trim().isEmpty()) {
                        NAMES.put((i == 0 ? INTERNATIONAL_OUT : PRIVATE_CHANNEL_OUT) + ":" + up[i], n.trim());
                    }
                }
            } catch (Memory.Unavailable e) {
                // not logged in yet: what was read before stands
            }
        }
    }

    /**
     * The rooms of one channel (0 international, 1 private channel) the player is in: those the
     * chat model keeps lines for, the one it has up, and every one it has had up since the game
     * started. Switching rooms in the game's chat line doesn't leave one (live 2026-11-24: back on
     * French, a line into English still went there), but the chat model keeps no lines for a room
     * nothing was said in yet, so the rooms seen up are remembered too. Under NAMES' lock.
     */
    private static List<Integer> seen(int i) {
        int out = i == 0 ? INTERNATIONAL_OUT : PRIVATE_CHANNEL_OUT;
        List<Integer> ids = new ArrayList<Integer>();
        for (int id : joined[i]) ids.add(id);
        if (current[i] > 0 && !ids.contains(current[i])) ids.add(current[i]);
        for (String key : NAMES.keySet()) {
            int colon = key.indexOf(':');
            if (Integer.parseInt(key.substring(0, colon)) != out) continue;
            Integer id = Integer.valueOf(key.substring(colon + 1));
            if (!ids.contains(id)) ids.add(id);
        }
        return ids;
    }

    /** "rooms":[{"channel":5,"id":10,"name":"English","current":true}, ...]: every joined room. */
    private static void rooms(StringBuilder sb) {
        readRooms();
        sb.append(",\"rooms\":[");
        boolean first = true;
        synchronized (NAMES) {
            for (int i = 0; i < 2; i++) {
                int out = i == 0 ? INTERNATIONAL_OUT : PRIVATE_CHANNEL_OUT;
                List<Integer> ids = seen(i);
                for (int id : ids) {
                    if (!first) sb.append(',');
                    first = false;
                    String name = NAMES.get(out + ":" + id);
                    sb.append("{\"channel\":").append(out).append(",\"id\":").append(id).append(",\"name\":");
                    if (name == null) sb.append("null");
                    else sb.append('"').append(Protocol.jsonEscape(name)).append('"');
                    sb.append(",\"current\":").append(id == current[i]).append('}');
                }
            }
        }
        sb.append(']');
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
                if ((Integer) e[6] != 0) sb.append(",\"room\":").append(e[6]);
                sb.append('}');
            }
        }
        sb.append(']');
        rooms(sb);
        return sb.append('}').toString();
    }

    /**
     * `sayin <5|6> <room> <text>`: a line into one of the international rooms or private channels
     * the player has joined, the one the game has up or not - the game's sender takes the room as
     * its target either way.
     */
    static String sayIn(int channel, int target, String text) {
        if ((channel != INTERNATIONAL_OUT && channel != PRIVATE_CHANNEL_OUT) || target <= 0
                || text == null || text.trim().isEmpty()) {
            return "error: sayin <5|6> <room> <text>";
        }
        synchronized (NAMES) {
            roomsRead = 0;
        }
        readRooms();
        boolean in;
        synchronized (NAMES) {
            in = seen(channel == INTERNATIONAL_OUT ? 0 : 1).contains(Integer.valueOf(target));
        }
        if (!in) return "error: not in that room";
        if (PrivateChat.sender() == null) return "error: the game's chat sender wasn't found";
        ChatQueue.add(channel, target, text);
        return "sent";
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
