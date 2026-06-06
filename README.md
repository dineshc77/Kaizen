# Kaizen Voice — MVP

Hands-free, voice-based Kaizen (continuous improvement) idea capture for shop-floor
workers at the Aditya Birla Grasim VSF fibre plant. No login, no typing — the worker
just talks, Claude understands, classifies **Tier 1/2/3** and **Department**, asks a
follow-up only if needed, then thanks them.

## Run it

1. Install Python 3.9+.
2. In this folder:
   ```
   pip install -r requirements.txt
   python app.py
   ```
3. Open **Google Chrome** (required for voice) at:
   ```
   http://localhost:5000/?name=Ramesh
   ```
   Change `Ramesh` to any employee name — the app greets them by that name.

## How it works

- Worker picks a language (Hindi / Gujarati / English / Tamil).
- Browser asks for mic permission once, then listens hands-free.
- The worker speaks; the app records the clip and after a ~1s pause sends it to
  **Sarvam AI** (server-side) for speech-to-text — consistent on every browser.
- Claude understands it, classifies tier + department, and replies; the reply text
  goes back to **Sarvam** for text-to-speech and the browser plays that audio.
- The final entry (ticket no., idea, department, tier, cost) is shown and downloadable.

## Keys (both required)

Set as environment variables (local shell, or Render secrets):
- `ANTHROPIC_API_KEY` — Claude (understanding + classification)
- `SARVAM_API_KEY` — Sarvam (speech-to-text + text-to-speech)

```
# Windows PowerShell, local run
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:SARVAM_API_KEY="sk_..."
python app.py
```

Tuning the listening feel: in `templates/index.html`, `SILENCE_MS` (pause before it
stops, default 1000ms) and `VAD_THRESHOLD` (mic loudness counted as speech, raise it
for noisy areas).

## Security

The Anthropic API key lives only on the server (`app.py`), never in the browser.
Prefer setting it as an environment variable instead of editing the file:

```
# Windows (PowerShell)
$env:ANTHROPIC_API_KEY="sk-ant-..."
python app.py
```

> The key currently in `app.py` was shared in chat — **rotate it** at
> console.anthropic.com and replace it before any real use.

## Tuning

- **Departments / tier rules**: edit `DEPARTMENTS` and `system_prompt()` in `app.py`.
- **Silence sensitivity**: change `SILENCE_MS` in `templates/index.html` (default 1900 ms).
- **Indian-language accuracy**: browser speech is weaker for Tamil/Gujarati and in noise.
  For production, swap the browser recognizer for Sarvam AI (server-side STT).

## Next steps for real rollout

- Save submissions to a database or Google Sheet (currently on-screen + download).
- Real employee authentication before the language screen.
- Sarvam AI for speech-to-text; deploy behind HTTPS on the plant network.
