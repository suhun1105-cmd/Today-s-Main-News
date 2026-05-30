import sys
import httpx
import feedparser
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import NAVER_CATEGORIES, MAX_ARTICLES_PER_CATEGORY

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

# 해외 서버에서 네이버 차단 시 사용할 RSS 폴백
_RSS_FALLBACK = {
    100: "https://www.yna.co.kr/rss/politics.xml",
    101: "https://www.yna.co.kr/rss/economy.xml",
    102: "https://www.yna.co.kr/rss/society.xml",
    103: "https://www.yna.co.kr/rss/culture.xml",
    104: "https://feeds.bbci.co.uk/news/world/rss.xml",
    105: "https://feeds.bbci.co.uk/news/technology/rss.xml",
}


def _fetch_rss_fallback(cat: dict) -> list[dict]:
    """네이버 차단 시 RSS로 대체 수집"""
    url = _RSS_FALLBACK.get(cat["id"])
    if not url:
        return []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        articles = []
        for entry in feed.entries[:MAX_ARTICLES_PER_CATEGORY]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            if title and link:
                articles.append({"title": title, "link": link, "summary": ""})
        return articles
    except Exception:
        return []


def _fetch_category(cat: dict) -> dict:
    # 1차: 네이버 스크래핑 시도
    articles = []
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
                if len(articles) >= MAX_ARTICLES_PER_CATEGORY:
                    break
            if len(articles) >= MAX_ARTICLES_PER_CATEGORY:
                break
    except Exception:
        pass

    # 2차: 네이버 차단 시 RSS 폴백
    if not articles:
        articles = _fetch_rss_fallback(cat)
        source = "RSS"
    else:
        source = "Naver"

    return {
        "id": cat["id"],
        "name": cat["name"],
        "emoji": cat["emoji"],
        "articles": articles[:MAX_ARTICLES_PER_CATEGORY],
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
                print(f"  [OK] {result['name']} ({result.get('source','?')}) - {len(result['articles'])}건")
            results.append(result)

    order = {cat["id"]: i for i, cat in enumerate(NAVER_CATEGORIES)}
    results.sort(key=lambda r: order.get(r["id"], 999))
    return results
