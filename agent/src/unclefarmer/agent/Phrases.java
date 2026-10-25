package unclefarmer.agent;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;

/**
 * Setting a quick phrase (Shift+1 .. Shift+= send them; Shift+Backspace opens their window).
 *
 * The window's own save goes through the client's local-command dispatcher, the static
 * `N.a(int, int cmd, int[] args, String text)` in the class that also sends chat (game.N in this
 * build): command 12 with args {slot} puts the text into the chat model's phrase map and writes
 * users/<character id>/phrase. Nothing goes to the server. The same call is made here, so the
 * game and its file agree as if the player had typed it.
 */
final class Phrases {

    static final int SET_PHRASE = 12;
    private static volatile Method dispatcher;

    private Phrases() { }

    /** `phrase <slot> <text>`. */
    static String set(int slot, String text) {
        if (slot < 1 || slot > 12) return "error: slot 1-12";
        if (text == null || text.trim().isEmpty()) return "error: phrase <slot> <text>";
        Method m = method();
        if (m == null) return "error: the game's command dispatcher wasn't found";
        try {
            m.invoke(null, Integer.valueOf(0), Integer.valueOf(SET_PHRASE), new int[] {slot}, text.trim());
            return "set";
        } catch (Throwable t) {
            Log.error("phrase set", t);
            return "error: " + t;
        }
    }

    private static Method method() {
        Method m = dispatcher;
        if (m != null) return m;
        Class<?> c = PrivateChat.senderClass();
        if (c == null) return null;
        Method found = null;
        int count = 0;
        for (Method x : c.getDeclaredMethods()) {
            Class<?>[] p = x.getParameterTypes();
            if (Modifier.isStatic(x.getModifiers()) && x.getReturnType() == void.class && p.length == 4
                    && p[0] == int.class && p[1] == int.class && p[2] == int[].class && p[3] == String.class) {
                found = x;
                count++;
            }
        }
        if (count != 1) return null;
        found.setAccessible(true);
        dispatcher = found;
        Log.info("phrases are set through " + c.getName() + "." + found.getName());
        return found;
    }
}
