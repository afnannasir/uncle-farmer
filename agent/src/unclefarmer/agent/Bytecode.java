package unclefarmer.agent;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * A minimal class file model for the agent's few changes to game classes as they load
 * (SpeechSwitch, VoicePlayer): read a class, add constants and fields, put instructions in front
 * of a method, write it back. No library: the agent is built with javac alone.
 */
final class Bytecode {

    private Bytecode() { }

    static final class Attr {
        int name;
        byte[] data;
    }

    static final class Member {
        int access;
        int name;
        int desc;
        List<Attr> attrs = new ArrayList<Attr>();
    }

    /** A quick look for a name or signature's text, so only a handful of classes get parsed. */
    static boolean mentions(byte[] b, String text) {
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

    static final class ClassFile {
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

        /** The method's Code attribute, or null (abstract, native). */
        Attr code(Member m) {
            for (Attr a : m.attrs) {
                if (utf8(a.name).equals("Code")) return a;
            }
            return null;
        }

        /** The one method with code of this signature; null when there is none or more than one
         *  (not a class we know). */
        Member unique(String desc) {
            Member found = null;
            for (Member m : methods) {
                if (!utf8(m.desc).equals(desc) || code(m) == null) continue;
                if (found != null) return null;
                found = m;
            }
            return found;
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

    static final class Reader {
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

    // ---------------------------------------------------------------- instructions in front of a method

    /** Where a Code attribute's instructions start: after max_stack, max_locals and the length. */
    static final int CODE_START = 8;

    /**
     * A Code attribute with prefix put in front of the method's own instructions. The prefix's
     * length must be a multiple of 4, so a switch's padding (counted from the method start) stays
     * as it was. targets are the offsets inside the prefix that a jump lands on, in order; the
     * method's state there is its starting state, so each gets a same_frame. The last is
     * prefix.length, the old start, where the method itself begins.
     */
    static byte[] prefix(ClassFile cf, byte[] code, byte[] prefix, int[] targets, int minStack) throws IOException {
        int shift = prefix.length;
        if (shift % 4 != 0) throw new IllegalArgumentException("prefix of " + shift + " bytes");
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
        o.writeShort(Math.max(maxStack, minStack));
        o.writeShort(maxLocals);
        o.writeInt(len + shift);
        o.write(prefix);
        o.write(body);
        o.writeShort(excCount);
        for (int[] e : exc) {
            o.writeShort(e[0] + shift);
            o.writeShort(e[1] + shift);
            o.writeShort(e[2] + shift);
            o.writeShort(e[3]);
        }
        boolean hadFrames = false;
        for (Attr a : attrs) {
            String n = cf.utf8(a.name);
            if (n.equals("StackMapTable")) {
                a.data = frames(a.data, targets, shift);
                hadFrames = true;
            } else if (n.equals("LineNumberTable")) {
                shiftTable(a.data, 4, shift);
            } else if (n.equals("LocalVariableTable") || n.equals("LocalVariableTypeTable")) {
                shiftTable(a.data, 10, shift);
            }
        }
        if (!hadFrames) {
            Attr a = new Attr();
            a.name = cf.addUtf8("StackMapTable");
            a.data = frames(new byte[] {0, 0}, targets, shift);
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

    /** Tables whose entries start with a u2 pc: add shift to each. */
    private static void shiftTable(byte[] t, int entrySize, int shift) {
        int n = ((t[0] & 0xFF) << 8) | (t[1] & 0xFF);
        for (int i = 0; i < n; i++) {
            int p = 2 + i * entrySize;
            int pc = (((t[p] & 0xFF) << 8) | (t[p + 1] & 0xFF)) + shift;
            t[p] = (byte) (pc >> 8);
            t[p + 1] = (byte) pc;
        }
    }

    /**
     * The method's frames with a same_frame at each prefix target in front. Frames are stored as
     * offsets from the previous one, plus one; the first is absolute. The method's own first frame,
     * at old offset O, is now at O + shift: its delta becomes O + shift - last - 1. A frame the
     * method already had at its start (O = 0) stays, in place of ours at the old start.
     */
    static byte[] frames(byte[] t, int[] targets, int shift) {
        int n = ((t[0] & 0xFF) << 8) | (t[1] & 0xFF);
        int first = n > 0 ? frameDelta(t, 2) : -1;
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        int count = 0;
        int prev = -1;
        for (int at : targets) {
            if (at == shift && first == 0) break;
            sameFrame(o, at - prev - 1);
            prev = at;
            count++;
        }
        if (n > 0) {
            byte[] rest = java.util.Arrays.copyOfRange(t, 2, t.length);
            setFrameDelta(rest, 0, first + shift - prev - 1);
            o.write(rest, 0, rest.length);
            count += n;
        }
        byte[] body = o.toByteArray();
        byte[] out = new byte[body.length + 2];
        out[0] = (byte) (count >> 8);
        out[1] = (byte) count;
        System.arraycopy(body, 0, out, 2, body.length);
        return out;
    }

    private static void sameFrame(ByteArrayOutputStream o, int delta) {
        if (delta <= 63) {
            o.write(delta);
        } else {
            o.write(251);                  // same_frame_extended
            o.write(delta >> 8);
            o.write(delta);
        }
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
        if (tag <= 127 && d > 63) throw new IllegalArgumentException("frame delta " + d + " doesn't fit tag " + tag);
        if (tag <= 63) {
            t[p] = (byte) d;
        } else if (tag <= 127) {
            t[p] = (byte) (64 + d);
        } else {
            t[p + 1] = (byte) (d >> 8);
            t[p + 2] = (byte) d;
        }
    }
}
