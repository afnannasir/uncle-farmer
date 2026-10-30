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
 * Lets the bot silence the game's speech, so a player can use the computer while the game runs.
 *
 * Every line the game speaks -- to the screen reader, to SAPI or to its own voices -- is queued by
 * one method of the sound engine (game.aT in this build): `a(int, String, int, float, float, int)`,
 * which every shorter speak method calls. Sound effects take a different path (a(byte[], ...)), so
 * they keep playing. As that class loads, this adds one field to it and three instructions to the
 * front of that method:
 *
 *     public static volatile boolean QUIET;
 *     ...  if (QUIET) return;   // then the method as it was
 *
 * The method is found by its signature, which only it has, not by its single-letter name.
 * {@link #set(boolean)} flips the field. It lives in the game's class, so it is the same for both of
 * our agents when both are loaded, and starts false (speaking) every time the game starts.
 */
final class SpeechSwitch implements ClassFileTransformer {

    static final String FIELD = "QUIET";
    private static final String DESC = "(ILjava/lang/String;IFFI)V";
    /**
     * The voices every line finally goes to, each found by a signature only it defines (the
     * classes that call them only refer to it): SAPI (game.aG in this build) and the game's own
     * two voices (Vv3, Vv4). The screen reader backend (game.Nn) speaks with a plain (String),
     * so it is known by also naming the NVDA controller. Gating the voices too matters: the
     * combat announcer (game.k: damage dealt and taken, criticals, dodges, heals) puts its lines
     * on the speech queue directly, past the method above - with only that gated, a "silenced"
     * game still read out every hit.
     *
     * The screen reader backend also has a "stop talking" method (no arguments, it calls
     * nvdaController_cancelSpeech), which the sound engine calls before each new line. That
     * one is gated too: silenced, the game still runs its speech queue, and every line it would
     * have said cut off whatever NVDA was reading for the player.
     */
    private static final String[] VOICES = {"(Ljava/lang/String;IIILjava/lang/String;FF)V",
                                            "(IIIIFFLjava/lang/String;FF)V"};
    private static final String SCREEN_READER = "(Ljava/lang/String;)V";
    private static final String NVDA = "nvdaController";

    /** Every class carrying the switch: its name and loader. */
    private static final List<Object[]> GATED = new java.util.concurrent.CopyOnWriteArrayList<Object[]>();

    private SpeechSwitch() { }

    static void install(Instrumentation inst) {
        inst.addTransformer(new SpeechSwitch(), false);
    }

    /** "on" (speaking), "off" (silenced), or "unknown" before the game's sound engine has loaded. */
    static String state() {
        List<Field> fs = fields();
        if (fs.isEmpty()) return "unknown";
        try {
            return fs.get(0).getBoolean(null) ? "off" : "on";
        } catch (IllegalAccessException e) {
            return "unknown";
        }
    }

    /** Silence (quiet = true) or restore the game's speech, in every gated class. Returns the new state. */
    static String set(boolean quiet) {
        List<Field> fs = fields();
        if (fs.isEmpty()) return "unknown";
        try {
            for (Field f : fs) f.setBoolean(null, quiet);
            Log.info("game speech " + (quiet ? "silenced" : "restored") + " (" + fs.size() + " gates)");
        } catch (IllegalAccessException e) {
            return "unknown";
        }
        return state();
    }

    private static List<Field> fields() {
        List<Field> out = new ArrayList<Field>();
        for (Object[] g : GATED) {
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

    private static void gated(String name, ClassLoader loader) {
        for (Object[] g : GATED) {
            if (g[0].equals(name)) return;
        }
        GATED.add(new Object[] {name, new WeakReference<ClassLoader>(loader)});
    }

    @Override
    public byte[] transform(ClassLoader loader, String name, Class<?> beingRedefined,
                            ProtectionDomain pd, byte[] bytes) {
        if (loader == null || name == null || beingRedefined != null) return null;
        if (!name.startsWith("game/") || name.contains("$Proxy")) return null;
        try {
            boolean reader = Bytecode.mentions(bytes, NVDA) && Bytecode.mentions(bytes, SCREEN_READER);
            if (!reader && !Bytecode.mentions(bytes, DESC) && !Bytecode.mentions(bytes, VOICES[0]) && !Bytecode.mentions(bytes, VOICES[1])) {
                return null;
            }
            byte[] out = patch(bytes, reader);
            if (out == null) return null;
            gated(name, loader);
            if (out != bytes) Log.info("speech switch added to " + name.replace('/', '.'));
            return out == bytes ? null : out;
        } catch (Throwable t) {
            try {
                Log.error("speech switch " + name, t);
            } catch (Throwable ignored) { }
            return null;
        }
    }

    /**
     * The class with the switch added; the same array when another agent already added it; null
     * when this is not the class.
     */
    static byte[] patch(byte[] in) throws IOException {
        return patch(in, false);
    }

    /** reader: this class names the NVDA controller, so its one (String) method with code speaks. */
    static byte[] patch(byte[] in, boolean reader) throws IOException {
        ClassFile cf = new ClassFile(in);
        if ((cf.access & 0x0200) != 0) return null;    // an interface has no code to gate
        List<Member> targets = new ArrayList<Member>();
        if (reader) {
            // the reader's one instance ()V method with code: cancel speech
            Member cancel = null;
            boolean unique = true;
            for (Member m : cf.methods) {
                if (!cf.utf8(m.desc).equals("()V") || cf.utf8(m.name).startsWith("<")
                        || (m.access & 0x0008) != 0 || cf.code(m) == null) continue;
                if (cancel != null) unique = false;
                cancel = m;
            }
            if (cancel != null && unique) targets.add(cancel);
        }
        List<String> descs = new ArrayList<String>();
        descs.add(DESC);
        descs.add(VOICES[0]);
        descs.add(VOICES[1]);
        if (reader) descs.add(SCREEN_READER);
        for (String d : descs) {
            Member m = cf.unique(d);                       // not unique: not a class we know
            if (m != null) targets.add(m);
        }
        if (targets.isEmpty()) return null;
        for (Member f : cf.fields) {
            if (cf.utf8(f.name).equals(FIELD)) return in;
        }

        int fname = cf.addUtf8(FIELD);
        int fdesc = cf.addUtf8("Z");
        int nat = cf.add(12, new byte[] {(byte) (fname >> 8), (byte) fname, (byte) (fdesc >> 8), (byte) fdesc});
        int ref = cf.add(9, new byte[] {(byte) (cf.thisClass >> 8), (byte) cf.thisClass, (byte) (nat >> 8), (byte) nat});

        Member field = new Member();
        field.access = 0x0001 | 0x0008 | 0x0040;      // public static volatile
        field.name = fname;
        field.desc = fdesc;
        cf.fields.add(field);

        // nop; getstatic QUIET; ifeq 8; return -- eight bytes, so a switch's padding stays as it was
        byte[] gate = {
            0x00,                                                   // 0 nop
            (byte) 0xB2, (byte) (ref >> 8), (byte) ref,             // 1 getstatic QUIET
            (byte) 0x99, 0x00, 0x04,                                // 4 ifeq 8
            (byte) 0xB1                                             // 7 return
        };
        for (Member m : targets) {
            Attr code = cf.code(m);
            code.data = Bytecode.prefix(cf, code.data, gate, new int[] {gate.length}, 1);
        }
        return cf.write();
    }
}
