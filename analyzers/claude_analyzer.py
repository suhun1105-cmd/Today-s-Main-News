import json
import os
import re
import time

import httpx

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL_DEFAULT = "gemini-2.0-flash"

NEWS_SYSTEM_PROMPT = (
    "당신은 뉴스 해설 전문가입니다. "
    "기사 제목과 본문을 바탕으로 핵심 요약과 쉬운 기사 설명을 한국어로 작성합니다. "
    "제공된 내용에 없는 사실을 지어내지 말고, 확인되는 내용과 일반적인 배경 설명을 구분하세요. "
    "응답은 반드시 요청한 JSON 구조만 출력하세요."
)

# Gemini 스키마는 대문자 타입 사용
ARTICLE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "explanation": {"type": "STRING"},
    },
    "required": ["summary", "explanation"],
}

REPORT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "categories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "INTEGER"},
                    "articles": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "index": {"type": "INTEGER"},
                                "summary": {"type": "STRING"},
                                "explanation": {"type": "STRING"},
                            },
                            "required": ["index", "summary", "explanation"],
                        },
                    },
                },
                "required": ["id", "articles"],
            },
        },
        "trends": {"type": "STRING"},
    },
    "required": ["categories", "trends"],
}

_TRENDS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "trends": {"type": "STRING"},
    },
    "required": ["trends"],
}


def _gemini_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 없습니다.")
    return api_key


def _gemini_model() -> str:
    return os.environ.get("GEMINI_MODEL", GEMINI_MODEL_DEFAULT)


def _extract_text(data: dict) -> str:
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"] or ""
    except (KeyError, IndexError):
        return ""


def _gemini_json(prompt: str, schema: dict, max_tokens: int) -> dict:
    model = _gemini_model()
    url = GEMINI_API_URL.format(model=model)

    payload = {
        "system_instruction": {"parts": [{"text": NEWS_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "maxOutputTokens": max_tokens,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": _gemini_api_key(),
    }

    for attempt in range(5):
        response = httpx.post(url, headers=headers, json=payload, timeout=120)
        if response.status_code == 429:
            wait = int(response.headers.get("retry-after", 2 ** attempt * 5))
            time.sleep(min(wait, 120))
            continue
        response.raise_for_status()
        text = _extract_text(response.json())
        if not text:
            raise ValueError("Gemini 응답에서 텍스트를 찾지 못했습니다.")
        return _parse_json_object(text)

    raise RuntimeError("Gemini API rate limit: 재시도 5회 모두 실패했습니다.")


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
    raise ValueError("Gemini 응답을 JSON으로 파싱하지 못했습니다.")


def analyze_article(cat_name: str, article: dict) -> dict:
    title = article["title"]
    snippet = article.get("summary", "").strip()
    body = article.get("body", "").strip()

    content_section = f"기사 제목: {title}\n"
    if snippet:
        content_section += f"발췌 요약: {snippet}\n"
    if body:
        content_section += f"기사 본문:\n{body}\n"

    caution = (
        "제공된 제목, 발췌, 본문에 근거해서 작성하세요. 본문에 없는 수치나 사실을 만들어내지 마세요."
        if (snippet or body)
        else "기사 제목만 제공됩니다. 제목에 없는 구체적인 수치, 발언, 원인, 결과는 지어내지 마세요."
    )

    prompt = (
        f"카테고리: {cat_name}\n"
        f"{content_section}\n"
        f"{caution}\n\n"
        "summary는 반드시 4문장 이상, explanation은 반드시 5문장 이상으로 작성하세요."
    )
    obj = _gemini_json(prompt, ARTICLE_SCHEMA, 1200)
    return {
        "title": title,
        "summary": obj.get("summary", ""),
        "explanation": obj.get("explanation", ""),
    }


def analyze_report(category_data: list[dict]) -> dict:
    payload = []
    for cat in category_data:
        articles_payload = []
        for i, article in enumerate(cat["articles"]):
            if not article.get("title"):
                continue
            entry = {"index": i, "title": article["title"]}
            snippet = (article.get("summary") or "").strip()
            body = (article.get("body") or "").strip()
            if snippet:
                entry["snippet"] = snippet
            if body:
                entry["body"] = body[:1200]
            articles_payload.append(entry)
        payload.append({"id": cat["id"], "name": cat["name"], "articles": articles_payload})

    prompt = (
        "아래 뉴스 데이터를 분석해서 JSON 객체로 응답하세요.\n"
        "각 기사에는 title(제목), snippet(발췌), body(본문) 중 확보된 정보가 포함되어 있습니다.\n"
        "제공된 실제 내용을 최대한 활용하되, 제공되지 않은 수치·발언·원인은 만들어내지 마세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "trends: 카테고리별 핵심 키워드 표만 작성하세요.\n"
        "형식: | 카테고리명 | `키워드1` `키워드2` `키워드3` |\n"
        "헤더·구분선 없이 데이터 행만 작성하세요.\n\n"
        "각 기사 분석:\n"
        "- summary: 4문장 이상, 핵심 사건·관련 주체·현재 상황·맥락 포함\n"
        "- explanation: 5문장 이상, 초등학생도 이해할 수 있게, '쉽게 말해' 등 표현 포함\n"
        "- 모든 카테고리와 모든 기사 index를 빠짐없이 포함하세요."
    )
    return _gemini_json(prompt, REPORT_SCHEMA, 14000)


def analyze_trends(category_data: list[dict]) -> str:
    report = analyze_report(category_data)
    return report.get("trends", "")


def analyze_trends_only(category_data: list[dict]) -> str:
    payload = [
        {
            "name": cat["name"],
            "titles": [a["title"] for a in cat["articles"] if a.get("title")],
        }
        for cat in category_data
    ]
    prompt = (
        "아래 카테고리별 뉴스 제목을 바탕으로 핵심 키워드 표를 작성하세요.\n\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n\n"
        "형식: | 카테고리명 | `키워드1` `키워드2` `키워드3` |\n"
        "헤더와 구분선 없이 데이터 행만 작성하세요."
    )
    result = _gemini_json(prompt, _TRENDS_SCHEMA, 600)
    return result.get("trends", "")
