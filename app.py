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
MODEL = "claude-haiku-4-5-20251001"   # fast model for the short classify/reply turn (was claude-sonnet-4-6)

# Sarvam (Indian-language speech) settings
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_MODEL = "saaras:v3"      # current recommended ASR (was saarika:v2.5, legacy)
SARVAM_TTS_MODEL = "bulbul:v3"     # current TTS; required for the Shubh voice (was bulbul:v2)
SARVAM_SPEAKER = "Shubh"            # bulbul:v3 voice (default male voice)
LANG_BCP47 = {"hi": "hi-IN", "gu": "gu-IN", "en": "en-IN", "ta": "ta-IN"}

if not API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. AI calls will fail until you set it.")
if not SARVAM_KEY:
    print("WARNING: SARVAM_API_KEY is not set. Voice (speech) will fail until you set it.")

client = anthropic.Anthropic(api_key=API_KEY)
app = Flask(__name__)

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


@app.route("/")
def index():
    return render_template("index.html")


# --- Employee idea-submission portal (voice integrated, served same-origin so /api works) ---
@app.route("/employee")
@app.route("/portal")
def employee_portal():
    return send_from_directory(app.root_path, "Kaizen_User_Reviewer_Approver.html")


@app.route("/api/health")
def health():
    """Lets the portal detect a live backend and choose Sarvam+Claude over the in-browser fallback."""
    return jsonify({"ok": True, "anthropic": bool(API_KEY), "sarvam": bool(SARVAM_KEY)})


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
            return jsonify({"error": f"sarvam tts {r.status_code}: {r.text[:200]}"}), 502
        audios = r.json().get("audios", [])
        return jsonify({"audio": audios[0] if audios else ""})
    except Exception as e:  # noqa
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Kaizen Voice running -> open http://localhost:5000/?name=Ramesh in Chrome")
    app.run(host="0.0.0.0", port=5000, debug=False)
