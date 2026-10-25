<div align="center">

# 👴🌾 Uncle Farmer

### Farms all night. Tells no one.

**A free, open-source farming bot for Mist World.**

[![Licence: GPL v3](https://img.shields.io/badge/licence-GPL%20v3-blue.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
![Requires NVDA](https://img.shields.io/badge/requires-NVDA-green.svg)
![No account needed](https://img.shields.io/badge/account-not%20needed-brightgreen.svg)

</div>

---

Uncle Farmer walks your character to the farming map, fights, drinks potions at the right moment,
looks after your pets, sorts your bag, does your daily chores, and goes home safely when you tell it
to. You don't need an account or a subscription, and it never tells anyone what you do.

Every line of it is here for you to read.

## Why Uncle Farmer exists

Farming in Mist World takes hours, so bots are popular. The ones out there are good. Their
makers put real work into them, they charge for them, and they run through an online account that
talks to their servers while you play. As far as anyone knows, they don't copy your chats or look
through your computer. That's worth saying clearly.

But a bot runs **inside your game**, sees everything your character sees, and is connected to the
internet. With a closed program you can't check what it does; you can only trust it. Many players
can't afford a subscription either, so some turn to **cracked copies** from strangers. That isn't
fair to the people who built those bots, and a crack can carry anything at all onto your computer
and your account.

Uncle Farmer is the other road. Instead of cracking someone's work, we build one together, out in
the open, free for everyone:

- **Nothing to trust blindly.** The code is public; anyone can read it, check it, and build it themselves.
- **Nothing to crack.** It's free. No licence key, no account, no server.

Uncle Farmer isn't here to fight anyone. If you're happy with a paid bot, keep using it. If you
make one, you're welcome to read this code too. It's simply a choice that costs nothing and hides
nothing.

## Our privacy promise 🔒

- **No account, no server, no telemetry.** There's no licence check, and nothing about you or your
  game is ever sent anywhere.
- **One question to GitHub, and you can turn it off.** When it starts, Uncle Farmer asks this
  repository's releases page whether there is a newer version, and offers it to you. That request
  carries no information about you or your game. Untick "Look for a new version when Uncle Farmer starts" in Settings and it
  never goes online at all; without internet it works just the same.
- **Your data stays in its folder.** Character settings, plans, scripts, logs and results are plain
  files next to the program. Delete the folder and it's all gone.
- **The game agent only listens to your own computer.** The small Java agent inside the game binds
  to `127.0.0.1` only and refuses any connection that doesn't know the secret it makes up at every
  launch.
- **No chat logging.** The agent reads the game's network messages to know where you are and what's
  around you. It doesn't write chats or packets to disk. The Chat window keeps the game's chat in
  memory only, gone when you close it. Private messages sent to your character while the bot runs
  are noted in your own log file in the program's `logs` folder, so you can read them later. They
  go nowhere else.
- **Don't take our word for it.** Everything that runs is in this repository.

## What it can do

| | |
|---|---|
| 🗺️ **Plans, no programming** | Pick maps, how long to farm each one, how to move around and which monsters to fight. Stop after a time, at a clock time, after a number of kills, or when an item runs low, then end where you choose. |
| 📜 **Scripts** | A small, readable script language with loops, conditions and stop rules, for anything a plan can't do. |
| 🧠 **Reads the game itself** | Scans each character by itself: class, skills on every key, talents and combos, potions, fusion beasts and pets. You don't have to type them in. |
| ⚔️ **Fights like a player** | Every class fights from its own attack's distance, picks skills by cooldown, rage, marks and combos, uses shields and heals when a pack is on it, and presses skills through the game itself, so nothing you hold on your keyboard gets in the way. |
| 🧪 **Smart potions** | Works out when to drink from the potion's own text, so HP and MP stay near full and nothing is wasted. Potions in the bag work too, even mid-fight. |
| 🎒 **Bag and supplies** | Sort every shard and item into Throw away, Keep, Store or Sell. Refill potions and food from the storehouse or the shop. Unload or stop when the bag is full, and stop when the day's drop time runs out. |
| 📅 **Daily chores** | Bank income, hunting bounties, free pet rebirths, gang donation and construction, and sharing arnebia in the secret room, each once a game day. |
| 💎 **Gem Maker and Equipment Enhancer** | Turns shards into gems and gems into higher ones, and enhances your gear with protection runes, never spending below your arnebia reserve. |
| 🐾 **Pets and beasts** | Keeps your favourite pet out, fed and healed, rides your fastest beast between maps and fights in your strongest one. |
| 🚡 **Travel** | Walks between any two of the game's 375 maps, using cable cars and airships. Walk to a map or a dungeon entrance on its own, too. |
| 🎮 **Several games at once** | Each game window runs its own plan, and its window title says which character it is. |
| 👥 **Teams** | `come` and `follow` by private message, team navigation, and healers that heal the player they follow. |
| 💬 **Chat and private messages** | Read and write every chat channel and play or send voice messages while you farm. Polite replies to strangers while you're away; commands for the friends you trust. |
| 📊 **Results** | Kills, contribution, experience, arnebia and every shard picked up (and how many were thrown away, sold or stored), for this run, today, this week or all time. Gem making and chores are kept apart from farming. |
| 🌍 **Any language** | Works with the game's language packs, and the program itself speaks English and Roman Urdu. |

## Requirements

- Windows and Mist World.
- NVDA, set as the game's default speech.

## Getting started

1. Download `UncleFarmer-<version>.zip` from this repository's [Releases](https://github.com/askunclefarmer/uncle-farmer/releases)
   and unzip it anywhere: it makes an `Uncle Farmer` folder.
2. Run `UncleFarmer.exe`. The first time, it opens **Settings**: choose your Mist World folder (or
   start the game and press **Find the running game**) and press **Install**.
3. Restart Mist World and log in. Make a plan in the **Plans** tab (pick a map and how long), then
   press **Start** on the **Bot** tab.

The **Pause** key starts and stops the bot from anywhere, even inside the game; **Shift+Pause**
speaks your results.

New versions: Uncle Farmer offers them itself when it starts (or Help, Check for updates), and
installs them for you. You can also unzip a newer release over the same folder. Your characters,
plans and settings are never in a release, so they stay.

The full guide is in [docs/USER_GUIDE.md](docs/USER_GUIDE.md), as README.html in the program's
folder, and in the program under Help (F1). More guides, also under Help:

- [Writing scripts](docs/SCRIPTING.md): a step-by-step guide, from your first three lines to full
  examples.
- [How the game works](docs/GAME_GUIDE.md): Mist World's rules and systems, from the game's own help
  and texts.
- [What to automate next](docs/ROADMAP.md): the plan, and where you can help.
- [Performance](docs/PERFORMANCE.md): where a decision's time goes, and how to rerun the benchmarks.
- [What's new](CHANGELOG.md): every version's changes.

Install adds one line to the game's `mw.exe.vmoptions` and keeps the original as
`mw.exe.vmoptions.bak-unclefarmer`. **Remove**, in the same place, takes the line out again.

### Running from source

You need Windows, Python 3.10 or newer (3.14 is what we use), and a JDK 9 or newer (for the game
agent).

```
pip install -r requirements.txt
python agent/build_agent.py
python agent/java_tests.py       # the agent's own tests, then the random-byte parser check
python UncleFarmer.pyw
```

`python build_exe.py` builds the shareable folder `dist\Uncle Farmer` with Nuitka (from
`requirements.txt`); it also needs UPX (`winget install UPX.UPX`), found on PATH, in `UPX_DIR`, or in `F:\upx-5.2.1-win64`.
`python build_native.py` builds the optional C module that makes map searches 10-24 times faster;
without it the same searches run in Python.

## How it works

```
Mist World (Java)                         Uncle Farmer (Python, wxPython)
+------------------------------+          +------------------------------+
| agent (unclefarmer-agent.jar)| loopback | link.py   talks to the agent |
|  World   reads the game's    |<-------->| engine/   bot, combat, travel|
|          network messages    |  secret  |           bag, potions, pets |
|  Keys    presses keys through|          | gui/      the windows        |
|          the game's own keys |          | data/     maps, NPCs, items  |
|  StateServer  127.0.0.1 only |          +------------------------------+
+------------------------------+
```

The game loads the agent itself (`-javaagent`). The agent keeps a model of the world from the
game's own messages and reads the game's own objects (your position, bag, target and dialogs). It
presses keys and skills through the same handlers the keyboard uses, so the game window doesn't
need to be in front. The Python app decides what to do next.

- `unclefarmer/` is the app: `engine/` (the bot), `gui/` (the windows), plus scanning and travel.
  The bot itself (`engine/bot.py`) is put together from one part per job in `engine/botparts/`:
  reading the game, keys, health and potions, walking, the bag, travel, NPC dialogs, fighting and
  farming. Skill choice is `engine/combat.py`, talent combos `engine/combos.py`, the results
  `stats.py`.
- `agent/src/` is the Java agent, with its own tests in `agent/test/`.
- `data/` is what the bot knows about the game: maps, walls, exits, NPCs, shops, skills and shards.
- `lang/unclefarmer.pot` holds every string, for translators. Translations are gettext catalogs.
- `tests/` checks it all without the game, against a pretend game that speaks the agent's protocol.

## Help Uncle Farmer grow 🤝

This is a community project. It gets better every time someone helps, and you don't have to be a
programmer to help:

- 🐞 **Report a bug.** [Open an issue](https://github.com/askunclefarmer/uncle-farmer/issues): say what you did, what
  you expected, and what happened. Attach the day's file from the `logs` folder if you can.
- 🛠️ **Fix something.** Pull requests are welcome, big or small. See [CONTRIBUTING.md](CONTRIBUTING.md).
- 🌍 **Translate it.** Copy `lang/unclefarmer.pot` to `lang/<your language code>.po`, fill in the
  `msgstr` lines and run `python -m unclefarmer --strings` to compile them.
- 🗺️ **Share what you know.** Maps where the bot gets stuck, NPC menus it doesn't understand yet,
  good farming spots, useful plans and scripts.
- 🧪 **Test it.** Try new versions and say how they went.
- 📣 **Tell a friend**, especially one who can't afford a paid bot.

## Support the project 💛

Uncle Farmer is free and always will be. If it saves you hours and you'd like to give something
back:

- ⭐ **Star this repository.** It helps other players find it.
- 🤝 **Contribute.** Your time and fixes are worth as much as money.
- ☕ **Donate.** Write to me at [ask.unclefarmer@gmail.com](mailto:ask.unclefarmer@gmail.com) and I'll tell you how.

## Contact 📧

Questions, ideas, or anything else: [ask.unclefarmer@gmail.com](mailto:ask.unclefarmer@gmail.com). Bugs are best reported as an
[issue](https://github.com/askunclefarmer/uncle-farmer/issues), so everyone can see the fix.

## Play fair

Using a bot may be against Mist World's rules, so you use Uncle Farmer at your own risk. Please be
a good neighbour: don't use it to harass other players, and please don't crack anyone's paid
software. If you need a bot and can't pay, that's exactly what this one is for.

## Licence

Copyright (C) 2026 the Uncle Farmer authors.

Uncle Farmer is free software under the **GNU General Public License v3.0**. You may use, change and
share it, and any version you share must stay open source under the same licence, so it stays free
for everyone. See [LICENSE](LICENSE).

`nvdaControllerClient64.dll` is part of NVDA by NV Access and comes under its own licence; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Thanks

To NV Access for NVDA, to the wxPython team, to everyone who has sent code, and to every player who
reports a bug, translates a line or shares a map. Uncle Farmer is yours as much as anyone's.
