"""
Kaizen Voice — conversational continuous-improvement intake for the
Aditya Birla Grasim VSF (viscose staple fibre) plant.

Run:
    pip install -r requirements.txt
    python app.py
Then open in Chrome:
    http://localhost:5000/?name=Ramesh

The Anthropic API key stays on the server (never sent to the browser).
Set it via environment variable ANTHROPIC_API_KEY (recommended) or use the fallback.
"""

import os
import json
import re

import requests
from flask import Flask, request, jsonify, render_template, send_from_directory
import anthropic

# --- Keys: read ONLY from the environment (never commit a real key) ---
# Local dev:  set these in your shell. Render: add them as secret env vars.
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SARVAM_KEY = os.environ.get("SARVAM_API_KEY", "")
MODEL = "claude-sonnet-4-6"   # fast model for the short classify/reply turn (was claude-sonnet-4-6)

# Sarvam (Indian-language speech) settings
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_MODEL = "saaras:v3"      # current recommended ASR (was saarika:v2.5, legacy)
SARVAM_TTS_MODEL = "bulbul:v3"     # current TTS; required for the Shubh voice (was bulbul:v2)
SARVAM_SPEAKER = "aditya"           # confirmed-available male bulbul:v3 voice (shubh not enabled on this key; names are lowercase)
LANG_BCP47 = {"hi": "hi-IN", "gu": "gu-IN", "en": "en-IN", "ta": "ta-IN"}

if not API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. AI calls will fail until you set it.")
if not SARVAM_KEY:
    print("WARNING: SARVAM_API_KEY is not set. Voice (speech) will fail until you set it.")

client = anthropic.Anthropic(api_key=API_KEY)
app = Flask(__name__)
try:
    from flask_compress import Compress
    Compress(app)  # gzip JSON — /api/portal/all-ideas goes from ~3.3 MB to ~380 KB
except ImportError:
    print("flask-compress not installed — responses will be uncompressed")

DEPARTMENTS = [
    "Pulp", "Viscose", "Spinning", "Spin Bath / Acid", "Auxiliary & CS2",
    "Aftertreatment", "Quality Control", "Mechanical Maintenance",
    "Electrical Maintenance", "Instrumentation", "Power & Utilities",
    "Effluent / ETP", "Stores & Logistics", "Packing & Dispatch",
    "Safety", "Security", "HR / Admin",
]

LANG_NAMES = {"hi": "Hindi", "gu": "Gujarati", "en": "English", "ta": "Tamil"}


def system_prompt(lang_code, name):
    lang_name = LANG_NAMES.get(lang_code, "English")
    return f"""You are "Kaizen Voice", a warm, friendly continuous-improvement (Kaizen) intake assistant on the shop floor of the Aditya Birla Grasim viscose staple fibre (VSF) plant.

You are talking BY VOICE with {name}, a worker who may have no computer knowledge. Speak naturally and very simply in their language: {lang_name} (code: {lang_code}). Keep every spoken line short and kind.

YOUR GOAL: understand their improvement idea well enough to record it with a clear title, description, the right DEPARTMENT, and the right TIER. Then thank them warmly.

DEPARTMENTS (choose exactly one that fits best):
{", ".join(DEPARTMENTS)}

TIER RULES:
- Tier 1 = NO purchase cost; only in-house labour. e.g. relocating a rack, 5S cleanup, removing an obstruction, changing a procedure/SOP, re-labelling, adjusting a work sequence, fixing an awkward reach.
- Tier 2 = cost up to INR 1,00,000. e.g. safety posters/signage, a small guard or fixture, extra lighting, an ergonomic mat, a low-cost sensor, a step platform, minor tooling, painting/marking.
- Tier 3 = cost above INR 1,00,000. e.g. new equipment, machine modification, automation, a conveyor, a major safety/interlock system, any capital project.

CONVERSATION RULES:
- Ask AT MOST 1-2 short clarifying questions, and ONLY when truly needed (e.g. you cannot tell which department it belongs to, or the idea is too vague to judge the tier/cost). If the worker already gave enough, do NOT ask — go straight to final.
- Never ask more than one question at a time. Keep it casual and encouraging.
- If the worker's words are unclear, make a reasonable best guess rather than over-asking.

YOU MUST REPLY WITH STRICT JSON ONLY (no markdown, no extra text), one of these two shapes:

To ask a follow-up:
{{"action":"ask","speech":"<one short, simple question in {lang_name}>"}}

When you have enough to record it:
{{"action":"final",
"speech":"<a warm 1-2 sentence message in {lang_name}: thank {name} by name, appreciate them for taking part in Kaizen, and say their idea will help make the plant better and safer>",
"title":"<short idea title in English, max 8 words>",
"description":"<clean 1-2 sentence description in English>",
"department":"<exactly one department from the list>",
"tier":1,
"tier_reason":"<short reason in English>",
"cost_estimate":"<e.g. No cost / Up to INR 1 lakh / Above INR 1 lakh>"}}
"""


def extract_json(text):
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.I)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"```$", "", text).strip()
    # grab the outermost {...} if there is stray text
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            text = m.group(0)
    return json.loads(text)


# Root now opens the voice-integrated employee portal (the main experience).
@app.route("/")
@app.route("/employee")
@app.route("/portal")
def employee_portal():
    return send_from_directory(app.root_path, "Kaizen_User_Reviewer_Approver.html")


# Original standalone voice MVP kept available here.
@app.route("/mvp")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    """Lets the portal detect a live backend and choose Sarvam+Claude over the in-browser fallback."""
    try:
        con = db(); n = con.execute("SELECT COUNT(*) c FROM ideas").fetchone()["c"]; con.close()
    except Exception:
        n = None
    return jsonify({"ok": True, "anthropic": bool(API_KEY), "sarvam": bool(SARVAM_KEY), "ideas": n})


@app.route("/api/converse", methods=["POST"])
def converse():
    data = request.get_json(force=True)
    lang = data.get("lang", "hi")
    name = (data.get("name") or "Saathi").strip()
    messages = data.get("messages", [])  # [{role, content}, ...]

    if not messages:
        return jsonify({"error": "no messages"}), 400

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=system_prompt(lang, name),
            messages=messages,
        )
        raw = resp.content[0].text if resp.content else ""
        result = extract_json(raw)
        # safety: validate department / tier
        if result.get("action") == "final":
            if result.get("department") not in DEPARTMENTS:
                result["department"] = result.get("department") or "HR / Admin"
            try:
                result["tier"] = int(result.get("tier", 1))
            except (ValueError, TypeError):
                result["tier"] = 1
        return jsonify(result)
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/stt", methods=["POST"])
def stt():
    """Browser sends a short audio clip -> Sarvam transcribes -> we return text."""
    if "audio" not in request.files:
        return jsonify({"error": "no audio"}), 400
    lang = request.form.get("lang", "hi")
    bcp = LANG_BCP47.get(lang, "unknown")
    audio = request.files["audio"]
    try:
        r = requests.post(
            SARVAM_STT_URL,
            headers={"api-subscription-key": SARVAM_KEY},
            files={"file": (audio.filename or "audio.webm", audio.stream, audio.mimetype or "audio/webm")},
            data={"model": SARVAM_STT_MODEL, "language_code": bcp, "mode": "transcribe"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"SARVAM STT ERROR {r.status_code}: {r.text[:500]}", flush=True)
            return jsonify({"error": f"sarvam stt {r.status_code}: {r.text[:200]}"}), 502
        return jsonify({"transcript": r.json().get("transcript", "")})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


@app.route("/api/tts", methods=["POST"])
def tts():
    """We send the AI's reply text -> Sarvam returns base64 wav -> browser plays it."""
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    lang = data.get("lang", "hi")
    bcp = LANG_BCP47.get(lang, "hi-IN")
    if not text:
        return jsonify({"error": "no text"}), 400
    try:
        r = requests.post(
            SARVAM_TTS_URL,
            headers={"api-subscription-key": SARVAM_KEY, "Content-Type": "application/json"},
            json={
                "text": text[:2500],
                "target_language_code": bcp,
                "speaker": SARVAM_SPEAKER,
                "model": SARVAM_TTS_MODEL,
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"SARVAM TTS ERROR {r.status_code}: {r.text[:500]}", flush=True)
            return jsonify({"error": f"sarvam tts {r.status_code}: {r.text[:200]}"}), 502
        audios = r.json().get("audios", [])
        return jsonify({"audio": audios[0] if audios else ""})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


# ================= DATA LAYER (read-only, kaizen.db) =================
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "kaizen.db")

# DB sla_status -> portal pill keys
SLA_MAP = {"ok": "ok", "at_risk": "warn", "breached": "breach", "closed": "-"}
# 3-letter month labels for the "last 7 months" charts (Dec 2025 .. Jun 2026)
MONTH_ABBR = {12: "Dec", 1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun"}
MONTH_WINDOW_START = "2025-12-01"


def db():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def month_label(date_str):
    """3-letter label only for ideas inside the chart window, else None."""
    if not date_str or date_str < MONTH_WINDOW_START:
        return None
    try:
        return MONTH_ABBR.get(int(date_str[5:7]))
    except (ValueError, IndexError):
        return None


# Shared SELECT producing the portal idea shape. bs = realized cost savings (₹ L/yr).
IDEA_SELECT = """
SELECT i.idea_code AS id, i.title, d.name AS dept, t.name AS cat, ti.name AS tier,
       s.code AS stage, i.estimated_savings AS est, bs.v AS act, i.score,
       i.sla_status AS sla, i.submitted_date AS sd, u.name AS sub, p.name AS plant,
       i.endorsement_count AS endo, i.collaborator_count AS collab,
       COALESCE(cmt.c, 0) AS cm
FROM ideas i
JOIN departments d ON d.id = i.department_id
JOIN tracks t      ON t.id = i.track_id
JOIN tiers ti      ON ti.id = i.tier_id
JOIN stages s      ON s.id = i.current_stage_id
JOIN users u       ON u.id = i.submitter_id
JOIN plants p      ON p.id = i.plant_id
LEFT JOIN (SELECT idea_id, ROUND(SUM(quantified_value),1) v
           FROM idea_benefits WHERE benefit_type_id = 1 GROUP BY idea_id) bs
       ON bs.idea_id = i.id
LEFT JOIN (SELECT idea_id, COUNT(*) c FROM comments GROUP BY idea_id) cmt
       ON cmt.idea_id = i.id
"""


def shape(r):
    """dict row -> portal idea object."""
    return {
        "id": r["id"], "title": r["title"], "dept": r["dept"], "cat": r["cat"],
        "tier": r["tier"], "stage": r["stage"], "est": r["est"], "act": r["act"],
        "score": r["score"], "sla": SLA_MAP.get(r["sla"], "-"),
        "month": month_label(r["sd"]), "date": r["sd"], "sub": r["sub"],
        "plant": r["plant"], "endo": r["endo"], "collab": r["collab"], "cm": r["cm"],
    }


def plant_clause(plant, params, col="p.name"):
    if plant and plant != "All":
        params.append(plant)
        return f" AND {col} = ?"
    return ""


# ------------------------------------------------------------------ pages
@app.route("/manager")
def manager_console():
    return send_from_directory(app.root_path, "Kaizen_CI_Manager_Console.html")


# ------------------------------------------------------------------ meta / stats
@app.route("/api/meta")
def api_meta():
    con = db()
    data = {
        "plants":      rows(con.execute("SELECT id, name FROM plants ORDER BY id")),
        "departments": rows(con.execute("SELECT id, name FROM departments ORDER BY name")),
        "stages":      rows(con.execute("SELECT code, name FROM stages ORDER BY sort_order")),
        "tracks":      rows(con.execute("SELECT id, name FROM tracks ORDER BY name")),
    }
    con.close()
    return jsonify(data)


@app.route("/api/stats")
def api_stats():
    con = db()
    total = con.execute("SELECT COUNT(*) c FROM ideas").fetchone()["c"]
    implemented = con.execute("SELECT COUNT(*) c FROM ideas WHERE current_stage_id=5").fetchone()["c"]
    savings = con.execute("SELECT ROUND(SUM(quantified_value),0) s FROM idea_benefits WHERE benefit_type_id=1").fetchone()["s"]
    by_stage = rows(con.execute(
        "SELECT s.name, COUNT(*) c FROM ideas i JOIN stages s ON s.id=i.current_stage_id GROUP BY s.id ORDER BY s.sort_order"))
    by_plant = rows(con.execute(
        "SELECT p.name, COUNT(*) c FROM ideas i JOIN plants p ON p.id=i.plant_id GROUP BY p.id ORDER BY p.id"))
    con.close()
    return jsonify({"total": total, "implemented": implemented,
                    "savings_inr_lakh": savings, "by_stage": by_stage, "by_plant": by_plant})


# ------------------------------------------------------------------ generic filtered list
@app.route("/api/ideas")
def api_ideas():
    plant = request.args.get("plant")
    dept = request.args.get("department")
    stage = request.args.get("stage")
    track = request.args.get("track")
    q = request.args.get("q")
    limit = min(int(request.args.get("limit", 50)), 500)
    offset = int(request.args.get("offset", 0))

    where, params = ["1=1"], []
    if plant:
        where.append("p.name = ?"); params.append(plant)
    if dept:
        where.append("d.name = ?"); params.append(dept)
    if stage:
        where.append("s.code = ?"); params.append(stage)
    if track:
        where.append("t.name = ?"); params.append(track)
    if q:
        where.append("i.title LIKE ?"); params.append(f"%{q}%")
    wsql = " AND ".join(where)

    con = db()
    total = con.execute(
        f"""SELECT COUNT(*) c FROM ideas i
            JOIN plants p ON p.id=i.plant_id JOIN departments d ON d.id=i.department_id
            JOIN stages s ON s.id=i.current_stage_id JOIN tracks t ON t.id=i.track_id
            WHERE {wsql}""", params).fetchone()["c"]
    data = [shape(r) for r in rows(con.execute(
        IDEA_SELECT + f" WHERE {wsql} ORDER BY i.submitted_date DESC LIMIT ? OFFSET ?",
        params + [limit, offset]))]
    con.close()
    return jsonify({"total": total, "count": len(data), "ideas": data})


# ------------------------------------------------------------------ one idea (rich detail)
@app.route("/api/idea/<code>")
def api_idea_detail(code):
    con = db()
    idea = con.execute(
        """SELECT i.*, p.name AS plant, d.name AS department, s.code AS stage_code,
                  s.name AS stage, t.name AS track, ti.name AS tier_name, u.name AS submitter
           FROM ideas i
           JOIN plants p ON p.id=i.plant_id JOIN departments d ON d.id=i.department_id
           JOIN stages s ON s.id=i.current_stage_id JOIN tracks t ON t.id=i.track_id
           JOIN tiers ti ON ti.id=i.tier_id JOIN users u ON u.id=i.submitter_id
           WHERE i.idea_code = ?""", [code]).fetchone()
    if not idea:
        con.close()
        return jsonify({"error": "not found"}), 404
    iid = idea["id"]
    outcome = con.execute("SELECT * FROM idea_outcomes WHERE idea_id=?", [iid]).fetchone()
    benefits = rows(con.execute(
        """SELECT bt.name AS benefit, b.quantified_value, b.unit, b.narrative
           FROM idea_benefits b JOIN benefit_types bt ON bt.id=b.benefit_type_id
           WHERE b.idea_id=?""", [iid]))
    history = rows(con.execute(
        """SELECT w.action, w.comment, w.ts, u.name AS actor,
                  sf.name AS from_stage, st.name AS to_stage
           FROM workflow_history w
           LEFT JOIN users u ON u.id=w.actor_id
           LEFT JOIN stages sf ON sf.id=w.from_stage_id
           LEFT JOIN stages st ON st.id=w.to_stage_id
           WHERE w.idea_id=? ORDER BY w.ts""", [iid]))
    evals = rows(con.execute(
        """SELECT u.name AS evaluator, e.total_score, e.decision, e.comment, e.date,
               e.impact_score, e.effort_score, e.feasibility_score
           FROM evaluations e JOIN users u ON u.id=e.evaluator_id
           WHERE e.idea_id=? ORDER BY e.date""", [iid]))
    cls = rows(con.execute(
        """SELECT label, value, confidence FROM idea_classifications
           WHERE idea_id=? ORDER BY id""", [iid]))
    tasks = rows(con.execute(
        """SELECT t.seq, t.title, t.phase, u.name AS assignee, t.due_date, t.status,
                  t.blocked_reason
           FROM implementation_tasks t LEFT JOIN users u ON u.id=t.assignee_id
           WHERE t.idea_id=? ORDER BY t.seq""", [iid]))
    apprs = rows(con.execute(
        """SELECT u.name AS approver, a.decision, a.comment, a.date
           FROM approvals a JOIN users u ON u.id=a.approver_id
           WHERE a.idea_id=? ORDER BY a.date""", [iid]))
    comments = rows(con.execute(
        """SELECT u.name AS author, c.text, c.ts FROM comments c
           JOIN users u ON u.id=c.user_id WHERE c.idea_id=? ORDER BY c.ts DESC LIMIT 10""", [iid]))
    att = con.execute(
        """SELECT SUM(CASE WHEN type IN ('photo','image') THEN 1 ELSE 0 END) photos,
                  COUNT(*) total FROM idea_attachments WHERE idea_id=?""", [iid]).fetchone()
    collaborators = rows(con.execute(
        """SELECT u.name, c.role, c.joined_date FROM collaborators c
           JOIN users u ON u.id=c.user_id WHERE c.idea_id=?""", [iid]))
    con.close()
    return jsonify({"idea": dict(idea), "outcome": dict(outcome) if outcome else None,
                    "benefits": benefits, "history": history, "evaluations": evals,
                    "approvals": apprs, "comments": comments, "collaborators": collaborators,
                    "classifications": cls, "tasks": tasks,
                    "attachments": {"photos": att["photos"] or 0, "total": att["total"] or 0}})


# ------------------------------------------------------------------ console: ALL ideas (compact)
@app.route("/api/portal/all-ideas")
def api_all_ideas():
    """Compact array-of-arrays of every idea (optionally per plant) for the
    CI-manager console, whose charts aggregate client-side.
    Row: [id,title,dept,cat,tier,stage,est,act,score,sla,month,sub,plant,endo,cm]
    ~3.3 MB raw / ~380 KB gzipped for all 20,793 ideas."""
    plant = request.args.get("plant")
    params = []
    wsql = "1=1" + plant_clause(plant, params)
    con = db()
    data = [[r["id"], r["title"], r["dept"], r["cat"], r["tier"], r["stage"],
             r["est"], r["act"], r["score"], SLA_MAP.get(r["sla"], "-"),
             month_label(r["sd"]), r["sub"], r["plant"], r["endo"], r["cm"], r["collab"]]
            for r in con.execute(IDEA_SELECT + f" WHERE {wsql}", params)]
    con.close()
    return jsonify({"count": len(data),
                    "cols": ["id", "title", "dept", "cat", "tier", "stage", "est", "act",
                             "score", "sla", "month", "sub", "plant", "endo", "cm", "collab"],
                    "ideas": data})


# ------------------------------------------------------------------ bootstrap (personas + meta)
@app.route("/api/portal/bootstrap")
def api_bootstrap():
    plant = request.args.get("plant", "Nagda")
    con = db()

    def persona(role_code):
        psql, params = ("AND p.name=?", [role_code, plant]) if plant != "All" else ("", [role_code])
        r = con.execute(
            f"""SELECT u.id, u.name, u.emp_code, d.name AS dept, p.name AS plant
               FROM users u JOIN roles r ON r.id=u.role_id
               JOIN plants p ON p.id=u.plant_id
               LEFT JOIN departments d ON d.id=u.department_id
               WHERE r.code=? {psql} LIMIT 1""", params).fetchone()
        return dict(r) if r else None

    # demo employee = the plant's most active submitter
    epsql, eparams = ("WHERE p.name=?", [plant]) if plant != "All" else ("", [])
    emp = con.execute(
        f"""SELECT u.id, u.name, u.emp_code, d.name AS dept, p.name AS plant, COUNT(*) n,
               u.joined_date, u.points_balance AS points
           FROM users u JOIN ideas i ON i.submitter_id=u.id
           JOIN plants p ON p.id=u.plant_id LEFT JOIN departments d ON d.id=u.department_id
           {epsql} GROUP BY u.id ORDER BY n DESC LIMIT 1""", eparams).fetchone()
    emp = dict(emp) if emp else None
    if emp:
        emp["joined"] = (emp.pop("joined_date") or "")[:4]
        emp["awards"] = {r["award_type"]: r["c"] for r in con.execute(
            "SELECT award_type, COUNT(*) c FROM reward_awards WHERE recipient_id=? GROUP BY award_type",
            [emp["id"]])}
        emp["areas"] = con.execute(
            "SELECT COUNT(DISTINCT track_id) FROM ideas WHERE submitter_id=?", [emp["id"]]).fetchone()[0]

    plants = [r["name"] for r in con.execute("SELECT name FROM plants ORDER BY id")]
    workflow_tracks = rows(con.execute(
        "SELECT key, name, domain, phase, always_present, color, default_sla_days FROM workflow_tracks"))
    departments = [r["name"] for r in con.execute("SELECT name FROM departments ORDER BY name")]
    tracks = [r["name"] for r in con.execute("SELECT name FROM tracks ORDER BY id")]

    # avg days spent in each stage (entry -> next transition), from workflow_history
    cyc = {r["code"]: r["d"] for r in con.execute(
        """SELECT s.code, ROUND(AVG(julianday(w2.ts) - julianday(w1.ts)),1) d
           FROM workflow_history w1
           JOIN workflow_history w2 ON w2.idea_id = w1.idea_id
                                   AND w2.from_stage_id = w1.to_stage_id
           JOIN stages s ON s.id = w1.to_stage_id
           GROUP BY s.code""")}

    # top contributor per impact dimension (for the dashboard widget)
    def top_by_benefit(bt_id, label_fmt):
        params = [bt_id]
        psql = plant_clause(plant if plant != "All" else None, params)
        r = con.execute(f"""
            SELECT u.name, d.name dept, ROUND(SUM(b.quantified_value),1) v
            FROM idea_benefits b JOIN ideas i ON i.id=b.idea_id
            JOIN users u ON u.id=i.submitter_id JOIN plants p ON p.id=i.plant_id
            LEFT JOIN departments d ON d.id=u.department_id
            WHERE b.benefit_type_id=? {psql}
            GROUP BY u.id ORDER BY v DESC LIMIT 1""", params).fetchone()
        return {"name": r["name"], "dept": r["dept"] or "—",
                "value": label_fmt(r["v"])} if r else None

    contributors = []
    spec = [(1, "money", "Cost saved", lambda v: f"₹{v} L"),
            (2, "energy", "Energy saved", lambda v: f"₹{v} L equiv"),
            (4, "safety", "Safety impact", lambda v: f"{int(v)} incidents ↓"),
            (5, "water", "Sustainability", lambda v: f"{int(v)} units/yr"),
            (6, "quality", "Quality lift", lambda v: f"{int(v)} pts")]
    for bt, key, lab, fmt in spec:
        t = top_by_benefit(bt, fmt)
        if t:
            contributors.append({"type": key, "label": lab, **t})

    out = {
        "plant": plant, "plants": plants, "departments": departments, "tracks": tracks,
        "workflow_tracks": workflow_tracks,
        "personas": {
            "employee": emp,
            "evaluator": persona("evaluator"),
            "approver": persona("approver"),
            "ci_manager": persona("ci_manager"),
            "master_ci_manager": persona("master_ci_manager"),
        },
        "cycle_times": cyc,
        "contributors": contributors,
    }
    con.close()
    return jsonify(out)


# ------------------------------------------------------------------ employee screens
@app.route("/api/feed")
def api_feed():
    plant = request.args.get("plant")
    dept = request.args.get("department")
    limit = min(int(request.args.get("limit", 12)), 50)
    params = []
    wsql = "s.code IN ('done','implementation','approval','evaluation')"
    wsql += plant_clause(plant, params)
    if dept:
        wsql += " AND d.name = ?"; params.append(dept)
    con = db()
    data = [shape(r) for r in rows(con.execute(
        IDEA_SELECT + f" WHERE {wsql} ORDER BY i.submitted_date DESC, i.endorsement_count DESC LIMIT ?",
        params + [limit]))]
    con.close()
    return jsonify({"ideas": data})


@app.route("/api/my-ideas")
def api_my_ideas():
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    con = db()
    data = [shape(r) for r in rows(con.execute(
        IDEA_SELECT + " WHERE i.submitter_id = ? ORDER BY i.submitted_date DESC", [user_id]))]
    con.close()
    return jsonify({"ideas": data})


@app.route("/api/stories")
def api_stories():
    plant = request.args.get("plant")
    limit = min(int(request.args.get("limit", 12)), 50)
    params = []
    wsql = "s.code='done' AND bs.v IS NOT NULL" + plant_clause(plant, params)
    con = db()
    data = [shape(r) for r in rows(con.execute(
        IDEA_SELECT + f" WHERE {wsql} ORDER BY bs.v DESC LIMIT ?", params + [limit]))]
    con.close()
    return jsonify({"ideas": data})


@app.route("/api/leaderboard")
def api_leaderboard():
    plant = request.args.get("plant")
    limit = min(int(request.args.get("limit", 5)), 20)
    params = []
    psql = plant_clause(plant, params)
    con = db()
    data = rows(con.execute(f"""
        SELECT u.name, d.name AS dept, ROUND(SUM(b.quantified_value),1) AS saved,
               COUNT(DISTINCT i.id) AS ideas
        FROM idea_benefits b
        JOIN ideas i ON i.id=b.idea_id JOIN users u ON u.id=i.submitter_id
        JOIN plants p ON p.id=i.plant_id LEFT JOIN departments d ON d.id=u.department_id
        WHERE b.benefit_type_id=1 {psql}
        GROUP BY u.id ORDER BY saved DESC LIMIT ?""", params + [limit]))
    con.close()
    return jsonify({"leaders": data})


# ------------------------------------------------------------------ reviewer / approver queues
def _queue(stage, plant, dept, limit):
    params = [stage]
    wsql = "s.code = ?" + plant_clause(plant, params)
    if dept:
        wsql += " AND d.name = ?"; params.append(dept)
    con = db()
    total = con.execute(
        f"""SELECT COUNT(*) c FROM ideas i JOIN stages s ON s.id=i.current_stage_id
            JOIN plants p ON p.id=i.plant_id JOIN departments d ON d.id=i.department_id
            WHERE {wsql}""", params).fetchone()["c"]
    data = [shape(r) for r in rows(con.execute(
        IDEA_SELECT + f""" WHERE {wsql}
        ORDER BY CASE i.sla_status WHEN 'breached' THEN 0 WHEN 'at_risk' THEN 1 ELSE 2 END,
                 i.submitted_date ASC LIMIT ?""", params + [limit]))]
    con.close()
    return jsonify({"total": total, "ideas": data})


@app.route("/api/evaluation-queue")
def api_eval_queue():
    return _queue("evaluation", request.args.get("plant"), request.args.get("department"),
                  min(int(request.args.get("limit", 40)), 200))


@app.route("/api/approval-queue")
def api_approval_queue():
    return _queue("approval", request.args.get("plant"), request.args.get("department"),
                  min(int(request.args.get("limit", 40)), 200))


# ------------------------------------------------------------------ aggregates (also agent fodder)
@app.route("/api/dashboard-stats")
def api_dashboard_stats():
    plant = request.args.get("plant")
    params = []
    psql = plant_clause(plant, params)
    con = db()
    base = f"""FROM ideas i JOIN plants p ON p.id=i.plant_id
               JOIN stages s ON s.id=i.current_stage_id
               JOIN departments d ON d.id=i.department_id
               JOIN tracks t ON t.id=i.track_id WHERE 1=1 {psql}"""
    by_stage = rows(con.execute(f"SELECT s.code, COUNT(*) c {base} GROUP BY s.code", params))
    by_dept = rows(con.execute(f"SELECT d.name, COUNT(*) c {base} GROUP BY d.name ORDER BY c DESC", params))
    by_track = rows(con.execute(f"SELECT t.name, COUNT(*) c {base} GROUP BY t.name", params))
    by_month = rows(con.execute(
        f"SELECT substr(i.submitted_date,1,7) ym, COUNT(*) c {base} GROUP BY ym ORDER BY ym", params))
    sav = con.execute(f"""SELECT ROUND(SUM(i.estimated_savings),1) est,
        (SELECT ROUND(SUM(b.quantified_value),1) FROM idea_benefits b
          JOIN ideas i2 ON i2.id=b.idea_id JOIN plants p ON p.id=i2.plant_id
          WHERE b.benefit_type_id=1 {psql.replace('p.name','p.name')}) act
        FROM ideas i JOIN plants p ON p.id=i.plant_id WHERE 1=1 {psql}""",
        params + params).fetchone()
    con.close()
    return jsonify({"by_stage": by_stage, "by_dept": by_dept, "by_track": by_track,
                    "by_month": by_month, "savings": dict(sav)})


# ------------------------------------------------------------------ rewards & groups
@app.route("/api/rewards")
def api_rewards():
    plant = request.args.get("plant")
    limit = min(int(request.args.get("limit", 50)), 200)
    params = []
    psql = plant_clause(plant, params)
    con = db()
    catalog = rows(con.execute("SELECT * FROM reward_catalog ORDER BY id"))
    awards = rows(con.execute(f"""
        SELECT a.award_type, a.points_awarded, a.awarded_date, a.status,
               rc.name AS reward, u.name AS recipient, i.idea_code, i.title,
               cy.period_label, p.name AS plant
        FROM reward_awards a
        JOIN reward_catalog rc ON rc.id=a.reward_id
        JOIN users u ON u.id=a.recipient_id
        JOIN ideas i ON i.id=a.idea_id JOIN plants p ON p.id=i.plant_id
        LEFT JOIN reward_cycles cy ON cy.id=a.cycle_id
        WHERE 1=1 {psql} ORDER BY a.awarded_date DESC LIMIT ?""", params + [limit]))
    rules = rows(con.execute("SELECT * FROM reward_rules WHERE active=1"))
    con.close()
    return jsonify({"catalog": catalog, "awards": awards, "rules": rules})


@app.route("/api/groups")
def api_groups():
    plant = request.args.get("plant")
    params = []
    psql = plant_clause(plant, params)
    con = db()
    groups = rows(con.execute(f"""
        SELECT g.id, g.name, g.purpose, p.name AS plant, d.name AS department, t.name AS track
        FROM groups g JOIN plants p ON p.id=g.plant_id
        LEFT JOIN departments d ON d.id=g.department_id
        LEFT JOIN tracks t ON t.id=g.track_id WHERE 1=1 {psql}""", params))
    for g in groups:
        g["members"] = rows(con.execute(
            """SELECT u.name, gm.role_in_group AS role, d.name AS dept,
                  CASE WHEN ? = 'approval'
                       THEN (SELECT COUNT(*) FROM approvals a WHERE a.approver_id = u.id)
                       ELSE (SELECT COUNT(*) FROM evaluations e WHERE e.evaluator_id = u.id)
                  END AS n_reviews
               FROM group_members gm
               JOIN users u ON u.id = gm.user_id
               LEFT JOIN departments d ON d.id = u.department_id
               WHERE gm.group_id = ?""", [g["purpose"], g["id"]]))
    con.close()
    return jsonify({"groups": groups})


@app.route("/api/intake-meta")
def api_intake_meta():
    """Bulk classification + attachment metadata for the console's intake cards."""
    plant = request.args.get("plant")
    limit = min(int(request.args.get("limit", 12) or 12), 40)
    params = []
    psql = plant_clause(plant, params)
    con = db()
    ideas = rows(con.execute(f"""
        SELECT i.id AS iid, i.idea_code AS id, i.problem_statement, i.proposed_solution
        FROM ideas i JOIN plants p ON p.id=i.plant_id
        WHERE i.current_stage_id=1 {psql}
        ORDER BY i.idea_code DESC LIMIT ?""", params + [limit]))
    out = []
    for it in ideas:
        cls = rows(con.execute(
            "SELECT label, value, confidence FROM idea_classifications WHERE idea_id=? ORDER BY id",
            [it["iid"]]))
        att = con.execute(
            """SELECT SUM(CASE WHEN type='photo' THEN 1 ELSE 0 END) photos, COUNT(*) total
               FROM idea_attachments WHERE idea_id=?""", [it["iid"]]).fetchone()
        out.append({"id": it["id"],
                    "desc": " ".join(x for x in [it["problem_statement"], it["proposed_solution"]] if x),
                    "classifications": cls,
                    "photos": att["photos"] or 0, "total": att["total"] or 0})
    con.close()
    return jsonify({"meta": out})


@app.route("/api/award-config")
def api_award_config():
    con = db()
    defs = rows(con.execute(
        """SELECT id, name, short, kind, icon, color, description, join_mode, conds_json
           FROM award_definitions WHERE active=1"""))
    for d in defs:
        d["conds"] = json.loads(d.pop("conds_json") or "[]")
    rules = rows(con.execute(
        """SELECT id, label, description, icon, color, aspect, op, threshold, unit
           FROM award_highlight_rules WHERE active=1"""))
    con.close()
    return jsonify({"definitions": defs, "rules": rules})


@app.route("/api/dup-keywords")
def api_dup_keywords():
    """Ideas that carry duplicate-detection keyword vocabularies (voice flow)."""
    con = db()
    data = []
    for r in con.execute(IDEA_SELECT + """
            WHERE i.id IN (SELECT DISTINCT idea_id FROM idea_keywords)"""):
        d = shape(r)
        d["kw"] = " ".join(k["keyword"] for k in rows(con.execute(
            "SELECT keyword FROM idea_keywords WHERE idea_id=(SELECT id FROM ideas WHERE idea_code=?)",
            [d["id"]])))
        data.append(d)
    con.close()
    return jsonify({"ideas": data})


if __name__ == "__main__":
    print("Kaizen running -> portal http://localhost:5000/  ·  manager console /manager")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
