#!/usr/bin/env python3
"""
Taiwan Temperature Timelapse - Data Update Script

Reads hourly temperature grid data from the gis-platform Supabase database
(realtime.temperature_grids) via its PostgREST RPC functions and generates the
timelapse JSON file consumed by the frontend.

Data source migration (2026-05):
    Previously this script read per-file objects from S3
    (temperature/YYYY/MM/DD/temperature_HHMM.json). On 2026-02-28 the
    data-collectors project switched to "local storage + daily tar.gz archive"
    and stopped uploading per-file objects to S3, so that path went stale.
    The canonical live source is now gis-platform's Supabase DB, exposed through
    the get_temperature_* RPC functions (see gis-platform
    migrations/020_temperature_rpc.sql).

Usage:
    # Using .env file in project root (SUPABASE_URL / SUPABASE_KEY)
    python3 scripts/update_data.py

    # Limit to recent days
    python3 scripts/update_data.py --days 30

    # Specify date range
    python3 scripts/update_data.py --start-date 2026-05-10 --end-date 2026-05-20
"""

import json
import argparse
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
PUBLIC_DIR = PROJECT_ROOT / 'public'
OUTPUT_FILE = PUBLIC_DIR / 'temperature_timelapse_data.json'

# Fixed grid geometry (kept identical to the original S3 dataset so the frontend
# renders the same grid / map bounds). The land cells returned by gis-platform
# all fall inside these bounds.
GEO_INFO = {
    'bottom_left_lon': 120.0,
    'bottom_left_lat': 21.88,
    'top_right_lon': 121.98,
    'top_right_lat': 25.45,
    'resolution_deg': 0.03,
    'resolution_km': 3.3,
}

TAIPEI_TZ = timezone(timedelta(hours=8))


def load_env_file(env_path: Path) -> None:
    """Load environment variables from a .env file (simple parser)."""
    if not env_path.exists():
        return
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))


class GisPlatformReader:
    """Reads temperature grid data from gis-platform via Supabase PostgREST RPC."""

    def __init__(self, base_url: str, api_key: str):
        if not base_url:
            raise ValueError("SUPABASE_URL is not configured")
        if not api_key:
            raise ValueError("SUPABASE_KEY is not configured")
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key

    def rpc(self, fn: str, body: dict | None = None):
        url = f"{self.base_url}/rest/v1/rpc/{fn}"
        data = json.dumps(body or {}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'apikey': self.api_key,
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')
            raise RuntimeError(f"RPC {fn} failed: HTTP {e.code} {detail}") from e

    def list_dates(self) -> list[str]:
        rows = self.rpc('get_temperature_dates')
        return [r['date'] for r in rows]

    def grid_info(self, date: str) -> list[tuple[float, float]]:
        rows = self.rpc('get_temperature_grid_info', {'target_date': date})
        return [(r['grid_lat'], r['grid_lng']) for r in rows]

    def frames(self, date: str) -> list[dict]:
        return self.rpc('get_temperature_frames', {'target_date': date})


def _to_taipei_iso(observed_at: str) -> str:
    """Convert an RPC timestamp (ISO 8601, any tz) to Asia/Taipei +08:00 ISO."""
    # Python's fromisoformat handles the "+00:00" / "+08:00" offsets returned.
    dt = datetime.fromisoformat(observed_at)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TAIPEI_TZ).isoformat()


def _build_grid(cells: list[tuple[float, float]], temps: list[float]):
    """Reconstruct the dense [rows][cols] grid from aligned (lat,lng) + temp lists."""
    res = GEO_INFO['resolution_deg']
    bl_lat = GEO_INFO['bottom_left_lat']
    bl_lon = GEO_INFO['bottom_left_lon']
    n_rows = round((GEO_INFO['top_right_lat'] - bl_lat) / res) + 1
    n_cols = round((GEO_INFO['top_right_lon'] - bl_lon) / res) + 1

    grid = [[None] * n_cols for _ in range(n_rows)]
    valid = []
    for (lat, lng), temp in zip(cells, temps):
        row = round((lat - bl_lat) / res)
        col = round((lng - bl_lon) / res)
        if 0 <= row < n_rows and 0 <= col < n_cols:
            grid[row][col] = temp
            valid.append(temp)
    return grid, valid


def download_temperature_data(
    reader: GisPlatformReader,
    start_date: str | None = None,
    end_date: str | None = None,
    max_frames: int = 720,
) -> list[dict]:
    """Fetch and reconstruct temperature frames from gis-platform."""
    print("Listing available dates...")
    all_dates = reader.list_dates()

    if not all_dates:
        print("No temperature data found")
        return []

    print(f"  Found {len(all_dates)} dates ({all_dates[0]} ~ {all_dates[-1]})")

    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if d <= end_date]

    if not all_dates:
        print("No data in specified date range")
        return []

    print(f"  Processing {len(all_dates)} dates")

    frames: list[dict] = []
    skipped = 0

    for date in all_dates:
        cells = reader.grid_info(date)
        n_cells = len(cells)
        day_frames = reader.frames(date)

        for fr in day_frames:
            # Alignment contract (gis-platform migrations/020_temperature_rpc.sql):
            # grid_info is the day's DISTINCT cell union, ordered by lat,lng;
            # a frame's `temps` is ordered the same way over the cells present at
            # that timestamp. When cell_count == len(union) the frame covers the
            # full union, so positions align 1:1 and reconstruction is exact.
            # Partial/duplicate frames (e.g. double inserts -> 2x cell_count) are
            # skipped rather than risk misaligned placement.
            if fr['cell_count'] != n_cells:
                skipped += 1
                continue

            temps = [float(x) for x in fr['temps'].split(',')]
            grid, valid = _build_grid(cells, temps)
            if not valid:
                skipped += 1
                continue

            frames.append({
                'time': _to_taipei_iso(fr['observed_at']),
                'stats': {
                    'min': round(min(valid), 1),
                    'max': round(max(valid), 1),
                    'avg': round(sum(valid) / len(valid), 1),
                    'valid_points': len(valid),
                },
                'data': grid,
            })

        print(f"  {date}: {len(day_frames)} frames")

    # Sort by time and keep the most recent max_frames
    frames.sort(key=lambda f: f['time'])
    if len(frames) > max_frames:
        print(f"  Limiting to latest {max_frames} frames")
        frames = frames[-max_frames:]

    print(f"Reconstructed {len(frames)} valid frames ({skipped} partial/empty skipped)")
    return frames


def generate_timelapse_json(frames: list[dict], output_path: Path) -> dict:
    """Generate the timelapse JSON file."""
    if not frames:
        return {}

    timelapse_data = {
        'metadata': {
            'generated_at': datetime.now(TAIPEI_TZ).isoformat(),
            'start_time': frames[0]['time'],
            'end_time': frames[-1]['time'],
            'total_frames': len(frames),
            'geo_info': GEO_INFO,
            'source': 'Central Weather Administration O-A0038-003 (via gis-platform)',
            'description': 'Taiwan Temperature Grid Timelapse',
        },
        'frames': frames,
    }

    print("Saving timelapse data...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(timelapse_data, f, ensure_ascii=False)

    file_size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved: {output_path.name} ({file_size_mb:.2f} MB)")
    return timelapse_data


def main():
    parser = argparse.ArgumentParser(description='Update Taiwan Temperature Timelapse data')
    parser.add_argument('--env-file', type=Path, help='Path to .env file (default: PROJECT_ROOT/.env)')
    parser.add_argument('--days', type=int, help='Limit to recent N days')
    parser.add_argument('--start-date', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='End date (YYYY-MM-DD)')
    parser.add_argument('--max-frames', type=int, default=720, help='Maximum number of frames (default: 720, ~30 days)')
    parser.add_argument('--output', type=Path, default=OUTPUT_FILE, help=f'Output file path (default: {OUTPUT_FILE})')
    args = parser.parse_args()

    print("=" * 60)
    print("Taiwan Temperature Timelapse - Data Update (gis-platform)")
    print("=" * 60)

    # Load .env if credentials are not already in the environment (e.g. CI)
    if not os.getenv('SUPABASE_URL'):
        env_path = args.env_file or (PROJECT_ROOT / '.env')
        if env_path.exists():
            print(f"Loading env: {env_path}")
            load_env_file(env_path)

    base_url = os.getenv('SUPABASE_URL')
    api_key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    if not base_url or not api_key:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not configured")
        print("  Set them as environment variables or in the project .env file")
        sys.exit(1)

    print(f"  Supabase URL: {base_url}")

    start_date = args.start_date
    end_date = args.end_date
    if args.days:
        end_date = datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')
        start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"  Limiting to last {args.days} days")

    try:
        reader = GisPlatformReader(base_url, api_key)
        frames = download_temperature_data(
            reader,
            start_date=start_date,
            end_date=end_date,
            max_frames=args.max_frames,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    if not frames:
        print("ERROR: No temperature data available")
        sys.exit(1)

    timelapse_data = generate_timelapse_json(frames, args.output)

    print()
    print("=" * 60)
    print("Update Complete")
    print("=" * 60)
    print(f"  Time range: {timelapse_data['metadata']['start_time'][:16]} ~ {timelapse_data['metadata']['end_time'][:16]}")
    print(f"  Total frames: {timelapse_data['metadata']['total_frames']}")
    print(f"  Output: {args.output}")


if __name__ == '__main__':
    main()
