#!/usr/bin/env python3
"""
Taiwan Humidity Timelapse - Data Update Script

Downloads weather station data from the gis-platform Supabase (PostgREST) view
`public.weather_observations`, interpolates humidity using scipy griddata,
and generates the timelapse JSON file.

Usage:
    # Using .env file in project root
    python scripts/update_humidity.py

    # Limit to recent days
    python scripts/update_humidity.py --days 7

    # Specify date range
    python scripts/update_humidity.py --start-date 2025-01-10 --end-date 2025-01-15
"""

import json
import argparse
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple

# Try to load dotenv
try:
    from dotenv import load_dotenv
    HAS_DOTENV = True
except ImportError:
    HAS_DOTENV = False

# Try to load boto3
try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

# Try to load scipy and numpy
try:
    import numpy as np
    from scipy.interpolate import griddata
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
PUBLIC_DIR = PROJECT_ROOT / 'public'
OUTPUT_FILE = PUBLIC_DIR / 'humidity_timelapse_data.json'
TEMPERATURE_FILE = PUBLIC_DIR / 'temperature_timelapse_data.json'

# Grid configuration (matching temperature grid)
GEO_INFO = {
    'bottom_left_lon': 120.0,
    'bottom_left_lat': 21.88,
    'top_right_lon': 121.98,
    'top_right_lat': 25.45,
    'resolution_deg': 0.03,
    'resolution_km': 3.3,
    'grid_rows': 120,
    'grid_cols': 67
}


def load_env_file(env_path: Path) -> Dict[str, str]:
    """Load environment variables from .env file"""
    if HAS_DOTENV:
        load_dotenv(env_path)
    else:
        if env_path.exists():
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip().strip('"\'')

    return {
        'SUPABASE_URL': os.getenv('SUPABASE_URL'),
        'SUPABASE_KEY': os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_ANON_KEY'),
    }


# Taipei timezone (UTC+8)
TAIPEI_TZ = timezone(timedelta(hours=8))


class GisWeatherReader:
    """
    gis-platform Supabase (PostgREST) Weather Station Data Reader.

    Reads from the `public.weather_observations` view via anonymous PostgREST.
    Station latitude/longitude/altitude are joined locally from
    `public/stations.json` (the view has no altitude column).
    """

    # 只取濕度內插需要的欄位
    SELECT_FIELDS = 'station_id,observed_at,humidity'

    def __init__(self, base_url: str, api_key: str):
        if not base_url:
            raise ValueError("SUPABASE_URL is not configured")
        if not api_key:
            raise ValueError("SUPABASE_KEY is not configured")

        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.station_meta = self._load_station_meta()

        if not self.station_meta:
            raise ValueError("No station metadata loaded from stations.json")

    def _load_station_meta(self) -> Dict[str, Dict[str, float]]:
        """
        載入 public/stations.json，建立 station_id -> 中繼資料 對照表。
        注意 JSON 是 lat/lon，這裡轉成 interpolate 需要的 latitude/longitude key。
        """
        stations_path = PUBLIC_DIR / 'stations.json'
        with open(stations_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        # stations.json 可能是 list，也可能是 {"count":..., "stations":[...]}
        stations = raw['stations'] if isinstance(raw, dict) else raw

        meta = {}
        for s in stations:
            sid = s.get('id')
            if not sid:
                continue
            meta[sid] = {
                'latitude': s.get('lat'),
                'longitude': s.get('lon'),
                'altitude': s.get('altitude'),
            }
        return meta

    def _request(self, path: str, extra_headers: Optional[Dict[str, str]] = None) -> Tuple[Any, Dict[str, str]]:
        """送出 PostgREST GET 請求，回傳 (json, response_headers)"""
        url = f"{self.base_url}/rest/v1/{path}"
        headers = {
            'apikey': self.api_key,
            'Authorization': f"Bearer {self.api_key}",
            'Accept': 'application/json',
        }
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            content = resp.read().decode('utf-8')
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return json.loads(content), resp_headers

    def list_dates(self) -> List[str]:
        """
        從 weather_observations 取得時間範圍 (observed_at, UTC)，
        轉成台北日期清單回傳。
        """
        try:
            newest, _ = self._request(
                'weather_observations?select=observed_at&order=observed_at.desc&limit=1'
            )
            oldest, _ = self._request(
                'weather_observations?select=observed_at&order=observed_at.asc&limit=1'
            )
        except urllib.error.URLError as e:
            print(f"Error querying observation range: {e}")
            return []

        if not newest or not oldest:
            return []

        end_date = self._utc_to_taipei(newest[0]['observed_at']).date()
        start_date = self._utc_to_taipei(oldest[0]['observed_at']).date()

        dates = []
        d = start_date
        while d <= end_date:
            dates.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
        return dates

    def list_files_by_date(self, date: str) -> List[Dict[str, Any]]:
        """
        抓取指定台北日期內的所有觀測，依 observed_at 分組成多個 frame。
        回傳每個 frame 的 {'time': <ISO+08:00>, 'stations': [...]}，
        其中 stations 已補上 latitude/longitude/altitude，可直接餵給 interpolate。
        """
        try:
            day_start = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=TAIPEI_TZ)
        except ValueError:
            return []
        day_end = day_start + timedelta(days=1)

        start_str = urllib.parse.quote(day_start.isoformat(), safe='')
        end_str = urllib.parse.quote(day_end.isoformat(), safe='')

        path = (
            f"weather_observations?select={self.SELECT_FIELDS}"
            f"&observed_at=gte.{start_str}&observed_at=lt.{end_str}"
            f"&order=observed_at"
        )

        rows = self._fetch_all(path)
        if not rows:
            return []

        # 依 observed_at 分組；組內每個 station_id 去重（保留第一筆）
        groups: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for row in rows:
            observed_at = row.get('observed_at')
            if observed_at is None:
                continue
            if observed_at not in groups:
                groups[observed_at] = {}
                order.append(observed_at)

            sid = row.get('station_id')
            if sid is None or sid in groups[observed_at]:
                continue

            meta = self.station_meta.get(sid)
            if meta is None:
                # station_id 不在 stations.json，跳過
                continue

            station = dict(meta)
            station['humidity'] = row.get('humidity')
            groups[observed_at][sid] = station

        frames = []
        for observed_at in order:
            stations = list(groups[observed_at].values())
            if not stations:
                continue
            frame_time = self._utc_to_taipei(observed_at).isoformat()
            frames.append({'time': frame_time, 'stations': stations})

        return sorted(frames, key=lambda x: x['time'])

    def _fetch_all(self, path: str, page_size: int = 10000) -> List[Dict]:
        """處理 PostgREST 分頁：用 Range header 持續抓到取完"""
        results: List[Dict] = []
        offset = 0
        while True:
            range_header = {'Range': f"{offset}-{offset + page_size - 1}"}
            try:
                rows, headers = self._request(path, extra_headers=range_header)
            except urllib.error.URLError as e:
                print(f"Error fetching page (offset={offset}): {e}")
                break

            if not rows:
                break

            results.extend(rows)

            # Content-Range 格式: start-end/total
            content_range = headers.get('content-range', '')
            total = None
            if '/' in content_range:
                total_part = content_range.split('/')[-1]
                if total_part.isdigit():
                    total = int(total_part)

            offset += len(rows)
            if total is not None and offset >= total:
                break
            if len(rows) < page_size:
                break

        return results

    @staticmethod
    def _utc_to_taipei(observed_at: str) -> datetime:
        """將 observed_at (UTC ISO 字串) 轉為台北時區 datetime"""
        # PostgREST 可能回傳 'Z' 或 '+00:00'
        ts = observed_at.replace('Z', '+00:00')
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI_TZ)


def load_land_mask() -> Optional[np.ndarray]:
    """
    Load land mask from temperature data.
    Returns a boolean array where True = land (has temperature data).
    """
    if not TEMPERATURE_FILE.exists():
        print("Warning: Temperature file not found, no land mask applied")
        return None

    try:
        with open(TEMPERATURE_FILE, 'r') as f:
            temp_data = json.load(f)

        # Use first frame to create mask
        first_frame = temp_data['frames'][0]['data']
        mask = np.array([
            [val is not None and val > -900 for val in row]
            for row in first_frame
        ])
        return mask
    except Exception as e:
        print(f"Warning: Could not load land mask: {e}")
        return None


# Global land mask (loaded once)
LAND_MASK = None


def get_land_mask() -> Optional[np.ndarray]:
    """Get or load the land mask."""
    global LAND_MASK
    if LAND_MASK is None:
        LAND_MASK = load_land_mask()
    return LAND_MASK


def interpolate_humidity(stations: List[Dict], geo_info: Dict) -> Tuple[List[List], Dict]:
    """
    Interpolate station humidity data to regular grid using scipy griddata.

    Args:
        stations: List of station data with latitude, longitude, humidity
        geo_info: Grid configuration

    Returns:
        Tuple of (grid_data as 2D list, stats dict)
    """
    # Filter stations with valid humidity data
    valid_stations = [
        s for s in stations
        if s.get('humidity') is not None
        and s.get('latitude') is not None
        and s.get('longitude') is not None
    ]

    if len(valid_stations) < 4:
        return None, {'error': 'Not enough valid stations'}

    # Extract coordinates and values
    points = np.array([
        (float(s['longitude']), float(s['latitude']))
        for s in valid_stations
    ])
    values = np.array([float(s['humidity']) for s in valid_stations])

    # Create target grid
    lon = np.linspace(
        geo_info['bottom_left_lon'],
        geo_info['top_right_lon'],
        geo_info['grid_cols']
    )
    lat = np.linspace(
        geo_info['bottom_left_lat'],
        geo_info['top_right_lat'],
        geo_info['grid_rows']
    )
    grid_lon, grid_lat = np.meshgrid(lon, lat)

    # Interpolate using cubic method (fallback to linear if fails)
    try:
        grid_humidity = griddata(
            points, values,
            (grid_lon, grid_lat),
            method='cubic'
        )
    except Exception:
        grid_humidity = griddata(
            points, values,
            (grid_lon, grid_lat),
            method='linear'
        )

    # Clip values to valid humidity range (0-100%)
    # Cubic interpolation can produce values outside the input range at edges
    grid_humidity = np.clip(grid_humidity, 0, 100)

    # Apply land mask from temperature data
    # This ensures humidity is only shown where temperature data exists (land areas)
    land_mask = get_land_mask()
    if land_mask is not None:
        # Set ocean areas to NaN
        grid_humidity[~land_mask] = np.nan

    # Calculate statistics (excluding NaN and clipped edge values)
    valid_grid = grid_humidity[~np.isnan(grid_humidity)]
    # Exclude values that were clipped to exactly 0 or 100 (edge artifacts)
    inner_values = valid_grid[(valid_grid > 0.1) & (valid_grid < 99.9)]

    stats = {
        'min': round(float(np.min(inner_values)), 1) if len(inner_values) > 0 else None,
        'max': round(float(np.max(inner_values)), 1) if len(inner_values) > 0 else None,
        'avg': round(float(np.mean(valid_grid)), 1) if len(valid_grid) > 0 else None,
        'valid_points': int(np.sum(~np.isnan(grid_humidity))),
        'station_count': len(valid_stations)
    }

    # Convert to list, replacing NaN with None
    grid_list = []
    for row in grid_humidity:
        row_list = []
        for val in row:
            if np.isnan(val):
                row_list.append(None)
            else:
                row_list.append(round(float(val), 1))
        grid_list.append(row_list)

    return grid_list, stats


def download_and_interpolate_humidity(
    reader: GisWeatherReader,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_frames: int = 720
) -> List[Dict]:
    """Download weather data and interpolate humidity"""
    print("Listing available dates...")
    all_dates = reader.list_dates()

    if not all_dates:
        print("No weather data found")
        return []

    print(f"  Found {len(all_dates)} dates ({all_dates[0]} ~ {all_dates[-1]})")

    # Filter date range
    if start_date:
        all_dates = [d for d in all_dates if d >= start_date]
    if end_date:
        all_dates = [d for d in all_dates if d <= end_date]

    if not all_dates:
        print("No data in specified date range")
        return []

    print(f"  Processing {len(all_dates)} dates")

    # Collect all observation frames (one per observed_at)
    all_obs = []
    for date in all_dates:
        obs = reader.list_files_by_date(date)
        all_obs.extend(obs)

    # 依時間排序所有 frames
    all_obs.sort(key=lambda x: x['time'])

    print(f"  Total {len(all_obs)} weather frames")

    # Limit frames (保留最新 max_frames 個)
    if len(all_obs) > max_frames:
        print(f"  Limiting to latest {max_frames} frames")
        all_obs = all_obs[-max_frames:]

    # Interpolate
    frames = []
    total = len(all_obs)

    print(f"Interpolating humidity data ({total} frames)...")

    for i, obs in enumerate(all_obs):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Progress: {i + 1}/{total} ({(i + 1) / total * 100:.1f}%)")

        stations = obs['stations']
        grid_data, stats = interpolate_humidity(stations, GEO_INFO)

        if grid_data:
            frame = {
                'time': obs['time'],
                'stats': stats,
                'data': grid_data
            }
            frames.append(frame)

    print(f"Processed {len(frames)} valid frames")
    return frames


def generate_timelapse_json(frames: List[Dict], output_path: Path) -> Dict:
    """Generate timelapse JSON file"""
    if not frames:
        return {}

    timelapse_data = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'start_time': frames[0]['time'],
            'end_time': frames[-1]['time'],
            'total_frames': len(frames),
            'geo_info': GEO_INFO,
            'source': 'Central Weather Administration Weather Stations',
            'description': 'Taiwan Humidity Grid Timelapse (Interpolated)',
            'interpolation_method': 'scipy.griddata cubic'
        },
        'frames': frames
    }

    # Save JSON
    print(f"Saving timelapse data...")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(timelapse_data, f, ensure_ascii=False)

    file_size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Saved: {output_path.name} ({file_size_mb:.2f} MB)")

    return timelapse_data


def main():
    parser = argparse.ArgumentParser(
        description='Update Taiwan Humidity Timelapse data'
    )
    parser.add_argument(
        '--env-file',
        type=Path,
        help='Path to .env file (default: PROJECT_ROOT/.env)'
    )
    parser.add_argument(
        '--days',
        type=int,
        help='Limit to recent N days'
    )
    parser.add_argument(
        '--start-date',
        help='Start date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end-date',
        help='End date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--max-frames',
        type=int,
        default=720,
        help='Maximum number of frames (default: 720, ~30 days)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=OUTPUT_FILE,
        help=f'Output file path (default: {OUTPUT_FILE})'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Taiwan Humidity Timelapse - Data Update")
    print("=" * 60)

    # Check dependencies
    if not HAS_SCIPY:
        print("ERROR: scipy/numpy is not installed")
        print("  Run: pip install scipy numpy")
        sys.exit(1)

    # Check if environment variables are already set (e.g., in CI)
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_ANON_KEY')

    # If not set, try loading from .env file
    if not (supabase_url and supabase_key):
        env_path = args.env_file
        if not env_path:
            possible_paths = [
                PROJECT_ROOT / '.env',
                Path.home() / '.env.weather_change',
            ]
            for p in possible_paths:
                if p.exists():
                    env_path = p
                    break

        if env_path and env_path.exists():
            print(f"Loading env: {env_path}")
            config = load_env_file(env_path)
            supabase_url = supabase_url or config.get('SUPABASE_URL')
            supabase_key = supabase_key or config.get('SUPABASE_KEY')
    else:
        print("Using environment variables")

    if not supabase_url or not supabase_key:
        print("ERROR: SUPABASE_URL / SUPABASE_KEY are not configured")
        print("  Set environment variables or create .env file")
        sys.exit(1)

    print(f"  Supabase URL: {supabase_url}")

    # Calculate date range
    start_date = args.start_date
    end_date = args.end_date

    if args.days:
        now_taipei = datetime.now(TAIPEI_TZ)
        end_date = now_taipei.strftime('%Y-%m-%d')
        start_date = (now_taipei - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"  Limiting to last {args.days} days")

    # Initialize Supabase reader
    try:
        reader = GisWeatherReader(supabase_url, supabase_key)
    except Exception as e:
        print(f"ERROR: Cannot initialize Supabase reader: {e}")
        sys.exit(1)

    # Download and interpolate
    frames = download_and_interpolate_humidity(
        reader,
        start_date=start_date,
        end_date=end_date,
        max_frames=args.max_frames
    )

    if not frames:
        print("ERROR: No humidity data available")
        sys.exit(1)

    # Generate JSON
    timelapse_data = generate_timelapse_json(frames, args.output)

    # Summary
    print()
    print("=" * 60)
    print("Update Complete")
    print("=" * 60)
    print(f"  Time range: {timelapse_data['metadata']['start_time'][:10]} ~ {timelapse_data['metadata']['end_time'][:10]}")
    print(f"  Total frames: {timelapse_data['metadata']['total_frames']}")
    print(f"  Output: {args.output}")


if __name__ == '__main__':
    main()
