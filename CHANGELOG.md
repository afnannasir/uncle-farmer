# What's new

## Not released yet

**Telegram group**

- Help, Join the Telegram group opens the players' Telegram group, where you can talk with other
  players and the developer. The Contact the developer window has a button for it too.

**Help**

- Help, Commands for trusted players and Script commands (Help menu and the script editor) are no
  longer empty in the built app. The build stripped the texts they show; it keeps them now, and
  the build's self-test stops a build that lacks them.

**Pets**

- The bot turns on the monsters next to your pet as soon as the pet loses 2% of its HP within 3
  seconds (about one hit), instead of 8%: it no longer lets the pet take a beating before it
  defends it.

**Group skills**

- Area skills are chosen by damage: each one counts once for every monster it would hit, and goes
  whenever that beats the best single hit. Out in the field they waited for three monsters close
  together (two in a cave), so casters hardly used them there: a Witcher only fired Shadow Arrow.
  Now two monsters are usually enough, and one when the area skill hits hardest.
- Area skills are weighed against each other by damage too, not by how many targets they could
  take: an Elf in a pack no longer casts only the 3-second Blazing Storm and now uses Fire Surge
  whenever it is ready. Area skills whose text gives no damage (Blizzard, Icicle) still wait for a
  pack.

**After farming**

- Character, Settings, Travel and pets: "Automatically sell shards after farming" and
  "Automatically clean the bag after farming", each on its own (both off). Ticked, Sell shards now
  and Clean the bag now run by themselves, one after the other, when a farming run finishes by
  itself - never after Stop. A failure in one doesn't keep the other from running, and the Tools
  menu still runs both by hand.

**Gem Maker**

- A lucky run where no gem breaks no longer keeps the Gem Maker going round. A combine that works
  gives no shard back, so the boost shard is only asked for while the bag holds one, and a combine
  the game didn't carry out (asking for a shard that wasn't there) no longer counts as a failed
  try: the Gem Maker said it had made progress, went back to the craftsman and tried the same thing
  again, up to 40 times ("make the most of my bag": 12). Now a try or a round that changed nothing
  in the bag ends it, and the log says why. When gems break, it works as before.

**Travel**

- Temporary: no trip goes to Polson Wetland, or through it to the Underground Water Cave or the
  sailing port, unless the Poison Immunity talent is activated (its poisoned ground hurts
  otherwise). The run stops before anything moves and the log says why. Every other trip is as
  before.

## 1.0.0

**Repairs**

- Sell shards, Update shard prices and Clean the bag now no longer repair gear that is fine. The
  game keeps an item's last "about to break" warning for as long as it is open, and each new run
  took the one an earlier run had already repaired for a new one, spending a toolbox. Only
  warnings that come during the run count now; gear the game shows as broken is still repaired.

**Fusion beasts**

- A fusion beast goes on only after 6 seconds without an attack on you or your pet (any hit or fall
  in HP starts the 6 seconds again). Getting off a Swift beast for a fight is never held back.
- A beast the game refuses is tried again after a minute, then two, four... up to ten minutes,
  instead of every minute, and the refusal is logged once. Pressing stops as soon as an attack
  starts, so a fight is never kept waiting.

**Pets**

- When your pet's HP falls fast, the monsters right next to it are fought first, even when the
  game didn't report who hit it, until its HP has stopped falling for 6 seconds.

## 0.9.1

**Travel**

- Routes leave out a cable car or airship whose fare is more than the arnebia in the bag (VIP rides
  are free), instead of walking to the NPC and being turned away. With no arnebia reading, every
  ride is still planned.
- With "Use airships" off, the nearest shop or craftsman is judged without airships too, as the
  trip there is made.
- A transport entry in the data that is missing its map or landing is left out instead of stopping
  the route plan.

**Chat**

- Agent 54: a chat line no longer shows a stray letter or number before the sender's name
  ("2magoestelar"). The agent read part of the sender's id into the name for about one player in
  four, whoever they were; it now reads the name where the game itself does. Names that start
  with a number keep it. Uncle Farmer offers the new agent at start (close your games first).

**Menus**

- Agent 55: every NPC menu's entry is chosen where it is, in one step, instead of a walk down the
  list one key at a time (each press read the menu again and made the game speak the entry). The
  agent first checks the menu is still the one the bot read; if it isn't, nothing is chosen and the
  bot walks to the entry with the keys as before.
- Walking with the keys goes the shorter way round: the game's menus wrap, so an entry near the
  bottom is reached by going up past the top. Across a 30-entry menu that's half the presses.
- A shop or Shard Market item the bot has found once is picked by its id after that, without
  reading the list again: selling a shard in three lots reads the market's list once instead of
  eight times.

**Updates**

- Settings, Updates, Release channel: **Master** (stable releases, the default) or **Dev**
  (development builds of the dev branch, made by GitHub for every change: the newest changes, and
  they can break). The choice is kept after a restart, and each channel only offers its own builds.
- The update log is now `logs/update.log`, with the other logs, and says more: each check, the
  download, unpacking, installing, cleaning and what went wrong. The old one moves there by itself.
- The `update` folder is emptied and removed after every update, failed or cancelled ones too, and
  old downloads no longer pile up in it. It is never cleaned while an update is using it, and
  nothing outside it is ever deleted.

## 0.9.0

**Schedules**

- Bot, Schedules (Ctrl+Shift+H): start a plan or script for a character at a set time, once,
  every day or on chosen days. Each time runs once; a time missed while Uncle Farmer was closed
  is not run later; a busy game waits up to 15 minutes. History says what happened each time.

**Results**

- Arnebia spent on the items used up: each item's unit price, how many were used, its cost and the
  total. Shop prices are the shop's fixed ones; shard prices are marked as estimates.
- A run saved twice is counted once.

**Help**

- Help, Contact the developer: the address to write to for questions, ideas or donations, with
  buttons to write an email or copy it.

**Shards and shops**

- Tools, Update shard prices: reads the nearest Shard Broker's prices into the price list. Only
  prices that check out are kept; the rest stay as they were. Script command `update_shard_prices`.
- Agent 53: the Shard Broker's list, its buy and sell menu and a potion seller's list are chosen in
  by their verified rows, after checking the menu, the item and buy or sell, instead of a key per
  row. A menu that doesn't match is chosen in by its text as before; a buy or sell menu for the
  wrong shard is never answered.

## 0.8.1

**Gang base, gem making and shards**

- Leaving a gang base: the bot walks out through the gang base's own exit (the south edge of the
  outer hall). It used to look for a "leave" entry in the Action menu, which a gang base doesn't
  have, so every trip out of the gang base stopped with "no way from ...".
- From inside the gang base, the nearest storehouse, market or craftsman is chosen from the city
  you came in by, not always Mario Basin.
- Shards are sold at the Shard Broker of the city you are in. Mario Basin and Holy Blood City's
  brokers were missing from the list, and central plains' and Tulip City's were skipped because
  of the counter in front of them.
- From the inner hall, which is walled off from the exit, the bot says so instead of "no way".

**Tools**

- Sell shards now: sells the shards the bag manager says to sell, at this city's Shard Broker
  or the nearest one.
- Clean the bag now: shows your throw-away list, asks, then throws away only what is on it,
  keeping the number each rule keeps.
- Both are script commands too: `sell_shards` and `clean_bag`.

## 0.8.0

**Travel**

- Airships: after boarding, the bot now waits on the airship until the game lands it, instead of
  trying to walk off the airship every few seconds. It then plans the rest of the way from where
  it landed. A ride that never lands in 3 minutes stops the run instead of walking about.
- Airship and cable car administrators: the target key switches to NPCs before talking to them,
  and goes back to the mode you had before.
- NPCs and other players standing in the way are walked round from the start.
- Attacked while travelling: the bot stops, fights on foot (no fusion beast while it is
  attacked), then walks on to the same place.
- Long straight walks are no longer called "stuck" after 60 seconds.

**Pets and fights**

- Priest and Sorceress: the heal now really reaches the pet. It used to be pressed a moment too
  early, so the game dropped it and pet potions were used instead.
- A monster attacking your pet is fought first.
- A monster killed by your pet or another player gets no more skills cast at it.
- A monster the game won't let you select (inside rock, behind a wall) is left alone longer each
  time, instead of being walked back to every 10 seconds.

**Restocking**

- "Refill when fewer than 30" now means 30 or fewer. Having 199 of 200 no longer sends the bot
  to town, and nothing is topped up at the start of a run unless it is down to its minimum.
- A restock trip does everything in one town, the one you are in if it can, and never goes on to
  another town for a shop.
- A restock that can't get anything (no arnebia, the storehouse is out) is said once and not tried
  again every 30 seconds.
- After the bag trip the log says "back to ... after the bag trip" instead of "died or moved?".

**Scripts, settings and results**

- `monsters` in a script can be multiplied, divided, made negative, and used with abs() and
  round(). Numbers like nan or 1e999 are a script error instead of a crash.
- Scripts, settings and results files saved by Notepad (with a byte order mark) are read
  correctly instead of being reset or losing their first line.
- A broken results line no longer spoils a whole day's or week's report.

## 0.7.2

**Updates**

- The downloaded Uncle Farmer now knows it is the downloaded copy. 0.7.0 and 0.7.1 took
  themselves for a copy run from the source code: they never looked for a new version at start,
  and Update said "update it with git pull". This version looks at start and installs updates
  again. Copies of 0.7.0 and 0.7.1 can't find it by themselves: download it once by hand and
  unpack it over your Uncle Farmer folder. Updates after this one arrive by themselves.
- bootup.log's first line now says "packaged build" for the downloaded copy.

## 0.7.1

**Logs**

- Two new files in the `logs` folder say how each start and each close of Uncle Farmer went:
  `bootup.log` (settings, language, the window, the hotkeys, the update check, the agent, then
  "ready") and `teardown.log` (stopping the bots, the message and chat readers, the hotkeys and
  the timer, then "closed"). A step that fails is there with its whole error; a start that died
  half way says at which step, even when the window never opened. A bot that doesn't end within
  5 seconds of closing is noted there.
- Closing now also stops each game's chat reader, and a step of the close that fails no longer
  keeps the window from closing.

## 0.7.0

**Updates and the agent**

- Uncle Farmer looks for new versions again (at start, and Help, Check for updates), on the
  project's GitHub releases. It had stopped looking when the old address went away.
- An update no longer stops at "close every Mist World window first": it asks, and on OK closes
  the games itself, then updates. Start the games again afterwards.
- When the agent needs installing again, Uncle Farmer says why when it starts. That happens when
  another tool removed it from the game's settings, when the game loads another Uncle Farmer's
  agent, or when an open game started without this version's agent. Press OK: every Mist World
  window closes (through its own close button, or ended after 20 seconds) and the agent is
  installed again. Settings, Install offers the same for games that are open.

**Voice messages**

- With two games open, the game's auto-play no longer turns every voice message slow and deep in
  one of them. Both games save a message to the same file, so one could read it while the other
  was rewriting it; the game's player then opened its next sound at the wrong speed and kept it
  until the game closed. The agent now has the player open each message at its own rate and skip
  one it can't play. Restart the games once for the new agent.
- If you liked the deep voices, untick "Fix the speed of voice messages the game plays by
  itself" (Settings, Speech and language): the games play as before, right away.

**Pets**

- Priests' pets get their Pet Rice Rolls. For a Priest the game's points key (T) sends one more
  byte than for other classes, so the agent never took the answer as ours, never learned the
  pet's amiability, and never fed it. The new agent reads it (restart the games once); until
  then, the pet's description is read again every few minutes instead.
- A Priest or Sorceress heals the pet in fights too. The heal was only tried while the global
  cooldown was still running, so in a fight it never went off and the pet lived on potions. Now
  it takes the place of an attack when the next press is due and the pet is under its mark.
- The pet's heal is never given up for the whole run any more. If it doesn't raise the pet's HP
  3 times running, pet potions take over for 5 minutes, then the heal is tried again. Whatever
  keeps it from going off is said once in the log: the game wouldn't select the pet (with the
  game's reason), the agent can't select in this game, or the heal's attack page couldn't be
  turned to.
- The same for teammates while following: heals that raise nobody's HP rest 5 minutes and are
  tried again (no more "no more teammate heals this run"), the reasons are said once, and
  teammates are healed in fights too, at the moment a press is due.

**Dungeons**

- One cave a day per character, as the game allows: Tools, Dungeons now has one choice, a cave or
  "The highest cave my level lets in" (the default), instead of a list to tick. A character that
  has had its cave today isn't sent to another. Old settings keep the first cave that was ticked.
  The caves at levels 80, 90 and 100 will join the same list.
- Faster from one kill to the next (a Hunter's Scorpion Cave: 380 kills on 10-29, about half the
  casts its cooldown allows). A fight now ends the moment the game's report of our cast shows the
  monster at 0 HP, instead of at the next look up to 0.4 s later. A monster shot dead on the way
  ends the walk to it at once. (The short stand after each shot that players see is the game's:
  it holds a character still for about 0.7 s after every cast, keys held or not.)
- In a dungeon a monster the game won't let us hit is left after two refused tries instead of
  three: in a Scorpion Cave visit such monsters cost up to 5 s each.
- Area skills, for every class, count what they would really hit, by the shape their own text
  gives: an arc in front (Multi Shot, Rapid Arrows, Ice Storm, Ice Orb, Icicle, Fire Surge, Blazing
  Storm, Holy Light Wave, Fan of Knives), straight ahead (Sweep Attack, Flesh Cleave, Rampage
  Hack), a ring round you (Whirlwind Slash, Blade Fury Dance, Frost Nova, Divine Nova, Dead Toxin)
  or an area where they land (Blizzard, Skeleton Explosion). Before, every one counted only the
  monsters round the target: in that cave run Multi Shot went off 37 times of the ~150 its
  cooldown allowed.
- Every class that fights from afar (Hunter, Mage, Elf, Priest, Sorceress) shoots on the way with
  instant skills only: a channel such as Pyroblast or Blazing Storm breaks as soon as it walks on.
  The quicker move to the next monster after a kill works for every class.

**Fighting** (from a contributor's changes)

- A skill set to Never is no longer cast behind your back: when the skill picker finds nothing, the
  bot presses the strongest attack you allow that is off its cooldown (or waits), instead of the
  profile's attack keys, which cast whatever sits on them.
- A panic shield that works outside the global cooldown (Concentrated Will, Summon Spirit) comes out
  at once instead of waiting for the cooldown to end.
- A heal or buff the game refuses (no mana, no rage) no longer gets the monster written off as one
  the game won't let the bot hit.
- When the skill page can't be turned, the fallback uses the strongest skill of the same kind on
  the page that is up, not simply the first one on it.

**Scripts**

- `dungeon <cave>` goes to that cave (`dungeon Spider cave`), one a day; Start in Tools, Dungeons
  names the chosen cave in its script, so a stale profile can't send the run elsewhere.

**For developers**

- CI/CD: `master` (production) and `dev` (development). Every pull request into either and every
  push to them runs, at the same time and each once, the static checks (ruff, pyright), the tests
  with their coverage floors, pip-audit of `uv.lock` and the Java agent's build and tests, on
  Windows with Python 3.14; a push to `dev` or `master` is then built into the Windows app, only once every check
  passed, and uploaded as an Actions artifact (`UncleFarmer-dev-<commit>` or
  `UncleFarmer-master-<version>-<commit>`) with its SHA-256 and `build-info.json`. See
  CONTRIBUTING.md, "CI/CD".
- The built app checks itself without a window (`UncleFarmer.exe --self-test report.json`), and a
  build stops if its folder or zip lacks anything it must hold.
- Python 3.14 is the only supported Python (`requires-python = ">=3.14"`); 3.11 is no longer
  tested. The build keeps Nuitka's compile cache between runs, so a build that changed a few modules
  compiles only those instead of all of them.
- `requirements.txt` and `requirements-dev.txt` for running from source with pip: the exact pins
  of `uv.lock`, with hashes, made by `export_requirements.py`; a test fails when they fall behind
  the lock.
- `check.py --only ruff,pyright` (or `--only pytest`, `--only agent`) runs just those steps.
- `build_exe.py` no longer falls back on a UPX folder on one PC: UPX comes from `UPX_DIR` or PATH,
  else the build stops and says so.

**Restocking**

- Potions (and grocer goods) are bought at the nearest shop that sells them, not always in Giant
  Rock City. Only Apothecary Phillips's menu was known, so farming Flaming Mountain meant a trip
  past Holy Blood City and Butterfly Valley to Giant Rock City and back. Now every town's potion
  seller counts, nearest by travel first; one whose menu turns out not to list the item is
  remembered and the next nearest tried, and what each shop stocks is learned
  (profiles\_learned_shops.json).

**Runs**

- The dungeon, daily-chores and gathering settings no longer share the defaults' own lists: a
  change in one run's settings could leak into every later one.
- After a teleport the map is read again after the character: when the two disagree, the game was
  still changing maps, and the bot waits.
- A run no longer stops with "noplayer: id ... not in the entity map" when the server drops your
  own character for a moment (it did mid-fight on a laggy server, and put it back seconds later).
  The bot now waits up to a minute for it.

## 0.6.0

**Dungeons** (Tools, Dungeons, Ctrl+Shift+G)

- Scorpion Cave and Spider cave, each once a game day, for as many kills as their 15 minutes give.
  Learned from a hand run of each: where they are entered, the menu, the timer, and how the game
  moves you out at the end.
- Before setting out it tops up the pet's blue potions, Pet Rice Rolls and your own blue and red
  potions (25, 3, 50 and 30 by default) from the storehouse or the shop, and doesn't go if they
  are still short, unless you say go anyway.
- Inside it never stops: it takes the nearest monster within 90 steps (simulated visits showed
  the chase range, not the loop, decides the kills: 5 to 13% more for a caster than farming's
  40), uses area skills on two monsters close together, drinks instead of resting, and keeps the
  pet out, healed and fed - the pet that is out, when none is ticked. Nothing sends it out early.
- Kills now include the pet's, as the game counts them: in the first Scorpion Cave run the game
  said 298 and the bot 286, the pet having struck the last blow on 22.
- Shoot while walking (on by default): a character that fights from afar shoots whatever comes
  into reach on its way to the next monster, instead of stopping for each one. One hit kills a
  Scorpion, and simulated visits made about half as many kills again this way.
- Pet: the pets ticked in Character, Settings (else the one that is out), no pet, or one pet just
  for the caves.
- Then the next cave, and at the end back where you started it, or the place you choose. It waits
  for the game to finish moving it out first (a hearthstone used during the move was lost).
  Scripts: `dungeon`, `dungeon check`.

**Fighting**

- Attack keys are never switched faster than the game takes them: it throws away a press that
  comes within about 80 ms of a press on another attack key, and a cave fight once cast nothing
  for five seconds that way.
- Inside a dungeon a monster that takes no damage is only skipped itself; its whole kind is never
  written off (that left a whole cave alone after one lost fight).
- The fight results count only the run's own fighting, not a fight played earlier.

**Gathering** (Tools, Gathering, Ctrl+Shift+F)

- Fishing, mining or collecting herbs, whichever the character has learned: gangs' Lakes,
  Mining Areas or Herbal Gardens first - your own gang's and other gangs' by name, in the order you
  choose, each once a game day - then outside at your own places ("Add where I
  stand") or ones the bot knows, the best area for your level and the skill's rank.
- Before setting out it looks at the bag's load and says when the bag is full (less than 1,000
  left; you can change it), sells first if you let it, and never enters a gang's area without room.
- It starts the skill as players do (Right Ctrl, Start, Confirm). In a gang's area it leaves it
  to the game for the half hour; outside it drinks, heals and fights back, then starts again.
- It fights whatever attacks it and starts again on its spot, sells the collection to the
  Aquaculture, Ore or Herb Buyer when the bag fills and comes back, and stops when Vitality runs out.
- Tools, Sell the collection now: sells fish, ore and herbs from wherever you are, keeping what
  Bag and supplies says to keep. Scripts: `gather`, `sell_collection`.
- Outside it gathers until Vitality runs out, or until it is down to the mark you set (read from
  the character sheet).
- The game agent (version 50) reads each profession skill's rank and the sheet's Vitality. Restart Mist World after the
  update so it loads the new agent.

## 0.5.0

Uncle Farmer's first release in its new home. Everything below is new since 0.4.0.

**Fighting**

- Skills are pressed through the game itself, not the keyboard: nothing you hold in another window
  (NVDA's Ctrl, say) can turn a skill into something else. Turn it on or off per character with
  "Press skills through the game, not the keyboard".
- Every class fights from its own base attack's distance, read from the skill's own text.
- A monster that another player is about to kill is left alone instead of walked to.
- Only a monster that actually strikes you or your pet counts as attacking you. Monsters fighting
  other players no longer pull the bot out of its farming area.
- Healers heal the player they follow and the team in sight.
- Potions that aren't on a hotkey are drunk from the bag in the middle of a fight too.

**Daily chores** (Tools, Daily chores, Ctrl+Shift+D)

- Bank income, hunting bounties, free pet rebirths, the gang donation and construction builds,
  and sharing arnebia in the secret room, each once a game day. The bot remembers what it did, and
  can do them before the day's first farming run.

**Bag and results**

- Shards can go to the storehouse too (Bag and supplies, Shards: Store).
- Results count every shard picked up, and say how many of them were thrown away, sold or stored.
- Gem making, enhancing and chores are kept apart from farming, inside a farming run too. Results,
  Show switches between farming and those jobs.
- Nothing is counted while a run is paused: what you kill or pick up by hand is yours.
- Contribution, experience and the drop time are read after every kill, without the sheet's key or
  window.
- The report puts time, kills and contribution first and the fighting details last.

**Safer and steadier**

- Uncle Farmer finds the game folder by itself, and no longer asks to install an agent that is
  already installed.
- Your quick phrases are never written over: codes go on empty slots only, and only when the bot
  can see the character's phrases.
- A game that answers slowly no longer leaves the bot reading every answer one step behind: the
  run stops cleanly instead.
- A key held by one controller (the message helper walking, say) is never let go by another.
- Uncle Farmer looks for new versions on its GitHub page when it starts (Settings, "Look for a new
  version when Uncle Farmer starts"; Help, Check for updates).
- Map searches run in C when the optional module is built: 10-24 times faster.
- Mist World loads the new game agent (48) the next time you start it.

## 0.4.0

Faster farming, fewer key presses, and voice messages.

- HP and MP are read from the server every few seconds without pressing R.
- Bag items are used straight from the bag, with no arrow keys.
- The monster the bot picks becomes your target at once, and only one the game's own target keys
  would take.
- Every hour the log says how much contribution that hour brought.
- Voice messages in Chat (Ctrl+T): play and record them for Team, Gang and private chats.
- The leader command, cable cars and airships, and settings that keep their values.
- Faster route finding, safer saving, one copy of Uncle Farmer at a time, Roman Urdu.

## 0.1 to 0.3

The first releases built what Uncle Farmer is: plans and scripts, travel across all 375 maps with
cable cars and airships, smart potions, the bag manager, pets and beasts, talent combos, survival
(toolbox repairs, self-heals, buffs and dispels), the walking assistant, quick phrases and team
navigation, several games at once, private messages, the Chat window, game language packs, the
daily stop before the drop time resets, results, and a full set of tests and checks.
