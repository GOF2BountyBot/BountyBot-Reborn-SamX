"""T6 — PvC loot result on the /check API response (LOOT_JOURNAL §5.9).

Covers the bot-core -> gateway contract: the internal ``LootOutcome``
(produced by T5 on a player combat win) is mapped onto the wire
``BountyCheckOutcome.loot`` / ``BountyCheckResponse.loot`` as a ``LootResult``,
applying the §5.9 omission rule (no loot / ``none`` -> ``None`` parent field).

Import-path setup and sqlalchemy_utils mocking come from tests/api/conftest.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures (local — the router fixtures live in test_bounty_router.py, which is
# a sibling module, not a shared conftest; define what this module needs).
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bounty_service():
    service = AsyncMock()
    return service


@pytest.fixture
def mock_loadout_response_service():
    svc = MagicMock()
    svc.build_bounty_loadout = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def client(mock_bounty_service, mock_loadout_response_service):
    from api.routers.bounties import (
        get_bounty_service,
        get_loadout_response_service,
    )
    from api.routers.bounties import router as bounties_router

    app = FastAPI()
    app.include_router(bounties_router, prefix="/api/v1")
    app.dependency_overrides[get_bounty_service] = lambda: mock_bounty_service
    app.dependency_overrides[get_loadout_response_service] = lambda: mock_loadout_response_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _configure_db_mock(mock_get_db):
    """Configure mock_get_db as an async context manager yielding a mock session."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute.return_value = mock_result
    mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_loot_outcome(outcome, **overrides):
    """Build an internal :class:`LootOutcome` (T5) for a given outcome state."""
    from services.bounty_service import LootOutcome

    return LootOutcome(outcome=outcome, **overrides)


def make_check_response_with_loot(loots):
    """Build a MultiCheckResponse whose outcomes carry the given loot results.

    ``loots`` is a list of ``LootOutcome | None`` — one per CORRECT combat-win
    outcome.  Each outcome is a CORRECT combat-win CheckResponse.
    """
    from services.bounty_service import CheckResponse, CheckResult, MultiCheckResponse

    outcomes = []
    for idx, loot in enumerate(loots, start=1):
        outcomes.append(
            CheckResponse(
                result=CheckResult.CORRECT,
                bounty_id=idx,
                message="Captured!",
                combat_won=True,
                loot=loot,
            )
        )
    return MultiCheckResponse(outcomes=outcomes)


# ===========================================================================
# 1. LootOutcome -> LootResult mapping (the conversion helper)
# ===========================================================================


class TestLootToSchema:
    """Unit tests for ``_loot_to_schema`` (the §5.9 omission rule + field map)."""

    def _convert(self, loot):
        from api.routers.bounties import _loot_to_schema

        return _loot_to_schema(loot)

    def test_looted_full_haul(self):
        """``looted``: full haul, qty_looted == qty_total, item + emoji surfaced."""
        result = self._convert(
            make_loot_outcome(
                "looted",
                item_name="Booze",
                item_type="commodity",
                qty_looted=16,
                qty_total=16,
                tractor_name="AB-1 Retractor",
                tractor_emoji="<:ab1:111>",
            )
        )
        assert result is not None
        assert result.outcome == "looted"
        assert result.item_name == "Booze"
        assert result.qty_looted == 16
        assert result.qty_total == 16
        assert result.tractor_emoji == "<:ab1:111>"
        assert result.cargo_current is None
        assert result.cargo_max is None
        # item_type is intentionally NOT on the wire model.
        assert not hasattr(result, "item_type")

    def test_partial_clamped_haul(self):
        """``partial``: qty_looted < qty_total (§5.4 cargo clamp)."""
        result = self._convert(
            make_loot_outcome(
                "partial",
                item_name="Booze",
                qty_looted=6,
                qty_total=16,
                tractor_emoji="<:ab2:222>",
            )
        )
        assert result is not None
        assert result.outcome == "partial"
        assert result.qty_looted == 6
        assert result.qty_total == 16
        assert result.qty_looted < result.qty_total
        assert result.item_name == "Booze"

    def test_failed_rng_miss(self):
        """``failed``: beam equipped + room, RNG missed — no item name."""
        result = self._convert(make_loot_outcome("failed", tractor_emoji="<:ab3:333>"))
        assert result is not None
        assert result.outcome == "failed"
        assert result.item_name is None
        assert result.qty_looted == 0
        assert result.qty_total == 0
        assert result.tractor_emoji == "<:ab3:333>"

    def test_cargo_full_carries_nn_xx(self):
        """``cargo_full``: 0 free cargo at win — carries (NN/XX)."""
        result = self._convert(
            make_loot_outcome(
                "cargo_full",
                tractor_emoji="<:ab4:444>",
                cargo_current=50,
                cargo_max=50,
            )
        )
        assert result is not None
        assert result.outcome == "cargo_full"
        assert result.cargo_current == 50
        assert result.cargo_max == 50
        assert result.item_name is None

    def test_none_outcome_omitted(self):
        """``none`` (no tractor beam) -> None (gateway omits the field)."""
        assert self._convert(make_loot_outcome("none")) is None

    def test_none_loot_omitted(self):
        """``loot is None`` (no combat win / no loot write) -> None."""
        assert self._convert(None) is None


# ===========================================================================
# 2. _outcome_to_schema wires loot onto each per-bounty outcome
# ===========================================================================


class TestOutcomeToSchemaLoot:
    def _to_schema(self, loot):
        from api.routers.bounties import _outcome_to_schema
        from services.bounty_service import CheckResponse, CheckResult

        return _outcome_to_schema(
            CheckResponse(
                result=CheckResult.CORRECT,
                bounty_id=7,
                combat_won=True,
                loot=loot,
            )
        )

    def test_renderable_loot_attached(self):
        schema = self._to_schema(
            make_loot_outcome("looted", item_name="Gold", qty_looted=3, qty_total=3, tractor_emoji="<:t:1>")
        )
        assert schema.loot is not None
        assert schema.loot.outcome == "looted"
        assert schema.loot.item_name == "Gold"

    def test_none_loot_is_none_on_schema(self):
        assert self._to_schema(make_loot_outcome("none")).loot is None
        assert self._to_schema(None).loot is None


# ===========================================================================
# 3. Full /check router path carries the loot payload
# ===========================================================================


class TestCheckRouterLootPayload:
    @patch("api.routers.bounties.get_db_session")
    def test_check_response_includes_looted(self, mock_get_db, client, mock_bounty_service):
        """Renderable loot serializes onto both the per-outcome and legacy mirror."""

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response_with_loot(
                [
                    make_loot_outcome(
                        "looted",
                        item_name="Booze",
                        qty_looted=16,
                        qty_total=16,
                        tractor_emoji="<:ab1:111>",
                    )
                ]
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        # Per-bounty outcome carries its own loot.
        assert data["outcomes"][0]["loot"]["outcome"] == "looted"
        assert data["outcomes"][0]["loot"]["item_name"] == "Booze"
        assert data["outcomes"][0]["loot"]["qty_looted"] == 16
        assert data["outcomes"][0]["loot"]["tractor_emoji"] == "<:ab1:111>"
        # item_type must NOT leak onto the wire payload (§5.9).
        assert "item_type" not in data["outcomes"][0]["loot"]
        # Legacy top-level mirror reflects outcomes[0].
        assert data["loot"]["outcome"] == "looted"
        assert data["loot"]["item_name"] == "Booze"

    @patch("api.routers.bounties.get_db_session")
    def test_check_response_cargo_full_nn_xx(self, mock_get_db, client, mock_bounty_service):

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response_with_loot(
                [
                    make_loot_outcome(
                        "cargo_full",
                        tractor_emoji="<:ab4:444>",
                        cargo_current=50,
                        cargo_max=50,
                    )
                ]
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        loot = response.json()["outcomes"][0]["loot"]
        assert loot["outcome"] == "cargo_full"
        assert loot["cargo_current"] == 50
        assert loot["cargo_max"] == 50

    @patch("api.routers.bounties.get_db_session")
    def test_check_response_no_beam_omits_loot(self, mock_get_db, client, mock_bounty_service):
        """``none`` outcome -> loot is None on both per-outcome and mirror."""

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response_with_loot([make_loot_outcome("none")])
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["outcomes"][0]["loot"] is None
        assert data["loot"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_check_response_no_loot_field_at_all(self, mock_get_db, client, mock_bounty_service):
        """Backward-compat: an outcome with ``loot=None`` (no T5 write) still
        serializes; loot defaults to None on the wire."""

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(return_value=make_check_response_with_loot([None]))

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["outcomes"][0]["loot"] is None
        assert data["loot"] is None

    @patch("api.routers.bounties.get_db_session")
    def test_multi_bounty_each_outcome_own_loot(self, mock_get_db, client, mock_bounty_service):
        """Multi-bounty: each outcome carries its OWN loot; legacy mirror == first."""

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response_with_loot(
                [
                    make_loot_outcome("looted", item_name="Gold", qty_looted=2, qty_total=2, tractor_emoji="<:t1:1>"),
                    make_loot_outcome("partial", item_name="Booze", qty_looted=3, qty_total=9, tractor_emoji="<:t2:2>"),
                    make_loot_outcome("none"),
                ]
            )
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["outcomes"]) == 3
        # Outcome 0: full haul.
        assert data["outcomes"][0]["loot"]["outcome"] == "looted"
        assert data["outcomes"][0]["loot"]["item_name"] == "Gold"
        # Outcome 1: partial — qty_looted < qty_total.
        assert data["outcomes"][1]["loot"]["outcome"] == "partial"
        assert data["outcomes"][1]["loot"]["qty_looted"] == 3
        assert data["outcomes"][1]["loot"]["qty_total"] == 9
        # Outcome 2: no beam -> omitted.
        assert data["outcomes"][2]["loot"] is None
        # Legacy top-level mirror reflects the FIRST outcome (existing convention).
        assert data["loot"]["outcome"] == "looted"
        assert data["loot"]["item_name"] == "Gold"

    @patch("api.routers.bounties.get_db_session")
    def test_existing_non_loot_response_unbroken(self, mock_get_db, client, mock_bounty_service):
        """Backward-compat: a plain CORRECT response (no loot) still serializes,
        with loot defaulting to None — no existing field changes."""
        from .test_bounty_router import make_check_response

        _configure_db_mock(mock_get_db)
        mock_bounty_service.check_bounty = AsyncMock(
            return_value=make_check_response("correct", bounty_id=1, message="Correct!")
        )

        response = client.post(
            "/api/v1/bounties/check?guild_id=67890",
            json={"player_id": 42, "system_name": "Tau Ceti"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "correct"
        assert data["loot"] is None
        assert data["outcomes"][0]["loot"] is None
