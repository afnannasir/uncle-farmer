package unclefarmer.agent;

import java.io.IOException;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.security.ProtectionDomain;
import unclefarmer.agent.Bytecode.Attr;
import unclefarmer.agent.Bytecode.ClassFile;
import unclefarmer.agent.Bytecode.Member;

/**
 * Lets keys that another program sends reach the game: NVDA Remote, a controller mapper, an
 * on-screen keyboard, anything that presses keys with SendInput.
 *
 * Windows marks such a key as injected (KBDLLHOOKSTRUCT flags bit 0x10), and the game's key method
 * (game.Win's static `int a(int flags, int scanCode, int vkCode)`, see {@link Keys}) drops every
 * key that carries the mark (javap 2026-11-20, pc 480):
 *
 *     bipush 16; iand; bipush 16; if_icmpeq <swallow>      // (flags & 16) == 16
 *
 * So a blind player could not be helped over NVDA Remote, and a controller mapper needed a kernel
 * driver. As that class loads, this adds one field to it and rewrites those eight bytes to read it:
 *
 *     public static volatile int INJECTED_MASK;             // 0: injected keys count; 16: dropped
 *     getstatic INJECTED_MASK; iand; nop; ifne <swallow>    // (flags & INJECTED_MASK) != 0
 *
 * It is the same length and the jump stays at the same place, so nothing else in the method moves:
 * no offset, no stack map frame. The field starts at 0, so injected keys count from the start;
 * {@link #set(boolean)} puts the game's own check back (16) or takes it away again while the game
 * runs. A key still counts only while the game's window has focus, as a key of the keyboard does.
 *
 * The method is found by its signature and those bytes, which occur once in the class, not by its
 * single-letter name. A class without them (a game update, or another program's patch already
 * applied) is left as it is, and the log says so.
 */
final class InjectedKeys implements ClassFileTransformer {

    static final String FIELD = "INJECTED_MASK";
    static final int INJECTED = 0x10;
    private static final String WIN = "game/Win";
    private static final String DESC = "(III)I";
    /** bipush 16; iand; bipush 16; if_icmpeq - the two bytes after it are the jump's offset. */
    private static final byte[] CHECK = {0x10, 0x10, 0x7E, 0x10, 0x10, (byte) 0x9F};

    /** The game's class carrying the switch: its loader, once it has loaded. */
    private static volatile WeakReference<ClassLoader> loader;

    private InjectedKeys() { }

    /** Must run before the game's main class loads, so first thing in premain (as GamePorts). */
    static void install(Instrumentation inst) {
        inst.addTransformer(new InjectedKeys(), false);
    }

    /** "on" (injected keys count), "off" (the game drops them), or "unknown" (no switch in this game). */
    static String state() {
        Field f = field();
        if (f == null) return "unknown";
        try {
            return f.getInt(null) == 0 ? "on" : "off";
        } catch (IllegalAccessException e) {
            return "unknown";
        }
    }

    /** Let injected keys through (allow) or put the game's own check back. Returns the new state. */
    static String set(boolean allow) {
        Field f = field();
        if (f == null) return "unknown";
        try {
            f.setInt(null, allow ? 0 : INJECTED);
            Log.info("keys from other programs " + (allow ? "count" : "are dropped, as the game does"));
        } catch (IllegalAccessException e) {
            return "unknown";
        }
        return state();
    }

    private static Field field() {
        WeakReference<ClassLoader> ref = loader;
        ClassLoader l = ref == null ? null : ref.get();
        if (l == null) return null;
        try {
            return Class.forName(WIN.replace('/', '.'), false, l).getField(FIELD);
        } catch (Throwable t) {
            return null;
        }
    }

    @Override
    public byte[] transform(ClassLoader cl, String name, Class<?> beingRedefined,
                            ProtectionDomain pd, byte[] bytes) {
        if (cl == null || beingRedefined != null || !WIN.equals(name)) return null;
        try {
            byte[] out = patch(bytes);
            if (out == null) {
                Log.info("keys from other programs: the game's check wasn't found in " + name.replace('/', '.')
                         + " - left as it is");
                return null;
            }
            loader = new WeakReference<ClassLoader>(cl);
            if (out != bytes) Log.info("keys from other programs count: switch added to " + name.replace('/', '.'));
            return out == bytes ? null : out;
        } catch (Throwable t) {
            // A malformed guess must never stop the game's main class from loading.
            try {
                Log.error("injected keys " + name, t);
            } catch (Throwable ignored) { }
            return null;
        }
    }

    /**
     * The class with the switch in place of the check; the same array when another agent already
     * added it; null when this is not the class we know (no such method, or the check not there
     * exactly once).
     */
    static byte[] patch(byte[] in) throws IOException {
        ClassFile cf = new ClassFile(in);
        for (Member f : cf.fields) {
            if (cf.utf8(f.name).equals(FIELD)) return in;
        }
        Attr found = null;
        int at = -1;
        for (Member m : cf.methods) {
            if ((m.access & 0x0008) == 0 || !cf.utf8(m.desc).equals(DESC)) continue;
            Attr code = cf.code(m);
            if (code == null) continue;
            byte[] d = code.data;
            int end = Bytecode.CODE_START + (((d[4] & 0xFF) << 24) | ((d[5] & 0xFF) << 16) | ((d[6] & 0xFF) << 8) | (d[7] & 0xFF));
            for (int i = Bytecode.CODE_START; i + CHECK.length + 2 <= end; i++) {
                if (!matches(d, i)) continue;
                if (found != null) return null;        // twice: not the class we know
                found = code;
                at = i;
            }
        }
        if (found == null) return null;

        int fname = cf.addUtf8(FIELD);
        int fdesc = cf.addUtf8("I");
        int nat = cf.add(12, new byte[] {(byte) (fname >> 8), (byte) fname, (byte) (fdesc >> 8), (byte) fdesc});
        int ref = cf.add(9, new byte[] {(byte) (cf.thisClass >> 8), (byte) cf.thisClass, (byte) (nat >> 8), (byte) nat});

        Member field = new Member();
        field.access = 0x0001 | 0x0008 | 0x0040;      // public static volatile
        field.name = fname;
        field.desc = fdesc;
        cf.fields.add(field);

        byte[] d = found.data.clone();
        d[at] = (byte) 0xB2;                          // getstatic INJECTED_MASK
        d[at + 1] = (byte) (ref >> 8);
        d[at + 2] = (byte) ref;
        d[at + 3] = 0x7E;                             // iand
        d[at + 4] = 0x00;                             // nop
        d[at + 5] = (byte) 0x9A;                      // ifne, where if_icmpeq was: its offset stays
        found.data = d;
        return cf.write();
    }

    private static boolean matches(byte[] d, int i) {
        for (int j = 0; j < CHECK.length; j++) {
            if (d[i + j] != CHECK[j]) return false;
        }
        return true;
    }
}
