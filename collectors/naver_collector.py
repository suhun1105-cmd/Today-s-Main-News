import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import feedparser
import httpx
from bs4 import BeautifulSoup

from config import DEFAULT_ARTICLES_PER_CATEGORY, NAVER_CATEGORIES

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://news.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 해외 클라우드에서 네이버 접근이 막힐 때 사용하는 RSS 폴백
_RSS_FALLBACK = {
    100: "https://www.yna.co.kr/rss/politics.xml",
    101: "https://www.yna.co.kr/rss/economy.xml",
    102: "https://www.yna.co.kr/rss/society.xml",
    103: "https://www.yna.co.kr/rss/culture.xml",
    104: "https://feeds.bbci.co.uk/news/world/rss.xml",
    105: "https://feeds.bbci.co.uk/news/technology/rss.xml",
}


def _category_limit(cat: dict) -> int:
    return int(cat.get("limit", DEFAULT_ARTICLES_PER_CATEGORY))


def _fetch_rss_fallback(cat: dict) -> list[dict]:
    url = _RSS_FALLBACK.get(cat["id"])
    limit = _category_limit(cat)
    if not url:
        return []

    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        articles = []
        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if title and link:
                articles.append({"title": title, "link": link, "summary": ""})
        return articles
    except Exception:
        return []


def _fetch_category(cat: dict) -> dict:
    limit = _category_limit(cat)
    articles = []
    source = "Naver"

    try:
        url = f"https://news.naver.com/section/{cat['id']}"
        resp = httpx.get(url, headers=_HEADERS, follow_redirects=True, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        seen_titles = set()
        selectors = [
            "a.cluster_head_link",
            "a.sa_text_title",
            ".cluster_head a",
            ".sa_list a.sa_text_title",
            ".section_article a",
            "a[class*='title']",
        ]

        for selector in selectors:
            for tag in soup.select(selector):
                title = tag.get_text(strip=True)
                href = tag.get("href", "")
                if not title or not href or title in seen_titles or len(title) < 6:
                    continue
                if not href.startswith("http"):
                    href = "https://news.naver.com" + href
                seen_titles.add(title)
                articles.append({"title": title, "link": href, "summary": ""})
                if len(articles) >= limit:
                    break
            if len(articles) >= limit:
                break
    except Exception:
        pass

    if not articles:
        articles = _fetch_rss_fallback(cat)
        source = "RSS"

    return {
        "id": cat["id"],
        "name": cat["name"],
        "emoji": cat["emoji"],
        "articles": articles[:limit],
        "error": None if articles else "기사 수집 실패",
        "source": source,
    }


def collect_all() -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_category, cat): cat for cat in NAVER_CATEGORIES}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                print(f"  [경고] {result['name']}: {result['error']}")
            else:
                print(
                    f"  [OK] {result['name']} ({result.get('source', '?')}) "
                    f"- {len(result['articles'])}건"
                )
            results.append(result)

    order = {cat["id"]: i for i, cat in enumerate(NAVER_CATEGORIES)}
    results.sort(key=lambda r: order.get(r["id"], 999))
    return results
