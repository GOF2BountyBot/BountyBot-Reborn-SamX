"""DB-free, picklable, process-pool worker leaf for combat resolution.

IMPORT DISCIPLINE — READ BEFORE ADDING ANY IMPORT
==================================================
This module is a *leaf* designed to be imported cheaply by a forkserver child
process.  It MUST NOT import any of the following at module top-level (or
transitively via module-level imports):

  - ``fastapi``, ``main`` (the FastAPI app / create_app)          → starts ASGI machinery
  - ``sqlalchemy``, ``asyncpg``                                    → opens DB engine pools
  - ``persist.*``  (models, database manager, repositories)        → ORM / engine setup
  - ``utils.executors.*``                                          → pulls in services/ORM
  - any module that calls ``db_manager.initialize()`` at import    → connects to DB

When a forkserver worker imports this file it does so in a *fresh* interpreter
with *no* inherited file-descriptors.  Any module that opens a DB connection or
binds a socket at import time would either fail or silently exhaust the pool.

Top-level imports of ``services.combat_models`` and ``services.combat_resolver``
are SAFE: ``services/__init__.py`` is inert (empty, no auto-importer), and
both leaf modules import only pure stdlib + dataclasses — no ORM, no httpx.

This module lives at ``compute/`` (top-level), NOT under ``utils/``, so
importing it never triggers ``utils/__init__.py``'s pkgutil auto-importer
(which would drag in all executors and their transitive ORM/httpx deps).

P2-T1: ``run_fight`` is implemented here.  It is a plain (non-async) callable
so it can be submitted to ``concurrent.futures.ProcessPoolExecutor``.
"""

# ---------------------------------------------------------------------------
# Top-level imports — safe because:
#   * services/__init__.py is INERT (no auto-importer)
#   * combat_models / combat_resolver import only stdlib + dataclasses (DB-free)
#   * this file is at compute/, not utils/, so utils/__init__ is never triggered
# ---------------------------------------------------------------------------

from services.combat_models import FightResults
from services.combat_resolver import TickResolver, _extract_key_events, deplete_side1_loadout

# ---------------------------------------------------------------------------
# P2-T1: run_fight — pure, picklable worker function
# ---------------------------------------------------------------------------


def run_fight(
    loadout1,
    loadout2,
    *,
    pvc_damage_reduction: float,
    seed,
    combatant1_label: str,
    combatant2_label: str,
    compact: bool,
):
    """Execute a single combat simulation in an isolated worker process.

    Designed to run inside a ``concurrent.futures.ProcessPoolExecutor``
    (forkserver context).  Every argument and return value must be picklable.

    Parameters
    ----------
    loadout1, loadout2:
        ``ShipLoadout`` instances (frozen dataclasses — picklable).
    pvc_damage_reduction:
        Keith T. Maxwell DR (0.33 for PvC, 0.0 for PvP).  Passed through
        to ``TickResolver.resolve()`` unchanged.
    seed:
        Integer seed (or ``None``) for ``TickResolver(seed=seed)``.
        Production callers pass ``None`` to match current default-RNG
        behaviour; tests pin a fixed integer for determinism.
        The constructor handles seeding; an ``rng=`` object is NOT passed
        (it may not be picklable).
    combatant1_label, combatant2_label:
        CI-20 display labels for C1 / C2.  Empty string → resolver defaults
        to ship_name (preflight / sim paths unchanged).
    compact:
        ``True``  → return ``(winner_side, is_stalemate)`` only.
                    Tiny tuple; designed for 20-sim preflight passes.
        ``False`` → return a full plain-dict result (see Returns below).

    Returns
    -------
    tuple[int | None, bool]
        When ``compact=True``: ``(winner_side, is_stalemate)``.
        ``winner_side`` is 1 (C1 wins), 2 (C2 wins), or ``None`` (stalemate).

    dict
        When ``compact=False``: a picklable dict with the following keys::

            winner_side     : int | None
            winner_name     : str | None
            loser_name      : str | None
            is_stalemate    : bool
            timeline        : list[dict]   — CombatEvent list serialised via
                                             manual projection (byte-identical
                                             to dataclasses.asdict per event)
            summary         : dict         — fight summary from metadata
            metadata        : dict         — full metadata block from resolver
            key_events      : list[dict]   — from _extract_key_events()
            ship1_stats     : dict         — FightStats fields as plain dict
            ship2_stats     : dict         — FightStats fields as plain dict

    Notes
    -----
    ``timeline`` uses a manual dict projection rather than ``dataclasses.asdict``
    to avoid the deep-copy overhead across thousands of events.  The projection
    is byte-identical to ``dataclasses.asdict(ev)`` for each ``CombatEvent``
    because ``CombatEvent.data`` is a plain ``dict`` of primitives (no nested
    dataclasses) and ``dataclasses.asdict`` performs a shallow copy of plain
    dicts.
    """
    # Construct resolver INSIDE the function — do NOT pass an rng= object
    # (random.Random is picklable but seeds passed via constructor are cleaner
    # and avoid the caller needing to construct an RNG instance).
    resolver = TickResolver(seed=seed)

    result: FightResults = resolver.resolve(
        loadout1,
        loadout2,
        pvc_damage_reduction=pvc_damage_reduction,
        combatant1_label=combatant1_label,
        combatant2_label=combatant2_label,
    )

    winner_side = result.winner_side
    is_stalemate = result.is_stalemate

    if compact:
        return (winner_side, is_stalemate)

    # --- Full result path ---

    # Serialize timeline: manual dict projection over CombatEvent instances.
    # Byte-identical to [dataclasses.asdict(ev) for ev in result.combat_log]
    # because CombatEvent has exactly 5 fields (tick, type, actor, target, data)
    # and ev.data is a plain dict of primitives — asdict() shallow-copies plain
    # dicts, so {**ev.data} produces the same result.  No nested dataclasses.
    timeline: list[dict] = [
        {
            "tick": ev.tick,
            "type": ev.type,
            "actor": ev.actor,
            "target": ev.target,
            "data": dict(ev.data),
        }
        for ev in result.combat_log
    ]

    # Extract key events from the serialized timeline.
    # Pass combatants_map so display-name resolution matches the read path
    # (X2 parity by construction — same function, same inputs as persist-time
    # computation in CombatLogService).
    _combatants_map: dict = result.metadata.get("summary", {}).get("combatants", {})
    key_events = _extract_key_events(timeline, combatants_map=_combatants_map)

    # FightStats → plain dict (picklable; avoids shipping a frozen dataclass
    # with slots across process boundaries — though those are picklable too,
    # a plain dict is unambiguously safe for any downstream consumer).
    def _stats_dict(fs) -> dict:
        return {
            "ship_name": fs.ship_name,
            "raw_hp": fs.raw_hp,
            "raw_dps": fs.raw_dps,
            "varied_hp": fs.varied_hp,
            "varied_dps": fs.varied_dps,
            "ttk": fs.ttk,
        }

    return {
        "winner_side": winner_side,
        "winner_name": result.winner_name,
        "loser_name": result.loser_name,
        "is_stalemate": is_stalemate,
        "timeline": timeline,
        "summary": result.metadata.get("summary", {}),
        "metadata": result.metadata,
        "key_events": key_events,
        "ship1_stats": _stats_dict(result.ship1_stats),
        "ship2_stats": _stats_dict(result.ship2_stats),
    }


def run_fight_batch(
    matchups: list[tuple],
    *,
    pvc_damage_reduction: float,
    compact: bool = True,
    carry_side1_resources: bool = False,
) -> list[tuple]:
    """Execute a batch of combat simulations in a single worker process.

    Designed to replace N separate ``offload_cpu(run_fight, ...)`` calls with a
    single process-pool dispatch so the event loop blocks only once for the
    entire preflight Monte-Carlo pass.

    Parameters
    ----------
    matchups:
        A list of 5-tuples, each of the form::

            (loadout1, loadout2, seed, combatant1_label, combatant2_label)

        All elements must be picklable.  ``seed`` may be ``None`` (non-deterministic)
        or an ``int`` (pinned for tests).
    pvc_damage_reduction:
        Keith T. Maxwell DR forwarded to every ``run_fight`` call unchanged.
    compact:
        Forwarded to ``run_fight``.  ``True`` (default) → each result is a
        ``(winner_side, is_stalemate)`` 2-tuple.  ``False`` → full dict.
    carry_side1_resources:
        When ``True`` (preflight Monte-Carlo), run the matchups SEQUENTIALLY and
        thread the side-1 (player) loadout's consumable state forward: secondary
        ammo and the one-use EmergencySystem deplete across fights instead of
        refilling every sim (HP/shields/armour still reset each fight).  Each
        fight is resolved with the full summary internally (to read what was
        consumed) but the per-matchup result is still projected to the compact
        ``(winner_side, is_stalemate)`` tuple — this mode therefore implies
        compact results and is only meaningful with ``compact=True``.  The
        rolling loadout is seeded from ``matchups[0]``'s ``loadout1``; per-matchup
        ``loadout1`` entries after the first are ignored (the preflight builds all
        matchups from the same player loadout).  Default ``False`` ⇒ behaviour is
        byte-identical to the original per-matchup loop.

    Returns
    -------
    list[tuple[int | None, bool]]
        One ``(winner_side, is_stalemate)`` per matchup when ``compact=True`` (or
        ``carry_side1_resources=True``).
        One full-result ``dict`` per matchup when ``compact=False``.
    """
    results = []

    if carry_side1_resources:
        # Sequential carry mode: thread a depleting side-1 (player) loadout through
        # the run. Resolve each fight with compact=False to read the summary, apply
        # cross-fight depletion, and project the result to the compact tuple.
        current_l1 = matchups[0][0] if matchups else None
        for _l1, loadout2, seed, combatant1_label, combatant2_label in matchups:
            full = run_fight(
                current_l1,
                loadout2,
                pvc_damage_reduction=pvc_damage_reduction,
                seed=seed,
                combatant1_label=combatant1_label,
                combatant2_label=combatant2_label,
                compact=False,
            )
            results.append((full["winner_side"], full["is_stalemate"]))
            # Deplete the player's carried consumables for the next fight.
            current_l1 = deplete_side1_loadout(current_l1, full["summary"])
        return results

    for loadout1, loadout2, seed, combatant1_label, combatant2_label in matchups:
        result = run_fight(
            loadout1,
            loadout2,
            pvc_damage_reduction=pvc_damage_reduction,
            seed=seed,
            combatant1_label=combatant1_label,
            combatant2_label=combatant2_label,
            compact=compact,
        )
        results.append(result)
    return results


__all__: list[str] = ["run_fight", "run_fight_batch"]
