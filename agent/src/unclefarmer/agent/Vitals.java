package unclefarmer.agent;

/**
 * Our own HP and MP, as the game answers the "own HP" key (W_SELF_HP, R by default).
 *
 * The key sends OUT op=22 with our own id -- the same "tell me about this one" request that
 * selecting a target sends -- and the server answers IN op=51: two ints, HP then MP, which the
 * client speaks as "hp;mp" (game.i.a(int,int,int,int); the same numbers the character sheet
 * shows). op=51 also answers for monsters, so a reply only counts as
 * ours when the last op=22 asked about us.
 *
 * MP exists nowhere else on the client except the character sheet, which opens a window; this is
 * the quiet way to read it.
 *
 * After the two ints come two optional bytes: how many buffs and how many debuffs are on us (game.N
 * reads them when the packet is long enough and the client speaks them after hp;mp). Both are left
 * off when there are none, so a short reply means 0 and 0.
 */
final class Vitals {

    static volatile int hp = -1;
    static volatile int mp = -1;
    static volatile int buffs;
    static volatile int debuffs;
    static volatile long seen;
    static volatile int id;          // whose numbers they are (a relog changes character)

    private static volatile int asked;
    private static volatile long askedAt;

    private Vitals() { }

    /** OUT op=22: whose numbers the next op=51 will be (id 0: the pet's, from the pet HP key). */
    static void outbound(byte[] m) {
        if (m.length >= 5) {
            asked = Protocol.readInt(m, 1);
            askedAt = System.currentTimeMillis();
        }
    }

    /** IN op=51: hp, mp (and sometimes two status bytes). */
    static void inbound(byte[] m) {
        if (m.length < 9) return;
        long now = System.currentTimeMillis();
        if (now - askedAt > 3000) return;
        if (asked == 0) {
            // the pet HP key asks about id 0: the answer is the out pet's HP and MP
            PetInfo.vitals(Protocol.readInt(m, 1), Protocol.readInt(m, 5));
            askedAt = 0;
            return;
        }
        if (asked != ownId()) return;
        hp = Protocol.readInt(m, 1);
        mp = Protocol.readInt(m, 5);
        buffs = m.length >= 10 ? m[9] & 0xFF : 0;
        debuffs = m.length >= 11 ? m[10] & 0xFF : 0;
        id = asked;
        seen = now;
    }

    /** ,"vitals":{"hp","mp","buffs","debuffs","age_ms"} for `mem self`. */
    static void json(StringBuilder sb, long now) {
        sb.append(",\"vitals\":{\"hp\":").append(hp).append(",\"mp\":").append(mp)
                .append(",\"buffs\":").append(buffs).append(",\"debuffs\":").append(debuffs)
                .append(",\"age_ms\":").append(now - seen).append('}');
    }

    private static int ownId() {
        try {
            return Memory.selfId();
        } catch (Throwable t) {
            return World.selfId();
        }
    }
}
