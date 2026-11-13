package unclefarmer.agent;

import java.io.InputStream;
import java.lang.instrument.ClassFileTransformer;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.security.ProtectionDomain;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/** Replace only verified SourceDataLine.write calls, preserving instruction sizes and frames. */
final class SoundWrites implements ClassFileTransformer {
    static final AtomicInteger patched = new AtomicInteger();
    private static int u2(byte[] b, int p) { return (b[p] & 255) << 8 | b[p + 1] & 255; }
    private static int i4(byte[] b, int p) { return (b[p] & 255) << 24 | (b[p+1] & 255) << 16 | (b[p+2] & 255) << 8 | b[p+3] & 255; }
    private static byte[] pair(int a, int b) { return new byte[] {(byte)(a >> 8), (byte)a, (byte)(b >> 8), (byte)b}; }
    private static String[] ref(Bytecode.ClassFile c, int index) {
        byte[] r = c.pool.get(index - 1);
        byte[] owner = c.pool.get(u2(r, 1) - 1);
        byte[] nt = c.pool.get(u2(r, 3) - 1);
        return new String[] {c.utf8(u2(owner, 1)), c.utf8(u2(nt, 1)), c.utf8(u2(nt, 3))};
    }
    @Override public byte[] transform(ClassLoader loader, String name, Class<?> redef, ProtectionDomain pd, byte[] bytes) {
        if (loader == null || name == null || !name.startsWith("game/") || redef != null
                || !Bytecode.mentions(bytes, "javax/sound/sampled/SourceDataLine")) return null;
        try {
            if (loader.loadClass(SoundControl.class.getName()) != SoundControl.class) return null;
            byte[] out = patch(bytes);
            if (out != null) patched.incrementAndGet();
            return out;
        } catch (Throwable ignored) { return null; }
    }
    static byte[] patch(byte[] bytes) throws Exception {
        Bytecode.ClassFile c = new Bytecode.ClassFile(bytes);
        List<Integer> writes = new ArrayList<Integer>();
        for (int n = 1; n <= c.pool.size(); n++) {
            byte[] e = c.pool.get(n-1);
            if (e != null && e[0] == 11) {
                String[] r = ref(c, n);
                if (r[0].equals("javax/sound/sampled/SourceDataLine") && r[1].equals("write") && r[2].equals("([BII)I")) writes.add(n);
            }
        }
        if (writes.isEmpty()) return null;
        int owner = c.add(7, new byte[] {(byte)(c.addUtf8("unclefarmer/agent/SoundControl") >> 8), (byte)c.addUtf8("unclefarmer/agent/SoundControl")});
        int nt = c.add(12, pair(c.addUtf8("write"), c.addUtf8("(Ljavax/sound/sampled/SourceDataLine;[BII)I")));
        int method = c.add(10, pair(owner, nt));
        boolean changed = false;
        for (Bytecode.Member m : c.methods) {
            Bytecode.Attr a = c.code(m);
            if (a == null) continue;
            int end = 8 + i4(a.data, 4);
            for (int p = 8; p < end;) {
                int op = a.data[p] & 255;
                int step = length(a.data, p, 8);
                if (op == 185 && writes.contains(u2(a.data,p+1))) {
                    a.data[p] = (byte)184; a.data[p+1] = (byte)(method >> 8); a.data[p+2] = (byte)method;
                    a.data[p+3] = 0; a.data[p+4] = 0; changed = true;
                }
                p += step;
            }
        }
        return changed ? c.write() : null;
    }
    private static int length(byte[] b, int p, int base) {
        int op = b[p] & 255;
        if (op == 170 || op == 171) {
            int q = p + 1; while ((q-base)%4 != 0) q++;
            return q-p + (op == 170 ? 12 + 4*(i4(b,q+8)-i4(b,q+4)+1) : 8 + 8*i4(b,q+4));
        }
        if (op == 196) return (b[p+1]&255)==132 ? 6 : 4;
        if (op == 185 || op == 186 || op == 200 || op == 201) return 5;
        if (op == 197) return 4;
        if (op == 17 || op == 19 || op == 20 || op == 132 || (op >= 153 && op <= 168)
                || (op >=178 && op <=184) || op ==187 || op ==189 || op ==192 || op ==193 || op ==198 || op ==199) return 3;
        if (op ==16 || op ==18 || (op >=21 && op <=25) || (op >=54 && op <=58) || op ==169 || op ==188) return 2;
        return 1;
    }
    private static Bytecode.ClassFile read(Class<?> type) throws Exception {
        try (InputStream in = type.getResourceAsStream("/" + type.getName().replace('.', '/') + ".class")) {
            if (in == null) throw new IllegalArgumentException("class bytes unavailable");
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] b = new byte[4096]; int n; while ((n = in.read(b)) != -1) out.write(b,0,n);
            return new Bytecode.ClassFile(out.toByteArray());
        }
    }
    static Field[] orderedFields(Class<?> type) throws Exception {
        List<Field> fs = new ArrayList<Field>();
        Bytecode.ClassFile c = read(type);
        for (Bytecode.Member m : c.fields) fs.add(type.getDeclaredField(c.utf8(m.name)));
        return fs.toArray(new Field[0]);
    }
    static Field[] coordinates(Class<?> type) throws Exception {
        Field[] fields = orderedFields(type);
        List<Field> fs = new ArrayList<Field>();
        for (Field f : fields) if (!Modifier.isStatic(f.getModifiers())) fs.add(f);
        if (fs.size()!=2 || fs.get(0).getType()!=float.class || fs.get(1).getType()!=float.class) return null;
        Bytecode.ClassFile c = read(type);
        for (Bytecode.Member m : c.methods) {
            if (!c.utf8(m.name).equals("<init>") || !c.utf8(m.desc).equals("(FF)V")) continue;
            Bytecode.Attr a = c.code(m); String[] names = new String[2];
            for (int p=8; p<8+i4(a.data,4); p+=length(a.data,p,8)) {
                if ((a.data[p]&255)==181 && p>=10 && (a.data[p-2]&255)==42) {
                    int load = a.data[p-1]&255;
                    if (load==35 || load==36) names[load-35]=ref(c,u2(a.data,p+1))[1];
                }
            }
            if (names[0]!=null && names[1]!=null && !names[0].equals(names[1])) {
                Field x=type.getDeclaredField(names[0]), y=type.getDeclaredField(names[1]);
                x.setAccessible(true); y.setAccessible(true); return new Field[]{x,y};
            }
        }
        return null;
    }
}
