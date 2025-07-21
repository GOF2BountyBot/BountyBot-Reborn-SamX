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
        flogger.trace(f"Converting payload to embed")
        
        # Create empty embed - don't set any defaults to maintain consistency
        embed = discord.Embed()
        
        # Set title and description (only if provided)
        if payload.title is not None:
            embed.title = payload.title
        if payload.description is not None:
            embed.description = payload.description
            
        # Set color (only if provided)
        if payload.color is not None:
            embed.color = discord.Color(payload.color)
        
        # Add fields (preserve exact order and properties)
        for field in payload.fields:
            embed.add_field(
                name=field.name,
                value=field.value,
                inline=field.inline
            )
        
        # Set footer (only if text is provided)
        if payload.footer_text is not None:
            embed.set_footer(
                text=payload.footer_text,
                icon_url=payload.footer_icon_url  # Can be None
            )
        
        # Set timestamp (only if provided)
        if payload.timestamp is not None:
            embed.timestamp = payload.timestamp
        
        # Set images (only if provided)
        if payload.thumbnail_url is not None:
            embed.set_thumbnail(url=payload.thumbnail_url)
        if payload.image_url is not None:
            embed.set_image(url=payload.image_url)
        
        flogger.trace(f"Embed created successfully")
        return embed

    @staticmethod
    def _inject_spacers(fields: List[EmbedField], per_row: int) -> List[EmbedField]:
        """
        After every `per_row` real fields (except the last group) insert
        a zero-width spacer so Discord will wrap exactly at per_row.
        """
        out: List[EmbedField] = []
        for idx, f in enumerate(fields):
            out.append(f)
            if (idx + 1) % per_row == 0 and (idx + 1) < len(fields):
                out.append(
                    EmbedField(name="\u200B", value="\u200B", inline=True)
                )
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
        # make a shallow copy so we don’t clobber the original
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
        flogger.trace(f"Converting embed to payload")
        
        # Extract basic properties - handle Empty values properly
        title = embed.title if embed.title != discord.Embed.Empty else None
        description = embed.description if embed.description != discord.Embed.Empty else None
        color = embed.color.value if embed.color and embed.color != discord.Embed.Empty else None
        
        # Extract fields - preserve exact order and properties
        fields = []
        for field in embed.fields:
            fields.append(EmbedField(
                name=field.name,
                value=field.value,
                inline=field.inline
            ))
        
        # Extract footer
        footer_text = None
        footer_icon_url = None
        if embed.footer and embed.footer != discord.Embed.Empty:
            footer_text = embed.footer.text if embed.footer.text != discord.Embed.Empty else None
            footer_icon_url = embed.footer.icon_url if embed.footer.icon_url != discord.Embed.Empty else None
        
        # Extract timestamp
        timestamp = embed.timestamp
        
        # Extract images
        thumbnail_url = None
        if embed.thumbnail and embed.thumbnail != discord.Embed.Empty:
            thumbnail_url = embed.thumbnail.url if embed.thumbnail.url != discord.Embed.Empty else None
            
        image_url = None
        if embed.image and embed.image != discord.Embed.Empty:
            image_url = embed.image.url if embed.image.url != discord.Embed.Empty else None
        
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
        
        flogger.trace(f"Payload created successfully")
        return payload
    
    @staticmethod
    def test_round_trip_consistency(payload: EmbedPayload) -> bool:
        """
        Test that payload -> embed -> payload maintains consistency.
        
        Args:
            payload: Original EmbedPayload
            
        Returns:
            True if round-trip is consistent, False otherwise
        """
        try:
            # Convert to embed and back
            embed = EmbedConverter.payload_to_embed(payload)
            result_payload = EmbedConverter.embed_to_payload(embed)
            
            # Compare the payloads
            return payload.dict() == result_payload.dict()
        except Exception as e:
            flogger.error(f"Round-trip test failed: {e}")
            return False
