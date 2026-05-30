import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from collectors.naver_collector import collect_all
from analyzers.claude_analyzer import analyze_article, analyze_trends
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).parent / "reports"


def _analyze_cat(cat: dict) -> dict:
    """카테고리 내 기사를 병렬로 각각 분석"""
    articles = cat["articles"]
    if not articles:
        return cat

    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(analyze_article, cat["name"], art): i for i, art in enumerate(articles)}
        results = {}
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    for i, article in enumerate(articles):
        article["analysis"] = results.get(i, {})
    return cat


def main():
    print("=" * 50)
    print(" Today's Main News — 리포트 생성 시작")
    print("=" * 50)

    # 1. 뉴스 수집
    print("\n[1/4] 뉴스 수집 중...")
    category_data = collect_all()

    # 2. 기사별 AI 분석 (카테고리 순차, 기사는 병렬)
    print("\n[2/4] 기사별 AI 분석 중...")
    for cat in category_data:
        if not cat["articles"]:
            continue
        print(f"  -> {cat['name']} ({len(cat['articles'])}건) 분석 중...")
        _analyze_cat(cat)

    # 3. 전체 트렌드 분석
    print("\n[3/4] 전체 트렌드 분석 중...")
    trends = analyze_trends(category_data)

    # 4. 리포트 저장
    print("\n[4/4] 리포트 저장 중...")
    html_path = html_reporter.generate(category_data, trends, REPORTS_DIR)
    md_path = md_reporter.generate(category_data, trends, REPORTS_DIR)

    print("\n" + "=" * 50)
    print(" 완료!")
    print(f"  HTML: {html_path}")
    print(f"  MD  : {md_path}")
    print("=" * 50)


if __name__ == "__main__":
    main()
