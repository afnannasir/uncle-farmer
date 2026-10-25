"""Quick phrases: twelve lines a character sends with Shift+1 .. Shift+= (Shift+Backspace opens
their window). Help "8 Quick Phrase Controls" and the client (javap game.t.b(int)):

A phrase that starts with a code does something instead of (or as well as) chatting:
  /0 text  set yourself as the team navigation target (teammates select you with ' to heal you)
  /1 text  set your target as the team navigation target (focus fire)
  /2 text  cancel the team navigation target (as Left Ctrl + ')
           /0 /1 /2 send their text to the team channel; with no text they only change navigation
  /3       target what your target is attacking
  /4       target what is attacking your target
  /5       target what is attacking you
  /6       target a teammate on this map; each press the next one. The server answers with that
           teammate's position (IN op=59), which the agent keeps (`team`), so it finds teammates
           out of sight
  /7, /8   target the teammate with the lowest HP share within 20 / 30 yards
  /9       view the enemy in the arena
A code from /3 up sends nothing to chat, whatever follows it.

Each character keeps its own: <game>/users/<character id>/phrase, "slot,text" lines (UTF-8) that the
game writes the moment one is changed. The agent (v27) sets one the way the window does.
"""

import os

from . import gamekeys

from .engine.botparts.base import STOPPING
from .errors import carry_on
from .i18n import _


def what(code):
    """What a code does, in the player's language ('' for none)."""
    return {
        "/0": _("set yourself as the team's navigation target"),
        "/1": _("set your target as the team's navigation target"),
        "/2": _("cancel the team's navigation target"),
        "/3": _("target what your target is attacking"),
        "/4": _("target what is attacking your target"),
        "/5": _("target what is attacking you"),
        "/6": _("target the next teammate on this map (and learn where they are)"),
        "/7": _("target the weakest teammate within 20 yards"),
        "/8": _("target the weakest teammate within 30 yards"),
        "/9": _("view the enemy in the arena"),
    }.get(code, "")

# What goes on an EMPTY phrase slot when no slot does it yet, in this order (slots already in use
# are never touched). /6 the bot uses itself to find teammates; the rest are targeting shortcuts
# for the player, and /7 only for classes that heal others.
WANTED = ("/6", "/5", "/4", "/7")
HEALERS = ("priest", "sorceress", "witcher")
_told = set()       # characters whose phrases were listed in the log this session


def path(game_folder, char_id):
    return os.path.join(game_folder or "", "users", str(char_id), "phrase")


def known(game_folder, char_id):
    """Whether this character's phrases can be seen: the game folder is a real one and the game
    has made the character's own folder in it (users/<id>). Without the game folder, or in another
    copy of the game than the one this character plays in, there is no file to read - which is
    not the same as no phrases: taken for twelve empty slots, the player's own phrases were
    written over with codes."""
    folder = os.path.dirname(path(game_folder, char_id))
    return bool(game_folder) and os.path.isfile(os.path.join(game_folder, "mw.exe")) and os.path.isdir(folder)


def read(game_folder, char_id):
    """{slot: text} of this character's phrases ({} when it has none or the file can't be read)."""
    try:
        with open(path(game_folder, char_id), encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return {}
    out = {}
    for line in lines:
        slot, sep, text = line.partition(",")
        if sep and slot.strip().isdigit() and 1 <= int(slot) <= 12 and text.strip():
            out[int(slot)] = text.strip()
    return out


def code_of(text):
    """"/0" .. "/9" when the phrase starts with a code, else None."""
    t = (text or "").strip()
    return t[:2] if len(t) >= 2 and t[0] == "/" and t[1].isdigit() else None


def slot_of(phrases, code):
    """The slot whose phrase starts with code, or None (a code from /3 up ignores what follows it;
    /0 /1 /2 only count when they carry no chat text)."""
    for slot in sorted(phrases):
        text = phrases[slot].strip()
        if code_of(text) == code and (code >= "/3" or text == code):
            return slot
    return None


def describe(phrases):
    """One line per set phrase: 'Shift+1: /6 - target the next teammate ...' or the plain text."""
    keys = gamekeys.phrase_key_list()
    out = []
    for slot in sorted(phrases):
        text, code = phrases[slot], code_of(phrases[slot])
        does = what(code)
        rest = text[2:].strip() if code else text
        if code and rest and code <= "/2":
            does += _(", and says it to the team")
        elif not code:
            does = _("a chat line")
        out.append("%s: %s - %s" % (keys[slot - 1], code or _("text"), does))
    return out


def wanted(class_name):
    cls = (class_name or "").strip().lower()
    return [c for c in WANTED if c != "/7" or cls in HEALERS]


def arrange(bot, game_folder, char_id, class_name, can_set):
    """Log this character's phrases, then put each WANTED code no slot has yet on an empty slot
    (lowest first) - only empty ones, never the player's own. can_set: the agent can set phrases
    (v27). Returns the phrases as they are afterwards."""
    phrases = read(game_folder, char_id)
    bot.phrases = phrases
    if char_id not in _told:
        _told.add(char_id)
        for line in describe(phrases):
            bot.log(_("phrase {line}", line=line))
    if not bot.p.get("fill_empty_phrases", True):
        return phrases
    if not known(game_folder, char_id):
        bot.log(_("can't see this character's quick phrases in the game folder ({folder}) - leaving them "
                  "all alone", folder=game_folder or _("not set")))
        return phrases
    keys = gamekeys.phrase_key_list()
    missing = [c for c in wanted(class_name) if slot_of(phrases, c) is None]
    for code in missing:
        empty = [s for s in range(1, 13) if s not in phrases]
        if not empty:
            bot.log(_("no empty phrase slot for {code} ({what})", code=code, what=what(code)))
            break
        if not can_set:
            bot.log(_("phrase {code} ({what}) would go on the empty {key} - restart the game so the new agent "
                      "loads", code=code, what=what(code), key=keys[empty[0] - 1]))
            break
        try:
            r = bot.a.send("phrase %d %s" % (empty[0], code))
        except STOPPING:
            raise                       # a stop or a lost agent is not "couldn't set a phrase"
        except Exception as e:          # a phrase is never worth ending the run over
            carry_on(e, "setting a phrase")
            r = str(e)
        if r != "set":
            bot.log(_("couldn't set a phrase: {why}", why=r))
            break
        phrases[empty[0]] = code
        bot.log(_("put {code} ({what}) on the empty phrase {key}", code=code, what=what(code),
                  key=keys[empty[0] - 1]))
    return phrases
