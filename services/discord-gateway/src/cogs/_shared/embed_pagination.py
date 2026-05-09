"""Cog-adjacent helpers for Discord embed field pagination.

These helpers live alongside cogs (not in utils/) because they encode
game-layer rendering conventions for bounty/listing embeds. If the game
is replaced with something else, this module is removed along with cogs/.

Primary helper: :func:`add_continuation_fields` — appends one or more
fields to a ``discord.Embed`` so that a single conceptual section is
rendered with exactly one visible header, even when the content must be
split across multiple fields due to Discord's 1024-char per-field cap.
"""

from __future__ import annotations

import discord

# Discord hard limits (see Discord Embed Limits documentation).
MAX_FIELD_VALUE = 1024
MAX_FIELDS = 25

# LEFT-TO-RIGHT MARK: zero-width but counts as a non-empty field name.
# Used for continuation-field headers so Discord renders a single visual
# section without repeating the initial label.
SPACER_NAME = "\u200e"

# Default cap for game listings.  The current max category (modules,
# 66 items) stays well under this, so truncation is future-proofing.
DEFAULT_LIST_CAP = 100


def add_continuation_fields(
    embed: discord.Embed,
    header_name: str,
    lines: list[str],
    *,
    cap: int = DEFAULT_LIST_CAP,
    inline: bool = False,
) -> int:
    """Append one-or-more fields carrying ``lines`` to ``embed``.

    The first field is named ``header_name``. Any subsequent continuation
    fields use :data:`SPACER_NAME` (zero-width) so Discord renders a single
    visual section rather than repeating the header text.

    Splitting rule: each field's value stays under :data:`MAX_FIELD_VALUE`
    characters. Lines are joined with ``\\n`` and flushed to a new field
    whenever appending the next line would exceed the cap.

    Truncation rule: at most ``cap`` lines are rendered. The caller is
    responsible for setting a footer (e.g. ``"Showing first N of M objects"``)
    when it knows the source list was larger than ``cap``.

    Args:
        embed: The embed to mutate (fields appended in place).
        header_name: Visible header for the first field.
        lines: Pre-formatted lines (one per row). The caller controls
            per-line formatting, including any emoji prefixes.
        cap: Max lines to render (default :data:`DEFAULT_LIST_CAP`).
        inline: Forwarded to ``embed.add_field(inline=...)``.

    Returns:
        The number of lines rendered (never more than ``cap``).

    Notes:
        - If ``lines`` is empty, no fields are added and returns ``0``.
        - Respects Discord's 25-field-per-embed limit defensively; stops
          adding fields if the ceiling is reached.
        - Emoji-prefixed lines are preserved verbatim.
    """
    if not lines:
        return 0

    # Enforce the cap up-front so subsequent logic is simple.
    effective = lines[:cap]

    rendered = 0
    first_field = True
    current_buf: list[str] = []
    current_len = 0

    def _flush() -> None:
        """Emit the accumulated buffer as one field (header or continuation)."""
        nonlocal first_field, current_buf, current_len
        if not current_buf:
            return
        if len(embed.fields) >= MAX_FIELDS:
            # Defensive: embed is already at Discord's hard ceiling.
            current_buf = []
            current_len = 0
            return
        name = header_name if first_field else SPACER_NAME
        value = "\n".join(current_buf)
        embed.add_field(name=name, value=value, inline=inline)
        first_field = False
        current_buf = []
        current_len = 0

    for line in effective:
        # Account for the newline separator that will be inserted between
        # lines in the same field. The first line in a buffer has no leading
        # newline.
        projected = current_len + (1 if current_buf else 0) + len(line)
        if current_buf and projected > MAX_FIELD_VALUE:
            _flush()
            if len(embed.fields) >= MAX_FIELDS:
                # Can't add any more fields — stop processing.
                break
            current_buf = [line]
            current_len = len(line)
        else:
            current_buf.append(line)
            current_len = projected
        rendered += 1

    _flush()
    return rendered
