package unclefarmer.agent;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.lang.ref.WeakReference;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;

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
            boolean reader = mentions(bytes, NVDA) && mentions(bytes, SCREEN_READER);
            if (!reader && !mentions(bytes, DESC) && !mentions(bytes, VOICES[0]) && !mentions(bytes, VOICES[1])) {
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

    /** A quick look for the signature's text, so only a handful of classes get parsed. */
    private static boolean mentions(byte[] b, String text) {
        byte[] t = text.getBytes(StandardCharsets.US_ASCII);
        outer:
        for (int i = 0; i + t.length <= b.length; i++) {
            for (int j = 0; j < t.length; j++) {
                if (b[i + j] != t[j]) continue outer;
            }
            return true;
        }
        return false;
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
        List<Integer> targets = new ArrayList<Integer>();
        List<String> descs = new ArrayList<String>();
        descs.add(DESC);
        descs.add(VOICES[0]);
        descs.add(VOICES[1]);
        if (reader) descs.add(SCREEN_READER);
        if (reader) {
            // the reader's one instance ()V method with code: cancel speech
            int cancel = -1;
            boolean unique = true;
            for (int i = 0; i < cf.methods.size(); i++) {
                Member m = cf.methods.get(i);
                if (!cf.utf8(m.desc).equals("()V") || cf.utf8(m.name).startsWith("<")
                        || (m.access & 0x0008) != 0 || !hasCode(cf, m)) continue;
                if (cancel >= 0) unique = false;
                cancel = i;
            }
            if (cancel >= 0 && unique) targets.add(cancel);
        }
        for (String d : descs) {
            int target = -1;
            boolean unique = true;
            for (int i = 0; i < cf.methods.size(); i++) {
                Member m = cf.methods.get(i);
                if (!cf.utf8(m.desc).equals(d) || !hasCode(cf, m)) continue;
                if (target >= 0) unique = false;           // not unique: not a class we know
                target = i;
            }
            if (target >= 0 && unique) targets.add(target);
        }
        if (targets.isEmpty()) return null;
        for (Member f : cf.fields) {
            if (cf.utf8(f.name).equals(FIELD)) return in;
        }

        int fname = cf.addUtf8(FIELD);
        int fdesc = cf.addUtf8("Z");
        int nat = cf.add(12, new byte[] {(byte) (fname >> 8), (byte) fname, (byte) (fdesc >> 8), (byte) fdesc});
        int ref = cf.add(9, new byte[] {(byte) (cf.thisClass >> 8), (byte) cf.thisClass, (byte) (nat >> 8), (byte) nat});
        int smtName = cf.addUtf8("StackMapTable");

        Member field = new Member();
        field.access = 0x0001 | 0x0008 | 0x0040;      // public static volatile
        field.name = fname;
        field.desc = fdesc;
        cf.fields.add(field);

        for (int target : targets) {
            Member m = cf.methods.get(target);
            for (int a = 0; a < m.attrs.size(); a++) {
                if (cf.utf8(m.attrs.get(a).name).equals("Code")) {
                    m.attrs.get(a).data = gateCode(cf, m.attrs.get(a).data, ref, smtName);
                }
            }
        }
        return cf.write();
    }

    private static boolean hasCode(ClassFile cf, Member m) {
        for (Attr a : m.attrs) {
            if (cf.utf8(a.name).equals("Code")) return true;
        }
        return false;
    }

    /** Bytes put in front of the method: nop; getstatic QUIET; ifeq +4; return. Eight bytes, so
     *  a switch's 4-byte padding (counted from the method start) stays as it was. */
    private static final int PREFIX = 8;

    private static byte[] gateCode(ClassFile cf, byte[] code, int fieldRef, int smtName) throws IOException {
        Reader r = new Reader(code);
        int maxStack = r.u2();
        int maxLocals = r.u2();
        int len = r.u4();
        byte[] body = r.bytes(len);
        int excCount = r.u2();
        int[][] exc = new int[excCount][4];
        for (int i = 0; i < excCount; i++) {
            for (int j = 0; j < 4; j++) exc[i][j] = r.u2();
        }
        int attrCount = r.u2();
        List<Attr> attrs = new ArrayList<Attr>();
        for (int i = 0; i < attrCount; i++) {
            Attr a = new Attr();
            a.name = r.u2();
            a.data = r.bytes(r.u4());
            attrs.add(a);
        }

        ByteArrayOutputStream bo = new ByteArrayOutputStream();
        DataOutputStream o = new DataOutputStream(bo);
        o.writeShort(Math.max(maxStack, 1));
        o.writeShort(maxLocals);
        o.writeInt(len + PREFIX);
        o.write(new byte[] {
            0x00,                                                   // 0 nop
            (byte) 0xB2, (byte) (fieldRef >> 8), (byte) fieldRef,   // 1 getstatic QUIET
            (byte) 0x99, 0x00, 0x04,                                // 4 ifeq 8
            (byte) 0xB1                                             // 7 return
        });
        o.write(body);
        o.writeShort(excCount);
        for (int[] e : exc) {
            o.writeShort(e[0] + PREFIX);
            o.writeShort(e[1] + PREFIX);
            o.writeShort(e[2] + PREFIX);
            o.writeShort(e[3]);
        }
        boolean hadFrames = false;
        for (Attr a : attrs) {
            String n = cf.utf8(a.name);
            if (n.equals("StackMapTable")) {
                a.data = frames(a.data);
                hadFrames = true;
            } else if (n.equals("LineNumberTable")) {
                shiftTable(a.data, 4);
            } else if (n.equals("LocalVariableTable") || n.equals("LocalVariableTypeTable")) {
                shiftTable(a.data, 10);
            }
        }
        if (!hadFrames) {
            Attr a = new Attr();
            a.name = smtName;
            a.data = new byte[] {0x00, 0x01, (byte) PREFIX};      // one same_frame at the old start
            attrs.add(a);
        }
        o.writeShort(attrs.size());
        for (Attr a : attrs) {
            o.writeShort(a.name);
            o.writeInt(a.data.length);
            o.write(a.data);
        }
        o.flush();
        return bo.toByteArray();
    }

    /** Tables whose entries start with a u2 pc: add PREFIX to each. */
    private static void shiftTable(byte[] t, int entrySize) {
        int n = ((t[0] & 0xFF) << 8) | (t[1] & 0xFF);
        for (int i = 0; i < n; i++) {
            int p = 2 + i * entrySize;
            int pc = (((t[p] & 0xFF) << 8) | (t[p + 1] & 0xFF)) + PREFIX;
            t[p] = (byte) (pc >> 8);
            t[p + 1] = (byte) pc;
        }
    }

    /**
     * The old start is now at PREFIX and is where ifeq lands, so it needs a frame: the method's
     * starting state, i.e. a same_frame. Frames are stored as offsets from the previous one; the
     * first is absolute. If the method already had a frame at 0, that frame just moves to PREFIX;
     * otherwise a same_frame at PREFIX goes in front and the old first frame's delta (its old
     * absolute offset O) becomes O + PREFIX - PREFIX - 1 = O - 1.
     */
    private static byte[] frames(byte[] t) {
        int n = ((t[0] & 0xFF) << 8) | (t[1] & 0xFF);
        if (n == 0) return new byte[] {0x00, 0x01, (byte) PREFIX};
        int delta = frameDelta(t, 2);
        if (delta == 0) {
            setFrameDelta(t, 2, PREFIX);   // fits: every form can hold 8
            return t;
        }
        setFrameDelta(t, 2, delta - 1);
        byte[] out = new byte[t.length + 1];
        out[0] = (byte) ((n + 1) >> 8);
        out[1] = (byte) (n + 1);
        out[2] = (byte) PREFIX;            // same_frame, offset 8
        System.arraycopy(t, 2, out, 3, t.length - 2);
        return out;
    }

    private static int frameDelta(byte[] t, int p) {
        int tag = t[p] & 0xFF;
        if (tag <= 63) return tag;
        if (tag <= 127) return tag - 64;
        if (tag >= 247) return ((t[p + 1] & 0xFF) << 8) | (t[p + 2] & 0xFF);
        throw new IllegalArgumentException("stack map frame tag " + tag);
    }

    private static void setFrameDelta(byte[] t, int p, int d) {
        int tag = t[p] & 0xFF;
        if (tag <= 63) {
            t[p] = (byte) d;
        } else if (tag <= 127) {
            t[p] = (byte) (64 + d);
        } else {
            t[p + 1] = (byte) (d >> 8);
            t[p + 2] = (byte) d;
        }
    }

    // ---------------------------------------------------------------- a minimal class file model

    private static final class Attr {
        int name;
        byte[] data;
    }

    private static final class Member {
        int access;
        int name;
        int desc;
        List<Attr> attrs = new ArrayList<Attr>();
    }

    private static final class ClassFile {
        final byte[] head = new byte[8];
        final List<byte[]> pool = new ArrayList<byte[]>();   // raw entries; index i+1 (Long/Double: next is null)
        int access;
        int thisClass;
        int superClass;
        int[] interfaces;
        final List<Member> fields = new ArrayList<Member>();
        final List<Member> methods = new ArrayList<Member>();
        final List<Attr> attrs = new ArrayList<Attr>();

        ClassFile(byte[] b) {
            Reader r = new Reader(b);
            System.arraycopy(r.bytes(8), 0, head, 0, 8);
            int count = r.u2();
            for (int i = 1; i < count; i++) {
                int start = r.p;
                int tag = r.u1();
                switch (tag) {
                    case 1: r.skip(r.u2()); break;
                    case 3: case 4: r.skip(4); break;
                    case 5: case 6: r.skip(8); break;
                    case 7: case 8: case 16: case 19: case 20: r.skip(2); break;
                    case 15: r.skip(3); break;
                    case 9: case 10: case 11: case 12: case 17: case 18: r.skip(4); break;
                    default: throw new IllegalArgumentException("constant pool tag " + tag);
                }
                pool.add(java.util.Arrays.copyOfRange(b, start, r.p));
                if (tag == 5 || tag == 6) {
                    pool.add(null);
                    i++;
                }
            }
            access = r.u2();
            thisClass = r.u2();
            superClass = r.u2();
            interfaces = new int[r.u2()];
            for (int i = 0; i < interfaces.length; i++) interfaces[i] = r.u2();
            members(r, fields);
            members(r, methods);
            readAttrs(r, attrs);
        }

        private static void members(Reader r, List<Member> into) {
            int n = r.u2();
            for (int i = 0; i < n; i++) {
                Member m = new Member();
                m.access = r.u2();
                m.name = r.u2();
                m.desc = r.u2();
                readAttrs(r, m.attrs);
                into.add(m);
            }
        }

        private static void readAttrs(Reader r, List<Attr> into) {
            int n = r.u2();
            for (int i = 0; i < n; i++) {
                Attr a = new Attr();
                a.name = r.u2();
                a.data = r.bytes(r.u4());
                into.add(a);
            }
        }

        String utf8(int index) {
            byte[] e = pool.get(index - 1);
            if (e == null || e[0] != 1) return "";
            int len = ((e[1] & 0xFF) << 8) | (e[2] & 0xFF);
            // Modified UTF-8; the names we compare are ASCII.
            return new String(e, 3, len, StandardCharsets.UTF_8);
        }

        int add(int tag, byte[] info) {
            byte[] e = new byte[info.length + 1];
            e[0] = (byte) tag;
            System.arraycopy(info, 0, e, 1, info.length);
            pool.add(e);
            return pool.size();
        }

        int addUtf8(String s) {
            for (int i = 0; i < pool.size(); i++) {
                byte[] e = pool.get(i);
                if (e != null && e[0] == 1 && utf8(i + 1).equals(s)) return i + 1;
            }
            byte[] b = s.getBytes(StandardCharsets.UTF_8);
            byte[] info = new byte[b.length + 2];
            info[0] = (byte) (b.length >> 8);
            info[1] = (byte) b.length;
            System.arraycopy(b, 0, info, 2, b.length);
            return add(1, info);
        }

        byte[] write() throws IOException {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            DataOutputStream o = new DataOutputStream(bo);
            o.write(head);
            o.writeShort(pool.size() + 1);
            for (byte[] e : pool) {
                if (e != null) o.write(e);
            }
            o.writeShort(access);
            o.writeShort(thisClass);
            o.writeShort(superClass);
            o.writeShort(interfaces.length);
            for (int i : interfaces) o.writeShort(i);
            writeMembers(o, fields);
            writeMembers(o, methods);
            writeAttrs(o, attrs);
            o.flush();
            return bo.toByteArray();
        }

        private static void writeMembers(DataOutputStream o, List<Member> ms) throws IOException {
            o.writeShort(ms.size());
            for (Member m : ms) {
                o.writeShort(m.access);
                o.writeShort(m.name);
                o.writeShort(m.desc);
                writeAttrs(o, m.attrs);
            }
        }

        private static void writeAttrs(DataOutputStream o, List<Attr> as) throws IOException {
            o.writeShort(as.size());
            for (Attr a : as) {
                o.writeShort(a.name);
                o.writeInt(a.data.length);
                o.write(a.data);
            }
        }
    }

    private static final class Reader {
        final byte[] b;
        int p;

        Reader(byte[] b) {
            this.b = b;
        }

        int u1() {
            return b[p++] & 0xFF;
        }

        int u2() {
            int v = ((b[p] & 0xFF) << 8) | (b[p + 1] & 0xFF);
            p += 2;
            return v;
        }

        int u4() {
            int v = ((b[p] & 0xFF) << 24) | ((b[p + 1] & 0xFF) << 16) | ((b[p + 2] & 0xFF) << 8) | (b[p + 3] & 0xFF);
            p += 4;
            return v;
        }

        void skip(int n) {
            p += n;
        }

        byte[] bytes(int n) {
            byte[] out = java.util.Arrays.copyOfRange(b, p, p + n);
            p += n;
            return out;
        }
    }
}
