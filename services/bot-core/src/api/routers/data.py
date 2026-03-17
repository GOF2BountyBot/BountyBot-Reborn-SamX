from enum import StrEnum

from fastapi import APIRouter, HTTPException
from utils.data_loader import load_data
from shared import bblogger

flogger = bblogger.get_logger("data-router")


class DataCategory(StrEnum):
    module     = "module"
    primary    = "primary_weapon"
    secondary  = "secondary_weapon"
    turret     = "turret_weapon"
    ship       = "ship"
    criminal   = "criminal"
    system     = "system"

router = APIRouter(prefix="/data", tags=["data"])

@router.post("/{category}", response_model=list[str])
async def api_load_data(category: DataCategory):
    """
    POST /data/{category}
    Triggers an upsert of all JSON files under data/{category}/.
    Only the categories in DataCategory are accepted.
    """
    flogger.info(f"Request received: category={category.value}")
    try:
        results = await load_data(category.value)
        flogger.debug(f"Results: count={len(results)}")
        return results
    except ValueError as e:
        flogger.info(f"ValueError loading data for category={category.value}: {e}")
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        flogger.exception(f"Unexpected error: category={category.value}")
        raise HTTPException(status_code=500, detail="Internal server error") from e

@router.get("/categories", response_model=list[str])
def list_data_categories():
    """
    GET /data/categories
    Returns all valid DataCategory values.
    """
    flogger.info("Request received: list_data_categories")
    categories = [c.value for c in DataCategory]
    flogger.debug(f"Results: count={len(categories)}")
    return categories
