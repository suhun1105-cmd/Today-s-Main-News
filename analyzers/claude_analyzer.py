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
        "응답은 반드시 순수 JSON 객체만 출력하세요. "
        "마크다운 코드블록이나 JSON 밖의 문장은 절대 포함하지 마세요."
    ),
)

_text_model = genai.GenerativeModel(
    GEMINI_MODEL,
    system_instruction=(
        "당신은 뉴스 트렌드 분석 전문가입니다. "
        "주어진 카테고리별 헤드라인을 바탕으로 오늘의 주요 이슈와 핵심 키워드를 "
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
        "아래 두 필드만 가진 JSON으로 응답하세요.\n"
        '{"summary": "기사 핵심을 2~3문장으로 요약", '
        '"explanation": "기사 내용을 초등학생도 이해할 수 있게 3~4문장으로 설명"}'
    )

    response = _json_model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            temperature=0.3,
            max_output_tokens=600,
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
        "아래 두 섹션만 작성하세요.\n\n"
        "## 1. 오늘의 주요 이슈\n"
        "헤더와 구분선 없이 데이터 행만 있는 마크다운 표로 작성하세요.\n"
        "형식: | 카테고리명 | 해당 카테고리의 핵심 이슈를 1~2문장으로 설명 |\n\n"
        "## 2. 카테고리별 핵심 키워드\n"
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
