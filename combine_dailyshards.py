import json
import os
from datetime import datetime, timedelta
import pytz

# =====================================================
# ENV-DRIVEN CONFIG
# =====================================================
IST = pytz.timezone("Asia/Kolkata")
NOW_IST = datetime.now(IST)
LAST_UPDATED = NOW_IST.strftime("%Y-%m-%d %H:%M IST")   # observation time

MODE = os.environ.get("MODE", "daily").lower()
if MODE not in ("daily", "advance"):
    raise SystemExit(f"Invalid MODE={MODE}. Use 'daily' or 'advance'.")

DATE_CODE = os.environ.get("DATE_CODE") or NOW_IST.strftime("%Y%m%d")
BASE_DIR  = os.environ.get("BASE_DIR")  or f"daily/data/{DATE_CODE}"

try:
    TARGET_DATE = datetime.strptime(DATE_CODE, "%Y%m%d").replace(tzinfo=IST)
except Exception:
    TARGET_DATE = NOW_IST

FINAL_DETAILED = os.path.join(BASE_DIR, "finaldetailed.json")
FINAL_SUMMARY  = os.path.join(BASE_DIR, "finalsummary.json")

print(f"🎛 MODE: {MODE}")
print(f"📅 Target date: {TARGET_DATE.strftime('%Y-%m-%d')}")
print(f"📁 Using directory: {BASE_DIR}")
print(f"⏱ Last updated: {LAST_UPDATED}")

# =====================================================
# HELPERS
# =====================================================
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =====================================================
# NORMALIZE ROW
# =====================================================
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
    r["date"] = r.get("date") or DATE_CODE

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

# =====================================================
# DEDUPE
# =====================================================
def dedupe(rows):
    seen = set()
    out = []
    dupes = 0
    for r in rows:
        key = (
            r.get("venue", ""),
            r.get("time", ""),
            r.get("session_id", ""),
            r.get("audi", ""),
        )
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dupes

# =====================================================
# LOAD + COMBINE SHARDS
# =====================================================
all_rows = []
for i in range(1, 10):
    path = os.path.join(BASE_DIR, f"detailed{i}.json")
    data = load_json(path)
    if data:
        print(f"✅ detailed{i}.json → {len(data)} rows")
        all_rows.extend(data)

print(f"📊 Raw rows: {len(all_rows)}")

all_rows = [normalize_row(r) for r in all_rows]

final_rows, dupes = dedupe(all_rows)
print(f"🧹 Duplicates removed: {dupes}")
print(f"🎯 Final detailed rows: {len(final_rows)}")

final_rows.sort(key=lambda x: (x["movie"], x["city"], x["venue"], x["time"]))

# =====================================================
# SAVE finaldetailed.json (compressed)
# =====================================================
def compress_detail_row(r):
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

compressed_rows = [compress_detail_row(dict(r)) for r in final_rows]

os.makedirs(os.path.dirname(FINAL_DETAILED), exist_ok=True)
with open(FINAL_DETAILED, "w", encoding="utf-8") as f:
    json.dump(
        {"last_updated": LAST_UPDATED, "data": compressed_rows},
        f,
        ensure_ascii=False,
        separators=(",", ":"),
    )

print("🎉 finaldetailed.json saved")

# =====================================================
# BUILD FINAL SUMMARY
# =====================================================
summary = {}
for r in final_rows:
    movie = r["movie"]
    city = r["city"]
    state = r["state"]
    venue = r["venue"]
    chain = r["chain"]
    total = r["totalSeats"]
    sold = r["sold"]
    gross = r["gross"]
    occ = (sold / total * 100) if total else 0

    if movie not in summary:
        summary[movie] = {
            "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
            "venues": set(), "cities": set(),
            "fastfilling": 0, "housefull": 0,
            "details": {}, "Chain_details": {}
        }

    m = summary[movie]
    m["shows"] += 1
    m["gross"] += gross
    m["sold"] += sold
    m["totalSeats"] += total
    m["venues"].add(venue)
    m["cities"].add(city)
    if occ >= 98:
        m["housefull"] += 1
    elif occ >= 50:
        m["fastfilling"] += 1

    ck = (city, state)
    if ck not in m["details"]:
        m["details"][ck] = {
            "city": city, "state": state, "venues": set(),
            "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
            "fastfilling": 0, "housefull": 0
        }
    d = m["details"][ck]
    d["venues"].add(venue)
    d["shows"] += 1
    d["gross"] += gross
    d["sold"] += sold
    d["totalSeats"] += total
    if occ >= 98:
        d["housefull"] += 1
    elif occ >= 50:
        d["fastfilling"] += 1

    if chain not in m["Chain_details"]:
        m["Chain_details"][chain] = {
            "chain": chain, "venues": set(),
            "shows": 0, "gross": 0.0, "sold": 0, "totalSeats": 0,
            "fastfilling": 0, "housefull": 0
        }
    c = m["Chain_details"][chain]
    c["venues"].add(venue)
    c["shows"] += 1
    c["gross"] += gross
    c["sold"] += sold
    c["totalSeats"] += total
    if occ >= 98:
        c["housefull"] += 1
    elif occ >= 50:
        c["fastfilling"] += 1

# =====================================================
# FINALIZE SUMMARY
# =====================================================
final_summary = {}
for movie, m in summary.items():
    final_summary[movie] = {
        "shows": m["shows"],
        "gross": round(m["gross"], 2),
        "sold": m["sold"],
        "totalSeats": m["totalSeats"],
        "venues": len(m["venues"]),
        "cities": len(m["cities"]),
        "fastfilling": m["fastfilling"],
        "housefull": m["housefull"],
        "occupancy": round((m["sold"] / m["totalSeats"]) * 100, 2) if m["totalSeats"] else 0.0,
        "details": [],
        "Chain_details": [],
    }

    for d in m["details"].values():
        final_summary[movie]["details"].append({
            "city": d["city"],
            "state": d["state"],
            "venues": len(d["venues"]),
            "shows": d["shows"],
            "gross": round(d["gross"], 2),
            "sold": d["sold"],
            "totalSeats": d["totalSeats"],
            "fastfilling": d["fastfilling"],
            "housefull": d["housefull"],
            "occupancy": round((d["sold"] / d["totalSeats"]) * 100, 2) if d["totalSeats"] else 0.0,
        })

    for c in m["Chain_details"].values():
        final_summary[movie]["Chain_details"].append({
            "chain": c["chain"],
            "venues": len(c["venues"]),
            "shows": c["shows"],
            "gross": round(c["gross"], 2),
            "sold": c["sold"],
            "totalSeats": c["totalSeats"],
            "fastfilling": c["fastfilling"],
            "housefull": c["housefull"],
            "occupancy": round((c["sold"] / c["totalSeats"]) * 100, 2) if c["totalSeats"] else 0.0,
        })

os.makedirs(os.path.dirname(FINAL_SUMMARY), exist_ok=True)
with open(FINAL_SUMMARY, "w", encoding="utf-8") as f:
    json.dump(
        {"last_updated": LAST_UPDATED, "movies": final_summary},
        f,
        ensure_ascii=False,
        separators=(",", ":"),
    )

# =====================================================
# MONTHLY BOXOFFICE LOG
#
#   DATE_KEY  = TARGET date   (show date, not observation date)
#   Log month = TARGET month  (bucket by show month, not observation month)
#   TIME_KEY  = observation hour bucket
#               → "02PM" for daily, "A02PM" for advance
#                 (prefix prevents advance snapshot from being
#                  overwritten by the same-hour day-of snapshot)
#   Log root  = daily/logs/   (shared pool, NOT under BASE_DIR)
# =====================================================
DAILY_LOG_ROOT = "daily/logs"
os.makedirs(DAILY_LOG_ROOT, exist_ok=True)

TARGET_MONTH_STR = TARGET_DATE.strftime("%Y-%m")
MONTH_FOLDER = os.path.join(DAILY_LOG_ROOT, TARGET_MONTH_STR)
os.makedirs(MONTH_FOLDER, exist_ok=True)

DAILY_LOG_FILE = os.path.join(MONTH_FOLDER, "monthlylogs.json")

DATE_KEY = TARGET_DATE.strftime("%d/%m/%Y")

minute = NOW_IST.minute
hour = NOW_IST.hour + (1 if minute > 45 else 0)
bucket_time = (
    NOW_IST.replace(minute=0, second=0, microsecond=0)
    .replace(hour=hour if hour < 24 else 0)
)
PREFIX = "A" if MODE == "advance" else ""
TIME_KEY = f"{PREFIX}{bucket_time.strftime('%I%p')}"

if os.path.exists(DAILY_LOG_FILE):
    try:
        with open(DAILY_LOG_FILE, "r", encoding="utf-8") as f:
            daily_logs = json.load(f)
    except Exception:
        daily_logs = {}
else:
    daily_logs = {}

for movie, d in final_summary.items():
    if movie not in daily_logs:
        daily_logs[movie] = {}
    if DATE_KEY not in daily_logs[movie]:
        daily_logs[movie][DATE_KEY] = {}
    daily_logs[movie][DATE_KEY][TIME_KEY] = [
        d["shows"],
        d["gross"],
        d["sold"],
        round(d["occupancy"], 2),
        d["venues"],
        d["cities"],
        d["fastfilling"],
        d["housefull"],
    ]

with open(DAILY_LOG_FILE, "w", encoding="utf-8") as f:
    json.dump(daily_logs, f, ensure_ascii=False, separators=(",", ":"))

print(f"📦 Daily monthly log updated → {DAILY_LOG_FILE}  [key={DATE_KEY} slot={TIME_KEY}]")
print("📄 Files ready:")
print(f"   • {FINAL_DETAILED}")
print(f"   • {FINAL_SUMMARY}")
