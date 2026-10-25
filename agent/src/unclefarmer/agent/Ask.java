package unclefarmer.agent;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;

/**
 * The own-HP key's request without the key: `ask vitals` and `ask petvitals`.
 *
 * R calls game.i.I(), which calls game.N.d(own id); Shift+R calls game.i.J(), which calls
 * game.N.d(0) (javap 2026-10-04). N.d writes five bytes - 22, then the id as a big-endian int - with
 * writeAndFlush on the game's netty channel (N's static instance, its Channel field). Here the same
 * five bytes go through that same channel, so the server sees exactly what R sends, the tap sees it
 * as an OUT op=22 (Vitals then credits the answer), and netty puts a write from this thread onto its
 * own event loop. No method is picked by name, so a renamed game build can't make this call
 * something else: without the channel it answers an error and the bot presses R as before.
 *
 * No key means no chat box swallowing an "r", no modifier turning it into a hotkey, and nothing to
 * set up in a profile. The server answers as it does for R: HP, MP, buffs and debuffs (Vitals).
 */
final class Ask {

    static final int OP_VITALS = 22;
    /** A request this soon after the last one is refused, ours and the pet's alike: an answer only
     *  counts for whoever the last op=22 asked about, so a second request must wait for the first
     *  one's answer. */
    static final long MIN_GAP_MS = 1000L;

    private static volatile Field self;           // N's static N
    private static volatile Field channel;        // N's io.netty.channel.Channel
    private static volatile Method write;         // Channel.writeAndFlush(Object)
    private static volatile long last;

    private Ask() { }

    /** The sheet key's request (W_SELF_INFO): OUT op=37 `25 0d 00000001 <character id>`, the same
     *  "show this character's info" players send for each other, with our own id. */
    static final int OP_INFO = 37;
    static final int INFO_CHARACTER = 13;
    private static volatile long lastSheet;

    /** `ask vitals` (ours), `ask petvitals` (the pet out) or `ask sheet` (our character sheet, read
     *  without its window: see Sheet.expectQuiet): "asked", or why not. */
    static String handle(String what) {
        if (what.equals("sheet")) return sheet();
        int id;
        if (what.equals("vitals")) {
            id = ownId();
            if (id == 0) return "error: our id isn't known yet";
        } else if (what.equals("petvitals")) {
            id = 0;                                // the game asks about id 0 for the pet out
        } else {
            return "error: ask vitals|petvitals|sheet";
        }
        long now = System.currentTimeMillis();
        if (now - last < MIN_GAP_MS) return "error: too soon";
        String failed = send(packet(OP_VITALS, id), "ask " + what);
        if (failed != null) return failed;
        last = now;
        return "asked";
    }

    /**
     * Bytes onto the game's own connection, as the game's N senders write them: null when sent,
     * else why not. Only for requests the game itself sends byte for byte (see Ask, Act).
     */
    static String send(byte[] packet, String what) {
        Object ch = gameChannel();
        if (ch == null) return "error: the game's connection wasn't found";
        try {
            write.invoke(ch, (Object) packet);
            return null;
        } catch (Throwable t) {
            Log.error(what, t);
            return "error: " + Protocol.jsonEscape(String.valueOf(t));
        }
    }

    /** Our character sheet without the key: the server answers with the sheet (IN op=62) about a
     *  quarter of a second later, and Sheet reads it and keeps it off the screen. */
    private static String sheet() {
        int id = ownId();
        if (id == 0) return "error: our id isn't known yet";
        long now = System.currentTimeMillis();
        if (now - lastSheet < MIN_GAP_MS) return "error: too soon";
        Sheet.expectQuiet(now);
        String failed = send(sheetPacket(id), "ask sheet");
        if (failed != null) {
            Sheet.expectQuiet(0);
            return failed;
        }
        lastSheet = now;
        return "asked";
    }

    /** 37, 13, the int 1, then the character's id: what the sheet key sends (the recorder's
     *  captures, 2026-10-23: every one of 303 own sheets came after 25 0d 00000001 <own id>). */
    static byte[] sheetPacket(int id) {
        return new byte[] {(byte) OP_INFO, (byte) INFO_CHARACTER, 0, 0, 0, 1,
                           (byte) (id >>> 24), (byte) (id >>> 16), (byte) (id >>> 8), (byte) id};
    }

    /** op, then the id as four big-endian bytes: what N.d builds with DataOutputStream. */
    static byte[] packet(int op, int id) {
        return new byte[] {(byte) op, (byte) (id >>> 24), (byte) (id >>> 16), (byte) (id >>> 8), (byte) id};
    }

    /** The game's live channel (a reconnect replaces it, so it is read every time), or null. */
    private static Object gameChannel() {
        try {
            if (channel == null && !find()) return null;
            Object n = self.get(null);
            return n == null ? null : channel.get(n);
        } catch (Throwable t) {
            return null;
        }
    }

    /** In the class the chat sender lives in (game.N): its static self and its Channel field. */
    private static synchronized boolean find() throws Exception {
        if (channel != null) return true;
        Class<?> c = PrivateChat.senderClass();
        if (c == null) return false;
        Field s = null;
        Field ch = null;
        for (Field f : c.getDeclaredFields()) {
            if (Modifier.isStatic(f.getModifiers()) && f.getType() == c) s = f;
            if (!Modifier.isStatic(f.getModifiers()) && f.getType().getName().equals("io.netty.channel.Channel")) ch = f;
        }
        if (s == null || ch == null) return false;
        s.setAccessible(true);
        ch.setAccessible(true);
        write = ch.getType().getMethod("writeAndFlush", Object.class);
        self = s;
        channel = ch;
        Log.info("ask: requests go through " + c.getName() + "." + ch.getName());
        return true;
    }

    private static int ownId() {
        try {
            return Memory.selfId();
        } catch (Throwable t) {
            return World.selfId();
        }
    }
}
