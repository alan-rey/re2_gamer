"""A small, polite MediaWiki API client built on the standard library.

Why the API instead of scraping HTML: `action=query` returns the article's
*raw wikitext* plus links and categories as JSON. That is the same data the
wiki itself renders from, it is stable across Fandom theme changes, it is far
smaller than HTML, and it needs no HTML parser. Scraping rendered pages would
mean re-parsing navigation chrome on every article and would break whenever
Fandom ships a theme.

Caching: every response is written to `story-builder/cache/<lang>/<sha1>.json`.
Re-runs are therefore offline and byte-identical, which is what makes the
mapping step testable and reproducible.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .config import (
    CACHE_DIR,
    DEFAULT_MAXLAG,
    DEFAULT_RATE_LIMIT_S,
    DEFAULT_TIMEOUT_S,
    DEFAULT_USER_AGENT,
    ENGLISH_PREFIXES,
    FETCH_CHUNK,
    PROP_LIMIT,
    SKIP_TITLE_PREFIXES,
    WIKI_HOST,
)


class WikiError(RuntimeError):
    """Raised when the API returns an error we cannot recover from."""


def api_url(host: str, lang: str) -> str:
    """Map a wiki language to its API endpoint."""
    if lang in ENGLISH_PREFIXES:
        return f"https://{host}/api.php"
    return f"https://{host}/{lang}/api.php"


def page_url(host: str, lang: str, title: str) -> str:
    """Human-readable article URL, for attribution and click-through."""
    prefix = "" if lang in ENGLISH_PREFIXES else f"/{lang}"
    return f"https://{host}{prefix}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"


def is_entity_title(title: str) -> bool:
    """False for File:/Category:/Template:/interwiki targets."""
    return not title.strip().lower().startswith(SKIP_TITLE_PREFIXES)


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


class WikiClient:
    """Read-only MediaWiki API access with caching, throttling and retries."""

    def __init__(
        self,
        lang: str = "en",
        host: str = WIKI_HOST,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limit_s: float = DEFAULT_RATE_LIMIT_S,
        cache_dir: Path | None = None,
        offline: bool = False,
        refresh: bool = False,
        max_retries: int = 3,
        timeout: float = DEFAULT_TIMEOUT_S,
        maxlag: int = DEFAULT_MAXLAG,
    ) -> None:
        self.lang = lang
        self.host = host
        self.user_agent = user_agent
        self.rate_limit_s = rate_limit_s
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR / lang
        self.offline = offline
        self.refresh = refresh
        self.max_retries = max_retries
        self.timeout = timeout
        self.maxlag = maxlag
        self._last_request = 0.0
        self.request_count = 0
        self.cache_hits = 0

    # -- plumbing ------------------------------------------------------------

    @property
    def endpoint(self) -> str:
        return api_url(self.host, self.lang)

    def _cache_path(self, params: dict[str, Any]) -> Path:
        payload = json.dumps(sorted((k, str(v)) for k, v in params.items()),
                             ensure_ascii=False)
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.rate_limit_s:
            time.sleep(self.rate_limit_s - elapsed)
        self._last_request = time.monotonic()

    def _http_get(self, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self.request_count += 1
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 429/503 mean "slow down"; anything 4xx-other is fatal.
                if exc.code not in (429, 500, 502, 503, 504):
                    raise WikiError(f"HTTP {exc.code} for {url}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            time.sleep(2 ** attempt)
        raise WikiError(f"request failed after {self.max_retries} attempts: {last_error}")

    def _get(self, params: dict[str, Any], *, use_cache: bool = True) -> dict[str, Any]:
        """Perform one API call, reading and writing the on-disk cache."""
        full = {k: v for k, v in params.items() if v is not None}
        full.setdefault("format", "json")
        full.setdefault("formatversion", "2")
        full["maxlag"] = self.maxlag

        cache_path = self._cache_path(full)
        if use_cache and not self.refresh and cache_path.exists():
            self.cache_hits += 1
            return json.loads(cache_path.read_text(encoding="utf-8"))

        if self.offline:
            raise WikiError(
                "offline and response not cached; run without --offline once, "
                f"or pass --refresh. Missing: {full.get('action')}/{full.get('list') or full.get('prop')}"
            )

        data = self._http_get(full)
        if "error" in data:
            raise WikiError(json.dumps(data["error"])[:400])
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(cache_path)     # atomic-ish: never leave a half-written cache file
        return data

    def _drain(
        self,
        params: dict[str, Any],
        extract: Callable[[dict[str, Any]], list[Any]],
        max_items: int,
    ) -> list[Any]:
        """Follow `continue` tokens until exhausted or `max_items` reached."""
        current = dict(params)
        collected: list[Any] = []
        while True:
            data = self._get(current)
            collected.extend(extract(data))
            if len(collected) >= max_items:
                return collected[:max_items]
            cont = data.get("continue")
            if not cont:
                return collected
            current = {**params, **cont}

    # -- diagnostics ---------------------------------------------------------

    def siteinfo(self) -> dict[str, Any]:
        """Wiki identity: site name, generator, and the content licence."""
        data = self._get({
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general|rightsinfo|namespaces",
        })
        return data.get("query", {})

    def licence(self) -> dict[str, str]:
        """Return {'text': ..., 'url': ...} for the wiki's content licence.

        This matters: Fandom wikis are usually CC-BY-SA, which is share-alike.
        We need the exact licence wording to attribute scraped text correctly.
        """
        rights = self.siteinfo().get("rightsinfo", {})
        return {"text": rights.get("text", ""), "url": rights.get("url", "")}

    def robots_txt(self) -> str:
        """Fetch robots.txt so we can confirm our access path is permitted."""
        url = f"https://{self.host}/robots.txt"
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:                       # noqa: BLE001 - diagnostic only
            return f"# could not fetch robots.txt: {exc}"

    # -- page access ---------------------------------------------------------

    def fetch_pages(self, titles: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Fetch wikitext + categories + links for many titles in bulk.

        Three `prop` modules are requested in one round trip per 20 titles, so
        a 60-page corpus costs 3 HTTP calls rather than 180. If the API signals
        that a module was truncated for a hub page, that title is recorded in
        `_truncated` by the caller and refetched individually.
        """
        wanted = [t for t in dict.fromkeys(titles) if t.strip()]
        results: dict[str, dict[str, Any]] = {}
        for chunk in chunks(wanted, FETCH_CHUNK):
            data = self._get({
                "action": "query",
                "prop": "revisions|categories|links",
                "rvprop": "content",
                "rvslots": "main",
                "cllimit": "max",
                "pllimit": "max",
                "plnamespace": "0",
                "titles": "|".join(chunk),
                "redirects": "1",
            })
            query = data.get("query", {})
            redirected_from: dict[str, str] = {}
            for entry in query.get("redirects", []):
                redirected_from[entry["to"]] = entry["from"]
            for page in query.get("pages", []):
                title = page.get("title", "")
                if page.get("missing"):
                    results[title] = {"title": title, "missing": True}
                    continue
                revisions = page.get("revisions") or [{}]
                slots = revisions[0].get("slots", {}).get("main", {})
                results[title] = {
                    "title": title,
                    "missing": False,
                    "redirected_from": redirected_from.get(title),
                    "wikitext": slots.get("content", ""),
                    "categories": [
                        c["title"] for c in page.get("categories", [])
                    ],
                    "links": [
                        l["title"] for l in page.get("links", [])
                        if is_entity_title(l["title"])
                    ],
                }
        return results

    def full_links(self, title: str, *, max_items: int = 5000) -> list[str]:
        """Every namespace-0 link on one page, following continuation."""
        def extract(data: dict[str, Any]) -> list[str]:
            pages = data.get("query", {}).get("pages", [])
            if not pages:
                return []
            return [l["title"] for l in pages[0].get("links", [])
                    if is_entity_title(l["title"])]

        return self._drain(
            {"action": "query", "prop": "links", "titles": title,
             "pllimit": "max", "plnamespace": "0"},
            extract, max_items,
        )

    def full_categories(self, title: str, *, max_items: int = 1000) -> list[str]:
        """Every category on one page, following continuation."""
        def extract(data: dict[str, Any]) -> list[str]:
            pages = data.get("query", {}).get("pages", [])
            if not pages:
                return []
            return [c["title"] for c in pages[0].get("categories", [])]

        return self._drain(
            {"action": "query", "prop": "categories", "titles": title, "cllimit": "max"},
            extract, max_items,
        )

    def category_members(self, category: str, *, limit: int = 500) -> list[str]:
        """Titles in a category, e.g. 'Category:Characters'."""
        def extract(data: dict[str, Any]) -> list[str]:
            return [m["title"] for m in data.get("query", {}).get("categorymembers", [])
                    if m.get("ns") == 0 and is_entity_title(m["title"])]

        return self._drain(
            {"action": "query", "list": "categorymembers", "cmtitle": category,
             "cmlimit": "max", "cmtype": "page", "cmnamespace": "0"},
            extract, limit,
        )

    def backlinks(self, title: str, *, limit: int = 500) -> list[str]:
        """Pages that link *to* `title` -- cheap relevance signal."""
        def extract(data: dict[str, Any]) -> list[str]:
            return [b["title"] for b in data.get("query", {}).get("backlinks", [])
                    if is_entity_title(b["title"])]

        return self._drain(
            {"action": "query", "list": "backlinks", "bltitle": title,
             "bllimit": "max", "blnamespace": "0"},
            extract, limit,
        )

    def search(self, query: str, *, limit: int = 50) -> list[str]:
        """Full-text search titles -- used for automated seed discovery."""
        def extract(data: dict[str, Any]) -> list[str]:
            return [h["title"] for h in data.get("query", {}).get("search", [])
                    if is_entity_title(h["title"])]

        return self._drain(
            {"action": "query", "list": "search", "srsearch": query,
             "srlimit": "max", "srnamespace": "0"},
            extract, limit,
        )

    # -- convenience ---------------------------------------------------------

    def fetch_extended(self, titles: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Like `fetch_pages`, then refetch any page the bulk pass truncated.

        Hub articles (a protagonist, a major location) can have more than 500
        links. The bulk call silently returns the first 500 and sets a
        `continue` token; we detect that and re-query those pages one at a time
        with full pagination so the graph does not quietly lose edges.
        """
        results = self.fetch_pages(titles)
        for title, page in list(results.items()):
            if page.get("missing"):
                continue
            probe = self._get({
                "action": "query", "prop": "links", "titles": title,
                "pllimit": "max", "plnamespace": "0",
            })
            if "continue" in probe:
                page["links"] = self.full_links(title)
                page["truncated_links"] = True
            if len(page.get("categories", [])) >= PROP_LIMIT:
                page["categories"] = self.full_categories(title)
        return results
