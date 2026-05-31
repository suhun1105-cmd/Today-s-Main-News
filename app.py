import base64
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from analyzers.claude_analyzer import analyze_report
from collectors.naver_collector import collect_all
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
SUBS_FILE = Path(__file__).resolve().parent / "subscriptions.json"
KST = ZoneInfo("Asia/Seoul")
WEEKDAYS_KO = ["\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08", "\ud1a0", "\uc77c"]

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


def _send_push(title: str, body: str) -> None:
    from pywebpush import WebPushException, webpush

    private_key_b64 = os.environ.get("VAPID_PRIVATE_KEY_B64", "")
    if not private_key_b64 or not _subscriptions:
        return

    private_key = base64.b64decode(private_key_b64).decode("utf-8")
    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    expired = []

    for sub in list(_subscriptions):
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:news@example.com"},
            )
        except WebPushException as exc:
            if exc.response and exc.response.status_code in (404, 410):
                expired.append(sub)
        except Exception:
            pass

    for sub in expired:
        if sub in _subscriptions:
            _subscriptions.remove(sub)
    if expired:
        _save_subs()


def _report_path_for_date(date_key: str) -> Path | None:
    if not re.fullmatch(r"\d{8}", date_key or ""):
        return None
    path = REPORTS_DIR / f"report_{date_key}.html"
    return path if path.exists() else None


def _report_entries() -> list[dict]:
    if not REPORTS_DIR.exists():
        return []

    entries = []
    for path in sorted(REPORTS_DIR.glob("report_*.html"), reverse=True):
        match = re.fullmatch(r"report_(\d{8})\.html", path.name)
        if not match:
            continue
        date_key = match.group(1)
        report_date = datetime.strptime(date_key, "%Y%m%d")
        weekday = WEEKDAYS_KO[report_date.weekday()]
        label = f"{date_key[:4]}\ub144 {date_key[4:6]}\uc6d4 {date_key[6:]}\uc77c ({weekday})"
        entries.append({"date": date_key, "label": label, "path": str(path)})
    return entries


def _latest_report_path() -> Path | None:
    entries = _report_entries()
    if not entries:
        return None
    return _report_path_for_date(entries[0]["date"])


def _is_broken_report(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False

    broken_markers = [
        "models/gemini-1.5-flash is not found",
        "트렌드 분석을 불러오지 못했습니다",
        "분석 중 오류가 발생했습니다",
        "429 You exceeded your current quota",
    ]
    return any(marker in content for marker in broken_markers)


def _has_today_report(path: Path) -> bool:
    if _is_broken_report(path):
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=KST)
    return mtime.date() == datetime.now(tz=KST).date()


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

    path = _latest_report_path()
    if path and _has_today_report(path):
        return

    threading.Thread(target=_run_pipeline, daemon=True).start()


_load_subs()
scheduler = BackgroundScheduler(timezone=KST)
scheduler.add_job(_scheduled_run, "cron", hour=9, minute=0)
scheduler.start()


@app.route("/debug")
def debug():
    latest = _latest_report_path()
    return jsonify(
        {
            "reports_dir": str(REPORTS_DIR),
            "reports_dir_exists": REPORTS_DIR.exists(),
            "reports": _report_entries(),
            "latest": str(latest) if latest else None,
            "has_today_report": bool(latest and _has_today_report(latest)),
            "subscriptions": len(_subscriptions),
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
    date_key = request.args.get("date", "")
    path = _report_path_for_date(date_key) if date_key else _latest_report_path()
    if not path:
        return redirect("/")
    return send_file(path)


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

    return jsonify({"ok": True})


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    endpoint = (request.get_json() or {}).get("endpoint")
    global _subscriptions
    _subscriptions = [sub for sub in _subscriptions if sub.get("endpoint") != endpoint]
    _save_subs()
    return jsonify({"ok": True})


@app.route("/trigger")
def trigger():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "already running"})

    force = request.args.get("force")
    path = _latest_report_path()
    if path and _has_today_report(path) and not force:
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
