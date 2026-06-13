"""
kaizen_mcp_server.py
--------------------
MCP server exposing the Kaizen database (kaizen.db) as tools for an MCP client
such as Claude Desktop. Claude Desktop is the client; this is the server.

It wraps the read-only functions in kaizen_tools.py as MCP tools and can run in
two transports:

  • stdio  (local dev / Claude Desktop local connector)
        python kaizen_mcp_server.py
  • streamable-http  (remote, e.g. deployed on Render; Claude Desktop connects by URL)
        TRANSPORT=http python kaizen_mcp_server.py
        -> serves the MCP endpoint at  http://HOST:PORT/mcp

Remote auth: if MCP_AUTH_TOKEN is set, every HTTP request must send
    Authorization: Bearer <MCP_AUTH_TOKEN>
(initialization/handshake requests are allowed through so the connector can probe).

IMPORTANT: in stdio mode never print() to stdout — it corrupts JSON-RPC. We log to stderr.
"""

import os
import sys
from typing import Optional, List

from mcp.server.fastmcp import FastMCP

import kaizen_tools

# host/port are read from settings by streamable_http_app(); set before tools.
mcp = FastMCP(
    "kaizen",
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8000")),
)


@mcp.tool()
def search_ideas(query: str, track: Optional[str] = None, department: Optional[str] = None,
                 plant: Optional[str] = None, stage: Optional[str] = None,
                 min_savings: Optional[float] = None, only_rewarded: bool = False,
                 limit: int = 10) -> dict:
    """Search past kaizen improvement ideas by free-text keywords (matches title,
    problem statement and proposed solution), with optional filters. Use this FIRST
    for "has anyone done X" or "similar to Y" questions.

    Args:
        query: Free text describing the improvement, e.g. "pump replacement energy saving".
        track: Optional track (Energy Conservation, Safety, Quality, Cost Reduction,
            Sustainability, Productivity, Digitalization, 5S & Housekeeping).
        department: Optional department (e.g. Spinning, Viscose, Utilities (Power & Steam)).
        plant: Optional plant (Vilayat, Nagda, Kharach, Harihar, BJFCL, IBR, TRC).
        stage: Optional stage (submitted, evaluation, approval, implementation, implemented,
            rejected, on_hold).
        min_savings: Optional minimum estimated savings in INR lakh/year.
        only_rewarded: If true, return only ideas that received a reward.
        limit: Max results (default 10).
    """
    return kaizen_tools.search_ideas(query=query, track=track, department=department,
                                     plant=plant, stage=stage, min_savings=min_savings,
                                     only_rewarded=only_rewarded, limit=limit)


@mcp.tool()
def get_idea_detail(idea_code: str) -> dict:
    """Get full detail for one kaizen by its idea_code (e.g. "KZ-2025-00010"):
    description, quantified benefits, implementation outcome, evaluation decisions,
    and any reward."""
    return kaizen_tools.get_idea_detail(idea_code=idea_code)


@mcp.tool()
def get_recognition_and_reward(idea_codes: Optional[List[str]] = None,
                               idea_code: Optional[str] = None) -> dict:
    """Check whether one or more kaizens were recognised/rewarded: award type, points,
    status, date, recipient and current stage. Pass either idea_code (single) or
    idea_codes (list). Use after search_ideas to see which similar ideas were rewarded."""
    return kaizen_tools.get_recognition_and_reward(idea_codes=idea_codes, idea_code=idea_code)


@mcp.tool()
def get_person_standing(name: Optional[str] = None, emp_code: Optional[str] = None) -> dict:
    """Where a person stands now: points balance, department/plant, ideas submitted,
    ideas implemented, rewards won, and points rank within their plant. Match by name
    (partial ok) or emp_code."""
    return kaizen_tools.get_person_standing(name=name, emp_code=emp_code)


@mcp.tool()
def get_department_standing(plant: Optional[str] = None, metric: str = "implemented",
                            limit: int = 12) -> dict:
    """Ranking of departments by a metric. metric = "implemented" (count of implemented
    ideas), "submitted" (all ideas), or "savings" (sum of estimated savings). Optionally
    scope to one plant. Use for "where does department X stand" questions."""
    return kaizen_tools.get_department_standing(plant=plant, metric=metric, limit=limit)


@mcp.tool()
def aggregate_stats(group_by: str = "track", metric: str = "count",
                    plant: Optional[str] = None, department: Optional[str] = None,
                    stage: Optional[str] = None) -> dict:
    """Flexible rollup for totals and charts. group_by one of: track, department, plant,
    stage, tier, year. metric = "count" or "savings". Optional filters: plant, department,
    stage. Use to summarise the programme or build chart data."""
    return kaizen_tools.aggregate_stats(group_by=group_by, metric=metric, plant=plant,
                                        department=department, stage=stage)


# --- optional bearer-token guard for the remote (HTTP) transport -------------
class BearerAuthMiddleware:
    """Minimal ASGI middleware: require Authorization: Bearer <token> on HTTP requests
    when MCP_AUTH_TOKEN is set. Lets non-HTTP scopes and CORS preflight through."""

    def __init__(self, app, token):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        if scope.get("method") == "OPTIONS" or auth == f"Bearer {self.token}":
            return await self.app(scope, receive, send)
        body = b'{"error":"unauthorized"}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def build_http_app():
    """Return the ASGI app for streamable-HTTP, wrapped with optional auth."""
    app = mcp.streamable_http_app()
    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if token:
        app = BearerAuthMiddleware(app, token)
    return app


# expose `app` so a server runner can import kaizen_mcp_server:app
app = build_http_app()


if __name__ == "__main__":
    transport = os.environ.get("TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http", "streamable_http"):
        import uvicorn
        host = os.environ.get("HOST", "0.0.0.0")
        port = int(os.environ.get("PORT", "8000"))
        print(f"Kaizen MCP (streamable-http) on {host}:{port}/mcp", file=sys.stderr)
        uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
    else:
        print("Kaizen MCP (stdio) starting…", file=sys.stderr)
        mcp.run(transport="stdio")
