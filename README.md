<div align="center">

# 👴🌾 Uncle Farmer

### Farms all night. Tells no one.

**A free, open-source farming bot for Mist World.**
</div>

Welcome to Uncle Farmer!

Farming in Mist World can take a lot of time. Travelling between maps, fighting monsters, collecting items, managing supplies, and looking after your pets can keep you busy for hours.

That's where Uncle Farmer comes in. It's a free, open-source farming bot that helps take care of these routine tasks while you focus on whatever else you'd like to do.

Set up a farming plan, start the bot, and let it do the work. You can pause it, stop it, or change your plans whenever you want.

Uncle Farmer is free for everyone. There's no subscription, licence key, or account to worry about. And because the source code is public, you're welcome to explore it, see how it works, or even help make it better.

Whether you're just looking for a little help with farming or interested in contributing to the project, you're welcome here!

## Why Uncle Farmer exists

Farming in Mist World takes hours, so it's no surprise that many players use bots. There are already good ones out there. Their developers put real work into them, charge for their products, and run them through online accounts that communicate with their servers while you play. As far as anyone knows, they don't copy your chats or look through your computer. That's worth saying clearly.

But a bot runs **inside your game**. It can see everything your character sees, and it may be connected to the internet. With a closed-source program, you can't check what it does. You have to trust its developers.

Many players also can't afford a subscription. Some end up looking for **cracked copies** from strangers. That's unfair to the people who built those bots, and cracked software can put both your computer and your game account at risk.

We wanted another option: a bot that anyone can use, inspect, and help improve.

That's why we're building Uncle Farmer in the open:

- **Nothing to trust blindly.** The source code is public. Anyone can read it, check it, and build it themselves.
- **Nothing to crack.** It's free for everyone. No licence key, no account, and no private server.

Uncle Farmer isn't here to compete with or put down other bot developers. If you're happy with a paid bot, that's fine. If you build one yourself, you're welcome to explore this project too.

We simply want to offer another choice: one that's free, open, and built for the community.

## Your privacy matters 🔒

We want you to feel comfortable knowing what Uncle Farmer does with your data. Here's how it works:

- **No account or private server.** There's no account registration, licence check, or telemetry.
- **Optional update checks.** When enabled, Uncle Farmer checks this repository's GitHub releases page for newer versions. That request doesn't include your character or game data. You can turn the check off in Settings by unticking "Look for a new version when Uncle Farmer starts". With the check disabled, Uncle Farmer doesn't go online to look for updates.
- **Your files stay on your computer.** Character settings, plans, scripts, logs, and results are stored as ordinary files beside the program.
- **The game agent stays on your computer.** The small Java agent inside the game listens on `127.0.0.1` only. It also requires a secret that is generated each time it starts.
- **Your chat isn't sent to an Uncle Farmer server.** The agent reads the game's network messages so it can understand what's happening. It doesn't write chat messages or network packets to disk. The Chat window keeps the game's chat in memory and clears it when you close the window. Private messages received by your character while the bot is running are saved in your local `logs` folder, so you can read them later.
- **You can inspect the code yourself.** Everything that runs is available in this repository. You don't have to take our word for it.

## What it can do

Uncle Farmer has plenty of features to help with everyday farming and more complicated tasks.

- 🗺️ **Farming plans:** Choose your maps, how long to farm each one, how to move around, and which monsters to fight. Set conditions for stopping, such as a time limit, a kill count, or an item running low. You can also choose where your character should go when farming ends.

- 📜 **Scripts:** Use a small, readable scripting language with loops, conditions, and stop rules. It's there for tasks that need more than an ordinary farming plan.

- 🧠 **Automatic character scanning:** Uncle Farmer reads each character's class, skills, key bindings, talents, combos, potions, fusion beasts, and pets. You don't have to enter all that information yourself.

- ⚔️ **Class-aware combat:** Each class fights according to its own attack range and abilities. Uncle Farmer chooses skills based on cooldowns, rage, marks, and combos. It also uses shields and healing when needed, and sends key presses through the game itself without interfering with keys you're holding.

- 🧪 **Smart potions:** Uncle Farmer reads the potion's own description to decide when to use it. It keeps HP and MP topped up while avoiding unnecessary waste. It can also use potions from your bag, even during a fight.

- 🎒 **Bag and supplies:** Decide which shards and items to throw away, keep, store, or sell. Restock potions and food from the storehouse or shops. You can also have the bot unload items or stop when your bag is full or the day's drop time runs out.

- 📅 **Daily chores:** Handle daily tasks such as banking income, hunting bounties, free pet rebirths, gang donations and construction, and sharing arnebia in the secret room.

- 💎 **Gem Maker and Equipment Enhancer:** Turn shards into gems, combine gems into higher-level ones, and enhance your equipment with protection runes. Uncle Farmer respects your arnebia reserve and won't spend below it.

- 🐾 **Pets and beasts:** Keep your favourite pet active, fed, and healed. Ride your fastest beast when travelling between maps and use your strongest one for fighting.

- 🚡 **Travel:** Travel between any of the game's 375 maps using walking, cable cars, and airships. You can also ask Uncle Farmer to walk to a map or dungeon entrance on its own.

- 🎮 **Multiple game windows:** Run several game windows at once. Each window can have its own character and farming plan.

- 👥 **Team support:** Use `come` and `follow` through private messages, navigate as a team, and let healers support the player they're following.

- 💬 **Chat and private messages:** Read and send messages in every chat channel. You can also play or send voice messages while farming, set up polite replies to strangers, and give trusted friends access to bot commands.

- 📊 **Run results and statistics:** Track kills, contribution, experience, arnebia, and every shard picked up. See how many items were thrown away, sold, or stored. Check results for the current run, today, this week, or all time. Gem making and daily chores are tracked separately from farming.

- 🌍 **Language support:** Uncle Farmer works with the game's language packs. The program itself is available in English and Roman Urdu.

## Requirements

Before getting started, you'll need:

- Windows and Mist World.
- NVDA, set as the game's default speech.

## Getting started

Ready to give it a try? Here's how to get started.

1. Download `UncleFarmer-<version>.zip` from the [Releases page](https://github.com/afnannasir/uncle-farmer/releases/latest) and unzip it anywhere you like. This creates an `Uncle Farmer` folder.
2. Run `UncleFarmer.exe`. The first time you start it, the program opens **Settings**. Choose your Mist World folder, or start the game and press **Find the running game**. Then press **Install**.
3. Restart Mist World and log in. Open the **Plans** tab and create a plan by choosing a map and how long to farm. When you're ready, open the **Bot** tab and press **Start**.

That's it! Uncle Farmer is ready to work.

A few useful things to know:

- Press **Pause** to start or stop the bot from anywhere, even while you're inside the game.
- Press **Shift+Pause** to hear your results.

### Updating Uncle Farmer

Uncle Farmer can check for new versions when it starts. You can also check manually through **Help, Check for updates**. When an update is available, the program offers to download and install it for you.

If a game is open, Uncle Farmer asks first and closes the game before updating because the running game is using the agent file. Start your games again after the update finishes.

You can also update manually by closing Uncle Farmer and all game windows, then unpacking a newer release over the same folder. Your characters, plans, and settings aren't included in releases, so they stay in place.

### Reinstalling the game agent

Sometimes the game agent needs to be installed again. This can happen if another tool removes its entry from the game's settings, if you move Uncle Farmer to another folder, or if a game was already open before the correct agent was loaded.

When this happens, Uncle Farmer explains the reason when it starts. Press **OK** to close every Mist World window and reinstall the agent. Then start the game again.

### More help and documentation

New to Uncle Farmer? The full guide is available in [docs/USER_GUIDE.md](docs/USER_GUIDE.md), as `README.html` in the program's folder, and in the program under **Help (F1)**.

There are also guides for specific topics:

- [Writing scripts](docs/SCRIPTING.md): Learn the scripting language step by step, from your first few lines to complete examples.
- [How the game works](docs/GAME_GUIDE.md): Explore Mist World's rules and systems, based on the game's own help and texts.
- [What to automate next](docs/ROADMAP.md): See what's planned and where you can help.
- [Performance](docs/PERFORMANCE.md): Learn where the program spends its time and how to run the benchmarks again.
- [What's new](CHANGELOG.md): Browse changes made in each version.
- [Contributing](CONTRIBUTING.md): Find out how to report bugs, contribute code, translate the program, or help test it.

Installing the agent adds one line to the game's `mw.exe.vmoptions` file and keeps a copy of the original as `mw.exe.vmoptions.bak-unclefarmer`. You can use **Remove**, in the same place, to take that line out again.

### Running from source

Want to explore the code or work on Uncle Farmer yourself? You can run it directly from the source code.

You'll need Windows, [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`), and JDK 9 or newer for the game agent. uv manages the required Python version for you: Python 3.14, the only version Uncle Farmer currently supports.

Run these commands from the repository folder:

```text
uv sync
uv run python agent/build_agent.py
uv run python agent/java_tests.py       # The agent's own tests, followed by the random-byte parser check
uv run UncleFarmer.pyw
```

`uv sync` installs the exact package versions recorded in `uv.lock` into `.venv`. This includes the packages needed by the app, the development tools, and Nuitka. The project's dependencies are listed in `pyproject.toml`.

#### Running without uv

If you'd rather use pip, install Python 3.14 from [python.org](https://www.python.org/) and run:

```text
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python agent/build_agent.py
.venv\Scripts\python UncleFarmer.pyw
```

`requirements.txt` contains the packages needed to run the app. `requirements-dev.txt` adds the development tools used by `python check.py` and Nuitka, which is used by `python build_exe.py`.

#### Building the application

Run `uv run python build_exe.py` to build the shareable application folder at `dist\Uncle Farmer` using Nuitka.

The build also needs UPX (`winget install UPX.UPX`). It looks for UPX in a folder named `UPX_DIR` or on your system's PATH.

#### Building the optional C module

Run `uv run python build_native.py` to build the optional C module. It makes map searches around 10–24 times faster. If you don't build it, Uncle Farmer uses the Python implementation instead.

## How it works

Uncle Farmer has two main parts: a Java agent that runs inside Mist World and a Python application that decides what the bot should do.

```text
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

The game loads the agent itself through Java's `-javaagent` option. The agent builds a model of the game world from the game's own messages and reads information such as your position, bag, target, and dialogs.

It also sends key presses and skills through the same handlers the keyboard uses. This means the game window doesn't have to stay in front. The Python application decides what to do next.

Here's a quick look at the main parts of the source code:

- `unclefarmer/` contains the application.
  - `engine/` contains the bot's logic.
  - `gui/` contains the application windows.
  - `engine/bot.py` brings together the different parts of the bot in `engine/botparts/`, including game reading, keys, health and potions, walking, the bag, travel, NPC dialogs, fighting, and farming.
  - `engine/combat.py` handles skill selection, while `engine/combos.py` handles talent combos. `stats.py` handles results.
- `agent/src/` contains the Java agent. Its tests are in `agent/test/`.
- `data/` contains the bot's game knowledge, including maps, walls, exits, NPCs, shops, skills, and shards.
- `lang/unclefarmer.pot` contains the program's translatable text. Translations use gettext catalogs.
- `tests/` contains tests that run without the actual game, using a pretend game that follows the agent's communication protocol.

## Help Uncle Farmer grow 🤝

Uncle Farmer is a community project, and there's room for all kinds of help. You don't need to be a programmer to take part!

Here are a few ways you can help:

- 🐞 **Report a bug.** [Open an issue](https://github.com/afnannasir/uncle-farmer/issues) and tell us what you were doing, what you expected to happen, and what actually happened. If possible, attach the day's log file from the `logs` folder. If Uncle Farmer won't open or close properly, include `logs/bootup.log` or `logs/teardown.log`.
- 🛠️ **Fix something.** Pull requests are welcome, whether they're small improvements or larger fixes. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.
- 🌍 **Translate it.** Copy `lang/unclefarmer.pot` to `lang/<your language code>.po`, fill in the `msgstr` lines, and run `python -m unclefarmer --strings` to compile the translation.
- 🗺️ **Share what you know.** Tell us about maps where the bot gets stuck, NPC menus it doesn't understand, good farming spots, or useful plans and scripts.
- 🧪 **Test it.** Try new versions and let us know how they work for you.
- 📣 **Spread the word.** Tell other players about Uncle Farmer, especially those who could use a free alternative.

## Support the project 💛

Uncle Farmer is free and always will be. If it saves you time and you'd like to support the project, there are several ways to help:

- ⭐ **Star the repository.** It helps other players discover Uncle Farmer.
- 🤝 **Contribute.** Your time, feedback, translations, and fixes are just as valuable as financial support.
- ☕ **Donate.** Send an email to [afnan.nasir2000@gmail.com](mailto:afnan.nasir2000@gmail.com), and I'll let you know how.

Thank you for being part of the community!

## Contact 📧

Have a question, an idea, or something you'd like to share? You can reach the developer at [afnan.nasir2000@gmail.com](mailto:afnan.nasir2000@gmail.com).

For bugs, it's best to [open a GitHub issue](https://github.com/afnannasir/uncle-farmer/issues). That way, everyone can follow the discussion and see the fix.

You can also join the [Telegram group](https://t.me/+55NtoqsY7u1lMzNk) to chat with other players and the developer. You can open the group from the app under **Help, Join the Telegram group**.

## Play fair

Please remember that using a bot may be against Mist World's rules. You use Uncle Farmer at your own risk.

Be considerate of other players. Don't use the bot to harass anyone, and please don't crack someone else's paid software. If you need a bot but can't afford a subscription, that's exactly why we want Uncle Farmer to be available for free.

## Licence

Copyright (C) 2026 the Uncle Farmer authors.

Uncle Farmer is free software under the **GNU General Public License v3.0**. You're free to use, modify, and share it. Any version you distribute must remain open source under the same licence, helping keep it free for everyone.

See [LICENSE](LICENSE) for the full licence.

`nvdaControllerClient64.dll` is part of NVDA by NV Access and is covered by its own licence. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for details.

## Thanks

A big thank-you to NV Access for NVDA, the wxPython team, everyone who has contributed code, and every player who has reported a bug, translated a line, or shared a map.

Every contribution helps Uncle Farmer grow. This project belongs to its community, and we're glad you're here.