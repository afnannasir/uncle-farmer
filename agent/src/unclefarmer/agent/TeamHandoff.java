package unclefarmer.agent;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The same sender the local confirmation invokes, with the envelope and commands read from
 * a real roster. Never infer a transfer command from a chat name or a supplied member id. */
final class TeamHandoff {
    private static final Pattern LEADER = Pattern.compile("(.+?) has become the team leader", Pattern.CASE_INSENSITIVE);
    private static final Pattern MEMBERSHIP = Pattern.compile(
            "team disbanded|.+? (?:joined|has quit|has been removed from) the team", Pattern.CASE_INSENSITIVE);

    static final class Proof {
        String owner;
        final Map<Integer, int[]> commands = new HashMap<Integer, int[]>();
    }

    private static final class Cached {
        final Object world, connection;
        final int ownId, dialogId, kind;
        final Method sender;
        final Proof proof;
        String leader;

        Cached(Object world, Object connection, int ownId, int dialogId, int kind, Method sender, Proof proof) {
            this.world = world;
            this.connection = connection;
            this.ownId = ownId;
            this.dialogId = dialogId;
            this.kind = kind;
            this.sender = sender;
            this.proof = proof;
            this.leader = proof.owner;
        }
    }

    private static Cached cached;

    private TeamHandoff() { }

    static Proof proof(Menus.Menu menu, int ownId) {
        Proof found = new Proof();
        Set<Integer> members = new HashSet<Integer>();
        for (List<Menus.Entry> row : menu.rows) {
            Menus.Entry player = row.isEmpty() ? null : row.get(0);
            if (player == null || player.flags != 8192 || player.cmd == null || player.cmd.length != 1) return null;
            int id = player.cmd[0];
            String key = player.options == null || player.options.length == 0 ? "" : player.options[0];
            String suffix = ";" + id;
            if (id <= 0 || key == null || key.length() <= suffix.length() || !key.endsWith(suffix)
                    || !members.add(id)) return null;
            if (id == ownId && has(player, "leader")) found.owner = key.substring(0, key.length() - suffix.length());
            for (Menus.Entry entry : row) {
                if (entry == null || !has(entry, "Transfer team leader")) continue;
                int[] cmd = entry.cmd;
                if (entry.flags != 14 || id == ownId || cmd == null || cmd.length != 3
                        || cmd[0] != 1 || cmd[1] != 9 || cmd[2] != id
                        || found.commands.put(Integer.valueOf(id), cmd.clone()) != null) return null;
            }
        }
        return found.owner != null && found.commands.size() == members.size() - 1
                && !found.commands.isEmpty() ? found : null;
    }

    private static boolean has(Menus.Entry entry, String text) {
        if (entry.options == null) return false;
        for (String option : entry.options) {
            if (text.equalsIgnoreCase(option)) return true;
        }
        return false;
    }

    static synchronized String learn(Object world, int dialogId, int kind, Menus.Menu menu) throws Exception {
        int ownId = Memory.selfId();
        Proof found = proof(menu, ownId);
        Object connection = Ask.gameChannel();
        if (found == null || connection == null) return "refused: not a verified leader roster";
        Class<?> network = Class.forName("game.N", false, world.getClass().getClassLoader());
        Method sender = network.getMethod("a", int.class, int.class, int[].class);
        if (!Modifier.isStatic(sender.getModifiers()) || sender.getReturnType() != void.class) {
            return "refused: the team's sender wasn't found";
        }
        remember(world, connection, ownId, dialogId, kind, sender, found);
        return "cached";
    }

    static synchronized void remember(Object world, Object connection, int ownId, int dialogId, int kind,
                                      Method sender, Proof proof) {
        cached = new Cached(world, connection, ownId, dialogId, kind, sender, proof);
    }

    static synchronized void chat(int channel, String name, String text) {
        if (cached == null || !name.isEmpty()) return;
        if (MEMBERSHIP.matcher(text).matches()) {
            cached = null;
            return;
        }
        Matcher change = LEADER.matcher(text);
        if (channel == 46 && change.matches()) cached.leader = change.group(1).trim();
    }

    static String send(int member) {
        try {
            return send(member, Memory.world(), Ask.gameChannel(), Memory.selfId());
        } catch (Memory.Unavailable e) {
            return "refused: " + e.getMessage();
        } catch (Exception e) {
            Log.error("teamtransfer", e);
            return "uncertain";
        }
    }

    static String send(int member, Object world, Object connection, int ownId) throws ReflectiveOperationException {
        Cached roster;
        synchronized (TeamHandoff.class) {
            roster = cached;
            if (roster == null || roster.world != world || roster.connection != connection
                    || roster.ownId != ownId || !roster.proof.owner.equalsIgnoreCase(roster.leader)
                    || !roster.proof.commands.containsKey(member)) return "refused: no verified transfer";
        }
        roster.sender.invoke(null, roster.dialogId, roster.kind, roster.proof.commands.get(member).clone());
        return "sent";                      // the server's leader-change line confirms it
    }
}
