from pydantic import BaseModel, ConfigDict


# Response Models
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    discord_username: str | None
    display_name: str | None = None
    created_at: str
    updated_at: str


class CreateUserRequest(BaseModel):
    id: int  # Discord user ID
    discord_username: str | None = None
    display_name: str | None = None


class UpdateUserRequest(BaseModel):
    discord_username: str | None = None
    display_name: str | None = None
