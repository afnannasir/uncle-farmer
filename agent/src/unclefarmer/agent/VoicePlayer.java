package unclefarmer.agent;

import java.io.IOException;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;
import unclefarmer.agent.Bytecode.Attr;
import unclefarmer.agent.Bytecode.ClassFile;
import unclefarmer.agent.Bytecode.Member;

/**
 * Mends the game's voice message player (game.ac in this build, javap 2026-10-29), which with two
 * games open could go on playing every voice message slow and deep in one of them.
 *
 * How it goes wrong. Each game saves a voice message it receives as voice/&lt;name&gt; and its
 * auto-play reads the file back from there a moment later. Two games share one voice folder, so
 * both write the same file at once: one game can read it while the other has just emptied it to
 * write it again, and gets only zeros. The player takes the first byte as the sound's rate (0: 8
 * kHz) and opens a new sound line for it - but with the rate it had before, then keeps the new
 * one. So after that one empty read, the next real 16 kHz message opens its line at 8 kHz and
 * plays at half speed, an octave down, and so does every message after it until the game closes.
 * A zero frame length also kept the player decoding nothing, forever.
 *
 * The player's method is a(byte[], float, float) - the sound, and where the speaker stands. As
 * the class loads, two changes:
 *
 *   the new line is opened at the new rate: inside `if (rate != this.rate) { ... }` every read of
 *   the field becomes a read of the local holding the new rate (`aload_0; getfield` to `iload;
 *   nop...`, the same length, so nothing else moves);
 *
 *   in front, a sound the player can't play is left alone: null, under 3 bytes, a zero frame
 *   length, or a first byte that isn't mono or stereo (0-19) - such as a half-read file.
 *
 * Some players like the deep voices, so the fix can be switched off while the game runs (the
 * `voicefix` command, from a setting in the app). The game's own method is kept as it was under
 * another name, and the mended one starts with
 *
 *     if (ORIGINAL) { theGamesOwn(sound, x, y); return; }
 *
 * ORIGINAL is a field added to the class: false (mended) every time the game starts.
 *
 * The class is recognised by that method and what it calls, not by its single-letter name.
 */
final class VoicePlayer implements ClassFileTransformer {

    static final String FIELD = "ORIGINAL";
    private static final String KEPT = "unclefarmer$asTheGamePlays";
    private static final String DESC = "([BFF)V";
    private static final String DECODER = "createDecoder";
    private static final String FORMAT = "javax/sound/sampled/AudioFormat";

    private static final int ALOAD_0 = 0x2A;
    private static final int GETFIELD = 0xB4;
    private static final int PUTFIELD = 0xB5;
    private static final int ILOAD = 0x15;
    private static final int ILOAD_0 = 0x1A;
    private static final int IF_ICMPEQ = 0x9F;

    /** In front of the player: return unless the sound (local 1) looks like one it can play. */
    static final byte[] GUARD = {
        0x00,                                   //  0 nop (the prefix stays a multiple of 4)
        0x2B,                                   //  1 aload_1
        (byte) 0xC6, 0x00, 0x1D,                //  2 ifnull 31
        0x2B,                                   //  5 aload_1
        (byte) 0xBE,                            //  6 arraylength
        0x06,                                   //  7 iconst_3
        (byte) 0xA1, 0x00, 0x17,                //  8 if_icmplt 31
        0x2B,                                   // 11 aload_1
        0x04,                                   // 12 iconst_1
        0x33,                                   // 13 baload: the frame length
        (byte) 0x99, 0x00, 0x11,                // 14 ifeq 31
        0x2B,                                   // 17 aload_1
        0x03,                                   // 18 iconst_0
        0x33,                                   // 19 baload: channels * 10 + rate code
        (byte) 0x9B, 0x00, 0x0B,                // 20 iflt 31
        0x2B,                                   // 23 aload_1
        0x03,                                   // 24 iconst_0
        0x33,                                   // 25 baload
        0x10, 0x14,                             // 26 bipush 20
        (byte) 0xA1, 0x00, 0x04,                // 28 if_icmplt 32
        (byte) 0xB1,                            // 31 return
    };

    /** Every class carrying the switch: its name and loader. */
    private static final List<Object[]> MENDED = new java.util.concurrent.CopyOnWriteArrayList<Object[]>();

    private VoicePlayer() { }

    static void install(Instrumentation inst) {
        inst.addTransformer(new VoicePlayer(), false);
    }

    @Override
    public byte[] transform(ClassLoader loader, String name, Class<?> beingRedefined,
                            ProtectionDomain pd, byte[] bytes) {
        if (loader == null || name == null || beingRedefined != null) return null;
        if (!name.startsWith("game/") || name.contains("$Proxy")) return null;
        try {
            if (!Bytecode.mentions(bytes, DECODER) || !Bytecode.mentions(bytes, FORMAT)
                    || !Bytecode.mentions(bytes, DESC)) {
                return null;
            }
            byte[] out = patch(bytes);
            if (out == null) return null;
            MENDED.add(new Object[] {name, new WeakReference<ClassLoader>(loader)});
            Log.info("voice player mended in " + name.replace('/', '.'));
            return out;
        } catch (Throwable t) {
            // A malformed guess must never stop a class from loading.
            try {
                Log.error("voice player " + name, t);
            } catch (Throwable ignored) { }
            return null;
        }
    }

    /** "on" (mended), "off" (as the game plays), or "unknown" before the player has loaded. */
    static String state() {
        List<Field> fs = fields();
        if (fs.isEmpty()) return "unknown";
        try {
            return fs.get(0).getBoolean(null) ? "off" : "on";
        } catch (IllegalAccessException e) {
            return "unknown";
        }
    }

    /** Mend the player (on) or let it play as the game does (off). Returns the new state. */
    static String set(boolean on) {
        List<Field> fs = fields();
        if (fs.isEmpty()) return "unknown";
        try {
            for (Field f : fs) f.setBoolean(null, !on);
            Log.info("voice player " + (on ? "mended" : "as the game plays it"));
        } catch (IllegalAccessException e) {
            return "unknown";
        }
        return state();
    }

    private static List<Field> fields() {
        List<Field> out = new ArrayList<Field>();
        for (Object[] g : MENDED) {
            @SuppressWarnings("unchecked")
            ClassLoader l = ((WeakReference<ClassLoader>) g[1]).get();
            if (l == null) continue;
            try {
                out.add(Class.forName(((String) g[0]).replace('/', '.'), false, l).getField(FIELD));
            } catch (Throwable ignored) {
                // not loadable any more
            }
        }
        return out;
    }

    /** The class with the player mended; null when it isn't the player we know, or already is. */
    static byte[] patch(byte[] in) throws IOException {
        ClassFile cf = new ClassFile(in);
        Member m = cf.unique(DESC);
        if (m == null || (m.access & 0x0008) != 0) return null;
        for (Member f : cf.fields) {
            if (cf.utf8(f.name).equals(FIELD)) return null;
        }
        Attr code = cf.code(m);
        byte[] data = code.data.clone();
        int len = ((data[4] & 0xFF) << 24) | ((data[5] & 0xFF) << 16) | ((data[6] & 0xFF) << 8) | (data[7] & 0xFF);
        if (!openAtNewRate(data, Bytecode.CODE_START, Bytecode.CODE_START + len)) return null;

        // the game's own player, as it was, under another name: private, the rest of it unchanged
        Member kept = new Member();
        kept.access = (m.access & ~0x0005) | 0x0002;
        kept.name = cf.addUtf8(KEPT);
        kept.desc = m.desc;
        kept.attrs = new ArrayList<Attr>(m.attrs);
        cf.methods.add(kept);

        int fname = cf.addUtf8(FIELD);
        int fdesc = cf.addUtf8("Z");
        int fnat = cf.add(12, u2u2(fname, fdesc));
        int field = cf.add(9, u2u2(cf.thisClass, fnat));
        int mnat = cf.add(12, u2u2(kept.name, kept.desc));
        int method = cf.add(10, u2u2(cf.thisClass, mnat));
        Member f = new Member();
        f.access = 0x0001 | 0x0008 | 0x0040;      // public static volatile
        f.name = fname;
        f.desc = fdesc;
        cf.fields.add(f);

        byte[] choice = {
            (byte) 0xB2, (byte) (field >> 8), (byte) field,          //  0 getstatic ORIGINAL
            (byte) 0x99, 0x00, 0x0D,                                 //  3 ifeq 16
            0x2A, 0x2B, 0x24, 0x25,                                  //  6 aload_0; aload_1; fload_2; fload_3
            (byte) 0xB7, (byte) (method >> 8), (byte) method,        // 10 invokespecial the game's own
            0x00, 0x00,                                              // 13 nops (the prefix stays a multiple of 4)
            (byte) 0xB1,                                             // 15 return
        };
        byte[] prefix = new byte[choice.length + GUARD.length];
        System.arraycopy(choice, 0, prefix, 0, choice.length);
        System.arraycopy(GUARD, 0, prefix, choice.length, GUARD.length);
        int[] targets = {choice.length, choice.length + 31, choice.length + 32};

        Attr mended = new Attr();
        mended.name = code.name;
        mended.data = Bytecode.prefix(cf, data, prefix, targets, 4);
        m.attrs.set(m.attrs.indexOf(code), mended);
        return cf.write();
    }

    private static byte[] u2u2(int a, int b) {
        return new byte[] {(byte) (a >> 8), (byte) a, (byte) (b >> 8), (byte) b};
    }

    /**
     * Finds `iload r; aload_0; getfield F; if_icmpeq end` (the new rate against the old) and, up
     * to end, turns every `aload_0; getfield F` into `iload r` and nops. Only when there is exactly
     * one such test, its block stores F (the new rate kept) and reads it at least twice (the
     * AudioFormat's sample and frame rates). True when the code was changed.
     */
    static boolean openAtNewRate(byte[] c, int from, int to) {
        int found = -1;
        int foundLoad = 0;
        for (int i = from; i + 8 <= to; i++) {
            int op = c[i] & 0xFF;
            int load;
            if (op >= ILOAD_0 && op <= ILOAD_0 + 3) {
                load = 1;
            } else if (op == ILOAD) {
                load = 2;
            } else {
                continue;
            }
            int j = i + load;
            if (j + 6 >= to || (c[j] & 0xFF) != ALOAD_0 || (c[j + 1] & 0xFF) != GETFIELD
                    || (c[j + 4] & 0xFF) != IF_ICMPEQ) continue;
            if (found >= 0) return false;      // two such tests: not the player we know
            found = i;
            foundLoad = load;
        }
        if (found < 0) return false;
        int j = found + foundLoad;
        int fieldHi = c[j + 2];
        int fieldLo = c[j + 3];
        int branch = j + 4;
        int end = branch + (short) (((c[branch + 1] & 0xFF) << 8) | (c[branch + 2] & 0xFF));
        if (end <= branch || end > to) return false;

        boolean stores = false;
        int reads = 0;
        for (int k = branch + 3; k + 2 < end; k++) {
            if ((c[k] & 0xFF) == PUTFIELD && c[k + 1] == fieldHi && c[k + 2] == fieldLo) stores = true;
            if (k + 3 < end && (c[k] & 0xFF) == ALOAD_0 && (c[k + 1] & 0xFF) == GETFIELD
                    && c[k + 2] == fieldHi && c[k + 3] == fieldLo) reads++;
        }
        if (!stores || reads < 2) return false;
        for (int k = branch + 3; k + 3 < end; k++) {
            if ((c[k] & 0xFF) == ALOAD_0 && (c[k + 1] & 0xFF) == GETFIELD && c[k + 2] == fieldHi && c[k + 3] == fieldLo) {
                c[k] = c[found];
                c[k + 1] = foundLoad == 2 ? c[found + 1] : 0;
                c[k + 2] = 0;
                c[k + 3] = 0;
                k += 3;
            }
        }
        return true;
    }
}
