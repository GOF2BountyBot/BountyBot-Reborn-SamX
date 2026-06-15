"""
Abstract base classes for message payload builders.

This module defines the interface that all message-specific builders
must implement to ensure consistency and maintainability.
"""

from abc import ABC, abstractmethod
from typing import Any

from shared import bblogger

flogger = bblogger.get_logger("message-builder-base")


class MessagePayloadBuilder(ABC):
    """Abstract base class for message payload builders."""

    @abstractmethod
    def build_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build the embed payload from input data."""
        flogger.log(5, "build_payload called by subclass")

    def extract_data(self, payload: str) -> dict[str, Any] | None:
        """Extract structured data from stored payload.

        Concrete subclasses may override this when they need to parse back a
        previously-serialised embed payload.  The default returns None so that
        future builder subclasses are not forced to implement this method if
        they have no parsing-back-from-string need.
        """
        flogger.log(5, "extract_data called on base; returning None")

    @abstractmethod
    def get_message_type(self) -> str:
        """Return the message type identifier."""
        flogger.log(5, "get_message_type called by subclass")

    @abstractmethod
    def validate_input(self, data: dict[str, Any]) -> bool:
        """Validate input data for this message type."""
        flogger.log(5, "validate_input called by subclass")
