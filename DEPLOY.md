# Deploy Kaizen Voice to Render (free, permanent HTTPS URL)

You'll get a live link like `https://kaizen-voice.onrender.com`. The mic works because
Render serves over HTTPS. Your API key is stored as a secret, not in the code.

## 0. One-time: rotate your API key first
The old key was shared in chat — go to console.anthropic.com → **API Keys** →
create a new key. Use the NEW key in step 3 below.

## 1. Put the code on GitHub
1. Create a free account at github.com if you don't have one.
2. Create a new **empty** repository, e.g. `kaizen-voice` (Private is fine).
3. Upload this whole folder. Easiest no-tools way:
   - On the new repo page click **uploading an existing file**.
   - Drag in: `app.py`, `requirements.txt`, `render.yaml`, `Procfile`,
     `.gitignore`, and the **`templates`** folder (with `index.html` inside).
   - Click **Commit changes**.

   (Or with git installed:)
   ```
   cd D:\1TRC\voice
   git init
   git add app.py requirements.txt render.yaml Procfile .gitignore templates
   git commit -m "Kaizen Voice MVP"
   git branch -M main
   git remote add origin https://github.com/<you>/kaizen-voice.git
   git push -u origin main
   ```

## 2. Create the service on Render
1. Sign up free at render.com (log in with GitHub — easiest).
2. Click **New +** → **Web Service**.
3. Connect your `kaizen-voice` GitHub repo.
4. Render auto-detects `render.yaml`. Confirm:
   - Runtime: **Python**
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`
   - Plan: **Free**

## 3. Add your API keys as secrets
In the service's **Environment** tab → **Add Environment Variable** (add BOTH):
- `ANTHROPIC_API_KEY` = `sk-ant-...`  (your NEW Anthropic key from step 0)
- `SARVAM_API_KEY`    = `sk_...`      (your Sarvam key — for the voice/speech)

Click **Save**, then **Deploy**.

## 4. Use it
After ~2-3 minutes you'll get a URL. Open in **Chrome**:
```
https://kaizen-voice.onrender.com/?name=Dinesh
```
Change `Dinesh` to any employee name.

## Notes
- **Free tier sleeps** after ~15 min idle and takes ~50 sec to wake on the next visit.
  Fine for demos; upgrade to a paid plan ($7/mo) for always-on.
- To update the app later: push changes to GitHub → Render redeploys automatically.
- Submissions are currently on-screen + downloadable. For a shared record, ask me to
  add a Google Sheet or database next.
