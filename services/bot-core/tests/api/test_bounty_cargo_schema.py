"""Unit tests for the T4b read-only ``cargo`` field on bounty response schemas.

``BountyResponse`` and ``BountyPublicResponse`` derive a nullable ``cargo``
(``{item_name, item_type, quantity}``) from the persisted
``criminal_ship["cargo"]`` blob (T4).  The derivation MUST:

* surface the cargo when the blob is well-formed (validating both from a dict and
  from an ORM-like attribute object, since ``model_validate`` is fed ORM rows);
* yield ``cargo=None`` for every legacy / malformed shape (absent key, wrong type,
  blank name, non-positive / missing quantity) — never raise.

Import path setup is handled by tests/api/conftest.py.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from api.schemas.bounty_schema import BountyPublicResponse, BountyResponse

_NOW = datetime(2026, 6, 19, 12, 0, 0)


def _orm_bounty(criminal_ship):
    """An ORM-like row (attribute access) carrying the full BountyResponse surface."""
    return SimpleNamespace(
        id=5,
        guild_id=123,
        division="bronze",
        criminal_name="Dark Mage",
        criminal_faction="Void Syndicate",
        route=["Sol", "Vega"],
        answer="Vega",
        reward=10000,
        reward_per_sys=500,
        checked={"Sol": -1, "Vega": -1},
        issue_time=_NOW,
        end_time=None,
        tech_level=3,
        criminal_ship=criminal_ship,
        status="active",
        escape_count=0,
        win_user_id=None,
    )


def _public_orm_bounty(criminal_ship):
    """ORM-like row for the player-facing list payload (no answer field needed)."""
    return SimpleNamespace(
        id=5,
        guild_id=123,
        division="bronze",
        criminal_name="Dark Mage",
        criminal_faction="Void Syndicate",
        route=["Sol", "Vega"],
        reward=10000,
        reward_per_sys=500,
        checked={"Sol": -1, "Vega": -1},
        issue_time=_NOW,
        end_time=None,
        tech_level=3,
        status="active",
        # ORM rows carry criminal_ship even though it's not a public field — the
        # mode="before" validator reads it off the attribute to derive cargo.
        criminal_ship=criminal_ship,
    )


_GOOD_SHIP = {"ship_name": "Interceptor", "cargo": {"item_type": "commodity", "item_name": "Booze", "quantity": 16}}


class TestBountyResponseCargo:
    def test_cargo_derived_from_orm_object(self):
        out = BountyResponse.model_validate(_orm_bounty(_GOOD_SHIP))
        assert out.cargo is not None
        assert out.cargo.item_name == "Booze"
        assert out.cargo.item_type == "commodity"
        assert out.cargo.quantity == 16

    def test_cargo_qty_one_surfaces(self):
        ship = {"cargo": {"item_type": "module", "item_name": "AB-1 Retractor", "quantity": 1}}
        out = BountyResponse.model_validate(_orm_bounty(ship))
        assert out.cargo is not None
        assert out.cargo.quantity == 1

    def test_legacy_no_cargo_key_yields_none(self):
        out = BountyResponse.model_validate(_orm_bounty({"ship_name": "Interceptor"}))
        assert out.cargo is None

    def test_criminal_ship_none_yields_none(self):
        out = BountyResponse.model_validate(_orm_bounty(None))
        assert out.cargo is None

    def test_malformed_blobs_yield_none(self):
        for blob in (
            {"item_name": "", "quantity": 5},
            {"item_name": "Booze", "quantity": 0},
            {"item_name": "Booze", "quantity": -1},
            {"item_name": "Booze"},
            {"quantity": 5},
            "not-a-dict",
        ):
            out = BountyResponse.model_validate(_orm_bounty({"cargo": blob}))
            assert out.cargo is None, f"expected None for blob={blob!r}"

    def test_dict_input_also_derives_cargo(self):
        payload = {
            "id": 5,
            "guild_id": 123,
            "division": "bronze",
            "criminal_name": "Dark Mage",
            "criminal_faction": "Void Syndicate",
            "route": ["Sol"],
            "answer": "Sol",
            "reward": 1,
            "reward_per_sys": 1,
            "checked": {"Sol": -1},
            "issue_time": _NOW,
            "tech_level": 3,
            "criminal_ship": _GOOD_SHIP,
            "status": "active",
        }
        out = BountyResponse.model_validate(payload)
        assert out.cargo is not None and out.cargo.item_name == "Booze"


class TestBountyPublicResponseCargo:
    def test_cargo_derived_from_orm_object(self):
        out = BountyPublicResponse.model_validate(_public_orm_bounty(_GOOD_SHIP))
        assert out.cargo is not None
        assert out.cargo.item_name == "Booze"
        assert out.cargo.quantity == 16

    def test_legacy_no_cargo_key_yields_none(self):
        out = BountyPublicResponse.model_validate(_public_orm_bounty({"ship_name": "Interceptor"}))
        assert out.cargo is None

    def test_criminal_ship_none_yields_none(self):
        out = BountyPublicResponse.model_validate(_public_orm_bounty(None))
        assert out.cargo is None
