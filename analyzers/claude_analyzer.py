import re
import json
import anthropic
from config import CLAUDE_MODEL

_client = anthropic.Anthropic()

_SYSTEM_JSON = (
    "당신은 뉴스 해설 전문가입니다. "
    "기사를 분석하여 핵심 요약과 초등학생도 이해할 수 있는 쉬운 설명을 제공합니다. "
    "응답은 반드시 순수한 JSON 객체만 출력하세요. "
    "```json 같은 마크다운 코드블록을 절대 사용하지 마세요. "
    "JSON 외의 어떠한 텍스트도 포함하지 마세요."
)

_SYSTEM_MARKDOWN = (
    "당신은 뉴스 트렌드 분석 전문가입니다. "
    "주어진 헤드라인을 바탕으로 오늘의 주요 이슈를 한국어 마크다운 형식으로 분석합니다. "
    "JSON이 아닌 일반 마크다운 텍스트로만 응답하세요."
)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    return text.strip()


def _parse_json_object(text: str) -> dict | None:
    """단일 JSON 객체 추출 시도"""
    text = _strip_code_fence(text)

    # 1차: 전체 직접 파싱
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list) and obj:
            return obj[0]
    except json.JSONDecodeError:
        pass

    # 2차: 첫 번째 { ~ 마지막 } 추출
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    return None


def analyze_article(cat_name: str, article: dict) -> dict:
    """기사 1건 → {summary, explanation} 반환.
    title은 원본 그대로 사용해 JSON 파싱 오류 방지."""
    title = article["title"]
    user_msg = (
        f"카테고리: {cat_name}\n"
        f"기사 제목: {title}\n\n"
        "위 기사를 분석하여 아래 두 필드만 가진 JSON으로만 응답하세요:\n"
        '{"summary": "핵심 내용을 2~3문장으로 요약", '
        '"explanation": "초등학생도 이해할 수 있는 쉬운 말로 3~4문장 설명"}'
    )

    response = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=[{"type": "text", "text": _SYSTEM_JSON, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )

    obj = _parse_json_object(response.content[0].text)
    if obj and (obj.get("summary") or obj.get("explanation")):
        return {"title": title, "summary": obj.get("summary", ""), "explanation": obj.get("explanation", "")}
    return {"title": title, "summary": "", "explanation": ""}


def analyze_trends(category_data: list[dict]) -> str:
    lines = []
    for cat in category_data:
        titles = [a["title"] for a in cat["articles"] if a.get("title")]
        if titles:
            lines.append(f"[{cat['name']}]")
            lines.extend(f"- {t}" for t in titles)
            lines.append("")

    user_msg = (
        "아래는 오늘 뉴스 각 카테고리의 주요 헤드라인입니다.\n\n"
        + "\n".join(lines)
        + "\n\n"
        "아래 두 섹션을 마크다운 형식으로 작성해 주세요.\n\n"
        "## 1. 오늘의 주요 이슈\n"
        "헤더 없이 데이터 행만 있는 마크다운 표로 작성하세요 (구분선 없음):\n"
        "| 카테고리명 | 해당 카테고리의 핵심 이슈를 1~2문장으로 서술 |\n"
        "| 카테고리명 | 해당 카테고리의 핵심 이슈를 1~2문장으로 서술 |\n\n"
        "## 2. 카테고리별 핵심 키워드\n"
        "헤더 없이 데이터 행만 있는 마크다운 표로 작성하세요 (구분선 없음):\n"
        "| 카테고리명 | `키워드1` `키워드2` `키워드3` |\n"
        "| 카테고리명 | `키워드1` `키워드2` `키워드3` |\n\n"
        "반드시 모든 카테고리를 포함하고, 위 두 섹션만 작성하세요."
    )

    response = _client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        system=[{"type": "text", "text": _SYSTEM_MARKDOWN, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )

    return response.content[0].text.strip()
