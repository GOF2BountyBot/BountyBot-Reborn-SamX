"""B.19 — repair loadout↔inventory consistency (data fixup, no schema change).

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-29

This migration deduplicates legacy slot references in ``player_ships`` JSON
columns so each (item_name, slot_kind) appears in at most one ship per player.

DESIGN CONTRACT (per /proj/recon/B19-design.md § Data fixup migration):

For each ``player_id`` in ``players``:
  1. Load all ``player_ships`` rows (active first, then by id ascending).
  2. Build ``seen: dict[(item_name, slot_kind), winning_ship_id]``.
  3. For each ship × kind × item:
     - If the (name, kind) pair is unseen, keep this reference.
     - Else: drop this duplicate reference.  **Do NOT** create an inventory
       row (preserves I2: no materialisation from nothing during repair).
  4. Persist the cleaned slot lists.

Tie-breaking — the active ship's references win (preserves observable
post-repair UX).  Phantom items on a single ship are deliberately preserved;
they cause no exploit on subsequent flows because the choke-point's anti-
duplication guard handles the post-repair edge cases.

IDEMPOTENCY — Naturally idempotent.  A re-run on already-clean data finds no
duplicates and modifies nothing.

DOWNGRADE — No-op.  We only deleted illegitimate references; "restoring" them
would re-introduce the bug.
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

# Alembic revision identifiers
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger("alembic.b19_repair")


_SLOT_KINDS: tuple[str, ...] = ("weapons", "modules", "turrets", "secondary_weapons")


def _coerce_to_list(value) -> list[str]:
    """Normalise a JSON column value to a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        # PostgreSQL may return raw JSON strings on some configurations
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    return []


def _to_jsonable(items: list[str], original):
    """Return a value compatible with the original column type.

    PostgreSQL JSON columns accept lists directly; we preserve the empty-vs-null
    distinction the original row had so we don't accidentally materialise
    ``[]`` where the row was ``NULL``.
    """
    if items:
        return items
    # Preserve original NULL vs [] distinction
    if original is None:
        return None
    return []


def upgrade() -> None:
    """Deduplicate cross-ship slot references per player.

    Idempotent: a re-run on clean data is a no-op.
    """
    bind = op.get_bind()

    # Pull (player_id, ship_id, is_active, weapons, modules, turrets, secondary_weapons)
    # ordered so active ships come first, then by id ascending — gives the
    # active ship "winning" preference per the spec.
    rows_query = sa.text(
        """
        SELECT player_id, id, is_active, weapons, modules, turrets, secondary_weapons
        FROM player_ships
        ORDER BY player_id ASC, is_active DESC, id ASC
        """
    )
    result = bind.execute(rows_query)
    rows = result.fetchall()

    if not rows:
        logger.info("B.19 repair: no player_ships rows; nothing to do.")
        return

    # Group rows by player_id (preserving the input ordering)
    players_seen_order: list[int] = []
    rows_by_player: dict[int, list[tuple]] = {}
    for row in rows:
        pid = row[0]
        if pid not in rows_by_player:
            rows_by_player[pid] = []
            players_seen_order.append(pid)
        rows_by_player[pid].append(row)

    total_duplicates = 0
    total_ships_modified = 0

    for player_id in players_seen_order:
        # seen: (item_name, kind) -> winning_ship_id
        seen: dict[tuple[str, str], int] = {}
        for row in rows_by_player[player_id]:
            ship_id = row[1]
            slot_values = {
                "weapons": row[3],
                "modules": row[4],
                "turrets": row[5],
                "secondary_weapons": row[6],
            }
            cleaned: dict[str, list[str] | None] = {}
            ship_modified = False
            for kind in _SLOT_KINDS:
                original = slot_values[kind]
                items = _coerce_to_list(original)
                if not items:
                    cleaned[kind] = None  # marker — skip update for this kind
                    continue
                kept: list[str] = []
                modified = False
                for name in items:
                    key = (name, kind)
                    if key not in seen:
                        seen[key] = ship_id
                        kept.append(name)
                    else:
                        total_duplicates += 1
                        modified = True
                        logger.warning(
                            "B.19 repair: removed duplicate %s '%s' from player_ship %d "
                            "(player %d, kept on player_ship %d)",
                            kind,
                            name,
                            ship_id,
                            player_id,
                            seen[key],
                        )
                if modified:
                    cleaned[kind] = _to_jsonable(kept, original)
                    ship_modified = True
                else:
                    cleaned[kind] = None  # skip update — unchanged

            if ship_modified:
                total_ships_modified += 1
                # Build update statement only with changed kinds
                set_pairs: list[str] = []
                params: dict[str, object] = {"sid": ship_id}
                for kind, new_value in cleaned.items():
                    if new_value is None:
                        # If the original was None we leave it; if items reduced to
                        # empty we DO want to write [] to make idempotency safe.
                        continue
                    set_pairs.append(f"{kind} = CAST(:{kind} AS json)")
                    params[kind] = json.dumps(new_value)
                if set_pairs:
                    sql = f"UPDATE player_ships SET {', '.join(set_pairs)} WHERE id = :sid"
                    bind.execute(sa.text(sql), params)

    logger.info(
        "B.19 repair complete: %d player(s) scanned, %d duplicate slot references removed, %d ships modified",
        len(players_seen_order),
        total_duplicates,
        total_ships_modified,
    )


def downgrade() -> None:
    """No-op.

    The B.19 repair removes illegitimate duplicate slot references.
    "Restoring" them would re-introduce the bug, so the down-migration is
    intentionally empty.
    """
    return None
