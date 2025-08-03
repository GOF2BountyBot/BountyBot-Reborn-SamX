"""
Role router for Discord Gateway API.

This module provides REST endpoints for managing Discord roles
including listing, creating, updating, and deleting roles and managing role members.
"""

from fastapi import APIRouter, HTTPException, Request, status
import discord

import shared.bblogger as bblogger
from api.schemas.role_schemas import (
    RoleListResponse, RoleDetailResponse, RoleCreateRequest,
    RoleUpdateRequest, RoleMemberListResponse
)
from api.schemas.base_schemas import SuccessResponse, DeleteResponse
from utils.discord_converters import RoleConverter, UserConverter
from utils.discord_helpers import resolve_bot, get_entity_or_404, handle_discord_exception

flogger = bblogger.get_logger("gateway-role-router")

router = APIRouter(
    prefix="/guilds/{guild_id}/roles",
    tags=["roles"],
    responses={
        404: {"description": "Guild, role, or user not found"},
        403: {"description": "Insufficient permissions"},
        500: {"description": "Internal server error"},
        503: {"description": "Service unavailable - bot not ready"}
    }
)

# GET endpoints (ordered: List, Get Details, Get Extra Info)

@router.get(
    "",
    response_model=RoleListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Guild Roles",
    description="Get a list of all roles in a guild"
)
async def list_guild_roles(request: Request, guild_id: int) -> RoleListResponse:
    """
    List all roles in a guild.
    
    Args:
        guild_id: The ID of the guild to get roles from
        
    Returns:
        List of roles in the guild
    """
    flogger.info(f"list_guild_roles endpoint called for guild_id: {guild_id}")
    flogger.debug(f"Starting role list retrieval for guild {guild_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}, found {len(guild.roles)} roles")
        
        roles = []
        for role in guild.roles:
            flogger.trace(f"Processing role: {role.name} ({role.id})")
            role_payload = RoleConverter.role_to_payload(role)
            roles.append(role_payload)
        
        flogger.info(f"Successfully retrieved {len(roles)} roles from guild {guild.name}")
        return RoleListResponse(
            status="success",
            roles=roles
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_guild_roles for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_guild_roles for guild {guild_id}: {exc}")
        await handle_discord_exception("list guild roles", exc)

@router.get(
    "/{role_id}",
    response_model=RoleDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Role Details",
    description="Get detailed information about a specific role"
)
async def get_role(request: Request, guild_id: int, role_id: int) -> RoleDetailResponse:
    """
    Get detailed information about a specific role.
    
    Args:
        guild_id: The ID of the guild the role belongs to
        role_id: The ID of the role to retrieve
        
    Returns:
        Detailed role information
    """
    flogger.info(f"get_role endpoint called for guild_id: {guild_id}, role_id: {role_id}")
    flogger.debug(f"Starting role detail retrieval for role {role_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        flogger.debug(f"Role retrieved: {role.name}")
        
        role_payload = RoleConverter.role_to_payload(role)
        flogger.trace(f"Role detail conversion completed for {role.name}")
        
        flogger.info(f"Successfully retrieved role details for {role.name}")
        return RoleDetailResponse(
            status="success",
            role=role_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in get_role for role {role_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in get_role for role {role_id}: {exc}")
        await handle_discord_exception("get role details", exc)

@router.get(
    "/{role_id}/members",
    response_model=RoleMemberListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Role Members",
    description="Get a list of users who have this role"
)
async def list_role_members(request: Request, guild_id: int, role_id: int) -> RoleMemberListResponse:
    """
    List all users who have a specific role.
    
    Args:
        guild_id: The ID of the guild the role belongs to
        role_id: The ID of the role to get members for
        
    Returns:
        List of members who have the role
    """
    flogger.info(f"list_role_members endpoint called for guild_id: {guild_id}, role_id: {role_id}")
    flogger.debug(f"Starting role member list retrieval for role {role_id}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        flogger.debug(f"Role retrieved: {role.name}, has {len(role.members)} members")
        
        members = []
        for member in role.members:
            flogger.trace(f"Processing member: {member.display_name} ({member.id})")
            member_payload = UserConverter.member_to_payload(member)
            members.append(member_payload)
        
        flogger.info(f"Successfully retrieved {len(members)} members with role {role.name}")
        return RoleMemberListResponse(
            status="success",
            members=members
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in list_role_members for role {role_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in list_role_members for role {role_id}: {exc}")
        await handle_discord_exception("list role members", exc)

# POST endpoints

@router.post(
    "",
    response_model=RoleDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Role",
    description="Create a new role in a guild"
)
async def create_role(
    request: Request,
    guild_id: int,
    role_data: RoleCreateRequest
) -> RoleDetailResponse:
    """
    Create a new role in a guild.
    
    Args:
        guild_id: The ID of the guild to create the role in
        role_data: Role creation parameters
        
    Returns:
        Details of the created role
    """
    flogger.info(f"create_role endpoint called for guild_id: {guild_id}, name: {role_data.name}")
    flogger.debug(f"Starting role creation with data: {role_data.dict()}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        # Create role with provided parameters
        create_kwargs = {
            "name": role_data.name,
            "hoist": role_data.hoist,
            "mentionable": role_data.mentionable
        }
        
        if role_data.permissions is not None:
            create_kwargs["permissions"] = discord.Permissions(role_data.permissions)
            flogger.trace(f"Will set permissions: {hex(role_data.permissions)}")
        if role_data.color is not None:
            create_kwargs["color"] = discord.Color(role_data.color)
            flogger.trace(f"Will set color: {hex(role_data.color)}")
        
        flogger.debug(f"Creating role with kwargs: {create_kwargs}")
        role = await guild.create_role(**create_kwargs)
        flogger.debug(f"Role created: {role.name} (ID: {role.id})")
        
        role_payload = RoleConverter.role_to_payload(role)
        flogger.trace("Role detail conversion completed")
        
        flogger.info(f"Successfully created role {role.name} (ID: {role.id})")
        return RoleDetailResponse(
            status="created",
            role=role_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in create_role for guild {guild_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in create_role for guild {guild_id}: {exc}")
        await handle_discord_exception("create role", exc)

# PUT endpoints

@router.put(
    "/{role_id}",
    response_model=RoleDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Role",
    description="Update a role's properties (uses PATCH internally)"
)
async def update_role(
    request: Request,
    guild_id: int,
    role_id: int,
    role_data: RoleUpdateRequest
) -> RoleDetailResponse:
    """
    Update a role's properties.
    
    Args:
        guild_id: The ID of the guild the role belongs to
        role_id: The ID of the role to update
        role_data: Role update parameters
        
    Returns:
        Details of the updated role
    """
    flogger.info(f"update_role endpoint called for guild_id: {guild_id}, role_id: {role_id}")
    flogger.debug(f"Starting role update with data: {role_data.dict()}")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        flogger.debug(f"Role retrieved: {role.name}")
        
        # Update role with provided parameters
        update_kwargs = {}
        if role_data.name is not None:
            update_kwargs["name"] = role_data.name
            flogger.trace(f"Will update name to: {role_data.name}")
        if role_data.permissions is not None:
            update_kwargs["permissions"] = discord.Permissions(role_data.permissions)
            flogger.trace(f"Will update permissions to: {hex(role_data.permissions)}")
        if role_data.color is not None:
            update_kwargs["color"] = discord.Color(role_data.color)
            flogger.trace(f"Will update color to: {hex(role_data.color)}")
        if role_data.hoist is not None:
            update_kwargs["hoist"] = role_data.hoist
            flogger.trace(f"Will update hoist to: {role_data.hoist}")
        if role_data.position is not None:
            update_kwargs["position"] = role_data.position
            flogger.trace(f"Will update position to: {role_data.position}")
        if role_data.mentionable is not None:
            update_kwargs["mentionable"] = role_data.mentionable
            flogger.trace(f"Will update mentionable to: {role_data.mentionable}")
        
        if update_kwargs:
            flogger.debug(f"Applying updates: {update_kwargs}")
            await role.edit(**update_kwargs)
        else:
            flogger.debug("No updates to apply")
        
        role_payload = RoleConverter.role_to_payload(role)
        flogger.trace("Role detail conversion completed")
        
        flogger.info(f"Successfully updated role {role.name}")
        return RoleDetailResponse(
            status="updated",
            role=role_payload
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in update_role for role {role_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in update_role for role {role_id}: {exc}")
        await handle_discord_exception("update role", exc)

@router.put(
    "/{role_id}/members/{user_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Role to User",
    description="Assign a role to a user"
)
async def assign_role_to_user(
    request: Request,
    guild_id: int,
    role_id: int,
    user_id: int
) -> SuccessResponse:
    """
    Assign a role to a user.
    
    Args:
        guild_id: The ID of the guild
        role_id: The ID of the role to assign
        user_id: The ID of the user to assign the role to
        
    Returns:
        Success confirmation
    """
    flogger.info(f"assign_role_to_user endpoint called for guild_id: {guild_id}, role_id: {role_id}, user_id: {user_id}")
    flogger.debug(f"Starting role assignment")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        flogger.debug(f"Role retrieved: {role.name}")
        
        member = guild.get_member(user_id)
        if not member:
            flogger.trace(f"Member {user_id} not in cache, fetching")
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                flogger.error(f"Member {user_id} not found in guild {guild_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild_id}"
                )
        
        flogger.debug(f"Member retrieved: {member.display_name}")
        
        # Add role to member
        flogger.debug(f"Adding role {role.name} to member {member.display_name}")
        await member.add_roles(role)
        
        message = f"Role {role.name} assigned to {member.display_name}"
        flogger.info(message)
        return SuccessResponse(
            status="assigned",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in assign_role_to_user")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in assign_role_to_user: {exc}")
        await handle_discord_exception("assign role to user", exc)

# DELETE endpoints

@router.delete(
    "/{role_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Role",
    description="Delete a role from a guild"
)
async def delete_role(request: Request, guild_id: int, role_id: int) -> DeleteResponse:
    """
    Delete a role from a guild.
    
    Args:
        guild_id: The ID of the guild the role belongs to
        role_id: The ID of the role to delete
        
    Returns:
        Deletion confirmation
    """
    flogger.info(f"delete_role endpoint called for guild_id: {guild_id}, role_id: {role_id}")
    flogger.debug(f"Starting role deletion")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        role_name = role.name
        flogger.debug(f"Role retrieved: {role_name}")
        
        # Delete the role
        flogger.debug(f"Deleting role: {role_name}")
        await role.delete()
        
        message = f"Role {role_name} deleted"
        
        flogger.info(message)
        return DeleteResponse(
            status="deleted",
            deleted=True,
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in delete_role for role {role_id}")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in delete_role for role {role_id}: {exc}")
        await handle_discord_exception("delete role", exc)

@router.delete(
    "/{role_id}/members/{user_id}",
    response_model=SuccessResponse,
    status_code=status.HTTP_200_OK,
    summary="Remove Role from User",
    description="Remove a role from a user"
)
async def remove_role_from_user(
    request: Request,
    guild_id: int,
    role_id: int,
    user_id: int
) -> SuccessResponse:
    """
    Remove a role from a user.
    
    Args:
        guild_id: The ID of the guild
        role_id: The ID of the role to remove
        user_id: The ID of the user to remove the role from
        
    Returns:
        Success confirmation
    """
    flogger.info(f"remove_role_from_user endpoint called for guild_id: {guild_id}, role_id: {role_id}, user_id: {user_id}")
    flogger.debug(f"Starting role removal")
    
    try:
        bot = await resolve_bot(request)
        flogger.debug("Bot resolved successfully")
        
        guild = await get_entity_or_404(
            bot.get_guild,
            bot.fetch_guild,
            guild_id,
            "Guild"
        )
        flogger.debug(f"Guild retrieved: {guild.name}")
        
        role = guild.get_role(role_id)
        if not role:
            flogger.error(f"Role {role_id} not found in guild {guild_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role {role_id} not found in guild {guild_id}"
            )
        
        flogger.debug(f"Role retrieved: {role.name}")
        
        member = guild.get_member(user_id)
        if not member:
            flogger.trace(f"Member {user_id} not in cache, fetching")
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                flogger.error(f"Member {user_id} not found in guild {guild_id}")
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Member {user_id} not found in guild {guild_id}"
                )
        
        flogger.debug(f"Member retrieved: {member.display_name}")
        
        # Remove role from member
        flogger.debug(f"Removing role {role.name} from member {member.display_name}")
        await member.remove_roles(role)
        
        message = f"Role {role.name} removed from {member.display_name}"
        flogger.info(message)
        return SuccessResponse(
            status="removed",
            message=message
        )
        
    except HTTPException:
        flogger.warning(f"HTTP exception occurred in remove_role_from_user")
        raise
    except Exception as exc:
        flogger.error(f"Unexpected error in remove_role_from_user: {exc}")
        await handle_discord_exception("remove role from user", exc)
