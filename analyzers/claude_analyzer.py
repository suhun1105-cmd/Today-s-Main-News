import json
import os
import re

import httpx

from config import OPENAI_MODEL


OPENAI_API_URL = "https://api.openai.com/v1/responses"
NEWS_SYSTEM_PROMPT = (
    "당신은 뉴스 해설 전문가입니다. "
    "기사 제목을 바탕으로 핵심 요약과 쉬운 기사 설명을 한국어로 작성합니다. "
    "제목에 없는 사실을 지어내지 말고, 제목에서 확인되는 내용과 일반적인 배경 설명을 구분하세요. "
    "응답은 반드시 요청한 JSON 구조만 출력하세요."
)

REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["categories", "trends"],
    "properties": {
        "categories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "articles"],
                "properties": {
                    "id": {"type": "integer"},
                    "articles": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["index", "summary", "explanation"],
                            "properties": {
                                "index": {"type": "integer"},
                                "summary": {"type": "string"},
                                "explanation": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        "trends": {"type": "string"},
    },
}

ARTICLE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "explanation"],
    "properties": {
        "summary": {"type": "string"},
        "explanation": {"type": "string"},
    },
}


def _openai_headers() -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 없습니다.")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_output_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]

    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(content["text"])
    return "".join(parts).strip()


def _responses_json(prompt: str, schema: dict, schema_name: str, max_output_tokens: int) -> dict:
    payload = {
        "model": os.environ.get("OPENAI_MODEL", OPENAI_MODEL),
        "instructions": NEWS_SYSTEM_PROMPT,
        "input": prompt,
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    response = httpx.post(
        OPENAI_API_URL,
        headers=_openai_headers(),
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    text = _extract_output_text(response.json())
    if not text:
        raise ValueError("OpenAI 응답에서 텍스트를 찾지 못했습니다.")
    return _parse_json_object(text)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_json_object(text: str) -> dict:
    text = _strip_code_fence(text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError("OpenAI 응답을 JSON으로 파싱하지 못했습니다.")


def analyze_article(cat_name: str, article: dict) -> dict:
    title = article["title"]
    prompt = (
        f"카테고리: {cat_name}\n"
        f"기사 제목: {title}\n\n"
        "기사 본문이 아니라 제목만 제공됩니다. 제목에 없는 구체적인 수치, 발언, 원인, 결과는 지어내지 마세요.\n"
        "다만 독자가 이해하기 쉽도록 제목에서 확인되는 내용, 배경, 왜 중요한지를 충분히 풀어 쓰세요.\n\n"
        "summary는 반드시 4문장 이상, explanation은 반드시 5문장 이상으로 작성하세요."
    )
    obj = _responses_json(prompt, ARTICLE_SCHEMA, "article_analysis", 1200)
    return {
        "title": title,
        "summary": obj.get("summary", ""),
        "explanation": obj.get("explanation", ""),
    }


def analyze_report(category_data: list[dict]) -> dict:
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
        "아래 뉴스 데이터를 분석해서 JSON 객체로 응답하세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
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
    return _responses_json(prompt, REPORT_SCHEMA, "news_report_analysis", 14000)


def analyze_trends(category_data: list[dict]) -> str:
    report = analyze_report(category_data)
    return report.get("trends", "")
