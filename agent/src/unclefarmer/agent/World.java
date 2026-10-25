package unclefarmer.agent;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Live picture of what the client knows: where we are, what map we are on, and every entity the
 * server is reporting.
 *
 * Read-only. It observes messages already passing the tap and never sends anything, so it carries
 * no risk of its own. On its own it is a radar: the server reports monsters out to roughly 80 tiles
 * with exact coordinates, which the game's audio never surfaces.
 */
final class World {

    /**
     * An entity is dropped if the server stops mentioning it for this long. Generous on purpose: an
     * idle monster sends nothing at all, so a short window would forget monsters that are still
     * standing there. Every entity reports its age so callers can judge for themselves, and deaths
     * remove entities immediately.
     */
    private static final long STALE_MS = 60000L;

    private static final Map<Integer, Entity> ENTITIES = new ConcurrentHashMap<Integer, Entity>();

    private static volatile String map = "?";
    private static volatile double selfX;
    private static volatile double selfY;
    private static volatile long selfSeen;

    /** False until we have a position for the map we are currently on. */
    private static volatile boolean selfKnown;

    /** Our own entity id, learned from the outbound map request. */
    private static volatile int selfId;

    static double selfX() {
        return selfX;
    }

    static double selfY() {
        return selfY;
    }

    static int selfId() {
        return selfId;
    }

    static boolean selfKnown() {
        return selfKnown;
    }

    /** The map name from the last op=6 we saw ("?" before any). */
    static String mapName() {
        return map;
    }

    private World() { }

    static final class Entity {
        final int id;
        volatile String name;
        volatile double x;
        volatile double y;
        volatile long seen;

        Entity(int id) {
            this.id = id;
        }

        /** Negative ids are monsters and NPCs; positive ids are players. */
        boolean monster() {
            return id < 0;
        }
    }

    /** Reads one message from the server. True when it is ours alone and the game must not see
     *  it: only the sheet that answers our own `ask sheet` (Sheet.inbound). */
    static boolean inbound(byte[] m) {
        if (m == null || m.length < 2) return false;
        try {
            int op = m[0] & 0xFF;
            if (op == 6) {
                changeMap(m);
            } else if (op == 32) {
                appeared(m);
            } else if (op == 36 || op == 37) {
                position(m);
            } else if (op == 41) {
                combat(m);
            } else if (op == 62) {
                return Sheet.inbound(m);
            } else if (op == 51) {
                Vitals.inbound(m);
            } else if (op == 60) {
                if (!Points.inbound(m)) {
                    PetInfo.inbound(m);
                    Said.inbound(m);
                }
            } else if (op == 56) {
                PrivateChat.inbound(m);
            } else if (op == 55) {
                GameChat.inbound(m);
            } else if (op == 8) {
                GameChat.roomNamed(m);
            } else if (op == 59) {
                Team.inbound(m);
            }
        } catch (Throwable t) {
            Log.error("world in", t);
        }
        return false;
    }

    static void outbound(byte[] m) {
        // one byte is a whole request: the points key (T) sends OUT op=23 with nothing after it
        if (m == null || m.length < 1) return;
        try {
            int op = m[0] & 0xFF;
            if (op == 12 && m.length >= 26) {
                // Our own movement, 32-bit at 1000 per tile. Only sent while actually moving, so
                // standing still does not mean the position went bad -- see selfKnown.
                double x = Protocol.tiles1000(Protocol.readInt(m, 17));
                double y = Protocol.tiles1000(Protocol.readInt(m, 21));
                selfX = x;
                selfY = y;
                selfSeen = System.currentTimeMillis();
                selfKnown = true;
            } else if (op == 6 && m.length >= 5) {
                selfId = Protocol.readInt(m, 1);   // the map request carries our own id
            } else if (op == 22) {
                Vitals.outbound(m);
            } else if (op == 23) {
                Points.outbound(m);
            } else if (op == 25) {
                PrivateChat.outbound(m);
            }
        } catch (Throwable t) {
            Log.error("world out", t);
        }
    }

    /** op=32: an entity came into view, carrying its name and where it was at that moment. */
    private static void appeared(byte[] m) {
        int id = Protocol.readInt(m, 1);
        int[] pos = Protocol.entityPosition(m);
        String name = Protocol.entityName(m);

        if (id == selfId && selfId != 0) {
            // Our own record. This is the only way to know where we are after arriving on a map,
            // before we have taken a step and sent an op=12.
            if (pos != null && !selfKnown) {
                selfX = Protocol.tiles100(pos[0]);
                selfY = Protocol.tiles100(pos[1]);
                selfSeen = System.currentTimeMillis();
                selfKnown = true;
            }
            return;
        }

        Entity e = entity(id);
        if (name != null) e.name = name;
        if (name != null && id > 0) PrivateChat.learn(id, name);     // a player: its name, for private messages
        if (pos != null) {
            e.x = Protocol.tiles100(pos[0]);
            e.y = Protocol.tiles100(pos[1]);
            e.seen = System.currentTimeMillis();
        }
    }

    /*
     * op=41 is plain text, and its "id,hp" lines report a death as hp 0. Removing those keeps the
     * bot from walking to something it already killed.
     */
    /** Recent casts seen in IN op=41: "caster,seq,skill" lines (e.g. "1234,446,Level 7 Aimed Shot";
     *  monsters cast too: "-62,32,Shooting"). Kept so the bot learns which of its key presses the
     *  game really cast (live 2026-10-04: 11 presses gave 4 casts) and times cooldowns from those. */
    private static final java.util.ArrayDeque<String> CASTS = new java.util.ArrayDeque<String>();
    /** Everyone's casts land here, monsters' too: in a busy spot 64 went by between two of the
     *  bot's reads, and with them our own casts and the hits that make a kill ours. */
    private static final int CASTS_KEPT = 256;

    /** Monster deaths seen in op=41 ("id,0" lines): [id, ms], newest last. With our casts' hits
     *  they tell the bot which monsters it killed or helped kill, whoever struck last - and the
     *  ones its area skills killed that it never selected. */
    private static final java.util.ArrayDeque<long[]> DEATHS = new java.util.ArrayDeque<long[]>();
    private static final int DEATHS_KEPT = 256;

    /** {"now":ms,"casts":[{"t":ms,"caster":id,"seq":n,"skill":"...","hits":[[target,result,amount]],
     *  "targets":[[id,hp]]}],"deaths":[[id,ms]]} newer than since (ms); "hits" and "targets" only
     *  when the packet carried them (see {@link #hits}, {@link #targets}). */
    static String casts(long since) {
        StringBuilder sb = new StringBuilder(256);
        sb.append("{\"now\":").append(System.currentTimeMillis()).append(",\"casts\":[");
        boolean first = true;
        synchronized (CASTS) {
            for (String c : CASTS) {
                int bar = c.indexOf('|');
                if (Long.parseLong(c.substring(0, bar)) <= since) continue;
                if (!first) sb.append(',');
                first = false;
                sb.append(c.substring(bar + 1));
            }
        }
        sb.append("],\"deaths\":[");
        first = true;
        synchronized (DEATHS) {
            for (long[] d : DEATHS) {
                if (d[1] <= since) continue;
                if (!first) sb.append(',');
                first = false;
                sb.append('[').append(d[0]).append(',').append(d[1]).append(']');
            }
        }
        return sb.append("]}").toString();
    }

    private static void died(int id) {
        if (id >= 0) return;          // players come back; only monsters are kills
        synchronized (DEATHS) {
            DEATHS.addLast(new long[] {id, System.currentTimeMillis()});
            while (DEATHS.size() > DEATHS_KEPT) DEATHS.removeFirst();
        }
    }

    private static void cast(int caster, int seq, String skill, String hits, String targets) {
        long now = System.currentTimeMillis();
        String row = now + "|{\"t\":" + now + ",\"caster\":" + caster + ",\"seq\":" + seq
                + ",\"skill\":\"" + Protocol.jsonEscape(skill) + "\""
                + (hits != null ? ",\"hits\":" + hits : "")
                + (targets != null ? ",\"targets\":" + targets : "") + "}";
        synchronized (CASTS) {
            CASTS.addLast(row);
            while (CASTS.size() > CASTS_KEPT) CASTS.removeFirst();
        }
    }

    /** Wear warnings from IN op=41: equipment uid -> {flag, ms}. The game's own handler
     *  (game.N.I) reads them as pairs after the skill name, "caster,seq,skill,uid,flag,...":
     *  flag 0 says the item is about to break, anything else that it broke (the client then marks
     *  that item's equipped field negative). They ride on whatever hit or cast involved the item. */
    private static final Map<Integer, long[]> WEAR = new ConcurrentHashMap<Integer, long[]>();

    /** {"now":ms,"wear":[{"uid":n,"broken":bool,"t":ms}]}: the newest warning per item after since. */
    static String wear(long since) {
        StringBuilder sb = new StringBuilder(128);
        sb.append("{\"now\":").append(System.currentTimeMillis()).append(",\"wear\":[");
        boolean first = true;
        for (Map.Entry<Integer, long[]> e : WEAR.entrySet()) {
            long[] v = e.getValue();
            if (v[1] <= since) continue;
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"uid\":").append(e.getKey()).append(",\"broken\":").append(v[0] != 0)
              .append(",\"t\":").append(v[1]).append('}');
        }
        return sb.append("]}").toString();
    }

    private static boolean hasLetter(String s) {
        for (int i = 0; i < s.length(); i++) {
            if (Character.isLetter(s.charAt(i))) return true;
        }
        return false;
    }

    /**
     * The packet's fifth CRLF section: what the cast did to each target that involves us (our own
     * casts, and casts at us), one "target,result,amount[,...]" line each. result & 7 is what the
     * game's combat announcer (game.k) reads out: 2 dodged, 4 defence break, 5 critical hit,
     * 6 heal; & 8 marks a damage-over-time tick, & 16 a heal-over-time one. As JSON
     * [[target,result,amount],...], or null when the packet has no such section.
     */
    private static String hits(String[] sections) {
        if (sections.length < 5) return null;
        StringBuilder sb = new StringBuilder(64).append('[');
        int n = 0;
        for (String line : sections[4].split("\n")) {
            String[] f = line.split(",");
            if (f.length < 3 || n >= 16) continue;
            try {
                int target = Integer.parseInt(f[0].trim());
                int result = Integer.parseInt(f[1].trim());
                long amount = Long.parseLong(f[2].trim());
                if (n++ > 0) sb.append(',');
                sb.append('[').append(target).append(',').append(result).append(',').append(amount).append(']');
            } catch (NumberFormatException ignored) {
                // not a result line
            }
        }
        return sb.append(']').toString();
    }

    /**
     * Whom the packet's cast struck, and their HP after it: the "id,hp" lines of the first CRLF
     * section after the cast line and the caster's own HP line. Sent for every caster - another
     * player's cast at a monster is the moment it stops being free, a second before its HP reading
     * shows it going down. As JSON [[id,hp],...], or null when there is no such line.
     */
    static String targets(String[] sections) {
        String[] rows = sections[0].split("\n");
        StringBuilder sb = new StringBuilder(32).append('[');
        int n = 0;
        for (int i = 2; i < rows.length && n < 16; i++) {
            String[] f = rows[i].split(",");
            if (f.length != 2) continue;
            try {
                int id = Integer.parseInt(f[0].trim());
                long hp = Long.parseLong(f[1].trim());
                if (n++ > 0) sb.append(',');
                sb.append('[').append(id).append(',').append(hp).append(']');
            } catch (NumberFormatException ignored) {
                // not an id,hp line
            }
        }
        return n == 0 ? null : sb.append(']').toString();
    }

    private static void combat(byte[] m) {
        String text = new String(m, 1, m.length - 1, Protocol.UTF8);
        String[] sections = text.split("\r\n");
        String hits = hits(sections);             // they belong to the packet's own cast line
        String targets = targets(sections);
        for (String line : text.split("[\r\n]+")) {
            int comma = line.indexOf(',');
            if (comma <= 0) continue;
            int comma2 = line.indexOf(',', comma + 1);
            if (comma2 > 0) {
                // caster,seq,skill name[,uid,flag]... (the name is never a number). Before the wear
                // pairs were read, a line carrying them was dropped whole: once the gear started to
                // wear out, no cast was seen and the bot lost track of who was attacking it.
                String[] f = line.split(",");
                String skill = f[2].trim();
                if (skill.isEmpty() || !hasLetter(skill)) continue;
                try {
                    cast(Integer.parseInt(f[0].trim()), Integer.parseInt(f[1].trim()), skill, hits, targets);
                    hits = null;
                    targets = null;
                } catch (NumberFormatException ignored) {
                    continue;   // Not a cast line.
                }
                long now = System.currentTimeMillis();
                for (int i = 3; i + 1 < f.length; i += 2) {
                    try {
                        WEAR.put(Integer.valueOf(Integer.parseInt(f[i].trim())),
                                 new long[] {Integer.parseInt(f[i + 1].trim()), now});
                    } catch (NumberFormatException ignored) {
                        break;
                    }
                }
                continue;
            }
            try {
                int id = Integer.parseInt(line.substring(0, comma).trim());
                int hp = Integer.parseInt(line.substring(comma + 1).trim());
                if (hp == 0) {
                    ENTITIES.remove(Integer.valueOf(id));
                    died(id);
                }
            } catch (NumberFormatException ignored) {
                // Not an id,hp line.
            }
        }
    }

    private static void changeMap(byte[] m) {
        String name = Protocol.mapName(m);
        if (name != null) {
            map = name;
        }
        // Either form means we left where we were, so nothing tracked there is valid any more --
        // including our own position, until we move or our own entity record arrives.
        ENTITIES.clear();
        selfKnown = false;
    }

    private static void position(byte[] m) {
        int off = Protocol.positionOffset(m);
        if (off < 0 || off + 4 > m.length) return;

        Entity e = entity(Protocol.readInt(m, 1));
        e.x = Protocol.tiles100(Protocol.readShort(m, off));
        e.y = Protocol.tiles100(Protocol.readShort(m, off + 2));
        e.seen = System.currentTimeMillis();
    }

    private static Entity entity(int id) {
        Entity e = ENTITIES.get(Integer.valueOf(id));
        if (e == null) {
            e = new Entity(id);
            Entity raced = ENTITIES.putIfAbsent(Integer.valueOf(id), e);
            if (raced != null) e = raced;
        }
        return e;
    }

    private static void prune(long now) {
        for (Map.Entry<Integer, Entity> entry : ENTITIES.entrySet()) {
            if (now - entry.getValue().seen > STALE_MS) ENTITIES.remove(entry.getKey());
        }
    }

    /** Entities the server is currently reporting, nearest first. */
    static List<Entity> entities() {
        long now = System.currentTimeMillis();
        prune(now);
        List<Entity> all = new ArrayList<Entity>(ENTITIES.values());
        final double x = selfX;
        final double y = selfY;
        Collections.sort(all, new Comparator<Entity>() {
            @Override
            public int compare(Entity a, Entity b) {
                return Double.compare(distance(a, x, y), distance(b, x, y));
            }
        });
        return all;
    }

    private static double distance(Entity e, double x, double y) {
        double dx = e.x - x;
        double dy = e.y - y;
        return Math.sqrt(dx * dx + dy * dy);
    }

    /** One-line JSON snapshot, hand-built to keep the agent dependency-free. */
    static String snapshot() {
        long now = System.currentTimeMillis();
        Collection<Entity> all = entities();

        StringBuilder sb = new StringBuilder(256 + all.size() * 80);
        sb.append("{\"map\":\"").append(Protocol.jsonEscape(map)).append('"');
        sb.append(",\"self\":{\"x\":").append(round(selfX)).append(",\"y\":").append(round(selfY))
                .append(",\"age\":").append(round((now - selfSeen) / 1000.0))
                .append(",\"known\":").append(selfKnown)
                .append(",\"id\":").append(selfId).append('}');
        sb.append(",\"entities\":[");

        boolean first = true;
        for (Entity e : all) {
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"id\":").append(e.id);
            sb.append(",\"kind\":\"").append(e.monster() ? "monster" : "player").append('"');
            if (e.name != null) {
                sb.append(",\"name\":\"").append(Protocol.jsonEscape(e.name)).append('"');
            }
            sb.append(",\"x\":").append(round(e.x)).append(",\"y\":").append(round(e.y));
            sb.append(",\"dist\":").append(round(distance(e, selfX, selfY)));
            sb.append(",\"age\":").append(round((now - e.seen) / 1000.0));
            sb.append('}');
        }
        sb.append("]}");
        return sb.toString();
    }

    private static String round(double v) {
        return String.format(java.util.Locale.ROOT, "%.2f", v);      // a comma decimal would break the JSON
    }
}
