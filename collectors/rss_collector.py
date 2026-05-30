import sys
import feedparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import NEWS_SOURCES, MAX_ARTICLES_PER_SOURCE

# Windows 콘솔 UTF-8 출력
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _fetch_one(source: dict) -> dict:
    try:
        feed = feedparser.parse(source["url"], request_headers={"User-Agent": _UA})
        articles = []
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            articles.append({
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
            })
        return {
            "name": source["name"],
            "lang": source["lang"],
            "category": source["category"],
            "articles": articles,
            "error": None,
        }
    except Exception as e:
        return {
            "name": source["name"],
            "lang": source["lang"],
            "category": source["category"],
            "articles": [],
            "error": str(e),
        }


def collect_all() -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_fetch_one, src): src for src in NEWS_SOURCES}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                print(f"  [경고] {result['name']} 수집 실패: {result['error']}")
            else:
                print(f"  [OK] {result['name']} - {len(result['articles'])}건")
            results.append(result)

    # 원래 NEWS_SOURCES 순서로 정렬
    order = {src["name"]: i for i, src in enumerate(NEWS_SOURCES)}
    results.sort(key=lambda r: order.get(r["name"], 999))
    return results
