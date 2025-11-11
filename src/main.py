thonimport argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from extractors.google_news_parser import GoogleNewsScraper
from extractors.utils_date_filter import (
    build_time_range,
    filter_articles_by_published_at,
    parse_since_from_relative_config,
)
from outputs.exporters import export_to_json

logger = logging.getLogger(__name__)

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

def load_settings(config_path: Optional[Path]) -> Dict[str, Any]:
    base_dir = Path(__file__).resolve().parents[1]
    if config_path is None:
        config_path = base_dir / "src" / "config" / "settings.example.json"

    if not config_path.is_file():
        logger.warning("Config file %s not found. Using empty defaults.", config_path)
        return {}

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("Loaded settings from %s", config_path)
        return data
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse config file %s: %s", config_path, exc)
        return {}

def merge_cli_with_settings(args: argparse.Namespace, settings: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    # Query
    result["query"] = args.query or settings.get("query") or "banana"

    # Language / region
    result["language"] = args.language or settings.get("language") or "en"
    result["region"] = args.region or settings.get("region") or "US"

    # Limits
    result["max_items"] = args.max_items or settings.get("max_items") or 50

    # Relative time config: hours / days / years
    time_cfg = settings.get("relative_time", {})
    if args.hours is not None:
        time_cfg["hours"] = args.hours
    if args.days is not None:
        time_cfg["days"] = args.days
    if args.years is not None:
        time_cfg["years"] = args.years
    result["relative_time"] = time_cfg

    # Output configuration
    out_cfg = settings.get("output", {})
    if args.output:
        out_cfg["path"] = args.output
    if "format" not in out_cfg:
        out_cfg["format"] = "json"
    if "pretty" not in out_cfg:
        out_cfg["pretty"] = True
    result["output"] = out_cfg

    # Network configuration
    net_cfg = settings.get("network", {})
    if args.timeout is not None:
        net_cfg["timeout"] = args.timeout
    result["network"] = net_cfg

    # Decode article URLs
    if args.no_decode:
        result["decode_articles"] = False
    else:
        result["decode_articles"] = settings.get("decode_articles", True)

    return result

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape Google News search results into structured JSON."
    )
    parser.add_argument(
        "--query",
        "-q",
        help="Search query (e.g. 'banana when:1h'). Defaults to config or 'banana'.",
    )
    parser.add_argument("--language", "-l", help="Language code for Google News (e.g. 'en').")
    parser.add_argument("--region", "-r", help="Region / country code (e.g. 'US').")
    parser.add_argument(
        "--max-items",
        "-m",
        type=int,
        help="Maximum number of articles to collect (default from config or 50).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        help="Limit news to items from the last N hours (overrides config).",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="Limit news to items from the last N days (overrides config).",
    )
    parser.add_argument(
        "--years",
        type=int,
        help="Limit news to items from the last N years (overrides config).",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Path to settings JSON file. Defaults to src/config/settings.example.json.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output JSON file path. Overrides config output.path.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="HTTP timeout in seconds for Google News requests (overrides config).",
    )
    parser.add_argument(
        "--no-decode",
        action="store_true",
        help="Disable decoding article redirect URLs to the final destination.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    config_path = Path(args.config) if args.config else None
    settings = load_settings(config_path)
    effective = merge_cli_with_settings(args, settings)

    logger.info("Running Google News scraper with query='%s'", effective["query"])

    network_cfg = effective.get("network", {})
    timeout = int(network_cfg.get("timeout", 10))
    proxies = network_cfg.get("proxies") or None
    user_agent = network_cfg.get(
        "user_agent",
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36",
    )

    scraper = GoogleNewsScraper(
        language=effective["language"],
        region=effective["region"],
        timeout=timeout,
        proxies=proxies,
        user_agent=user_agent,
        decode_articles=effective["decode_articles"],
    )

    relative_time_cfg = effective.get("relative_time", {})
    when_param = build_time_range(
        hours=relative_time_cfg.get("hours"),
        days=relative_time_cfg.get("days"),
        years=relative_time_cfg.get("years"),
    )
    since_dt = parse_since_from_relative_config(relative_time_cfg)

    try:
        articles = scraper.search(
            query=effective["query"],
            when=when_param,
            max_items=effective["max_items"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Scraping failed: %s", exc)
        sys.exit(1)

    if since_dt is not None:
        logger.info("Filtering articles published since %s", since_dt.isoformat())
        articles = filter_articles_by_published_at(articles, since_dt)

    logger.info("Collected %d articles", len(articles))

    output_cfg = effective.get("output", {})
    base_dir = Path(__file__).resolve().parents[1]
    output_path_raw = output_cfg.get("path") or "data/sample_output.json"
    output_path = (base_dir / output_path_raw).resolve()
    pretty = bool(output_cfg.get("pretty", True))

    try:
        export_to_json(
            articles,
            output_path=output_path,
            pretty=pretty,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to export results: %s", exc)
        sys.exit(1)

    logger.info("Exported results to %s", output_path)

if __name__ == "__main__":
    main()