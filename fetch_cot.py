"""
fetch_cot.py — CFTC COT data fetcher for GitHub Actions
=========================================================
Fetches CFTC disaggregated futures report, extracts Natural Gas
(ICE LD1, code 023391), and writes structured JSON.

Runs on GitHub Actions every Friday at 20:30 UTC (2:00 AM IST Sat).
Output: data/cot_data.json  (read by ng_seasonality_dashboard.html)
"""

import urllib.request
import urllib.error
import csv
import json
import io
import os
import sys
from datetime import datetime, timezone, timedelta

CFTC_URL    = 'https://www.cftc.gov/dea/newcot/f_disagg.txt'
NG_CODE     = '023391'
OUTPUT_FILE = 'data/cot_data.json'
HISTORY_MAX = 12   # Keep last 12 weeks in JSON


def fetch_raw() -> str:
    print(f"Fetching {CFTC_URL} ...")
    req = urllib.request.Request(
        CFTC_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; NGDashboard/1.0)',
            'Accept': 'text/plain',
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode('utf-8', errors='replace')
    print(f"  Downloaded {len(raw):,} bytes")
    return raw


def find_ng_line(raw: str) -> str:
    for line in raw.splitlines():
        if NG_CODE in line and line.count(',') > 20:
            return line.strip()
    for line in raw.splitlines():
        if 'NAT GAS' in line.upper() and 'ICE' in line.upper() and ',' in line:
            return line.strip()
    return ''


def parse_line(line: str) -> dict:
    reader = csv.reader(io.StringIO(line))
    f = next(reader)

    def n(i):
        try: return int(float(f[i].strip()))
        except: return 0

    def s(i):
        return f[i].strip().strip('"') if i < len(f) else ''

    d = {
        'reportDate': s(2),
        'oi':         n(7),
        'pm_l':       n(8),  'pm_s':  n(9),
        'sd_l':       n(10), 'sd_s':  n(11),
        'mm_l':       n(13), 'mm_s':  n(14),
        'chg_oi':     n(39),
        'chg_mm_l':   n(45), 'chg_mm_s': n(46),
        'chg_sd_l':   n(42), 'chg_sd_s': n(43),
    }
    d['mm_net'] = d['mm_l'] - d['mm_s']
    d['sd_net'] = d['sd_l'] - d['sd_s']
    d['pm_net'] = d['pm_l'] - d['pm_s']

    # Normalise date to YYYY-MM-DD
    for fmt in ['%m/%d/%Y', '%Y-%m-%d', '%d-%b-%y', '%d-%b-%Y']:
        try:
            d['reportDate'] = datetime.strptime(d['reportDate'], fmt).strftime('%Y-%m-%d')
            break
        except ValueError:
            continue

    return d


def load_existing() -> list:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
            return data.get('history', [])
    except Exception as e:
        print(f"  Warning: could not load existing data: {e}")
        return []


def main():
    print("=" * 60)
    print("COT Fetcher — Natural Gas Dashboard Feed")
    print(f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # Fetch and parse
    raw  = fetch_raw()
    line = find_ng_line(raw)
    if not line:
        print("ERROR: NG line not found in CFTC file")
        sys.exit(1)

    d = parse_line(line)
    print(f"  Parsed: {d['reportDate']} | MM Net: {d['mm_net']:+,} | OI: {d['oi']:,}")

    # Load existing history and merge
    history = load_existing()
    existing_dates = {r['reportDate'] for r in history}

    if d['reportDate'] in existing_dates:
        print(f"  Already have {d['reportDate']} — no update needed")
    else:
        # Compute WoW changes vs previous week
        if history:
            prev = history[-1]
            d['mm_net_chg'] = d['mm_net'] - prev['mm_net']
            d['sd_net_chg'] = d['sd_net'] - prev['sd_net']
        else:
            d['mm_net_chg'] = d['chg_mm_l'] - d['chg_mm_s']
            d['sd_net_chg'] = None

        history.append(d)
        print(f"  Added new week. History now: {len(history)} weeks")

    # Keep last N weeks
    history = history[-HISTORY_MAX:]

    # Write output
    os.makedirs('data', exist_ok=True)
    output = {
        'updated':    datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source':     'CFTC Disaggregated Futures (023391 NAT GAS ICE LD1)',
        'latestDate': history[-1]['reportDate'] if history else '',
        'history':    history,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"  Written to {OUTPUT_FILE}")
    latest = history[-1]
    print(f"\n  Latest COT snapshot:")
    print(f"    Report date : {latest['reportDate']}")
    print(f"    OI          : {latest['oi']:,}")
    print(f"    MM Long     : {latest['mm_l']:,}")
    print(f"    MM Short    : {latest['mm_s']:,}")
    print(f"    MM Net      : {latest['mm_net']:+,}")
    print(f"    MM Net Chg  : {latest.get('mm_net_chg', 0):+,}")
    print(f"    SD Net      : {latest['sd_net']:+,}")
    print(f"    PM Net      : {latest['pm_net']:+,}")
    print("Done.")


if __name__ == '__main__':
    main()
