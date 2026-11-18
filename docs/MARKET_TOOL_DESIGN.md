# Market Searcher and Buyer (design, 2026-11-18)

A Tools-menu tool that reads the player market (the Ansible Space-time Marketplace), finds
what the player asks for, keeps the results, and buys the lots the player picks.

Status: built 2026-11-18 (branch feature/market-tool): engine/market.py, gui/market.py (Tools,
Ctrl+Shift+K), the `market` script command, agent 71 `detail`. Tested against a pretend market and
the recorded menus; not yet run live. Two hand visits were recorded on 2026-11-18 (section 1b).

Things settled while building: the agent's reading of an NPC menu puts an entry's words in
"options" (text and raw are empty), so the rows are read from there; the game's category list
order is ... materials (100), collections (102), usable items (101); the market folder is
`market/` beside the program (kept by updates); the starting list `data/market_list.json` was made
from the recorded pages only (86 items in 162 lots, all goods page 1, materials, weapons page 1)
until one full scan replaces it.


## 1b. Recorded 2026-11-18 (central plains, 13:08:10-13:08:51 by the PC clock)

The menu is an ordinary `IN op=62` NPC menu from the marketplace object (npc id -54 in that
city). Each entry is `<flags>\r<code>\r...\r<text>`, entries split by `\n`. A choice is
`OUT op=34 <npc:4> <menu:1> <code ints:4 each>` (menu byte was 0x1b), so the codes below are
exactly what a choice sends.

| Entry | Flags | Code |
|---|---|---|
| buy goods | 12 | `2,-1,0,1` |
| sell goods / the goods I'm selling now / trading description | 8 / 12 / 8 | `4` / `8` / `10` |
| `<Category>; Press to select category` | 8 | `1` (opens the category list) |
| category list entries | 12 | `2,<cat>,0,1` |
| in chronological order / in price order | 12 (0 when selected) | `2,<cat>,<order>,<page>` (keeps the page) |
| page 1, Previous page: N, Next page: N, Last page: N | 12 | `2,<cat>,<order>,<N>` |
| Jump to... ("Please enter the number of pages") | 13 | `2,<cat>,<order>,0` + typed number |
| an item lot | 73 (0x49: number box + details) | `3,<listing id>,0`, prompt "Please enter the purchase quantity"; the typed number replaces the last 0 |
| an equipment lot | 74 (0x4A: yes/no + details) | `3,<listing id>`, prompt "Purchase requires <p> gold coins, are you sure you want to buy?" |
| one of my own lots (the goods I'm selling now) | 14 | `9,<listing id>`, prompt "Do you want to retrieve this item?" |

- **Category ids:** all goods -1, weapons 0, helmets 1, hangings 2, shawls 3, armors 4, belts 5,
  shoes 6, pant legs 7, handwears 8, rings 9, other equipments 12, materials 100,
  usable items 101, collections 102. Order: 0 = chronological (newest first), 1 = price
  (cheapest unit price first).
- **Any page can be opened directly** with one choice (`2,cat,order,page`): no walking through
  pages to reach a remembered one.
- **20 lots a page.** That day: all goods 74 pages (about 1,480 lots), materials 25.
- **Each lot has a listing id** in its code (261463 was the newest; ids only grow). Buying and
  re-finding go by this id, never by row text.
- **The server answers a page in about 0.25 s** (seven pages measured).
- **Details (G):** `OUT op=35 <npc:4> <menu:1> <the lot's code ints>`, sent by the game's own
  `game.i.i()` on the selected entry when its flags have 0x40. The answer is spoken text
  (`IN op=60`): `1 Lovebird Stone; <description>; Seller ID: 1234`. Answered in 0.26 s.
- Price order really is per unit: the last materials page ran 300,000 to 3,000,000 a unit.
- Prices are set by players and can be absurd (33 Lesser Blood at 500,000 each next to 14 at 5):
  the max price box matters.

**Second visit, 13:17:02-13:18:12 by the PC clock: buying.**

- **An item lot is bought in one step:** the number box gives `OUT op=34 ... 3,<id>,<qty>`
  (listing 255347, qty 1) and the server answers `IN op=60` "successfully purchased" in 0.27 s.
  **There is no yes/no for items**: the quantity box is the only question.
- **An equipment lot asks yes/no** (the game's own dialog, from the entry's prompt). Yes sends
  `OUT op=34 ... 3,<id>` (listing 260606, Scepter of Bones, 50 gold), answer "successfully
  purchased". No sends nothing.
- Equipment rows read `<name>;<quality> ;Price: <p> gold coins;Sale time ...`. Their details
  (`op=35` on `3,<id>`) give durability, enhance level, quality, strengthen (the attack range),
  class, level, weight, description and `Seller ID: <id>`.
- **A lot's price can't change.** The price belongs to the listing id. A seller who wants a new
  price has to retrieve the lot and list it again, which gives a new id. So buying by id buys at
  the price that was read. Only the quantity left can shrink.
- Weapons had 2 pages that day (no "Last page" entry when there are only 2).
- Bought in the recording: 1 Bactrian camel's metatarsal (5), 1 Golden Eagle's Wings (5),
  Scepter of Bones (50).

Still unseen: the refusal answers (a `New;` lot, not enough gold, already sold, more than is
left, bag full). The buyer treats any answer other than "successfully purchased" as not bought,
shows the game's own words, and checks the bag. No more recording is needed to build.


## 1. What the game already tells us (no recording needed)

From the game's own language files (`language/english/more/equipment_market.txt`, the Turkish
`store-spaceMarket.txt`, which holds the whole market menu) and `data/knowledge.json`:

**Where.** A marketplace stands in every city. `knowledge.json` has it under the service
`market` on maps 1021, 1041, 1061, 1081, 1101, 1121, 1141, 1161 and the Ocean secret room (209).
The bot's town code already walks to services, so "go to the nearest marketplace" is free.

**Menus.**

| Level | Entries |
|---|---|
| Top | buy goods, sell goods, the goods I'm selling now, trading description |
| Buy goods | `<category>; Press to select category`, `in chronological order` / `in price order` (the selected one ends in `; Selected`), the rows, `Previous page: N`, `Next page: N`, `Last page: N`, `page 1`, back |
| Categories | All goods, Weapons, Helmets, Hangings, Shawls, Armors, Belts, Shoes, Leggings, Bracelets, Rings, Other equipments, Materials, Collections, Usable goods |

**There is no name search in the game.** Finding an item means turning the pages of a category.
The server can sort a category by price (cheapest first), which lets a cheapest-first search stop
early.

**Rows** (the parser's patterns):

- stackable item: `<qty> <name>;Unit price <p> gold coins;Sale time <month> <day>, <hh>:<mm>, <year>`
- equipment: `<name>;<quality> ;Price: <p> gold coins;Sale time ...`
- a row still in its first hour starts with `New;` and ends with
  `<m> minutes and <s> seconds remaining in the display time`. It can't be bought yet
  ("New listing goods cannot be traded within one hour").

**Details (G key on a row)** give the seller:
`<qty> <name>; <description>; Seller ID: <id>` for items, and for equipment the durability,
quality, strengthen, class, level, weight and `Seller ID: <id>`. The row itself carries no seller,
so a search by seller needs one details look per row.

**Buying.** `Please enter the purchase quantity` (stackable lots can be bought in part), then
`Purchase requires <n> gold coins, are you sure to buy?`, then `successfully purchased` or a
refusal. Collections have a daily cap: "Exceeded the maximum number of collected items that one
player can purchase per day". Money is gold coins, not arnebia. The seller pays 5%.

**What the bot can already do.** Open an NPC menu, read every entry's text (`menus.read`),
choose an entry by its code (agent 55 direct choose), turn pages (`crafting.py` does it for the
Forger), type a number into a "how many" box (`town.type_number`, used by Shard Market buying),
and check what really arrived in the bag (`transactions.InventoryTransaction`).

**Item names for the pick list.** `item.txt` (about 1,700 names) and `equipment.txt` (about
1,780) in the language folder. They have no "can be traded" flag, so the list is seeded from them
and every name actually seen on the market is marked "seen for sale" (see 4).


## 2. What still has to be recorded by hand

Nothing more is needed to build. Both visits are in section 1b. The refusal answers will be
seen the first time they happen live; the buyer reports the game's own words until then.

What only you can decide: which items go on your list, your price limits, and every purchase
(the tool never buys without your Buy press in its window or an explicit script line).


## 3. How searching works

All searches read pages into one **snapshot**: every row with its page, place, quantity, unit
price, total, sale time or display time left, kind (item or equipment) and, when looked up, seller
id. A snapshot is saved (`market/<YYYY-MM-DD>/<hh-mm-ss>-<category>.jsonl` beside settings), so
results stay readable and buyable after the market window is closed, and the next search can show
what is new or gone.

| Mode | How it reads | When it stops |
|---|---|---|
| **By item** | the item's category (known from the catalog, else All goods), chronological order, all pages; keeps rows whose name contains any of the typed names (several names joined by `|`) | last page, or the page limit |
| **Cheapest first** | same category, **in price order** | the first page whose cheapest row is above the max price (if one is set), or once N matching lots are found at or under it |
| **By seller ID** | category (default All goods) page by page, G details on each row not already known | last page; known rows come from the details cache |
| **Browse** | one category, either order, pages one to N, no filter | page limit |

**The details cache.** A lot's seller never changes, so it is saved under the listing id.
A second seller search only looks up lots listed since (ids above the highest one seen). A lot
missing from a full read of its category is dropped. Partly bought lots keep their id.

**Search by seller ID (for example "everything 1234 is selling").** The row never names the
seller, so the tool finds out by looking:

1. It reads the chosen category (default: every category, as in the full scan), page by page.
2. For every lot on the page whose seller isn't in the cache yet, it selects the lot and presses
   details the game's own way (`game.i.i()`), then reads "Seller ID: N" from the spoken answer.
   Lots already in the cache cost nothing.
3. Each lot whose seller matches goes straight into the results list while the search goes on,
   so the first finds can be read (or ticked for buying) before it ends. Stop works at any time,
   and the lots looked at so far stay in the cache.
4. At the end: "1234 sells 7 lots (materials 5, weapons 2), 74 pages, 1,480 lots, 1,312 looked
   up, 11 minutes".

- **A player can have at most 10 lots on sale at once** (game rule, from the user; a lot can
  be a stack). So once 10 lots of a seller are found, nothing of theirs is left: the search stops
  there ("all 10 found"), and with several IDs it stops when each has 10. Fewer than 10 can only
  be proven by reading to the end. With the neighbours-first order below, a seller who lists in
  batches is often complete after a few pages. If an 11th lot ever turns up, the rule is wrong:
  the tool logs it and keeps reading.
- Several IDs can be typed at once (`1234, 5678`).
- `New;` lots are looked up too, so a seller's lots that open within the hour are listed with
  their opening time.
- A seller often lists several things in the same minute (the selling list showed five lots
  listed at 15:53 with ids 261151-261155). So when a lot matches, its neighbours with the same
  sale time are looked up first: on a stopped search they are the likeliest other finds.
- **My sellers** (settings): seller IDs searched are remembered, with a note the player can type
  ("cheap bloods"). Picking one searches again. After the first time, only new lots need a look,
  so a repeat search takes about as long as reading the pages (under two minutes).
- With "Also count sellers" ticked, the full scan fills the cache, and a seller search right
  after it needs no lookups at all.

**Speed (measured: 0.25 s a page, 0.26 s a details look; the bot adds its own menu wait).**
Materials (25 pages) about 10 seconds, the whole market (74 pages, about 1,480 lots) under a
minute. The first seller search over the whole market needs about 1,480 details looks, roughly
10 minutes; after that only new lots, usually a few dozen. The tool says the page count and time
when done.

**Cheapest first stops early:** in price order, once a page's cheapest lot is above the max
price, no later page can match.

Results come back sorted cheapest unit price first, then oldest listing, and can be re-sorted by
name, quantity, total, seller or page.


## 4. The market list ("what's for sale", by category)

The pick list is built from the market itself, so it only holds things people really sell.

**Full scan.** The first time the tool opens on a server, and whenever the player presses
"Refresh the market list", it reads every category one after another (weapons ... rings, other
equipments, materials, usable items, collections), all pages, chronological order. That is the
same lots as All goods (about 74 pages that day) but each lot gets its category. Measured page
speed puts it at about one to two minutes. The scan also fills a snapshot, so the first search
after it needs no reading at all if the list is fresh.

**The list it makes** (user data, `market/list-<server>.json`, one per game server S1/S2):

| Category | Item | Lots | In all | Cheapest | Usual | Sellers |
|---|---|---|---|---|---|---|
| materials | Lesser Blood | 12 | 340 | 5 | 10 | 5 |
| materials | Worthless Blood | 4 | 61 | 5 | 5 | 3 |
| weapons | Savage invader (perfect) | 2 | 2 | 20,000 | 20,000 | ? |

- Lots = listings, In all = total quantity, Usual = the median unit price.
- Lots still in their display hour are counted apart ("+2 opening soon").
- Equipment is listed by name and quality, since quality changes the price a lot.
- **Sellers** needs one details look per lot (about 10 minutes for the whole market the first
  time). So it is a separate tick, "Also count sellers", off by default. Sellers are cached by
  listing id, so after the first time a refresh only looks up new lots. Without it the column
  shows "?" and Lots stands in (one seller often lists several lots).
- Each refresh keeps the old list's prices, so the list can say "cheaper than last time".

**Ready-made list, before the first scan.** Uncle Farmer ships `data/market_list.json`, made from
one full scan during development. It has every item name seen for sale with its category, and
the starting prices marked with their date. A new player sees real names and categories at once,
and "Refresh" replaces the numbers with today's.

**Every language pack.** The tool doesn't keep its own word lists. Rows are read through the same
agent menu read as every other menu, and Uncle Farmer's existing game-language table
(`gamelang.py`, built from the player's chosen language pack) turns them back to the game's
English. The market's row templates are in the packs (`equipment_market.txt`,
`store-spaceMarket.txt`), so the numbers always parse.

- Each item is stored under its English name when it can be turned back, else under the name as
  shown. Lots are always known by listing id, which no language changes.
- The list shows names the way the player's pack shows them, and the category names come from
  the menu itself.
- Typing in the player's language or in English both find the item.
- To check live once: one scan with a non-English pack selected.

**Also in the pick list:**

- **My list** (settings, shared by all characters): every item searched is added (newest first,
  up to 100) with an optional max price. "Remember" adds one by hand; items can be removed.
- **Custom name box**: any text, matched as "contains" with case ignored, for names the list
  doesn't have yet (listed after the last refresh).


## 5. How buying works

From any results list (even one read 20 pages ago), tick the lots to buy and set a quantity for
each (up to the lot size; equipment is always 1). Buy shows one summary: "Buy 4 lots, 23 items,
12,480 gold coins in all?" and an optional spend cap. After Yes, for each lot:

1. Open the marketplace (walking there if needed), the same category and the same order the
   snapshot was read in.
2. Find the lot again **by its listing id**: open its remembered page directly
   (`2,cat,order,page`), then the pages either side, then the whole category, because lots move
   as others buy and list. Never by row number or text alone.
3. If its quantity dropped, buy what is left only if it is still at least what was asked
   for; otherwise "only N left" and it is skipped (ask again on the next Buy).
4. Check on the page just read, before choosing: the same listing id, not `New;`, the price at or
   under the lot's max, and quantity x price still under the spend cap. The price can't have
   changed (it belongs to the id), so this check is the safety step for items, which get no
   yes/no from the game.
5. Item lot: choose it and type the quantity (one step, `3,id,qty`). Equipment lot: choose it and
   answer **Yes** only when the game's "Purchase requires N gold coins" names the price read;
   otherwise answer No.
6. Wait for the answer ("successfully purchased" = bought; any other text = not bought, shown as
   the game says it), then check the bag (InventoryTransaction): what arrived.

Each lot ends as one of: bought N, gone (sold or taken back), only N left, still on display
(buyable at hh:mm), over your max, not bought (with the game's words: not enough gold, bag full,
daily limit ...), or "no answer" with the log line. Only the final summary and problems are spoken; the rest goes to the window and the
log. A lot that failed stays ticked so Buy can be pressed again.

`New;` rows are shown (greyed, with the time they open) and can be ticked: the buyer waits for
their display hour to end only if the player chose "wait for it" in that row.


## 6. Window (Tools, Market searcher and buyer)

Plain controls in tab order, no menu separators (NVDA counts them):

1. Category (combo: My list, All, then the 14 categories, each with its item count).
2. Items in that category (list: name, lots, in all, cheapest, usual, sellers). Type to jump.
   Enter on an item searches for it. The list's age is in the status line ("market list from
   today 13:20, 1,462 lots").
3. Refresh the market list (button) and "Also count sellers" (tick box).
4. Search kind: By item, Cheapest first, By seller ID, Browse category (radio buttons).
5. Custom name (text box; overrides the picked item when filled).
6. Seller ID (text box, enabled for By seller ID).
7. Max unit price, Pages at most (default 30).
8. Search / Stop. Status line (pages read, lots matched, time).
9. Results list: tick, name, quantity, unit price, total, seller ID, page, listed / opens at.
   Space ticks, a number key row or the Quantity field sets how many.
10. Quantity for the selected lot, Spend cap, Buy ticked / Check only (does every step but the
   last choice, or answers No for equipment), Save list (CSV), Remember this item.
11. Earlier searches (combo of saved snapshots) to reopen any past result list.

Script and trigger commands, same engine:
`market search <name|seller:ID> [category] [max <price>]`,
`market buy <name> <qty> max <unit price> [cap <gold>]` (cheapest lots first, never above max).


## 7. Code shape (follows the master plan)

- `data/market.json`: the recipe: service name, menu entry patterns (from the language files),
  categories, page entries, row patterns, the confirm and answer texts, codes once recorded.
- `unclefarmer/engine/market.py`: pure parts (row parser, snapshot, matching, lot identity,
  confirm-price check) plus `open_market`, `read_pages`, `details`, `buy_lot`, all through the
  existing menu, choose and type_number calls. No hand-made packets.
- `unclefarmer/gui/market.py`: the window; work runs on the bot thread like the other tools.
- `unclefarmer/engine/activities.py`: one entry, so triggers and the script get it.
- Tests: the row parser over every row form in `equipment_market.txt`, lot re-finding when rows
  move or vanish, the confirm-price refusal, the spend cap, and a pretend market in the pretend
  game (pages, details, buy, refusals) built from the recorded visit, anonymised.
- Agent: reading and paging need nothing new (menu read, direct choose by code). Details need
  one small agent command, `detail <row> [sig]`: select that entry the way `choose` does, then
  call the game's own `game.i.i()` (the G key's method), and hand back the next `op=60` text.
  That is a new agent number (next free on dev when pushed). Buying needs nothing new:
  direct choose with a typed number (as Shard Market buying does) and the yes/no answer.

Build order: parser + snapshot + full scan + market list (with the shipped `data/market_list.json`
from one dev scan) + By item / Browse / Cheapest first (read only)
-> agent `detail` + details cache + By seller ID -> Buy with Check only
-> real Buy -> script commands.
