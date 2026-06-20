# How Combat Works

*A plain-language guide to ship-to-ship combat — for pilots who know Galaxy on Fire, not code. (For the exact rules and numbers, see `COMBAT_SPEC_LOCKED.md`.)*

---

## The short version

When two ships fight — a duel between players, or a player hunting a criminal — the game doesn't just flip a coin for the winner. It **plays the whole dogfight out**, moment by moment, then tells you who won and gives you a blow-by-blow of how it went down.

---

## Thinking in "ticks" — the fight, frame by frame

Picture the battle like a film. Instead of rolling dice once, the game runs the dogfight as a rapid sequence of tiny snapshots — each one a hundredth-of-a-second slice called a **tick**. On every tick it asks the same questions:

- How far apart are the ships right now?
- Whose weapons have finished reloading and are ready to fire?
- Does each shot hit or miss?
- How much damage gets through — and to which layer of the hull?
- Has anyone's gadget kicked in?
- Have the ships drifted closer together?

It stacks thousands of these snapshots back-to-back — up to about **three minutes** of simulated combat — to produce a fight that unfolds like a real engagement, not a single lucky roll. That's why a faster-firing gun, a well-timed cloak, or closing the distance at the right moment genuinely changes the outcome.

---

## Distance, and closing the gap

Ships begin a fair distance apart — several kilometres — and **drift closer** as the fight goes on. Distance matters: every weapon has a range, and you simply can't hit something that's still too far away. Some tactics deliberately *change* the distance (more on shock-blasts and boosters below), which can swing a fight in your favour.

---

## Your three layers of protection

Damage doesn't go straight to the wreck. It chews through your ship in order:

**Shield → Armour → Hull**

A hit drains your shield first; once that's gone it bites into armour, and finally into the hull itself. **When the hull reaches zero, the ship is destroyed.** Shields (and certain repair gadgets) can recover a little over the course of a fight, so a drawn-out battle isn't always a losing one.

---

## Weapons

### Primary weapons — your main guns
These are your bread-and-butter cannons. They fire over and over on a reload timer — the shorter the reload, the more shots you put downrange. Each shot has to *land*, so accuracy matters. A **Primary Weapon Mod** can be fitted to tune these guns, trading or boosting raw damage against rate of fire.

### Turrets
Turrets sit in a separate slot and come in three flavours:

- **Automatic turrets** fire on their own at anything in range — handy as a constant trickle of damage, though a touch less accurate than a shot you aim yourself.
- **Manual turrets** you take direct control of — but only when your main guns can't reach. The switch is automatic and driven purely by distance: whenever the enemy is beyond the reach of *every* main gun you carry — closing in at the start of a fight, flung apart by a shock-blast, or held at arm's length by a booster — you man the turret and aim with your full piloting skill. The instant any main gun comes back into reach, your hands return to it and the manual turret falls silent (even if that gun is still reloading). A ship with no main guns at all fights entirely from its manual turrets.
- **Plasma collectors** aren't weapons at all. They're mining tools for harvesting plasma from gas clouds, and they do nothing in a fight.

### Secondary weapons — the heavy ordnance
Five distinct kinds, each with its own character:

- **Rockets** — straightforward unguided shots. The closer you are, the more reliably they connect.
- **Guided missiles** — how dependable they are hinges on your **scanner**: a basic scanner means you still have to line the shot up, while a better scanner lets the missile lock on and track the target.
- **Cluster missiles** — loose a *burst* of several warheads at once, each rolling to hit independently. Brutal at close range when most of them land.
- **Nukes** — area weapons that never roll to hit. Instead, each one is *lobbed at the enemy's position* and detonates somewhere near it — sometimes a near-direct hit that devastates, sometimes a short round that falls back toward **you**. Both ships take damage based on how close they are to the blast, so a short round at long range can still singe you, and close-range nuking is genuinely dangerous for both sides. Three things every nuke pilot should know:
  - **They arm during the fight.** Nuke tubes start the battle loading — your first shot comes one full reload in, never at the opening bell.
  - **Big blasts are reliable; big yields are gambles.** A wide-blast nuke (Extinctor, Oppressor) lands meaningful damage nearly every time; a compact high-yield one (Liberator) can one-shot — or whiff entirely.
  - **Don't bother stacking them.** Each detonation after your first hits at half the strength of the one before (radiation interference). One good nuke is a weapon; four nukes are a light show.
- **Shock-blasts** — deal no damage at all. Instead they hurl the enemy back out to long range — perfect for resetting a brawl, breaking off, or buying time for a gadget to recharge. They only trigger **up close** (inside about half a kilometre); at distance the launcher simply holds its charge.

*(A few weapons also carry an **EMP** charge — they may do little or no hull damage but disrupt the target instead.)*

**Ammunition matters.** Secondary weapons are consumables: every trigger pull spends one round, and your rounds for each weapon ride with the ship it's mounted on (equipping moves your whole stock from cargo to the launcher). Run a launcher dry mid-fight and it simply falls silent; after the battle the empty launcher is unmounted automatically. Criminals' ships carry limited ordnance too — and a criminal never gets more than **one** nuke per fight.

---

## Accuracy — will the shot land?

Whether a shot connects comes down to a mix of your **pilot skill**, the **distance** to the target, your **scanner tier**, and any **modules** in play on either side. Nothing is ever a dead certainty: there's always a sliver of a chance to whiff a point-blank shot, and a sliver of a chance to slip a shot past even a heavily-protected foe.

---

## Modules — the gadgets that turn a fight

Most defensive gadgets trigger **automatically** when your hull drops to set danger thresholds, so they fire exactly when you need them:

- **Cloak** — you all but vanish. For a window, the enemy's chance of hitting you collapses to almost nothing.
- **Booster** — kicks you back out to long range *and* makes you harder to hit while it's active.
- **Thruster** — steadies your hands, sharpening the accuracy of every shot you aim yourself — main guns and manual turrets alike (automatic turrets don't benefit).
- **Emergency System** — a one-shot lifeline. When you're on the brink, it grants a brief window of near-invulnerability to weather the storm.
- **Repair bot / shield recharge** — slow, steady healing that ticks away in the background.

These gadgets aren't capped at a fixed number of uses anymore — cloak and booster can fire again and again over a long fight, held back only by their own recharge timers (so a drawn-out brawl can see several boosts or cloaks, not just two or four).

**The Emergency System sets off a chain.** When it kicks in, your **booster** fires too (if it's recharged) — the extra speed helps you reposition while you're untouchable. Then, the moment the invulnerability *wears off*, your **cloak** snaps on (if it's recharged) to cover the dangerous recovery — a cloak would be wasted *during* invulnerability, so it deliberately waits for the window to close. It's a "panic button → reposition → vanish" sequence that turns a near-death moment into an escape.

---

## Hunting criminals (Player vs Criminal)

When you, a player, take on a criminal NPC for a bounty, the game gives you a built-in **damage-reduction edge** so the fight isn't stacked against you — by default a criminal's shots land for roughly a **third less** damage against a player. (In a straight player-vs-player duel, no such handicap applies — it's an even match.)

**How a criminal is kitted out.** A criminal's loadout is generated to roughly match the threat level of its division (Bronze through Platinum), not hand-picked. Its main guns lean toward **long range** — at least half its primary slots are guaranteed long-range weapons, with the exact gun drawn from a small band around the criminal's tech level. Its gadgets are filled in by a fixed **priority order** — scanner, armour and shield first (always fitted), then the situational gear (cloak, booster, emergency system, weapon mod) whose odds rise sharply with division (a Bronze thug rarely cloaks; a Platinum boss almost always does), then a repair bot and thruster, and finally harmless filler if any slots remain. One deliberate exclusion: weapons that deal mostly **EMP** disruption (which does no real hull damage in the current combat model) are kept *out* of criminal hands — otherwise a criminal could field guns that look threatening but can't actually win, handing you a free kill. (All of these knobs — the long-range share, the per-division gadget odds, the EMP exclusion — are tunable per server.)

**Loot — pulling the cargo off a kill.** Every criminal flies with one item of cargo in its hold, and you can see what it is before you engage (the bounty announcement and `/criminal-loadout` show a "Loot aboard" line). To haul it off you need a **tractor beam** equipped and you have to **win the fight** — looting only fires on a clean kill, never on a loss, a draw, or a Bronze auto-capture you didn't fight for. A better beam pulls more often: the four tiers give a 20 / 40 / 60 / 80 % chance, and with no beam you get nothing. Most hauls are bulk commodities (which you sell off for credits); occasionally you'll pull a weapon or module near the criminal's tech level. Your cargo hold is finite — if it's full you can't loot, and an over-full hold locks you out of leaving station (no `/check`, no duels) until you sell or fit a compressor to make room. (Drop odds, stack sizes, and the tractor chances are all tunable per server.)

---

## How a fight ends

A battle wraps up one of two ways:

1. **A ship is destroyed** — its hull hits zero.
2. **The clock runs out** — if neither ship falls within the time limit, the fight is called a **draw**. Being ahead on hull when time expires doesn't win it for you — survive the clock and nobody collects. (Both ships destroying each other on the same instant is also a draw.)

---

## The after-action report

Everything that happens is written down as it occurs — every shot, hit, miss, heal, distance change, and gadget activation. From that record the game builds a tidy **summary**: how many shots each side fired and landed, your overall accuracy, total damage dealt and taken, which modules fired and how often, and how much hull each ship had left. That summary is what powers the combat result you see after the dust settles.
