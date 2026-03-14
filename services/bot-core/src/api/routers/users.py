"""
Users API router for the BountyBot inventory system.

Handles REST API endpoints for user management operations.
All operations are performed via this API by the discord-gateway service.
"""


from fastapi import APIRouter, Depends, HTTPException, status
from persist.database.manager import get_db_session
from persist.repositories.user_repository import UserRepository
from shared import bblogger

from api.schemas.users_schema import CreateUserRequest, UpdateUserRequest, UserResponse

flogger = bblogger.get_logger("users-api-router")

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={
        404: {"description": "User not found"},
        500: {"description": "Internal server error"}
    }
)

# Dependency injection
async def get_user_repository():
    return UserRepository()

@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    request: CreateUserRequest,
    user_repo: UserRepository = Depends(get_user_repository)
):
    """
    Create a new user.

    This endpoint is called when a Discord user first interacts with the bot.
    """
    flogger.info(f"Creating user: {request.id}")

    try:
        async with get_db_session() as db:
            # Check if user already exists
            existing_user = await user_repo.get_by_id(db, request.id)
            if existing_user:
                flogger.warning(f"User {request.id} already exists")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"User with ID {request.id} already exists"
                )

            # Create new user
            user_data = request.model_dump()
            user = await user_repo.create_or_update(db, user_data)

            flogger.info(f"Successfully created user: {user.id}")
            return UserResponse(
                id=user.id,
                discord_username=user.discord_username,
                created_at=user.created_at.isoformat(),
                updated_at=user.updated_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error creating user {request.id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user"
        ) from e

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    user_repo: UserRepository = Depends(get_user_repository)
):
    """
    Get a user by Discord ID.
    """
    flogger.debug(f"Getting user: {user_id}")

    try:
        async with get_db_session() as db:
            user = await user_repo.get_by_id(db, user_id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User {user_id} not found"
                )

            return UserResponse(
                id=user.id,
                discord_username=user.discord_username,
                created_at=user.created_at.isoformat(),
                updated_at=user.updated_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error getting user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get user"
        ) from e

@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    user_repo: UserRepository = Depends(get_user_repository)
):
    """
    Update a user's information.
    """
    flogger.info(f"Updating user: {user_id}")

    try:
        async with get_db_session() as db:
            user = await user_repo.get_by_id(db, user_id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"User {user_id} not found"
                )

            # Update user data
            update_data = request.model_dump(exclude_unset=True)
            update_data["id"] = user_id

            user = await user_repo.create_or_update(db, update_data)

            flogger.info(f"Successfully updated user: {user.id}")
            return UserResponse(
                id=user.id,
                discord_username=user.discord_username,
                created_at=user.created_at.isoformat(),
                updated_at=user.updated_at.isoformat()
            )

    except HTTPException:
        raise
    except Exception as e:
        flogger.error(f"Error updating user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        ) from e

@router.get("/", response_model=list[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    user_repo: UserRepository = Depends(get_user_repository)
):
    """
    List all users with pagination.
    """
    flogger.debug(f"Listing users: skip={skip}, limit={limit}")

    try:
        async with get_db_session() as db:
            users = await user_repo.list_all(db)

            # Apply pagination
            paginated_users = users[skip:skip + limit]

            return [
                UserResponse(
                    id=user.id,
                    discord_username=user.discord_username,
                    created_at=user.created_at.isoformat(),
                    updated_at=user.updated_at.isoformat()
                )
                for user in paginated_users
            ]

    except Exception as e:
        flogger.error(f"Error listing users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list users"
        ) from e

@router.post("/{user_id}/get-or-create", response_model=UserResponse)
async def get_or_create_user(
    user_id: int,
    discord_username: str | None = None,
    user_repo: UserRepository = Depends(get_user_repository)
):
    """
    Get existing user or create new one if doesn't exist.

    This is the primary endpoint used by discord-gateway for user management.
    """
    flogger.debug(f"Get or create user: {user_id}")

    try:
        async with get_db_session() as db:
            user = await user_repo.get_or_create_user(db, user_id, discord_username)

            return UserResponse(
                id=user.id,
                discord_username=user.discord_username,
                created_at=user.created_at.isoformat(),
                updated_at=user.updated_at.isoformat()
            )

    except Exception as e:
        flogger.error(f"Error getting/creating user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get or create user"
        ) from e
