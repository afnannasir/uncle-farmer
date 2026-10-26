# What to automate next

Farming already works: travel, fighting with skills, potions, pets, beasts, the bag, results and
private messages. This is everything else in the game the bot could take over, ranked by how much
it helps players and how much it takes. It's built on [GAME_GUIDE.md](GAME_GUIDE.md).

**Value:** how much time or money it saves a player. **Effort:** how much is still unknown.

The game's own files hold the text of every menu, instruction page and mission (the translation
file `version\language.gz` carries all of it), so the bot knows each menu's entries before it gets
there. The numbers the server fills in at that moment (today's cost, your tries left, your success
chance) it reads off the menu when it opens it. "Watch" below means a behaviour the bot learns by
watching the game's data during a normal run: nothing a player has to do.


## Next: high value, most pieces already here

| # | Task | Value | Effort | What the bot would do | What's still needed |
|---|---|---|---|---|---|
| 1 | **Best maps for my level** | High | Small | In the plan editor, list the maps whose monsters are 9 levels below to 5 above you (full loot and full experience, from the game's own rules) | Nothing: map monster levels are already in the atlas |
| 2 | **Gems from shards** (built: the Gem Maker tool below) | High | Small | Shards into gems, from the bag, the storehouse or the Shard Market | Done |
| 3 | **Corpse harvesting** | High | Medium | After each kill, use your Draw Blood / Skinning / Cut Flesh skill on the corpse (Z, X or C, then Space) for free potion and food materials, while Vitality lasts | Watch: how a corpse shows up after a kill, and how long harvesting takes |
| 4 | **Daily chores** (built: Tools, Daily chores) | High | Small | One button, once a day per character: collect bank income and Value Products, hunting bounties, make the gang donation, do the gang construction builds, use the free pet rebirths, share arnebia in the secret room | Cultivating talent points; the first live run of each chore may show a confirm box still to learn |
| 5 | **Repairs** (built: toolboxes from the bag) | High | Small | When the game warns that gear is about to break, use the cheapest toolbox that covers the character's level and confirm "repair all equipped" | The blacksmith ("Repair equipment") for players without toolboxes: record its menu once |
| 6 | **Kill missions** | High | Small | Take the repeatable "Eliminate 100 monsters" missions that match where you farm anyway, and hand them in: free experience and arnebia | Nothing: the mission texts are in the game's files, and the bot already farms and talks to NPCs |


## Built as tools

Under **Tools** in the main window, separate from farming and daily chores. Both can use your
gang's craftsmen (cheaper) or the nearest city's, never spend below your arnebia reserve, and have a
**Check only** button that walks there, reads the real menus and prices, and changes nothing.

- **Gem Maker**, two jobs:
  - *Make the most of my bag:* every shard into gems (4 make 1), then each colour up level by level
    (3 make 1 at 50%) as far as it goes, again and again. Choose colours (or all), the highest
    level, and how many of each level to keep.
  - *Make a gem:* asks which gem, which level, and how many (or as many as it can). Each round it
    makes that colour's shards into gems (4 make 1), then combines up a level at a time (3 make 1 at
    50%) only as far as the target still needs; a failed combine leaves a shard the next round turns
    back into a gem, and leftover shards are the +20% boost. When the bag runs short it gets Level 1
    shards (the only level the market sells) from the storehouse, the Shard Market, or both, a bagful
    at a time, with an optional top price, buying only as many as it can also pay the gem fee for.
  Gem Protection Runes are optional for combining. The plan shows how many Level 1 shards the
  target takes first.
- **Equipment Enhancer:** enhances the equipment you choose up to +N (+12, Godbless, is the top), with
  protection runes on every try, from a level on, or never, and boost runes if you have enough.
  Enhance Crystals are made from crystal shards taken from the storehouse or bought at the Shard
  Broker (optional top price): first a stock you choose (100, 200...) before the first try at a
  crystal level, then a set number more whenever they run out. The crystal level follows the gear's
  level (15-29: 1, 30-44: 2, 45-59: 3, then one more per 10 levels up to 8 at 100); the bot reads it
  from the Forger, and learns from the Gem Craftsman how many shards one crystal takes. It stops after a number of failures in a row, when the runes it
  needs run out, or at the arnebia reserve, and never forges without confirming the protection rune
  is ticked when you asked for one.


## Then: bigger jobs

| # | Task | Value | Effort | What the bot would do | What's still needed |
|---|---|---|---|---|---|
| 7 | **Gathering** (built: Tools, Gathering) | High | Medium | Mine, fish or collect herbs in the gang's production area (once a day), then at the player's own or known places, and sell to the ore, fish and herb buyers | A live run: the start's confirm box, the gang's Mining Area and Herbal Garden entrances, more known places |
| 8 | **Crafting potions and food** | Medium | Medium | Turn harvested blood, pelts and jerky into potions and food at the Pharmacist and Chef, instead of buying them | The recipes are listed in the menu itself when it opens |
| 9 | **Gang missions** | Medium | Large | Take and complete the two daily gang missions from the Head of the Inner Hall | The mission texts are in the game's files (deliveries, watering crops, collecting materials); each kind needs its own routine |
| 10 | **Hunting bounties** | Medium | Small | Collect hunting bounties from the city officers on a town trip | Nothing: the menu texts are in the game's files |
| 11 | **Enhancing and sockets** (Equipment Enhancer built; sockets to do) | Medium | Medium | Enhance gear at the Forger and insert gems at the Artificer, only with protection runes and only when the player asks; read out the success chance first | The rules are in the game's files and the chance is shown in the menu. Enhancing is a gamble, so the bot advises and the player confirms |
| 12 | **Recycling** | Low | Small | Sell or recycle equipment you've outgrown on a town trip (a new list in Bag and supplies); recycled things can be bought back within 48 hours | Nothing: the menu texts are in the game's files |
| 13 | **Experience dungeons** | Medium | Large | The daily solo experience dungeon: go in, fight as many as possible in the time, come out | Each dungeon is its own map; the bot can already fight anywhere |
| 14 | **New skills** | Low | Small | Say when a new combat skill becomes learnable at your level, and where | Masters and levels are in the help |


## Later, or not at all

| Task | Why |
|---|---|
| Dungeons (experience, gang, tutor-and-pupil, couple, treasure hunts, Mystery Valley) | Each one is its own puzzle with timers and teammates; possible one at a time, as scripts, once the basics above exist |
| Competitions, Arena, Tactical Battles, Gang Tournament | Player against player: not something to bot, to keep it fair |
| Market trading, weddings | Need a human's judgement; the bot shouldn't spend your money or speak for you |


## Where the knowledge comes from

All of it is in the game folder: the help pages, `language\english\more`, and above all the
translation file `version\language.gz` (plain gzip), whose language sections pair every English
server text with its translation. Next step for the bot: a builder that pulls those texts into
`data/`, so each automation reads its menu entries and rules from the game, not from code.
