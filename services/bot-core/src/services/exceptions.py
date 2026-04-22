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
