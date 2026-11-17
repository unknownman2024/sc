#!/usr/bin/env python3
"""
marketstrip.py

Generates market strip JSON (data/marketstrip.json) from daily aggregated data.
Compares current and previous day's show grosses up to a dynamically rounded cutoff time.

Runs ONLY in MODE=daily. Skips gracefully in MODE=advance.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from zoneinfo import ZoneInfo

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
MODE = os.environ.get("MODE", "daily").lower()
# ✅ FIXED — respects env vars set by YAML
DAILY_DATA_DIR = Path(os.environ.get("DATA_ROOT",       "daily/data"))
OUTPUT_FILE    = Path(os.environ.get("MARKETSTRIP_OUT", "data/marketstrip.json"))

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S %Z",
)
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def get_ist_today() -> datetime.date:
    return datetime.now(IST).date()


def round_to_nearest_hour(time_str: str) -> str:
    match = re.search(r"(\d{1,2}):(\d{2})", time_str)
    if not match:
        raise ValueError(f"Could not parse time from: {time_str}")
    h, m = int(match.group(1)), int(match.group(2))
    if m >= 30:
        h = (h + 1) % 24
    return f"{h:02d}:00"


def parse_time_to_minutes(time_str: str) -> int:
    time_str = time_str.strip()
    dt = datetime.strptime(time_str, "%I:%M %p")
    return dt.hour * 60 + dt.minute


def parse_movie_label(raw: str) -> Tuple[str, str]:
    match = re.match(r"^(.*?)\s*\[.*?\|\s*([^\]]+)\]$", raw)
    if not match:
        logger.warning(f"Could not parse movie label: '{raw}', using raw as movie name")
        return raw.strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def format_indian_currency(amount: int) -> str:
    if amount >= 10_000_000:
        val = amount / 10_000_000
        return f"₹{val:.2f}Cr".rstrip('0').rstrip('.') if val % 1 != 0 else f"₹{val:.0f}Cr"
    elif amount >= 100_000:
        val = amount / 100_000
        return f"₹{val:.2f}L".rstrip('0').rstrip('.') if val % 1 != 0 else f"₹{val:.0f}L"
    elif amount >= 1000:
        val = amount / 1000
        return f"₹{val:.1f}K".rstrip('0').rstrip('.') if val % 1 != 0 else f"₹{val:.0f}K"
    else:
        return f"₹{amount}"


# ----------------------------------------------------------------------
# Data loading & aggregation
# ----------------------------------------------------------------------
def load_daily_data(date_obj: datetime.date) -> Optional[Dict[str, Any]]:
    date_str = date_obj.strftime("%Y%m%d")
    file_path = DAILY_DATA_DIR / date_str / "finaldetailed.json"
    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {file_path}: {e}")
        return None


def aggregate_movies(
    data: Dict[str, Any], cutoff_minutes: int
) -> Tuple[Dict[str, Tuple[str, int, int]], int]:
    if not data or "data" not in data:
        return {}, 0

    agg: Dict[str, Tuple[str, int, int]] = {}
    filtered_count = 0

    for record in data["data"]:
        time_str = record.get("time", "")
        if not time_str:
            continue
        try:
            show_minutes = parse_time_to_minutes(time_str)
        except Exception:
            logger.warning(f"Skipping record with invalid time: {time_str}")
            continue
        if show_minutes > cutoff_minutes:
            continue
        filtered_count += 1
        movie_raw = record.get("movie", "")
        gross = record.get("gross", 0)
        movie, lang = parse_movie_label(movie_raw)
        key = f"{movie} {lang}".strip()
        label = key
        if key in agg:
            _, old_gross, old_count = agg[key]
            agg[key] = (label, old_gross + gross, old_count + 1)
        else:
            agg[key] = (label, gross, 1)

    return agg, filtered_count


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    start_time = time.time()

    # MODE gate — advance mode skips market strip entirely
    if MODE != "daily":
        logger.info(f"MODE={MODE} — market strip only runs in daily mode. Skipping.")
        return 0

    logger.info("Starting market strip generation")

    today = get_ist_today()
    yesterday = today - timedelta(days=1)

    logger.info(f"Current IST date: {today.isoformat()}")

    current_data = load_daily_data(today)
    if not current_data:
        logger.error("Current day data missing. Aborting.")
        return 1

    last_updated = current_data.get("last_updated", "")
    if not last_updated:
        logger.error("Current data missing 'last_updated' field.")
        return 1

    cutoff_str = round_to_nearest_hour(last_updated)
    hh, mm = map(int, cutoff_str.split(":"))
    cutoff_minutes = hh * 60 + mm

    logger.info(f"Cutoff time (rounded): {cutoff_str}")

    current_agg, current_filtered = aggregate_movies(current_data, cutoff_minutes)
    logger.info(f"Current day: loaded {len(current_data.get('data', []))} records, "
                f"kept {current_filtered} after cutoff")

    prev_data = load_daily_data(yesterday)
    if prev_data:
        prev_agg, prev_filtered = aggregate_movies(prev_data, cutoff_minutes)
        logger.info(f"Previous day: loaded {len(prev_data.get('data', []))} records, "
                    f"kept {prev_filtered} after cutoff")
    else:
        prev_agg = {}
        logger.info("Previous day data not found. Using empty set.")

    sorted_current = sorted(
        current_agg.items(),
        key=lambda item: item[1][1],
        reverse=True,
    )[:30]

    items = []
    for key, (label, curr_gross, curr_shows) in sorted_current:
        prev_gross = prev_agg.get(key, (None, 0, 0))[1]

        if prev_gross == 0 and curr_gross > 0:
            pct_change, trend = 100, "up"
        elif prev_gross == 0 and curr_gross == 0:
            pct_change, trend = 0, "flat"
        else:
            pct_change = ((curr_gross - prev_gross) / prev_gross) * 100
            trend = "up" if pct_change > 0 else ("down" if pct_change < 0 else "flat")

        pct_int = int(round(pct_change))

        items.append({
            "label": label,
            "value": f"{pct_int:+}%".replace("+", "") if pct_int != 0 else "0%",
            "trend": trend,
            "gross": format_indian_currency(curr_gross),
            "shows": f"{curr_shows:,}",
        })

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    logger.info(f"Market strip saved to {OUTPUT_FILE} ({len(items)} items)")
    logger.info(f"Total execution time: {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    exit(main())
