package unclefarmer.agent;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.lang.management.ManagementFactory;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.SecureRandom;

/**
 * Exposes the world state to a controller outside the JVM, over a line protocol on loopback.
 *
 * Connecting: the server takes the first free port in {@link #FIRST_PORT}..{@link #LAST_PORT}, so
 * several game clients can run side by side, and makes up a fresh random secret every launch. Both
 * go in the port file `agent.<pid>.port` in the output folder, as "port pid secret". The first line
 * of every connection must be `auth <secret>`; anything else is refused and the connection closed.
 * That keeps other programs on this PC (and web pages poking at localhost) from driving the game.
 * tools/agentlink.py does all of this for the Python side.
 *
 * After that, send a command line, get one line back:
 *
 *   state          -> the JSON snapshot from {@link World} (built from packets)
 *   mem            -> the JSON snapshot from {@link Memory} (read from the game's own objects)
 *   mem <section>  -> one part of it: map, self, entities, bag, slots, modes, ui or state
 *                     (state is map+self+entities, the lean snapshot the bot reads each iteration)
 *   memprobe       -> our own entity field by field, beside the packet view, to check the field map
 *   memfind <n>    -> every field under the world holding n (or 'text'), to locate a stat
 *   menu           -> the open menu's verified layout (Menus): shard_market, shard_trade, potion_shop
 *                     or unknown, each item's row, id and price, and whether it is the verified one
 *   ping           -> pong
 *   health         -> whether the game is still sound: out-of-memory errors so far, threads, heap
 *   version        -> "mwagent <VERSION>"; the bot refuses an agent older than it needs
 *   keys           -> whether the game's key handler is resolved
 *   speech [on|off] -> the game's own speech: report, restore or silence it (sounds keep playing)
 *   voicefix [on|off] -> the game's voice message player: mended (on, the default) or as the game plays it
 *   wear [since]   -> equipment wear warnings from combat (about to break / broken) after since (ms)
 *   pms [after] [ms] -> private messages received after message number `after`, oldest first;
 *                     with ms, waits up to that long for one to arrive (answers the moment it does)
 *   pm <id> <text> -> send a private message, as the game's private chat line does (armed)
 *   closegame      -> close the game, as its window's close button does (armed)
 *   arm            -> allow this connection to act; everything below needs it
 *   team             -> teammates on our map, as the game answered the quick phrase "/6"
 *   phrase <slot> <text> -> set quick phrase 1-12 the way its window does (armed)
 *   pick <layout> <id> [buy|sell] -> choose that item of a verified menu by its index, after
 *                     checking it (armed); `refused: <why>` and nothing pressed otherwise
 *   shardsell <id> <count> -> choose a verified shard, sell it, and enter its quantity as one
 *                     stateful local transaction (armed)
 *   sharddirect <id> <count> -> send one verified sale command from the currently verified Shard
 *                     Market (armed)
 *   sharddirectall <id> <count> ... -> validate all entries, then send that market plan in order
 *                     without closing the market between sales (armed)
 *   discarddirectall <uid> ... -> validate every requested bag unit, then send all direct discard
 *                     requests in order (armed)
 *   guardmods on|off -> while on, the player's own Shift/Ctrl/Alt are not recorded in this game
 *                    (the connection closing turns it off again)
 *   key <k> [ms]   -> press and release a key, held for ms (default 60). Names: a..z 0..9,
 *                     num0..num9, up down left right, space enter esc tab backspace, f1..f12,
 *                     . , ; / ' - = [ ] \, lctrl rctrl alt shift. Combinations: lctrl+alt+up
 *   keydown <k> / keyup <k> / keysup -> hold, release, release everything. Held keys are
 *                     released automatically when this connection closes.
 *
 * Bound to 127.0.0.1 only, so nothing off this machine can reach it.
 */
final class StateServer implements Runnable {

    /** Bumped whenever a command or reply changes, so the bot can tell a stale agent apart. */
    static final int VERSION = 57;   // 4-56: prior commands; 57: `discarddirectall <uid> ...` validates the bag snapshot, then sends the game's captured discard request for every uid

    static final int FIRST_PORT = 17311;
    static final int LAST_PORT = 17330;

    /** A controller that goes this long without a command is dropped. */
    private static final int IDLE_MS = 120000;

    private final String secret;

    private StateServer(String secret) {
        this.secret = secret;
    }

    static void start() {
        byte[] raw = new byte[16];
        new SecureRandom().nextBytes(raw);
        StringBuilder hex = new StringBuilder(32);
        for (byte b : raw) hex.append(String.format("%02x", b & 0xFF));

        Thread t = new Thread(new StateServer(hex.toString()), "mwagent-state");
        t.setDaemon(true);
        t.start();
    }

    @Override
    public void run() {
        ServerSocket server = bind();
        if (server == null) return;
        int port = server.getLocalPort();
        Log.info("state server listening on 127.0.0.1:" + port);
        writePortFile(port);

        while (true) {
            try {
                final Socket socket = server.accept();
                // One request, one reply: never let Nagle hold a reply back waiting for more.
                socket.setTcpNoDelay(true);
                // One thread per controller, so a bot holding its connection open doesn't lock out
                // a second tool (mem.py, the radar) at the same time.
                Thread t = new Thread(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            socket.setSoTimeout(IDLE_MS);
                            serve(socket);
                        } catch (Throwable ignored) {
                            // The peer went away or idled out.
                        } finally {
                            close(socket);
                        }
                    }
                }, "mwagent-conn");
                t.setDaemon(true);
                t.start();
            } catch (Throwable t) {
                Log.error("state server accept", t);
            }
        }
    }

    private static ServerSocket bind() {
        for (int port = FIRST_PORT; port <= LAST_PORT; port++) {
            try {
                return new ServerSocket(port, 8, InetAddress.getByName("127.0.0.1"));
            } catch (Throwable t) {
                // Taken, most likely by another game client: try the next one.
            }
        }
        Log.info("state server: no free port in " + FIRST_PORT + ".." + LAST_PORT);
        return null;
    }

    /** "port pid secret", removed again when the game exits. */
    private void writePortFile(int port) {
        try {
            String pid = ManagementFactory.getRuntimeMXBean().getName().split("@")[0];
            Path file = Log.dir().resolve("agent." + pid + ".port");
            Files.write(file, (port + " " + pid + " " + secret).getBytes(Protocol.UTF8));
            file.toFile().deleteOnExit();
        } catch (Throwable t) {
            Log.error("port file", t);
        }
    }

    /** One connection may issue several commands; it ends when the peer closes or goes quiet. */
    private void serve(Socket socket) throws Exception {
        BufferedReader in = new BufferedReader(
                new InputStreamReader(socket.getInputStream(), Protocol.UTF8));
        OutputStream out = socket.getOutputStream();
        boolean[] armed = {false};
        boolean[] guarded = {false};      // this connection turned the modifier guard on (guardmods)
        // Keys this connection holds down. Only these are let go when it closes: the bot and the
        // private-message helper both hold armed connections, and one closing must not stop the
        // other's character mid-walk.
        java.util.Set<String> mine = new java.util.HashSet<String>();

        String line = in.readLine();
        if (line == null) return;
        if (!authorised(line.trim())) {
            out.write("refused: send 'auth <secret>' first\n".getBytes(Protocol.UTF8));
            out.flush();
            return;
        }
        out.write("ok\n".getBytes(Protocol.UTF8));
        out.flush();

        try {
            while ((line = in.readLine()) != null) {
                String reply;
                try {
                    reply = handle(line.trim(), armed, mine, guarded);
                } catch (Throwable t) {
                    // One bad command (a stray number, a game object caught mid-change) must not
                    // drop the connection: the bot would see "closed" and stop the whole run.
                    Log.error("command: " + line.trim(), t);
                    reply = "{\"ok\":false,\"error\":\"" + Protocol.jsonEscape(String.valueOf(t)) + "\"}";
                }
                if (reply == null) break;
                out.write((reply + "\n").getBytes(Protocol.UTF8));
                out.flush();
            }
        } finally {
            // A controller that pressed keys and then went away (crash, Ctrl+C, timeout) must not
            // leave the character walking.
            for (String k : mine) Keys.up(k);
            if (!mine.isEmpty()) Log.info("ACTION released " + mine.size() + " key(s) a closed connection held");
            if (guarded[0]) Keys.guardModifiers(false);
        }
    }

    /** Constant-time, so the secret can't be guessed a character at a time from reply timing. */
    private boolean authorised(String line) {
        if (!line.startsWith("auth ")) return false;
        byte[] given = line.substring(5).trim().getBytes(Protocol.UTF8);
        return MessageDigest.isEqual(given, secret.getBytes(Protocol.UTF8));
    }

    /** The same as the window's close button: the game exits (it sets EXIT_ON_CLOSE). */
    private static String closeGame() {
        final java.awt.Frame win = Memory.window();
        if (win == null) return "error: no game window";
        Log.info("closing the game on request");
        java.awt.EventQueue.invokeLater(new Runnable() {
            @Override
            public void run() {
                try {
                    win.dispatchEvent(new java.awt.event.WindowEvent(win, java.awt.event.WindowEvent.WINDOW_CLOSING));
                } catch (Throwable ignored) {
                    // The window closed while this event was queued.
                }
            }
        });
        return "closing";
    }

    static String handle(String command, boolean[] armed, java.util.Set<String> mine, boolean[] guarded) {
        String[] parts = command.split("\\s+");
        String verb = parts[0].toLowerCase();

        if (verb.equals("state")) return World.snapshot();
        if (verb.equals("ping")) return "pong";
        if (verb.equals("health")) return Health.snapshot();
        if (verb.equals("version")) return "mwagent " + VERSION;
        if (verb.equals("mem")) {
            return parts.length > 1 ? Memory.section(parts[1].toLowerCase()) : Memory.snapshot();
        }
        if (verb.equals("dialog")) return Dialogs.read();
        if (verb.equals("menu")) return Dialogs.menu();
        if (verb.equals("input")) return TextInput.state();
        if (verb.equals("casts")) return World.casts(parts.length > 1 ? Long.parseLong(parts[1]) : 0L);
        if (verb.equals("wear")) return World.wear(parts.length > 1 ? Long.parseLong(parts[1]) : 0L);
        if (verb.equals("team")) return Team.snapshot();
        if (verb.equals("said")) return Said.since(parts.length > 1 ? Long.parseLong(parts[1]) : 0L);
        if (verb.equals("memprobe")) return Memory.probe();
        if (verb.equals("memfind")) return MemFind.find(command.substring(verb.length()));
        if (verb.equals("quit")) return null;

        // Anything that sends must be armed first, per connection. A stray script that connects
        // and starts issuing commands does nothing until it says so explicitly.
        if (verb.equals("arm")) {
            armed[0] = true;
            return "armed";
        }

        if (verb.equals("keys")) return Keys.status();
        if (verb.equals("pms")) {
            try {
                long after = parts.length > 1 ? Long.parseLong(parts[1]) : 0L;
                return parts.length > 2 ? PrivateChat.await(after, Long.parseLong(parts[2]))
                        : PrivateChat.since(after);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("idof")) {
            String[] who = command.trim().split("\\s+", 2);
            return who.length < 2 ? "0" : PrivateChat.idOf(who[1]);
        }
        if (verb.equals("chat")) {
            try {
                long after = parts.length > 1 ? Long.parseLong(parts[1]) : 0L;
                return GameChat.since(after, parts.length > 2 ? Long.parseLong(parts[2]) : 0L);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("say")) {
            if (!armed[0]) return "refused: send 'arm' first";
            String[] sy = command.trim().split("\\s+", 3);
            if (sy.length < 3) return "error: say <0-6> <text>";
            try {
                return GameChat.say(Integer.parseInt(sy[1]), sy[2]);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        // Sending a private message and closing the game act on the game: armed connections only.
        if ((verb.equals("pm") || verb.equals("closegame")) && !armed[0]) return "refused: send 'arm' first";
        if (verb.equals("pm")) {
            String[] pm = command.trim().split("\\s+", 3);
            if (pm.length < 3) return "error: pm <id> <text>";
            try {
                return PrivateChat.send(Integer.parseInt(pm[1]), pm[2]);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("closegame")) return closeGame();
        if (verb.equals("voice")) {
            String what = parts.length > 1 ? parts[1].toLowerCase() : "";
            if (what.equals("play")) return parts.length > 2 ? Voice.play(parts[2]) : "error: voice play <name>";
            if (what.equals("status")) return Voice.status();
            if (!armed[0]) return "refused: send 'arm' first";
            if (what.equals("record")) return Voice.record();
            if (what.equals("cancel")) return Voice.cancel();
            if (what.equals("send")) return Voice.send(java.util.Arrays.copyOfRange(parts, 1, parts.length));
            return "error: voice play <name> | record | send <3|4> | send pm <id> | cancel | status";
        }
        if (verb.equals("use") || verb.equals("select")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length < 2) return "error: " + verb + " <id>";
            try {
                int id = Integer.parseInt(parts[1]);
                return verb.equals("use") ? Act.use(id) : Act.select(id);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("slot")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length < 2) return "error: slot <1-10> [sustain] [lock]";
            try {
                int n = Integer.parseInt(parts[1]);
                boolean sustain = false;
                boolean lock = false;
                for (int i = 2; i < parts.length; i++) {
                    if (parts[i].equals("sustain")) sustain = true;
                    else if (parts[i].equals("lock")) lock = true;
                    else return "error: slot <1-10> [sustain] [lock]";
                }
                return Act.slot(n, sustain, lock);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("pick")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length < 3 || parts.length > 4) return "error: pick <layout> <id> [buy|sell]";
            try {
                return Dialogs.pick(parts[1].toLowerCase(), Integer.parseInt(parts[2]),
                        parts.length > 3 ? parts[3].toLowerCase() : null);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("shardsell")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length != 3) return "error: shardsell <item id> <count>";
            try {
                return Dialogs.shardSell(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]));
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("sharddirect")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length != 3) return "error: sharddirect <item id> <count>";
            try {
                return Dialogs.shardSellDirect(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]));
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("sharddirectall")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length < 3 || parts.length % 2 == 0) return "error: sharddirectall <item id> <count> ...";
            try {
                int[] ids = new int[(parts.length - 1) / 2];
                int[] counts = new int[ids.length];
                for (int i = 0; i < ids.length; i++) {
                    ids[i] = Integer.parseInt(parts[1 + 2 * i]);
                    counts[i] = Integer.parseInt(parts[2 + 2 * i]);
                }
                return Dialogs.shardSellDirectAll(ids, counts);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("discarddirectall")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length < 2) return "error: discarddirectall <uid> ...";
            try {
                int[] ids = new int[parts.length - 1];
                for (int i = 0; i < ids.length; i++) ids[i] = Integer.parseInt(parts[i + 1]);
                return Act.discardAll(ids);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("choose")) {
            if (!armed[0]) return "refused: send 'arm' first";
            if (parts.length != 4) return "error: choose <row> <col> <sig>";
            try {
                return Dialogs.choose(Integer.parseInt(parts[1]), Integer.parseInt(parts[2]), Integer.parseInt(parts[3]));
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        if (verb.equals("ask")) {
            if (!armed[0]) return "refused: send 'arm' first";
            return Ask.handle(parts.length > 1 ? parts[1].toLowerCase() : "");
        }
        // A quick phrase changes the player's own setup: armed connections only.
        if (verb.equals("phrase")) {
            if (!armed[0]) return "refused: send 'arm' first";
            String[] ph = command.trim().split("\\s+", 3);
            if (ph.length < 3) return "error: phrase <slot> <text>";
            try {
                return Phrases.set(Integer.parseInt(ph[1]), ph[2]);
            } catch (NumberFormatException e) {
                return "bad number in: " + command;
            }
        }
        // The player's own Shift/Ctrl/Alt ignored in this game for as long as a run asks (Keys.GuardedKeys).
        if (verb.equals("guardmods")) {
            if (!armed[0]) return "refused: send 'arm' first";
            boolean on = parts.length < 2 || !parts[1].equalsIgnoreCase("off");
            if (on == guarded[0]) return on ? "on" : "off";
            String r = Keys.guardModifiers(on);
            if (r.equals("on") || r.equals("off")) guarded[0] = on;
            return r;
        }
        // The game's own speech: "speech" -> on/off/unknown; "speech off" silences, "speech on" restores.
        if (verb.equals("speech")) {
            if (parts.length > 1 && parts[1].equalsIgnoreCase("off")) return SpeechSwitch.set(true);
            if (parts.length > 1 && parts[1].equalsIgnoreCase("on")) return SpeechSwitch.set(false);
            return SpeechSwitch.state();
        }

        // The game's voice message player: "voicefix" -> on/off/unknown; "voicefix on" mends it (the
        // default), "voicefix off" lets it play as the game does, deep voices and all (VoicePlayer).
        if (verb.equals("voicefix")) {
            if (parts.length > 1 && parts[1].equalsIgnoreCase("on")) return VoicePlayer.set(true);
            if (parts.length > 1 && parts[1].equalsIgnoreCase("off")) return VoicePlayer.set(false);
            return VoicePlayer.state();
        }

        // Keys are pressed through the game's own key handler, so they don't need the protocol
        // senders resolved -- only arming.
        boolean keying = verb.equals("key") || verb.equals("keydown") || verb.equals("keyup")
                || verb.equals("keysup") || verb.equals("type") || verb.equals("typeenter") || verb.equals("number")
                || verb.equals("typeclear") || verb.equals("typeesc");
        if (keying && !armed[0]) return "refused: send 'arm' first";
        // The input box (quantities): its own AWT handler, not the keyboard hook.
        if (verb.equals("type")) {
            int sp = command.indexOf(' ');
            return sp < 0 ? "missing argument in: " + command : TextInput.type(command.substring(sp + 1));
        }
        if (verb.equals("typeenter")) return TextInput.enter();
        if (verb.equals("number")) return parts.length == 2 ? TextInput.number(parts[1]) : "missing number";
        if (verb.equals("typeclear")) return TextInput.clear();
        if (verb.equals("typeesc")) return TextInput.escape();
        try {
            if (verb.equals("key")) {
                return Keys.tap(parts[1].toLowerCase(), parts.length > 2 ? Long.parseLong(parts[2]) : 60);
            }
            if (verb.equals("keydown")) {
                String k = parts[1].toLowerCase();
                String r = Keys.down(k);
                if (r.equals("down " + k)) mine.add(k);
                return r;
            }
            if (verb.equals("keyup")) {
                String k = parts[1].toLowerCase();
                if (!mine.remove(k)) return "up " + k + " (not held by this connection)";
                return Keys.up(k);
            }
            if (verb.equals("keysup")) {
                return Keys.release(mine);
            }
        } catch (NumberFormatException e) {
            return "bad number in: " + command;
        } catch (ArrayIndexOutOfBoundsException e) {
            return "missing argument in: " + command;
        }

        return "unknown command: " + verb;
    }

    private static void close(Socket socket) {
        if (socket == null) return;
        try {
            socket.close();
        } catch (Throwable ignored) { }
    }
}
