"""Tests for GET/POST/DELETE /api/v1/events endpoints.

Mirrors test_duel_router.py style: TestClient + mocks/overrides.
Uses the conftest.py shared fixtures (mock_db_session, sqlalchemy_utils mock).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_event(**kw):
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    defaults = dict(
        id=1,
        guild_id=99,
        type_slug="duels_won",
        state="draft",
        params={},
        duration_days=7,
        scheduled_start_at=None,
        started_at=None,
        ends_at=None,
        created_by_user_id=42,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kw)
    ev = MagicMock()
    for k, v in defaults.items():
        setattr(ev, k, v)
    return ev


def make_prize(**kw):
    defaults = dict(id=1, event_id=1, rank_from=1, rank_to=1, kind="credits", item_ref=None, qty=1000)
    defaults.update(kw)
    p = MagicMock()
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


def make_player(pid=10, uid=1001, display_name="Alpha"):
    p = MagicMock()
    p.id = pid
    p.user_id = uid
    p.display_name = display_name
    u = MagicMock()
    u.discord_username = f"user_{uid}"
    p.user = u
    return p


# ---------------------------------------------------------------------------
# Shared app fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_ctx():
    """Return (mock_session, patcher) — patcher must be started/stopped."""
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _begin():
        yield

    mock_session.begin = MagicMock(side_effect=lambda: _begin())

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_session, mock_cm


@pytest.fixture
def app():
    from api.routers.events import router as ev_router

    application = FastAPI()
    application.include_router(ev_router, prefix="/api/v1")
    return application


@pytest.fixture
def client(app):
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. GET /events/types
# ---------------------------------------------------------------------------


class TestListEventTypes:
    def test_returns_all_known_types(self, client):
        resp = client.get("/api/v1/events/types")
        assert resp.status_code == 200
        body = resp.json()
        slugs = {t["slug"] for t in body}
        assert "duels_won" in slugs
        assert "avg_accuracy" in slugs
        for t in body:
            assert "display_name" in t
            assert "category" in t
            assert isinstance(t["params"], list)


# ---------------------------------------------------------------------------
# 2. POST /events — create event
# ---------------------------------------------------------------------------


class TestCreateEvent:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_create_valid(self, mock_audit, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        ev = make_event(id=5, type_slug="duels_won", duration_days=14)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_db.return_value = mock_cm

        # add() sets ev.id via the ORM flush — stub it so response has an id
        def _add(obj):
            obj.id = 5

        mock_session.add = MagicMock(side_effect=_add)

        resp = client.post(
            "/api/v1/events?user_id=42",
            json={"guild_id": 99, "type_slug": "duels_won", "duration_days": 14, "params": {}},
        )
        assert resp.status_code == 201

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    def test_create_invalid_type(self, mock_admin, client):
        resp = client.post(
            "/api/v1/events?user_id=42",
            json={"guild_id": 99, "type_slug": "not_a_real_type", "duration_days": 7},
        )
        assert resp.status_code == 400
        assert "Unknown event type" in resp.json()["detail"]

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    def test_create_bad_duration(self, mock_admin, client):
        # 0 days — should fail pydantic validation
        resp = client.post(
            "/api/v1/events?user_id=42",
            json={"guild_id": 99, "type_slug": "duels_won", "duration_days": 0},
        )
        assert resp.status_code == 422

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    def test_create_bad_division_param(self, mock_admin, client):
        resp = client.post(
            "/api/v1/events?user_id=42",
            json={"guild_id": 99, "type_slug": "duels_won", "duration_days": 7, "params": {"division": "Diamond"}},
        )
        assert resp.status_code == 400
        assert "division" in resp.json()["detail"].lower()

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=False)
    def test_create_forbidden(self, mock_admin, client):
        resp = client.post(
            "/api/v1/events?user_id=99",
            json={"guild_id": 99, "type_slug": "duels_won", "duration_days": 7},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. POST /events/{id}/prizes — overlap rejection
# ---------------------------------------------------------------------------


class TestAddPrize:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_overlap_rejected(self, mock_audit, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm

        ev = make_event(state="draft")
        existing_prize = make_prize(rank_from=1, rank_to=3)

        # execute returns event on first call, prizes on second
        calls = [
            MagicMock(scalar_one_or_none=lambda ev=ev: ev),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [existing_prize])),
        ]
        mock_session.execute = AsyncMock(side_effect=calls)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"rank_from": 2, "rank_to": 4, "kind": "credits", "qty": 500},
        )
        assert resp.status_code == 400
        assert "overlap" in resp.json()["detail"].lower()

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    def test_credits_with_item_ref_rejected(self, mock_admin, client):
        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"rank_from": 1, "rank_to": 1, "kind": "credits", "item_ref": "SomeItem", "qty": 100},
        )
        assert resp.status_code == 400
        assert "item_ref" in resp.json()["detail"].lower()

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    def test_rank_from_gt_rank_to_rejected(self, mock_admin, client):
        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"rank_from": 5, "rank_to": 2, "kind": "credits", "qty": 100},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. POST /events/{id}/start — scheduling validation
# ---------------------------------------------------------------------------


class TestStartEvent:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_schedule_future(self, mock_audit, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()

        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": future},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "scheduled"

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_schedule_past_rejected(self, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": past},
        )
        assert resp.status_code == 400
        assert "future" in resp.json()["detail"].lower()

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_schedule_beyond_90d_rejected(self, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        far = (datetime.now(UTC) + timedelta(days=91)).isoformat()
        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": far},
        )
        assert resp.status_code == 400
        assert "90" in resp.json()["detail"]

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.event_service.start_event", new_callable=AsyncMock)
    @patch("api.routers.events.event_service.announce", new_callable=AsyncMock)
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_start_now(self, mock_audit, mock_announce, mock_start_ev, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()
        mock_start_ev.return_value = (99, 123, {}, None)  # announcement tuple

        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": None},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        mock_announce.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. POST /events/{id}/end — 409 when not active
# ---------------------------------------------------------------------------


class TestEndEvent:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_end_not_active_409(self, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        resp = client.post(
            "/api/v1/events/1/end?guild_id=99&user_id=42",
            json={"payout": True},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 6. GET /events/guild/{guild_id} — state filtering
# ---------------------------------------------------------------------------


class TestListGuildEvents:
    @patch("api.routers.events.get_db_session")
    def test_list_filters_by_state(self, mock_db, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm

        ev_active = make_event(id=1, state="active")

        execute_calls = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [ev_active])),  # filtered list
            MagicMock(all=lambda: []),  # prize count query
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.get("/api/v1/events/guild/99?state=active")
        assert resp.status_code == 200
        body = resp.json()
        assert all(e["state"] == "active" for e in body)

    @patch("api.routers.events.get_db_session")
    def test_list_includes_type_display(self, mock_db, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(id=1, state="active", type_slug="duels_won")
        execute_calls = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [ev])),
            MagicMock(all=lambda: []),
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.get("/api/v1/events/guild/99")
        assert resp.status_code == 200
        assert resp.json()[0]["type_display"] == "Duels Won"


# ---------------------------------------------------------------------------
# 7. GET /events/guild/{guild_id}/medals — Olympic ordering
# ---------------------------------------------------------------------------


class TestMedals:
    @patch("api.routers.events.get_db_session")
    def test_medals_olympic_ordering(self, mock_db, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm

        # player 10: 1 gold; player 20: 2 silver — gold should be first
        r1 = MagicMock(player_id=10, rank=1, qualified=True, guild_id=99, type_slug="duels_won")
        r2 = MagicMock(player_id=20, rank=2, qualified=True, guild_id=99, type_slug="duels_won")
        r3 = MagicMock(player_id=20, rank=2, qualified=True, guild_id=99, type_slug="bounty_caps")

        p10 = make_player(pid=10, uid=1001, display_name="GoldGuy")
        p20 = make_player(pid=20, uid=1002, display_name="SilverGuy")

        execute_calls = [
            MagicMock(scalars=lambda: MagicMock(all=lambda: [r1, r2, r3])),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [p10, p20])),
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.get("/api/v1/events/guild/99/medals")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # GoldGuy has gold=1; SilverGuy has gold=0 → GoldGuy first
        assert body[0]["display_name"] == "GoldGuy"
        assert body[0]["gold"] == 1
        assert body[1]["silver"] == 2


# ---------------------------------------------------------------------------
# 8. Router path sanity — no /events/events double-prefix
# ---------------------------------------------------------------------------


class TestRouterPaths:
    def test_no_events_events_path(self, app):
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        doubles = [p for p in paths if "events/events" in p]
        assert doubles == [], f"Double-prefix routes found: {doubles}"


# ---------------------------------------------------------------------------
# 9. Guild cross-check — 403 when event.guild_id != guild_id
# ---------------------------------------------------------------------------


class TestGuildCrossCheck:
    @pytest.mark.parametrize(
        "method,path_tmpl,body",
        [
            ("DELETE", "/api/v1/events/{event_id}?guild_id=999&user_id=42", None),
            ("POST", "/api/v1/events/{event_id}/prizes?guild_id=999&user_id=42",
             {"rank_from": 1, "rank_to": 1, "kind": "credits", "qty": 100}),
            ("DELETE", "/api/v1/events/{event_id}/prizes/1?guild_id=999&user_id=42", None),
            ("POST", "/api/v1/events/{event_id}/start?guild_id=999&user_id=42",
             {"scheduled_start_at": None}),
            ("POST", "/api/v1/events/{event_id}/end?guild_id=999&user_id=42",
             {"payout": False}),
        ],
    )
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_cross_guild_403(self, mock_db, mock_admin, method, path_tmpl, body, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        # Event belongs to guild 99; request uses guild_id=999
        ev = make_event(id=1, guild_id=99, state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        path = path_tmpl.format(event_id=1)
        resp = client.request(method, path, json=body)
        assert resp.status_code == 403, f"{method} {path} expected 403, got {resp.status_code}: {resp.text}"
        assert "another guild" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 10. start_event state gates
# ---------------------------------------------------------------------------


class TestStartEventStateGates:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_schedule_branch_on_active_409(self, mock_db, mock_admin, client, db_ctx):
        """Scheduling (body with future timestamp) from active state → 409."""
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="active")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": future},
        )
        assert resp.status_code == 409

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_immediate_start_on_active_409(self, mock_db, mock_admin, client, db_ctx):
        """Immediate start (no scheduled_start_at) from active state → 409."""
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="active")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": None},
        )
        assert resp.status_code == 409

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.event_service.start_event", new_callable=AsyncMock)
    @patch("api.routers.events.event_service.announce", new_callable=AsyncMock)
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_immediate_start_on_scheduled_clears_scheduled_at(
        self, mock_audit, mock_announce, mock_start_ev, mock_db, mock_admin, client, db_ctx
    ):
        """Immediate start on scheduled event → active; event_service.start_event clears scheduled_start_at."""
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="scheduled", scheduled_start_at=datetime.now(UTC) + timedelta(days=1))
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()
        mock_start_ev.return_value = (99, 123, {}, None)

        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": None},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        mock_start_ev.assert_awaited_once()


# ---------------------------------------------------------------------------
# 11. delete_event state gates
# ---------------------------------------------------------------------------


class TestDeleteEventStateGates:
    @pytest.mark.parametrize("state", ["active", "ended"])
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_delete_non_deletable_state_409(self, mock_db, mock_admin, state, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state=state)
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        resp = client.delete("/api/v1/events/1?guild_id=99&user_id=42")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 12. add_prize / delete_prize state gates
# ---------------------------------------------------------------------------


class TestPrizeStateGates:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_delete_prize_on_active_409(self, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="active")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))

        resp = client.delete("/api/v1/events/1/prizes/1?guild_id=99&user_id=42")
        assert resp.status_code == 409

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    def test_add_prize_on_ended_409(self, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="ended")

        execute_calls = [
            MagicMock(scalar_one_or_none=lambda ev=ev: ev),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [])),
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"rank_from": 1, "rank_to": 1, "kind": "credits", "qty": 100},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 13. end_event with payout=False → cancelled
# ---------------------------------------------------------------------------


class TestEndEventCancelled:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.event_service.end_event", new_callable=AsyncMock)
    @patch("api.routers.events.event_service.announce", new_callable=AsyncMock)
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_end_payout_false_cancelled(
        self, mock_audit, mock_announce, mock_end_ev, mock_db, mock_admin, client, db_ctx
    ):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="active")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_end_ev.return_value = {"state": "cancelled"}

        resp = client.post(
            "/api/v1/events/1/end?guild_id=99&user_id=42",
            json={"payout": False},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "cancelled"


# ---------------------------------------------------------------------------
# 14. Participation prize duplicate → 400
# ---------------------------------------------------------------------------


class TestParticipationPrizeDuplicate:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_second_participation_prize_400(self, mock_audit, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm

        ev = make_event(state="draft")
        existing_participation = make_prize(rank_from=None, rank_to=None)

        execute_calls = [
            MagicMock(scalar_one_or_none=lambda ev=ev: ev),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [existing_participation])),
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"kind": "credits", "qty": 100},
        )
        assert resp.status_code == 400
        assert "participation" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 15. Touching rank ranges → 400  (1..3 + 3..5 overlaps at 3)
# ---------------------------------------------------------------------------


class TestTouchingRanges:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_touching_ranges_400(self, mock_audit, mock_db, mock_admin, client, db_ctx):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm

        ev = make_event(state="draft")
        existing = make_prize(rank_from=1, rank_to=3)

        execute_calls = [
            MagicMock(scalar_one_or_none=lambda ev=ev: ev),
            MagicMock(scalars=lambda: MagicMock(all=lambda: [existing])),
        ]
        mock_session.execute = AsyncMock(side_effect=execute_calls)

        resp = client.post(
            "/api/v1/events/1/prizes?guild_id=99&user_id=42",
            json={"rank_from": 3, "rank_to": 5, "kind": "credits", "qty": 100},
        )
        assert resp.status_code == 400
        assert "overlap" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 16. state=bogus → 400
# ---------------------------------------------------------------------------


class TestListGuildEventsStateValidation:
    def test_unknown_state_400(self, client):
        resp = client.get("/api/v1/events/guild/99?state=bogus")
        assert resp.status_code == 400
        assert "bogus" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 17. announce-after-commit ordering
# ---------------------------------------------------------------------------


class TestAnnounceAfterCommit:
    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.event_service.start_event", new_callable=AsyncMock)
    @patch("api.routers.events.event_service.announce", new_callable=AsyncMock)
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    def test_announce_called_after_db_context_exits(
        self, mock_audit, mock_announce, mock_start_ev, mock_db, mock_admin, client, db_ctx
    ):
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()
        mock_start_ev.return_value = (99, 123, {}, None)

        call_order: list[str] = []

        async def track_exit(*a):
            call_order.append("db_exit")
            return False

        async def track_announce(*a):
            call_order.append("announce")

        mock_cm.__aexit__ = track_exit
        mock_announce.side_effect = track_announce

        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": None},
        )
        assert resp.status_code == 200
        assert "db_exit" in call_order
        assert "announce" in call_order
        assert call_order.index("db_exit") < call_order.index("announce"), (
            f"announce was called before db context exited: {call_order}"
        )

    @patch("api.routers.events.verify_admin_permissions", new_callable=AsyncMock, return_value=True)
    @patch("api.routers.events.get_db_session")
    @patch("api.routers.events.AuditService.log_action", new_callable=AsyncMock)
    @patch("api.routers.events._push_events_cache", new_callable=AsyncMock)
    def test_scheduled_push_called_after_db_context_exits(
        self, mock_push, mock_audit, mock_db, mock_admin, client, db_ctx
    ):
        """Scheduled path: _push_events_cache must be called after the db context exits (post-commit)."""
        mock_session, mock_cm = db_ctx
        mock_db.return_value = mock_cm
        ev = make_event(state="draft")
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: ev))
        mock_session.flush = AsyncMock()

        call_order: list[str] = []

        async def track_exit(*a):
            call_order.append("db_exit")
            return False

        async def track_push(*a):
            call_order.append("push")

        mock_cm.__aexit__ = track_exit
        mock_push.side_effect = track_push

        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        resp = client.post(
            "/api/v1/events/1/start?guild_id=99&user_id=42",
            json={"scheduled_start_at": future},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "scheduled"
        assert "db_exit" in call_order
        assert "push" in call_order
        assert call_order.index("db_exit") < call_order.index("push"), (
            f"push was called before db context exited: {call_order}"
        )
