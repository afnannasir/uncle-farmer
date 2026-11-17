package unclefarmer.agent;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.BooleanControl;
import javax.sound.sampled.SourceDataLine;

/** Sound controls with fake requests and audio lines: never opens a device or plays audio. */
final class SoundTests {
    private static int checks;

    private SoundTests() { }

    static void run() throws Exception {
        sets();
        hooks();
        forwardingAndFiltering();
        eventsAndSubscriptions();
        liveMuting();
        writeTransformation();
        lambdaCapture();
        System.out.println("sound tests: " + checks + " checks passed");
    }

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) throw new AssertionError(message);
    }

    private static void sets() {
        IdSet s = IdSet.parse("1,5|7;10-20,15-22");
        check(s.size() == 16, "ranges merge without counting duplicates");
        check(s.has(1) && s.has(5) && s.has(7) && s.has(22), "mixed separators and ranges");
        check(!s.has(0) && !s.has(6) && !s.has(23) && !s.has(100000), "set boundaries");
        check(IdSet.parse("none").isEmpty() && IdSet.parse("off").isEmpty(), "off aliases");
        check(IdSet.parse("all").size() == 99999, "all bounded IDs");
        check(IdSet.parse(s.toString()).size() == s.size(), "canonical round trip");
        for (String invalid : new String[] {"0", "100000", "20-10", "-1", "hello", "1,,2", "1-2-3"}) {
            boolean rejected = false;
            try { IdSet.parse(invalid); } catch (IllegalArgumentException expected) { rejected = true; }
            check(rejected, "reject invalid ID set: " + invalid);
        }
    }

    private static void fresh() {
        SoundControl.resetForTests();
        SoundControl.setKnownForTests(101, 102, 103);
        Window.executor = new ImmediateExecutor();
        check(SoundControl.hook(Window.class), "hook one compatible executor");
    }

    private static void hooks() {
        fresh();
        Object first = Window.executor;
        check(Proxy.isProxyClass(first.getClass()), "installed dynamic proxy");
        check(SoundControl.hook(Window.class) && Window.executor == first, "hook idempotence");
        check(!SoundControl.hook(NoExecutor.class), "no executor rejected");
        check(!SoundControl.hook(Ambiguous.class), "two executors rejected");
        check(!SoundControl.hook(FinalExecutor.class), "final executor rejected");
        check(!SoundControl.hook(ConcreteExecutor.class), "concrete executor field rejected");
    }

    private static void forwardingAndFiltering() throws Exception {
        fresh();
        final int[] ordinary = {0};
        Window.executor.execute(new Runnable() { public void run() { ordinary[0]++; } });
        check(ordinary[0] == 1, "ordinary task forwarded");
        SoundControl.mute("101");
        SoundTask muted = new SoundTask(new Request(101));
        Window.executor.execute(muted);
        check(muted.calls == 0, "matching execute dropped");
        Future<?> f = Window.executor.submit(muted);
        check(f.isDone() && f.get() == null && muted.calls == 0, "muted submit completes");
        Object result = new Object();
        check(Window.executor.submit(muted, result).get() == result, "submit preserves supplied result");
        SoundTask allowed = new SoundTask(new Request(102));
        Window.executor.execute(allowed);
        check(allowed.calls == 1, "unmatched sound forwarded");
        SoundTask unknown = new SoundTask(new Request(55555));
        Window.executor.execute(unknown);
        check(unknown.calls == 1, "unknown ID forwarded safely");
        SoundControl.mute("off");
        Window.executor.execute(muted);
        check(muted.calls == 1, "unmuted ID becomes usable again");
        final IllegalStateException original = new IllegalStateException("delegate failure");
        try {
            Window.executor.execute(new Runnable() { public void run() { throw original; } });
            throw new AssertionError("delegate exception swallowed");
        } catch (IllegalStateException caught) {
            check(caught == original, "delegate cause unwrapped, task not retried");
        }
        Window.executor.shutdown();
        check(Window.executor.isShutdown(), "lifecycle calls forwarded");
    }

    private static void eventsAndSubscriptions() {
        fresh();
        SoundControl.watch("101");
        Request request = new Request(101);
        request.position = new Position(12.5f, -3.25f);
        Window.executor.execute(new SoundTask(request));
        String event = SoundControl.since(0);
        check(event.contains("\"id\":101") && event.contains("12.5") && event.contains("-3.25"), "event ID and coordinates");
        SoundControl.watch("off");
        Object owner = new Object();
        SoundControl.listen(owner, "102");
        Window.executor.execute(new SoundTask(new Request(102)));
        check(SoundControl.since(0).contains("\"id\":102"), "connection subscription emits event");
        long before = number(SoundControl.since(0), "now");
        SoundControl.unlisten(owner);
        Window.executor.execute(new SoundTask(new Request(102)));
        check(!SoundControl.since(before).contains("\"id\":102"), "disconnected subscription removed");
        SoundControl.watch("all");
        SoundControl.mute("103");
        Window.executor.execute(new SoundTask(new Request(103)));
        check(SoundControl.since(before).contains("\"muted\":true"), "watched muted request still emits");
        long cursor = number(SoundControl.since(0), "now");
        Window.executor.execute(new SoundTask(new Request(101)));
        Window.executor.execute(new SoundTask(new Request(102)));
        check(number(SoundControl.since(cursor), "now") >= cursor + 2, "sequence distinguishes rapid events");
        for (int i = 0; i < 2048; i++) Window.executor.execute(new SoundTask(new Request(101)));
        check(SoundControl.since(1).contains("\"lost\":true"), "bounded history reports overrun");
    }

    private static long number(String json, String key) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\\"" + key + "\\\"\\s*:\\s*(\\d+)").matcher(json);
        if (!m.find()) throw new AssertionError("missing " + key + " in " + json);
        return Long.parseLong(m.group(1));
    }

    private static void liveMuting() {
        fresh();
        final FakeLine fake = new FakeLine(false);
        final Request request = new Request(101);
        request.line = fake.line();
        Window.executor.execute(new SoundTask(request));
        SoundControl.mute("101");
        check(fake.mute.getValue(), "already-playing line muted");
        SoundControl.mute("off");
        check(!fake.mute.getValue(), "previous unmuted state restored");
        fake.mute.setValue(true);
        SoundControl.mute("101");
        SoundControl.mute("off");
        check(fake.mute.getValue(), "pre-existing game mute preserved");
        final FakeLine unsupported = new FakeLine(false);
        unsupported.supported = false;
        final Request late = new Request(102);
        SoundTask task = new SoundTask(late);
        final byte[] samples = {1, 2, 3, 4};
        task.action = new Runnable() {
            public void run() {
                late.line = unsupported.line();
                SoundControl.mute("102");
                check(SoundControl.write(late.line, samples, 0, samples.length) == samples.length,
                        "muted PCM write preserves byte count");
                check(java.util.Arrays.equals(unsupported.written, new byte[4]),
                        "unsupported MUTE uses silent PCM for late-created line");
                check(samples[0] == 1 && samples[3] == 4, "PCM hook never corrupts game buffer");
                SoundControl.mute("off");
                SoundControl.write(late.line, samples, 0, samples.length);
                check(java.util.Arrays.equals(unsupported.written, samples), "PCM resumes when unmuted");
            }
        };
        Window.executor.execute(task);
    }

    private static void lambdaCapture() {
        fresh();
        SoundControl.watch("101");
        final Request request = new Request(101);
        final int[] calls = {0};
        Runnable task = () -> { if (request.id == 101) calls[0]++; };
        Window.executor.execute(task);
        check(calls[0] == 1 && number(SoundControl.status(), "observed") == 1,
                "lambda captured request fields accessible on this JDK");
        SoundControl.mute("101");
        Window.executor.execute(task);
        check(calls[0] == 1, "lambda sound request can be muted");
    }

    public static final class WriteFixture {
        public static int play(SourceDataLine line, byte[] data) {
            switch (data.length) {
                case 0: return 0;
                case 1: return line.write(data, 0, 1);
                default: return line.write(data, 0, data.length);
            }
        }
    }
    private static void writeTransformation() throws Exception {
        byte[] bytes;
        try (java.io.InputStream in = WriteFixture.class.getResourceAsStream("/" + WriteFixture.class.getName().replace('.', '/') + ".class")) {
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] b = new byte[4096]; int n; while ((n = in.read(b)) != -1) out.write(b, 0, n);
            bytes = out.toByteArray();
        }
        final byte[] patched = SoundWrites.patch(bytes);
        check(patched != null, "audio write calls transformed");
        class Loader extends ClassLoader {
            Loader() { super(SoundTests.class.getClassLoader()); }
            Class<?> define() { return defineClass(WriteFixture.class.getName(), patched, 0, patched.length); }
        }
        Class<?> fixture = new Loader().define();
        FakeLine line = new FakeLine(false);
        Object count = fixture.getMethod("play", SourceDataLine.class, byte[].class).invoke(null, line.line(), new byte[]{4,5});
        check(count.equals(Integer.valueOf(2)), "transformed switch method verifies and preserves write result");
        check(line.written[0] == 4, "untracked audio remains unchanged");
    }

    static final class Position {
        float x;
        float y;
        Position(float x, float y) { this.x = x; this.y = y; }
    }

    static final class Request {
        Position position;
        int id;
        int unrelated;
        volatile SourceDataLine line;
        Request(int id) { this.id = id; }
    }

    static class SoundTask implements Runnable {
        private final Request request;
        int calls;
        Runnable action;
        SoundTask(Request request) { this.request = request; }
        public void run() { calls++; if (action != null) action.run(); }
    }

    static final class Window { static ExecutorService executor; }
    static final class NoExecutor { }
    static final class Ambiguous {
        static ExecutorService first = new ImmediateExecutor();
        static ExecutorService second = new ImmediateExecutor();
    }
    static final class FinalExecutor { static final ExecutorService executor = new ImmediateExecutor(); }
    static final class ConcreteExecutor { static ImmediateExecutor executor = new ImmediateExecutor(); }

    static final class ImmediateExecutor extends AbstractExecutorService {
        private boolean stopped;
        public void shutdown() { stopped = true; }
        public List<Runnable> shutdownNow() { stopped = true; return Collections.emptyList(); }
        public boolean isShutdown() { return stopped; }
        public boolean isTerminated() { return stopped; }
        public boolean awaitTermination(long timeout, TimeUnit unit) { return stopped; }
        public void execute(Runnable task) {
            if (stopped) throw new RejectedExecutionException();
            task.run();
        }
    }

    static final class FakeLine implements InvocationHandler {
        final BooleanControl mute;
        boolean supported = true;
        byte[] written;
        FakeLine(boolean muted) { mute = new BooleanControl(BooleanControl.Type.MUTE, muted) { }; }
        SourceDataLine line() {
            return (SourceDataLine) Proxy.newProxyInstance(getClass().getClassLoader(), new Class<?>[] {SourceDataLine.class}, this);
        }
        public Object invoke(Object proxy, Method method, Object[] args) {
            String name = method.getName();
            if (name.equals("hashCode")) return Integer.valueOf(System.identityHashCode(proxy));
            if (name.equals("equals")) return Boolean.valueOf(proxy == args[0]);
            if (name.equals("isControlSupported")) return Boolean.valueOf(supported);
            if (name.equals("getControl")) {
                if (!supported) throw new IllegalArgumentException("unsupported");
                return mute;
            }
            if (name.equals("getFormat")) return new AudioFormat(22050, 16, 2, true, false);
            if (name.equals("write")) {
                written = java.util.Arrays.copyOfRange((byte[]) args[0], ((Integer) args[1]).intValue(),
                        ((Integer) args[1]).intValue() + ((Integer) args[2]).intValue());
                return args[2];
            }
            if (method.getReturnType() == boolean.class) return Boolean.TRUE;
            if (method.getReturnType() == int.class) return Integer.valueOf(0);
            if (method.getReturnType() == long.class) return Long.valueOf(0);
            return null;
        }
    }
}
