"""
Embed conversion utilities for Discord Gateway service.

This module provides bidirectional conversion between JSON payloads
and Discord embeds, ensuring 100% consistency and round-trip accuracy.
The converter is completely generic and contains no business logic.
"""

from typing import Dict, Any, Optional, List
import discord
from datetime import datetime
from api.schemas.message_schemas import EmbedPayload, EmbedField
import shared.bblogger as bblogger

flogger = bblogger.get_logger("discord-embed-converter")


class EmbedConverter:
    """
    Utility class for bidirectional conversion between payloads and Discord embeds.
    
    This converter is completely generic and maintains 100% consistency:
    payload -> embed -> payload should return identical data.
    """

    @staticmethod
    def payload_to_embed(payload: EmbedPayload) -> discord.Embed:
        """
        Convert a JSON payload to a Discord embed.
        
        Args:
            payload: EmbedPayload containing embed structure
            
        Returns:
            discord.Embed object
        """
        flogger.debug(f"payload_to_embed called with payload: {payload.dict()}")
        try:
            embed = discord.Embed()
            if payload.title is not None:
                embed.title = payload.title
                flogger.debug(f"  set title: {payload.title!r}")
            if payload.description is not None:
                embed.description = payload.description
                flogger.debug(f"  set description: {payload.description!r}")
            if payload.color is not None:
                embed.color = discord.Color(payload.color)
                flogger.debug(f"  set color: {hex(payload.color)}")

            for idx, field in enumerate(payload.fields):
                embed.add_field(name=field.name, value=field.value, inline=field.inline)
                flogger.debug(f"  added field[{idx}]: name={field.name!r}, inline={field.inline}")

            if payload.footer_text is not None:
                embed.set_footer(text=payload.footer_text, icon_url=payload.footer_icon_url)
                flogger.debug(f"  set footer: text={payload.footer_text!r}, icon_url={payload.footer_icon_url!r}")

            if payload.timestamp is not None:
                embed.timestamp = payload.timestamp
                flogger.debug(f"  set timestamp: {payload.timestamp}")

            if payload.thumbnail_url is not None:
                embed.set_thumbnail(url=payload.thumbnail_url)
                flogger.debug(f"  set thumbnail_url: {payload.thumbnail_url!r}")
            if payload.image_url is not None:
                embed.set_image(url=payload.image_url)
                flogger.debug(f"  set image_url: {payload.image_url!r}")

            flogger.info("payload_to_embed successfully created embed")
            return embed

        except Exception as exc:
            flogger.exception("Error converting payload to embed")
            raise

    @staticmethod
    def _inject_spacers(fields: List[EmbedField], per_row: int) -> List[EmbedField]:
        """
        After every `per_row` real fields (except the last group) insert
        a zero-width spacer so Discord will wrap exactly at per_row.
        """
        flogger.debug(f"_inject_spacers called: {len(fields)} fields, {per_row}/row")
        out: List[EmbedField] = []
        for idx, f in enumerate(fields):
            out.append(f)
            if (idx + 1) % per_row == 0 and (idx + 1) < len(fields):
                spacer = EmbedField(name="\u200B", value="\u200B", inline=True)
                out.append(spacer)
                flogger.debug(f"  inserted spacer after index {idx}")
        return out

    @staticmethod
    def payload_to_grid_embed(
        payload: EmbedPayload,
        fields_per_row: int
    ) -> discord.Embed:
        """
        Same as payload_to_embed, but first injects zero-width spacers
        so you get exactly `fields_per_row` inline fields per row.
        """
        flogger.debug(f"payload_to_grid_embed called: {fields_per_row} per row")
        grid_payload = payload.copy(update={
            "fields": EmbedConverter._inject_spacers(payload.fields, fields_per_row)
        })
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
        try:
            title = embed.title if embed.title is not None else None
            description = embed.description if embed.description is not None else None
            color = embed.color.value if embed.color and embed.color is not None else None

            fields = [
                EmbedField(name=f.name, value=f.value, inline=f.inline)
                for f in embed.fields
            ]
            flogger.debug(f"  extracted {len(fields)} fields")

            footer_text = None
            footer_icon_url = None
            if embed.footer and embed.footer != discord.Embed.Empty:
                footer_text = embed.footer.text or None
                footer_icon_url = embed.footer.icon_url or None
                flogger.debug(f"  extracted footer: text={footer_text!r}, icon_url={footer_icon_url!r}")

            timestamp = embed.timestamp

            thumbnail_url = embed.thumbnail.url if embed.thumbnail and embed.thumbnail is not None else None
            image_url = embed.image.url if embed.image and embed.image is not None else None

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
            flogger.info("embed_to_payload successfully created payload")
            return payload

        except Exception as exc:
            flogger.exception("Error converting embed to payload")
            raise

    @staticmethod
    def test_round_trip_consistency(payload: EmbedPayload) -> bool:
        """
        Test that payload -> embed -> payload maintains consistency.
        
        Args:
            payload: Original EmbedPayload
            
        Returns:
            True if round-trip is consistent, False otherwise
        """
        flogger.debug("test_round_trip_consistency called")
        try:
            embed = EmbedConverter.payload_to_embed(payload)
            result_payload = EmbedConverter.embed_to_payload(embed)
            consistent = payload.dict() == result_payload.dict()
            flogger.info(f"Round-trip consistency: {consistent}")
            return consistent
        except Exception as e:
            flogger.error(f"Round-trip test failed: {e}")
            return False