package unclefarmer.agent;

import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The rank of each profession skill (0 Newbie, 1 Oldhand, 2 Elite, 3 Expert, 4 Master, 5 Grand
 * Master), from IN op=17: the profession skill list the server sends at login, one "id,rank,name"
 * line per skill ("13,5,Fishing"). The client keeps the id and the name (game.aq) and drops the
 * rank, so it is read here, before the game sees the message. 0 is also what a skill that comes by
 * itself (Snuggle, Blood Transfusion) carries before the character has a spouse or a pupil.
 */
final class Profession {

    private static final Map<Integer, Integer> RANKS = new LinkedHashMap<Integer, Integer>();

    private Profession() { }

    /** IN op=17: the whole list, so it replaces what was read before (a skill forgotten goes). */
    static void inbound(byte[] m) {
        if (m.length < 2) return;
        Map<Integer, Integer> got = new LinkedHashMap<Integer, Integer>();
        for (String line : new String(m, 1, m.length - 1, StandardCharsets.ISO_8859_1).split("\n")) {
            String[] f = line.trim().split(",", 3);
            if (f.length < 3 || !f[0].matches("[0-9]{1,6}") || !f[1].matches("[0-9]{1,3}")) continue;
            got.put(Integer.valueOf(f[0]), Integer.valueOf(f[1]));
        }
        if (got.isEmpty()) return;
        synchronized (RANKS) {
            RANKS.clear();
            RANKS.putAll(got);
        }
    }

    /** {"13":5,"21":3} for `mem skills`: skill id -> rank; {} before the list arrived. */
    static void json(StringBuilder sb) {
        sb.append('{');
        synchronized (RANKS) {
            boolean first = true;
            for (Map.Entry<Integer, Integer> e : RANKS.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                sb.append('"').append(e.getKey()).append("\":").append(e.getValue());
            }
        }
        sb.append('}');
    }
}
