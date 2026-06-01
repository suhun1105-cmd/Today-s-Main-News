import json
import os
import re

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from config import GEMINI_MODEL


_api_key = os.environ.get("GOOGLE_API_KEY", "")
genai.configure(api_key=_api_key)

_json_model = genai.GenerativeModel(
    GEMINI_MODEL,
    system_instruction=(
        "당신은 뉴스 해설 전문가입니다. "
        "기사 제목을 바탕으로 핵심 요약과 쉬운 기사 설명을 한국어로 작성합니다. "
        "제목에 없는 사실을 지어내지 말고, 제목에서 확인되는 내용과 일반적인 배경 설명을 구분하세요. "
        "응답은 반드시 순수 JSON 객체만 출력하세요. "
        "마크다운 코드블록이나 JSON 밖의 문장은 절대 포함하지 마세요."
    ),
)

_text_model = genai.GenerativeModel(
    GEMINI_MODEL,
    system_instruction=(
        "당신은 뉴스 트렌드 분석 전문가입니다. "
        "주어진 카테고리별 헤드라인을 바탕으로 핵심 키워드를 "
        "한국어 마크다운 형식으로 간결하고 읽기 쉽게 정리합니다."
    ),
)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json_object(text: str) -> dict | None:
    text = _strip_code_fence(text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list) and obj:
            return obj[0]
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def analyze_article(cat_name: str, article: dict) -> dict:
    title = article["title"]
    prompt = (
        f"카테고리: {cat_name}\n"
        f"기사 제목: {title}\n\n"
        "기사 본문이 아니라 제목만 제공됩니다. 제목에 없는 구체적인 수치, 발언, 원인, 결과는 지어내지 마세요.\n"
        "다만 독자가 이해하기 쉽도록 제목에서 확인되는 내용, 배경, 왜 중요한지를 충분히 풀어 쓰세요.\n\n"
        "아래 두 필드만 가진 JSON으로 응답하세요.\n"
        "{\n"
        '  "summary": "기사 핵심을 3~4문장으로 요약. 누가/무엇을/왜 중요한지를 포함하되 추측은 피함.",\n'
        '  "explanation": "초등학생도 이해할 수 있게 4~5문장으로 설명. 어려운 단어를 쉽게 풀고, 이 일이 사람들에게 어떤 의미인지 설명."\n'
        "}"
    )

    response = _json_model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.3,
            max_output_tokens=1000,
        ),
        request_options={"timeout": 60},
    )

    obj = _parse_json_object(response.text or "")
    if obj and (obj.get("summary") or obj.get("explanation")):
        return {
            "title": title,
            "summary": obj.get("summary", ""),
            "explanation": obj.get("explanation", ""),
        }
    return {"title": title, "summary": "", "explanation": ""}


def analyze_report(category_data: list[dict]) -> dict:
    """전체 리포트를 Gemini 1회 호출로 분석한다.

    무료 Gemini API는 분당 요청 수 제한이 낮기 때문에 기사별로 18회 호출하면
    429가 쉽게 발생한다. 전체 기사와 트렌드를 한 번에 요청해 요청 수를 줄인다.
    """
    payload = []
    for cat in category_data:
        payload.append(
            {
                "id": cat["id"],
                "name": cat["name"],
                "articles": [
                    {"index": i, "title": article["title"]}
                    for i, article in enumerate(cat["articles"])
                    if article.get("title")
                ],
            }
        )

    prompt = (
        "아래 뉴스 데이터를 분석해서 반드시 JSON 객체 하나로만 응답하세요.\n"
        "마크다운 코드블록은 사용하지 마세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "응답 형식:\n"
        "{\n"
        '  "categories": [\n'
        '    {"id": 100, "articles": [\n'
        '      {"index": 0, "summary": "기사 핵심을 자세히 4문장 이상", "explanation": "기사 설명을 쉽게 5문장 이상"},\n'
        '      {"index": 1, "summary": "...", "explanation": "..."},\n'
        '      {"index": 2, "summary": "...", "explanation": "..."}\n'
        "    ]}\n"
        "  ],\n"
        '  "trends": "## 카테고리별 핵심 키워드\\n| 정치 | `키워드1` `키워드2` `키워드3` |\\n..."\n'
        "}\n\n"
        "trends에는 아래 섹션만 포함하세요. '오늘의 주요 이슈' 또는 '오늘의 주요 뉴스' 섹션은 만들지 마세요.\n"
        "## 카테고리별 핵심 키워드\n"
        "헤더와 구분선 없이 데이터 행만 있는 마크다운 표로 작성하세요.\n"
        "형식: | 카테고리명 | `키워드1` `키워드2` `키워드3` |\n\n"
        "각 기사 분석 작성 규칙:\n"
        "- summary는 반드시 4문장 이상으로 작성하세요. 한 문장으로 끝내지 마세요.\n"
        "- summary에는 제목에서 확인되는 핵심 사건, 관련 주체, 현재 상황, 독자가 알아야 할 맥락을 포함하세요.\n"
        "- explanation은 반드시 5문장 이상으로 작성하세요. 한 문장으로 끝내지 마세요.\n"
        "- explanation은 초등학생도 이해할 수 있게 어려운 단어를 풀어 설명하고, 배경과 왜 중요한 뉴스인지 알려주세요.\n"
        "- explanation에는 '쉽게 말해', '이 뉴스가 중요한 이유는'처럼 이해를 돕는 표현을 자연스럽게 포함하세요.\n"
        "- 제목에 없는 세부 사실, 수치, 원인, 결과는 절대 만들어내지 마세요.\n"
        "- 정보가 부족하면 '제목만 보면' 또는 '제목상으로는'처럼 한계를 드러내세요.\n"
        "- 모든 카테고리와 모든 기사 index를 빠짐없이 포함하세요."
    )

    response = _json_model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.3,
            max_output_tokens=12000,
        ),
        request_options={"timeout": 120},
    )

    obj = _parse_json_object(response.text or "")
    if not obj:
        raise ValueError("Gemini 응답을 JSON으로 파싱하지 못했습니다.")
    return obj


def analyze_trends(category_data: list[dict]) -> str:
    lines = []
    for cat in category_data:
        titles = [a["title"] for a in cat["articles"] if a.get("title")]
        if titles:
            lines.append(f"[{cat['name']}]")
            lines.extend(f"- {title}" for title in titles)
            lines.append("")

    prompt = (
        "아래는 오늘 뉴스 각 카테고리의 주요 헤드라인입니다.\n\n"
        + "\n".join(lines)
        + "\n\n"
        "아래 섹션만 작성하세요. '오늘의 주요 이슈' 또는 '오늘의 주요 뉴스' 섹션은 만들지 마세요.\n\n"
        "## 카테고리별 핵심 키워드\n"
        "헤더와 구분선 없이 데이터 행만 있는 마크다운 표로 작성하세요.\n"
        "형식: | 카테고리명 | `키워드1` `키워드2` `키워드3` |\n\n"
        "반드시 정치, 경제, 사회, 생활/문화, 세계, IT/과학을 모두 포함하세요."
    )

    response = _text_model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            temperature=0.4,
            max_output_tokens=1500,
        ),
        request_options={"timeout": 60},
    )
    return (response.text or "").strip()
