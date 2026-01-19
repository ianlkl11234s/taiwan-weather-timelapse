#!/usr/bin/env python3
"""
Taiwan Humidity Timelapse - Data Update Script

Downloads weather station data from S3, interpolates humidity using scipy griddata,
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
from pathlib import Path
from datetime import datetime, timedelta
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
        'S3_BUCKET': os.getenv('S3_BUCKET'),
        'S3_ACCESS_KEY': os.getenv('S3_ACCESS_KEY'),
        'S3_SECRET_KEY': os.getenv('S3_SECRET_KEY'),
        'S3_REGION': os.getenv('S3_REGION', 'ap-northeast-1'),
        'S3_ENDPOINT': os.getenv('S3_ENDPOINT'),
    }


class S3WeatherReader:
    """S3 Weather Station Data Reader"""

    def __init__(self, config: Dict[str, str]):
        if not HAS_BOTO3:
            raise ImportError("boto3 is required. Install with: pip install boto3")

        if not config.get('S3_BUCKET'):
            raise ValueError("S3_BUCKET is not configured")

        client_kwargs = {
            'region_name': config.get('S3_REGION', 'ap-northeast-1'),
        }

        if config.get('S3_ACCESS_KEY') and config.get('S3_SECRET_KEY'):
            client_kwargs['aws_access_key_id'] = config['S3_ACCESS_KEY']
            client_kwargs['aws_secret_access_key'] = config['S3_SECRET_KEY']

        if config.get('S3_ENDPOINT'):
            client_kwargs['endpoint_url'] = config['S3_ENDPOINT']

        self.s3 = boto3.client('s3', **client_kwargs)
        self.bucket = config['S3_BUCKET']

    def list_dates(self) -> List[str]:
        """List all dates with weather data"""
        dates = set()
        prefix = "weather/"
        paginator = self.s3.get_paginator('list_objects_v2')

        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter='/'):
                for cp in page.get('CommonPrefixes', []):
                    year_prefix = cp['Prefix']
                    for month_page in paginator.paginate(Bucket=self.bucket, Prefix=year_prefix, Delimiter='/'):
                        for month_cp in month_page.get('CommonPrefixes', []):
                            month_prefix = month_cp['Prefix']
                            for day_page in paginator.paginate(Bucket=self.bucket, Prefix=month_prefix, Delimiter='/'):
                                for day_cp in day_page.get('CommonPrefixes', []):
                                    parts = day_cp['Prefix'].rstrip('/').split('/')
                                    if len(parts) >= 4:
                                        date_str = f"{parts[1]}-{parts[2]}-{parts[3]}"
                                        dates.add(date_str)
        except ClientError as e:
            print(f"Error listing S3 data: {e}")
            return []

        return sorted(dates)

    def list_files_by_date(self, date: str) -> List[Dict[str, Any]]:
        """List all weather files for a specific date"""
        try:
            parsed_date = datetime.strptime(date, '%Y-%m-%d')
            prefix = f"weather/{parsed_date.strftime('%Y/%m/%d')}/"
        except ValueError:
            return []

        files = []
        paginator = self.s3.get_paginator('list_objects_v2')

        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('.json') and 'latest' not in key:
                        filename = key.split('/')[-1]
                        if filename.startswith('weather_'):
                            time_str = filename.replace('weather_', '').replace('.json', '')
                            if len(time_str) == 4:
                                hour = time_str[:2]
                                minute = time_str[2:]
                                files.append({
                                    'key': key,
                                    'time': f"{date}T{hour}:{minute}:00+08:00",
                                    'size': obj['Size']
                                })
        except ClientError as e:
            print(f"Error listing files for {date}: {e}")

        return sorted(files, key=lambda x: x['time'])

    def get_json(self, s3_key: str) -> Optional[Dict]:
        """Read JSON file from S3"""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            content = response['Body'].read().decode('utf-8')
            return json.loads(content)
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                return None
            print(f"Error reading {s3_key}: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"JSON parse error for {s3_key}: {e}")
            return None


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
    reader: S3WeatherReader,
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

    # Collect all files
    all_files = []
    for date in all_dates:
        files = reader.list_files_by_date(date)
        all_files.extend(files)

    print(f"  Total {len(all_files)} weather files")

    # Limit frames
    if len(all_files) > max_frames:
        print(f"  Limiting to latest {max_frames} frames")
        all_files = all_files[-max_frames:]

    # Download and interpolate
    frames = []
    total = len(all_files)

    print(f"Downloading and interpolating humidity data ({total} files)...")

    for i, file_info in enumerate(all_files):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Progress: {i + 1}/{total} ({(i + 1) / total * 100:.1f}%)")

        data = reader.get_json(file_info['key'])
        if data and 'data' in data:
            stations = data['data']
            grid_data, stats = interpolate_humidity(stations, GEO_INFO)

            if grid_data:
                frame = {
                    'time': file_info['time'],
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
    if not HAS_BOTO3:
        print("ERROR: boto3 is not installed")
        print("  Run: pip install boto3")
        sys.exit(1)

    if not HAS_SCIPY:
        print("ERROR: scipy/numpy is not installed")
        print("  Run: pip install scipy numpy")
        sys.exit(1)

    # Check if environment variables are already set (e.g., in CI)
    s3_config = {
        'S3_BUCKET': os.getenv('S3_BUCKET'),
        'S3_ACCESS_KEY': os.getenv('S3_ACCESS_KEY'),
        'S3_SECRET_KEY': os.getenv('S3_SECRET_KEY'),
        'S3_REGION': os.getenv('S3_REGION', 'ap-northeast-1'),
        'S3_ENDPOINT': os.getenv('S3_ENDPOINT'),
    }

    # If not set, try loading from .env file
    if not s3_config.get('S3_BUCKET'):
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

        if not env_path or not env_path.exists():
            print("ERROR: No S3 credentials found")
            print("  Set environment variables or create .env file")
            sys.exit(1)

        print(f"Loading env: {env_path}")
        s3_config = load_env_file(env_path)
    else:
        print("Using environment variables")

    if not s3_config.get('S3_BUCKET'):
        print("ERROR: S3_BUCKET is not configured")
        sys.exit(1)

    print(f"  S3 Bucket: {s3_config['S3_BUCKET']}")
    print(f"  S3 Region: {s3_config.get('S3_REGION', 'ap-northeast-1')}")

    # Calculate date range
    start_date = args.start_date
    end_date = args.end_date

    if args.days:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
        print(f"  Limiting to last {args.days} days")

    # Initialize S3 reader
    try:
        reader = S3WeatherReader(s3_config)
    except Exception as e:
        print(f"ERROR: Cannot connect to S3: {e}")
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
