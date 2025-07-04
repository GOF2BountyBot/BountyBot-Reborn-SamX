import json
from pathlib import Path
from importlib import import_module

from persist.database.manager import get_db_session

import shared.logging as logging
logger = logging.get_logger("bot-data-loader")

def get_repository(category: str):
    """
    Dynamically import e.g. persist.repositories.modules_repository.ModuleRepository
    Expects:
      - file:   persist/repositories/{category}_repository.py
      - class:  {SingularCategoryTitle}Repository
    """
    logger.debug(f"Attempting to load repository for category: {category}")

    repo_module = f"persist.repositories.{category}_repository"
    try:
        mod = import_module(repo_module)
    except ModuleNotFoundError:
        raise RuntimeError(f"No repository module at {repo_module}.py")

    # singularize by dropping trailing 's'
    singular = category[:-1] if category.endswith("s") else category
    class_name = singular.title().replace("_", "") + "Repository"

    try:
        repo_cls = getattr(mod, class_name)
    except AttributeError:
        raise RuntimeError(f"{repo_module}.py does not export class {class_name}")

    return repo_cls()

def load_folder(repo, data_dir: Path):
    logger.debug(f"Loading data from folder: {data_dir}")

    for json_path in data_dir.rglob("*.json"):
        try:
            payload = json.loads(json_path.read_text())
            logger.trace(f"  Loading {json_path}...")
            logger.trace(f"  {payload}")

        except json.JSONDecodeError as e:
            logger.warn(f"  ⏭ Skipping invalid JSON {json_path}: {e}")
            continue

        with get_db_session() as db:
            obj = repo.create_or_update(db, payload)
            db.commit()
            logger.debug(f"  ✓ Upserted {obj!r}")

# public entry‐point for FastAPI
def load_data(category: str, data_root: str | Path = None) -> list[str]:
    """
    Upsert all JSON files under data/{category}/ into the DB.
    Returns a list of status messages (one per file).
    """
    logger.info(f"load_data called for category='{category}'")
    # determine root/data dir (one level above “src”)
    project_root = Path(__file__).parents[2]
    root = Path(data_root) if data_root else project_root / "import_data"
    category_dir = root / category
    logger.debug(f"Looking for data under: {category_dir}")
    if not category_dir.is_dir():
        msg = f"No such data directory: {category_dir}"
        logger.error(msg)
        raise ValueError(msg)

    repo = get_repository(category)
    results: list[str] = []
    for json_path in category_dir.rglob("*.json"):
        # load JSON
        try:
            payload = json.loads(json_path.read_text())
        except json.JSONDecodeError as e:
            warn = f"Skipping invalid JSON {json_path.name}: {e}"
            logger.warning(warn)
            results.append(warn)
            continue

        # upsert into DB
        with get_db_session() as db:
            try:
                obj = repo.create_or_update(db, payload)
                db.commit()
                msg = f"Upserted {obj!r} from {json_path.name}"
                logger.debug(msg)
                results.append(msg)
            except Exception as e:
                err = f"Error upserting {json_path.name}: {e}"
                logger.exception(err)
                results.append(err)

    logger.info(f"Completed load_data for '{category}', processed {len(results)} items")
    return results
