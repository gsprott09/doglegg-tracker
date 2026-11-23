"""Record which Doglegg Golf Huntsville bays are booked, from the public booking page.

Reads only. Never books, signs in or clicks. Appends one row per time slot to
data/snapshots.csv, and keeps the raw page outline in data/raw/ as a backup.
"""
import csv
import re
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

VENUE_URL = "https://albaplay.com/en/venue/doglegg-golf-huntsville?date={date}"
TZ = ZoneInfo("America/Chicago")
END_DATE = "2026-12-13"
BAYS = 4
DATA = Path(__file__).parent / "data"
CSV_PATH = DATA / "snapshots.csv"
RAW_DIR = DATA / "raw"

TIME_RE = re.compile(r"^\s*-\s*paragraph:\s*\"?(\d{1,2}:\d{2})\"?\s*$")
AMPM_RE = re.compile(r"^\s*-\s*paragraph:\s*\"?(AM|PM)\"?\s*$")
PRICE_RE = re.compile(r"\$\d+(?:\.\d{2})?")
IMG_RE = re.compile(r"^\s*-\s*img(\s+\"\")?(\s*\[[^\]]*\])*\s*:?\s*$")


def to_24h(hhmm: str, ampm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    if ampm == "PM" and h != 12:
        h += 12
    if ampm == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def parse(outline: str):
    """Turn the page's accessibility outline into [(slot, 'xoox'), ...].

    Times are listed first (e.g. '5:00' then 'PM'), then the cells row by row,
    4 per row: a line with a price means open, a bare image (calendar icon) means booked.
    """
    lines = outline.splitlines()
    times, last_time_idx = [], -1
    for i, line in enumerate(lines[:-1]):
        t, ap = TIME_RE.match(line), AMPM_RE.match(lines[i + 1])
        if t and ap:
            times.append(to_24h(t.group(1), ap.group(1)))
            last_time_idx = i + 1
    if not times:
        return None
    cells = []
    for line in lines[last_time_idx + 1:]:
        if PRICE_RE.search(line):
            cells.append("o")
        elif IMG_RE.match(line):
            cells.append("x")
    if len(cells) != len(times) * BAYS:
        return None
    return [(t, "".join(cells[i * BAYS:(i + 1) * BAYS])) for i, t in enumerate(times)]


def read_day(page, date: str):
    page.goto(VENUE_URL.format(date=date), wait_until="domcontentloaded", timeout=60000)
    try:
        page.get_by_text(re.compile(r"^\$\d")).first.wait_for(timeout=30000)
    except Exception:
        page.wait_for_timeout(8000)  # maybe fully booked or slow; read whatever is there
    page.wait_for_timeout(1500)
    return page.locator("body").first.aria_snapshot()


def main():
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    if today > END_DATE:
        print("Tracking period is over; nothing to do.")
        return 0
    if now.hour < 4:
        print("Overnight; skipping.")
        return 0
    dates = [today]
    if now.hour == 21:  # once a night, also record tomorrow's advance bookings
        dates.append((now + timedelta(days=1)).strftime("%Y-%m-%d"))

    DATA.mkdir(exist_ok=True)
    new_file = not CSV_PATH.exists()
    captured = now.strftime("%Y-%m-%d %H:%M")
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            timezone_id="America/Chicago",
            viewport={"width": 1280, "height": 2400},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        )
        with CSV_PATH.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["captured", "date", "slot", "bay1", "bay2", "bay3", "bay4"])
            for date in dates:
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                stamp = f"{date}_at_{now:%Y%m%d_%H%M}"
                try:
                    outline = read_day(page, date)
                except Exception:
                    ok = False
                    (RAW_DIR / f"{stamp}_ERROR.txt").write_text(traceback.format_exc())
                    try:
                        page.screenshot(path=str(RAW_DIR / f"{stamp}_ERROR.png"), full_page=True)
                    except Exception:
                        pass
                    print(f"{date}: failed to read page; error saved.")
                    traceback.print_exc()
                    continue
                (RAW_DIR / f"{stamp}.txt").write_text(outline)  # backup copy
                rows = parse(outline)
                if rows is None:
                    ok = False
                    print(f"{date}: could not parse; raw outline saved for later.")
                    continue
                for slot, st in rows:
                    w.writerow([captured, date, slot, *["booked" if c == "x" else "open" for c in st]])
                booked = sum(st.count("x") for _, st in rows)
                print(f"{date}: {len(rows)} slots, {booked} bay-slots booked.")
        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
