"""
enrich_ideas.py — makes kaizen.db realistic and context-rich for GenAI querying.

What it does (deterministic — same output every run, seeded by idea_code):
  1. Rewrites title / problem_statement / proposed_solution / area_of_impact for
     every idea (except the 5 voice-walkthrough ideas, whose titles are referenced
     by the duplicate-detection keywords):
       - every title UNIQUE across all 20,798 ideas
       - department- and track-aware shop-floor content with realistic equipment,
         readings, shifts, lines and consequences
       - length mix: ~18% brief (2 sentences), ~40% medium (3-4), ~30% high (5-7),
         ~12% detailed multi-paragraph; exactly 8 ideas remain one-liners
  2. Fills evaluations.comment (18,319 rows) with decision-appropriate evaluator notes
  3. Fills approvals.comment (12,503 rows) with approver notes
  4. Replaces the generic comments table with realistic clarification threads:
     reviewer/approver question -> employee reply (plus some peer comments)
"""
import sqlite3, hashlib, random, datetime as dt

DB = "kaizen.db"
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row
cur = con.cursor()

def rng_for(code, salt=""):
    seed = int(hashlib.md5((code + salt).encode()).hexdigest()[:12], 16)
    return random.Random(seed)

# ============================================================================
# DOMAIN VOCABULARY — per department
# ============================================================================
D = {
 # each topic is an ALIGNED triplet: (symptom template, fix phrase [noun], equipment)
 "Viscose": dict(
  areas=["viscose preparation hall","churn floor","ripening gallery","filtration bay","deaeration deck","slurry house"],
  topics=[
   ("viscosity drifting {a}-{b} seconds between batches","an inline viscometer with auto-feedback to the churn","xanthation churn {n}"),
   ("filtration clogging every {a} hours instead of the rated {b}","staged backflush sequencing on the candle bank","viscose filter press F-{n}"),
   ("gel particles appearing after ripening","a re-baselined ripening time-temperature recipe","ripening drum {n}"),
   ("air entrainment above {a}% after deaeration","a vacuum-hold extension with level-radar interlock","deaeration vessel V-{n}"),
   ("CS2 consumption running {a} kg/ton over norm","tighter CS2 dosing with a mass-flow trim loop","CS2 dosing skid {n}"),
   ("batch gamma value swinging by {a} points","a churn-end gamma check with auto hold/release","churn line {n}"),
   ("spillage of roughly {a} litres per shift at the transfer point","hard piping plus a drip-tray and level interlock","viscose transfer pump P-{n}"),
   ("dissolver lump carry-over choking the downstream screen","a slurry shear-plate upgrade with amp-based feedback","dissolver D-{n}"),
  ]),
 "Spinning": dict(
  areas=["spinning floor east wing","machine row 7-12","doffing aisle","spinneret cleaning room","cutter gallery"],
  topics=[
   ("yarn breaks averaging {a} per machine per shift","an acoustic break-detector with machine-stop interlock","spinning machine SM-{n}"),
   ("denier variation of ±{a} on the off-shift","a spin-pump pulsation damper plus pressure trend alarm","spin pump SP-{n}"),
   ("spinneret choking forcing a change every {a} hours","upgraded ultrasonic cleaning with a residue scorecard","spinneret cluster row {n}"),
   ("tow laps building on the godet roughly every {a} doffs","a quick-release godet guard with lap-detection stop","godet stand G-{n}"),
   ("cutter blade life dropping to {a} hours","a blade-life tracker tied to cutter vibration","cutter unit K-{n}"),
   ("acid mist drifting into the operator aisle from a sticking damper","a damper position feedback with auto-balance","aspirator hood line {n}"),
   ("filament drag marks on {a}% of cakes","a traverse-guide ceramic upgrade with wear gauge","take-up winder W-{n}"),
  ]),
 "Spin Bath": dict(
  areas=["spin bath recovery house","evaporator deck","crystallizer floor","acid trim bay"],
  topics=[
   ("zinc carry-over losing about {a} kg/day to drain","a density-based zinc recovery loop on the drain line","bath circulation pump BP-{n}"),
   ("bath temperature hunting ±{a} °C at the trim station","cascaded PID retune with feed-forward","acid trim station {n}"),
   ("evaporator steam economy slipping to {a} from design {b}","a vapour-line insulation and vent-loss audit package","evaporator effect {n}"),
   ("sulphate scaling the HX tubes every {a} days","a scheduled sponge-ball cleaning system","heat exchanger HX-{n}"),
   ("acid concentration drifting {a} g/l between lab checks","an online conductivity analyser feeding the trim","mother liquor tank ML-{n}"),
   ("centrifuge cake moisture running {a}% over spec","a cake-moisture sensor interlocked to spin time","Glauber salt centrifuge {n}"),
  ]),
 "Auxiliary & CS2": dict(
  areas=["CS2 plant battery limit","sulphur yard","vapour recovery shed","adsorber deck"],
  topics=[
   ("CS2 recovery efficiency stuck near {a}% against the {b}% target","staggered adsorber changeover with a polishing bed","activated carbon adsorber A-{n}"),
   ("fugitive CS2 odour during adsorber changeover","a changeover purge-capture line to the blower suction","vapour recovery blower {n}"),
   ("melter refractory hot-spots reading {a}0 °C on the shell","an IR shell-scan routine with patch criteria","sulphur melter SM-{n}"),
   ("sulphur fines carryover blocking the condenser every {a} weeks","a cyclone pre-separator ahead of the condenser","CS2 condenser bank {n}"),
   ("blower amperage creeping {a}% above baseline","an impeller wash schedule keyed to the amp trend","recovery blower RB-{n}"),
  ]),
 "Pulp": dict(
  areas=["pulp street","bale storage shed","blend chest mezzanine"],
  topics=[
   ("consistency swinging {a}% after each bale change","auto-tuned consistency control with bale-change feedforward","blend chest BC-{n}"),
   ("wire fragments from dewiring reaching the slusher about {a} times a month","a magnet-plus-camera check at the dewiring station","bale dewiring station {n}"),
   ("wet lap moisture varying {a}-{b}% across the deckle","deckle-edge moisture profiling with steam-box trim","wet lap machine {n}"),
   ("pulper motor tripping on overload roughly every {a} bales","a soft-start ramp profile for the pulper drive","pulper P-{n}"),
   ("blend chest level alarms chattering on night shift","a level transmitter re-span with deadband logic","level loop LC-{n}"),
  ]),
 "Acid & Aux Chemicals": dict(
  areas=["acid farm","chemical unloading apron","dilution house"],
  topics=[
   ("dilution exotherm spiking {a} °C above the safe band","a tempered-water dilution loop with rate limiter","dilution skid DS-{n}"),
   ("hose couplings weeping during {a} of every {b} tanker receipts","dry-break couplers with a drip-containment kerb","unloading bay {n}"),
   ("proportioning pump calibration drifting {a}% monthly","an auto-calibration check against a master flowmeter","proportioning pump PP-{n}"),
   ("scrubber pH excursions during caustic transfer","an interlock tying caustic transfer to scrubber pH","scrubber loop {n}"),
  ]),
 "Utilities (Power & Steam)": dict(
  areas=["powerhouse","compressor house","cooling tower farm","DM plant"],
  topics=[
   ("boiler O2 trim sitting at {a}% against the {b1}% target","an O2-trim auto-control retune with CO cross-limit","boiler B-{n}"),
   ("compressed-air leaks estimated near {a}0 cfm across the area","an ultrasonic leak survey with tagging and a weekly fix list","compressor house header {n}"),
   ("condensate return languishing at {c}0% versus design","a steam-trap audit with condensate line re-routing","condensate recovery pump {n}"),
   ("cooling tower approach widened to {a} °C","fill replacement plus a fan VFD on the worst cell","cooling tower cell {n}"),
   ("power factor dipping below 0.9{a} on peak shifts attracting penalty","capacitor bank tuning with automatic step switching","substation {n} capacitor bank"),
   ("DM regeneration chemical use {a}% above norm","a counter-current regeneration trial on the beds","DM train {n}"),
   ("chiller approach creeping {a} °C above commissioning value","a condenser tube brushing routine with approach trending","chiller CH-{n}"),
  ]),
 "ETP & Environment": dict(
  areas=["effluent treatment plant","aeration deck","sludge yard","MEE building"],
  topics=[
   ("aeration DO hunting between {a} and {b1} mg/l","DO-based blower VFD control with most-open-valve logic","aeration blower AB-{n}"),
   ("clarifier sludge blanket rising on rain days","a storm-flow diversion sequence to the guard pond","clarifier {n}"),
   ("centrifuge polymer dose {a}% above benchmark","jar-test based polymer auto-dosing","sludge centrifuge {n}"),
   ("MEE downtime of {a} hours/month on scaling","an antiscalant trial with on-line conductivity watch","MEE train {n}"),
   ("COD analyser drift forcing manual cross-checks every {a} hours","a scheduled auto-validation against grab samples","COD analyser station {n}"),
  ]),
 "Maintenance": dict(
  areas=["central workshop","lube room","motor repair bay","conveyor gallery"],
  topics=[
   ("repeat bearing failures on {a} pumps in {b1} months","a vibration-route consolidation with severity-based work orders","pump cluster area {n}"),
   ("belt tracking faults causing {a} stoppages a week","a self-aligning idler retrofit on the worst pulleys","conveyor drive CV-{n}"),
   ("grease consumption untracked across {a}0 lube points","a QR-coded lube route with right-quantity guns","lube route {n}"),
   ("gearbox oil sampling overdue on {c}0% of critical drives","an oil-analysis contract with traffic-light reporting","gearbox GB-1{n}"),
   ("crane brake adjustments needed every {a} weeks","an automatic brake-wear indicator with limit alarm","overhead crane bay {n}"),
   ("HT motor winding temperatures trending {a} °C up year-on-year","a thermography round folded into the operator checklist","HT motor M-2{n}"),
  ]),
 "Quality & Lab": dict(
  areas=["central QC lab","physical testing room","wet lab","sample receipt counter"],
  topics=[
   ("sample turnaround stretching to {a} hours against the {b1}-hour promise","barcode sample tracking from floor to LIMS","sample receipt counter {n}"),
   ("retest rate at {a}% due to conditioning lapses","a conditioning-room door interlock with timer logging","conditioning room {n}"),
   ("manual register entries delaying LIMS updates by a shift","tablet-based result entry at the tester","LIMS terminal row {n}"),
   ("inter-tester variation of {a}% on tenacity","correlation runs with a golden-fibre standard each shift","tenacity tester bank {n}"),
   ("sample mix-ups about {a} times a month at receipt","a sequenced rack system with slot lights","sample rack line {n}"),
  ]),
 "Packing & Baling": dict(
  areas=["baling hall","despatch dock","finished goods godown"],
  topics=[
   ("strap breakages causing {a} re-pressings per shift","closed-loop strap tension control","bale press BP-{n}"),
   ("bale weight variation of ±{a} kg slipping past the check","an inline checkweigher with auto-reject","bale conveyor merge {n}"),
   ("stencil smudging on {a}% of bales in humid weeks","quick-dry ink with a print-and-apply labeller trial","stencil printer {n}"),
   ("dock congestion adding {min} minutes per truck","a dock slot-booking board with staging lanes","despatch dock bay {n}"),
   ("wrap film tears exposing roughly {a} bales a week","a pre-stretch carriage upgrade on the wrapper","wrap dispenser {n}"),
  ]),
 "Safety": dict(
  areas=["plant main gate","contractor assembly point","tank farm perimeter","maintenance workshop"],
  topics=[
   ("near-miss reports averaging only {a}/month suggesting under-reporting","a one-tap mobile near-miss app with shift leaderboards","reporting kiosk zone {n}"),
   ("LOTO deviations found in {a} of the last {b1} audits","photo-verified LOTO with QR point tags","LOTO station area {n}"),
   ("gas detector calibration overdue at {a} points","a calibration-due dashboard wired to the F&G panel","gas detection loop {n}"),
   ("forklift-pedestrian conflicts at the godown junction","a segregated pedestrian corridor with spring gates","godown junction {n}"),
   ("hydrant pressure dropping to {a}.5 bar at the farthest point","ring-main looping plus a jockey-pump pressure alarm","hydrant ring sector {n}"),
   ("work-at-height kit returns missing inspection tags {a} times a month","a tagged kit-issue counter with photo log","height-kit store {n}"),
  ]),
}
DEFAULT = D["Maintenance"]

BENEFIT = {
 "Energy Conservation": ["cutting specific power by {p}%","saving roughly {kwh} kWh a day","trimming steam use by about {p}%"],
 "Safety": ["removing a recurring injury risk","closing a long-standing audit point","improving emergency readiness measurably"],
 "Quality": ["lifting first-pass yield by {p}%","cutting customer complaints on this count","tightening process capability on the parameter"],
 "Cost Reduction": ["saving consumables worth the estimate below","cutting rework cost visibly","reducing contract-labour hours on the job"],
 "Sustainability": ["saving about {kl} kL of water a day","cutting effluent load at source","reducing hazardous waste generation"],
 "Productivity": ["recovering roughly {p}% machine availability","shortening the changeover by {min} minutes","de-bottlenecking the section"],
 "Digitalization": ["replacing manual logs with live data","giving shift in-charges real-time visibility","enabling analytics on a blind spot"],
 "5S & Housekeeping": ["freeing floor space and search time","making abnormalities visible at a glance","cutting material-hunting time each shift"],
}

QA = [  # paired (question, matching reply)
 ("How did you arrive at the figures — from the log book or measured?", "Took them from the shift log for the last month — can attach the sheet."),
 ("Will this need a shutdown window, or can it be done on the run?", "No shutdown needed, we can do it during the weekly cleaning slot."),
 ("Have you checked with the downstream section whether this affects their step?", "Yes, discussed with their in-charge informally; they are supportive."),
 ("What is the rough cost of the hardware you are proposing?", "Ballpark under the estimate I gave; vendor quote attached to the idea."),
 ("Was this tried before on any other line? What happened?", "Tried partially in {yr} but stopped when the line was modified; this version fixes that gap."),
 ("Does this need any change to the SOP or operator training?", "Only a one-page SOP addition; I can draft it."),
 ("Is the payback based on current rates or last year's?", "Current rates — used this quarter's tariff sheet."),
 ("Can you share a photo or reading from the last occurrence?", "Uploaded the meter snapshot from last Tuesday's B shift."),
 ("Who maintains this after installation — shift crew or central maintenance?", "Shift crew can handle it; just one point added to the route."),
 ("Any permit implications for the installation work?", "Only a cold-work permit; checked with the safety officer."),
]
PEER = [
 "Facing the same on our line — strongly support this.",
 "Good catch, this has been pending for long.",
 "We did something similar in {plant}; happy to share drawings.",
 "Adding my endorsement — the numbers match what we see too.",
 "Please consider rolling this to all units if the trial works.",
]

EVAL_ADV = [
 "Numbers check out against the {dept} log; recommend advancing. Impact justifies the effort.",
 "Sound proposal with low execution risk. Suggested a small trial before full rollout; submitter agreed.",
 "Verified the baseline with the area engineer. Benefit is conservative if anything — advance.",
 "Feasible with in-house resources. Asked for a vendor quote to firm up the estimate; otherwise clear.",
 "Good problem definition and the fix addresses the root cause, not the symptom. Recommend.",
 "Cross-checked with last quarter's downtime Pareto — this item features. Worth doing.",
]
EVAL_REJ = [
 "Benefit does not cover the modification cost at current volumes; parking it. Revisit if line loading rises.",
 "Same change was trialled earlier and reversed due to process upsets; root cause differs from stated.",
 "Overlaps with the capex project already sanctioned for this section; closing as duplicate scope.",
 "Safety review flagged the proposed bypass as unacceptable; cannot advance in this form.",
 "Savings estimate could not be substantiated from the meter data shared; submitter to rework basis.",
]
APPR_NOTES = [
 "Approved. Fund from the plant CI budget; finance to verify savings after 90 days of stable running.",
 "Approved with condition: trial on one line first, review results in the monthly CI meeting before rollout.",
 "Approved. Coordinate the installation with the planned maintenance window to avoid production loss.",
 "Approved. Good cross-department coordination already done; recognise the team at the town hall.",
 "Approved. Procurement to use the rate contract vendor; target commissioning within the quarter.",
]

def bundle(r, dv, code):
    """one coherent slot bundle per idea — used by BOTH title and body"""
    n = dict(a=r.choice([2,3,4,5,6,8,12,15,18,25]), b=r.choice([12,16,24,48,72]),
             b1=r.choice([6,8,10,12]), n=r.randint(1,48), c=r.choice([3,4,5,6,7]),
             p=r.choice([3,5,7,8,10,12,15]), kwh=r.choice([180,260,340,420,650,900]),
             kl=r.choice([8,15,25,40,60]), min=r.choice([10,15,20,30,45]))
    sym_t, fix, equip_t = r.choice(dv["topics"])
    return dict(n=n, sym=sym_t.format(**n), fix=fix, equip=equip_t.format(**n),
                area=r.choice(dv["areas"]),
                shift=r.choice(["A shift","B shift","C shift","the night shift","shift change"]),
                period=r.choice(["the last three months","this quarter","the monsoon months","the past six weeks","the last two stock-takes"]),
                crew=r.choice(["the operators","our shift crew","the area technicians","the line team"]))

TITLE_PAT = [
 "{Fix} on {equip}",
 "Fix {symshort} — {equip}",
 "Stop {symshort} in the {area}",
 "{Fix} for the {area}",
 "Reduce {symshort} ({equip})",
 "{Fix} at {plant} {area}",
 "Cut {symshort} on {equip}",
]
def make_title(r, b, plant, used):
    symshort = b["sym"].split(" against")[0].split(" instead")[0].split(" versus")[0].split(" suggesting")[0]
    symshort = (symshort[:1].lower() if len(symshort)>1 and symshort[1].islower() else symshort[:1])+symshort[1:70].rstrip(" .,-")
    Fix = b["fix"][:1].upper()+b["fix"][1:]
    pats = TITLE_PAT[:]
    r.shuffle(pats)
    for p in pats:
        t = p.format(Fix=Fix, equip=b["equip"], area=b["area"], plant=plant, symshort=symshort)[:120]
        if t not in used:
            used.add(t); return t
    quals = [f" — phase {r.randint(2,4)}", f" ({plant} unit)", f" — line {r.randint(1,16)}",
             f" — {r.choice(['east','west','north','south'])} bay", f" — cell {r.randint(1,9)}"]
    base = t
    for q in quals:
        if base + q not in used:
            t = base + q; used.add(t); return t
    i = 2
    while f"{base} — rev {i}" in used: i += 1
    t = f"{base} — rev {i}"; used.add(t); return t

def make_body(r, b, track, plant, dept, est, length):
    n = b["n"]; sym, fix, equip, area = b["sym"], b["fix"], b["equip"], b["area"]
    shift, period, crew = b["shift"], b["period"], b["crew"]
    ben = r.choice(BENEFIT.get(track, BENEFIT["Cost Reduction"])).format(**n)

    if length == "oneliner":
        return (f"{sym[0].upper()+sym[1:]} at {equip}.", f"Proposed: {fix}.")
    if length == "brief":
        lead = r.choice([f"In the {area} we have", f"On {equip} we keep seeing", f"The {area} team has been living with"])
        tail = r.choice([f"{crew[0].upper()+crew[1:]} have flagged it through {period}.",
                         f"It has been on the shift handover notes through {period}.",
                         f"The area engineer has logged it repeatedly over {period}."])
        prob = f"{lead} {sym}, most visibly during {shift}. {tail}"
        sol  = r.choice([f"Proposal: {fix} at {equip}. Expected to help by {ben}; the ₹{est} L/yr estimate is at current rates.",
                         f"Suggesting {fix} on {equip} — should help by {ben}. Savings of ₹{est} L/yr worked out at current rates.",
                         f"Fix proposed: {fix} ({equip}). Benefit: {ben}; estimate ₹{est} L/yr at today's rates."])
        return prob, sol
    if length == "medium":
        prob = (f"For {period} the {area} has been seeing {sym}, traced to {equip} and showing up mainly on {shift}. "
                f"Beyond the direct loss it creates follow-on work for {crew}, and the current workaround is manual and depends on who is on duty. "
                f"The shift log extract attached shows the trend clearly.")
        sol  = (f"Proposed solution: {fix}, implemented at {equip}. This removes the manual dependence and attacks the root cause rather than the symptom. "
                f"Benefit expected: {ben}. The ₹{est} L/yr estimate uses this quarter's rates; the work can ride on the next planned window at {plant}.")
        return prob, sol
    if length == "high":
        prob = (f"Background: {equip} in the {area} at {plant} has had a recurring issue through {period} — {sym}. "
                f"We pulled the shift logs and the pattern concentrates around {shift}, worsening after every changeover. "
                f"The {dept} team currently absorbs it with extra rounds, and {crew} have raised it verbally several times; this idea formalises it with data attached. "
                f"If left as is, the loss compounds and the workaround keeps a person tied up every shift.")
        sol  = r.choice([
                (f"Proposal: {fix} at {equip}. Plan in three steps — (1) a one-week baseline with the instrument we already have, "
                 f"(2) implementation during the next maintenance window under a standard permit, (3) a two-week monitored trial with daily readings shared to the CI board. "
                 f"Expected benefit: {ben}, which is the basis of the ₹{est} L/yr estimate. "
                 f"No SOP rewrite beyond a one-page addition; {crew} can own it after handover."),
                (f"What we propose: {fix}, fitted at {equip}. We would first lock a baseline for a week, then install in the planned window, then run a fortnight's trial with readings reviewed daily by the area engineer. "
                 f"The benefit case is {ben} — that is where the ₹{est} L/yr comes from, computed at current rates. "
                 f"Maintenance ownership stays with {crew}; only one checklist point gets added."),
                (f"Suggested fix: {fix} on {equip}. Execution is low-risk — baseline week, permit-controlled installation, monitored trial — and reversible if the trial disappoints. "
                 f"Benefit: {ben}; the ₹{est} L/yr estimate deliberately excludes the avoided overtime, so it is conservative. "
                 f"After handover {crew} run it as part of the normal route.")])
        return prob, sol
    prob = (f"BACKGROUND\nThe {area} at {plant} runs round the clock and {equip} sits on the critical path for {dept}. "
            f"Over {period} we have recorded {sym}. The occurrences cluster around {shift}; the log extract attached to this idea shows the trend.\n\n"
            + r.choice([
             f"OBSERVATION\nRoot-cause discussion with {crew} points to the current setup rather than operating discipline: the arrangement was adequate at older throughput but not at today's loading. It also triggers extra rounds and occasional overtime for the {dept} crew. We compared notes with the corresponding section at another unit; they see a milder version of the same issue, so a proven fix here is replicable.",
             f"OBSERVATION\nA small why-why session with {crew} traced it to the equipment condition rather than how it is operated. Each occurrence pulls someone off their round, and twice last month it spilled into overtime. A sister unit reports the same symptom at lower intensity, so whatever we prove here travels.",
             f"OBSERVATION\nWe instrumented one occurrence end-to-end with {crew}: the trigger is mechanical, not procedural. The hidden cost is attention — the workaround needs a person nearby through {shift}. Bench-marking against the corresponding section elsewhere shows we are the outlier on this parameter."]))
    sol  = (f"PROPOSAL\nImplement {fix} at {equip}. Scope covers supply, installation during a planned window, and a monitored trial.\n\n"
            f"EXECUTION PLAN\n1. One-week baseline with existing instrumentation to lock the before-numbers.\n"
            f"2. Installation under a standard work permit — no production interruption beyond the window already planned.\n"
            f"3. Two-week trial with readings logged each shift and reviewed by the area engineer.\n"
            f"4. Handover to {crew} with a one-page SOP addition and a point on the autonomous-maintenance checklist.\n\n"
            f"EXPECTED BENEFIT\n{ben[0].upper()+ben[1:]}; the ₹{est} L/yr figure is at current rates and conservative — it ignores avoided overtime. "
            f"If the trial holds, the package can be replicated at the other units, multiplying the benefit across the group.")
    return prob, sol

# ============================================================================
# 1. REWRITE IDEAS
# ============================================================================
keep = {r[0] for r in cur.execute(
    "SELECT idea_code FROM ideas WHERE id IN (SELECT DISTINCT idea_id FROM idea_keywords)")}
ideas = cur.execute("""SELECT i.id, i.idea_code code, d.name dept, t.name trk, p.name plant,
                              i.estimated_savings est
                       FROM ideas i JOIN departments d ON d.id=i.department_id
                       JOIN tracks t ON t.id=i.track_id JOIN plants p ON p.id=i.plant_id
                       ORDER BY i.id""").fetchall()
used_titles = set(keep)  # protect walkthrough titles
for r0 in cur.execute("SELECT title FROM ideas WHERE idea_code IN (%s)" %
                      ",".join("?"*len(keep)), list(keep)):
    used_titles.add(r0[0])

# choose exactly 8 one-liner ideas deterministically
onearr = sorted(ideas, key=lambda x: hashlib.md5(x["code"].encode()).hexdigest())[:8]
oneliners = {x["code"] for x in onearr if x["code"] not in keep}

updates = []
for it in ideas:
    if it["code"] in keep:
        continue
    r = rng_for(it["code"])
    dv = D.get(it["dept"], DEFAULT)
    b = bundle(r, dv, it["code"])
    title = make_title(r, b, it["plant"], used_titles)
    if it["code"] in oneliners: length = "oneliner"
    else:
        roll = r.random()
        length = "brief" if roll < .18 else "medium" if roll < .58 else "high" if roll < .88 else "detailed"
    prob, sol = make_body(r, b, it["trk"], it["plant"], it["dept"],
                          round(it["est"] or 1.0, 1), length)
    area = f"{b['area']} · {it['plant']}"
    updates.append((title, prob, sol, area[:120], it["id"]))
cur.executemany("""UPDATE ideas SET title=?, problem_statement=?, proposed_solution=?,
                   area_of_impact=?, updated_at=date('now') WHERE id=?""", updates)
print("ideas rewritten:", len(updates), "| one-liners kept:", len(oneliners))

# ============================================================================
# 2 & 3. EVALUATOR / APPROVER NOTES
# ============================================================================
ev = cur.execute("""SELECT e.id, e.decision, i.idea_code code, d.name dept
                    FROM evaluations e JOIN ideas i ON i.id=e.idea_id
                    JOIN departments d ON d.id=i.department_id""").fetchall()
evu = []
for e in ev:
    r = rng_for(e["code"], "ev"+str(e["id"]))
    pool = EVAL_REJ if e["decision"] == "reject" else EVAL_ADV
    evu.append((r.choice(pool).format(dept=e["dept"]), e["id"]))
cur.executemany("UPDATE evaluations SET comment=? WHERE id=?", evu)

# align evaluation total_score with the idea's reward score (same 100-point scale)
ev2 = cur.execute("""SELECT e.id, i.idea_code code, i.score FROM evaluations e
                     JOIN ideas i ON i.id=e.idea_id WHERE i.score IS NOT NULL""").fetchall()
cur.executemany("UPDATE evaluations SET total_score=? WHERE id=?",
    [(max(30, min(98, (e["score"] or 70) + rng_for(e["code"], "ts"+str(e["id"])).randint(-4, 4))), e["id"]) for e in ev2])
print("evaluation scores aligned to idea scale:", len(ev2))

ap = cur.execute("""SELECT a.id, i.idea_code code FROM approvals a
                    JOIN ideas i ON i.id=a.idea_id""").fetchall()
apu = [(rng_for(a["code"], "ap"+str(a["id"])).choice(APPR_NOTES), a["id"]) for a in ap]
cur.executemany("UPDATE approvals SET comment=? WHERE id=?", apu)
print("evaluation notes:", len(evu), "| approval notes:", len(apu))

# ============================================================================
# 4. CLARIFICATION THREADS (replace generic comments)
# ============================================================================
cur.execute("DELETE FROM comments")
# reviewer pool per plant
revpool = {}
for r0 in cur.execute("""SELECT g.plant_id, gm.user_id FROM groups g
                         JOIN group_members gm ON gm.group_id=g.id"""):
    revpool.setdefault(r0["plant_id"], []).append(r0["user_id"])
allu = [r0[0] for r0 in cur.execute("SELECT id FROM users")]

rows2 = cur.execute("""SELECT i.id, i.idea_code code, i.submitter_id sub, i.plant_id,
                              i.submitted_date sd, s.code stage, d.name dept, p.name plant
                       FROM ideas i JOIN stages s ON s.id=i.current_stage_id
                       JOIN departments d ON d.id=i.department_id
                       JOIN plants p ON p.id=i.plant_id
                       WHERE s.code IN ('evaluation','approval','implementation','done','on_hold')""").fetchall()
crow = []
for it in rows2:
    r = rng_for(it["code"], "cl")
    base = dt.date.fromisoformat(it["sd"] or "2025-06-01")
    pool = revpool.get(it["plant_id"]) or allu
    roll = r.random()
    qa = 2 if roll < .12 else 1 if roll < .55 else 0
    t = base
    used_q = set()
    for q in range(qa):
        asker = r.choice(pool)
        qi = r.randrange(len(QA))
        while qi in used_q: qi = r.randrange(len(QA))
        used_q.add(qi)
        question, reply = QA[qi]
        t = t + dt.timedelta(days=r.randint(1, 12))
        crow.append((it["id"], asker, question, t.isoformat()))
        t = t + dt.timedelta(days=r.randint(1, 4))
        crow.append((it["id"], it["sub"], reply.format(yr=r.choice([2022,2023,2024])), t.isoformat()))
    if r.random() < .15:
        t = t + dt.timedelta(days=r.randint(1, 9))
        crow.append((it["id"], r.choice(allu), r.choice(PEER).format(plant=it["plant"]), t.isoformat()))
cur.executemany("INSERT INTO comments(idea_id,user_id,text,ts) VALUES (?,?,?,?)", crow)
print("clarification/peer comments:", len(crow))

con.commit()

# ---- verification ----
print("\n--- verify ---")
print("distinct titles:", cur.execute("SELECT COUNT(DISTINCT title) FROM ideas").fetchone()[0],
      "of", cur.execute("SELECT COUNT(*) FROM ideas").fetchone()[0])
print("avg problem len:", cur.execute("SELECT ROUND(AVG(LENGTH(problem_statement))) FROM ideas").fetchone()[0])
print("one-liner-ish (<90 chars problem):",
      cur.execute("SELECT COUNT(*) FROM ideas WHERE LENGTH(problem_statement)<90").fetchone()[0])
print("eval notes empty:", cur.execute("SELECT COUNT(*) FROM evaluations WHERE comment=''").fetchone()[0])
print("appr notes empty:", cur.execute("SELECT COUNT(*) FROM approvals WHERE comment=''").fetchone()[0])
print("comments:", cur.execute("SELECT COUNT(*) FROM comments").fetchone()[0])
con.close()
