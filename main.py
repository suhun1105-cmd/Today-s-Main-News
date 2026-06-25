import sys
import time
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from analyzers.claude_analyzer import analyze_article, analyze_trends_only
from collectors.naver_collector import collect_all
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).parent / "reports"


def _fallback_analysis(cat_name: str, title: str) -> dict:
    return {"title": title, "summary": "", "explanation": ""}


def main():
    print("=" * 50)
    print(" Today's Main News — 리포트 생성 시작")
    print("=" * 50)

    print("\n[1/3] 뉴스 수집 중...")
    category_data = collect_all()

    total = sum(len(cat["articles"]) for cat in category_data)
    done = 0
    print(f"\n[2/3] AI 분석 중... (총 {total}건 순차 처리)")

    for cat in category_data:
        for article in cat["articles"]:
            done += 1
            print(f"  [{done}/{total}] {cat['name']}: {article['title'][:40]}...")
            try:
                article["analysis"] = analyze_article(cat["name"], article)
            except Exception as exc:
                print(f"    [경고] 분석 실패: {exc}")
                article["analysis"] = _fallback_analysis(cat["name"], article["title"])
            if done < total:
                time.sleep(2)

    print("\n[3/3] 트렌드 분석 및 리포트 저장 중...")
    try:
        trends = analyze_trends_only(category_data)
    except Exception as exc:
        print(f"  [경고] 트렌드 분석 실패: {exc}")
        trends = ""

    html_path = html_reporter.generate(category_data, trends, REPORTS_DIR)
    md_path = md_reporter.generate(category_data, trends, REPORTS_DIR)

    print("\n" + "=" * 50)
    print(" 완료!")
    print(f"  HTML: {html_path}")
    print(f"  MD  : {md_path}")
    print("=" * 50)


if __name__ == "__main__":
    main()
