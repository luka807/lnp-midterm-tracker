#!/usr/bin/env python3
"""Pull prediction-market prices from the Polymarket and Kalshi public APIs into entries.json.

  python fetch_markets.py map              find each on-ballot entry's Polymarket/Kalshi market (saved in entry.market_ids)
  python fetch_markets.py history [START]  daily price history from START (default 2026-08-01) through today
  python fetch_markets.py today            today's prices only (use for routine refreshes)
  python fetch_markets.py national [START] House/Senate control history into national.json (series.house_d / series.senate_d)

Each price is stored as a market observation {observed: date, platform, pct, note: "api"}; one per platform per day.
A manual market entry for the same platform and day is replaced by the API value.
"""
import json, os, re, subprocess, sys, time, datetime, unicodedata
HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(HERE, *a)
STATES = dict(AL="alabama",AK="alaska",AZ="arizona",AR="arkansas",CA="california",CO="colorado",CT="connecticut",DE="delaware",
  FL="florida",GA="georgia",HI="hawaii",ID="idaho",IL="illinois",IN="indiana",IA="iowa",KS="kansas",KY="kentucky",LA="louisiana",
  ME="maine",MD="maryland",MA="massachusetts",MI="michigan",MN="minnesota",MS="mississippi",MO="missouri",MT="montana",NE="nebraska",
  NV="nevada",NH="new-hampshire",NJ="new-jersey",NM="new-mexico",NY="new-york",NC="north-carolina",ND="north-dakota",OH="ohio",
  OK="oklahoma",OR="oregon",PA="pennsylvania",RI="rhode-island",SC="south-carolina",SD="south-dakota",TN="tennessee",TX="texas",
  UT="utah",VT="vermont",VA="virginia",WA="washington",WV="west-virginia",WI="wisconsin",WY="wyoming")
PARTY_WORDS = {"D": ("democrat", "democratic"), "R": ("republican",), "I": ("independent",)}
KAPI = "https://api.elections.kalshi.com/trade-api/v2"

def get(url):
    for attempt in range(5):
        time.sleep(0.25)   # stay under API rate limits
        r = subprocess.run(["curl", "-sS", "-m", "30", url], capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
            if isinstance(d, dict) and d.get("error") and "rate" in json.dumps(d).lower(): raise ValueError
            return d
        except Exception: time.sleep(2 * (attempt + 1))
    return None
def norm(s): return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower())
def lastname(n): return norm(n.split()[-1])

def load():
    return json.load(open(P("entries.json")))
def save(store): json.dump(store, open(P("entries.json"), "w"), ensure_ascii=False, indent=1)

def poly_slugs(e):
    st = e["state"]; name = STATES[st]
    if e["office"] == "House":
        d = e["race"].split("-")[1]
        d = "al" if d == "AL" else f"{int(d):02d}"
        return [f"{st.lower()}-{d}-house-election-winner"]
    if e["office"] == "Senate":
        return [f"{name}-senate-election-winner", f"{name}-senate-special-election-winner"]
    return [f"{name}-governor-winner-2026", f"{name}-governor-election-winner"]
def kalshi_events(e):
    st = e["state"]
    if e["office"] == "House":
        d = e["race"].split("-")[1]
        return [f"HOUSE{st}{'AL' if d == 'AL' else int(d)}-26", f"HOUSE{st}{d}-26"]
    if e["office"] == "Senate": return [f"SENATE{st}-26", f"SENATE{st}S-26", f"SENATE{st}SPECIAL-26"]
    return [f"GOVPARTY{st}-26", f"GOV{st}-26"]

def pick(options, e):
    """options: list of (label, payload). Match by last name, then by party word."""
    ln = lastname(e["name"])
    for lab, pay in options:
        if ln and ln in norm(lab): return lab, pay, "candidate"
    for lab, pay in options:
        l = norm(lab)
        if any(w in l for w in PARTY_WORDS.get(e["party"], ())) or l == e["party"].lower(): return lab, pay, "party"
    return None

def map_markets():
    store = load(); n_p = n_k = 0; missing = []
    for e in store["entries"].values():
        if e["ballot"] != "On ballot": continue
        ids = e.get("market_ids") or {}
        if "polymarket" not in ids:
            for slug in poly_slugs(e):
                ev = get(f"https://gamma-api.polymarket.com/events?slug={slug}")
                if not ev: continue
                opts = []
                for m in ev[0].get("markets", []):
                    if m.get("clobTokenIds") and m.get("outcomePrices"):
                        opts.append((m.get("groupItemTitle") or m.get("question"), json.loads(m["clobTokenIds"])[0]))
                hit = pick(opts, e)
                if hit:
                    ids["polymarket"] = {"slug": slug, "outcome": hit[0], "token": hit[1], "match": hit[2]}; break
        if "kalshi" not in ids:
            for evt in kalshi_events(e):
                r = get(f"{KAPI}/markets?event_ticker={evt}")
                ms = (r or {}).get("markets") or []
                if not ms: continue
                hit = pick([(m.get("yes_sub_title") or m["ticker"], m["ticker"]) for m in ms], e)
                if hit:
                    ids["kalshi"] = {"event": evt, "series": evt.rsplit("-", 1)[0], "ticker": hit[1], "outcome": hit[0], "match": hit[2]}; break
        e["market_ids"] = ids
        n_p += "polymarket" in ids; n_k += "kalshi" in ids
        if not ids: missing.append(e["id"])
    save(store)
    print(f"mapped Polymarket {n_p}, Kalshi {n_k}; no market found: {', '.join(missing) or 'none'}")

def day(ts): return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date().isoformat()

def poly_history(token, start):
    h = get(f"https://clob.polymarket.com/prices-history?market={token}&interval=max&fidelity=1440") or {}
    out = {}
    for pt in h.get("history", []):
        d = day(pt["t"])
        if d >= start: out[d] = round(pt["p"] * 100, 1)   # last point of each day wins
    return out
def kalshi_history(series, ticker, start):
    s = int(datetime.datetime.fromisoformat(start).replace(tzinfo=datetime.timezone.utc).timestamp())
    now = int(time.time()); out = {}
    while s < now:   # API caps the window; walk it in 80-day chunks
        e = min(s + 80 * 86400, now)
        r = get(f"{KAPI}/series/{series}/markets/{ticker}/candlesticks?start_ts={s}&end_ts={e}&period_interval=1440") or {}
        for c in r.get("candlesticks", []):
            # Prefer the bid/ask midpoint: last-trade prices in thin markets can sit on one stray trade for days.
            p = c.get("price", {}); v = None
            b, a = c.get("yes_bid", {}).get("close_dollars"), c.get("yes_ask", {}).get("close_dollars")
            if b is not None and a is not None and 0 < float(a) - float(b) <= 0.15: v = (float(b) + float(a)) / 2
            if v is None: v = p.get("close_dollars") or p.get("previous_dollars")
            if v is not None: out[day(c["end_period_ts"] - 1)] = round(float(v) * 100, 1)
        s = e
    return out

def despike(series_by_day, half=7, limit=25):
    """Drop isolated thin-market spikes: points more than `limit` pts from the median of the surrounding +/-`half` days."""
    days = sorted(series_by_day); vals = [series_by_day[d] for d in days]; out = {}
    for i, d in enumerate(days):
        win = sorted(vals[max(0, i - half): i + half + 1])
        med = win[len(win) // 2]
        if len(win) >= 5 and abs(vals[i] - med) > limit: continue
        out[d] = vals[i]
    return out

def write_obs(e, platform, series_by_day, outcome, match):
    if len(series_by_day) > 1:
        old = {m["observed"]: m["pct"] for m in e["markets"] if m["platform"] == platform and m.get("note", "").startswith("api") and m.get("pct") is not None}
        merged = despike({**old, **series_by_day})
        dropped = [d for d in series_by_day if d not in merged]
        series_by_day = {d: v for d, v in series_by_day.items() if d in merged}
        e["markets"] = [m for m in e["markets"] if not (m["platform"] == platform and m["observed"] in dropped)]
    note = "api" + ("" if match == "candidate" else f"; party market ({outcome})")
    keep = [m for m in e["markets"] if not (m["platform"] == platform and m["observed"] in series_by_day)]
    e["markets"] = sorted(keep + [{"observed": d, "platform": platform, "pct": v, "note": note} for d, v in series_by_day.items()],
                          key=lambda m: (m["observed"], m["platform"]))

def history(start="2026-08-01", today_only=False):
    store = load(); today = datetime.date.today().isoformat(); changed = []
    if today_only: start = today
    for e in store["entries"].values():
        ids = e.get("market_ids") or {}
        got = False
        if "polymarket" in ids:
            s = poly_history(ids["polymarket"]["token"], start)
            if today_only and s: s = {max(s): s[max(s)]}
            if s: write_obs(e, "Polymarket", s, ids["polymarket"]["outcome"], ids["polymarket"]["match"]); got = True
        if "kalshi" in ids:
            k = ids["kalshi"]; s = kalshi_history(k["series"], k["ticker"], (datetime.date.today() - datetime.timedelta(days=3)).isoformat() if today_only else start)
            if today_only and s: s = {max(s): s[max(s)]}
            if s: write_obs(e, "Kalshi", s, k["outcome"], k["match"]); got = True
        if got:
            e["updated"] = max(e.get("updated") or "", max(m["observed"] for m in e["markets"])); changed.append(e["id"])
    save(store)
    os.makedirs(P("out"), exist_ok=True)
    prev = set(open(P("out", "changed.txt")).read().split()) if os.path.exists(P("out", "changed.txt")) else set()
    open(P("out", "changed.txt"), "w").write("\n".join(sorted(prev | set(changed))))
    print(f"market history written for {len(changed)} entries ({start} → {today})")

def national(start="2026-08-01"):
    nat = json.load(open(P("national.json")))
    series = nat.setdefault("series", {})
    for key, slug, kev in (("house_d", "which-party-will-win-the-house-in-2026", "CONTROLH-2026"),
                           ("senate_d", "which-party-will-win-the-senate-in-2026", "CONTROLS-2026")):
        rows = {}
        ev = get(f"https://gamma-api.polymarket.com/events?slug={slug}")
        if ev:
            for m in ev[0]["markets"]:
                if "democrat" in norm(m.get("groupItemTitle") or m.get("question")) and m.get("clobTokenIds"):
                    for d, v in poly_history(json.loads(m["clobTokenIds"])[0], start).items(): rows.setdefault(d, {})["Polymarket"] = v
        r = get(f"{KAPI}/markets?event_ticker={kev}") or {}
        for m in r.get("markets", []):
            if "democrat" in norm(m.get("yes_sub_title")):
                for d, v in kalshi_history(kev.rsplit("-", 1)[0], m["ticker"], start).items(): rows.setdefault(d, {})["Kalshi"] = v
        series[key] = [{"date": d, **v} for d, v in sorted(rows.items())]
        print(key, len(series[key]), "days")
    json.dump(nat, open(P("national.json"), "w"), ensure_ascii=False, indent=1)
    prev = set(open(P("out", "changed.txt")).read().split()) if os.path.exists(P("out", "changed.txt")) else set()
    open(P("out", "changed.txt"), "w").write("\n".join(sorted(prev | {"meta:national"})))

if __name__ == "__main__":
    a = sys.argv[1:] or ["help"]
    if a[0] == "map": map_markets()
    elif a[0] == "history": history(a[1] if len(a) > 1 else "2026-08-01")
    elif a[0] == "today": history(today_only=True)
    elif a[0] == "national": national(a[1] if len(a) > 1 else "2026-08-01")
    else: print(__doc__)
