import json
from pathlib import Path
from importlib import import_module

from persist.database.manager import db_manager
from utils.emoji_service import EmojiService

import shared.logging as logging
logger = logging.get_logger("bot-data-loader")

# Global emoji service instance
_emoji_service: EmojiService | None = None

def get_emoji_service() -> EmojiService:
    """Get or create the global emoji service instance."""
    global _emoji_service
    if _emoji_service is None:
        _emoji_service = EmojiService()
    return _emoji_service

def get_repository(category: str):
    """
    Dynamically import e.g. persist.repositories.modules_repository.ModuleRepository
    Expects:
    - file: persist/repositories/{category}_repository.py
    - class:   {SingularCategoryTitle}Repository
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

async def load_folder(repo, data_dir: Path) -> None:
    """Async load all JSON files under a given folder into the DB."""
    logger.debug(f"Loading data from folder: {data_dir}")

    for json_path in data_dir.rglob("*.json"):
        try:
            payload = json.loads(json_path.read_text())
            logger.trace(f" Loading {json_path} with payload {payload}")
        except json.JSONDecodeError as e:
            logger.warning(f" ⏭ Skipping invalid JSON {json_path}: {e}")
            continue

        async with db_manager.get_session() as db:
            obj = await repo.create_or_update(db, payload)
            logger.debug(f" ✓ Upserted {obj!r}")

def _resolve_emojis(obj):
    """
    Only resolve the top-level 'emoji' field, using obj['name'] as the lookup key.
    Leaves 'name' (and all other fields) untouched.
    """
    service = get_emoji_service()
    if isinstance(obj, dict) and 'name' in obj:
        new_emoji = service.resolve_emoji(obj['name'])
        if new_emoji:
            obj['emoji'] = new_emoji
    return obj

async def load_data(category: str, data_root: str | Path = None) -> list[str]:
    """
    Upsert all JSON files under data/{category}/ into the DB.
    Returns a list of status messages (one per file).
    """
    logger.info(f"load_data called for category='{category}'")

    # Pre-load emojis for module category
    try:
        logger.info("Pre-loading Discord application emojis...")
        emoji_service = get_emoji_service()
        emoji_service.load_emojis()
        logger.info("✓ Emojis pre-loaded successfully")
    except Exception as e:
        logger.error(f"Failed to pre-load emojis: {e}")

    # Determine root/data dir (one level above "src")
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
        try:
            payload = json.loads(json_path.read_text())
        except json.JSONDecodeError as e:
            warn = f"Skipping invalid JSON {json_path.name}: {e}"
            logger.warning(warn)
            results.append(warn)
            continue

        # Attempt emoji-resolution on the loaded payload
        try:
            payload = _resolve_emojis(payload)
        except Exception as e:
            logger.warning(f"Failed to resolve emojis in {json_path.name}: {e}")

        async with db_manager.get_session() as db:
            try:
                obj = await repo.create_or_update(db, payload)
                msg = f"Upserted {obj!r} from {json_path.name}"
                logger.debug(msg)
                results.append(msg)
            except Exception as e:
                err = f"Error upserting {json_path.name}: {e}"
                logger.exception(err)
                results.append(err)

    logger.info(f"Completed load_data for '{category}', processed {len(results)} items")
    return results