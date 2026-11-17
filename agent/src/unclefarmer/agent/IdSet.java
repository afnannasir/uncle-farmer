package unclefarmer.agent;

import java.util.BitSet;
import java.util.Locale;

/** Immutable bounded sound IDs. Parsing finishes before any live setting is changed. */
final class IdSet {
    private final BitSet ids;
    private IdSet(BitSet ids) { this.ids = ids; }
    static IdSet parse(String text) {
        if (text == null || text.length() > 10000) throw new IllegalArgumentException("invalid sound IDs");
        text = text.trim().toLowerCase(Locale.ROOT);
        BitSet bits = new BitSet(100000);
        if (text.equals("all")) bits.set(1, 100000);
        else if (!text.equals("off") && !text.equals("none")) {
            for (String part : text.split("[,|;]", -1)) {
                String[] range = part.trim().split("\\s*-\\s*", -1);
                if (range.length > 2 || !range[0].matches("[0-9]+")
                        || (range.length == 2 && !range[1].matches("[0-9]+")))
                    throw new IllegalArgumentException("use sound IDs or ranges, all, or off");
                int lo = Integer.parseInt(range[0]);
                int hi = range.length == 2 ? Integer.parseInt(range[1]) : lo;
                if (lo < 1 || hi >= 100000 || hi < lo) throw new IllegalArgumentException("sound IDs must be 1..99999");
                bits.set(lo, hi + 1);
            }
        }
        return new IdSet(bits);
    }
    boolean has(int id) { return id > 0 && id < 100000 && ids.get(id); }
    int size() { return ids.cardinality(); }
    boolean isEmpty() { return ids.isEmpty(); }
    @Override public String toString() {
        if (isEmpty()) return "off";
        if (size() == 99999) return "all";
        StringBuilder s = new StringBuilder();
        for (int lo = ids.nextSetBit(1); lo >= 0;) {
            int end = ids.nextClearBit(lo);
            if (s.length() > 0) s.append(',');
            s.append(lo);
            if (end > lo + 1) s.append('-').append(end - 1);
            lo = ids.nextSetBit(end);
        }
        return s.toString();
    }
}
