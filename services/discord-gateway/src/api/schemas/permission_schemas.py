"""
Permission-specific Pydantic schemas for Discord Gateway API.

This module defines request/response models for Discord permission operations
including permission overwrites and permission reference data.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, validator, field_validator, model_validator
from api.schemas.base_schemas import BaseListResponse, BaseDetailResponse, BaseUpdateRequest

class PermissionOverwrite(BaseModel):
    """Permission overwrite information model."""
    target_id: int = Field(..., description="Target ID (role or user)")
    type:       str = Field(..., description="Target type (role or member)")
    allow:      int = Field(0, ge=0, description="Allowed permissions (bitfield, ≥0)")
    deny:       int = Field(0, ge=0, description="Denied permissions (bitfield, ≥0)")

    @field_validator('allow', 'deny')
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError('bitfield must be non-negative')
        return v

    @model_validator(mode='after')
    def no_conflicting_bits(self):
        # At this point `allow` and `deny` are both validated integers.
        if self.allow & self.deny:
            raise ValueError('same bit(s) cannot be both allowed and denied')
        return self

class PermissionFlag(BaseModel):
    name: str = Field(..., description="Permission name")
    value: int = Field(..., description="Permission bit value")
    description: str = Field(..., description="Permission description")
    channel_types: List[str] = Field(default_factory=list, description="Applicable channel types")

class PermissionOverwriteListResponse(BaseListResponse):
    overwrites: List[PermissionOverwrite] = Field(..., description="List of permission overwrites")

class PermissionOverwriteDetailResponse(BaseDetailResponse):
    overwrite: PermissionOverwrite = Field(..., description="Permission overwrite details")

class PermissionFlagListResponse(BaseListResponse):
    permissions: List[PermissionFlag] = Field(..., description="List of permission flags")

class PermissionOverwriteRequest(BaseUpdateRequest):
    allow: Optional[int] = Field(None, ge=0, description="Permissions to allow (bitfield)")
    deny: Optional[int] = Field(None, ge=0, description="Permissions to deny (bitfield)")

class PermissionOverwriteListRequest(BaseModel):
    """Request model for setting multiple permission overwrites."""
    overwrites: List[PermissionOverwrite] = Field(..., description="List of overwrites to set")

class PermissionCheckResponse(BaseModel):
    """Response model for a single permission check."""
    allowed: bool = Field(..., description="Whether the permission is granted")

class NamesToValueRequest(BaseModel):
    """Convert a list of permission names to a bitfield."""
    names: List[str] = Field(..., description="List of permission names (uppercase)")

class NamesToValueResponse(BaseModel):
    value: int = Field(..., description="Combined bitfield value")

class ValueToNamesRequest(BaseModel):
    """Convert a bitfield to permission names."""
    value: int = Field(..., ge=0, description="Permission bitfield")

class ValueToNamesResponse(BaseModel):
    names: List[str] = Field(..., description="List of granted permission names")

class CalculatePermissionsRequest(BaseModel):
    """Calculate effective permissions from base, allow, and deny bitfields."""
    base: int = Field(..., ge=0, description="Base permissions bitfield")
    allow: Optional[int] = Field(None, ge=0, description="Allow overwrite bitfield")
    deny: Optional[int] = Field(None, ge=0, description="Deny overwrite bitfield")

class CalculatePermissionsResponse(BaseModel):
    effective: int = Field(..., description="Effective permissions bitfield")