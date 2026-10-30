package unclefarmer.agent;

import java.lang.instrument.Instrumentation;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * The agent shipped with Uncle Farmer -- the build other players get.
 *
 * It keeps the world model fed, answers the bot on loopback (StateServer, with its per-launch
 * secret) and puts its key presses on the game's own input queue (Keys). It lets several game clients
 * run at once (GamePorts) and names each window after its character (Titles). It does not dump classes,
 * log packets or write chat logs, and it carries none of the protocol-level actions: everything the
 * bot does is a key press, handled by the game the way it handles the keyboard.
 *
 * Attach with:  -javaagent:<Uncle Farmer>/agent/unclefarmer-agent.jar=<Uncle Farmer>/agent/run
 * Uncle Farmer adds and removes that line itself.
 */
public final class BotAgent {

    private BotAgent() { }

    public static void premain(String args, Instrumentation inst) {
        try {
            GamePorts.install(inst);       // first: the game's main class loads right after premain
            Health.install();
            SpeechSwitch.install(inst);
            VoicePlayer.install(inst);
            PrivateChat.init(inst);
        } catch (Throwable ignored) { }
        try {
            Path out = Agent.outputDir(args, "run");
            Files.createDirectories(out);
            Log.init(out);
            Log.info("Uncle Farmer agent attached  java=" + System.getProperty("java.version"));
            writeGameFolder(out);
            Agent.start(new ChannelHunter(inst));
            Keys.start();
            StateServer.start();
            Titles.start();
        } catch (Throwable t) {
            // The game must start even if the agent is broken.
            try {
                Log.error("premain", t);
            } catch (Throwable ignored) { }
        }
    }

    /** The game's own folder (its working folder: it opens config/, users/ and voice/ from there)
     *  beside the logs: in game_folder.txt (the last game started), so Uncle Farmer finds the game
     *  it is installed in even when its settings have no game folder, and in agent.<pid>.folder for
     *  this game alone (gone when it exits) - with several copies of the game, a character's
     *  phrases and key settings are in the copy it runs from. */
    static void writeGameFolder(Path out) {
        try {
            String folder = new java.io.File(System.getProperty("user.dir")).getAbsolutePath();
            byte[] bytes = folder.getBytes(Protocol.UTF8);
            Files.write(out.resolve("game_folder.txt"), bytes);
            String pid = java.lang.management.ManagementFactory.getRuntimeMXBean().getName().split("@")[0];
            Path mine = out.resolve("agent." + pid + ".folder");
            Files.write(mine, bytes);
            mine.toFile().deleteOnExit();
        } catch (Throwable t) {
            Log.error("game folder", t);
        }
    }
}
