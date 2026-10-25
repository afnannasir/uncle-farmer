package unclefarmer.agent;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import javax.sound.sampled.AudioFormat;
import javax.sound.sampled.AudioSystem;
import javax.sound.sampled.DataLine;
import javax.sound.sampled.Mixer;
import javax.sound.sampled.TargetDataLine;

/**
 * Voice messages, done with the game's own recorder, codec and senders (javap 2026-10-23), so the
 * Chat window can play and send them in the chats that carry them: Team, Gang and private messages.
 *
 * A voice message is a 32-character name and the sound. The game keeps each one it receives or
 * sends as voice/&lt;name&gt; in its own folder. The sound is Opus at 16 kHz, mono, 20 ms frames: 01
 * and the frame length (40 bytes at the recorder's 16 kbit/s, game.ab), then the frames.
 *
 *   IN  op=55  the channel byte (13) plus 64: after "name\n" the 32-character name, then the sound
 *   IN  op=56  the sender's id negated (17): after "name\n" the name, then the sound
 *   OUT        channels: 19 &lt;channel+64&gt; &lt;name&gt; &lt;sound&gt;, N.a(channel, 0, name, sound) (Team and Gang
 *              only, as the game allows; the same 2 s gap as text, so it goes through ChatQueue)
 *              private: 19 4b &lt;to:4&gt; &lt;name&gt; &lt;sound&gt;, N.a(to, name, sound) (no gap)
 *
 * Playing: the frames are decoded with game.OpusUtils (the game's own opus.dll) into a WAV file
 * the bot plays. Recording: from the microphone the game uses, cleaned up (clean) and encoded with
 * the same codec at twice the game's bitrate; the file's first byte tells the player the rate. A
 * long recording goes as several messages of PART_MS at most (cuts), like a long text in parts.
 */
final class Voice {

    static final int FLAG = 64;              // added to the channel byte of a voice line
    static final int PRIVATE_OUT = PrivateChat.PRIVATE + FLAG;
    static final int GANG = 3;
    static final int TEAM = 4;
    static final int NAME = 32;
    static final int MIN_BYTES = 1002;       // the game's own: a shorter recording isn't sent (game.bU)
    static final int RATE = 16000;
    static final int FRAME = 320;            // samples in 20 ms
    static final String FOLDER = "voice";
    static final String CODEC_CLASS = "game.OpusUtils";
    static final String RECORDER_CLASS = "game.ab";        // the game's recorder: its microphone
    static final int RATE_CODE = 1;          // the file's first byte: mono (tens 0), 16 kHz (units 1)
    static final int BITRATE = 32000;        // the game's own recorder: 16000 (40-byte frames; ours 80)
    static final int COMPLEXITY = 8;         // createEncoder's third number, as the game passes it
    static final int PART_MS = 20000;        // the game's recorder stops at 20 s: one message at most this long
    static final int MOST_MS = 300000;       // a recording stops by itself after 5 minutes
    static final int MIN_MS = 500;           // less voice than this isn't sent
    static final double HIGH_PASS = 90.0;    // Hz: hum and desk bumps below it are taken out
    static final double SPEECH_LEVEL = 250;  // RMS a 20 ms frame of speech has at least (of 32767)
    static final int MARGIN_FRAMES = 8;      // 160 ms kept before the first word and after the last
    static final double TARGET_LEVEL = 3300; // RMS the voice is brought to (about -20 dBFS)
    static final double MAX_GAIN = 8.0;
    static final double KNEE = 0.7;

    private static volatile Thread recording;
    private static volatile ByteArrayOutputStream captured;
    private static volatile boolean stopping;
    private static volatile long recordStarted;

    private Voice() { }

    // ---------------------------------------------------------------- reading the packets

    /** The voice message's name right after the newline at nl, or null when there isn't one. */
    static String nameAfter(byte[] m, int nl) {
        if (m == null || nl < 0 || nl + 1 + NAME > m.length) return null;
        for (int i = nl + 1; i <= nl + NAME; i++) {
            int c = m[i] & 0xFF;
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'))) return null;
        }
        return new String(m, nl + 1, NAME, Protocol.UTF8);
    }

    /** True for a name the game could have made (no path tricks in what is opened as a file). */
    static boolean validName(String name) {
        if (name == null || name.length() != NAME) return false;
        byte[] b = name.getBytes(Protocol.UTF8);
        return b.length == NAME && nameAfter(concat(new byte[] {0x0A}, b), 0) != null;
    }

    /** The Opus frames of a voice file: 01 &lt;frame length&gt;, then frames of that length (the game's
     *  player reads them so, game.Vv3); a part frame at the end is left out. */
    static List<byte[]> frames(byte[] file) {
        List<byte[]> out = new ArrayList<byte[]>();
        if (file == null || file.length < 2 || file[0] != 1) return out;
        int len = file[1] & 0xFF;
        if (len == 0) return out;
        for (int i = 2; i + len <= file.length; i += len) {
            byte[] f = new byte[len];
            System.arraycopy(file, i, f, 0, len);
            out.add(f);
        }
        return out;
    }

    /** How long a voice message plays, in milliseconds (20 ms a frame). */
    static long millis(byte[] file) {
        return 20L * frames(file).size();
    }

    // ---------------------------------------------------------------- playing

    /** `voice play <name>`: decoded into a WAV file; answers "wav <path> <ms>". */
    static String play(String name) {
        if (!validName(name)) return "error: not a voice message name";
        File file = new File(FOLDER, name);
        if (!file.isFile()) return "error: the game has no voice message " + name;
        try {
            byte[] data = Files.readAllBytes(file.toPath());
            List<byte[]> frames = frames(data);
            if (frames.isEmpty()) return "error: voice message " + name + " holds no sound";
            Class<?> codec = gameClass(CODEC_CLASS);
            Method create = codec.getMethod("createDecoder", int.class, int.class);
            Method decode = codec.getMethod("decode", long.class, byte[].class, short[].class);
            Method destroy = codec.getMethod("destroyDecoder", long.class);
            long dec = (Long) create.invoke(null, Integer.valueOf(RATE), Integer.valueOf(1));
            ByteArrayOutputStream pcm = new ByteArrayOutputStream(frames.size() * FRAME * 2);
            try {
                short[] out = new short[FRAME];
                for (byte[] f : frames) {
                    int n = (Integer) decode.invoke(null, Long.valueOf(dec), f, out);
                    for (int i = 0; i < n && i < out.length; i++) {
                        pcm.write(out[i] & 0xFF);
                        pcm.write((out[i] >> 8) & 0xFF);
                    }
                }
            } finally {
                destroy.invoke(null, Long.valueOf(dec));
            }
            File wav = new File(new File(System.getProperty("java.io.tmpdir"), "unclefarmer-voice"), name + ".wav");
            if (!wav.getParentFile().isDirectory() && !wav.getParentFile().mkdirs()) {
                return "error: can't write " + wav.getParent();
            }
            OutputStream o = new FileOutputStream(wav);
            try {
                writeWav(o, pcm.toByteArray());
            } finally {
                o.close();
            }
            return "wav " + wav.getAbsolutePath() + " " + 20L * frames.size();
        } catch (Memory.Unavailable u) {
            return "error: " + u.getMessage();
        } catch (Throwable t) {
            Log.error("voice play", t);
            return "error: couldn't decode " + name + ": " + t;
        }
    }

    /** 16-bit mono PCM at RATE as a WAV file. */
    static void writeWav(OutputStream o, byte[] pcm) throws IOException {
        o.write(new byte[] {'R', 'I', 'F', 'F'});
        le32(o, 36 + pcm.length);
        o.write(new byte[] {'W', 'A', 'V', 'E', 'f', 'm', 't', ' '});
        le32(o, 16);
        le16(o, 1);                 // PCM
        le16(o, 1);                 // mono
        le32(o, RATE);
        le32(o, RATE * 2);
        le16(o, 2);
        le16(o, 16);
        o.write(new byte[] {'d', 'a', 't', 'a'});
        le32(o, pcm.length);
        o.write(pcm);
    }

    private static void le32(OutputStream o, int v) throws IOException {
        o.write(v & 0xFF);
        o.write((v >> 8) & 0xFF);
        o.write((v >> 16) & 0xFF);
        o.write((v >> 24) & 0xFF);
    }

    private static void le16(OutputStream o, int v) throws IOException {
        o.write(v & 0xFF);
        o.write((v >> 8) & 0xFF);
    }

    // ---------------------------------------------------------------- recording

    /**
     * `voice record`: from the microphone the game uses, recorded here rather than by game.ab: at
     * the same 16 kHz (the game's player can't decode the 20 ms frames of a higher rate - its buffer
     * holds 320 samples, and the game's encoder only makes 20 ms frames), but cleaned up and encoded
     * at twice the game's 16 kbit/s. Runs until `voice send ...` or `voice cancel`, MOST_MS at most.
     */
    static synchronized String record() {
        if (recording != null && recording.isAlive()) return "error: already recording";
        AudioFormat format = new AudioFormat(RATE, 16, 1, true, false);
        final TargetDataLine line;
        try {
            DataLine.Info info = new DataLine.Info(TargetDataLine.class, format);
            Mixer mixer = gameMixer();
            line = (TargetDataLine) (mixer != null ? mixer.getLine(info) : AudioSystem.getLine(info));
            line.open(format);
            line.start();
        } catch (Throwable t) {
            Log.error("voice record", t);
            return "error: no microphone to record from: " + t.getMessage();
        }
        final ByteArrayOutputStream pcm = new ByteArrayOutputStream(RATE * 2 * 30);
        stopping = false;
        captured = pcm;
        recordStarted = System.currentTimeMillis();
        Thread t = new Thread(new Runnable() {
            public void run() {
                byte[] buf = new byte[FRAME * 2];
                long most = (long) RATE * 2 * MOST_MS / 1000;
                try {
                    while (!stopping && pcm.size() < most) {
                        int n = line.read(buf, 0, buf.length);
                        if (n > 0) {
                            synchronized (pcm) {
                                pcm.write(buf, 0, n);
                            }
                        }
                    }
                } catch (Throwable e) {
                    Log.error("voice record", e);
                } finally {
                    line.stop();
                    line.close();
                }
            }
        }, "unclefarmer-voice-record");
        t.setDaemon(true);
        recording = t;
        t.start();
        return "recording";
    }

    /** The microphone the game records from (game.ab's Mixer, from its sound settings), or null. */
    private static Mixer gameMixer() {
        try {
            for (java.lang.reflect.Field f : gameClass(RECORDER_CLASS).getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers()) && f.getType() == Mixer.class) {
                    f.setAccessible(true);
                    return (Mixer) f.get(null);
                }
            }
        } catch (Throwable ignored) {
            // the system's own default microphone then
        }
        return null;
    }

    /** Stops recording and gives what was recorded, 16-bit little-endian PCM (null: none). */
    private static synchronized byte[] stop() {
        Thread t = recording;
        if (t == null) return null;
        stopping = true;
        try {
            t.join(2000);
        } catch (InterruptedException ignored) {
            // what is there so far is what was said
        }
        recording = null;
        ByteArrayOutputStream pcm = captured;
        captured = null;
        if (pcm == null) return null;
        synchronized (pcm) {
            return pcm.toByteArray();
        }
    }

    /** `voice cancel`: stop recording and send nothing. */
    static String cancel() {
        return stop() == null ? "nothing recorded" : "cancelled";
    }

    // ---------------------------------------------------------------- cleaning up and cutting

    static short[] samples(byte[] le) {
        short[] out = new short[le.length / 2];
        for (int i = 0; i < out.length; i++) out[i] = (short) ((le[2 * i] & 0xFF) | (le[2 * i + 1] << 8));
        return out;
    }

    /**
     * What makes a microphone recording sound better than the game's own: the low hum and the
     * bumps of a desk under HIGH_PASS Hz taken out, the silence before and after cut (the key
     * press), and the voice brought to an even level (a quiet microphone was hard to hear, a loud
     * one distorted), with the peaks rounded off rather than clipped. Empty when nothing was said.
     */
    static short[] clean(short[] in) {
        int n = in.length;
        if (n < FRAME) return new short[0];
        double[] x = new double[n];
        double rc = 1.0 / (2 * Math.PI * HIGH_PASS);
        double a = rc / (rc + 1.0 / RATE);
        double prevIn = in[0];
        double prevOut = 0;
        for (int i = 0; i < n; i++) {
            prevOut = a * (prevOut + in[i] - prevIn);
            prevIn = in[i];
            x[i] = prevOut;
        }
        int frames = n / FRAME;
        double[] level = new double[frames];
        double[] sorted = new double[frames];
        for (int f = 0; f < frames; f++) {
            double sum = 0;
            for (int i = f * FRAME; i < (f + 1) * FRAME; i++) sum += x[i] * x[i];
            level[f] = Math.sqrt(sum / FRAME);
            sorted[f] = level[f];
        }
        java.util.Arrays.sort(sorted);
        double floor = sorted[frames / 10];                        // the room's own noise
        double gate = Math.max(SPEECH_LEVEL, floor * 3);
        int first = -1;
        int last = -1;
        for (int f = 0; f < frames; f++) {
            if (level[f] >= gate) {
                if (first < 0) first = f;
                last = f;
            }
        }
        if (first < 0) return new short[0];
        first = Math.max(0, first - MARGIN_FRAMES);
        last = Math.min(frames - 1, last + MARGIN_FRAMES);
        double sum = 0;
        int voiced = 0;
        double peak = 1;
        for (int f = first; f <= last; f++) {
            if (level[f] >= gate) {
                sum += level[f] * level[f];
                voiced++;
            }
            for (int i = f * FRAME; i < (f + 1) * FRAME; i++) peak = Math.max(peak, Math.abs(x[i]));
        }
        double rms = Math.sqrt(sum / Math.max(1, voiced));
        double gain = Math.min(MAX_GAIN, TARGET_LEVEL / Math.max(1, rms));
        short[] out = new short[(last - first + 1) * FRAME];
        for (int i = 0; i < out.length; i++) {
            double v = x[first * FRAME + i] * gain / 32767.0;
            // rounded off above 0.7 of full scale, never past it
            double s2 = Math.abs(v) <= KNEE ? v : Math.signum(v) * (KNEE + (1 - KNEE) * Math.tanh((Math.abs(v) - KNEE) / (1 - KNEE)));
            out[i] = (short) Math.round(s2 * 32767.0);
        }
        return out;
    }

    /** Where a recording is cut into messages of at most PART_MS each, all about the same length
     *  (45 s: three of 15 s, not 20 + 20 + 5); the cuts fall on frame boundaries. */
    static int[] cuts(int samples) {
        int most = RATE * PART_MS / 1000;
        int parts = Math.max(1, (samples + most - 1) / most);
        int frames = (samples + FRAME - 1) / FRAME;
        int[] out = new int[parts + 1];
        for (int i = 0; i <= parts; i++) out[i] = Math.min(samples, (int) ((long) frames * i / parts) * FRAME);
        out[parts] = samples;
        return out;
    }

    /** One frame of PCM in, one Opus frame out: the game's own codec (OpusUtils), or a stand-in. */
    interface Encoder {
        byte[] frame(short[] pcm) throws Exception;
    }

    /** A voice message in the game's file shape (01 and the frame length, then the frames), or
     *  null when the encoder's frames weren't all one size (the game's player needs that). */
    static byte[] message(short[] pcm, int from, int to, Encoder enc) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int len = -1;
        for (int at = from; at < to; at += FRAME) {
            short[] f = new short[FRAME];
            System.arraycopy(pcm, at, f, 0, Math.min(FRAME, to - at));
            byte[] b = enc.frame(f);
            if (b == null || b.length == 0 || b.length > 255) return null;
            if (len < 0) {
                len = b.length;
                out.write(RATE_CODE);
                out.write(len);
            } else if (b.length != len) {
                return null;
            }
            out.write(b, 0, b.length);
        }
        return out.toByteArray();
    }

    /** The game's codec at our rate: a new encoder for each message (each is decoded on its own). */
    private static final class GameEncoder implements Encoder {
        private final Method encode;
        private final Method destroy;
        private final long handle;
        private final byte[] out = new byte[640];

        GameEncoder() throws Exception {
            Class<?> codec = gameClass(CODEC_CLASS);
            Method create = codec.getMethod("createEncoder", int.class, int.class, int.class, int.class);
            encode = codec.getMethod("encode", long.class, short[].class, int.class, byte[].class);
            destroy = codec.getMethod("destroyEncoder", long.class);
            handle = (Long) create.invoke(null, Integer.valueOf(RATE), Integer.valueOf(1),
                    Integer.valueOf(COMPLEXITY), Integer.valueOf(BITRATE));
        }

        public byte[] frame(short[] pcm) throws Exception {
            int n = (Integer) encode.invoke(null, Long.valueOf(handle), pcm, Integer.valueOf(0), out);
            return n > 0 ? java.util.Arrays.copyOf(out, n) : null;
        }

        void close() {
            try {
                destroy.invoke(null, Long.valueOf(handle));
            } catch (Throwable ignored) {
                // gone with the game anyway
            }
        }
    }

    /**
     * `voice send <3|4>` (Gang, Team) or `voice send pm <id>`: stops the recording, cleans it up,
     * cuts it into messages of PART_MS at most and sends them in turn - each kept as voice/&lt;name&gt;
     * first, so it can be played back here too. Answers "sent &lt;messages&gt; &lt;ms&gt;".
     */
    static String send(String[] args) {
        int channel;
        int to = 0;
        try {
            if (args.length >= 3 && args[1].equals("pm")) {
                channel = PrivateChat.PRIVATE;
                to = Integer.parseInt(args[2]);
                if (to <= 0) return "error: voice send pm <id>";
            } else if (args.length >= 2) {
                channel = Integer.parseInt(args[1]);
                if (channel != TEAM && channel != GANG) return "error: the game sends voice only to Team, Gang and private chats";
            } else {
                return "error: voice send <3|4> | voice send pm <id>";
            }
        } catch (NumberFormatException e) {
            return "error: voice send <3|4> | voice send pm <id>";
        }
        byte[] raw = stop();
        if (raw == null || raw.length == 0) return "error: nothing was recorded (no microphone?)";
        short[] pcm = clean(samples(raw));
        if (pcm.length < RATE * MIN_MS / 1000) return "error: too short";
        int[] cut = cuts(pcm.length);
        List<byte[]> messages = new ArrayList<byte[]>();
        for (int i = 0; i + 1 < cut.length; i++) {
            GameEncoder enc;
            try {
                enc = new GameEncoder();
            } catch (Throwable t) {
                Log.error("voice encoder", t);
                return "error: the game's voice encoder wasn't found";
            }
            try {
                byte[] m = message(pcm, cut[i], cut[i + 1], enc);
                if (m == null || m.length <= MIN_BYTES) return "error: the game's encoder didn't make a voice message";
                messages.add(m);
            } catch (Throwable t) {
                Log.error("voice encode", t);
                return "error: couldn't encode the recording: " + t;
            } finally {
                enc.close();
            }
        }
        File dir = new File(FOLDER);
        if (!dir.isDirectory() && !dir.mkdir()) return "error: can't write the game's voice folder";
        for (byte[] m : messages) {
            String name = UUID.randomUUID().toString().replace("-", "");
            try {
                OutputStream o = new FileOutputStream(new File(dir, name));
                try {
                    o.write(m);
                } finally {
                    o.close();
                }
            } catch (IOException e) {
                return "error: can't keep the recording: " + e.getMessage();
            }
            // in turn, through the same queue as text: they keep their order and the game's gap
            ChatQueue.addVoice(channel, to, name, m);
        }
        return "sent " + messages.size() + " " + 1000L * pcm.length / RATE;
    }

    /** N.a(int to, String name, byte[] sound): writes OUT op=25 4b. */
    static String sendPrivate(int to, String name, byte[] sound) {
        Method m = senderOf(int.class, String.class, byte[].class);
        if (m == null) return "error: the game's voice sender wasn't found";
        try {
            m.invoke(null, Integer.valueOf(to), name, sound);
            return null;
        } catch (Throwable t) {
            Log.error("voice pm", t);
            return "error: the game didn't send it: " + t;
        }
    }

    /** N.a(int channel, int target, String name, byte[] sound), for ChatQueue. */
    static void sendChannel(int channel, String name, byte[] sound) {
        Method m = senderOf(int.class, int.class, String.class, byte[].class);
        if (m == null) return;
        try {
            m.invoke(null, Integer.valueOf(channel), Integer.valueOf(0), name, sound);
        } catch (Throwable t) {
            Log.error("voice send", t);
        }
    }

    /** `voice status`: "recording <ms>" or "idle". */
    static String status() {
        Thread t = recording;
        return t != null && t.isAlive() ? "recording " + (System.currentTimeMillis() - recordStarted) : "idle";
    }

    // ---------------------------------------------------------------- the game's classes

    private static Class<?> gameClass(String name) throws Memory.Unavailable {
        Object world = Memory.world();
        try {
            return Class.forName(name, true, world.getClass().getClassLoader());
        } catch (ClassNotFoundException e) {
            throw new Memory.Unavailable("the game has no " + name + " (game patched?)");
        }
    }

    /** A static void method of the game's chat sender class with these parameters. */
    private static Method senderOf(Class<?>... params) {
        Class<?> c = PrivateChat.senderClass();
        if (c == null) return null;
        for (Method m : c.getDeclaredMethods()) {
            if (Modifier.isStatic(m.getModifiers()) && m.getReturnType() == void.class
                    && java.util.Arrays.equals(m.getParameterTypes(), params)) {
                m.setAccessible(true);
                return m;
            }
        }
        return null;
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] r = new byte[a.length + b.length];
        System.arraycopy(a, 0, r, 0, a.length);
        System.arraycopy(b, 0, r, a.length, b.length);
        return r;
    }
}
