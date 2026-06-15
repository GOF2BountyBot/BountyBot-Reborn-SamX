"""Tests for the combat_log API router endpoints.

Covers:
  - GET /combat-log → list[CombatLogListItem]
  - GET /combat-log/{id}?user_id=... → CombatLogDetail
  - Ownership gate: non-combatant → 404
  - Not-found → 404
  - Missing required params → 422

Import path setup is handled by tests/api/conftest.py (already in place).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_list_item(
    row_id: int = 1,
    guild_id: int = 699744305274945650,
    context: str = "duel",
    opponent_name: str = "Foe",
    outcome: str = "won",
    ordinal: int = 1,
) -> dict:
    return {
        "id": row_id,
        "guild_id": guild_id,
        "context": context,
        "opponent_name": opponent_name,
        "outcome": outcome,
        "created_at": datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        "ordinal": ordinal,
    }


def _make_detail(
    row_id: int = 1,
    user_id: int = 402296276617527306,
    outcome: str = "won",
) -> dict:
    return {
        "id": row_id,
        "guild_id": 699744305274945650,
        "context": "duel",
        "combatant1_name": "Betty",
        "combatant2_name": "Betty",
        "combatant1_user_id": user_id,
        "combatant2_user_id": 970691862035841048,
        "winner_name": "Betty",
        "is_stalemate": False,
        "created_at": datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
        "outcome": outcome,
        "combatant1": {
            "name": "Betty",
            "ship": "Betty",
            "start_hp": {"hull": 95, "armour": 40, "shield": 0},
            "final_hp": {"hull": 95, "armour": 40, "shield": 0},
            "shots_fired": 60,
            "shots_hit": 40,
            "accuracy": 40 / 60,
            "damage_dealt": 120,
            "damage_taken": 80,
        },
        "combatant2": {
            "name": "Betty",
            "ship": "Betty",
            "start_hp": {"hull": 95, "armour": 40, "shield": 0},
            "final_hp": {"hull": 0, "armour": 0, "shield": 0},
            "shots_fired": 55,
            "shots_hit": 35,
            "accuracy": 35 / 55,
            "damage_dealt": 80,
            "damage_taken": 120,
        },
        "duration_ticks": 3488,
        "duration_s": 34.88,
        "pvc_damage_reduction": 0.0,
        "key_events": [
            {
                "tick": 100,
                "time_s": 1.0,
                "actor": "Betty",
                "event_type": "Armour depleted",
                "detail": "Betty: Armour depleted",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service():
    svc = AsyncMock()
    svc.list_for_player = AsyncMock(return_value=[_make_list_item()])
    svc.get_detail = AsyncMock(return_value=_make_detail())
    return svc


@pytest.fixture
def test_app(mock_service):
    app = FastAPI()
    from api.routers.combat_log import get_combat_log_service
    from api.routers.combat_log import router as combat_log_router

    app.dependency_overrides[get_combat_log_service] = lambda: mock_service
    app.include_router(combat_log_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(test_app, mock_db_session):
    _session, mock_cm = mock_db_session
    with patch("api.routers.combat_log.get_db_session", return_value=mock_cm), TestClient(test_app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/combat-log
# ---------------------------------------------------------------------------


class TestListCombatLog:
    def test_returns_list(self, client):
        resp = client.get("/api/v1/combat-log", params={"user_id": 402296276617527306, "guild_id": 699744305274945650})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == 1
        assert data[0]["outcome"] == "won"
        assert data[0]["opponent_name"] == "Foe"
        assert data[0]["ordinal"] == 1

    def test_missing_user_id_returns_422(self, client):
        resp = client.get("/api/v1/combat-log", params={"guild_id": 699744305274945650})
        assert resp.status_code == 422

    def test_missing_guild_id_returns_422(self, client):
        resp = client.get("/api/v1/combat-log", params={"user_id": 402296276617527306})
        assert resp.status_code == 422

    def test_limit_default_25(self, client, mock_service):
        resp = client.get("/api/v1/combat-log", params={"user_id": 100, "guild_id": 999})
        assert resp.status_code == 200
        call_kwargs = mock_service.list_for_player.call_args
        assert call_kwargs is not None

    def test_empty_list(self, client, mock_service):
        mock_service.list_for_player = AsyncMock(return_value=[])
        resp = client.get("/api/v1/combat-log", params={"user_id": 404, "guild_id": 999})
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Tests: GET /api/v1/combat-log/{id}
# ---------------------------------------------------------------------------


class TestGetCombatLogDetail:
    def test_returns_detail(self, client):
        resp = client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert data["outcome"] == "won"
        assert "key_events" in data
        assert "combatant1" in data
        assert "combatant2" in data

    def test_pvc_damage_reduction_in_response(self, client, mock_service):
        detail = _make_detail()
        detail["pvc_damage_reduction"] = 0.33
        mock_service.get_detail = AsyncMock(return_value=detail)
        resp = client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        assert resp.json()["pvc_damage_reduction"] == 0.33

    def test_ownership_gate_non_combatant_returns_404(self, client, mock_service):
        """Service raises KeyError for non-combatant — router maps to 404."""
        mock_service.get_detail = AsyncMock(side_effect=KeyError("not a combatant"))
        resp = client.get("/api/v1/combat-log/1", params={"user_id": 9999999})
        assert resp.status_code == 404

    def test_not_found_returns_404(self, client, mock_service):
        mock_service.get_detail = AsyncMock(side_effect=KeyError("not found"))
        resp = client.get("/api/v1/combat-log/9999", params={"user_id": 402296276617527306})
        assert resp.status_code == 404

    def test_missing_user_id_returns_422(self, client):
        resp = client.get("/api/v1/combat-log/1")
        assert resp.status_code == 422

    def test_stalemate_outcome_in_response(self, client, mock_service):
        detail = _make_detail()
        detail["outcome"] = "stalemate"
        detail["is_stalemate"] = True
        detail["winner_name"] = None
        mock_service.get_detail = AsyncMock(return_value=detail)
        resp = client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "stalemate"
