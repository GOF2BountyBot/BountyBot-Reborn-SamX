"""
Role router for Discord Gateway API.

This module provides REST endpoints for managing Discord roles
with simplified URIs that don't require guild context.
"""

import discord
from fastapi import APIRouter, HTTPException, Query, Request, status
from shared import bblogger

from api.schemas.base_schemas import DeleteResponse, SuccessResponse
from api.schemas.permission_schemas import PermissionCheckResponse
from api.schemas.role_schemas import RoleResponse, RoleUpdateRequest
from api.schemas.user_schemas import MemberListResponse
from utils.discord_converters import RoleConverter, UserConverter
from utils.discord_helpers import handle_discord_exception, resolve_bot
from utils.permission_utils import PERMISSION_FLAGS, check_permission

flogger = bblogger.get_logger("gateway-role-router")

router = APIRouter(
    tags=["roles"],
    responses={
        404: {"description": "Role not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

@router.get(
    "/roles/{role_id}",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Role Details",
    description="Get detailed information about a specific role"
)
async def get_role(request: Request, role_id: int) -> RoleResponse:
    """Get detailed information about a specific role."""
    flogger.info(f"get_role endpoint called for role_id: {role_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        for guild in bot.guilds:
            role = guild.get_role(role_id)
            if role:
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        role_data = RoleConverter.role_to_payload(role)
        flogger.info(f"Successfully retrieved role details for {role.name}")

        return RoleResponse(
            status="success",
            data=role_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in get_role for role {role_id}: {exc}")
        await handle_discord_exception("get role details", exc)

@router.put(
    "/roles/{role_id}",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Role",
    description="Update a role's properties"
)
async def update_role(
    request: Request, role_id: int, role_data: RoleUpdateRequest
) -> RoleResponse:
    """Update a role's properties."""
    flogger.info(f"update_role endpoint called for role_id: {role_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        for guild in bot.guilds:
            role = guild.get_role(role_id)
            if role:
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        # Update role with provided parameters
        update_kwargs = {}
        if role_data.name is not None:
            update_kwargs["name"] = role_data.name
        if role_data.permissions is not None:
            if role_data.permissions < 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid permissions bitmask"
                )
            perms = discord.Permissions(role_data.permissions)
            if perms.value != role_data.permissions:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid permissions bitmask"
                )
            update_kwargs["permissions"] = perms
        if role_data.color is not None:
            update_kwargs["color"] = discord.Color(role_data.color)
        if role_data.hoist is not None:
            update_kwargs["hoist"] = role_data.hoist
        if role_data.position is not None:
            update_kwargs["position"] = role_data.position
        if role_data.mentionable is not None:
            update_kwargs["mentionable"] = role_data.mentionable

        if update_kwargs:
            await role.edit(**update_kwargs)

        updated_role_data = RoleConverter.role_to_payload(role)
        flogger.info(f"Successfully updated role {role.name}")

        return RoleResponse(
            status="updated",
            data=updated_role_data
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in update_role for role {role_id}: {exc}")
        await handle_discord_exception("update role", exc)

@router.delete(
    "/roles/{role_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Role",
    description="Delete a role from its guild"
)
async def delete_role(request: Request, role_id: int) -> DeleteResponse:
    """Delete a role from its guild."""
    flogger.info(f"delete_role endpoint called for role_id: {role_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        for guild in bot.guilds:
            role = guild.get_role(role_id)
            if role:
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        role_name = role.name
        await role.delete()

        message = f"Role {role_name} deleted"
        flogger.info(message)

        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in delete_role for role {role_id}: {exc}")
        await handle_discord_exception("delete role", exc)

@router.get(
    "/roles/{role_id}/members",
    response_model=MemberListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Role Members",
    description="Get a list of users who have this role"
)
async def list_role_members(request: Request, role_id: int) -> MemberListResponse:
    """List all users who have a specific role."""
    flogger.info(f"list_role_members endpoint called for role_id: {role_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        for guild in bot.guilds:
            role = guild.get_role(role_id)
            if role:
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        members = []
        for member in role.members:
            member_data = UserConverter.member_to_payload(member)
            members.append(member_data)

        flogger.info(f"Successfully retrieved {len(members)} members with role {role.name}")
        return MemberListResponse(
            status="success",
            data=members
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in list_role_members for role {role_id}: {exc}")
        await handle_discord_exception("list role members", exc)

@router.put(
    "/roles/{role_id}/members/{user_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Role to User",
    description="Assign a role to a user"
)
async def assign_role_to_user(
    request: Request, role_id: int, user_id: int
) -> SuccessResponse:
    """Assign a role to a user."""
    flogger.info(f"assign_role_to_user endpoint called for role_id: {role_id}, user_id: {user_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        guild = None
        for g in bot.guilds:
            role = g.get_role(role_id)
            if role:
                guild = g
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        # Get the member
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound as exc:
                flogger.error(f"Member {user_id} not found in guild {guild.id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild.id}"
                ) from exc

        await member.add_roles(role)

        message = f"Role {role.name} assigned to {member.display_name}"
        flogger.info(message)
        return SuccessResponse(
            status="assigned",
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in assign_role_to_user: {exc}")
        await handle_discord_exception("assign role to user", exc)

@router.delete(
    "/roles/{role_id}/members/{user_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Role from User",
    description="Remove a role from a user"
)
async def remove_role_from_user(
    request: Request, role_id: int, user_id: int
) -> SuccessResponse:
    """Remove a role from a user."""
    flogger.info(f"remove_role_from_user endpoint called for role_id: {role_id}, user_id: {user_id}")
    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        guild = None
        for g in bot.guilds:
            role = g.get_role(role_id)
            if role:
                guild = g
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        # Get the member
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound as exc:
                flogger.error(f"Member {user_id} not found in guild {guild.id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild.id}"
                ) from exc

        await member.remove_roles(role)

        message = f"Role {role.name} removed from {member.display_name}"
        flogger.info(message)
        return SuccessResponse(
            status="removed",
            message=message
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in remove_role_from_user: {exc}")
        await handle_discord_exception("remove role from user", exc)

@router.get(
    "/roles/{role_id}/permissions/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check Role Guild Permission",
    description="Check whether a role (by id) has a specific guild-level permission. Superceded by /permissions/check.",
    deprecated=True
)
async def check_role_permission(
    request: Request,
    role_id: int,
    permission: str = Query(..., description="Permission name (uppercase, e.g. MANAGE_GUILD)")
) -> PermissionCheckResponse:
    """Check whether a role has the named guild-level permission."""
    flogger.info(f"check_role_permission endpoint called for role_id={role_id}, permission={permission}")
    # Validate permission name
    if permission not in PERMISSION_FLAGS:
        flogger.error(f"check_role_permission: unknown permission '{permission}'")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown permission: {permission}"
        )

    try:
        bot = await resolve_bot(request)

        # Search for the role across all guilds
        role = None
        for guild in bot.guilds:
            role = guild.get_role(role_id)
            if role:
                break

        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )

        # Evaluate permission from role permissions bitfield
        perms_value = getattr(role.permissions, "value", int(role.permissions))
        allowed = check_permission(perms_value, permission)

        flogger.info(f"Permission '{permission}' for role '{role.name}' ({role_id}): {allowed}")
        return PermissionCheckResponse(
            status="success",
            data={"allowed": allowed}
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in check_role_permission for role {role_id}: {exc}")
        await handle_discord_exception("check role permission", exc)

@router.get(
    "/roles/{role_id}/members/{user_id}/check",
    response_model=PermissionCheckResponse,
    status_code=status.HTTP_200_OK,
    summary="Check User Role Membership",
    description="Check whether a given user has a specific role (by role id)"
)
async def check_user_has_role(request: Request, role_id: int, user_id: int) -> PermissionCheckResponse:
    """Check whether a user has the specified role."""
    flogger.info(f"check_user_has_role called for role_id={role_id}, user_id={user_id}")
    try:
        bot = await resolve_bot(request)
        # Find role and its guild
        role = None
        guild = None
        for g in bot.guilds:
            r = g.get_role(role_id)
            if r:
                role = r
                guild = g
                break
        if not role:
            flogger.error(f"Role {role_id} not found")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found"
            )
        # Get member
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound as exc:
                flogger.error(f"Member {user_id} not found in guild {guild.id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild.id}"
                ) from exc
        has_role = any(r.id == role_id for r in member.roles)
        flogger.info(f"User {user_id} has role {role_id}: {has_role}")
        return PermissionCheckResponse(
            status="success",
            data={"allowed": has_role}
        )
    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        flogger.error(f"Unexpected error in check_user_has_role for role {role_id}, user {user_id}: {exc}")
        await handle_discord_exception("check user role membership", exc)
