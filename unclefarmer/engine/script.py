"""Uncle Farmer scripts: a small line-based language for anything the plan editor can't express.

One command per line; `#` starts a comment. Blocks end with `end`.

    travel <map> [at x,y]            go to a map (and a cell on it) from anywhere: on foot, by cable
                                     car, airship or teleport circle; a map id or name
    go <map> [at x,y] [to <npc>]     the walking assistant: like travel, but on the fastest Swift
                                     fusion beast the whole way and never stopping to fight; with
                                     "to", walk up to that NPC (standing near x,y) on arrival
    go dungeon <name> [talk]         walk to a dungeon's entrance NPC the same way (talk: open its
                                     menu there)
    talk <npc name> [at x,y]         walk up to an NPC (the one nearest x,y) and open its menu
    choose <text>                    pick the menu entry containing text (/regex/ for a pattern),
                                     using the walking keys and Enter; stops the run if it's missing
    visit <role or NPC name>         go to the nearest NPC of a kind and open its menu: storehouse,
                                     potions, grocer, bank, blacksmith, forger, gem_craftsman,
                                     artificer, shard_market, fish_buyer, herb_buyer, ore_buyer...
    restock                          top up the profile's restock list now (storehouse, then shops)
    keyboard_profile <name>          apply a saved Keyboard Manager profile and prepare its selected skills
    gem_maker [check]                run the Gem Maker as set in Tools, Gem Maker (check: only look)
    gem_breaker [check]              run the Gem Breaker as set in Tools, Gem Breaker (check: only look)
    potion_maker [check]             make and fuse potions as set in Tools, Potion Maker (check: only look)
    triggers off                     this script does its own Before/After farming steps: the
                                     Triggers lists don't run around it (Run, Autopilot writes it)
    enhance [check]                  run the Equipment Enhancer as set in Tools (check: only look)
    chores [check] [only=a,b]        the day's chores as set in Tools, Daily chores, each once a game
                                     day: bank, pet_rebirth, contribution_chests, donation, construction, feed, secret_room
    bounties [check]                 ask the officers ticked in Tools, Hunting bounties for what they pay
    construction_scan [publish=<kind>]  read how far the gang's constructions are; or send the last report
                                     (announcement, broadcast or system: it takes a gang elder);
                                     auto=<kind> scans and publishes if there is something new;
                                     add lines=yes or lines=no for an announcement: a line to each highland
                                     or one line (without it, Tools, Construction scanner decides)
    gather [check]                   fish, mine or collect herbs as set in Tools, Gathering: the
                                     gang's area once a day, then outside; sells the collection
    lose_xp [check]                  die to guards until the next level needs the experience set
                                     in Tools, XP loser (check: only read the numbers and look)
    dungeon [check] [<cave>]         the cave named (Spider cave, Scorpion Cave), else the one chosen in
                                     Tools, Dungeons - one a game day: supplies first, then all 15 minutes inside
    sell_collection [fish|ore|herb|all]   sell to the buyer for the character's gathering skill
                                     (or the goods in the bag), unless a kind or all is specified
    sell_shards                      sell the shards the bag manager says to sell, at the nearest
                                     Shard Market (this city's when it has one)
    clean_bag                        throw away, where you stand, what is on the bag manager's
                                     throw-away list (keeping what each rule keeps) - nothing else
    update_shard_prices              read the nearest Shard Market's prices into data/shards.json
                                     (only prices the menu shows and sends alike; the rest stay)
    store_items                      put into the storehouse what is on the bag manager's storage
                                     list (keeping what each rule keeps) - nothing else
    survey [cities|all|everything|here|<map>]   learn NPCs and their menus from the game itself (only looks)
    follow <player>                  stay with that player and help with their fights, until stopped
    come <player> [<map>]            walk up to that player (on their map, if it is given)
    buy <count> <item>               buy from the shop that sells it (walks there)
    sell <item> [count]              sell to the NPC that buys it (all, if no count)
    withdraw <count> <item>          take out of the storehouse
    deposit <item> [count]           put into the storehouse (all, if no count)
    login [character]                select this saved character before the script runs
    walk <x> <y>                     walk to a cell on the current map
    farm [map=<map>] [minutes=N] [kills=N] [patrol=random|full|quick|short|points] [area=x1,y1,x2,y2]
         [points=x,y;x,y;...] [nogo=x,y,radius;...]
         [only=<names>] [skip=<names>] [elites=fight|avoid] [until=<condition>]
                                     farm until a limit is reached (no limit = until stopped);
                                     until= takes the rest of the line, so it goes last;
                                     map=best: the best field map for the character's level now
                                     (monsters 9 below to 5 above it, the highest first)
    key <key> [milliseconds]         press a key: a-z 0-9 . , ; / space enter esc tab f1-f12 up down left right
    hold <key> <seconds>             hold a key down
    wait <seconds>
    sound_watch <ids>                set the game's WATCH filter: IDs/ranges, all, or off
    sound_mute <ids>                 set the game's MUTE filter (persists until changed)
    sound_wait <ids> [timeout=N]     wait for a new matching sound request; seconds, default 30
                                     sets sound_received, sound_id, sound_x, sound_y, sound_muted
                                     temporary listening ends after the wait; WATCH stays unchanged
    say <text>                       write to the log; {expression} is filled in: say HP is {hp_pct}%
    set <name> = <expression>
    heal                             press your HP key now
    hearthstone                      go home: press your hearthstone key, check the map changed, retry
    stop_when <condition> [: message]   from here on, end the run as soon as the condition is true
    stop [message]                   end the run
    repeat [N] ... end               N times, or forever
    while <condition> ... end
    if <condition> ... elif <condition> ... else ... end
    break                            leave the innermost repeat/while
    sub <name> ... end               define a block; `call <name>` runs it
    on_stop ... end                  runs when the script ends or a stop rule fires (not on Stop)

Conditions and expressions use: hp maxhp hp_pct mp maxmp mp_pct level map map_name x y kills
minutes clock ("HH:MM") monsters drop_left, your own `set` names, numbers, "text", and/or/not,
< <= > >= == !=, + - * / and the functions bag("item"), random(a, b), min, max, abs, round, int.
mp, maxmp, mp_pct and level come from the game (the own-HP key and the character sheet); they are -1
until they have been seen. What this run earned: arnebia_gained contribution_gained xp_gained
levels_gained shards_gained, e.g. `stop_when contribution_gained >= 5000`. drop_left: minutes of the
day's drop time left (the game gives 7 hours a day), -1 until the character sheet has said; a farm
whose until= is already true when it is reached is skipped, trip and all:
`farm map=5361 until=drop_left >= 0 and drop_left <= 240` farms Underwater World for the first three
hours of the day's drop time, and only those - a restart in the middle carries on with what is left.
"""

from __future__ import annotations

import ast
import math
import operator
import random
import re
import shlex
import time
from typing import Any

from .. import atlas, chat, dungeons, jsonio, knowledge, sounds, stats
from ..settings import LEVEL_KEEP_MOST
from ..link import AgentError
from ..clock import CLOCK, Clock, clock_for
from ..i18n import N_, _
from . import (activities, bosses, chores, constructionscan, dungeon, follow, keyboard, market, resume, survey,
               teamlead, town, xpcard)
from .bot import Bot, Respawned, Stopped
from .botparts.base import GameLost, WrongCharacter
from collections.abc import Callable, Mapping

def _at(rest: str, n: int):
    """Split a trailing 'at x,y' (or 'at x y') off a command: (rest, (x, y) or None)."""
    m = re.match(r"^(.*?)\s+at\s+(\d+)\s*[, |]\s*(\d+)\s*$", rest.strip(), re.I)
    if not m:
        return rest, None
    return m.group(1), (int(m.group(2)), int(m.group(3)))


BEST_MAP = "best"        # farm map=best: the best map for the character's level when the line runs
BLOCKS = ("repeat", "while", "if", "sub", "on_stop")
END_WORDS = ("start", "safe", "hearthstone", "here")
COMMANDS = ("login", "end_at", "keep_drop", "keep_level", "travel", "go", "talk", "choose", "visit", "restock", "gem_maker", "gem_breaker", "potion_maker", "triggers", "enhance", "chores", "bounties", "construction_scan", "gather", "lose_xp", "dungeon", "sell_collection", "market", "sell_shards", "clean_bag", "update_shard_prices", "store_items", "survey", "follow", "come", "buy", "sell", "withdraw", "deposit", "walk", "farm", "key", "hold", "wait", "say", "set", "heal", "hearthstone", "manage_team",
            "stop_when", "stop", "break", "call", "elif", "else", "end",
            "sound_watch", "sound_mute", "sound_wait", keyboard.COMMAND, "chat", "use", "wait_until") + BLOCKS
FARM_OPTIONS = ("map", "minutes", "kills", "patrol", "area", "points", "nogo", "only", "skip", "elites", "until")
MOST_POINTS = 10          # a farm's own patrol points (patrol=points)
VARIABLES = ("hp", "maxhp", "hp_pct", "mp", "maxmp", "mp_pct", "level", "map", "map_name", "x", "y",
             "kills", "minutes", "clock", "monsters", "arnebia_gained", "contribution_gained", "xp_gained",
             "levels_gained", "shards_gained", "sound_received", "sound_id", "sound_x", "sound_y",
             "sound_muted", "sound_positioned", "drop_left",
             "boss", "boss_dist", "in_dungeon", "bag_pct", "deaths", "wait_met", "used")
# chat <room> <text>: the chat channels a script may write to, by the names players use
CHAT_ROOMS = {"global": 0, "map": 1, "nearby": 2, "gang": 3, "team": 4, "international": 5, "private": 6}
CHAT_GAP = 3.0               # seconds between two chat lines to one room, at least
CHAT_MOST = 300              # characters in one chat line, at most
WAIT_UNTIL_MOST = 86400.0    # wait_until's timeout: at most a day
WAIT_UNTIL_LOOK = 0.5        # ... and how often it looks


SKIPPED_MOST = 10     # farms in a row skipped as already done: a repeat with nothing left to farm


class ScriptError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(_("line {n}: {msg}", n=line, msg=message))
        self.line = line


class _Break(Exception):
    pass


class _End(Exception):
    """`stop` in a script: a normal ending, so on_stop runs."""


# ---------------------------------------------------------------- expressions
# Any: a script's values are numbers or text, worked out as it runs (see evaluate)
_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: operator.mod}
_CMP: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}
FUNCTIONS = ("bag", "random", "min", "max", "abs", "round", "int", "near")

# Scripts are player-written files: bound what one can make the app allocate. These are far above
# any real script (a few dozen lines, a handful of nested blocks, short expressions) and keep a
# typo or a hostile file from exhausting memory or the Python stack.
MAX_SCRIPT = 1_000_000       # characters in one script
MAX_BLOCK_DEPTH = 64         # if/repeat/while nested inside each other
MAX_EXPR_TEXT = 4000         # characters in one condition or value
MAX_EXPR_NODES = 300         # parts in one expression
MAX_EXPR_DEPTH = 120         # ... and how deeply they nest
MAX_CALL_DEPTH = 50          # subs calling subs at run time
MAX_INT_BITS = 4096          # a `set` value can't grow into a huge integer
MAX_TEXT = 10_000            # ... or a huge string


def _check_value(value: object, line: int) -> None:
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > MAX_INT_BITS:
        raise Stopped(_("line {n}: the number grew too large (over {bits} bits)", n=line, bits=MAX_INT_BITS))
    if isinstance(value, (str, bytes)) and len(value) > MAX_TEXT:
        raise Stopped(_("line {n}: the text grew too large (over {max} characters)", n=line, max=MAX_TEXT))


def compile_expr(text: str, line: int) -> ast.expr:
    """Parse once, refusing anything that isn't plain arithmetic, comparison and our functions."""
    text = text.strip()
    if not text:
        raise ScriptError(line, _("a condition or value is missing"))
    if len(text) > MAX_EXPR_TEXT:
        raise ScriptError(line, _("this expression is too long (over {max} characters)", max=MAX_EXPR_TEXT))
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        raise ScriptError(line, _("can't read this expression: {expr}", expr=text))
    count = depth = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    while stack:
        node, d = stack.pop()
        count += 1
        depth = max(depth, d)
        if count > MAX_EXPR_NODES or depth > MAX_EXPR_DEPTH:
            raise ScriptError(line, _("this expression is too complex (over {n} parts or {d} deep)",
                                      n=MAX_EXPR_NODES, d=MAX_EXPR_DEPTH))
        if isinstance(node, ast.Constant):
            if isinstance(node.value, int) and not isinstance(node.value, bool) \
                    and node.value.bit_length() > MAX_INT_BITS:
                raise ScriptError(line, _("this number is too large: {expr}", expr=text))
            if isinstance(node.value, (str, bytes)) and len(node.value) > MAX_TEXT:
                raise ScriptError(line, _("this text is too long: {expr}", expr=text))
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ScriptError(line, _("this number is too large: {expr}", expr=text))
        stack.extend((child, d + 1) for child in ast.iter_child_nodes(node))
    for node in ast.walk(tree):
        ok = isinstance(node, (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
                               ast.UAdd, ast.BinOp, ast.Compare, ast.Name, ast.Load, ast.Constant, ast.Call))
        ok = ok or type(node) in _BIN or type(node) in _CMP
        if not ok:
            raise ScriptError(line, _("not allowed in an expression: {expr}", expr=text))
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS):
            raise ScriptError(line, _("unknown function in: {expr}", expr=text))
    return tree.body


def _clock_rule(expr: ast.expr | None) -> bool:
    """`clock == "HH:MM"`: a plan's stop time."""
    return (isinstance(expr, ast.Compare) and len(expr.ops) == 1 and isinstance(expr.ops[0], ast.Eq)
            and isinstance(expr.left, ast.Name) and expr.left.id == "clock")


def end_time(expr: ast.expr | None, started: float, now: float) -> float | None:
    """The clock time a stop rule ends the run at, for the two kinds a plan writes: a time limit
    (`minutes >= 300`, counted from the run's start like the `minutes` value) and a stop time
    (`clock == "04:30"`, the next time the computer's clock shows it). None for any other rule:
    when it comes true can't be known in advance."""
    if not isinstance(expr, ast.Compare) or len(expr.ops) != 1 or not isinstance(expr.left, ast.Name):
        return None
    op, right = expr.ops[0], expr.comparators[0]
    if not isinstance(right, ast.Constant):
        return None
    value: object = right.value
    if expr.left.id == "minutes" and isinstance(op, (ast.GtE, ast.Gt)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            return None
        return started + max(0.0, float(value)) * 60.0
    if expr.left.id == "clock" and isinstance(op, ast.Eq) and isinstance(value, str):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
        here = time.localtime(now)
        at = time.mktime((here.tm_year, here.tm_mon, here.tm_mday, hour, minute, 0, 0, 0, -1))
        if at <= now - 60:                # its minute is over today: tomorrow's
            at = time.mktime((here.tm_year, here.tm_mon, here.tm_mday + 1, hour, minute, 0, 0, 0, -1))
        return at
    return None


def _sized(value: Any) -> Any:
    """value, unless it is text (or bytes) grown past MAX_TEXT: "a" + "a" + ... eats memory too."""
    if isinstance(value, (str, bytes)) and len(value) > MAX_TEXT:
        raise ValueError(_("the text grew too long (over {n} characters)", n=MAX_TEXT))
    return value


def evaluate(node: ast.expr, names: Mapping[str, Any], funcs: Mapping[str, Callable[..., Any]]) -> Any:
    """The value of a checked expression (compile_expr). Any: a script's values are whatever its
    variables and the game's numbers hold - numbers, text, true or false."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        if node.id not in names:
            raise KeyError(node.id)
        found = names[node.id]
        return found.get() if isinstance(found, _Later) else found    # (read from the game only now)
    if isinstance(node, ast.BoolOp):
        vals = (evaluate(v, names, funcs) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = evaluate(node.operand, names, funcs)
        return (not v) if isinstance(node.op, ast.Not) else (-v if isinstance(node.op, ast.USub) else +v)
    if isinstance(node, ast.BinOp):
        left, right = evaluate(node.left, names, funcs), evaluate(node.right, names, funcs)
        if isinstance(node.op, ast.Mult) and (isinstance(left, (str, bytes)) or isinstance(right, (str, bytes))):
            raise ValueError(_("text can't be multiplied"))   # "a" * 10**9 would eat the memory
        return _sized(_BIN[type(node.op)](left, right))   # compile_expr let only these operators through
    if isinstance(node, ast.Compare):
        left = evaluate(node.left, names, funcs)
        for op, right_node in zip(node.ops, node.comparators):
            right = evaluate(right_node, names, funcs)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        if not isinstance(fn, ast.Name):
            raise ValueError("unsupported")
        return funcs[fn.id](*[evaluate(a, names, funcs) for a in node.args])
    raise ValueError("unsupported")


# ---------------------------------------------------------------- parsing
class Node:
    """One command of a script, with the block under it (repeat, while, if, sub, on_stop)."""

    def __init__(self, kind: str, line: int, args: str = "", body: list[Node] | None = None) -> None:
        self.kind, self.line, self.args = kind, line, args
        self.body: list[Node] = body or []
        self.branches: list[tuple[ast.expr | None, list[Node]]] = []   # for if: [(condition, body)], else as (None, body)
        self.expr: ast.expr | None = None
        self.opts: dict[str, Any] = {}       # what the command's prepare step read from its line


def _strip_comment(text: str) -> str:
    out: list[str] = []
    quote: str | None = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def parse(text: str) -> tuple[list[Node], dict[str, list[Node]], list[Node]]:
    """Text -> (main nodes, subs {name: nodes}, on_stop nodes). Raises ScriptError. A UTF-8 byte
    order mark at the start (Notepad writes one) is not part of the first command."""
    text = text[1:] if text.startswith("\ufeff") else text
    if len(text) > MAX_SCRIPT:
        raise ScriptError(1, _("the script is too long (over {max} characters)", max=MAX_SCRIPT))
    root = Node("root", 0)
    stack = [root]
    subs: dict[str, list[Node]] = {}
    on_stop: list[Node] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if not line:
            continue
        parts = line.split(None, 1)
        word = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        if word not in COMMANDS:
            raise ScriptError(n, _("unknown command: {cmd}", cmd=word))
        if word == "end":
            _parse_end(stack, subs, on_stop, n)
        elif word in ("elif", "else"):
            _parse_branch(stack[-1], word, rest, n)
        else:
            _parse_command(stack, word, rest, n)
    if len(stack) > 1:
        raise ScriptError(stack[-1].line, _("'{word}' block has no 'end'", word=stack[-1].kind))
    _check_script(root.body, subs, on_stop)
    return root.body, subs, on_stop


def _parse_end(stack: list[Node], subs: dict[str, list[Node]], on_stop: list[Node], n: int):
    """'end': the block on top is done; a sub or on_stop block goes where it belongs."""
    if len(stack) == 1:
        raise ScriptError(n, _("'end' without a block to end"))
    done = stack.pop()
    if done.kind == "sub":
        subs[done.args] = done.body
    elif done.kind == "on_stop":
        on_stop.extend(done.body)


def _parse_branch(top: Node, word: str, rest: str, n: int):
    """'elif' or 'else' inside an if block: a new branch the next lines go into."""
    if top.kind != "if":
        raise ScriptError(n, _("'{word}' outside an if block", word=word))
    if top.branches and top.branches[-1][0] is None:
        raise ScriptError(n, _("nothing can follow 'else' in the same if"))
    top.branches.append((compile_expr(rest, n) if word == "elif" else None, []))
    top.body = top.branches[-1][1]


def _parse_command(stack: list[Node], word: str, rest: str, n: int):
    """Any other command: checked, put in the block on top, and opening a block of its own when it
    is one (sub and on_stop only at the top level)."""
    node = Node(word, n, rest)
    _prepare(node)
    if word in ("sub", "on_stop"):
        if len(stack) != 1:
            raise ScriptError(n, _("'{word}' must be at the top level, not inside a block", word=word))
        if word == "sub" and not rest:
            raise ScriptError(n, _("a sub needs a name"))
        stack.append(node)
        return
    stack[-1].body.append(node)
    if word in ("repeat", "while", "if"):
        if len(stack) - 1 >= MAX_BLOCK_DEPTH:
            raise ScriptError(n, _("blocks are nested too deeply (over {max})", max=MAX_BLOCK_DEPTH))
        if word == "if":
            node.branches.append((node.expr, []))
            node.body = node.branches[0][1]
        stack.append(node)


def _check_script(main: list[Node], subs: dict[str, list[Node]], on_stop: list[Node]):
    """What can only be checked once the whole script is read: calls, recursion, breaks."""
    _check_calls(main, subs)
    for node in main[1:]:
        if node.kind == "login":
            raise ScriptError(node.line, _("login must be the first command in a script"))
    for body in list(subs.values()) + [on_stop]:
        _check_calls(body, subs)
        for node in body:
            if node.kind == "login":
                raise ScriptError(node.line, _("login must be the first command in a script"))
    _check_recursion(subs)
    _check_breaks(main, False)
    for body in list(subs.values()) + [on_stop]:
        _check_breaks(body, False)


def _check_breaks(nodes: list[Node], in_loop: bool):
    """'break' only inside repeat/while: anywhere else it would end the run with a crash."""
    for node in nodes:
        if node.kind == "break" and not in_loop:
            raise ScriptError(node.line, _("'break' outside a repeat or while block"))
        inner = in_loop or node.kind in ("repeat", "while")
        _check_breaks(node.body, inner)
        for _c, body in node.branches:
            _check_breaks(body, inner)


def _check_calls(nodes: list[Node], subs: dict[str, list[Node]]):
    for node in nodes:
        if node.kind == "call" and node.args not in subs:
            raise ScriptError(node.line, _("no sub named {name}", name=node.args))
        _check_calls(node.body, subs)
        for _c, body in node.branches:
            _check_calls(body, subs)


def _calls(nodes: list[Node]) -> list[Node]:
    """Every call node in a block, nested blocks included."""
    out: list[Node] = []
    for node in nodes:
        if node.kind == "call":
            out.append(node)
        out.extend(_calls(node.body))
        for _c, body in node.branches:
            out.extend(_calls(body))
    return out


def _check_recursion(subs: dict[str, list[Node]]):
    """Refuse a sub that reaches itself: it would recurse until the Python stack dies.

    Iterative on purpose: a file with thousands of subs must be refused or run, not crash the
    checker itself. Block nesting is already capped, so _calls() stays shallow.
    """
    edges = {name: _calls(body) for name, body in subs.items()}
    color: dict[str, int] = {}
    for start in subs:
        if color.get(start):
            continue
        color[start] = 1
        stack = [(start, iter(edges[start]))]
        while stack:
            _name, calls = stack[-1]
            for call in calls:
                target = call.args
                if color.get(target) == 1:
                    raise ScriptError(call.line, _("recursion is not allowed: {name} ends up calling itself",
                                                   name=start))
                if not color.get(target):
                    color[target] = 1
                    stack.append((target, iter(edges[target])))
                    break
            else:
                color[stack[-1][0]] = 2
                stack.pop()


def _split(rest: str, line: int) -> list[str]:
    try:
        return shlex.split(rest, posix=True)
    except ValueError:
        raise ScriptError(line, _("unbalanced quotes"))


def _message_split(rest: str) -> tuple[str, str, str]:
    """`condition : message` split at the first colon outside quotes ("23:00" stays whole)."""
    quote: str | None = None
    for i, c in enumerate(rest):
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == ":":
            return rest[:i], ":", rest[i + 1:]
    return rest, "", ""


def _number(text: str, line: int, what: str) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ScriptError(line, _("{what} must be a number, not {value}", what=what, value=text))
    if not math.isfinite(value):
        # "nan", "inf", 1e999: no count, wait or cell can be that (int() of one raised OverflowError)
        raise ScriptError(line, _("{what} must be a number, not {value}", what=what, value=text))
    return value


def _prepare(node: Node):
    """Check a command's arguments as it is read, so mistakes show before anything runs. One
    function per command (PREPARE); a command missing from it takes no arguments to check."""
    prep = PREPARE.get(node.kind)
    if prep is not None:
        prep(node, node.args, node.line)


def _prep_condition(node: Node, rest: str, n: int):
    node.expr = compile_expr(rest, n)


def _prep_repeat(node: Node, rest: str, n: int):
    node.opts["times"] = int(_number(rest, n, "repeat")) if rest else None


def _prep_set(node: Node, rest: str, n: int):
    name, eq, expr = rest.partition("=")
    name = name.strip()
    if not eq or not re.match(r"^[A-Za-z_]\w*$", name):
        raise ScriptError(n, _("write it as: set name = value"))
    if name in VARIABLES:
        raise ScriptError(n, _("{name} is read from the game and can't be set", name=name))
    node.opts["name"], node.expr = name, compile_expr(expr, n)


def _prep_stop_when(node: Node, rest: str, n: int):
    cond, colon, msg = _message_split(rest)
    node.expr = compile_expr(cond, n)
    node.opts["message"] = msg.strip() if colon else _("stop rule: {cond}", cond=cond.strip())


def _prep_end_at(node: Node, rest: str, n: int):
    word = rest.strip().lower()
    if word in END_WORDS:
        node.opts["end"] = (word,)
        return
    rest2, cell = _at(rest, n)
    mid = atlas.resolve(" ".join(_split(rest2, n))) if rest2.strip() else None
    if mid is None:
        raise ScriptError(n, _("end_at takes start, safe, hearthstone, here, or a map (and 'at x,y'): {what}",
                               what=rest))
    node.opts["end"] = ("place", mid, cell)


def _prep_keep_drop(node: Node, rest: str, n: int):
    """keep_drop <minutes>: the day's drop time this run leaves unused (the character's setting
    otherwise). 0 uses all of it."""
    word = rest.strip()
    if not word.isdigit() or int(word) > 1440:
        raise ScriptError(n, _("keep_drop takes the minutes of drop time to save, 0 to 1440: {what}", what=rest))
    node.opts["minutes"] = int(word)


def _prep_keep_level(node: Node, rest: str, n: int):
    """keep_level <experience>: this run stops once the next level needs less experience than
    that (the character's "Stop before the next level" otherwise). 0: no such stop this run."""
    word = rest.strip().replace(",", "")
    if not word.isdigit() or int(word) > LEVEL_KEEP_MOST:
        raise ScriptError(n, _("keep_level takes the experience the next level must still need, 0 to {most}: {what}",
                               most=LEVEL_KEEP_MOST, what=rest))
    node.opts["xp"] = int(word)


def _map_of(where: str, n: int):
    """The map a script names (by name or number), or the line's error."""
    mid = atlas.resolve(" ".join(_split(where, n)))
    if mid is None:
        raise ScriptError(n, _("unknown map (or several share that name; use the number): {map}", map=where))
    return mid


def _prep_travel(node: Node, rest: str, n: int):
    rest, cell = _at(rest, n)
    node.opts["map"], node.opts["cell"] = _map_of(rest, n), cell


def _prep_go(node: Node, rest: str, n: int):
    m = re.match(r"^\s*dungeon\s+(.+?)(\s+talk)?\s*$", rest, re.I)
    if m:
        e = dungeons.find(" ".join(_split(m.group(1), n)))
        if e is None:
            raise ScriptError(n, _("no known entrance for the dungeon {name}", name=m.group(1).strip()))
        node.opts.update(dungeon=e["name"], talk=bool(m.group(2)))
        return
    m = re.match(r"^(.*?)\s+to\s+(.+)$", rest.strip(), re.I)
    npc = " ".join(_split(m.group(2), n)) if m else None
    rest, cell = _at(m.group(1) if m else rest, n)
    node.opts.update(map=_map_of(rest, n), cell=cell, npc=npc, talk=False)


def _prep_player(node: Node, rest: str, n: int):
    k = node.kind
    words = _split(rest, n)
    if not words:
        raise ScriptError(n, _("{cmd} whom? write: {cmd} <player name>", cmd=k))
    node.opts["player"] = words[0]
    if k == "come" and len(words) > 1:
        where, cell = _at(" ".join(words[1:]), n)
        mid = atlas.resolve(where)
        if mid is None:
            raise ScriptError(n, _("unknown map (or several share that name; use the number): {map}",
                                   map=where))
        node.opts["map"] = mid
        node.opts["cell"] = cell


FOLLOW_OPTS = ("role", "distance", "pull", "help", "skip")
OFF_WORDS = ("off", "no", "false", "0")
ON_WORDS = ("on", "yes", "true", "1")


def _prep_follow(node: Node, rest: str, n: int):
    """follow <player> [role=heal|attack|both] [distance=N] [pull=N|off] [help=on|off] [skip="A, B"]:
    this follow's own choices (engine/followopts.py), before the character's Team settings."""
    from . import followopts
    _prep_player(node, rest, n)
    chosen: dict[str, Any] = {}
    for word in _split(rest, n)[1:]:
        key, sep, value = word.partition("=")
        key, value = key.strip().lower(), value.strip()
        if not sep or key not in FOLLOW_OPTS:
            raise ScriptError(n, _("follow: unknown option {option} - write role=, distance=, pull=, help= or skip=",
                                   option=word))
        if key == "role":
            if value.lower() not in followopts.ROLES:
                raise ScriptError(n, _("follow: role= is heal, attack or both, not {value}", value=value))
            chosen[key] = value.lower()
        elif key == "pull" and value.lower() in OFF_WORDS:
            chosen[key] = 0
        elif key in ("distance", "pull"):
            most = followopts.DISTANCE_MOST if key == "distance" else followopts.PULL_MOST
            num = _number(value, n, key)
            if not 1 <= num <= most:
                raise ScriptError(n, _("follow: {option}= is from 1 to {most} tiles, not {value}",
                                       option=key, most=most, value=value))
            chosen[key] = int(num)
        elif key == "help":
            if value.lower() not in OFF_WORDS + ON_WORDS:
                raise ScriptError(n, _("follow: help= is on or off, not {value}", value=value))
            chosen[key] = value.lower() in ON_WORDS
        else:
            chosen[key] = value
    node.opts["follow"] = chosen


def _prep_talk(node: Node, rest: str, n: int):
    rest, cell = _at(rest, n)
    if not rest.strip():
        raise ScriptError(n, _("talk to whom? write: talk Cable Car manager"))
    node.opts["npc"], node.opts["cell"] = " ".join(_split(rest, n)), cell


def _prep_survey(node: Node, rest: str, n: int):
    w = rest.strip() or "cities"
    if w not in ("cities", "all", "everything", "here"):
        mid = atlas.resolve(w)
        if mid is None:
            raise ScriptError(n, _("survey cities, survey all, survey everything, survey here, or survey <map>"))
        w = [mid]
    node.opts["scope"] = w


def _prep_triggers(node: Node, rest: str, n: int):
    if rest.strip().lower() != "off":
        raise ScriptError(n, _("write it as: triggers off"))


def _prep_check_only(node: Node, rest: str, n: int):
    if rest.strip().lower() not in ("", "check"):
        raise ScriptError(n, _("write it as: {cmd}  or  {cmd} check", cmd=node.kind))
    node.opts["check"] = rest.strip().lower() == "check"


def _prep_dungeon(node: Node, rest: str, n: int):
    """dungeon [check] [<cave>]: the cave named, or the one set in Tools, Dungeons."""
    words = rest.strip()
    check = False
    m = re.match(r"^check\b\s*(.*)$", words, re.I)
    if m:
        check, words = True, m.group(1).strip()
    cave = words.strip('"').strip()
    known = {k.lower(): k for k in dungeon.CAVES}
    if "," in cave:
        raise ScriptError(n, _("one cave only: a character goes into one cave a day"))
    if cave and cave.lower() not in known:
        raise ScriptError(n, _("no such dungeon: {name} (there are: {all})", name=cave, all=", ".join(dungeon.CAVES)))
    node.opts.update(check=check, cave=known[cave.lower()] if cave else None)


def _prep_chores(node: Node, rest: str, n: int):
    words = rest.split()
    node.opts["check"] = "check" in [w.lower() for w in words]
    only: list[str] = []
    for w in words:
        if w.lower() == "check":
            continue
        if not w.lower().startswith("only="):
            raise ScriptError(n, _("write it as: chores [check] [only=bank,donation]"))
        only += [x.strip().lower() for x in w[5:].split(",") if x.strip()]
    unknown = [x for x in only if x not in chores.CHORES]
    if unknown:
        raise ScriptError(n, _("no such chore: {names} (there are: {all})", names=", ".join(unknown),
                               all=", ".join(chores.CHORES)))
    node.opts["only"] = only


def _prep_construction_scan(node: Node, rest: str, n: int):
    words = rest.split()
    kinds = tuple(constructionscan.ENTRIES)
    if not words:
        return
    how, _sep, kind = words[0].lower().partition("=")
    extra = words[1].lower().partition("=") if len(words) == 2 else None
    if (len(words) > 2 or how not in ("publish", "auto") or kind not in kinds
            or (extra is not None and (extra[0] != "lines" or extra[2] not in ("yes", "no")))):
        raise ScriptError(n, _("write it as: construction_scan, construction_scan publish=broadcast or "
                               "construction_scan auto=broadcast (announcement, broadcast or system), "
                               "and for an announcement add lines=yes or lines=no to choose a line to each "
                               "highland or one line"))
    node.opts[how] = kind
    if extra is not None:
        node.opts["lines"] = extra[2] == "yes"


def _prep_sell_collection(node: Node, rest: str, n: int):
    w = rest.strip().lower()
    kinds = tuple(town.COLLECTION_BUYERS)
    if w and w not in kinds + ("all",):
        raise ScriptError(n, _("write it as: sell_collection  or  sell_collection fish (fish, ore or herb)"))
    node.opts["kinds"] = (kinds if w == "all" else (w,)) if w else None


MARKET_HOW = N_("write it as: market scan [sellers] | market search [item=\"<name>\"] [cheapest] [max=<price>] "
                  "[category=<name>] [pages=<n>] | market seller <id>[,<id>] | "
                  "market buy [check] [item=\"<name>\" count=<n> max=<price> [cap=<gold>]]")


def _prep_market(node: Node, rest: str, n: int):
    """market scan [sellers] / search [...] / seller <ids> / buy [...]: the Market searcher and
    buyer (engine/market.py). With no words after search or buy, the window's own files."""
    try:
        words = shlex.split(rest)
    except ValueError as error:
        raise ScriptError(n, _(MARKET_HOW)) from error
    if not words or words[0].lower() not in ("scan", "search", "seller", "buy"):
        raise ScriptError(n, _(MARKET_HOW))
    what = words[0].lower()
    o: dict[str, Any] = {"what": what}
    cats = {market.category_name(c).lower(): c for c, _n in market.CATEGORIES}
    cats.update({name.lower(): c for c, name in market.CATEGORIES})
    for w in words[1:]:
        key, _eq, value = w.partition("=")
        key = key.lower()
        if what == "seller" and not _eq:
            ids = market.ids_of(w)
            if not ids:
                raise ScriptError(n, _("market seller needs seller IDs: market seller 1234,5678"))
            o.setdefault("sellers", []).extend(ids)
        elif not _eq and key in ("sellers", "check", "cheapest"):
            o[key] = True
            if key == "cheapest":
                o["kind"] = "cheapest"
        elif key == "item":
            o["names"] = market.names_of(value)
        elif key in ("max", "count", "pages", "cap") and value.isdigit():
            o["max_price" if key == "max" else key] = int(value)
        elif key == "category" and value.lower() in cats:
            o["cat"] = cats[value.lower()]
        else:
            raise ScriptError(n, _(MARKET_HOW))
    if what == "seller" and not o.get("sellers"):
        raise ScriptError(n, _("market seller needs seller IDs: market seller 1234,5678"))
    if what == "buy" and o.get("names") and not o.get("count"):
        raise ScriptError(n, _("how many? write count=<n>"))
    node.opts.update(o)


def _prep_visit(node: Node, rest: str, n: int):
    if not rest.strip():
        raise ScriptError(n, _("visit what? write: visit storehouse  or  visit Grocer John"))
    node.opts["what"] = rest.strip()


def _prep_count_item(node: Node, rest: str, n: int):
    m = re.match(r"^\s*(\d+)\s+(.+?)\s*$", rest)
    if not m:
        raise ScriptError(n, _("write it as: {cmd} 50 Large Red Potion", cmd=node.kind))
    node.opts["count"], node.opts["item"] = int(m.group(1)), m.group(2).strip('"')


def _prep_item_count(node: Node, rest: str, n: int):
    m = re.match(r"^\s*(.+?)(?:\s+(\d+))?\s*$", rest)
    if not m or not m.group(1).strip():
        raise ScriptError(n, _("write it as: {cmd} Milkfish 20", cmd=node.kind))
    node.opts["item"] = m.group(1).strip('"')
    node.opts["count"] = int(m.group(2)) if m.group(2) else None


def _prep_choose(node: Node, rest: str, n: int):
    text = rest.strip()
    if not text:
        raise ScriptError(n, _("choose what? write: choose Tulip City"))
    if len(text) > 2 and text.startswith("/") and text.endswith("/"):
        try:
            re.compile(text[1:-1])
        except re.error as e:
            raise ScriptError(n, _("bad pattern: {why}", why=e))
        node.opts["pattern"] = text[1:-1]
    else:
        node.opts["pattern"] = re.escape(text.strip('"'))


def _prep_walk(node: Node, rest: str, n: int):
    parts = _split(rest.replace(",", " "), n)
    if len(parts) != 2:
        raise ScriptError(n, _("write it as: walk x y"))
    node.opts["cell"] = (int(_number(parts[0], n, "x")), int(_number(parts[1], n, "y")))


def _prep_key(node: Node, rest: str, n: int):
    parts = _split(rest, n)
    if not parts:
        raise ScriptError(n, _("which key?"))
    node.opts["key"] = parts[0].lower()
    node.opts["ms"] = int(_number(parts[1], n, "milliseconds")) if len(parts) > 1 else 60


def _prep_hold(node: Node, rest: str, n: int):
    parts = _split(rest, n)
    if len(parts) != 2:
        raise ScriptError(n, _("write it as: hold key seconds"))
    node.opts["key"], node.opts["secs"] = parts[0].lower(), _number(parts[1], n, "seconds")


def _prep_wait(node: Node, rest: str, n: int):
    node.opts["secs"] = _number(rest, n, "seconds")


def _farm_options(rest: str, n: int) -> tuple[dict[str, Any], str]:
    """farm's name=value words, as written (until= takes the rest of the line: a condition may hold
    spaces and quotes)."""
    opts: dict[str, Any] = {}
    m = re.search(r"(?:^|\s)until=", rest)
    if m:
        opts["until"] = rest[m.end():].strip()
        rest = rest[:m.start()]
        if not opts["until"]:
            raise ScriptError(n, _("until= needs a condition, for example: until=kills >= 100"))
    for part in _split(rest, n):
        key, eq, value = part.partition("=")
        key = key.lower()
        if not eq or key not in FARM_OPTIONS:
            raise ScriptError(n, _("farm doesn't know '{opt}'. It takes: {opts}", opt=part,
                                   opts=", ".join(FARM_OPTIONS)))
        opts[key] = value
    return opts, rest


def _farm_area(text: str, n: int) -> list[int]:
    box = [int(_number(v, n, "area")) for v in text.split(",")]
    if len(box) != 4:
        raise ScriptError(n, _("area is four numbers: x1,y1,x2,y2"))
    return [min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])]


def _farm_cells(text: str, n: int, size: int, what: str) -> list[list[int]]:
    """"x,y;x,y" (size 2) or "x,y,r;..." (size 3) as lists of numbers."""
    out: list[list[int]] = []
    for part in text.split(";"):
        if not part.strip():
            continue
        nums = [int(_number(v.strip(), n, what)) for v in part.split(",")]
        if len(nums) != size or any(v < 0 for v in nums):
            raise ScriptError(n, _("points is x,y;x,y;... (up to 10)") if size == 2
                              else _("nogo is x,y,radius;x,y,radius;..."))
        out.append(nums)
    return out


def _farm_choices(opts: dict[str, Any], n: int):
    if "points" in opts and "patrol" not in opts:
        opts["patrol"] = "points"         # points alone: go round them
    if opts.get("patrol", "random") not in ("random", "full", "quick", "short", "points"):
        raise ScriptError(n, _("patrol is random, full, quick, short or points"))
    if opts.get("patrol") == "points" and not opts.get("points"):
        raise ScriptError(n, _("patrol=points needs points=x,y;x,y;..."))
    if len(opts.get("points") or []) > MOST_POINTS:
        raise ScriptError(n, _("points is x,y;x,y;... (up to 10)"))
    if opts.get("elites", "fight") not in ("fight", "avoid"):
        raise ScriptError(n, _("elites is fight or avoid"))
    for rx in ("only", "skip"):
        if rx in opts:
            try:
                re.compile(opts[rx])
            except re.error:
                raise ScriptError(n, _("{opt} isn't a valid name pattern", opt=rx))


def _prep_farm(node: Node, rest: str, n: int):
    opts, rest = _farm_options(rest, n)
    if "map" in opts and str(opts["map"]).strip().lower() == BEST_MAP:
        opts["map"] = BEST_MAP            # chosen when the line runs, for the level then (best_map)
    elif "map" in opts:
        opts["map"] = atlas.resolve(opts["map"])
        if opts["map"] is None:
            raise ScriptError(n, _("unknown map: {map}", map=rest))
    for num in ("minutes", "kills"):
        if num in opts:
            opts[num] = _number(opts[num], n, num)
    if "area" in opts:
        opts["area"] = _farm_area(opts["area"], n)
    if "points" in opts:
        opts["points"] = _farm_cells(opts["points"], n, 2, "points")
    if "nogo" in opts:
        opts["nogo"] = _farm_cells(opts["nogo"], n, 3, "nogo")
    _farm_choices(opts, n)
    if "until" in opts:
        opts["until_text"] = opts["until"]
        opts["until"] = compile_expr(opts["until"], n)
    node.opts = opts


def _prep_say(node: Node, rest: str, n: int):
    node.opts["parts"] = [(compile_expr(m, n) if i % 2 else m)
                          for i, m in enumerate(re.split(r"\{([^}]*)\}", rest))]


def _prep_chat(node: Node, rest: str, n: int):
    """chat <room> <text>: a line on a game chat channel (CHAT_ROOMS), {values} worked out as say's."""
    room, _sp, text = rest.strip().partition(" ")
    if room.lower() not in CHAT_ROOMS:
        raise ScriptError(n, _("chat to which room? write: chat <room> <text>, the room one of: {rooms}",
                               rooms=", ".join(CHAT_ROOMS)))
    if not text.strip():
        raise ScriptError(n, _("chat: write the text after the room"))
    node.opts["room"] = CHAT_ROOMS[room.lower()]
    _prep_say(node, text.strip(), n)


def _prep_use(node: Node, rest: str, n: int):
    """use <item>: an item from the bag (quotes for a name with spaces are welcome, not needed)."""
    words = _split(rest, n)
    if not words:
        raise ScriptError(n, _("use what? write: use <item name>"))
    node.opts["item"] = " ".join(words)


def _prep_wait_until(node: Node, rest: str, n: int):
    """wait_until <condition> [timeout=<seconds>]: 60 seconds unless said, a day at most."""
    m = re.search(r"\s+timeout=(\S+)\s*$", rest)
    cond = rest[:m.start()] if m else rest
    secs = _number(m.group(1), n, "timeout") if m else 60.0
    if not 1 <= secs <= WAIT_UNTIL_MOST:
        raise ScriptError(n, _("wait_until: timeout= is from 1 to {most} seconds, not {value}",
                               most=int(WAIT_UNTIL_MOST), value=m.group(1) if m else secs))
    node.expr = compile_expr(cond, n)
    node.opts["secs"] = secs


def _prep_login(node: Node, rest: str, n: int):
    words = _split(rest, n)
    if len(words) > 1:
        raise ScriptError(n, _("write it as: login  or  login <character>"))
    node.opts["character"] = words[0] if words else None


def _prep_sound(node: Node, rest: str, n: int):
    timeout = 30.0
    parts = rest.rsplit(" timeout=", 1) if node.kind == "sound_wait" else [rest]
    if len(parts) == 2:
        timeout = _number(parts[1], n, "timeout")
        if timeout < 0:
            raise ScriptError(n, _("Sound wait timeout cannot be negative."))
    try:
        ids = sounds.IdSet.parse(parts[0])
    except ValueError as error:
        raise ScriptError(n, str(error)) from error
    if node.kind == "sound_wait" and ids.is_empty():
        raise ScriptError(n, _("sound_wait needs at least one sound ID, or all."))
    node.opts.update(ids=ids.text, timeout=timeout)


def _prep_keyboard_profile(node: Node, rest: str, n: int):
    args = _split(rest, n)
    if len(args) != 1:
        raise ScriptError(n, _("Use keyboard_profile followed by one quoted profile name."))
    try:
        node.opts["name"] = keyboard.profile_name(args[0])
    except ValueError as error:
        raise ScriptError(n, str(error)) from error


PREPARE = {
    "login": _prep_login,
    keyboard.COMMAND: _prep_keyboard_profile,
    "while": _prep_condition, "if": _prep_condition, "repeat": _prep_repeat, "set": _prep_set,
    "stop_when": _prep_stop_when, "end_at": _prep_end_at, "keep_drop": _prep_keep_drop, "keep_level": _prep_keep_level, "travel": _prep_travel, "go": _prep_go,
    "follow": _prep_follow, "come": _prep_player, "talk": _prep_talk, "survey": _prep_survey,
    "triggers": _prep_triggers, "gem_maker": _prep_check_only, "gem_breaker": _prep_check_only, "potion_maker": _prep_check_only, "enhance": _prep_check_only, "chores": _prep_chores, "bounties": _prep_check_only, "construction_scan": _prep_construction_scan, "visit": _prep_visit,
    "gather": _prep_check_only, "lose_xp": _prep_check_only, "dungeon": _prep_dungeon, "sell_collection": _prep_sell_collection,
    "market": _prep_market,
    "buy": _prep_count_item, "withdraw": _prep_count_item, "sell": _prep_item_count, "deposit": _prep_item_count,
    "choose": _prep_choose, "walk": _prep_walk, "key": _prep_key, "hold": _prep_hold, "wait": _prep_wait,
    "farm": _prep_farm, "say": _prep_say, "chat": _prep_chat, "use": _prep_use, "wait_until": _prep_wait_until,
    "sound_watch": _prep_sound, "sound_mute": _prep_sound, "sound_wait": _prep_sound,
}     # call, sub and stop take nothing to check


# ---------------------------------------------------------------- running
class Runner:
    own_lists = False             # `triggers off`: the script does its own Before/After farming steps

    def __init__(self, bot: Bot, text: str):
        self.bot = bot
        self.main, self.subs, self.on_stop = parse(text)
        # `triggers off` anywhere at the top: the script carries its own Before/After steps
        self.own_lists = any(node.kind == "triggers" for node in self.main)
        self.vars: dict[str, Any] = dict(sound_received=False, sound_id=0, sound_x=0.0, sound_y=0.0,
                                         sound_muted=False, sound_positioned=False, wait_met=False, used=False)
        self.call_depth = 0
        self.watched: set[Node] = set()
        self.skipped_farms = 0          # farms in a row skipped as already done (SKIPPED_MOST)
        self.sheet_asked = -1e9         # when drop_left last asked the game for the sheet
        # Where the run is (engine/resume.py): one frame per block being run, its line ("i") and
        # what the line there has done (a loop's "turn", an if's "branch", a farm's progress).
        self.trail: list[dict[str, Any]] = []
        self.ahead: list[dict[str, Any]] = []     # carrying on: the saved frames still to reach
        self.arriving: tuple[Node, dict[str, Any]] | None = None   # the saved line being gone into
        self.unwinding = False          # an exception is leaving the blocks: the trail is no place
        self.minutes_before = 0.0       # carrying on: what the interrupted run had already done
        self.kills_before = 0

    def names(self) -> dict[str, Any]:
        """What an expression can read: the script's own values, then the game's (hp, map...)."""
        b = self.bot
        me = b.me()
        sheet: dict[str, Any] = me.get("sheet") or {}
        snap_map = b.a.json("mem map")
        hp, top = me.get("hp", 0), me.get("maxhp", 0) or 1
        reading = b.mp_now(me, clock_for(b).time())
        mp, maxmp = reading if reading else (sheet.get("mp", -1), sheet.get("maxmp", -1))
        names: dict[str, Any] = dict(self.vars)
        names.update(hp=hp, maxhp=top, hp_pct=round(100.0 * hp / top, 1), mp=mp, maxmp=maxmp,
                     mp_pct=round(100.0 * mp / maxmp, 1) if maxmp > 0 else -1, level=sheet.get("level", -1),
                     map=snap_map.get("id"), map_name=snap_map.get("name"), x=me["x"], y=me["y"],
                     kills=b.kills + self.kills_before, minutes=round(b.minutes() + self.minutes_before, 2),
                     clock=time.strftime("%H:%M"))
        names["monsters"] = _Lazy(lambda: len(b.targets(b.snap())))
        names["drop_left"] = _Lazy(lambda: self.drop_left(sheet))
        # boss and boss_dist from ONE read: "boss != "" and boss_dist < 20" must not see two moments
        seen: list[tuple[str, float]] = []

        def boss_now() -> tuple[str, float]:
            if not seen:
                seen.append(self.boss_seen())
            return seen[0]
        names["boss"] = _Later(lambda: boss_now()[0])
        names["boss_dist"] = _Later(lambda: boss_now()[1])
        names["in_dungeon"] = _Later(lambda: xpcard.in_dungeon(b))
        load, most = sheet.get("load"), sheet.get("maxload")
        names["bag_pct"] = (round(100.0 * load / most, 1) if isinstance(load, (int, float)) and
                            isinstance(most, (int, float)) and most > 0 else -1)
        names["deaths"] = int(getattr(b, "deaths", 0) or 0)
        r: dict[str, Any] = b.tally.result() if b.tally else {}
        picked: dict[str, int] = r.get("picked") or {}
        names.update(arnebia_gained=r.get("arnebia", 0), contribution_gained=r.get("contribution", 0),
                     xp_gained=r.get("experience", 0), levels_gained=r.get("levels", 0),
                     shards_gained=sum(v for n, v in picked.items() if stats.is_shard(n)))
        return names

    def drop_left(self, sheet: dict[str, Any]) -> int:
        """Minutes of the day's drop time left, from the character sheet; -1 when it hasn't said.
        No reading yet (a run that hasn't killed anything): the sheet is asked for, at most once a
        minute."""
        left = stats.drop_left(sheet)
        now = clock_for(self.bot).time()
        ask = getattr(self.bot, "ask_sheet", None)
        if left is None and ask is not None and now - self.sheet_asked >= 60:
            self.sheet_asked = now
            got = ask(force=True)
            left = stats.drop_left(got) if jsonio.is_object(got) else None
        return -1 if left is None else int(left // 60)

    def funcs(self) -> dict[str, Callable[..., Any]]:
        def bag(item: str) -> int:
            rows = self.bot.a.json("mem bag")
            if not jsonio.is_array(rows):
                return 0
            want = str(item).strip().lower()
            exact = [r["count"] for r in rows if (r.get("name") or "").strip().lower() == want]
            return sum(exact) if exact else sum(r["count"] for r in rows if want in (r.get("name") or "").lower())
        def rand(a: float, b: float) -> int:
            return random.randint(int(a), int(b))

        def near(name: str) -> float:
            """Tiles to the player of that name in view (the whole name, any case); -1 when not."""
            snap = self.bot.snap()
            them = follow.find_player(snap, str(name))
            if them is None:
                return -1
            me = snap["self"]
            return round(math.hypot(them["x"] - me["x"], them["y"] - me["y"]), 1)
        return {"bag": bag, "random": rand, "min": min, "max": max,
                "abs": abs, "round": round, "int": int, "near": near}

    def boss_seen(self) -> tuple[str, float]:
        """(name, tiles) of the nearest field boss in view; ("", -1) with none."""
        snap = self.bot.snap()
        me = snap["self"]
        seen = [(math.hypot(e["x"] - me["x"], e["y"] - me["y"]), str(e.get("name") or "")) for e in bosses.in_sight(snap)]
        if not seen:
            return "", -1
        d, name = min(seen)
        return name, round(d, 1)

    def value(self, expr: ast.expr | None, line: int) -> Any:
        """An expression's value now (Any: see evaluate)."""
        if expr is None:
            raise Stopped(_("line {n}: a condition or value is missing", n=line))
        try:
            v = evaluate(expr, self.names(), self.funcs())
            v = v.get() if isinstance(v, _Lazy) else v
            _check_value(v, line)
            return v
        except KeyError as e:
            raise Stopped(_("line {n}: {name} has no value yet (set it first)", n=line, name=e.args[0]))
        except (TypeError, OverflowError, ZeroDivisionError, ValueError) as e:
            raise Stopped(_("line {n}: {err}", n=line, err=e))

    # ------------------------------------------------------------ the run's place
    def resume_from(self, row: dict[str, Any]) -> None:
        """Carry on an interrupted run (engine/resume.py): its place, its values, its minutes and kills."""
        path: list[Any] = row.get("path") or []
        self.ahead = [dict(f) for f in path if jsonio.is_object(f)]
        saved: dict[str, Any] = row.get("vars") or {}
        self.vars.update({k: v for k, v in saved.items() if isinstance(v, (int, float, str, bool))})
        self.minutes_before = float(row.get("minutes") or 0.0)
        self.kills_before = int(row.get("kills") or 0)

    def position(self) -> dict[str, Any] | None:
        """Where the run is now, to save (engine/resume.py); None when it can't be told (the blocks
        are being left, or another thread changed a frame while it was read: the next save will do)."""
        if self.unwinding or not self.trail:
            return None
        b = self.bot
        now = clock_for(b).time()
        try:
            frames: list[dict[str, Any]] = []
            for f in list(self.trail):
                g = {k: v for k, v in list(f.items()) if not k.startswith("_")}
                if "_farm_at" in f:
                    g["farm_minutes"] = round(float(f.get("_farm_m0", 0.0)) + (now - float(f["_farm_at"])) / 60.0, 2)
                    g["farm_kills"] = int(f.get("_farm_k0", 0)) + b.kills - int(f["_farm_kills"])
                frames.append(g)
        except (RuntimeError, KeyError, TypeError, ValueError):
            return None
        values = {k: v for k, v in list(self.vars.items()) if isinstance(v, (int, float, str, bool))}
        return {"path": frames, "vars": values, "minutes": round(b.minutes() + self.minutes_before, 2),
                "kills": b.kills + self.kills_before}

    def _arrive(self, node: Node) -> dict[str, Any] | None:
        """The saved frame of this line when the run is carrying on into it (once), else None."""
        if self.arriving is not None and self.arriving[0] is node:
            frame = self.arriving[1]
            self.arriving = None
            return frame
        return None

    def run(self) -> None:
        try:
            self.block(self.main)
            self.bot.log(_("script finished"))
        except _End as e:
            self.bot.log(str(e) or _("script stopped itself"))
        except (GameLost, WrongCharacter):
            raise                 # no on_stop: the game is gone, or another character is in it
        except Stopped as e:
            if self.bot.stop_event.is_set():
                raise
            self.bot.log(_("STOPPED: {why}", why=e))
            self.finish()
            raise
        self.finish()

    def finish(self) -> None:
        if not self.on_stop:
            return
        self.bot.log(_("running on_stop"))
        self.bot.watches = []
        try:
            self.block(self.on_stop)
        except (_End, _Break):
            pass

    def block(self, nodes: list[Node]) -> None:
        saved = self.ahead.pop(0) if self.ahead else None
        start = min(max(0, int(saved.get("i", 0))), len(nodes)) if saved else 0
        frame: dict[str, Any] = {"i": start}
        self.trail.append(frame)
        try:
            for i, node in enumerate(nodes):
                if i < start:
                    if node.kind in resume.KEPT:
                        self._line(node)    # carrying on: what sets how the run ends still counts
                    continue
                frame.clear()
                frame["i"] = i
                if saved is not None and i == start:
                    self.arriving = (node, saved)
                    if node.kind not in resume.OPENS:
                        self.ahead.clear()    # nothing deeper to reach
                self.unwinding = False
                self._line(node)
        except BaseException:
            self.unwinding = True     # the place to keep is the last one saved, not this
            raise
        finally:
            self.trail.pop()

    def _line(self, node: Node) -> None:
        self.bot.check()
        deaths = 0
        while True:
            try:
                self.unwinding = False
                self.step(node)
                break
            except Respawned:
                # died during this line (travel, walk, talk...) and came back: do it again
                deaths += 1
                if deaths >= 3:
                    raise Stopped(_("line {n}: died every time I tried it", n=node.line))
                self.bot.log(_("line {n} again after respawning", n=node.line))

    LOOP_TURNS = 20.0     # a loop goes round at most this often a second

    @classmethod
    def loop_pause(cls, began: float, clock: Clock = CLOCK) -> float:
        """The pause after one turn of a repeat or while: a moment after a turn that did something,
        longer after one that took no time (waiting for HP to drop, say), so a script polling the
        game never reads it a hundred times a second."""
        return max(0.01, 1.0 / cls.LOOP_TURNS - (clock.time() - began))

    def step(self, node: Node) -> None:
        """One command: the method for its kind, _do_<kind> (a kind with none does nothing here:
        sub and on_stop blocks are kept aside when the script is read)."""
        do = getattr(self, "_do_" + node.kind, None)
        if do is not None:
            do(node, self.bot, node.opts)

    def _loop_turn(self, body: list[Node], began: float) -> bool:
        """One turn of a repeat or while; False when a break ended the loop."""
        b = self.bot
        try:
            self.block(body)
        except _Break:
            return False
        b.sleep(self.loop_pause(began, clock_for(b)))
        return True

    def _do_repeat(self, node: Node, b: Bot, o: dict[str, Any]):
        frame = self.trail[-1]
        saved = self._arrive(node)
        count = max(0, int(saved.get("turn", 1)) - 1) if saved else 0
        while o["times"] is None or count < o["times"]:
            count += 1
            frame["turn"] = count
            if not self._loop_turn(node.body, clock_for(b).time()):
                break

    def _do_while(self, node: Node, b: Bot, o: dict[str, Any]):
        inside = self._arrive(node) is not None    # carrying on inside it: that turn goes on first
        while inside or self.value(node.expr, node.line):
            inside = False
            if not self._loop_turn(node.body, clock_for(b).time()):
                break

    def _do_if(self, node: Node, b: Bot, o: dict[str, Any]):
        frame = self.trail[-1]
        saved = self._arrive(node)
        taken = saved.get("branch") if saved else None
        if isinstance(taken, int) and 0 <= taken < len(node.branches):
            frame["branch"] = taken        # carrying on inside the branch it had taken
            self.block(node.branches[taken][1])
            return
        for k, (cond, body) in enumerate(node.branches):
            if cond is None or self.value(cond, node.line):
                frame["branch"] = k
                self.block(body)
                break

    def _do_break(self, node: Node, b: Bot, o: dict[str, Any]):
        raise _Break()

    def _do_call(self, node: Node, b: Bot, o: dict[str, Any]):
        self.call_depth += 1
        try:
            if self.call_depth > MAX_CALL_DEPTH:
                raise Stopped(_("line {n}: subs are nested more than {max} calls deep",
                                n=node.line, max=MAX_CALL_DEPTH))
            self.block(self.subs[node.args])
        finally:
            self.call_depth -= 1

    def _do_set(self, node: Node, b: Bot, o: dict[str, Any]):
        self.vars[o["name"]] = self.value(node.expr, node.line)

    def _do_stop_when(self, node: Node, b: Bot, o: dict[str, Any]):
        expr, line = node.expr, node.line
        if node not in self.watched:      # a stop_when in a loop must not pile up watchers
            self.watched.add(node)
            began: float | None = getattr(b, "started", None)
            start = began - self.minutes_before * 60.0 if began is not None else None   # carried on: the first part's
            # the stop time's next minute from when the line is reached - from the first part's
            # start for a run carried on (it was reached then; a hand-written script's line reached
            # later in a run waits for its own next minute)
            at: float | None = None
            if start is not None:
                at = end_time(expr, start, start if self.minutes_before else clock_for(b).time())
            if at is not None and _clock_rule(expr):
                # `clock == "04:30"` is true for one minute only: a run busy all through it (a wait,
                # logging back in, a game opened again, a pause) farmed on until the daily stop.
                # Past the first such time since the run began, it is over all the same
                clock = clock_for(b)
                b.watches.append((lambda: bool(self.value(expr, line)) or clock.time() >= at, o["message"]))
            else:
                b.watches.append((lambda: bool(self.value(expr, line)), o["message"]))
            if b.tally is not None and at is not None:   # the results' Time remaining counts down to it
                b.tally.deadline(at)

    def _do_stop(self, node: Node, b: Bot, o: dict[str, Any]):
        raise _End(node.args)

    def _do_sound_watch(self, node: Node, b: Bot, o: dict[str, Any]):
        try:
            sounds.watch(b.a, o["ids"])
        except AgentError as error:
            raise Stopped(str(error)) from error

    def _do_sound_mute(self, node: Node, b: Bot, o: dict[str, Any]):
        try:
            sounds.mute(b.a, o["ids"])
        except AgentError as error:
            raise Stopped(str(error)) from error

    def _do_sound_wait(self, node: Node, b: Bot, o: dict[str, Any]):
        self.vars.update(sound_received=False, sound_id=0, sound_x=0.0, sound_y=0.0,
                         sound_muted=False, sound_positioned=False)
        ids = sounds.IdSet.parse(o["ids"])
        listening = False
        try:
            # Establish the cursor before subscribing: historical requests never trigger a wait.
            cursor = sounds.events(b.a, 0)["now"]
            from . import listening as run_sounds
            # its own ids with the run's (the farm's, the team's): only its own end the wait
            status = sounds.listen(b.a, run_sounds.with_ours(b, ids.text))
            listening = True
            if not status["hooked"]:
                raise Stopped(_("Sound observer is unavailable: {reason}", reason=status["reason"]))
            deadline = clock_for(b).time() + o["timeout"]
            while True:
                b.check()
                batch = sounds.events(b.a, cursor)
                cursor = batch["now"]
                if batch["lost"]:
                    raise Stopped(_("Sound events were lost because the observer buffer filled. Start the wait again."))
                for event in batch["events"]:
                    if ids.has(event["id"]):
                        self.vars.update(sound_received=True, sound_id=event["id"],
                                         sound_x=event["x"] if event["x"] is not None else 0.0,
                                         sound_y=event["y"] if event["y"] is not None else 0.0,
                                         sound_muted=event["muted"],
                                         sound_positioned=event["x"] is not None and event["y"] is not None)
                        return
                remaining = deadline - clock_for(b).time()
                if remaining <= 0:
                    return
                b.sleep(min(0.1, remaining))
        except AgentError as error:
            raise Stopped(str(error)) from error
        finally:
            if listening:
                from . import listening as run_sounds
                try:
                    sounds.listen(b.a, run_sounds.wanted(b))    # the run's own sounds again, or "off"
                except (AgentError, OSError):
                    # Closing a failed link also releases its subscription on the agent.
                    b.log(_("Could not release sound listening; it is released when the connection closes."))

    def _do_travel(self, node: Node, b: Bot, o: dict[str, Any]):
        b.travel(o["map"], o.get("cell"))

    def _do_go(self, node: Node, b: Bot, o: dict[str, Any]):
        if o.get("dungeon"):
            e = dungeons.find(o["dungeon"])      # now: a circle learned since is included
            if e is None:
                raise Stopped(_("no known entrance for the dungeon {name}", name=o["dungeon"]))
            b.go_dungeon(e, o.get("talk", False))
        else:
            b.go(o["map"], o.get("cell"), o.get("npc"), o.get("talk", False))

    def _do_talk(self, node: Node, b: Bot, o: dict[str, Any]):
        if not b.talk(o["npc"], o.get("cell")):
            raise Stopped(_("couldn't talk to {npc}", npc=o["npc"]))

    def _do_follow(self, node: Node, b: Bot, o: dict[str, Any]):
        follow.follow(b, o["player"], o.get("follow"))

    def _do_come(self, node: Node, b: Bot, o: dict[str, Any]):
        answer = follow.come_to(b, o["player"], o.get("map"), o.get("cell"))
        b.log(answer)
        tell: Callable[[str], None] | None = getattr(b, "tell", None)   # a bot in the tests may have none
        if tell:
            tell(answer)

    def _do_survey(self, node: Node, b: Bot, o: dict[str, Any]):
        survey.survey(b, o["scope"])

    # The Tools menu's jobs (engine/activities.py): the same code triggers and schedules run
    def _do_activity(self, node: Node, b: Bot, o: dict[str, Any]):
        activities.run(b, node.kind, o.get("check", False), o)

    _do_gem_maker = _do_gem_breaker = _do_potion_maker = _do_enhance = _do_chores = _do_gather = _do_lose_xp = _do_activity
    _do_dungeon = _do_sell_collection = _do_bounties = _do_construction_scan = _do_activity

    def _do_triggers(self, node: Node, b: Bot, o: dict[str, Any]):
        pass                      # read when the script is (own_lists): nothing to do as it runs

    def _do_market(self, node: Node, b: Bot, o: dict[str, Any]):
        with stats.doing(b, "market"):
            market.run(b, o["what"], o)

    def _do_visit(self, node: Node, b: Bot, o: dict[str, Any]):
        w = o["what"]
        role = w.lower().replace(" ", "_")
        if knowledge.service_npcs(role):
            town.visit(b, role)
        else:
            town.visit(b, None, name=w)

    _do_sell_shards = _do_clean_bag = _do_update_shard_prices = _do_store_items = _do_restock = _do_activity

    def _do_keyboard_profile(self, node: Node, b: Bot, o: dict[str, Any]):
        keyboard.apply(b, o["name"])

    def _do_buy(self, node: Node, b: Bot, o: dict[str, Any]):
        town.buy(b, o["item"], o["count"])

    def _do_withdraw(self, node: Node, b: Bot, o: dict[str, Any]):
        town.withdraw(b, o["item"], o["count"])

    def _do_sell(self, node: Node, b: Bot, o: dict[str, Any]):
        town.sell(b, o["item"], o["count"])

    def _do_deposit(self, node: Node, b: Bot, o: dict[str, Any]):
        town.deposit(b, o["item"], o["count"])

    def _do_choose(self, node: Node, b: Bot, o: dict[str, Any]):
        if b.choose(o["pattern"]) is None:
            raise Stopped(_("no menu entry like \"{want}\"", want=o["pattern"]))

    def _do_walk(self, node: Node, b: Bot, o: dict[str, Any]):
        b.walk_to(o["cell"])

    def _do_key(self, node: Node, b: Bot, o: dict[str, Any]):
        b.key(o["key"], o["ms"])

    def _do_hold(self, node: Node, b: Bot, o: dict[str, Any]):
        b.hold(o["key"])
        try:
            b.sleep(o["secs"])
        finally:
            b.let_go(o["key"])

    def _do_wait(self, node: Node, b: Bot, o: dict[str, Any]):
        b.sleep(o["secs"])

    def _do_say(self, node: Node, b: Bot, o: dict[str, Any]):
        b.log(self._text(node, o), speak=True)

    def _text(self, node: Node, o: dict[str, Any]) -> str:
        """say's and chat's text, its {values} worked out now."""
        return "".join(str(self.value(p, node.line)) if not isinstance(p, str) else p for p in o["parts"])

    def _do_chat(self, node: Node, b: Bot, o: dict[str, Any]):
        """A line on a chat channel. One the game won't take is said in the log and the script goes
        on: the game takes any line for the team or the gang - with none, it goes nowhere - so a
        character in no gang (its side) sends none there, and says so."""
        room = int(o["room"])
        name = _(chat.CHANNELS[room])
        text = " ".join(self._text(node, o).split())
        if not text:
            return
        if len(text) > CHAT_MOST:
            text = text[:CHAT_MOST]
            b.log(_("line {n}: the chat line is cut to {most} characters", n=node.line, most=CHAT_MOST))
        # A loop with no wait would send many lines a second (the game may mute a character for
        # that): one line a room every CHAT_GAP seconds at most - the next waits its turn.
        last: dict[int, float] = self.__dict__.setdefault("chat_sent_at", {})
        wait = last.get(room, -CHAT_GAP) + CHAT_GAP - clock_for(b).time()
        if wait > 0:
            b.sleep(wait)
        last[room] = clock_for(b).time()
        if room == CHAT_ROOMS["gang"] and bosses.gangless(b, b.snap()):
            b.log(_("line {n}: not sent to {room}: the character is in no gang", n=node.line, room=name))
            return
        try:
            answer = b.a.send("say %d %s" % (room, text))
        except (AgentError, OSError) as e:
            answer = str(e)
        if answer == "sent":
            b.log(_("{room}: {line}", room=name, line=text))
        else:
            b.log(_("line {n}: not sent to {room}: {why}", n=node.line, room=name, why=bosses.refused_why(answer)))

    def _do_use(self, node: Node, b: Bot, o: dict[str, Any]):
        """An item from the bag; `used` says whether it went."""
        ok = bool(b.use_item(o["item"]))
        self.vars["used"] = ok
        if not ok:
            b.log(_("line {n}: couldn't use {item} (not in the bag, or the game didn't take it)",
                    n=node.line, item=o["item"]))

    def _do_wait_until(self, node: Node, b: Bot, o: dict[str, Any]):
        """Wait until the condition holds, looking WAIT_UNTIL_LOOK seconds apart, at most secs;
        `wait_met` says whether it came true. Stop and Pause work all along."""
        end = clock_for(b).time() + o["secs"]
        while True:
            b.check()
            if self.value(node.expr, node.line):
                self.vars["wait_met"] = True
                return
            left = end - clock_for(b).time()
            if left <= 0:
                self.vars["wait_met"] = False
                return
            b.sleep(min(WAIT_UNTIL_LOOK, left))

    def _do_heal(self, node: Node, b: Bot, o: dict[str, Any]):
        key, name = b.potion("hp")
        b.drink("hp", name, key)

    def _do_hearthstone(self, node: Node, b: Bot, o: dict[str, Any]):
        b.go_home()

    def _do_manage_team(self, node: Node, b: Bot, o: dict[str, Any]):
        teamlead.manage(b)          # the team set up, then managed until the run is stopped

    def _do_end_at(self, node: Node, b: Bot, o: dict[str, Any]):
        b.end_place = o["end"]

    def _do_keep_drop(self, node: Node, b: Bot, o: dict[str, Any]):
        b.drop_keep = o["minutes"]        # this run's, ahead of the character's (drop_time_check)

    def _do_keep_level(self, node: Node, b: Bot, o: dict[str, Any]):
        b.level_keep = o["xp"]            # this run's, ahead of the character's (level_check)

    def _do_farm(self, node: Node, b: Bot, o: dict[str, Any]):
        until = o.get("until")
        mid = best_map(b) if o.get("map") == BEST_MAP else (o.get("map") or b.map_id())
        saved = self._arrive(node)
        done_minutes = float(saved.get("farm_minutes", 0.0)) if saved else 0.0
        done_kills = int(saved.get("farm_kills", 0)) if saved else 0
        minutes, kills = float(o.get("minutes", 0) or 0), int(o.get("kills", 0) or 0)
        if (minutes and done_minutes >= minutes) or (kills and done_kills >= kills):
            b.log(_("not farming {map}: the interrupted run had already done it", map=atlas.label(mid)))
            return
        if until is not None and self.value(until, node.line):
            # Already done: no trip to it. A plan's spot whose share of the drop time is used up
            # (a run restarted later in the day) goes straight on to the next spot.
            self.skipped_farms += 1
            b.log(_("not farming {map}: {cond} is already true", map=atlas.label(mid), cond=o.get("until_text", "")))
            if self.skipped_farms >= SKIPPED_MOST:
                raise Stopped(_("every farm of the script is already done - stopping"))
            return
        self.skipped_farms = 0
        if saved:
            b.log(_("carrying on with {map} where it was: {minutes} minutes and {kills} kills done",
                    map=atlas.label(mid), minutes=int(done_minutes), kills=done_kills))
        frame = self.trail[-1]
        frame.update(_farm_at=clock_for(b).time(), _farm_kills=b.kills, _farm_m0=done_minutes, _farm_k0=done_kills)
        b.farm(mid, area=o.get("area"), minutes=minutes - done_minutes if minutes else 0,
               kills=kills - done_kills if kills else 0,
               only=o.get("only", ""), skip=o.get("skip", ""), patrol=o.get("patrol", "random"),
               avoid_elites=o.get("elites") == "avoid",
               until=(lambda: bool(self.value(until, node.line))) if until is not None else None,
               nogo=o.get("nogo") or (), points=o.get("points") or ())


def best_map(b: Bot) -> int:
    """`farm map=best`: the first of atlas.maps_for_level for the character's level as the sheet
    says now - an ordinary field before a dungeon or any other kind, the highest fitting monsters
    first. Stopped (said) when the level isn't known or no map fits it."""
    sheet = b.refresh_sheet() or {}
    level = sheet.get("level")
    if not isinstance(level, int) or level <= 0:
        raise Stopped(_("can't choose the best map: the character's level isn't known yet"), final=True)
    fits = atlas.maps_for_level(level)
    if not fits:
        raise Stopped(_("no farming map fits level {level}", level=level), final=True)
    mid, name = fits[0][0], fits[0][1]
    b.log(_("the best map for level {level}: {map}", level=level, map=name))
    return mid


class _Later:
    """A text or yes/no value only worked out if an expression reads it (boss, in_dungeon: each a
    read of the game); evaluate takes its value when the name is read."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn = fn

    def get(self) -> Any:
        return self.fn()


class _Lazy:
    """A value only worked out if a condition actually uses it (monsters needs a full read)."""

    def __init__(self, fn: Callable[[], int]) -> None:
        self.fn = fn

    def get(self) -> int:
        return self.fn()

    def _v(self) -> int:
        return self.fn()

    def __lt__(self, o: float) -> bool: return self._v() < o
    def __le__(self, o: float) -> bool: return self._v() <= o
    def __gt__(self, o: float) -> bool: return self._v() > o
    def __ge__(self, o: float) -> bool: return self._v() >= o
    def __eq__(self, o: object) -> bool: return self._v() == o
    def __ne__(self, o: object) -> bool: return self._v() != o
    def __bool__(self) -> bool: return bool(self._v())
    def __int__(self) -> int: return int(self._v())
    def __str__(self) -> str: return str(self._v())
    def __add__(self, o: float) -> float: return self._v() + o
    def __radd__(self, o: float) -> float: return o + self._v()
    def __sub__(self, o: float) -> float: return self._v() - o
    def __rsub__(self, o: float) -> float: return o - self._v()
    # the rest of what SCRIPTING.md promises for numbers: * / // %, a minus sign, abs() and round()
    def __mul__(self, o: float) -> float: return self._v() * o
    def __rmul__(self, o: float) -> float: return o * self._v()
    def __truediv__(self, o: float) -> float: return self._v() / o
    def __rtruediv__(self, o: float) -> float: return o / self._v()
    def __floordiv__(self, o: float) -> float: return self._v() // o
    def __rfloordiv__(self, o: float) -> float: return o // self._v()
    def __mod__(self, o: float) -> float: return self._v() % o
    def __rmod__(self, o: float) -> float: return o % self._v()
    def __neg__(self) -> int: return -self._v()
    def __pos__(self) -> int: return +self._v()
    def __abs__(self) -> int: return abs(self._v())
    def __round__(self, ndigits: int | None = None) -> float: return round(self._v(), ndigits)
    def __float__(self) -> float: return float(self._v())
    def __index__(self) -> int: return self._v()


def check(text: str) -> str | None:
    """None if the script reads correctly, else the error message."""
    try:
        parse(text)
        return None
    except ScriptError as e:
        return str(e)


def login_target(text: str) -> str | None:
    """The optional character named by a script's first `login` line."""
    main, _subs, _on_stop = parse(text)
    return main[0].opts["character"] if main and main[0].kind == "login" else None
