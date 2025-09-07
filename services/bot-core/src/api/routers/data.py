from enum import Enum
from fastapi import APIRouter, HTTPException
from typing import List

from utils.data_loader import load_data

class DataCategory(str, Enum):
    module     = "module"
    primary    = "primary_weapon"
    secondary  = "secondary_weapon"
    turret     = "turret_weapon"
    ship       = "ship"
    criminal   = "criminal"
    system     = "system"

router = APIRouter(prefix="/data", tags=["data"])

@router.post("/{category}", response_model=List[str])
async def api_load_data(category: DataCategory):
    """
    POST /data/{category}
    Triggers an upsert of all JSON files under data/{category}/.
    Only the categories in DataCategory are accepted.
    """
    try:
        return await load_data(category.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/categories", response_model=List[str])
def list_data_categories():
    """
    GET /data/categories
    Returns all valid DataCategory values.
    """
    return [c.value for c in DataCategory]