"""Unit tests for ItemRepository.

Mock-based tests (no SQLite/ARRAY columns involved).
Covers:
- __init__ stores Item model (stub repository)
"""

import os
import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock shared.bblogger and sqlalchemy_utils BEFORE any src imports
# ---------------------------------------------------------------------------
_mock_shared = ModuleType("shared")
_mock_shared.bblogger = MagicMock()
_mock_shared.bblogger.get_logger = MagicMock(return_value=MagicMock())
sys.modules.setdefault("shared", _mock_shared)
sys.modules.setdefault("shared.bblogger", _mock_shared.bblogger)

_mock_sau = ModuleType("sqlalchemy_utils")
_mock_sau.UUIDType = MagicMock()
sys.modules.setdefault("sqlalchemy_utils", _mock_sau)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from persist.repositories.item_repository import ItemRepository

# ---------------------------------------------------------------------------
# TestItemRepositoryInit
# ---------------------------------------------------------------------------


class TestItemRepositoryInit:
    def test_init_stores_item_model(self):
        """ItemRepository.__init__ must store the Item model class."""
        from persist.models.item import Item

        repo = ItemRepository()
        assert repo._model is Item

    def test_init_creates_instance_successfully(self):
        """ItemRepository can be instantiated without errors."""
        repo = ItemRepository()
        assert repo is not None

    def test_inherits_generic_repository(self):
        """ItemRepository must be a subclass of GenericRepository."""
        from persist.repositories.generic_repository import GenericRepository

        repo = ItemRepository()
        assert isinstance(repo, GenericRepository)
