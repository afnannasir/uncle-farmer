# Uncle Farmer user guide

A farming bot for Mist World. It needs NVDA set as the game's default speech.

## What it does

Uncle Farmer walks your character to a farming map, fights the monsters there, drinks potions when
HP gets low, and stops by itself when a rule you set is reached (time, kills, potions running low).
It reads the game directly, so it knows exact positions, HP, your bag and every monster around you.
It works with the game in the background, and it needs no drivers and no virtual machine.

Everything works with a screen reader. Important events are spoken through NVDA.

## Installing

1. Unzip this folder anywhere you like, for example your Documents. Keep everything inside it
   together.
2. Start **UncleFarmer.exe**. The first time, it offers to open Settings.
3. In Settings, Game tab, choose your Mist World folder (the one with mw.exe), or start the game
   first and press **Find the running game**. Press **Install**. (Once the agent is installed,
   Uncle Farmer finds the game folder by itself, even if the setting is lost.)
4. Restart Mist World. That's all.

The installer adds one line to the game's file mw.exe.vmoptions and keeps a copy of the original
as mw.exe.vmoptions.bak-unclefarmer. **Remove**, in the same place, takes the line out again. If you
move the Uncle Farmer folder, install the agent again so the line points to the new place.

## The main window

The main window has three tabs (Ctrl+1, Ctrl+2, Ctrl+3):

- **Bot:** your games, the plan to run, Start and Stop, and live status: what the bot is doing,
  where, HP, MP, your pet's HP and MP, the players near you (with how many steps away), the bag,
  the drop time and this run's kills.
- **Plans:** your plans and scripts, with a summary of the selected one.
- **Log:** everything the bot did, or only the important events.

Help, Quick guide (F1) explains everything step by step, and Help, Keyboard shortcuts lists every
key. The **Pause** key starts and stops the bot from anywhere, including while you are playing.
To hold a run without ending it, use the **Pause** button on the Bot tab (or F6): the bot lets go of
every key and your own Shift, Ctrl and Alt work in the game again, so you can play by hand. **Go
on** (the same button, or F6 again) carries on where it stopped.

The kill count is the game's own: every monster that dies with one of your hits on it counts,
including the ones your area skills kill and the ones another player finishes.

Once an hour while farming, the log says where the time went: searching, walking to monsters,
selecting, fighting, resting, town trips. Few kills an hour with a lot of searching means the spot
is crowded, or its movement setting suits it badly.

Everything the bot did is also written to the program's `logs` folder, one file per day, and a
crash keeps its traceback in `errors.log` beside it. Settings, Logging chooses how much goes into
those files and how big they may grow before the oldest are dropped; the Log tab always shows the
run's lines whatever is chosen there.

## Plans

A plan answers three questions, one tab each.

### Where to farm

One or more farming spots (map, movement, which monsters). With one spot it farms there until the
plan stops. With several it goes round them in turn, or through them once; each spot then says how
long before moving on.

Movement: wander at random, or walk a patrol loop. The short loop is the quickest lap that still
passes within 40 steps of nearly every spot, and the best choice in mazes such as turkey cave. On
any loop the bot goes after monsters within 40 walking steps and leaves the rest until the lap
brings it near them.

### When to stop

A time limit, a clock time, a kill count, an item running low. The drop time, a full bag, the last
death you allow, broken gear and the last potion used up stop it by themselves (character
settings).

### Where to end

Back where you started the bot (the default; if monsters spawn there, the nearest map without
them), a place you choose (or "where I stand now", or a city square), home with the hearthstone,
the nearest map without monsters, or where it is. Every end goes there.

Plans made with older versions open in the new form by themselves: a single step's minutes become
the plan's time limit, and they end back where the run started.

## The farming day

The game's drop time resets at 00:00 GMT (05:00 in Pakistan). Farming past that spends the next
day's drop time, so every run stops at a set time of your computer's clock, whatever the plan says
and whatever the bot is doing, and ends where its plan says. No run starts between that time and the
reset. The time is in File, Settings, Farming day; by default it is the reset minus 10 minutes
(04:50 in Pakistan), to leave time to get away from the monsters.

### After farming

The same tab says what happens when farming stops by itself: the daily stop, the plan's time, the
drop time used up, a full bag, the last death. Never when you press Stop, and never after a run
that didn't farm (the Gem Maker, the Enhancer, Walk to, come, follow). The choices:

- Do nothing.
- Close the game.
- Shut down the computer. A shutdown waits until every running bot has finished and its game is
  closed, then runs Windows' own forced shutdown (shutdown /s /f /t 60, so no program can hold it
  up) after a warning you can cancel: the bot says it, and a window asks "Cancel the shutdown?"
  (Windows' shutdown /a).

## Your character

The bot scans each character by itself (class, skills, keys, potions, fusion beasts, pets).
Character, Settings (Ctrl+P) holds the choices that are yours: how to fight, which potions, which
beast, which pets. Keys and timings are under Advanced.

Advanced, Skills lists every skill the scan found, with its key and what kind of skill it is. For
each one you choose:

- **The bot decides** (the default): the bot picks when to press it.
- **Never**: the bot never presses it, never puts it on an empty key, and never asks you to.
- **Only when HP is low**: kept for bad moments (under the shield and defensive marks on the Low HP
  tab).
- **Whenever it's ready**: pressed in fights as soon as its cooldown allows, before the bot's own
  choice. Use it for a skill the bot leaves alone, such as a stun.

"Skills not in this list" says what happens to a skill you learn or put on a key later. Set it to
Never, and set the skills you want to The bot decides, and the bot uses only the skills you chose.

The other Advanced tabs hold the finer settings: how far to chase monsters on a patrol loop, how
far to keep from bosses, when to rest, when the defensive skills, shields and heal skill come out,
the pet's potions and Rice Rolls, how to travel
(walking, cable cars, airships, travel items, VIP), and the keys for dialogs, discarding and the
Action menu.

**Bosses.** The bot knows the field bosses by name: Pig-Face Bat King, Man-wolf Caesar, Ice Queen
Los, Cat King Carter, Pharaoh Tar, Elven King Erth, Fire Lord, Whale Monster Bigger, Monkey King,
Barbarian Tyrant, Thunder Lord, Lich King, Meteor Queen Elsa, Dark Priest, Dracula and the rest of
that family, plain and Demonized (data\bosses.json). It never attacks one, nor anything within 40 steps of one, and when a boss
comes within 40 steps it walks to the spot furthest from it, then farms on. Change the 40, or turn
this off, in Advanced, Fighting. The game files don't say when a boss appears, so it reacts to the
bosses it sees.

Every character has its own settings. A character the bot has never seen gets them from its first
scan.

## The bag

Character, Bag and supplies (Ctrl+B):

- **Shards:** sort every shard into Throw away, Keep, Store or Sell. A shard you never sorted is
  kept. Throwing away happens right where you farm; storing and selling happen on a bag trip, so
  for those choose "Sell and store by these lists" on the Full bag page. To make gems, use Tools,
  Gem Maker.
- **Other items:** Keep, Store, Sell or Throw away.
- **Supplies:** which potions and food are refilled, and from where.
- **Full bag:** what happens when the bag is full, and stopping when the day's drop time runs out.
  Full means full: the bot farms until the load reaches its limit (or stops rising just under it
  while monsters keep dying), unless you set a lower mark there.

## Fighting and staying alive

The bot stands and fights, however many monsters are on it. It never turns its back on a pack
(they follow as fast as it walks and keep hitting). With a pack on it and HP going, it uses the
class's shields and big heals as well as the potions. Damage shields (Formless...) go up on the way
into a fight and on the way out, not only while hitting. A melee character takes a monster with few
others round it before one in the middle of a pack. With nothing on it but HP low, it rests before
the next fight. Only monsters that attack are fought on the way somewhere.

What counts as a monster is the game's own call, the same one its target key makes: townsfolk,
shops, a town's ducks and hens are NPCs, guards and other players' summons are neither, and none of
them is fought. Before every fight the bot checks the target key (V) is on enemies and puts it back
if not; to talk to an NPC it switches to NPCs.

### Rage, marks and combos

A Warrior or Orc, a Hunter and an Assassin press the accumulation-points key (T) every second and a
half in fights. The game answers with rage, marked targets or combos (then fusion power, and the
pet's amiability), so Execute, Whirlwind Slash or Battle Cry are pressed only with the rage they
need, and Marked Shot when two or more targets carry marks (three or more even for a single
target).

### Potions and deaths

When the last red or blue potion is used, the run stops and ends at its end place (Character,
Settings, Potions), unless a restock rule refills that potion; it warns when 20 are left.

After the last death you allow (Character, Settings, Fighting), it respawns in the city first and
only then stops, so the character is never left lying dead. Untick "Stop after too many deaths"
there and it never stops for deaths: it respawns and goes back every time.

### Your own skills

The bot reads which skills you put on which keys (both pages, any keys you chose) and what each one
does from the game's own skill texts, then looks after you with them:

- **Heal:** a Sorceress or Priest heals itself with its quick heal (Healing Wave, Heal) below 85%
  HP, in fights and between them; red potions are then only a backup under half HP.
- **Buffs:** long ones (Strong-Arm Aura, Battle Cry, Requiem... lasting a minute or more) are kept
  up all the time, cast again when they run out or when the game says one was taken off you; short
  ones (Bull's Eye, Scorch, Winter Horn...) open hard fights and fights against packs.
- **Debuffs:** the R key's answer says how many debuffs are on you; a class with a dispel (Soul
  Purify, Holy Light, Purification, Dispel Magic) removes them. A debuff the dispel can't remove
  (poison, say) is left alone until something changes.
- **Bad moments** (HP under half, or three or more monsters on you under 75%): first a shield that
  doesn't stop you fighting (Formless, Ignore the Pain, Summon Spirit, Ice Shield...), then a big
  heal cast at once (Sorceress totems, Priest Light of Miracles). Under 30% HP, a shield that stops
  attacks is fine too (Turtle Guardian); one that stops you moving (Ice Barrier, Soul of
  Redemption) only as the last thing under 25%.

These skills are cast standing still, never walking you to a monster.

### Combos

Many talents make one skill set up the next. The bot reads which talents you have ACTIVATED (each
class has two paths that exclude each other; a talent on the other path does nothing) and uses
their combos whenever the skills they name are on your keys. The run's log lists them at the start
("combos: ..."), and Character, Overview shows them after a scan. Some of them:

- **Cleave Combo:** Sweep Attack right after Cleave. **Powerful Leap Attack:** Leap Slash to open,
  then the next skill at once (the global cooldown is halved).
- **Ice Finger:** Aqua Jet, an Ice Arrow into it to condense the target, then Ice Lance (3x damage).
  Without any talent: Ice Lance right after Frost Nova freezes, and Ice Arrow instead of Magic Sphere
  (its Ice Spikes add to Ice Lance). **Freezing Cold:** Ice Storm on its shorter cooldown.
- **Enhanced Explosion:** Corrosion Spell on the target, then Skeleton Explosion, which always crits
  on it. **Divine Whip:** Mind Whip kept on the target, Divine Nova while it is.
- **Steady Hands:** Aimed Shot just before Rapid Arrows or Marked Shot. Ignition once Pyroblast and
  Blazing Storm built three fire elements.
- **On a critical hit** (Call of the Wilderness, Flame Combo, Crisp Wisdom, Wisdom, Dead Toxin
  Surge, Enrage, Blazing Combo): the skill whose cooldown the crit ended goes next. These need
  agent 28 or newer, the version that tells the bot about critical hits.

Untick "Use the combos of the activated talents" (Character, Settings, Fighting) to fight without
them.

### Empty keys

When a run starts, the skills above and the items the bot uses (the chosen potions, the toolbox,
the hearthstone) that are on no key are put on EMPTY keys: a skill key with nothing on it, a hotkey
1 to = with nothing on it, the game's way (Ctrl+Alt+key in the skills panel, Ctrl+number in the
backpack). A key with anything on it is never changed, not even a hotkey kept for a potion you ran
out of.

The same happens during a run: when the red or blue potions on a hotkey are used up (or the game
puts something else on that key) and the backpack still holds red or blue potions of any size, the
bot says so and puts them on an empty hotkey between fights. With no empty hotkey, items are used
from the backpack between fights. With no empty skill key, the bot can't cast the skill at all (the
game only casts skills from skill keys), so it says which ones it is missing and which of your keys
repeat a skill that is also on another key, for you to decide. Turn it off on the Fighting page.

### Repairs

At the game's first warning that gear is about to break, the bot repairs everything you wear with
one toolbox (Small up to level 60, Large up to 80, Alloy any level; the cheapest that covers your
level), never pliers. A toolbox on a hotkey (1 to =) is pressed at once, even in a fight; one only
in the bag is used as soon as the fight is over. Without a toolbox it says so; if something has
broken and can't be repaired, it goes somewhere safe and stops. Both are on the Fighting page of
Character, Settings.

## Tools

Both tools can use your own gang's craftsmen (gangs pay less), another gang's (type its name), or
the nearest city's; a gang's craftsmen stand in its base's Outer hall. They never spend below your
arnebia reserve, and have a **Check only** button that walks there, reads the real prices and
chances, says what it would do, and spends nothing.

### Gem Maker

Tools, Gem Maker (Ctrl+G) has two jobs:

- **Make a gem:** you choose which gem, its level and how many (or as many as it can). It makes them
  from the bag's gems and shards, and fetches more Level 1 shards itself from the storehouse or the
  Shard Broker, a bagful at a time.
- **Make the most of my bag:** every shard into gems, then every colour up as far as it goes.

### Equipment Enhancer

Tools, Equipment Enhancer (Ctrl+Shift+E) enhances your equipment up to the level you choose (+12 at
most), with protection runes. It makes its Enhance Crystals from crystal shards out of the
storehouse or bought at the Shard Broker: a stock of crystals before it starts, and a set number
more when they run out.

### Daily chores

Tools, Daily chores (Ctrl+Shift+D) does what a character can do once a game day (the game's day
starts at 00:00 GMT). Each chore is done once a day: the bot writes down what it did, so starting
it again doesn't donate twice.

- **Bank:** the deposit income and every Value Product card's daily income.
- **Hunting bounties** from the nearest city officer who pays them.
- **Pet rebirth:** the day's free rebirths of the pet you choose. Rebirths that cost diamonds are
  never used.
- **Gang:** the once-a-day donation you choose (10,000 or 20,000 arnebia, or 20 diamonds) and a
  number of construction builds a day (10,000 arnebia for 2,500 gang contribution each), never
  below your arnebia reserve. It builds wherever the gang has something to build: the inner hall's
  West and East encampment reinforced constructions, whatever a leader has started in the inner or
  outer hall (a Pharmacy room, a Supply Camp...), and the gang's highland if you tick it. It walks
  round each part to find them, and reads the Information Board for what is being built.
- **Secret room, sharing arnebia:** sharing works once in each half hour of the clock (from :00
  and from :30), 5 times a day, and both players get arnebia. So the bot doesn't stay: it goes in
  a little before the half hour (10:28, say), shares, shares again once 10:30 comes, and leaves.
  While farming it does that by itself: it leaves the farm a few minutes before a half hour whose
  share isn't made yet (10 minutes by default, the walk there included) and comes back, about once
  an hour, until the 5 are made. It shares with the players you name first, or anyone in the room.
  Practising there (an assistant kills for you) is a separate setting, 0 minutes by default: time
  inside comes out of the day's public-area drop time.

Check only walks to each NPC and reads the menus without collecting, spending or entering. Tick
"at the start of the first farming run" and the first farming run of each game day does the chores
first. In a script: `chores`, `chores check`, or `chores only=bank,donation`.

### Gathering

Tools, Gathering (Ctrl+Shift+F) fishes, mines or collects herbs: whichever of the three the
character has learned (a character learns one, at the gang's Guru Elder or Master Pepper). The
window says which skill the last scan found and its rank, from Newbie to Grand Master.

- **Gangs' areas first:** a gang's Lake, Mining Area or Herbal Garden gives more than the open
  world, the higher its level and saturation. On the Gangs tab, list the gangs to gather in, in
  order: your own gang ("Add our own gang") and any other gang by its name ("Add a gang by name",
  as the Gang Entrance's list shows it), for players who gather in a friend's or an allied gang.
  The bot goes to each in turn: into the gang's base (another gang is found through the Gang
  Entrance's "all gangs" list), talks to the entrance for your skill, reads its level, saturation
  and fee, and goes in, for a fee up to the limit you set and never below your arnebia reserve.
  Each gang's area once a game day. After half an hour the game puts you back outside and the bot
  goes on to the next gang. It sells what you carry before each one, since that half hour can't
  be left and taken up again.
- **Then outside:** at one of your own places, or one where the game has been seen letting players
  gather. To add a place, stand where you fish, mine or collect herbs by hand and press "Add where
  I stand": the bot stands on that very spot. With "The best for this character" it takes the
  highest-level area whose monsters are at most a few levels above you (5 by default). A place
  where the game never starts gathering is remembered and left alone until the skill's rank rises.
- **Starting and while gathering:** the bot does what you do: Right Ctrl on the skill in the bag's
  Profession skills panel, "Start", then "Confirm". From then on the game gathers by itself every
  two seconds until it is stopped or another skill or item is used. In a gang's area the bot
  leaves it to the game for the half hour. Outside, monsters come, so it looks after you the whole
  time: potions and heals as in any fight, fighting whatever attacks you, then back onto the spot
  and starting again.
- **The bag's load first:** before it sets out to a gang's area or a place, the bot looks at the
  bag's load. With less than 1,000 left (900 to 1,000 is safe; Selling tab) it says the bag is
  full, sells first if you let it, and only goes when there is room. No gang's area is entered
  without that room, since its half hour can't be left to sell.
- **Selling:** when the bag is full outside it walks to the nearest Aquaculture, Ore or Herb Buyer,
  sells, and comes back (or stops, if you choose). Everything the buyer lists is sold, except what
  you keep in Bag and supplies (an item set to Keep, or the number you keep of it). It sells once
  more at the end.
- **When it stops:** when Vitality runs out for the day (more costs diamonds, which the bot never
  spends), or after the minutes you set. Outside you can also keep some Vitality for later: "stop
  when Vitality is down to" (When to stop tab) reads the Vitality on the character sheet while it
  gathers, and doesn't set out at all when it is already that low.

Check only reads the gang area's entrance and says where it would gather. Tools, Sell the
collection now sells to all three buyers from wherever you are. In a script: `gather`,
`gather check`, `sell_collection` or `sell_collection fish`.

## Walking

Bot, Walk to a dungeon or map (Ctrl+W):

- **Dungeon entrance:** the dungeons whose entrance NPC the bot knows (a teleport circle or the NPC
  at the cave mouth). It walks up to the NPC and, if you like, opens its menu so you choose.
- **Map:** any map of the open world (the ones you can walk to and back from; closed maps and the
  maps inside dungeons aren't listed), or a cell on it.

Show the route says the way before you go. The whole way it rides your fastest Swift fusion beast,
in towns and through monster ground alike, and it doesn't stop to fight; potions still keep your HP
up. Pause stops it.

## Several games at once

Only one Uncle Farmer runs per Windows session: starting it again brings the open window forward
instead of opening a second one. While it runs, it keeps your settings, plans, profiles, scripts,
message files and results open, so Windows refuses to delete or move them; close Uncle Farmer first
if you want to clean those files by hand.

Bot, Open another game window (Ctrl+O), or just start Mist World again. The agent lets every copy
run, however many you open (each needs about 1 GB of memory). Each game window is named after its
character, such as "Mist World - YourName".

Choose a game in the Games list, choose its plan and press Start, then do the same for the next
game. Inside a game window, Pause starts and stops that game's bot. Bot, Stop every bot
(Ctrl+Shift+S) stops them all.

## Results

Bot, Results (Ctrl+R) shows what each character earned farming: time and kills, contribution,
experience and levels, arnebia, every shard picked up (with its Shard Broker value, and how many of
it were thrown away, sold or put into the storehouse), other items picked up, what was bought or
taken out of the storehouse, and the potions used. It covers the current run, today, the last 7 or
30 days, or all time.

Only farming counts there. Show can switch to the Gem Maker, Equipment Enhancer and chores instead:
what each of them made, used and cost, kept apart because shards made into gems are not loot. Those
jobs are kept apart inside a farming run too (chores before farming, `gem_maker` in a script).
While a run is paused nothing is counted: what you kill or pick up by hand is yours, not the bot's.

It also shows the fight itself, from the game's own hit reports: damage given and received, hits
and misses, critical hits and defence breaks (with the best and the last of each), the best normal
hit, the last monster killed, and the monsters that hurt you most. Anything the game did not report
is shown as `null`. These numbers need agent 28 or newer, like the crit-timed combos.

Shift+Pause speaks a one-line summary of the game in front, from anywhere. When a run ends one short
line is spoken; Settings can add a line every few minutes, or turn it off. The records are in the
results folder, one file per character.

## The game's own voice

While a bot runs, the game's speech is muted so you can use the computer (sounds still play): every
line, the combat announcer's damage numbers too, whichever voice the game uses (screen reader, SAPI
or its own voices). Settings chooses: while a bot runs, always, or never.

By hand, Ctrl+M mutes the game selected in the Games list and Ctrl+U lets it talk again, each game
on its own (the Bot menu also has Mute every game and Unmute every game). A run that ends keeps a
game muted if you muted it.

## Quick phrases and teammates

Quick phrases (Shift+1 to Shift+=, set up with Shift+Backspace): each character has its own. A
phrase starting with a code does something instead of chatting (see the game's help, "Quick Phrase
Controls"):

| Code | What it does |
| --- | --- |
| /6 | targets the next teammate on this map |
| /5 | targets what is attacking you |
| /4 | targets what is attacking your target |
| /3 | targets what your target attacks |
| /7, /8 | target the weakest teammate within 20 or 30 yards |
| /0, /1, /2 | set or cancel the team's navigation target |

At the start of a run the log lists what each of the character's phrases does (never the chat lines
themselves), and Character, Scan shows them. Codes the character has on no phrase (/6, /5, /4, and
/7 for a Priest, Sorceress or Witcher) go on EMPTY phrase slots only; a phrase with anything in it
is never changed (profile setting fill_empty_phrases). This needs the game restarted once so it
loads the new agent.

Finding and reaching teammates: the bot presses the /6 phrase to learn where a teammate it can't
see is on the map (the game answers with their position), and Shift+P teleports it to the team
leader: only on the same map, from further than 20 yards, once per about 50 seconds. `come` and
`follow` use both when the player they go to leads the team.

## Chat

Bot, Chat (Ctrl+T) reads and writes the game's chat while the bot farms, without the game's keys:
what you send goes through the game itself, as if typed there, so the bot's keys and your chat
never get in each other's way. Two tabs, as in the game (Ctrl+Tab moves between them):

**Channels**
- **Channel:** All, Team, Gang, Nearby, Map, Global, International, Private channel (the channel
  you joined in the game) and Notices (loot and events). Ctrl+1 to Ctrl+9 jump straight to one. A
  channel with new messages says how many.
- **Messages:** that channel's messages, newest last.
- **Message:** Enter sends it to the channel; the label says where it goes. In All it goes to the
  channel of the selected message, so you can answer whatever you are reading.

**Private chats** (private messages, one player to another)
- **People:** only the people you have talked with, the latest first, with how many new messages.
- **Messages:** your conversation with the person picked, both sides ("you: ..." for yours, also
  the ones you typed in the game itself).
- **Message:** Enter sends it to that person.
- **New chat with:** type a name or the player's id and press Enter, or pick one from its list (Alt+Down
  Arrow): the people you have talked with and the players in sight. A name works for anyone in sight
  or anyone you have messaged with this session; an id works for anyone.

**Voice messages.** Both tabs have the same two buttons beside Send. A voice message shows as "voice
message" in Messages; Play (Alt+Y), or Enter on it, plays it. Record voice (Alt+V) records from the
microphone the game uses; press it again (it now reads "Stop and send voice") to send what you said,
or press Escape to throw it away. The game takes voice messages only on Team and Gang and in private
chats, so on other channels the button says so.

It sounds better than the game's own recording: the low hum and the silence before and after are
taken out, a quiet or loud microphone comes out at an even level, and it is sent at twice the game's
quality, in the game's own sound format - so other players hear it in their normal game. The game
takes at most 20 seconds in one voice message, so a longer recording goes as several, of about the
same length (45 seconds: three of 15), one after the other, the way a long text goes in parts. A
recording stops by itself after 5 minutes. Voice needs agent 42: restart the game once after
updating.

**Settings**
- **Read new messages:** only while the Chat window is open (to start with), always, or never; and
  which channels (Team, Gang and Nearby to start with).
- **Tones:** a private message to you plays a tone - one for your trusted players (the names and
  ids in Private messages), another for everyone else, so you know who it is before you look. Pick
  "No tone" for either. Private messages are read out as set in Private messages.
- **Times:** show the time after each message or not, with a 24-hour or 12-hour clock.

**Sending, and the game's limits.** The game takes one line every 2 seconds (3 on Global) - for
every channel and private message together - and drops a line sent sooner. So everything Uncle
Farmer sends (the Chat window, the auto replies, answers to trusted players) waits its turn as the
game allows, also after lines you type in the game itself, and a line the game dropped anyway is
sent again. A message longer than the game's 200 characters goes in parts, cut at a space, one after
the other.

Nothing is read out and no tone plays while that game's own window is in front.

Chat is never saved: it is kept in memory while Uncle Farmer runs, and gone when you close it.

## Private messages

Bot, Private messages (Ctrl+Shift+M). While a bot runs, strangers who message the character get a
few lines from your reply list at once (never the same line twice), then one goodbye line and
silence for an hour. Edit the lines there; a few polite ones are there to start with. Write {name}
in a line to put the player's name there ("Hi {name}, I'm away"); with "Call them by their name"
ticked, a line without {name} starts with their name ("Kim, I'm busy right now").

Trusted players can be listed by name or by the game's id, mixed ("Kim, 2001"): either works for
commands, team invites and being read out.

While the game's own window is in front, you are in the game and read your messages there: Uncle
Farmer doesn't read them out or play a tone (they still show in its log and Chat window), and sends
no away note. Trusted players' commands still work then unless you untick "Take their commands while
the game window is in front". A trusted player never gets the strangers' replies.

Trusted players can send a command as the whole message: help, status, where, players (who is
near, nearest first; also "who"), pet (its HP and MP), results, today, bag,
drop, pause, resume, stop, start [plan], home, come, follow, unfollow, buff, add (take their team
application), leader (make them team leader), leave (also "quit" or "quit team"), mute, unmute,
quit game (closes the game). "help" answers with the list; "help" and a command's name ("help
follow") answers with what that command does. Their team invites are accepted; anyone else's are
declined with Escape while a bot runs.

### come

Walk up to them. If they are one of the characters running in this Uncle Farmer, it knows where
they are and travels there; otherwise it looks where any of your games last saw them (up to 10
minutes ago).

Best: press E in the game (your location), copy it with Shift+C and send "come" followed by what
you copied. The bot reads the map and the x, y from it and walks to that spot. "come" followed by a
map's name works too. A farming run stops there, beside them, and waits; while following them it
just comes closer and keeps following.

### follow

Stay with them: catch up when they move off, fight what attacks, help with the monsters near them
that they are fighting, and go along when they change map (anywhere for our own characters, through
the exit they took for anyone else). A run that was going stops first. After a minute without any
sign of them it stops and says so.

While following, it casts no buffs (heals, shields and dispels still go). Send "buff" to have it
keep its buffs up for the rest of this follow, and "buff off" to stop again. Each new "follow"
starts with buffs off.

### unfollow

Stop following.

## Updates

When it starts, Uncle Farmer asks its GitHub releases page whether there is a newer version, and
offers it with what changed; Help, Check for updates asks at any time. Say yes and it downloads the
release, checks it, and restarts as the new version. The request carries nothing about you or your
game. Settings, "Look for a new version when Uncle Farmer starts" turns the check off; then it never
goes online.

You can also update by hand: close Uncle Farmer and the games, unpack a newer
UncleFarmer-<version>.zip over this folder and let it replace the files. Either way your
characters, plans, scripts, settings and logs stay as they are, since a release never carries
them. "What's new.txt" in the folder says what changed.

## The game in another language

Uncle Farmer works with any of the game's language packs (language\lang.txt), not only English.
The game translates names and menus as they arrive; the bot reads the same language files the game
does and turns what it sees back into English, so it finds the same potions, skills, monsters,
pets and menus. Whatever a language pack leaves untranslated is English already. Players' names are
never changed. Pick the language in the game as usual; after changing it, restart the game.

## Things to know

- If the bot says it can't find the game: other tools that clean the game's settings (such as
  mwMod) remove the agent line. Open File, Settings and press Install again, then restart the game.
- Your character settings, plans, scripts and logs are stored in this folder (profiles, plans,
  scripts, logs). Copy them to keep a backup.
- Translations: copy `lang\unclefarmer.pot` to `lang\<language code>.po`, fill in the `msgstr`
  lines and run `python -m unclefarmer --strings` to compile it. It then appears under Language in
  Settings.
- Urdu is provided as Roman Urdu in `lang\ur.po`: write it in Latin letters, not Nastaliq or other
  Arabic script. Uncle Farmer keeps wx in its English, left-to-right mode for it.
