import os
import re
import unicodedata
import requests
from typing import Dict, Optional
import shared.bblogger as bblogger

flogger = bblogger.get_logger("bot-emoji-service")

class EmojiService:
    def __init__(self):
        self.bot_token = os.getenv('BOTTOKEN')
        self.app_id = os.getenv('BOTAPPID')
        self.emojis_cache: Dict[str, str] = {}
        
        if not self.bot_token:
            raise ValueError("BOTTOKEN environment variable is required")
        if not self.app_id:
            raise ValueError("BOTAPPID environment variable is required")
    
    def normalize_emoji_name(self, object_name: str) -> str:
        """
        Convert object name to emoji name format:
         - lowercase
         - turn any non-alphanumeric into underscores
         - collapse multiple underscores
        Example: "E6 D-X Plating" -> "e6_d_x_plating"
                 "Mass Driver MD 10" -> "mass_driver_md_10"
        """
        s = object_name.lower()
        # → decompose accents (e.g. 'é' → 'e' + '´') and drop the accent parts
        s = unicodedata.normalize('NFD', s)
        s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
        normalized = re.sub(r'[^a-z0-9]', '', s)
        flogger.debug(f"Normalized '{object_name}' to '{normalized}'")
        return normalized
    
    def fetch_application_emojis(self) -> Dict[str, str]:
        """
        Fetch all application emojis from Discord API.
        Returns a dictionary mapping emoji names to their IDs.
        """
        flogger.info("Fetching application emojis from Discord API")
        
        url = f"https://discord.com/api/v10/applications/{self.app_id}/emojis"
        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            emoji_dict = {}
            
            # Handle both direct list and items wrapper
            emojis = data.get('items', data) if isinstance(data, dict) else data
            
            for emoji in emojis:
                name = emoji.get('name')
                emoji_id = emoji.get('id')
                if name and emoji_id:
                    emoji_dict[name.lower()] = emoji_id
                    flogger.trace(f"Loaded emoji: {name} -> {emoji_id}")
            
            flogger.info(f"Successfully loaded {len(emoji_dict)} application emojis")
            return emoji_dict
            
        except requests.exceptions.RequestException as e:
            flogger.error(f"Failed to fetch application emojis: {e}")
            raise RuntimeError(f"Discord API request failed: {e}")
        except Exception as e:
            flogger.error(f"Error processing emoji data: {e}")
            raise RuntimeError(f"Failed to process emoji data: {e}")
    
    def load_emojis(self):
        """Load and cache all application emojis."""
        self.emojis_cache = self.fetch_application_emojis()
        flogger.info(f"Emoji cache loaded with {len(self.emojis_cache)} emojis")
    
    def resolve_emoji(self, object_name: str) -> Optional[str]:
        """
        Resolve an object name to Discord emoji format.
        Returns format: <:emojiname:emojiid> or None if not found.
        """
        flogger.trace(f"Resolving emoji for object name: {object_name}")
        if not self.emojis_cache:
            flogger.warning("Emoji cache is empty, loading emojis first")
            self.load_emojis()
        
        normalized_name = self.normalize_emoji_name(object_name)
        emoji_id = self.emojis_cache.get(normalized_name)
        
        if emoji_id:
            emoji_format = f"<:{normalized_name}:{emoji_id}>"
            flogger.debug(f"Resolved '{object_name}' to '{emoji_format}'")
            return emoji_format
        else:
            flogger.warning(f"No emoji found for '{object_name}' (normalized: '{normalized_name}')")
            return None
    
    def get_available_emojis(self) -> Dict[str, str]:
        """Return a copy of the current emoji cache."""
        return self.emojis_cache.copy()
