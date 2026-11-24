package unclefarmer.agent;

import java.util.ArrayList;
import java.util.List;

/**
 * The game's own last item requests (OUT op=18: use, or discard with minus a count), kept in
 * memory only, so a by-hand action can be read off once: which panel byte the game sends for
 * equipment thrown away from the bag's equipment tab (Act.discardPacket sends the bag's, 0).
 * `uselog` lists them, newest last, as hex. Nothing is written anywhere.
 */
final class UseLog {

    private static final int KEEP = 20;
    private static final List<Object[]> LAST = new ArrayList<Object[]>();   // {ms, bytes}

    private UseLog() { }

    static void outbound(byte[] m) {
        synchronized (LAST) {
            LAST.add(new Object[] {System.currentTimeMillis(), m.clone()});
            while (LAST.size() > KEEP) LAST.remove(0);
        }
    }

    static String list() {
        StringBuilder sb = new StringBuilder("{\"ok\":true,\"now\":").append(System.currentTimeMillis())
                .append(",\"uses\":[");
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
