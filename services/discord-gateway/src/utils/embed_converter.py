"""
Embed conversion utilities for Discord Gateway service.

This module provides bidirectional conversion between JSON payloads
and Discord embeds, ensuring 100% consistency and round-trip accuracy.
The converter is completely generic and contains no business logic.
"""

from datetime import datetime
from typing import Any

import discord
from api.schemas.message_schemas import EmbedField, EmbedPayload
from shared import bblogger

flogger = bblogger.get_logger("discord-embed-converter")


class EmbedConverter:
    """
    Utility class for bidirectional conversion between payloads and Discord embeds.

    This converter is completely generic and maintains 100% consistency:
    payload -> embed -> payload should return identical data (modulo trivial
    normalization such as None vs omitted).
    """

    @staticmethod
    def _coerce_to_embed_payload(payload: EmbedPayload | dict[str, Any] | Any) -> EmbedPayload:
        """
        Coerce various input shapes into an EmbedPayload instance.

        Accepted input:
          - EmbedPayload (returned unchanged)
          - dict (used to instantiate EmbedPayload)
          - an object exposing .model_dump() that returns a mapping (e.g., another Pydantic model)
          - any mapping convertible via dict(payload)

        Raises TypeError / ValueError on unsupported types or validation failures.
        """
        flogger.debug(f"_coerce_to_embed_payload called with type: {type(payload).__name__}")
        if isinstance(payload, EmbedPayload):
            flogger.trace("Already EmbedPayload instance, returning unchanged")
            return payload

        # direct dict -> model
        if isinstance(payload, dict):
            flogger.trace(f"Coercing dict to EmbedPayload (keys: {list(payload.keys())[:5]}...)")
            return EmbedPayload(**payload)

        # pydantic v2 model or mapping-like with model_dump()
        if hasattr(payload, "model_dump") and callable(payload.model_dump):
            flogger.trace("Found .model_dump() method, attempting coercion")
            try:
                return EmbedPayload(**payload.model_dump())
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"Failed to coerce payload via .model_dump(): {e}")
                raise

        # pydantic v1 model with .dict() method
        if hasattr(payload, "dict") and callable(payload.dict):
            flogger.trace("Found .dict() method (Pydantic v1), attempting coercion")
            try:
                return EmbedPayload(**payload.dict())
            except Exception as e:  # pylint: disable=broad-exception-caught
                flogger.error(f"Failed to coerce payload via .dict(): {e}")
                raise

        # last-ditch: try to cast to dict()
        flogger.trace("Attempting final dict() conversion on mapping-like object")
        try:
            maybe_dict = dict(payload)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            flogger.error(f"payload_to_embed received unsupported payload type: {type(payload).__name__}")
            raise TypeError("payload must be an EmbedPayload or a dict-like mapping convertible to it") from exc
        try:
            flogger.trace(f"Instantiating EmbedPayload from dict with {len(maybe_dict)} keys")
            return EmbedPayload(**maybe_dict)
        except Exception:  # pylint: disable=broad-exception-caught
            flogger.error("Failed to coerce iterable-mapping payload to EmbedPayload")
            raise

    @staticmethod
    def payload_to_embed(payload: EmbedPayload | dict[str, Any] | Any) -> discord.Embed:
        """
        Convert a JSON payload (or EmbedPayload model) to a Discord embed.

        Accepts:
          - an EmbedPayload instance
          - a dict (will be coerced to EmbedPayload)
          - any mapping convertible to dict that matches EmbedPayload fields

        Returns:
          discord.Embed object

        Raises:
          - TypeError / ValueError if coercion fails or payload is of unsupported type
        """
        flogger.debug("payload_to_embed called")
        ep = EmbedConverter._coerce_to_embed_payload(payload)

        flogger.trace(f"Payload contains: title={bool(ep.title)}, description={bool(ep.description)}, color={ep.color}")
        try:
            # Create embed and set canonical fields
            embed = discord.Embed()
            flogger.trace("Created new discord.Embed instance")
            if ep.title is not None:
                embed.title = ep.title
                flogger.trace(f"Set title: {ep.title!r}")
            if ep.description is not None:
                embed.description = ep.description
                flogger.trace(f"Set description: {ep.description!r}")
            if ep.color is not None:
                try:
                    embed.color = discord.Color(ep.color)
                    flogger.trace(f"Set color from int value: {hex(ep.color)}")
                except Exception:  # pylint: disable=broad-exception-caught
                    # discord.Color may raise for invalid values; try int coercion
                    try:
                        embed.color = discord.Color(int(ep.color))
                        flogger.trace(f"Set color via int() conversion: {hex(ep.color)}")
                    except Exception:  # pylint: disable=broad-exception-caught
                        flogger.error(f"Invalid color value: {ep.color}")
                        raise

            # Fields: be defensive if fields is None
            field_count = len(ep.fields or [])
            flogger.trace(f"Processing {field_count} fields")
            for idx, field in enumerate(ep.fields or []):
                # field is expected to be an EmbedField (pydantic) already
                name = getattr(field, "name", "")
                value = getattr(field, "value", "")
                inline = bool(getattr(field, "inline", False))
                embed.add_field(name=name, value=value, inline=inline)
                flogger.trace(f"Added field[{idx}]: name={name!r}, inline={inline}, value_len={len(value)}")

            # Footer
            if ep.footer_text is not None:
                embed.set_footer(text=ep.footer_text, icon_url=ep.footer_icon_url)
                flogger.trace(f"Set footer: text={ep.footer_text!r}, icon_url_present={bool(ep.footer_icon_url)}")

            # Timestamp
            if ep.timestamp is not None:
                # discord.Embed expects a datetime; ensure instance is a datetime
                if not isinstance(ep.timestamp, datetime):
                    flogger.error(f"timestamp was not a datetime instance: {type(ep.timestamp).__name__}")
                    raise TypeError("timestamp must be a datetime instance")
                embed.timestamp = ep.timestamp
                flogger.trace(f"Set timestamp: {ep.timestamp}")

            # Images
            if ep.thumbnail_url is not None:
                embed.set_thumbnail(url=ep.thumbnail_url)
                flogger.trace(f"Set thumbnail_url: {ep.thumbnail_url!r}")
            if ep.image_url is not None:
                embed.set_image(url=ep.image_url)
                flogger.trace(f"Set image_url: {ep.image_url!r}")

            flogger.debug("payload_to_embed successfully created embed")
            return embed

        except Exception:  # pylint: disable=broad-exception-caught
            flogger.error("Error converting payload to embed")
            raise

    @staticmethod
    def _inject_spacers(fields: list[EmbedField], per_row: int) -> list[EmbedField]:
        """
        After every `per_row` real fields (except the last group) insert
        a zero-width spacer so Discord will wrap exactly at per_row.
        """
        flogger.debug(f"_inject_spacers called: {len(fields)} fields, {per_row}/row")
        out: list[EmbedField] = []
        for idx, f in enumerate(fields):
            out.append(f)
            flogger.trace(f"Added field[{idx}]")
            if (idx + 1) % per_row == 0 and (idx + 1) < len(fields):
                spacer = EmbedField(name="\u200B", value="\u200B", inline=True)
                out.append(spacer)
                flogger.trace(f"Inserted zero-width spacer after field index {idx}")
        flogger.debug(f"_inject_spacers completed: {len(out)} items (added {len(out) - len(fields)} spacers)")
        return out

    @staticmethod
    def payload_to_grid_embed(
        payload: EmbedPayload | dict[str, Any] | Any,
        fields_per_row: int
    ) -> discord.Embed:
        """
        Same as payload_to_embed, but first injects zero-width spacers
        so you get exactly `fields_per_row` inline fields per row.
        """
        # Coerce to EmbedPayload so we can copy/update
        flogger.debug(f"payload_to_grid_embed called: {fields_per_row} per row")
        ep = EmbedConverter._coerce_to_embed_payload(payload)
        flogger.trace(f"Creating grid layout with {len(ep.fields or [])} original fields")
        grid_payload = ep.model_copy(update={
            "fields": EmbedConverter._inject_spacers(ep.fields or [], fields_per_row)
        })
        flogger.trace(f"Grid payload created with {len(grid_payload.fields or [])} total fields (including spacers)")
        return EmbedConverter.payload_to_embed(grid_payload)

    @staticmethod
    def embed_to_payload(embed: discord.Embed) -> EmbedPayload:
        """
        Convert a Discord embed to a JSON payload.

        Args:
            embed: Discord embed object

        Returns:
            EmbedPayload containing embed structure
        """
        flogger.debug("embed_to_payload called")
        flogger.trace(f"Extracting embed structure: title_present={bool(embed.title)}, "
                      f"description_present={bool(embed.description)}, color={embed.color}")
        try:
            title = embed.title if embed.title is not None else None
            description = embed.description if embed.description is not None else None
            flogger.trace("Extracted title and description")
            color = None
            if getattr(embed, "color", None) is not None:
                try:
                    # color may be a discord.Color; try to extract integer value
                    color = getattr(embed.color, "value", None)
                    if color is None:
                        # as fallback, try int()
                        color = int(embed.color)
                    flogger.trace(f"Extracted color: {hex(color) if color else 'None'}")
                except Exception:  # pylint: disable=broad-exception-caught
                    flogger.trace("Failed to extract color value, setting to None")
                    color = None

            fields: list[EmbedField] = []
            try:
                field_count = len(getattr(embed, "fields", []) or [])
                flogger.trace(f"Extracting {field_count} fields from embed")
                for f in getattr(embed, "fields", []) or []:
                    # discord embed fields expose name/value/inline
                    fname = getattr(f, "name", "")
                    fval = getattr(f, "value", "")
                    finline = bool(getattr(f, "inline", False))
                    fields.append(EmbedField(name=fname, value=fval, inline=finline))
                flogger.trace(f"Successfully extracted {len(fields)} fields")
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.error("Failed to iterate embed.fields defensively; setting fields empty")
                fields = []

            footer_text = None
            footer_icon_url = None
            try:
                if getattr(embed, "footer", None) is not None:
                    footer = embed.footer
                    footer_text = getattr(footer, "text", None) or None
                    footer_icon_url = getattr(footer, "icon_url", None) or None
                    flogger.trace(f"Extracted footer: text_present={bool(footer_text)}, "
                                  f"icon_url_present={bool(footer_icon_url)}")
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.trace("Failed to extract footer, setting to None")
                # swallow — treat as no footer
                footer_text = None
                footer_icon_url = None

            timestamp = getattr(embed, "timestamp", None)
            flogger.trace(f"Extracted timestamp: {timestamp}")

            thumbnail_url = None
            try:
                if getattr(embed, "thumbnail", None) is not None:
                    thumbnail_url = getattr(embed.thumbnail, "url", None)
                    flogger.trace(f"Extracted thumbnail_url: {thumbnail_url!r}")
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.trace("Failed to extract thumbnail_url")
                thumbnail_url = None

            image_url = None
            try:
                if getattr(embed, "image", None) is not None:
                    image_url = getattr(embed.image, "url", None)
                    flogger.trace(f"Extracted image_url: {image_url!r}")
            except Exception:  # pylint: disable=broad-exception-caught
                flogger.trace("Failed to extract image_url")
                image_url = None

            payload = EmbedPayload(
                title=title,
                description=description,
                color=color,
                fields=fields,
                footer_text=footer_text,
                footer_icon_url=footer_icon_url,
                timestamp=timestamp,
                thumbnail_url=thumbnail_url,
                image_url=image_url
            )
            flogger.debug("embed_to_payload successfully created payload")
            return payload

        except Exception:  # pylint: disable=broad-exception-caught
            flogger.error("Error converting embed to payload")
            raise

    @staticmethod
    def test_round_trip_consistency(payload: EmbedPayload | dict[str, Any] | Any) -> bool:
        """
        Test that payload -> embed -> payload maintains consistency.

        Args:
            payload: Original EmbedPayload or dict-like

        Returns:
            True if round-trip is consistent, False otherwise
        """
        flogger.debug("test_round_trip_consistency called")
        try:
            flogger.trace("Coercing input payload to EmbedPayload")
            ep = EmbedConverter._coerce_to_embed_payload(payload)
            flogger.trace("Converting EmbedPayload to discord.Embed")
            embed = EmbedConverter.payload_to_embed(ep)
            flogger.trace("Converting discord.Embed back to EmbedPayload")
            result_payload = EmbedConverter.embed_to_payload(embed)
            consistent = ep.model_dump() == result_payload.model_dump()
            flogger.debug(f"Round-trip consistency: {consistent}")
            return consistent
        except Exception as e:  # pylint: disable=broad-exception-caught
            flogger.error(f"Round-trip test failed: {e}")
            return False
