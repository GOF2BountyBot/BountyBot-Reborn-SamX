from typing import List, Optional
from pydantic import BaseModel

# Response Models
class UserResponse(BaseModel):
    id: int
    discord_username: Optional[str]
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True

class CreateUserRequest(BaseModel):
    id: int  # Discord user ID
    discord_username: Optional[str] = None

class UpdateUserRequest(BaseModel):
    discord_username: Optional[str] = None