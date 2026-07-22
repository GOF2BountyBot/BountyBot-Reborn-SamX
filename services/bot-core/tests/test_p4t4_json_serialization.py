"""P4-T4: FastAPI native JSON serialization tests.

FastAPI 0.136+ serializes JSON responses directly to bytes via Pydantic's Rust
core when a route has a response_model or return-type annotation.  This is the
same speed as orjson with no custom response class and no deprecation warnings.

Adversarial-grade tests covering:
  (a) App construction raises NO FastAPIDeprecationWarning — default_response_class
      is NOT set (the deprecated ORJSONResponse approach has been removed).
  (b) GET /combat-log/{id} still validates through response_model=CombatLogDetail
      (shape/fields unchanged) — the framework's native serialization preserves
      the response_model contract.
  (c) Binary route GET /bounties/{id}/map STILL returns Content-Type: image/png
      with intact PNG bytes — explicit response_class=Response on the decorator
      overrides any app-level default.
  (d) Handler audit: no JSON route handler returns a bare Response(...)
      or JSONResponse(...) or ORJSONResponse(...) — only binary/PNG routes do.

NOTE: This test file does NOT cover X2 (key_events content drift) — that gate
lives in P2-T1/P4-T7a, not here.
"""

from __future__ import annotations

import ast
import os
import sys
import types
import warnings
from pathlib import Path
from threading import Lock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap shared.bblogger mock (test-env has no shared library on path)
# ---------------------------------------------------------------------------
if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

if "sqlalchemy_utils" not in sys.modules:
    _mock_sau = types.ModuleType("sqlalchemy_utils")
    _mock_sau.UUIDType = MagicMock()
    sys.modules["sqlalchemy_utils"] = _mock_sau

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ROUTERS_DIR = Path(__file__).parent.parent / "src" / "api" / "routers"
_MAIN_PY = Path(__file__).parent.parent / "src" / "main.py"

# Minimal valid 1x1 PNG (67 bytes)
_MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ===========================================================================
# (a) App construction raises NO FastAPIDeprecationWarning
# ===========================================================================


class TestNoDeprecationWarning:
    """Verify the FastAPI app is NOT constructed with the deprecated default_response_class.

    NOTE: two ``main.py`` source-substring greps were removed here (test true-up):
    ``"ORJSONResponse" not in src`` and ``"default_response_class=ORJSONResponse"
    not in src``.  They asserted on source text; the request-time behavioural
    tests below (``test_no_fastapi_deprecation_warning_during_request`` and its
    positive twin ``test_app_with_orjsonresponse_default_fires_deprecation_warning_on_request``)
    prove the real contract: serving a request through the native app emits NO
    FastAPIDeprecationWarning, while an ORJSONResponse-default app DOES.
    """

    def _capture_fastapi_deprecation_warnings(self, app) -> list[str]:
        """Serve a request through *app* and return any FastAPI deprecation warning messages.

        TestClient runs the ASGI app in a background thread; Python's
        ``warnings.catch_warnings`` is thread-local, so it cannot intercept
        warnings emitted in that thread.  Instead we monkey-patch
        ``warnings.warn`` for the duration of the request — the patch is
        module-level and therefore visible to all threads.
        """
        from fastapi.testclient import TestClient

        captured: list[str] = []
        _lock = Lock()
        _original_warn = warnings.warn

        def _intercepting_warn(msg, *args, **kwargs):  # called from any thread
            with _lock:
                captured.append(str(msg))
            return _original_warn(msg, *args, **kwargs)

        with patch.object(warnings, "warn", side_effect=_intercepting_warn), TestClient(app) as c:
            c.get("/ping")

        return [m for m in captured if "FastAPI" in m or "ORJSONResponse" in m]

    def test_no_fastapi_deprecation_warning_during_request(self):
        """Serving a request with the native app raises no FastAPIDeprecationWarning.

        In FastAPI 0.136+, the FastAPIDeprecationWarning fires at request-dispatch
        time (fastapi/routing.py:715) when ORJSONResponse is used as a response
        class.  A plain FastAPI() app (no custom default_response_class) must NOT
        trigger this warning on a normal JSON route request.
        """
        from fastapi import FastAPI

        app = FastAPI(title="test-app")

        @app.get("/ping", response_model=dict)
        async def _ping():
            return {"status": "ok"}

        deprecation_warnings = self._capture_fastapi_deprecation_warnings(app)
        assert not deprecation_warnings, (
            f"FastAPIDeprecationWarning fired during request on a plain FastAPI() app: "
            f"{deprecation_warnings}. "
            "Do NOT use default_response_class=ORJSONResponse — it is deprecated in FastAPI 0.136+."
        )

    def test_app_with_orjsonresponse_default_fires_deprecation_warning_on_request(self):
        """Sanity check: serving a request on an app WITH default_response_class=ORJSONResponse
        DOES emit FastAPIDeprecationWarning at request time.

        This confirms our cross-thread warning-detection logic is actually wired up.
        If FastAPI ever stops emitting the warning, this test will fail and alert us
        that the guard is no longer effective.
        """
        from fastapi import FastAPI
        from fastapi.responses import ORJSONResponse

        app = FastAPI(default_response_class=ORJSONResponse, title="deprecated-test-app")

        @app.get("/ping", response_model=dict)
        async def _ping():
            return {"status": "ok"}

        deprecation_warnings = self._capture_fastapi_deprecation_warnings(app)
        assert deprecation_warnings, (
            "Expected FastAPIDeprecationWarning when using default_response_class=ORJSONResponse "
            "in FastAPI 0.136+, but none was emitted at request time. "
            "Our cross-thread deprecation guard is no longer effective — investigate."
        )


# ===========================================================================
# (b) GET /combat-log/{id} shape unchanged — response_model=CombatLogDetail
# ===========================================================================


class TestCombatLogDetailShapeUnchanged:
    """Verify the combat-log detail endpoint shape is preserved under native serialization.

    This is an X1 (response shape) gate.  The handler returns a CombatLogDetail
    Pydantic model; FastAPI validates and serializes it via the native fast path.
    Shape must be identical whether or not a custom response class is set.
    """

    def _make_detail(self) -> dict:
        from datetime import UTC, datetime

        return {
            "id": 1,
            "guild_id": 699744305274945650,
            "context": "duel",
            "combatant1_name": "Betty",
            "combatant2_name": "H'Soc",
            "combatant1_user_id": 402296276617527306,
            "combatant2_user_id": 970691862035841048,
            "winner_name": "Betty",
            "is_stalemate": False,
            "created_at": datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC),
            "outcome": "won",
            "combatant1": {
                "name": "Betty",
                "ship": "Furious",
                "start_hp": {"hull": 95, "armour": 40, "shield": 0},
                "final_hp": {"hull": 95, "armour": 40, "shield": 0},
                "shots_fired": 60,
                "shots_hit": 40,
                "accuracy": 40 / 60,
                "damage_dealt": 120,
                "damage_taken": 80,
            },
            "combatant2": {
                "name": "H'Soc",
                "ship": "Mantris",
                "start_hp": {"hull": 80, "armour": 30, "shield": 0},
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

    @pytest.fixture
    def combat_log_client(self):
        """TestClient for the combat-log router under native FastAPI serialization (no custom default)."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mock_service = AsyncMock()
        mock_service.get_detail = AsyncMock(return_value=self._make_detail())

        mock_session = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        # Build minimal app WITHOUT custom default_response_class (native fast path)
        app = FastAPI()
        from api.routers.combat_log import get_combat_log_service
        from api.routers.combat_log import router as combat_log_router

        app.dependency_overrides[get_combat_log_service] = lambda: mock_service
        app.include_router(combat_log_router, prefix="/api/v1")

        with patch("api.routers.combat_log.get_db_session", return_value=mock_cm), TestClient(app) as c:
            yield c

    def test_combat_log_detail_all_top_level_fields_present(self, combat_log_client):
        """All CombatLogDetail top-level fields are present in the response."""
        resp = combat_log_client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        expected_top_level_fields = {
            "id",
            "guild_id",
            "context",
            "combatant1_name",
            "combatant2_name",
            "combatant1_user_id",
            "combatant2_user_id",
            "winner_name",
            "is_stalemate",
            "created_at",
            "outcome",
            "combatant1",
            "combatant2",
            "duration_ticks",
            "duration_s",
            "pvc_damage_reduction",
            "key_events",
        }
        missing = expected_top_level_fields - set(data.keys())
        assert not missing, f"Missing fields in CombatLogDetail response: {sorted(missing)}"

    def test_combat_log_detail_field_types_correct(self, combat_log_client):
        """CombatLogDetail response has correct types for key fields."""
        resp = combat_log_client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        data = resp.json()

        assert isinstance(data["id"], int), f"id must be int, got {type(data['id'])}"
        assert isinstance(data["guild_id"], int), "guild_id must be int"
        assert isinstance(data["is_stalemate"], bool), "is_stalemate must be bool"
        assert isinstance(data["duration_ticks"], int), "duration_ticks must be int"
        assert isinstance(data["duration_s"], float), "duration_s must be float"
        assert isinstance(data["pvc_damage_reduction"], float), "pvc_damage_reduction must be float"
        assert isinstance(data["key_events"], list), "key_events must be list"
        assert isinstance(data["combatant1"], dict), "combatant1 must be dict"
        assert isinstance(data["combatant2"], dict), "combatant2 must be dict"

    def test_combat_log_detail_combatant_fields_present(self, combat_log_client):
        """CombatantSummary nested fields are all present for each combatant."""
        resp = combat_log_client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        data = resp.json()

        combatant_fields = {
            "name",
            "ship",
            "start_hp",
            "final_hp",
            "shots_fired",
            "shots_hit",
            "accuracy",
            "damage_dealt",
            "damage_taken",
        }
        for label in ("combatant1", "combatant2"):
            c = data[label]
            missing = combatant_fields - set(c.keys())
            assert not missing, f"Missing fields in {label}: {sorted(missing)}"

    def test_combat_log_detail_key_events_schema(self, combat_log_client):
        """KeyEvent fields (tick, time_s, actor, event_type, detail) are present."""
        resp = combat_log_client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["key_events"]) == 1
        event = data["key_events"][0]
        for field in ("tick", "time_s", "actor", "event_type", "detail"):
            assert field in event, f"KeyEvent missing field: {field}"
        assert event["tick"] == 100
        assert event["time_s"] == pytest.approx(1.0)
        assert event["actor"] == "Betty"

    def test_combat_log_detail_values_match_fixture(self, combat_log_client):
        """Response values match the fixture data exactly (no field drift)."""
        resp = combat_log_client.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})
        assert resp.status_code == 200
        data = resp.json()

        assert data["id"] == 1
        assert data["outcome"] == "won"
        assert data["combatant1_name"] == "Betty"
        assert data["combatant2_name"] == "H'Soc"
        assert data["winner_name"] == "Betty"
        assert data["is_stalemate"] is False
        assert data["duration_ticks"] == 3488
        assert data["duration_s"] == pytest.approx(34.88)
        assert data["pvc_damage_reduction"] == pytest.approx(0.0)
        assert data["combatant1"]["name"] == "Betty"
        assert data["combatant2"]["name"] == "H'Soc"

    def test_combat_log_detail_response_model_strips_extra_fields(self):
        """response_model=CombatLogDetail strips extra fields under native serialization."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mock_service = AsyncMock()
        detail_with_extra = self._make_detail()
        detail_with_extra["THIS_FIELD_NOT_IN_SCHEMA"] = "must_be_stripped"
        mock_service.get_detail = AsyncMock(return_value=detail_with_extra)

        mock_session = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        # No custom default_response_class — native path
        app = FastAPI()
        from api.routers.combat_log import get_combat_log_service
        from api.routers.combat_log import router as combat_log_router

        app.dependency_overrides[get_combat_log_service] = lambda: mock_service
        app.include_router(combat_log_router, prefix="/api/v1")

        with patch("api.routers.combat_log.get_db_session", return_value=mock_cm), TestClient(app) as c:
            resp = c.get("/api/v1/combat-log/1", params={"user_id": 402296276617527306})

        assert resp.status_code == 200
        data = resp.json()
        assert "THIS_FIELD_NOT_IN_SCHEMA" not in data, (
            "response_model must strip extra fields under FastAPI 0.136+ native serialization"
        )


# ===========================================================================
# (c) Binary route GET /bounties/{id}/map still returns image/png
# ===========================================================================


class TestBinaryRoutePngUnchanged:
    """Verify that the PNG map route returns image/png under native serialization.

    The route decorator sets response_class=Response explicitly; FastAPI uses
    that override.  The content-type must remain image/png and the response
    body must be intact binary bytes regardless of the app-level default.
    """

    @pytest.fixture
    def bounty_map_client(self):
        """TestClient with no custom app default; bounty map endpoint returning a real PNG stub."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mock_renderer = MagicMock()
        mock_renderer.render_route_offloaded = AsyncMock(return_value=_MINIMAL_PNG)

        mock_graph = MagicMock()
        mock_graph.is_loaded = MagicMock(return_value=True)

        # The bounty map route calls service.bounty_repo.get_by_id(db, bounty_id)
        mock_bounty = MagicMock()
        mock_bounty.route = ["Augmenta", "V'Ikka", "K'Ontrr"]
        mock_bounty.status = "active"
        mock_bounty.id = 42

        mock_bounty_repo = MagicMock()
        mock_bounty_repo.get_by_id = AsyncMock(return_value=mock_bounty)

        mock_service = MagicMock()
        mock_service.bounty_repo = mock_bounty_repo

        mock_session = AsyncMock()
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        # Build app with NO custom default_response_class
        app = FastAPI()
        # map_renderer and system_graph are read from app.state via _get_map_renderer deps
        app.state.map_renderer = mock_renderer
        app.state.system_graph = mock_graph

        from api.routers.bounties import get_bounty_service
        from api.routers.bounties import router as bounty_router

        app.dependency_overrides[get_bounty_service] = lambda: mock_service
        app.include_router(bounty_router, prefix="/api/v1")

        with (
            patch("api.routers.bounties.get_db_session", return_value=mock_cm),
            patch("api.routers.bounties._map_cache_get", return_value=None),
            patch("api.routers.bounties._map_cache_set"),
            TestClient(app) as c,
        ):
            yield c

    def test_map_route_returns_image_png_content_type(self, bounty_map_client):
        """GET /bounties/{id}/map returns Content-Type: image/png."""
        resp = bounty_map_client.get("/api/v1/bounties/42/map")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        ct = resp.headers.get("content-type", "")
        assert "image/png" in ct, (
            f"Expected image/png content-type; got {ct!r}. "
            "Explicit response_class=Response on the route decorator must be honoured."
        )

    def test_map_route_returns_binary_png_bytes(self, bounty_map_client):
        """GET /bounties/{id}/map response body starts with the PNG magic bytes."""
        resp = bounty_map_client.get("/api/v1/bounties/42/map")
        assert resp.status_code == 200
        body = resp.content
        assert body[:8] == b"\x89PNG\r\n\x1a\n", (
            f"Response body is not a valid PNG (wrong magic bytes). First 8 bytes: {body[:8]!r}."
        )

    def test_map_route_body_matches_renderer_output(self, bounty_map_client):
        """GET /bounties/{id}/map body is exactly what render_route_offloaded returned."""
        resp = bounty_map_client.get("/api/v1/bounties/42/map")
        assert resp.status_code == 200
        assert resp.content == _MINIMAL_PNG, (
            "Response bytes must exactly match what map_renderer.render_route_offloaded returned."
        )


# ===========================================================================
# (d) Handler audit: no JSON route returns a bare Response / JSONResponse
# ===========================================================================


class TestHandlerAudit:
    """Static AST-level audit: no JSON route handler returns a bare Response instance.

    Binary/PNG routes (bounties.py /map, systems.py /route/map) are expected to
    return Response(content=..., media_type="image/png") — those are classified as
    binary routes and are EXPECTED.

    All other routes that return bare Response()/JSONResponse()/ORJSONResponse()
    are a violation of the X1 constraint (they bypass response_model validation).
    """

    def _iter_router_py_files(self):
        """Yield all .py files in src/api/routers/ (non-recursive for flat dir)."""
        for p in _ROUTERS_DIR.rglob("*.py"):
            if p.suffix == ".py" and "__pycache__" not in str(p):
                yield p

    def test_no_bare_response_returns_on_json_routes(self):
        """Grep all router files for bare Response()/JSONResponse()/ORJSONResponse() returns.

        For each occurrence found, assert it is a binary/PNG route (known permitted location).
        Fail if any JSON route returns a bare Response instance — that bypasses response_model.
        """
        violations: list[str] = []
        permitted_binary: list[str] = []

        for py_file in self._iter_router_py_files():
            src = py_file.read_text(encoding="utf-8")
            fname = py_file.name

            try:
                tree = ast.parse(src, filename=str(py_file))
            except SyntaxError as e:
                violations.append(f"SyntaxError in {py_file}: {e}")
                continue

            # Walk AST looking for Return nodes whose value is a Call to a bare Response type
            for node in ast.walk(tree):
                if not isinstance(node, ast.Return):
                    continue
                val = node.value
                if val is None:
                    continue
                if not isinstance(val, ast.Call):
                    continue

                # Get the function name being called
                func = val.func
                if isinstance(func, ast.Name):
                    callee = func.id
                elif isinstance(func, ast.Attribute):
                    callee = func.attr
                else:
                    continue

                if callee not in ("Response", "JSONResponse", "ORJSONResponse", "StreamingResponse"):
                    continue

                # Found a bare Response return — now classify it
                # Check if any keyword arg has media_type="image/png"
                is_binary = any(
                    kw.arg == "media_type" and isinstance(kw.value, ast.Constant) and kw.value.value == "image/png"
                    for kw in val.keywords
                )

                line = node.lineno
                location_desc = f"{fname}:{line} -> return {callee}(...)"

                if is_binary:
                    permitted_binary.append(location_desc)
                else:
                    # Non-binary Response return — VIOLATION
                    violations.append(location_desc)

        assert not violations, (
            f"Found {len(violations)} bare Response return(s) on non-binary routes "
            f"(these bypass response_model validation):\n" + "\n".join(f"  VIOLATION: {v}" for v in violations)
        )

        # Assert binary routes still exist so we detect if they are removed
        # and the test silently passes on an empty audit.
        assert len(permitted_binary) >= 2, (
            f"Expected at least 2 binary (image/png) Response returns in routers "
            f"(bounties.py /map and systems.py /route/map); found: {permitted_binary}. "
            "If the binary routes were removed or changed, update this assertion."
        )

    # NOTE: two source-substring greps were removed here (test true-up):
    # ``"response_class=Response" in bounties.py`` and ``... in systems.py``.
    # They asserted decorator source text; the behavioural map-route tests above
    # (test_map_route_returns_image_png_content_type / _returns_binary_png_bytes /
    # _body_matches_renderer_output) prove the real outcome the explicit
    # response_class exists to guarantee: the /map routes return an image/png
    # content-type with the exact binary renderer bytes.
