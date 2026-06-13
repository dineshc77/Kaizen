"""
kaizen_tools.py
---------------
Read-only data-access tools over kaizen.db for the Kaizen GenAI agent.

These are plain Python functions (no Flask, no Anthropic, no MCP dependency) so they
can be reused in three places:
  1. the Flask /api/agent/ask endpoint (Claude tool-use)  -> CI Manager chat
  2. a thin MCP server (kaizen_mcp_server.py)              -> Claude Desktop / future
  3. direct unit testing / scripts

Every function returns plain JSON-serialisable Python (dict / list) so the caller can
hand results straight back to the model or to an HTTP response.

Schema reference (verified against the real db):
  ideas(id, idea_code, title, problem_statement, proposed_solution, submitter_id,
        plant_id, department_id, track_id, tier_id, current_stage_id, priority,
        area_of_impact, estimated_savings, score, submitted_date, sla_status, ...)
  stages(id, code, name, sort_order)         1 Submitted .. 5 Implemented, 6 Rejected, 7 On hold
  tracks(id, name, description)              Energy Conservation, Safety, Quality, ...
  tiers(id, name 'A'/'B'/'C', sla_days)
  departments(id, code, name)                12 departments
  plants(id, code, name, plant_type)         7 fibre plants
  users(id, emp_code, name, points_balance, department_id, plant_id, role_id, ...)
  reward_awards(id, idea_id, recipient_id, award_type, points_awarded, status, awarded_date)
  idea_benefits(id, idea_id, benefit_type_id, quantified_value, unit, narrative)
  benefit_types(id, name, default_unit)
  idea_outcomes(id, idea_id, implemented_date, outcome_summary, verification_status, ...)
"""

import os
import re
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "kaizen.db")

STAGE_BY_CODE = {
    "submitted": 1, "intake": 1,
    "evaluation": 2, "in_evaluation": 2,
    "approval": 3, "in_approval": 3,
    "implementation": 4, "in_implementation": 4,
    "done": 5, "implemented": 5,
    "rejected": 6,
    "on_hold": 7,
}


def _db():
    """Open kaizen.db strictly read-only."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---- shared SELECT building blocks -----------------------------------------

_IDEA_SELECT = """
SELECT i.id, i.idea_code, i.title, i.problem_statement, i.proposed_solution,
       i.estimated_savings, i.score, i.area_of_impact, i.submitted_date,
       s.name AS stage, s.code AS stage_code,
       t.name AS track, ti.name AS tier,
       d.name AS department, p.name AS plant,
       u.name AS submitter, u.id AS submitter_id
FROM ideas i
LEFT JOIN stages s       ON s.id  = i.current_stage_id
LEFT JOIN tracks t       ON t.id  = i.track_id
LEFT JOIN tiers ti       ON ti.id = i.tier_id
LEFT JOIN departments d  ON d.id  = i.department_id
LEFT JOIN plants p       ON p.id  = i.plant_id
LEFT JOIN users u        ON u.id  = i.submitter_id
"""


def _kw_tokens(text):
    """Pull useful keyword tokens out of a free-text query for LIKE matching."""
    stop = {
        "the", "a", "an", "to", "of", "in", "on", "at", "for", "and", "or", "is",
        "was", "did", "do", "i", "we", "this", "that", "with", "by", "has", "have",
        "some", "any", "their", "would", "like", "know", "if", "such", "kind",
        "planning", "change", "bring", "saving", "savings", "get", "got", "where",
        "stand", "now", "anyone", "someone", "similar", "already", "my", "me",
    }
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{2,}", (text or "").lower())
    seen, out = set(), []
    for w in toks:
        if w in stop or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


# ---- TOOLS ------------------------------------------------------------------

def search_ideas(query="", track=None, department=None, plant=None, stage=None,
                 min_savings=None, only_rewarded=False, limit=10):
    """
    Keyword search across kaizen ideas (title / problem / solution) with optional
    filters. Ranks matches by how many query keywords appear, then by score.
    Returns a list of idea summary dicts. Use this for "has anyone done X" questions.
    """
    con = _db(); cur = con.cursor()
    where, params = [], []

    tokens = _kw_tokens(query)
    if tokens:
        ors = []
        for tk in tokens:
            ors.append("(i.title LIKE ? OR i.problem_statement LIKE ? OR i.proposed_solution LIKE ?)")
            params += [f"%{tk}%", f"%{tk}%", f"%{tk}%"]
        where.append("(" + " OR ".join(ors) + ")")

    if track:
        where.append("t.name LIKE ?"); params.append(f"%{track}%")
    if department:
        where.append("d.name LIKE ?"); params.append(f"%{department}%")
    if plant:
        where.append("p.name LIKE ?"); params.append(f"%{plant}%")
    if stage:
        sid = STAGE_BY_CODE.get(str(stage).lower().replace(" ", "_"))
        if sid:
            where.append("i.current_stage_id = ?"); params.append(sid)
    if min_savings is not None:
        where.append("i.estimated_savings >= ?"); params.append(float(min_savings))
    if only_rewarded:
        where.append("EXISTS (SELECT 1 FROM reward_awards ra WHERE ra.idea_id = i.id)")

    sql = _IDEA_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    # rank: keyword hit-count in title first, then score
    if tokens:
        score_expr = " + ".join([f"(i.title LIKE ?)" for _ in tokens])
        sql += f" ORDER BY ({score_expr}) DESC, i.score DESC"
        params += [f"%{tk}%" for tk in tokens]
    else:
        sql += " ORDER BY i.score DESC"
    sql += " LIMIT ?"; params.append(int(limit))

    cur.execute(sql, params)
    res = _rows(cur)
    con.close()
    return {"count": len(res), "ideas": res}


def get_idea_detail(idea_code):
    """
    Full detail for one kaizen by its code (e.g. 'KZ-2025-00010'): core fields,
    benefits, outcome, evaluation/approval decisions, and any reward.
    """
    con = _db(); cur = con.cursor()
    cur.execute(_IDEA_SELECT + " WHERE i.idea_code = ?", [idea_code])
    base = _rows(cur)
    if not base:
        con.close()
        return {"error": f"No idea found with code {idea_code}"}
    idea = base[0]
    iid = idea["id"]

    cur.execute("""SELECT bt.name AS benefit, b.quantified_value, b.unit, b.narrative
                   FROM idea_benefits b LEFT JOIN benefit_types bt ON bt.id = b.benefit_type_id
                   WHERE b.idea_id = ?""", [iid])
    idea["benefits"] = _rows(cur)

    cur.execute("""SELECT implemented_date, outcome_summary, verification_status, verified_date
                   FROM idea_outcomes WHERE idea_id = ?""", [iid])
    idea["outcome"] = (_rows(cur) or [None])[0]

    cur.execute("""SELECT e.total_score, e.decision, e.comment, e.date, u.name AS evaluator
                   FROM evaluations e LEFT JOIN users u ON u.id = e.evaluator_id
                   WHERE e.idea_id = ? ORDER BY e.date""", [iid])
    idea["evaluations"] = _rows(cur)

    cur.execute("""SELECT ra.award_type, ra.points_awarded, ra.status, ra.awarded_date,
                          u.name AS recipient
                   FROM reward_awards ra LEFT JOIN users u ON u.id = ra.recipient_id
                   WHERE ra.idea_id = ?""", [iid])
    idea["rewards"] = _rows(cur)
    con.close()
    return idea


def get_recognition_and_reward(idea_codes=None, idea_code=None):
    """
    For one or more kaizen codes, report whether each was recognised/rewarded:
    award type, points, status, date, recipient, and the idea's current stage.
    Accepts idea_code (single) or idea_codes (list).
    """
    codes = idea_codes or ([idea_code] if idea_code else [])
    if not codes:
        return {"error": "Provide idea_code or idea_codes"}
    con = _db(); cur = con.cursor()
    out = []
    for code in codes:
        cur.execute("""
            SELECT i.idea_code, i.title, s.name AS stage,
                   ra.award_type, ra.points_awarded, ra.status, ra.awarded_date,
                   u.name AS recipient, d.name AS recipient_dept
            FROM ideas i
            LEFT JOIN stages s ON s.id = i.current_stage_id
            LEFT JOIN reward_awards ra ON ra.idea_id = i.id
            LEFT JOIN users u ON u.id = ra.recipient_id
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE i.idea_code = ?""", [code])
        r = _rows(cur)
        if not r:
            out.append({"idea_code": code, "found": False})
        else:
            row = r[0]
            out.append({
                "idea_code": row["idea_code"], "title": row["title"], "stage": row["stage"],
                "rewarded": bool(row["award_type"]),
                "award_type": row["award_type"], "points_awarded": row["points_awarded"],
                "status": row["status"], "awarded_date": row["awarded_date"],
                "recipient": row["recipient"], "recipient_dept": row["recipient_dept"],
            })
    con.close()
    return {"results": out}


def get_person_standing(name=None, emp_code=None):
    """
    Where a person stands now: points balance, dept/plant, how many ideas they've
    submitted, how many were implemented, how many rewards they've won, and their
    points rank within their plant. Match by name (partial) or emp_code.
    """
    if not name and not emp_code:
        return {"error": "Provide name or emp_code"}
    con = _db(); cur = con.cursor()
    if emp_code:
        cur.execute("SELECT * FROM users WHERE emp_code = ?", [emp_code])
    else:
        cur.execute("SELECT * FROM users WHERE name LIKE ? ORDER BY points_balance DESC", [f"%{name}%"])
    people = _rows(cur)
    if not people:
        con.close()
        return {"error": f"No user matched {name or emp_code}"}

    out = []
    for u in people[:5]:
        uid = u["id"]
        cur.execute("SELECT COUNT(*) c FROM ideas WHERE submitter_id = ?", [uid])
        submitted = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM ideas WHERE submitter_id = ? AND current_stage_id = 5", [uid])
        implemented = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM reward_awards WHERE recipient_id = ?", [uid])
        rewards = cur.fetchone()["c"]
        cur.execute("""SELECT COUNT(*)+1 r FROM users
                       WHERE plant_id = ? AND points_balance > ?""", [u["plant_id"], u["points_balance"]])
        rank = cur.fetchone()["r"]
        cur.execute("SELECT COUNT(*) c FROM users WHERE plant_id = ?", [u["plant_id"]])
        plant_people = cur.fetchone()["c"]
        cur.execute("SELECT name FROM departments WHERE id = ?", [u["department_id"]])
        dept = (cur.fetchone() or {"name": None})["name"]
        cur.execute("SELECT name FROM plants WHERE id = ?", [u["plant_id"]])
        plant = (cur.fetchone() or {"name": None})["name"]
        out.append({
            "name": u["name"], "emp_code": u["emp_code"], "department": dept, "plant": plant,
            "points_balance": u["points_balance"], "ideas_submitted": submitted,
            "ideas_implemented": implemented, "rewards_won": rewards,
            "points_rank_in_plant": rank, "people_in_plant": plant_people,
        })
    con.close()
    return {"matches": out}


def get_department_standing(plant=None, metric="implemented", limit=12):
    """
    Ranking of departments. metric = 'implemented' (count of implemented ideas),
    'submitted' (all ideas), or 'savings' (sum of estimated_savings).
    Optionally scope to one plant. Good for "where does dept X stand" questions.
    """
    con = _db(); cur = con.cursor()
    params = []
    plant_join = ""
    if plant:
        plant_join = "JOIN plants p ON p.id = i.plant_id AND p.name LIKE ?"
        params.append(f"%{plant}%")

    if metric == "savings":
        agg = "ROUND(SUM(i.estimated_savings), 2)"
        having = ""
    elif metric == "submitted":
        agg = "COUNT(*)"
        having = ""
    else:  # implemented
        agg = "SUM(CASE WHEN i.current_stage_id = 5 THEN 1 ELSE 0 END)"
        having = ""

    sql = f"""SELECT d.name AS department, {agg} AS value
              FROM ideas i
              JOIN departments d ON d.id = i.department_id
              {plant_join}
              GROUP BY d.id {having}
              ORDER BY value DESC LIMIT ?"""
    params.append(int(limit))
    cur.execute(sql, params)
    ranked = _rows(cur)
    con.close()
    for idx, r in enumerate(ranked, 1):
        r["rank"] = idx
    return {"metric": metric, "plant": plant or "All plants", "departments": ranked}


def aggregate_stats(group_by="track", plant=None, department=None, stage=None,
                    metric="count"):
    """
    Flexible rollup for charts/answers. group_by one of: track, department, plant,
    stage, tier, year. metric = 'count' (ideas) or 'savings' (sum estimated_savings).
    Optional filters by plant / department / stage. Returns grouped totals.
    """
    cols = {
        "track": ("tracks", "t.name", "LEFT JOIN tracks t ON t.id = i.track_id"),
        "department": ("departments", "d.name", "LEFT JOIN departments d ON d.id = i.department_id"),
        "plant": ("plants", "p.name", "LEFT JOIN plants p ON p.id = i.plant_id"),
        "stage": ("stages", "s.name", "LEFT JOIN stages s ON s.id = i.current_stage_id"),
        "tier": ("tiers", "ti.name", "LEFT JOIN tiers ti ON ti.id = i.tier_id"),
        "year": ("year", "substr(i.submitted_date,1,4)", ""),
    }
    if group_by not in cols:
        return {"error": f"group_by must be one of {list(cols)}"}
    _, label_expr, join = cols[group_by]

    con = _db(); cur = con.cursor()
    joins = [join]
    where, params = [], []
    if plant:
        joins.append("LEFT JOIN plants pp ON pp.id = i.plant_id")
        where.append("pp.name LIKE ?"); params.append(f"%{plant}%")
    if department:
        joins.append("LEFT JOIN departments dd ON dd.id = i.department_id")
        where.append("dd.name LIKE ?"); params.append(f"%{department}%")
    if stage:
        sid = STAGE_BY_CODE.get(str(stage).lower().replace(" ", "_"))
        if sid:
            where.append("i.current_stage_id = ?"); params.append(sid)

    agg = "ROUND(SUM(i.estimated_savings),2)" if metric == "savings" else "COUNT(*)"
    sql = f"SELECT {label_expr} AS label, {agg} AS value FROM ideas i " + " ".join(j for j in joins if j)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY label ORDER BY value DESC"
    cur.execute(sql, params)
    res = _rows(cur)
    con.close()
    return {"group_by": group_by, "metric": metric, "rows": res}


# ---- tool registry (shared by Flask endpoint and MCP server) ----------------

TOOLS = {
    "search_ideas": search_ideas,
    "get_idea_detail": get_idea_detail,
    "get_recognition_and_reward": get_recognition_and_reward,
    "get_person_standing": get_person_standing,
    "get_department_standing": get_department_standing,
    "aggregate_stats": aggregate_stats,
}

# JSON schemas for Claude tool-use (Anthropic 'tools' format)
TOOL_SCHEMAS = [
    {
        "name": "search_ideas",
        "description": "Search past kaizen improvement ideas by free-text keywords (matches title, problem, solution) with optional filters. Use this first for 'has anyone done X' / 'similar to Y' questions. Returns matching idea summaries including idea_code, submitter, department, plant, stage, score and estimated savings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text describing the improvement, e.g. 'pump replacement energy saving'"},
                "track": {"type": "string", "description": "Optional track name, e.g. Energy Conservation, Safety, Quality, Cost Reduction, Sustainability, Productivity, Digitalization, 5S & Housekeeping"},
                "department": {"type": "string", "description": "Optional department name, e.g. Spinning, Viscose, Utilities (Power & Steam)"},
                "plant": {"type": "string", "description": "Optional plant name: Vilayat, Nagda, Kharach, Harihar, BJFCL, IBR, TRC"},
                "stage": {"type": "string", "description": "Optional stage: submitted, evaluation, approval, implementation, implemented, rejected, on_hold"},
                "min_savings": {"type": "number", "description": "Optional minimum estimated savings (INR lakh/year)"},
                "only_rewarded": {"type": "boolean", "description": "If true, only return ideas that received a reward"},
                "limit": {"type": "integer", "description": "Max results (default 10)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_idea_detail",
        "description": "Get full detail for one kaizen by its idea_code (e.g. 'KZ-2025-00010'): description, quantified benefits, implementation outcome, evaluation decisions, and any reward.",
        "input_schema": {
            "type": "object",
            "properties": {"idea_code": {"type": "string"}},
            "required": ["idea_code"]
        }
    },
    {
        "name": "get_recognition_and_reward",
        "description": "Check whether one or more kaizens (by idea_code) were recognised/rewarded: award type, points, status, date, recipient and current stage. Use after search_ideas to see which similar ideas were rewarded.",
        "input_schema": {
            "type": "object",
            "properties": {
                "idea_codes": {"type": "array", "items": {"type": "string"}, "description": "List of idea codes"},
                "idea_code": {"type": "string", "description": "A single idea code (alternative to idea_codes)"}
            }
        }
    },
    {
        "name": "get_person_standing",
        "description": "Where a person stands now: points balance, department/plant, ideas submitted, ideas implemented, rewards won, and points rank within their plant. Match by name (partial ok) or emp_code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "emp_code": {"type": "string"}
            }
        }
    },
    {
        "name": "get_department_standing",
        "description": "Ranking of departments by a metric. metric='implemented' (implemented idea count), 'submitted' (all ideas) or 'savings' (sum of estimated savings). Optionally scope to one plant. Use for 'where does department X stand' questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plant": {"type": "string"},
                "metric": {"type": "string", "enum": ["implemented", "submitted", "savings"]},
                "limit": {"type": "integer"}
            }
        }
    },
    {
        "name": "aggregate_stats",
        "description": "Flexible rollup for charts and totals. group_by one of: track, department, plant, stage, tier, year. metric='count' or 'savings'. Optional filters: plant, department, stage. Use this to build the data behind a chart or a high-level summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["track", "department", "plant", "stage", "tier", "year"]},
                "metric": {"type": "string", "enum": ["count", "savings"]},
                "plant": {"type": "string"},
                "department": {"type": "string"},
                "stage": {"type": "string"}
            },
            "required": ["group_by"]
        }
    },
]


def run_tool(name, args):
    """Dispatch a tool call by name with a dict of args. Returns JSON-serialisable result."""
    fn = TOOLS.get(name)
    if not fn:
        return {"error": f"Unknown tool {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"error": f"Bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {e}"}


if __name__ == "__main__":
    import json
    # quick self-test against the real db
    print("search_ideas('pump'):")
    r = search_ideas("pump replacement spin pump", limit=3)
    print(json.dumps(r, indent=2)[:1200])
    print("\nget_department_standing(Vilayat):")
    print(json.dumps(get_department_standing(plant="Vilayat", limit=4), indent=2))
