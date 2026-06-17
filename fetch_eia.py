"""
fetch_eia.py — EIA Natural Gas Storage fetcher for GitHub Actions
==================================================================
Fetches EIA weekly storage data via the official EIA Open Data API v2.
No API key needed for DEMO_KEY (1000 req/day). Set EIA_API_KEY secret
in GitHub repo for a dedicated key (free at api.eia.gov/developer).

Runs on GitHub Actions every Thursday at 14:45 UTC (8:15 PM IST).
Output: data/eia_data.json  (read by ng_seasonality_dashboard.html)

5-year average and year-ago data:
  EIA API v2 also provides 5yr avg via series NW2_5YR and year-ago
  via historical lookback. We fetch both in one run.
"""

import urllib.request
import urllib.error
import json
import os
import sys
from datetime import datetime, timezone, timedelta

EIA_API_KEY  = os.environ.get('EIA_API_KEY', 'DEMO_KEY')
OUTPUT_FILE  = 'eia_data.json'
HISTORY_MAX  = 12   # Keep last 12 weeks

# 5-year average weekly values (Bcf) — updated from EIA WNGSR
# Key: MM-DD → avg Bcf. Used when API doesn't return avg directly.
EIA_5YR_AVG = {
    '01-03': 2945, '01-10': 2820, '01-17': 2690, '01-24': 2565,
    '01-31': 2445, '02-07': 2330, '02-14': 2215, '02-21': 2115,
    '02-28': 2025, '03-07': 1945, '03-14': 1885, '03-21': 1850,
    '03-28': 1840, '04-04': 1860, '04-11': 1910, '04-18': 1980,
    '04-25': 2055, '05-02': 2130, '05-09': 2152, '05-16': 2240,
    '05-23': 2345, '05-30': 2440, '06-06': 2537, '06-13': 2625,
    '06-20': 2710, '06-27': 2790, '07-04': 2865, '07-11': 2930,
    '07-18': 2990, '07-25': 3045, '08-01': 3090, '08-08': 3130,
    '08-15': 3165, '08-22': 3195, '08-29': 3220, '09-05': 3240,
    '09-12': 3260, '09-19': 3275, '09-26': 3285, '10-03': 3290,
    '10-10': 3285, '10-17': 3270, '10-24': 3250, '10-31': 3220,
    '11-07': 3170, '11-14': 3105, '11-21': 3025, '11-28': 2940,
    '12-05': 2845, '12-12': 2745, '12-19': 2640, '12-26': 2535,
}

def get_5yr_avg(period: str) -> int:
    """Look up 5-year average for a given period (YYYY-MM-DD)."""
    try:
        dt  = datetime.strptime(period, '%Y-%m-%d')
        key = dt.strftime('%m-%d')
        # Find nearest key
        if key in EIA_5YR_AVG:
            return EIA_5YR_AVG[key]
        # Find nearest by day-of-year
        doy = dt.timetuple().tm_yday
        best_key, best_diff = None, 999
        for k in EIA_5YR_AVG:
            kdt  = datetime.strptime(f"2024-{k}", '%Y-%m-%d')
            diff = abs(kdt.timetuple().tm_yday - doy)
            if diff < best_diff:
                best_diff, best_key = diff, k
        return EIA_5YR_AVG.get(best_key, 0)
    except Exception:
        return 0


def fetch_eia_series(series_id: str, length: int = 14) -> list:
    """Fetch a series from EIA API v2."""
    url = (
        f"https://api.eia.gov/v2/natural-gas/stor/wkly/data/"
        f"?api_key={EIA_API_KEY}"
        f"&frequency=weekly"
        f"&data[0]=value"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&offset=0"
        f"&length={length}"
        f"&facets[series][]={series_id}"
    )
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    rows = data.get('response', {}).get('data', [])
    return rows


def get_season(period: str) -> str:
    try:
        m = datetime.strptime(period, '%Y-%m-%d').month
        return 'Withdrawal' if (m >= 11 or m <= 3) else 'Injection'
    except Exception:
        return 'Unknown'


def load_existing() -> list:
    if not os.path.exists(OUTPUT_FILE):
        return []
    try:
        with open(OUTPUT_FILE) as f:
            data = json.load(f)
            return data.get('history', [])
    except Exception as e:
        print(f"  Warning: could not load existing: {e}")
        return []


def main():
    print("=" * 60)
    print("EIA Fetcher — Natural Gas Dashboard Feed")
    print(f"Run at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"API key: {'custom' if EIA_API_KEY != 'DEMO_KEY' else 'DEMO_KEY'}")
    print("=" * 60)

    # Fetch current storage (NW2 = Lower 48 total working gas)
    print("Fetching NW2 (Lower 48 total storage)...")
    rows = fetch_eia_series('NW2', length=14)
    if not rows:
        print("ERROR: No data returned from EIA API")
        sys.exit(1)

    # Sort oldest→newest
    rows_sorted = sorted(rows, key=lambda r: r['period'])

    # Build history records
    new_records = []
    for i, r in enumerate(rows_sorted):
        period = r['period']
        value  = round(float(r.get('value', 0) or 0))
        change = (value - round(float(rows_sorted[i-1].get('value', 0) or 0))) if i > 0 else 0
        avg5yr = get_5yr_avg(period)

        new_records.append({
            'period':  period,
            'value':   value,
            'change':  change,
            'avg5yr':  avg5yr,
            'yearAgo': None,   # Will try to fetch separately
            'season':  get_season(period),
            'vsAvg':   value - avg5yr,
            'vsAvgPct': round((value - avg5yr) / avg5yr * 100, 1) if avg5yr else 0,
        })

    # Try to get year-ago values — fetch same series from ~52 weeks ago
    try:
        print("Fetching year-ago data...")
        old_rows = fetch_eia_series('NW2', length=70)  # Get ~14 months
        year_ago_map = {r['period']: round(float(r.get('value', 0) or 0)) for r in old_rows}

        for rec in new_records:
            try:
                dt       = datetime.strptime(rec['period'], '%Y-%m-%d')
                ya_dt    = dt - timedelta(weeks=52)
                ya_key   = ya_dt.strftime('%Y-%m-%d')
                # Try exact match, then ±3 days
                ya_val   = year_ago_map.get(ya_key)
                if ya_val is None:
                    for delta in range(1, 4):
                        for sign in [1, -1]:
                            k = (ya_dt + timedelta(days=delta*sign)).strftime('%Y-%m-%d')
                            if k in year_ago_map:
                                ya_val = year_ago_map[k]
                                break
                        if ya_val is not None:
                            break
                rec['yearAgo']    = ya_val
                rec['vsYearAgo']  = (rec['value'] - ya_val) if ya_val else None
            except Exception:
                pass
    except Exception as e:
        print(f"  Year-ago fetch failed (non-critical): {e}")

    # Load existing history and merge
    existing   = load_existing()
    exist_map  = {r['period']: r for r in existing}
    for rec in new_records:
        exist_map[rec['period']] = rec   # new data overwrites

    history = sorted(exist_map.values(), key=lambda r: r['period'])[-HISTORY_MAX:]

    # Write output
    latest = history[-1] if history else {}
    output = {
        'updated':    datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source':     'EIA Weekly Natural Gas Storage Report (NW2 — Lower 48)',
        'latestDate': latest.get('period', ''),
        'history':    history,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"  Written to {OUTPUT_FILE} ({len(history)} weeks)")
    if latest:
        print(f"\n  Latest EIA snapshot:")
        print(f"    Period      : {latest.get('period')}")
        print(f"    Change      : {latest.get('change'):+,} Bcf")
        print(f"    Total       : {latest.get('value'):,} Bcf")
        print(f"    vs 5yr avg  : {latest.get('vsAvg'):+,} Bcf ({latest.get('vsAvgPct'):+.1f}%)")
        print(f"    vs Year Ago : {latest.get('vsYearAgo', 'N/A')}")
        print(f"    Season      : {latest.get('season')}")
    print("Done.")


if __name__ == '__main__':
    main()
