import sys
import threading
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, send_file, redirect
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from collectors.naver_collector import collect_all
from analyzers.claude_analyzer import analyze_article, analyze_trends
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
KST = ZoneInfo("Asia/Seoul")
app = Flask(__name__, template_folder="templates")

# 분석 상태 공유 객체
_state = {
    "running": False,
    "step": "",
    "steps_done": 0,
    "steps_total": 4,
    "error": None,
    "last_report": None,
}
_lock = threading.Lock()


def _latest_report_path() -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    reports = sorted(REPORTS_DIR.glob("report_*.html"), reverse=True)
    return reports[0] if reports else None


def _run_pipeline():
    global _state
    try:
        with _lock:
            _state.update({"running": True, "error": None, "steps_done": 0})

        # Step 1 — 뉴스 수집
        with _lock: _state["step"] = "뉴스 수집 중..."
        category_data = collect_all()
        with _lock: _state["steps_done"] = 1

        # Step 2 — 기사별 AI 분석 (카테고리 순차, 기사 병렬)
        total_cats = len([c for c in category_data if c["articles"]])
        done_cats = 0
        for cat in category_data:
            if not cat["articles"]:
                continue
            with _lock:
                _state["step"] = f"AI 분석 중... ({done_cats + 1}/{total_cats}) {cat['name']}"

            results = {}

            def _safe_analyze(idx, art, cat_name):
                try:
                    results[idx] = analyze_article(cat_name, art)
                except Exception as e:
                    results[idx] = {"title": art["title"], "summary": "", "explanation": ""}

            threads = [
                threading.Thread(target=_safe_analyze, args=(i, art, cat["name"]))
                for i, art in enumerate(cat["articles"])
            ]
            for t in threads: t.start()
            for t in threads: t.join(timeout=90)  # 기사당 최대 90초

            for i, article in enumerate(cat["articles"]):
                article["analysis"] = results.get(i, {})

            done_cats += 1

        with _lock: _state["steps_done"] = 2

        # Step 3 — 트렌드 분석
        with _lock: _state["step"] = "트렌드 분석 중..."
        try:
            trends = analyze_trends(category_data)
        except Exception:
            trends = "트렌드 분석을 불러오지 못했습니다."
        with _lock: _state["steps_done"] = 3

        # Step 4 — 저장
        with _lock: _state["step"] = "리포트 저장 중..."
        html_path = html_reporter.generate(category_data, trends, REPORTS_DIR)
        md_reporter.generate(category_data, trends, REPORTS_DIR)
        with _lock:
            _state["steps_done"] = 4
            _state["step"] = "완료!"
            _state["last_report"] = str(html_path)
            _state["running"] = False

    except Exception as e:
        with _lock:
            _state["running"] = False
            _state["step"] = "오류 발생"
            _state["error"] = str(e)


# ── 라우트 ─────────────────────────────────

@app.route("/debug")
def debug():
    from flask import jsonify
    return jsonify({
        "REPORTS_DIR": str(REPORTS_DIR),
        "exists": REPORTS_DIR.exists(),
        "files": [str(p) for p in REPORTS_DIR.glob("*.html")] if REPORTS_DIR.exists() else [],
        "latest": str(_latest_report_path()),
    })


def _is_today_report(path: Path) -> bool:
    """리포트가 오늘(KST) 생성됐는지 확인"""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=KST)
    return mtime.date() == datetime.now(tz=KST).date()


@app.route("/")
def index():
    report_path = _latest_report_path()

    # 오늘 리포트가 이미 있고 분석 중이 아니면 바로 리포트로 이동
    from flask import request as freq
    force = freq.args.get("force")
    if report_path and _is_today_report(report_path) and not _state["running"] and not force:
        return redirect("/report")

    report_date = None
    if report_path:
        mtime = report_path.stat().st_mtime
        report_date = datetime.fromtimestamp(mtime).strftime("%Y년 %m월 %d일 %H:%M")
    return render_template("home.html",
                           report_date=report_date,
                           state=_state)


@app.route("/analyze", methods=["POST"])
def analyze():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "이미 분석이 진행 중입니다."})
    t = threading.Thread(target=_run_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/status")
def status():
    with _lock:
        return jsonify(dict(_state))


@app.route("/report")
def report():
    path = _latest_report_path()
    if not path:
        return redirect("/")
    return send_file(path)


@app.route("/trigger")
def trigger():
    """UptimeRobot / cron-job.org 등 외부 서비스에서 호출하는 자동 실행 엔드포인트"""
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "already running"})

    # 오늘 이미 생성된 리포트가 있으면 스킵
    path = _latest_report_path()
    if path and _is_today_report(path):
        return jsonify({"ok": False, "msg": "today report already exists"})

    t = threading.Thread(target=_run_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True, "msg": "pipeline started"})


def _scheduled_run():
    """APScheduler가 오전 10시(KST)에 호출"""
    with _lock:
        if _state["running"]:
            return
    path = _latest_report_path()
    if path and _is_today_report(path):
        return
    t = threading.Thread(target=_run_pipeline, daemon=True)
    t.start()


# ── 스케줄러 (매일 오전 10시 KST) ──────────────
scheduler = BackgroundScheduler(timezone=KST)
scheduler.add_job(_scheduled_run, "cron", hour=10, minute=0)
scheduler.start()


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print("=" * 45)
    print("  Today's Main News — 웹 서버 시작")
    print(f"  http://localhost:{port}  으로 접속하세요")
    print("=" * 45)
    app.run(host="0.0.0.0", port=port, debug=False)
