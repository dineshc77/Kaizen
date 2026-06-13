# Kaizen MCP server — setup

This exposes `kaizen.db` to **Claude Desktop** (the MCP *client*) through an MCP
*server* that runs the read-only tools in `kaizen_tools.py`.

Two ways to run it. The demo uses the **remote (Render)** path.

---

## Files

| File | Role |
|------|------|
| `kaizen_tools.py` | The actual data-access functions over `kaizen.db` (read-only). The valuable, reusable core. |
| `kaizen_mcp_server.py` | Wraps those functions as MCP tools. Runs as **stdio** (local) or **streamable-HTTP** (remote). |
| `requirements-mcp.txt` | Deps for the MCP service (`mcp`, `uvicorn`). |
| `render.yaml` | Adds the second Render service `kaizen-mcp` next to the existing `kaizen-voice`. |

Tools exposed: `search_ideas`, `get_idea_detail`, `get_recognition_and_reward`,
`get_person_standing`, `get_department_standing`, `aggregate_stats`.

---

## A. Remote on Render (what the demo uses)

1. **Commit & push** these files to the same GitHub repo.

2. In Render, the `render.yaml` defines a second web service **`kaizen-mcp`**. Create it
   (Blueprint sync, or "New > Web Service" pointing at the repo with the settings from
   `render.yaml`). Key settings:
   - Build: `pip install -r requirements-mcp.txt`
   - Start: `python kaizen_mcp_server.py`
   - Plan: **Starter (paid)** — do *not* use Free; a sleeping endpoint stalls the first
     question for ~30–60s.

3. **Set env vars** on the `kaizen-mcp` service in the Render dashboard:
   - `TRANSPORT = http`
   - `MCP_AUTH_TOKEN = <a long random string>`  ← you'll paste this in Claude Desktop too
   - `PYTHON_VERSION = 3.11`
   (No `ANTHROPIC_API_KEY` needed here — the model lives in Claude Desktop, not in the server.)

4. After deploy, the endpoint is:
   ```
   https://kaizen-mcp.onrender.com/mcp
   ```
   (Render shows the exact host; the path is always `/mcp`.)

5. **Quick health check** from your laptop (replace TOKEN/URL):
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://kaizen-mcp.onrender.com/mcp \
     -H "Authorization: Bearer TOKEN" \
     -H "Content-Type: application/json" \
     -H "Accept: application/json, text/event-stream" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}'
   ```
   `200` = good. `401` = token mismatch.

6. **Connect Claude Desktop** (paid plan required for remote connectors):
   - Settings → **Connectors** → **Add custom connector**
   - Name: `Kaizen`
   - URL: `https://kaizen-mcp.onrender.com/mcp`
   - Add header: `Authorization: Bearer <MCP_AUTH_TOKEN>`
   - Save → enable it. The 6 tools should appear.

7. **Warm it up** right before the demo (hit the curl health check once) so the first
   real question is instant.

---

## B. Local (stdio) — fallback / dev

No network needed; runs on the same machine as Claude Desktop.

```bash
pip install -r requirements-mcp.txt
```

Add to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "kaizen": {
      "command": "python",
      "args": ["/ABSOLUTE/PATH/TO/Kaizen/kaizen_mcp_server.py"]
    }
  }
}
```

Restart Claude Desktop. (stdio mode needs no token and no TRANSPORT var.)

Inspect tools interactively without Claude Desktop:
```bash
npx @modelcontextprotocol/inspector python kaizen_mcp_server.py
```

---

## Try it in Claude Desktop

Once connected, ask naturally — Claude will pick the tools:

- *"I'm planning to change a pump on the spinning floor to save energy. Has anyone
  already done a similar pump kaizen? Did it get recognised or rewarded, and where
  does that person stand now?"*
- *"Which department has implemented the most kaizens at Vilayat?"*
- *"Show total estimated savings by track across all plants."*

For the pump question Claude typically chains: `search_ideas` → `get_recognition_and_reward`
→ `get_person_standing`, then answers with real idea codes, names and numbers.

---

## Notes

- The server opens `kaizen.db` strictly **read-only** — it cannot modify data.
- `MCP_AUTH_TOKEN` is a simple shared-secret guard so the public URL isn't open to all.
  Rotate it by changing the Render env var and the Claude Desktop header.
- Same repo, same `kaizen.db` as the portal; the MCP service just runs a different
  process (`kaizen_mcp_server.py`) with its own URL.
