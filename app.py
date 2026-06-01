import base64
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from analyzers.claude_analyzer import analyze_report
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

    if "data-notification-status" not in html and "</nav>" in html:
        html = html.replace(
            "</nav>",
            '<div class="notification-status" data-notification-status hidden></div></nav>',
            1,
        )

    if "/static/notifications.js" not in html and "</body>" in html:
        html = html.replace(
            "</body>",
            '<script src="/static/notifications.js"></script>\n</body>',
            1,
        )

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
        with _lock:
            _state["steps_done"] = 1
            _state["step"] = "AI 분석 중... (전체 기사 1회 처리)"

        try:
            report_analysis = analyze_report(category_data)
            analysis_by_cat = {
                item.get("id"): item.get("articles", [])
                for item in report_analysis.get("categories", [])
            }

            for cat in category_data:
                by_index = {
                    item.get("index"): item
                    for item in analysis_by_cat.get(cat["id"], [])
                }
                for i, article in enumerate(cat["articles"]):
                    item = by_index.get(i, {})
                    article["analysis"] = {
                        "title": article["title"],
                        "summary": item.get("summary", ""),
                        "explanation": item.get("explanation", ""),
                    }

            trends = report_analysis.get("trends", "")
        except Exception as exc:
            trends = f"트렌드 분석을 불러오지 못했습니다.\n\n오류: {str(exc)[:200]}"
            for cat in category_data:
                for article in cat["articles"]:
                    article["analysis"] = {
                        "title": article["title"],
                        "summary": "",
                        "explanation": f"분석 중 오류가 발생했습니다: {str(exc)[:120]}",
                    }

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

        now_str = datetime.now(tz=KST).strftime("%m월 %d일 오전 9시")
        _send_push(
            "오늘의 뉴스 리포트가 준비됐습니다",
            f"{now_str} 기준 뉴스 리포트가 생성됐습니다. 앱에서 확인하세요.",
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
            "has_google_api_key": bool(os.environ.get("GOOGLE_API_KEY")),
            "has_vapid_private_key": bool(os.environ.get("VAPID_PRIVATE_KEY_B64")),
            "has_vapid_public_key": bool(os.environ.get("VAPID_PUBLIC_KEY")),
        }
    )


@app.route("/test-api")
def test_api():
    if not os.environ.get("GOOGLE_API_KEY"):
        return jsonify({"ok": False, "error": "GOOGLE_API_KEY 환경변수가 없습니다."})

    try:
        import google.generativeai as genai

        from config import GEMINI_MODEL

        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        model = genai.GenerativeModel(GEMINI_MODEL)
        response = model.generate_content(
            "한국어로 'API 정상'이라고만 답하세요.",
            request_options={"timeout": 30},
        )
        return jsonify({"ok": True, "response": response.text})
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


@app.route("/subscribe", methods=["POST"])
def subscribe():
    sub = request.get_json()
    if not sub or "endpoint" not in sub:
        return jsonify({"ok": False}), 400

    if not any(saved.get("endpoint") == sub["endpoint"] for saved in _subscriptions):
        _subscriptions.append(sub)
        _save_subs()

    test_sent, _ = _send_push_to_subscription(
        sub,
        "알림 설정 완료",
        "이제 매일 오전 9시 뉴스 리포트가 생성되면 알림을 보내드릴게요.",
    )

    return jsonify({"ok": True, "test_sent": test_sent})


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    endpoint = (request.get_json() or {}).get("endpoint")
    global _subscriptions
    _subscriptions = [sub for sub in _subscriptions if sub.get("endpoint") != endpoint]
    _save_subs()
    return jsonify({"ok": True})


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

    if _has_today_report():
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
