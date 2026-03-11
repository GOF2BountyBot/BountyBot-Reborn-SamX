"""
Factory for creating message payload builders.

This factory pattern allows for easy registration and creation
of message-specific payload builders.
"""

from typing import Dict, List, Type

from shared import bblogger

from message_builders.base import MessagePayloadBuilder
from message_builders.builders.time_announcement import TimeAnnouncementBuilder

logger = bblogger.get_logger("message-builder-factory")

class MessageBuilderFactory:
    """Factory for creating message payload builders."""

    _builders: Dict[str, Type[MessagePayloadBuilder]] = {
        "time_announcement": TimeAnnouncementBuilder,
        # Add more builders as they're created
    }

    @classmethod
    def create_builder(cls, message_type: str) -> MessagePayloadBuilder:
        """Create a builder instance for the specified message type."""
        logger.debug(f"create_builder called with message_type={message_type}")
        if message_type not in cls._builders:
            logger.error(f"Unknown message type requested: {message_type}")
            raise ValueError(f"Unknown message type: {message_type}")

        builder = cls._builders[message_type]()
        logger.info(f"Instantiated builder '{builder.__class__.__name__}' for type '{message_type}'")
        return builder

    @classmethod
    def register_builder(cls, message_type: str, builder_class: Type[MessagePayloadBuilder]):
        """Register a new builder type."""
        logger.info(f"Registering new builder '{builder_class.__name__}' under message_type='{message_type}'")
        cls._builders[message_type] = builder_class

    @classmethod
    def get_supported_types(cls) -> List[str]:
        """Get list of supported message types."""
        types = list(cls._builders.keys())
        logger.debug(f"Supported message types: {types}")
        return types
