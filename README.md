# Leadership Now Slate Midterm Tracker

## Repository layout
- `data/` — master data, committed after every update so git history doubles as the change log:
  - `entries_core.json` — one record per candidate: static fields, ratings, polls, finance, results, manual market entries, and each entry's `market_ids` (Polymarket/Kalshi market identifiers)
  - `market_history.csv` — daily Polymarket and Kalshi prices pulled from their APIs (entry_id, platform, date, pct, note)
  - `national.json` — national snapshots plus time series (control odds, generic ballot, Trump approval)
- `tracker.py` — apply updates, export the workbook/CSVs/dashboard documents, pack/unpack `data/`
- `fetch_markets.py` — pulls prices from the Polymarket and Kalshi public APIs
- `dashboard/index.html` — source of the live dashboard (https://claude.ai/artifact/6BoWiCypB3FcCacosF1DNB). The page reads its data from the artifact's database (collections "entries" and "meta"), so data updates don't require republishing it.
- Generated and working files (`out/`, `entries.json`, `national.json` at the root, backups) are git-ignored. The Excel workbook and CSVs in `out/` are rebuilt by `export`; don't hand-edit them.
- The same data files are mirrored to the Midterm Analysis project (claude/tracker/) so Claude sessions can pick them up.

Quick start:

    python tracker.py unpack data      # build working copy entries.json / national.json from data/
    python fetch_markets.py today      # add today's market prices
    python tracker.py apply update.json
    python tracker.py export           # out/LNP_Midterms_Tracker.xlsx, out/csv/, out/db/
    python tracker.py pack             # write data/ back
    git add data && git commit -m "Update through <date>"

Requires Python 3.10+ with openpyxl, and curl on the PATH (used for API calls).

## Data model
One record per candidate on the November ballot ("entry"). Candidates who are not on the ballot (lost a primary, withdrew, not up this cycle) are not kept or referenced anywhere: remove them with {"kind": "remove_entry", "entry": "<id>"}, then delete the ids listed in out/db/deletions.txt from the dashboard database (ArtifactData batch, op "delete"). new_entry only accepts on-ballot candidates. Each record has static fields plus append-only histories. Nothing is overwritten: every observation is dated, so trends and before/after comparisons are always available.

Entry id format: office letter + race + last name, e.g. h-ny17-conley, s-oh-brown, g-wi-crowley.

Static fields: id, office, name, last_name (used for alphabetical sorting; set it explicitly for compound surnames, e.g. "Trone Garriott"), party, state, race, slate_status, list_tier (short / long / blank), ballot, opponents, seat_held, pres24, note, market_ids.

Histories:
- markets: observed (the price date), platform (Polymarket / Kalshi / PredictIt), pct (the candidate's own win probability, 0-100), note ("api" for API pulls; "party market (...)" when only a party contract exists)
- polls: date (field end date, or release date if no field dates), observed (when added), pollster, sponsor, sponsor_lean (D / R / blank = independent), start_date, end_date, release_date, sample, population (LV/RV/A), cand_pct, opp_name, opp_pct, others, text, url. Margin = cand_pct - opp_pct.
- ratings: observed, source (Cook / Sabato / Inside Elections), rating, as_of
- finance: observed, report_date, cand_coh, opp_coh (dollars), text
- result: status (pending / called / runoff / uncalled), won (true/false), cand_pct, opp_pct, called_by, called_at, note

National (national.json): "snapshots" (one per collection date) and "series":
- house_d / senate_d: daily Democratic control odds {date, Polymarket, Kalshi}
- generic_polls {pollster, start_date, end_date, sample, d, r, url}; generic_avg {source, date, d, r, margin, url}
- approval_polls {pollster, end_date, approve, disapprove, ...}; approval_avg {source, date, approve, disapprove}

## How trends are shown (polling first, markets secondary)
- Dashboard: polling leads everywhere. National strip opens with the generic-ballot and approval polling averages and a count of slate candidates leading in recent polls; market control odds follow. Trend charts put generic ballot and approval polls first, market control odds second (smaller). Race board columns run: polling average (candidate's margin across polls ending in the last 30 days, or the latest poll if none), poll shift (change in the 21-day trailing average over the chosen window), ratings, then market odds. Default sort is closest polling margin. Movers: biggest polling shifts first, market moves second. Race detail: poll-margin chart and poll list first, market chart second.
- Excel: "Latest" lists poll average, poll count, latest poll margin and latest poll before ratings and market odds; tab order is Poll trend, Polls, Market trend, Market history, then the rest.

## Routine refresh (each session)
1. Rebuild the working copy: `python tracker.py unpack data` (or point it at a folder holding the project copies).
2. Markets: `python fetch_markets.py today` (adds today's Polymarket and Kalshi prices for every mapped race; runs `history <start>` instead to fill a gap of several days) and `python fetch_markets.py national <start>` for control odds. New entries need `python fetch_markets.py map` first.
   - Thin-market spikes (a price more than 25 pts from the surrounding two-week median) are dropped automatically.
3. Polls, ratings, finance, results, national averages: write an update file (below) and run `python tracker.py apply update.json`. The script checks every entry id and field and saves nothing if any line is wrong. A second observation for the same source on the same day replaces the first.
4. `python tracker.py export` — rebuilds out/LNP_Midterms_Tracker.xlsx, out/csv/*.csv and out/db/ (changed documents only).
5. Push out/db/entries/*.json and out/db/meta/*.json to the dashboard with ArtifactData "batch" (op "set", collection "entries" or "meta", doc_id = file name, file_path = the file, up to 50 per batch). Every existing document needs if_version: get current versions with an ArtifactData "list" first.
6. `python tracker.py pack` (writes data/), commit data/ to git, and mirror data/entries_core.json, data/market_history.csv and data/national.json to the project's claude/tracker/. Then `python tracker.py clear-changed`.

Update file example:

    {
      "observed": "2026-10-15",
      "updates": [
        {"kind": "poll",    "entry": "h-ny17-conley", "pollster": "Siena", "sponsor": null, "sponsor_lean": null,
         "start_date": "2026-10-09", "end_date": "2026-10-12", "date": "2026-10-12", "sample": "600 LV", "population": "LV",
         "cand_pct": 49, "opp_name": "Lawler", "opp_pct": 45,
         "text": "Siena Oct 9-12 (600 LV): Conley 49, Lawler 45", "url": "https://..."},
        {"kind": "market",  "entry": "h-ny17-conley", "platform": "PredictIt", "pct": 66, "note": "manual"},
        {"kind": "rating",  "entry": "h-va01-taylor", "source": "Cook", "rating": "Toss Up", "as_of": "2026-10-14"},
        {"kind": "finance", "entry": "h-wi03-cooke", "report_date": "2026-09-30", "cand_coh": 2400000,
         "opp_coh": 4100000, "text": "$2.4M vs $4.1M (9/30)"},
        {"kind": "field",   "entry": "h-fl22-dandiya", "set": {"list_tier": "short"}},
        {"kind": "result",  "entry": "h-nj07-bennett", "status": "called", "won": true,
         "cand_pct": 51.2, "opp_pct": 47.9, "called_by": "AP", "called_at": "2026-11-04T01:12"},
        {"kind": "new_entry", "office": "House", "name": "Jane Doe", "party": "D", "state": "XX",
         "race": "XX-01", "slate_status": "Challenger", "ballot": "On ballot", "opponents": "John Roe (R)"}
      ]
    }

National poll and average series are appended directly to national.json "series" (same field shapes as above); add a snapshot with {"kind": "national", "data": {...}} for ratings tallies and notes.

## Notes
- Market pct is always the tracked candidate's own win probability. Where only a party market exists (DE Senate, and the independent contracts for Bodnar and Osborn on Polymarket), the party/"Independent" price is used and the note says so.
- Oct 1 baseline rows without numeric toplines keep their text only; all polls added since Oct 6 carry numbers.
- Short list / long list: set tiers with "field" updates (list_tier: short / long); the dashboard adds a list filter once any tier is set.
- Tracked (non-slate) races: add with a "new_entry" whose slate_status starts with "Tracked" (e.g. "Tracked race (not on LNP slate)"). The dashboard tags them, offers a filter, and leaves them out of the slate won–lost record. Current tracked races: s-mi-elsayed (MI Senate, Abdul El-Sayed).
