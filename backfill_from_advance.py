#!/usr/bin/env python3
"""
backfill_from_advance.py

One-time backfill: adds shows from advance/data/<DATE>/ into daily/data/<DATE>/
that were missed due to an error. Only shows with start time <= CUTOFF are added.

Rules:
  - Only ADDS shows; never modifies or removes existing daily rows
  - Dedup via the per-shard show_key (matches daily.py for shards 1-8,
    matches daily9.py for shard 9)
  - Skips any source row whose 'date' field disagrees with the target date
  - Rebuilds movie_summary{N}.json per shard
  - Rebuilds finaldetailed.json + finalsummary.json via combiner logic
"""
import argparse
import json
import os
import sys
from datetime import datetime

try:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
except ImportError:
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))


# ----------------------------------------------------------------------
# args
# ----------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=None, help="YYYYMMDD (default: today IST)")
    p.add_argument("--root", default=".", help="base dir containing daily/ and advance/")
    p.add_argument("--cutoff", default="11:00 AM", help="show-time cutoff, inclusive")
    p.add_argument("--dry-run", action="store_true", help="no writes, just report")
    return p.parse_args()


# ----------------------------------------------------------------------
# io helpers
# ----------------------------------------------------------------------
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"⚠️  load error {path}: {e}")
        return None


def save_json(path, data, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)


def time_to_minutes(t):
    if not t:
        return None
    try:
        dt = datetime.strptime(t.strip(), "%I:%M %p")
        return dt.hour * 60 + dt.minute
    except Exception:
        return None


# ----------------------------------------------------------------------
# per-shard show key — MUST match each script's convention
# ----------------------------------------------------------------------
def show_key(r, shard):
    if shard == 9:
        return (str(r.get("venue_id", "")), r.get("time", ""),
                str(r.get("session_id", "")), r.get("audi", ""))
    return (r.get("venue", ""), r.get("time", ""),
            str(r.get("session_id", "")), r.get("audi", ""))


# ----------------------------------------------------------------------
# per-shard summary — matches daily.py (1-8) and daily9.py (9)
# ----------------------------------------------------------------------
def build_summary(detailed, shard):
    summary = {}
    for r in detailed:
        movie = r.get("movie", "Unknown")
        city = r.get("city", "Unknown")
        total = int(r.get("totalSeats", 0) or 0)
        sold = int(r.get("sold", 0) or 0)
        gross = float(r.get("gross", 0) or 0)
        occ = (sold / total * 100) if total else 0

        if movie not in summary:
            summary[movie] = {
                "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
                "venues": set(), "cities": set(),
                "fastfilling": 0, "housefull": 0,
            }
        m = summary[movie]
        m["shows"] += 1
        m["gross"] += gross
        m["sold"] += sold
        m["totalSeats"] += total
        if shard == 9:
            vid = r.get("venue_id")
            if vid is not None:
                m["venues"].add(str(vid))
        else:
            m["venues"].add(r.get("venue", "Unknown"))
        m["cities"].add(city)
        if occ >= 98:
            m["housefull"] += 1
        elif occ >= 50:
            m["fastfilling"] += 1

    return {
        movie: {
            "shows": m["shows"],
            "gross": round(m["gross"], 2),
            "sold": m["sold"],
            "totalSeats": m["totalSeats"],
            "venues": len(m["venues"]),
            "cities": len(m["cities"]),
            "fastfilling": m["fastfilling"],
            "housefull": m["housefull"],
            "occupancy": round((m["sold"] / m["totalSeats"]) * 100, 2) if m["totalSeats"] else 0.0,
        }
        for movie, m in summary.items()
    }


# ----------------------------------------------------------------------
# combiner — normalize / dedupe / compress / aggregate
# ----------------------------------------------------------------------
def normalize_row(r):
    r["movie"] = r.get("movie") or "Unknown"
    r["city"] = r.get("city") or "Unknown"
    r["state"] = r.get("state") or "Unknown"
    r["venue"] = r.get("venue") or "Unknown"
    r["address"] = r.get("address") or ""
    r["time"] = r.get("time") or ""
    r["audi"] = r.get("audi") or ""
    r["session_id"] = str(r.get("session_id") or "")
    r["chain"] = r.get("chain") or "Unknown"
    r["source"] = r.get("source") or "Unknown"
    r["totalSeats"] = int(r.get("totalSeats") or 0)
    r["available"] = int(r.get("available") or 0)
    r["sold"] = int(r.get("sold") or 0)
    r["gross"] = float(r.get("gross") or 0.0)
    occ = r.get("occupancy", "")
    if isinstance(occ, (int, float)):
        r["occupancy"] = f"{round(float(occ), 2)}%"
    elif isinstance(occ, str):
        if not occ.endswith("%"):
            try:
                r["occupancy"] = f"{round(float(occ), 2)}%"
            except Exception:
                r["occupancy"] = "0%"
    else:
        r["occupancy"] = "0%"
    return r


def dedupe(rows):
    seen, out = set(), []
    for r in rows:
        key = (r.get("venue", ""), r.get("time", ""),
               r.get("session_id", ""), r.get("audi", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def compress_row(r):
    r = dict(r)
    r.pop("address", None)
    r.pop("date", None)
    src = r.pop("source", None)
    if src == "BMS":
        r["s"] = "B"
    elif src == "District":
        r["s"] = "D"
    elif src:
        r["s"] = src[0].upper()
    return r


def build_final_summary(final_rows):
    summary = {}
    for r in final_rows:
        movie = r["movie"]; city = r["city"]; state = r["state"]
        venue = r["venue"]; chain = r["chain"]
        total = r["totalSeats"]; sold = r["sold"]; gross = r["gross"]
        occ = (sold / total * 100) if total else 0

        if movie not in summary:
            summary[movie] = {
                "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
                "venues": set(), "cities": set(),
                "fastfilling": 0, "housefull": 0,
                "details": {}, "Chain_details": {},
            }
        m = summary[movie]
        m["shows"] += 1; m["gross"] += gross; m["sold"] += sold
        m["totalSeats"] += total; m["venues"].add(venue); m["cities"].add(city)
        if occ >= 98: m["housefull"] += 1
        elif occ >= 50: m["fastfilling"] += 1

        ck = (city, state)
        if ck not in m["details"]:
            m["details"][ck] = {
                "city": city, "state": state, "venues": set(),
                "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
                "fastfilling": 0, "housefull": 0,
            }
        d = m["details"][ck]
        d["venues"].add(venue); d["shows"] += 1; d["gross"] += gross
        d["sold"] += sold; d["totalSeats"] += total
        if occ >= 98: d["housefull"] += 1
        elif occ >= 50: d["fastfilling"] += 1

        if chain not in m["Chain_details"]:
            m["Chain_details"][chain] = {
                "chain": chain, "venues": set(),
                "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
                "fastfilling": 0, "housefull": 0,
            }
        c = m["Chain_details"][chain]
        c["venues"].add(venue); c["shows"] += 1; c["gross"] += gross
        c["sold"] += sold; c["totalSeats"] += total
        if occ >= 98: c["housefull"] += 1
        elif occ >= 50: c["fastfilling"] += 1

    final = {}
    for movie, m in summary.items():
        final[movie] = {
            "shows": m["shows"], "gross": round(m["gross"], 2),
            "sold": m["sold"], "totalSeats": m["totalSeats"],
            "venues": len(m["venues"]), "cities": len(m["cities"]),
            "fastfilling": m["fastfilling"], "housefull": m["housefull"],
            "occupancy": round((m["sold"] / m["totalSeats"]) * 100, 2) if m["totalSeats"] else 0.0,
            "details": [], "Chain_details": [],
        }
        for d in m["details"].values():
            final[movie]["details"].append({
                "city": d["city"], "state": d["state"],
                "venues": len(d["venues"]), "shows": d["shows"],
                "gross": round(d["gross"], 2), "sold": d["sold"],
                "totalSeats": d["totalSeats"],
                "fastfilling": d["fastfilling"], "housefull": d["housefull"],
                "occupancy": round((d["sold"] / d["totalSeats"]) * 100, 2) if d["totalSeats"] else 0.0,
            })
        for c in m["Chain_details"].values():
            final[movie]["Chain_details"].append({
                "chain": c["chain"],
                "venues": len(c["venues"]), "shows": c["shows"],
                "gross": round(c["gross"], 2), "sold": c["sold"],
                "totalSeats": c["totalSeats"],
                "fastfilling": c["fastfilling"], "housefull": c["housefull"],
                "occupancy": round((c["sold"] / c["totalSeats"]) * 100, 2) if c["totalSeats"] else 0.0,
            })
    return final


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main():
    args = parse_args()
    now_ist = datetime.now(IST)
    date_code = args.date or now_ist.strftime("%Y%m%d")

    adv_dir = os.path.join(args.root, "advance", "data", date_code)
    daily_dir = os.path.join(args.root, "daily", "data", date_code)

    if not os.path.isdir(adv_dir):
        print(f"❌ advance dir not found: {adv_dir}")
        return 1
    if not os.path.isdir(daily_dir):
        print(f"❌ daily dir not found: {daily_dir}")
        return 1

    cutoff_minutes = time_to_minutes(args.cutoff)
    if cutoff_minutes is None:
        print(f"❌ cannot parse cutoff: {args.cutoff}")
        return 1

    print(f"📅 date          = {date_code}")
    print(f"📂 advance dir   = {adv_dir}")
    print(f"📂 daily dir     = {daily_dir}")
    print(f"✂️  cutoff        = {args.cutoff} ({cutoff_minutes} min)")
    print(f"🚧 dry-run       = {args.dry_run}")
    print()

    # ---------- 1. per-shard merge ----------
    total_added = 0
    per_shard_added = {}
    skipped_dates = 0

    for shard in range(1, 10):
        adv = load_json(os.path.join(adv_dir, f"detailed{shard}.json"))
        if not adv:
            print(f"[shard {shard}] ⏭️  no advance data")
            continue

        eligible = []
        for r in adv:
            d = r.get("date")
            if d and d != date_code:
                skipped_dates += 1
                continue
            mins = time_to_minutes(r.get("time", ""))
            if mins is not None and mins <= cutoff_minutes:
                eligible.append(r)

        if not eligible:
            print(f"[shard {shard}] ⏭️  no advance shows ≤ {args.cutoff}")
            continue

        daily_path = os.path.join(daily_dir, f"detailed{shard}.json")
        daily = load_json(daily_path) or []
        existing = {show_key(r, shard) for r in daily}

        added = 0
        for r in eligible:
            k = show_key(r, shard)
            if k in existing:
                continue
            daily.append(r)
            existing.add(k)
            added += 1

        if added == 0:
            print(f"[shard {shard}] ✓  nothing missing ({len(eligible)} eligible, all in daily)")
            continue

        if not args.dry_run:
            save_json(daily_path, daily)
            save_json(
                os.path.join(daily_dir, f"movie_summary{shard}.json"),
                build_summary(daily, shard),
            )

        per_shard_added[shard] = added
        total_added += added
        print(f"[shard {shard}] ✅ +{added} (eligible={len(eligible)}, daily now={len(daily)})")

    if skipped_dates:
        print(f"⚠️  skipped {skipped_dates} advance rows with mismatched date")

    if total_added == 0:
        print("\nℹ️  Nothing to backfill — no files changed.")
        return 0

    # ---------- 2. rebuild finaldetailed ----------
    all_rows = []
    for shard in range(1, 10):
        all_rows.extend(load_json(os.path.join(daily_dir, f"detailed{shard}.json")) or [])

    all_rows = [normalize_row(r) for r in all_rows]
    final_rows = dedupe(all_rows)
    final_rows.sort(key=lambda x: (x["movie"], x["city"], x["venue"], x["time"]))

    fd_path = os.path.join(daily_dir, "finaldetailed.json")
    old = load_json(fd_path) or {}
    last_updated = old.get("last_updated") or now_ist.strftime("%Y-%m-%d %H:%M IST")

    if not args.dry_run:
        save_json(fd_path,
                  {"last_updated": last_updated,
                   "data": [compress_row(dict(r)) for r in final_rows]},
                  compact=True)
    print(f"\n📦 finaldetailed.json rebuilt ({len(final_rows)} shows)")

    # ---------- 3. rebuild finalsummary ----------
    final_summary = build_final_summary(final_rows)
    if not args.dry_run:
        save_json(os.path.join(daily_dir, "finalsummary.json"),
                  {"last_updated": last_updated, "movies": final_summary},
                  compact=True)
    print(f"📦 finalsummary.json rebuilt ({len(final_summary)} movies)")

    # ---------- report ----------
    print("\n" + "=" * 50)
    print("  BACKFILL REPORT")
    print("=" * 50)
    print(f"Date           : {date_code}")
    print(f"Cutoff         : {args.cutoff}")
    print(f"Shards touched : {sorted(per_shard_added)}")
    for s in sorted(per_shard_added):
        print(f"  shard {s:<2}     : +{per_shard_added[s]}")
    print(f"Total added    : {total_added}")
    print(f"Final rows     : {len(final_rows)}")
    print(f"Final movies   : {len(final_summary)}")
    if args.dry_run:
        print("⚠️  DRY RUN — nothing written")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
