# Writing scripts: a guide

A **plan** covers ordinary farming: pick maps, say how long, add stop rules. A **script** is for
everything else. It's a short text, one command per line, that the bot follows from top to bottom.
You can loop, make decisions, visit shops between rounds, and react to what happens in the game.

You don't need to be a programmer. If you can write a shopping list, you can write a script.

This guide starts simple and builds up. Every example here can be copied into the script editor as
it is.


## 1. Your first script

1. Open the **Plans** tab and press **New script**.
2. Give it a name, for example `My first script`.
3. Replace the example text with these three lines:

        travel "Wolf Prairie"
        farm minutes=10
        hearthstone

4. Press **F5** to check it. "The script reads correctly" means the bot understood every line.
5. Press **Ctrl+S** to save, then Escape to close the editor.
6. On the **Bot** tab, choose `My first script (script)` in the Plan box and press **Start**.

The bot walks to Wolf Prairie, farms for ten minutes and goes home with the hearthstone. That's a
whole script.


## 2. The rules of writing

- **One command per line.** The first word is the command, the rest are its details.
- **`#` starts a note.** Everything after `#` on a line is for you; the bot ignores it.

        # farm the wolves before dinner
        farm minutes=30    # half an hour is enough

- **Names with spaces go in quotes:** `travel "Wolf Prairie"`. One-word names don't need them.
- **Upper or lower case doesn't matter** for commands: `Farm` and `farm` are the same.
- **Indenting is up to you.** Spaces at the start of a line are ignored, but they make blocks
  easier to read, so the examples use two spaces.
- **Blocks end with `end`.** Commands like `repeat`, `if` and `while` hold the lines under them
  until the matching `end`.


## 3. Going places

    travel <map>                 go to a map from anywhere
    travel <map> at <x>,<y>      go to a map and to a cell on it
    walk <x> <y>                 walk to a cell on the map you're on

`travel` works out the whole route by itself: walking, cable cars, airships and teleport circles.
You can give the map's name or its number:

    travel "Orcus Tribe"
    travel 5301
    travel "central plains" at 220,215

Some names are used by more than one map. If the bot picks the wrong one, use the number. The
plan editor's map list shows each map's number in brackets.

`go` is the walking assistant (Bot, Walk to, Ctrl+W, in the window). It takes the same route as
`travel`, but rides your fastest Swift fusion beast the whole way, in towns and through monster
ground alike, and never stops to fight. Potions still keep your HP up.

    go <map> [at <x>,<y>]              go to a map (and a cell on it)
    go <map> at <x>,<y> to <npc>       go to a map and walk up to an NPC standing near x,y
    go dungeon <name>                  walk up to a dungeon's entrance NPC
    go dungeon <name> talk             ...and open its menu there

    go dungeon "Nether Cave" talk
    go "Lifting Platform No. 1" at 10,75 to "Nether Cave"

The dungeons it knows are listed in the Walk to window, with the ones whose entrance hasn't been
seen in the game yet.


## 4. Farming

    farm                         farm here until something stops the bot

On its own, `farm` fights on the map you're on, using the map's whole monster area, until a stop
rule or the Stop key ends the run. You can add details, in any order:

    map=<map>                    go to this map first
    minutes=<N>                  stop farming after N minutes
    kills=<N>                    stop farming after N kills
    patrol=random|full|quick|short
                                 wander at random (the default), or walk a patrol loop: full
                                 passes close to every spot, quick most of them, short is the
                                 shortest lap that keeps nearly the whole map within 40 steps
                                 (best in mazes such as turkey cave). On a loop, monsters more
                                 than 40 walking steps away are left until the lap comes by.
    area=x1,y1,x2,y2             only fight inside this rectangle
    only=<names>                 only fight monsters whose names contain one of these
    skip=<names>                 never fight these
    elites=fight|avoid           stay out of elite zones with avoid
    until=<condition>            stop farming as soon as the condition is true (put it last)

Several names are separated with `|`, all inside quotes:

    farm map="Wolf Prairie" minutes=30 patrol=full
    farm kills=200 only="wolf|fox" elites=avoid
    farm map=5301 until=bag("Large Red Potion") < 20

`until=` takes everything after it as its condition, so it always goes at the end of the line.

When a `farm` line ends, the script goes on to the next line. Without minutes, kills or until, it
farms until the whole run is stopped.


## 5. Stopping well

    stop_when <condition> : <message>    from this line on, end the run when this becomes true
    stop <message>                       end the run now
    on_stop ... end                      what to do whenever the run ends
    hearthstone                          go home now
    end_at <where>                       where every end of the run goes (see below)
    keep_drop <minutes>                  leave this much of the day's drop time unused

`stop_when` is a rule that keeps watching, even in the middle of a fight. Put your stop rules at
the top, so they watch the whole script:

    stop_when bag("Large Red Potion") < 10 : out of red potions
    stop_when clock == "23:00" : bed time
    stop_when minutes >= 120 : two hours are enough

The message after `:` is written in the log and spoken, so you know why it stopped.

`end_at` says where the run ends, whatever ends it - your stop rules, the end of the script, the
drop time, a full bag, the last death, broken gear. Put it at the top:

    end_at start                         back where the run started (the default)
    end_at 1041 at 200,205               a map (name or number), and a spot on it
    end_at hearthstone                   home with the hearthstone
    end_at safe                          the nearest map where no monsters spawn
    end_at here                          stay

`start` ends on the nearest map without monsters instead when the run was started where monsters
spawn, and any way that fails ends there too.

`keep_drop` saves drop time for later: `keep_drop 60` ends the run while an hour of the day's
drop time is still left (plus the few minutes it keeps running after you leave the monsters). It
counts for this run only, ahead of the character's own "Drop time to save" (Bag and supplies,
Full bag); a plan has the same setting on its "When to stop" page.

`on_stop` says what to do at the end, however the end comes (a stop rule, `stop`, or the end of
the script). It does *not* run when you press Stop yourself; then the bot just lets go of every
key at once.

    on_stop
      hearthstone
      say finished with {kills} kills
    end


## 6. What the bot can read

These names can be used in conditions and in `say`:

| Name | What it is |
|---|---|
| `hp`, `maxhp`, `hp_pct` | your HP, your maximum, and HP as a percent |
| `mp`, `maxmp`, `mp_pct` | the same for MP |
| `level` | your level |
| `map`, `map_name` | the map's number and name |
| `x`, `y` | where you stand |
| `kills` | monsters killed in this run |
| `minutes` | minutes since the run started |
| `clock` | the time of day, like `"21:45"` |
| `monsters` | how many monsters are around you now |
| `arnebia_gained`, `shards_gained`, `xp_gained`, `levels_gained`, `contribution_gained` | what this run has earned so far (shards: every one picked up, thrown away or not) |

MP and level come from the server's answer to the own-HP request (sent without a key from agent 39) and your character sheet; until the bot has seen
them they read `-1`.

Functions:

| Function | What it gives |
|---|---|
| `bag("item name")` | how many of an item are in your bag |
| `random(a, b)` | a random whole number from a to b |
| `min(a, b)`, `max(a, b)` | the smaller or the larger |
| `abs(x)`, `round(x)`, `int(x)` | the usual maths helpers |

Comparisons are `<  <=  >  >=  ==  !=` (`==` means "is equal to", `!=` means "is not equal
to"), and you can combine them with `and`, `or` and `not`:

    stop_when hp_pct < 30 and monsters > 3 : too dangerous here


## 7. Decisions and loops

**if / elif / else** chooses what to do:

    if bag("Large Red Potion") < 50
      buy 200 "Large Red Potion"
    elif bag("Large Red Potion") < 100
      say potions are getting low: {bag("Large Red Potion")} left
    else
      say plenty of potions
    end

**repeat** does something again. `repeat 3` does it three times; `repeat` alone goes on until
something stops the run:

    repeat
      farm map="Wolf Prairie" minutes=20
      farm map="Orcus Tribe" minutes=20
    end

**while** goes on as long as a condition stays true:

    while kills < 500
      farm minutes=10
      say {kills} kills so far
    end

**break** leaves the loop you're in and carries on after its `end`:

    repeat
      farm minutes=15
      if clock >= "22:00"
        break
      end
    end
    hearthstone


## 8. Your own values

`set` stores a value under a name you choose; `say` writes to the log, and anything in `{ }` is
worked out first:

    set rounds = 0
    repeat
      farm minutes=15
      set rounds = rounds + 1
      say round {rounds} done, {kills} kills, HP {hp_pct}%
    end

Your names can be used anywhere the bot's own names can.


## 9. Blocks you can reuse

`sub` gives a group of lines a name; `call` runs it. It keeps long scripts tidy:

    sub refill
      travel "central plains"
      buy 200 "Large Red Potion"
      buy 200 "Large Blue Potion"
    end

    repeat
      if bag("Large Red Potion") < 30
        call refill
      end
      farm map="Wolf Prairie" minutes=30
    end


## 10. Towns, shops and the storehouse

    visit <kind or NPC name>     go to the nearest NPC of a kind and open its menu
    talk <NPC name>              walk up to an NPC and open its menu
    choose <text>                pick the menu entry that contains this text
    buy <count> <item>           buy from the nearest shop that sells it
    sell <item> [count]          sell to the NPC that buys it (all of them if no count)
    withdraw <count> <item>      take out of the storehouse
    deposit <item> [count]       put into the storehouse (all of them if no count)
    restock                      top up your supplies now, as set in Bag and supplies
    gem_maker                    run the Gem Maker as set up in Tools, Gem Maker
    gem_maker check              only look: walk there, read the menus and prices, change nothing
    enhance                      run the Equipment Enhancer as set up in Tools
    enhance check                only look
    chores                       the day's chores as set up in Tools, Daily chores (each once a game day)
    chores check                 only look
    chores only=bank,donation    just those: bank, bounty, pet_rebirth, donation, construction, secret_room
    gather                       fish, mine or collect herbs as set up in Tools, Gathering: the gang's
                                 area once a game day, then outside; fights back and sells the collection
    gather check                 only look: the skill, the gang area's entrance, the place it would use
    sell_collection              sell what gathering brought in to the Aquaculture, Ore and Herb Buyers
    sell_collection fish         just one buyer: fish, ore or herb
    dungeon                      the caves set up in Tools, Dungeons (Scorpion Cave, Spider cave), each
                                 once a game day: supplies first, then all 15 minutes inside
    dungeon check                only look: what is short, which caves it would go to

The kinds `visit` knows include: `storehouse`, `potions`, `grocer`, `bank`, `blacksmith`,
`forger`, `gem_craftsman`, `artificer`, `shard_market`, `fish_buyer`, `herb_buyer`, `ore_buyer`.

`buy`, `sell`, `withdraw` and `deposit` walk to the right NPC, pick the right entries and type the
number for you. For anything else, `talk` and `choose` let you drive any menu yourself:

    visit storehouse
    choose "item storage"

`choose` stops the run if the entry isn't there, so a script never presses on blindly.

To learn what the NPCs are, where they stand and what their menus say, from the game itself:

    survey cities                the towns and villages only
    survey all                   every map the game's data puts NPCs, a town, a dungeon entrance or a
                                 transport on - not the plain farming fields - looking only round them
    survey everything            every map you can walk out of, farming fields too (it can take days)
    survey <map>                 one map
    survey here                  the map you are on, even one you can't walk to (your gang's base, a dungeon)

A survey walks each map so every part of it comes into view, writes down every NPC (with the game's
own description of it) and every kind of monster (with its level), then opens
each NPC's menu and reads it, pressing only entries that show more information - never one that
buys, sells, takes, teleports, enters or confirms. An NPC or object that answers with a spoken line
instead of a menu ("Requirements not met") has that line saved too. Each map is saved to the `survey` folder as soon
as it is done, so stopping and starting again picks up where it left off. It goes from map to
nearest map. The day's farming stop (before the drop-time reset) doesn't end a survey, since it
farms nothing. It rides the Swift fusion beast the whole way, like the walking assistant, and never
stops to fight: it outruns what attacks it, and potions keep its HP up. A death sends it back to the
map it was on; use a character strong enough for the maps it visits.



## 11. Keys

    key <key>                press a key once
    key <key> <milliseconds> press and hold it that long
    hold <key> <seconds>     hold a key down
    wait <seconds>           do nothing for a while
    heal                     drink an HP potion now

Key names: letters, digits, `. , ; / space enter esc tab`, `f1` to `f12`, `up down left right`,
and combinations like `lctrl+alt+down`. Most scripts never need these: farming, potions, travel and
menus are all handled for you.


## 12. Complete examples

**Two maps in turn, with safe stop rules**

    # Wolves and orcs, until 11 at night or the potions run low.
    stop_when bag("Large Red Potion") < 15 : out of red potions
    stop_when clock == "23:00" : bed time

    on_stop
      hearthstone
    end

    repeat
      farm map="Wolf Prairie" minutes=30 patrol=full
      farm map="Orcus Tribe" minutes=30 elites=avoid
    end

**Farm, refill, and go back, all evening**

    sub refill
      buy 200 "Large Red Potion"
      buy 100 "Large Blue Potion"
    end

    stop_when clock == "22:30" : evening is over

    repeat
      if bag("Large Red Potion") < 40
        call refill
      end
      farm map=5301 until=bag("Large Red Potion") < 40
    end

**A kill target, with progress in the log**

    set goal = 1000
    while kills < goal
      farm map="Wolf Prairie" minutes=10
      say {kills} of {goal} kills, {arnebia_gained} arnebia so far
    end
    say goal reached
    hearthstone

**Only the monsters you want, in one corner of a map**

    farm map="central plains" area=180,150,240,210 only="boar" skip="Boar King" minutes=45

**Earn contribution, then stop**

    stop_when contribution_gained >= 5000 : 5000 contribution earned
    farm map="Orcus Tribe"


## 13. When something goes wrong

- **Press F5 often.** The check names the line and says what's wrong, for example
  `line 4: farm: unknown option "minuts"`. The cursor jumps to that line.
- **Every `repeat`, `if`, `while`, `sub` and `on_stop` needs its own `end`.** Most mistakes are a
  missing `end`.
- **Names need quotes** when they contain spaces: `travel "Wolf Prairie"`, not `travel Wolf Prairie`.
- **The Log tab tells you what the bot did,** line by line. The day's log is also kept in the
  `logs` folder.
- **To try something safely,** add `stop_when minutes >= 5 : test over` at the top.

A plan can also be your starting point: in the plan editor, **Save as script** turns the plan
into script lines you can change.


## Quick reference

    travel <map> [at x,y]           walk <x> <y>
    farm [map= minutes= kills= patrol= area= only= skip= elites= until=]
    visit <kind>   talk <npc>   choose <text>
    buy <n> <item>   sell <item> [n]   withdraw <n> <item>   deposit <item> [n]   restock
    gem_maker [check]   enhance [check]   chores [check] [only=...]   survey cities|all|everything|here|<map>
    gather [check]   sell_collection [fish|ore|herb|all]   dungeon [check]
    follow <player>   come <player> [<map> [at x,y]]
    key <key> [ms]   hold <key> <s>   wait <s>   heal   hearthstone
    say <text>   set <name> = <value>
    stop_when <condition> [: message]   stop [message]   on_stop ... end   keep_drop <minutes>
    repeat [N] ... end   while <condition> ... end   break
    if ... elif ... else ... end
    sub <name> ... end   call <name>
