package unclefarmer.agent;

import java.util.ArrayList;
import java.util.List;

/** DeadLinks with fake channels, event loops and groups shaped like netty's: no sockets are opened. */
final class LinkTests {
    private static int checks;

    private LinkTests() { }

    static void run() {
        aDroppedConnectionsThreadsAreShutDownAfterTheGrace();
        aGroupStillRunningAnOpenChannelIsLeftAlone();
        anOldVoiceSocketIsClosedThenItsThreadsShutDown();
        withoutTheCurrentVoiceSocketNothingIsClosed();
        aGroupAlreadyShuttingDownIsLeftAlone();
        theSoundDispatcherIsOnlyHookedWhenSoundsAreWanted();
        System.out.println("link tests: " + checks + " checks passed");
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    // ------------------------------------------------------------- fakes shaped like netty's
    public static final class Group {
        public int shutdowns;
        public boolean shuttingDown;
        public Object shutdownGracefully() { shutdowns++; shuttingDown = true; return this; }
        public boolean isShuttingDown() { return shuttingDown; }
    }

    public static final class Loop {
        private final Group group;
        Loop(Group group) { this.group = group; }
        public Group parent() { return group; }
    }

    public static class SocketChannel {
        public boolean open = true;
        private final Loop loop;
        SocketChannel(Group group) { loop = new Loop(group); }
        public boolean isActive() { return open; }
        public Loop eventLoop() { return loop; }
        public Object close() { open = false; return this; }
    }

    public static final class DatagramChannel extends SocketChannel {
        DatagramChannel(Group group) { super(group); }
    }

    // ------------------------------------------------------------- tests
    private static void aDroppedConnectionsThreadsAreShutDownAfterTheGrace() {
        DeadLinks links = new DeadLinks();
        Group g = new Group();
        SocketChannel old = new SocketChannel(g);
        links.tapped(old);
        old.open = false;                       // the server dropped it
        List<Object> open = new ArrayList<Object>();
        links.closed(old, 1000);
        links.reap(open, 1000 + DeadLinks.GRACE_MS - 1);
        check(g.shutdowns == 0, "not before the grace");
        links.reap(open, 1000 + DeadLinks.GRACE_MS);
        check(g.shutdowns == 1 && links.groupsShut == 1, "shut down once the grace is over");
        links.reap(open, 1000 + 3 * DeadLinks.GRACE_MS);
        check(g.shutdowns == 1 && links.pendingForTests() == 0, "only once");
    }

    private static void aGroupStillRunningAnOpenChannelIsLeftAlone() {
        DeadLinks links = new DeadLinks();
        Group shared = new Group();
        SocketChannel closed = new SocketChannel(shared);
        SocketChannel live = new SocketChannel(shared);
        links.tapped(closed);
        links.tapped(live);
        closed.open = false;
        links.closed(closed, 0);
        List<Object> open = new ArrayList<Object>();
        open.add(live);
        links.reap(open, DeadLinks.GRACE_MS * 2);
        check(shared.shutdowns == 0 && links.pendingForTests() == 0, "a group an open channel runs on stays");
    }

    private static void anOldVoiceSocketIsClosedThenItsThreadsShutDown() {
        DeadLinks links = new DeadLinks();
        Group oldGroup = new Group();
        Group newGroup = new Group();
        DatagramChannel oldVoice = new DatagramChannel(oldGroup);
        DatagramChannel voice = new DatagramChannel(newGroup);
        SocketChannel server = new SocketChannel(new Group());
        links.tapped(oldVoice);
        links.tapped(server);
        links.tapped(voice);
        List<Object> open = new ArrayList<Object>();
        open.add(oldVoice);
        open.add(server);
        open.add(voice);
        links.closeOldVoice(open, voice);
        check(!oldVoice.open && voice.open && server.open, "only the voice socket before the current one closes");
        check(links.voiceClosed == 1, "counted");
        open.remove(oldVoice);
        links.closed(oldVoice, 0);
        links.reap(open, DeadLinks.GRACE_MS);
        check(oldGroup.shutdowns == 1 && newGroup.shutdowns == 0, "its group goes, the current one stays");
    }

    private static void withoutTheCurrentVoiceSocketNothingIsClosed() {
        DeadLinks links = new DeadLinks();
        DatagramChannel a = new DatagramChannel(new Group());
        DatagramChannel b = new DatagramChannel(new Group());
        links.tapped(a);
        List<Object> open = new ArrayList<Object>();
        open.add(a);
        links.closeOldVoice(open, null);
        links.closeOldVoice(open, b);           // a current socket the hunter hasn't tapped yet
        check(a.open, "the game's current voice socket unknown: nothing is closed");
        DatagramChannel newer = new DatagramChannel(new Group());
        links.tapped(newer);
        open.add(newer);
        links.closeOldVoice(open, a);           // the current one is older than another tapped one
        check(a.open && newer.open, "a socket tapped after the current one is never closed");
    }

    private static void aGroupAlreadyShuttingDownIsLeftAlone() {
        DeadLinks links = new DeadLinks();
        Group g = new Group();
        g.shuttingDown = true;
        SocketChannel c = new SocketChannel(g);
        links.tapped(c);
        c.open = false;
        links.closed(c, 0);
        links.reap(new ArrayList<Object>(), DeadLinks.GRACE_MS);
        check(g.shutdowns == 0 && links.groupsShut == 0, "the game's own shutdown is not repeated");
    }

    private static void theSoundDispatcherIsOnlyHookedWhenSoundsAreWanted() {
        SoundControl.resetForTests();
        SoundControl.mute("off");
        SoundControl.watch("off");
        SoundControl.listen(new Object(), "off");
        check(SoundControl.status().contains(SoundControl.IDLE), "nothing wanted: no hook was tried");
        SoundControl.mute("5");                 // no game window in the tests: the hook is tried and waits
        check(SoundControl.status().contains(SoundControl.NO_WINDOW), "a mute tries the hook");
        SoundControl.mute("off");
        SoundControl.resetForTests();
    }
}
