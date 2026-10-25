package unclefarmer.agent;

import java.awt.Frame;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.ConcurrentModificationException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads the client's own world object by reflection: position, hp, bag and hotkey slots, exactly as
 * the game holds them. The packet view in {@link World} has to reconstruct these and never sees the
 * bag or slots at all.
 *
 * Read-only: fields are read, never written, and nothing is called on game objects.
 *
 * The names below are the game's obfuscated ones, confirmed with javap against out/classes on
 * 2026-10-01. A game patch can reshuffle them, so every field is checked for the expected type
 * before use, and a mismatch is reported as "fields moved" instead of returning wrong numbers.
 *
 *   game.Win (a Frame)  .n  -> the world (game.i)
 *   world  .i  HashMap<Integer, game.B>   every entity the client knows, keyed by id
 *          .E  int                        our own entity id
 *          .j  int / .k String            map id / map name
 *          .ad ArrayList<game.K>          bag rows
 *          .aj HashMap<Integer, int[]>    hotkey slots 1..12 -> [type, id]
 *   game.B (entity base)  .e int, .f int type, .g short facing, .h String name, .i/.j int x/y
 *   game.ak extends B     .r int hp, .s int max hp                    (players and monsters)
 *
 * Positions are 1000 per tile (live: memory 200000,206270 = packets 200.0,206.27). hp/max hp are
 * stats 1 and 2 of the client's own stat-update handler in game.i, confirmed live 2026-10-01.
 * Level is not read yet: the short fields z/A hold 1032/1033 on us AND on every town NPC, so
 * neither is level. Use `memprobe` to look at the raw fields when identifying more stats.
 *   game.K extends B      .m int count                                (a bag row; name is .h)
 *
 *   world  .P  int   target mode, cycled by v:  0 all, 1 own side, 2 enemies, 3 NPCs
 *          .Q  int   PK mode, cycled by b:  0 pacifist, 1 pacifist with counterattack, then the
 *                    others up to 5 (game.i.k(int) / l(int) speak them; found 2026-10-01)
 *          .R  int   -1 normally; anything else locks the PK mode (b does nothing)
 *          .W  int   which panel the arrow keys work on (left/right, game.i.q/r): 0 = the bag
 *          .Y  int   category within the panel (lctrl+alt+up/down, U/V); 0 = the whole bag list
 *          .X  int   cursor in that list (up/down, s/t); -1 = none yet. In the bag it indexes .ad
 *          .av game.B the selected target (set by the target keys, game.i.o). NOT .G: o() resets
 *                    .G to 0 on every press; .G only holds the special team targets 1..10.
 *   See {@link Dialogs} for open NPC dialogs (world .U).
 */
final class Memory {

    static final String WIN_CLASS = "game.Win";
    static final String CHAT_MODEL_CLASS = "game.t";
    static final String CHAT_INTERNATIONAL = "i";
    static final String CHAT_PRIVATE_CHANNEL = "j";
    static final String WIN_WORLD = "n";

    static final String W_ENTITIES = "i";
    static final String W_SELF_ID = "E";
    static final String W_MAP_ID = "j";
    static final String W_MAP_NAME = "k";
    static final String W_BAG = "ad";
    static final String W_SLOTS = "aj";
    static final String W_TARGET_MODE = "P";
    static final String W_PK_MODE = "Q";
    static final String W_PK_LOCK = "R";
    static final String W_PANEL = "W";
    static final String W_CATEGORY = "Y";
    static final String W_CURSOR = "X";
    static final String W_TARGET = "av";
    static final String W_LAST_TARGET = "aw";      // the target keys set it with av (game.i.o)
    static final String W_TEAM_TARGET = "G";       // the team view's special targets 1..10; o() sets it to 0
    static final String W_SELF_X = "g";            // our position, 1/1000 of a tile (game.i.o reads them)
    static final String W_SELF_Y = "h";
    /** game.i.a(double,double,double,double): the line of sight between two tiles, walls and the
     *  edges of one-way door tiles included. The target keys take nothing it says no to. */
    static final String W_SIGHT = "a";
    /** game.B.f bits the target keys leave out (game.i.o: f & 48). */
    static final int F_UNSELECTABLE = 48;
    /** How far the target keys reach, 1/1000 of a tile (game.i.o: 20000.0). */
    static final double SELECT_REACH = 20000.0;
    static final String E_ID = "e";

    static final String E_KIND = "f";
    static final String E_FACING = "g";
    static final String E_NAME = "h";
    static final String E_X = "i";
    static final String E_Y = "j";
    static final String E_HP = "r";
    static final String E_MAX_HP = "s";
    /** game.ak.A: which side a living thing is on. The target key's own filter (game.i.o) treats -1
     *  as an NPC (townsfolk, shops, doors, and critters such as ducks and hens) and leaves it out
     *  of "enemies"; monsters are -2; guards, other players' summons and war-zone towers 0; other
     *  players' traps and totems their owner's number (1000 and up). */
    static final String E_SIDE = "A";
    /** game.B.f bit 2: the game's hostility check (game.i.a(ak,int,ak)) never counts it as an enemy. */
    static final int F_PROTECTED = 2;
    static final String BAG_COUNT = "m";
    // Panel lists (javap 2026-10-04): W 0..6 = items ad, equipment ae, fusion beasts af, pets t,
    // combat skills ag, talents ah, profession skills ai; ak / al = attack / profession key -> skill.
    static final String W_EQUIP = "ae";
    static final String W_BEASTS = "af";
    static final String W_PETS = "t";
    static final String W_COMBAT = "ag";
    static final String W_TALENTS = "ah";
    static final String W_PROFESSION = "ai";
    static final String W_ATTACK_KEYS = "ak";
    static final String W_PROFESSION_KEYS = "al";
    /** json key -> field, per row class. game.M beast: a id, b active, c name, d usable-at ms, e expires-at ms. */
    static final String[][] BEAST_ROW = {{"id", "a"}, {"active", "b"}, {"name", "c"}, {"usable_at", "d"}, {"expires_at", "e"}};
    /** game.I extends B: e uid, h name; m equipped(1); n o p unnamed ints; q r times (r = expiry ms). */
    static final String[][] EQUIP_ROW = {{"uid", "e"}, {"type", "g"}, {"name", "h"}, {"equipped", "m"}, {"n", "n"}, {"o", "o"}, {"p", "p"}, {"q", "q"}, {"expires_at", "r"}};
    /** game.f: a skill id (the attack-key map's value), b kind, c name. */
    static final String[][] COMBAT_ROW = {{"id", "a"}, {"kind", "b"}, {"name", "c"}};
    static final String[][] TALENT_ROW = {{"id", "a"}, {"b", "b"}, {"name", "c"}};
    static final String[][] PROFESSION_ROW = {{"id", "a"}, {"name", "b"}};
    static final String[][] PET_ROW = {{"id", "a"}, {"usable_at", "b"}, {"name", "c"}};
    /** game.K extends B: e uid, g item type (= a hotkey slot's first number), h name, m count; n o p q unnamed. */
    static final String[][] BAG_ROW = {{"uid", "e"}, {"type", "g"}, {"name", "h"}, {"count", "m"}, {"n", "n"}, {"o", "o"}, {"p", "p"}, {"q", "q"}};

    private static final int COPY_RETRIES = 5;

    private Memory() { }

    /** Thrown with a short reason that goes straight back to the caller. */
    static final class Unavailable extends Exception {
        private static final long serialVersionUID = 1L;

        Unavailable(String why) {
            super(why, null, false, false);
        }
    }

    // ---------------------------------------------------------------- locating the world

    private static volatile Object cachedWin;

    /** The live world object, or Unavailable explaining why there is none (e.g. not logged in). */
    static Object world() throws Unavailable {
        Object win = cachedWin;
        if (win == null || !((Frame) win).isDisplayable()) {
            win = null;
            for (Frame f : Frame.getFrames()) {
                if (f.getClass().getName().equals(WIN_CLASS)) {
                    win = f;
                    break;
                }
            }
            if (win == null) throw new Unavailable("nowin: no " + WIN_CLASS + " frame");
            cachedWin = win;
        }
        Object world = get(win, WIN_WORLD, null);
        if (world == null) throw new Unavailable("noworld: not logged in");
        return world;
    }

    /** The chat model's two joined rooms, {international room, private channel} (0: none). The
     *  world (game.i) holds the chat model (game.t) in one field; the game's own chat line sends
     *  international lines with its field i and private-channel lines with its field j (javap of
     *  game.Win, 2026-10-21). Found by type, read by name. */
    static int[] chatRooms() throws Unavailable {
        Object w = world();
        for (Class<?> k = w.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            for (Field f : k.getDeclaredFields()) {
                if (!f.getType().getName().equals(CHAT_MODEL_CLASS)) continue;
                try {
                    f.setAccessible(true);
                    Object t = f.get(w);
                    if (t == null) return new int[] {0, 0};
                    return new int[] {intField(t, CHAT_INTERNATIONAL), intField(t, CHAT_PRIVATE_CHANNEL)};
                } catch (IllegalAccessException e) {
                    throw new Unavailable("chat model unreadable");
                }
            }
        }
        throw new Unavailable("no chat model in the world");
    }

    // ---------------------------------------------------------------- snapshots

    /** Everything in one reply. */
    static String snapshot() {
        try {
            Object world = world();
            StringBuilder sb = new StringBuilder(1024);
            sb.append("{\"ok\":true,\"map\":");
            map(world, sb);
            sb.append(",\"self\":");
            self(world, sb);
            sb.append(",\"entities\":");
            entities(world, sb);
            sb.append(",\"bag\":");
            bag(world, sb);
            sb.append(",\"slots\":");
            slots(world, sb);
            sb.append(",\"modes\":");
            modes(world, sb);
            sb.append(",\"ui\":");
            ui(world, sb);
            sb.append('}');
            return sb.toString();
        } catch (Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            Log.error("memory snapshot", t);
            return error("failed: " + t);
        }
    }

    /** What the bot reads every iteration: map, self and entities, nothing else. */
    static String state() {
        try {
            Object world = world();
            StringBuilder sb = new StringBuilder(1024);
            sb.append("{\"ok\":true,\"map\":");
            map(world, sb);
            sb.append(",\"self\":");
            self(world, sb);
            sb.append(",\"entities\":");
            entities(world, sb);
            return sb.append('}').toString();
        } catch (Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            Log.error("memory state", t);
            return error("failed: " + t);
        }
    }

    /** One section only: map, self, entities, bag, slots, or the lean state. */
    static String section(String which) {
        if (which.equals("state")) return state();
        try {
            Object world = world();
            StringBuilder sb = new StringBuilder(512);
            if (which.equals("map")) map(world, sb);
            else if (which.equals("self")) self(world, sb);
            else if (which.equals("entities")) entities(world, sb);
            else if (which.equals("bag")) bag(world, sb);
            else if (which.equals("slots")) slots(world, sb);
            else if (which.equals("modes")) modes(world, sb);
            else if (which.equals("ui")) ui(world, sb);
            else if (which.equals("bagfull")) rows(world, W_BAG, BAG_ROW, sb);
            else if (which.equals("beasts")) rows(world, W_BEASTS, BEAST_ROW, sb);
            else if (which.equals("equipment")) rows(world, W_EQUIP, EQUIP_ROW, sb);
            else if (which.equals("pets")) rows(world, W_PETS, PET_ROW, sb);
            else if (which.equals("skills")) skills(world, sb);
            else return error("unknown section: " + which);
            return sb.toString();
        } catch (Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            Log.error("memory " + which, t);
            return error("failed: " + t);
        }
    }

    /**
     * For checking the field map after a game patch: our own entity rendered field by field, next
     * to what the packet view believes, so the two can be compared by eye.
     */
    static String probe() {
        try {
            Object world = world();
            Object me = selfEntity(world);
            return "self=" + Describe.of(me)
                    + " | packets: x=" + World.selfX() + " y=" + World.selfY()
                    + " known=" + World.selfKnown();
        } catch (Unavailable u) {
            return error(u.getMessage());
        } catch (Throwable t) {
            return error("failed: " + t);
        }
    }

    /** The F1 settings menu's checkboxes, as bits of world.p (javap 2026-10-04, game.i's settings
     *  menu): 256 skip corpses switching ally targets, 512 ... enemy targets, 1024 swap the follow
     *  and stationary attack hotkeys, 8192 attack skills auto-lock a new target in the facing
     *  direction; the rest are speech options. */
    static final String W_OPTIONS = "p";

    private static void modes(Object world, StringBuilder sb) throws Unavailable {
        sb.append("{\"target\":").append(intField(world, W_TARGET_MODE));
        sb.append(",\"pk\":").append(intField(world, W_PK_MODE));
        sb.append(",\"pk_locked\":").append(intField(world, W_PK_LOCK) != -1);
        int o = intField(world, W_OPTIONS);
        sb.append(",\"options\":").append(o);
        sb.append(",\"swap_attack\":").append((o & 1024) != 0);
        sb.append(",\"auto_lock_target\":").append((o & 8192) != 0);
        sb.append(",\"skip_enemy_corpses\":").append((o & 512) != 0).append('}');
    }

    private static void ui(Object world, StringBuilder sb) throws Unavailable {
        sb.append("{\"panel\":").append(intField(world, W_PANEL));
        sb.append(",\"category\":").append(intField(world, W_CATEGORY));
        sb.append(",\"cursor\":").append(intField(world, W_CURSOR));
        Object target = get(world, W_TARGET, null);
        sb.append(",\"target\":").append(target == null ? "null" : String.valueOf(intField(target, E_ID)));
        Object dialogs = get(world, Dialogs.W_DIALOGS, ArrayList.class);
        sb.append(",\"dialogs\":").append(dialogs == null ? 0 : ((ArrayList<?>) dialogs).size());
        sb.append(",\"attack_page\":").append(attackPage());
        sb.append('}');
    }

    /** Which attack-key page is up: Win.y, flipped by W_ATTACK_PAGETURN (Caps Lock); 0 = keys 1-10,
     *  1 = keys 11-20 (javap 2026-10-04: says 第一页 / 第二页). -1 when it can't be read. */
    static final String WIN_ATTACK_PAGE = "y";

    static int attackPage() {
        for (Frame f : Frame.getFrames()) {
            if (!f.getClass().getName().equals(WIN_CLASS)) continue;
            Field fl = field(f.getClass(), WIN_ATTACK_PAGE);
            try {
                return fl != null && fl.getType() == int.class ? fl.getInt(f) : -1;
            } catch (IllegalAccessException e) {
                return -1;
            }
        }
        return -1;
    }

    private static void map(Object world, StringBuilder sb) throws Unavailable {
        sb.append("{\"id\":").append(intField(world, W_MAP_ID));
        sb.append(",\"name\":");
        str(sb, (String) get(world, W_MAP_NAME, String.class));
        sb.append('}');
    }

    private static void self(Object world, StringBuilder sb) throws Unavailable {
        int id = intField(world, W_SELF_ID);
        entity(selfEntity(world), id, sb);
        // Level and MP aren't held by the client; they come from the last character sheet we saw.
        if (Sheet.seen != 0 && Sheet.id == id) {
            sb.setLength(sb.length() - 1);
            sb.append(",\"sheet\":{\"level\":").append(Sheet.level)
                    .append(",\"mp\":").append(Sheet.mp).append(",\"maxmp\":").append(Sheet.maxMp)
                    .append(",\"hp\":").append(Sheet.hp).append(",\"maxhp\":").append(Sheet.maxHp)
                    .append(",\"load\":").append(Sheet.load).append(",\"maxload\":").append(Sheet.maxLoad)
                    .append(",\"fusion\":").append(Sheet.fusion)
                    .append(",\"drop_seconds\":").append(Sheet.dropSeconds)
                    .append(",\"counter\":").append(Sheet.counter).append(",\"counter_name\":\"")
                    .append(Protocol.jsonEscape(Sheet.counterName)).append('"')
                    .append(",\"contribution\":").append(Sheet.contribution)
                    .append(",\"xp_needed\":").append(Sheet.xpNeeded)
                    .append(",\"merit\":").append(Sheet.merit)
                    .append(",\"age\":").append((System.currentTimeMillis() - Sheet.seen) / 1000)
                    .append("}}");
        }
        // HP and MP as the game answered the own-HP key (R), without opening any window.
        if (Vitals.seen != 0 && Vitals.id == id) {
            sb.setLength(sb.length() - 1);
            Vitals.json(sb, System.currentTimeMillis());
            sb.append('}');
        }
        // Rage / marks / combos, fusion power and the pet's amiability, as the T key's answer.
        if (Points.seen != 0) {
            sb.setLength(sb.length() - 1);
            Points.json(sb);
            sb.append('}');
        }
        // The pet's HP and MP (the pet HP key) and its description (the pet panel).
        if (PetInfo.vitalsSeen != 0 || PetInfo.described != 0) {
            sb.setLength(sb.length() - 1);
            PetInfo.json(sb);
            sb.append('}');
        }
    }

    /**
     * Make an entity the game's selected target, as the target keys do: game.i.o(int,int) sets
     * av and aw to the one it picked and speaks it, nothing more - no packet goes out; the next
     * skill carries av's id. Done under the world's own lock (game.i's methods are synchronized),
     * so a key or a packet handler of the game never sees half of it.
     *
     * Only what the keys would take: within their reach, not flagged unselectable, and in a clear
     * line by the game's own sight check. Without those checks (agent 40) a Hunter in turkey cave,
     * a maze of one-way doors, was given targets behind door edges our map doesn't treat as walls;
     * the server refused every cast and each one cost the 6 s no-damage wait (221 in a night).
     */
    static String select(int id) throws Unavailable {
        Object world = world();
        if (id == selfId()) return "error: that is us";
        Object e = entityMap(world).get(Integer.valueOf(id));
        if (e == null) return "error: " + id + " isn't in sight";
        Field av = field(world.getClass(), W_TARGET);
        if (av == null || !av.getType().isInstance(e)) throw moved(world, W_TARGET);
        Field aw = field(world.getClass(), W_LAST_TARGET);
        try {
            synchronized (world) {
                String no = unselectable(world, e);
                if (no != null) return "refused: " + id + " " + no;
                av.set(world, e);
                if (aw != null && aw.getType().isInstance(e)) aw.set(world, e);
                // the keys also clear a team target (game.i.o: G = 0); one left over (the /6 quick
                // phrase sets it) would take the Enter that should open an NPC's menu
                Field g = field(world.getClass(), W_TEAM_TARGET);
                if (g != null && g.getType() == int.class) g.setInt(world, 0);
            }
        } catch (IllegalAccessException x) {
            throw moved(world, W_TARGET);
        }
        return "selected";
    }

    private static volatile Method sight;

    /** Why the target keys would not take this entity, or null when they would (game.i.o). */
    private static String unselectable(Object world, Object e) throws Unavailable {
        if ((intField(e, E_KIND) & F_UNSELECTABLE) != 0) return "can't be selected";
        int mx = intField(world, W_SELF_X), my = intField(world, W_SELF_Y);
        int ex = intField(e, E_X), ey = intField(e, E_Y);
        if (Math.hypot(ex - mx, ey - my) > SELECT_REACH) return "is out of the target keys' reach";
        Method m = sight;
        if (m == null) {
            try {
                m = world.getClass().getDeclaredMethod(W_SIGHT, double.class, double.class, double.class, double.class);
                if (m.getReturnType() != boolean.class) throw moved(world, W_SIGHT);
                m.setAccessible(true);
                sight = m;
            } catch (NoSuchMethodException x) {
                throw moved(world, W_SIGHT);
            }
        }
        try {
            Object clear = m.invoke(world, (double) ((mx + 500) / 1000), (double) ((my + 500) / 1000),
                    (double) ((ex + 500) / 1000), (double) ((ey + 500) / 1000));
            return Boolean.TRUE.equals(clear) ? null : "isn't in a clear line";
        } catch (ReflectiveOperationException x) {
            throw moved(world, W_SIGHT);
        } catch (RuntimeException x) {
            return null;          // the game's own check failed (a tile it has no data for): as before
        }
    }

    /** True when an item with this uid (game.B.e) is in our bag (world.ad). */
    static boolean inBag(int uid) throws Unavailable {
        Object bag = get(world(), W_BAG, ArrayList.class);
        if (bag == null) throw new Unavailable("nobag");
        // copied the way every other reader does, retried while the game changes the bag (a potion
        // drunk as we look): one failed copy used to read as "the agent can't" for the whole run
        for (Object item : copy((ArrayList<?>) bag)) {
            if (item != null && intField(item, E_ID) == uid) return true;
        }
        return false;
    }

    /** Our own entity id, straight from the world object. */
    static int selfId() throws Unavailable {
        return intField(world(), W_SELF_ID);
    }

    /** The logged-in character's name, or null when nobody is logged in. */
    static String selfName() {
        try {
            return (String) get(selfEntity(world()), E_NAME, String.class);
        } catch (Unavailable | RuntimeException e) {
            return null;
        }
    }

    /** The game's main window, or null before it opens. */
    static Frame window() {
        Object win = cachedWin;
        if (win != null && ((Frame) win).isDisplayable()) return (Frame) win;
        for (Frame f : Frame.getFrames()) {
            if (f.getClass().getName().equals(WIN_CLASS)) return f;
        }
        return null;
    }

    private static Object selfEntity(Object world) throws Unavailable {
        int id = intField(world, W_SELF_ID);
        Object me = entityMap(world).get(Integer.valueOf(id));
        if (me == null) throw new Unavailable("noplayer: id " + id + " not in the entity map");
        return me;
    }

    private static void entities(Object world, StringBuilder sb) throws Unavailable {
        int selfId = intField(world, W_SELF_ID);
        List<Map.Entry<Object, Object>> all = copy(entityMap(world));
        sb.append('[');
        boolean first = true;
        for (Map.Entry<Object, Object> e : all) {
            if (!(e.getKey() instanceof Integer) || e.getValue() == null) continue;
            int id = (Integer) e.getKey();
            if (id == selfId) continue;
            if (!first) sb.append(',');
            first = false;
            entity(e.getValue(), id, sb);
        }
        sb.append(']');
    }

    private static void entity(Object e, int id, StringBuilder sb) throws Unavailable {
        sb.append("{\"id\":").append(id);
        boolean living = has(e, E_HP) && has(e, E_SIDE);
        int side = living ? shortField(e, E_SIDE) : 0;
        String kind;
        if (id >= 0) kind = "player";
        else if (!living) kind = "monster";       // drops and objects: no hp, as before
        else if (side == -1) kind = "npc";
        else if (side < -1 && (intField(e, E_KIND) & F_PROTECTED) == 0) kind = "monster";
        else kind = "other";                      // guards, others' summons, traps and totems
        sb.append(",\"kind\":\"").append(kind).append('"');
        if (living) sb.append(",\"side\":").append(side);
        sb.append(",\"name\":");
        str(sb, (String) get(e, E_NAME, String.class));
        int x = intField(e, E_X);
        int y = intField(e, E_Y);
        sb.append(",\"x\":").append(tiles(x)).append(",\"y\":").append(tiles(y));
        sb.append(",\"rawx\":").append(x).append(",\"rawy\":").append(y);
        sb.append(",\"type\":").append(intField(e, E_KIND));
        sb.append(",\"facing\":").append(shortField(e, E_FACING));
        // Only living things (game.ak) carry hp; items and doors don't.
        if (has(e, E_HP)) {
            sb.append(",\"hp\":").append(intField(e, E_HP));
            sb.append(",\"maxhp\":").append(intField(e, E_MAX_HP));
        }
        sb.append('}');
    }

    private static void bag(Object world, StringBuilder sb) throws Unavailable {
        Object list = get(world, W_BAG, ArrayList.class);
        if (list == null) throw new Unavailable("nobag");
        sb.append('[');
        boolean first = true;
        for (Object row : copy((ArrayList<?>) list)) {
            if (row == null) continue;
            if (!first) sb.append(',');
            first = false;
            sb.append("{\"name\":");
            str(sb, (String) get(row, E_NAME, String.class));
            sb.append(",\"count\":").append(intField(row, BAG_COUNT));
            sb.append('}');
        }
        sb.append(']');
    }

    private static void slots(Object world, StringBuilder sb) throws Unavailable {
        Object map = get(world, W_SLOTS, HashMap.class);
        if (map == null) throw new Unavailable("noslots");
        sb.append('{');
        boolean first = true;
        for (Map.Entry<Object, Object> e : copy((HashMap<?, ?>) map)) {
            if (!(e.getValue() instanceof int[])) continue;
            int[] v = (int[]) e.getValue();
            if (!first) sb.append(',');
            first = false;
            sb.append('"').append(e.getKey()).append("\":[");
            for (int i = 0; i < v.length; i++) {
                if (i > 0) sb.append(',');
                sb.append(v[i]);
            }
            sb.append(']');
        }
        sb.append('}');
    }

    /** {"combat":[..],"talents":[..],"profession":[..],"attack_keys":{key:id},"profession_keys":{..}} */
    private static void skills(Object world, StringBuilder sb) throws Unavailable {
        sb.append("{\"combat\":");
        rows(world, W_COMBAT, COMBAT_ROW, sb);
        sb.append(",\"talents\":");
        rows(world, W_TALENTS, TALENT_ROW, sb);
        sb.append(",\"profession\":");
        rows(world, W_PROFESSION, PROFESSION_ROW, sb);
        sb.append(",\"attack_keys\":");
        keyMap(world, W_ATTACK_KEYS, sb);
        sb.append(",\"profession_keys\":");
        keyMap(world, W_PROFESSION_KEYS, sb);
        sb.append(",\"now\":").append(System.currentTimeMillis());
        sb.append('}');
    }

    /** A world list as json rows, each field written by its own type (a missing field is null). */
    private static void rows(Object world, String listField, String[][] spec, StringBuilder sb) throws Unavailable {
        Object list = get(world, listField, ArrayList.class);
        if (list == null) throw new Unavailable("nolist: " + listField);
        sb.append('[');
        boolean first = true;
        for (Object row : copy((ArrayList<?>) list)) {
            if (row == null) continue;
            if (!first) sb.append(',');
            first = false;
            sb.append('{');
            for (int i = 0; i < spec.length; i++) {
                if (i > 0) sb.append(',');
                sb.append('"').append(spec[i][0]).append("\":");
                value(sb, row, spec[i][1]);
            }
            sb.append('}');
        }
        sb.append(']');
    }

    private static void keyMap(Object world, String mapField, StringBuilder sb) throws Unavailable {
        Object map = get(world, mapField, HashMap.class);
        sb.append('{');
        if (map != null) {
            boolean first = true;
            for (Map.Entry<Object, Object> e : copy((HashMap<?, ?>) map)) {
                if (!first) sb.append(',');
                first = false;
                sb.append('"').append(e.getKey()).append("\":");
                Object v = e.getValue();
                if (v instanceof Number) sb.append(((Number) v).longValue());
                else str(sb, v == null ? null : v.toString());
            }
        }
        sb.append('}');
    }

    /** Any primitive, boxed or String field as json; null when the class has no such field. */
    private static void value(StringBuilder sb, Object o, String name) {
        Field f = field(o.getClass(), name);
        if (f == null) {
            sb.append("null");
            return;
        }
        try {
            Object v = f.get(o);
            if (v == null) sb.append("null");
            else if (v instanceof Number || v instanceof Boolean) sb.append(v);
            else if (v instanceof Character || v instanceof String) str(sb, v.toString());
            else str(sb, v.getClass().getName());
        } catch (IllegalAccessException e) {
            sb.append("null");
        }
    }

    // ---------------------------------------------------------------- reflection

    /** Lookups are cached, including misses (stored as MISSING, since the map can't hold null). */
    private static final Map<String, Object> FIELDS =
            new java.util.concurrent.ConcurrentHashMap<String, Object>();
    private static final Object MISSING = new Object();

    /** The named field on the object's class or a superclass, or null when there is none. */
    private static Field field(Class<?> c, String name) {
        String key = c.getName() + '.' + name;
        Object f = FIELDS.get(key);
        if (f == null) {
            f = MISSING;
            for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
                try {
                    Field found = k.getDeclaredField(name);
                    found.setAccessible(true);
                    f = found;
                    break;
                } catch (NoSuchFieldException ignored) {
                    // try the superclass
                }
            }
            FIELDS.put(key, f);
        }
        return f == MISSING ? null : (Field) f;
    }

    private static boolean has(Object o, String name) {
        return field(o.getClass(), name) != null;
    }

    /** Reads a reference field, checking it holds the expected type when one is given. */
    private static Object get(Object o, String name, Class<?> type) throws Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null) throw moved(o, name);
        if (f.getType().isPrimitive() || (type != null && !type.isAssignableFrom(f.getType()))) {
            throw moved(o, name);
        }
        try {
            return f.get(o);
        } catch (IllegalAccessException e) {
            throw moved(o, name);
        }
    }

    private static int intField(Object o, String name) throws Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null || f.getType() != int.class) throw moved(o, name);
        try {
            return f.getInt(o);
        } catch (IllegalAccessException e) {
            throw moved(o, name);
        }
    }

    private static int shortField(Object o, String name) throws Unavailable {
        Field f = field(o.getClass(), name);
        if (f == null || f.getType() != short.class) throw moved(o, name);
        try {
            return f.getShort(o);
        } catch (IllegalAccessException e) {
            throw moved(o, name);
        }
    }

    private static Unavailable moved(Object o, String name) {
        return new Unavailable("fields moved: " + o.getClass().getName() + "." + name
                + " is missing or has the wrong type (game patched?)");
    }

    @SuppressWarnings("unchecked")
    private static HashMap<Object, Object> entityMap(Object world) throws Unavailable {
        Object m = get(world, W_ENTITIES, HashMap.class);
        if (m == null) throw new Unavailable("noworld: entity map is null");
        return (HashMap<Object, Object>) m;
    }

    /**
     * The game mutates these collections on its own threads without locking, so a copy can hit a
     * concurrent modification. It is retried; the window is tiny and a retry almost always wins.
     */
    @SuppressWarnings("unchecked")
    private static List<Map.Entry<Object, Object>> copy(HashMap<?, ?> map) throws Unavailable {
        for (int attempt = 0; attempt < COPY_RETRIES; attempt++) {
            try {
                List<Map.Entry<Object, Object>> out = new ArrayList<Map.Entry<Object, Object>>();
                for (Map.Entry<?, ?> e : map.entrySet()) {
                    out.add(new java.util.AbstractMap.SimpleImmutableEntry<Object, Object>(
                            e.getKey(), e.getValue()));
                }
                return out;
            } catch (ConcurrentModificationException | ArrayIndexOutOfBoundsException
                     | NullPointerException e) {
                Thread.yield();
            }
        }
        throw new Unavailable("busy: the game kept changing the map");
    }

    private static List<Object> copy(ArrayList<?> list) throws Unavailable {
        for (int attempt = 0; attempt < COPY_RETRIES; attempt++) {
            try {
                return new ArrayList<Object>(list);
            } catch (ConcurrentModificationException | ArrayIndexOutOfBoundsException e) {
                Thread.yield();
            }
        }
        throw new Unavailable("busy: the game kept changing the bag");
    }

    // ---------------------------------------------------------------- json

    /** Memory positions are 1000 per tile, the same scale as our own outbound op=12. */
    private static String tiles(int raw) {
        return String.format(java.util.Locale.ROOT, "%.2f", raw / 1000.0);
    }

    private static void str(StringBuilder sb, String s) {
        if (s == null) {
            sb.append("null");
        } else {
            sb.append('"').append(Protocol.jsonEscape(s)).append('"');
        }
    }

    private static String error(String why) {
        StringBuilder sb = new StringBuilder("{\"ok\":false,\"error\":");
        str(sb, why);
        return sb.append('}').toString();
    }
}
