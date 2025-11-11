thonimport json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

GOOGLE_NEWS_BASE = "https://news.google.com"

@dataclass
class GoogleNewsArticle:
    googleNewsUrl: str
    articleUrl: str
    decodedArticleUrl: Optional[str]
    title: str
    publishedAt: Optional[str]
    imageUrl: Optional[str]
    source: Optional[str]
    sourceIconUrl: Optional[str]
    author: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class GoogleNewsScraper:
    """
    High-level interface to search Google News and extract structured items.

    This scraper operates purely on the HTML search results page, without any
    private APIs. It is intentionally conservative in parsing: if a field
    cannot be confidently resolved, it is left as None.
    """

    def __init__(
        self,
        language: str = "en",
        region: str = "US",
        timeout: int = 10,
        proxies: Optional[Dict[str, str]] = None,
        user_agent: Optional[str] = None,
        decode_articles: bool = True,
    ) -> None:
        self.language = language
        self.region = region
        self.timeout = timeout
        self.proxies = proxies
        self.decode_articles = decode_articles

        self.session = requests.Session()
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{language}-{region},{language};q=0.9",
            "User-Agent": user_agent
            or (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
        }
        self.session.headers.update(headers)

    # URL building ---------------------------------------------------------

    def build_search_url(self, query: str, when: Optional[str] = None) -> str:
        """
        Build a Google News search URL.

        `when` is a string like '1h', '7d', or '1y', which is appended as 'when:1h'
        to the query term (the caller is responsible for choosing a suitable value).
        """
        q = query.strip()
        if when and f"when:{when}" not in q:
            # Avoid doubling the when: param if caller provided it manually.
            q = f"{q} when:{when}"

        params = {
            "q": q,
            "hl": f"{self.language}-{self.region}",
            "gl": self.region,
            "ceid": f"{self.region}:{self.language}",
        }
        return f"{GOOGLE_NEWS_BASE}/search?{urlencode(params)}"

    # Networking -----------------------------------------------------------

    def _get(self, url: str) -> str:
        logger.debug("Requesting Google News page: %s", url)
        resp = self.session.get(url, timeout=self.timeout, proxies=self.proxies)
        resp.raise_for_status()
        logger.debug("Received %d bytes from Google News", len(resp.text))
        return resp.text

    def _resolve_redirect(self, url: str) -> Optional[str]:
        """
        Resolve Google News redirect links to the final destination URL.

        This uses a HEAD/GET with redirects enabled. It is safe but may be
        slower on very large result sets, so callers can disable via config.
        """
        try:
            logger.debug("Resolving article redirect: %s", url)
            # HEAD first – some servers may not support it, so fall back to GET.
            resp = self.session.head(
                url,
                timeout=self.timeout,
                allow_redirects=True,
                proxies=self.proxies,
            )
            if resp.is_redirect or resp.history:
                return resp.url

            # Some servers ignore HEAD's redirect history; do a GET if needed.
            if resp.status_code >= 400:
                resp = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    proxies=self.proxies,
                )
            return resp.url
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to resolve redirect for %s: %s", url, exc)
            return None

    # Parsing --------------------------------------------------------------

    def _extract_article_elements(self, soup: BeautifulSoup) -> Iterable[Tag]:
        # Google uses <article> tags for each item as of this implementation.
        # This may change over time; we keep the selector simple and robust.
        for article in soup.find_all("article"):
            yield article

    @staticmethod
    def _extract_title(article: Tag) -> str:
        link = article.find("a")
        if not isinstance(link, Tag):
            return ""
        return link.get_text(strip=True)

    @staticmethod
    def _extract_raw_article_url(article: Tag) -> Optional[str]:
        link = article.find("a", href=True)
        if not isinstance(link, Tag):
            return None
        href = link["href"]
        # Google News uses relative paths like "./articles/..."
        if href.startswith("./") or href.startswith("/"):
            return urljoin(GOOGLE_NEWS_BASE, href.lstrip("./"))
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return urljoin(GOOGLE_NEWS_BASE, href)

    @staticmethod
    def _extract_published_at(article: Tag) -> Optional[str]:
        # Prefer ISO-8601 datetime attribute if available.
        time_tag = article.find("time")
        if isinstance(time_tag, Tag):
            datetime_attr = time_tag.get("datetime")
            if datetime_attr:
                return datetime_attr
            text = time_tag.get_text(strip=True)
            if text:
                try:
                    dt = date_parser.parse(text, fuzzy=True)
                    return dt.isoformat()
                except Exception:  # noqa: BLE001
                    return text
        return None

    @staticmethod
    def _extract_source(article: Tag) -> Optional[str]:
        # Sources are usually in a <div> or <span> near the time element.
        # We search for the first eligible span containing non-empty text.
        candidates = article.find_all("span")
        for span in candidates:
            txt = span.get_text(strip=True)
            if txt and len(txt.split()) <= 5:
                # Heuristic: source names are short (e.g. "MyLondon")
                return txt
        return None

    @staticmethod
    def _extract_image(article: Tag) -> Optional[str]:
        img = article.find("img")
        if not isinstance(img, Tag):
            return None
        return img.get("src") or img.get("data-src")

    @staticmethod
    def _extract_source_icon(article: Tag) -> Optional[str]:
        # Some layouts expose source icons as small <img> elements.
        imgs = article.find_all("img")
        if not imgs:
            return None
        # Heuristic: icon-sized images tend to be the smallest.
        smallest = None
        smallest_area = None
        for img in imgs:
            w = img.get("width")
            h = img.get("height")
            try:
                area = int(w) * int(h)
            except Exception:  # noqa: BLE001
                continue
            if smallest is None or area < smallest_area:
                smallest = img
                smallest_area = area
        if smallest is None:
            return None
        return smallest.get("src") or smallest.get("data-src")

    @staticmethod
    def _extract_author(article: Tag) -> Optional[str]:
        # Authors are uncommon in Google News search results but may appear as
        # "By Name" somewhere in the metadata.
        text = article.get_text(separator=" ", strip=True)
        if not text:
            return None
        marker = "By "
        idx = text.find(marker)
        if idx == -1:
            return None
        segment = text[idx + len(marker) :].split("  ")[0].strip()
        # Limit length to avoid returning whole paragraphs.
        if segment and len(segment) <= 80:
            return "By " + segment
        return None

    # Public API -----------------------------------------------------------

    def search(self, query: str, when: Optional[str] = None, max_items: int = 50) -> List[Dict[str, Any]]:
        """
        Perform a search and return a list of article dictionaries.

        Parameters
        ----------
        query:
            The search query, e.g. "banana" or "banana when:1h".
        when:
            Relative time window like '1h', '7d', '1y'. If provided and not already
            part of `query`, it is appended as 'when:1h'.
        max_items:
            Maximum number of articles to extract. The scraper stops early when the
            limit is reached.
        """
        search_url = self.build_search_url(query, when=when)
        html = self._get(search_url)
        soup = BeautifulSoup(html, "html.parser")

        articles: List[GoogleNewsArticle] = []
        for article_tag in self._extract_article_elements(soup):
            raw_url = self._extract_raw_article_url(article_tag)
            if not raw_url:
                continue

            decoded_url: Optional[str] = None
            if self.decode_articles:
                decoded_url = self._resolve_redirect(raw_url)
                # Be kind to servers when resolving many redirects.
                time.sleep(0.05)

            item = GoogleNewsArticle(
                googleNewsUrl=search_url,
                articleUrl=raw_url,
                decodedArticleUrl=decoded_url,
                title=self._extract_title(article_tag),
                publishedAt=self._extract_published_at(article_tag),
                imageUrl=self._extract_image(article_tag),
                source=self._extract_source(article_tag),
                sourceIconUrl=self._extract_source_icon(article_tag),
                author=self._extract_author(article_tag),
            )
            articles.append(item)

            if len(articles) >= max_items:
                break

        logger.info("Parsed %d articles from Google News", len(articles))
        return [a.to_dict() for a in articles]

    # Debug helpers --------------------------------------------------------

    @staticmethod
    def to_pretty_json(articles: Iterable[Dict[str, Any]]) -> str:
        return json.dumps(list(articles), indent=2, ensure_ascii=False)