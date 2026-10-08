# Changelog

How the bot evolved, version by version: what broke, why, and how it was fixed.
Built for a real client who tested every release on their own PC.

---

## v2.0 — Oct 8, 2026 · fixes from the first field test

**What the client reported after testing v1.0 in the real game:**
> "It finds a target, attacks, uses the skill and kills it… then it doesn't loot and just stands there.
> If I press Pick Up myself it loots, then it targets again but sometimes doesn't attack."

**Root causes and fixes**

| Problem in the field | Root cause | Fix |
|---|---|---|
| "Sometimes it attacks, sometimes it doesn't" · no loot | Key presses were instant (down/up in the same frame). Unreal reads input once per frame, so some presses were never seen. | Keys are now held ~80 ms (`tecla_duracion_ms`) with a 40 ms gap between presses. |
| Stands still after a kill | The dead target's bar kept looking alive, so the bot waited forever. | **No-progress watchdog** (the client's idea): if the target's HP doesn't change in 6 s → cancel, try to loot anyway, find a new target. A second watchdog covers 12 s of searching without a fight. |
| Slow to re-engage | Attack was repeated every 3 s. | Attack every 1.5 s while fighting. |
| Sit/stand could end up inverted | Sit/stand is a single **toggle** key: one missed press and the bot believes the opposite of reality. | Desync detection from **regeneration speed** (sitting regenerates faster), calibrated with an A/B probe on the first rest of the session, plus "two targets in a row that take no damage". Optional idempotent `/stand` macro. |
| Hard to debug on someone else's PC | No visibility into what the bot saw. | `agent.log` with a state line every second and every key pressed; `debug/` snapshots whenever the target stops changing. |

**Bugs caught by the new tests before shipping v2.0**
- A first desync rule ("being hit but can't attack → I must be sitting") **confused an unreachable mob with sitting** and made the character sit mid-fight. Removed.
- The standing-regeneration reference was contaminated by time spent sitting → measure only after standing up, take the median of 9 windows.
- The A/B probe was left half-done if a mob attacked during it → reset on stand-up.
- A probe window with zero regeneration produced an absurd reference → treated as inconclusive.
- The simulator itself let unreachable mobs deal damage → fixed, so the tests stopped hiding the real bug above.

**New:** `tests/test_robustez.py`, a robustness matrix that injects the field failures (5 % and 20 % ignored key presses, dead target that still looks alive). Results are in the [README](README.md#how-it-was-tested).

---

## v1.0 — Oct 5, 2026 · loot + the client's exact rules

- **Loot:** after each kill, wait 0.5 s and press Pick Up several times. New damage interrupts it, and the pending presses carry over to the next kill.
- **Client's rotation as the default config:** search key with a fallback, attack, skill while target > 50 %, loot at 0 %, sit below 50 % HP until full, potion below 30 % during a fight.
- **Window detection fix:** the bot waited forever for a window called after the game, but the real title is `<server> - <character name>`. Now it matches known titles and, if none is found, shows a numbered list of open windows and remembers the choice.
- **Out-of-potions detection:** 3 potions with no HP gain → escape scroll and stop, instead of dying.
- One-click launchers for a non-technical user (auto-elevate, install dependencies on first run) and an optional PyInstaller build.

**Bugs the game simulator caught in v1.0**
- **Ran out of potions and died** → potion-effect detection above.
- **Near-dead target's bar looked empty**, so the game kept re-selecting it while the bot never attacked → a "blind attack" after every target search.
- **Sat down while still being hit** → wait for 3 s without damage before sitting.
- **The killed mob's last hit counted as "I'm being attacked"**, so the bot never rested and burned 2.3× more potions → only damage *after* the kill counts. Kills per 3 h went from **340 to 570**, loot from **55 % to 96 %**.

---

## v0.1 — Oct 5, 2026 · first prototype

- Screen capture → health-bar reader (OpenCV) → state machine → keyboard output.
- Loop: find target, attack, potion when low, rest when no fight.
- Logic separated from Windows (`brain.py` takes an injected `io`), so it could be tested from day one against a simulated game.
