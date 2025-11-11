thonimport json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

logger = logging.getLogger(__name__)

def _ensure_parent_dir(path: Path) -> None:
    if not path.parent.exists():
        logger.debug("Creating parent directory for %s", path)
        path.parent.mkdir(parents=True, exist_ok=True)

def export_to_json(
    articles: Union[Iterable[Dict[str, Any]], List[Dict[str, Any]]],
    output_path: Path,
    pretty: bool = True,
) -> None:
    """
    Write scraped articles to a JSON file.

    If `pretty` is True, writes an indented JSON array. Otherwise, writes a
    compact JSON array to save disk space.
    """
    if not isinstance(articles, list):
        articles = list(articles)

    _ensure_parent_dir(output_path)

    logger.info("Writing %d articles to JSON file %s", len(articles), output_path)
    with output_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(articles, f, indent=2, ensure_ascii=False)
        else:
            json.dump(articles, f, separators=(",", ":"), ensure_ascii=False)