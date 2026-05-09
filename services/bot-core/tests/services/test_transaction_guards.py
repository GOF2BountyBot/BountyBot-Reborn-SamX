"""Unit tests for the requires_transaction decorator (AC-6, B.34 remediation)."""

import pytest
from services._transaction_guards import requires_transaction


class _FakeSession:
    """Minimal AsyncSession stand-in exposing in_transaction()."""

    def __init__(self, in_tx: bool):
        self._in_tx = in_tx

    def in_transaction(self) -> bool:
        return self._in_tx


class _FakeService:
    """Minimal service with a guarded method."""

    @requires_transaction
    async def equip_one(self, db, *, item_name: str) -> str:
        return f"equipped {item_name}"


@pytest.mark.asyncio
async def test_requires_transaction_passes_when_in_transaction():
    svc = _FakeService()
    db = _FakeSession(in_tx=True)
    result = await svc.equip_one(db, item_name="Nirai Impulse EX 1")
    assert result == "equipped Nirai Impulse EX 1"


@pytest.mark.asyncio
async def test_requires_transaction_raises_when_not_in_transaction():
    svc = _FakeService()
    db = _FakeSession(in_tx=False)
    with pytest.raises(RuntimeError, match="requires an active transaction"):
        await svc.equip_one(db, item_name="x")


@pytest.mark.asyncio
async def test_requires_transaction_error_message_names_method_and_class():
    svc = _FakeService()
    db = _FakeSession(in_tx=False)
    with pytest.raises(RuntimeError) as excinfo:
        await svc.equip_one(db, item_name="x")
    # Error must identify the offending site for debuggability
    assert "_FakeService" in str(excinfo.value)
    assert "equip_one" in str(excinfo.value)
    # Must hint at the canonical fix
    assert "async with" in str(excinfo.value)
    assert "db.begin" in str(excinfo.value)


@pytest.mark.asyncio
async def test_requires_transaction_preserves_method_signature():
    """The decorator must preserve the wrapped method's name (functools.wraps)."""
    assert _FakeService.equip_one.__name__ == "equip_one"
