package unclefarmer.agent;

import java.util.Random;

/**
 * Deterministic random-byte fuzzing for the packet parsers.
 *
 * The agent runs inside the game's JVM: an exception that escapes a packet entry point can take
 * the client down. This exercises the World tap and the lower-level parsers directly, so a bad
 * length, offset or text field is found here rather than in a live game.
 */
public final class PacketFuzz {

    private static final int MAX_LEN = 512;

    private PacketFuzz() { }

    public static void main(String[] args) {
        long seed = args.length > 0 ? Long.decode(args[0]).longValue() : 0x5EEDL;
        int iterations = args.length > 1 ? Integer.parseInt(args[1]) : 100000;
        Random random = new Random(seed);

        for (int i = 0; i < iterations; i++) {
            byte[] packet = new byte[random.nextInt(MAX_LEN + 1)];
            random.nextBytes(packet);
            exercise(packet, "random #" + i);
        }

        for (int length = 0; length <= 100; length++) {
            for (int op : new int[] {6, 12, 22, 23, 32, 36, 37, 41, 51, 56, 59, 60, 62}) {
                byte[] packet = new byte[length];
                random.nextBytes(packet);
                if (length > 0) packet[0] = (byte) op;
                exercise(packet, "op=" + op + " len=" + length);
            }
        }

        for (int length = 0; length <= MAX_LEN; length++) {
            byte[] zeros = new byte[length];
            exercise(zeros, "zeros len=" + length);
            byte[] ones = new byte[length];
            for (int i = 0; i < length; i++) ones[i] = (byte) 0xFF;
            exercise(ones, "ff len=" + length);
        }

        // The exact short boundary that used to read past the end of a 25-byte op=12.
        byte[] movement = new byte[25];
        movement[0] = 12;
        exercise(movement, "op=12 len=25");

        try {
            World.snapshot();
            World.casts(0L);
            World.wear(0L);
            Team.snapshot();
            Said.since(0L);
            PrivateChat.since(0L);
        } catch (Throwable t) {
            throw new AssertionError("a world reader threw after fuzzing", t);
        }

        System.out.println("packet fuzz ok: " + iterations + " random packets + targeted cases, seed " + seed);
    }

    private static void exercise(byte[] packet, String label) {
        try {
            World.inbound(packet);
            World.outbound(packet);

            Protocol.readInt(packet, 0);
            Protocol.readInt(packet, packet.length - 1);
            Protocol.readShort(packet, 0);
            Protocol.readShort(packet, packet.length - 1);
            Protocol.entityName(packet);
            Protocol.entityPosition(packet);
            Protocol.positionOffset(packet);
            Protocol.mapName(packet);
            Protocol.jsonEscape(new String(packet, Protocol.UTF8));

            // These are package-private parser steps reached from World.inbound. Call them
            // directly so an exception cannot be hidden by World's outer fail-safe catch.
            if (packet.length >= 2) Sheet.inbound(packet);
            Vitals.inbound(packet);
            Points.inbound(packet);
            Said.inbound(packet);
            PrivateChat.inbound(packet);
            Team.inbound(packet);
        } catch (Throwable t) {
            throw new AssertionError("packet parser threw for " + label, t);
        }
    }
}
