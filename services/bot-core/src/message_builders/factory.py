"""
Factory for creating message payload builders.

This factory pattern allows for easy registration and creation
of message-specific payload builders.
"""

from typing import Dict, Type, List
from .base import MessagePayloadBuilder
from .builders.time_announcement import TimeAnnouncementBuilder

class MessageBuilderFactory:
    """Factory for creating message payload builders."""
    
    _builders: Dict[str, Type[MessagePayloadBuilder]] = {
        "time_announcement": TimeAnnouncementBuilder,
        # Add more builders as they're created
    }
    
    @classmethod
    def create_builder(cls, message_type: str) -> MessagePayloadBuilder:
        """Create a builder instance for the specified message type."""
        if message_type not in cls._builders:
            raise ValueError(f"Unknown message type: {message_type}")
        
        return cls._builders[message_type]()
    
    @classmethod
    def register_builder(cls, message_type: str, builder_class: Type[MessagePayloadBuilder]):
        """Register a new builder type."""
        cls._builders[message_type] = builder_class
    
    @classmethod
    def get_supported_types(cls) -> List[str]:
        """Get list of supported message types."""
        return list(cls._builders.keys())
