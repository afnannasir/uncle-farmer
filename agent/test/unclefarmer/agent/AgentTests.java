package unclefarmer.agent;

import java.io.File;
import java.nio.file.Files;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * The agent's own checks, without the game: the byte rules, the own-HP answers, the chat queue's
 * parts and the command replies. Plain Java (the agent is built with javac alone, no JUnit):
 * run by agent/java_tests.py, which check.py runs whenever a JDK is installed.
 *
 * Everything that needs the game's classes (the world object, the speech patch) stays a live test.
 */
public final class AgentTests {

    private static int run;
    private static int failed;

    private AgentTests() { }

    public static void main(String[] args) throws Exception {
        Map<String, String> contract = contract(args.length > 0 ? args[0] : "agent/test/contract.txt");

        protocolReadsNumbersAndNeverPastTheEnd();
        protocolEscapesJson();
        protocolReadsMapAndEntityNames();
        protocolFindsThePositionAfterTheTimestamp();
        positionsSurviveTheClocksFiftyDayTurn();
        numbersStayJsonInACommaDecimalLocale();
        aTapLeavesAKeyAnotherConnectionHolds();
        theGameFolderIsWrittenForTheApp();
        vitalsCreditOnlyTheAnswerToOurOwnRequest(contract);
        vitalsForIdZeroAreThePets();
        askPacketIsTheOwnHpKeysRequest(contract);
        usePacketIsTheUseKeysRequest(contract);
        aSheetWeAskedForIsReadAndKeptFromTheGame(contract);
        everyCastSaysWhomItStruck(contract);
        theAttackKeyMethodIsFoundByItsShape();
        aSlotPressIsLetGoLikeAKey();
        chatQueueCutsLongTextAtSpaces();
        commandsAnswerWithoutTheGame();
        voiceFramesAndNames();
        voiceMessagesArriveWithTheirName();
        voiceIsCleanedAndCutIntoMessages();

        System.out.println("agent tests: " + (run - failed) + " of " + run + " passed");
        if (failed > 0) System.exit(1);
    }

    // ---------------------------------------------------------------- protocol
    private static void protocolReadsNumbersAndNeverPastTheEnd() {
        byte[] m = {0x00, 0x12, (byte) 0xD6, (byte) 0x87, 0x7F};
        eq(Protocol.readInt(m, 0), 1234567, "readInt big-endian");
        eq(Protocol.readShort(m, 1), 0x12D6, "readShort big-endian");
        eq(Protocol.readInt(m, 2), 0, "readInt past the end is 0");
        eq(Protocol.readInt(m, -1), 0, "readInt before the start is 0");
        eq(Protocol.readShort(m, 4), 0, "readShort past the end is 0");
        eq(Protocol.readInt(null, 0), 0, "readInt of nothing is 0");
    }

    private static void protocolEscapesJson() {
        eq(Protocol.jsonEscape("a\"b\\c\nd\te\u0001"), "a\\\"b\\\\c\\nd\\te\\u0001", "jsonEscape");
        eq(Protocol.jsonEscape(null), "", "jsonEscape of null");
    }

    private static void protocolReadsMapAndEntityNames() {
        byte[] map = concat(new byte[] {6, 0, 1, 0, 2, 0, 0}, "Wolf Prairie".getBytes(Protocol.UTF8));
        eq(Protocol.mapName(map), "Wolf Prairie", "mapName");
        eq(Protocol.mapName(new byte[] {6, 0, 1, 0, 2, 0, 0}), null, "a 7-byte op=6 means the map was left");

        // op=32: a positive id is a player, its name at 41 ("name,title,set,"); a monster's at 37
        byte[] player = new byte[41];
        player[0] = 32;
        player[4] = 5;                                 // id 5
        player = concat(player, "Hero,Knight,,".getBytes(Protocol.UTF8));
        eq(Protocol.entityName(player), "Hero", "a player's name at 41");
        byte[] monster = new byte[37];
        monster[0] = 32;
        monster[1] = (byte) 0xFF;                      // a negative id
        monster = concat(monster, "Wolf".getBytes(Protocol.UTF8));
        eq(Protocol.entityName(monster), "Wolf", "a monster's name at 37");
        eq(Protocol.entityName(new byte[20]), null, "a record too short has no name");
    }

    private static void protocolFindsThePositionAfterTheTimestamp() {
        long now = System.currentTimeMillis();
        byte[] m = new byte[30];
        m[0] = 36;
        for (int i = 0; i < 8; i++) m[5 + i] = (byte) (now >>> (56 - 8 * i));   // after a 4-byte counter
        eq(Protocol.positionOffset(m), 13, "the position sits just past the timestamp");
        eq(Protocol.positionOffset(new byte[30]), -1, "no timestamp, no position");
    }

    private static byte[] movement(long stamp) {
        byte[] m = new byte[30];
        m[0] = 36;
        for (int i = 0; i < 8; i++) m[5 + i] = (byte) (stamp >>> (56 - 8 * i));
        return m;
    }

    private static void positionsSurviveTheClocksFiftyDayTurn() {
        long turn = 0x19AL << 32;                       // the top four bytes of the clock change here
        eq(Protocol.positionOffset(movement(turn - 3000), turn + 2000), 13,
                "a timestamp from just before the turn, read just after it");
        eq(Protocol.positionOffset(movement(turn + 2000), turn + 2000), 13, "and one after it");
        eq(Protocol.positionOffset(movement(turn + 2000), turn + (3L << 32)), -1,
                "a stamp from another era is no timestamp");
    }

    private static void numbersStayJsonInACommaDecimalLocale() {
        java.util.Locale was = java.util.Locale.getDefault();
        java.util.Locale.setDefault(java.util.Locale.GERMANY);
        try {
            check(World.snapshot().contains("\"x\":0.00,"), "World's numbers keep their point");
            byte[] member = new byte[13];
            member[0] = 59;
            member[4] = 7;                               // id 7
            member[7] = 0x05;                            // x 1500 = 1.50 tiles
            member[8] = (byte) 0xDC;
            Team.inbound(concat(member, "Mate".getBytes(Protocol.UTF8)));
            check(Team.snapshot().contains("\"x\":1.50,"), "Team's numbers keep their point");
        } finally {
            java.util.Locale.setDefault(was);
        }
    }

    @SuppressWarnings("unchecked")
    private static void aTapLeavesAKeyAnotherConnectionHolds() throws Exception {
        java.lang.reflect.Field f = Keys.class.getDeclaredField("HELD");
        f.setAccessible(true);
        Map<String, Long> held = (Map<String, Long>) f.get(null);
        held.put("w", Long.valueOf(System.currentTimeMillis()));     // another connection walks on w
        try {
            eq(Keys.tap("w", 30), "tapped w" + Keys.ALREADY, "the tap goes as the key already is");
            check(held.containsKey("w"), "and the key stays held for the one walking on it");
        } finally {
            held.remove("w");
        }
    }

    private static void theGameFolderIsWrittenForTheApp() throws Exception {
        java.nio.file.Path dir = java.nio.file.Files.createTempDirectory("uf-agent-test");
        try {
            BotAgent.writeGameFolder(dir);
            String got = new String(java.nio.file.Files.readAllBytes(dir.resolve("game_folder.txt")), Protocol.UTF8);
            eq(got, new File(System.getProperty("user.dir")).getAbsolutePath(), "the game's working folder, for the app");
            String pid = java.lang.management.ManagementFactory.getRuntimeMXBean().getName().split("@")[0];
            String mine = new String(java.nio.file.Files.readAllBytes(dir.resolve("agent." + pid + ".folder")), Protocol.UTF8);
            eq(mine, got, "and this game's own folder file");
        } finally {
            File[] left = dir.toFile().listFiles();
            if (left != null) for (File f : left) java.nio.file.Files.deleteIfExists(f.toPath());
            java.nio.file.Files.deleteIfExists(dir);
        }
    }

    // ---------------------------------------------------------------- own HP and MP
    private static void vitalsCreditOnlyTheAnswerToOurOwnRequest(Map<String, String> contract) {
        int us = 1234567;
        World.outbound(concat(Ask.packet(6, us), new byte[] {0, 0}));      // the map request: our id
        eq(World.selfId(), us, "our id comes from the map request");

        World.outbound(Ask.packet(22, 777));           // someone else selected
        World.inbound(answer(5000, 100, -1, -1));
        eq(Vitals.hp == 5000, false, "an answer about someone else isn't ours");

        World.outbound(Ask.packet(22, us));
        World.inbound(answer(91000, 82000, 2, 1));
        eq(Vitals.hp, 91000, "our HP from op=51");
        eq(Vitals.mp, 82000, "our MP from op=51");
        eq(Vitals.buffs, 2, "buffs from the status byte");
        eq(Vitals.debuffs, 1, "debuffs from the status byte");
        eq(Vitals.id, us, "the numbers are ours");

        World.outbound(Ask.packet(22, us));
        World.inbound(answer(90000, 80000, -1, -1));
        eq(Vitals.buffs, 0, "a short answer means no buffs");

        Vitals.hp = 91000;
        Vitals.mp = 82000;
        Vitals.buffs = 2;
        Vitals.debuffs = 1;
        Vitals.seen = 1000L;
        StringBuilder sb = new StringBuilder();
        Vitals.json(sb, 2500L);
        eq(sb.toString(), ",\"vitals\":" + contract.get("vitals"), "mem self vitals as in contract.txt");
    }

    private static void vitalsForIdZeroAreThePets() {
        World.outbound(Ask.packet(22, 0));
        World.inbound(answer(3000, 400, -1, -1));
        eq(PetInfo.vitalsHp, 3000, "id 0 is the pet: its HP");
        eq(PetInfo.vitalsMp, 400, "id 0 is the pet: its MP");
    }

    private static void askPacketIsTheOwnHpKeysRequest(Map<String, String> contract) {
        StringBuilder sb = new StringBuilder();
        for (byte b : Ask.packet(Ask.OP_VITALS, 1234567)) sb.append(sb.length() == 0 ? "" : " ").append(b & 0xFF);
        eq(sb.toString(), contract.get("ask_packet"), "ask vitals sends what R sends");
    }

    /** `ask sheet`: the sheet key's bytes go out; the sheet that answers is read and held back from
     *  the game (World.inbound true), one sheet per request, never someone else's, never late. */
    private static void aSheetWeAskedForIsReadAndKeptFromTheGame(Map<String, String> contract) {
        eq(bytes(Ask.sheetPacket(1234567)), contract.get("sheet_packet"), "ask sheet sends what the sheet key sends");
        int us = 1234567;
        World.outbound(concat(Ask.packet(6, us), new byte[] {0, 0}));      // our id, from the map request
        long now = System.currentTimeMillis();

        eq(World.inbound(sheet(us, 1000)), false, "a sheet nobody asked for reaches the game as before");
        eq(Sheet.contribution, 1000L, "and is still read");

        Sheet.expectQuiet(now);
        eq(World.inbound(sheet(777, 5)), false, "someone else's sheet is the player's to see");
        eq(World.inbound(sheet(us, 1200)), true, "the sheet that answers ask sheet is kept from the game");
        eq(Sheet.contribution, 1200L, "and read all the same");
        eq(Sheet.dropSeconds, 3 * 3600 + 25 * 60, "the drop time with it");
        eq(World.inbound(sheet(us, 1300)), false, "one request, one sheet: the next is the player's");

        Sheet.expectQuiet(now - Sheet.QUIET_WAIT_MS - 1000);
        eq(World.inbound(sheet(us, 1400)), false, "an answer far too late is the player's own sheet");
        Sheet.expectQuiet(now);
        Sheet.expectQuiet(0);
        eq(World.inbound(sheet(us, 1500)), false, "a request that never went out holds nothing back");
    }

    /** An IN op=62 character sheet as the server sends it (the parts Sheet reads). */
    private static byte[] sheet(int id, long contribution) {
        String text = "(1(Someone)1)|||((," + id + ";))|||Hunter|||;|||male |||. level 79|||; Contribution "
                + contribution + "|||HP 40000/50000|||MP 30000/45000|||marked targets 0|||fusion power 99.8|||"
                + "load 2000/4000|||public area drop 3 hours 25 minutes  remaining";
        return concat(new byte[] {62}, text.getBytes(Protocol.UTF8));
    }

    private static void usePacketIsTheUseKeysRequest(Map<String, String> contract) {
        eq(bytes(Act.packet(1234567)), contract.get("use_packet"), "use sends what the use key sends");
    }

    private static String bytes(byte[] packet) {
        StringBuilder sb = new StringBuilder();
        for (byte b : packet) sb.append(sb.length() == 0 ? "" : " ").append(b & 0xFF);
        return sb.toString();
    }

    // ---------------------------------------------------------------- chat queue
    private static void chatQueueCutsLongTextAtSpaces() {
        eq(ChatQueue.split("hello").size(), 1, "a short line is one part");
        eq(ChatQueue.split("   ").size(), 0, "nothing to send is no part");

        StringBuilder words = new StringBuilder();
        while (words.length() < 450) words.append("farming ");
        String text = words.toString().trim();
        List<String> parts = ChatQueue.split(text);
        eq(parts.size(), 3, "450 characters go in three parts");
        StringBuilder back = new StringBuilder();
        for (String p : parts) {
            check(p.length() <= ChatQueue.MOST, "a part fits the chat box: " + p.length());
            check(!p.startsWith(" ") && !p.endsWith(" "), "a part is cut at a space");
            back.append(back.length() == 0 ? "" : " ").append(p);
        }
        eq(back.toString(), text, "the parts put together are the text");

        StringBuilder word = new StringBuilder();
        for (int i = 0; i < 250; i++) word.append('x');
        parts = ChatQueue.split(word.toString());
        eq(parts.size(), 2, "a word longer than the box is cut hard");
        eq(parts.get(0).length(), ChatQueue.MOST, "the hard cut is at the limit");

        StringBuilder exact = new StringBuilder();
        for (int i = 0; i < ChatQueue.MOST; i++) exact.append('y');
        eq(ChatQueue.split(exact.toString()).size(), 1, "exactly the limit is one part");
    }

    // ---------------------------------------------------------------- commands
    private static void commandsAnswerWithoutTheGame() {
        boolean[] armed = {false};
        boolean[] guarded = {false};
        java.util.Set<String> mine = new java.util.HashSet<String>();
        eq(StateServer.handle("version", armed, mine, guarded), "mwagent " + StateServer.VERSION, "version");
        eq(StateServer.handle("ping", armed, mine, guarded), "pong", "ping");
        eq(StateServer.handle("frobnicate", armed, mine, guarded), "unknown command: frobnicate", "unknown");
        eq(StateServer.handle("ask vitals", armed, mine, guarded), "refused: send 'arm' first", "ask needs arm");
        eq(StateServer.handle("key j", armed, mine, guarded), "refused: send 'arm' first", "keys need arm");
        eq(StateServer.handle("say 1 hi", armed, mine, guarded), "refused: send 'arm' first", "say needs arm");
        eq(StateServer.handle("use 5", armed, mine, guarded), "refused: send 'arm' first", "use needs arm");
        eq(StateServer.handle("select 5", armed, mine, guarded), "refused: send 'arm' first", "select needs arm");
        eq(StateServer.handle("slot 1", armed, mine, guarded), "refused: send 'arm' first", "slot needs arm");
        eq(StateServer.handle("arm", armed, mine, guarded), "armed", "arm");
        eq(StateServer.handle("slot 1", armed, mine, guarded), "error: the attack page can't be read",
                "slot without a game says so instead of throwing");
        eq(StateServer.handle("slot 11", armed, mine, guarded), "error: slot 1-10", "a key number, not a page-2 slot");
        eq(StateServer.handle("slot 2 sideways", armed, mine, guarded), "error: slot <1-10> [sustain] [lock]",
                "slot with a word it doesn't know");
        eq(StateServer.handle("slot x", armed, mine, guarded), "bad number in: slot x", "slot a bad number");
        check(StateServer.handle("use 5", armed, mine, guarded).startsWith("error: nowin"),
                "use without a game says so instead of throwing");
        check(StateServer.handle("select 5", armed, mine, guarded).startsWith("error: nowin"),
                "select without a game says so instead of throwing");
        eq(StateServer.handle("use", armed, mine, guarded), "error: use <id>", "use what");
        eq(StateServer.handle("select x", armed, mine, guarded), "bad number in: select x", "select a bad id");
        eq(StateServer.handle("ask", armed, mine, guarded), "error: ask vitals|petvitals|sheet", "ask what");
        eq(StateServer.handle("ask vitals", armed, mine, guarded), "error: the game's connection wasn't found",
                "ask without a game says so instead of throwing");
        eq(StateServer.handle("say 1", armed, mine, guarded), "error: say <0-6> <text>", "say without text");
        eq(StateServer.handle("pms x", armed, mine, guarded), "bad number in: pms x", "a bad number is answered");
    }

    // ---------------------------------------------------------------- voice
    private static final String VOICE_NAME = "0123456789abcdef0123456789ABCDEF";

    private static void voiceFramesAndNames() throws Exception {
        byte[] file = {1, 2, 9, 9, 7, 1, 5};                   // two frames of 2 bytes, then part of one
        List<byte[]> f = Voice.frames(file);
        eq(f.size(), 2, "voice frames: 01 <frame length>, then the frames; a part frame left out");
        eq(f.get(1)[0], (byte) 7, "the second frame's byte");
        eq(Voice.frames(new byte[] {2, 2, 9, 9}).size(), 0, "not a voice file");
        eq(Voice.millis(file), 40L, "20 ms a frame");
        check(Voice.validName(VOICE_NAME), "a 32-character name is a voice name");
        check(!Voice.validName("../../../../windows/system32/xx"), "a path is not");
        check(!Voice.validName("abc"), "nor a short one");
        java.io.ByteArrayOutputStream o = new java.io.ByteArrayOutputStream();
        Voice.writeWav(o, new byte[] {1, 0, 2, 0});
        byte[] wav = o.toByteArray();
        eq(wav.length, 48, "a WAV header of 44 bytes and the samples");
        eq(new String(wav, 0, 4, Protocol.UTF8), "RIFF", "RIFF");
        eq(wav[24] & 0xFF | (wav[25] & 0xFF) << 8, 16000, "16 kHz");
        eq(StateServer.handle("voice play ../x", new boolean[] {false}, new java.util.HashSet<String>(),
                new boolean[] {false}), "error: not a voice message name", "voice play checks the name");
        eq(StateServer.handle("voice record", new boolean[] {false}, new java.util.HashSet<String>(),
                new boolean[] {false}), "refused: send 'arm' first", "recording needs arm");
    }

    private static void voiceIsCleanedAndCutIntoMessages() throws Exception {
        int rate = Voice.RATE;
        // 1 s of silence, 2 s of a quiet 300 Hz "voice" (-30 dBFS), 1 s of silence
        short[] rec = new short[4 * rate];
        for (int i = rate; i < 3 * rate; i++) rec[i] = (short) (1000 * Math.sin(2 * Math.PI * 300 * i / rate));
        short[] c = Voice.clean(rec);
        check(c.length > 2 * rate && c.length < 2 * rate + 2 * Voice.MARGIN_FRAMES * Voice.FRAME + Voice.FRAME,
                "the silence before and after is cut, a little kept: " + c.length);
        double sum = 0;
        int peak = 0;
        for (short v : c) {
            sum += (double) v * v;
            peak = Math.max(peak, Math.abs(v));
        }
        double rms = Math.sqrt(sum / c.length);
        check(rms > 2500 && rms < 3600, "a quiet voice is brought up to an even level: " + rms);
        check(peak < 32767, "never clipped");
        eq(Voice.clean(new short[2 * rate]).length, 0, "nothing said: nothing to send");

        int[] cut = Voice.cuts(45 * rate);
        eq(cut.length - 1, 3, "45 s: three messages");
        eq(cut[1], 15 * rate, "of 15 s each, not 20 + 20 + 5");
        eq(cut[3], 45 * rate, "the last ends at the end");
        eq(Voice.cuts(10 * rate).length - 1, 1, "10 s: one message");
        check(cut[1] % Voice.FRAME == 0 && cut[2] % Voice.FRAME == 0, "cuts fall on frames");

        Voice.Encoder same = new Voice.Encoder() {
            public byte[] frame(short[] pcm) {
                return new byte[80];
            }
        };
        byte[] m = Voice.message(c, 0, c.length, same);
        eq(m[0], (byte) Voice.RATE_CODE, "the file says mono 16 kHz");
        eq(m[1] & 0xFF, 80, "and the frame length");
        eq(Voice.frames(m).size(), (c.length + Voice.FRAME - 1) / Voice.FRAME, "every frame in it");
        Voice.Encoder uneven = new Voice.Encoder() {
            int n;

            public byte[] frame(short[] pcm) {
                return new byte[n++ == 3 ? 79 : 80];
            }
        };
        check(Voice.message(c, 0, c.length, uneven) == null, "frames of different sizes aren't sent");
    }

    private static void voiceMessagesArriveWithTheirName() {
        byte[] head = new byte[18];
        head[0] = 55;
        head[13] = (byte) (12 + Voice.FLAG);                   // team (4, arriving as 12), a voice line
        head[17] = 9;                                          // the sender
        byte[] line = concat(head, "Ali\n".getBytes(Protocol.UTF8), VOICE_NAME.getBytes(Protocol.UTF8),
                new byte[] {1, 2, 10, 10});
        GameChat.inbound(line);
        String chat = GameChat.since(0, 0);
        check(chat.contains("\"channel\":12,\"name\":\"Ali\",\"text\":\"\",\"voice\":\"" + VOICE_NAME + "\""),
                "a voice line on a channel: " + chat);

        byte[] pm = new byte[21];
        pm[0] = 56;
        pm[17] = (byte) 0xFF;                                  // the sender's id negated: -2
        pm[18] = (byte) 0xFF;
        pm[19] = (byte) 0xFF;
        pm[20] = (byte) 0xFE;
        PrivateChat.inbound(concat(pm, "Bob\n".getBytes(Protocol.UTF8), VOICE_NAME.getBytes(Protocol.UTF8),
                new byte[] {1, 1, 0}));
        String pms = PrivateChat.since(0);
        check(pms.contains("\"id\":2,\"name\":\"Bob\",\"text\":\"\",\"voice\":\"" + VOICE_NAME + "\""),
                "a voice private message: " + pms);

        byte[] mine = concat(new byte[] {25, (byte) Voice.PRIVATE_OUT, 0, 0, 0, 2}, VOICE_NAME.getBytes(Protocol.UTF8),
                new byte[] {1, 1, 0});
        PrivateChat.outbound(mine);
        check(PrivateChat.since(0).contains("\"mine\":true,\"voice\":\"" + VOICE_NAME + "\""),
                "our own voice message shows too");
    }

    // ---------------------------------------------------------------- helpers
    /** IN op=51: hp, mp, then the buff and debuff bytes (-1 leaves one off). */
    private static byte[] answer(int hp, int mp, int buffs, int debuffs) {
        byte[] head = concat(new byte[] {51}, Ask.packet(0, hp), Ask.packet(0, mp));
        byte[] m = new byte[] {51, head[2], head[3], head[4], head[5], head[7], head[8], head[9], head[10]};
        if (buffs >= 0) m = concat(m, new byte[] {(byte) buffs});
        if (debuffs >= 0) m = concat(m, new byte[] {(byte) debuffs});
        return m;
    }

    private static byte[] concat(byte[]... parts) {
        int n = 0;
        for (byte[] p : parts) n += p.length;
        byte[] out = new byte[n];
        int at = 0;
        for (byte[] p : parts) {
            System.arraycopy(p, 0, out, at, p.length);
            at += p.length;
        }
        return out;
    }

    /** Another player's cast carries its targets and their HP after it (agent 44): a monster that
     *  someone else has started on is known at once. One struck to 0 is a death as before. */
    private static void everyCastSaysWhomItStruck(Map<String, String> contract) {
        String text = contract.get("cast_packet").replace("\\r", "\r").replace("\\n", "\n");
        byte[] body = text.getBytes(Protocol.UTF8);
        byte[] m = new byte[body.length + 1];
        m[0] = 41;
        System.arraycopy(body, 0, m, 1, body.length);
        long before = System.currentTimeMillis() - 1;
        World.inbound(m);
        String got = World.casts(before);
        check(got.contains("\"caster\":4152,\"seq\":7,\"skill\":\"Mind Whip\""), "the cast is read: " + got);
        check(got.contains("\"targets\":" + contract.get("cast_targets")), "with its targets: " + got);
        check(!got.contains("\"hits\""), "and no hits section, which this packet doesn't have: " + got);
        check(got.contains("[-6,"), "the one struck to 0 died: " + got);
        eq(World.targets(new String[] {"4152,8,Buff\n4152,64200"}), null, "a cast with no target line has none");
    }

    /** Stand-ins for game.i: the one method of the attack keys' shape is found whatever its name. */
    static final class OneShape {
        int slot;
        boolean sustain;
        boolean lock;
        void q(int a, boolean b) { }
        void zz(int s, boolean sus, boolean lk, long delay) { slot = s; sustain = sus; lock = lk; }
    }

    static final class TwoShapes {
        void a(int s, boolean b, boolean c, long d) { }
        void b(int s, boolean b, boolean c, long d) { }
    }

    private static void theAttackKeyMethodIsFoundByItsShape() throws Exception {
        java.lang.reflect.Method m = Act.pressMethod(OneShape.class);
        eq(m == null ? null : m.getName(), "zz", "found by its parameters");
        OneShape w = new OneShape();
        m.invoke(w, 12, true, false, 0L);
        check(w.slot == 12 && w.sustain && !w.lock, "and called the way game.Win calls it");
        eq(Act.pressMethod(TwoShapes.class), null, "two of that shape: neither is guessed");
    }

    /** A stand-in for game.i with both halves of an attack key: down (slot) and up (release). */
    static final class KeyWorld {
        final java.util.List<String> calls = java.util.Collections.synchronizedList(new java.util.ArrayList<String>());
        void a(int s, boolean sus, boolean lk, long delay) { calls.add("down " + s); }
        void a(long delay) { calls.add("up"); }
        void b(int other) { }
    }

    private static void aSlotPressIsLetGoLikeAKey() throws Exception {
        KeyWorld w = new KeyWorld();
        eq(Act.press(w, 3, false, false, 20L), "pressed 3", "a slot press answers as before");
        eq(String.valueOf(w.calls), "[down 3]", "key down first, the release waits a key's hold");
        Thread.sleep(200L);
        eq(String.valueOf(w.calls), "[down 3, up]", "then the key comes back up, or the game thinks it is held");
        KeyWorld quick = new KeyWorld();
        Act.press(quick, 1, false, false, 100L);
        Act.press(quick, 2, false, false, 100L);
        Thread.sleep(400L);
        eq(String.valueOf(quick.calls), "[down 1, down 2, up]", "a press before the last let go takes its release over");
        eq(Act.releaseMethod(OneShape.class), null, "no release of that shape: not guessed");
    }

    private static Map<String, String> contract(String path) throws Exception {
        Map<String, String> out = new HashMap<String, String>();
        for (String line : Files.readAllLines(new File(path).toPath(), Protocol.UTF8)) {
            line = line.trim();
            int eq = line.indexOf('=');
            if (line.isEmpty() || line.startsWith("#") || eq < 0) continue;
            out.put(line.substring(0, eq), line.substring(eq + 1));
        }
        return out;
    }

    private static void eq(Object got, Object want, String what) {
        boolean same = got instanceof Number && want instanceof Number
                ? ((Number) got).longValue() == ((Number) want).longValue()     // an int field against a long one
                : got == null ? want == null : got.equals(want);
        check(same, what + ": got " + got + ", want " + want);
    }

    private static void check(boolean ok, String what) {
        run++;
        if (!ok) {
            failed++;
            System.out.println("FAILED " + what);
        }
    }
}
