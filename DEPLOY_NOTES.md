# Kaizen — live-data portal deploy notes

## What this is
The voice app and the data layer are now ONE Flask app (`app.py`). It serves:

| URL | What |
|---|---|
| `/` (also `/employee`, `/portal`) | Employee / Reviewer / Approver portal — wired to live data |
| `/manager` | CI Manager console — wired to live data |
| `/mvp` | Original standalone voice MVP page (unchanged) |
| `/api/converse`, `/api/stt`, `/api/tts` | Voice stack (unchanged — Sarvam + Claude) |
| `/api/...` | New read-only data endpoints over kaizen.db |

## To deploy
Push these to `dineshc77/Kaizen` (main) — Render auto-redeploys:
- `app.py` (replaces existing)
- `kaizen.db` (new, ~20 MB — fine on ephemeral disk, reads only)
- `Kaizen_User_Reviewer_Approver.html` (replaces existing)
- `Kaizen_CI_Manager_Console.html` (new)
- `requirements.txt` (adds flask-compress)
- `templates/index.html`, `Procfile`, `render.yaml` (unchanged, included for completeness)

Manager console URL after deploy: `https://kaizen-d5pb.onrender.com/manager`

## How the wiring works
Both HTML files still render their built-in demo data instantly, then `loadLive()`
fetches real data and swaps it in. If the backend is unreachable (e.g. opening the
file directly from disk), they silently keep the demo data — nothing breaks.

**Employee portal** (scoped to Nagda demo personas — employee Ganesh Patil,
evaluator Sneha Desai, approver Rahul Gupta):
- feed, my-ideas (27 real ideas), success stories, evaluation queue (450),
  approval queue (276), leaderboard — all real
- idea detail lazy-fetches `/api/idea/<code>`: real problem/solution text,
  benefits, workflow history
- the 5 seed dup-check ideas (KZ-2026-0521..25) are kept so voice duplicate
  detection still matches

**Manager console** (all 7 plants):
- fetches all 20,793 ideas compact + gzipped (~360 KB wire) so every existing
  chart/KPI aggregates over the real dataset
- real cycle times, top contributors, published stories, rewards
- on_hold stage (993 ideas) now visible in funnel + labels
- long lists capped for DOM sanity: intake 12 cards, pipeline 80 (breaches
  first), kanban selector 100, rewards 30, stories 30
- intake detail lazy-fetches real problem/solution, attachments, language/source

## Final pass (June 11, later): DB extended so EVERYTHING on screen is DB data
kaizen.db now also contains: workflow_tracks (12), idea_classifications (17,771),
implementation_tasks (30,790), award_definitions (10), award_highlight_rules (7),
idea_keywords (62) and 5 walkthrough ideas. Created by extend_db_for_poc.py
(included, idempotent). Both UIs now show a brief loading screen and render ONLY
live DB data; the built-in demo dataset is used solely as an offline fallback.
See Kaizen_Data_Provenance.xlsx for the screen-by-screen proof sheet.

## Earlier sweep (June 11)
- Endorsement & comment counts on every card (DB endorsements/comments)
- Profile: joined year, reward points, award counts, areas — all from users/
  reward_awards tables
- Reviewer screen: real problem/solution text, real attachment counts, real
  clarification comments, real evaluation criteria (impact/effort/feasibility)
- Approver screen: real evaluator names + their actual scores
- Console groups screen: the 14 real plant panels with their 62 real members
- All reviewer/assignee name pools (intake designer, pipeline, kanban): real
  group members from the DB
- Rewards engagement rule: computed from real endorsements + comments
- Voice greeting uses the live persona's name
- The 5 dup-check seed ideas stay in memory for voice duplicate detection but
  no longer appear in any list

## Still mock (by design, per handoff)
- All writes — buttons show toasts only; persistence comes with Postgres
- Kanban task checklists: the 10-step implementation template (no tasks table
  exists in the DB) — but assignees, progress and dates derive from real data
- Intake LLM classification confidences: deterministic placeholders (the
  designed LLM pipeline isn't built yet; no confidence data exists)
- Login — role switcher uses seeded personas

## New API endpoints (read-only)
`/api/meta` `/api/stats` `/api/ideas` `/api/idea/<code>` `/api/feed`
`/api/my-ideas` `/api/stories` `/api/leaderboard` `/api/evaluation-queue`
`/api/approval-queue` `/api/dashboard-stats` `/api/rewards` `/api/groups`
`/api/portal/all-ideas` `/api/portal/bootstrap`
