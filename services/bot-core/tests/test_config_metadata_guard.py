"""
Deliverable 3 — parity guard tests for GET /config/metadata (issue #70).

Covers:
  1. Metadata field set == _OVERRIDE_FIELDS ∪ {starting_credits, sale_price_factor}.
  2. Every description is non-empty and ends with a period.
  3. Every FIELD_TO_CATALOG_ROW value appears as a `NAME` row in
     GAME_CONSTANTS_CATALOG.md.  Skips with a clear message when the catalog
     file is not visible from the current working environment (e.g. bare-
     container run that mounts only services/bot-core).
  4. Endpoint smoke tests via FastAPI TestClient: 200, all 97 fields present,
     sample of ~10 fields has correct type/min/max/default/description.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Bootstrap sys.path so imports resolve correctly.
# ---------------------------------------------------------------------------
_SRC = Path(__file__).parent.parent / "src"
_SRC_STR = str(_SRC)

if _SRC_STR in sys.path:
    sys.path.remove(_SRC_STR)
sys.path.insert(0, _SRC_STR)

for _key in list(sys.modules):
    if _key == "api" or _key.startswith("api."):
        _mod = sys.modules[_key]
        _file = getattr(_mod, "__file__", "") or ""
        if _SRC_STR not in _file:
            del sys.modules[_key]

from api.config_metadata import DEPRECATED_FIELDS, FIELD_DESCRIPTIONS, FIELD_TO_CATALOG_ROW
from api.routers.config import _METADATA_FIELDS, _OVERRIDE_FIELDS

# ---------------------------------------------------------------------------
# Catalog file — locate relative to this test file (repo root when CI checks
# out the full repo; not present when running inside the bare container).
# When the test file is at a shallow path (bare-container mount), parents[3]
# may not exist — catch that and treat the catalog as not visible.
# ---------------------------------------------------------------------------
try:
    _CATALOG_PATH: Path = Path(__file__).parents[3] / "GAME_CONSTANTS_CATALOG.md"
except IndexError:
    _CATALOG_PATH = Path("/nonexistent/GAME_CONSTANTS_CATALOG.md")
_CATALOG_VISIBLE: bool = _CATALOG_PATH.is_file()

# ---------------------------------------------------------------------------
# Expected total: 110 _OVERRIDE_FIELDS + starting_credits + sale_price_factor
# (revision 0033 dropped 7 JSONB dict fields, reducing _OVERRIDE_FIELDS from 117 to 110)
# ---------------------------------------------------------------------------
_EXPECTED_TOTAL = len(_OVERRIDE_FIELDS) + 2


class TestMetadataFieldSetParity:
    """Guard 1: metadata field set == _OVERRIDE_FIELDS ∪ {starting_credits, sale_price_factor}."""

    def test_metadata_fields_equals_override_fields_plus_two(self):
        """_METADATA_FIELDS must be exactly _OVERRIDE_FIELDS + starting_credits + sale_price_factor."""
        expected = set(_OVERRIDE_FIELDS) | {"starting_credits", "sale_price_factor"}
        actual = set(_METADATA_FIELDS)
        assert actual == expected, (
            f"_METADATA_FIELDS mismatch.\n"
            f"  In _METADATA_FIELDS only: {actual - expected!r}\n"
            f"  In expected only:         {expected - actual!r}"
        )

    def test_metadata_fields_has_no_duplicates(self):
        """_METADATA_FIELDS must not contain duplicates."""
        seen: set[str] = set()
        dupes: list[str] = []
        for f in _METADATA_FIELDS:
            if f in seen:
                dupes.append(f)
            seen.add(f)
        assert not dupes, f"Duplicate entries in _METADATA_FIELDS: {dupes}"

    def test_metadata_fields_count(self):
        """_METADATA_FIELDS has exactly 112 entries (110 _OVERRIDE_FIELDS + 2 config columns)."""
        assert len(_METADATA_FIELDS) == _EXPECTED_TOTAL, (
            f"Expected {_EXPECTED_TOTAL} metadata fields, got {len(_METADATA_FIELDS)}."
        )

    def test_field_descriptions_covers_all_metadata_fields(self):
        """FIELD_DESCRIPTIONS must have an entry for every field in _METADATA_FIELDS."""
        missing = [f for f in _METADATA_FIELDS if f not in FIELD_DESCRIPTIONS]
        assert not missing, f"FIELD_DESCRIPTIONS missing entries for: {missing!r}"

    def test_field_descriptions_has_no_stale_entries(self):
        """FIELD_DESCRIPTIONS must not contain keys absent from _METADATA_FIELDS."""
        expected = set(_METADATA_FIELDS)
        stale = [k for k in FIELD_DESCRIPTIONS if k not in expected]
        assert not stale, (
            f"FIELD_DESCRIPTIONS contains stale keys not in _METADATA_FIELDS: {stale!r}. Remove or move them."
        )


class TestDescriptionFormat:
    """Guard 2: every description is non-empty and ends with a period."""

    def test_all_descriptions_non_empty(self):
        """No description is empty or whitespace-only."""
        empty = [k for k, v in FIELD_DESCRIPTIONS.items() if not v.strip()]
        assert not empty, f"Empty descriptions for fields: {empty!r}"

    def test_all_descriptions_end_with_period(self):
        """Every description must end with a period (sentence-style help text)."""
        bad = [k for k, v in FIELD_DESCRIPTIONS.items() if not v.rstrip().endswith(".")]
        assert not bad, f"Descriptions not ending with a period: {bad!r}. Add a trailing period to each."

    def test_deprecated_field_descriptions_contain_keyword(self):
        """Deprecated JSONB fields must mention 'Deprecated' in their description."""
        missing_kw = [
            f for f in DEPRECATED_FIELDS if f in FIELD_DESCRIPTIONS and "Deprecated" not in FIELD_DESCRIPTIONS[f]
        ]
        assert not missing_kw, f"Deprecated fields whose descriptions don't mention 'Deprecated': {missing_kw!r}"


class TestCatalogParity:
    """Guard 3: every FIELD_TO_CATALOG_ROW value appears as a `NAME` row in the catalog.

    Skips when GAME_CONSTANTS_CATALOG.md is not visible (bare-container run).
    """

    @pytest.mark.skipif(
        not _CATALOG_VISIBLE,
        reason=(
            f"GAME_CONSTANTS_CATALOG.md not visible at {_CATALOG_PATH}. "
            "Run with a full repo checkout (e.g. the second docker run command "
            "that mounts /home/bweigel/BountyBot-Reborn-SamX as /app) to enforce "
            "catalog-parity tests."
        ),
    )
    def test_all_catalog_row_values_appear_in_catalog(self):
        """Every value in FIELD_TO_CATALOG_ROW is a `NAME` row in GAME_CONSTANTS_CATALOG.md."""
        catalog_text = _CATALOG_PATH.read_text(encoding="utf-8")
        # Extract all constant names from backtick cells in markdown tables:
        # matches `CONSTANT_NAME` patterns in table rows
        found_constants: set[str] = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", catalog_text))

        missing: list[str] = []
        for field, catalog_row in FIELD_TO_CATALOG_ROW.items():
            if catalog_row not in found_constants:
                missing.append(f"{field!r} → {catalog_row!r}")

        assert not missing, f"FIELD_TO_CATALOG_ROW values not found in {_CATALOG_PATH.name}:\n" + "\n".join(
            f"  {m}" for m in missing
        )

    @pytest.mark.skipif(
        not _CATALOG_VISIBLE,
        reason=f"GAME_CONSTANTS_CATALOG.md not visible at {_CATALOG_PATH}.",
    )
    def test_field_to_catalog_row_covers_non_deprecated_non_config_fields(self):
        """Every non-deprecated, non-config-column _OVERRIDE_FIELD has a catalog-row mapping."""
        config_only = {"starting_credits", "sale_price_factor"}
        not_in_map = [
            f
            for f in _OVERRIDE_FIELDS
            if f not in DEPRECATED_FIELDS and f not in config_only and f not in FIELD_TO_CATALOG_ROW
        ]
        assert not not_in_map, f"_OVERRIDE_FIELDS entries with no FIELD_TO_CATALOG_ROW entry: {not_in_map!r}"


class TestMetadataEndpointSampleAssertions:
    """Guard 4: endpoint smoke tests via FastAPI TestClient."""

    @pytest.fixture(autouse=True)
    def _setup_client(self):
        from api.routers.config import router as config_router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(config_router, prefix="/api/v1")
        self.client = TestClient(app)

    def test_metadata_returns_200(self):
        """GET /config/metadata returns HTTP 200."""
        response = self.client.get("/api/v1/config/metadata")
        assert response.status_code == 200

    def test_metadata_has_fields_key(self):
        """Response has a top-level 'fields' dict."""
        data = self.client.get("/api/v1/config/metadata").json()
        assert "fields" in data
        assert isinstance(data["fields"], dict)

    def test_metadata_has_all_112_fields(self):
        """The 'fields' dict has exactly 112 entries (110 _OVERRIDE_FIELDS + 2 config columns)."""
        data = self.client.get("/api/v1/config/metadata").json()
        fields = data["fields"]
        assert len(fields) == _EXPECTED_TOTAL, (
            f"Expected {_EXPECTED_TOTAL} fields in metadata response, got {len(fields)}.\n"
            f"Missing: {set(_METADATA_FIELDS) - set(fields)!r}\n"
            f"Extra:   {set(fields) - set(_METADATA_FIELDS)!r}"
        )

    def test_metadata_contains_all_expected_field_names(self):
        """Every expected field name is present in the response."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        missing = [f for f in _METADATA_FIELDS if f not in fields]
        assert not missing, f"Missing fields in metadata response: {missing!r}"

    def test_close_bounty_threshold_sample(self):
        """close_bounty_threshold has type=int, min=1, max=50, default=4."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["close_bounty_threshold"]
        assert f["type"] == "int"
        assert f["min"] == 1
        assert f["max"] == 50
        assert f["default"] == 4
        assert f["deprecated"] is False
        assert f["description"].endswith(".")

    def test_criminal_long_range_pct_sample(self):
        """criminal_long_range_pct has type=float, min=0.0, max=1.0, default=0.5."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["criminal_long_range_pct"]
        assert f["type"] == "float"
        assert f["min"] == pytest.approx(0.0)
        assert f["max"] == pytest.approx(1.0)
        assert f["default"] == pytest.approx(0.5)
        assert f["deprecated"] is False

    def test_criminal_exclude_emp_weapons_sample(self):
        """criminal_exclude_emp_weapons has type=bool, min=null, max=null, default=True."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["criminal_exclude_emp_weapons"]
        assert f["type"] == "bool"
        assert f["min"] is None
        assert f["max"] is None
        assert f["default"] is True
        assert f["deprecated"] is False

    def test_division_max_tl_bronze_flat_scalar_sample(self):
        """division_max_tl_bronze has type=int, min=1, max=10, default=2, not deprecated."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["division_max_tl_bronze"]
        assert f["type"] == "int"
        assert f["min"] == 1
        assert f["max"] == 10
        assert f["default"] == 2
        assert f["deprecated"] is False

    def test_loot_commodity_sell_fraction_sample(self):
        """loot_commodity_sell_fraction has type=float, min=0.0, max=10.0, default=1.0."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["loot_commodity_sell_fraction"]
        assert f["type"] == "float"
        assert f["min"] == pytest.approx(0.0)
        assert f["max"] == pytest.approx(10.0)
        assert f["default"] == pytest.approx(1.0)

    def test_shop_banded_tl_weight_sample(self):
        """shop_banded_tl_weight has type=float, min=0.0, max=1.0, default=0.7."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["shop_banded_tl_weight"]
        assert f["type"] == "float"
        assert f["min"] == pytest.approx(0.0)
        assert f["max"] == pytest.approx(1.0)
        assert f["default"] == pytest.approx(0.7)

    def test_starting_credits_sample(self):
        """starting_credits has type=int, default=0, min/max=null (no mixin field)."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["starting_credits"]
        assert f["type"] == "int"
        assert f["default"] == 0
        assert f["deprecated"] is False
        assert f["description"].endswith(".")

    def test_sale_price_factor_sample(self):
        """sale_price_factor has type=float, default=0.8, min/max=null (no mixin field)."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["sale_price_factor"]
        assert f["type"] == "float"
        assert f["default"] == pytest.approx(0.8)
        assert f["deprecated"] is False

    def test_check_cooldown_sample(self):
        """check_cooldown has type=int, min=0, max=86400, default=180."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["check_cooldown"]
        assert f["type"] == "int"
        assert f["min"] == 0
        assert f["max"] == 86400
        assert f["default"] == 180

    def test_pvc_damage_reduction_sample(self):
        """pvc_damage_reduction has type=float, min=0.0, max=1.0, default=0.33."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        f = fields["pvc_damage_reduction"]
        assert f["type"] == "float"
        assert f["min"] == pytest.approx(0.0)
        assert f["max"] == pytest.approx(1.0)
        assert f["default"] == pytest.approx(0.33)

    def test_all_field_objects_have_required_keys(self):
        """Every field object has type, min, max, default, description, deprecated."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        required_keys = {"type", "min", "max", "default", "description", "deprecated"}
        for name, obj in fields.items():
            missing = required_keys - set(obj.keys())
            assert not missing, f"Field {name!r} missing keys: {missing!r}"

    def test_all_descriptions_end_with_period_in_response(self):
        """All description strings in the response end with a period."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        bad = [name for name, obj in fields.items() if not obj["description"].rstrip().endswith(".")]
        assert not bad, f"Descriptions not ending with period in response: {bad!r}"

    def test_deprecated_fields_are_marked_deprecated(self):
        """All 7 deprecated JSONB dict fields are marked deprecated=True in the response."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        not_deprecated = [f for f in DEPRECATED_FIELDS if f in fields and not fields[f]["deprecated"]]
        assert not not_deprecated, f"Expected deprecated=True for: {not_deprecated!r}"

    def test_scalar_override_fields_are_not_deprecated(self):
        """Scalar (non-JSONB-dict) override fields are marked deprecated=False."""
        fields = self.client.get("/api/v1/config/metadata").json()["fields"]
        non_deprecated_overrides = [f for f in _OVERRIDE_FIELDS if f not in DEPRECATED_FIELDS]
        wrongly_deprecated = [f for f in non_deprecated_overrides if f in fields and fields[f]["deprecated"]]
        assert not wrongly_deprecated, (
            f"Fields marked deprecated=True but not in DEPRECATED_FIELDS: {wrongly_deprecated!r}"
        )
