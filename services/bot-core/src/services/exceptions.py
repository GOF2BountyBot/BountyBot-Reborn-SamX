"""
Service-layer exceptions for the BountyBot system.

These exceptions are defined in a separate module to avoid circular imports
and to allow them to be imported without pulling in SQLAlchemy dependencies.
"""


class GuildNotConfiguredError(Exception):
    """Raised when a guild has no guild_configs row (admin_setup not run yet)."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        super().__init__(f"Guild {guild_id} has not been configured. An admin must run /admin_setup first.")


class OverCapError(ValueError):
    """Raised when a player is over their cargo cap and tries to leave station.

    T7 over-cap lockout (LOOT_JOURNAL §5.5 C-3a): the duel-challenge and
    duel-accept entries gate on this BEFORE resolving the duel. Over-cap is
    STRICTLY ``current_load > effective_cap`` (being exactly AT cap is allowed).

    Extends ``ValueError`` so existing ``except ValueError`` clauses in routers
    still treat it as a friendly 400 by default; routers that want the dedicated
    structured 409 over-cap response should catch ``OverCapError`` *before* the
    generic ``ValueError`` handler. Carries ``current_load`` / ``effective_cap``
    so the gateway can render "Cargo Overloaded — NN/XX. Unable to leave station."
    """

    def __init__(self, current_load: int, effective_cap: int, player_id: int | None = None):
        self.current_load = current_load
        self.effective_cap = effective_cap
        self.player_id = player_id
        super().__init__(f"Cargo Overloaded — {current_load}/{effective_cap}. Unable to leave station.")


class InvalidItemTypeError(ValueError):
    """Raised when an item_type is unrecognised or not currently enabled.

    Extends ``ValueError`` so that existing ``except ValueError:`` clauses in
    routers remain backward-compatible.  Routers that want to return HTTP 422
    instead of 400 for this specific case should catch ``InvalidItemTypeError``
    *before* the generic ``ValueError`` handler:

        except InvalidItemTypeError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    """
