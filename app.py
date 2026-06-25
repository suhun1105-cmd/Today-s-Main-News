import base64
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from analyzers.claude_analyzer import analyze_article, analyze_trends_only
from collectors.naver_collector import collect_all
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
SUBS_FILE = Path(__file__).resolve().parent / "subscriptions.json"
KST = ZoneInfo("Asia/Seoul")
WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "suhun1105-cmd/Today-s-Main-News")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
REPORT_RETENTION_DAYS = 7
TREND_COLOR_STYLE = """
<style id="trend-color-style">
.trend-content tbody tr:nth-child(1) td{background:#eff6ff!important;border-color:#bfdbfe!important}.trend-content tbody tr:nth-child(2) td{background:#f0fdf4!important;border-color:#bbf7d0!important}.trend-content tbody tr:nth-child(3) td{background:#fff7ed!important;border-color:#fed7aa!important}.trend-content tbody tr:nth-child(4) td{background:#fdf2f8!important;border-color:#fbcfe8!important}.trend-content tbody tr:nth-child(5) td{background:#f5f3ff!important;border-color:#ddd6fe!important}.trend-content tbody tr:nth-child(6) td{background:#ecfeff!important;border-color:#a5f3fc!important}.trend-content tbody tr:nth-child(1) td:first-child,.trend-content tbody tr:nth-child(1) code,.trend-content tbody tr:nth-child(1) .keyword-link{color:#1d4ed8!important}.trend-content tbody tr:nth-child(2) td:first-child,.trend-content tbody tr:nth-child(2) code,.trend-content tbody tr:nth-child(2) .keyword-link{color:#15803d!important}.trend-content tbody tr:nth-child(3) td:first-child,.trend-content tbody tr:nth-child(3) code,.trend-content tbody tr:nth-child(3) .keyword-link{color:#c2410c!important}.trend-content tbody tr:nth-child(4) td:first-child,.trend-content tbody tr:nth-child(4) code,.trend-content tbody tr:nth-child(4) .keyword-link{color:#be185d!important}.trend-content tbody tr:nth-child(5) td:first-child,.trend-content tbody tr:nth-child(5) code,.trend-content tbody tr:nth-child(5) .keyword-link{color:#6d28d9!important}.trend-content tbody tr:nth-child(6) td:first-child,.trend-content tbody tr:nth-child(6) code,.trend-content tbody tr:nth-child(6) .keyword-link{color:#0e7490!important}.trend-content code,.trend-content .keyword-link{background:rgba(255,255,255,.72)!important;border:1px solid rgba(148,163,184,.3)!important;cursor:pointer}.trend-content .keyword-link{border-radius:999px;padding:2px 10px;font:inherit;font-size:.78rem;font-weight:600;margin:2px 3px;white-space:nowrap}.trend-content code:hover,.trend-content .keyword-link:hover{background:#fff!important;box-shadow:0 4px 10px rgba(15,23,42,.12)}@media(max-width:768px){.trend-content tbody tr:nth-child(1) td:first-child{background:#dbeafe!important}.trend-content tbody tr:nth-child(2) td:first-child{background:#dcfce7!important}.trend-content tbody tr:nth-child(3) td:first-child{background:#ffedd5!important}.trend-content tbody tr:nth-child(4) td:first-child{background:#fce7f3!important}.trend-content tbody tr:nth-child(5) td:first-child{background:#ede9fe!important}.trend-content tbody tr:nth-child(6) td:first-child{background:#cffafe!important}}
</style>
"""
KEYWORD_LINK_SCRIPT = """
<script id="keyword-link-script">
(function(){
  function norm(v){return (v||'').replace(/[^\\w가-힣/]/g,'').toLowerCase();}
  function cardFor(name){
    const n=norm(name);
    return Array.from(document.querySelectorAll('.cat-card')).find(card=>{
      const label=card.dataset.catName||card.querySelector('.cat-name')?.textContent||'';
      return norm(label).includes(n);
    });
  }
  function bind(){
    document.querySelectorAll('.trend-content tr').forEach(row=>{
      const name=row.querySelector('td:first-child,th:first-child')?.textContent.trim();
      const card=cardFor(name);
      if(!card)return;
      row.querySelectorAll('code,.keyword-link').forEach(el=>{
        if(el.dataset.keywordBound)return;
        el.dataset.keywordBound='1';
        el.setAttribute('title', name+' 기사 설명으로 이동');
        el.addEventListener('click',()=>{
          card.classList.add('open');
          (card.querySelector('.art-block-explain')||card).scrollIntoView({behavior:'smooth',block:'center'});
        });
      });
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind);else bind();
})();
</script>
"""

app = Flask(__name__, template_folder="templates")

_subscriptions: list[dict] = []
_lock = threading.Lock()
_state = {
    "running": False,
    "step": "",
    "steps_done": 0,
    "steps_total": 4,
    "error": None,
    "last_report": None,
}


def _github_enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _github_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github_contents_url(path: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"


def _label_for_date(date_key: str) -> str:
    report_date = datetime.strptime(date_key, "%Y%m%d")
    weekday = WEEKDAYS_KO[report_date.weekday()]
    return f"{date_key[:4]}년 {date_key[4:6]}월 {date_key[6:]}일 {weekday}"


def _today_label() -> str:
    now = datetime.now(tz=KST)
    return f"{now:%Y년 %m월 %d일} {WEEKDAYS_KO[now.weekday()]}"


def _is_broken_html(content: str) -> bool:
    broken_markers = [
        "models/gemini-1.5-flash is not found",
        "트렌드 분석을 불러오지 못했습니다",
        "분석 중 오류가 발생했습니다",
        "429 You exceeded your current quota",
    ]
    return any(marker in content for marker in broken_markers)


def _fallback_article_analysis(cat_name: str, title: str) -> dict:
    return {
        "title": title,
        "summary": (
            f"{cat_name} 분야의 주요 기사입니다. 제목상으로는 '{title}'와 관련된 사안이 "
            "오늘 주요 뉴스로 다뤄지고 있습니다. 구체적인 세부 내용은 원문 확인이 필요하지만, "
            "해당 분야에서 관심 있게 볼 만한 변화나 논의가 생긴 것으로 볼 수 있습니다."
        ),
        "explanation": (
            "쉽게 말해, 이 기사는 제목에 나온 사건이나 변화가 왜 뉴스가 되는지를 살펴보는 내용입니다. "
            "현재 앱은 기사 본문 전체가 아니라 제목을 바탕으로 설명을 만들기 때문에, 세부 사실은 원문 기사에서 확인하는 것이 좋습니다. "
            f"이 뉴스가 중요한 이유는 {cat_name} 분야의 흐름을 이해하는 데 도움이 되기 때문입니다. "
            "어떤 사람이나 기관이 관련되어 있고, 그 일이 앞으로 어떤 영향을 줄 수 있는지 생각해보면 뉴스를 더 쉽게 이해할 수 있습니다. "
            "정확한 판단을 위해서는 기사 제목을 누른 뒤 원문을 함께 확인하는 것이 좋습니다."
        ),
    }


def _fallback_trends(category_data: list[dict]) -> str:
    lines = ["## 카테고리별 핵심 키워드"]
    for cat in category_data:
        titles = [article.get("title", "") for article in cat["articles"] if article.get("title")]
        words = []
        for title in titles:
            for token in re.findall(r"[가-힣A-Za-z0-9/]{2,}", title):
                if token not in words:
                    words.append(token)
                if len(words) >= 3:
                    break
            if len(words) >= 3:
                break
        while len(words) < 3:
            words.append(cat["name"])
        keyword_text = " ".join(f"`{word}`" for word in words[:3])
        lines.append(f"| {cat['name']} | {keyword_text} |")
    return "\n".join(lines)


def _load_subs() -> None:
    global _subscriptions
    if not SUBS_FILE.exists():
        return
    try:
        _subscriptions = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        _subscriptions = []


def _save_subs() -> None:
    try:
        SUBS_FILE.write_text(json.dumps(_subscriptions), encoding="utf-8")
    except Exception:
        pass


def _send_push_to_subscription(sub: dict, title: str, body: str) -> tuple[bool, bool]:
    from pywebpush import WebPushException, webpush

    private_key_b64 = os.environ.get("VAPID_PRIVATE_KEY_B64", "")
    if not private_key_b64:
        return False, False

    private_key = base64.b64decode(private_key_b64).decode("utf-8")
    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)

    try:
        webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": "mailto:news@example.com"},
        )
        return True, False
    except WebPushException as exc:
        expired = bool(exc.response and exc.response.status_code in (404, 410))
        return False, expired
    except Exception:
        return False, False


def _send_push(title: str, body: str) -> None:
    if not _subscriptions:
        return

    expired = []

    for sub in list(_subscriptions):
        sent, is_expired = _send_push_to_subscription(sub, title, body)
        if not sent and is_expired:
            expired.append(sub)

    for sub in expired:
        if sub in _subscriptions:
            _subscriptions.remove(sub)
    if expired:
        _save_subs()


def _local_report_path_for_date(date_key: str) -> Path | None:
    if not re.fullmatch(r"\d{8}", date_key or ""):
        return None
    path = REPORTS_DIR / f"report_{date_key}.html"
    return path if path.exists() else None


def _local_report_entries() -> list[dict]:
    if not REPORTS_DIR.exists():
        return []

    entries = []
    for path in sorted(REPORTS_DIR.glob("report_*.html"), reverse=True):
        match = re.fullmatch(r"report_(\d{8})\.html", path.name)
        if not match:
            continue
        date_key = match.group(1)
        entries.append(
            {"date": date_key, "label": _label_for_date(date_key), "source": "local"}
        )
    return entries


def _github_report_entries() -> list[dict]:
    if not _github_enabled():
        return []

    try:
        resp = httpx.get(
            _github_contents_url("reports"),
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        entries = []
        for item in resp.json():
            match = re.fullmatch(r"report_(\d{8})\.html", item.get("name", ""))
            if not match:
                continue
            date_key = match.group(1)
            entries.append(
                {
                    "date": date_key,
                    "label": _label_for_date(date_key),
                    "source": "github",
                }
            )
        return entries
    except Exception:
        return []


def _github_report_files() -> list[dict]:
    if not _github_enabled():
        return []

    try:
        resp = httpx.get(
            _github_contents_url("reports"),
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        files = []
        for item in resp.json():
            match = re.fullmatch(r"report_(\d{8})\.html", item.get("name", ""))
            if match:
                files.append({"date": match.group(1), "path": item["path"], "sha": item["sha"]})
        return files
    except Exception:
        return []


def _report_entries() -> list[dict]:
    merged: dict[str, dict] = {}
    for entry in _local_report_entries():
        merged[entry["date"]] = entry
    for entry in _github_report_entries():
        merged[entry["date"]] = entry
    return sorted(merged.values(), key=lambda item: item["date"], reverse=True)


def _github_report_html(date_key: str) -> str | None:
    if not _github_enabled() or not re.fullmatch(r"\d{8}", date_key or ""):
        return None

    try:
        resp = httpx.get(
            _github_contents_url(f"reports/report_{date_key}.html"),
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        encoded = data.get("content", "")
        return base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return None


def _prepare_report_html(html: str) -> str:
    html = html.replace(' onclick="toggleNotif()"', "")
    html = html.replace(" onclick='toggleNotif()'", "")
    html = re.sub(r'<div class=["\']header-pills["\'][\s\S]*?</div>', "", html)
    html = re.sub(
        r'<p class=["\']header-sub["\']>[\s\S]*?</p>',
        "",
        html,
        count=1,
    )
    html = re.sub(
        r'<footer class=["\']site-footer["\'][\s\S]*?</footer>',
        '<footer class="site-footer"><strong>Today\'s Main News</strong> &nbsp;·&nbsp; <a href="/" style="color:var(--accent);text-decoration:none;">📅 다른 날짜 보기</a></footer>',
        html,
        count=1,
    )
    html = re.sub(
        r'<button[^>]*(id=["\']notifBtn["\']|data-notification-button)[\s\S]*?</button>',
        "",
        html,
    )
    html = re.sub(
        r'<div class=["\']notification-status["\'][^>]*data-notification-status[^>]*></div>',
        "",
        html,
    )
    html = html.replace('<script src="/static/notifications.js"></script>', "")
    html = re.sub(
        r"##\s*1\.\s*오늘의 주요\s*(이슈|뉴스)[\s\S]*?(?=##\s*2\.\s*카테고리별 핵심 키워드)",
        "",
        html,
    )
    html = re.sub(r"##\s*2\.\s*카테고리별 핵심 키워드", "## 카테고리별 핵심 키워드", html)
    if "trend-color-style" not in html and "</head>" in html:
        html = html.replace("</head>", f"{TREND_COLOR_STYLE}\n</head>", 1)
    if "keyword-link-script" not in html and "</body>" in html:
        html = html.replace("</body>", f"{KEYWORD_LINK_SCRIPT}\n</body>", 1)

    return html


def _save_report_to_github(date_key: str, html: str) -> None:
    if not _github_enabled() or _is_broken_html(html):
        return

    path = f"reports/report_{date_key}.html"
    sha = None

    try:
        existing = httpx.get(
            _github_contents_url(path),
            headers=_github_headers(),
            params={"ref": GITHUB_BRANCH},
            timeout=15,
        )
        if existing.status_code == 200:
            sha = existing.json().get("sha")
    except Exception:
        sha = None

    payload = {
        "message": f"Add news report {date_key}",
        "content": base64.b64encode(html.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    try:
        httpx.put(
            _github_contents_url(path),
            headers=_github_headers(),
            json=payload,
            timeout=30,
        ).raise_for_status()
    except Exception:
        pass


def _cleanup_old_github_reports() -> None:
    if not _github_enabled():
        return

    cutoff = (datetime.now(tz=KST) - timedelta(days=REPORT_RETENTION_DAYS - 1)).strftime("%Y%m%d")
    for item in _github_report_files():
        if item["date"] >= cutoff:
            continue
        try:
            httpx.delete(
                _github_contents_url(item["path"]),
                headers=_github_headers(),
                json={
                    "message": f"Remove old news report {item['date']}",
                    "sha": item["sha"],
                    "branch": GITHUB_BRANCH,
                },
                timeout=30,
            ).raise_for_status()
        except Exception:
            pass


def _latest_report_date() -> str | None:
    entries = _report_entries()
    return entries[0]["date"] if entries else None


def _has_today_report() -> bool:
    today_key = datetime.now(tz=KST).strftime("%Y%m%d")

    html = _github_report_html(today_key)
    if html and not _is_broken_html(html):
        return True

    path = _local_report_path_for_date(today_key)
    if not path:
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return not _is_broken_html(content)


def _run_pipeline() -> None:
    try:
        with _lock:
            _state.update({"running": True, "error": None, "steps_done": 0})
            _state["step"] = "뉴스 수집 중..."

        category_data = collect_all()

        total = sum(len(cat["articles"]) for cat in category_data)
        done = 0
        with _lock:
            _state["steps_done"] = 1
            _state["steps_total"] = total + 3
            _state["step"] = f"AI 분석 중... (0/{total})"

        # 기사 1개씩 순차 호출 — 배치 호출 대비 토큰·레이트리밋 부담 최소화
        for cat in category_data:
            for article in cat["articles"]:
                fallback = _fallback_article_analysis(cat["name"], article["title"])
                try:
                    result = analyze_article(cat["name"], article)
                    article["analysis"] = {
                        "title": article["title"],
                        "summary": result.get("summary") or fallback["summary"],
                        "explanation": result.get("explanation") or fallback["explanation"],
                    }
                except Exception:
                    article["analysis"] = fallback
                done += 1
                with _lock:
                    _state["steps_done"] = 1 + done
                    _state["step"] = f"AI 분석 중... ({done}/{total})"
                if done < total:
                    time.sleep(1)

        with _lock:
            _state["step"] = "트렌드 분석 중..."
        try:
            trends = analyze_trends_only(category_data) or _fallback_trends(category_data)
        except Exception:
            trends = _fallback_trends(category_data)

        with _lock:
            _state["steps_done"] = 3
            _state["step"] = "리포트 저장 중..."

        html_path = html_reporter.generate(category_data, trends, REPORTS_DIR)
        md_reporter.generate(category_data, trends, REPORTS_DIR)
        date_key = datetime.now(tz=KST).strftime("%Y%m%d")
        html = html_path.read_text(encoding="utf-8")
        _save_report_to_github(date_key, html)
        _cleanup_old_github_reports()

        with _lock:
            _state["steps_done"] = 4
            _state["step"] = "완료!"
            _state["last_report"] = str(html_path)
            _state["running"] = False

        _send_push(
            "Today's Main News",
            "오늘의 주요 뉴스를 확인하세요!",
        )

    except Exception as exc:
        with _lock:
            _state["running"] = False
            _state["step"] = "오류 발생"
            _state["error"] = str(exc)


def _scheduled_run() -> None:
    with _lock:
        if _state["running"]:
            return

    if _has_today_report():
        return

    threading.Thread(target=_run_pipeline, daemon=True).start()


_load_subs()
scheduler = BackgroundScheduler(timezone=KST)
scheduler.add_job(_scheduled_run, "cron", hour=9, minute=0)
scheduler.start()


@app.route("/debug")
def debug():
    latest = _latest_report_date()
    return jsonify(
        {
            "reports_dir": str(REPORTS_DIR),
            "reports_dir_exists": REPORTS_DIR.exists(),
            "reports": _report_entries(),
            "latest": latest,
            "has_today_report": _has_today_report(),
            "subscriptions": len(_subscriptions),
            "github_enabled": _github_enabled(),
            "github_repo": GITHUB_REPO,
            "github_branch": GITHUB_BRANCH,
            "has_gemini_api_key": bool(os.environ.get("GEMINI_API_KEY")),
            "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            "has_vapid_private_key": bool(os.environ.get("VAPID_PRIVATE_KEY_B64")),
            "has_vapid_public_key": bool(os.environ.get("VAPID_PUBLIC_KEY")),
        }
    )


@app.route("/test-collect")
def test_collect():
    from collectors.naver_collector import _fetch_category
    from config import NAVER_CATEGORIES

    cat = NAVER_CATEGORIES[1]  # 경제 카테고리로 테스트
    result = _fetch_category(cat)
    return jsonify({
        "category": result["name"],
        "error": result.get("error"),
        "articles": [
            {
                "title": a["title"],
                "snippet_len": len(a.get("summary", "")),
                "snippet": a.get("summary", ""),
                "body_len": len(a.get("body", "")),
                "body_preview": a.get("body", "")[:300],
            }
            for a in result["articles"]
        ],
    })


@app.route("/test-analyze")
def test_analyze():
    """수집 + AI 분석 end-to-end 테스트 (기사 1건)"""
    from collectors.naver_collector import _fetch_category
    from analyzers.claude_analyzer import analyze_article
    from config import NAVER_CATEGORIES

    cat = NAVER_CATEGORIES[1]
    result = _fetch_category(cat)
    if result.get("error") or not result["articles"]:
        return jsonify({"ok": False, "error": result.get("error", "기사 없음")})

    article = result["articles"][0]
    try:
        analysis = analyze_article(cat["name"], article)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})

    return jsonify({
        "ok": True,
        "title": article["title"],
        "snippet": article.get("summary", ""),
        "body_len": len(article.get("body", "")),
        "body_preview": article.get("body", "")[:400],
        "summary": analysis.get("summary", ""),
        "explanation": analysis.get("explanation", ""),
    })


@app.route("/test-api")
def test_api():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return jsonify({"ok": False, "error": "GEMINI_API_KEY 환경변수가 없습니다."})

    try:
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        response = httpx.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json={"contents": [{"role": "user", "parts": [{"text": "한국어로 'API 정상'이라고만 답하세요."}]}]},
            timeout=30,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"ok": True, "response": text, "model": model})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/")
def index():
    reports = _report_entries()
    latest = reports[0] if reports else None
    report_date = latest["label"] if latest else None
    return render_template(
        "home.html",
        report_date=report_date,
        reports=reports,
        today_label=_today_label(),
        state=_state,
    )


@app.route("/analyze", methods=["POST"])
def analyze():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "이미 분석이 진행 중입니다."})

    threading.Thread(target=_run_pipeline, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with _lock:
        return jsonify(dict(_state))


@app.route("/report")
def report():
    date_key = request.args.get("date") or _latest_report_date()
    if not date_key:
        return redirect("/")

    html = _github_report_html(date_key)
    if html:
        return Response(_prepare_report_html(html), mimetype="text/html; charset=utf-8")

    path = _local_report_path_for_date(date_key)
    if not path:
        return redirect("/")
    html = path.read_text(encoding="utf-8", errors="ignore")
    return Response(_prepare_report_html(html), mimetype="text/html; charset=utf-8")


@app.route("/vapid-public-key")
def vapid_public_key():
    return jsonify({"key": os.environ.get("VAPID_PUBLIC_KEY", "")})


@app.route("/sw.js")
def service_worker():
    response = send_file(
        Path(__file__).resolve().parent / "static" / "sw.js",
        mimetype="application/javascript; charset=utf-8",
    )
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    if not sub or "endpoint" not in sub:
        return jsonify({"ok": False}), 400

    if not any(saved.get("endpoint") == sub["endpoint"] for saved in _subscriptions):
        _subscriptions.append(sub)
        _save_subs()

    if request.args.get("test") == "1":
        threading.Thread(
            target=_send_push_to_subscription,
            args=(
                sub,
                "알림 설정 완료",
                "이제 매일 오전 9시 뉴스 리포트가 생성되면 알림을 보내드릴게요.",
            ),
            daemon=True,
        ).start()

    return jsonify({"ok": True, "test_queued": True})


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    endpoint = (request.get_json() or {}).get("endpoint")
    global _subscriptions
    _subscriptions = [sub for sub in _subscriptions if sub.get("endpoint") != endpoint]
    _save_subs()
    return jsonify({"ok": True})


@app.route("/test-push")
def test_push():
    _send_push(
        "News 알림 테스트",
        "알림 설정이 정상이라면 이 메시지가 핸드폰에 표시됩니다.",
    )
    return jsonify({"ok": True, "subscriptions": len(_subscriptions)})


@app.route("/trigger")
def trigger():
    force = request.args.get("force")
    if not force:
        return jsonify(
            {"ok": True, "msg": "awake", "now_kst": datetime.now(tz=KST).isoformat()}
        )

    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "already running"})

    if request.args.get("skip_existing") == "1" and _has_today_report():
        return jsonify({"ok": False, "msg": "today report already exists"})

    threading.Thread(target=_run_pipeline, daemon=True).start()
    return jsonify({"ok": True, "msg": "pipeline started"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 45)
    print("  Today's Main News - web server")
    print(f"  http://localhost:{port}")
    print("=" * 45)
    app.run(host="0.0.0.0", port=port, debug=False)
