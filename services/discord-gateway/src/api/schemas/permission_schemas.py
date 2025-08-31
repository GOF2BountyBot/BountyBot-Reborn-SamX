"""
Permission-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord permission operations
including permission overwrites and permission reference data.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator
from api.schemas.base_schemas import BaseResponse, PaginatedResponse, BaseUpdateRequest

class PermissionOverwrite(BaseModel):
    """Permission overwrite information model."""
    id: str = Field(..., description="Permission overwrite ID (channel_id:target_id)")
    channel_id: int = Field(..., description="Channel ID")
    target_id: int = Field(..., description="Target ID (role or user)")
    type: str = Field(..., description="Target type (role or member)")
    allow: int = Field(0, ge=0, description="Allowed permissions (bitfield, ≥0)")
    deny: int = Field(0, ge=0, description="Denied permissions (bitfield, ≥0)")

    @field_validator('allow', 'deny')
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError('bitfield must be non-negative')
        return v

    @model_validator(mode='after')
    def no_conflicting_bits(self):
        if self.allow & self.deny:
            raise ValueError('same bit(s) cannot be both allowed and denied')
        return self

class PermissionFlag(BaseModel):
    """Permission flag information model."""
    name: str = Field(..., description="Permission name")
    value: int = Field(..., description="Permission bit value")
    description: str = Field(..., description="Permission description")
    channel_types: List[str] = Field(default_factory=list, description="Applicable channel types")

class PermissionOverwriteResponse(BaseResponse):
    """Response model for single permission overwrite endpoint."""
    data: PermissionOverwrite = Field(..., description="Permission overwrite data")

class PermissionOverwriteListResponse(PaginatedResponse):
    """Response model for permission overwrite list endpoint."""
    data: List[PermissionOverwrite] = Field(..., description="List of permission overwrites")

class PermissionFlagResponse(BaseResponse):
    """Response model for single permission flag endpoint."""
    data: PermissionFlag = Field(..., description="Permission flag data")

class PermissionFlagListResponse(PaginatedResponse):
    """Response model for permission flag list endpoint."""
    data: List[PermissionFlag] = Field(..., description="List of permission flags")

class PermissionOverwriteRequest(BaseUpdateRequest):
    """Request model for setting permission overwrites."""
    allow: Optional[int] = Field(None, ge=0, description="Permissions to allow (bitfield)")
    deny: Optional[int] = Field(None, ge=0, description="Permissions to deny (bitfield)")

class PermissionOverwriteListRequest(BaseModel):
    """Request model for setting multiple permission overwrites."""
    overwrites: List[PermissionOverwrite] = Field(..., description="List of overwrites to set")

class PermissionCheckResponse(BaseResponse):
    """Response model for a single permission check."""
    data: Dict[str, bool] = Field(..., description="Permission check result")

class NamesToValueRequest(BaseModel):
    """Convert a list of permission names to a bitfield."""
    names: List[str] = Field(..., description="List of permission names (uppercase)")

class NamesToValueResponse(BaseResponse):
    """Response for converting permission names to bitfield."""
    data: Dict[str, int] = Field(..., description="Combined bitfield value")

class ValueToNamesRequest(BaseModel):
    """Convert a bitfield to permission names."""
    value: int = Field(..., ge=0, description="Permission bitfield")

class ValueToNamesResponse(BaseResponse):
    """Response for converting bitfield to permission names."""
    data: Dict[str, List[str]] = Field(..., description="List of granted permission names")

class CalculatePermissionsRequest(BaseModel):
    """Calculate effective permissions from base, allow, and deny bitfields."""
    base: int = Field(..., ge=0, description="Base permissions bitfield")
    allow: Optional[int] = Field(None, ge=0, description="Allow overwrite bitfield")
    deny: Optional[int] = Field(None, ge=0, description="Deny overwrite bitfield")

class CalculatePermissionsResponse(BaseResponse):
    """Response for calculating effective permissions."""
    data: Dict[str, int] = Field(..., description="Effective permissions bitfield")

class PermissionCheckTarget(BaseModel):
    """Target to check permissions for."""
    type: str = Field(..., description="Target type: 'member', 'role', or 'bot'")
    id: Optional[int] = Field(None, description="Target ID (user id or role id). Not required for 'bot' when scope is guild-wide and bot identity is implied")

class PermissionScope(BaseModel):
    """Scope where permissions are evaluated."""
    type: str = Field(..., description="Scope type: 'guild', 'channel', 'category', or 'thread'")
    id: Optional[int] = Field(None, description="ID of the scope (guild_id, channel_id, category_id, or thread_id). Required for non-guild scopes")

class PermissionCheckRequest(BaseModel):
    """
    Request model for checking multiple permissions for a target within a scope.

    - permissions: list of permission names (uppercase, e.g. SEND_MESSAGES)
    - target: who to evaluate (member, role, or bot)
    - scope: where to evaluate (guild, channel, category, thread)
    """
    permissions: List[str] = Field(..., description="List of permission names (uppercase) to check")
    target: PermissionCheckTarget = Field(..., description="Target to evaluate")
    scope: PermissionScope = Field(..., description="Scope in which to evaluate permissions")

class PermissionCheckResult(BaseModel):
    """Result for a single permission check."""
    permission: str = Field(..., description="Permission name")
    bit: int = Field(..., description="Permission bit value")
    allowed: bool = Field(..., description="Whether the permission is effectively allowed for the target in the scope")

class MultiPermissionCheckData(BaseModel):
    """Container for multi-permission check results."""
    results: List[PermissionCheckResult] = Field(..., description="List of permission check results")
    allowed_all: bool = Field(..., description="True if all requested permissions are allowed")

class MultiPermissionCheckResponse(BaseModel):
    """Response model for multi-permission checks."""
    status: str = Field(..., description="Response status")
    data: MultiPermissionCheckData = Field(..., description="Check results")

class CanDoResponseData(BaseModel):
    """Response data for semantic can-do endpoints (single action)."""
    allowed: bool = Field(..., description="Whether the action is allowed")
    breakdown: Optional[List[PermissionCheckResult]] = Field(
        None, description="Optional breakdown of underlying permission checks that determined the result"
    )

class CanDoResponse(BaseModel):
    """Response for semantic action endpoints."""
    status: str = Field(..., description="Response status")
    data: CanDoResponseData = Field(..., description="Action check result")

class BotPermissionSummaryData(BaseModel):
    """Summary of bot permissions for a given scope."""
    base: int = Field(..., description="Raw permissions bitfield for the bot in the requested scope (guild or channel)")
    allowed_names: List[str] = Field(..., description="List of permission names that are allowed")
    denied_names: List[str] = Field(..., description="List of permission names that are denied")

class BotPermissionSummaryResponse(BaseModel):
    """Response for bot permissions summary endpoint."""
    status: str = Field(..., description="Response status")
    data: BotPermissionSummaryData = Field(..., description="Bot permission summary")