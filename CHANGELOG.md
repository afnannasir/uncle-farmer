# What's new

## Next

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
