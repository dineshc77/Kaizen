"""
extend_db_for_poc.py — adds the data the portal & console screens need that the
original kaizen.db build did not contain, so that EVERYTHING rendered on screen
comes from the database (no client-side synthetic data).

Adds (idempotent — safe to re-run):
  1. workflow_tracks        — the 12 specialist review tracks of the designed workflow
  2. idea_classifications   — intake-screen LLM-style classification rows + confidences
                              (Department / Track / Tier / Language) and suggested
                              workflow-track assignments, for every intake-stage idea
  3. implementation_tasks   — per-idea delivery task boards (10-step standard Kaizen
                              implementation flow) for every implementation-stage idea,
                              with real plant-group assignees, statuses and due dates
  4. award_definitions      — the 10 recognition awards the CI manager works with
  5. award_highlight_rules  — the 7 "why this idea deserves a reward" rules + thresholds
  6. five Nagda demo-walkthrough ideas + idea_keywords — the voice duplicate-detection
     vocabulary, stored in the DB instead of the HTML
"""
import sqlite3, hashlib, json, datetime as dt

DB = "kaizen.db"
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
cur = con.cursor()

def h(s, mod, base=0):
    """deterministic pseudo-random int from a string (stable across runs)"""
    return base + int(hashlib.md5(str(s).encode()).hexdigest(), 16) % mod

today = dt.date(2026, 6, 11)

# ------------------------------------------------------------------ 1. workflow_tracks
cur.execute("""CREATE TABLE IF NOT EXISTS workflow_tracks(
  key TEXT PRIMARY KEY, name TEXT, domain TEXT, phase TEXT,
  always_present INTEGER, color TEXT, default_sla_days INTEGER)""")
WT = [
 ("technical",  "Technical & feasibility", "Engineering review",      "eval", 1, "#2A6FE0", 2),
 ("safety",     "Safety / EHS",            "Hazard & compliance",     "eval", 0, "#D8423A", 2),
 ("environment","Environmental",           "Energy · water · waste",  "eval", 0, "#159468", 2),
 ("quality",    "Quality / ISO",           "Product & process quality","eval",0, "#7a5cd1", 2),
 ("digital",    "IT / OT / Digital",       "Systems & data",          "eval", 0, "#0f8a8a", 3),
 ("equipment",  "Machine / Equipment",     "Asset & maintenance",     "eval", 0, "#D98A12", 2),
 ("finance",    "Finance",                 "Cost & payback",          "both", 1, "#159468", 1),
 ("compliance", "Compliance / Legal",      "Regulatory",              "both", 0, "#5b4ad1", 2),
 ("operations", "Operations / Production", "Line impact",             "appr", 0, "#2A6FE0", 1),
 ("hr",         "HR / People",             "Workforce impact",        "appr", 0, "#c2476a", 2),
 ("procurement","Procurement / SCM",       "Vendors & materials",     "appr", 0, "#D98A12", 2),
 ("commercial", "Commercial",              "Customer & market",       "appr", 0, "#EF5026", 3),
]
cur.execute("DELETE FROM workflow_tracks")
cur.executemany("INSERT INTO workflow_tracks VALUES (?,?,?,?,?,?,?)", WT)
print("workflow_tracks:", len(WT))

# ------------------------------------------------------------------ 2. idea_classifications
cur.execute("""CREATE TABLE IF NOT EXISTS idea_classifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea_id INTEGER, label TEXT,
  value TEXT, confidence INTEGER)""")
cur.execute("DELETE FROM idea_classifications")

# which specialist workflow tracks a DB category track maps to (the designed routing)
TRACK_ROUTE = {
 "Energy Conservation": ["technical", "environment", "finance"],
 "Safety":              ["technical", "safety", "finance"],
 "Quality":             ["technical", "quality", "finance"],
 "Cost Reduction":      ["technical", "finance", "procurement"],
 "Sustainability":      ["technical", "environment", "compliance", "finance"],
 "Productivity":        ["technical", "operations", "finance"],
 "Digitalization":      ["technical", "digital", "finance"],
 "5S & Housekeeping":   ["technical", "operations", "finance"],
}
intake = cur.execute("""
  SELECT i.id, i.idea_code, d.name dept, t.name trk, ti.name tier,
         i.submission_language lang, i.submission_source src
  FROM ideas i JOIN departments d ON d.id=i.department_id
  JOIN tracks t ON t.id=i.track_id JOIN tiers ti ON ti.id=i.tier_id
  WHERE i.current_stage_id=1""").fetchall()
rows = []
for r in intake:
    code = r["idea_code"]
    conf = lambda salt, lo=78, span=19: h(code + salt, span, lo)
    rows += [
        (r["id"], "Department", r["dept"], conf("d")),
        (r["id"], "Track",      r["trk"],  conf("t")),
        (r["id"], "Tier",       r["tier"], conf("r")),
        (r["id"], "Language",   (r["lang"] or "en").upper() +
                                (" · Voice" if r["src"] == "voice" else " · Form"), conf("l", 88, 11)),
    ]
    for k in TRACK_ROUTE.get(r["trk"], ["technical", "finance"]):
        rows.append((r["id"], "workflow_track", k, conf("w" + k, 70, 28)))
cur.executemany(
    "INSERT INTO idea_classifications(idea_id,label,value,confidence) VALUES (?,?,?,?)", rows)
print("idea_classifications:", len(rows), "rows for", len(intake), "intake ideas")

# ------------------------------------------------------------------ 3. implementation_tasks
cur.execute("""CREATE TABLE IF NOT EXISTS implementation_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea_id INTEGER, seq INTEGER,
  title TEXT, phase TEXT, assignee_id INTEGER, due_date TEXT,
  status TEXT, blocked_reason TEXT)""")
cur.execute("DELETE FROM implementation_tasks")
FLOW = [
 ("Feasibility & baseline study",           "Design"),
 ("Detailed engineering & control logic",   "Design"),
 ("Procure equipment & spares",             "Procure"),
 ("Vendor PO approval",                     "Procure"),
 ("Mounting & cabling",                     "Install"),
 ("Setpoint & software configuration",      "Install"),
 ("Trial run & data validation",            "Commission"),
 ("Safety & quality sign-off",              "Commission"),
 ("SOP update & operator training",         "Rollout"),
 ("30-day ROI verification",                "Verify"),
]
# real assignee pools = members of the plant's evaluation panel + approval board
pool = {}
for r in cur.execute("""SELECT g.plant_id, gm.user_id FROM groups g
                        JOIN group_members gm ON gm.group_id=g.id"""):
    pool.setdefault(r["plant_id"], []).append(r["user_id"])

impl = cur.execute("""SELECT id, idea_code, plant_id, score, sla_status
                      FROM ideas WHERE current_stage_id=4""").fetchall()
trows = []
for r in impl:
    code, members = r["idea_code"], pool.get(r["plant_id"]) or [1]
    done_n = max(2, min(7, 2 + round(((r["score"] or 70) - 66) / 5)))
    blocked = r["sla_status"] in ("at_risk", "breached")
    for i, (title, phase) in enumerate(FLOW):
        if i < done_n:               status = "done"
        elif i == done_n:            status = "inprogress"
        elif i == done_n + 1:        status = "blocked" if blocked else "inprogress"
        elif i == done_n + 2:        status = "review"
        else:                        status = "todo"
        reason = ""
        if status == "blocked":
            reason = ("Awaiting vendor quote" if h(code, 2) else
                      "Awaiting validation data from trials")
        due = (today + dt.timedelta(days=(i - done_n) * 2 + 1)).isoformat()
        trows.append((r["id"], i + 1, title, phase,
                      members[h(code + str(i), len(members))], due, status, reason))
cur.executemany("""INSERT INTO implementation_tasks
  (idea_id,seq,title,phase,assignee_id,due_date,status,blocked_reason)
  VALUES (?,?,?,?,?,?,?,?)""", trows)
print("implementation_tasks:", len(trows), "rows for", len(impl), "implementation ideas")

# ------------------------------------------------------------------ 4 & 5. award config
cur.execute("""CREATE TABLE IF NOT EXISTS award_definitions(
  id TEXT PRIMARY KEY, name TEXT, short TEXT, kind TEXT, icon TEXT, color TEXT,
  description TEXT, join_mode TEXT, conds_json TEXT, active INTEGER)""")
cur.execute("DELETE FROM award_definitions")
AD = [
 ("gold","Gold Medallion","Gold","Medal","ti-medal","var(--amber)",
  "Apex recognition for outstanding, high-impact ideas","all",
  [{"a":"score","op":"gte","th":85},{"a":"roi","op":"gte","th":10}]),
 ("silver","Silver Medallion","Silver","Medal","ti-medal-2","#8a9099",
  "Strong, well-executed improvements","all",
  [{"a":"score","op":"gte","th":75},{"a":"roi","op":"gte","th":5}]),
 ("bronze","Bronze Medallion","Bronze","Medal","ti-award","#b87333",
  "Solid contributions that cleared the reward bar","all",
  [{"a":"score","op":"gte","th":66}]),
 ("cash","Cash Incentive","Cash","Monetary","ti-cash","var(--emerald)",
  "Monetary reward shared from validated savings","all",
  [{"a":"roi","op":"gte","th":8}]),
 ("safety","Safety Champion","Safety","Themed","ti-shield-check","var(--red)",
  "For ideas that measurably improve plant or worker safety","all",
  [{"a":"safety","op":"is"}]),
 ("green","Green Award","Green","Themed","ti-leaf","var(--teal)",
  "For energy, water, waste or emissions reduction","all",
  [{"a":"sustain","op":"is"}]),
 ("innov","Innovation Award","Innovation","Themed","ti-bulb","var(--violet)",
  "For original, digital-first or replicable ideas","any",
  [{"a":"replicable","op":"is"}]),
 ("spot","Spot Recognition","Spot","Recognition","ti-confetti","var(--blue)",
  "Quick recognition for highly-endorsed ideas","all",
  [{"a":"eng","op":"gte","th":100}]),
 ("team","Team Excellence","Team","Recognition","ti-users-group","#0f8a8a",
  "For cross-department or multi-plant collaboration","any",
  [{"a":"collab","op":"gte","th":2}]),
 ("cert","Certificate of Appreciation","Certificate","Recognition","ti-certificate","#7a7a72",
  "Baseline acknowledgement for every implemented idea","all",[]),
]
cur.executemany("""INSERT INTO award_definitions VALUES (?,?,?,?,?,?,?,?,?,1)""",
                [(a, b, c, d, e, f, g, j, json.dumps(k)) for a, b, c, d, e, f, g, j, k in AD])

cur.execute("""CREATE TABLE IF NOT EXISTS award_highlight_rules(
  id TEXT PRIMARY KEY, label TEXT, description TEXT, icon TEXT, color TEXT,
  aspect TEXT, op TEXT, threshold REAL, unit TEXT, active INTEGER)""")
cur.execute("DELETE FROM award_highlight_rules")
HR = [
 ("fin","High financial impact","Realized savings at or above the threshold",
  "ti-coin-rupee","var(--emerald)","roi","gte",10,"₹ L"),
 ("eng","Strong engagement","Endorsements + comments score above the threshold",
  "ti-heart","var(--red)","eng","gte",120,"pts"),
 ("safe","Safety improvement","Idea improves plant or worker safety",
  "ti-shield-check","var(--red)","safety","is",None,""),
 ("env","Sustainability impact","Reduces energy, water, waste or emissions",
  "ti-leaf","var(--teal)","sustain","is",None,""),
 ("score","High evaluation score","Reward score at or above the threshold",
  "ti-star","var(--amber)","score","gte",85,"/100"),
 ("beat","Beat the estimate","Realized exceeds the estimate by at least X%",
  "ti-trending-up","var(--blue)","variance","gte",10,"%"),
 ("repl","Replicable across plants","Can be standardised to other units",
  "ti-copy","var(--violet)","replicable","is",None,""),
]
cur.executemany("INSERT INTO award_highlight_rules VALUES (?,?,?,?,?,?,?,?,?,1)", HR)
print("award_definitions:", len(AD), "| award_highlight_rules:", len(HR))

# ------------------------------------------------------------------ 6. dup-check walkthrough ideas + keywords
cur.execute("""CREATE TABLE IF NOT EXISTS idea_keywords(
  idea_id INTEGER, keyword TEXT)""")

DUP = [
 ("Auto shut-off for conveyors left running idle", "Maintenance",
  "Energy Conservation", "B", "implementation", 3.4, 82,
  "Conveyors on lines 2 and 3 keep running during breaks and shift change, wasting power.",
  "Fit idle-detection sensors that cut motor power after 5 minutes of no load.",
  "conveyor belt idle running motor switch off automatic stop power waste shutoff sensor"),
 ("Anti-fatigue cushioned mats at spinning workstations", "Spinning",
  "Safety", "A", "done", 1.6, 78,
  "Operators stand 8+ hours on bare concrete at the spinning frames, causing leg fatigue.",
  "Install anti-fatigue rubber mats at all standing workstations on the spinning floor.",
  "mat mats standing rubber fatigue leg pain floor cushion comfort operator workstation"),
 ("LED high-bay lighting upgrade across the shop floor", "Utilities (Power & Steam)",
  "Energy Conservation", "B", "evaluation", 7.5, 71,
  "400W metal-halide high-bay fixtures consume heavily and dim with age.",
  "Replace with 150W LED high-bays — same lux, ~60% lower consumption, longer life.",
  "led light lighting bulb tube lamp electricity bill energy saving bright high bay"),
 ("Safety guard on the godet to stop yarn-pull injuries", "Spinning",
  "Safety", "A", "implementation", 1.2, 80,
  "Yarn-pull incidents near the unguarded godet rolls have caused two near-miss hand injuries.",
  "Fabricate and mount a hinged mesh guard with an interlock on each godet stand.",
  "guard godet yarn pull hand injury finger safety cover mesh roller machine"),
 ("Organize morning vehicle entry at the plant gate with barricades", "Safety",
  "Safety", "A", "evaluation", 0.8, 74,
  "Morning truck and two-wheeler entry tangles at gate 1, risking accidents and delays.",
  "Lane barricades plus staggered entry slots for trucks vs two-wheelers at the main gate.",
  "gate entry vehicle truck traffic queue morning rush barricade lane parking congestion"),
]
plant_id = cur.execute("SELECT id FROM plants WHERE name='Nagda'").fetchone()[0]
existing = {r["title"] for r in cur.execute(
    "SELECT title FROM ideas WHERE plant_id=?", [plant_id])}
seq = cur.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]
added = 0
for title, dept, trk, tier, stage, est, score, prob, sol, kws in DUP:
    if title in existing:
        continue
    seq += 1
    code = f"KZ-2026-{seq:05d}"
    dept_id = cur.execute("SELECT id FROM departments WHERE name=?", [dept]).fetchone()[0]
    trk_id  = cur.execute("SELECT id FROM tracks WHERE name=?", [trk]).fetchone()[0]
    tier_id = cur.execute("SELECT id FROM tiers WHERE name=?", [tier]).fetchone()[0]
    st_id   = cur.execute("SELECT id FROM stages WHERE code=?", [stage]).fetchone()[0]
    sub_id  = cur.execute("""SELECT u.id FROM users u WHERE u.plant_id=? AND u.department_id=?
                             ORDER BY u.id LIMIT 1""", [plant_id, dept_id]).fetchone()
    sub_id = sub_id[0] if sub_id else cur.execute(
        "SELECT id FROM users WHERE plant_id=? LIMIT 1", [plant_id]).fetchone()[0]
    cur.execute("""INSERT INTO ideas
        (idea_code,title,problem_statement,proposed_solution,submitter_id,plant_id,
         department_id,track_id,tier_id,current_stage_id,estimated_savings,score,
         sla_status,priority,endorsement_count,collaborator_count,submission_language,
         submission_source,area_of_impact,submitted_date,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (code, title, prob, sol, sub_id, plant_id, dept_id, trk_id, tier_id, st_id,
         est, score, "ok", "medium", h(code, 30, 8), h(code, 3),
         "en", "voice", dept, "2026-06-01", "2026-06-01", "2026-06-01"))
    iid = cur.lastrowid
    for kw in kws.split():
        cur.execute("INSERT INTO idea_keywords VALUES (?,?)", (iid, kw))
    added += 1
print("dup-check ideas added:", added, "(+ keywords)")

con.commit()
# verify
for t in ["workflow_tracks","idea_classifications","implementation_tasks",
          "award_definitions","award_highlight_rules","idea_keywords"]:
    print(" verify", t, "=", cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
print(" verify ideas total =", cur.execute("SELECT COUNT(*) FROM ideas").fetchone()[0])
con.close()
print("DB extension complete.")
