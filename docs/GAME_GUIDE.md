# Mist World: how the game works

Everything here comes from the game itself: its help pages (Help.htm and the help folder), its own
texts (language\english\more: items, NPCs, equipment, gang and system messages), the translation
file version\language.gz (which holds the text of every menu, instruction page and mission the
server can send, because translators need all of it), and the NPC menus as the game shows them. It's the knowledge the bot is built on, and a reference for anyone
who wants to automate more of the game.


## The world

- A top-down 2D world with fixed directions: W north, S south, A west, D east. A character turns
  before walking (a light "swish"), and the way you face decides which side a wide attack covers.
- Footsteps sound only when you cross a whole coordinate; the body has an outline and fills its
  cells.
- Casting a skill stops you for a moment (cast recovery). Chasing monsters move faster than
  usual, so long-range classes often swap follow attack and stationary attack (F1 settings).
- Most skills share a **common cooldown** that depends on the class (Assassin fastest, then
  Warrior; Orc slowest).

**Main route by level** (Central Kingdom):

| Level | Area |
|---|---|
| start | Karon Village (finish its mission to open it) |
| 11-15 | Brute Valley |
| 15 | Mario Basin: Sunset City, Northern Town, Breezy Village (first city, many missions) |
| 16-20 | Karst Cave (Northern Town's north gate) |
| 21-25 | Fred Forest (Breezy Village's east entrance) |
| 26-30 | Takama Desert |
| 30 | Central Plains: Giant Rock City (first capital: weddings, competitions, Gang Tournament), Mettle Village, Starlight Village |
| 30 | Beauty of River and Mountain (fishing), Abundant Green Hills (herbs), Underground Mine (mining, by cable car) |
| 31-35 | Wolf Prairie |
| 36-40 | Polson Wetland |
| 41-45 | Vincent Snowfields |
| 45 | Fertile Wilds: White Sand City, Traveler Camp, Tari Village |
| 46-50 | Carter Plain |
| 66-70 | Marion Sea (from Sailing Port) |

West of the continent is Loki Union (capital Tulip City), east is Garth Empire (capital Holy Blood
City), each with higher-level areas and two more cities. Cable cars and airships link the
capitals and cities.


## Experience and loot

- Same level: standard experience. Monsters 1-4 levels above give 104-116%; 5 or more above
  give 120%. Each level below gives 4% less; 25 or more below gives nothing.
- A team gets +10% per extra member, shared out; teammates within 40 yards share it.
- **Loot on public maps:** monsters up to 9 levels below you always drop; 10-19 below drop half the
  time; 20-29 below a quarter; 30 or more below never. Dungeons mostly follow the same rule.
- Experience and loot go to whoever lands the killing blow; when several players fight over one
  monster, to the one highest on its threat list.

*For the bot:* the best farming maps for a character have monsters from 9 levels below to about 5
levels above it.


## Combat rules

- **Threat:** damage makes threat; monsters share threat with companions nearby; a monster switches
  target when someone has 115% of its current target's threat; healing makes threat at 70% of the
  amount healed; taunts ignore the list.
- **Buffs and debuffs:** your own buffs can't be dispelled or replaced. Others' buffs of the same
  kind replace each other (a weaker one can overwrite a stronger one). Physical-defence debuffs stack
  up to 20% and toughness debuffs up to 40%, but only when refreshed before they run out. Magic,
  curse and poison debuffs each need their own dispel.
- **Control:** stun, restrain/ensnare, knock-out/freeze (broken by damage), transfix/fire-trap,
  silence. Too much of one kind gives 15 seconds of immunity to it. Bosses and city guards are
  immune to control.
- **Morality:** starts at 0, from -2000 to 2000, and sets the name's colour. Killing players costs
  morality; red and black names can drop items when killed, and black names risk 20% equipment
  wreck on every death. Farming monsters on public maps gives +5 every 10 minutes.


## Character

- **Eight classes:** Warrior (all-round tank), Orc (physical tank), Assassin (burst, magic tank),
  Hunter (ranged physical), Mage (ranged magic, control), Elf (ranged magic burst), Priest (healer),
  Witcher (healer with offence). Each class has its own counter, read with T: rage (Warrior, Orc),
  combo (Assassin), marked targets (Hunter), Ice Spike (Mage), fire element (Elf), allies and
  enemies near you (Priest), the target's agony (Witcher).
- **Combat skills** are learned from Masters by level (Level 15 skills from Master Taufan, Level
  25 from Master Drunk, class skills from the senior mentors) and from skill crystals. Ten attack
  keys, two pages (Caps Lock).
- **Talents** are passive. General talents protect against terrain (Anti-Corrosion, Turtle Breath,
  Poison Immunity); class talents boost the class. A talent must be learned, then **activated**
  with talent points (cultivated at a Master or the gang's Guru Elder for arnebia, and gang
  contribution in the gang) and arnebia.
- **Profession skills** (Z, X, C, then Space):
  - corpse harvesting, one of: Draw Blood (blood, for red potions), Skinning (pelts, for blue
    potions), Cut Flesh (jerky, for food);
  - production and collection, one of: Mining, Fishing, Collect Herbs, only in their areas (mining
    areas, lakes, herbal gardens, and the gang's own production area); an important source of
    arnebia;
  - Snuggle (married couples) and Blood Transfusion (tutor and pupil) come by themselves.
- **Accounts:** up to 8 characters; gender is fixed; a character can be deleted after 10 days
  without logging in.


## Equipment

- Drops by area: Brute Valley level 15 gear, Takama Desert level 30, Vincent Snowfields level 45,
  Dark Pit City level 60, Magnet Hill level 70. The other areas mainly drop **shards**.
- Monster parts ("Alpaca's Pelt", "Robot Dog's Tail"...) are materials for crafting level 30 to 100
  equipment.
- Durability wears down: equipment becomes **Damaged**, then **Wrecked**. Blacksmiths repair it, as
  do repair tools (pliers, toolboxes, Dark Iron water for wrecked gear).
  - The game warns in the combat packet: after the skill name come pairs "item id, flag" - flag 0
    "is about to break", anything else "is Damaged" (the client then marks the item's equipped
    field negative, and a broken item gives no stats).
  - Pliers repair one item; toolboxes repair all equipped gear (Small up to level 60, Large up to
    80, Alloy any level) after "Do you want to repair all the equipment that is already
    equipped?"; repair scrolls repair everything in the bag.
- **Forger**, from its own instructions:
  - *Enhance* needs one Enhance Crystal per try: level 1 crystals for gear of levels 15-29, level 2
    for 30-44, level 3 for 45-59, then one crystal level higher for every 10 gear levels. The chance
    falls as the level rises (5% at +11); a failure drops the enhancement level by 1 unless an
    Enhance Protection Rune is used; each Boost Rune adds 5%. From +5 the name gets a prefix:
    +5-7 Refined, +8-9 Great, +10 Epic, +11 Heavenly, +12 Godbless.
  - *Smelt* (always succeeds): a second item of the same type, with a lower enhancement level,
    passes its quality and extra effects to the first (unless the first already has effects).
  - *Elevate* (always succeeds) lifts level 30+ Great to Godbless gear one level stage, with Pale
    Jade or Violet Haze Crystals and arnebia; it costs one enhancement level and keeps sockets and
    gems.
- **Artificer:** add sockets to equipment and insert or take out gems (a Socket Protection Rune
  keeps a socket's colour).
- **Recycling:** weapon, armour and jewellery merchants and the Recycle Station take old equipment.


## Shards, gems and crystals

Shards are the raw material for gems. Each shard makes one kind of gem, and each gem fits a socket
of its own colour:

| Gem | Socket | Gives |
|---|---|---|
| Ruby | Crimson | maximum HP |
| Sapphire | Dark Blue | maximum MP |
| Tiger Eye Stone | Brown Yellow | physical attack |
| Opal | Honey Yellow | magic attack |
| Spinel | Pink | physical defence |
| Aquamarine | Blue | magic defence |
| Garnet | Dark Purple | hit chance |
| Blond Jade | Dark Yellow | dodge |
| Grape Stone | Blue Purple | defence break chance |
| Olivine | Dark Green | defence break damage |
| Topaz | Golden Yellow | toughness |
| Obsidian | Black | critical hit chance |
| Sunlight Stone | Orange | critical hit damage |
| Moonstone | White | critical hit resist |
| Turquoise | Emerald Green | movement speed |

The **Gem Craftsman** (in every city and in the gang) offers *shard synthesis*, *gem synthesis*,
*batch gem synthesis* and *break into shards*. Its own instructions say:

- **4 shards** of the same colour and level make **1 gem** of that level.
- **3 gems** of the same colour and level make **1 gem one level higher**. This has a **50% chance
  to fail**, and a failure shatters one gem into a shard of the same level.
- For level 2 and higher gems, adding a **shard of the same colour and level** raises the chance by
  **20%**; a **Gem Protection Rune** stops the gem from shattering on a failure.
- A gem can be split into 3 gems a level lower, or into 3 shards of its level.

Weapon and Equipment Enhance Crystal Shards make the Enhance Crystals the Forger uses.

**Sockets** (Artificer): equipment above level 15 can get sockets: 1 from level 15, 2 from 30, 3
from 70, 4 from 90. The socket's colour decides the gem; reforge if you don't like the colour.
Taking a gem out costs a fee.

The **Shard Broker** (Shard Marketplace) buys and sells shards at prices that change.


## Potions and food

- Red potions restore HP, blue ones MP: a share of the maximum plus a fixed amount, with a
  cooldown ("Recover 15% Max HP and 1500 points of HP, 11 seconds CD"). Over-time potions heal
  every 2 seconds. Pet potions are separate.
- Food restores HP slowly over a long time and grows more useful as HP grows; it can only be
  crafted, and selling it is a good income.
- The **Pharmacist / Apothecary** sells, makes and fuses medicine; the **Chef** makes food.
  Ingredients come from corpse harvesting (blood, pelts, jerky).


## Fusion beasts

- **Swift** beasts make you much faster (up to twice your speed) but block every skill: for travel.
- **Fierce** beasts add speed, HP, MP, attack and defence and keep your skills: for fighting.
- Transforming costs **fusion power** (the second value of T). You can't transform below 10; at 0
  the transformation ends; it recovers while you're not transformed. "Lord", "Mist" and similar
  versions cost no fusion power.
- Beast crystals are crafted from beast materials (Werewolf, Minotaur, Mammoth crystals...).


## Pets

- Summoned from soul stones (by level band: frog, beetle, rooster, duck at 30; donkey, goat, steed,
  cow at 45; up to snow wolf, lion, cheetah, tiger at 90), or blessed pets (Pixie, Sprite, Goblin,
  Slime, Cherub) that keep growing.
- **Personality:** Cautious and Loyal follow your target (melee classes); Smart and Courageous keep
  their own target and can tank.
- **Pet Rebirth** at a Pet Trainer re-rolls the five assets (Strength, Physique, Spirit, Resolve,
  Agility): **2 free per day**, then diamonds. Rebirth points build towards mutations (primary up
  to perfect).
- Pets can't enter adventure or level dungeons, or competitions.
- Pet skill crystals teach pet skills; a talent protection item keeps a chosen pet talent.


## Gangs

Joining a gang opens:

- **Daily activity:** construction (each build gives gang contribution, e.g. 2500), one donation a
  day at the Head of the Inner Hall (10,000 or 20,000 arnebia, or 20 diamonds), and gang
  **missions** (also at the Head of the Inner Hall). Each counts towards the gang's daily
  activity.
- **The gang base:** Guru Elder (cultivate talent points, gang skills, profession skills), its own
  Forger, Gem Craftsman, Artificer, Pharmacist, Chef and Equipment Maker at better prices, storages
  for medicine, equipment and materials, an animal shed, and buildings to construct.
- **Production area:** the gang's own mining area, herbal garden and lake, with boost facilities;
  choose your collection skill to match it. They also make the gang's own craftsmen cheaper: the
  **Lake** lowers the cost of making gems and adding sockets, the **Mining Area** the cost of making
  and forging equipment, and the **Herbal Garden** the cost of medicine and food. The higher the
  area's level and its boost facilities, the bigger the discount, so different gangs pay different
  prices. Every gang base has the same layout: Forger, Gem Craftsman and Artificer stand in the same
  places in all of them.
- **Gang dungeon, Gang Tournament** (bidding, then a tournament), allied gangs, supply camps and
  magic cannons, highlands.
- Mentorship points from pupils can be exchanged for gang contribution.


## Missions and daily limits

- **Repeatable kill missions** from city lords and others: "Eliminate 100 monsters on the
  grassland, 10,000 experience, up to 8 times", Polson Wetland insects (5 times), Flaming Mountain
  (level 60, 10 times), rainforest (level 70), Brute Valley (30 monsters, 8 times). Some can be
  taken again only after an hour.
- **Hunting bounties** are collected from the city officers (General Paul in Sunset City, Colonel
  Demar in White Sand City and others), who also reset talents.
- **Gang missions:** taken at the Head of the Inner Hall; only **two kinds a day**. Examples from the
  game's texts: deliver medical supplies from the gang's potion merchant, help water crops against
  locusts, collect materials.
- **Vitality:** every profession skill (mining, fishing, herbs, corpse harvesting) uses Vitality;
  the daily amount is enough for normal play and more can be bought.
- **Gang production areas:** one entry a day per player, for a fee set by the gang; results depend on
  the area's level, its saturation (best above 50%) and boost facilities.
- **Experience dungeons** (Spider Cave and others): one a day, as many monsters as you can within the
  time limit; pets fight on their own there. The **Wraith Cave** is single-player and can be entered
  often, but rewards count once a day. **Nether Cave** (3 players) and **Demon Cave** (5 players)
  unlock deeper floors by repeats.
- **Secret rooms:** 3 hours of practice a day for drops and experience, which uses 5 hours of
  public-area drop time.
- **Gang dungeons** refresh daily and give double rewards for the first clear each week.
- **Arena and Tactical Battles** (from level 40): every two hours from 1:00; each kind twice a day.
- **Gang Tournament:** weekly; register on Tuesday, matches Wednesday, Friday and Sunday at 20:00.
- **Royal training ground** (level 30+): free player fights; dying there drops part of the ore,
  fish and herbs you carry.


## Other systems

- **Bank:** deposit diamonds and collect income once a day (10,000 diamonds earn 1000 gold coins and
  50,000 arnebia a day); income starts the day after a deposit and doesn't carry over if not
  collected. "Value Products" from the Value Mall (like the 30-day card: 60 bound diamonds and
  50,000 arnebia a day) are collected at the bank too, once a day.
- **Recycling:** things recycled in the last 48 hours can be bought back for double the price.
- **Storehouses:** the Ansible Space-Time Storehouse (items, equipment, beasts, free) and the Soul
  Storehouse (soul-bound gear, shared by every character on the account).
- **Market:** the Ansible Space-Time Marketplace trades between players for gold coins.
- **Travel items:** Hearthstone (back to the last city), Memory Butterfly (save a place, return to
  it), teleport stones for each city, and Spirit Land crystals.
- **Experience cards:** +30% to +100% experience for 30 minutes; the bonus ends at a server restart.
- **Competitions and arena:** daily competitions (Competition Host), Arena Fights and Tactical
  Battles with medals and battlefield merit points.
- **Tutor and pupil:** from level 60 you can take pupils into the tutor-and-pupil dungeon every day.
- **Marriage:** from level 30 with romance, lovebird or eternity stones; rings, the Snuggle skill, a
  couple dungeon and the wedding events.
- **Events and mini-games:** treasure hunts (Wonderland, under the sea), Lucky Draw, festive
  hunting, Mist God Valley games (500 morality to enter, white names), seasonal gift boxes.


## Hotkeys the bot relies on

| Key | Does |
|---|---|
| W A S D | walk; browse a window |
| K, O, L, ; | pick a target west, north, south, east (Alt reverses) |
| V | which kind of target the keys pick |
| P | move to the target |
| Enter | talk / choose |
| Esc | close a window |
| Left Ctrl+Enter | the Action menu |
| Y U I, H J, N M , . / | the ten attack keys (Alt: stationary attack) |
| Caps Lock | second page of attack keys |
| 1 to = | item hotkeys |
| Arrows, Right Ctrl, G | browse the bag, use an item, read its description |
| Shift+Delete | throw an item away |
| Z X C, then Space | profession skills |
| R, T, ` (backquote) | HP and MP; counters and fusion power; the character sheet |
| B | PK mode |
