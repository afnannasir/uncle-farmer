package unclefarmer.agent;

import java.awt.Frame;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.instrument.Instrumentation;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.WeakHashMap;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicLong;
import java.util.zip.GZIPInputStream;
import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.BooleanControl;
import javax.sound.sampled.SourceDataLine;

/** Sound requests, not confirmed playback. No I/O or Python callbacks on the audio thread. */
public final class SoundControl {
    private static volatile IdSet watching = IdSet.parse("off");
    private static volatile IdSet muting = IdSet.parse("off");
    private static volatile BitSet known = new BitSet(100000);
    private static final Map<Object, IdSet> listeners = new IdentityHashMap<Object, IdSet>();
    private static volatile IdSet[] subscriptions = new IdSet[0];
    private static final Map<Class<?>, Field[]> captures = new ConcurrentHashMap<Class<?>, Field[]>();
    private static final Map<Class<?>, Shape> shapes = new ConcurrentHashMap<Class<?>, Shape>();
    private static final java.util.Set<Class<?>> negative = Collections.newSetFromMap(new ConcurrentHashMap<Class<?>, Boolean>());
    private static final Map<Object, Seen> seen = Collections.synchronizedMap(new WeakHashMap<Object, Seen>());
    private static final ThreadLocal<Seen> active = new ThreadLocal<Seen>();
    private static final ThreadLocal<byte[]> zeros = new ThreadLocal<byte[]>();
    private static final AtomicLong observed = new AtomicLong();
    private static final AtomicLong dropped = new AtomicLong();
    private static volatile boolean hooked;
    private static volatile String reason = "waiting for the game sound dispatcher";
    private static final int KEEP = 1024;
    private static final String[] events = new String[KEEP];
    private static long sequence;

    private SoundControl() { }

    static void install(Instrumentation inst) { inst.addTransformer(new SoundWrites(), false); }

    static void start() {
        Thread t = new Thread(new Runnable() {
            @Override public void run() {
                loadKnown(Paths.get(System.getProperty("user.dir")));
                for (int attempt = 0; attempt < 150; attempt++) {
                    try {
                        for (Frame f : Frame.getFrames()) {
                            if (f.getClass().getName().equals(Keys.WIN_CLASS)) {
                                if (hook(f.getClass())) return;
                                // A not-yet-initialised dispatcher can be retried; ambiguity cannot.
                                if (!reason.equals("sound dispatcher is not initialised")) return;
                            }
                        }
                        Thread.sleep(2000);
                    } catch (InterruptedException e) { Thread.currentThread().interrupt(); return;
                    } catch (Throwable e) { reason = "sound discovery failed: " + e.getClass().getSimpleName(); }
                }
                reason = "sound dispatcher discovery timed out";
                Log.info(reason);
            }
        }, "unclefarmer-sounds");
        t.setDaemon(true);
        t.start();
    }

    static synchronized boolean hook(Class<?> window) {
        try {
            Field candidate = null;
            for (Field f : window.getDeclaredFields()) {
                if (!Modifier.isStatic(f.getModifiers()) || !ExecutorService.class.isAssignableFrom(f.getType())) continue;
                if (candidate != null) { reason = "multiple sound dispatcher candidates"; Log.info(reason); return false; }
                candidate = f;
            }
            if (candidate == null) { reason = "no sound dispatcher candidate"; Log.info(reason); return false; }
            if (Modifier.isFinal(candidate.getModifiers()) || candidate.getType() != ExecutorService.class) {
                reason = "sound dispatcher field is final or not the ExecutorService interface"; Log.info(reason); return false;
            }
            candidate.setAccessible(true);
            Object value = candidate.get(null);
            if (value == null) { reason = "sound dispatcher is not initialised"; return false; }
            if (!(Proxy.isProxyClass(value.getClass()) && Proxy.getInvocationHandler(value) instanceof Dispatcher)) {
                Object proxy = Proxy.newProxyInstance(window.getClassLoader(), new Class<?>[] {ExecutorService.class}, new Dispatcher((ExecutorService) value));
                candidate.set(null, proxy);
            }
            hooked = true;
            reason = "sound requests connected";
            Log.info(reason);
            return true;
        } catch (Throwable e) { reason = "cannot hook sound dispatcher: " + e.getClass().getSimpleName(); Log.info(reason); return false; }
    }

    private static final class Dispatcher implements InvocationHandler {
        private final ExecutorService delegate;
        Dispatcher(ExecutorService delegate) { this.delegate = delegate; }
        @Override public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            Object[] forwarded = args;
            if ((method.getName().equals("execute") || method.getName().equals("submit"))
                    && args != null && args.length > 0 && args[0] instanceof Runnable) {
                try {
                    final Runnable runnable = (Runnable) args[0];
                    final Seen request = inspect(runnable);
                    if (request != null) {
                        boolean muted = muting.has(request.id);
                        observed.incrementAndGet();
                        emit(request, muted);
                        if (muted) {
                            // Preserve the executor's shutdown/rejection contract too.
                            if (delegate.isShutdown()) return forward(method, args);
                            dropped.incrementAndGet();
                            return method.getName().equals("execute") ? null : CompletableFuture.completedFuture(args.length == 2 ? args[1] : null);
                        }
                        forwarded = args.clone();
                        forwarded[0] = new Runnable() {
                            @Override public void run() {
                                if (muting.has(request.id)) { dropped.incrementAndGet(); return; }
                                Seen previous = active.get();
                                active.set(request);
                                try { runnable.run(); }
                                finally { if (previous == null) active.remove(); else active.set(previous); }
                            }
                        };
                    }
                } catch (Throwable ignored) { forwarded = args; } // Inspection always fails open.
            }
            return forward(method, forwarded);
        }
        private Object forward(Method method, Object[] args) throws Throwable {
            try { return method.invoke(delegate, args); }
            catch (InvocationTargetException e) { throw e.getCause(); }
        }
    }

    private static final class Shape {
        final Field line, position, x, y;
        final Field[] integers;
        volatile Field id;
        Shape(Field line, Field position, Field x, Field y, Field[] integers) {
            this.line = line; this.position = position; this.x = x; this.y = y; this.integers = integers;
        }
        int id(Object request) throws IllegalAccessException {
            Field cached = id;
            if (cached != null) { int n = cached.getInt(request); if (isKnown(n)) return n; }
            for (Field f : integers) { int n = f.getInt(request); if (isKnown(n)) { id = f; return n; } }
            id = null;
            return 0;
        }
    }
    private static final class Seen {
        final int id;
        final Float x, y;
        volatile SourceDataLine live;
        BooleanControl control;
        boolean original;
        Seen(int id, Float x, Float y) { this.id = id; this.x = x; this.y = y; }
    }

    private static Seen inspect(Runnable runnable) throws Exception {
        Class<?> type = runnable.getClass();
        Field[] fs = captures.get(type);
        if (fs == null) {
            List<Field> found = new ArrayList<Field>();
            for (Field f : type.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers()) || f.getType().isPrimitive() || jdk(f.getType())) continue;
                f.setAccessible(true); found.add(f);
            }
            fs = found.toArray(new Field[0]); captures.put(type, fs);
        }
        for (Field f : fs) {
            Object object = f.get(runnable);
            if (object == null) continue;
            Shape shape = shape(object.getClass());
            if (shape == null) continue;
            int id = shape.id(object);
            if (id == 0) continue;
            Object pos = shape.position.get(object);
            Float x = pos == null ? null : Float.valueOf(shape.x.getFloat(pos));
            Float y = pos == null ? null : Float.valueOf(shape.y.getFloat(pos));
            if (x != null && (!Float.isFinite(x.floatValue()) || !Float.isFinite(y.floatValue()))) { x = null; y = null; }
            Seen state = new Seen(id, x, y);
            state.live = (SourceDataLine) shape.line.get(object);
            seen.put(object, state);
            return state;
        }
        return null;
    }
    private static boolean jdk(Class<?> c) { return c.getName().startsWith("java.") || c.getName().startsWith("javax.") || c.getName().startsWith("sun."); }
    private static Shape shape(Class<?> type) throws Exception {
        Shape cached = shapes.get(type);
        if (cached != null) return cached;
        if (negative.contains(type)) return null;
        Field line = null, position = null;
        Field[] xy = null;
        List<Field> ints = new ArrayList<Field>();
        // Class-file declaration order is stable; Class.getDeclaredFields() order is unspecified.
        for (Field f : SoundWrites.orderedFields(type)) {
            if (Modifier.isStatic(f.getModifiers())) continue;
            if (SourceDataLine.class.isAssignableFrom(f.getType())) {
                if (line != null) { negative.add(type); return null; } line = f;
            } else if (f.getType() == int.class) ints.add(f);
            else if (!f.getType().isPrimitive() && !jdk(f.getType())) {
                Field[] fields = SoundWrites.coordinates(f.getType());
                if (fields != null) {
                    if (position != null) { negative.add(type); return null; }
                    position = f; xy = fields;
                }
            }
        }
        if (line == null || position == null || xy == null || ints.isEmpty()) { negative.add(type); return null; }
        line.setAccessible(true); position.setAccessible(true);
        for (Field f : ints) f.setAccessible(true);
        Shape result = new Shape(line, position, xy[0], xy[1], ints.toArray(new Field[0]));
        shapes.put(type, result);
        return result;
    }

    /** Called at the game's actual line writes. Same-size bytecode replacement preserves frames. */
    public static int write(SourceDataLine line, byte[] bytes, int offset, int length) {
        byte[] payload = bytes;
        int start = offset;
        try {
            Seen request = active.get();
            if (request != null) {
                request.live = line;
                boolean muted = muting.has(request.id);
                apply(request, muted);
                if (muted && AudioFormat.Encoding.PCM_SIGNED.equals(line.getFormat().getEncoding())
                        && bytes != null && offset >= 0 && length >= 0 && offset <= bytes.length - length) {
                    byte[] silence = zeros.get();
                    if (silence == null || silence.length < length) { silence = new byte[length]; zeros.set(silence); }
                    payload = silence; start = 0;
                }
            }
        } catch (Throwable ignored) { payload = bytes; start = offset; }
        return line.write(payload, start, length); // Preserve playback pacing and original exceptions.
    }

    private static synchronized void emit(Seen request, boolean muted) {
        boolean watch = watching.has(request.id);
        if (!watch) for (IdSet ids : subscriptions) if (ids.has(request.id)) { watch = true; break; }
        if (!watch) return;
        long seq = ++sequence;
        events[(int) ((seq - 1) % KEEP)] = "{\"seq\":" + seq + ",\"id\":" + request.id
                + ",\"x\":" + request.x + ",\"y\":" + request.y + ",\"t\":" + System.currentTimeMillis()
                + ",\"muted\":" + muted + "}";
    }
    static synchronized String since(long after) {
        if (after < 0) throw new IllegalArgumentException("sound cursor must be nonnegative");
        long oldest = Math.max(1, sequence - KEEP + 1);
        StringBuilder b = new StringBuilder("{\"now\":").append(sequence).append(",\"lost\":")
                .append(after < oldest - 1 || after > sequence).append(",\"events\":[");
        for (long n = Math.max(oldest, after + 1); n <= sequence; n++) {
            if (n > Math.max(oldest, after + 1)) b.append(',');
            b.append(events[(int) ((n - 1) % KEEP)]);
        }
        return b.append("]}").toString();
    }
    static String watch(String text) { watching = IdSet.parse(text); return status(); }
    static synchronized String listen(Object owner, String text) {
        IdSet ids = IdSet.parse(text);
        if (ids.isEmpty()) listeners.remove(owner); else listeners.put(owner, ids);
        subscriptions = listeners.values().toArray(new IdSet[0]);
        return status();
    }
    static void unlisten(Object owner) { listen(owner, "off"); }
    static String mute(String text) { muting = IdSet.parse(text); return status(); }

    private static synchronized boolean apply(Seen s, boolean mute) {
        SourceDataLine line = s.live;
        if (line == null || !line.isOpen()) return false;
        try {
            if (!line.isControlSupported(BooleanControl.Type.MUTE)) return false;
            BooleanControl c = (BooleanControl) line.getControl(BooleanControl.Type.MUTE);
            if (mute) {
                if (s.control != c) { s.original = c.getValue(); s.control = c; }
                c.setValue(true);
            } else if (s.control == c) { c.setValue(s.original); s.control = null; }
            return true;
        } catch (Throwable ignored) { return false; }
    }
    static String status() {
        List<Seen> copy;
        synchronized (seen) { copy = new ArrayList<Seen>(seen.values()); }
        int live = 0, unsupported = 0;
        for (Seen s : copy) {
            boolean mute = muting.has(s.id);
            if (apply(s, mute)) { if (mute) live++; }
            else if (mute && s.live != null && s.live.isOpen()) unsupported++;
        }
        return "{\"hooked\":" + hooked + ",\"reason\":\"" + Protocol.jsonEscape(reason)
                + "\",\"watch\":\"" + watching + "\",\"mute\":\"" + muting + "\",\"observed\":" + observed.get()
                + ",\"dropped\":" + dropped.get() + ",\"live_muted\":" + live + ",\"unsupported\":" + unsupported
                + ",\"playback_hooks\":" + SoundWrites.patched.get() + "}";
    }

    private static boolean isKnown(int n) { return n > 0 && n < 100000 && known.get(n); }
    static void loadKnown(Path game) {
        BitSet ids = new BitSet(100000);
        Path index = game.resolve("sound-index.csv.gz");
        try {
            if (Files.isRegularFile(index)) {
                try (BufferedReader r = new BufferedReader(new InputStreamReader(new GZIPInputStream(Files.newInputStream(index)), StandardCharsets.UTF_8))) {
                    String row;
                    while ((row = r.readLine()) != null) {
                        try { addKnown(ids, Integer.parseInt(row.split(",", 2)[0].trim())); } catch (NumberFormatException ignored) { }
                    }
                }
            } else {
                try (java.nio.file.DirectoryStream<Path> files = Files.newDirectoryStream(game.resolve("sound"))) {
                    for (Path p : files) {
                        String name = p.getFileName().toString(); int dot = name.lastIndexOf('.');
                        if (dot <= 0 || !Files.isRegularFile(p)) continue;
                        try { addKnown(ids, Integer.parseInt(name.substring(0, dot))); } catch (NumberFormatException ignored) { }
                    }
                }
            }
            known = ids;
        } catch (Exception e) { Log.info("sound index unavailable: " + e.getClass().getSimpleName()); }
    }
    private static void addKnown(BitSet ids, int n) { if (n > 0 && n < 100000) ids.set(n); }
    static void setKnownForTests(int... ids) { BitSet bits = new BitSet(); for (int n : ids) addKnown(bits, n); known = bits; }
    static synchronized void resetForTests() {
        watching = IdSet.parse("off"); muting = IdSet.parse("off"); listeners.clear(); subscriptions = new IdSet[0];
        captures.clear(); shapes.clear(); negative.clear(); seen.clear(); observed.set(0); dropped.set(0); sequence = 0;
        java.util.Arrays.fill(events, null); active.remove(); zeros.remove(); hooked = false; reason = "test reset";
    }
}
