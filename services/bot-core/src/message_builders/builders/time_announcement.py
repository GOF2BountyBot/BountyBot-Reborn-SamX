"""
Time announcement message payload builder.

This module contains the business logic for constructing and
parsing time announcement embed payloads.
"""

import json
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from shared import bblogger
from message_builders.base import MessagePayloadBuilder

logger = bblogger.get_logger("time-announcement-builder")


class TimeAnnouncementBuilder(MessagePayloadBuilder):
    """Builder for time announcement messages."""

    def get_message_type(self) -> str:
        logger.debug("get_message_type called")
        return "time_announcement"

    def validate_input(self, data: Dict[str, Any]) -> bool:
        logger.debug(f"validate_input called with data={data}")
        valid = "current_time" in data and isinstance(data["current_time"], str)
        logger.debug(f"validate_input result={valid}")
        return valid

    def build_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug(f"build_payload called with data={data}")
        if not self.validate_input(data):
            logger.error(f"Invalid input data for time announcement: {data}")
            raise ValueError("Invalid input data for time announcement")

        payload = {
            "title": "🕒 Current Time",
            "description": f"**Current time:** {data['current_time']}",
            "color": 0x3498db,
            "footer_text": "Time Announcement",
            "timestamp": datetime.now(UTC).isoformat()
        }
        logger.info(f"build_payload generated payload: {payload}")
        return payload

    def extract_data(self, payload: str) -> Optional[Dict[str, Any]]:
        logger.debug(f"extract_data called with payload={payload}")
        try:
            payload_dict = json.loads(payload)
            description = payload_dict.get("description", "")
            if "**Current time:**" in description:
                time_str = description.split("**Current time:**", 1)[1].strip()
                result = {"current_time": time_str}
                logger.info(f"extract_data extracted data: {result}")
                return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in extract_data: {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(f"Unexpected error in extract_data: {e}")
        logger.debug("extract_data returning None")
        return None
