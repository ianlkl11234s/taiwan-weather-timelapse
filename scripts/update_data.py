#!/usr/bin/env python3
"""
Taiwan Temperature Timelapse - Data Update Script

Downloads temperature grid data from S3 and generates the timelapse JSON file.
This script is designed to run standalone without external dependencies.

Usage:
    # Using .env file in project root
    python scripts/update_data.py

    # Specify custom .env path
    python scripts/update_data.py --env-file /path/to/.env

    # Limit to recent days
    python scripts/update_data.py --days 7

    # Specify date range
    python scripts/update_data.py --start-date 2025-01-10 --end-date 2025-01-15
"""

import json
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

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


# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
PUBLIC_DIR = PROJECT_ROOT / 'public'
OUTPUT_FILE = PUBLIC_DIR / 'temperature_timelapse_data.json'


def load_env_file(env_path: Path) -> Dict[str, str]:
    """Load environment variables from .env file"""
    if HAS_DOTENV:
        load_dotenv(env_path)
    else:
        # Manual .env parsing
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


class S3TemperatureReader:
    """S3 Temperature Data Reader"""

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
        """List all dates with temperature data"""
        dates = set()
        prefix = "temperature/"
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
        """List all temperature files for a specific date"""
        try:
            parsed_date = datetime.strptime(date, '%Y-%m-%d')
            prefix = f"temperature/{parsed_date.strftime('%Y/%m/%d')}/"
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
                        if filename.startswith('temperature_'):
                            time_str = filename.replace('temperature_', '').replace('.json', '')
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


def download_temperature_data(
    reader: S3TemperatureReader,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_frames: int = 720
) -> List[Dict]:
    """Download temperature data from S3"""
    print("Listing available dates...")
    all_dates = reader.list_dates()

    if not all_dates:
        print("No temperature data found")
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

    print(f"  Total {len(all_files)} temperature files")

    # Limit frames
    if len(all_files) > max_frames:
        print(f"  Limiting to latest {max_frames} frames")
        all_files = all_files[-max_frames:]

    # Download data
    frames = []
    total = len(all_files)

    print(f"Downloading temperature data ({total} files)...")

    for i, file_info in enumerate(all_files):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Progress: {i + 1}/{total} ({(i + 1) / total * 100:.1f}%)")

        data = reader.get_json(file_info['key'])
        if data:
            frame = {
                'time': file_info['time'],
                'stats': {
                    'min': data.get('min_temp'),
                    'max': data.get('max_temp'),
                    'avg': data.get('avg_temp'),
                    'valid_points': data.get('valid_points', 0)
                },
                'data': data.get('data', [])
            }

            # Save geo_info from first frame
            if not frames and data.get('geo_info'):
                frame['geo_info'] = data['geo_info']

            frames.append(frame)

    print(f"Downloaded {len(frames)} valid frames")
    return frames


def generate_timelapse_json(frames: List[Dict], output_path: Path) -> Dict:
    """Generate timelapse JSON file"""
    if not frames:
        return {}

    # Get geo_info from first frame with it
    geo_info = None
    for frame in frames:
        if 'geo_info' in frame:
            geo_info = frame['geo_info']
            break

    if not geo_info:
        geo_info = {
            'bottom_left_lon': 118.0,
            'bottom_left_lat': 21.0,
            'top_right_lon': 123.0,
            'top_right_lat': 26.0,
            'resolution_deg': 0.03,
            'resolution_km': 3.3
        }

    timelapse_data = {
        'metadata': {
            'generated_at': datetime.now().isoformat(),
            'start_time': frames[0]['time'],
            'end_time': frames[-1]['time'],
            'total_frames': len(frames),
            'geo_info': geo_info,
            'source': 'Central Weather Administration O-A0038-003',
            'description': 'Taiwan Temperature Grid Timelapse'
        },
        'frames': []
    }

    # Process frames (remove geo_info from individual frames)
    for frame in frames:
        frame_data = {
            'time': frame['time'],
            'stats': frame['stats'],
            'data': frame['data']
        }
        timelapse_data['frames'].append(frame_data)

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
        description='Update Taiwan Temperature Timelapse data'
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
    print("Taiwan Temperature Timelapse - Data Update")
    print("=" * 60)

    # Check boto3
    if not HAS_BOTO3:
        print("ERROR: boto3 is not installed")
        print("  Run: pip install boto3")
        sys.exit(1)

    # Determine .env path
    env_path = args.env_file
    if not env_path:
        # Try multiple locations
        possible_paths = [
            PROJECT_ROOT / '.env',
            Path.home() / '.env.weather_change',
        ]
        for p in possible_paths:
            if p.exists():
                env_path = p
                break

    if not env_path or not env_path.exists():
        print("ERROR: No .env file found")
        print("  Create .env file with S3 credentials or use --env-file")
        sys.exit(1)

    print(f"Loading env: {env_path}")
    s3_config = load_env_file(env_path)

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
        reader = S3TemperatureReader(s3_config)
    except Exception as e:
        print(f"ERROR: Cannot connect to S3: {e}")
        sys.exit(1)

    # Download data
    frames = download_temperature_data(
        reader,
        start_date=start_date,
        end_date=end_date,
        max_frames=args.max_frames
    )

    if not frames:
        print("ERROR: No temperature data available")
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
