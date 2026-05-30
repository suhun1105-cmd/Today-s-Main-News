import sys
import threading
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, send_file, redirect

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from collectors.naver_collector import collect_all
from analyzers.claude_analyzer import analyze_article, analyze_trends
from reporters import html_reporter, md_reporter

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
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

        # Step 1
        with _lock: _state["step"] = "뉴스 수집 중..."
        category_data = collect_all()
        with _lock: _state["steps_done"] = 1

        # Step 2
        with _lock: _state["step"] = "기사 AI 분석 중..."
        for cat in category_data:
            if not cat["articles"]:
                continue
            with threading.Lock():
                pass
            results = {}
            threads = []
            for i, art in enumerate(cat["articles"]):
                def _job(idx=i, a=art, c=cat):
                    results[idx] = analyze_article(c["name"], a)
                t = threading.Thread(target=_job)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            for i, article in enumerate(cat["articles"]):
                article["analysis"] = results.get(i, {})
        with _lock: _state["steps_done"] = 2

        # Step 3
        with _lock: _state["step"] = "트렌드 분석 중..."
        trends = analyze_trends(category_data)
        with _lock: _state["steps_done"] = 3

        # Step 4
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


@app.route("/")
def index():
    report_path = _latest_report_path()
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


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print("=" * 45)
    print("  Today's Main News — 웹 서버 시작")
    print(f"  http://localhost:{port}  으로 접속하세요")
    print("=" * 45)
    app.run(host="0.0.0.0", port=port, debug=False)
