"""Cog-adjacent shared helpers (game-layer).

Modules in this package are imported by cogs in the sibling `cogs/` package.
They live here (not under `utils/`) because they encode game-layer rendering
conventions — if the game layer is replaced, this package is removed along
with the cogs.

Not auto-loaded as a cog: `bot.py`'s extension loader iterates entries that
end with `.py` on `os.listdir("src/cogs")`, and a directory entry does not
match that predicate. The leading underscore is an additional convention
signalling "private-but-colocated" to future maintainers.
"""
