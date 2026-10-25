package unclefarmer.agent;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Where team members are, from the game's own answer to the quick phrase code "/6" ("change your
 * target to your teammates on the map"). The phrase sends OUT op=29 `1d 00 00 00 00 <n:2 LE>`, a
 * counter the client steps on every press, and the server answers with one teammate on our map:
 *     IN op=59  3b <id:4> <x:4> <y:4> <name utf8>     (x, y at 1000 per tile)
 * Each press names the next one. With no team the answer is the spoken line "no team" instead.
 * Members off our map never come back, so a member here was on the map we were on (`map`).
 */
final class Team {

    private static final Map<Integer, Object[]> MEMBERS = new LinkedHashMap<Integer, Object[]>();  // id -> {name, x, y, at, map}

    private Team() { }

    /** IN op=59. */
    static void inbound(byte[] m) {
        if (m.length < 14) return;
        int id = Protocol.readInt(m, 1);
        double x = Protocol.tiles1000(Protocol.readInt(m, 5));
        double y = Protocol.tiles1000(Protocol.readInt(m, 9));
        String name = new String(m, 13, m.length - 13, Protocol.UTF8).trim();
        synchronized (MEMBERS) {
            MEMBERS.remove(Integer.valueOf(id));
            MEMBERS.put(Integer.valueOf(id), new Object[] {name, x, y, System.currentTimeMillis(), World.mapName()});
        }
    }

    /** {"now":ms,"members":[{"id","name","x","y","age_ms","map"}]}, newest last. */
    static String snapshot() {
        long now = System.currentTimeMillis();
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"now\":").append(now).append(",\"members\":[");
        boolean first = true;
        synchronized (MEMBERS) {
            for (Map.Entry<Integer, Object[]> e : MEMBERS.entrySet()) {
                Object[] v = e.getValue();
                if (!first) sb.append(',');
                first = false;
                sb.append("{\"id\":").append(e.getKey())
                  .append(",\"name\":\"").append(Protocol.jsonEscape((String) v[0])).append('"')
                  .append(",\"x\":").append(String.format(java.util.Locale.ROOT, "%.2f", (Double) v[1]))
                  .append(",\"y\":").append(String.format(java.util.Locale.ROOT, "%.2f", (Double) v[2]))
                  .append(",\"age_ms\":").append(now - (Long) v[3])
                  .append(",\"map\":\"").append(Protocol.jsonEscape((String) v[4])).append("\"}");
            }
        }
        return sb.append("]}").toString();
    }
}
