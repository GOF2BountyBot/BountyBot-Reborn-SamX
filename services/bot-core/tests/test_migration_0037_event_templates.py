"""Migration tests for 0037_event_templates: game_events.name + partial unique index, idempotent."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock

import sqlalchemy as sa
from sqlalchemy import create_engine, text

if "shared" not in sys.modules:
    _mock_shared = types.ModuleType("shared")
    _mock_bblogger = types.ModuleType("shared.bblogger")
    _mock_bblogger.get_logger = MagicMock(return_value=MagicMock())
    _mock_shared.bblogger = _mock_bblogger
    sys.modules["shared"] = _mock_shared
    sys.modules["shared.bblogger"] = _mock_bblogger

_MIGRATION_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "src",
        "persist",
        "database",
        "revisions",
        "versions",
        "0037_event_templates.py",
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_0037_real", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(
            text(
                "CREATE TABLE game_events "
                "(id INTEGER PRIMARY KEY, guild_id BIGINT NOT NULL, state VARCHAR(16) NOT NULL)"
            )
        )
    return engine


def _mock_op(engine):
    op = MagicMock()
    op.get_bind.return_value = engine.connect()

    def add_column(table, column, **_):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} ADD COLUMN {column.name} VARCHAR(64)"))

    def create_index(name, table, cols, unique=False, sqlite_where=None, **_):
        where = f" WHERE {sqlite_where}" if sqlite_where is not None else ""
        with engine.begin() as c:
            c.execute(text(f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {table} ({', '.join(cols)}){where}"))

    def drop_index(name, table_name=None, **_):
        with engine.begin() as c:
            c.execute(text(f"DROP INDEX {name}"))

    def drop_column(table, col, **_):
        with engine.begin() as c:
            c.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))

    op.add_column.side_effect = add_column
    op.create_index.side_effect = create_index
    op.drop_index.side_effect = drop_index
    op.drop_column.side_effect = drop_column
    return op


def _cols(engine):
    return {c["name"] for c in sa.inspect(engine).get_columns("game_events")}


def _idx(engine):
    return {i["name"] for i in sa.inspect(engine).get_indexes("game_events")}


def test_upgrade_adds_name_and_partial_unique_index():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    assert "name" in _cols(engine) and "ux_game_events_template_name" in _idx(engine)
    with engine.begin() as c:  # only templates are constrained: two drafts may share a name, two templates may not
        c.execute(text("INSERT INTO game_events (guild_id, state, name) VALUES (1, 'draft', 'x'), (1, 'draft', 'x')"))
        c.execute(text("INSERT INTO game_events (guild_id, state, name) VALUES (1, 'template', 'Weekly')"))
        c.execute(text("INSERT INTO game_events (guild_id, state, name) VALUES (2, 'template', 'Weekly')"))
    import pytest

    with pytest.raises(sa.exc.IntegrityError), engine.begin() as c:
        c.execute(text("INSERT INTO game_events (guild_id, state, name) VALUES (1, 'template', 'Weekly')"))


def test_upgrade_is_idempotent():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    mod.op = _mock_op(engine)
    mod.upgrade()  # second run must skip both ops
    assert mod.op.add_column.call_count == 0 and mod.op.create_index.call_count == 0


def test_downgrade_removes_both():
    engine, mod = _engine(), _load()
    mod.op = _mock_op(engine)
    mod.upgrade()
    mod.op = _mock_op(engine)
    mod.downgrade()
    assert "name" not in _cols(engine) and "ux_game_events_template_name" not in _idx(engine)
