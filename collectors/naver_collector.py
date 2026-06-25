import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

import httpx

from config import DEFAULT_ARTICLES_PER_CATEGORY, NAVER_CATEGORIES

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
_API_URL = "https://openapi.naver.com/v1/search/news.json"
_SCRAPE_TIMEOUT = 6
_BODY_MAX_CHARS = 1500

_CATEGORY_KEYWORDS = {
    100: "정치",
    101: "경제",
    102: "사회",
    103: "생활문화",
    104: "세계",
    105: "IT과학",
}

_NAVER_ARTICLE_SELECTORS = [
    "#dic_area",
    "#articleBodyContents",
    "#articeBody",
    "#newsEndContents",
    ".go_trans._article_content",
]
_GENERIC_ARTICLE_SELECTORS = [
    "article",
    '[class*="article-body"]',
    '[class*="article_body"]',
    '[id*="article"]',
    ".news-body",
]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _fetch_article_body(naver_link: str, original_link: str) -> str:
    """네이버 뉴스 또는 원본 URL에서 기사 본문을 추출합니다."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    # 네이버 뉴스 링크 우선 시도 (스크래핑 안정성이 높음)
    urls_to_try = []
    if naver_link and "naver.com" in naver_link:
        urls_to_try.append(naver_link)
    if original_link and original_link not in urls_to_try:
        urls_to_try.append(original_link)
    if naver_link and naver_link not in urls_to_try:
        urls_to_try.append(naver_link)

    for url in urls_to_try:
        try:
            resp = httpx.get(url, timeout=_SCRAPE_TIMEOUT, follow_redirects=True, headers=headers)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe", "figure"]):
                tag.decompose()

            selectors = _NAVER_ARTICLE_SELECTORS if "naver.com" in url else _GENERIC_ARTICLE_SELECTORS
            for selector in selectors:
                elem = soup.select_one(selector)
                if elem:
                    text = elem.get_text(separator=" ", strip=True)
                    text = re.sub(r"\s{2,}", " ", text)
                    if len(text) > 100:
                        return text[:_BODY_MAX_CHARS]

            # 범용 fallback
            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s{2,}", " ", text)
            if len(text) > 200:
                return text[:_BODY_MAX_CHARS]
        except Exception:
            continue

    return ""


def _fetch_category(cat: dict) -> dict:
    limit = int(cat.get("limit", DEFAULT_ARTICLES_PER_CATEGORY))
    keyword = _CATEGORY_KEYWORDS.get(cat["id"], cat["name"])

    try:
        qs = urlencode({"query": keyword, "display": limit, "sort": "date"})
        resp = httpx.get(
            f"{_API_URL}?{qs}",
            headers={
                "X-Naver-Client-Id": _CLIENT_ID,
                "X-Naver-Client-Secret": _CLIENT_SECRET,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        articles = [
            {
                "title": _strip_html(item["title"]),
                "link": item.get("link", ""),
                "originallink": item.get("originallink", ""),
                "summary": _strip_html(item.get("description", "")),
                "body": "",
            }
            for item in items
        ]

        # 기사 본문 병렬 스크래핑
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(_fetch_article_body, art["link"], art["originallink"]): i
                for i, art in enumerate(articles)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    articles[idx]["body"] = future.result()
                except Exception:
                    pass

        return {
            "id": cat["id"],
            "name": cat["name"],
            "emoji": cat["emoji"],
            "articles": articles,
            "error": None,
            "source": "NaverAPI",
        }
    except Exception as e:
        return {
            "id": cat["id"],
            "name": cat["name"],
            "emoji": cat["emoji"],
            "articles": [],
            "error": str(e),
            "source": "NaverAPI",
        }


def collect_all() -> list[dict]:
    if not _CLIENT_ID or not _CLIENT_SECRET:
        raise RuntimeError(
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수가 없습니다. .env 파일을 확인하세요."
        )

    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch_category, cat): cat for cat in NAVER_CATEGORIES}
        for future in as_completed(futures):
            result = future.result()
            if result["error"]:
                print(f"  [경고] {result['name']}: {result['error']}")
            else:
                print(f"  [OK] {result['name']} (NaverAPI) - {len(result['articles'])}건")
            results.append(result)

    order = {cat["id"]: i for i, cat in enumerate(NAVER_CATEGORIES)}
    results.sort(key=lambda r: order.get(r["id"], 999))
    return results
