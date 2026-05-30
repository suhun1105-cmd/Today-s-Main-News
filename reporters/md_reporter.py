from pathlib import Path
from datetime import datetime


def generate(category_data: list[dict], trends: str, output_dir: Path) -> Path:
    lines = []
    now_str = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

    lines.append("# 네이버 뉴스 카테고리별 분석 리포트")
    lines.append(f"\n생성: {now_str}\n")
    lines.append("---\n")
    lines.append("## 오늘의 트렌드 분석\n")
    lines.append(trends)
    lines.append("\n---\n")

    for cat in category_data:
        lines.append(f"## {cat['emoji']} {cat['name']}\n")
        for article in cat["articles"]:
            an = article.get("analysis", {})
            lines.append(f"### [{article['title']}]({article['link']})\n")
            if an.get("summary"):
                lines.append(f"**📋 요약**  \n{an['summary']}\n")
            if an.get("explanation"):
                lines.append(f"**🎒 쉬운 설명**  \n{an['explanation']}\n")
            lines.append("")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"report_{datetime.now().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
