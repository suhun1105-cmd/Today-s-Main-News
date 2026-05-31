from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def generate(category_data: list[dict], trends: str, output_dir: Path) -> Path:
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)))
    template = env.get_template("report.html")

    now = datetime.now()
    html = template.render(
        generated_at=now.strftime("%Y년 %m월 %d일"),
        trends=trends,
        categories=category_data,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"report_{now.strftime('%Y%m%d')}.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
