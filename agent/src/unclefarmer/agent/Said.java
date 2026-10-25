package unclefarmer.agent;

import java.nio.charset.StandardCharsets;

/**
 * The game's last spoken lines: IN op=60, the client's general "say this" message (one byte, then
 * text). Talking to some NPCs and objects opens no menu; the answer comes this way instead
 * ("Requirements not met", "The energy switch is off"). Kept in memory only, never written
 * anywhere. Chat is a different message (op=55/56) and never comes through here; the points key's
 * answers are taken by Points first and left out.
 */
final class Said {

    private static final int KEEP = 40;
    private static final String[] TEXT = new String[KEEP];
    private static final long[] AT = new long[KEEP];
    private static int next;              // ring position of the next line
    private static int count;

    private Said() { }

    static synchronized void inbound(byte[] m) {
        if (m.length < 3) return;
        String s = new String(m, 2, m.length - 2, StandardCharsets.UTF_8).trim();
        if (s.isEmpty()) return;
        TEXT[next] = s;
        AT[next] = System.currentTimeMillis();
        next = (next + 1) % KEEP;
        if (count < KEEP) count++;
    }

    /** {"now":ms,"said":[{"t":ms,"text":...}]}: the lines after since, oldest first. */
    static synchronized String since(long since) {
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"now\":").append(System.currentTimeMillis()).append(",\"said\":[");
        boolean first = true;
        for (int i = 0; i < count; i++) {
            int j = (next - count + i + KEEP) % KEEP;
            if (AT[j] <= since) continue;
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"t\":").append(AT[j]).append(",\"text\":\"").append(Protocol.jsonEscape(TEXT[j])).append("\"}");
        }
        return sb.append("]}").toString();
    }
}
