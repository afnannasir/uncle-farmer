package unclefarmer.agent;

import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;

/**
 * Lets more than one game client run at the same time.
 *
 * The client allows one copy of itself through two fixed listening sockets (javap 2026-10-04):
 *
 *   game.Win.main      new ServerSocket(18458) -> Win.j; if that fails, System.exit(0). A second
 *                      client therefore closes itself about 5 ms after starting.
 *   game.i.<clinit>    new ServerSocket(19276) -> i.an; a failure is swallowed, but game.i later
 *                      returns early when an is null, so that client has no speech and can't log in.
 *
 * Neither socket is ever used for anything else: Win.j has a thread that accepts and drops, and
 * i.an is only checked for null. So each client can bind its own free port instead. This
 * transformer rewrites the constant as the classes load: `sipush 18458` / `sipush 19276`, directly
 * followed by `invokespecial java/net/ServerSocket.<init>(I)V`, becomes `sipush 0`. That is the same
 * length, so nothing else in the class moves. Every client started with the agent then works,
 * including the first one, and no client ever has to give a port up while it runs.
 *
 * The classes are recognised by that byte pattern, not by their single-letter names, which change
 * with game patches. A class that has already been patched (by the other agent jar in the same
 * JVM) no longer matches and is left alone.
 */
final class GamePorts implements ClassFileTransformer {

    static final int[] PORTS = {18458, 19276};

    /** Our own package as a class-name prefix. Worked out from this class and not from Agent.PKG:
     *  touching another of our classes from inside transform() makes that class load through
     *  transform() while it is still loading, and the agent dies before its log is even open. */
    private static final String OWN = GamePorts.class.getName()
            .substring(0, GamePorts.class.getName().lastIndexOf('.') + 1).replace('.', '/');

    private static final int SIPUSH = 0x11;
    private static final int INVOKESPECIAL = 0xB7;

    private GamePorts() { }

    /** Must run before the game's main class loads, so first thing in premain. */
    static void install(Instrumentation inst) {
        inst.addTransformer(new GamePorts(), false);
    }

    @Override
    public byte[] transform(ClassLoader loader, String name, Class<?> beingRedefined,
                            ProtectionDomain pd, byte[] bytes) {
        if (loader == null || name == null || beingRedefined != null) return null;
        if (name.startsWith("java/") || name.startsWith("javax/") || name.startsWith("sun/")
                || name.startsWith("com/sun/") || name.startsWith("jdk/") || name.startsWith("io/netty/")
                || name.startsWith(OWN)) {
            return null;
        }
        try {
            if (!mentionsPort(bytes)) return null;      // the cheap test, true for almost no class
            return patch(name, bytes);
        } catch (Throwable t) {
            // A malformed guess must never stop a class from loading.
            try {
                Log.error("game ports " + name, t);
            } catch (Throwable ignored) { }
            return null;
        }
    }

    private static boolean mentionsPort(byte[] b) {
        for (int i = 0; i + 2 < b.length; i++) {
            if ((b[i] & 0xFF) != SIPUSH) continue;
            int v = ((b[i + 1] & 0xFF) << 8) | (b[i + 2] & 0xFF);
            for (int port : PORTS) {
                if (v == port) return true;
            }
        }
        return false;
    }

    private static byte[] patch(String name, byte[] in) {
        ConstantPool cp = new ConstantPool(in);
        byte[] out = null;
        // Only the bytes after the constant pool can be code; a string constant that happens to
        // contain the pattern is never touched.
        for (int i = cp.end; i + 5 < in.length; i++) {
            if ((in[i] & 0xFF) != SIPUSH || (in[i + 3] & 0xFF) != INVOKESPECIAL) continue;
            int v = ((in[i + 1] & 0xFF) << 8) | (in[i + 2] & 0xFF);
            boolean guard = false;
            for (int port : PORTS) {
                if (v == port) guard = true;
            }
            if (!guard) continue;
            int ref = ((in[i + 4] & 0xFF) << 8) | (in[i + 5] & 0xFF);
            if (!cp.isMethod(ref, "java/net/ServerSocket", "<init>", "(I)V")) continue;
            if (out == null) out = in.clone();
            out[i + 1] = 0;
            out[i + 2] = 0;
            Log.info("game ports: " + name.replace('/', '.') + " takes any free port instead of " + v
                    + ", so another game can start beside it");
        }
        return out;
    }

    /** Just enough of the constant pool to resolve a method reference. */
    private static final class ConstantPool {

        private final byte[] b;
        private final int[] offset;     // where each entry's tag byte is
        final int end;                  // first byte after the pool

        ConstantPool(byte[] b) {
            this.b = b;
            int count = u2(8);
            offset = new int[count];
            int p = 10;
            for (int i = 1; i < count; i++) {
                offset[i] = p;
                int tag = b[p] & 0xFF;
                switch (tag) {
                    case 1: p += 3 + u2(p + 1); break;                   // Utf8
                    case 3: case 4: p += 5; break;                       // Integer, Float
                    case 5: case 6: p += 9; i++; break;                  // Long, Double: two slots
                    case 7: case 8: case 16: case 19: case 20: p += 3; break;
                    case 15: p += 4; break;                              // MethodHandle
                    case 9: case 10: case 11: case 12: case 17: case 18: p += 5; break;
                    default: throw new IllegalArgumentException("constant pool tag " + tag + " at " + p);
                }
            }
            end = p;
        }

        boolean isMethod(int index, String owner, String method, String desc) {
            if (index <= 0 || index >= offset.length || tag(index) != 10) return false;
            int p = offset[index];
            int cls = u2(p + 1);
            int nat = u2(p + 3);
            if (tag(cls) != 7 || tag(nat) != 12) return false;
            int q = offset[nat];
            return utf8(u2(offset[cls] + 1)).equals(owner)
                    && utf8(u2(q + 1)).equals(method)
                    && utf8(u2(q + 3)).equals(desc);
        }

        private int tag(int index) {
            return index > 0 && index < offset.length ? b[offset[index]] & 0xFF : -1;
        }

        private String utf8(int index) {
            if (tag(index) != 1) return "";
            int p = offset[index];
            // Class and member names here are plain ASCII.
            return new String(b, p + 3, u2(p + 1), java.nio.charset.StandardCharsets.ISO_8859_1);
        }

        private int u2(int p) {
            return ((b[p] & 0xFF) << 8) | (b[p + 1] & 0xFF);
        }
    }
}
