"""
Time announcement message payload builder.

This module contains the business logic for constructing and
parsing time announcement embed payloads.
"""

from datetime import datetime
from typing import Dict, Any, Optional
import json
from ..base import MessagePayloadBuilder

class TimeAnnouncementBuilder(MessagePayloadBuilder):
    """Builder for time announcement messages."""
    
    def get_message_type(self) -> str:
        return "time_announcement"
    
    def validate_input(self, data: Dict[str, Any]) -> bool:
        """Validate that we have a current_time field."""
        return "current_time" in data and isinstance(data["current_time"], str)
    
    def build_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Build time announcement embed payload."""
        if not self.validate_input(data):
            raise ValueError("Invalid input data for time announcement")
            
        return {
            "title": "🕒 Current Time",
            "description": f"**Current time:** {data['current_time']}",
            "color": 0x3498db,
            "footer_text": "Time Announcement",
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def extract_data(self, payload: str) -> Optional[Dict[str, Any]]:
        """Extract time from time announcement payload."""
        try:
            payload_dict = json.loads(payload)
            description = payload_dict.get("description", "")
            if "**Current time:**" in description:
                time_str = description.split("**Current time:**")[1].strip()
                return {"current_time": time_str}
        except (json.JSONDecodeError, KeyError):
            pass
        return None
