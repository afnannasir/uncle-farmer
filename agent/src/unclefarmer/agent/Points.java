package unclefarmer.agent;

import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Our accumulation points, fusion power and the pet's amiability, as the game answers the
 * "accumulation points" key (W_SELF_LJ, T by default).
 *
 * The key sends OUT op=23 with nothing after the opcode (game.i.K -> game.N.e(-1)), except for a
 * Priest (game.B.g 13 or 14: class (g - 1) / 2 = 6), whose request carries one more byte, the
 * PK mode (game.i.Q, 0-5); taking only the one-byte request left Priests with no amiability, so
 * no Rice Rolls for their pets. The server
 * answers IN op=60, the client's general "say this" message (one byte, then text), with
 * "points;fusion power" and, when a pet is out, ";amiability". Points are the class's own
 * resource: a Warrior's or Orc's rage (0-300), how many targets a Hunter has marked, an
 * Assassin's combos, or two counts at once ("s1 e3"). op=60 also carries target descriptions and
 * other speech, so a reply only counts when it has that shape and comes soon after our request.
 */
final class Points {

    private static final Pattern REPLY =
            Pattern.compile("\\s*([^;]{1,14}?)\\s*;\\s*([0-9]+(?:\\.[0-9]+)?)\\s*(?:;\\s*([0-9]+(?:\\.[0-9]+)?)\\s*)?");

    static volatile String raw;           // the points as the game wrote them ("300", "2", "s1 e3")
    static volatile double fusion = -1;
    static volatile double pet = -1;      // -1: no pet out
    static volatile long seen;

    private static volatile long askedAt;

    private Points() { }

    /** OUT op=23 on its own, or with a Priest's PK mode byte: the request. */
    static void outbound(byte[] m) {
        if (m.length == 1 || m.length == 2) askedAt = System.currentTimeMillis();
    }

    /** IN op=60: speech; ours when it has the points shape and follows a request. True if taken. */
    static boolean inbound(byte[] m) {
        if (m.length < 4) return false;
        long now = System.currentTimeMillis();
        if (now - askedAt > 3000) return false;
        Matcher r = REPLY.matcher(new String(m, 2, m.length - 2, StandardCharsets.UTF_8));
        if (!r.matches()) return false;
        raw = r.group(1);
        fusion = Double.parseDouble(r.group(2));
        pet = r.group(3) != null ? Double.parseDouble(r.group(3)) : -1;
        seen = now;
        askedAt = 0;
        return true;
    }

    /** ,"points":{...} for `mem self`, or nothing before the first reply. */
    static void json(StringBuilder sb) {
        if (seen == 0) return;
        sb.append(",\"points\":{\"raw\":\"").append(Protocol.jsonEscape(raw)).append('"');
        String n = raw.trim();
        if (n.matches("[0-9]+")) sb.append(",\"n\":").append(n);
        sb.append(",\"fusion\":").append(fusion);
        if (pet >= 0) sb.append(",\"pet\":").append(pet);
        sb.append(",\"age_ms\":").append(System.currentTimeMillis() - seen).append('}');
    }
}
