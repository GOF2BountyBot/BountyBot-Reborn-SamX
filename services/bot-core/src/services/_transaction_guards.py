"""Runtime transaction-discipline guards (AC-6, B.34 remediation).

Provides decorators that enforce, at call time, that a service method is
invoked inside an active SQLAlchemy transaction. Combined with the static
linter (``tests/test_transaction_discipline.py``) and the session-manager
auto-commit (``persist/database/manager.py``), this completes the
defense-in-depth against the B.34 silent-rollback class of bugs.

Why a runtime check in addition to the static linter?
======================================================
The static linter is AST-based and cannot reason about dynamic dispatch
(e.g. ``getattr(svc, method_name)(...)``). The bot-core codebase does not
currently use such patterns in routers, but if a future refactor
introduces one this decorator catches the unsafe call at runtime — much
better than a silent rollback.

Where to apply
==============
Apply ``@requires_transaction`` to every public method of
:class:`~services.loadout_consistency_service.LoadoutConsistencyService`.
The choke-point's whole design contract (I3) is "caller owns the
transaction; service uses commit=False" — making that contract
runtime-enforceable is the decorator's primary use case.

Do NOT apply to:
  - Methods that are designed to be transaction-owners (e.g. those with
    their own ``await db.commit()`` on the success path). The decorator
    would force an outer transaction onto routes that are correctly
    designed for bare-session use.
  - Repository methods. Repos honour a ``commit`` parameter and are
    semantically dual-mode by design.

Behaviour
=========
``requires_transaction`` raises a :class:`RuntimeError` immediately if
``db.in_transaction()`` is False at call time. The error message names
the offending method and recommends the wrapping pattern.

Test escape hatch
=================
Unit tests that mock the AsyncSession can supply a mock whose
``in_transaction()`` returns True; this is the same approach used in the
bot-core test suite for SQLAlchemy session protocol mocking.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from shared import bblogger
from sqlalchemy.ext.asyncio import AsyncSession

flogger = bblogger.get_logger("transaction-guards")


def requires_transaction(method: Callable[..., Any]) -> Callable[..., Any]:
    """Enforce that ``method`` is called inside an active transaction.

    The decorated method MUST accept a positional ``AsyncSession`` as its
    first non-``self`` argument; the decorator inspects that session's
    ``in_transaction()`` state.

    Raises:
        RuntimeError: if the session is not currently in a transaction.

    Usage::

        from services._transaction_guards import requires_transaction

        class LoadoutConsistencyService:
            @requires_transaction
            async def equip_one(self, db: AsyncSession, ...) -> dict:
                ...
    """

    @wraps(method)
    async def wrapper(self: Any, db: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        # Some test mocks expose in_transaction as a callable, others as a
        # property; either is acceptable. We only require that .in_transaction()
        # is truthy for "yes, we're in a transaction."
        try:
            in_tx = db.in_transaction()
        except TypeError:
            # in_transaction was a non-callable (e.g. test mock attribute);
            # accept it as long as it's truthy
            in_tx = bool(getattr(db, "in_transaction", True))
        if not in_tx:
            raise RuntimeError(
                f"{type(self).__name__}.{method.__name__} requires an active "
                f"transaction. Wrap the caller in:\n"
                f"    async with get_db_session() as db, db.begin():\n"
                f"        await service.{method.__name__}(db, ...)\n"
                f"This contract is enforced because the service performs "
                f"flush-only writes (commit=False) and depends on the caller "
                f"owning the commit (Package G B.19 invariant I3)."
            )
        return await method(self, db, *args, **kwargs)

    return wrapper
