#!/usr/bin/env python3
"""LNP Slate Midterm Tracker — data pipeline.

Master data lives in two JSON files (kept in the Midterm Analysis project under claude/tracker/):
  entries.json   one record per slate candidate, with append-only observation histories
  national.json  list of dated national snapshots

Commands
  python tracker.py apply UPDATE.json [--observed YYYY-MM-DD]
      Append observations / set fields from an update file (format in README.md).
      Writes the changed entry ids to out/changed.txt.
  python tracker.py export [--all]
      Rebuild outputs: out/LNP_Midterms_Tracker.xlsx, out/csv/*.csv, and
      out/db/<collection>/<id>.json (one file per document to push to the dashboard database;
      only changed docs unless --all).
  python tracker.py pack             split data for the project: data/entries_core.json + market_history.csv + national.json
  python tracker.py unpack DIR       rebuild entries.json from those project files (start of every new session)
  python tracker.py migrate          (one-time: build entries.json/national.json from data.py)
"""
import json, re, sys, os, csv, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, *a)
SCHEMA_VERSION = 1
KINDS = ("rating", "poll", "market", "finance", "result")
STATIC = ["id","office","name","last_name","party","state","race","slate_status","list_tier","ballot",
          "opponents","seat_held","pres24","note"]

def load(name, default):
    try: return json.load(open(P(name)))
    except FileNotFoundError: return default
def save(name, obj): json.dump(obj, open(P(name), "w"), ensure_ascii=False, indent=1)

def slug(office, race, name):
    o = {"House":"h","Senate":"s","Governor":"g"}[office]
    r = re.sub(r"[^a-z0-9]", "", race.lower().replace("senate","").replace("governor","").replace("special",""))
    last = re.sub(r"[^a-z]", "", name.split()[-1].lower())
    return f"{o}-{r}-{last}"

def blank_entry():
    return {k: None for k in STATIC} | {"ratings": [], "polls": [], "markets": [], "finance": [],
                                        "result": None, "updated": None}

# ---------------------------------------------------------------- migrate
def migrate():
    sys.path.insert(0, HERE)
    import data as D
    obs = D.AS_OF
    entries = {}
    for r in D.HOUSE + D.SENATE + D.GOV:
        race = r["race"].strip("()") if r["ballot"] == "On ballot" else r["race"]
        e = blank_entry()
        e.update({k: r[k] for k in ["office","name","party","state","slate_status","ballot",
                                    "opponents","seat_held","pres24","note"]})
        e["race"] = race.strip("()")
        e["id"] = slug(r["office"], e["race"], r["name"])
        for k in ("opponents","seat_held","pres24"):
            if e[k] in ("—", ""): e[k] = None
        if r["ballot"] == "On ballot":
            src_date = {"House": "2026-09-25", "Senate": "2026-09-23", "Governor": "2026-09-17"}[r["office"]]
            for src, key in (("Cook","cook"),("Sabato","sabato"),("Inside Elections","ie")):
                v = r[key]
                if v and v not in ("n/a","—"):
                    e["ratings"].append({"observed": obs, "source": src, "rating": v,
                                         "as_of": src_date if src == "Cook" else None})
            if r["poll"] and not r["poll"].startswith("No "):
                for part in re.split(r";\s+(?=[A-Z])", r["poll"]):
                    sp = "D" if re.search(r"\(D[\)-]|\(D-aligned\)", part) else ("R" if re.search(r"\(R[\)-]|R-lean", part) else None)
                    e["polls"].append({"observed": obs, "text": part, "pollster": part.split(" (")[0].split(" Sep")[0].split(" Aug")[0][:60],
                                       "sponsor_lean": sp, "end_date": None, "sample": None,
                                       "cand_pct": None, "opp_pct": None, "url": None})
            note = r["mkt_note"] or ""
            plat = "Kalshi" if note.startswith("Kalshi") else "Polymarket"
            if r["mkt"] is not None:
                e["markets"].append({"observed": obs, "platform": plat, "pct": r["mkt"], "note": note})
            elif note and note != "—":
                e["markets"].append({"observed": obs, "platform": plat, "pct": None, "note": note})
            m = re.search(r"Kalshi (?:D )?(\d+)", note)
            if m and plat != "Kalshi":
                e["markets"].append({"observed": obs, "platform": "Kalshi", "pct": int(m.group(1)), "note": ""})
            m = re.search(r"Polymarket (\d+)", note)
            if m and plat == "Kalshi":
                e["markets"].append({"observed": obs, "platform": "Polymarket", "pct": int(m.group(1)), "note": ""})
            if r["coh"] and r["coh"] not in ("n/a", "—"):
                e["finance"].append({"observed": obs, "text": r["coh"], "cand_coh": None, "opp_coh": None, "report_date": None})
            e["result"] = {"status": "pending"}
        e["updated"] = obs
        assert e["id"] not in entries, e["id"]
        entries[e["id"]] = e
    save("entries.json", {"schema_version": SCHEMA_VERSION, "entries": entries})
    N = D.NATIONAL
    snap = {"observed": obs, **N}
    save("national.json", {"schema_version": SCHEMA_VERSION, "snapshots": [snap]})
    print(f"migrated {len(entries)} entries")

# ---------------------------------------------------------------- apply
def apply(path, observed=None):
    store = load("entries.json", None); entries = store["entries"]
    nat = load("national.json", {"schema_version": SCHEMA_VERSION, "snapshots": []})
    upd = json.load(open(path))
    observed = observed or upd.get("observed") or datetime.date.today().isoformat()
    changed, errors = set(), []
    for i, u in enumerate(upd.get("updates", [])):
        kind = u.get("kind")
        if kind == "national":
            snap = {"observed": observed, **u["data"]}
            nat["snapshots"] = [s for s in nat["snapshots"] if s["observed"] != observed] + [snap]
            changed.add("meta:national"); continue
        if kind == "remove_entry":
            # Candidates not on the November ballot are dropped entirely (not archived) — the tracker only covers live races.
            if u.get("entry") in entries: del entries[u["entry"]]; changed.add("delete:" + u["entry"])
            else: errors.append(f"#{i}: unknown entry '{u.get('entry')}'")
            continue
        if kind == "new_entry":
            if u.get("ballot", "On ballot") != "On ballot":
                errors.append(f"#{i}: only candidates on the November ballot can be added"); continue
            u.setdefault("ballot", "On ballot")
            e = blank_entry(); e.update({k: u.get(k) for k in STATIC if k in u})
            e["id"] = e["id"] or slug(e["office"], e["race"], e["name"])
            e["last_name"] = e.get("last_name") or e["name"].split()[-1]
            e["result"] = {"status": "pending"}; e["updated"] = observed
            entries[e["id"]] = e; changed.add(e["id"]); continue
        eid = u.get("entry")
        if eid not in entries:
            errors.append(f"#{i}: unknown entry '{eid}'"); continue
        e = entries[eid]
        if kind == "field":
            for k, v in u["set"].items():
                if k not in STATIC or k == "id": errors.append(f"#{i}: field '{k}' not editable"); continue
                e[k] = v
        elif kind in ("rating","poll","market","finance"):
            rec = {"observed": observed} | {k: v for k, v in u.items() if k not in ("kind","entry")}
            key = kind + "s" if kind != "finance" else "finance"
            # replace a same-day duplicate (same source/platform/pollster) instead of stacking
            ident = {"rating":"source","market":"platform","poll":"text","finance":"text"}[kind]
            e[key] = [x for x in e[key] if not (x["observed"] == observed and x.get(ident) == rec.get(ident))] + [rec]
        elif kind == "result":
            e["result"] = {k: v for k, v in u.items() if k not in ("kind","entry")} | {"recorded": observed}
        else:
            errors.append(f"#{i}: unknown kind '{kind}'"); continue
        e["updated"] = observed; changed.add(eid)
    if errors:
        print("NOT SAVED — fix these:\n  " + "\n  ".join(errors)); sys.exit(1)
    save("entries.json", store); save("national.json", nat)
    os.makedirs(P("out"), exist_ok=True)
    prev = set(open(P("out","changed.txt")).read().split()) if os.path.exists(P("out","changed.txt")) else set()
    open(P("out","changed.txt"), "w").write("\n".join(sorted(prev | changed)))
    print(f"applied {len(upd.get('updates', []))} updates, {len(changed)} docs changed (observed {observed})")

# ---------------------------------------------------------------- latest helpers
def latest_ratings(e):
    out = {}
    for r in sorted(e["ratings"], key=lambda x: x["observed"]): out[r["source"]] = r["rating"]
    return out
def latest_market(e):
    ms = [m for m in e["markets"] if m.get("pct") is not None]
    if not ms: return None
    last = max(m["observed"] for m in ms)
    day = [m for m in ms if m["observed"] == last]
    day.sort(key=lambda m: 0 if m["platform"] == "Polymarket" else 1)
    return day[0]
def latest(lst, key="observed"):
    return max(lst, key=lambda x: x[key]) if lst else None

def poll_date(p): return p.get("date") or p.get("end_date") or p.get("release_date")
def margin(p):
    return round(p["cand_pct"] - p["opp_pct"], 1) if isinstance(p.get("cand_pct"), (int, float)) and isinstance(p.get("opp_pct"), (int, float)) else None
def market_series(e):
    """date -> pct, Polymarket preferred, Kalshi where Polymarket has no price that day."""
    s = {}
    for m in sorted(e["markets"], key=lambda m: 0 if m["platform"] == "Polymarket" else 1, reverse=True):
        if m.get("pct") is not None: s[m["observed"]] = m["pct"]
    return s

# ---------------------------------------------------------------- export
def export(all_docs=False):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    store = load("entries.json", None); entries = store["entries"]
    nat = load("national.json", {"snapshots": []})
    os.makedirs(P("out","csv"), exist_ok=True)
    order = {"House":0,"Senate":1,"Governor":2}
    E = sorted(entries.values(), key=lambda e: (order[e["office"]], (e.get("last_name") or e["name"].split()[-1]).lower(), e["name"].lower()))

    tables = {}
    tables["candidates"] = (STATIC + ["updated"], [[e.get(k) for k in STATIC + ["updated"]] for e in E])
    tables["ratings"] = (["entry_id","observed","source","rating","as_of"],
        [[e["id"], r["observed"], r["source"], r["rating"], r.get("as_of")] for e in E for r in e["ratings"]])
    tables["polls"] = (["entry_id","poll_date","observed","pollster","sponsor","sponsor_lean","start_date","end_date","release_date","sample","population","cand_pct","opp_name","opp_pct","margin","others","text","url"],
        sorted([[e["id"], poll_date(p), p["observed"], p.get("pollster"), p.get("sponsor"), p.get("sponsor_lean"), p.get("start_date"), p.get("end_date"), p.get("release_date"),
          p.get("sample"), p.get("population"), p.get("cand_pct"), p.get("opp_name"), p.get("opp_pct"), margin(p), p.get("others"), p.get("text"), p.get("url")]
          for e in E for p in e["polls"]], key=lambda r: (r[0], r[1] or "")))
    tables["markets"] = (["entry_id","observed","platform","pct","note"],
        [[e["id"], m["observed"], m["platform"], m.get("pct"), m.get("note")] for e in E for m in e["markets"]])
    tables["finance"] = (["entry_id","observed","report_date","cand_coh","opp_coh","text"],
        [[e["id"], f["observed"], f.get("report_date"), f.get("cand_coh"), f.get("opp_coh"), f.get("text")] for e in E for f in e["finance"]])
    tables["results"] = (["entry_id","status","won","cand_pct","opp_pct","called_by","called_at","recorded","note"],
        [[e["id"]] + [(e["result"] or {}).get(k) for k in ["status","won","cand_pct","opp_pct","called_by","called_at","recorded","note"]] for e in E if e["result"]])
    natrows = []
    for s in nat["snapshots"]:
        for sec in ("generic_ballot","approval","house","senate"):
            for it in s.get(sec, []): natrows.append([s["observed"], sec, it[0], it[1], it[2]])
        for it in s.get("generic_polls", []): natrows.append([s["observed"], "generic_poll", it[0], f"{it[3]} ({it[2]})", it[1]])
        for k, v in s.get("cook_house", {}).items(): natrows.append([s["observed"], "cook_house_ratings", k, v, ""])
    tables["national"] = (["observed","metric","source","value","as_of"], natrows)
    S = nat.get("series", {})
    tables["national_markets"] = (["date","chamber","Polymarket_D","Kalshi_D"],
        [[r["date"], ch, r.get("Polymarket"), r.get("Kalshi")] for ch, key in (("House","house_d"),("Senate","senate_d")) for r in S.get(key, [])])
    tables["generic_ballot"] = (["kind","source_or_pollster","date","start_date","sample","D","R","margin_D_minus_R","url"],
        [["average", a["source"], a["date"], None, None, a.get("d"), a.get("r"), a.get("margin"), a.get("url")] for a in S.get("generic_avg", [])] +
        [["poll", g["pollster"], g.get("end_date"), g.get("start_date"), g.get("sample"), g.get("d"), g.get("r"),
          round(g["d"] - g["r"], 1) if g.get("d") is not None and g.get("r") is not None else None, g.get("url")] for g in S.get("generic_polls", [])])
    tables["approval"] = (["kind","source_or_pollster","date","start_date","sample","approve","disapprove","net","url"],
        [["average", a["source"], a["date"], None, None, a.get("approve"), a.get("disapprove"), round(a["approve"] - a["disapprove"], 1), a.get("url")] for a in S.get("approval_avg", [])] +
        [["poll", g["pollster"], g.get("end_date"), g.get("start_date"), g.get("sample"), g.get("approve"), g.get("disapprove"), round(g["approve"] - g["disapprove"], 1), g.get("url")] for g in S.get("approval_polls", [])])
    # trend summaries (one row per entry)
    marks = ["2026-08-01","2026-08-15","2026-09-01","2026-09-15","2026-10-01"]
    trows = []
    for e in E:
        s = market_series(e)
        if not s: continue
        last_d = max(s); last = s[last_d]
        at = lambda d: s[max([x for x in s if x <= d])] if any(x <= d for x in s) else None
        d30 = (datetime.date.fromisoformat(last_d) - datetime.timedelta(days=30)).isoformat()
        d7 = (datetime.date.fromisoformat(last_d) - datetime.timedelta(days=7)).isoformat()
        chg = lambda d: round(last - at(d), 1) if at(d) is not None else None
        trows.append([e["id"], e["office"], e["name"], e["race"]] + [at(m) for m in marks] + [last, last_d, chg(d7), chg(d30), chg(marks[0])])
    tables["market_trend"] = (["entry_id","office","name","race"] + [f"pct_{m}" for m in marks] + ["pct_latest","latest_date","change_7d","change_30d","change_since_Aug1"], trows)
    prow = []
    for e in E:
        ps = [p for p in e["polls"] if margin(p) is not None and poll_date(p)]
        if not ps: continue
        ps.sort(key=poll_date); last_d = poll_date(ps[-1])
        cut = (datetime.date.fromisoformat(last_d) - datetime.timedelta(days=30)).isoformat()
        rec = [margin(p) for p in ps if poll_date(p) > cut]; old_ = [margin(p) for p in ps if poll_date(p) <= cut]
        rec_nd = [margin(p) for p in ps if poll_date(p) > cut and not p.get("sponsor_lean")]
        avg = lambda a: round(sum(a)/len(a), 1) if a else None
        prow.append([e["id"], e["office"], e["name"], e["race"], len(ps), poll_date(ps[0]), last_d, margin(ps[-1]), ps[-1].get("pollster"),
                     avg(rec), len(rec), avg(rec_nd), avg(old_), len(old_), round(avg(rec) - avg(old_), 1) if rec and old_ else None])
    tables["poll_trend"] = (["entry_id","office","name","race","polls","first_poll","latest_poll","latest_margin","latest_pollster",
                             "avg_margin_last30d","n_last30d","avg_margin_last30d_nonpartisan","avg_margin_before","n_before","shift"], prow)

    for name, (cols, rows) in tables.items():
        with open(P("out","csv", f"{name}.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(cols); w.writerows(rows)

    # Excel: Latest view (one row per candidate) + one tab per table + guide
    wb = Workbook(); wb.remove(wb.active)
    hdr = Font(name="Arial", bold=True, color="FFFFFF"); fill = PatternFill("solid", fgColor="1F3A5F"); body = Font(name="Arial", size=10)
    def sheet(title, cols, rows, widths=None):
        ws = wb.create_sheet(title); ws.append(cols)
        for c in ws[1]: c.font = hdr; c.fill = fill; c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in rows: ws.append(["" if v is None else v for v in r])
        for row in ws.iter_rows(min_row=2):
            for c in row: c.font = body; c.alignment = Alignment(wrap_text=True, vertical="top")
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(c, max(10, min(50, len(c) + 4)))
        ws.freeze_panes = "B2"; ws.auto_filter.ref = ws.dimensions
        return ws
    latest_cols = ["id","office","name","last_name","party","state","race","slate_status","list_tier","opponents",
                   "poll_avg_margin_30d","polls_30d","latest_poll_margin","latest_poll_date","latest_poll",
                   "Cook","Sabato","Inside Elections","market_pct","market_platform","market_date",
                   "seat_held","pres24","cash_on_hand","result_status","won","cand_pct","opp_pct","note","updated"]
    today = datetime.date.today().isoformat(); cut30 = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    lrows = []
    for e in E:
        rt = latest_ratings(e); m = latest_market(e); p = max(e["polls"], key=lambda x: (poll_date(x) or "", x["observed"])) if e["polls"] else None; f = latest(e["finance"]); res = e["result"] or {}
        num = sorted([q for q in e["polls"] if margin(q) is not None and poll_date(q)], key=poll_date)
        rec = [margin(q) for q in num if poll_date(q) >= cut30]
        lp = num[-1] if num else None
        lrows.append([e["id"], e["office"], e["name"], e.get("last_name"), e["party"], e["state"], e["race"], e["slate_status"], e["list_tier"], e["opponents"],
                      round(sum(rec)/len(rec), 1) if rec else None, len(rec), lp and margin(lp), lp and poll_date(lp), (lp or p) and (lp or p)["text"],
                      rt.get("Cook"), rt.get("Sabato"), rt.get("Inside Elections"), m and m["pct"], m and m["platform"], m and m["observed"],
                      e["seat_held"], e["pres24"], f and f["text"],
                      res.get("status"), res.get("won"), res.get("cand_pct"), res.get("opp_pct"), e["note"], e["updated"]])
    W = {"id":18,"name":22,"race":14,"opponents":32,"latest_poll":55,"poll_avg_margin_30d":12,"latest_poll_margin":12,"cash_on_hand":24,"note":45,"pres24":16}
    sheet("Latest", latest_cols, lrows, W)
    NAMES = {"market_trend":"Market trend","poll_trend":"Poll trend","candidates":"Candidates","ratings":"Ratings","polls":"Polls",
             "markets":"Market history","finance":"Finance","results":"Results","national":"National snapshots",
             "national_markets":"Control odds history","generic_ballot":"Generic ballot","approval":"Trump approval"}
    for name in ["poll_trend","polls","market_trend","markets","ratings","finance","results","candidates","national_markets","generic_ballot","approval","national"]:
        cols, rows = tables[name]
        sheet(NAMES[name], cols, rows, {"text":60,"note":40,"entry_id":18,"opponents":32,"value":30,"source":26})
    g = wb.create_sheet("Guide")
    for line in open(P("README.md")).read().splitlines(): g.append([line])
    g.column_dimensions["A"].width = 140
    for row in g.iter_rows():
        for c in row: c.font = Font(name="Courier New" if row[0].value and row[0].value.startswith("    ") else "Arial", size=10)
    wb.save(P("out", "LNP_Midterms_Tracker.xlsx"))

    # Dashboard database documents
    changed = set(open(P("out","changed.txt")).read().split()) if os.path.exists(P("out","changed.txt")) and not all_docs else None
    for sub in ("entries","meta"): os.makedirs(P("out","db",sub), exist_ok=True)
    for d in ("entries","meta"):
        for fn in os.listdir(P("out","db",d)): os.remove(P("out","db",d,fn))
    n = 0
    for e in E:
        if changed is None or e["id"] in changed:
            json.dump(e, open(P("out","db","entries", e["id"] + ".json"), "w"), ensure_ascii=False); n += 1
    if changed is None or "meta:national" in changed:
        json.dump({"schema_version": SCHEMA_VERSION, "snapshots": nat["snapshots"], "series": nat.get("series", {})}, open(P("out","db","meta","national.json"), "w"), ensure_ascii=False); n += 1
    dels = sorted(c[7:] for c in (changed or set()) if c.startswith("delete:"))
    open(P("out","db","deletions.txt"), "w").write("\n".join(dels))
    if dels: print("documents to delete from the dashboard database (collection 'entries'):", ", ".join(dels))
    last = max([e["updated"] for e in E] + [s["observed"] for s in nat["snapshots"]])
    json.dump({"schema_version": SCHEMA_VERSION, "last_updated": last, "entry_count": len(E)},
              open(P("out","db","meta","info.json"), "w")); n += 1
    print(f"exported {len(E)} entries; {n} db docs in out/db (changed only: {changed is not None})")

# ---------------------------------------------------------------- project storage
def pack():
    """Split master data for the project: daily API market prices go to a compact CSV, everything else to entries_core.json."""
    store = load("entries.json", None); os.makedirs(P("data"), exist_ok=True)
    core = json.loads(json.dumps(store)); rows = []
    for e in core["entries"].values():
        api = [m for m in e["markets"] if (m.get("note") or "").startswith("api")]
        e["markets"] = [m for m in e["markets"] if m not in api]
        rows += [[e["id"], m["platform"], m["observed"], m["pct"], m.get("note") or ""] for m in api]
    json.dump(core, open(P("data", "entries_core.json"), "w"), ensure_ascii=False, indent=1)
    with open(P("data", "market_history.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["entry_id","platform","date","pct","note"]); w.writerows(sorted(rows))
    import shutil; shutil.copy(P("national.json"), P("data", "national.json"))
    print(f"packed: {len(core['entries'])} entries, {len(rows)} market rows -> data/")

def unpack(folder):
    """Rebuild entries.json from entries_core.json + market_history.csv (as committed in data/)."""
    core = json.load(open(os.path.join(folder, "entries_core.json")))
    with open(os.path.join(folder, "market_history.csv")) as f:
        for r in csv.DictReader(f):
            e = core["entries"].get(r["entry_id"])
            if e is not None: e["markets"].append({"observed": r["date"], "platform": r["platform"], "pct": float(r["pct"]), "note": r["note"]})
    for e in core["entries"].values(): e["markets"].sort(key=lambda m: (m["observed"], m["platform"]))
    save("entries.json", core)
    nat = os.path.join(folder, "national.json")
    if os.path.exists(nat): import shutil; shutil.copy(nat, P("national.json"))
    print(f"unpacked {len(core['entries'])} entries")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(0)
    if a[0] == "migrate": migrate()
    elif a[0] == "apply":
        obs = a[a.index("--observed") + 1] if "--observed" in a else None
        apply(a[1], obs)
    elif a[0] == "export": export("--all" in a)
    elif a[0] == "pack": pack()
    elif a[0] == "unpack": unpack(a[1] if len(a) > 1 else P("data"))
    elif a[0] == "clear-changed":
        open(P("out","changed.txt"), "w").write(""); print("cleared")
    else: print(__doc__)
