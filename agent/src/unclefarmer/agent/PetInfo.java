package unclefarmer.agent;

import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The pet's own numbers, from two answers of the game.
 *
 * The pet panel (bag panel 3): when the cursor lands on a pet the client sends OUT op=20
 * `03 <pet id>` and the server answers IN op=60 with the pet's description, e.g. "Shorthair Cat
 * Mittens, level 63, ..., amiability 72.0, HP 98000/100000, MP 75000/110000, skill: ...". That is
 * the only place the pet's maximum MP shows up.
 *
 * The pet HP key (W_PET_HP, Shift + the own-HP key): the client sends OUT op=22 with id 0 and the
 * server answers IN op=51 with two ints, the out pet's HP then MP - the same answer the own-HP key
 * gets for us (see {@link Vitals}). It opens no window, so it works in a fight too.
 */
final class PetInfo {

    private static final Pattern DESCRIPTION = Pattern.compile(
            "^(.*?), level \\d+.*?amiability ([0-9.]+), HP (\\d+)/(\\d+), MP (\\d+)/(\\d+)");

    static volatile String name;
    static volatile double amiability = -1;
    static volatile long hp, maxHp, mp, maxMp;
    static volatile long described;      // when the description last came

    static volatile long vitalsHp = -1, vitalsMp = -1;
    static volatile long vitalsSeen;

    private PetInfo() { }

    /** IN op=60: speech; a pet description when it has that shape. Never consumes the line. */
    static void inbound(byte[] m) {
        if (m.length < 40) return;
        Matcher d = DESCRIPTION.matcher(new String(m, 2, m.length - 2, StandardCharsets.UTF_8));
        if (!d.find()) return;
        try {
            name = d.group(1).replace(">><<", "").replace("|||", "").trim();
            amiability = Double.parseDouble(d.group(2));
            hp = Long.parseLong(d.group(3));
            maxHp = Long.parseLong(d.group(4));
            mp = Long.parseLong(d.group(5));
            maxMp = Long.parseLong(d.group(6));
            described = System.currentTimeMillis();
        } catch (NumberFormatException ignored) {
            // not a pet description after all
        }
    }

    /** The answer to the pet HP key (op=51 after an op=22 for id 0). */
    static void vitals(int hpNow, int mpNow) {
        vitalsHp = hpNow;
        vitalsMp = mpNow;
        vitalsSeen = System.currentTimeMillis();
    }

    /** ,"pet_vitals":{...},"pet_info":{...} for `mem self`, each only once it has been seen. */
    static void json(StringBuilder sb) {
        long now = System.currentTimeMillis();
        if (vitalsSeen != 0) {
            sb.append(",\"pet_vitals\":{\"hp\":").append(vitalsHp).append(",\"mp\":").append(vitalsMp)
              .append(",\"age_ms\":").append(now - vitalsSeen).append('}');
        }
        if (described != 0) {
            sb.append(",\"pet_info\":{\"name\":\"").append(Protocol.jsonEscape(name)).append('"')
              .append(",\"amiability\":").append(amiability)
              .append(",\"hp\":").append(hp).append(",\"maxhp\":").append(maxHp)
              .append(",\"mp\":").append(mp).append(",\"maxmp\":").append(maxMp)
              .append(",\"age_ms\":").append(now - described).append('}');
        }
    }
}
