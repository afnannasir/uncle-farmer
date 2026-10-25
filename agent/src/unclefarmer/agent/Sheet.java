package unclefarmer.agent;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Our own level and MP, taken from the character sheet.
 *
 * The client keeps neither as a number: a full search of the world object for the MP value found
 * nothing, and level is only ever text. Both arrive in the sheet the server sends when the sheet is
 * opened: IN op=62, plain text such as
 *
 *   (1(Name)1)|||((,1234;))|||Hunter|||;|||male |||. level 50 ... HP 40000/50000. MP 30000/45000.
 *
 * The sheet is asked for with OUT op=37 `25 0d 00000001 <character id>`. Opening another player's
 * sheet arrives the same way, so only a sheet carrying our own id is kept.
 *
 * `ask sheet` sends that request itself (Ask.sheet) and calls expectQuiet first: the sheet that
 * answers it is read here and then held back from the game (inbound answers true, and PipeTap
 * doesn't pass it on), so no window opens and nothing waits to be closed. The client only shows
 * the sheet; closing one sends the server nothing (the recorder's captures: no packet follows a
 * closed sheet), so a sheet the client never saw leaves nothing open anywhere.
 */
final class Sheet {

    private static final Pattern ID = Pattern.compile("\\(\\(,(\\d+);\\)\\)");
    private static final Pattern LEVEL = Pattern.compile("\\blevel (\\d+)");
    private static final Pattern HP = Pattern.compile("\\bHP (\\d+)/(\\d+)");
    private static final Pattern MP = Pattern.compile("\\bMP (\\d+)/(\\d+)");
    /** "load 2904/4000": the bag's weight against its limit. */
    private static final Pattern LOAD = Pattern.compile("\\bload (\\d+)/(\\d+)");
    private static final Pattern FUSION = Pattern.compile("\\bfusion power ([\\d.]+)");
    /** "level 50|||; Contribution 100000" -- not "Gang Contributions 50000", which has an s. */
    private static final Pattern CONTRIBUTION = Pattern.compile("\\bContribution (\\d+)");
    /** Below the top level: "level 50|||; upgrading requires 30000 experience". */
    private static final Pattern XP_NEEDED = Pattern.compile("upgrading requires (\\d+) experience");
    private static final Pattern MERIT = Pattern.compile("battlefield merit points (\\d+)");
    /** "public area drop 6 hours 48 minutes 16 seconds  remaining" / "... 7 hours  remaining": the
     *  day's drop time - loot only drops in public areas while it lasts. */
    private static final Pattern DROP = Pattern.compile(
            "public area drop\\s+(?:(\\d+) hours?)?\\s*(?:(\\d+) minutes?)?\\s*(?:(\\d+) seconds?)?\\s*remaining");
    /** The class's counter between MP and fusion power: "marked targets 0" (Hunter), rage, combo...
     *  The sheet separates fields with control bytes and "|||", so skip any non-word run. */
    private static final Pattern COUNTER =
            Pattern.compile("\\bMP \\d+/\\d+\\W+([a-z][a-z ]*?) ([\\d.]+)\\W+fusion power");

    static volatile int id;
    static volatile int level;
    static volatile int hp;
    static volatile int maxHp;
    static volatile int mp;
    static volatile int maxMp;
    static volatile int load = -1;
    static volatile int maxLoad = -1;
    static volatile double fusion = -1;
    static volatile int dropSeconds = -1;
    static volatile long contribution = -1;
    static volatile long xpNeeded = -1;      // -1: at the top level (the sheet has no such line)
    static volatile long merit = -1;
    static volatile String counterName = "";
    static volatile double counter = -1;
    static volatile long seen;
    /** Until when a sheet that arrives answers our own `ask sheet` (0: none asked). */
    private static volatile long quietUntil;
    /** How long the server may take: it answers in 230-330 ms (the recorder's captures). */
    static final long QUIET_WAIT_MS = 3000L;

    private Sheet() { }

    /** `ask sheet` is about to go out at now (0: it didn't go after all). */
    static void expectQuiet(long now) {
        quietUntil = now == 0 ? 0 : now + QUIET_WAIT_MS;
    }

    /** Reads our sheet out of an IN op=62. True when it answered our own `ask sheet`: then it is
     *  ours alone and the game is not to see it (see the class comment). */
    static boolean inbound(byte[] m) {
        if (m == null || m.length < 2) return false;
        String text = new String(m, 1, m.length - 1, Protocol.UTF8);
        Matcher idm = ID.matcher(text);
        Matcher lv = LEVEL.matcher(text);
        Matcher mp = MP.matcher(text);
        if (!idm.find() || !lv.find() || !mp.find()) return false;

        boolean ours;
        try {
            ours = parse(text, idm, lv, mp);
        } catch (NumberFormatException ignored) {
            return false;      // a malformed sheet must not take the parser down; the game shows it
        }
        if (!ours || System.currentTimeMillis() > quietUntil) return false;
        quietUntil = 0;        // one request, one sheet: a sheet the player opens next is shown
        return true;
    }

    /** True when the sheet was ours and has been read. */
    private static boolean parse(String text, Matcher idm, Matcher lv, Matcher mp) {
        int sheetId = Integer.parseInt(idm.group(1));
        if (sheetId != ownId()) return false;   // someone else's sheet

        Matcher hpm = HP.matcher(text);
        if (hpm.find()) {
            hp = Integer.parseInt(hpm.group(1));
            maxHp = Integer.parseInt(hpm.group(2));
        }
        Matcher ld = LOAD.matcher(text);
        if (ld.find()) {
            load = Integer.parseInt(ld.group(1));
            maxLoad = Integer.parseInt(ld.group(2));
        }
        Matcher dp = DROP.matcher(text);
        if (dp.find()) {
            int h = dp.group(1) == null ? 0 : Integer.parseInt(dp.group(1));
            int mi = dp.group(2) == null ? 0 : Integer.parseInt(dp.group(2));
            int se = dp.group(3) == null ? 0 : Integer.parseInt(dp.group(3));
            dropSeconds = h * 3600 + mi * 60 + se;
        } else if (text.contains("public area drop")) {
            dropSeconds = 0;          // the line is there but gives no time: nothing left
        }
        Matcher cb = CONTRIBUTION.matcher(text);
        contribution = cb.find() ? Long.parseLong(cb.group(1)) : -1;
        Matcher xp = XP_NEEDED.matcher(text);
        xpNeeded = xp.find() ? Long.parseLong(xp.group(1)) : -1;
        Matcher mr = MERIT.matcher(text);
        merit = mr.find() ? Long.parseLong(mr.group(1)) : -1;
        Matcher fp = FUSION.matcher(text);
        if (fp.find()) fusion = Double.parseDouble(fp.group(1));
        Matcher ct = COUNTER.matcher(text);
        if (ct.find()) {
            counterName = ct.group(1).trim();
            counter = Double.parseDouble(ct.group(2));
        }
        level = Integer.parseInt(lv.group(1));
        Sheet.mp = Integer.parseInt(mp.group(1));
        maxMp = Integer.parseInt(mp.group(2));
        id = sheetId;
        seen = System.currentTimeMillis();
        return true;
    }

    /** From the world object when logged in; the packet view's guess otherwise. */
    private static int ownId() {
        try {
            return Memory.selfId();
        } catch (Throwable t) {
            return World.selfId();
        }
    }
}
