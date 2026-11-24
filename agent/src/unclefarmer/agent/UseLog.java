package unclefarmer.agent;

import java.util.ArrayList;
import java.util.List;

/**
 * The game's own last requests (every OUT packet but op=12, our walking, which goes many times a
 * second), kept in memory only, so a by-hand action can be read off once: live on 2026-11-24 an
 * equipment piece thrown away from the bag's equipment tab went as `12 01 <uid:4> 02` (op 18,
 * panel 1, then 2), not as the bag's other items do (panel 0, minus the count). `outlog` lists them,
 * newest last, as hex. Nothing is written anywhere.
 */
final class UseLog {

    private static final int KEEP = 40;
    private static final int WALK = 12;
    private static final List<Object[]> LAST = new ArrayList<Object[]>();   // {ms, bytes}

    private UseLog() { }

    static void outbound(byte[] m) {
        if (m.length == 0 || (m[0] & 0xFF) == WALK) return;
        synchronized (LAST) {
            LAST.add(new Object[] {System.currentTimeMillis(), m.clone()});
            while (LAST.size() > KEEP) LAST.remove(0);
        }
    }

    static String list() {
        StringBuilder sb = new StringBuilder("{\"ok\":true,\"now\":").append(System.currentTimeMillis())
                .append(",\"out\":[");
        synchronized (LAST) {
            boolean first = true;
            for (Object[] e : LAST) {
                if (!first) sb.append(',');
                first = false;
                sb.append("{\"at\":").append(e[0]).append(",\"hex\":\"");
                for (byte b : (byte[]) e[1]) sb.append(String.format("%02x", b & 0xFF));
                sb.append("\"}");
            }
        }
        return sb.append("]}").toString();
    }
}
