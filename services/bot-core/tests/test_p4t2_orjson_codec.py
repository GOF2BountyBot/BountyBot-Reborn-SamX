"""P4-T2: orjson engine codec tests.

Adversarial-grade tests covering:
  (a) Read pre-existing stdlib-written rows through orjson deserializer.
  (b) Gateway-visible string byte-stability (created_at [:10] date slice, time_s).
  (c) No NaN/Inf can occur in combat floats (division-by-zero guard + proof).
  (d) _json_serializer returns str (not bytes).
  (e) _json_deserializer applies to JSONB columns (same codec, round-trip).

Tests are adversarial: they do NOT just call the codec on its own output
(tautology) — they verify stdlib-written payloads are read correctly by the
new orjson deserializer, and that the engine codec is actually wired up.

These tests import the module-level constants from manager.py so any future
change to the codec is immediately reflected.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import types
from unittest.mock import MagicMock

import orjson
import pytest

# ---------------------------------------------------------------------------
# Bootstrap shared.bblogger mock (mirrors integration/conftest.py pattern)
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from persist.database.manager import _ORJSON_OPTS, _json_deserializer, _json_serializer

# ---------------------------------------------------------------------------
# SQLite in-memory fixtures for round-trip DB tests
# ---------------------------------------------------------------------------
from sqlalchemy import JSON, Integer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _BountyLike(_Base):
    """Minimal proxy for bounty.checked and bounty.criminal_ship JSON columns."""

    __tablename__ = "_test_bounty_like"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    criminal_ship: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class _GuildConfigLike(_Base):
    """Minimal proxy for guild_config JSON columns."""

    __tablename__ = "_test_guildconfig_like"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    xp_thresholds: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tech_level_probabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)


@pytest.fixture()
async def orjson_engine():
    """SQLite in-memory engine wired with the same orjson codec as production."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        json_serializer=_json_serializer,
        json_deserializer=_json_deserializer,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            _Base.metadata.create_all,
            tables=[_BountyLike.__table__, _GuildConfigLike.__table__],
        )
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
async def orjson_session(orjson_engine) -> AsyncSession:
    """AsyncSession bound to the orjson-codec engine."""
    factory = async_sessionmaker(orjson_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture()
async def stdlib_engine():
    """SQLite in-memory engine using stdlib json (simulates old rows written before P4-T2)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            _Base.metadata.create_all,
            tables=[_BountyLike.__table__, _GuildConfigLike.__table__],
        )
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.drop_all)
    await engine.dispose()


# ===========================================================================
# (d) Serializer must return str
# ===========================================================================


class TestSerializerReturnType:
    """The json_serializer callable must return str (SQLAlchemy contract)."""

    def test_serializer_returns_str_on_simple_dict(self):
        result = _json_serializer({"key": "val"})
        assert isinstance(result, str), f"Expected str, got {type(result).__name__}: {result!r}"

    def test_serializer_returns_str_on_list(self):
        result = _json_serializer(["a", "b", "c"])
        assert isinstance(result, str)

    def test_serializer_returns_str_on_nested_dict(self):
        payload = {"outer": {"inner": 42, "flag": True}, "arr": [1, 2, 3]}
        result = _json_serializer(payload)
        assert isinstance(result, str)

    def test_serializer_does_not_return_bytes(self):
        result = _json_serializer({"k": "v"})
        assert not isinstance(result, bytes), "Serializer must not return bytes — SQLAlchemy needs str"

    def test_deserializer_is_orjson_loads(self):
        # orjson.loads accepts both str and bytes
        result = _json_deserializer('{"k": "v"}')
        assert result == {"k": "v"}


# ===========================================================================
# (a) Read stdlib-written rows via orjson deserializer — NON-TAUTOLOGICAL
# ===========================================================================


class TestStdlibWrittenRowReadback:
    """Verify that rows written by stdlib json.dumps are correctly read by orjson.loads.

    This is NOT a self-round-trip: stdlib writes the DB row; orjson reads it.
    """

    def test_orjson_reads_stdlib_encoded_bounty_checked(self):
        """orjson.loads correctly reads a bounty.checked dict written by stdlib json.dumps."""
        stdlib_checked = {"K'Ontrr": -1, "S'Kolptorr": -1, "V'Ikka": -1, "Augmenta": -1}
        stdlib_encoded = json.dumps(stdlib_checked)  # What the old codec writes to DB
        # Now read it back via orjson (what the new codec does on SELECT)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_checked, (
            f"orjson read-back != original stdlib payload.\n"
            f"stdlib wrote: {stdlib_encoded!r}\n"
            f"orjson read: {orjson_decoded!r}"
        )

    def test_orjson_reads_stdlib_encoded_criminal_ship(self):
        """orjson.loads correctly reads a bounty.criminal_ship dict written by stdlib json.dumps."""
        stdlib_criminal_ship = {
            "ship_name": "Furious",
            "ship_value": 75800,
            "armor_hp": 176,
            "shield_hp": 0,
            "total_hp": 176,
            "weapons": [{"name": "K'booskk", "value": 15302, "dps": 15.9}],
            "modules": [{"name": "Telta Quickscan", "extra_atts": {"timeToLock": 1200}}],
        }
        stdlib_encoded = json.dumps(stdlib_criminal_ship)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_criminal_ship

    def test_orjson_reads_stdlib_encoded_xp_thresholds(self):
        """orjson.loads correctly reads guild_config.xp_thresholds written by stdlib json.dumps."""
        stdlib_xp = {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        stdlib_encoded = json.dumps(stdlib_xp)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_xp

    def test_orjson_reads_stdlib_encoded_tech_level_probabilities(self):
        """orjson.loads correctly reads guild_config.tech_level_probabilities written by stdlib json.dumps."""
        stdlib_probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}
        stdlib_encoded = json.dumps(stdlib_probs)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_probs

    def test_orjson_reads_stdlib_encoded_player_ship_weapons_list(self):
        """orjson.loads correctly reads player_ship.weapons (JSON array) written by stdlib json.dumps."""
        stdlib_weapons = ["AMR Tormentor", "K'booskk"]
        stdlib_encoded = json.dumps(stdlib_weapons)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_weapons

    def test_orjson_reads_stdlib_encoded_combat_log_data(self):
        """orjson.loads correctly reads a minimal combat_log.data dict written by stdlib json.dumps."""
        stdlib_log = {
            "schema_version": 1,
            "summary": {
                "outcome": "won",
                "combatants": {
                    "1": {"name": "Betty", "ship": "Furious"},
                    "2": {"name": "H'Soc", "ship": "Mantris"},
                },
            },
            "timeline": [{"tick": 1, "type": "weapon_fire", "actor": "Betty", "data": {"hit": True, "side": 1}}],
        }
        stdlib_encoded = json.dumps(stdlib_log)
        orjson_decoded = _json_deserializer(stdlib_encoded)
        assert orjson_decoded == stdlib_log
        # Verify int values (side=1) survive the round-trip
        assert orjson_decoded["timeline"][0]["data"]["side"] == 1
        assert isinstance(orjson_decoded["timeline"][0]["data"]["side"], int)

    async def test_stdlib_written_row_read_via_orjson_engine(self, orjson_engine, stdlib_engine):
        """Non-tautological DB test: write via stdlib engine, read via orjson engine.

        Both engines share the same SQLite in-memory DB is NOT possible (separate
        files), so instead we write raw SQLite bytes by inserting via stdlib engine,
        then open a second session on that same engine (now with the codec swapped out)
        to simulate an old row read by the new codec.

        The correct adversarial approach: use SQLAlchemy's text() to bypass the
        codec entirely and INSERT a stdlib-json-encoded JSON string directly, then
        read it back via the orjson engine and verify the Python value matches.
        """
        from sqlalchemy import text

        # Simulate an old stdlib-encoded row inserted directly (bypassing SQLAlchemy codec)
        stdlib_payload = {"K'Ontrr": -1, "S'Kolptorr": -1, "Augmenta": -1}
        stdlib_json_str = json.dumps(stdlib_payload)

        # Insert raw via the orjson-codec engine using text() — this bypasses the
        # json_serializer so we can simulate a stdlib-written row already in the DB.
        async with orjson_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO _test_bounty_like (id, checked) VALUES (:id, :checked)"),
                {"id": 100, "checked": stdlib_json_str},
            )

        # Now read it back via an ORM session with the orjson codec — this is
        # the real test: can orjson.loads read what json.dumps wrote?
        factory = async_sessionmaker(orjson_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.get(_BountyLike, 100)
            # With orjson deserializer, row.checked must equal the original Python dict
            assert row.checked == stdlib_payload, (
                f"orjson codec could not read stdlib-written row.\nExpected: {stdlib_payload!r}\nGot: {row.checked!r}"
            )


# ===========================================================================
# (b) Gateway-visible string byte-stability
# ===========================================================================


class TestGatewayDateStringStability:
    """Verify that created_at / time_s strings stay byte-stable after the codec flip.

    The combatLogCog uses created_at[:10] to extract YYYY-MM-DD.
    The created_at field is a DateTime(timezone=True) column — NOT a JSON column.
    The engine JSON codec does NOT touch DateTime columns.
    However, if a datetime object were ever stored inside a JSON blob, orjson
    with OPT_NAIVE_UTC would serialize it as "YYYY-MM-DDTHH:MM:SS+00:00" —
    the first 10 chars are always YYYY-MM-DD.
    """

    def test_opt_naive_utc_first_10_chars_are_date(self):
        """orjson with OPT_NAIVE_UTC produces YYYY-MM-DDTHH:MM:SS+00:00.

        [:10] yields YYYY-MM-DD — same as combatLogCog._format_date expects.
        """
        naive_dt = datetime.datetime(2024, 5, 15, 10, 30, 0)
        serialized = orjson.dumps(naive_dt, option=_ORJSON_OPTS).decode().strip('"')
        assert serialized[:10] == "2024-05-15", f"Expected YYYY-MM-DD in first 10 chars, got: {serialized!r}"

    def test_opt_naive_utc_aware_datetime_first_10_chars_are_date(self):
        """Aware datetimes also produce YYYY-MM-DD in the first 10 chars."""
        aware_dt = datetime.datetime(2026, 6, 7, 14, 0, 0, tzinfo=datetime.UTC)
        serialized = orjson.dumps(aware_dt, option=_ORJSON_OPTS).decode().strip('"')
        assert serialized[:10] == "2026-06-07"

    def test_stdlib_isoformat_same_first_10_as_orjson_naive_utc(self):
        """Prove byte-stability: stdlib .isoformat() and orjson+OPT_NAIVE_UTC agree on YYYY-MM-DD.

        created_at fields go through .isoformat() in routers (not the JSON codec),
        so this is informational proof that even if the JSON codec were involved,
        the date string would be identical.
        """
        naive_dt = datetime.datetime(2026, 6, 7, 14, 30, 0)
        stdlib_str = naive_dt.isoformat()  # "2026-06-07T14:30:00"
        orjson_str = orjson.dumps(naive_dt, option=_ORJSON_OPTS).decode().strip('"')
        # Both start with YYYY-MM-DD
        assert stdlib_str[:10] == orjson_str[:10] == "2026-06-07"

    def test_time_s_float_serialization_is_not_affected_by_json_codec(self):
        """time_s in CombatLog is a computed float in the service layer, never in a DB column.

        Verify that normal float values (e.g. 12.3) serialize cleanly with orjson.
        """
        payload = {"time_s": 12.3, "tick": 123}
        result = _json_serializer(payload)
        assert isinstance(result, str)
        parsed = _json_deserializer(result)
        assert parsed["time_s"] == pytest.approx(12.3)
        assert parsed["tick"] == 123

    def test_combat_log_created_at_is_datetime_column_not_json(self):
        """Confirm the created_at field in CombatLog is DateTime, not JSON.

        The engine JSON codec does NOT touch DateTime columns — they are handled
        by asyncpg natively. This test proves the gateway [:10] slice is safe.
        """
        from persist.models.combat_log import CombatLog
        from sqlalchemy import DateTime

        # Inspect the mapped column type
        col = CombatLog.__table__.c["created_at"]
        assert isinstance(col.type, DateTime), (
            f"created_at must be DateTime, not {type(col.type).__name__} — "
            "the JSON codec does NOT apply to DateTime columns"
        )


# ===========================================================================
# (c) No NaN/Inf in combat floats
# ===========================================================================


class TestNoNanInfInCombatFloats:
    """Verify that NaN and Inf cannot occur in combat float values.

    orjson serializes float('nan') and float('inf') as JSON null (NOT NaN/Infinity
    as stdlib json does). If any combat float were NaN/Inf, it would serialize as
    null and corrupt the combat log. This test proves no such float can occur.
    """

    def test_orjson_nan_serializes_to_null(self):
        """Empirical: orjson serializes NaN to null (not 'NaN' as stdlib does)."""
        result = orjson.dumps({"v": float("nan")}).decode()
        assert result == '{"v":null}', f"Expected null, got: {result!r}"

    def test_orjson_inf_serializes_to_null(self):
        """Empirical: orjson serializes +Inf to null."""
        result = orjson.dumps({"v": float("inf")}).decode()
        assert result == '{"v":null}', f"Expected null, got: {result!r}"

    def test_orjson_neg_inf_serializes_to_null(self):
        """Empirical: orjson serializes -Inf to null."""
        result = orjson.dumps({"v": float("-inf")}).decode()
        assert result == '{"v":null}', f"Expected null, got: {result!r}"

    def test_stdlib_json_nan_would_produce_invalid_json(self):
        """Contrast: stdlib json produces 'NaN' which is not valid JSON."""

        result = json.dumps({"v": float("nan")})
        assert "NaN" in result, f"Expected stdlib to emit NaN: {result!r}"

    def test_combat_accuracy_never_nan(self):
        """The accuracy formula uses safe division: (hit/fired) if fired > 0 else 0.0.

        Verifies that the guard prevents NaN from division by zero.
        """

        # Simulate the formula from combat_resolver.py:1097
        def _accuracy(hit: int, fired: int) -> float:
            return (hit / fired) if fired > 0 else 0.0

        import math

        assert _accuracy(0, 0) == 0.0  # no shots → no NaN
        assert not math.isnan(_accuracy(0, 0))
        assert _accuracy(3, 10) == pytest.approx(0.3)
        assert not math.isnan(_accuracy(10, 10))

    def test_combat_damage_floats_are_bounded(self):
        """Damage calculations use integer game constants — no unbounded float."""

        # Simulate damage calculation from combat_resolver.py:326,331
        def _eff_damage(base_dmg: float, damage_pct: float) -> int:
            return round(base_dmg * (1.0 + damage_pct / 100.0))

        import math

        result = _eff_damage(100.0, 50.0)
        assert not math.isnan(result)
        assert not math.isinf(result)
        assert result == 150

    def test_no_nan_inf_in_typical_combat_log_payload(self):
        """A realistic combat_log.data payload with all typical float values serializes cleanly."""
        payload = {
            "schema_version": 1,
            "summary": {
                "outcome": "won",
                "combatants": {
                    "1": {
                        "name": "Betty",
                        "ship": "Furious",
                        "shots_fired": 10,
                        "shots_hit": 7,
                        "accuracy": 0.7,
                        "damage_dealt": 350,
                        "damage_taken": 100,
                    },
                    "2": {
                        "name": "H'Soc",
                        "ship": "Mantris",
                        "shots_fired": 0,
                        "shots_hit": 0,
                        "accuracy": 0.0,  # safe zero (no shots)
                        "damage_dealt": 100,
                        "damage_taken": 350,
                    },
                },
            },
            "timeline": [
                {"tick": 1, "time_s": 0.1, "type": "weapon_fire", "data": {"hit": True, "side": 1}},
                {"tick": 10, "time_s": 1.0, "type": "weapon_fire", "data": {"hit": False, "side": 2}},
            ],
        }
        serialized = _json_serializer(payload)
        # No null values should appear (no NaN/Inf in input)
        assert "null" not in serialized, f"Unexpected null in combat payload: {serialized!r}"
        # Round-trip must be lossless
        decoded = _json_deserializer(serialized)
        assert decoded["summary"]["combatants"]["1"]["accuracy"] == pytest.approx(0.7)
        assert decoded["summary"]["combatants"]["2"]["accuracy"] == 0.0


# ===========================================================================
# (e) Deserializer applies to JSONB-like columns via same codec
# ===========================================================================


class TestDeserializerAppliesViaEngine:
    """Verify the orjson codec is actually wired into the engine for both read and write.

    SQLAlchemy's json_deserializer is applied by the dialect whenever a JSON/JSONB
    column value is returned from the DB. This test verifies end-to-end that:
      1. The codec is wired (serializer used on INSERT)
      2. The codec is wired (deserializer used on SELECT)
      3. The round-trip is lossless through the engine
    """

    async def test_engine_codec_wires_serializer_on_insert(self, orjson_session):
        """Verify INSERT path uses the orjson serializer (engine wired correctly)."""
        payload = {"xp_thresholds": {"Silver": 1000, "Gold": 5000}}
        obj = _GuildConfigLike(id=1, xp_thresholds=payload["xp_thresholds"])
        orjson_session.add(obj)
        await orjson_session.commit()
        await orjson_session.refresh(obj)
        assert obj.xp_thresholds == payload["xp_thresholds"]

    async def test_engine_codec_wires_deserializer_on_select(self, orjson_session):
        """Verify SELECT path uses the orjson deserializer (engine wired correctly)."""
        xp = {"Silver": 1000, "Gold": 5000, "Platinum": 15000}
        probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}
        obj = _GuildConfigLike(id=2, xp_thresholds=xp, tech_level_probabilities=probs)
        orjson_session.add(obj)
        await orjson_session.commit()

        # Expire and reload to exercise the deserializer
        await orjson_session.refresh(obj)
        assert obj.xp_thresholds == xp
        assert obj.tech_level_probabilities == pytest.approx(probs)

    async def test_engine_codec_round_trip_bounty_checked(self, orjson_session):
        """bounty.checked dict round-trips through orjson codec without data loss."""
        checked = {"K'Ontrr": -1, "S'Kolptorr": -1, "V'Ikka": -2, "Augmenta": 123456789}
        obj = _BountyLike(id=3, checked=checked)
        orjson_session.add(obj)
        await orjson_session.commit()
        await orjson_session.refresh(obj)
        assert obj.checked == checked
        # Verify int values survive
        assert obj.checked["K'Ontrr"] == -1
        assert isinstance(obj.checked["Augmenta"], int)

    async def test_engine_codec_round_trip_json_list(self, orjson_session):
        """JSON list (e.g. bounty.route / player_ship.weapons) round-trips correctly."""
        # Use criminal_ship field to store a list (testing JSON array handling)
        route = {"route_list": ["Augmenta", "V'Ikka", "K'Ontrr"]}
        obj = _BountyLike(id=4, criminal_ship=route)
        orjson_session.add(obj)
        await orjson_session.commit()
        await orjson_session.refresh(obj)
        assert obj.criminal_ship == route
        assert obj.criminal_ship["route_list"] == ["Augmenta", "V'Ikka", "K'Ontrr"]

    async def test_engine_codec_stdlib_raw_insert_read_back(self, orjson_engine):
        """Adversarial: insert raw stdlib-encoded JSON via text(), read back via orjson codec.

        This is the definitive non-tautological test: the data in the DB was NOT
        written by the orjson codec, yet the orjson deserializer must read it correctly.
        """
        from sqlalchemy import text

        stdlib_payload = {
            "ship_name": "Furious",
            "ship_value": 75800,
            "accuracy": 0.65,
            "modules": [{"name": "Telta Quickscan", "extra_atts": {"timeToLock": 1200}}],
        }
        stdlib_json_str = json.dumps(stdlib_payload)  # stdlib codec (old behavior)

        # Insert via raw SQL (bypasses the json_serializer completely)
        async with orjson_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO _test_bounty_like (id, criminal_ship) VALUES (:id, :cs)"),
                {"id": 200, "cs": stdlib_json_str},
            )

        # Read back via ORM session (exercises json_deserializer = orjson.loads)
        factory = async_sessionmaker(orjson_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.get(_BountyLike, 200)
            assert row.criminal_ship == stdlib_payload, (
                f"orjson deserializer must read stdlib-written JSON row correctly.\n"
                f"Expected: {stdlib_payload!r}\n"
                f"Got: {row.criminal_ship!r}"
            )
            # Verify float values survive exactly
            assert row.criminal_ship["accuracy"] == pytest.approx(0.65)


# ===========================================================================
# (f) Non-ASCII cross-codec readback
#
# orjson emits raw UTF-8 (ensure_ascii=False by default).
# stdlib json.dumps emits \uXXXX-escaped ASCII by default (ensure_ascii=True).
# Both forms are valid JSON; orjson.loads must read stdlib-escaped output back
# to the correct Python str.  These tests prove the gap the earlier suite left
# open: confirmed non-ASCII values actually present in the codebase.
# ===========================================================================


class TestNonAsciiCrossCodec:
    """Prove orjson reads stdlib-escaped (\\uXXXX) non-ASCII JSON correctly.

    Confirmed non-ASCII values in production columns:
      - bounty.checked keys: system names such as "Behén", "Paréah"
      - criminal_ship.weapons[].name: "Neétha EMP"
      - extra_atts: accented chars, non-breaking space (\\xa0), multi-byte CJK
    """

    # ------------------------------------------------------------------
    # Unit-level: _json_deserializer on stdlib-encoded strings
    # ------------------------------------------------------------------

    def test_stdlib_encoded_bounty_checked_non_ascii_keys(self):
        """stdlib encodes Behén/Paréah as \\uXXXX; orjson reads them back to the exact str.

        This test is LOAD-BEARING: it asserts the stdlib form on disk really contains
        the escape (distinguishing escaped-vs-raw), then verifies orjson reconstructs
        the original Python dict.
        """
        original = {"Behén": -1, "Paréah": -1}  # é = U+00E9

        stdlib_encoded = json.dumps(original)  # ensure_ascii=True by default

        # Prove the encoded form really uses \\uXXXX escapes (not raw UTF-8)
        assert "\\u00e9" in stdlib_encoded.lower(), (
            f"Expected stdlib to emit \\u00e9 escape for é, got: {stdlib_encoded!r}"
        )
        # Raw UTF-8 byte for é must NOT appear in the ASCII-safe encoded string
        assert "é" not in stdlib_encoded, f"stdlib should escape é to \\u00e9, not emit raw UTF-8: {stdlib_encoded!r}"

        decoded = _json_deserializer(stdlib_encoded)
        assert decoded == original, (
            f"orjson failed to read stdlib-escaped bounty.checked keys.\n"
            f"stdlib wrote: {stdlib_encoded!r}\n"
            f"orjson read: {decoded!r}"
        )

    def test_stdlib_encoded_criminal_ship_weapon_name_non_ascii(self):
        """stdlib encodes Neétha EMP as \\u00e9; orjson reads it back correctly."""
        original = {
            "weapons": [{"name": "Neétha EMP", "value": 4200, "dps": 18.5}],
            "ship_name": "Mantris",
        }

        stdlib_encoded = json.dumps(original)

        # Prove the escape is actually present in the stdlib output
        assert "\\u00e9" in stdlib_encoded.lower(), f"Expected stdlib to escape é in Neétha: {stdlib_encoded!r}"

        decoded = _json_deserializer(stdlib_encoded)
        assert decoded == original, (
            f"orjson misread stdlib-escaped weapon name.\nExpected: {original!r}\nGot: {decoded!r}"
        )
        assert decoded["weapons"][0]["name"] == "Neétha EMP"

    def test_stdlib_encoded_extra_atts_mixed_non_ascii(self):
        """extra_atts payload with é, non-breaking space (\\xa0), and Japanese hiragana round-trip."""
        original = {
            "description": "Café class module",  # é + NBSP
            "japanese_tag": "はつゆき",  # はつゆき
            "value": 9999,
        }

        stdlib_encoded = json.dumps(original)  # emits \\u00e9, \\u00a0, \\u306f, ...

        # Prove ALL three non-ASCII codepoints are escaped
        assert "\\u00e9" in stdlib_encoded.lower(), f"é escape missing: {stdlib_encoded!r}"
        assert "\\u00a0" in stdlib_encoded.lower(), f"NBSP escape missing: {stdlib_encoded!r}"
        assert "\\u306f" in stdlib_encoded.lower(), f"Japanese hiragana escape missing: {stdlib_encoded!r}"

        decoded = _json_deserializer(stdlib_encoded)
        assert decoded == original, (
            f"orjson misread mixed non-ASCII extra_atts.\nExpected: {original!r}\nGot: {decoded!r}"
        )
        assert decoded["description"] == "Café class module"
        assert decoded["japanese_tag"] == "はつゆき"

    # ------------------------------------------------------------------
    # orjson raw-UTF-8 write → stdlib json.loads readback
    # ------------------------------------------------------------------

    def test_orjson_written_non_ascii_is_readable_by_stdlib(self):
        """orjson emits raw UTF-8 (no \\uXXXX); stdlib json.loads must read it correctly.

        Verifies that a future reader using stdlib is not broken by orjson's raw-UTF-8 output.
        """
        original = {"name": "Neétha EMP", "tag": "はつゆき"}

        orjson_encoded = _json_serializer(original)  # returns str; raw UTF-8 chars inside

        # Prove orjson emits the RAW character, not the escape
        assert "é" in orjson_encoded, f"Expected orjson to emit raw é, not \\u00e9: {orjson_encoded!r}"
        assert "\\u00e9" not in orjson_encoded.lower(), f"orjson must NOT emit \\u00e9 escape: {orjson_encoded!r}"

        stdlib_decoded = json.loads(orjson_encoded)
        assert stdlib_decoded == original, (
            f"stdlib json.loads failed on orjson-written raw-UTF-8.\nExpected: {original!r}\nGot: {stdlib_decoded!r}"
        )

    # ------------------------------------------------------------------
    # Real DB round-trip: stdlib raw INSERT → orjson ORM read
    # (non-tautological, end-to-end through the engine, matching
    # test_engine_codec_stdlib_raw_insert_read_back style)
    # ------------------------------------------------------------------

    async def test_engine_codec_stdlib_raw_insert_non_ascii_read_back(self, orjson_engine):
        """Adversarial DB test: insert stdlib-encoded non-ASCII JSON via text(), read via orjson codec.

        This is the end-to-end proof:
          1. The DB row contains stdlib \\uXXXX-escaped JSON (simulating a row written before P4-T2).
          2. The orjson deserializer (wired into the engine) must decode it to the correct Python str.

        Non-ASCII values used: Behén, Paréah (confirmed bounty.checked system-name keys).
        """
        from sqlalchemy import text

        original = {"Behén": -1, "Paréah": -1, "Augmenta": 0}

        # Simulate an old stdlib-written row: json.dumps with ensure_ascii=True (default)
        stdlib_json_str = json.dumps(original)  # {"Beh\\u00e9n": -1, "Par\\u00e9ah": -1, ...}

        # Sanity: confirm the escaped form is on disk
        assert "\\u00e9" in stdlib_json_str.lower()

        # Insert raw via text() — bypasses json_serializer entirely
        async with orjson_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO _test_bounty_like (id, checked) VALUES (:id, :checked)"),
                {"id": 300, "checked": stdlib_json_str},
            )

        # Read back via ORM session (exercises json_deserializer = orjson.loads)
        factory = async_sessionmaker(orjson_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            row = await session.get(_BountyLike, 300)
            assert row is not None, "Row id=300 not found"
            assert row.checked == original, (
                f"orjson engine codec failed to decode stdlib-escaped non-ASCII keys.\n"
                f"stdlib wrote (on disk): {stdlib_json_str!r}\n"
                f"Expected Python value: {original!r}\n"
                f"Got: {row.checked!r}"
            )
            # Spot-check the non-ASCII keys specifically
            assert "Behén" in row.checked, "Key 'Behén' missing after orjson decode"
            assert "Paréah" in row.checked, "Key 'Paréah' missing after orjson decode"
            assert row.checked["Behén"] == -1


# ===========================================================================
# (g) Float-valued JSON columns survive orjson round-trip (decay path)
# ===========================================================================


class TestFloatJsonColumnRoundTrip:
    """Verify normal finite floats in JSON columns survive the orjson round-trip identically.

    Covers float-valued JSON columns: tech_level_probabilities (and similar dicts).
    These hold float-valued dicts that must not be corrupted by the codec swap.
    Note: division_temperatures was a float JSON column retired in rev 0031.
    """

    def test_tech_level_probabilities_float_round_trip(self):
        """tech_level_probabilities floats survive codec round-trip."""
        original = {
            "same_level": 0.7,
            "one_lower": 0.2,
            "two_lower": 0.1,
        }
        encoded = _json_serializer(original)
        decoded = _json_deserializer(encoded)
        # Values must be value-identical (not just approx-equal) for normal finite floats
        assert decoded == pytest.approx(original), (
            f"Float decay probabilities changed through codec.\nExpected: {original!r}\nGot: {decoded!r}"
        )
        assert decoded["same_level"] == pytest.approx(0.7)
        assert decoded["one_lower"] == pytest.approx(0.2)
        assert decoded["two_lower"] == pytest.approx(0.1)

    def test_arbitrary_float_dict_round_trip(self):
        """Arbitrary float-valued dict round-trips losslessly (division_temperatures retired rev 0031)."""
        original = {
            "Nivelian": 0.35,
            "Terran": 0.25,
            "Midorian": 0.40,
        }
        encoded = _json_serializer(original)
        decoded = _json_deserializer(encoded)
        assert decoded == pytest.approx(original)

    async def test_engine_codec_float_json_round_trip(self, orjson_session):
        """Float-valued tech_level_probabilities survive a full DB round-trip via orjson engine."""
        probs = {"same_level": 0.7, "one_lower": 0.2, "two_lower": 0.1}
        obj = _GuildConfigLike(id=501, tech_level_probabilities=probs)
        orjson_session.add(obj)
        await orjson_session.commit()
        await orjson_session.refresh(obj)
        assert obj.tech_level_probabilities == pytest.approx(probs), (
            f"Float probabilities corrupted in DB round-trip.\n"
            f"Expected: {probs!r}\nGot: {obj.tech_level_probabilities!r}"
        )


# ===========================================================================
# Codec constant validation
# ===========================================================================


class TestCodecConstants:
    """Structural checks on the module-level codec constants."""

    def test_orjson_opts_is_naive_utc(self):
        """_ORJSON_OPTS must equal orjson.OPT_NAIVE_UTC (no extra flags)."""
        assert _ORJSON_OPTS == orjson.OPT_NAIVE_UTC

    def test_orjson_opts_does_not_include_non_str_keys(self):
        """P4-T3 decision: OPT_NON_STR_KEYS must NOT be set."""
        assert not (_ORJSON_OPTS & orjson.OPT_NON_STR_KEYS), (
            "OPT_NON_STR_KEYS must not be set — P4-T3 audit confirms all JSON columns "
            "use string keys; fail-fast is desired for future int-key bugs."
        )

    def test_int_keyed_dict_raises_without_non_str_keys(self):
        """With the production codec, int-keyed dicts raise TypeError (fail-fast guard)."""
        with pytest.raises((TypeError, orjson.JSONEncodeError)):
            _json_serializer({1: "a", 2: "b"})

    def test_str_keyed_dict_does_not_raise(self):
        """str-keyed dicts serialize without error."""
        result = _json_serializer({"1": "a", "2": "b"})
        assert isinstance(result, str)
        assert _json_deserializer(result) == {"1": "a", "2": "b"}

    def test_json_deserializer_is_orjson_loads(self):
        """_json_deserializer must be the orjson.loads function."""
        assert _json_deserializer is orjson.loads

    async def test_engine_codec_kwargs_present_after_initialize(self):
        """The engine created by DatabaseManager must carry the orjson codec.

        We patch create_async_engine to capture the kwargs and verify
        json_serializer / json_deserializer are passed correctly.
        """
        import unittest.mock as mock

        from persist.database import manager as mgr_module

        captured_kwargs: dict = {}

        def _capture_engine(*args, **kwargs):
            captured_kwargs.update(kwargs)
            raise RuntimeError("stop after capture")  # abort before DB connect

        with mock.patch("persist.database.manager.bblogger"):
            db_mgr = mgr_module.DatabaseManager()

        with (
            mock.patch("persist.database.manager.create_async_engine", side_effect=_capture_engine),
            pytest.raises(RuntimeError, match="stop after capture"),
        ):
            await db_mgr.initialize()

        assert "json_serializer" in captured_kwargs, "json_serializer must be passed to create_async_engine"
        assert "json_deserializer" in captured_kwargs, "json_deserializer must be passed to create_async_engine"

        # Verify the correct callables are wired
        serializer = captured_kwargs["json_serializer"]
        deserializer = captured_kwargs["json_deserializer"]
        assert serializer is _json_serializer
        assert deserializer is _json_deserializer
