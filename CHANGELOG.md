# What's new

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
