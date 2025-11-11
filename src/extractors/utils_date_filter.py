thonfrom __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, Iterable, List, Optional

from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

def build_time_range(
    hours: Optional[int] = None,
    days: Optional[int] = None,
    years: Optional[int] = None,
) -> Optional[str]:
    """
    Build a Google News 'when:' time window string like '1h', '7d', or '1y'.

    The smallest non-zero unit wins: hours > days > years.
    Returns None if no constraints are provided.
    """
    if hours is not None and hours > 0:
        return f"{hours}h"
    if days is not None and days > 0:
        return f"{days}d"
    if years is not None and years > 0:
        return f"{years}y"
    return None

def parse_since_from_relative_config(cfg: Dict[str, Any]) -> Optional[dt.datetime]:
    """
    Turn a relative time config into an absolute 'since' datetime in UTC.

    This is used as a second-level filter on top of the 'when:' query, to
    precisely trim results after parsing.
    """
    now = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)
    hours = cfg.get("hours")
    days = cfg.get("days")
    years = cfg.get("years")

    if hours:
        return now - dt.timedelta(hours=int(hours))
    if days:
        return now - dt.timedelta(days=int(days))
    if years:
        return now - dt.timedelta(days=int(years) * 365)
    return None

def filter_articles_by_published_at(
    articles: Iterable[Dict[str, Any]],
    since: dt.datetime,
) -> List[Dict[str, Any]]:
    """
    Filter articles by their 'publishedAt' field, keeping only items with a
    timestamp greater than or equal to 'since'. If parsing fails, the item
    is kept (to avoid accidentally losing data).
    """
    since_utc = since.astimezone(dt.timezone.utc)
    filtered: List[Dict[str, Any]] = []

    for art in articles:
        published_raw = art.get("publishedAt")
        if not published_raw:
            filtered.append(art)
            continue

        try:
            parsed_dt = date_parser.parse(str(published_raw))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=dt.timezone.utc)
            else:
                parsed_dt = parsed_dt.astimezone(dt.timezone.utc)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Failed to parse publishedAt '%s': %s. Keeping article.",
                published_raw,
                exc,
            )
            filtered.append(art)
            continue

        if parsed_dt >= since_utc:
            filtered.append(art)

    return filtered