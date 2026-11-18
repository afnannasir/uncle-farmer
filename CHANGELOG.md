# What's new

## Unreleased

**Gang construction reaches every place, and all your highlands**

- You choose where the day's builds go: highlands, Magic Cannon, Supply Camp, East and West posts.
- Highland defences under 100% come first, then what the gang is building, then the posts, and
  last the highland defences at 100%, which the game also repairs and rewards.
- No more highland number to type: the bot goes through all your gang's highlands and comes back
  through the Teleportation circle from anywhere on the map.
- The day you join a gang, and a breed beast already fed today, no longer count as failures.
- Daily chores, City tab: exchange contribution (the kind earned at the top level, not gang
  contribution) for Contribution Chests at Guard Captain Suke, 5,000 each, up to the game's two a
  day. It is off until you tick it.
- Gathering: a minimum saturation for gangs' areas. An area under it is skipped.
- Hunting bounties are no longer a daily chore: each officer pays for bosses of his own, after your
  killing blow. Tools, Hunting bounties (script: `bounties`) asks the officers you tick, Guard
  Captain Suke included.
- Tools, Construction scanner: reads how far the Magic Cannon, Supply Camp and highland defences
  are, repairing nothing. Copy the report, or as a gang elder publish it as an announcement, a
  broadcast or a system message. A new announcement replaces only your own older reports.
- Leaving a gang base, the bot walks out first instead of teleporting away, and a teleport skill
  that is on no key is dropped at once, with one message instead of one for each place.
- A window left open, such as the character sheet, no longer makes the game refuse the fusion beast.

**Market searcher and buyer (Tools, Ctrl+Shift+K)**

- A new window for the player market (the Ansible Space-time Marketplace). It lists what is for
  sale by category: each item with how many lots, how many in all, the cheapest and usual price,
  and how many sellers. The first time, press "Refresh the market list": the bot walks to the
  nearest marketplace and reads every category (about a minute or two). Until then it shows a
  starting list that came with Uncle Farmer. Names show the way your game's language pack shows
  them.
- Search by item (one or several names, or a custom name), cheapest first (it stops as soon as
  the prices pass your most a unit), by seller ID (it looks at each lot's details, remembers every
  seller it has seen, and stops as soon as all 10 lots of a seller are found), or browse a
  category. Finds show up while the bot reads. Searched items and sellers are remembered.
- Two tabs, Search and Results, and the Search tab shows only the boxes the chosen kind uses.
  Results grow in place while the bot reads (the row you are on stays put), and the total is
  spoken at the end. The character stays at the marketplace after each search, so the next one
  starts at once.
- Tick the lots you want and how many of each, then Buy: one question names the lots, items and
  gold coins in all. Each lot is found again by its listing number, even after it moved to another
  page, and bought at the price you saw (a lot's price can't change). Lots still in their first
  hour are never bought. A spending limit and "Check only" are there too. Each lot ends as bought,
  gone, only N left, over your most, over the limit, or not bought with the game's own words.
- Scripts: `market scan`, `market search`, `market seller`, `market buy` (docs/SCRIPTING.md).
- Agent 71: `detail` presses the details key (G) on a market lot the game's own way, so the seller
  comes back without a key press.

**Draw Blood, Skinning and Cut Flesh: walking up to the corpse**

- Corpse harvesting only took corpses within 4.5 steps of where the fight ended, since the game
  takes the skill only from close by. A character that fights from afar kills about 18 steps off,
  so almost every corpse was left (one night: 12 harvests in about 1,150 kills), and even
  close-range characters often ended a few steps too far. Nothing in the log said why.
- Now the bot walks up to the corpse first, as the game's own key does: up to 20 steps away, never
  to one near a boss, and still only between fights. A fight on the way comes first. The new
  choice in Character settings, Fighting, Corpses, **Walk up to corpses further off**, is on by
  default; turn it off to harvest only the corpses within reach, as before.
- A corpse of yours that is left (too far, near a boss, or harvested by someone else before you got
  there) is now logged, once for each reason in a run.

**Gathering outside: the pet helps, and the way home has no fights**

- While fishing, mining or collecting herbs at an outside place, the bot now keeps your pet out and
  looked after, as it does while farming (the pets ticked in Character settings): it helps kill
  what comes, and a monster that goes for the pet is fought too. Summoning the pet or giving it a
  potion stops the gathering for a moment, as your own potions do; it starts again by itself. In a
  gang's area nothing changes: the half hour there is left to the game.
- Once gathering is over, the way home - to the buyer to sell, then to where the run ends - is
  walked as the walking assistant goes: no stopping to fight, not even what attacks you, riding
  your Swift beast past it, with potions still keeping you up. The trips while gathering (to the
  place, and to sell and come back when the bag fills) still fight as before. A script that farms
  after gathering ends as a farming run does.

**Spanish and Turkish**

- Uncle Farmer now comes in Spanish and Turkish too (File, Settings, Language). Both cover every
  string of this version. Game names follow the game's own Spanish and Turkish packs, every
  Alt+letter shortcut is the same letter as in English, and the standard buttons (OK, Cancel, Yes,
  No...) are translated as well. The Turkish one was largely rewritten: terms are now consistent
  (Kalp taşı, depo, Parça pazarı, Dökümcü, betik, farm) and trusted-player commands stay in English
  so they still work.

**No trip to the farm with the drop time used up**

- A farming run whose day's drop time is already down to what you save (Drop time to save, or
  the plan's `keep_drop`) no longer travels all the way to the farm for the first sheet there to
  stop it: it says "the drop time is nearly used up (... min left) - not starting to farm" and
  stays where it is, as "Stop before the next level" already did.
**Fusion power comes back to 50 before the beast goes on again**

- Measured in the game: a fusion beast that is on costs 3 fusion power a minute, moving or not
  (Swift and Fierce alike), and power comes back at 6 a minute once it is off. The bot got off
  under 15 and back on as soon as power was over 15 again, so it was off within two minutes: a
  change every two and a half minutes, each one waiting on the game. Once power has run low it
  now waits on foot until it is back to 50, then rides about 13 minutes before the next rest.
  Beasts that cost no fusion power still ride meanwhile.
**Leave demonized elites alone**

- A new choice in Character settings, Advanced, Fighting: **Leave demonized elites alone**, off by
  default. The game's "demonized" elites (demonized Boar, black bear, Water Elf...) are much
  stronger than the monsters around them, and one the same level as the character can kill it,
  depending on its gear. Ticked, the bot never picks one as a target; one that attacks still gets
  fought. The demonized field bosses are kept away from as bosses, as before.
**Flee a lost fight**

- Character settings, Low HP: **Stop when HP stays low** became **When HP stays low**, with a third
  choice: **Go to town with the hearthstone, heal and come back (while farming)**. On the farming
  map, when HP stays under the mark, the bot drops the fight, uses the hearthstone, heals up in
  town (to 90%, three minutes at most), looks at the restock rules and goes back. A hit stops the
  hearthstone's cast: one that doesn't take the character home leaves it fighting on. Without a
  hearthstone it says so once and fights on. The third flee within 30 minutes ends the run in town.
  **Fight on** stays the default; a profile that had the stop ticked keeps it.
- The low HP mark is now 30% for 3 seconds by default (it was 25% for 10 seconds, nearly dead
  already for a flee). Profiles that were saved keep their numbers.

**Toolboxes: one per warning again (a 1.2.0 bug)**

- Since 1.2.0 the bot checks that a repair really happened. It only believed a used toolbox when
  the toolbox's level limit covered the character's level. But the limit is the gear's level, not
  the character's: a level-69 Mage wearing level-60 gear is repaired by a Small Toolbox. So every
  such repair was "couldn't confirm the repair", the warning stayed, and the bot used another
  toolbox every 10 minutes all night (29 Small Toolboxes in one night, where 1.1.0 used one or
  two). A toolbox the game uses up now counts as a repair again. When the bag can't be read to
  tell, the warning is dropped too: the game warns again if the gear is still worn.

**Several kinds of experience cards**

- Character, Settings, Fighting: the experience card list became one tick per card (1.3x, 1.5x,
  1.75x, 2x, 2.5x and 3x), each with how many the last scan saw in the bag. When no card is
  running on the farm, the bot uses the strongest ticked card it has, so a character can finish
  its 2x and then burn the small cards looted from chests on the way. A ticked card that runs out
  stays ticked, and the next one looted is used. Cards that aren't ticked are never used. The
  card chosen before is the one ticked.

**The skill page turns with the game's own key**

- The bot always turned the skill page with Caps Lock, the game's default. A player who gives
  that job to another key in the game's options (Space, for example) had every Caps Lock thrown
  away by the game: the page never turned, and the skills on the other page were pressed on
  empty keys. An Elf with two skills on her second page cast only those two for a whole night
  (88 casts an hour, a kill every 80 seconds). The bot now reads the page key from the game's
  key settings like the skill keys, and says which key it uses when it isn't Caps Lock. Caps
  Lock stays the key when the game names no other.

**Experience dungeons: a missed entry is tried again**

- Getting into an experience dungeon had one go. If the entrance's menu came late, had no
  "Enter the dungeon" row, or the game took more than ten seconds to move the character in, the
  day's visit was written down as used and not tried again until the next game day. When the
  move in came late, the bot even walked out of the dungeon again for the end of the run. The bot
  now has three goes at the entrance, sees a late move in before trying again, and writes the day
  down only when it got in or the game said "already entered an experience dungeon today". After
  three failed goes it says so, and the next run tries again.

**Airships and cable cars that need a level**

- Below an airship's level, its administrator opens no menu: the game only says "Go to Violet
  City; requires Level 76 or above". The bot waited for a menu, said "didn't answer", tried once
  more and only then went another way, about 20 seconds lost on every trip (a character under
  level 76 heading for Margit Rainforest from Tulip City, every time). The bot now knows the level
  of every airship out of a capital and doesn't plan one below it: Giant Rock City to Sunset City
  26 and to White Sand City 36, Tulip City to Moon City 56 and to Violet City 76, Holy Blood City
  to Gouging Knife City 66 and to Dark Night City 86 (the flights back need none); the cable car
  from Giant Rock City's underground to Tulip City's needs 45, to Holy Blood City's 60. A ride that
  turns the character away for its level anyway is left out at once, said ("the Airship
  Administrator on Tuneful Wetland takes only level 76 and above"), and not tried again for the
  rest of the run.
- Dark Night City and Gouging Knife City now fly back to Holy Blood City: their administrators
  were missing, and from Dark Night City the bot walked out through skeleton basin, four times
  longer. Holy Blood City's own two airships now land where they really do, not in the middle of
  the map.
- Every fare is known now: each airship takes 1000 arnebia, and the cable cars to and from the
  Lifting Platforms are free. A character with less than 1000 arnebia in the bag no longer plans
  an airship it can't pay for.

**A shop that doesn't answer no longer ends the run**

- When a potion ran low while farming, the bot went to town to restock, and one shop or
  storehouse that didn't answer ended the whole run. It happened on the way between maps and
  after a bag trip too, and on a bag trip a Shard Broker that didn't answer did the same. Now the
  bot says what it couldn't do, skips that errand and goes back to farming, as "Restock now"
  already did; the item is tried again later.
- A storehouse that doesn't answer leaves the item to the shop instead of skipping the shop too.
- A shop that doesn't answer is passed over for the next nearest one, and isn't remembered as a
  shop that doesn't sell the item. When none answers, the bot says so instead of "no shop near
  enough lists it".

**No trip to town and back when a run starts on its farming map**

- **Skip this list when the run starts on its first farming map** (Character settings, Triggers,
  Before farming) is now ticked by default, and ticked once for every profile saved before. A run
  started where it farms goes straight to farming; the farm loop still goes to town by itself
  when a supply runs low or the bag is full. Untick it to run the list on every start.
- **Update shard prices** in a Before or After farming list no longer walks to the Shard Broker
  when the prices were read less than 8 hours ago, by any character on the PC: it was the job
  that sent a run started on its farming map to town and back for prices read minutes before.
  The Tools menu and the `update_shard_prices` script command still read them every time.

**A full storehouse**

- A full storehouse still takes more of an item it already holds, but turns away an item it holds
  none of: the game says "item storage is full". The bot now hears it, says which items stay in
  the bag because the storehouse is full (not that it "may be full"), and stops walking them back
  to the storehouse on every bag trip of the run. Taking something out of the storehouse makes
  room, so the bot tries them again after that.

## 1.2.0

**XP loser: die at the nearest village**

- Tools, XP loser has a new choice, **Where to die**: at the nearest guard (as before), or at the
  nearest village. The game brings a dead character back at the last town it walked into, so the
  bot walks into the village first; its gate guards stand a few steps from there, so each death is
  much quicker: on Tuneful
  Wetland a death took 11 seconds at Aria Village against 29 at Tulip City, whose guards stand far
  out past its streets. The villages come from the game's own map table (16 of them, from mettle
  village to Orchid woods Village). A village with no guard to fight is left for the next nearest;
  and if the game brings the character back somewhere else, the bot says so and dies at the
  nearest guard instead.

**Automatic login picks the character's server**

- A scheduled script's `login` now selects the saved character's server (S1 or S2) before signing
  in with the saved account, so a character on the other server is found. `login "Name - S2"`
  works as `login "Name - S1"` does. The game has to be started again to load the new agent (69).

**The fusion beast after a death**

- Right after a death the game refuses a fusion beast without a word, and a refused press made
  the next try wait for the game's gap between two fusion changes: the XP loser stood still about
  ten seconds after each respawn before its beast came, then rode up to the guard just to get off
  again. The beast now waits until six and a half seconds after the death and goes on at the
  first press, so the character rides off a few seconds after it is back.

**Sales counted in the run that earned them**

- The arnebia a sale brings in is counted in the results again. The game takes the shards out
  of the bag first and pays a moment later: when the money came in after the sale was over, the
  results put it under the next job (for example "Updating shard prices"), and the farming run's
  earnings left it out. Selling shards, selling fish, ore and herbs, and selling to a shop now
  wait for the money (three seconds at most).

**Settings and learned data survive a busy disk and two writers at once**

- A settings file another program held open for a moment (an antivirus, a backup) was taken
  for a broken one: it went to settings.json.corrupt and the defaults were saved in its place.
  A file that can't be read is now left alone, read again after a short wait, and nothing is
  saved over it until it can be read. The same goes for the character profiles.
- The sound silencer, the scheduler and the main window each saved the whole settings file, so
  one could put back a value another had just saved (the daily stop time, the sound list). A
  save now writes only what its holder changed, onto the file as it is then.
- A run saving what it learned (the hearthstone's square, gathering places) no longer puts back
  the character settings as they were when it started: changes made while it ran stay.
- A run whose results couldn't be saved (a full disk) left the window saying it was already
  running until the app was restarted. The run now always ends; the results are still shown in
  the log, and the failure is said.
- A scheduled run whose save failed was taken for done and never started. It now starts at the
  next look, once.
- The results window could fail with "dictionary changed size during iteration" while a run
  was adding to them.
- The game's options file (mw.exe.vmoptions), the learned dungeon entrances, item weights and
  landings, the daily chores' list, shard prices and survey records are written whole or not at
  all, and two saves at once no longer lose one of them.

**Safer updates**

- Uncle Farmer no longer seems to freeze after a download: the update is prepared in the
  background ("Preparing the update to ..."), and the window closes by itself once it is ready.
  Closing it meanwhile waits for that, and pressing Update again starts nothing twice.
- A connection cut in the middle of GitHub's answer is now an ordinary "update check failed"
  instead of silently ending the update check.
- A read-only file in the Uncle Farmer folder (for example data/game_texts.json) stopped every
  update half-way, after a minute of retries. The update now names the file at once, changes
  nothing, and works again once the file is writable. Read-only leftovers of such a failed try no
  longer block later updates.
- Leftovers of an earlier update are cleaned up reliably: a stale "update in use" note of this
  copy, or of a long-gone program that Windows gave the same process number, no longer keeps
  the update folder for hours.
- An Uncle Farmer folder whose name has brackets in it ("Uncle Farmer [2]") is no longer taken
  for an incomplete copy.
- An update archive far larger than any release, or one the disk has no room for, is refused
  before it is unpacked; a checksum GitHub gives in an unexpected shape stops the update instead
  of being skipped.

**XP loser attacks the guard with your basic attack**

- The XP loser pressed whatever skill was on your first attack key, once. For a Hunter that is
  often Escape, which jumped away from the guard. Other first-key skills started no fight at all,
  and the guard was left as "didn't strike back".
- It now uses your class's basic attack: Bash, Valiant Strike, Blade Strike, Arcane Shot, Magic
  Sphere, Fireball, Retribution or Shadow Arrow. If that skill is on no key, it is put on an empty
  one first. If it is on the other skill page, the page is turned. It is pressed again every second
  until you fall, so a press the game drops (just after getting off the beast) no longer ends the try.
- The log says which key it attacks with.

**The walk to death wormhole finds its circle**

- The walking assistant reached hidden water cave but never found the death wormhole's circle:
  the game calls it "Wormhole teleportation Circle", a name that doesn't start with the
  dungeon's, so the bot walked past it. The circle's name is in the dungeon list now.
- The five circles of hidden water cave (death wormhole, wolf head mountain, bubble cube, ice
  maze, Herbal Mountain) have their places now, measured in the game: the walk goes straight to
  the right one instead of trying each stone room in turn.
- When none of the marked entrances is the dungeon's circle, the log now names the other
  circles seen on the way, so a circle with an unexpected name is easy to report.

**Every class's talents, as the game lists them**

- The Warrior's defensive talents were missing (Defensive Stance, Sturdy Sentry, Defensive
  Barrier): the Orc has talents of the same names, and only the Orc's were kept. The Warrior has
  its ten talents again.
- The Orc's Defensive Stance carried the Warrior's text; it now says it can't be used with
  Rampage Stance, as the game does.
- Talents whose text says "Require ..." all say which talent they need (four Hunter and Mage
  talents and the Warrior's Precise Block didn't).
- The gang talents (Imperious, Focus, Ravage, Havoc, Anger, Brutal ... and their Aggressive and
  Defensive ranks) are in their own Gang group instead of the general talents, with the second
  "Anger (Aggressive)" rank that was missing.
  These are the talents a gang develops at its Head of inner hall (gang Affairs Management, Gang
  Skill Management), three per gang at most.

**Fourteen more dungeon entrances placed**

- The entrances of fourteen dungeons are in the dungeon list now, read in the game: the walking
  assistant goes straight to them instead of trying each entrance marked on the map.
- Three of them couldn't be found before: open sea, space battleship and Treasure pavilion
  stand in the south-east of far off regions, far from the two entrances the game's files mark
  there ("none of the entrances marked on far off regions is the open sea circle").
- Lisa's Ukulele has no circle: you go in by talking to Villager Lisa, which the bot now knows.
- The others: Dryad Jungle, zagrama canyon, Machine Fortress, Silver Peak, bottomless pit
  (Valley of Luxuriant forests), fully mountain, Machining plant (far off regions), graceful
  mountain (water curtained cave), Baru Grottoe and Elemental Maze (thousand miles of rivers).

**Stop before the next level**

- For characters you keep at their level: Character, Settings, Fighting has a new group, **Drop
  time and experience**, with **Stop before the next level** (off by default) and how much
  experience the next level must still need (100,000 by default). The character sheet is read
  after every kill; once the next level needs less, Uncle Farmer says so, leaves the monsters and
  ends the run. The After farming list runs next, so the XP loser can win some experience back.
- A run that starts with the next level already that close doesn't set out: it says so before
  the Before farming list and does nothing else, instead of going all the way to the farm.
- Inside a dungeon it only says so, once: leaving would lose the visit. It doesn't go into an
  experience dungeon once the next level is that close, and the day's cave is still yours.
- A plan's When to stop page can set it for that plan, in place of the character's setting, as
  it can the drop time to save; scripts have `keep_level <experience>` (0: no such stop).
- **Stop when the day's drop time runs out** and **Drop time to save** moved from Bag and
  supplies, Full bag, to the same new group in Character, Settings, Fighting, beside the other
  reasons to stop. Your settings stay as they were.

**From a secret room, the nearest shop is the one outside the door**

- A run that started inside a secret room chose its Shard Broker, storehouse or other shop as if
  every city were as far: leaving a secret room puts you back where you came in, which the map
  data can't say. A room entered from Tulip City sent the bot by cable car to central plains'
  Shard Broker, right past Tulip City's. It now leaves the room first, then picks the nearest one
  from where it comes out, as it already did from a gang base.

**A start again on the farm goes straight back to farming**

- Character settings, Triggers, Before farming has a new tick, **Skip this list when the run
  starts on its first farming map** (off by default). After a Stop, starting the plan again where
  the character was farming used to run the whole Before farming list first - Update shard prices
  always walks to a Shard Broker, so that was a trip to town and back every time. With the tick,
  a run that starts on its first farming map leaves the list out and says so.

**XP loser stays in all mode, and puts your PK mode back**

- Guards strike back only when hit in all mode, but a walk to the guard could switch the PK mode
  back to your profile's (a monster in the way, a ride's NPC), so the hit came in a peaceful mode
  and the guard never answered. The XP loser now keeps all mode for the whole run and checks it
  right before every hit.
- When the run ends - done, out of guards, or stopped by you - your PK mode is put back. After
  Stop it used to stay in all mode ("please set it back by hand"). On a map that holds its own PK
  mode, Uncle Farmer says which mode to set once you leave it.

**Updates start Uncle Farmer again**

- After an update, Uncle Farmer opens again by itself. The updater is its own small program now,
  `bootstrap.exe`: it waits for Uncle Farmer to close, has the files replaced, starts the updated
  `UncleFarmer.exe` and closes. `UncleFarmer.exe` is the program itself again, the one you start.
- If an update was ever cut off (a power cut, a crash), starting `UncleFarmer.exe` hands it to
  `bootstrap.exe` first, which finishes it or puts the old version back, then starts Uncle Farmer.
- Dev builds from 2026-11-14 on (where the program was `UncleFarmerApp.exe`) can't install this
  update themselves: unzip it over the folder once by hand.
- Why it didn't open again before: after an update, the recovery check that runs before Uncle
  Farmer starts always refused to run in the built program (it looked for the wrong file name, and
  the folder's short name "UNCLEF~1" didn't match "Uncle Farmer"), so nothing was started.
- An update no longer fails to start on a slower PC: the new copy may take up to 30 seconds to get
  ready (it had 5, and a freshly unpacked build needs 3 to 6), and the two programs no longer
  trip over each other's hand-over files ("Access is denied").

**Fewer duplicate buttons and menu entries**

- The Plans menu keeps New plan and New script. Edit (Ctrl+E), Duplicate (Ctrl+D), Delete, Export
  and Import are on the plans list's context menu (the Applications key or Shift+F10).
- Bag and supplies: the Move to Keep, Store, Sell and Throw Away buttons are gone; each list's
  context menu moves the selected item.

**Trigger lists and scheduled tools**

- Character settings, Triggers: Before farming and After farming are ordered lists now. Each step is
  a tool or a saved script, can be switched off, moved up or down, wait up to an hour before the
  next step, and be marked DETERMINISTIC (if it fails, the rest of the list stops, and before
  farming, farming doesn't start). The old ticks carry over in their old order.
- Schedules has Tools and Triggers tabs: a tool or a whole trigger list can run on a schedule by itself.
- A step that farms (Dungeons, or a script with a farm or dungeon line) keeps the daily stop: it
  isn't started between the daily stop and the drop-time reset, and it stops at the daily stop like
  any farming run. Only the waits between steps may run past it. A scheduled Dungeons run keeps it too.
- When an After farming step fails, it is said and the run counts as failed, but closing the game or
  shutting the PC down still happens, since farming itself ended by itself.
- A saved list that can't be read says where to fix it, and Character settings still opens.

**No town trip right after throwing things away**

- When the bag filled up and the bag manager's rules threw things away right where you farm, the
  sheet could still show the old load for a moment, and the bot went to town anyway. It now reads
  the load again for up to 3 seconds and keeps farming once it is under your mark. A bag that is
  really full of things you keep still gets the trip (or stop) you chose.

**Gem Maker: make most finishes each level first**

- Making the most from the bag no longer moves up a level while gems of a lower level could still
  be combined (left over after a batch, or kept by a protected failure); it combines those, and
  turns leftover shards into gems, first. One or two gems that can't make a combine stay, as do
  the gems you keep.
- Making a set number of gems still uses only the tries that number needs.

**Golden Pegasus**

- The Golden Pegasus fusion beast (Swift, speed +3.2, level 100) is known now: the bot rides it to
  travel, gets off it for a fight, and offers it in "Travel on fusion beast" after a scan. It was
  newer than the bot's list of beasts, so it was never chosen.
- When the only fusion beast that could carry us is one the bot doesn't know yet, the log names it
  once a run and asks for its description, instead of only saying there is no Swift beast.

**Teleport stones**

- With "Use teleport stones and the hearthstone from the bag" ticked (Character, Settings, Travel),
  the bot also uses the stones of the villages and ruins outside the cities: Soul Burial Village,
  Plague Abyss, Orchid woods Village, City of the Ancient tower, mist gods Ruins, West Sealing
  Tower, Hermit Camp, East Sealing Tower and portal of the demon world. A West Sealing Tower stone
  goes straight to ancient Sealed Land, where the White Bone Cave is entered.
- The seal Teleportation stone (West or East Sealing Tower, or the portal of the demon world), and
  the Black Gold stone's fifth place, City of the Ancient tower (next to the way into ancient
  Sealed Land).

**Travel**

- Map exits whose landing the game's map data doesn't give no longer send the bot round in
  loops. On the way to ancient Sealed Land, the small hall of Ancient Ruins looked like a short
  cut and the bot went in and out of it for a minute before giving up.
- The six landings of Ancient Ruins' small hall and of Plague Abyss back into Ancient Ruins were
  measured in the game and added to the atlas. The atlas only carries each arrival's marker,
  sources, point and walkable area; the bot works out its port and its routes to the map's other
  ports when it reads the atlas, so they never go stale.
- An exit with no landing in the data is taken to land beside the door it stands next to, as the
  measured ones do. Where the bot really lands through one is learned and kept in
  profiles/_learned_landings.json, and the log gives it as the atlas line to report, so it can be
  added to the atlas for everyone.
- An exit the same trip already went through is left out of the next plan, so no loop through
  one can go on.
- A landing further than a few cells from where the atlas says (a measurement gone wrong, or a
  game update) is said in the log with the corrected atlas line to report. The bot goes on from
  where it really is either way.

**Dungeons**

- Tools, Dungeons has two more experience dungeons: the White Bone Cave (level 90, entered at
  ancient Sealed Land, reached by the cable car; tested: 204 kills in a visit) and the Bottomless
  pit (level 80, entered at Valley of Luxuriant forests; untested so far). The highest cave my
  level lets in picks them too.
- Starting a dungeon run while already inside one of the caves (after the bot or the game was
  restarted mid-visit) now hunts on until the game moves the character out, instead of leaving.
  The end place "where the run started" then ends on the nearest map without monsters.
- When the game turns the character away because the day's experience dungeon is used, the bot
  says so.

**Experience cards**

- Character, Settings, Fighting, Experience cards: "Experience card to keep running while farming".
  Choose which card: the list holds only the cards the last scan found in the bag (1.3x, 1.5x,
  1.75x, 2x, 2.5x, 3x), with how many you have, or None. On the farm, a paused card is resumed, and
  when none is running a card of the chosen kind is used - only that kind, never a stronger one
  from the bag. When that kind runs out, the bot says so once and keeps farming without a card.
- "Pause the card while away from the monsters": a running card is paused when a farming run
  travels to another map - to town for potions or repairs, back after a death, home at the end -
  and when the run ends. It runs again once farming starts on the farm. A pause costs a minute of
  the card, so a card with under a minute and a half left is not paused, and nothing is paused in a
  dungeon (the game doesn't allow it there). Both off by default.
- The bot reads the card's state and time left from the character sheet and pauses or resumes it
  the way the sheet's own entry does, so it never presses it the wrong way.
- Needs the new game agent (60): restart the game after updating.

**XP loser**

- Tools, XP loser (Ctrl+Shift+X), for a character that shouldn't level up: say how much experience
  the next level should need at least, or how much to lose. The bot finds a guard - in sight, where
  one was seen before, or in the nearest town - switches the PK mode to all mode, hits the guard
  once and lets it kill the character, with no potions, heals or fighting back. It respawns in the
  city and goes again until the character sheet shows the goal.
- It stops at the goal, after the most deaths you allow (30 unless you say), or when dying no
  longer costs experience. The pet is put away first and the PK mode put back at the end.
- Guards seen are remembered, so the next run knows where to go.
- Also a Triggers job (before or after farming) and a script command, `lose_xp [check]`.
- Not for a black-name character, who can lose worn gear when dying: the bot reads Morality from
  the character sheet and refuses to start for one.
- Only guards outside a town's safe zone are fought (the ones in front of the gates): the guards
  walking the streets can't be, and no attack works from inside the zone. The bot switches to all
  mode before it looks, stands outside the zone, gets off its fusion beast, and walks right up to
  a guard when a wall is in the way. From inside a town it heads for the town's edges first.

**Safe zones**

- The bot now knows every safe zone in the game - towns, villages, the zones in front of some exits
  - from the game's own map table (data/safe_zones.json). Standing in one, no attack key works,
  and nothing standing in one can be fought.

**Teleport skills**

- A Mage's Teleportation (city squares) and an Elf's Elven Teleportation (Soul Burial Village,
  mist gods Ruins and the other high-level lands) are used for travel whenever they make the trip
  shorter. They cost only mana, never an item.
- The bot walks out of a town's safe zone first, deals with anything attacking (any hit breaks the
  10-second channel), gets off its fusion beast, presses the skill and picks the place from the
  game's list. A skill on no key is put on an empty skill key. A teleport counts only when the new
  map arrives; a broken channel means fight, then try again, and after three broken tries the bot
  goes another way.
- Each character's list of places is learned from the game's menu the first time, since it grows
  with the skill's level. Nearest-shop choices count the teleport too.
- Character, Travel: "Use the class's teleport skill (Mage, Elf)", on by default.

**Corpse harvesting**

- Character, Settings, Fighting, Corpses: "Harvest the monsters we kill with the learned Draw Blood,
  Skinning or Cut Flesh". The bot finds which one the character has learned in its profession skills
  and casts it straight through the game, the way the game's own key would: no Z, X or C key needed,
  nothing to bind, and nothing selected. Between fights, with nothing attacking you or your pet, it
  takes the corpse of a monster it killed in the last minute, within the game's reach, once per
  corpse, the nearest first.
- The cast is held for a moment and then let go, as a key press is. The first test night sent it
  once and never let go: the game then treated the character as still attacking, every walk
  failed until the next fight (hundreds of "stuck" lines, hearthstones, an early stop), and casts
  sent in the short pause after a kill were mostly ignored.
- It stops for the rest of the run when Vitality runs out (the game says "lack of vitality"), or
  when the character sheet's Vitality is down to the number you set. It never spends diamonds.
- If the game answers none of four casts in a row, the bot says so and tries again five minutes
  later. A character with no corpse skill is told where to learn one. Off by default.
- Needs the new game agent (61 or newer): restart the game after updating.

**Treasure chests**

- Farming now opens the treasure chests that turn up on the map ("There's a Shining Small Chest in
  Margit Rainforest..."). A chest in sight is walked to, selected, and opened with Enter and "Open
  the chest", once any fight is over; never one near a boss, nor one more than 80 steps away. The
  bot says each chest it opens, and the results count them. Before, the bot took a chest for a
  town NPC (it has exactly an NPC's HP) and walked past it. On by default; Advanced, Fighting,
  "Open the chests that turn up while farming".
- `chestwatch.py`: a read-only watcher that writes down what the agent sees of chests (and of
  anything else that isn't an ordinary monster), the game's chest broadcasts and what the bag gains,
  for whoever teaches the bot more about chests.

## 1.1.0

**Shooting while walking when farming**

- Character, Settings, Fighting: "Shoot monsters in reach while walking to the next one". Dungeons
  already did this; now field farming can too. A character that fights from afar shoots whatever it
  would fight that comes into reach on its walk, instead of stopping for each one. It never shoots
  monsters outside the farm area, bosses, or monsters another player is killing, and never on a
  trip. Off by default. A character that fights from close by is told it can't use it.

**Pausing**

- A pause of more than two minutes no longer ends the run as "lost the connection to the game"
  when it goes on: the game's agent dropped a link that asked nothing for two minutes, and a paused
  run asked nothing. A paused run now checks in every 30 seconds, so a pause can last as long as
  you like.
- Moved the character while paused: going on, the bot now plans its walk again from where you
  left it on the map (and says how far it was moved), instead of pressing on along the old line
  and maybe calling itself stuck.

**Polson Wetland and lava**

- Polson Wetland without Poison Immunity: a trip through it now gets on a fusion beast (any kind)
  and crosses, fighting nothing on the way, instead of refusing to go. A trip that ends there goes
  with a spoken warning. Leaving it is never refused.
- The volcanic caves' lava lake and magma, which take HP from whoever stands in them, count as
  walls: the bot walks, stands and fights on the islands, and shoots monsters in the lava from the
  shore when they are within reach.

**Repairs**

- The Alloy Toolbox repairs again: a character whose level wasn't known yet used the cheapest
  toolbox, a toolbox the game turned down was never followed by a stronger one, and gear a Small or
  Large Toolbox left worn counted as repaired. Now the level is read first, an unknown level takes
  the Alloy Toolbox, and a toolbox that can't do it hands over to the next, stronger one.

**Prices**

- The shops' prices of Pet Rice Roll (1,000), Hearthstone (10,000), Small Toolbox (20,000), Large
  Toolbox (40,000) and Alloy Toolbox (80,000) arnebia, with the game's descriptions. Results cost the
  ones used up, the bag manager shows them, and restocking buys only what your arnebia pays for.

**Triggers and storing**

- Tools, Store items now: puts the bag manager's Store list into the storehouse, nothing else.
- Character, Settings, Triggers: Sell shards, Clean the bag, Update shard prices, Gathering and
  Store items, each with its own tick before farming and after farming. The after-farming ticks
  moved here from Travel and pets, keeping what you had ticked.
- Script command `store_items`.

**Windows**

- Settings, Updater: all the update settings, this copy's version, and Save and check for updates now.
- Tabs that hold more than the window shows now scroll, and windows never open bigger than the
  screen (on a small screen the Fighting tab's Gear choices were out of reach).

**Also in this version**

- Results: any day's results (Yesterday or a day you pick), casts and damage for each skill, and
  healing done to yourself, your pet and others. Spoken results say the time farmed and the drop
  time left first.
- Characters with the same name on different servers keep separate profiles, chores and state.
- Elf: Ignition is used only right before Pyroblast or Blazing Storm with three fire elements, and
  a Fireball critical hit with Flame Combo makes Fire Impact ready at once.
- Sorcerers and Priests cast their dispels on themselves (Shift + key), so their debuffs come off.
- Shard and collection sales are much faster, and bag-rule discards go in one step each.
- Travel: walks round one-way gates, no false "no way" after a map exit, the Holy Blood City cable
  car is paid for, a free VIP ride that goes nowhere is paid for instead, and share trips pick the
  instructor cheapest there and back.
- After a fight the beast is mounted again, travel no longer wastes buffs, and skipped monsters are
  forgotten after a death or a map change.
- Daily chores fixed from recorded runs; the Gem Craftsman no longer leaves the gang base first;
  the Equipment Enhancer never offers rings, medals, badges, emblems or bags.
- Fixes for crashes on map changes and at start, kill counts from the last run, locked log files,
  summer-time schedules, and game languages (equipment names, Shard Broker wording).
- Needs the new game agent (58): restart the game after updating.

## 1.0.1

**Sharing settings and plans**

- Character, Export character settings and Import character settings: a character's settings
  (combat, keys, potions and the rest) saved to a file and loaded into another character or another
  copy of Uncle Farmer.
- Plans, Export plan and Import plan: a plan saved to a file and loaded elsewhere.

**Results**

- Results show Time used and Time remaining (to the earliest known end: the plan's time limit, the
  stop time or the daily stop), and earnings so far, total expenses and net earnings.

**Gem Maker buying**

- The Gem Maker buys from the market in batches worked out from the room in your bag, your arnebia
  and the game's 2000-at-a-time limit, and takes a bagful from the storehouse at once. A job that
  is really blocked stops at once and says why.

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
